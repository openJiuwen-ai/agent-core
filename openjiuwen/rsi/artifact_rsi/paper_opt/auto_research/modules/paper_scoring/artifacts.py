"""Atomic JSON artifact writers for paper scoring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_scoring.aggregation import compact_scoresheet
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_scoring.schemas import Scoresheet


def ensure_output_dir(path: str | Path) -> Path:
    directory = Path(path).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def atomic_write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, BaseModel):
        text = payload.model_dump_json(indent=2)
    else:
        text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    return path


def write_scoresheet(output_dir: Path, scoresheet: Scoresheet) -> Path:
    return atomic_write_json(output_dir / "scoresheet.json", compact_scoresheet(scoresheet))


def write_named(output_dir: Path, name: str, payload: Any) -> Path:
    return atomic_write_json(output_dir / name, payload)
