# coding: utf-8
"""Shared utilities for agent-team E2E scripts."""

from __future__ import annotations

import asyncio
import os
import re
from asyncio import CancelledError
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import (
    Any,
    Awaitable,
    Callable,
    Optional,
)

import yaml

from openjiuwen.agent_teams.monitor import (
    MonitorEvent,
    TeamMonitor,
    TeamStreamLogger,
)
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.stream import TeamOutputSchema
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.logging.log_config import configure_log_config
from openjiuwen.core.runner.runner import Runner

OnRuntimeReady = Callable[[str, str], Awaitable[None]]

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
_ENV_VAR_RE = re.compile(r"\$\{(\w+)}")


def expand_env_vars(value: Any) -> Any:
    """Recursively replace ``${VAR}`` placeholders with environment values."""
    if isinstance(value, str):
        return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env_vars(v) for v in value]
    return value


def load_team_config(path: Path) -> dict[str, Any]:
    """Load and env-expand a YAML team config, pop the ``runtime`` key."""
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return expand_env_vars(raw)


def configure_logging_into(config_path: Path, log_dir: Path) -> None:
    """Apply ``config_path``'s logging config with every sink pinned to ``log_dir``.

    The config's sink targets are relative (``./logs/jiuwen.log``), so each one
    resolves against whatever the cwd happens to be *when that sink first
    opens*. An E2E chdirs into its scratch dir partway through startup, and a
    logger is only built on first use — so any logger touched before the chdir
    pins its file to the launch directory while every later one lands in the
    scratch dir, splitting one run's log across two trees and leaving the
    verdict somewhere the docs don't mention.

    Rewriting the targets to absolute paths removes the ordering dependency
    rather than relying on nobody logging too early.

    Args:
        config_path: A logging YAML with a top-level ``logging:`` section.
        log_dir: Directory every sink writes into. Created by the log backend
            on first write; only the file names from the config are kept.
    """
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    config = raw["logging"]
    for sink in (config.get("sinks") or {}).values():
        target = sink.get("target")
        if target:
            sink["target"] = str(log_dir / Path(target).name)
    configure_log_config(config)


# ---------------------------------------------------------------------------
# Async stdin helper
# ---------------------------------------------------------------------------
async def ainput(prompt: str = "> ") -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


# ---------------------------------------------------------------------------
# Stream consumer
# ---------------------------------------------------------------------------
_CHUNK_LLM_OUTPUT = "llm_output"
_CHUNK_LLM_REASONING = "llm_reasoning"
_CHUNK_ANSWER = "answer"
_CHUNK_TOOL_CALL = "tool_call"
_CHUNK_TOOL_RESULT = "tool_result"
_CHUNK_MESSAGE = "message"
_CHUNK_INTERACTION = "__interaction__"

_COLOR_RESET = "\033[0m"
_COLOR_DIM = "\033[2m"
_COLOR_GREEN = "\033[92m"
_COLOR_CYAN = "\033[96m"
_COLOR_YELLOW = "\033[93m"
_COLOR_MAGENTA = "\033[95m"


def _write(text: str) -> None:
    os.write(1, text.encode())


def _flush_buffer(chunk_type: str, buf: list[str], source_member: str | None = None) -> None:
    if not buf:
        return
    text = "".join(buf)
    if not text.strip():
        return
    source = f"[{source_member}]" if source_member else ""
    if chunk_type == _CHUNK_LLM_REASONING:
        _write(f"{_COLOR_DIM}[Reasoning]{source} {text}{_COLOR_RESET}\n")
    elif chunk_type == _CHUNK_LLM_OUTPUT:
        _write(f"{_COLOR_GREEN}[Output]{source} {_COLOR_RESET}{text}\n")
    elif chunk_type == _CHUNK_ANSWER:
        _write(f"{_COLOR_YELLOW}[Answer]{source} {_COLOR_RESET}{text}\n")
    else:
        _write(f"[{chunk_type}]{source} {text}\n")


def _extract_content(payload: Any) -> str:
    if isinstance(payload, dict):
        return payload.get("content", "") or payload.get("output", "")
    if isinstance(payload, str):
        return payload
    return str(payload)


