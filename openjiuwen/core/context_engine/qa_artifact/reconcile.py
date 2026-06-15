# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.context.session_memory_manager import get_context_message_id
from openjiuwen.core.context_engine.observability import write_context_trace
from openjiuwen.core.context_engine.qa_artifact.schema import QAArtifactState
from openjiuwen.core.context_engine.qa_artifact.store import qa_artifact_paths
from openjiuwen.core.foundation.llm import BaseMessage

if TYPE_CHECKING:
    from openjiuwen.core.context_engine.qa_artifact.store import QAArtifactStore


def _has_artifact_products(state: QAArtifactState) -> bool:
    return state.products_ready or state.state in ("STAGED", "COMPACTED")


async def disk_artifacts_ok_at_paths(
    store: QAArtifactStore,
    *,
    overview_path: str,
    catalog_path: str,
) -> bool:
    overview = (await store.read_text(overview_path)).strip()
    if overview:
        return True
    catalog_text = (await store.read_text(catalog_path)).strip()
    if not catalog_text:
        return False
    try:
        payload = json.loads(catalog_text)
    except json.JSONDecodeError:
        return False
    entries = payload.get("entries") if isinstance(payload, dict) else None
    return bool(entries)


def _infer_covers_upto(l0_messages: list[BaseMessage] | None) -> str | None:
    if not l0_messages:
        return None
    return get_context_message_id(l0_messages[-1])


async def reconcile_artifact_state_from_disk(
    store: QAArtifactStore,
    qa_id: str,
    *,
    l0_messages: list[BaseMessage] | None = None,
    for_history: bool = True,
) -> tuple[QAArtifactState | None, str]:
    """Align ``__qa_memory__`` with on-disk overview/catalog before hydrate.

    When checkpoint ``pre_run`` drops in-memory artifact state but disk files exist,
    upgrade or create session state so compact hydrate can run without raw L0 replay.
    """
    existing = store.load(qa_id)
    if existing is not None and existing.is_extracting:
        return existing, "is_extracting"

    paths = qa_artifact_paths(store.workspace_root, store.session_id, qa_id)
    if not await disk_artifacts_ok_at_paths(
        store,
        overview_path=paths["overview_path"],
        catalog_path=paths["catalog_path"],
    ):
        return existing, "no_disk"

    state_before = existing.state if existing is not None else None
    if existing is None:
        state = QAArtifactState(
            overview_path=paths["overview_path"],
            catalog_path=paths["catalog_path"],
            pending_path=paths["pending_path"],
            state="COMPACTED" if for_history else "STAGED",
        )
        reason = "reconciled_from_disk"
    elif existing.state == "RAW" and not existing.products_ready:
        state = existing
        if for_history:
            state.state = "COMPACTED"
        reason = "upgraded_from_raw"
    elif _has_artifact_products(existing):
        state = existing
        reason = "state_ok"
    else:
        state = existing
        if for_history:
            state.state = "COMPACTED"
        reason = "upgraded_to_compacted"

    if state.covers_upto_message_id is None:
        covers = _infer_covers_upto(l0_messages)
        if covers is not None:
            state.covers_upto_message_id = covers

    store.save(qa_id, state)
    logger.info(
        "[QAArtifactReconcile] qa_id=%s session_id=%s reason=%s state_before=%s state_after=%s",
        qa_id,
        store.session_id,
        reason,
        state_before,
        state.state,
    )
    write_context_trace(
        "qa_artifact.reconcile_from_disk",
        {
            "qa_id": qa_id,
            "session_id": store.session_id,
            "reason": reason,
            "state_before": state_before,
            "state_after": state.state,
            "covers_upto": state.covers_upto_message_id,
        },
    )
    return state, reason
