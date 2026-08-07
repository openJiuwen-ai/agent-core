# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.context_engine import (
    CacheAggregationKey,
    ContextCategory,
    ContextEngine,
    ContextEngineConfig,
    ContextUsageAnalyzer,
    ContextWindow,
    RequestKVCacheUsage,
    SessionKVCacheAggregator,
    TiktokenCounter,
)
import pytest
from openjiuwen.core.context_engine.usage.provider_usage import request_usage_from_metadata
from openjiuwen.core.foundation.llm import SystemMessage, UsageMetadata, UserMessage
from openjiuwen.core.foundation.tool import ToolInfo


class _CountingStringCounter:
    """Small counter wrapper used to prove report-cache reuse."""

    measurement_source = "test_counter"
    measurement_estimated = True
    measurement_tokenizer = "test"
    measurement_fallback_reason = None
    measurement_fallback_tokenizer_model = None

    def __init__(self) -> None:
        from openjiuwen.core.context_engine import StringLengthCounter

        self._counter = StringLengthCounter()
        self.calls = {"text": 0, "messages": 0, "tools": 0}

    def measure(self, text, *, model="", **kwargs):
        self.calls["text"] += 1
        return self._counter.measure(text, model=model, **kwargs)

    def measure_messages(self, messages, *, model="", **kwargs):
        self.calls["messages"] += 1
        return self._counter.measure_messages(messages, model=model, **kwargs)

    def measure_tools(self, tools, *, model="", **kwargs):
        self.calls["tools"] += 1
        return self._counter.measure_tools(tools, model=model, **kwargs)

    def count(self, text, *, model="", **kwargs):
        return self._counter.count(text, model=model, **kwargs)

    def count_messages(self, messages, *, model="", **kwargs):
        return self._counter.count_messages(messages, model=model, **kwargs)

    def count_tools(self, tools, *, model="", **kwargs):
        return self._counter.count_tools(tools, model=model, **kwargs)


def test_analyzer_emits_four_categories_and_provider_reconciliation() -> None:
    window = ContextWindow(
        system_messages=[SystemMessage(content="ignored by logical sections")],
        tools=[ToolInfo(name="read_file", description="read", parameters={})],
        context_messages=[UserMessage(content="hello")],
    )

    snapshot = ContextUsageAnalyzer(
        TiktokenCounter(),
        model="gpt-4o",
        context_window_limit=1_000,
    ).analyze(
        window,
        request_id="req-1",
        session_id="session-1",
        provider_input_tokens=100,
        system_prompt_sections=[
            (ContextCategory.SYSTEM_PROMPT, "rules"),
            (ContextCategory.SKILLS, "skill index"),
        ],
    )

    assert set(snapshot.parts) == {"system_prompt", "tools", "skills", "messages"}
    assert snapshot.context_window.input_tokens == 100
    assert snapshot.context_window.tokens_source == "provider_usage"
    assert snapshot.context_window.reconciliation_delta_tokens is not None
    assert snapshot.measurement.authoritative_total is True
    assert snapshot.parts["skills"].carrier == "system_message"
    assert snapshot.parts["tools"].carrier == "tools"
    fixed_tokens = sum(
        snapshot.parts[name].tokens for name in ("system_prompt", "skills", "tools")
    )
    assert snapshot.parts["messages"].tokens == 100 - fixed_tokens
    assert sum(part.tokens for part in snapshot.parts.values()) == 100
    assert sum(part.percentage_of_input or 0 for part in snapshot.parts.values()) == pytest.approx(1.0)
    assert snapshot.parts["messages"].source == "provider_usage_residual"
    assert snapshot.parts["messages"].fallback_reason == "provider_total_residual"


def test_report_post_call_reconciles_messages_without_remeasuring_other_parts() -> None:
    analyzer = ContextUsageAnalyzer(
        _CountingStringCounter(),
        model="test-model",
        context_window_limit=1_000,
    )
    report = analyzer.analyze(
        ContextWindow(
            context_messages=[UserMessage(content="local message")],
        ),
        request_id="req-report",
        session_id="session-report",
    )
    local_parts = {name: part.tokens for name, part in report.parts.items()}

    snapshot = analyzer.analyze_from_report(
        report,
        request_id="req-report",
        session_id="session-report",
        phase="post_call",
        provider_input_tokens=local_parts["system_prompt"] + local_parts["skills"] + local_parts["tools"] + 123,
        kv_cache={"session": {"weighted_hit_rate": 0.625}},
    )

    assert snapshot.parts["system_prompt"].tokens == local_parts["system_prompt"]
    assert snapshot.parts["skills"].tokens == local_parts["skills"]
    assert snapshot.parts["tools"].tokens == local_parts["tools"]
    assert snapshot.parts["messages"].tokens == 123
    assert sum(part.tokens for part in snapshot.parts.values()) == snapshot.context_window.input_tokens
    assert snapshot.context_window.local_estimated_input_tokens == sum(local_parts.values())
    assert snapshot.context_window.reconciliation_delta_tokens == 123 - local_parts["messages"]
    assert snapshot.session_kv_cache_hit_rate == pytest.approx(0.625)


