# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Aggregated diagnostic logging of team streaming output.

``Runner.run_agent_team_streaming`` yields every leader and (in-process)
teammate chunk through one stream, each tagged with ``source_member`` /
``role`` (see ``TeamOutputSchema``). :class:`TeamStreamLogger` is an
opt-in processing object the caller builds with a target file path and
passes into the runner: it buffers token-streamed chunks **per source**
and writes one readable, multi-line record per run directly to that
file -- text at INFO, thinking / tool calls at DEBUG, interruptions /
task failures at WARN.

Chunks from different members are interleaved on the single leader
stream, so aggregation is tracked **per ``(member, role)`` source**
independently: a teammate's reasoning tokens landing between two leader
text tokens must not split the leader's run. Each source keeps its own
pending run, flushed when that source switches category, emits a
discrete chunk, or the stream ends.

The chunk-handling flow mirrors the team CLI renderer
(``openjiuwen/harness/cli/ui/renderer.py`` +
``openjiuwen/agent_teams/cli/stream_renderer.py``): the same chunk-type
vocabulary, the same ``answer``-vs-``llm_output`` dedup, and the same
controller-output extraction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

import logging

from openjiuwen.agent_teams.schema.stream import TeamOutputSchema
from openjiuwen.agent_teams.schema.team import TeamRole
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
_CHUNK_TOOL_UPDATE = "tool_update"
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

_logger = logging.getLogger(__name__)

