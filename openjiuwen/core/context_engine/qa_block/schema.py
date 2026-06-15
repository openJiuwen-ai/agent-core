# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

REGISTRY_STATE_KEY = "qa_block_registry"

QA_ID_METADATA_KEY = "qa_id"
SOURCE_QA_ID_METADATA_KEY = "source_qa_id"

QA_BLOCK_CATALOG_START = "[QA_BLOCK_CATALOG]"
QA_BLOCK_CATALOG_END = "[END_QA_BLOCK_CATALOG]"
QA_BLOCK_L0_START_PREFIX = "[QA_BLOCK_L0: qa_id="
QA_BLOCK_L0_END = "[END_QA_BLOCK_L0]"


class L0Store(BaseModel):
    type: Literal["filesystem"] = "filesystem"
    path: str
    handle: str


class QABlockEntry(BaseModel):
    qa_id: str
    qa_index: int
    status: Literal["completed", "interrupted"]
    is_history: bool = False
    user_message_id: str | None = None
    message_id_range: tuple[str, str] | None = None
    message_count: int = 0
    approx_tokens: int = 0
    user_query_excerpt: str = ""
    final_answer_excerpt: str = ""
    l1_text: str = ""
    l1_source: Literal["inline", "compressed"] | None = None
    l1_catalog_tier: Literal["full", "short"] = "full"
    l0_store: L0Store | None = None
    l0_persist_status: Literal["pending", "done", "failed"] = "pending"
    l0_content_mode: Literal["delta", "compact_summary_tail"] = "delta"
    had_full_compact_in_qa: bool = False
    preloaded_qa_ids: list[str] = Field(default_factory=list)
    freeze_committed_at: str | None = None
    l0_persisted_at: str | None = None


class QABlockRegistry(BaseModel):
    version: int = 1
    session_id: str
    next_qa_index: int = 1
    current_qa_id: str | None = None
    blocks: dict[str, QABlockEntry] = Field(default_factory=dict)
