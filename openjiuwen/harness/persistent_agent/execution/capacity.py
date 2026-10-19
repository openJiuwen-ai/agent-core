# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Context-window counting for PA-11, with legacy capacity parsing retained for compatibility.

PA-11 gets the active model window from ContextEngine and applies its own 80% rollover threshold.
Execution and construction do not call the legacy limit parser or require its configuration fields.
Request counts use the shared ContextEngine policy: model-reported usage plus an estimated appended
tail, otherwise the selected tokenizer with character-estimate fallback.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.context_engine.usage.context_tokens import count_context_window_tokens

if TYPE_CHECKING:
    from openjiuwen.core.context_engine.token import TokenCounter

    from ..schema.spec import PersistentAgentSpec

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


def count_request(counter: "TokenCounter | None", messages: list, tools: list | None) -> int:
    """Count a final request using the ContextEngine compressor's shared policy."""
    return count_context_window_tokens(
        messages,
        list(tools or []),
        counter,
        "PersistentAgent",
    )
