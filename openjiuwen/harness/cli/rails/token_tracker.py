"""Token usage tracking rail.

Hooks into ``after_model_call`` to accumulate token counts from the
LLM response.  Provides :meth:`get_summary` for ``/status`` and
``/cost`` commands.
"""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.core.single_agent.rail.base import AgentRail


class TokenTrackingRail(AgentRail):
    """Accumulate token usage across model calls.

    This rail hooks into the SDK rail framework via
    ``after_model_call`` and tracks prompt / completion tokens.

    Attributes:
        total_input_tokens: Sum of prompt / input tokens.
        total_output_tokens: Sum of completion / output tokens.
        call_count: Number of model calls tracked.
    """

    priority = 10  # low priority — run after other rails

    def __init__(self) -> None:
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.call_count: int = 0

    async def after_model_call(self, ctx: Any) -> None:
        """Extract token usage from the model response.

        The response object is expected to carry a ``usage`` attribute
        with ``prompt_tokens`` and ``completion_tokens`` fields.
        """
        self.call_count += 1
        response = getattr(ctx.inputs, "response", None)
        if response is None:
            return
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.total_input_tokens += getattr(
            usage, "prompt_tokens", 0
        ) or 0
        self.total_output_tokens += getattr(
            usage, "completion_tokens", 0
        ) or 0

    def get_summary(self) -> Dict[str, Any]:
        """Return a dict suitable for ``/status`` and ``/cost``."""
        return {
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "total_tokens": (
                self.total_input_tokens + self.total_output_tokens
            ),
            "model_calls": self.call_count,
        }
