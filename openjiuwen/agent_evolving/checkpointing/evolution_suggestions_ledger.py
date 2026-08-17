# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Workspace-level Suggest-mode suggestions ledger.

Path: ``.office-claw/evolution-suggestions-ledger.json``

Only Suggest mode writes here. Each item stores id + summary + timestamp.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence, Union

from openjiuwen.core.common.logging import logger

_LEDGER_FILENAME = "evolution-suggestions-ledger.json"
_MAX_RETRIES = 5


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _as_path_list(
    skills_dirs: Optional[Union[Path, str, Sequence[Union[Path, str]]]],
) -> list[Path]:
    if skills_dirs is None:
        return []
    if isinstance(skills_dirs, (str, Path)):
        return [Path(skills_dirs).expanduser()]
    return [Path(item).expanduser() for item in skills_dirs if item]


def resolve_ledger_path(
    skills_dirs: Optional[Union[Path, str, Sequence[Union[Path, str]]]] = None,
) -> Optional[Path]:
    """Resolve ``.office-claw/evolution-suggestions-ledger.json`` next to host root."""
    configured = (
        (os.getenv("OFFICE_CLAW_CONFIG_ROOT") or "").strip()
        or (os.getenv("OFFICE_CLAW_ROOT") or "").strip()
    )
    if configured:
        return Path(configured).expanduser().resolve() / ".office-claw" / _LEDGER_FILENAME

    for skills_dir in _as_path_list(skills_dirs):
        try:
            path = skills_dir.resolve()
        except OSError:
            path = skills_dir
        # ``.../.office-claw/skills`` → ledger beside capabilities.json
        if path.name == "skills" and path.parent.name == ".office-claw":
            return path.parent / _LEDGER_FILENAME
        # project root containing ``.office-claw``
        nested_dir = path / ".office-claw"
        if nested_dir.is_dir() or (nested_dir / "capabilities.json").is_file():
            return nested_dir / _LEDGER_FILENAME
        # parent project root
        parent_office = path.parent / ".office-claw"
        if parent_office.is_dir() or (parent_office / "capabilities.json").is_file():
            return parent_office / _LEDGER_FILENAME
        if path.name == "skills":
            return path.parent / _LEDGER_FILENAME
    return None


def _empty_ledger() -> dict[str, Any]:
    return {"version": 1, "updated_at": _now_iso(), "skills": []}


def _empty_bucket(skill_name: str) -> dict[str, Any]:
    return {
        "skillName": skill_name,
        "updated_at": _now_iso(),
        "generated": [],
        "accepted": [],
        "rejected": [],
    }


def _read_ledger(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return _empty_ledger()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_ledger()
    if not isinstance(raw, dict):
        return _empty_ledger()
    skills = raw.get("skills")
    if not isinstance(skills, list):
        skills = []
    return {
        "version": raw.get("version", 1) if isinstance(raw.get("version"), int) else 1,
        "updated_at": raw.get("updated_at") if isinstance(raw.get("updated_at"), str) else _now_iso(),
        "skills": [s for s in skills if isinstance(s, dict)],
    }


def _atomic_write(path: Path, ledger: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger["updated_at"] = _now_iso()
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{time.time_ns()}.tmp")
    text = json.dumps(ledger, ensure_ascii=False, indent=2) + "\n"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _ensure_bucket(ledger: dict[str, Any], skill_name: str) -> dict[str, Any]:
    skills = ledger.setdefault("skills", [])
    for bucket in skills:
        if isinstance(bucket, dict) and bucket.get("skillName") == skill_name:
            return bucket
    bucket = _empty_bucket(skill_name)
    skills.append(bucket)
    return bucket


def _upsert_generated(bucket: dict[str, Any], item: dict[str, str]) -> None:
    generated = bucket.setdefault("generated", [])
    for idx, existing in enumerate(generated):
        if isinstance(existing, dict) and existing.get("id") == item["id"]:
            generated[idx] = {
                "id": item["id"],
                "summary": item.get("summary", ""),
                "timestamp": item.get("timestamp") or _now_iso(),
            }
            bucket["updated_at"] = _now_iso()
            return
    generated.append(
        {
            "id": item["id"],
            "summary": item.get("summary", ""),
            "timestamp": item.get("timestamp") or _now_iso(),
        }
    )
    bucket["updated_at"] = _now_iso()


def improvement_summary_from_record(record: Any) -> str:
    """Extract improvement text from summary only (never change.content)."""
    summary = getattr(record, "summary", None)
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    change = getattr(record, "change", None)
    change_summary = getattr(change, "summary", None) if change is not None else None
    if isinstance(change_summary, str) and change_summary.strip():
        return change_summary.strip()
    return ""


# Back-compat alias
def improvement_content_from_record(record: Any) -> str:
    return improvement_summary_from_record(record)


def record_generated_suggestion(
    skill_name: str,
    record: Any,
    *,
    skills_dirs: Optional[Union[Path, str, Sequence[Union[Path, str]]]] = None,
    ledger_path: Optional[Path] = None,
) -> None:
    """Append/update a Suggest-mode experience into the workspace ledger.

    Failures are logged and swallowed so evolution itself is not blocked.
    """
    name = (skill_name or "").strip()
    record_id = str(getattr(record, "id", "") or "").strip()
    if not name or not record_id:
        return

    path = ledger_path or resolve_ledger_path(skills_dirs)
    if path is None:
        logger.warning(
            "[EvolutionSuggestionsLedger] skip generated: cannot resolve ledger path skill=%s",
            name,
        )
        return

    timestamp = getattr(record, "timestamp", None)
    if not isinstance(timestamp, str) or not timestamp.strip():
        timestamp = _now_iso()
    item = {
        "id": record_id,
        "summary": improvement_summary_from_record(record),
        "timestamp": timestamp,
    }

    last_error: Optional[BaseException] = None
    for attempt in range(_MAX_RETRIES):
        try:
            ledger = _read_ledger(path)
            bucket = _ensure_bucket(ledger, name)
            _upsert_generated(bucket, item)
            _atomic_write(path, ledger)
            return
        except BaseException as exc:
            last_error = exc
            time.sleep(0.02 * (attempt + 1))

    logger.warning(
        "[EvolutionSuggestionsLedger] failed to record generated skill=%s id=%s err=%s",
        name,
        record_id,
        last_error,
    )