# Log category -> level label written into each record. Declarative so
# every category's level is visible in one place.
_CATEGORY_LEVEL = {
    "text": "INFO",
    "reasoning": "DEBUG",
    "tool_call": "DEBUG",
    "tool_result": "DEBUG",
    "tool_update": "DEBUG",
    "interaction": "WARN",
    "controller_output": "WARN",
    "runtime_ready": "INFO",
    "message": "INFO",
    "todo": "INFO",
    "other": "INFO",
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
    if ctype == _CHUNK_TOOL_UPDATE:
        return "tool_update"
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
    """One-line summary of a ``tool_call`` chunk.

    When the standard top-level ``tool_name`` / ``tool_args`` keys are
    missing (non-``tool_tracker`` emission paths use different shapes),
    fall back to a capped dump of the whole payload so the record stays
    informative instead of showing two empty fields.
    """
    if not isinstance(payload, dict):
        return _cap(str(payload), _GENERIC_CAP)
    name = payload.get("tool_name", "")
    args_raw = payload.get("tool_args", "")
    if not name and not args_raw:
        return _cap(str(payload), _GENERIC_CAP)
    args = _cap(str(args_raw), _TOOL_ARGS_CAP)
    return f"tool_name={name} tool_args={args}"


def _tool_result_summary(payload: Any) -> str:
    """Two-line summary of a ``tool_result`` chunk, result capped.

    Same empty-field fallback as :func:`_tool_call_summary`.
    """
    if not isinstance(payload, dict):
        return _cap(str(payload), _GENERIC_CAP)
    name = payload.get("tool_name", "")
    args_raw = payload.get("tool_args", "")
    result_raw = payload.get("tool_result", "")
    if not name and not args_raw and not result_raw:
        return _cap(str(payload), _GENERIC_CAP)
    args = _cap(str(args_raw), _TOOL_ARGS_CAP)
    result = _cap(str(result_raw), _TOOL_RESULT_CAP)
    return f"tool_name={name} tool_args={args}\nresult: {result}"


def _tool_update_summary(payload: Any) -> str:
    """Summary of a ``tool_update`` chunk (tool-call status notification).

    The canonical shape (emitted by ``stream_event_rail`` in third-party
    rail packages such as ``jiuwenclaw``) wraps the tool fields in an
    inner ``tool_update`` key with ``status`` carrying ``in_progress`` /
    ``finish``. Fall back to the raw payload if the wrapper is missing.
    """
    if not isinstance(payload, dict):
        return _cap(str(payload), _GENERIC_CAP)
    update = payload.get("tool_update")
    if not isinstance(update, dict):
        return _cap(str(payload), _GENERIC_CAP)
    name = update.get("tool_name", "")
    status = update.get("status", "")
    call_id = update.get("tool_call_id", "")
    args = _cap(str(update.get("arguments", "")), _TOOL_ARGS_CAP)
    return f"tool_name={name} status={status} tool_call_id={call_id} arguments={args}"


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


@dataclass
class _Run:
    """A pending per-source accumulation run of token-streamed chunks."""

    category: str
    buf: list[str] = field(default_factory=list)


class TeamStreamLogger:
    """Opt-in processing object that writes aggregated team stream records.

    Construct one per ``run_agent_team_streaming`` call with the target
    file path and pass it via the ``stream_logger`` keyword argument. The
    runner feeds every chunk through :meth:`feed` and calls :meth:`flush`
    once the stream ends.

    Token-streamed chunks (``llm_output`` / ``llm_reasoning``) are
    buffered **per ``(member, role)`` source** and emitted as one record
    per contiguous same-category run; every other chunk type is written
    immediately. Tracking runs per source -- rather than with a single
    cursor -- is what keeps interleaved teammate / leader chunks from
    splitting each other's runs.

    Both :meth:`feed` and :meth:`flush` swallow their own exceptions (a
    best-effort marker is written to the file instead) -- a diagnostic
    logger must never break the stream it observes. The constructor does
    *not* swallow: an unusable path fails fast at construction time.
    """

    def __init__(self, file_path: str | Path) -> None:
        """Open *file_path* for appending aggregated stream records.

        Args:
            file_path: Destination file. Parent directories are created
                if missing. Opened in append mode; raises on an unusable
                path so the caller fails fast at construction time.
        """
        self._path = Path(file_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file: TextIO | None = open(self._path, "a", encoding="utf-8")
        self._runs: dict[tuple[str | None, str | None], _Run] = {}
        self._llm_output_seen: set[tuple[str | None, str | None]] = set()
        self._chunk_count: int = 0

    def feed(self, chunk: OutputSchema) -> None:
        """Consume one stream chunk, buffering or writing as appropriate.

        Args:
            chunk: An ``OutputSchema`` (``TeamOutputSchema`` on the team
                path, carrying ``source_member`` / ``role``).
        """
        try:
            self._feed(chunk)
        except Exception as exc:  # diagnostic logger must never break the stream
            self._safe_write(f"[WARN] stream logger feed error: {exc!r}")

    def flush(self) -> None:
        """Flush every pending per-source run and close the file.

        Call once after the stream ends. Never raises.
        """
        try:
            for key in list(self._runs.keys()):
                self._flush_key(key)
            if self._chunk_count:
                self._safe_write(f"[INFO] stream end, {self._chunk_count} chunks")
        except Exception as exc:
            self._safe_write(f"[WARN] stream logger flush error: {exc!r}")
        finally:
            if self._file is not None:
                try:
                    self._file.close()
                except Exception as exc:
                    _logger.debug("Failed to close stream log file: %s", exc)
                self._file = None

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _feed(self, chunk: OutputSchema) -> None:
        """Core (exception-raising) body of :meth:`feed`."""
        self._chunk_count += 1
        # Untagged chunks come from infrastructure layers (tracer
        # spans, workflow normalisation, etc.) that bypass the team
        # ``StreamController._tag_chunk`` path. They are not team-member
        # output -- skipping them keeps the diagnostic file focused on
        # team flow and prevents tracer-span dumps from drowning the log.
        if not isinstance(chunk, TeamOutputSchema):
            return
        ctype = chunk.type or ""
        payload = chunk.payload
        member = chunk.source_member
        role = _render_role(chunk.role)
        key = (member, role)

        # `answer` duplicates the same source's already-streamed
        # llm_output; drop it once that source has produced llm_output.
        if ctype == _CHUNK_ANSWER and key in self._llm_output_seen:
            return

        category = _classify(ctype, payload)

        if ctype in _ACCUMULATING_TYPES:
            content = _extract_content(payload)
            if not content:
                return
            run = self._runs.get(key)
            if run is not None and run.category != category:
                # Same source switched category -> flush its old run.
                self._flush_key(key)
                run = None
            if run is None:
                run = _Run(category=category)
                self._runs[key] = run
            run.buf.append(content)
            if ctype == _CHUNK_LLM_OUTPUT:
                self._llm_output_seen.add(key)
            return

        # Discrete chunk: flush this source's pending run, then write now.
        self._flush_key(key)
        summary = self._discrete_summary(category, payload)
        self._emit(category, member, role, summary)

    @staticmethod
    def _discrete_summary(category: str, payload: Any) -> str:
        """Build the record content for a discrete (non-accumulating) chunk."""
        if category == "tool_call":
            return _tool_call_summary(payload)
        if category == "tool_result":
            return _tool_result_summary(payload)
        if category == "tool_update":
            return _tool_update_summary(payload)
        if category == "controller_output":
            return _controller_output_summary(payload)
        if category == "runtime_ready":
            return _runtime_ready_summary(payload)
        if category == "interaction":
            return _interaction_summary(payload)
        return _generic_summary(payload)

    def _flush_key(self, key: tuple[str | None, str | None]) -> None:
        """Write and drop the pending run for *key*, if any."""
        run = self._runs.pop(key, None)
        if run is None or not run.buf:
            return
        member, role = key
        self._emit(run.category, member, role, "".join(run.buf))

    def _emit(
        self,
        category: str,
        member: str | None,
        role: str | None,
        content: str,
    ) -> None:
        """Format one record and write it to the file."""
        if not content:
            return
        level = _CATEGORY_LEVEL.get(category, "INFO")
        prefixed = "\n".join(f"  | {line}" for line in content.split("\n"))
        header = f"[{level}] member={member or _UNKNOWN} role={role or _UNKNOWN} category={category}"
        self._safe_write(f"{header}\n{prefixed}")

    def _safe_write(self, body: str) -> None:
        """Write one timestamped record to the file; swallow any I/O error."""
        if self._file is None:
            return
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        try:
            self._file.write(f"{timestamp} {body}\n")
            self._file.flush()
        except Exception as exc:
            _logger.debug("Stream log write failed: %s", exc)


__all__ = ["TeamStreamLogger"]