def test_memory_is_normalized_to_system_prompt_in_usage_protocol() -> None:
    snapshot = ContextUsageAnalyzer(TiktokenCounter()).analyze(
        ContextWindow(),
        request_id="req-memory",
        session_id="session-memory",
        system_prompt_sections=[("memory", "remember this")],
    )

    assert set(snapshot.parts) == {"system_prompt", "tools", "skills", "messages"}
    assert snapshot.parts["system_prompt"].tokens > 0
    assert ContextCategory.MEMORY.value == "memory"


def test_dynamic_attachment_category_is_removed_from_generic_messages() -> None:
    analyzer = ContextUsageAnalyzer(TiktokenCounter())
    snapshot = analyzer.analyze(
        ContextWindow(
            context_messages=[
                UserMessage(
                    content="skill instructions",
                    metadata={"_context_usage_category": "skills"},
                ),
                UserMessage(content="user request"),
            ]
        ),
        request_id="req-attachment",
        session_id="session-attachment",
    )

    assert snapshot.parts["skills"].tokens > 0
    assert snapshot.parts["messages"].tokens > 0
    assert snapshot.parts["skills"].carrier == "context_message"
    unclassified = analyzer.analyze(
        ContextWindow(
            context_messages=[
                UserMessage(content="skill instructions"),
                UserMessage(content="user request"),
            ]
        ),
        request_id="req-unclassified",
        session_id="session-attachment",
    )
    assert snapshot.parts["messages"].tokens < unclassified.parts["messages"].tokens


def test_mixed_attachment_carrier_preserves_one_message_and_category_totals() -> None:
    snapshot = ContextUsageAnalyzer(TiktokenCounter()).analyze(
        ContextWindow(
            context_messages=[
                UserMessage(
                    content="memory skill memory",
                    metadata={
                        "_context_usage_fragments": [
                            {"category": "system_prompt", "text": "memory"},
                            {"category": "skills", "text": "skill"},
                            {"category": "memory", "text": "memory"},
                        ]
                    },
                )
            ]
        ),
        request_id="req-mixed-attachment",
        session_id="session-attachment",
    )

    assert snapshot.parts["messages"].tokens == 0
    assert snapshot.parts["system_prompt"].tokens > 0
    assert snapshot.parts["skills"].tokens > 0


def test_session_cache_is_token_weighted_and_idempotent() -> None:
    aggregator = SessionKVCacheAggregator()
    key = CacheAggregationKey("session-1", "deepseek", "deepseek-chat")

    first = RequestKVCacheUsage(input_tokens=100, cache_read_tokens=40, cache_miss_tokens=60)
    second = RequestKVCacheUsage(input_tokens=300, cache_read_tokens=240)
    aggregator.record(request_id="req-1", scope_key=key, usage=first)
    result = aggregator.record(request_id="req-2", scope_key=key, usage=second)
    duplicate = aggregator.record(request_id="req-2", scope_key=key, usage=second)

    assert result.calls_total == 2
    assert result.calls_observed == 2
    assert result.input_tokens_total == 400
    assert result.cache_read_tokens_total == 280
    assert result.cache_miss_tokens_total == 120
    assert result.weighted_hit_rate == 0.7
    assert result.average_input_per_call == 200
    assert result.average_cache_read_per_call == 140
    assert result.status == "partial_hit"
    assert result.data_quality == "mixed"
    assert duplicate.model_dump() == result.model_dump()
    assert result.aggregation_key["model"] == "deepseek-chat"


def test_unknown_cache_usage_is_not_counted_as_miss() -> None:
    aggregator = SessionKVCacheAggregator()
    key = CacheAggregationKey("session-1", "qwen", "qwen-max")

    result = aggregator.record(
        request_id="req-1",
        scope_key=key,
        usage=RequestKVCacheUsage(input_tokens=None, cache_read_tokens=None),
    )

    assert result.calls_total == 1
    assert result.calls_not_reported == 1
    assert result.calls_observed == 0
    assert result.cache_miss_tokens_total == 0
    assert result.weighted_hit_rate is None