@dataclass(slots=True)
class _OrderedEntry:
    """One source-owned stream run ordered by its first arriving chunk."""

    source_key: tuple[str | None, str | None]
    source_member: str | None
    chunk_type: str
    payload: Any = None
    buf: list[str] = field(default_factory=list)
    complete: bool = False


class _FirstArrivalRenderer:
    """Serialize interleaved member chunks by first-arrival order.

    Text deltas are accumulated independently for each member.  A run becomes
    complete when that same member changes stream category or emits a discrete
    chunk (for example a tool call).  Completed runs are printed only when all
    earlier-arriving runs are also complete, so another member can no longer
    split a sentence in the terminal.  Remaining runs are completed when the
    team stream closes.
    """

    _TEXT_TYPES = {_CHUNK_LLM_OUTPUT, _CHUNK_LLM_REASONING, _CHUNK_ANSWER}

    def __init__(self) -> None:
        self._entries: deque[_OrderedEntry] = deque()
        self._active: dict[tuple[str | None, str | None], _OrderedEntry] = {}
        self._llm_output_seen: set[tuple[str | None, str | None]] = set()

    @staticmethod
    def _source(chunk: Any) -> tuple[tuple[str | None, str | None], str | None]:
        if not isinstance(chunk, TeamOutputSchema):
            return (None, None), None
        role = chunk.role.value if chunk.role is not None else None
        return (chunk.source_member, role), chunk.source_member

    def feed(self, chunk: Any) -> None:
        chunk_type = getattr(chunk, "type", "")
        payload = getattr(chunk, "payload", None)
        source_key, source_member = self._source(chunk)

        if chunk_type == _CHUNK_ANSWER and source_key in self._llm_output_seen:
            return

        if chunk_type in self._TEXT_TYPES:
            content = _extract_content(payload)
            if not content:
                return
            active = self._active.get(source_key)
            if active is None or active.chunk_type != chunk_type:
                self._finish_source(source_key)
                active = _OrderedEntry(
                    source_key=source_key,
                    source_member=source_member,
                    chunk_type=chunk_type,
                )
                self._active[source_key] = active
                self._entries.append(active)
            active.buf.append(content)
            if chunk_type == _CHUNK_LLM_OUTPUT:
                self._llm_output_seen.add(source_key)
            self._drain_ready()
            return

        self._finish_source(source_key)
        self._entries.append(
            _OrderedEntry(
                source_key=source_key,
                source_member=source_member,
                chunk_type=chunk_type,
                payload=payload,
                complete=True,
            )
        )
        self._drain_ready()

    def flush(self) -> None:
        for entry in self._active.values():
            entry.complete = True
        self._active.clear()
        self._drain_ready()

    def _finish_source(self, source_key: tuple[str | None, str | None]) -> None:
        active = self._active.pop(source_key, None)
        if active is not None:
            active.complete = True

    def _drain_ready(self) -> None:
        while self._entries and self._entries[0].complete:
            self._render(self._entries.popleft())

    @staticmethod
    def _render(entry: _OrderedEntry) -> None:
        chunk_type = entry.chunk_type
        payload = entry.payload
        member = f"[{entry.source_member}] " if entry.source_member else ""
        if chunk_type in _FirstArrivalRenderer._TEXT_TYPES:
            _flush_buffer(chunk_type, entry.buf, entry.source_member)
            return
        if chunk_type == _CHUNK_TOOL_CALL:
            tool_name = payload.get("tool_name", "") if isinstance(payload, dict) else ""
            tool_args = payload.get("tool_args", "") if isinstance(payload, dict) else ""
            text = f"{_COLOR_CYAN}{member}● {tool_name}{_COLOR_RESET}"
            if tool_args:
                text += f"{_COLOR_DIM}({tool_args}){_COLOR_RESET}"
            _write(text + "\n")
            return
        if chunk_type == _CHUNK_TOOL_RESULT:
            tool_result = payload.get("tool_result", "") if isinstance(payload, dict) else str(payload)
            _write(f"{_COLOR_DIM}{member}  ⎿ {str(tool_result)[:200]}{_COLOR_RESET}\n\n")
            return
        if chunk_type == _CHUNK_MESSAGE:
            _write(f"{_COLOR_DIM}{member}  ⚙ {_extract_content(payload)}{_COLOR_RESET}\n")
            return
        if chunk_type == _CHUNK_INTERACTION:
            _write(f"{_COLOR_YELLOW}{member}[Interaction] {payload}{_COLOR_RESET}\n")
            return
        _flush_buffer(chunk_type, [_extract_content(payload)], entry.source_member)


