# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Attach-based lifecycle for trajectory recording.

Recording is per-agent and off by default. ``TrajectoryRecorder.attach``
marks an agent for recording and registers the capture handlers with the
global ``TracerHandlerRegistry``, plus a context-compression capture
callback on the runner callback framework (compression rewrites history
without emitting tracer spans). Agent sessions pick up the handlers when
they are created, so attach before the run you want recorded; only sessions
carrying an attached agent's identity are captured (``Runner.run_agent``
passes the agent's card to the session automatically). Sub-agent sessions
spawned inside a recorded run are captured through lineage without being
attached themselves. ``close`` flushes final metrics for all open
trajectories and unregisters the handlers; prefer ``async with`` for
exception-safe scoping. A recorder dropped without ``close`` is defused by
a GC finalizer (handlers unregister, open trajectories are not finalized).

Limitations: recording is process-local — sessions created in spawned child
processes (``Runner.spawn_agent``) or behind remote/A2A agents are invisible
to this process's handler registry and are silently not recorded. ``close``
warns when nothing was recorded at all.
"""

import weakref
from pathlib import Path
from uuid import uuid4

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import session_logger
from openjiuwen.core.common.logging.utils import normalize_and_validate_log_path
from openjiuwen.core.runner.callback import lazy_callback_framework
from openjiuwen.core.runner.callback.events import ContextEvents
from openjiuwen.core.session.tracer.tracer import TracerHandlerRegistry
from openjiuwen.core.session.trajectory.collector import TrajectoryCollector
from openjiuwen.core.session.trajectory.config import TrajectoryConfig, default_root
from openjiuwen.core.session.trajectory.handler import (
    TrajectoryAgentHandler,
    TrajectoryCompressionBridge,
    TrajectoryWorkflowHandler,
)

_AGENT_HANDLER_PREFIX = "trajectory_agent"
_WORKFLOW_HANDLER_PREFIX = "trajectory_workflow"


def _agent_key(agent) -> str:
    """Resolve the identity key sessions bind under: card name, else card id.

    Mirrors the ``agent_name`` resolution in ``AgentSession`` so an attached
    agent matches the traces its sessions produce. Accepts an agent exposing
    ``.card`` or a card-like object directly.
    """
    card = getattr(agent, "card", agent)
    key = getattr(card, "name", None) or getattr(card, "id", None)
    if not key:
        raise build_error(
            StatusCode.SESSION_TRAJECTORY_EXECUTION_ERROR,
            reason=f"cannot attach {type(agent).__name__}: no card name or id to identify its sessions",
        )
    return key


def _release_leaked_handlers(agent_handler_name: str, workflow_handler_name: str, compression_callback) -> None:
    """GC finalizer: unregister a dropped recorder's handlers.

    Module-level (must not close over the recorder) and best-effort: it stops
    the leak and the zombie capture, but open trajectories lose their
    final-metrics footer because flushing requires an event loop.
    """
    removed = TracerHandlerRegistry.unregister_handler(agent_handler_name)
    removed = TracerHandlerRegistry.unregister_handler(workflow_handler_name) or removed
    try:
        lazy_callback_framework.unregister_sync(ContextEvents.CONTEXT_COMPRESSION_STATE, compression_callback)
    except Exception:  # noqa: BLE001 - the runner may already be gone at interpreter shutdown
        pass
    if removed:
        session_logger.warning(
            "trajectory: recorder was garbage-collected without close(); "
            "handlers unregistered, open trajectories were not finalized"
        )


class TrajectoryRecorder:
    """Records trajectories for the agents explicitly attached to it."""

    def __init__(self, session_id: str = "default", config: TrajectoryConfig | None = None):
        self._session_id = session_id
        self._config = config or TrajectoryConfig()
        self._collector = TrajectoryCollector(session_id, self._config)
        self._agent_handler = TrajectoryAgentHandler(self._collector)
        self._workflow_handler = TrajectoryWorkflowHandler(self._collector)
        self._compression_bridge = TrajectoryCompressionBridge(self._collector)
        # Unique registry names so multiple recorders can coexist in one process.
        suffix = uuid4().hex[:8]
        self._agent_handler_name = f"{_AGENT_HANDLER_PREFIX}_{suffix}"
        self._workflow_handler_name = f"{_WORKFLOW_HANDLER_PREFIX}_{suffix}"
        self._registered = False
        self._finalizer: weakref.finalize | None = None
        # Identity key -> card id, to detect distinct agents colliding on a key.
        self._attached_ids: dict[str, str] = {}

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def root(self) -> Path:
        return Path(self._config.root) if self._config.root else default_root()

    def attach(self, agent) -> "TrajectoryRecorder":
        """Record future sessions of `agent`; chainable and idempotent.

        Sessions pick up capture handlers at creation time, so sessions that
        already exist when ``attach`` is called are not recorded.

        Args:
            agent: An agent exposing ``.card`` (e.g. ``BaseAgent``) or a
                card-like object with ``name``/``id``.

        Returns:
            This recorder, for chaining.
        """
        key = _agent_key(agent)
        card = getattr(agent, "card", agent)
        card_id = getattr(card, "id", None) or key
        known_id = self._attached_ids.get(key)
        if known_id is not None and known_id != card_id:
            session_logger.warning(
                "trajectory: distinct agents resolve to identity key '%s'; their runs will be recorded together",
                key,
            )
        if not self._registered:
            self._prepare_root()
            TracerHandlerRegistry.register_handler(self._agent_handler_name, self._agent_handler)
            TracerHandlerRegistry.register_handler(self._workflow_handler_name, self._workflow_handler)
            # Compression state travels over the runner callback framework,
            # not the tracer; capture it so the summary that replaces
            # compressed history lands in the trajectory.
            lazy_callback_framework.register_sync(
                ContextEvents.CONTEXT_COMPRESSION_STATE,
                self._compression_bridge.on_compression_state,
                namespace=self._agent_handler_name,
            )
            self._registered = True
            self._finalizer = weakref.finalize(
                self,
                _release_leaked_handlers,
                self._agent_handler_name,
                self._workflow_handler_name,
                self._compression_bridge.on_compression_state,
            )
        self._attached_ids[key] = card_id
        self._collector.allow_agent(key)
        return self

    def detach(self, agent) -> None:
        """Stop recording new sessions of `agent`. No-op when not attached.

        Sessions already recording keep recording until they close; only
        sessions created after ``detach`` are excluded.
        """
        key = _agent_key(agent)
        self._attached_ids.pop(key, None)
        self._collector.disallow_agent(key)

    def set_enabled(self, enabled: bool) -> None:
        """Pause (False) or resume (True) recording without detaching.

        Unlike ``detach``, this takes effect immediately for sessions that
        are already recording: while paused, their steps are dropped and no
        new traces are accepted. Resuming continues recording into the same
        trajectories. For hosts exposing a runtime recording toggle.
        """
        self._collector.recording_enabled = enabled

    async def close(self) -> None:
        """Unregister handlers and flush final metrics for all open trajectories.

        Idempotent. Warns when the recorder recorded nothing — the usual
        causes are sessions created before ``attach``, sessions missing the
        agent's card, or sessions living in another process. The recorder can
        be reused: a later ``attach`` re-registers the handlers.
        """
        if self._registered:
            if self._finalizer is not None:
                self._finalizer.detach()
                self._finalizer = None
            TracerHandlerRegistry.unregister_handler(self._agent_handler_name)
            TracerHandlerRegistry.unregister_handler(self._workflow_handler_name)
            try:
                lazy_callback_framework.unregister_sync(
                    ContextEvents.CONTEXT_COMPRESSION_STATE,
                    self._compression_bridge.on_compression_state,
                )
            except Exception as exc:  # noqa: BLE001 - flushing must proceed even if the runner is torn down
                session_logger.warning("trajectory: failed to unregister compression callback: %s", exc)
            self._registered = False
            if self._collector.ever_accepted == 0:
                session_logger.warning(
                    "trajectory: recorder closed without recording any trace — ensure sessions carry the "
                    "attached agent's card and are created after attach(); sessions in other processes "
                    "or behind remote agents cannot be recorded"
                )
        await self._collector.finalize_all()

    async def __aenter__(self) -> "TrajectoryRecorder":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    def _prepare_root(self) -> None:
        """Validate the storage root against sensitive paths and create it."""
        try:
            normalized = normalize_and_validate_log_path(str(self.root))
        except BaseError as err:
            raise build_error(
                StatusCode.SESSION_TRAJECTORY_EXECUTION_ERROR,
                cause=err,
                reason=f"trajectory root rejected: {self.root}",
            ) from err
        try:
            Path(normalized).mkdir(parents=True, exist_ok=True)
        except OSError as err:
            raise build_error(StatusCode.SESSION_TRAJECTORY_EXECUTION_ERROR, cause=err, reason=str(err)) from err
