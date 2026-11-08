# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Install-directory layout, zip opening, and copies that never delete user source."""

import json
import os
import shutil
import stat
import tempfile
import uuid
import zipfile
from collections.abc import Collection, Sequence
from contextlib import nullcontext
from pathlib import Path, PurePosixPath

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.extension.errors import ExtensionFailure
from openjiuwen.core.extension.manifest import has_manifest, load_manifest
from openjiuwen.core.extension.schema.manifest import Manifest

# Bound package archives and reject symbolic links before loading code.
_MAX_ZIP_FILE_COUNT = 10_000
_MAX_ZIP_TOTAL_BYTES = 512 * 1024 * 1024
_MAX_ZIP_SINGLE_FILE_BYTES = 32 * 1024 * 1024
_MAX_ZIP_PATH_DEPTH = 32


class OpenedSource:
    """A directory whose manifest has been read.

    ``close`` deletes a temporary extract directory. It does not delete the
    caller's zip or source directory.
    """

    def __init__(
        self,
        manifest: Manifest,
        root: Path,
        source_path: Path,
        cleanup_root: Path | None,
    ) -> None:
        self.manifest = manifest
        self.root = root
        self.source_path = source_path
        self._cleanup_root = cleanup_root
        self._closed = False

    def close(self) -> None:
        """Release the temporary extraction directory, if this source owns one."""
        if self._closed:
            return
        if self._cleanup_root is None:
            self._closed = True
            return
        cleanup = self._cleanup_root.resolve()
        source = self.source_path.resolve()
        if cleanup == source or source.is_relative_to(cleanup):
            raise ExtensionFailure(
                StatusCode.EXTENSION_INSTALL_COPY_FAILED,
                reason="temp directory overlaps the caller source; refusing to delete",
            )
        shutil.rmtree(cleanup, ignore_errors=True)
        self._closed = True

    def __enter__(self) -> "OpenedSource":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class PackageManager:
    """Owns the install directory. It does not record which packages are active."""

    def __init__(
        self, store: Path, *, domain: str | None = None, allowed_domains: Collection[str] | None = None,
    ) -> None:
        self.store = store.expanduser().resolve()
        self.domain = domain
        self.allowed_domains = None if allowed_domains is None else frozenset(allowed_domains)
        self.require_domain = domain is not None or allowed_domains is not None
        self.install_report = None

    def validate_manifest(self, manifest: Manifest, *, match_domain: bool = True) -> None:
        """Validate generic host restrictions without knowing product domain names."""
        if self.require_domain and (not isinstance(manifest.domain, str) or not manifest.domain.strip()):
            raise ExtensionFailure(StatusCode.EXTENSION_MANIFEST_INVALID, reason="package.domain is required")
        if self.allowed_domains is not None and manifest.domain not in self.allowed_domains:
            raise ExtensionFailure(StatusCode.EXTENSION_MANIFEST_INVALID, reason="package.domain is not allowed")
        if match_domain and self.domain is not None and manifest.domain != self.domain:
            raise ExtensionFailure(StatusCode.EXTENSION_DOMAIN_MISMATCH,
                                   reason=f"{manifest.package_id} belongs to {manifest.domain!r}")

    def _guard_mutation(self, package_id: str) -> None:
        if self.install_report is not None:
            self.install_report.require_file_operation(package_id)

    def list_installed(self) -> list[Path]:
        """Return each ``<package.id>/<version>`` directory that has a manifest.

        Hidden directories are skipped. The caller passes these paths to the
        startup batch install. This method does not choose among duplicates.
        """
        if not self.store.exists():
            return []
        found: list[Path] = []
        for package_dir in sorted(self.store.iterdir(), key=lambda path: path.name):
            if not package_dir.is_dir() or package_dir.name.startswith("."):
                continue
            for version_dir in sorted(package_dir.iterdir(), key=lambda path: path.name):
                if not version_dir.is_dir() or version_dir.name.startswith("."):
                    continue
                if has_manifest(version_dir):
                    found.append(version_dir.resolve())
        return found

    def open_source(self, source: Path) -> OpenedSource:
        """Open a package directory or a zip.

        A directory is read in place. A zip is extracted to a new temporary
        directory. The original path is left untouched.

        Raises:
            ExtensionFailure: The path is not a directory or zip, the zip
                escapes its directory, or the manifest is invalid.
        """
        source = source.expanduser().resolve()
        if source.is_file() and source.suffix.lower() == ".zip":
            return self._open_zip(source)
        if source.is_dir():
            _assert_no_symlinks(source)
            manifest = load_manifest(source, require_domain=self.require_domain)
            self.validate_manifest(manifest)
            return OpenedSource(manifest, source, source, None)
        raise ExtensionFailure(
            StatusCode.EXTENSION_SOURCE_INVALID,
            reason=f"install source must be a directory or zip: {source}",
        )

    def copy_into_store(self, source_root: Path, manifest: Manifest) -> Path:
        """Copy a package tree into ``<store>/<package.id>/<version>``.

        If ``source_root`` is already that directory, it is returned as-is.
        The source tree is never deleted. An existing store copy is not
        replaced; callers must uninstall or reload instead.

        Raises:
            ExtensionFailure: The copy cannot be completed. The source is kept.
        """
        self.validate_manifest(manifest)
        self._guard_mutation(manifest.package_id)
        source_root = source_root.resolve()
        if not source_root.exists():
            raise ExtensionFailure(
                StatusCode.EXTENSION_INSTALL_COPY_FAILED,
                reason="caller source does not exist",
            )
        _assert_no_symlinks(source_root)
        target = self.version_dir(manifest.package_id, manifest.version)
        if source_root == target:
            return target
        if target.exists():
            raise ExtensionFailure(
                StatusCode.EXTENSION_INSTALL_COPY_FAILED,
                reason=(
                    f"install store already has {manifest.package_id}/"
                    f"{manifest.version}; refuse to overwrite"
                ),
            )
        package_dir = self.store / manifest.package_id
        package_dir.mkdir(parents=True, exist_ok=True)
        staging = package_dir / f".{manifest.version}.staging-{uuid.uuid4().hex}"
        if staging.is_relative_to(source_root) or source_root.is_relative_to(staging):
            raise ExtensionFailure(
                StatusCode.EXTENSION_INSTALL_COPY_FAILED,
                reason="staging directory overlaps the caller source",
            )
        try:
            shutil.copytree(source_root, staging, symlinks=False)
            _write_manifest_enable(staging, manifest.enable)
        except ExtensionFailure:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            raise
        except OSError as exc:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            raise ExtensionFailure(
                StatusCode.EXTENSION_INSTALL_COPY_FAILED,
                reason=f"copy into install directory failed: {exc}",
                cause=exc,
            ) from exc
        try:
            with (self.install_report.file_operation_scope(manifest.package_id)
                  if self.install_report is not None else nullcontext()):
                staging.rename(target)
        except OSError as exc:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            raise ExtensionFailure(
                StatusCode.EXTENSION_INSTALL_COPY_FAILED,
                reason=f"replace install directory failed: {exc}",
                cause=exc,
            ) from exc
        return target

    def normalize_legacy_enable(self) -> None:
        """Add the default enable field to valid managed copies that lack it.

        Callers that coordinate multiple processes hold the install-report
        discovery lock while invoking this maintenance operation.
        """
        for root in self.list_installed():
            try:
                manifest = load_manifest(root, require_domain=self.require_domain)
                self.validate_manifest(manifest, match_domain=False)
                if manifest.package_id != root.parent.name or manifest.version != root.name:
                    continue
                document = _read_manifest_document(root)
            except ExtensionFailure:
                continue
            if "enable" not in document:
                try:
                    _write_manifest_enable(root, True)
                except ExtensionFailure:
                    # Omitting enable is already equivalent to True. A legacy
                    # copy that cannot be rewritten must not block startup.
                    continue

    def set_enabled(self, package_id: str, version: str, enabled: bool) -> None:
        """Persist the loading state of one verified managed package copy."""
        if not isinstance(enabled, bool):
            raise ExtensionFailure(StatusCode.EXTENSION_MANIFEST_INVALID, reason="enable must be a boolean")
        self._guard_mutation(package_id)
        target = self.version_dir(package_id, version)
        original = self.store / package_id / version
        unsafe_components = any(
            (
                original.is_symlink(),
                original.parent.is_symlink(),
                Path(package_id).name != package_id,
                Path(version).name != version,
            )
        )
        outside_store = not target.is_relative_to(self.store)
        wrong_depth = target.parent.parent != self.store
        if unsafe_components or outside_store or wrong_depth:
            raise ExtensionFailure(
                StatusCode.EXTENSION_INSTALL_COPY_FAILED,
                reason="refusing to update a path outside the install store",
            )
        if not target.is_dir() or not has_manifest(target):
            raise ExtensionFailure(
                StatusCode.EXTENSION_PACKAGE_NOT_INSTALLED,
                package_id=package_id,
                reason=f"install store has no {package_id} {version}",
            )
        _assert_no_symlinks(target)
        manifest = load_manifest(target, require_domain=self.require_domain)
        self.validate_manifest(manifest)
        if manifest.package_id != package_id or manifest.version != version:
            raise ExtensionFailure(StatusCode.EXTENSION_SOURCE_INVALID, reason="managed copy identity mismatch")
        _write_manifest_enable(target, enabled)

    def managed_versions(self, package_id: str, *, known_versions: Sequence[str] = ()) -> tuple[str, ...]:
        """Verify copies, retaining ownership facts from active or pending records."""
        package_dir = self.store / package_id
        if package_dir.is_symlink() or package_dir.resolve().parent != self.store or package_dir == self.store:
            raise ExtensionFailure(StatusCode.EXTENSION_SOURCE_INVALID, reason="invalid managed package directory")
        if not package_dir.exists():
            return ()
        versions: list[str] = []
        for root in sorted(package_dir.iterdir()):
            if root.name.startswith(".") or not root.is_dir():
                continue
            if root.is_symlink() or root.resolve().parent != package_dir.resolve():
                raise ExtensionFailure(StatusCode.EXTENSION_SOURCE_INVALID, reason="managed version is redirected")
            if root.name in known_versions:
                versions.append(root.name)
                continue
            if not has_manifest(root):
                continue
            try:
                manifest = load_manifest(root, require_domain=self.require_domain)
                self.validate_manifest(manifest, match_domain=False)
                if manifest.package_id != package_id or manifest.version != root.name:
                    raise ExtensionFailure(
                        StatusCode.EXTENSION_SOURCE_INVALID,
                        reason="managed copy identity mismatch",
                    )
            except ExtensionFailure:
                continue
            versions.append(root.name)
        return tuple(versions)

    def remove_installed(self, package_id: str, version: str, *, missing_ok: bool = False) -> None:
        """Delete one version directory inside the install store.

        ``missing_ok`` is for verified uninstall retries: a partial removal
        can already have removed the directory or its manifest.

        Raises:
            ExtensionFailure: The path would leave the store, or the directory
                is not an installed package.
        """
        self._guard_mutation(package_id)
        target = self.version_dir(package_id, version)
        store = self.store.resolve()
        original = self.store / package_id / version
        unsafe_components = any(
            (
                original.is_symlink(),
                original.parent.is_symlink(),
                Path(package_id).name != package_id,
                Path(version).name != version,
            )
        )
        outside_store = not target.is_relative_to(store)
        is_store_root = target == store
        wrong_depth = target.parent.parent != store
        if any((outside_store, is_store_root, wrong_depth, unsafe_components)):
            raise ExtensionFailure(
                StatusCode.EXTENSION_INSTALL_COPY_FAILED,
                reason="refusing to delete a path outside the install store",
            )
        if missing_ok and not target.exists():
            return
        if not target.is_dir() or (not missing_ok and not has_manifest(target)):
            raise ExtensionFailure(
                StatusCode.EXTENSION_PACKAGE_NOT_INSTALLED,
                package_id=package_id,
                reason=f"install store has no {package_id} {version}",
            )
        _assert_no_symlinks(target)
        try:
            with (self.install_report.file_operation_scope(package_id)
                  if self.install_report is not None else nullcontext()):
                shutil.rmtree(target)
                package_dir = target.parent
                if package_dir.exists() and not any(package_dir.iterdir()):
                    package_dir.rmdir()
        except OSError as exc:
            raise ExtensionFailure(
                StatusCode.EXTENSION_INSTALL_COPY_FAILED,
                cause=exc,
                reason="could not fully remove the managed install copy; retry uninstall",
            ) from exc

    def version_dir(self, package_id: str, version: str) -> Path:
        """Return the resolved install path; deletion validates its containment."""
        return (self.store / package_id / version).resolve()

    def _open_zip(self, source: Path) -> OpenedSource:
        temp = Path(tempfile.mkdtemp(prefix="openjiuwen-ext-"))
        try:
            _extract_zip(source, temp)
            _assert_no_symlinks(temp)
            manifest = load_manifest(temp, require_domain=self.require_domain)
            self.validate_manifest(manifest)
        except Exception as error:
            shutil.rmtree(temp, ignore_errors=True)
            raise error
        return OpenedSource(manifest, temp.resolve(), source, temp.resolve())


