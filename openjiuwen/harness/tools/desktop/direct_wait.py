"""Bounded observable signals after an action; no inference of page readiness."""

from typing import Literal

from pydantic import Field, model_validator

from .schema import Action, Observation, StrictModel


class ReadinessWait(StrictModel):
    """An explicit visual-change or exact visible-control condition, never a task oracle."""

    signal: Literal["screen_change", "control_enabled"]
    label: str = Field(default="", max_length=120)
    timeout_seconds: float | int = Field(default=2, ge=0, le=2)

    @model_validator(mode="after")
    def check_label(self):
        """Require an exact control label only for control-based waits."""
        if (self.signal == "control_enabled") != bool(self.label):
            raise ValueError("label is required only for control_enabled")
        return self

    def met(self, before: Observation, current: Observation) -> bool:
        """Check the explicit condition against the latest observation."""
        if self.signal == "screen_change":
            return before.png_b64 != current.png_b64
        return any(e.get("label") == self.label and e.get("enabled") and e.get("actionable") for e in current.elements)


async def wait_for_signal(state, condition: ReadinessWait, before: Observation) -> tuple[dict, dict]:
    """Poll through ordinary bounded actions, so recipient checks and budgets stay effective."""
    import time

    started = time.monotonic()
    result = state.result(include_image=True)
    polls = 0
    reason = ""
    while not state.closed and not condition.met(before, state.observation):
        remaining = condition.timeout_seconds - (time.monotonic() - started)
        if remaining <= 0 or polls >= 8:
            reason = "signal timeout"
            break
        if (
            state.steps >= state.settings.budgets.max_steps
            or state.observations >= state.settings.budgets.max_steps + 1
        ):
            reason = "remaining native action or observation budget exhausted"
            break
        result = await state.execute("act", action=Action(kind="wait", seconds=min(0.1, remaining)))
        polls += 1
    details = {
        "signal": condition.signal,
        "met": not state.closed and condition.met(before, state.observation),
        "polls": polls,
        "elapsed": time.monotonic() - started,
        "reason": reason or state.reason,
    }
    state.emit("wait_signal", **details)
    return result, details
