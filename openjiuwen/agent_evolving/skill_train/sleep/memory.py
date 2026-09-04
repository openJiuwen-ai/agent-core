# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Protected learned-region edits for sleep skill/memory documents."""

from __future__ import annotations

import re
from typing import List, Tuple

from openjiuwen.agent_evolving.skill_train.sleep.types import EditRecord

LEARNED_START = "<!-- SKILL-TRAIN-SLEEP:LEARNED START -->"
LEARNED_END = "<!-- SKILL-TRAIN-SLEEP:LEARNED END -->"
_BANNER = (
    "_This block is maintained by skill_train sleep. Edits here are proposed "
    "offline, validated against harvested trajectories, and adopted only after "
    "you approve them. Hand-edits outside this block are never touched._"
)


def extract_learned(doc: str) -> str:
    start = doc.find(LEARNED_START)
    end = doc.find(LEARNED_END)
    if start == -1 or end == -1:
        return ""
    return doc[start + len(LEARNED_START):end].strip()


def _strip_learned(doc: str) -> str:
    while True:
        start = doc.find(LEARNED_START)
        if start == -1:
            break
        end = doc.find(LEARNED_END, start)
        if end == -1:
            doc = doc[:start]
            break
        doc = doc[:start] + doc[end + len(LEARNED_END):]
    while "\n\n\n" in doc:
        doc = doc.replace("\n\n\n", "\n\n")
    return doc.rstrip()


def set_learned(doc: str, learned_lines: List[str]) -> str:
    base = _strip_learned(doc)
    body = "\n".join(
        f"- {line.strip().lstrip('- ').strip()}"
        for line in learned_lines
        if line.strip()
    )
    block = (
        f"\n\n{LEARNED_START}\n"
        f"## Learned preferences & procedures\n\n{_BANNER}\n\n{body}\n"
        f"{LEARNED_END}\n"
    )
    return (base + block).lstrip("\n")


def current_learned_lines(doc: str) -> List[str]:
    lines: List[str] = []
    for line in extract_learned(doc).splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            lines.append(stripped[2:].strip())
    return lines


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def apply_edits_detailed(
    doc: str,
    edits: List[EditRecord],
) -> Tuple[str, List[EditRecord], List[EditRecord]]:
    lines = current_learned_lines(doc)
    norm_set = {_norm(line) for line in lines}
    applied: List[EditRecord] = []
    unmatched: List[EditRecord] = []

    for edit in edits:
        op = (edit.op or "add").lower()
        if op == "add":
            if _norm(edit.content) in norm_set or not edit.content.strip():
                unmatched.append(edit)
                continue
            lines.append(edit.content.strip())
            norm_set.add(_norm(edit.content))
            applied.append(edit)
        elif op == "delete":
            anchor = _norm(edit.anchor or edit.content)
            if not anchor:
                unmatched.append(edit)
                continue
            keep = [line for line in lines if anchor not in _norm(line)]
            if len(keep) != len(lines):
                lines = keep
                norm_set = {_norm(line) for line in lines}
                applied.append(edit)
            else:
                unmatched.append(edit)
        elif op == "replace":
            anchor = _norm(edit.anchor)
            replacement = edit.content.strip()
            new_lines: List[str] = []
            changed = False
            for line in lines:
                if anchor and anchor in _norm(line):
                    new_lines.append(replacement)
                    changed = changed or replacement != line
                else:
                    new_lines.append(line)
            if changed:
                lines = new_lines
                norm_set = {_norm(line) for line in lines}
                applied.append(edit)
            else:
                unmatched.append(edit)
        else:
            unmatched.append(edit)

    return set_learned(doc, lines), applied, unmatched


def ensure_skill_scaffold(doc: str, *, name: str, description: str) -> str:
    if doc.lstrip().startswith("---"):
        return doc
    frontmatter = (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "version: 1.0.0\n"
        "---\n\n"
        f"# {name}\n\n"
        "Preferences and procedures learned from offline sleep consolidation.\n"
    )
    return frontmatter + doc
