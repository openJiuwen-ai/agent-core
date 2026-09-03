# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Translate single-Harness state into the shared RSI event vocabulary."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

from openjiuwen.rsi.events import EventNode, EventProgress
from openjiuwen.rsi.schema import RsiChange, RsiTreeNode

_PROVISIONAL_STATUSES = {"provisional"}


def progress_event(
    state: Mapping[str, Any],
    *,
    total_iterations: int,
) -> EventProgress:
    """Build the latest durable metric snapshot."""

    return EventProgress(
        iteration=len(_mapping_items(state.get("candidate_gates"))),
        total_iterations=max(0, int(total_iterations)),
        score=_number(state.get("best_score")),
        baseline=_number(state.get("baseline_score")),
        usage=None,
    )


def node_event(
    candidate: Mapping[str, Any],
    *,
    iteration: int,
    parent_id: str | None,
) -> EventNode:
    """Build a generic node snapshot from one persisted candidate record."""

    status = str(candidate.get("status", "") or "").strip().lower()
    adopted = bool(candidate.get("accepted")) and status == "accepted"
    node_type = "ADOPTED" if adopted else "PROVISIONAL" if status in _PROVISIONAL_STATUSES else "REJECTED"
    changes = _changes(candidate.get("capabilities"))
    reason = _public_text(str(candidate.get("reason", "") or "").strip()) or None
    artifact_path = str(candidate.get("candidate_harness_refs_path", "") or "").strip()
    return EventNode(
        node=RsiTreeNode(
            node_id=_node_id(candidate, iteration=iteration),
            iteration=iteration,
            parent_id=parent_id,
            type=node_type,
            adopted=adopted,
            score=_number(candidate.get("candidate_score")),
            summary=_summary(changes, reason),
            snapshot_artifact_id=None,
            reason=None if adopted else reason,
            failure_class=(
                str(candidate.get("failure_class") or candidate.get("causal_failure_class") or "").strip() or None
            ),
            changes=changes,
            extra={"artifact_path": artifact_path} if artifact_path else {},
        )
    )


def parent_node_id(
    candidate: Mapping[str, Any],
    persisted_candidates: Sequence[Mapping[str, Any]],
) -> str | None:
    """Resolve the latest candidate whose output is this candidate's parent."""

    parent_artifact = str(candidate.get("before_harness_refs_path", "") or "").strip()
    if not parent_artifact:
        return None
    for index in range(len(persisted_candidates) - 1, -1, -1):
        prior = persisted_candidates[index]
        prior_artifact = str(prior.get("candidate_harness_refs_path", "") or "").strip()
        if prior_artifact and prior_artifact == parent_artifact:
            return _node_id(prior, iteration=index + 1)
    return None


def _node_id(candidate: Mapping[str, Any], *, iteration: int) -> str:
    explicit = str(candidate.get("candidate_id", "") or "").strip()
    if explicit:
        return explicit
    stable_parts = (
        str(candidate.get("member_optimization_ref_path", "") or ""),
        str(candidate.get("candidate_harness_refs_path", "") or ""),
        str(iteration),
    )
    digest = hashlib.sha256("\0".join(stable_parts).encode("utf-8")).hexdigest()
    return f"candidate-{digest[:16]}"


def _changes(raw_capabilities: Any) -> list[RsiChange]:
    changes: list[RsiChange] = []
    for capability in raw_capabilities if isinstance(raw_capabilities, list) else []:
        if not isinstance(capability, Mapping):
            continue
        summary = str(
            capability.get("expected_effect")
            or capability.get("description")
            or capability.get("rationale")
            or capability.get("purpose")
            or ""
        ).strip()
        changes.append(
            RsiChange(
                group=str(capability.get("action_group", "") or "").strip().upper(),
                operation=str(capability.get("operation", "") or "").strip().upper(),
                function=(str(capability.get("function", "") or "").strip() or None),
                target=(
                    str(
                        capability.get("target_path") or capability.get("target_ref") or capability.get("target") or ""
                    ).strip()
                    or None
                ),
                summary=_public_text(summary),
            )
        )
    return changes


def _summary(changes: list[RsiChange], reason: str | None) -> str | None:
    descriptions = [change.summary for change in changes if change.summary]
    return "; ".join(descriptions[:3]) if descriptions else reason


def _public_text(value: str) -> str:
    """Remove control-plane vocabulary from user-facing event text."""

    text = value.replace("_", " ")
    replacements = {
        r"\bgates?\b": "reviews",
        r"\bepochs?\b": "cycles",
        r"\bbatches?\b": "case groups",
        r"\battempts?\b": "proposals",
        r"\bcheckpoints?\b": "reviews",
    }
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _mapping_items(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["node_event", "parent_node_id", "progress_event"]
