"""Internal directory inventory shared by runtime graph and tree operations."""

from __future__ import annotations

import asyncio
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.symphony._tasks import run_artifact_io
from openjiuwen.symphony.config import CapabilityConfig, CapabilitySourceConfig
from openjiuwen.symphony.discovery.models import SkillRecord
from openjiuwen.symphony.expert_manifest import scan_expert_manifests
from openjiuwen.symphony.shared.fingerprint import ScanDiagnostic, ScanResult, SkillFolderScanner, SkillManifestParser
from openjiuwen.symphony.shared.fingerprint._io import atomic_write_json
from openjiuwen.symphony.runtime_storage import RuntimeStorage


class DirectoryInventory:
    """Read one configured collection; never scan until explicitly requested."""

    def __init__(self, config: CapabilityConfig, storage: RuntimeStorage, *, kind: str | None = None) -> None:
        self.config = config
        self.storage = storage
        self.kind = kind
        self.last_scan: ScanResult | None = None

    def sources(self, kind: str | None = None) -> tuple[CapabilitySourceConfig, ...]:
        return tuple(source for source in self.config.sources if kind is None or source.type == kind)

    def validate_readers(self) -> None:
        for source in self.config.sources:
            if source.type not in {"skill", "expert"}:
                raise ValueError(f"Unsupported capability reader: {source.type}")

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
        if self.kind is None:
            self.validate_readers()
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

    def experts(self, *, allow_partial: bool) -> ScanResult:
        return self._collect(self.sources("expert"), allow_partial=allow_partial, with_roots=True)


def _id_key(identifier: str) -> str:
    return unicodedata.normalize("NFKC", identifier).casefold()
