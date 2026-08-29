# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Analyze the four logical context categories."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable, MutableMapping

from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.context_engine.token.base import TokenCounter, TokenMeasurement
from openjiuwen.core.context_engine.usage.models import (
    ContextCategory,
    ContextPartUsage,
    ContextUsageMeasurement,
    ContextUsageSnapshot,
    ContextWindowUsage,
    ContextWindowTokenReport,
)


class ContextUsageAnalyzer:
    """Compute local category estimates and reconcile them with provider totals."""

    _ATTACHMENT_CATEGORY_METADATA_KEY = "_context_usage_category"
    _ATTACHMENT_CARRIER_METADATA_KEY = "_context_usage_carrier"

    def __init__(
        self, token_counter: TokenCounter, *, model: str = "", context_window_limit: int | None = None
    ) -> None:
        self.token_counter = token_counter
        self.model = model
        self.context_window_limit = context_window_limit

    def analyze(
        self,
        context_window: ContextWindow,
        *,
        request_id: str,
        session_id: str,
        phase: str = "pre_call",
        provider: str | None = None,
        agent_id: str | None = None,
        sequence: int = 0,
        provider_input_tokens: int | None = None,
        system_prompt_sections: (
            Iterable[tuple[ContextCategory | str, str] | tuple[ContextCategory | str, str, str]] | None
        ) = None,
        kv_cache: dict[str, Any] | None = None,
        deployment: str | None = None,
        measurement_cache: MutableMapping[str, TokenMeasurement] | None = None,
        measurement_cache_info: dict[str, Any] | None = None,
        context_snapshot_id: str | None = None,
        context_changes: dict[str, Any] | None = None,
        attribution: dict[str, Any] | None = None,
    ) -> ContextUsageSnapshot:
        parts: dict[str, ContextPartUsage] = {}

        grouped: dict[ContextCategory, list[TokenMeasurement]] = {
            ContextCategory.SYSTEM_PROMPT: [],
            ContextCategory.SKILLS: [],
        }
        carriers: dict[ContextCategory, list[str]] = {
            ContextCategory.SYSTEM_PROMPT: [],
            ContextCategory.SKILLS: [],
        }
        if system_prompt_sections is None:
            grouped[ContextCategory.SYSTEM_PROMPT].append(
                self._measure_cached(
                    "system_prompt",
                    context_window.system_messages,
                    lambda: self.token_counter.measure_messages(
                        context_window.system_messages,
                        model=self.model,
                    ),
                    measurement_cache=measurement_cache,
                    cache_info=measurement_cache_info,
                )
            )
            carriers[ContextCategory.SYSTEM_PROMPT].append("system_message")
            grouped[ContextCategory.SKILLS].append(TokenMeasurement(tokens=0, source="not_reported"))
        else:
            for section in system_prompt_sections:
                category, text = section[0], section[1]
                carrier = str(section[2]) if len(section) > 2 else "system_message"
                normalized = self._normalize_category(category)
                if normalized not in grouped:
                    normalized = ContextCategory.SYSTEM_PROMPT
                grouped[normalized].append(
                    self._measure_cached(
                        normalized.value,
                        {"text": text, "carrier": carrier},
                        lambda text=text: self.token_counter.measure(text, model=self.model),
                        measurement_cache=measurement_cache,
                        cache_info=measurement_cache_info,
                    )
                )
                carriers[normalized].append(carrier)

        ordinary_messages = []
        for message in context_window.context_messages:
            metadata = getattr(message, "metadata", {}) or {}
            fragments = metadata.get("_context_usage_fragments")
            if isinstance(fragments, list) and fragments:
                attachment_measurement = self._measure_cached(
                    "attachments",
                    message,
                    lambda message=message: self.token_counter.measure_messages([message], model=self.model),
                    measurement_cache=measurement_cache,
                    cache_info=measurement_cache_info,
                )
                for normalized, measurement, carrier in self._split_fragment_measurement(
                    attachment_measurement,
                    fragments,
                ):
                    grouped[normalized].append(measurement)
                    carriers[normalized].append(carrier)
                continue
            attachment_category = metadata.get(self._ATTACHMENT_CATEGORY_METADATA_KEY)
            if attachment_category is None:
                ordinary_messages.append(message)
                continue
            normalized = self._normalize_category(attachment_category)
            if normalized in grouped:
                grouped[normalized].append(
                    self._measure_cached(
                        normalized.value,
                        message,
                        lambda message=message: self.token_counter.measure_messages([message], model=self.model),
                        measurement_cache=measurement_cache,
                        cache_info=measurement_cache_info,
                    )
                )
                carriers[normalized].append(
                    str(metadata.get(self._ATTACHMENT_CARRIER_METADATA_KEY) or "context_message")
                )
            else:
                ordinary_messages.append(message)

        for category in (ContextCategory.SYSTEM_PROMPT, ContextCategory.SKILLS):
            parts[category.value] = self._part(
                category,
                self._merge_measurements(grouped[category]),
                carrier=self._merge_carriers(carriers[category]),
            )

        parts[ContextCategory.TOOLS.value] = self._part(
            ContextCategory.TOOLS,
            self._measure_cached(
                ContextCategory.TOOLS.value,
                context_window.tools,
                lambda: self.token_counter.measure_tools(context_window.tools, model=self.model),
                measurement_cache=measurement_cache,
                cache_info=measurement_cache_info,
            ),
            carrier="tools",
        )
        parts[ContextCategory.MESSAGES.value] = self._part(
            ContextCategory.MESSAGES,
            self._measure_cached(
                ContextCategory.MESSAGES.value,
                ordinary_messages,
                lambda: self.token_counter.measure_messages(ordinary_messages, model=self.model),
                measurement_cache=measurement_cache,
                cache_info=measurement_cache_info,
            ),
            carrier="context_message",
        )

        local_total = sum(part.tokens for part in parts.values())
        self._attribute_provider_remainder_to_messages(parts, provider_input_tokens)
        total_tokens = provider_input_tokens if provider_input_tokens is not None else local_total
        total_source = "provider_usage" if provider_input_tokens is not None else self._merge_source(parts.values())
        limit = self.context_window_limit
        input_denominator = provider_input_tokens if provider_input_tokens is not None else local_total

        for part in parts.values():
            part.percentage_of_window = self._ratio(part.tokens, limit)
            part.percentage_of_input = self._ratio(part.tokens, input_denominator)

        context_usage = ContextWindowUsage(
            limit_tokens=limit,
            input_tokens=total_tokens,
            occupancy_rate=self._ratio(total_tokens, limit),
            tokens_source=total_source,
            local_estimated_input_tokens=local_total,
            reconciliation_delta_tokens=(provider_input_tokens - local_total)
            if provider_input_tokens is not None
            else None,
            context_snapshot_id=context_snapshot_id,
        )
        category_source = self._merge_source(parts.values())
        tokenizer = self._merge_tokenizer(parts.values())
        return ContextUsageSnapshot(
            phase=phase,
            request_id=request_id,
            session_id=session_id,
            **self._attribution_fields(attribution),
            agent_id=agent_id,
            sequence=sequence,
            provider=provider,
            model=self.model or None,
            deployment=deployment,
            timestamp=datetime.now(timezone.utc).isoformat(),
            context_snapshot_id=context_snapshot_id,
            context_changes=context_changes or {},
            measurement_cache=self._public_cache_info(measurement_cache_info),
            context_window=context_usage,
            parts=parts,
            kv_cache=kv_cache or {},
            session_kv_cache_hit_rate=self._session_kv_cache_hit_rate(kv_cache),
            measurement=ContextUsageMeasurement(
                category_source=category_source,
                total_source=total_source,
                tokenizer=tokenizer,
                estimated=any(part.estimated for part in parts.values() if part.source != "not_reported"),
                authoritative_total=provider_input_tokens is not None,
                context_snapshot_id=context_snapshot_id,
            ),
        )

    def _split_fragment_measurement(
        self,
        measurement: TokenMeasurement,
        fragments: list[Any],
    ) -> list[tuple[ContextCategory, TokenMeasurement, str]]:
        """Attribute one unchanged carrier message to logical categories.

        The model still receives one rendered attachment message.  Since the
        tokenizer does not expose token offsets for arbitrary provider
        counters, distribute the measured carrier total by logical fragment
        text length and assign the rounding remainder to the final fragment.
        This preserves the exact carrier total without changing prompt order.
        """
        valid_fragments = [fragment for fragment in fragments if isinstance(fragment, dict)]
        if not valid_fragments:
            return [(ContextCategory.SYSTEM_PROMPT, measurement, "context_message")]
        weights = [max(len(str(fragment.get("text") or "")), 1) for fragment in valid_fragments]
        total_weight = sum(weights)
        remaining = measurement.tokens
        result: list[tuple[ContextCategory, TokenMeasurement, str]] = []
        for index, (fragment, weight) in enumerate(zip(valid_fragments, weights)):
            tokens = remaining if index == len(valid_fragments) - 1 else measurement.tokens * weight // total_weight
            remaining -= tokens
            normalized = self._normalize_category(fragment.get("category", ContextCategory.SYSTEM_PROMPT))
            if normalized not in (ContextCategory.SYSTEM_PROMPT, ContextCategory.SKILLS):
                normalized = ContextCategory.SYSTEM_PROMPT
            result.append(
                (
                    normalized,
                    TokenMeasurement(
                        tokens=tokens,
                        source=measurement.source,
                        estimated=measurement.estimated,
                        tokenizer=measurement.tokenizer,
                        model=measurement.model,
                        fallback_reason=measurement.fallback_reason,
                        fallback_tokenizer_model=measurement.fallback_tokenizer_model,
                    ),
                    str(fragment.get("carrier") or "context_message"),
                )
            )
        return result

    def analyze_from_report(
        self,
        report: ContextWindowTokenReport,
        *,
        request_id: str,
        session_id: str,
        phase: str = "pre_call",
        provider: str | None = None,
        agent_id: str | None = None,
        sequence: int = 0,
        provider_input_tokens: int | None = None,
        kv_cache: dict[str, Any] | None = None,
        deployment: str | None = None,
        attribution: dict[str, Any] | None = None,
    ) -> ContextUsageSnapshot:
        """Create an event snapshot from an already measured window report.

        No tokenizer method is called here.  ``post_call`` uses this path so a
        concurrent context mutation cannot replace the report captured by the
        corresponding ``pre_call``.
        """
        parts = {name: part.model_copy(deep=True) for name, part in report.parts.items()}
        local_total = report.context_window.local_estimated_input_tokens
        self._attribute_provider_remainder_to_messages(parts, provider_input_tokens)
        total_tokens = provider_input_tokens if provider_input_tokens is not None else local_total
        total_source = "provider_usage" if provider_input_tokens is not None else report.context_window.tokens_source
        denominator = provider_input_tokens if provider_input_tokens is not None else local_total
        for part in parts.values():
            part.percentage_of_window = self._ratio(part.tokens, report.context_window.limit_tokens)
            part.percentage_of_input = self._ratio(part.tokens, denominator)

        context_window = report.context_window.model_copy(
            deep=True,
            update={
                "input_tokens": total_tokens,
                "tokens_source": total_source,
                "occupancy_rate": self._ratio(total_tokens, report.context_window.limit_tokens),
                "reconciliation_delta_tokens": (
                    provider_input_tokens - local_total if provider_input_tokens is not None else None
                ),
                "context_snapshot_id": report.context_snapshot_id,
            },
        )
        measurement = report.measurement.model_copy(
            deep=True,
            update={
                "total_source": total_source,
                "authoritative_total": provider_input_tokens is not None,
                "context_snapshot_id": report.context_snapshot_id,
            },
        )
        return ContextUsageSnapshot(
            phase=phase,
            request_id=request_id,
            session_id=session_id,
            **self._attribution_fields(attribution or report.model_dump(mode="python")),
            agent_id=agent_id,
            sequence=sequence,
            provider=provider,
            model=self.model or None,
            deployment=deployment,
            timestamp=datetime.now(timezone.utc).isoformat(),
            context_snapshot_id=report.context_snapshot_id,
            context_changes=report.context_changes,
            measurement_cache=report.measurement_cache,
            context_window=context_window,
            parts=parts,
            kv_cache=kv_cache or {},
            session_kv_cache_hit_rate=self._session_kv_cache_hit_rate(kv_cache),
            measurement=measurement,
        )

    @staticmethod
    def _attribution_fields(attribution: dict[str, Any] | None) -> dict[str, Any]:
        """Select the stable top-level owner/lineage fields for an event."""
        values = attribution or {}
        names = (
            "product_session_id",
            "execution_session_id",
            "context_owner_id",
            "team_id",
            "member_name",
            "invocation_id",
            "parent_invocation_id",
            "delegation_id",
            "agent_path",
            "depth",
            "cache_identity",
            "parent_cache_identity",
            "cache_mode",
            "cache_scope",
        )
        result = {name: values.get(name) for name in names if name in values}
        return result

    @staticmethod
    def _part(
        category: ContextCategory,
        measurement: TokenMeasurement,
        *,
        carrier: str,
    ) -> ContextPartUsage:
        return ContextPartUsage(
            category=category,
            tokens=measurement.tokens,
            estimated=measurement.estimated,
            tokenizer=measurement.tokenizer,
            carrier=carrier,
            source=measurement.source,
            measurement_source=measurement.source,
            fallback_reason=measurement.fallback_reason,
            fallback_tokenizer_model=measurement.fallback_tokenizer_model,
        )

    @staticmethod
    def _attribute_provider_remainder_to_messages(
        parts: dict[str, ContextPartUsage],
        provider_input_tokens: int | None,
    ) -> None:
        """Put provider/local reconciliation into the logical messages bucket.

        ``system_prompt``, ``skills`` and ``tools`` are measured locally and
        remain unchanged.  The provider's authoritative input total is then
        reconciled by assigning the remainder to ``messages``.  This makes
        the four displayed categories add up to the provider total while the
        original local estimate remains available on ``context_window`` for
        diagnostics.
        """
        if provider_input_tokens is None:
            return
        messages = parts.get(ContextCategory.MESSAGES.value)
        if messages is None:
            return

        fixed_tokens = sum(
            part.tokens for name, part in parts.items() if name != ContextCategory.MESSAGES.value
        )
        residual_tokens = provider_input_tokens - fixed_tokens
        messages.tokens = max(residual_tokens, 0)
        messages.source = "provider_usage_residual"
        messages.measurement_source = "provider_usage_residual"
        messages.estimated = True
        messages.fallback_reason = (
            "provider_total_residual"
            if residual_tokens >= 0
            else "provider_total_below_fixed_categories"
        )

    @staticmethod
    def _session_kv_cache_hit_rate(kv_cache: dict[str, Any] | None) -> float | None:
        if not isinstance(kv_cache, dict):
            return None
        session_usage = kv_cache.get("session")
        if not isinstance(session_usage, dict):
            return None
        value = session_usage.get("weighted_hit_rate")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    def _measure_cached(
        self,
        part_name: str,
        value: Any,
        measure: Callable[[], TokenMeasurement],
        *,
        measurement_cache: MutableMapping[str, TokenMeasurement] | None,
        cache_info: dict[str, Any] | None,
    ) -> TokenMeasurement:
        """Measure one logical input, reusing only token results.

        Context processors and final-window assembly still run on every
        request.  This helper only avoids repeating tokenizer work for the
        same canonical input and tokenizer identity.
        """
        if measurement_cache is None:
            measurement = measure()
            self._record_cache_info(cache_info, part_name, hit=False)
            return measurement
        key = self._measurement_key(part_name, value)
        cached = measurement_cache.get(key)
        if cached is not None:
            self._record_cache_info(cache_info, part_name, hit=True)
            return cached
        measurement = measure()
        measurement_cache[key] = measurement
        self._record_cache_info(cache_info, part_name, hit=False)
        return measurement

    def _measurement_key(self, part_name: str, value: Any) -> str:
        payload = json.dumps(self._canonicalize(value), ensure_ascii=False, sort_keys=True, default=str)
        identity = ":".join(
            (
                type(self.token_counter).__name__,
                str(getattr(self.token_counter, "measurement_tokenizer", None) or ""),
                str(self.model or ""),
                "measurement-v1",
                part_name,
                payload,
            )
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @classmethod
    def _canonicalize(cls, value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return cls._canonicalize(value.model_dump(mode="json"))
        if isinstance(value, dict):
            sorted_items = sorted(value.items(), key=lambda item: str(item[0]))
            return {
                str(key): cls._canonicalize(item)
                for key, item in sorted_items
            }
        if isinstance(value, (list, tuple)):
            return [cls._canonicalize(item) for item in value]
        if isinstance(value, set):
            return sorted(cls._canonicalize(item) for item in value)
        return value

    @staticmethod
    def _record_cache_info(cache_info: dict[str, Any] | None, part_name: str, *, hit: bool) -> None:
        if cache_info is None:
            return
        bucket = "reused_parts" if hit else "recomputed_parts"
        values = cache_info.setdefault(bucket, set())
        if isinstance(values, set):
            values.add(part_name)

    @staticmethod
    def _public_cache_info(cache_info: dict[str, Any] | None) -> dict[str, Any]:
        if not cache_info:
            return {"hit": "miss", "reused_parts": [], "recomputed_parts": []}
        reused = sorted(cache_info.get("reused_parts", set()))
        recomputed = sorted(cache_info.get("recomputed_parts", set()))
        hit = "full" if reused and not recomputed else ("partial" if reused else "miss")
        return {"hit": hit, "reused_parts": reused, "recomputed_parts": recomputed}

    @staticmethod
    def _normalize_category(category: ContextCategory | str) -> ContextCategory:
        try:
            normalized = ContextCategory(category)
        except (TypeError, ValueError):
            normalized = ContextCategory.SYSTEM_PROMPT
        if normalized is ContextCategory.MEMORY:
            return ContextCategory.SYSTEM_PROMPT
        return normalized

    @staticmethod
    def _merge_measurements(measurements: list[TokenMeasurement]) -> TokenMeasurement:
        if not measurements:
            return TokenMeasurement(tokens=0, source="not_reported", tokenizer=None)
        sources = {item.source for item in measurements}
        tokenizers = {item.tokenizer for item in measurements if item.tokenizer}
        reasons = {item.fallback_reason for item in measurements if item.fallback_reason}
        fallback_models = {item.fallback_tokenizer_model for item in measurements if item.fallback_tokenizer_model}
        return TokenMeasurement(
            tokens=sum(item.tokens for item in measurements),
            source=next(iter(sources)) if len(sources) == 1 else "mixed",
            estimated=any(item.estimated for item in measurements),
            tokenizer=next(iter(tokenizers)) if len(tokenizers) == 1 else ("mixed" if tokenizers else None),
            fallback_reason=next(iter(reasons)) if len(reasons) == 1 else ("mixed" if reasons else None),
            fallback_tokenizer_model=(
                next(iter(fallback_models)) if len(fallback_models) == 1 else ("mixed" if fallback_models else None)
            ),
        )

    @staticmethod
    def _merge_carriers(carriers: list[str]) -> str:
        values = {carrier for carrier in carriers if carrier}
        if not values:
            return "system_message"
        return next(iter(values)) if len(values) == 1 else "mixed"

    @classmethod
    def _merge_source(cls, parts: Iterable[ContextPartUsage]) -> str:
        sources = {part.source for part in parts if part.source != "not_reported"}
        return next(iter(sources)) if len(sources) == 1 else ("mixed" if sources else "not_reported")

    @classmethod
    def _merge_tokenizer(cls, parts: Iterable[ContextPartUsage]) -> str | None:
        tokenizers = {part.tokenizer for part in parts if part.tokenizer}
        return next(iter(tokenizers)) if len(tokenizers) == 1 else ("mixed" if tokenizers else None)

    @staticmethod
    def _ratio(numerator: int | None, denominator: int | None) -> float | None:
        if numerator is None or denominator is None or denominator <= 0:
            return None
        return numerator / denominator


__all__ = ["ContextUsageAnalyzer"]
