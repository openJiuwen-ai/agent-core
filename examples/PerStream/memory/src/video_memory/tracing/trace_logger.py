from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


class TraceLogger:
    def __init__(self, traces_dir: str | Path, enabled: bool = True) -> None:
        self.traces_dir = Path(traces_dir)
        self.enabled = enabled
        if enabled:
            self.traces_dir.mkdir(parents=True, exist_ok=True)

    def write_json(self, name: str, data: Any) -> None:
        if not self.enabled:
            return
        path = self.traces_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(_jsonable(data), f, ensure_ascii=False, indent=2)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(item) for item in value]
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value

