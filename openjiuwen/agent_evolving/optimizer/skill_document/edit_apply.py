# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Document-level text edit operations for skill documents.

The Update stage of the ReflACT pipeline: apply a ranked set of edits
to the current skill document, producing an updated candidate.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_evolving.optimizer.skill_document.types import Edit, Patch

SLOW_UPDATE_START = "<!-- SLOW_UPDATE_START -->"
SLOW_UPDATE_END = "<!-- SLOW_UPDATE_END -->"


def _is_in_slow_update_region(skill: str, target: str) -> bool:
    """Check if *any* occurrence of target text falls within the protected region."""
    start_idx = skill.find(SLOW_UPDATE_START)
    end_idx = skill.find(SLOW_UPDATE_END)
    if start_idx == -1 or end_idx == -1:
        return False
    region_end = end_idx + len(SLOW_UPDATE_END)
    # Check every occurrence — if any falls inside the protected zone, block it.
    search_from = 0
    while True:
        target_idx = skill.find(target, search_from)
        if target_idx == -1:
            return False
        if start_idx <= target_idx < region_end:
            return True
        search_from = target_idx + 1


def _strip_slow_update_markers(text: str) -> str:
    """Remove any SLOW_UPDATE markers from edit content to prevent duplication."""
    return text.replace(SLOW_UPDATE_START, "").replace(SLOW_UPDATE_END, "")


def _edit_fields(edit: Edit | dict[str, Any]) -> tuple[str, str, str]:
    """Extract (op, content, target) from an Edit dataclass or plain dict."""
    op = edit.op if hasattr(edit, "op") else edit.get("op", "")
    content = _strip_slow_update_markers(
        str(edit.content if hasattr(edit, "content") else edit.get("content", "")).strip()
    )
    target = edit.target if hasattr(edit, "target") else edit.get("target", "")
    return op, content, target


def _apply_edit_with_report(skill: str, edit: Edit | dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Apply one edit and return the updated skill plus a status report dict."""
    op, content, target = _edit_fields(edit)
    report: dict[str, Any] = {
        "op": op,
        "target": target[:200],
        "content_preview": content[:200],
        "status": "unknown",
    }

    # Layer 1: target-based skip for protected region
    if target and _is_in_slow_update_region(skill, target):
        report["status"] = "skipped_protected_slow_update_region"
        return skill, report

    if op == "append":
        su_start = skill.find(SLOW_UPDATE_START)
        if su_start != -1:
            before = skill[:su_start].rstrip()
            after = skill[su_start:]
            report["status"] = "applied_append_before_slow_update"
            return before + "\n\n" + content + "\n\n" + after, report
        report["status"] = "applied_append"
        return skill.rstrip() + "\n\n" + content + "\n", report

    if op == "insert_after":
        if not target or target not in skill:
            su_start = skill.find(SLOW_UPDATE_START)
            if su_start != -1:
                before = skill[:su_start].rstrip()
                after = skill[su_start:]
                report["status"] = "applied_insert_after_fallback_before_slow_update"
                return before + "\n\n" + content + "\n\n" + after, report
            report["status"] = "applied_insert_after_fallback_append"
            return skill.rstrip() + "\n\n" + content + "\n", report
        idx = skill.index(target) + len(target)
        newline = skill.find("\n", idx)
        insert_at = newline + 1 if newline != -1 else len(skill)
        report["status"] = "applied_insert_after"
        return skill[:insert_at] + "\n" + content + "\n" + skill[insert_at:], report

    if op == "replace":
        if not target:
            report["status"] = "skipped_replace_missing_target"
            return skill, report
        if target not in skill:
            report["status"] = "skipped_replace_target_not_found"
            return skill, report
        report["status"] = "applied_replace"
        return skill.replace(target, content, 1), report

    if op == "delete":
        if not target:
            report["status"] = "skipped_delete_missing_target"
            return skill, report
        if target not in skill:
            report["status"] = "skipped_delete_target_not_found"
            return skill, report
        report["status"] = "applied_delete"
        return skill.replace(target, "", 1), report

    report["status"] = "skipped_unknown_op"
    return skill, report


def apply_edit(skill: str, edit: Edit | dict[str, Any]) -> str:
    """Apply a single edit operation to the skill document.

    Edits targeting the protected slow-update region are silently skipped.
    """
    updated_skill, _ = _apply_edit_with_report(skill, edit)
    return updated_skill


def apply_patch_with_report(
    skill: str,
    patch: Patch | dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    """Apply a patch and return the updated skill plus a per-edit report."""
    edits = patch.edits if hasattr(patch, "edits") else patch.get("edits", [])
    reports: list[dict[str, Any]] = []
    for idx, edit in enumerate(edits, 1):
        try:
            skill, report = _apply_edit_with_report(skill, edit)
            report["index"] = idx
        except Exception as exc:
            report = {
                "index": idx,
                "op": "",
                "target": "",
                "content_preview": "",
                "status": "error",
                "error": str(exc),
            }
        reports.append(report)
    return skill, reports


def apply_patch(skill: str, patch: Patch | dict[str, Any]) -> str:
    """Apply a patch (list of edits) to the skill document sequentially."""
    updated_skill, _ = apply_patch_with_report(skill, patch)
    return updated_skill


def replace_slow_update_field(skill: str, content: str) -> str:
    """Force-inject content into the slow_update protected region.

    If SLOW_UPDATE markers exist, replace content between them.
    If markers don't exist, append them with content at the end.
    This bypasses normal edit protection — only authorized for
    epoch-level slow_update.
    """
    start_idx = skill.find(SLOW_UPDATE_START)
    end_idx = skill.find(SLOW_UPDATE_END)

    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        # Replace content between existing markers
        before = skill[: start_idx + len(SLOW_UPDATE_START)]
        after = skill[end_idx:]
        return f"{before}\n{content}\n{after}"

    # No markers — append at the end
    return f"{skill.rstrip()}\n\n{SLOW_UPDATE_START}\n{content}\n{SLOW_UPDATE_END}\n"


def extract_slow_update_content(skill: str) -> str:
    """Extract the current content between SLOW_UPDATE markers.

    Returns empty string if markers don't exist or region is empty.
    """
    start_idx = skill.find(SLOW_UPDATE_START)
    end_idx = skill.find(SLOW_UPDATE_END)
    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        return ""
    content_start = start_idx + len(SLOW_UPDATE_START)
    return skill[content_start:end_idx].strip()
