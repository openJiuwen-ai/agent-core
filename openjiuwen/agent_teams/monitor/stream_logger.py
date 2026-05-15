# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Aggregated diagnostic logging of team streaming output.

``Runner.run_agent_team_streaming`` yields every leader and (in-process)
teammate chunk through one stream, each tagged with ``source_member`` /
``role`` (see ``TeamOutputSchema``). :class:`TeamStreamLogger` is an
opt-in processing object the caller passes into the runner: it buffers
token-streamed chunks per ``(member, role, category)`` run and emits one
readable, multi-line log record per run via ``team_logger`` -- text at
INFO, thinking / tool calls at DEBUG, interruptions / task failures at
WARN.

The chunk-handling flow mirrors the team CLI renderer
(``openjiuwen/harness/cli/ui/renderer.py`` +
``openjiuwen/agent_teams/cli/stream_renderer.py``): the same chunk-type
vocabulary, the same ``answer``-vs-``llm_output`` dedup, and the same
controller-output extraction.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_teams.schema.stream import TeamOutputSchema
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.session.stream import OutputSchema

# Chunk ``type`` values. ``OutputSchema.type`` is a plain ``str`` with no
# canonical enum in the core stream layer; these mirror the constants in
# ``openjiuwen/harness/cli/ui/renderer.py`` and the SDK chunk vocabulary.
_CHUNK_LLM_OUTPUT = "llm_output"
_CHUNK_LLM_REASONING = "llm_reasoning"
_CHUNK_ANSWER = "answer"
_CHUNK_INTERACTION = "__interaction__"
_CHUNK_MESSAGE = "message"
_CHUNK_TOOL_CALL = "tool_call"
_CHUNK_TOOL_RESULT = "tool_result"
_CHUNK_TODO_UPDATED = "todo.updated"
_CHUNK_CONTROLLER_OUTPUT = "controller_output"

# Categories that buffer token-streamed chunks across a contiguous run.
_ACCUMULATING_TYPES = frozenset({_CHUNK_LLM_OUTPUT, _CHUNK_LLM_REASONING})

# The first stream item ``run_agent_team_streaming`` yields is a
# ``message`` chunk carrying this event type; logged as its own category.
_RUNTIME_READY_EVENT = "team.runtime_ready"

# Readability caps. Model text output is never capped (the whole point of
# the log is the model's full output); bulky tool payloads are.
_TOOL_RESULT_CAP = 2000
_TOOL_ARGS_CAP = 500
_GENERIC_CAP = 2000

_UNKNOWN = "<unknown>"

# Log category -> team_logger level. Declarative so every category's
# level is visible in one place; _emit dispatches explicitly.
_CATEGORY_LEVEL = {
    "text": "info",
    "reasoning": "debug",
    "tool_call": "debug",
    "tool_result": "debug",
    "interaction": "warning",
    "controller_output": "warning",
    "runtime_ready": "info",
    "message": "info",
    "todo": "info",
    "other": "info",
}


def _cap(text: str, limit: int) -> str:
    """Truncate *text* to *limit* characters with a visible marker."""
    if len(text) <= limit:
        return text
    return text[:limit] + "… (truncated)"


def _extract_content(payload: Any) -> str:
    """Pull text content from a chunk payload.

    Mirrors ``renderer._extract_content``: dict payloads expose the text
    under ``content`` or ``output``; str payloads are returned as-is;
    anything else is stringified.
    """
    if isinstance(payload, dict):
        return payload.get("content", "") or payload.get("output", "")
    if isinstance(payload, str):
        return payload
    return str(payload)


def _render_role(role: TeamRole | None) -> str | None:
    """Render a ``TeamRole`` as its string value, tolerating ``None``."""
    if role is None:
        return None
    return role.value


