# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Map sleep EditRecords onto EvolutionRecords for SemVer / changelog."""

from __future__ import annotations

from typing import Any, Iterable, List, Mapping, Optional, Sequence

from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch, EvolutionRecord
from openjiuwen.agent_evolving.signal.base import EvolutionTarget
from openjiuwen.agent_evolving.skill_train.sleep.types import EditRecord

# Not in versioning._PATCH_SOURCES → Instructions edits classify as MINOR.
_SLEEP_SOURCE = "skill_train_sleep"
_DEFAULT_SECTION = "Instructions"

_OP_TO_ACTION = {
    "add": "append",
    "append": "append",
    "replace": "replace",
    "merge": "merge",
    "delete": "skip",
}


def _as_edit(raw: Any) -> Optional[EditRecord]:
    if isinstance(raw, EditRecord):
        return raw
    if isinstance(raw, Mapping):
        return EditRecord(
            target=str(raw.get("target") or "skill"),
            op=str(raw.get("op") or "").strip(),
            content=str(raw.get("content") or ""),
            anchor=str(raw.get("anchor") or ""),
            rationale=str(raw.get("rationale") or ""),
        )
    return None


def edit_record_to_evolution(
    edit: EditRecord,
    *,
    skill_name: str,
) -> EvolutionRecord:
    """Convert one sleep edit into an EvolutionRecord for rebuild finalize APIs."""
    op = (edit.op or "").strip().lower()
    action = _OP_TO_ACTION.get(op, "append")
    content = (edit.content or "").strip()
    rationale = (edit.rationale or "").strip()
    summary = rationale or (content.splitlines()[0].strip() if content else "skill_train sleep edit")
    context = f"skill_train sleep adopt skill={skill_name}"
    if edit.anchor:
        context = f"{context}; anchor={edit.anchor}"

    if action == "skip":
        change = EvolutionPatch(
            section=_DEFAULT_SECTION,
            action="skip",
            content=content or rationale or "deleted via sleep edit",
            target=EvolutionTarget.BODY,
            skip_reason=rationale or f"sleep edit op={op or 'delete'}",
        )
    else:
        change = EvolutionPatch(
            section=_DEFAULT_SECTION,
            action=action,
            content=content or summary,
            target=EvolutionTarget.BODY,
        )
    return EvolutionRecord.make(
        source=_SLEEP_SOURCE,
        context=context,
        change=change,
        summary=summary,
        root_cause=rationale or None,
    )


def consolidation_fallback_record(skill_name: str) -> EvolutionRecord:
    """One Instructions append so empty-edit adopts still bump MINOR + changelog."""
    return EvolutionRecord.make(
        source=_SLEEP_SOURCE,
        context=f"skill_train sleep consolidation skill={skill_name}",
        change=EvolutionPatch(
            section=_DEFAULT_SECTION,
            action="append",
            content="skill_train sleep consolidation",
            target=EvolutionTarget.BODY,
        ),
        summary="skill_train sleep consolidation",
        root_cause="accepted sleep skill update without discrete edits",
    )


def edits_to_evolution_records(
    edits: Sequence[Any] | None,
    *,
    skill_name: str,
) -> List[EvolutionRecord]:
    """Build EvolutionRecords from applied edits; always at least one active record."""
    records: List[EvolutionRecord] = []
    for raw in edits or ():
        edit = _as_edit(raw)
        if edit is None:
            continue
        if edit.target and edit.target not in {"skill", "body", ""}:
            # Memory-targeted edits do not drive skill SemVer / changelog.
            continue
        records.append(edit_record_to_evolution(edit, skill_name=skill_name))

    active = [r for r in records if not r.change.skip_reason]
    if not active:
        return [consolidation_fallback_record(skill_name)]
    return records


def edits_to_dicts(edits: Iterable[Any] | None) -> List[dict]:
    """Serialize EditRecords (or dicts) for staging manifest persistence."""
    out: List[dict] = []
    for raw in edits or ():
        edit = _as_edit(raw)
        if edit is None:
            continue
        out.append(
            {
                "target": edit.target,
                "op": edit.op,
                "content": edit.content,
                "anchor": edit.anchor,
                "rationale": edit.rationale,
            }
        )
    return out
