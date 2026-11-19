"""Collection inventory and artifact layout shared by runtime engines."""

from __future__ import annotations

import asyncio
import hashlib
import json
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from filelock import FileLock

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.symphony.config import CapabilityConfig, CapabilitySourceConfig, SymphonyConfig
from openjiuwen.symphony.discovery.models import SkillRecord
from openjiuwen.symphony.expert_manifest import scan_expert_manifests
from openjiuwen.symphony.models import CapabilityDescriptor
from openjiuwen.symphony.shared.fingerprint import ScanDiagnostic, ScanResult, SkillFolderScanner, SkillManifestParser
from openjiuwen.symphony.shared.fingerprint._io import atomic_write_json
from openjiuwen.symphony.shared.storage import run_artifact_io

LAYOUT_SCHEMA = "symphony.runtime.v1"
_TAXONOMY_LAYOUT_SCHEMA = "symphony.taxonomy.v1"
_TAXONOMY_COLLECTIONS = "taxonomy_collections"
# Pre-existing connector caches also belong to the host and may share this root.
_HOST_CACHE_DIRS = frozenset({"expert", "mcp", "connector"})


def _directory_sources(config: CapabilityConfig) -> tuple[CapabilitySourceConfig, ...]:
    return tuple(source for source in config.sources if source.type in {"skill", "expert"})


def _entries_without_finder_metadata(directory: Path) -> list[Path]:
    return [
        path for path in directory.iterdir()
        if not (path.name == ".DS_Store" and not path.is_symlink() and path.is_file())
    ]


def _is_shared_root_entry(path: Path) -> bool:
    if path.is_symlink() or not path.is_dir():
        return False
    if path.name == _TAXONOMY_COLLECTIONS:
        return True
    if path.name != "cache":
        return False
    entries = _entries_without_finder_metadata(path)
    if not entries:
        return True
    fingerprints = path / "fingerprints"
    if entries != [fingerprints] or fingerprints.is_symlink() or not fingerprints.is_dir():
        return False
    # Host catalogs have their own namespaces beside the unscoped Skill cache.
    return all(
        entry.name in _HOST_CACHE_DIRS and not entry.is_symlink() and entry.is_dir()
        for entry in _entries_without_finder_metadata(fingerprints)
    )


class RuntimeStorage:
    """Bind an artifact directory on first write, without constructor side effects."""

    def __init__(self, config: SymphonyConfig, *, skill_source: CapabilitySourceConfig | None = None) -> None:
        self.base = Path(config.storage.artifact_root)
        sources = (skill_source,) if skill_source is not None else _directory_sources(config.capability)
        self.scoped = len(sources) != 1 or sources[0].type != "skill"
        if self.scoped:
            self.identity = {
                "schema_version": _TAXONOMY_LAYOUT_SCHEMA,
                "sources": [{"type": source.type, "root": str(source.root)} for source in sources],
            }
        else:
            source = sources[0]
            self.identity = {
                "schema_version": LAYOUT_SCHEMA,
                "capability_root": str(source.root),
                "capability_type": source.type,
            }
        self.scope_id = hashlib.sha256(json.dumps(self.identity, sort_keys=True).encode()).hexdigest()[:24]
        self.root = self.base / _TAXONOMY_COLLECTIONS / self.scope_id if self.scoped else self.base

    def _unbound_entries(self) -> list[Path]:
        return [
            path for path in _entries_without_finder_metadata(self.root)
            if path.name != ".runtime.lock" and (self.scoped or not _is_shared_root_entry(path))
        ]

    def check(self, *, write: bool = False) -> None:
        if self.scoped and (self.root.is_symlink() or self.root.parent.is_symlink()):
            raise ValueError("Symphony taxonomy artifact directory must not be symlinked")
        marker = self.root / "runtime.json"
        if marker.is_file():
            self._validate(marker)
            return
        if not write:
            if self.root.exists() and self._unbound_entries():
                raise ValueError("Unsupported Symphony artifact layout; select a new empty artifact_root and rebuild")
            return
        self.root.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.root / ".runtime.lock")):
            if marker.exists():
                self._validate(marker)
            else:
                if self._unbound_entries():
                    raise ValueError("Artifact directory was populated before collection binding")
                atomic_write_json(marker, self.identity)

    def _validate(self, marker: Path) -> None:
        if json.loads(marker.read_text(encoding="utf-8")) != self.identity:
            raise ValueError("Symphony artifact directory belongs to a different collection or unsupported layout")


