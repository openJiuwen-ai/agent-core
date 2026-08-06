# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deterministic and secret-aware discovery of folder-based Skills."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn, overload

from openjiuwen.symphony.models import CapabilityDescriptor, SourceSnapshot
from openjiuwen.symphony.models._redaction import is_sensitive_name, normalize_sensitive_name
from openjiuwen.symphony.shared.fingerprint.normalization import (
    IONameVocabulary,
    build_io_name_vocabulary,
)
from openjiuwen.symphony.shared.fingerprint.parser import (
    ManifestDiagnostic,
    ParsedSkillManifest,
    SkillManifestParser,
)
from openjiuwen.symphony.shared.fingerprint.safe_filesystem import (
    open_directory_no_follow,
    open_regular_file_no_follow,
    supports_anchored_open,
)

_HASH_PROTOCOL = b"openjiuwen-symphony-safe-assets-v1\0"
_SOURCE_SNAPSHOT_PROTOCOL = "symphony-source-snapshot-v1"
_READ_CHUNK_SIZE = 1024 * 1024
_DEFAULT_MAX_ASSET_FILES = 10_000
_DEFAULT_MAX_ASSET_FILE_BYTES = 256 * 1024 * 1024
_DEFAULT_MAX_TOTAL_ASSET_BYTES = 512 * 1024 * 1024
_DEFAULT_MAX_SCAN_DIRECTORIES = 50_000
_DEFAULT_MAX_DIRECTORY_ENTRIES = 20_000
_DEFAULT_MAX_ENTRYPOINTS = 10_000
_DEFAULT_MAX_SCAN_ASSET_FILES = 100_000
_DEFAULT_MAX_SCAN_ASSET_BYTES = 2 * 1024 * 1024 * 1024
_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".cache",
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".venv",
        ".ssh",
        "__pycache__",
        "cache",
        "caches",
        "credential",
        "credentials",
        "key",
        "keys",
        "node_modules",
        "private_keys",
        "secret",
        "secrets",
    }
)
_SENSITIVE_FILE_TOKENS = frozenset(
    {
        "credential",
        "credentials",
        "key",
        "keys",
        "password",
        "private",
        "secret",
        "secrets",
        "token",
        "tokens",
    }
)
_KEY_MATERIAL_SUFFIXES = frozenset(
    {
        ".der",
        ".jks",
        ".key",
        ".keystore",
        ".p12",
        ".pem",
        ".pfx",
        ".pkcs12",
        ".ppk",
        ".pub",
    }
)
_SENSITIVE_EXACT_FILENAMES = frozenset(
    {
        ".netrc",
        ".npmrc",
        ".pypirc",
        "authorized_keys",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
    }
)


ScanDiagnostic = ManifestDiagnostic


@dataclass
class _ScanBudget:
    directories: int = 0
    asset_files: int = 0
    asset_bytes: int = 0


@dataclass(frozen=True)
class _SafeDirectoryEntry:
    name: str
    mode: int | None


