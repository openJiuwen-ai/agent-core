# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Load SkillsBench ``task.md`` packages into skill_train item dicts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.agent_evolving.skill_train.datasets.base import SplitDataLoader

DEFAULT_SPLIT_RATIO = "7:1:2"


def skillsbench_root() -> Path:
    """Checkout root from ``SKILLSBENCH_ROOT``; there is no built-in default."""
    raw = os.getenv("SKILLSBENCH_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser()
    raise FileNotFoundError(
        "SKILLSBENCH_ROOT is not set; point it at the SkillsBench checkout "
        "(the parent of tasks/) or pass the split dir explicitly"
    )


def default_tasks_dir() -> Path:
    """Default runnable tasks live under ``<root>/tasks``."""
    return skillsbench_root() / "tasks"


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    stripped = text.lstrip("\ufeff")
    if not stripped.startswith("---"):
        return {}, stripped.strip()
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return {}, stripped.strip()
    loaded = yaml.safe_load(parts[1]) or {}
    meta = loaded if isinstance(loaded, dict) else {}
    return meta, parts[2].strip()


def _as_task_type(raw: object) -> str:
    if isinstance(raw, list):
        labels = [str(item).strip() for item in raw if str(item).strip()]
        return ",".join(labels)
    text = str(raw or "").strip()
    return text


def _timeout_sec(block: object, default: int) -> int:
    if not isinstance(block, dict):
        return default
    raw = block.get("timeout_sec", default)
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def _skill_records(task_dir: Path) -> list[dict[str, str]]:
    skills_dir = task_dir / "environment" / "skills"
    if not skills_dir.is_dir():
        return []
    records: list[dict[str, str]] = []
    for child in sorted(skills_dir.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.is_file():
            records.append({"name": child.name, "path": str(skill_md.resolve())})
    return records


def parse_task_dir(task_dir: Path) -> dict[str, Any]:
    """Turn one ``tasks/<id>/`` package into a skill_train item."""
    meta, question = _split_frontmatter((task_dir / "task.md").read_text(encoding="utf-8"))
    metadata = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    verifier = task_dir / "verifier" / "test.sh"
    return {
        "id": task_dir.name,
        "question": question,
        "task_type": _as_task_type(metadata.get("task_type")) or "skillsbench",
        "task_dir": str(task_dir.resolve()),
        "timeout_sec": _timeout_sec(meta.get("agent"), 900),
        "verifier_timeout_sec": _timeout_sec(meta.get("verifier"), 900),
        "verifier_script": str(verifier.resolve()) if verifier.is_file() else "",
        "skills": _skill_records(task_dir),
    }


def load_task_records(tasks_dir: str | Path) -> list[dict[str, Any]]:
    """Scan immediate subdirectories that contain ``task.md``."""
    root = Path(tasks_dir)
    if not root.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "task.md").is_file():
            records.append(parse_task_dir(child))
    return records


class SkillsBenchDataLoader(SplitDataLoader):
    """Ratio-split loader over a SkillsBench ``tasks/`` directory."""

    def load_raw_items(self, data_path: str) -> list[dict]:
        location = Path(data_path)
        tasks_dir = location / "tasks" if (location / "tasks").is_dir() else location
        return load_task_records(tasks_dir)
