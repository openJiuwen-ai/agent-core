# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class QABlockConfig(BaseModel):
    enabled: bool = True
    l1_inline_max_chars: int = Field(default=500, gt=0)
    l1_summary_max_chars: int = Field(default=300, gt=0)
    l1_llm_min_chars: int = Field(default=1500, gt=0)
    history_qa_buffer_size: int = Field(default=3, gt=0)
    async_freeze_persist: bool = True
    excerpt_max_chars: int = Field(default=200, gt=0)
    catalog_max_tokens: int = Field(default=8000, gt=0)
    catalog_short_max_chars: int = Field(default=150, gt=0)
    hydrate_artifact_aware: bool = True
    selector_enabled: bool = True
    selector_mode: Literal["rule", "llm", "hybrid"] = "llm"
    max_preload_blocks: int = Field(default=3, gt=0)
    selector_fallback: Literal["last_n", "none"] = "last_n"
    selector_fallback_on_empty: bool = False
    selector_rule_fallback: bool = True
    selector_min_relevance: float = Field(default=0.12, ge=0.0, le=1.0)
    selector_all_small_enabled: bool = True
    selector_all_small_per_block_tokens: int = Field(default=2500, gt=0)
    selector_all_small_total_tokens: int = Field(default=15000, gt=0)
    selector_all_small_max_blocks: int = Field(default=8, gt=0)
