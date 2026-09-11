# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Cross-night sleep state persistence."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_STATE: Dict[str, Any] = {
    "version": 1,
    "night": 0,
    "last_harvest": {},
    "history": [],
    "task_archive": [],
}


def _now_iso(clock: Optional[float] = None) -> str:
    return time.strftime(
        "%Y-%m-%dT%H:%M:%S",
        time.localtime(clock if clock is not None else time.time()),
    )


class SleepState:
    """Persistent night counter and harvest cursor."""

    def __init__(self, path: Path | str, data: Optional[Dict[str, Any]] = None) -> None:
        self.path = Path(path)
        self.data = data if data is not None else copy.deepcopy(DEFAULT_STATE)

    @classmethod
    def load(cls, path: Path | str) -> "SleepState":
        path = Path(path)
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                merged = copy.deepcopy(DEFAULT_STATE)
                if isinstance(loaded, dict):
                    merged.update(loaded)
                return cls(path, merged)
            except Exception:
                pass
        return cls(path, copy.deepcopy(DEFAULT_STATE))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @property
    def night(self) -> int:
        return int(self.data.get("night", 0))

    def begin_night(self, clock: Optional[float] = None) -> int:
        del clock
        self.data["night"] = self.night + 1
        return self.night

    def set_last_harvest(self, project: str, iso_ts: str | None = None) -> None:
        self.data.setdefault("last_harvest", {})[project] = iso_ts or _now_iso()

    def record_night(self, summary: Dict[str, Any]) -> None:
        self.data.setdefault("history", []).append(summary)

    def add_to_archive(self, task_dicts: List[Dict[str, Any]], cap: int = 300) -> None:
        archive = self.data.setdefault("task_archive", [])
        archive.extend(task_dicts)
        if len(archive) > cap:
            self.data["task_archive"] = archive[-cap:]
