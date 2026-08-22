# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""In-memory accumulation and snapshot persistence of trajectory steps.

One ``TrajectoryCollector`` serves all traces captured while recording is
active. Steps are keyed by tracer ``trace_id`` (one trace per agent session),
each trace becoming its own trajectory snapshot file that is atomically
rewritten every ``snapshot_every_n_steps`` steps (and always at
finalization). In-memory state is guarded by one lock, but disk writes run
outside it under per-trace locks, so slow I/O for one trace never stalls
capture for the others. Capture never raises into the agent loop:
persistence failures are logged and recording continues.

Scoping: once ``allow_agent`` has been called, only traces bound with an
allowed agent's identity — or descending from a live (not yet finalized)
accepted trace — are recorded; everything else is dropped at
``bind_trace``/``add_step``. Identity/lineage records of finalized traces
are bounded by ``max_retained_traces``. Without ``allow_agent`` the
collector stays permissive and records every trace it is told about
(direct/internal use).

Lineage: ``current_parent_trace_id`` marks the async context of a running
tool call with its trace id. Agent sessions created inside that context
(subagents) bind to it as their parent, so their trajectories land under the
parent run's ``subagents/`` directory and carry ``parent_trajectory_id`` in
their header. Traces whose parent cannot be determined become top-level runs.
"""

import asyncio
import json
import re
from collections import deque
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Literal, NamedTuple, Optional

from dateutil.tz import tzlocal

from openjiuwen.core.common.logging import session_logger
from openjiuwen.core.session.trajectory.config import TrajectoryConfig, default_root
from openjiuwen.core.session.trajectory.models import (
    TRAJECTORY_SCHEMA_VERSION,
    AgentInfo,
    ErrorRecord,
    FinalMetrics,
    Observation,
    StepError,
    StepMetrics,
    TrajectoryStep,
    TrajectoryToolCall,
)
from openjiuwen.core.session.trajectory.redaction import redact_text
from openjiuwen.core.session.trajectory.writer import TrajectoryWriter

# Trace id of the tool call currently executing in this async context, set by
# the trajectory handler around plugin spans. Sub-agent sessions created while
# a tool runs inherit it as their parent trajectory.
current_parent_trace_id: ContextVar[Optional[str]] = ContextVar("trajectory_parent_trace_id", default=None)

# Trace id of the agent-invoked workflow currently executing in this async
# context, set around workflow spans. Workflow component events carry no
# trace id of their own; this routes them to the invoking trace even when
# concurrent sessions have re-bound the shared workflow handler's instance
# state.
current_workflow_trace_id: ContextVar[Optional[str]] = ContextVar("trajectory_workflow_trace_id", default=None)


class _TraceBinding(NamedTuple):
    """Identity/lineage captured at tracer init, before any step arrives."""

    session_id: Optional[str]
    agent_name: Optional[str]
    parent_trace_id: Optional[str]


def _now_iso() -> str:
    return datetime.now(tz=tzlocal()).isoformat()


def _safe_dir_name(name: str) -> str:
    """Reduce an agent session id to a single safe directory name.

    Path separators and other special characters are replaced so a
    caller-supplied session id can never escape the trajectory root.
    """
    cleaned = re.sub(r"[^\w.-]", "_", name)
    if not cleaned.strip("."):
        return "_"
    return cleaned


def _file_stem(started_at_iso: str, trajectory_id: str) -> str:
    """Filename stem ``<YYYY-MM-DD_HH-MM-SS>_<trajectory_id>``.

    The timestamp prefix makes each run identifiable (and lexicographically
    sortable) by its start time; colons are avoided for Windows compatibility.
    """
    compact = datetime.fromisoformat(started_at_iso).strftime("%Y-%m-%d_%H-%M-%S")
    return f"{compact}_{trajectory_id}"


def to_text(value) -> str | None:
    """Best-effort conversion of an arbitrary payload to text."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