def _classify(ctype: str, payload: Any) -> str:
    """Map a chunk ``type`` to a log category.

    The ``message`` type splits into ``runtime_ready`` (the team runtime
    ack) and plain ``message`` based on the payload event type.
    """
    if ctype in (_CHUNK_LLM_OUTPUT, _CHUNK_ANSWER):
        return "text"
    if ctype == _CHUNK_LLM_REASONING:
        return "reasoning"
    if ctype == _CHUNK_TOOL_CALL:
        return "tool_call"
    if ctype == _CHUNK_TOOL_RESULT:
        return "tool_result"
    if ctype == _CHUNK_INTERACTION:
        return "interaction"
    if ctype == _CHUNK_CONTROLLER_OUTPUT:
        return "controller_output"
    if ctype == _CHUNK_MESSAGE:
        if isinstance(payload, dict) and payload.get("event_type") == _RUNTIME_READY_EVENT:
            return "runtime_ready"
        return "message"
    if ctype == _CHUNK_TODO_UPDATED:
        return "todo"
    return "other"


def _tool_call_summary(payload: Any) -> str:
    """One-line summary of a ``tool_call`` chunk."""
    if not isinstance(payload, dict):
        return _cap(str(payload), _GENERIC_CAP)
    name = payload.get("tool_name", "")
    args = _cap(str(payload.get("tool_args", "")), _TOOL_ARGS_CAP)
    return f"tool_name={name} tool_args={args}"


def _tool_result_summary(payload: Any) -> str:
    """Two-line summary of a ``tool_result`` chunk, result capped."""
    if not isinstance(payload, dict):
        return _cap(str(payload), _GENERIC_CAP)
    name = payload.get("tool_name", "")
    args = _cap(str(payload.get("tool_args", "")), _TOOL_ARGS_CAP)
    result = _cap(str(payload.get("tool_result", "")), _TOOL_RESULT_CAP)
    return f"tool_name={name} tool_args={args}\nresult: {result}"


def _controller_output_summary(payload: Any) -> str:
    """Extract a readable controller task-failure message.

    Mirrors ``renderer._extract_controller_output_error`` for the
    ``task_failed`` shape, falling back to a capped repr so nothing is
    silently dropped.
    """
    if isinstance(payload, dict):
        payload_type = str(payload.get("type", "")).lower()
        if "task_failed" in payload_type:
            data = payload.get("data", [])
            texts: list[str] = []
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        text = str(item.get("text", "")).strip()
                        if text:
                            texts.append(text)
            if texts:
                return "\n".join(texts)
    return _cap(str(payload), _GENERIC_CAP)


def _runtime_ready_summary(payload: Any) -> str:
    """Compact summary of the ``team.runtime_ready`` ack."""
    if not isinstance(payload, dict):
        return _cap(str(payload), _GENERIC_CAP)
    return (
        f"team={payload.get('team_name')} "
        f"session={payload.get('session_id')} "
        f"activation={payload.get('activation_kind')}"
    )


def _interaction_summary(payload: Any) -> str:
    """Summary of an ``__interaction__`` (HITL request) chunk."""
    if isinstance(payload, dict):
        iid = payload.get("interaction_id", "unknown")
    else:
        iid = getattr(payload, "id", "unknown")
    return f"interaction_id={iid}\n{_cap(str(payload), _GENERIC_CAP)}"


def _generic_summary(payload: Any) -> str:
    """Best-effort content for ``message`` / ``todo`` / unknown chunks."""
    content = _extract_content(payload)
    if content:
        return content
    return _cap(str(payload), _GENERIC_CAP)


