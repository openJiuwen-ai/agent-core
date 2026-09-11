# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Session-level provider KV-cache aggregation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from openjiuwen.core.context_engine.usage.models import RequestKVCacheUsage, SessionKVCacheUsage


@dataclass(frozen=True)
class CacheAggregationKey:
    session_id: str
    provider: str
    model: str
    deployment: str = ""
    cache_mode: str = "provider"
    cache_scope: str = "session"
    product_session_id: str | None = None
    execution_session_id: str | None = None
    cache_identity: str | None = None
    parent_cache_identity: str | None = None
    team_id: str | None = None
    agent_id: str | None = None
    member_name: str | None = None


@dataclass
class _Accumulator:
    calls_total: int = 0
    calls_observed: int = 0
    calls_partial: int = 0
    calls_not_reported: int = 0
    input_tokens_total: int = 0
    cache_read_tokens_total: int = 0
    cache_miss_tokens_total: int = 0
    cache_write_tokens_total: int | None = None
    request_ids: set[str] = field(default_factory=set)
    data_quality: str = "not_reported"
    cache_write_calls: int = 0


class SessionKVCacheAggregator:
    """Aggregate cache usage with request-id idempotency and weighted averages."""

    def __init__(self) -> None:
        self._values: dict[CacheAggregationKey, _Accumulator] = {}

    def record(
        self,
        *,
        request_id: str,
        scope_key: CacheAggregationKey,
        usage: RequestKVCacheUsage,
    ) -> SessionKVCacheUsage:
        accumulator = self._values.setdefault(scope_key, _Accumulator())
        if request_id in accumulator.request_ids:
            return self.snapshot(scope_key)
        accumulator.request_ids.add(request_id)
        accumulator.calls_total += 1

        if usage.invalid_reason:
            accumulator.calls_partial += 1
            self._merge_quality(accumulator, "invalid")
            return self.snapshot(scope_key)

        if usage.input_tokens is None or usage.cache_read_tokens is None:
            has_reported_usage = any(
                (
                    usage.input_tokens is not None,
                    usage.cache_read_tokens is not None,
                    usage.cache_miss_tokens is not None,
                    usage.cache_write_tokens is not None,
                )
            )
            if has_reported_usage:
                accumulator.calls_partial += 1
                self._merge_quality(accumulator, "partial")
            else:
                accumulator.calls_not_reported += 1
            return self.snapshot(scope_key)

        if not self._is_valid_observed_usage(usage):
            accumulator.calls_partial += 1
            self._merge_quality(accumulator, "invalid")
            return self.snapshot(scope_key)

        miss_tokens = usage.cache_miss_tokens
        miss_is_derived = miss_tokens is None or usage.miss_tokens_derived
        if miss_tokens is None:
            miss_tokens = usage.input_tokens - usage.cache_read_tokens
        if miss_tokens < 0 or miss_tokens > usage.input_tokens - usage.cache_read_tokens:
            accumulator.calls_partial += 1
            self._merge_quality(accumulator, "invalid")
            return self.snapshot(scope_key)

        write_tokens = usage.cache_write_tokens
        if write_tokens is not None and (write_tokens < 0 or write_tokens > miss_tokens):
            accumulator.calls_partial += 1
            self._merge_quality(accumulator, "invalid")
            return self.snapshot(scope_key)

        accumulator.calls_observed += 1
        accumulator.input_tokens_total += usage.input_tokens
        accumulator.cache_read_tokens_total += usage.cache_read_tokens
        accumulator.cache_miss_tokens_total += miss_tokens
        self._merge_quality(accumulator, "derived" if miss_is_derived else "authoritative")
        if write_tokens is not None:
            accumulator.cache_write_calls += 1
            accumulator.cache_write_tokens_total = (accumulator.cache_write_tokens_total or 0) + write_tokens
        return self.snapshot(scope_key)

    def snapshot(self, scope_key: CacheAggregationKey) -> SessionKVCacheUsage:
        accumulator = self._values.get(scope_key, _Accumulator())
        observed = accumulator.calls_observed
        input_total = accumulator.input_tokens_total
        write_total = (
            accumulator.cache_write_tokens_total
            if accumulator.cache_write_calls == observed and observed > 0
            else None
        )
        return SessionKVCacheUsage(
            aggregation_key=asdict(scope_key),
            calls_total=accumulator.calls_total,
            calls_observed=observed,
            calls_partial=accumulator.calls_partial,
            calls_not_reported=accumulator.calls_not_reported,
            input_tokens_total=input_total,
            cache_read_tokens_total=accumulator.cache_read_tokens_total,
            cache_miss_tokens_total=accumulator.cache_miss_tokens_total,
            cache_write_tokens_total=write_total,
            cache_write_coverage=self._write_coverage(accumulator),
            weighted_hit_rate=(accumulator.cache_read_tokens_total / input_total) if input_total else None,
            average_input_per_call=(input_total / observed) if observed else None,
            average_cache_read_per_call=(accumulator.cache_read_tokens_total / observed) if observed else None,
            average_cache_miss_per_call=(accumulator.cache_miss_tokens_total / observed) if observed else None,
            status=self._status(accumulator, input_total),
            data_quality=accumulator.data_quality,
        )

    @staticmethod
    def _merge_quality(accumulator: _Accumulator, quality: str) -> None:
        if accumulator.data_quality == "not_reported":
            accumulator.data_quality = quality
        elif accumulator.data_quality != quality:
            accumulator.data_quality = "mixed"

    @staticmethod
    def _is_valid_observed_usage(usage: RequestKVCacheUsage) -> bool:
        input_tokens = usage.input_tokens
        read_tokens = usage.cache_read_tokens
        return (
            isinstance(input_tokens, int)
            and not isinstance(input_tokens, bool)
            and input_tokens >= 0
            and isinstance(read_tokens, int)
            and not isinstance(read_tokens, bool)
            and 0 <= read_tokens <= input_tokens
        )

    @staticmethod
    def _write_coverage(accumulator: _Accumulator) -> str:
        if accumulator.cache_write_calls == 0:
            return "none"
        if accumulator.cache_write_calls == accumulator.calls_observed:
            return "complete"
        return "partial"

    @staticmethod
    def _status(accumulator: _Accumulator, input_total: int) -> str:
        if not accumulator.calls_observed:
            return "invalid" if accumulator.data_quality == "invalid" else "not_reported"
        if input_total <= 0:
            return "not_reported"
        if accumulator.cache_read_tokens_total <= 0:
            return "miss"
        if accumulator.cache_miss_tokens_total <= 0:
            return "full_hit"
        return "partial_hit"


__all__ = ["CacheAggregationKey", "SessionKVCacheAggregator"]
