"""Collection identity and artifact layout owned by a Symphony runtime."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from filelock import FileLock

from openjiuwen.symphony.config import CapabilitySourceConfig, SymphonyConfig
from openjiuwen.symphony.shared.fingerprint._io import atomic_write_json

LAYOUT_SCHEMA = "symphony.runtime.v1"
_TAXONOMY_LAYOUT_SCHEMA = "symphony.taxonomy.v1"
_TAXONOMY_COLLECTIONS = "taxonomy_collections"


class RuntimeStorage:
    """Bind an artifact directory on first write, without constructor side effects."""

    def __init__(self, config: SymphonyConfig, *, skill_source: CapabilitySourceConfig | None = None) -> None:
        self.base = Path(config.storage.artifact_root)
        sources = [skill_source] if skill_source is not None else config.capability.sources
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
            path for path in self.root.iterdir()
            if path.name != ".runtime.lock"
            and not (not self.scoped and path.name == _TAXONOMY_COLLECTIONS
                     and path.is_dir() and not path.is_symlink())
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

