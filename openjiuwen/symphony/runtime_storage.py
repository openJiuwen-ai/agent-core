"""Collection identity and artifact layout owned by a Symphony runtime."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from filelock import FileLock

from openjiuwen.symphony.config import SymphonyConfig
from openjiuwen.symphony.shared.fingerprint._io import atomic_write_json

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

