# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared helpers for online experience lifecycle orchestration."""

from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List, Optional

from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord
from openjiuwen.agent_evolving.experience.types import PendingChange
from openjiuwen.agent_evolving.experience.lifecycle import PendingCommitResult, RebuildRequest
from openjiuwen.agent_evolving.experience.types import ExperienceApplyResult
from openjiuwen.agent_evolving.protocols import EXPERIENCE_ENTRY, SKILL_EXPERIENCE_ENTRY
from openjiuwen.core.common.logging import logger


def make_pending_change(
    skill_name: str,
    records: List[EvolutionRecord],
    *,
    request_id_prefix: Optional[str] = None,
) -> PendingChange:
    """Build a checkpointing snapshot for staged experience approval."""
    pending = PendingChange.make(skill_name, records)
    if request_id_prefix:
        pending.change_id = f"{request_id_prefix}_{uuid.uuid4().hex[:8]}"
    return pending


def reject_pending_change(pending: PendingChange) -> ExperienceApplyResult:
    """Build a rejection result without mutating persistent state."""
    return ExperienceApplyResult(
        skill_name=pending.skill_name,
        rejected_count=len(pending.payload),
    )


async def commit_pending_change(
    pending_by_id: Dict[str, PendingChange],
    change_id: str,
    *,
    store: Any,
) -> PendingCommitResult:
    """Persist one staged pending change, retaining the unwritten tail on failure."""
    pending = pending_by_id.get(change_id)
    if pending is None:
        raise KeyError(change_id)

    if pending.change_type not in {SKILL_EXPERIENCE_ENTRY, EXPERIENCE_ENTRY}:
        raise KeyError(pending.change_type)

    applied_count = 0
    remaining_records = list(pending.payload)

    for index, record in enumerate(list(pending.payload)):
        try:
            await store.append_record(pending.skill_name, record)
        except Exception:
            remaining_records = list(pending.payload[index:])
            break
        applied_count += 1
    else:
        remaining_records = []

    pending.payload[:] = remaining_records
    if not remaining_records:
        pending_by_id.pop(change_id, None)

    return PendingCommitResult(
        applied_count=applied_count,
        pending_count=len(remaining_records),
    )


async def execute_simplify_actions(
    store: Any,
    skill_name: str,
    actions: List[Dict[str, Any]],
) -> Dict[str, int]:
    """Execute simplify actions against the evolution store."""
    counts = {"deleted": 0, "merged": 0, "refined": 0, "kept": 0, "errors": 0}

    for action in actions:
        action_type = action.get("action", "KEEP")
        record_id = action.get("record_id", "")

        try:
            if action_type == "DELETE":
                deleted = await store.delete_records(skill_name, [record_id])
                if deleted > 0:
                    counts["deleted"] += 1
                else:
                    counts["errors"] += 1

            elif action_type == "MERGE":
                result = await store.merge_records(
                    skill_name,
                    record_id,
                    action.get("merge_remove_ids", []),
                    action.get("new_content", ""),
                )
                if result:
                    counts["merged"] += 1
                else:
                    counts["errors"] += 1

            elif action_type == "REFINE":
                result = await store.update_record_content(
                    skill_name,
                    record_id,
                    action.get("new_content", ""),
                )
                if result:
                    counts["refined"] += 1
                else:
                    counts["errors"] += 1

            elif action_type == "KEEP":
                counts["kept"] += 1

            else:
                logger.warning("[experience.common] unknown action type: %s", action_type)
                counts["errors"] += 1

        except Exception as exc:
            logger.error(
                "[experience.common] execute action %s failed for %s: %s",
                action_type,
                record_id,
                exc,
            )
            counts["errors"] += 1

    logger.info(
        "[experience.common] executed simplify actions for skill=%s: %s",
        skill_name,
        counts,
    )
    return counts


async def request_rebuild_context(
    store: Any,
    request: RebuildRequest,
    *,
    format_records: Callable[[List[EvolutionRecord]], str],
    default_intent: str,
    template: str,
    archive_evolutions_on_success: bool = True,
) -> Optional[Dict[str, Any]]:
    """Archive current state, filter rebuild inputs, and build rebuild prompt text."""
    if not store.skill_exists(request.skill_name):
        return None

    evo_archive: Optional[str] = None
    archive_error: Optional[Exception] = None

    try:
        await store.archive_skill_body(request.skill_name)
    except Exception as exc:  # pragma: no cover - logging only
        logger.warning("[experience.common] skill body archive failed for '%s': %s", request.skill_name, exc)

    try:
        evo_archive = await store.archive_evolutions(request.skill_name)
    except Exception as exc:
        archive_error = exc
        logger.warning("[experience.common] evolutions archive failed for '%s': %s", request.skill_name, exc)

    records_log = await store.load_full_evolution_log(request.skill_name)
    filtered_records: List[EvolutionRecord] = []
    for record in records_log.entries:
        if getattr(record, "score", 0.0) < request.min_score:
            continue
        change = getattr(record, "change", None)
        if getattr(change, "skip_reason", None):
            continue
        filtered_records.append(record)

    prompt = template.format(
        evolution_records=format_records(filtered_records),
        user_intent=request.user_intent or default_intent,
        min_score=request.min_score,
    )

    if archive_evolutions_on_success and evo_archive:
        await store.clear_evolutions(request.skill_name)

    return {
        "skill_name": request.skill_name,
        "records_log": records_log,
        "filtered_records": filtered_records,
        "prompt": prompt,
        "archive_path": evo_archive,
        "archive_error": archive_error,
    }
