# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the cua rails."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.single_agent.rail.base import ToolCallInputs
from openjiuwen.harness.tools.cua.desktop_tools import CuaDesktop
from openjiuwen.harness.tools.cua.rails import (
    CuaProgressRail,
    CuaRepeatFailureRail,
    CuaRuntimeRail,
    CuaScreenshotDownscaleRail,
    CuaUserTakeoverRail,
)

# The native cua tools are registered under their bare names.
_PREFIX = ""


def _ctx(session_id: str = "session-1") -> SimpleNamespace:
    session = MagicMock()
    session.get_session_id.return_value = session_id
    return SimpleNamespace(agent=MagicMock(), session=session)


class _ScreenBackend:
    """Just enough backend for the preflight: it only reads the screen size."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def screen_size(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return 1920, 1080


@pytest.mark.asyncio
async def test_preflight_fails_loud_with_remediation_when_the_desktop_is_unusable() -> None:
    # A missing extra or display must stop the run before the first model
    # step, not surface as a ❌ on the model's first action.
    backend = _ScreenBackend(ModuleNotFoundError("No module named 'pyautogui'"))
    rail = CuaRuntimeRail(CuaDesktop(lambda: backend))

    with pytest.raises(BaseError) as excinfo:
        await rail.before_invoke(_ctx())

    message = str(excinfo.value)
    assert "pyautogui" in message
    assert "uv sync --extra cua" in message


@pytest.mark.asyncio
async def test_preflight_fails_loud_when_no_backend_exists_for_the_platform() -> None:
    def _unsupported():
        raise RuntimeError("cua desktop tools have no backend for platform 'darwin'")

    rail = CuaRuntimeRail(CuaDesktop(_unsupported))

    with pytest.raises(BaseError, match="darwin"):
        await rail.before_invoke(_ctx())


@pytest.mark.asyncio
async def test_preflight_is_cached_after_the_first_success() -> None:
    # before_invoke re-fires on every loop entry, including each approval
    # re-entry; the probe must not repeat once the desktop is known good.
    backend = _ScreenBackend()
    rail = CuaRuntimeRail(CuaDesktop(lambda: backend))

    for _ in range(3):
        await rail.before_invoke(_ctx())

    assert backend.calls == 1


@pytest.mark.asyncio
async def test_a_failed_preflight_is_retried_on_the_next_invoke() -> None:
    backend = _ScreenBackend(OSError("display not reachable"))
    rail = CuaRuntimeRail(CuaDesktop(lambda: backend))

    with pytest.raises(BaseError):
        await rail.before_invoke(_ctx())
    backend.error = None
    await rail.before_invoke(_ctx())

    assert backend.calls == 2


# ---------------------------------------------------------------------------
# CuaUserTakeoverRail
# ---------------------------------------------------------------------------


def _tool_ctx(tool_name: str, tool_args) -> SimpleNamespace:
    # A real ToolCallInputs: the rail narrows on it before touching tool_args,
    # so a duck-typed stand-in would pass the test while skipping the rail.
    inputs = ToolCallInputs(
        tool_call=SimpleNamespace(id="call-1"),
        tool_name=tool_name,
        tool_args=tool_args,
    )
    return SimpleNamespace(inputs=inputs, extra={})


# ── CuaUserTakeoverRail ──────────────────────────────────────────────────
#
# "Once the user takes over the screen, the agent should pause." The rail
# must hold desktop actions while the OS reports recent user input, release
# them once the desktop is idle again, and give up (with a report) rather
# than wait forever. All timing is driven by a fake clock and probe.


class _FakeDesktop:
    """Scripted probe + clock: ``idle_values`` are returned per poll in order."""

    def __init__(self, idle_values):
        self.idle_values = list(idle_values)
        self.now = 100.0
        self.slept = 0.0

    def seconds_since_last_input(self):
        if len(self.idle_values) > 1:
            return self.idle_values.pop(0)
        return self.idle_values[0]

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.slept += seconds
        self.now += seconds


def _takeover_rail(desk: _FakeDesktop, **overrides) -> CuaUserTakeoverRail:
    kwargs = dict(
        probe=desk,
        active_within_s=3.0,
        resume_after_idle_s=5.0,
        max_wait_s=10.0,
        poll_interval_s=1.0,
        sleep=desk.sleep,
        monotonic=desk.monotonic,
    )
    kwargs.update(overrides)
    return CuaUserTakeoverRail(_PREFIX, **kwargs)


def _finish_call(ctx, text: str = "\u2705 ok") -> None:
    ctx.inputs.tool_result = {"content": text}
    ctx.inputs.tool_msg = ToolMessage(content=text, tool_call_id="call-1")


@pytest.mark.asyncio
async def test_idle_desktop_never_delays_an_action() -> None:
    desk = _FakeDesktop([42.0])
    rail = _takeover_rail(desk)
    ctx = _tool_ctx("click", {"pid": 1})

    await rail.before_tool_call(ctx)
    _finish_call(ctx)
    await rail.after_tool_call(ctx)

    assert desk.slept == 0
    assert not ctx.extra.get("_skip_tool")
    assert "held" not in ctx.inputs.tool_msg.content


@pytest.mark.asyncio
async def test_action_is_held_while_the_user_is_active_then_released() -> None:
    # User active (idle 0.5s, 1.0s), then hands off: idle grows past 5s.
    desk = _FakeDesktop([0.5, 1.0, 2.0, 6.0])
    rail = _takeover_rail(desk)
    ctx = _tool_ctx("type_text", {"pid": 1, "text": "hi"})

    await rail.before_tool_call(ctx)

    assert not ctx.extra.get("_skip_tool")
    assert desk.slept == 3.0  # three polls before the desktop was idle >= 5s
    _finish_call(ctx)
    await rail.after_tool_call(ctx)
    assert "held for 3s" in ctx.inputs.tool_msg.content
    assert "re-capture" in ctx.inputs.tool_msg.content


@pytest.mark.asyncio
async def test_a_takeover_that_outlasts_the_budget_ends_with_a_report() -> None:
    desk = _FakeDesktop([0.2])  # user never stops
    rail = _takeover_rail(desk)
    ctx = _tool_ctx("click", {"pid": 1})

    await rail.before_tool_call(ctx)

    assert ctx.extra["_skip_tool"] is True
    assert desk.slept == 10.0
    assert "taken over the desktop" in ctx.inputs.tool_result["error"]
    assert ctx.inputs.tool_msg.tool_call_id == "call-1"


@pytest.mark.asyncio
async def test_the_agents_own_foreground_input_is_not_mistaken_for_the_user() -> None:
    # Under foreground delivery the agent's click is a real OS input event.
    # After a click 2.0s ago, the OS reports idle=2.0s: that is the agent.
    desk = _FakeDesktop([2.0])
    rail = _takeover_rail(desk)
    click = _tool_ctx("click", {"pid": 1})
    _finish_call(click)
    await rail.after_tool_call(click)  # records own input at now=100
    desk.now = 102.0

    ctx = _tool_ctx("get_window_state", {"pid": 1})
    await rail.before_tool_call(ctx)

    assert desk.slept == 0
    assert not ctx.extra.get("_skip_tool")


@pytest.mark.asyncio
async def test_user_input_after_the_agents_own_input_still_pauses() -> None:
    # Agent clicked 2.0s ago but the OS saw input 0.3s ago: that is the user.
    desk = _FakeDesktop([0.3, 0.3, 9.0])
    rail = _takeover_rail(desk)
    click = _tool_ctx("click", {"pid": 1})
    _finish_call(click)
    await rail.after_tool_call(click)
    desk.now = 102.0

    ctx = _tool_ctx("scroll", {"pid": 1})
    await rail.before_tool_call(ctx)

    assert desk.slept == 2.0
    assert not ctx.extra.get("_skip_tool")


@pytest.mark.asyncio
async def test_a_slow_driver_round_trip_does_not_turn_the_agents_own_click_into_a_takeover() -> None:
    # The OS input event happens when the driver injects the click, shortly
    # after dispatch; the rail only learns the call is over when the MCP
    # round-trip returns. Timestamping own input at return time made every
    # round-trip slower than the grace window look like the user.
    desk = _FakeDesktop([42.0, 2.4])
    rail = _takeover_rail(desk)
    click = _tool_ctx("click", {"pid": 1})
    await rail.before_tool_call(click)  # dispatched at now=100, injected ~100.2
    desk.now = 102.5  # driver took 2.5s to answer
    _finish_call(click)
    await rail.after_tool_call(click)
    desk.now = 102.6

    ctx = _tool_ctx("get_window_state", {"pid": 1})
    await rail.before_tool_call(ctx)  # OS: last input 2.4s ago = the click

    assert desk.slept == 0
    assert not ctx.extra.get("_skip_tool")


@pytest.mark.asyncio
async def test_a_long_type_text_is_attributed_to_the_agent_until_its_last_keystroke() -> None:
    # Typing a long string keeps injecting keystrokes until the call returns,
    # so the last OS event sits near the END of the round-trip, not the start.
    desk = _FakeDesktop([42.0, 0.3])
    rail = _takeover_rail(desk)
    typing = _tool_ctx("type_text", {"pid": 1, "text": "a long paragraph"})
    await rail.before_tool_call(typing)  # dispatched at now=100
    desk.now = 103.0  # last keystroke landed ~102.9
    _finish_call(typing)
    await rail.after_tool_call(typing)
    desk.now = 103.2

    ctx = _tool_ctx("get_window_state", {"pid": 1})
    await rail.before_tool_call(ctx)  # OS: last input 0.3s ago = our keystroke

    assert desk.slept == 0
    assert not ctx.extra.get("_skip_tool")


@pytest.mark.asyncio
async def test_a_rejected_input_call_does_not_widen_the_agents_own_input_window() -> None:
    # A call this rail rejected never reached the desktop. Recording it as own
    # input would stretch the attribution window over the user's activity.
    desk = _FakeDesktop([0.2])  # user never stops
    rail = _takeover_rail(desk)
    click = _tool_ctx("click", {"pid": 1})
    await rail.before_tool_call(click)
    assert click.extra["_skip_tool"] is True
    await rail.after_tool_call(click)

    assert rail._last_own_input_at is None


@pytest.mark.asyncio
async def test_rail_is_inert_without_a_probe_and_for_non_cua_tools() -> None:
    desk = _FakeDesktop([0.1])
    no_probe = CuaUserTakeoverRail(_PREFIX, probe=None, sleep=desk.sleep, monotonic=desk.monotonic)
    no_probe._probe = None  # platform without a probe
    ctx = _tool_ctx("click", {"pid": 1})
    await no_probe.before_tool_call(ctx)
    assert not ctx.extra.get("_skip_tool") and desk.slept == 0

    rail = _takeover_rail(desk)
    other = _tool_ctx("read_file", {"path": "x"})
    await rail.before_tool_call(other)
    assert desk.slept == 0 and not other.extra.get("_skip_tool")


def test_takeover_thresholds_are_validated() -> None:
    with pytest.raises(ValueError):
        CuaUserTakeoverRail(_PREFIX, probe=_FakeDesktop([1.0]), max_wait_s=0)


@pytest.mark.asyncio
async def test_non_tool_call_inputs_are_ignored() -> None:
    rail = CuaRepeatFailureRail(_PREFIX)
    ctx = SimpleNamespace(inputs={"tool_name": "click"}, extra={})

    await rail.before_tool_call(ctx)  # must not raise on a non-ToolCallInputs payload

    assert ctx.extra == {}


# ---------------------------------------------------------------------------
# CuaScreenshotDownscaleRail
# ---------------------------------------------------------------------------


def _noise_png_data_url(width: int = 600, height: int = 400) -> str:
    """Random-noise PNG: incompressible, so it reliably exceeds the budget."""
    import base64
    import io
    import os

    from PIL import Image

    img = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _screenshot_ctx(tool_name: str, tool_result) -> SimpleNamespace:
    from openjiuwen.core.foundation.llm import ToolMessage

    inputs = ToolCallInputs(
        tool_call=SimpleNamespace(id="call-1"),
        tool_name=tool_name,
        tool_args={},
    )
    inputs.tool_result = tool_result
    inputs.tool_msg = ToolMessage(content="tree summary", tool_call_id="call-1")
    return SimpleNamespace(inputs=inputs, extra={})


@pytest.mark.asyncio
async def test_oversized_screenshot_is_downscaled_in_place() -> None:
    pytest.importorskip("PIL")
    original = _noise_png_data_url()
    item = {"type": "image", "source": "mcp", "mime_type": "image/png", "data_url": original}
    tool_result = SimpleNamespace(data={"content": "tree", "multimodal": [item]})
    ctx = _screenshot_ctx("get_window_state", tool_result)

    rail = CuaScreenshotDownscaleRail(_PREFIX)
    await rail.after_tool_call(ctx)

    # In-place mutation: react_agent consumes the same result object the
    # ability manager returned, so a replaced object would silently be lost.
    assert ctx.inputs.tool_result is tool_result
    assert tool_result.data["multimodal"][0] is item
    assert item["data_url"].startswith("data:image/jpeg;base64,")
    assert len(item["data_url"]) < len(original)
    assert item["mime_type"] == "image/jpeg"
    # 600x400 fits within max_edge: re-encode without resize keeps the
    # coordinate space, so no scale note may appear (it would be false).
    assert "[screenshot scaled]" not in ctx.inputs.tool_msg.content


@pytest.mark.asyncio
async def test_resized_screenshot_appends_scale_note_to_tool_message() -> None:
    # A model reading pixels off a resized screenshot misclicks by the scale
    # factor unless told; the note must reach the ALREADY-BUILT ToolMessage,
    # not just data["content"] (live-confirmed failure mode: coordinates were
    # read off an 800px image of a ~1330px window).
    pytest.importorskip("PIL")
    original = _noise_png_data_url(2000, 1200)
    item = {"type": "image", "source": "mcp", "mime_type": "image/png", "data_url": original}
    tool_result = SimpleNamespace(data={"content": "tree summary", "multimodal": [item]})
    ctx = _screenshot_ctx("get_window_state", tool_result)

    rail = CuaScreenshotDownscaleRail(_PREFIX)
    await rail.after_tool_call(ctx)

    assert item["data_url"].startswith("data:image/jpeg;base64,")
    note = ctx.inputs.tool_msg.content
    assert "[screenshot scaled]" in note
    assert "2000x1200" in note
    assert "multiplied by 2.50" in note  # 2000 -> 800px rung for incompressible noise
    assert "[screenshot scaled]" in tool_result.data["content"]


@pytest.mark.asyncio
async def test_small_screenshot_is_left_untouched() -> None:
    small = "data:image/png;base64,QQ=="
    item = {"type": "image", "mime_type": "image/png", "data_url": small}
    tool_result = SimpleNamespace(data={"content": "tree", "multimodal": [item]})
    ctx = _screenshot_ctx("get_window_state", tool_result)

    await CuaScreenshotDownscaleRail(_PREFIX).after_tool_call(ctx)

    assert item["data_url"] == small
    assert item["mime_type"] == "image/png"


@pytest.mark.asyncio
async def test_non_cua_tools_are_ignored() -> None:
    pytest.importorskip("PIL")
    original = _noise_png_data_url()
    item = {"type": "image", "mime_type": "image/png", "data_url": original}
    tool_result = SimpleNamespace(data={"content": "x", "multimodal": [item]})
    ctx = _screenshot_ctx("read_file", tool_result)

    await CuaScreenshotDownscaleRail(_PREFIX).after_tool_call(ctx)

    assert item["data_url"] == original


@pytest.mark.asyncio
async def test_a_non_empty_tool_prefix_is_honoured() -> None:
    # The same rail code serves prefixed registrations (e.g. MCP-served tools).
    pytest.importorskip("PIL")
    original = _noise_png_data_url()
    item = {"type": "image", "mime_type": "image/png", "data_url": original}
    tool_result = SimpleNamespace(data={"content": "x", "multimodal": [item]})
    ctx = _screenshot_ctx("mcp_cua-driver-run7_get_window_state", tool_result)

    await CuaScreenshotDownscaleRail("mcp_cua-driver-run7_").after_tool_call(ctx)

    assert item["data_url"].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_legacy_result_shape_is_a_noop() -> None:
    # Blind-mode results are {"result": str} dicts with no .data attribute.
    ctx = _screenshot_ctx("get_window_state", {"result": "snapshot text"})

    await CuaScreenshotDownscaleRail(_PREFIX).after_tool_call(ctx)

    assert ctx.inputs.tool_result == {"result": "snapshot text"}


@pytest.mark.asyncio
async def test_malformed_data_url_is_left_untouched() -> None:
    # Oversized so the decode path actually runs; not valid base64 image data.
    malformed = "data:image/png;base64," + "?" * 300_000
    item = {"type": "image", "mime_type": "image/png", "data_url": malformed}
    tool_result = SimpleNamespace(data={"content": "x", "multimodal": [item]})
    ctx = _screenshot_ctx("get_window_state", tool_result)

    await CuaScreenshotDownscaleRail(_PREFIX).after_tool_call(ctx)

    assert item["data_url"] == malformed


def _result_ctx(tool_name: str, tool_args, content: str) -> SimpleNamespace:
    """A ToolCallInputs carrying a tool response, as after_tool_call sees it."""
    inputs = ToolCallInputs(
        tool_call=SimpleNamespace(id="call-1"),
        tool_name=tool_name,
        tool_args=tool_args,
    )
    inputs.tool_result = SimpleNamespace(data={"content": content})
    inputs.tool_msg = ToolMessage(content=content, tool_call_id="call-1")
    return SimpleNamespace(inputs=inputs, extra={})


# ---------------------------------------------------------------------------
# CuaRepeatFailureRail
# ---------------------------------------------------------------------------


_FAILURE = "\u274c window_id 8 is not a window of pid 1. Enumerate with list_windows(pid=1)."
_UNVERIFIED = "Sent tab to pid 44452 (not verified)."
_SUCCESS = "\u2705 Sent tab on pid 44452 window 7."


async def _replay(rail, tool_name, args, content, times):
    """Drive the same call through the rail `times` times, returning contexts."""
    seen = []
    for _ in range(times):
        ctx = _result_ctx(tool_name, dict(args) if isinstance(args, dict) else args, content)
        await rail.before_tool_call(ctx)
        if not ctx.extra.get("_skip_tool"):
            await rail.after_tool_call(ctx)
        seen.append(ctx)
    return seen


@pytest.mark.asyncio
async def test_identical_failures_are_blocked_before_reaching_the_driver() -> None:
    # One recorded run burned 172 turns and 510s re-sending the same rejected
    # type_text 55 times and ended on the error it started with. Desktop errors
    # arrive as ordinary tool results, so no retry rail ever sees them.
    rail = CuaRepeatFailureRail(_PREFIX)
    args = {"pid": 1, "window_id": 8, "x": 1, "y": 2, "text": "hi"}

    ctxs = await _replay(rail, "type_text", args, _FAILURE, 7)

    # Blocking starts on the 7th attempt: 6 identical failures were recorded
    # before it, which is the block_after threshold.
    assert not any(c.extra.get("_skip_tool") for c in ctxs[:6])
    assert ctxs[6].extra["_skip_tool"] is True
    assert "Blocked" in ctxs[6].inputs.tool_result["error"]
    # The model is told what to do instead, not merely refused.
    assert "get_window_state" in ctxs[6].inputs.tool_result["error"]


@pytest.mark.asyncio
async def test_the_model_is_warned_before_it_is_blocked() -> None:
    # A warning the model can act on is worth more than a block it cannot, so
    # the advisory lands well before the hard stop.
    rail = CuaRepeatFailureRail(_PREFIX)
    args = {"pid": 1, "text": "hi"}

    ctxs = await _replay(rail, "type_text", args, _FAILURE, 3)

    assert not any(c.extra.get("_skip_tool") for c in ctxs)
    # Annotated in place on both the result data and the already-built message.
    assert "will not change the result" in ctxs[2].inputs.tool_msg.content
    assert "will not change the result" in ctxs[2].inputs.tool_result.data["content"]
    # ...and only the third one is annotated.
    assert "will not change the result" not in ctxs[0].inputs.tool_msg.content


@pytest.mark.asyncio
async def test_repeated_successes_are_never_blocked() -> None:
    # The most-repeated identical calls in the corpus are legitimate: one run
    # sent press_key tab 27 times walking a form. Repetition alone must never
    # trip the breaker, or real workflows break.
    rail = CuaRepeatFailureRail(_PREFIX)
    args = {"pid": 44452, "key": "tab"}

    ctxs = await _replay(rail, "press_key", args, _SUCCESS, 27)

    assert not any(c.extra.get("_skip_tool") for c in ctxs)
    assert all("will not change the result" not in c.inputs.tool_msg.content for c in ctxs)


@pytest.mark.asyncio
async def test_a_success_clears_the_history_for_that_call() -> None:
    # If the same call starts working, whatever the model was stuck on has
    # moved; a later unrelated failure must start from a clean count rather
    # than inheriting a block from earlier in the run.
    rail = CuaRepeatFailureRail(_PREFIX)
    args = {"pid": 1, "text": "hi"}

    await _replay(rail, "type_text", args, _FAILURE, 5)
    await _replay(rail, "type_text", args, _SUCCESS, 1)
    ctxs = await _replay(rail, "type_text", args, _FAILURE, 3)

    assert not any(c.extra.get("_skip_tool") for c in ctxs)


@pytest.mark.asyncio
async def test_differing_arguments_are_tracked_separately() -> None:
    # Changing the arguments is exactly the recovery the block asks for, so a
    # different call must not inherit the failed one's count.
    rail = CuaRepeatFailureRail(_PREFIX)

    await _replay(rail, "type_text", {"pid": 1, "text": "a"}, _FAILURE, 6)
    ctxs = await _replay(rail, "type_text", {"pid": 1, "text": "b"}, _FAILURE, 1)

    assert not ctxs[0].extra.get("_skip_tool")


@pytest.mark.asyncio
async def test_session_identity_is_not_part_of_the_repeat_key() -> None:
    # Concurrent agents run under distinct driver session keys; if session
    # were part of the key the breaker could silently never fire when the
    # identity shifts mid-run.
    rail = CuaRepeatFailureRail(_PREFIX)
    for i in range(7):
        ctx = _result_ctx(
            "type_text",
            {"pid": 1, "text": "hi", "session": f"cua-{i}"},
            _FAILURE,
        )
        await rail.before_tool_call(ctx)
        if not ctx.extra.get("_skip_tool"):
            await rail.after_tool_call(ctx)

    assert ctx.extra["_skip_tool"] is True


@pytest.mark.asyncio
async def test_counters_reset_between_invokes() -> None:
    # Counters are per-invoke: a new task must not start life already blocked
    # by the previous one's failures.
    rail = CuaRepeatFailureRail(_PREFIX)
    args = {"pid": 1, "text": "hi"}

    await _replay(rail, "type_text", args, _FAILURE, 6)
    await rail.before_invoke(_ctx())
    ctxs = await _replay(rail, "type_text", args, _FAILURE, 1)

    assert not ctxs[0].extra.get("_skip_tool")


@pytest.mark.asyncio
async def test_a_skipped_call_is_not_counted_as_a_driver_response() -> None:
    # Another rail's rejection (e.g. the background-only delivery gate) never
    # reached the driver, so counting it would blame the driver for a local
    # policy decision.
    rail = CuaRepeatFailureRail(_PREFIX)
    for _ in range(9):
        ctx = _result_ctx("click", {"pid": 1}, "rejected by policy")
        ctx.extra["_skip_tool"] = True
        await rail.after_tool_call(ctx)

    fresh = _result_ctx("click", {"pid": 1}, "rejected by policy")
    await rail.before_tool_call(fresh)

    assert not fresh.extra.get("_skip_tool")


def test_invalid_thresholds_fail_at_construction() -> None:
    with pytest.raises(ValueError):
        CuaRepeatFailureRail(_PREFIX, advise_after=5, block_after=2)
    with pytest.raises(ValueError):
        CuaRepeatFailureRail(_PREFIX, advise_after=0, block_after=3)


# --- CuaProgressRail ---------------------------------------------------


async def _snap(rail, tool_short, args, content):
    """Feed one completed cua tool call through the rail under test."""
    ctx = _result_ctx(_PREFIX + tool_short, dict(args), content)
    await rail.after_tool_call(ctx)
    return ctx


_WIN_A = "\u2705 Captured pid 1 window 2 ('Notepad'). Screenshot 400x300 attached."
_WIN_B = "\u2705 Captured pid 9 window 10 ('Calculator'). Screenshot 320x500 attached."


class _FakeSession:
    """Minimal dict-backed session double: enough for get_state/update_state."""

    def __init__(self) -> None:
        self._state: dict = {}

    def get_state(self, key):
        return self._state.get(key)

    def update_state(self, patch: dict) -> None:
        self._state.update(patch)


def _invoke_ctx(session=None) -> SimpleNamespace:
    return SimpleNamespace(agent=MagicMock(), session=session or _FakeSession())


def _after_invoke_ctx(result: dict, session=None) -> SimpleNamespace:
    inputs = SimpleNamespace(result=result)
    return SimpleNamespace(inputs=inputs, session=session or _FakeSession())


@pytest.mark.asyncio
async def test_current_window_tracks_the_latest_snapshot() -> None:
    rail = CuaProgressRail(_PREFIX)
    await rail.before_invoke(_invoke_ctx())

    await _snap(rail, "get_window_state", {"pid": 1, "window_id": 2}, _WIN_A)
    await _snap(rail, "get_window_state", {"pid": 9, "window_id": 10}, _WIN_B)

    result: dict = {"output": "..."}
    await rail.after_invoke(_after_invoke_ctx(result))

    assert result["cua_result"]["current_window"] == {"pid": 9, "window_id": 10}


@pytest.mark.asyncio
async def test_blockers_accumulate_for_non_success_action_responses() -> None:
    rail = CuaProgressRail(_PREFIX)
    await rail.before_invoke(_invoke_ctx())

    ctx = _result_ctx("type_text", {"pid": 1, "text": "hi"}, _FAILURE)
    await rail.after_tool_call(ctx)

    result: dict = {"output": "..."}
    await rail.after_invoke(_after_invoke_ctx(result))

    assert result["cua_result"]["blockers"] == [f"type_text: {_FAILURE}"]


@pytest.mark.asyncio
async def test_unverified_action_responses_are_not_blockers() -> None:
    # No ✅ and no ❌: the action ran but the driver could not verify it.
    # That is not a failure to report to a parent; only the failure marker is.
    rail = CuaProgressRail(_PREFIX)
    await rail.before_invoke(_invoke_ctx())

    await rail.after_tool_call(_result_ctx("press_key", {"pid": 1, "key": "tab"}, _UNVERIFIED))

    result: dict = {"output": "..."}
    await rail.after_invoke(_after_invoke_ctx(result))

    assert result["cua_result"]["blockers"] == []


@pytest.mark.asyncio
async def test_snapshot_and_freshness_neutral_responses_are_never_blockers() -> None:
    # get_window_state and read-only observers never carry the driver's
    # success marker even when they succeed, so they must be excluded from
    # blocker detection or every successful snapshot would look "blocked".
    rail = CuaProgressRail(_PREFIX)
    await rail.before_invoke(_invoke_ctx())

    await _snap(rail, "get_window_state", {"pid": 1, "window_id": 2}, _WIN_A)
    await rail.after_tool_call(_result_ctx("list_windows", {}, "pid=1 title=Notepad"))

    result: dict = {"output": "..."}
    await rail.after_invoke(_after_invoke_ctx(result))

    assert result["cua_result"]["blockers"] == []


@pytest.mark.asyncio
async def test_after_invoke_writes_cua_result_onto_the_answer_payload() -> None:
    rail = CuaProgressRail(_PREFIX)
    await rail.before_invoke(_invoke_ctx())
    await _snap(rail, "get_window_state", {"pid": 1, "window_id": 2}, _WIN_A)
    await rail.after_tool_call(_result_ctx("type_text", {"pid": 1}, _FAILURE))

    result: dict = {"output": "partial progress"}
    await rail.after_invoke(_after_invoke_ctx(result))

    cua_result = result["cua_result"]
    assert cua_result["status"] == "partial"
    assert cua_result["current_window"] == {"pid": 1, "window_id": 2}
    assert cua_result["blockers"] == [f"type_text: {_FAILURE}"]
    assert cua_result["recommended_recovery"]
    assert cua_result["resume_count"] == 1


@pytest.mark.asyncio
async def test_after_invoke_status_is_completed_with_no_signals() -> None:
    rail = CuaProgressRail(_PREFIX)
    await rail.before_invoke(_invoke_ctx())

    result: dict = {"output": "done"}
    await rail.after_invoke(_after_invoke_ctx(result))

    assert result["cua_result"]["status"] == "completed"
    assert result["cua_result"]["recommended_recovery"] is None


def _max_iterations_result() -> dict:
    # The shape ReActAgent._finish_max_iterations returns.
    return {
        "output": "Max iterations reached without completion",
        "result_type": "error",
        "termination_reason": "max_iterations",
        "truncated_by_max_iterations": True,
    }


@pytest.mark.asyncio
async def test_a_run_cut_off_at_the_iteration_limit_is_not_completed() -> None:
    # No blocker does not mean done: a run whose model calls kept failing (or
    # that simply ran out of budget) records no action blockers. Reporting it
    # "completed" told the parent the task was finished and not retryable.
    rail = CuaProgressRail(_PREFIX)
    await rail.before_invoke(_invoke_ctx())

    result = _max_iterations_result()
    await rail.after_invoke(_after_invoke_ctx(result))

    assert result["cua_result"]["status"] == "partial"
    assert "iteration limit" in result["cua_result"]["recommended_recovery"]


@pytest.mark.asyncio
async def test_a_hard_block_outranks_the_iteration_limit() -> None:
    # The hard block names the call that cannot work; that is the more useful
    # recovery hint when both happened in one run.
    repeat = CuaRepeatFailureRail(_PREFIX, advise_after=1, block_after=2)
    progress = CuaProgressRail(_PREFIX)
    await progress.before_invoke(_invoke_ctx())

    for _ in range(3):
        ctx = _result_ctx("type_text", {"pid": 1, "text": "hi"}, _FAILURE)
        await repeat.before_tool_call(ctx)
        if not ctx.extra.get("_skip_tool"):
            await repeat.after_tool_call(ctx)
        await progress.after_tool_call(ctx)

    result = _max_iterations_result()
    await progress.after_invoke(_after_invoke_ctx(result))

    assert result["cua_result"]["status"] == "blocked"


@pytest.mark.asyncio
async def test_after_invoke_ignores_a_non_dict_result() -> None:
    # Mirrors every other rail in this file: an optional signal must never
    # raise into the run.
    rail = CuaProgressRail(_PREFIX)
    ctx = SimpleNamespace(inputs=SimpleNamespace(result=None), session=_FakeSession())

    await rail.after_invoke(ctx)  # must not raise


@pytest.mark.asyncio
async def test_resume_count_increments_across_invocations_on_the_same_session() -> None:
    rail = CuaProgressRail(_PREFIX)
    session = _FakeSession()

    first_result: dict = {"output": "1"}
    await rail.before_invoke(_invoke_ctx(session))
    await rail.after_invoke(_after_invoke_ctx(first_result, session))

    second_result: dict = {"output": "2"}
    await rail.before_invoke(_invoke_ctx(session))
    await rail.after_invoke(_after_invoke_ctx(second_result, session))

    assert first_result["cua_result"]["resume_count"] == 1
    assert second_result["cua_result"]["resume_count"] == 2


@pytest.mark.asyncio
async def test_progress_state_resets_between_invokes() -> None:
    rail = CuaProgressRail(_PREFIX)
    await _snap(rail, "get_window_state", {"pid": 1, "window_id": 2}, _WIN_A)
    await rail.after_tool_call(_result_ctx("type_text", {"pid": 1}, _FAILURE))

    await rail.before_invoke(_invoke_ctx())
    result: dict = {"output": "..."}
    await rail.after_invoke(_after_invoke_ctx(result))

    assert result["cua_result"]["blockers"] == []
    assert result["cua_result"]["current_window"] is None


@pytest.mark.asyncio
async def test_a_repeat_failure_hard_block_reports_the_run_as_blocked() -> None:
    # A run CuaRepeatFailureRail cut off after N identical failures used to
    # reach after_invoke with only ordinary blockers recorded, so the parent
    # saw status="partial" and treated it as merely incomplete. A hard block
    # means the model was thrashing on a step it cannot complete this way:
    # that is a terminal signal and must surface as "blocked".
    repeat = CuaRepeatFailureRail(_PREFIX, advise_after=1, block_after=2)
    progress = CuaProgressRail(_PREFIX)
    await progress.before_invoke(_invoke_ctx())
    args = {"pid": 1, "text": "hi"}

    for _ in range(3):
        ctx = _result_ctx("type_text", dict(args), _FAILURE)
        await repeat.before_tool_call(ctx)
        if not ctx.extra.get("_skip_tool"):
            await repeat.after_tool_call(ctx)
        await progress.after_tool_call(ctx)
    assert ctx.extra.get("_skip_tool")

    result: dict = {"output": "..."}
    await progress.after_invoke(_after_invoke_ctx(result))

    assert result["cua_result"]["status"] == "blocked"
    assert "type_text" in result["cua_result"]["recommended_recovery"]


@pytest.mark.asyncio
async def test_ordinary_failures_below_the_block_threshold_stay_partial() -> None:
    # The counterpart: failures the breaker never tripped on are still just
    # blockers. Only the hard block upgrades the run to "blocked".
    repeat = CuaRepeatFailureRail(_PREFIX, advise_after=1, block_after=3)
    progress = CuaProgressRail(_PREFIX)
    await progress.before_invoke(_invoke_ctx())

    for _ in range(2):
        ctx = _result_ctx("type_text", {"pid": 1, "text": "hi"}, _FAILURE)
        await repeat.before_tool_call(ctx)
        await repeat.after_tool_call(ctx)
        await progress.after_tool_call(ctx)

    result: dict = {"output": "..."}
    await progress.after_invoke(_after_invoke_ctx(result))

    assert result["cua_result"]["status"] == "partial"


@pytest.mark.asyncio
async def test_bare_prefix_rails_ignore_the_agents_other_tools() -> None:
    """With bare tool names every name starts with the "" prefix, so the rails
    must scope by cua tool name: a repeated read_file is not desktop thrash."""
    repeat = CuaRepeatFailureRail("")
    progress = CuaProgressRail("")

    ctxs = await _replay(repeat, "read_file", {"file_path": "a.txt"}, _FAILURE, 7)
    for ctx in ctxs:
        await progress.after_tool_call(ctx)

    assert not any(c.extra.get("_skip_tool") for c in ctxs)
    assert all("will not change the result" not in c.inputs.tool_msg.content for c in ctxs)
    assert progress._blockers == []
