# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Small filesystem helpers for the L2/L3 offline-memory bank.

Nothing here talks to an LLM or knows about any particular trace-log
format — it only knows how to read/write the memory-bank files on disk
(YAML profiles, markdown correlation notes, JSONL append logs, and the
"already processed" ledgers that keep offline extraction idempotent
across re-runs).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Plain text / YAML

def load_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def save_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or {}


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=True),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# JSONL (used for the L3 append-only stores: team_compositions /
# role_suggestions / anti_patterns)

def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Plain JSON (used for the L3 playbook's outcome_stats rolling window —
# a flat dict + list of floats, a more natural fit than YAML)

def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def new_item_id(existing: dict[str, Any] | None = None) -> str:
    """Short stable id for a new playbook item, regenerated on the
    astronomically unlikely collision against ``existing``'s keys.
    """
    existing = existing or {}
    while True:
        candidate = uuid.uuid4().hex[:8]
        if candidate not in existing:
            return candidate


# ---------------------------------------------------------------------------
# Processed-episode ledgers (idempotency across re-runs)

def load_ledger(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8")))


def save_ledger(path: Path, sample_ids: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sorted(sample_ids), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Teammate-profile merge — L2 rule: profiles are merged, not replaced. List
# fields are unioned and capped, scalar fields are overwritten by the newer
# observation.

def merge_profile(old: dict[str, Any], new: dict[str, Any], list_cap: int = 5) -> dict[str, Any]:
    merged = dict(old)
    for key, value in new.items():
        old_value = merged.get(key)
        if isinstance(old_value, list) and isinstance(value, list):
            seen = set(old_value)
            combined = list(old_value)
            for item in value:
                if item not in seen:
                    combined.append(item)
                    seen.add(item)
            merged[key] = combined[:list_cap]
        else:
            merged[key] = value
    return merged
