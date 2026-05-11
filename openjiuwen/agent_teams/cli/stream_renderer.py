# coding: utf-8
"""Background stream consumer that renders ``run_agent_team_streaming`` chunks.

Each ``/team start`` (or ``/team switch`` / ``/team resume``) spawns
one :func:`spawn_stream` task per team. The task drives
``Runner.run_agent_team_streaming`` to completion, sets the
``runtime_ready`` future the first time the stream emits the
``team.runtime_ready`` event, and forwards the remaining chunks to
``openjiuwen.harness.cli.ui.renderer.render_stream`` for the same
token-buffered, tool-call-decorated rendering used by the single-agent
CLI.

The team-specific layer here is thin: just intercept the
``team.runtime_ready`` ack out of the stream and reuse the harness
renderer for everything visible.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import (
    dataclass,
    field,
)
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
)

from rich.console import Console
from rich.markup import escape as rich_escape

from openjiuwen.agent_teams.cli.state import StreamHandle
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.runner.runner import Runner
from openjiuwen.harness.cli.ui.renderer import render_stream

_TEAM_RUNTIME_READY_EVENT = "team.runtime_ready"

OnRuntimeReady = Callable[[str, str, dict[str, Any]], Awaitable[None]]


_REASONING_PREFIX = "\033[2m🤔 "
_REASONING_RESET = "\033[0m\n"


@dataclass(slots=True)
class _BoundaryChunk:
    """Empty ``message`` chunk used to flush the harness renderer's
    ``in_llm_output`` state before we start writing reasoning."""

    type: str = "message"
    payload: dict[str, Any] = field(default_factory=lambda: {"content": ""})


async def _wrap_stream(
    source: AsyncIterator[Any],
    handle: StreamHandle,
    on_runtime_ready: OnRuntimeReady | None,
    console: Console,
    show_reasoning: bool,
) -> AsyncIterator[Any]:
    """Wrap ``source`` to (1) intercept the ``team.runtime_ready`` ack and
    (2) render ``llm_reasoning`` chunks inline with a 🤔 prefix.

    The harness renderer treats reasoning tokens as token-per-line dim
    text without any prefix or terminating newline, which makes the
    reasoning blob run straight into the green ``● `` of the next
    ``llm_output`` chunk. The team CLI is a control surface where the
    leader's thinking is genuinely useful, so we render it ourselves
    (with a stable prefix + a flush newline whenever the chunk type
    leaves reasoning) and forward every other chunk to the harness
    renderer untouched.
    """
    callback_pending = on_runtime_ready
    in_reasoning = False
    sink = console.file
    async for chunk in source:
        payload = getattr(chunk, "payload", None)
        if isinstance(payload, dict) and payload.get("event_type") == _TEAM_RUNTIME_READY_EVENT:
            if not handle.runtime_ready.done():
                handle.runtime_ready.set_result(payload)
            if callback_pending is not None:
                cb = callback_pending
                callback_pending = None
                with contextlib.suppress(Exception):
                    await cb(handle.team_name, handle.session_id, payload)
            continue
        chunk_type = getattr(chunk, "type", "")
        if chunk_type == "llm_reasoning":
            if not show_reasoning:
                continue
            text = payload.get("content", "") if isinstance(payload, dict) else ""
            if not text:
                continue
            if not in_reasoning:
                # Yield an empty message so the harness renderer flushes
                # its in_llm_output state (writes the trailing newline)
                # before we start a fresh reasoning line.
                yield _BoundaryChunk()
                sink.write(_REASONING_PREFIX)
                in_reasoning = True
            sink.write(text)
            sink.flush()
            continue
        if in_reasoning:
            sink.write(_REASONING_RESET)
            sink.flush()
            in_reasoning = False
        yield chunk
    if in_reasoning:
        sink.write(_REASONING_RESET)
        sink.flush()


def spawn_stream(
    *,
    spec: TeamAgentSpec,
    session_id: str,
    inputs: dict[str, Any],
    console: Console,
    on_runtime_ready: OnRuntimeReady | None = None,
    show_reasoning: bool = True,
) -> StreamHandle:
    """Start a background stream consumer task and return its handle.

    Args:
        spec: TeamAgentSpec to drive.
        session_id: Session id to bind for this run.
        inputs: ``run_agent_team_streaming`` inputs (typically
            ``{"query": "..."}``).
        console: Rich console used by the harness renderer.
        on_runtime_ready: Optional async ``(team_name, session_id, payload)``
            callback invoked once after the first ``team.runtime_ready``
            chunk lands.
        show_reasoning: Forwarded to the harness renderer; when ``True``
            ``llm_reasoning`` chunks are shown in dim style.

    Returns:
        The :class:`StreamHandle` owning the background task. Caller is
        expected to either await ``handle.runtime_ready`` (with a
        timeout) or attach the handle to ``TeamCliState.stream_handles``
        for later teardown.
    """
    loop = asyncio.get_running_loop()
    runtime_ready: asyncio.Future[dict[str, Any]] = loop.create_future()
    handle = StreamHandle(
        team_name=spec.team_name,
        session_id=session_id,
        runtime_ready=runtime_ready,
        task=asyncio.create_task(asyncio.sleep(0)),
    )

    async def _consume() -> None:
        """Drive ``run_agent_team_streaming`` through the harness renderer."""
        source = Runner.run_agent_team_streaming(
            agent_team=spec,
            inputs=inputs,
            session=session_id,
        )
        filtered = _wrap_stream(source, handle, on_runtime_ready, console, show_reasoning)
        console.print(
            f"[dim cyan][{handle.team_name}] stream started (session={handle.session_id})[/dim cyan]",
        )
        try:
            # Reasoning is rendered by `_wrap_stream` with a stable 🤔 prefix,
            # so we tell the harness renderer to skip it (show_reasoning=False).
            await render_stream(filtered, console, show_reasoning=False)
        except asyncio.CancelledError:
            if not handle.cancelled:
                team_logger.info(
                    "[cli.stream] cancelled team={} session={}",
                    handle.team_name,
                    handle.session_id,
                )
            if not handle.runtime_ready.done():
                handle.runtime_ready.cancel()
            raise
        except Exception as exc:
            if not handle.runtime_ready.done():
                handle.runtime_ready.set_exception(exc)
            team_logger.exception(
                "[cli.stream] failed team={} session={}: {}",
                handle.team_name,
                handle.session_id,
                exc,
            )
            console.print(
                f"[red]\\[{handle.team_name}] stream failed: {rich_escape(str(exc))}[/red]",
            )

    handle.task = asyncio.create_task(_consume())
    return handle


async def stop_stream(handle: StreamHandle) -> None:
    """Cancel the consumer task and await its teardown.

    The caller is expected to have already invoked
    ``Runner.stop_agent_team`` so the gate is closed before cancel —
    cancelling first races the ``finally`` block in
    ``run_agent_team_streaming`` and surfaces ``gate_closed`` rather than
    ``not_active`` to subsequent interacts.
    """
    handle.cancelled = True
    if handle.task.done():
        return
    handle.task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await handle.task


__all__ = [
    "OnRuntimeReady",
    "spawn_stream",
    "stop_stream",
]
