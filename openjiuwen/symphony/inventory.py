"""Collection inventory and artifact layout shared by runtime engines."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from filelock import FileLock

from openjiuwen.symphony.config import CapabilityConfig, SymphonyConfig
from openjiuwen.symphony.discovery.models import SkillRecord
from openjiuwen.symphony.shared.fingerprint import ScanResult, SkillFolderScanner, SkillManifestParser
from openjiuwen.symphony.shared.fingerprint._io import atomic_write_json
from openjiuwen.symphony.shared.storage import run_artifact_io

LAYOUT_SCHEMA = "symphony.runtime.v1"


class RuntimeStorage:
    """Bind an artifact directory on first write, without constructor side effects."""

    def __init__(self, config: SymphonyConfig) -> None:
        self.root = Path(config.storage.artifact_root)
        source = config.capability.require_single_source()
        self.capability_root = Path(source.root)
        self.capability_type = source.type
        self.identity = {
            "schema_version": LAYOUT_SCHEMA,
            "capability_root": str(self.capability_root),
            "capability_type": self.capability_type,
        }
        self.scope_id = hashlib.sha256(json.dumps(self.identity, sort_keys=True).encode()).hexdigest()[:24]

    def check(self, *, write: bool = False) -> None:
        marker = self.root / "runtime.json"
        if marker.is_file():
            self._validate(marker)
            return
        if not write:
            if self.root.exists() and any(path.name != ".runtime.lock" for path in self.root.iterdir()):
                raise ValueError("Unsupported Symphony artifact layout; select a new empty artifact_root and rebuild")
            return
        self.root.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.root / ".runtime.lock")):
            if marker.exists():
                self._validate(marker)
            else:
                unexpected = [path for path in self.root.iterdir() if path.name != ".runtime.lock"]
                if unexpected:
                    raise ValueError("Artifact directory was populated before collection binding")
                atomic_write_json(marker, self.identity)

    def _validate(self, marker: Path) -> None:
        if json.loads(marker.read_text(encoding="utf-8")) != self.identity:
            raise ValueError("Symphony artifact directory belongs to a different collection or unsupported layout")


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
            codes = ", ".join(item.code for item in result.diagnostics)
            raise ValueError(f"Capability inventory is incomplete: {codes}")
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
