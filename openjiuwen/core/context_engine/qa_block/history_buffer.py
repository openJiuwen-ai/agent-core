# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from collections import OrderedDict

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.foundation.llm import BaseMessage


class HistoryQABuffer:
    """In-process FIFO cache of recent completed QA L0 message lists."""

    def __init__(self, max_blocks: int = 3):
        self._blocks: OrderedDict[str, list[BaseMessage]] = OrderedDict()
        self._max_blocks = max(1, max_blocks)

    def push(self, qa_id: str, messages: list[BaseMessage]) -> None:
        if qa_id in self._blocks:
            del self._blocks[qa_id]
        self._blocks[qa_id] = messages
        evicted: list[str] = []
        while len(self._blocks) > self._max_blocks:
            evicted_qa_id, _ = self._blocks.popitem(last=False)
            evicted.append(evicted_qa_id)
        logger.info(
            "[HistoryQABuffer] push qa_id=%s message_count=%s cached=%s evicted=%s",
            qa_id,
            len(messages),
            list(self._blocks.keys()),
            evicted,
        )

    def get(self, qa_id: str) -> list[BaseMessage] | None:
        hit = self._blocks.get(qa_id)
        if hit is not None:
            logger.info(
                "[HistoryQABuffer] hit qa_id=%s message_count=%s",
                qa_id,
                len(hit),
            )
        return hit

    def recent_qa_ids(self) -> list[str]:
        return list(self._blocks.keys())

    def clear(self) -> None:
        if self._blocks:
            logger.info("[HistoryQABuffer] clear removed=%s", list(self._blocks.keys()))
        self._blocks.clear()
