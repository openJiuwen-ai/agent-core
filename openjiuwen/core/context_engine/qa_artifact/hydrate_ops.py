# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.qa_artifact.catalog import CatalogBuilder
from openjiuwen.core.context_engine.qa_artifact.reconcile import reconcile_artifact_state_from_disk
from openjiuwen.core.context_engine.qa_artifact.schema import QAArtifacts, QAArtifactState
from openjiuwen.core.context_engine.qa_artifact.window import (
    compact_replacement_message,
    render_artifact_text,
    tail_after,
)
from openjiuwen.core.context_engine.qa_block.messages import load_qa_l0, tag_messages_with_qa_id
from openjiuwen.core.foundation.llm import BaseMessage

if TYPE_CHECKING:
    from openjiuwen.core.context_engine.qa_artifact.store import QAArtifactStore
    from openjiuwen.core.context_engine.qa_block.config import QABlockConfig
    from openjiuwen.core.context_engine.qa_block.history_buffer import HistoryQABuffer
    from openjiuwen.core.context_engine.qa_block.store import QABlockStore

HydrateMode = Literal["compact", "raw"]


def has_artifact_products(state: QAArtifactState) -> bool:
    return state.products_ready or state.state in ("STAGED", "COMPACTED")


async def disk_artifacts_ok(store: QAArtifactStore, state: QAArtifactState) -> bool:
    overview = (await store.read_text(state.overview_path)).strip()
    if overview:
        return True
    catalog_text = (await store.read_text(state.catalog_path)).strip()
    if not catalog_text:
        return False
    try:
        json.loads(catalog_text)
    except json.JSONDecodeError:
        return False
    return True


async def read_artifacts(store: QAArtifactStore, state: QAArtifactState) -> QAArtifacts:
    overview = await store.read_text(state.overview_path)
    catalog_text = await store.read_text(state.catalog_path)
    entries = CatalogBuilder.load_entries(catalog_text)
    return QAArtifacts(overview=overview, entries=entries)


async def resolve_hydrate_mode(
    qa_id: str,
    *,
    artifact_store: QAArtifactStore | None,
    config: QABlockConfig | None,
    l0_messages: list[BaseMessage] | None = None,
    reconciled: bool = False,
) -> tuple[HydrateMode, str]:
    cfg = config
    if cfg is not None and not cfg.hydrate_artifact_aware:
        return "raw", "hydrate_artifact_aware_disabled"
    if artifact_store is None:
        return "raw", "no_artifact_store"

    state = artifact_store.load(qa_id)
    if state is None and not reconciled:
        state, reconcile_reason = await reconcile_artifact_state_from_disk(
            artifact_store,
            qa_id,
            l0_messages=l0_messages,
            for_history=True,
        )
        if state is None:
            return "raw", reconcile_reason
    elif state is None:
        return "raw", "no_state"

    if state.is_extracting:
        return "raw", "is_extracting"
    if not has_artifact_products(state):
        return "raw", "no_products"
    if not await disk_artifacts_ok(artifact_store, state):
        logger.warning(
            "[QAHydrateOps] compact hydrate skipped, disk miss qa_id=%s state=%s",
            qa_id,
            state.state,
        )
        return "raw", "disk_miss"
    logger.info(
        "[QAHydrateOps] compact hydrate qa_id=%s state=%s products_ready=%s",
        qa_id,
        state.state,
        state.products_ready,
    )
    return "compact", "ok"


async def build_compact_hydrate_messages(
    qa_id: str,
    *,
    artifact_store: QAArtifactStore,
    l0_store: QABlockStore,
    history_buffer: HistoryQABuffer,
    cached_l0: list[BaseMessage] | None = None,
) -> list[BaseMessage]:
    state = artifact_store.load(qa_id)
    if state is None:
        state, _ = await reconcile_artifact_state_from_disk(
            artifact_store,
            qa_id,
            l0_messages=cached_l0,
            for_history=True,
        )
    if state is None:
        return []

    artifacts = await read_artifacts(artifact_store, state)
    text = render_artifact_text(qa_id, artifacts)
    compact = compact_replacement_message(qa_id, text)

    raw = cached_l0 if cached_l0 is not None else await load_qa_l0(qa_id, history_buffer, l0_store)
    tail = tail_after(raw, state.covers_upto_message_id)
    if not tail:
        return [compact]

    return [compact, *tag_messages_with_qa_id(tail, qa_id)]