class DirectoryInventory:
    """Read one configured collection; never scan until explicitly requested."""

    def __init__(self, config: CapabilityConfig, storage: RuntimeStorage, *, kind: str | None = None) -> None:
        self.config = config
        self.storage = storage
        self.kind = kind
        self.last_scan: ScanResult | None = None

    def sources(self, kind: str | None = None) -> tuple[CapabilitySourceConfig, ...]:
        return tuple(source for source in _directory_sources(self.config) if kind is None or source.type == kind)

    def _scan_source(self, source: CapabilitySourceConfig) -> ScanResult:
        root = Path(source.root)
        excluded = (self.storage.base,)
        if source.type == "expert":
            return scan_expert_manifests(root, self.config.scan, excluded_paths=excluded)
        parser = SkillManifestParser(**self.config.scan.manifest.model_dump())
        return SkillFolderScanner(
            root, parser=parser, max_depth=self.config.scan.max_depth,
            **self.config.scan.limits.model_dump(), excluded_paths=excluded,
        ).scan()

    def _collect(
        self, sources: Sequence[CapabilitySourceConfig], *, allow_partial: bool, with_roots: bool = False,
    ) -> ScanResult:
        diagnostics = []
        candidates = defaultdict(list)
        rejected = set()
        source_label = "skill-folder" if len(sources) == 1 and sources[0].type == "skill" else "capability-folders"
        for source in sources:
            try:
                scan = self._scan_source(source)
            except (BaseError, OSError):
                if not allow_partial:
                    raise
                diagnostics.append(ScanDiagnostic("source_read_failed", "Capability source could not be scanned",
                                                  path=str(source.root)))
                continue
            if len(sources) == 1:
                source_label = scan.source
            for diagnostic in scan.diagnostics:
                if len(sources) > 1:
                    diagnostic = replace(diagnostic, details={**diagnostic.details, "source_root": str(source.root)})
                diagnostics.append(diagnostic)
                if diagnostic.code == "duplicate_capability_id" and diagnostic.capability_id:
                    rejected.add((source.type, _id_key(diagnostic.capability_id)))
            for item in scan.capabilities:
                candidates[(source.type, _id_key(item.capability_id))].append((source, item))

        accepted = []
        for identity, entries in sorted(candidates.items()):
            if identity in rejected or len(entries) > 1:
                for source, item in entries:
                    diagnostics.append(ScanDiagnostic(
                        "duplicate_capability_id", "Duplicate capability ID was rejected",
                        path=str(Path(source.root) / item.metadata["entrypoint"]), capability_id=item.capability_id,
                    ))
                continue
            source, item = entries[0]
            if with_roots or len(sources) > 1:
                item = item.model_copy(update={"metadata": {**item.metadata, "source_root": str(source.root)}})
            accepted.append(item)
        if not allow_partial:
            error = next((item for item in diagnostics if item.severity == "error"), None)
            if error is not None:
                location = f" ({error.path})" if error.path else ""
                raise ValueError(f"Capability inventory is incomplete: {error.code}{location}")
        return ScanResult(tuple(accepted), tuple(diagnostics), source_label)

    def scan(self, *, allow_partial: bool = False) -> ScanResult:
        sources = self.sources(self.kind)
        # Preserve the legacy inventory payload and fingerprint signature.
        if not self.storage.scoped and len(sources) == 1 and sources[0].type == "skill" and not allow_partial:
            result = self._scan_source(sources[0])
            error = next((item for item in result.diagnostics if item.severity == "error"), None)
            if error is not None:
                location = f" ({error.path})" if error.path else ""
                raise ValueError(f"Capability inventory is incomplete: {error.code}{location}")
        else:
            result = self._collect(sources, allow_partial=allow_partial)
        self.last_scan = result
        return result

    async def inventory_snapshot(self):
        result = await asyncio.to_thread(self.scan)
        self.storage.check(write=True)
        filename = f"{self.kind}.json" if self.storage.scoped and self.kind else "current.json"
        await run_artifact_io(atomic_write_json, self.storage.root / "inventory" / filename, result.to_dict())
        return result.source_snapshot, result.capabilities

    async def capabilities(self):
        return (await self.inventory_snapshot())[1]

    async def source_snapshot(self):
        return (await self.inventory_snapshot())[0]

    def records(
        self, directories=None, *, disabled=(), sources=None, allow_partial: bool = False,
    ) -> tuple[tuple[SkillRecord, ...], tuple[ScanDiagnostic, ...]]:
        disabled_ids = set(disabled)
        source_labels = sources or {}
        skill_sources = self.sources("skill") if directories is None else tuple(
            CapabilitySourceConfig(type="skill", root=str(root))
            for root in dict.fromkeys(Path(directory).expanduser().resolve() for directory in directories)
        )
        result = self._collect(skill_sources, allow_partial=allow_partial, with_roots=True)
        records = []
        for item in result.capabilities:
            if item.capability_id in disabled_ids or not item.available:
                continue
            metadata = item.metadata
            records.append(SkillRecord(
                worker_id=item.capability_id, name=item.name, description=item.description,
                skill_file=str(Path(metadata["source_root"]) / metadata["entrypoint"]),
                source=str(source_labels.get(item.capability_id, item.source)),
                version=str(metadata.get("version", "")), author=str(metadata.get("author", "")),
                content_hash=item.content_hash, source_root=metadata["source_root"],
                aliases=tuple(metadata.get("aliases", ())),
            ))
        return tuple(records), tuple(item for item in result.diagnostics if item.severity == "error")

    def experts(self, *, allow_partial: bool, manifest_paths: set[Path] | None = None) -> ScanResult:
        result = self._collect(self.sources("expert"), allow_partial=allow_partial, with_roots=True)
        if manifest_paths is None:
            return result
        return replace(result, capabilities=tuple(
            item for item in result.capabilities if expert_manifest_path(item) in manifest_paths
        ))


def expert_manifest_path(descriptor: CapabilityDescriptor) -> Path:
    return Path(descriptor.metadata["source_root"]) / descriptor.metadata["entrypoint"]


def _id_key(identifier: str) -> str:
    return unicodedata.normalize("NFKC", identifier).casefold()
