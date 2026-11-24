# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import math
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.base import ContextStats
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.processor.base import ContextProcessor
from openjiuwen.core.context_engine.schema.context_state import (
    CONTEXT_COMPRESSION_STATE_TYPE,
    ContextCompressionMetric,
    ContextCompressionSaved,
    ContextCompressionState,
    ContextCompressionUsage,
)
from openjiuwen.core.context_engine.token.base import TokenCounter
from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.runner.callback import lazy_callback_framework as _fw
from openjiuwen.core.runner.callback.events import ContextEvents
from openjiuwen.core.session.stream.base import OutputSchema


@dataclass(frozen=True)
class CompressionBeforeSnapshot:
    """Frozen token metrics for one compression operation.

    ``tokens`` / ``raw_tokens`` / ``source`` are captured when the operation
    starts and reused for the terminal state.  Re-measuring the shallow-copied
    ``before_messages`` after ``set_messages`` would see stale usage metadata and
    silently change the baseline (and the after-calibration ratio).
    """

    tokens: int
    raw_tokens: int
    source: str
    scale_factor: Optional[float] = None


@dataclass(frozen=True)
class ContextProcessorStateInput:
    operation_id: str
    status: str
    phase: str
    trigger: str
    processor: Optional[ContextProcessor]
    reason: str
    before_messages: list[BaseMessage]
    after_messages: Optional[list[BaseMessage]]
    started_at: float
    ended_at: Optional[float]
    error: Optional[str]
    messages_to_modify: list[int]
    force: bool
    context_max: Optional[int]
    compact_summary: str = ""
    compression_usage: Optional[dict[str, Any]] = None
    overhead_tokens: int = 0
    model_config: Any = None
    model_client_config: Any = None
    # Context-level provider/tokenizer ratio used when no valid usage baseline
    # exists.  The context owns the ledger; the recorder only consumes it.
    token_scale_factor: Optional[float] = None
    # False for trimmed windows / snapshots missing system+tools overhead:
    # usage covers the whole conversation while raw covers only this window,
    # so their ratio is not a valid provider/tokenizer scale.
    token_scale_eligible: bool = True


@dataclass(frozen=True)
class _SummaryInput:
    status: str
    before: ContextCompressionMetric
    after: Optional[ContextCompressionMetric]
    saved: Optional[ContextCompressionSaved]
    reason: str
    messages_to_modify: list[int]