def _format_monitor_event(evt: MonitorEvent) -> str:
    """Render a MonitorEvent as its type plus only the populated fields."""
    fields = evt.model_dump(exclude_none=True)
    fields.pop("event_type", None)
    detail = " ".join(f"{k}={v}" for k, v in fields.items())
    return f"{evt.event_type.value} {detail}".rstrip()


async def _drain_monitor(monitor: TeamMonitor) -> None:
    """Print every monitor event until the monitor is stopped.

    Runs as a background task alongside the stream consumer; the
    ``events()`` iterator terminates when ``monitor.stop()`` enqueues
    its sentinel, so this coroutine returns on its own at teardown.
    """
    async for evt in monitor.events():
        _write(f"{_COLOR_MAGENTA}[Monitor] {_format_monitor_event(evt)}{_COLOR_RESET}\n")


async def consume_stream(
    spec: TeamAgentSpec,
    query: str,
    session_id: str,
    *,
    on_runtime_ready: Optional[OnRuntimeReady] = None,
    ordered_output: bool = False,
) -> None:
    """Drive ``Runner.run_agent_team_streaming`` for ``spec`` and render chunks.

    Args:
        spec: TeamAgentSpec passed straight to the Runner facade. Avoid
            calling ``spec.build()`` here — the agent_teams runtime
            owns the lifecycle.
        query: First god-view input forwarded as the leader's seed.
        session_id: Session id to bind for this run.
        on_runtime_ready: Optional async callback invoked once with
            ``(team_name, session_id)`` the first time the stream
            yields a ``team.runtime_ready`` event. Use it to wire up
            facade APIs that need the team to be in the pool (e.g.
            ``Runner.register_human_agent_inbound``).
        ordered_output: Buffer each member's text independently and render
            completed runs in first-arrival order. This prevents concurrent
            members from splitting one another's sentences in E2E output.

    A ``TeamMonitor`` is attached on the same ``team.runtime_ready``
    signal (once the team is in the pool) and every monitor event is
    printed from a background task until the stream ends.
    """
    logger.info("Starting team stream with query: %s", query)

    cur_type = ""
    buf: list[str] = []
    has_llm_output = False
    ordered_renderer = _FirstArrivalRenderer() if ordered_output else None
    ready_pending = on_runtime_ready
    monitor: TeamMonitor | None = None
    monitor_task: asyncio.Task[None] | None = None
    # Aggregated diagnostic log of the full team stream; the runner feeds
    # every chunk through it and flushes when the stream ends. A fresh
    # timestamped file per run avoids appending across separate runs.
    debug_path = f"./debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    stream_logger = TeamStreamLogger(debug_path)

    try:
        async for chunk in Runner.run_agent_team_streaming(
            agent_team=spec,
            inputs={"query": query},
            session=session_id,
            stream_logger=stream_logger,
        ):
            chunk_type = getattr(chunk, "type", "")
            payload = getattr(chunk, "payload", None)

            if isinstance(payload, dict) and payload.get("event_type") == "team.runtime_ready":
                if ready_pending is not None:
                    await ready_pending(spec.team_name, session_id)
                    ready_pending = None
                # Team is now in the pool, so the monitor facade can resolve it.
                if monitor is None:
                    monitor = await Runner.get_agent_team_monitor(
                        team_name=spec.team_name,
                        session_id=session_id,
                    )
                    if monitor is not None:
                        await monitor.start()
                        monitor_task = asyncio.create_task(_drain_monitor(monitor))

            if ordered_renderer is not None:
                ordered_renderer.feed(chunk)
                continue

            if chunk_type == _CHUNK_TOOL_CALL:
                _flush_buffer(cur_type, buf)
                cur_type, buf = "", []
                tool_name = payload.get("tool_name", "") if isinstance(payload, dict) else ""
                tool_args = payload.get("tool_args", "") if isinstance(payload, dict) else ""
                _write(f"{_COLOR_CYAN}● {tool_name}{_COLOR_RESET}")
                if tool_args:
                    _write(f"{_COLOR_DIM}({tool_args}){_COLOR_RESET}")
                _write("\n")
                continue

            if chunk_type == _CHUNK_TOOL_RESULT:
                tool_result = payload.get("tool_result", "") if isinstance(payload, dict) else str(payload)
                preview = str(tool_result)[:200]
                _write(f"{_COLOR_DIM}  ⎿ {preview}{_COLOR_RESET}\n\n")
                continue

            if chunk_type == _CHUNK_MESSAGE:
                _flush_buffer(cur_type, buf)
                cur_type, buf = "", []
                _write(f"{_COLOR_DIM}  ⚙ {_extract_content(payload)}{_COLOR_RESET}\n")
                continue

            if chunk_type == _CHUNK_INTERACTION:
                _flush_buffer(cur_type, buf)
                cur_type, buf = "", []
                _write(f"{_COLOR_YELLOW}[Interaction] {payload}{_COLOR_RESET}\n")
                continue

            if chunk_type == _CHUNK_ANSWER and has_llm_output:
                continue

            if chunk_type != cur_type:
                _flush_buffer(cur_type, buf)
                cur_type = chunk_type
                buf = []

            if chunk_type == _CHUNK_LLM_OUTPUT:
                has_llm_output = True

            buf.append(_extract_content(payload))

        _flush_buffer(cur_type, buf)
    finally:
        if ordered_renderer is not None:
            ordered_renderer.flush()
        if monitor is not None:
            await monitor.stop()
        if monitor_task is not None:
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
    logger.info("Team stream finished.")