@dataclass(frozen=True)
class ScanResult(Sequence[CapabilityDescriptor]):
    """Deterministically ordered capability inventory and non-fatal diagnostics."""

    capabilities: tuple[CapabilityDescriptor, ...] = ()
    diagnostics: tuple[ScanDiagnostic, ...] = ()
    source: str = "skill-folder"
    io_name_vocabulary: IONameVocabulary = field(default_factory=IONameVocabulary.empty)

    @property
    def descriptors(self) -> tuple[CapabilityDescriptor, ...]:
        """Alias emphasizing that the inventory contains plain descriptors."""

        return self.capabilities

    @property
    def content_hashes(self) -> dict[str, str]:
        """Map accepted capability IDs to their safe recursive content hashes."""

        return {item.capability_id: item.content_hash for item in self.capabilities}

    @property
    def source_snapshot(self) -> SourceSnapshot:
        """Return a stable provider snapshot derived from accepted capabilities."""

        return build_source_snapshot(self)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe inventory payload."""

        return {
            "capabilities": [item.model_dump(mode="json", exclude={"semantic_content"}) for item in self.capabilities],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "source_snapshot": self.source_snapshot.model_dump(mode="json"),
            "io_name_vocabulary": self.io_name_vocabulary.to_dict(),
        }

    @overload
    def __getitem__(self, index: int) -> CapabilityDescriptor:
        pass

    @overload
    def __getitem__(self, index: slice) -> tuple[CapabilityDescriptor, ...]:
        pass

    def __getitem__(self, index: int | slice) -> CapabilityDescriptor | tuple[CapabilityDescriptor, ...]:
        return self.capabilities[index]

    def __len__(self) -> int:
        return len(self.capabilities)

    def __iter__(self) -> Iterator[CapabilityDescriptor]:
        return iter(self.capabilities)


class SkillFolderScanner:
    """Discover ``SKILL.md`` files only below an explicitly supplied root."""

    def __init__(
        self,
        root: Path | str,
        *,
        parser: SkillManifestParser | None = None,
        max_depth: int | None = None,
        max_asset_files: int = _DEFAULT_MAX_ASSET_FILES,
        max_asset_file_bytes: int = _DEFAULT_MAX_ASSET_FILE_BYTES,
        max_total_asset_bytes: int = _DEFAULT_MAX_TOTAL_ASSET_BYTES,
        max_scan_directories: int = _DEFAULT_MAX_SCAN_DIRECTORIES,
        max_directory_entries: int = _DEFAULT_MAX_DIRECTORY_ENTRIES,
        max_entrypoints: int = _DEFAULT_MAX_ENTRYPOINTS,
        max_scan_asset_files: int = _DEFAULT_MAX_SCAN_ASSET_FILES,
        max_scan_asset_bytes: int = _DEFAULT_MAX_SCAN_ASSET_BYTES,
        capability_type: str = "skill",
        source: str = "skill-folder",
    ) -> None:
        self.root = Path(root)
        self.parser = parser or SkillManifestParser()
        self.max_depth = max_depth
        self.max_asset_files = max_asset_files
        self.max_asset_file_bytes = max_asset_file_bytes
        self.max_total_asset_bytes = max_total_asset_bytes
        self.max_scan_directories = max_scan_directories
        self.max_directory_entries = max_directory_entries
        self.max_entrypoints = max_entrypoints
        self.max_scan_asset_files = max_scan_asset_files
        self.max_scan_asset_bytes = max_scan_asset_bytes
        self.capability_type = capability_type
        self.source = source

    def scan(self) -> ScanResult:
        """Scan safely; one malformed Skill does not abort the remaining inventory."""

        root = _validated_root(self.root)
        if not supports_anchored_open():
            _raise_inventory_error("this platform cannot securely scan an anchored no-follow directory tree")
        if self.max_depth is not None and not _is_integer(self.max_depth, minimum=0):
            _raise_config_error("max_depth must be a non-negative integer or None")
        limits = (
            self.max_asset_files,
            self.max_asset_file_bytes,
            self.max_total_asset_bytes,
            self.max_scan_directories,
            self.max_directory_entries,
            self.max_entrypoints,
            self.max_scan_asset_files,
            self.max_scan_asset_bytes,
        )
        for value in limits:
            if not _is_integer(value, minimum=1):
                _raise_config_error("scanner discovery and asset limits must be positive integers")

        budget = _ScanBudget()
        entrypoints, discovery_diagnostics = _discover_entrypoints(
            root,
            self.max_depth,
            max_directories=self.max_scan_directories,
            max_directory_entries=self.max_directory_entries,
            max_entrypoints=self.max_entrypoints,
            budget=budget,
        )
        diagnostics = list(discovery_diagnostics)
        candidates: list[tuple[CapabilityDescriptor, ParsedSkillManifest]] = []

        for entrypoint in entrypoints:
            relative_entrypoint = entrypoint.relative_to(root).as_posix()
            parsed = self.parser.parse(
                entrypoint,
                root=root,
                capability_id_hint=entrypoint.parent.name,
                capability_type=self.capability_type,
                source=self.source,
                display_path=relative_entrypoint,
            )
            diagnostics.extend(parsed.diagnostics)
            if parsed.descriptor is None:
                continue

            hash_result = _hash_safe_assets(
                entrypoint.parent,
                scan_root=root,
                expected_entrypoint_content_hash=parsed.entrypoint_content_hash,
                max_files=self.max_asset_files,
                max_file_bytes=self.max_asset_file_bytes,
                max_total_bytes=self.max_total_asset_bytes,
                max_scan_directories=self.max_scan_directories,
                max_directory_entries=self.max_directory_entries,
                max_scan_files=self.max_scan_asset_files,
                max_scan_bytes=self.max_scan_asset_bytes,
                budget=budget,
            )
            diagnostics.extend(
                _with_manifest_identity(item, parsed.descriptor.capability_id, relative_entrypoint)
                for item in hash_result.diagnostics
            )
            if not hash_result.ok:
                continue

            metadata = {
                **parsed.descriptor.metadata,
                "asset_paths": list(hash_result.asset_paths),
                "content_hash_algorithm": "sha256-safe-assets-v1",
            }
            descriptor = parsed.descriptor.model_copy(
                update={
                    "content_hash": hash_result.content_hash,
                    "metadata": metadata,
                }
            )
            candidates.append((descriptor, parsed))

        accepted = _reject_duplicate_ids(candidates, diagnostics)
        accepted.sort(
            key=lambda item: (
                item.capability_id.casefold(),
                item.capability_type.casefold(),
                str(item.metadata.get("entrypoint", "")).casefold(),
            )
        )
        diagnostics.sort(key=_diagnostic_sort_key)
        capabilities = tuple(accepted)
        return ScanResult(
            capabilities=capabilities,
            diagnostics=tuple(diagnostics),
            source=self.source,
            io_name_vocabulary=build_io_name_vocabulary(capabilities),
        )

    def snapshot(self) -> tuple[list[CapabilityDescriptor], dict[str, str]]:
        """Return the inventory plus an entrypoint-keyed hash map."""

        result = self.scan()
        hashes = {
            str(item.metadata.get("entrypoint", item.capability_id)): item.content_hash for item in result.capabilities
        }
        return list(result.capabilities), hashes


@dataclass(frozen=True)
class _ContentHashResult:
    content_hash: str = ""
    asset_paths: tuple[str, ...] = ()
    diagnostics: tuple[ScanDiagnostic, ...] = ()

    @property
    def ok(self) -> bool:
        return bool(self.content_hash)


def build_source_snapshot(result: ScanResult) -> SourceSnapshot:
    """Build a deterministic source snapshot without caching directory state."""

    identities = sorted((item.capability_id, item.capability_type, item.content_hash) for item in result.capabilities)
    canonical = {
        "protocol": _SOURCE_SNAPSHOT_PROTOCOL,
        "source": result.source,
        "capabilities": identities,
    }
    encoded = json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    content_hash = hashlib.sha256(encoded).hexdigest()
    return SourceSnapshot(
        snapshot_id=content_hash,
        source=result.source,
        content_hash=content_hash,
        capability_count=len(result.capabilities),
        metadata={
            "algorithm": "sha256",
            "protocol": _SOURCE_SNAPSHOT_PROTOCOL,
            "identity_fields": ["capability_id", "capability_type", "content_hash"],
        },
    )


def _validated_root(root: Path) -> Path:
    absolute = Path(os.path.abspath(root))
    try:
        root_stat = absolute.lstat()
    except OSError as exc:
        _raise_inventory_error("scan root does not exist or cannot be inspected", cause=exc)
    if stat.S_ISLNK(root_stat.st_mode):
        _raise_inventory_error("symlinked scan roots are not allowed")
    if not stat.S_ISDIR(root_stat.st_mode):
        _raise_inventory_error("scan root must be a directory")
    return absolute


def _read_directory_entries(
    directory: Path,
    *,
    root: Path,
    max_directories: int,
    max_entries: int,
    budget: _ScanBudget,
) -> list[_SafeDirectoryEntry]:
    """List a bounded directory through an fd anchored to the configured root."""

    if budget.directories >= max_directories:
        _raise_inventory_error("Skill scan exceeds the configured directory limit")
    budget.directories += 1
    directory_fd = open_directory_no_follow(directory, root=root)
    if directory_fd is None:
        raise OSError("directory could not be opened beneath the configured root")
    try:
        entries: list[_SafeDirectoryEntry] = []
        with os.scandir(directory_fd) as iterator:
            for entry in iterator:
                if len(entries) >= max_entries:
                    _raise_inventory_error("a scanned directory exceeds the configured entry limit")
                try:
                    mode = entry.stat(follow_symlinks=False).st_mode
                except OSError:
                    mode = None
                entries.append(_SafeDirectoryEntry(name=entry.name, mode=mode))
    finally:
        os.close(directory_fd)
    entries.sort(key=lambda item: (item.name.casefold(), item.name))
    return entries


def _discover_entrypoints(
    root: Path,
    max_depth: int | None,
    *,
    max_directories: int,
    max_directory_entries: int,
    max_entrypoints: int,
    budget: _ScanBudget,
) -> tuple[list[Path], list[ScanDiagnostic]]:
    entrypoints: list[Path] = []
    diagnostics: list[ScanDiagnostic] = []
    pending: list[tuple[Path, int]] = [(root, 0)]

    while pending:
        directory, depth = pending.pop()
        try:
            children = _read_directory_entries(
                directory,
                root=root,
                max_directories=max_directories,
                max_entries=max_directory_entries,
                budget=budget,
            )
        except OSError:
            if directory == root:
                _raise_inventory_error("scan root changed or became unreadable during Skill discovery")
            diagnostics.append(
                ScanDiagnostic(
                    code="directory_read_failed",
                    message="directory could not be read during Skill discovery",
                    path=_relative_display(directory, root),
                )
            )
            continue

        subdirectories: list[Path] = []
        for entry in children:
            child = directory / entry.name
            child_mode = entry.mode
            if child_mode is None:
                diagnostics.append(
                    ScanDiagnostic(
                        code="path_inspection_failed",
                        message="path could not be inspected during Skill discovery",
                        path=_relative_display(child, root),
                    )
                )
                continue
            if stat.S_ISLNK(child_mode):
                diagnostics.append(
                    ScanDiagnostic(
                        code="symlink_skipped",
                        message="symlink was not traversed during Skill discovery",
                        severity="warning",
                        path=_relative_display(child, root),
                    )
                )
                continue
            if stat.S_ISREG(child_mode) and child.name == "SKILL.md":
                if len(entrypoints) >= max_entrypoints:
                    _raise_inventory_error("Skill discovery exceeds the configured entrypoint limit")
                entrypoints.append(child)
                continue
            if not stat.S_ISDIR(child_mode) or _exclude_directory(child.name):
                continue
            if max_depth is None or depth < max_depth:
                subdirectories.append(child)

        pending.extend((child, depth + 1) for child in reversed(subdirectories))

    entrypoints.sort(key=lambda item: (_relative_display(item, root).casefold(), _relative_display(item, root)))
    return entrypoints, diagnostics


def _hash_safe_assets(
    skill_root: Path,
    *,
    scan_root: Path,
    expected_entrypoint_content_hash: str,
    max_files: int,
    max_file_bytes: int,
    max_total_bytes: int,
    max_scan_directories: int,
    max_directory_entries: int,
    max_scan_files: int,
    max_scan_bytes: int,
    budget: _ScanBudget,
) -> _ContentHashResult:
    asset_paths, diagnostics = _collect_safe_asset_paths(
        skill_root,
        scan_root=scan_root,
        max_files=max_files,
        max_scan_directories=max_scan_directories,
        max_directory_entries=max_directory_entries,
        max_scan_files=max_scan_files,
        budget=budget,
    )
    if diagnostics:
        fatal = any(item.severity == "error" for item in diagnostics)
        if fatal:
            return _ContentHashResult(diagnostics=tuple(diagnostics))

    digest = hashlib.sha256()
    digest.update(_HASH_PROTOCOL)
    total_bytes = 0
    entrypoint_verified = not expected_entrypoint_content_hash
    for relative_path, absolute_path in asset_paths:
        encoded_path = relative_path.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        try:
            file_descriptor = open_regular_file_no_follow(absolute_path, root=scan_root)
        except OSError:
            diagnostics.append(
                ScanDiagnostic(
                    code="asset_read_failed",
                    message="safe asset could not be opened without following symlinks",
                    path=relative_path,
                )
            )
            return _ContentHashResult(diagnostics=tuple(diagnostics))
        if file_descriptor is None:
            diagnostics.append(
                ScanDiagnostic(
                    code="asset_changed_during_scan",
                    message="asset stopped being a regular file during hashing",
                    path=relative_path,
                )
            )
            return _ContentHashResult(diagnostics=tuple(diagnostics))

        try:
            file_stat = os.fstat(file_descriptor)
            if file_stat.st_size > max_file_bytes or total_bytes + file_stat.st_size > max_total_bytes:
                diagnostics.append(
                    ScanDiagnostic(
                        code="asset_size_limit_exceeded",
                        message="safe assets exceed the configured fingerprint hash byte limit",
                        path=relative_path,
                    )
                )
                return _ContentHashResult(diagnostics=tuple(diagnostics))
            if budget.asset_bytes + file_stat.st_size > max_scan_bytes:
                _raise_inventory_error("safe assets exceed the configured whole-scan byte limit")
            total_bytes += file_stat.st_size
            budget.asset_bytes += file_stat.st_size
            digest.update(file_stat.st_size.to_bytes(16, "big", signed=False))
            bytes_read = 0
            entrypoint_digest = hashlib.sha256() if relative_path == "SKILL.md" else None
            with os.fdopen(file_descriptor, "rb", closefd=False) as stream:
                chunk = stream.read(_READ_CHUNK_SIZE)
                while chunk:
                    digest.update(chunk)
                    if entrypoint_digest is not None:
                        entrypoint_digest.update(chunk)
                    bytes_read += len(chunk)
                    chunk = stream.read(_READ_CHUNK_SIZE)
            final_stat = os.fstat(file_descriptor)
            if bytes_read != file_stat.st_size or (
                final_stat.st_size,
                final_stat.st_mtime_ns,
            ) != (file_stat.st_size, file_stat.st_mtime_ns):
                diagnostics.append(
                    ScanDiagnostic(
                        code="asset_changed_during_scan",
                        message="asset changed while its content hash was being computed",
                        path=relative_path,
                    )
                )
                return _ContentHashResult(diagnostics=tuple(diagnostics))
            if entrypoint_digest is not None:
                if entrypoint_digest.hexdigest() != expected_entrypoint_content_hash:
                    diagnostics.append(
                        ScanDiagnostic(
                            code="manifest_changed_during_scan",
                            message="SKILL.md changed between parsing and content hashing",
                            path=relative_path,
                        )
                    )
                    return _ContentHashResult(diagnostics=tuple(diagnostics))
                entrypoint_verified = True
        except OSError:
            diagnostics.append(
                ScanDiagnostic(
                    code="asset_read_failed",
                    message="safe asset could not be read while computing content hash",
                    path=relative_path,
                )
            )
            return _ContentHashResult(diagnostics=tuple(diagnostics))
        finally:
            os.close(file_descriptor)

    if not entrypoint_verified:
        diagnostics.append(
            ScanDiagnostic(
                code="manifest_changed_during_scan",
                message="SKILL.md disappeared between parsing and content hashing",
                path="SKILL.md",
            )
        )
        return _ContentHashResult(diagnostics=tuple(diagnostics))

    return _ContentHashResult(
        content_hash=digest.hexdigest(),
        asset_paths=tuple(relative for relative, _ in asset_paths),
        diagnostics=tuple(diagnostics),
    )


def _collect_safe_asset_paths(
    skill_root: Path,
    *,
    scan_root: Path,
    max_files: int,
    max_scan_directories: int,
    max_directory_entries: int,
    max_scan_files: int,
    budget: _ScanBudget,
) -> tuple[list[tuple[str, Path]], list[ScanDiagnostic]]:
    assets: list[tuple[str, Path]] = []
    diagnostics: list[ScanDiagnostic] = []
    pending = [skill_root]

    while pending:
        directory = pending.pop()
        try:
            children = _read_directory_entries(
                directory,
                root=scan_root,
                max_directories=max_scan_directories,
                max_entries=max_directory_entries,
                budget=budget,
            )
        except OSError:
            diagnostics.append(
                ScanDiagnostic(
                    code="asset_directory_read_failed",
                    message="asset directory could not be read while computing content hash",
                    path=_relative_display(directory, skill_root),
                )
            )
            continue

        subdirectories: list[Path] = []
        for entry in children:
            child = directory / entry.name
            relative_path = _relative_display(child, skill_root)
            child_mode = entry.mode
            if child_mode is None:
                diagnostics.append(
                    ScanDiagnostic(
                        code="asset_inspection_failed",
                        message="asset could not be inspected while computing content hash",
                        path=relative_path,
                    )
                )
                continue
            if stat.S_ISLNK(child_mode):
                continue
            if stat.S_ISDIR(child_mode):
                if not _exclude_directory(child.name):
                    subdirectories.append(child)
                continue
            if stat.S_ISREG(child_mode) and not _exclude_file(child.name):
                if budget.asset_files >= max_scan_files:
                    _raise_inventory_error("safe assets exceed the configured whole-scan file limit")
                budget.asset_files += 1
                assets.append((relative_path, child))
                if len(assets) > max_files:
                    diagnostics.append(
                        ScanDiagnostic(
                            code="asset_file_limit_exceeded",
                            message="safe assets exceed the configured fingerprint hash file limit",
                            path=".",
                        )
                    )
                    return assets, diagnostics
        pending.extend(reversed(subdirectories))

    assets.sort(key=lambda item: (item[0].casefold(), item[0]))
    return assets, diagnostics


def _reject_duplicate_ids(
    candidates: list[tuple[CapabilityDescriptor, ParsedSkillManifest]],
    diagnostics: list[ScanDiagnostic],
) -> list[CapabilityDescriptor]:
    grouped: dict[str, list[tuple[CapabilityDescriptor, ParsedSkillManifest]]] = {}
    for candidate in candidates:
        duplicate_key = unicodedata.normalize("NFKC", candidate[0].capability_id).casefold()
        grouped.setdefault(duplicate_key, []).append(candidate)

    accepted: list[CapabilityDescriptor] = []
    ordered_groups = sorted(grouped.items(), key=lambda item: item[0])
    for _, group in ordered_groups:
        if len(group) == 1:
            accepted.append(group[0][0])
            continue
        paths = sorted(str(item[0].metadata.get("entrypoint", "SKILL.md")) for item in group)
        for descriptor, _ in group:
            diagnostics.append(
                ScanDiagnostic(
                    code="duplicate_capability_id",
                    message="duplicate capability ID was rejected",
                    path=str(descriptor.metadata.get("entrypoint", "SKILL.md")),
                    capability_id=descriptor.capability_id,
                    details={"conflicting_entrypoints": paths},
                )
            )
    return accepted


def _is_integer(value: object, *, minimum: int) -> bool:
    """Return whether *value* is a non-boolean integer at least *minimum*."""

    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _with_manifest_identity(
    diagnostic: ScanDiagnostic,
    capability_id: str,
    entrypoint: str,
) -> ScanDiagnostic:
    asset_path = diagnostic.path
    folder = Path(entrypoint).parent.as_posix()
    combined_path = asset_path if folder == "." else f"{folder}/{asset_path}"
    return ScanDiagnostic(
        code=diagnostic.code,
        message=diagnostic.message,
        severity=diagnostic.severity,
        path=entrypoint if not asset_path else combined_path,
        capability_id=capability_id,
        details=diagnostic.details,
    )


def _diagnostic_sort_key(diagnostic: ScanDiagnostic) -> tuple[str, str, str]:
    return (diagnostic.path.casefold(), diagnostic.code, diagnostic.capability_id or "")


def _exclude_directory(name: str) -> bool:
    lowered = name.casefold()
    if lowered == ".env" or lowered.startswith(".env.") or lowered == ".envrc":
        return True
    if lowered in _EXCLUDED_DIRECTORY_NAMES or lowered.endswith(".cache"):
        return True
    normalized = normalize_sensitive_name(name)
    tokens = set(normalized.split("_"))
    return is_sensitive_name(name) or bool(
        tokens
        & {
            "credential",
            "credentials",
            "key",
            "keys",
            "password",
            "passwords",
            "private",
            "secret",
            "secrets",
            "token",
            "tokens",
        }
    )


def _exclude_file(name: str) -> bool:
    lowered = name.casefold()
    if lowered == ".env" or lowered.startswith(".env.") or lowered == ".envrc":
        return True
    if lowered in {".git", "__pycache__", "cache"}:
        return True
    if lowered in _SENSITIVE_EXACT_FILENAMES or Path(lowered).suffix in _KEY_MATERIAL_SUFFIXES:
        return True
    if lowered.endswith((".pyc", ".pyo")):
        return True
    normalized_stem = normalize_sensitive_name(Path(name).stem)
    tokens = set(normalized_stem.split("_"))
    return is_sensitive_name(Path(name).stem) or bool(tokens & _SENSITIVE_FILE_TOKENS)


def _relative_display(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        return path.name
    return relative or "."


def _raise_inventory_error(reason: str, *, cause: BaseException | None = None) -> NoReturn:
    from openjiuwen.core.common.exception.codes import StatusCode
    from openjiuwen.core.common.exception.errors import build_error

    raise build_error(
        StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID,
        cause=cause,
        reason=reason,
    )


def _raise_config_error(reason: str) -> NoReturn:
    from openjiuwen.core.common.exception.codes import StatusCode
    from openjiuwen.core.common.exception.errors import build_error

    raise build_error(StatusCode.COMPONENT_SYMPHONY_CONFIG_ERROR, reason=reason)


__all__ = [
    "ScanDiagnostic",
    "ScanResult",
    "SkillFolderScanner",
    "build_source_snapshot",
]