class _TraceBuffer:
    """Mutable per-trace recording state."""

    def __init__(
        self,
        trajectory_id: str,
        binding: "_TraceBinding | None" = None,
        parent_buffer: "_TraceBuffer | None" = None,
        session_dir: str | None = None,
    ):
        self.trajectory_id = trajectory_id
        # Serialized steps retained for full-document snapshots.
        self.steps: list[dict] = []
        # Tool calls declared by agent steps, awaiting a matching observation.
        self.pending_tool_calls: list[dict] = []
        self.totals = FinalMetrics()
        self.started_at = _now_iso()
        # Steps appended since the last snapshot write (snapshot cadence).
        self.steps_since_snapshot = 0
        # Stable snapshot filename stem; the timestamp prefix identifies the run.
        self.file_stem = _file_stem(self.started_at, trajectory_id)
        # Claim lineage only when the parent run is live: a parent that
        # already finalized (e.g. a session created from a stale task
        # context) would leave a top-level file pointing at a run it is not
        # nested under.
        self.parent_trajectory_id = binding.parent_trace_id if binding and parent_buffer is not None else None
        self.agent_info = self._build_agent_info(binding)
        if parent_buffer is not None:
            # Nest under the top-level ancestor's run directory; deeper
            # ancestry stays flat in subagents/ and is expressed via
            # parent_trajectory_id headers.
            self.run_dir = parent_buffer.run_dir
            self.rel_path = f"{self.run_dir}/subagents/{self.file_stem}.json"
        else:
            self.run_dir = f"{session_dir}/{self.file_stem}" if session_dir else self.file_stem
            self.rel_path = f"{self.run_dir}/trajectory.json"

    @staticmethod
    def _build_agent_info(binding: "_TraceBinding | None") -> dict | None:
        if binding is None or (binding.agent_name is None and binding.session_id is None):
            return None
        extra = {"agent_session_id": binding.session_id} if binding.session_id else None
        return AgentInfo(name=binding.agent_name or "", extra=extra).model_dump(exclude_none=True)