def test_provider_zero_cache_read_is_authoritative_when_explicit() -> None:
    usage = UsageMetadata(
        input_tokens=100,
        cache_read_tokens=0,
        cache_miss_tokens=100,
        cache_source="provider_usage",
        cache_status="miss",
        cache_authoritative=True,
    )

    normalized = request_usage_from_metadata(usage)

    assert normalized.cache_read_tokens == 0
    assert normalized.cache_miss_tokens == 100
    assert normalized.authoritative is True
    assert normalized.status == "miss"


def test_observed_cache_status_is_reconciled_to_partial_hit() -> None:
    normalized = request_usage_from_metadata(
        UsageMetadata(
            input_tokens=100,
            cache_read_tokens=40,
            cache_status="observed",
            cache_authoritative=True,
        )
    )

    assert normalized.status == "partial_hit"


def test_provider_miss_is_reported_as_authoritative_quality() -> None:
    aggregator = SessionKVCacheAggregator()
    result = aggregator.record(
        request_id="req-authoritative",
        scope_key=CacheAggregationKey("session-quality", "provider", "model"),
        usage=RequestKVCacheUsage(input_tokens=100, cache_read_tokens=0, cache_miss_tokens=100),
    )

    assert result.data_quality == "authoritative"


def test_invalid_provider_cache_usage_is_not_clamped_into_a_miss() -> None:
    normalized = request_usage_from_metadata(
        UsageMetadata(
            input_tokens=100,
            cache_read_tokens=-1,
            cache_miss_tokens=101,
            cache_authoritative=True,
        )
    )

    assert normalized.status == "invalid"
    assert normalized.invalid_reason
    assert normalized.hit_rate is None

    aggregator = SessionKVCacheAggregator()
    key = CacheAggregationKey("invalid-session", "provider", "model")
    result = aggregator.record(request_id="invalid-request", scope_key=key, usage=normalized)
    assert result.calls_total == 1
    assert result.calls_observed == 0
    assert result.calls_partial == 1
    assert result.input_tokens_total == 0
    assert result.weighted_hit_rate is None
    assert result.status == "invalid"


def test_cache_write_is_overlap_diagnostic_with_partial_coverage() -> None:
    aggregator = SessionKVCacheAggregator()
    key = CacheAggregationKey("write-session", "provider", "model")
    aggregator.record(
        request_id="write-1",
        scope_key=key,
        usage=RequestKVCacheUsage(
            input_tokens=100,
            cache_read_tokens=90,
            cache_miss_tokens=10,
            cache_write_tokens=10,
        ),
    )
    result = aggregator.record(
        request_id="write-2",
        scope_key=key,
        usage=RequestKVCacheUsage(input_tokens=100, cache_read_tokens=100, cache_miss_tokens=0),
    )

    assert result.cache_miss_tokens_total == 10
    assert result.cache_write_tokens_total is None
    assert result.cache_write_coverage == "partial"


@pytest.mark.asyncio
async def test_context_report_reuses_stable_parts_and_tracks_message_revision() -> None:
    from openjiuwen.core.foundation.llm import SystemMessage

    counter = _CountingStringCounter()
    engine = ContextEngine(ContextEngineConfig(model_name="gpt-4o", context_window_tokens=1_000))
    context = await engine.create_context("report-context", None, token_counter=counter)
    await context.add_messages(UserMessage(content="first"))
    sections = [(ContextCategory.SYSTEM_PROMPT, "rules", "system_message")]
    window = await context.get_context_window(system_messages=[SystemMessage(content="rules")])

    first = context.build_context_usage_report(
        window,
        system_prompt_sections=sections,
        model="gpt-4o",
        attribution={"context_owner_id": "owner-a"},
    )
    calls_after_first = dict(counter.calls)
    second = context.build_context_usage_report(
        window,
        system_prompt_sections=sections,
        model="gpt-4o",
        attribution={"context_owner_id": "owner-a"},
    )

    assert second.measurement_cache["hit"] == "full"
    assert counter.calls == calls_after_first
    assert second.context_snapshot_id != first.context_snapshot_id

    await context.add_messages(UserMessage(content="second"))
    next_window = await context.get_context_window(system_messages=[SystemMessage(content="rules")])
    third = context.build_context_usage_report(
        next_window,
        system_prompt_sections=sections,
        model="gpt-4o",
        attribution={"context_owner_id": "owner-a"},
    )

    assert third.message_revision > first.message_revision
    assert third.context_changes["message_event"] == "append"
    assert third.measurement_cache["hit"] == "partial"
    assert third.measurement_cache["reused_parts"] == ["system_prompt", "tools"]
    assert third.measurement_cache["recomputed_parts"] == ["messages"]
