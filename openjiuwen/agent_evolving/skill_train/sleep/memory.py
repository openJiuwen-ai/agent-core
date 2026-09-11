# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bounded learned-region edits for sleep skill / memory documents."""

from __future__ import annotations

import re
from typing import Callable, List, Tuple

from openjiuwen.agent_evolving.skill_train.sleep.types import EditRecord

_MARK_OPEN = "<!-- SKILL-TRAIN-SLEEP:LEARNED START -->"
_MARK_CLOSE = "<!-- SKILL-TRAIN-SLEEP:LEARNED END -->"
# Back-compat aliases used by unit tests and callers.
LEARNED_START = _MARK_OPEN
LEARNED_END = _MARK_CLOSE
_NOTE = (
    "_This block is maintained by skill_train sleep. Edits here are proposed "
    "offline, validated against harvested trajectories, and adopted only after "
    "you approve them. Hand-edits outside this block are never touched._"
)


def extract_learned(doc: str) -> str:
    left = doc.find(_MARK_OPEN)
    right = doc.find(_MARK_CLOSE)
    if left < 0 or right < 0:
        return ""
    begin = left + len(_MARK_OPEN)
    return doc[begin:right].strip()


def _remove_regions(doc: str) -> str:
    text = doc
    while True:
        left = text.find(_MARK_OPEN)
        if left < 0:
            break
        right = text.find(_MARK_CLOSE, left)
        if right < 0:
            text = text[:left]
            break
        cut = right + len(_MARK_CLOSE)
        text = text[:left] + text[cut:]
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.rstrip()


def set_learned(doc: str, learned_lines: List[str]) -> str:
    base = _remove_regions(doc)
    bullets = []
    for line in learned_lines:
        cleaned = line.strip().lstrip("- ").strip()
        if cleaned:
            bullets.append(f"- {cleaned}")
    body = "\n".join(bullets)
    region = (
        f"\n\n{_MARK_OPEN}\n## Learned preferences & procedures\n\n{_NOTE}\n\n"
        f"{body}\n{_MARK_CLOSE}\n"
    )
    return (base + region).lstrip("\n")


def current_learned_lines(doc: str) -> List[str]:
    out: List[str] = []
    for line in extract_learned(doc).splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            out.append(stripped[2:].strip())
    return out


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _apply_add(
    lines: List[str],
    norms: set[str],
    edit: EditRecord,
) -> Tuple[bool, List[str], set[str]]:
    folded = _fold(edit.content)
    if folded in norms or not edit.content.strip():
        return False, lines, norms
    updated = list(lines)
    updated.append(edit.content.strip())
    norms = set(norms)
    norms.add(folded)
    return True, updated, norms


def _apply_delete(
    lines: List[str],
    norms: set[str],
    edit: EditRecord,
) -> Tuple[bool, List[str], set[str]]:
    anchor = _fold(edit.anchor or edit.content)
    if not anchor:
        return False, lines, norms
    kept = [line for line in lines if anchor not in _fold(line)]
    if len(kept) == len(lines):
        return False, lines, norms
    return True, kept, {_fold(line) for line in kept}


def _apply_replace(
    lines: List[str],
    norms: set[str],
    edit: EditRecord,
) -> Tuple[bool, List[str], set[str]]:
    del norms
    anchor = _fold(edit.anchor)
    replacement = edit.content.strip()
    rebuilt: List[str] = []
    touched = False
    for line in lines:
        if anchor and anchor in _fold(line):
            rebuilt.append(replacement)
            touched = touched or replacement != line
        else:
            rebuilt.append(line)
    if not touched:
        return False, lines, {_fold(line) for line in lines}
    return True, rebuilt, {_fold(line) for line in rebuilt}


_OPS: dict[str, Callable[[List[str], set[str], EditRecord], Tuple[bool, List[str], set[str]]]] = {
    "add": _apply_add,
    "delete": _apply_delete,
    "replace": _apply_replace,
}


def apply_edits_detailed(
    doc: str,
    edits: List[EditRecord],
) -> Tuple[str, List[EditRecord], List[EditRecord]]:
    lines = current_learned_lines(doc)
    norms = {_fold(line) for line in lines}
    applied: List[EditRecord] = []
    unmatched: List[EditRecord] = []

    for edit in edits:
        handler = _OPS.get((edit.op or "add").lower())
        if handler is None:
            unmatched.append(edit)
            continue
        ok, lines, norms = handler(lines, norms, edit)
        if ok:
            applied.append(edit)
        else:
            unmatched.append(edit)

    return set_learned(doc, lines), applied, unmatched


def ensure_skill_scaffold(doc: str, *, name: str, description: str) -> str:
    if doc.lstrip().startswith("---"):
        return doc
    header = (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "version: 1.0.0\n"
        "---\n\n"
        f"# {name}\n\n"
        "Preferences and procedures learned from offline sleep consolidation.\n"
    )
    return header + doc