def _assert_no_symlinks(src: Path) -> None:
    """Reject a package subtree that contains any symbolic link."""
    if src.is_symlink():
        raise ExtensionFailure(
            StatusCode.EXTENSION_SOURCE_INVALID,
            reason=f"symbolic links are not allowed in package: {src}",
        )
    try:
        entries = src.rglob("*")
        for entry in entries:
            if entry.is_symlink():
                raise ExtensionFailure(
                    StatusCode.EXTENSION_SOURCE_INVALID,
                    reason=f"symbolic links are not allowed in package: {entry}",
                )
    except OSError as exc:
        raise ExtensionFailure(
            StatusCode.EXTENSION_SOURCE_INVALID,
            reason=f"failed to inspect package for symlinks: {exc}",
            cause=exc,
        ) from exc


def _read_manifest_document(package_root: Path) -> dict[str, object]:
    """Read a validated package manifest as the mapping that will be updated."""
    path = package_root / "manifest.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=f"cannot read {path.name}: {exc}",
            cause=exc,
        ) from exc
    if not isinstance(document, dict):
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason="manifest.json top level must be a mapping",
        )
    return document


def _write_manifest_enable(package_root: Path, enabled: bool) -> None:
    """Atomically write an already validated loading flag in one package root."""
    document = _read_manifest_document(package_root)
    document["enable"] = enabled
    path = package_root / "manifest.json"
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
        raise ExtensionFailure(
            StatusCode.EXTENSION_INSTALL_COPY_FAILED,
            reason=f"cannot update manifest enable state: {exc}",
            cause=exc,
        ) from exc