class TrajectoryCollector:
    """Builds trajectory steps from capture events and snapshots them to disk."""

    def __init__(self, session_id: str, config: TrajectoryConfig):
        self._session_id = session_id
        self._config = config
        self._writer = TrajectoryWriter(Path(config.root) if config.root else default_root(), session_id)
        self._buffers: dict[str, _TraceBuffer] = {}
        self._bindings: dict[str, _TraceBinding] = {}
        # Guards in-memory state only; disk writes happen outside so slow
        # I/O for one trace never stalls capture for the others.
        self._lock = asyncio.Lock()
        # Per-trace write locks keep a trace's snapshots ordered.
        self._write_locks: dict[str, asyncio.Lock] = {}
        # None = permissive (record every trace); a set = record only traces
        # of these agents and their descendants.
        self._allowed_agents: set[str] | None = None
        self._accepted_traces: set[str] = set()
        # Finalized traces stop accepting descendants; insertion order bounds
        # retained identity state via config.max_retained_traces.
        self._finalized_traces: set[str] = set()
        self._finalized_order: deque[str] = deque()
        # Traces dropped via config.exclude_session_prefixes. Consulted by
        # add_step so permissive mode drops their steps too, and by
        # bind_trace so their descendants are excluded with them. Bounded
        # like finalized traces.
        self._excluded_traces: set[str] = set()
        self._excluded_order: deque[str] = deque()
        self._ever_accepted = 0
        # Runtime pause switch (TrajectoryRecorder.set_enabled): while False,
        # bind_trace and add_step drop everything, including steps of traces
        # that were already recording.
        self.recording_enabled = True

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def config(self) -> TrajectoryConfig:
        return self._config

    @property
    def ever_accepted(self) -> int:
        """Number of traces accepted for recording over this collector's life."""
        return self._ever_accepted

    def allow_agent(self, agent_key: str) -> None:
        """Restrict recording to allowed agents and add `agent_key` to them.

        The first call switches the collector from permissive to filtering
        mode; traces bound afterwards are recorded only when their agent is
        allowed or their parent trace was accepted.
        """
        if self._allowed_agents is None:
            self._allowed_agents = set()
        self._allowed_agents.add(agent_key)

    def disallow_agent(self, agent_key: str) -> None:
        """Remove `agent_key` from the allowed agents. No-op when unknown."""
        if self._allowed_agents is not None:
            self._allowed_agents.discard(agent_key)

    def bind_trace(self, trace_id: str, session_id: str | None = None, agent_name: str | None = None) -> None:
        """Record identity and lineage for a trace before its first step.

        Called synchronously from ``Tracer.init()`` via the trajectory
        handler. The parent is whatever tool span is executing in the current
        async context (None for top-level runs). In filtering mode, traces of
        agents that are not allowed (and not descendants of an accepted
        trace) are ignored entirely.
        """
        if not self.recording_enabled:
            return
        parent = current_parent_trace_id.get()
        if parent == trace_id:
            parent = None
        if self._is_excluded_session(session_id, parent):
            self._mark_excluded(trace_id)
            return
        if self._allowed_agents is not None:
            # Descent from a finalized parent is rejected: a task spawned
            # inside a recorded tool call carries the parent marker in its
            # copied context forever, and must not keep binding new sessions
            # to a run that already ended.
            descends_from_live_run = parent in self._accepted_traces and parent not in self._finalized_traces
            if agent_name not in self._allowed_agents and not descends_from_live_run:
                session_logger.debug("trajectory: ignoring trace %s (agent %s not attached)", trace_id, agent_name)
                return
            if trace_id not in self._accepted_traces:
                self._accepted_traces.add(trace_id)
                self._ever_accepted += 1
        self._bindings[trace_id] = _TraceBinding(session_id=session_id, agent_name=agent_name, parent_trace_id=parent)

    def claim_tool_call(self, trace_id: str, function_name: str) -> str | None:
        """Claim the earliest unclaimed declared tool call matching `function_name`.

        Returns its ``tool_call_id`` for observation linkage, or None when no
        declaration matches (e.g. a tool invoked outside an LLM turn).
        """
        buffer = self._buffers.get(trace_id)
        if buffer is None:
            return None
        for call in buffer.pending_tool_calls:
            if not call["claimed"] and call["name"] == function_name:
                call["claimed"] = True
                return call["id"]
        return None

    async def add_step(
        self,
        trace_id: str,
        *,
        role: Literal["user", "agent", "system"],
        invoke_type: str,
        message: str | None = None,
        model_name: str | None = None,
        reasoning_content: str | None = None,
        tool_calls: list[TrajectoryToolCall] | None = None,
        observation: Observation | None = None,
        error: StepError | None = None,
        metrics: StepMetrics | None = None,
        extra: dict | None = None,
    ) -> None:
        """Append one step to the trajectory of `trace_id` and snapshot it.

        In filtering mode, steps for traces that were not accepted at
        ``bind_trace`` (including never-bound traces) are dropped.
        """
        if not self.recording_enabled:
            return
        if self._allowed_agents is not None and trace_id not in self._accepted_traces:
            return
        if trace_id in self._excluded_traces:
            return
        async with self._lock:
            buffer = self._buffers.get(trace_id)
            if buffer is None:
                binding = self._bindings.get(trace_id)
                parent_buffer = None
                if binding is not None and binding.parent_trace_id is not None:
                    parent_buffer = self._buffers.get(binding.parent_trace_id)
                session_dir = None
                if self._config.group_by_agent_session and binding is not None and binding.session_id:
                    session_dir = _safe_dir_name(binding.session_id)
                buffer = _TraceBuffer(
                    trajectory_id=trace_id, binding=binding, parent_buffer=parent_buffer, session_dir=session_dir
                )
                self._buffers[trace_id] = buffer

            truncated_fields: list[str] = []
            message = self._sanitize("message", message, truncated_fields)
            reasoning_content = self._sanitize("reasoning_content", reasoning_content, truncated_fields)
            for call in tool_calls or []:
                call.arguments = self._sanitize("tool_calls.arguments", call.arguments, truncated_fields)
            for result in observation.results if observation else []:
                result.content = self._sanitize("observation.content", result.content, truncated_fields)
            if error is not None:
                error.message = self._sanitize("error.message", error.message, truncated_fields) or ""

            # Extra carries captured text too (plugin input arguments, workflow
            # component metadata); its top-level string values must pass the
            # same redaction/truncation guards as the primary fields.
            step_extra = {
                key: self._sanitize(f"extra.{key}", value, truncated_fields) if isinstance(value, str) else value
                for key, value in (extra or {}).items()
            }
            step_extra["invoke_type"] = invoke_type
            if truncated_fields:
                step_extra["truncated_fields"] = sorted(set(truncated_fields))

            step = TrajectoryStep(
                step_id=len(buffer.steps) + 1,
                timestamp=_now_iso(),
                role=role,
                message=message,
                model_name=model_name,
                reasoning_content=reasoning_content,
                tool_calls=tool_calls,
                observation=observation,
                error=error,
                metrics=metrics,
                extra=step_extra,
            )

            for call in tool_calls or []:
                buffer.pending_tool_calls.append(
                    {"id": call.tool_call_id, "name": call.function_name, "claimed": False}
                )
            self._update_totals(buffer, step, invoke_type)

            buffer.steps.append(step.model_dump(exclude_none=True))
            buffer.steps_since_snapshot += 1
            document = None
            rel_path = buffer.rel_path
            if buffer.steps_since_snapshot >= max(1, self._config.snapshot_every_n_steps):
                buffer.steps_since_snapshot = 0
                document = self._build_document(buffer, final_metrics=self._stamp_running(buffer))
        if document is not None:
            await self._write_snapshot(trace_id, rel_path, document)

    async def finalize_trace(self, trace_id: str) -> None:
        """Stamp final metrics for one trace and release its buffer.

        Called when the owning agent session finishes (``on_trace_close``),
        so long-lived processes do not accumulate step buffers for completed
        runs. The lightweight binding is kept: callers may keep invoking a
        session after ``post_run`` (e.g. ``Runner.run_agent`` closes the
        session it was handed), and late steps must still carry identity and
        lineage. A finalized trace stops accepting new descendants. No-op
        for unknown or already finalized traces.
        """
        async with self._lock:
            buffer = self._buffers.pop(trace_id, None)
            self._mark_finalized(trace_id)
            pending = None
            if buffer is not None and buffer.steps:
                pending = (buffer.rel_path, self._build_document(buffer, final_metrics=self._stamp_final(buffer)))
        if pending is not None:
            await self._write_snapshot(trace_id, *pending)

    async def finalize_all(self) -> None:
        """Stamp final metrics into every open trace's snapshot and reset."""
        async with self._lock:
            pending = [
                (buffer.trajectory_id, buffer.rel_path, self._build_document(buffer, self._stamp_final(buffer)))
                for buffer in self._buffers.values()
                if buffer.steps
            ]
            self._buffers.clear()
            self._bindings.clear()
            self._accepted_traces.clear()
            self._finalized_traces.clear()
            self._finalized_order.clear()
            self._excluded_traces.clear()
            self._excluded_order.clear()
        for trace_id, rel_path, document in pending:
            await self._write_snapshot(trace_id, rel_path, document)
        # _write_locks is left intact: a straggler add_step write may still
        # hold one, and re-minting a lock for the same trace would let two
        # writes race on the same file.

    def _is_excluded_session(self, session_id: str | None, parent: str | None) -> bool:
        """True when `session_id` matches ``config.exclude_session_prefixes``
        or the trace descends from an already excluded one."""
        if parent in self._excluded_traces:
            return True
        prefixes = tuple(self._config.exclude_session_prefixes)
        return bool(prefixes and session_id and session_id.startswith(prefixes))

    def _mark_excluded(self, trace_id: str) -> None:
        """Record an excluded trace so its late steps and descendants drop."""
        if trace_id in self._excluded_traces:
            return
        self._excluded_traces.add(trace_id)
        self._excluded_order.append(trace_id)
        limit = max(1, self._config.max_retained_traces)
        while len(self._excluded_order) > limit:
            self._excluded_traces.discard(self._excluded_order.popleft())

    def _mark_finalized(self, trace_id: str) -> None:
        """Record that `trace_id` ended; evict oldest identity state past the cap.

        Only meaningful in filtering mode: descent checks consult the
        finalized set, and the retention cap bounds bindings/acceptance for
        long-lived hosts. Evicted traces drop late steps.
        """
        if self._allowed_agents is None or trace_id not in self._accepted_traces:
            return
        if trace_id in self._finalized_traces:
            return
        self._finalized_traces.add(trace_id)
        self._finalized_order.append(trace_id)
        limit = max(1, self._config.max_retained_traces)
        while len(self._finalized_order) > limit:
            evicted = self._finalized_order.popleft()
            self._finalized_traces.discard(evicted)
            self._accepted_traces.discard(evicted)
            self._bindings.pop(evicted, None)
            self._write_locks.pop(evicted, None)

    @staticmethod
    def _stamp_running(buffer: _TraceBuffer) -> dict:
        """Running totals for a mid-run snapshot; ``ended_at`` stays empty.

        Long-lived hosts may never close a session mid-flight, so snapshots
        carry the totals accumulated so far — an empty ``ended_at`` marks the
        trajectory as still in progress.
        """
        totals = buffer.totals
        totals.success = totals.error_count == 0
        totals.started_at = buffer.started_at
        return totals.model_dump()

    @staticmethod
    def _stamp_final(buffer: _TraceBuffer) -> dict:
        """Fill the final-metrics footer of `buffer` and return it as a dict."""
        buffer.totals.ended_at = _now_iso()
        return TrajectoryCollector._stamp_running(buffer)

    def _sanitize(self, field: str, text: str | None, truncated_fields: list[str]) -> str | None:
        """Redact secret-shaped substrings, then apply the truncation guard.

        Redaction is unconditional — trajectory files must not contain
        secrets — and runs first so truncation cannot split a secret and
        leave a recognizable prefix behind.
        """
        text = redact_text(text)
        return self._truncate(field, text, truncated_fields)

    def _truncate(self, field: str, text: str | None, truncated_fields: list[str]) -> str | None:
        limit = self._config.max_content_chars
        if text is None or limit <= 0 or len(text) <= limit:
            return text
        truncated_fields.append(field)
        return text[:limit]

    @staticmethod
    def _update_totals(buffer: _TraceBuffer, step: TrajectoryStep, invoke_type: str) -> None:
        totals = buffer.totals
        if invoke_type == "llm":
            totals.llm_call_count += 1
        elif invoke_type == "plugin":
            totals.tool_call_count += 1
        if step.metrics is not None:
            totals.total_input_tokens += step.metrics.input_tokens
            totals.total_output_tokens += step.metrics.output_tokens
            totals.total_tokens += step.metrics.total_tokens
            totals.total_cache_tokens += step.metrics.cache_tokens
            totals.total_cost += step.metrics.cost
        if step.error is not None:
            totals.error_count += 1
            totals.errors.append(
                ErrorRecord(step_id=step.step_id, error_code=step.error.error_code, message=step.error.message)
            )

    def _build_document(self, buffer: _TraceBuffer, final_metrics: dict | None) -> dict:
        """Assemble the snapshot document. Must run under ``self._lock``.

        ``steps`` is copied so the write (which happens outside the lock)
        never races a concurrent append.
        """
        return {
            "schema_version": TRAJECTORY_SCHEMA_VERSION,
            "session_id": self._session_id,
            "trajectory_id": buffer.trajectory_id,
            "parent_trajectory_id": buffer.parent_trajectory_id,
            "started_at": buffer.started_at,
            "agent": buffer.agent_info,
            "steps": list(buffer.steps),
            "final_metrics": final_metrics,
        }

    async def _write_snapshot(self, trace_id: str, rel_path: str, document: dict) -> None:
        """Persist `document` under the trace's write lock.

        Runs outside the collector state lock: slow disk for one trace must
        not stall capture for concurrently recorded agents. The per-trace
        lock keeps a single trace's snapshots ordered.
        """
        lock = self._write_locks.setdefault(trace_id, asyncio.Lock())
        async with lock:
            try:
                await self._writer.write_snapshot(rel_path, document)
            except OSError as exc:
                session_logger.warning("trajectory: failed to persist snapshot: %s", exc)
