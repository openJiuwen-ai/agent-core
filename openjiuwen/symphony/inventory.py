"""Internal directory inventory shared by runtime graph and tree operations."""

from __future__ import annotations

import asyncio
from pathlib import Path

from openjiuwen.symphony._tasks import run_artifact_io
from openjiuwen.symphony.config import CapabilityConfig
from openjiuwen.symphony.discovery.models import SkillRecord
from openjiuwen.symphony.shared.fingerprint import ScanResult, SkillFolderScanner, SkillManifestParser
from openjiuwen.symphony.shared.fingerprint._io import atomic_write_json
from openjiuwen.symphony.runtime_storage import RuntimeStorage


class DirectoryInventory:
    """Read one configured collection; never scan until explicitly requested."""

    def __init__(self, config: CapabilityConfig, storage: RuntimeStorage) -> None:
        self.config = config
        self.source = config.require_single_source()
        self.storage = storage
        self.last_scan: ScanResult | None = None

    def scan(self, root: Path | None = None) -> ScanResult:
        if self.source.type != "skill":
            raise ValueError(f"Unsupported capability reader: {self.source.type}")
        parser = SkillManifestParser(**self.config.scan.manifest.model_dump())
        scanner = SkillFolderScanner(
            root or self.source.root,
            parser=parser,
            max_depth=self.config.scan.max_depth,
            **self.config.scan.limits.model_dump(),
            excluded_paths=(self.storage.root,),
        )
        result = scanner.scan()
        if any(item.severity == "error" for item in result.diagnostics):
            raise ValueError("Capability inventory is incomplete: " + ", ".join(item.code for item in result.diagnostics))
        self.last_scan = result
        return result

    async def inventory_snapshot(self):
        result = await asyncio.to_thread(self.scan)
        self.storage.check(write=True)
        await run_artifact_io(atomic_write_json, self.storage.root / "inventory" / "current.json", result.to_dict())
        return result.source_snapshot, result.capabilities

    async def capabilities(self):
        return (await self.inventory_snapshot())[1]

    async def source_snapshot(self):
        return (await self.inventory_snapshot())[0]

    def records(self, directories=None, *, disabled=(), sources=None) -> tuple[SkillRecord, ...]:
        records: dict[str, SkillRecord] = {}
        disabled_ids = set(disabled)
        source_labels = sources or {}
        for directory in directories if directories is not None else (self.source.root,):
            root = Path(directory).expanduser().resolve()
            for item in self.scan(root).capabilities:
                if item.capability_id in disabled_ids or not item.available:
                    continue
                if item.capability_id in records:
                    raise ValueError(f"Duplicate capability ID: {item.capability_id}")
                metadata = item.metadata
                records[item.capability_id] = SkillRecord(
                    worker_id=item.capability_id,
                    name=item.name,
                    description=item.description,
                    skill_file=str(root / str(metadata["entrypoint"])),
                    source=str(source_labels.get(item.capability_id, item.source)),
                    version=str(metadata.get("version", "")),
                    author=str(metadata.get("author", "")),
                    content_hash=item.content_hash,
                    source_root=str(root),
                    aliases=tuple(metadata.get("aliases", ())),
                )
        return tuple(records.values())
