# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

QA_MEMORY_STATE_KEY = "__qa_memory__"


class CatalogEntry(BaseModel):
    preview: str
    handle: str | None = None


class QAArtifacts(BaseModel):
    overview: str
    entries: list[CatalogEntry] = Field(default_factory=list)


class QAArtifactState(BaseModel):
    state: Literal["RAW", "STAGED", "COMPACTED"] = "RAW"
    overview_path: str = ""
    catalog_path: str = ""
    pending_path: str = ""
    is_extracting: bool = False
    products_ready: bool = False
    covers_upto_message_id: str | None = None


class QAArtifactConfig(BaseModel):
    enabled: bool = True
    history_block_compact_tokens: int = Field(default=8000, gt=0)
    qa_min_worth_tokens: int = Field(default=2000, gt=0)
    full_compact_target_tokens: int = Field(default=92000, gt=0)
    full_compact_max_qas: int = Field(default=4, gt=0)
    full_compact_max_waves: int = Field(default=8, gt=0)
    active_qa_rolling_keep_tail: int = Field(default=2, ge=0)
    catalog_long_message_tokens: int = Field(default=800, gt=0)
    catalog_summary_max_tokens: int = Field(default=80, gt=0)
    degrade_max_retries: int = Field(default=2, ge=0)
    await_products_max_polls: int = Field(default=20, gt=0)
    await_products_poll_interval_s: float = Field(default=0.1, gt=0)


def validate_qa_artifact_thresholds(
    qa_config: QAArtifactConfig,
    *,
    session_memory_trigger_tokens: int,
    full_compact_trigger_total_tokens: int,
) -> None:
    """四级阈值须严格递增（V2 §7）。"""
    if not qa_config.enabled:
        return
    ordered = (
        qa_config.history_block_compact_tokens,
        session_memory_trigger_tokens,
        qa_config.full_compact_target_tokens,
        full_compact_trigger_total_tokens,
    )
    if not (ordered[0] < ordered[1] < ordered[2] < ordered[3]):
        raise ValueError(
            "qa_artifact thresholds must satisfy: "
            "history_block_compact_tokens < session_memory.trigger_tokens < "
            "full_compact_target_tokens < full_compact.trigger_total_tokens; "
            f"got {ordered}"
        )


IRREDUCIBLE_CONTEXT_USER_MESSAGE_ZH = "当前对话上下文已超过模型上限，且无法进一步压缩。请新建会话或缩短本轮输入后重试。"


class IrreducibleContextError(Exception):
    """Whole-window compact后仍超硬上下文窗（§5.1 终态）。"""

    def user_message(self) -> str:
        return IRREDUCIBLE_CONTEXT_USER_MESSAGE_ZH