class ContextProcessorStateRecorder:
    def __init__(
            self,
            *,
            session_id: str,
            context_id: str,
            get_session_ref: Callable[[], Any],
            token_counter: TokenCounter = None,
            history_limit: int = 100,
    ):
        self._session_id = session_id
        self._context_id = context_id
        self._get_session_ref = get_session_ref
        self._token_counter = token_counter
        self._history_limit = history_limit
        self._history: list[dict[str, Any]] = []
        # operation_id -> frozen before metrics.  Bounded by history_limit.
        self._before_snapshots: OrderedDict[str, CompressionBeforeSnapshot] = OrderedDict()

    def history(self) -> list[dict[str, Any]]:
        return list(self._history)

    def load_history(self, history: list[dict[str, Any]] | None) -> None:
        self._history = list(history or [])[-self._history_limit:]
        self._before_snapshots.clear()

    def before_snapshot(self, operation_id: str) -> Optional[CompressionBeforeSnapshot]:
        """Return the snapshot frozen for *operation_id*, if any."""
        return self._before_snapshots.get(operation_id)

    def build_before_snapshot(
            self,
            messages: Optional[list[BaseMessage]],
            overhead_tokens: int = 0,
            *,
            fallback_scale_factor: Optional[float] = None,
            allow_scale: bool = True,
    ) -> CompressionBeforeSnapshot:
        """Measure a before snapshot once; callers must reuse it until terminal.

        Args:
            messages: Context messages before the processor runs.
            overhead_tokens: system + tool schema tokens at the same snapshot.
            fallback_scale_factor: Context-level usage/tokenizer ratio used when
                no valid usage baseline exists.
            allow_scale: False when usage and raw do not cover the same range
                (trimmed window or missing system+tools overhead).  Such a
                snapshot must not produce or consume a calibration ratio.

        Returns:
            A frozen snapshot carrying the source and optional calibration ratio.
        """
        before_messages = list(messages or [])
        overhead = max(int(overhead_tokens or 0), 0)
        raw_tokens = self._measure_messages(before_messages) + overhead
        usage_tokens = self._usage_aware_tokens(before_messages)

        if not allow_scale:
            # usage describes the whole conversation while raw covers only
            # this window; keeping before on raw keeps it comparable with
            # after and avoids fabricating a scale from mismatched ranges.
            return self._raw_before_snapshot(raw_tokens)

        if usage_tokens is not None and usage_tokens > 0:
            return CompressionBeforeSnapshot(
                tokens=usage_tokens,
                raw_tokens=raw_tokens,
                source="usage+tail",
                scale_factor=(usage_tokens / raw_tokens) if raw_tokens > 0 else None,
            )

        if not self._is_usable_scale_factor(fallback_scale_factor):
            return self._raw_before_snapshot(raw_tokens)
        if raw_tokens <= 0:
            return self._raw_before_snapshot(raw_tokens)

        return CompressionBeforeSnapshot(
            tokens=max(int(round(raw_tokens * fallback_scale_factor)), 0),
            raw_tokens=raw_tokens,
            source="tokenizer-calibrated",
            scale_factor=fallback_scale_factor,
        )

    @staticmethod
    def _raw_before_snapshot(raw_tokens: int) -> CompressionBeforeSnapshot:
        return CompressionBeforeSnapshot(
            tokens=raw_tokens,
            raw_tokens=raw_tokens,
            source="tokenizer",
            scale_factor=None,
        )

    @staticmethod
    def _is_usable_scale_factor(scale_factor: Optional[float]) -> bool:
        return scale_factor is not None and scale_factor > 0

    def _remember_before_snapshot(
            self,
            operation_id: str,
            snapshot: CompressionBeforeSnapshot,
    ) -> None:
        self._before_snapshots[operation_id] = snapshot
        while len(self._before_snapshots) > self._history_limit:
            self._before_snapshots.popitem(last=False)

    async def emit(
            self,
            context: Any,
            state: ContextCompressionState,
            *,
            emit_stream: bool = True,
    ) -> None:
        self._record(state)
        logger.info(
            "context compression state: status=%s phase=%s processor=%s "
            "session_id=%s context_id=%s op=%s before=%s after=%s saved=%s scale=%s",
            state.status,
            state.phase,
            state.processor,
            self._session_id,
            self._context_id,
            state.operation_id,
            self._format_metric_for_log(state.before),
            self._format_metric_for_log(state.after),
            state.saved,
            state.scale_factor,
        )
        try:
            await _fw.trigger(
                ContextEvents.CONTEXT_COMPRESSION_STATE,
                context=context,
                session_ref=self._get_session_ref(),
                session_id=self._session_id,
                context_id=self._context_id,
                state=state,
            )
        except Exception as exc:
            logger.warning(
                "failed to trigger context compression state callback: session_id=%s context_id=%s op=%s "
                "status=%s error=%s",
                self._session_id,
                self._context_id,
                state.operation_id,
                state.status,
                exc,
                exc_info=True,
            )
        if not emit_stream:
            logger.debug(
                "context compression state stream suppressed: session_id=%s context_id=%s op=%s "
                "status=%s processor=%s",
                self._session_id,
                self._context_id,
                state.operation_id,
                state.status,
                state.processor,
            )
            return
        session = self._get_session_ref()
        if session is None or not hasattr(session, "write_stream"):
            logger.debug(
                "context compression state stream skipped: no session writer session_id=%s context_id=%s op=%s",
                self._session_id,
                self._context_id,
                state.operation_id,
            )
            return
        try:
            await session.write_stream(
                OutputSchema(
                    type=CONTEXT_COMPRESSION_STATE_TYPE,
                    index=0,
                    payload=state.model_dump(mode="json"),
                )
            )
            logger.debug(
                "context compression state stream emitted: session_id=%s context_id=%s op=%s status=%s",
                self._session_id,
                self._context_id,
                state.operation_id,
                state.status,
            )
        except Exception as exc:
            logger.warning(
                "failed to emit context compression state to session stream: session_id=%s context_id=%s op=%s "
                "status=%s error=%s",
                self._session_id,
                self._context_id,
                state.operation_id,
                state.status,
                exc,
                exc_info=True,
            )

    def build_state(
            self,
            state_input: ContextProcessorStateInput,
    ) -> ContextCompressionState:
        overhead_tokens = max(int(state_input.overhead_tokens or 0), 0)
        before_messages = list(state_input.before_messages or [])
        before_snapshot = self._before_snapshots.get(state_input.operation_id)
        if before_snapshot is None:
            before_snapshot = self.build_before_snapshot(
                before_messages,
                overhead_tokens,
                fallback_scale_factor=state_input.token_scale_factor,
                allow_scale=state_input.token_scale_eligible,
            )
            self._remember_before_snapshot(state_input.operation_id, before_snapshot)
        before_tokens = before_snapshot.tokens
        before_raw_tokens = before_snapshot.raw_tokens
        before_source = before_snapshot.source
        before = self._build_metric(
            before_messages,
            state_input.context_max,
            observed_at=state_input.started_at,
            overhead_tokens=overhead_tokens,
            tokens=before_tokens,
            token_source=before_source,
        )
        after = (
            self._build_metric(
                state_input.after_messages,
                state_input.context_max,
                observed_at=state_input.ended_at,
                overhead_tokens=overhead_tokens,
            )
            if state_input.after_messages is not None
            else None
        )
        if after is not None and before_snapshot.scale_factor is not None and before_raw_tokens > 0:
            # Keep after on the frozen before scale.  The before metric is
            # captured when the operation starts; using it here avoids
            # recalibrating with a stale usage baseline after set_messages().
            # A snapshot whose usage/raw ranges differ has no scale_factor and
            # therefore intentionally leaves after on the tokenizer scale.
            ratio = before_tokens / before_raw_tokens
            after_tokens = max(int(round(after.tokens * ratio)), 0)
            after = after.model_copy(update={
                "tokens": after_tokens,
                "context_percent": self._context_percent(after_tokens, state_input.context_max),
                "token_source": "tokenizer-calibrated",
            })
        saved = self._build_saved(before, after)
        statistic_messages = (
            state_input.after_messages
            if state_input.after_messages is not None
            else state_input.before_messages
        )
        return ContextCompressionState(
            operation_id=state_input.operation_id,
            status=state_input.status,
            phase=state_input.phase,
            processor=state_input.processor.processor_type() if state_input.processor is not None else "",
            model=self._resolve_model_name(
                state_input.processor,
                state_input.trigger,
                state_input.force,
                model_config=state_input.model_config,
                model_client_config=state_input.model_client_config,
            ),
            before=before,
            after=after,
            statistic=self._build_statistic(statistic_messages),
            saved=saved,
            compression_usage=self._build_compression_usage(state_input.compression_usage),
            duration_ms=(
                int((state_input.ended_at - state_input.started_at) * 1000)
                if state_input.ended_at is not None
                else None
            ),
            context_max=state_input.context_max,
            scale_factor=before_snapshot.scale_factor,
            summary=self._build_summary(_SummaryInput(
                status=state_input.status,
                before=before,
                after=after,
                saved=saved,
                reason=state_input.reason,
                messages_to_modify=state_input.messages_to_modify,
            )),
            compact_summary=state_input.compact_summary or "",
            error=state_input.error,
        )

    @staticmethod
    def _build_compression_usage(usage: Optional[dict[str, Any]]) -> Optional[ContextCompressionUsage]:
        if not usage:
            return None
        return ContextCompressionUsage(**usage)

    def _record(self, state: ContextCompressionState) -> None:
        self._history.append(state.model_dump(mode="json"))
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit:]

    def _build_metric(
            self,
            messages: Optional[list[BaseMessage]],
            context_max: Optional[int],
            *,
            observed_at: Optional[float],
            overhead_tokens: int = 0,
            tokens: Optional[int] = None,
            token_source: str = "tokenizer",
    ) -> ContextCompressionMetric:
        messages = list(messages or [])
        if tokens is None:
            tokens = self._measure_messages(messages) + max(int(overhead_tokens or 0), 0)
        return ContextCompressionMetric(
            time=self._format_time(observed_at),
            messages=len(messages),
            tokens=tokens,
            context_percent=self._context_percent(tokens, context_max),
            token_source=token_source,
        )

    @staticmethod
    def _usage_aware_tokens(messages: list[BaseMessage]) -> Optional[int]:
        """Model-reported cumulative usage plus the tail estimate.

        The last AssistantMessage's usage already covers the system prompt and
        tool schemas present at that request, so this is the same basis the
        compression trigger uses.
        """
        if not messages:
            return None
        try:
            from openjiuwen.core.context_engine.processor.forked.compressor.support.util import (
                count_usage_tokens_with_tail,
            )
        except Exception as exc:  # pragma: no cover - optional import path
            logger.debug("usage-aware token counter unavailable: %s", exc)
            return None
        try:
            return count_usage_tokens_with_tail(messages)
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.debug("usage-aware token count failed: %s", exc)
            return None

    def _measure_messages(self, messages: list[BaseMessage]) -> int:
        if self._token_counter:
            try:
                tokens = self._token_counter.count_messages(messages)
                if isinstance(tokens, int):
                    return tokens
            except Exception as exc:
                logger.debug("token_counter failed while measuring context processor state: %s", exc)
        try:
            from openjiuwen.core.context_engine.processor.forked.compressor.support.util import (
                estimate_content_tokens,
            )

            return sum(
                estimate_content_tokens(getattr(message, "content", ""))
                for message in messages
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.debug("rough token estimator unavailable: %s", exc)
            total_chars = sum(
                len(str(getattr(message, "content", "") or ""))
                for message in messages
            )
            return math.ceil(total_chars / 4)

    def _build_statistic(self, messages: Optional[list[BaseMessage]]) -> ContextStats:
        stat = ContextStats()
        for message in list(messages or []):
            stat.total_messages += 1
            tokens = self._count_message_for_statistic(message)
            if message.role == "assistant":
                stat.assistant_messages += 1
                stat.assistant_message_tokens += tokens
            elif message.role == "user":
                stat.user_messages += 1
                stat.user_message_tokens += tokens
            elif message.role == "system":
                stat.system_messages += 1
                stat.system_message_tokens += tokens
            elif message.role == "tool":
                stat.tool_messages += 1
                stat.tool_message_tokens += tokens
        stat.total_tokens += (
                stat.assistant_message_tokens +
                stat.user_message_tokens +
                stat.system_message_tokens +
                stat.tool_message_tokens
        )
        stat.total_dialogues = len(ContextUtils.find_all_dialogue_round(list(messages or [])))
        return stat

    def _count_message_for_statistic(self, message: BaseMessage) -> int:
        if self._token_counter is None:
            return 0
        try:
            tokens = self._token_counter.count(message.content or "")
            if isinstance(tokens, int):
                return tokens
        except Exception as exc:
            logger.debug("token_counter failed while building context processor statistic: %s", exc)
        return 0

    @staticmethod
    def _build_saved(
            before: ContextCompressionMetric,
            after: Optional[ContextCompressionMetric],
    ) -> Optional[ContextCompressionSaved]:
        if after is None:
            return None
        saved_messages = before.messages - after.messages
        saved_tokens = before.tokens - after.tokens
        saved_percent = 0
        if before.tokens > 0:
            saved_percent = round(saved_tokens / before.tokens * 100, 1)
        return ContextCompressionSaved(
            messages=saved_messages,
            tokens=saved_tokens,
            percent=saved_percent,
        )

    @staticmethod
    def _format_metric_for_log(metric: Optional[ContextCompressionMetric]) -> Optional[str]:
        if metric is None:
            return None
        return (
            f"messages={metric.messages} tokens={metric.tokens} "
            f"token_source={metric.token_source} "
            f"percent={metric.context_percent} time={metric.time}"
        )

    @staticmethod
    def _context_percent(tokens: int, context_max: Optional[int]) -> Optional[int]:
        if not context_max:
            return None
        return max(0, min(100, round(tokens / context_max * 100)))

    @staticmethod
    def _compact_number(value: Optional[int]) -> str:
        if value is None:
            return "unknown"
        abs_value = abs(value)
        if abs_value >= 1_000_000:
            return f"{value / 1_000_000:.1f}m".replace(".0m", "m")
        if abs_value >= 1_000:
            return f"{value / 1_000:.1f}k".replace(".0k", "k")
        return str(value)

    @staticmethod
    def _format_time(timestamp: Optional[float]) -> Optional[str]:
        if timestamp is None:
            return None
        return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="milliseconds")

    def _build_summary(
            self,
            summary_input: _SummaryInput,
    ) -> str:
        status = summary_input.status
        before = summary_input.before
        after = summary_input.after
        saved = summary_input.saved
        if status == "started":
            return f"Compressing {before.messages} messages, ~{self._compact_number(before.tokens)} tokens"
        if status == "failed":
            return (
                f"Context processor failed; context remains ~{self._compact_number(before.tokens)} tokens"
            )
        if after is None or saved is None:
            return f"Context processor skipped: {summary_input.reason}"
        if status == "noop":
            return (
                f"Context unchanged at ~{self._compact_number(after.tokens)} tokens "
                f"(saved {saved.percent:.1f}%)"
            )
        messages_to_modify = summary_input.messages_to_modify
        modified = f", modified {len(messages_to_modify)} messages" if messages_to_modify else ""
        return (
            f"Compressed {before.messages} -> {after.messages} messages, "
            f"~{self._compact_number(before.tokens)} -> ~{self._compact_number(after.tokens)} tokens"
            f", saved ~{self._compact_number(saved.tokens)} tokens ({saved.percent:.1f}%)"
            f"{modified}"
        )

    @staticmethod
    def _resolve_model_name(
            processor: Optional[ContextProcessor],
            trigger: str,
            force: bool,
            *,
            model_config: Any = None,
            model_client_config: Any = None,
    ) -> str:
        _ = trigger, force
        config = getattr(processor, "config", None)
        if config is None:
            return ""
        # Mirror PrefixCompactProcessor._auto_compression_executors: both passive
        # and forced compaction run on the dedicated auto_compression model pair
        # (primary = auto_compression.model_name, else the request model actually
        # used). The per-call model_config/model_client_config override (e.g. from
        # main-call overflow recovery) wins over the processor config, so the
        # reported name matches the model that really ran.
        auto_config = getattr(config, "auto_compression", None)
        request_config = model_config or getattr(config, "model", None)
        client_config = model_client_config or getattr(config, "model_client", None)
        if auto_config is not None and request_config is not None and client_config is not None:
            auto_primary = getattr(auto_config, "model_name", None)
            if isinstance(auto_primary, str) and auto_primary.strip():
                return auto_primary.strip()
            resolved = _first_model_name(request_config)
            if resolved:
                return resolved
            return ""
        resolved = _first_model_name(getattr(config, "model", None)) or _first_model_name(config)
        return resolved or ""


def _first_model_name(obj: Any) -> str:
    """Return the first non-empty string among ``obj.model_name`` / ``obj.model``."""
    if obj is None:
        return ""
    for key in ("model_name", "model"):
        value = getattr(obj, key, None)
        if isinstance(value, str) and value:
            return value
    return ""