# ---------------------------------------------------------------------------
# Interactive loop
# ---------------------------------------------------------------------------
async def run_interactive(
    spec: TeamAgentSpec,
    runtime_cfg: dict[str, Any],
    default_session_id: str,
    default_initial_query: str = "hello",
    *,
    on_runtime_ready: Optional[OnRuntimeReady] = None,
) -> None:
    """Run the standard interactive CLI loop against ``spec``.

    All routing goes through Runner facades (``run_agent_team_streaming`` for
    the leader stream, ``interact_agent_team`` for stdin), so the caller never
    has to ``spec.build()`` or hold a ``TeamAgent`` reference.

    Args:
        spec: TeamAgentSpec to drive.
        runtime_cfg: Runtime overrides parsed from the YAML ``runtime`` block.
        default_session_id: Fallback session id when ``runtime_cfg`` omits one.
        default_initial_query: Fallback initial query when ``runtime_cfg`` omits one.
        on_runtime_ready: Optional async ``(team_name, session_id)`` callback
            fired once after the first ``team.runtime_ready`` chunk. Use it
            to register facade-bound listeners that need the pool entry to
            exist (e.g. ``Runner.register_human_agent_inbound``).
    """
    session_id = runtime_cfg.get("session_id", default_session_id)
    initial_query = runtime_cfg.get("initial_query", default_initial_query)

    stream_task = asyncio.create_task(
        consume_stream(spec, initial_query, session_id, on_runtime_ready=on_runtime_ready)
    )

    try:
        while True:
            try:
                user_input = await ainput("\n[You] > ")
            except (EOFError, CancelledError):
                break

            if user_input.strip().lower() in ("exit", "quit"):
                print("Exiting...")
                break
            if not user_input.strip():
                continue

            result = await Runner.interact_agent_team(
                user_input,
                team_name=spec.team_name,
                session_id=session_id,
            )
            if result.ok:
                print(f"[System] Input dispatched (message_id={result.message_id})")
            else:
                _write(f"{_COLOR_YELLOW}[Deliver failed] {result.reason}{_COLOR_RESET}\n")

    except KeyboardInterrupt:
        print("\nInterrupted by user.")

    if not stream_task.done():
        stream_task.cancel()
        try:
            await stream_task
        except asyncio.CancelledError:
            pass