class TeamStreamLogger:
    """Opt-in processing object that logs aggregated team stream chunks.

    Construct one per ``run_agent_team_streaming`` call and pass it via
    the ``stream_logger`` keyword argument. The runner feeds every chunk
    through :meth:`feed` and calls :meth:`flush` once the stream ends.

    Token-streamed chunks (``llm_output`` / ``llm_reasoning``) are
    buffered and emitted as one record per contiguous
    ``(member, role, category)`` run; every other chunk type is logged
    immediately. Both :meth:`feed` and :meth:`flush` swallow their own
    exceptions -- a diagnostic logger must never break the stream it
    observes.
    """

    def __init__(self) -> None:
        """Initialize an empty aggregator."""
        self._buf: list[str] = []
        self._cat: str | None = None
        self._member: str | None = None
        self._role: str | None = None
        self._has_llm_output: bool = False
        self._chunk_count: int = 0

    def feed(self, chunk: OutputSchema) -> None:
        """Consume one stream chunk, buffering or logging as appropriate.

        Args:
            chunk: An ``OutputSchema`` (``TeamOutputSchema`` on the team
                path, carrying ``source_member`` / ``role``).
        """
        try:
            self._feed(chunk)
        except Exception:
            team_logger.exception("[team.stream] failed to log a stream chunk")

    def flush(self) -> None:
        """Emit any buffered run; call once after the stream ends."""
        try:
            self._flush_accumulated()
            if self._chunk_count:
                team_logger.debug("[team.stream] end, {} chunks", self._chunk_count)
        except Exception:
            team_logger.exception("[team.stream] failed to flush stream log")

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _feed(self, chunk: OutputSchema) -> None:
        """Core (exception-raising) body of :meth:`feed`."""
        self._chunk_count += 1
        ctype = chunk.type or ""
        payload = chunk.payload
        if isinstance(chunk, TeamOutputSchema):
            member = chunk.source_member
            role = _render_role(chunk.role)
        else:
            member = None
            role = None

        # `answer` duplicates the already-streamed `llm_output` text;
        # drop it once any llm_output has been seen (mirrors renderer).
        if ctype == _CHUNK_ANSWER and self._has_llm_output:
            return

        category = _classify(ctype, payload)

        if ctype in _ACCUMULATING_TYPES:
            content = _extract_content(payload)
            if not content:
                return
            run_changed = category != self._cat or member != self._member or role != self._role
            if self._cat is not None and run_changed:
                self._flush_accumulated()
            self._cat = category
            self._member = member
            self._role = role
            self._buf.append(content)
            if ctype == _CHUNK_LLM_OUTPUT:
                self._has_llm_output = True
            return

        # Discrete chunk: flush any pending run, then log immediately.
        self._flush_accumulated()
        summary = self._discrete_summary(category, payload)
        self._emit(category, member, role, summary)

    @staticmethod
    def _discrete_summary(category: str, payload: Any) -> str:
        """Build the log content for a discrete (non-accumulating) chunk."""
        if category == "tool_call":
            return _tool_call_summary(payload)
        if category == "tool_result":
            return _tool_result_summary(payload)
        if category == "controller_output":
            return _controller_output_summary(payload)
        if category == "runtime_ready":
            return _runtime_ready_summary(payload)
        if category == "interaction":
            return _interaction_summary(payload)
        return _generic_summary(payload)

    def _flush_accumulated(self) -> None:
        """Emit the buffered token run, if any, and reset run state."""
        if not self._buf:
            self._reset_run()
            return
        content = "".join(self._buf)
        category = self._cat or "other"
        member = self._member
        role = self._role
        self._reset_run()
        self._emit(category, member, role, content)

    def _reset_run(self) -> None:
        """Clear the current accumulation run (keeps ``_has_llm_output``)."""
        self._buf = []
        self._cat = None
        self._member = None
        self._role = None

    @staticmethod
    def _emit(
        category: str,
        member: str | None,
        role: str | None,
        content: str,
    ) -> None:
        """Format and dispatch one log record at the category's level."""
        if not content:
            return
        prefixed = "\n".join(f"  | {line}" for line in content.split("\n"))
        block = f"[team.stream] member={member or _UNKNOWN} role={role or _UNKNOWN} category={category}\n{prefixed}"
        level = _CATEGORY_LEVEL.get(category, "info")
        # Pass the rendered block as a single positional arg so literal
        # braces in model output / tool args never reach the formatter.
        if level == "debug":
            team_logger.debug("{}", block)
        elif level == "warning":
            team_logger.warning("{}", block)
        else:
            team_logger.info("{}", block)


__all__ = ["TeamStreamLogger"]
