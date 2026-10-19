# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Trusted request counts for PA-11, with legacy capacity parsing retained for compatibility.

PA-11 gets the active model window from ContextEngine and applies its own 80% rollover threshold.
Execution and construction do not call the legacy limit parser or require its configuration fields.
A count is trusted only when the counter reports it as exact (not estimated, no fallback) and counts
what the model is actually sent; anything else is unknown, never zero.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.context_engine.content_sanitize import sanitize_value_for_text

if TYPE_CHECKING:
    from openjiuwen.core.context_engine.token import TokenCounter

    from ..schema.spec import PersistentAgentSpec

# Sources core counters report for fallbacks; they are estimates whatever their flags say.
_FALLBACK_SOURCES = frozenset(
    {"string_length_fallback", "tiktoken_fallback", "family_tokenizer_fallback", "not_reported"}
)


@dataclass(frozen=True)
class CapacityLimits:
    """Resolved limits of one model, in tokens."""

    context_window: int
    output_reserve: int
    input_limit: int
    safety_margin: int

    @property
    def available_input(self) -> int:
        """The most input tokens one request may carry."""
        return min(self.input_limit, self.context_window - self.output_reserve - self.safety_margin)


def _reject(message: str):
    return build_error(StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, error_msg=message)


def _positive(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def capacity_limits(spec: "PersistentAgentSpec") -> CapacityLimits | None:
    """Parse legacy limits for direct callers; execution and construction do not use this helper.

    Setting either Persistent field asks for the check; then every value must be present and the
    combination possible, otherwise the configuration is refused.
    """
    input_limit, margin = spec.model_input_limit_tokens, spec.capacity_safety_margin_tokens
    if input_limit is None and margin is None:
        return None
    request = spec.model.model_request_config if spec.model is not None else None
    window = getattr(request, "context_window", None)
    reserve = getattr(request, "max_tokens", None)
    if not _positive(window):
        raise _reject("capacity check requires model.model_request_config.context_window")
    if not _positive(reserve):
        raise _reject("capacity check requires model.model_request_config.max_tokens")
    if not _positive(input_limit):
        raise _reject("capacity check requires model_input_limit_tokens")
    if margin is None:
        raise _reject("capacity check requires capacity_safety_margin_tokens")
    if input_limit > window:
        raise _reject("model_input_limit_tokens cannot exceed context_window")
    if reserve + margin >= window:
        raise _reject("max_tokens plus capacity_safety_margin_tokens leave no room for input")
    return CapacityLimits(window, reserve, input_limit, margin)


def count_request(counter: "TokenCounter", messages: list, tools: list | None, model: str = "") -> int | None:
    """Input tokens of the final request, or None when they cannot be counted reliably.

    The final messages already include the system prompt, so messages and tool definitions are each
    counted once. Non-text message content is unknown. So is text the counters would rewrite before
    counting: they replace image data URLs with short placeholders, while the model is sent the
    original. A count the counter itself reports as estimated, or made through a fallback, is unknown.
    """
    for message in messages:
        content = getattr(message, "content", "")
        if not isinstance(content, str) or sanitize_value_for_text(content) != content:
            return None
    try:
        counted = [
            counter.measure_messages(messages, model=model),
            counter.measure_tools(list(tools or []), model=model),
        ]
    except Exception:  # noqa: BLE001 - a counter failure means the capacity is unknown, not that it fits
        return None
    if any(item.estimated or item.fallback_reason or item.source in _FALLBACK_SOURCES for item in counted):
        return None
    return sum(item.tokens for item in counted)
