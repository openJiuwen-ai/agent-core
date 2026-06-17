# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared helpers for online experience lifecycle orchestration."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from openjiuwen.agent_evolving.checkpointing.store_records import MergeRecordsRequest
from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord
from openjiuwen.agent_evolving.experience.lifecycle import PendingCommitResult
from openjiuwen.agent_evolving.experience.types import ExperienceApplyResult, PendingChange
from openjiuwen.agent_evolving.protocols import EXPERIENCE_ENTRY, SKILL_EXPERIENCE_ENTRY
from openjiuwen.core.common.logging import logger


def make_pending_change(
    skill_name: str,
    records: List[EvolutionRecord],
    *,
    request_id_prefix: Optional[str] = None,
    subject_kind: Optional[str] = None,
    trajectory: Any | None = None,
    messages: Optional[List[dict]] = None,
    is_shared_records: bool = False,
) -> PendingChange:
    """Build a checkpointing snapshot for staged experience approval."""
    pending = PendingChange.make(
        skill_name,
        records,
        subject_kind=subject_kind,
        trajectory=trajectory,
        messages=messages,
    )
    pending.is_shared_records = is_shared_records
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
    approved_record_ids: Optional[List[str]] = None,
) -> PendingCommitResult:
    """Persist one staged pending change one record at a time.

    Each individual record write is atomic at the store layer. The approval
    batch may be partially applied; on failure the unwritten approved tail is
    retained for retry.
    """
    pending = pending_by_id.get(change_id)
    if pending is None:
        raise KeyError(change_id)

    if pending.change_type not in {SKILL_EXPERIENCE_ENTRY, EXPERIENCE_ENTRY}:
        raise KeyError(pending.change_type)

    all_records = list(pending.payload)
    if approved_record_ids is None:
        records = all_records
        rejected_records: List[EvolutionRecord] = []
    else:
        approved_ids = set(approved_record_ids)
        records = [record for record in all_records if record.id in approved_ids]
        rejected_records = [record for record in all_records if record.id not in approved_ids]
    errors: List[str] = []

    if not records:
        pending.payload[:] = []
        pending_by_id.pop(change_id, None)
        return PendingCommitResult(
            applied_count=0,
            pending_count=0,
            rejected_count=len(rejected_records),
        )

    applied_count = 0
    remaining_records = list(records)

    for index, record in enumerate(records):
        try:
            await store.append_record(pending.skill_name, record, subject_kind=pending.subject_kind)
        except Exception as exc:
            errors.append(str(exc))
            remaining_records = list(records[index:])
            pending.payload[:] = remaining_records
            return PendingCommitResult(
                applied_count=applied_count,
                pending_count=len(remaining_records),
                rejected_count=len(rejected_records),
                errors=errors,
            )
        applied_count += 1
    else:
        remaining_records = []

    pending.payload[:] = remaining_records
    if not remaining_records:
        pending_by_id.pop(change_id, None)

    return PendingCommitResult(
        applied_count=applied_count,
        pending_count=len(remaining_records),
        rejected_count=len(rejected_records),
        errors=errors,
    )


async def execute_simplify_actions(
    store: Any,
    skill_name: str,
    actions: List[Dict[str, Any]],
    *,
    subject_kind: Optional[str] = None,
) -> Dict[str, int]:
    """Execute simplify actions against the evolution store."""
    counts = {"deleted": 0, "merged": 0, "refined": 0, "kept": 0, "errors": 0}

    for action in actions:
        action_type = action.get("action", "KEEP")
        record_id = action.get("record_id", "")

        try:
            if action_type == "DELETE":
                deleted = await store.delete_records(skill_name, [record_id], subject_kind=subject_kind)
                if deleted > 0:
                    counts["deleted"] += 1
                else:
                    counts["errors"] += 1

            elif action_type == "MERGE":
                result = await store.merge_records(
                    MergeRecordsRequest(
                        name=skill_name,
                        primary_id=record_id,
                        remove_ids=action.get("merge_remove_ids", []),
                        new_content=action.get("new_content", ""),
                        subject_kind=subject_kind,
                    )
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
                    subject_kind=subject_kind,
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