def _extract_zip(source: Path, destination: Path) -> None:
    destination = destination.resolve()
    try:
        archive = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ExtensionFailure(
            StatusCode.EXTENSION_SOURCE_INVALID,
            reason=f"cannot open zip: {exc}",
            cause=exc,
        ) from exc
    with archive:
        total_bytes = 0
        file_count = 0
        for info in archive.infolist():
            target = _member_path(destination, info.filename)
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_IFMT(unix_mode) == stat.S_IFLNK:
                raise ExtensionFailure(
                    StatusCode.EXTENSION_SOURCE_INVALID,
                    reason=f"zip symbolic links are not allowed: {info.filename}",
                )
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            file_count += 1
            if file_count > _MAX_ZIP_FILE_COUNT:
                raise ExtensionFailure(
                    StatusCode.EXTENSION_SOURCE_INVALID,
                    reason=f"zip file count exceeds {_MAX_ZIP_FILE_COUNT}",
                )
            declared = int(info.file_size or 0)
            if declared > _MAX_ZIP_SINGLE_FILE_BYTES:
                raise ExtensionFailure(
                    StatusCode.EXTENSION_SOURCE_INVALID,
                    reason=(
                        f"zip member exceeds {_MAX_ZIP_SINGLE_FILE_BYTES} bytes: "
                        f"{info.filename}"
                    ),
                )
            if total_bytes + max(declared, 0) > _MAX_ZIP_TOTAL_BYTES:
                raise ExtensionFailure(
                    StatusCode.EXTENSION_SOURCE_INVALID,
                    reason=f"zip uncompressed size exceeds {_MAX_ZIP_TOTAL_BYTES} bytes",
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source_file:
                data = source_file.read(_MAX_ZIP_SINGLE_FILE_BYTES + 1)
            if len(data) > _MAX_ZIP_SINGLE_FILE_BYTES:
                raise ExtensionFailure(
                    StatusCode.EXTENSION_SOURCE_INVALID,
                    reason=(
                        f"zip member exceeds {_MAX_ZIP_SINGLE_FILE_BYTES} bytes: "
                        f"{info.filename}"
                    ),
                )
            total_bytes += len(data)
            if total_bytes > _MAX_ZIP_TOTAL_BYTES:
                raise ExtensionFailure(
                    StatusCode.EXTENSION_SOURCE_INVALID,
                    reason=f"zip uncompressed size exceeds {_MAX_ZIP_TOTAL_BYTES} bytes",
                )
            target.write_bytes(data)


def _member_path(destination: Path, name: str) -> Path:
    if "\\" in name:
        raise ExtensionFailure(
            StatusCode.EXTENSION_SOURCE_INVALID,
            reason=f"zip member path escapes destination: {name}",
        )
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise ExtensionFailure(
            StatusCode.EXTENSION_SOURCE_INVALID,
            reason=f"zip member path escapes destination: {name}",
        )
    if len(pure.parts) > _MAX_ZIP_PATH_DEPTH:
        raise ExtensionFailure(
            StatusCode.EXTENSION_SOURCE_INVALID,
            reason=f"zip member path depth exceeds {_MAX_ZIP_PATH_DEPTH}: {name}",
        )
    target = destination.joinpath(*pure.parts).resolve()
    if not target.is_relative_to(destination):
        raise ExtensionFailure(
            StatusCode.EXTENSION_SOURCE_INVALID,
            reason=f"zip member path escapes destination: {name}",
        )
    return target
