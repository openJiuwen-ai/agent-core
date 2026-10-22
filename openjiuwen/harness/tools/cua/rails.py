# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rails for the cua desktop tools.

Every rail takes the model-facing tool-name prefix (``""`` for the native
tools) and acts only on cua tool names behind it, so the agent's other tools
are never counted, held or rewritten.
"""

from __future__ import annotations

import asyncio
import base64
import collections
import io
import json
import time
from typing import Any, Awaitable, Callable, Optional, Tuple

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail, ToolCallInputs
from openjiuwen.harness.tools.cua.cua_capabilities import INPUT_CUA_TOOL_NAMES
from openjiuwen.harness.tools.cua.desktop_tools import CUA_DESKTOP_TOOL_NAMES, CuaDesktop
from openjiuwen.harness.tools.cua.user_activity import UserActivityProbe, build_default_user_activity_probe

_PREFLIGHT_REMEDIATION = (
    "Install the desktop extra (`uv sync --extra cua`, or `pip install 'openjiuwen[cua]'`) and run the "
    "agent inside an interactive desktop session: Windows, or Linux with DISPLAY pointing at an X11 server."
)

_USER_TAKEOVER_BLOCKED = (
    "Paused for {waited:.0f}s because the user is actively using the desktop (mouse/keyboard "
    "input detected) and has not stopped. Do not send further desktop actions. Report that the "
    "user has taken over the desktop, summarize what was completed so far, and state that the "
    "task can be resumed once they hand control back."
)
_USER_TAKEOVER_RESUMED_NOTE = (
    "Note: this action was held for {waited:.0f}s because the user was using the desktop; it ran "
    "once the desktop had been idle for {idle:.0f}s. The screen may have changed meanwhile -- "
    "re-capture with get_window_state before acting on x,y read off an older screenshot."
)

# Success marker the cua tools put first in the reply of an action that ran
# (the cua-driver result contract); failures and unverified deliveries do not
# carry it. Verified against 165 recorded runs: every identical call repeated >=5
# times legitimately (press_key tab x27, hotkey ctrl+a x16) is marked, and the
# only unmarked high-repeat call is a genuine stuck loop.
_DRIVER_SUCCESS_MARKER = "✅"

# Failure marker: a call the cua tools could not carry out (target
# gone, argument rejected, value did not stick). A reply with neither marker
# is an action that ran but could not be verified. The two are counted alike
# by CuaRepeatFailureRail (a loop of unverified repeats is still a loop) but
# only a marked failure is a blocker for CuaProgressRail: an OSWorld bridge
# run (task 052, 2026-09-22) had 86 unverified clicks and 1 failure, and
# listing all 87 as blockers said nothing about what actually went wrong.
_DRIVER_FAILURE_MARKER = "❌"

# Volatile argument keys excluded from the repeat-identity of a call: they
# change between runs without changing what the call does.
_REPEAT_IDENTITY_IGNORED_ARGS = frozenset({"session"})

_REPEAT_ADVISORY = (
    "This exact call has now returned this same non-success response {count} times. "
    "Repeating it again will not change the result. Re-snapshot the window with "
    "get_window_state and address the target differently, or report what is blocking you."
)

_SNAPSHOT_TOOLS = frozenset({"get_window_state"})

# cua tools that observe without changing what is on screen.
_READ_ONLY_TOOLS = frozenset({"list_windows", "list_apps", "get_cursor_position", "get_screen_size", "zoom"})

_REPEAT_BLOCKED = (
    "Blocked: this exact call has already returned the same non-success response {count} times "
    "in this run and is not being sent again. The approach is not working — re-snapshot with "
    "get_window_state and change the arguments or the strategy, or report that the step is "
    "blocked. Repeating this call verbatim will stay blocked."
)

_CUA_BLOCKER_NOTE_MAX = 200
_CUA_MAX_TRACKED_BLOCKERS = 5
# Set on ctx.extra next to ``_skip_tool`` when CuaRepeatFailureRail refuses a
# call, so CuaProgressRail can tell a hard block from an ordinary rejection.
_REPEAT_BLOCKED_EXTRA_KEY = "_cua_repeat_blocked"

_CUA_PROGRESS_STATE_KEY = "cua_agent_progress_state"


class CuaRuntimeRail(AgentRail):
    """Fail loud before the first step when this host cannot run the desktop tools.

    Builds the shared backend and reads the screen size once: a missing ``cua``
    extra, an unsupported platform or an unreachable display surfaces here as
    an error with remediation, not as a ❌ on the model's first action. Cached
    after the first success, since ``before_invoke`` fires on every entry into
    the agent loop, including each approval re-entry.
    """

    def __init__(self, desktop: CuaDesktop) -> None:
        super().__init__()
        self._desktop = desktop
        self._verified = False

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        if self._verified:
            return
        try:
            await asyncio.to_thread(self._desktop.backend.screen_size)
        except Exception as exc:  # noqa: BLE001 - any failure here means no usable desktop
            raise_error(
                StatusCode.COMPONENT_TOOL_INIT_FAILED,
                cause=exc,
                error_msg=f"cua desktop preflight failed: {type(exc).__name__}: {exc}. {_PREFLIGHT_REMEDIATION}",
            )
        self._verified = True


def _cua_short_name(tool_name: str, tool_prefix: str) -> Optional[str]:
    """The cua tool name behind a model-facing name, or None for any other tool.

    With the bare prefix every name starts with it, so membership in the cua
    tool set is what keeps the rails off the agent's other tools.
    """
    if not tool_name.startswith(tool_prefix):
        return None
    short_name = tool_name.removeprefix(tool_prefix)
    return short_name if short_name in CUA_DESKTOP_TOOL_NAMES else None


def _normalize_tool_args(raw_args: Any) -> Tuple[Optional[dict], bool]:
    """Return tool args as a dict plus whether they arrived JSON-encoded.

    ``tool_args`` reaches ``before_tool_call`` as the raw ``ToolCall.arguments``
    value, which is a JSON string until the ability manager parses it. The
    caller must write the mutated args back in the same shape it received.
    """
    if isinstance(raw_args, dict):
        return dict(raw_args), False
    if isinstance(raw_args, str) and raw_args.strip():
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError:
            return None, True
        return (parsed, True) if isinstance(parsed, dict) else (None, True)
    return None, False


def _response_text(inputs: ToolCallInputs) -> Optional[str]:
    """Read the tool's response text, preferring the built ToolMessage."""
    content = getattr(inputs.tool_msg, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()
    data = getattr(inputs.tool_result, "data", None)
    if isinstance(data, dict) and isinstance(data.get("content"), str):
        return data["content"].strip() or None
    if isinstance(inputs.tool_result, dict) and isinstance(inputs.tool_result.get("content"), str):
        return inputs.tool_result["content"].strip() or None
    return None


def _append_advisory(inputs: ToolCallInputs, advisory: str) -> None:
    """Annotate a tool result in place; the ToolMessage already exists in after_tool_call."""
    suffix = "\n\n" + advisory
    data = getattr(inputs.tool_result, "data", None)
    if isinstance(data, dict) and isinstance(data.get("content"), str):
        data["content"] += suffix
    tool_msg = inputs.tool_msg
    if tool_msg is not None and isinstance(getattr(tool_msg, "content", None), str):
        tool_msg.content += suffix


class CuaUserTakeoverRail(AgentRail):
    """Pause desktop actions while the user is using the machine.

    "Once the user takes over the screen, the agent should pause." The tools
    have no such signal, so the rail asks the OS (``UserActivityProbe``) how
    long ago the last mouse/keyboard event happened, and holds every cua tool
    call while that is recent:

    - the user counts as active when the desktop was touched within
      ``active_within_s``;
    - a held call proceeds once the desktop has been idle for
      ``resume_after_idle_s`` (pause-and-wait, not stop), and the tool result
      is annotated so the model re-captures before trusting old pixels;
    - after ``max_wait_s`` of continuous user activity the call is rejected
      with an instruction to report the takeover, so a delegation never sits
      silent forever.

    The agent's own clicks and keystrokes are real OS input too. An input
    event that falls inside the agent's last own input call -- from dispatch
    to the tool's reply, padded by
    ``self_input_grace_s`` -- is attributed to the agent, not the user. The
    whole call is the window because the OS event lands shortly after
    dispatch for a click but near the reply for a long type_text, and a slow
    call separates the two by more than any fixed grace. Without a
    probe (non-Windows hosts) the rail is inert.
    """

    def __init__(
        self,
        tool_prefix: str,
        *,
        probe: Optional[UserActivityProbe] = None,
        active_within_s: float = 3.0,
        resume_after_idle_s: float = 5.0,
        max_wait_s: float = 60.0,
        poll_interval_s: float = 0.5,
        self_input_grace_s: float = 1.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        thresholds = (active_within_s, resume_after_idle_s, max_wait_s, poll_interval_s)
        if any(value <= 0 for value in thresholds):
            raise ValueError("cua takeover thresholds must be positive seconds")
        self._tool_prefix = tool_prefix
        # Input tools inject events the OS registers exactly like a human's, so
        # the takeover check must tell this agent's recent input from the user's.
        self._input_tool_names = frozenset(f"{tool_prefix}{tool_name}" for tool_name in INPUT_CUA_TOOL_NAMES)
        self._probe = probe if probe is not None else build_default_user_activity_probe()
        self._active_within_s = float(active_within_s)
        self._resume_after_idle_s = float(resume_after_idle_s)
        self._max_wait_s = float(max_wait_s)
        self._poll_interval_s = float(poll_interval_s)
        self._self_input_grace_s = float(self_input_grace_s)
        self._sleep = sleep
        self._monotonic = monotonic
        self._own_input_dispatched_at: Optional[float] = None
        self._last_own_input_at: Optional[float] = None
        self._pending_note: Optional[str] = None

    @property
    def active(self) -> bool:
        """Whether a probe is available, i.e. the rail can actually observe the user."""
        return self._probe is not None

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs) or self._probe is None:
            return
        tool_name = str(inputs.tool_name or "")
        if _cua_short_name(tool_name, self._tool_prefix) is None or ctx.extra.get("_skip_tool"):
            return

        waited = 0.0
        idle = self._user_idle_seconds()
        if idle is None or idle >= self._active_within_s:
            self._mark_own_input_dispatch(tool_name)
            return
        logger.warning("[CuaUserTakeoverRail] user is using the desktop (idle %.1fs); holding %s", idle, tool_name)
        while True:
            if waited >= self._max_wait_s:
                logger.warning("[CuaUserTakeoverRail] user still active after %.0fs; rejecting %s", waited, tool_name)
                self._reject_tool(ctx, inputs, _USER_TAKEOVER_BLOCKED.format(waited=waited))
                return
            await self._sleep(self._poll_interval_s)
            waited += self._poll_interval_s
            idle = self._user_idle_seconds()
            if idle is None or idle >= self._resume_after_idle_s:
                break
        logger.info("[CuaUserTakeoverRail] desktop idle again after %.1fs; releasing %s", waited, tool_name)
        self._pending_note = _USER_TAKEOVER_RESUMED_NOTE.format(waited=waited, idle=self._resume_after_idle_s)
        self._mark_own_input_dispatch(tool_name)

    def _mark_own_input_dispatch(self, tool_name: str) -> None:
        # Taken after any hold, so the window never covers the user's activity.
        if tool_name in self._input_tool_names:
            self._own_input_dispatched_at = self._monotonic()

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return
        tool_name = str(inputs.tool_name or "")
        if _cua_short_name(tool_name, self._tool_prefix) is None:
            return
        if tool_name in self._input_tool_names and not ctx.extra.get("_skip_tool"):
            self._last_own_input_at = self._monotonic()
        if self._pending_note is not None:
            _append_advisory(inputs, self._pending_note)
            self._pending_note = None

    def _user_idle_seconds(self) -> Optional[float]:
        idle = self._probe.seconds_since_last_input()
        if idle is None:
            return None
        if self._last_own_input_at is not None:
            last_event_at = self._monotonic() - idle
            dispatched_at = self._own_input_dispatched_at
            if dispatched_at is None or dispatched_at > self._last_own_input_at:
                dispatched_at = self._last_own_input_at
            # The last OS input event landed during our own input call: that is
            # the agent, not the user. Anything more recent is the user.
            if (
                dispatched_at - self._self_input_grace_s
                <= last_event_at
                <= self._last_own_input_at + self._self_input_grace_s
            ):
                return float("inf")
        return idle

    @staticmethod
    def _reject_tool(ctx: AgentCallbackContext, inputs: ToolCallInputs, error_msg: str) -> None:
        tool_call = inputs.tool_call
        tool_call_id = tool_call.id if tool_call else ""
        ctx.extra["_skip_tool"] = True
        inputs.tool_result = {"error": error_msg}
        inputs.tool_msg = ToolMessage(content=error_msg, tool_call_id=tool_call_id)


class CuaScreenshotDownscaleRail(AgentRail):
    """Downscale oversized cua screenshots before they enter context.

    A full-desktop PNG can reach millions of base64 chars; re-encode any
    multimodal image over the token budget as a bounded JPEG. Mutates the
    ``multimodal`` items of ``ctx.inputs.tool_result`` in place — the same
    object the ability manager returns to the agent, so reassigning
    ``tool_result`` here would silently not propagate.
    """

    # base64 chars × 0.125 ≈ tokens, and the same budget as ReadFileTool's
    # image path (filesystem.py); duplicated rather than shared to keep this
    # rail free of the filesystem tool's sys_operation coupling.
    _MAX_IMAGE_TOKENS = 25_000

    def __init__(
        self,
        tool_prefix: str,
        *,
        max_edge: int = 1536,
        jpeg_quality: int = 70,
    ) -> None:
        super().__init__()
        self._max_edge = max_edge
        self._jpeg_quality = jpeg_quality
        # Prefix-match the server's model-facing tool names so instance-isolated
        # registrations (cua_instance_key) are matched too.
        self._tool_prefix = tool_prefix

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return
        if _cua_short_name(str(inputs.tool_name or ""), self._tool_prefix) is None:
            return
        data = getattr(inputs.tool_result, "data", None)
        if not isinstance(data, dict):
            return
        items = data.get("multimodal")
        if not isinstance(items, list):
            return
        scale_notes = []
        for item in items:
            if not isinstance(item, dict) or item.get("type") != "image":
                continue
            data_url = item.get("data_url")
            if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
                continue
            if len(data_url) * 0.125 <= self._MAX_IMAGE_TOKENS:
                continue
            downscaled = self._downscale_data_url(data_url)
            if downscaled is not None:
                new_url, (orig_w, orig_h), (new_w, new_h) = downscaled
                item["data_url"] = new_url
                item["mime_type"] = "image/jpeg"
                if new_w and new_w < orig_w:
                    scale_notes.append(
                        f"[screenshot scaled] The attached screenshot is {new_w}x{new_h}, downscaled "
                        f"from the window's true {orig_w}x{orig_h}. Any x,y you read off the screenshot "
                        f"must be multiplied by {orig_w / new_w:.2f} to get window coordinates before "
                        f"click/drag."
                    )
        if scale_notes:
            self._append_scale_notes(inputs, data, scale_notes)

    @staticmethod
    def _append_scale_notes(inputs: ToolCallInputs, data: dict, scale_notes: list) -> None:
        """Surface the scale factor to the model or its screenshot reads misclick.

        The ToolMessage is built before after_tool_call rails run, so the note
        must be appended to the existing message object in place; updating only
        ``data["content"]`` would never reach the model.
        """
        suffix = "\n\n" + "\n".join(scale_notes)
        if isinstance(data.get("content"), str):
            data["content"] += suffix
        tool_msg = inputs.tool_msg
        if tool_msg is not None and isinstance(getattr(tool_msg, "content", None), str):
            tool_msg.content += suffix

    def _downscale_data_url(self, data_url: str) -> Optional[tuple]:
        """Return ``(jpeg_data_url, (orig_w, orig_h), (new_w, new_h))``, or None."""
        try:
            from PIL import Image
        except ImportError as exc:
            logger.debug("[CuaScreenshotDownscaleRail] Pillow unavailable: %s", exc)
            return None

        try:
            _, encoded = data_url.split(",", 1)
            raw = base64.b64decode(encoded)
        except ValueError as exc:  # binascii.Error is a ValueError subclass
            logger.debug("[CuaScreenshotDownscaleRail] malformed data URL: %s", exc)
            return None

        # Ladder mirrors ReadFileTool._read_image: bounded resize first, then
        # an aggressive fallback if the budget is still exceeded.
        for max_edge, quality in ((self._max_edge, self._jpeg_quality), (800, 40)):
            try:
                with Image.open(io.BytesIO(raw)) as img:
                    orig_size = img.size
                    img = img.convert("RGB")
                    img.thumbnail((max_edge, max_edge))
                    new_size = img.size
                    out = io.BytesIO()
                    img.save(out, format="JPEG", quality=quality)
            except (OSError, ValueError) as exc:
                logger.debug("[CuaScreenshotDownscaleRail] re-encode failed: %s", exc)
                return None
            candidate = base64.b64encode(out.getvalue()).decode("ascii")
            if len(candidate) * 0.125 <= self._MAX_IMAGE_TOKENS:
                return f"data:image/jpeg;base64,{candidate}", orig_size, new_size
        # Over budget even at the aggressive rung: ship the smallest attempt.
        return f"data:image/jpeg;base64,{candidate}", orig_size, new_size


class CuaRepeatFailureRail(AgentRail):
    """Stop the agent re-sending a call that keeps failing identically.

    Desktop errors come back as ordinary tool results, not exceptions, so
    nothing in the retry stack sees them: a model that misreads one can repeat
    the same rejected call until the iteration cap. One recorded run spent 172
    turns and 510s emitting the same rejected ``type_text`` 55 times and ended
    on the error it started with.

    Repetition alone is not the signal -- the most-repeated identical calls in
    the corpus are legitimate (``press_key tab`` x27 while walking a form).
    What separates thrash from progress is that the response is not a
    success, so only unmarked responses are counted. Two stages, because a
    warning the model can act on beats a block it cannot:

    - at ``advise_after`` identical non-success responses the result is
      annotated, telling the model the repeat is not working;
    - at ``block_after`` the call is refused before it reaches the desktop.

    Defaults come from the corpus: the longest legitimate run of identical
    non-success responses is 4 (unverified PostMessage deliveries), while the
    real stuck loop reached 20 -- so blocking at 6 misfires on no recorded
    run. Counters are per-invoke.
    """

    def __init__(
        self,
        tool_prefix: str,
        *,
        advise_after: int = 3,
        block_after: int = 6,
    ) -> None:
        super().__init__()
        if not 0 < advise_after <= block_after:
            raise ValueError(
                "cua repeat thresholds must satisfy 0 < advise_after <= block_after, "
                f"got advise_after={advise_after}, block_after={block_after}"
            )
        self._advise_after = advise_after
        self._block_after = block_after
        self._tool_prefix = tool_prefix
        # (tool_name, canonical args) -> Counter of response text -> hits
        self._seen: dict = {}

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        self._seen.clear()

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return
        key = self._identity(inputs)
        if key is None:
            return
        counts = self._seen.get(key)
        if not counts:
            return
        response, count = counts.most_common(1)[0]
        if count < self._block_after:
            return
        logger.warning(
            "[CuaRepeatFailureRail] Blocked %s after %d identical non-success responses",
            inputs.tool_name,
            count,
        )
        message = _REPEAT_BLOCKED.format(count=count) + "\n\nThe repeated response was:\n" + response
        self._reject_tool(ctx, inputs, message)
        ctx.extra[_REPEAT_BLOCKED_EXTRA_KEY] = True

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return
        if ctx.extra.get("_skip_tool"):
            # Our own refusal (or another rail's) is not a tool response.
            return
        key = self._identity(inputs)
        if key is None:
            return
        response = _response_text(inputs)
        if response is None or response.startswith(_DRIVER_SUCCESS_MARKER):
            # A success clears this call's history: whatever the model was
            # stuck on has moved, so a later failure starts a fresh run.
            self._seen.pop(key, None)
            return

        counts = self._seen.setdefault(key, collections.Counter())
        counts[response] += 1
        count = counts[response]
        if count == self._advise_after:
            logger.info(
                "[CuaRepeatFailureRail] %s returned the same non-success response %d times",
                inputs.tool_name,
                count,
            )
            _append_advisory(inputs, _REPEAT_ADVISORY.format(count=count))

    def _identity(self, inputs: ToolCallInputs):
        tool_name = str(inputs.tool_name or "")
        if _cua_short_name(tool_name, self._tool_prefix) is None:
            return None
        args, _ = _normalize_tool_args(inputs.tool_args)
        if args is None:
            return None
        stable = {k: v for k, v in args.items() if k not in _REPEAT_IDENTITY_IGNORED_ARGS}
        try:
            canonical = json.dumps(stable, sort_keys=True, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return None
        return tool_name, canonical

    @staticmethod
    def _reject_tool(ctx: AgentCallbackContext, inputs: ToolCallInputs, error_msg: str) -> None:
        """Hard-block a tool call using the shared rail contract."""
        tool_call = inputs.tool_call
        tool_call_id = tool_call.id if tool_call else ""
        ctx.extra["_skip_tool"] = True
        inputs.tool_result = {"error": error_msg}
        inputs.tool_msg = ToolMessage(content=error_msg, tool_call_id=tool_call_id)


class CuaProgressRail(AgentRail):
    """Surface a resume_context for the delegating caller.

    At run end (after_invoke) this rail packages what it tracked -- the
    current window, accumulated blockers, hard-blocked calls and a resume
    counter persisted in session state across delegations -- into
    ``result["cua_result"]``, mirroring how browser_agent's runtime attaches
    ``authoritative_browser_result``. task_tool.py reads this back and
    surfaces it to the delegating parent as ``resume_context``.
    ``status``/``recommended_recovery`` are simple heuristics derived from
    whether a blocker or a repeat-failure hard block was hit, or the run was
    cut off at its iteration limit -- not an
    authoritative task-completion judgment the way browser's phase-tracked
    status is.
    """

    def __init__(self, tool_prefix: str) -> None:
        super().__init__()
        self._tool_prefix = tool_prefix
        self._current_window: Optional[dict] = None
        self._blockers: list = []
        # Short names of calls CuaRepeatFailureRail refused in this run.
        self._hard_blocked: list = []

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        self._current_window = None
        self._blockers = []
        self._hard_blocked = []

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return
        short_name = _cua_short_name(str(inputs.tool_name or ""), self._tool_prefix)
        if short_name is None:
            return
        if ctx.extra.get("_skip_tool"):
            # A rail-side rejection never reached the desktop. A repeat-failure
            # block is still terminal for the run: the model was thrashing on
            # a call it cannot complete this way, and its earlier failures
            # only ever registered as ordinary blockers (status "partial").
            if ctx.extra.get(_REPEAT_BLOCKED_EXTRA_KEY):
                self._hard_blocked.append(short_name)
            return
        response = _response_text(inputs)

        if short_name in _SNAPSHOT_TOOLS:
            args, _ = _normalize_tool_args(inputs.tool_args)
            if args is not None and (args.get("pid") is not None or args.get("window_id") is not None):
                self._current_window = {"pid": args.get("pid"), "window_id": args.get("window_id")}
            return
        if short_name in _READ_ONLY_TOOLS:
            return

        # Action tool: the failure marker signals a blocker. An unmarked reply
        # is an unverified action, not a failure.
        if response is not None and response.startswith(_DRIVER_FAILURE_MARKER):
            self._note_blocker(short_name, response)

    def _note_blocker(self, short_name: str, response: str) -> None:
        if len(self._blockers) >= _CUA_MAX_TRACKED_BLOCKERS:
            return
        stripped = response.strip()
        if not stripped:
            return
        entry = f"{short_name}: {stripped.splitlines()[0][:_CUA_BLOCKER_NOTE_MAX]}"
        if entry not in self._blockers:
            self._blockers.append(entry)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        result = getattr(getattr(ctx, "inputs", None), "result", None)
        if not isinstance(result, dict):
            return

        resume_count = 0
        session = getattr(ctx, "session", None)
        if session is not None:
            try:
                state = session.get_state(_CUA_PROGRESS_STATE_KEY)
                resume_count = int(state.get("resume_count", 0)) + 1 if isinstance(state, dict) else 1
                session.update_state({_CUA_PROGRESS_STATE_KEY: {"resume_count": resume_count}})
            except Exception as exc:
                # Best-effort: an optional resume counter must never block the run.
                logger.debug("[CuaProgressRail] resume_count tracking failed: %s", exc)

        if self._hard_blocked:
            status = "blocked"
            tools = ", ".join(dict.fromkeys(self._hard_blocked))
            recovery = (
                f"A repeatedly failing call was hard-blocked ({tools}); that approach does not "
                "work. Resume with different arguments or a different strategy for that step."
            )
        elif result.get("termination_reason") == "max_iterations":
            # The run was cut off, not finished: no blocker does not mean done.
            status = "partial"
            recovery = (
                "The run hit its iteration limit before finishing; re-capture the window to see "
                "how far it got, then resume the remaining steps."
            )
        elif self._blockers:
            status = "partial"
            recovery = "Some actions did not report success; re-capture and verify before retrying."
        else:
            status = "completed"
            recovery = None

        result["cua_result"] = {
            "status": status,
            "current_window": self._current_window,
            "blockers": list(self._blockers),
            "recommended_recovery": recovery,
            "resume_count": resume_count,
        }


__all__ = [
    "CuaProgressRail",
    "CuaRepeatFailureRail",
    "CuaRuntimeRail",
    "CuaScreenshotDownscaleRail",
    "CuaUserTakeoverRail",
]
