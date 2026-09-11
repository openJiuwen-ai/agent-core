# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serializable context-token and KV-cache usage models."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from openjiuwen.core.context_engine.base import ContextStats


class ContextCategory(str, Enum):
    SYSTEM_PROMPT = "system_prompt"
    TOOLS = "tools"
    SKILLS = "skills"
    MESSAGES = "messages"
    # Compatibility input only.  Analyzer normalization maps this category
    # to ``system_prompt`` before protocol serialization.
    MEMORY = "memory"


class ContextPartUsage(BaseModel):
    category: ContextCategory
    tokens: int = 0
    percentage_of_window: float | None = None
    percentage_of_input: float | None = None
    estimated: bool = True
    tokenizer: str | None = None
    carrier: str | None = None
    source: str = "not_reported"
    measurement_source: str = "not_reported"
    fallback_reason: str | None = None
    fallback_tokenizer_model: str | None = None
    # Reserved for stable logical-fragment correlation.  The current public
    # payload remains category-aggregated, but callers can already attach the
    # final-window order/source without changing the four-category protocol.
    order: int | None = None
    stable_id: str | None = None


class ContextWindowUsage(BaseModel):
    limit_tokens: int | None = None
    input_tokens: int | None = None
    occupancy_rate: float | None = None
    tokens_source: str = "not_reported"
    local_estimated_input_tokens: int = 0
    reconciliation_delta_tokens: int | None = None
    context_snapshot_id: str | None = None


class RequestKVCacheUsage(BaseModel):
    input_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_miss_tokens: int | None = None
    cache_write_tokens: int | None = None
    hit_rate: float | None = None
    status: str = "not_reported"
    source: str = "not_reported"
    authoritative: bool = False
    miss_tokens_derived: bool = False
    cache_mode: str | None = None
    cache_scope: str | None = None
    invalid_reason: str | None = None


class SessionKVCacheUsage(BaseModel):
    scope: str = "session"
    aggregation_method: str = "token_weighted"
    aggregation_key: dict[str, Any] = Field(default_factory=dict)
    calls_total: int = 0
    calls_observed: int = 0
    calls_partial: int = 0
    calls_not_reported: int = 0
    input_tokens_total: int = 0
    cache_read_tokens_total: int = 0
    cache_miss_tokens_total: int = 0
    cache_write_tokens_total: int | None = None
    cache_write_coverage: str = "none"
    average_input_per_call: float | None = None
    weighted_hit_rate: float | None = None
    average_cache_read_per_call: float | None = None
    average_cache_miss_per_call: float | None = None
    status: str = "not_reported"
    data_quality: str = "not_reported"


class ContextUsageMeasurement(BaseModel):
    category_source: str = "not_reported"
    total_source: str = "not_reported"
    tokenizer: str | None = None
    estimated: bool = True
    authoritative_total: bool = False
    measurement_version: str = "1"
    context_snapshot_id: str | None = None


class ContextUsageSnapshot(BaseModel):
    event_type: str = "context.usage"
    schema_version: str = "context-usage.v1"
    phase: str = "pre_call"
    request_id: str
    session_id: str
    # ``session_id`` is retained for compatibility.  The following fields
    # prevent team members and child agents that share a product session from
    # being treated as one context/cache owner.
    product_session_id: str | None = None
    execution_session_id: str | None = None
    context_owner_id: str | None = None
    agent_id: str | None = None
    team_id: str | None = None
    member_name: str | None = None
    invocation_id: str | None = None
    parent_invocation_id: str | None = None
    delegation_id: str | None = None
    agent_path: list[str] = Field(default_factory=list)
    depth: int = 0
    cache_identity: str | None = None
    parent_cache_identity: str | None = None
    cache_mode: str | None = None
    cache_scope: str | None = None
    sequence: int = 0
    provider: str | None = None
    model: str | None = None
    deployment: str | None = None
    timestamp: str
    context_snapshot_id: str | None = None
    context_changes: dict[str, Any] = Field(default_factory=dict)
    measurement_cache: dict[str, Any] = Field(default_factory=dict)
    context_window: ContextWindowUsage
    parts: dict[str, ContextPartUsage]
    kv_cache: dict[str, Any] = Field(default_factory=dict)
    # Token-weighted cache-hit average for the current cache/session scope.
    # This is the same value as ``kv_cache.session.weighted_hit_rate`` but is
    # exposed at the top level for consumers that only need one number.
    session_kv_cache_hit_rate: float | None = None
    measurement: ContextUsageMeasurement


class ContextWindowTokenReport(BaseModel):
    """Immutable-by-convention local token report for one final context window.

    The report is owned by one ``SessionModelContext``.  It contains only
    serialized measurement results and metadata; the request pipeline keeps a
    request-local copy/reference so a later concurrent window cannot change a
    post-call event retroactively.
    """

    context_snapshot_id: str
    context_id: str
    session_id: str
    product_session_id: str | None = None
    execution_session_id: str | None = None
    context_owner_id: str | None = None
    agent_id: str | None = None
    team_id: str | None = None
    member_name: str | None = None
    invocation_id: str | None = None
    parent_invocation_id: str | None = None
    delegation_id: str | None = None
    agent_path: list[str] = Field(default_factory=list)
    depth: int = 0
    cache_identity: str | None = None
    parent_cache_identity: str | None = None
    cache_mode: str | None = None
    cache_scope: str | None = None
    message_revision: int = 0
    prompt_revision: int = 0
    skills_revision: int = 0
    attachment_revision: int = 0
    tools_signature: str | None = None
    tokenizer_identity: str | None = None
    measurement_version: str = "1"
    component_signatures: dict[str, str] = Field(default_factory=dict)
    context_window: ContextWindowUsage
    parts: dict[str, ContextPartUsage] = Field(default_factory=dict)
    context_stats: ContextStats = Field(default_factory=ContextStats)
    measurement: ContextUsageMeasurement = Field(default_factory=ContextUsageMeasurement)
    context_changes: dict[str, Any] = Field(default_factory=dict)
    measurement_cache: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ContextCategory",
    "ContextPartUsage",
    "ContextWindowUsage",
    "RequestKVCacheUsage",
    "SessionKVCacheUsage",
    "ContextUsageMeasurement",
    "ContextUsageSnapshot",
    "ContextWindowTokenReport",
]
