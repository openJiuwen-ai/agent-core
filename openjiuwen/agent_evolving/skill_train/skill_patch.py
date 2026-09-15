# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Apply structured edit batches to skill markdown documents.

Step-level analysts emit ordered edit operations; this module materializes
them into an updated skill body while keeping immutable tail sections intact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openjiuwen.agent_evolving.skill_train.types import Edit as EditType
    from openjiuwen.agent_evolving.skill_train.types import Patch as PatchType

SLOW_UPDATE_START = "<!-- SLOW_UPDATE_START -->"
SLOW_UPDATE_END = "<!-- SLOW_UPDATE_END -->"
APPENDIX_START = "<!-- APPENDIX_START -->"
APPENDIX_END = "<!-- APPENDIX_END -->"

_IMMUTABLE: tuple[tuple[str, str], ...] = (
    (SLOW_UPDATE_START, SLOW_UPDATE_END),
    (APPENDIX_START, APPENDIX_END),
)
_PREVIEW = 200


def _first_immutable_offset(skill: str) -> int:
    offsets = [skill.find(open_tag) for open_tag, _ in _IMMUTABLE]
    hits = [offset for offset in offsets if offset >= 0]
    return min(hits) if hits else -1


def _target_in_immutable(skill: str, target: str) -> bool:
    if not target:
        return False
    hit = skill.find(target)
    if hit < 0:
        return False
    for open_tag, close_tag in _IMMUTABLE:
        start = skill.find(open_tag)
        end = skill.find(close_tag)
        if start >= 0 and end >= 0 and start <= hit < end + len(close_tag):
            return True
    return False


def _scrub_markers(raw: str) -> str:
    cleaned = raw
    for open_tag, close_tag in _IMMUTABLE:
        cleaned = cleaned.replace(open_tag, "").replace(close_tag, "")
    return cleaned


def _unpack(edit: Any) -> tuple[str, str, str]:
    if isinstance(edit, dict):
        op = str(edit.get("op", ""))
        content = str(edit.get("content", ""))
        target = str(edit.get("target", ""))
    else:
        op = str(edit.op)
        content = str(edit.content)
        target = str(edit.target)
    return op, _scrub_markers(content.strip()), target


def _report(op: str, target: str, content: str) -> dict:
    return {
        "op": op,
        "target": target[:_PREVIEW],
        "content_preview": content[:_PREVIEW],
        "status": "unknown",
    }


def _insert_before_tail(
    skill: str,
    content: str,
    report: dict,
    *,
    with_tail: str,
    without_tail: str,
) -> tuple[str, dict]:
    split_at = _first_immutable_offset(skill)
    if split_at < 0:
        report["status"] = without_tail
        return f"{skill.rstrip()}\n\n{content}\n", report
    report["status"] = with_tail
    return f"{skill[:split_at].rstrip()}\n\n{content}\n\n{skill[split_at:]}", report


def _do_append(skill: str, content: str, report: dict) -> tuple[str, dict]:
    return _insert_before_tail(
        skill,
        content,
        report,
        with_tail="applied_append_before_protected_region",
        without_tail="applied_append",
    )


def _do_insert_after(skill: str, content: str, target: str, report: dict) -> tuple[str, dict]:
    if not target or target not in skill:
        return _insert_before_tail(
            skill,
            content,
            report,
            with_tail="applied_insert_after_fallback_before_protected_region",
            without_tail="applied_insert_after_fallback_append",
        )
    after = skill.index(target) + len(target)
    newline = skill.find("\n", after)
    at = newline + 1 if newline >= 0 else len(skill)
    report["status"] = "applied_insert_after"
    return f"{skill[:at]}\n{content}\n{skill[at:]}", report


def _mutate(
    skill: str,
    *,
    needle: str,
    replacement: str,
    report: dict,
    missing_status: str,
    absent_status: str,
    ok_status: str,
) -> tuple[str, dict]:
    if needle == "":
        report["status"] = missing_status
        return skill, report
    pos = skill.find(needle)
    if pos < 0:
        report["status"] = absent_status
        return skill, report
    report["status"] = ok_status
    return f"{skill[:pos]}{replacement}{skill[pos + len(needle):]}", report


def _do_replace(skill: str, content: str, target: str, report: dict) -> tuple[str, dict]:
    return _mutate(
        skill,
        needle=target,
        replacement=content,
        report=report,
        missing_status="skipped_replace_missing_target",
        absent_status="skipped_replace_target_not_found",
        ok_status="applied_replace",
    )


def _do_delete(skill: str, target: str, report: dict) -> tuple[str, dict]:
    return _mutate(
        skill,
        needle=target,
        replacement="",
        report=report,
        missing_status="skipped_delete_missing_target",
        absent_status="skipped_delete_target_not_found",
        ok_status="applied_delete",
    )


def _run_one(skill: str, edit: Any) -> tuple[str, dict]:
    op, content, target = _unpack(edit)
    report = _report(op, target, content)
    if target and _target_in_immutable(skill, target):
        report["status"] = "skipped_protected_region"
        return skill, report
    dispatch = {
        "append": lambda: _do_append(skill, content, report),
        "insert_after": lambda: _do_insert_after(skill, content, target, report),
        "replace": lambda: _do_replace(skill, content, target, report),
        "delete": lambda: _do_delete(skill, target, report),
    }
    handler = dispatch.get(op)
    if handler is None:
        report["status"] = "skipped_unknown_op"
        return skill, report
    return handler()


def apply_edit(skill: str, edit: EditType | dict) -> str:
    body, _status = _run_one(skill, edit)
    return body


def apply_patch_with_report(skill: str, patch: PatchType | dict) -> tuple[str, list[dict]]:
    edits = patch.edits if hasattr(patch, "edits") else patch.get("edits", [])
    reports: list[dict] = []
    body = skill
    for index, edit in enumerate(edits, 1):
        try:
            body, entry = _run_one(body, edit)
            entry["index"] = index
        except Exception as exc:  # noqa: BLE001
            entry = {
                "index": index,
                "op": "",
                "target": "",
                "content_preview": "",
                "status": "error",
                "error": str(exc),
            }
        reports.append(entry)
    return body, reports


def apply_patch(skill: str, patch: PatchType | dict) -> str:
    updated, _ = apply_patch_with_report(skill, patch)
    return updated
