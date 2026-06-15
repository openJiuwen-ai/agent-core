# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.observability import write_context_trace
from openjiuwen.core.context_engine.qa_block.config import QABlockConfig
from openjiuwen.core.context_engine.qa_block.messages import (
    estimate_messages_tokens,
    extract_final_answer,
    extract_user_query,
    had_full_compact_in_messages,
    message_id_range,
    truncate_excerpt,
)
from openjiuwen.core.context_engine.qa_block.schema import L0Store, QABlockEntry, QABlockRegistry

if TYPE_CHECKING:
    from openjiuwen.core.context_engine.qa_block.store import QABlockStore

_QA_ID_PATTERN = re.compile(r"^qa_(\d+)$")


def _qa_index_from_id(qa_id: str) -> int | None:
    match = _QA_ID_PATTERN.match(qa_id)
    if not match:
        return None
    return int(match.group(1))


def _infer_orphan_status(messages: list) -> str:
    if extract_final_answer(messages):
        return "completed"
    return "interrupted"


async def reconcile_orphan_l0_blocks(
    registry: QABlockRegistry,
    store: QABlockStore,
    *,
    config: QABlockConfig | None = None,
) -> tuple[QABlockRegistry, list[str]]:
    """Register on-disk L0 blocks that are missing from the checkpoint registry."""
    cfg = config or QABlockConfig()
    disk_ids = await store.list_l0_qa_ids()
    recovered: list[str] = []

    for qa_id in disk_ids:
        if qa_id in registry.blocks:
            continue
        if registry.current_qa_id == qa_id:
            continue

        qa_index = _qa_index_from_id(qa_id)
        if qa_index is None:
            logger.warning(
                "[QABlockReconcile] skip invalid qa_id on disk session_id=%s qa_id=%s",
                registry.session_id,
                qa_id,
            )
            continue

        messages = await store.read_l0(qa_id)
        if not messages:
            continue

        rel_path = store.l0_relative_path(qa_id)
        user_query = extract_user_query(messages)
        final_answer = extract_final_answer(messages)
        had_compact = had_full_compact_in_messages(messages)
        l0_mode = "compact_summary_tail" if had_compact else "delta"

        registry.blocks[qa_id] = QABlockEntry(
            qa_id=qa_id,
            qa_index=qa_index,
            status=_infer_orphan_status(messages),
            is_history=True,
            message_id_range=message_id_range(messages),
            message_count=len(messages),
            approx_tokens=estimate_messages_tokens(messages),
            user_query_excerpt=truncate_excerpt(user_query, cfg.excerpt_max_chars),
            final_answer_excerpt=truncate_excerpt(final_answer, cfg.excerpt_max_chars),
            l1_text="(recovered from L0)",
            l1_source="inline",
            l0_store=L0Store(path=rel_path, handle=qa_id),
            l0_persist_status="done",
            l0_content_mode=l0_mode,
            had_full_compact_in_qa=had_compact,
        )
        recovered.append(qa_id)

    if not recovered:
        return registry, recovered

    max_index = max(entry.qa_index for entry in registry.blocks.values())
    if registry.next_qa_index <= max_index:
        registry.next_qa_index = max_index + 1

    logger.info(
        "[QABlockReconcile] recovered orphan L0 blocks session_id=%s qa_ids=%s next_qa_index=%s",
        registry.session_id,
        recovered,
        registry.next_qa_index,
    )
    write_context_trace(
        "qa_block.reconcile_orphan_l0",
        {
            "session_id": registry.session_id,
            "recovered_qa_ids": recovered,
            "next_qa_index": registry.next_qa_index,
        },
    )
    return registry, recovered
