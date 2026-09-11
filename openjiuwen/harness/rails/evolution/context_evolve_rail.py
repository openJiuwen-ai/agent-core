# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ContextEvolveRail: algorithm-agnostic online driver for context evolution.

Template for the context-evolve dimension (first algorithm: Metis):

- read side: retrieve per task query via an injected ``ContextRetriever`` and
  inject the rendered ``content`` as a prompt section;
- write side: on the evolution trigger, build one signal and run the standard
  online contract (``SingleDimUpdater`` -> ``execute_updates`` -> wrap
  ``ContextEvolveRecord`` -> ``ContextStore.commit``).

Algorithm hooks: ``build_signal_context`` (mandatory) maps snapshot facts to
signal context fields; ``bind_config`` (optional) defaults to the dimension's
``scope_states`` key. One rail instance is the single writer for its
``(algorithm_id, scope_id)``; duplicate registration fails fast.
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from abc import abstractmethod
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Dict, List, Optional, Sequence

from openjiuwen.agent_evolving.optimizer.context_evolve_call.base import ContextEvolveOptimizerBase
from openjiuwen.agent_evolving.optimizer.context_evolve_call.contracts import (
    SCOPE_STATES_CONFIG_KEY,
    ContextEvolveRecord,
    ContextRetrievalResult,
    ContextRetriever,
    ContextStore,
)
from openjiuwen.agent_evolving.protocols import TASK_COMPLETED_SIGNAL
from openjiuwen.agent_evolving.signal.base import EvolutionSignal
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.update_execution import execute_updates
from openjiuwen.agent_evolving.updater import SingleDimUpdater
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.operator.context_evolve_call.base import ContextEvolveOperator
from openjiuwen.core.single_agent.rail import AgentCallbackContext
from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.rails.evolution.evolution_rail import (
    EvolutionRail,
    EvolutionTriggerPoint,
    PreparedEvolutionInput,
)

OutcomeResolver = Callable[[Optional[AgentCallbackContext], Optional[Trajectory]], str]

# Task-loop and common host statuses normalized to the dimension's outcome
# vocabulary. The task loop's regular success fallback is {"status": "completed"}
# (task_loop_event_handler.py), which must count as Success for codify evidence.
_SUCCESS_STATUSES = frozenset({"success", "succeeded", "completed", "ok", "resolved", "done"})
_FAILURE_STATUSES = frozenset({"failure", "failed", "error", "cancelled", "canceled", "timeout", "aborted"})


@dataclass(frozen=True)
class _ContextEvolvePreparedInput(PreparedEvolutionInput):
    """Detached task facts captured before background evolution starts."""

    facts: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _ContextEvolveInvokeState:
    """Read-side state isolated to one invoke context."""

    query: str = ""
    retrieval_key: Optional[tuple[str, str]] = None
    retrieval: Optional[ContextRetrievalResult] = None


def default_outcome_resolver(ctx: Optional[AgentCallbackContext], trajectory: Optional[Trajectory]) -> str:
    """Normalize framework result shapes into a Metis task outcome.

    The trajectory argument is part of the resolver contract for custom
    implementations; this default does not inspect it — main-branch
    trajectories expose no stable terminal-status field.
    """
    result = getattr(getattr(ctx, "inputs", None), "result", None)
    if not isinstance(result, dict):
        return "Unknown"

    status = result.get("status") or result.get("outcome")
    normalized_status = str(status).strip().lower() if status else ""
    result_type = str(result.get("result_type") or "").strip().lower()
    success = result.get("success")

    if normalized_status in _FAILURE_STATUSES or result_type == "error" or result.get("error") is not None:
        outcome = "Failure"
    elif normalized_status in _SUCCESS_STATUSES or result_type == "answer":
        outcome = "Success"
    elif isinstance(success, bool):
        outcome = "Success" if success else "Failure"
    elif status:
        outcome = str(status)
    else:
        outcome = "Unknown"
    return outcome


class ContextEvolveRail(EvolutionRail):
    """Online read/write template over injected retriever/store/optimizer/operator."""

    # Single-writer registry: one live rail per (algorithm_id, scope_id) per process.
    _WRITER_REGISTRY: ClassVar[Dict[tuple[str, str], "ContextEvolveRail"]] = {}

    def __init__(  # pylint: disable=too-many-arguments,too-many-locals
        self,
        *,
        retriever: ContextRetriever,
        store: ContextStore,
        optimizer: ContextEvolveOptimizerBase,
        operator: ContextEvolveOperator,
        scope_id: str,
        targets: Optional[Sequence[str]] = None,
        section_name: str,
        section_priority: int,
        signal_type: str = TASK_COMPLETED_SIGNAL,
        outcome_resolver: Optional[OutcomeResolver] = None,
        inject_context: bool = True,
        auto_evolve: bool = True,
        trajectory_span_processor: TrajectorySpanProcessor,
        evolution_trigger: EvolutionTriggerPoint = EvolutionTriggerPoint.AFTER_INVOKE,
        async_evolution: bool = True,
        max_concurrent_evolution: int = 1,
    ) -> None:
        """Initialize an algorithm-agnostic context-evolution driver.

        Args:
            retriever: Read-side service that selects injectable context.
            store: Authoritative state loader and commit boundary.
            optimizer: Optimizer that converts task evidence into updates.
            operator: Preview operator validating generated updates.
            scope_id: Stable identity of the state scope being evolved.
            targets: Operator targets to bind. Defaults to all tunables.
            section_name: Name of the injected system-prompt section.
            section_priority: Ordering priority of that prompt section.
            signal_type: Evolution signal emitted for a finished task.
            outcome_resolver: Optional task-result normalization callback.
            inject_context: Whether retrieval results enter the prompt.
            auto_evolve: Whether finished tasks trigger evolution.
            trajectory_span_processor: Shared trajectory capture processor.
            evolution_trigger: Lifecycle point that starts evolution.
            async_evolution: Whether evolution runs in the background.
            max_concurrent_evolution: Maximum concurrent evolution runs.

        Raises:
            BaseError: If operator, signal, target, or writer wiring is invalid.

        """
        super().__init__(
            evolution_trigger=evolution_trigger,
            async_evolution=async_evolution,
            max_concurrent_evolution=max_concurrent_evolution,
            trajectory_span_processor=trajectory_span_processor,
        )
        # Fail fast on wiring mistakes: once evolution runs in a background
        # task, a mismatch here would only ever surface as silent skips.
        if operator.scope_id != scope_id:
            raise build_error(
                StatusCode.TOOLCHAIN_AGENT_PARAM_ERROR,
                error_msg=f"operator scope_id {operator.scope_id!r} does not match rail scope_id {scope_id!r}",
            )
        if signal_type not in optimizer.supported_signal_types:
            raise build_error(
                StatusCode.TOOLCHAIN_AGENT_PARAM_ERROR,
                error_msg=(
                    f"signal_type {signal_type!r} not in optimizer.supported_signal_types "
                    f"{optimizer.supported_signal_types}"
                ),
            )
        tunables = set(operator.get_tunables())
        resolved_targets = list(targets) if targets is not None else sorted(tunables)
        unsupported = [t for t in resolved_targets if t not in tunables]
        if not resolved_targets or unsupported:
            raise build_error(
                StatusCode.TOOLCHAIN_AGENT_PARAM_ERROR,
                error_msg=(
                    f"targets {resolved_targets} not supported by operator tunables {sorted(tunables)}"
                    f" (unsupported: {unsupported})"
                ),
            )
        self._retriever = retriever
        self._store = store
        self._optimizer = optimizer
        self._operator = operator
        self._targets = resolved_targets
        self._scope_id = scope_id
        self._section_name = section_name
        self._section_priority = section_priority
        self._signal_type = signal_type
        self._outcome_resolver = outcome_resolver or default_outcome_resolver
        self._inject_context = inject_context
        self._auto_evolve = auto_evolve
        self._updater = SingleDimUpdater(optimizer)
        self._system_prompt_builder: Any = None

        # Mutable state lives inside the ContextVar value so a background task
        # spawned for this invoke can invalidate the same retrieval snapshot.
        self._context_evolve_state: ContextVar[Optional[_ContextEvolveInvokeState]] = ContextVar(
            f"{type(self).__name__}.context_evolve_state",
            default=None,
        )

        self._close_scheduled = False
        self._writer_key = (optimizer.algorithm_id, scope_id)
        existing = self._WRITER_REGISTRY.get(self._writer_key)
        if existing is not None:
            raise build_error(
                StatusCode.TOOLCHAIN_AGENT_PARAM_ERROR,
                error_msg=(
                    f"duplicate context-evolve writer for {self._writer_key}: "
                    "one rail instance per (algorithm_id, scope_id); close() the previous rail first"
                ),
            )
        self._WRITER_REGISTRY[self._writer_key] = self

    # ---- lifecycle -----------------------------------------------------

    def init(self, agent: Any) -> None:
        """Bind the DeepAgent prompt builder used by bridged model callbacks."""
        super().init(agent)
        self._system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    async def _on_before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Start a fresh read-side state for this invoke context."""
        await super()._on_before_invoke(ctx)
        self._context_evolve_state.set(_ContextEvolveInvokeState())

    def close(self) -> None:
        """Release this rail's single-writer slot.

        With async evolution a background run may still be committing when the
        host unregisters the rail (``uninit`` is called synchronously, without
        ``cleanup_background_tasks``). Releasing the slot immediately would let
        a replacement rail become a concurrent second writer, so with pending
        tasks the slot is released only after they reach a terminal state.
        Tasks are not cancelled — a commit may already be partially applied.
        Safe to call repeatedly; the drain is scheduled once.
        """
        if self._close_scheduled:
            return
        pending = [t for t in self._bg_tasks if not t.done()]
        if not pending:
            self._release_writer_slot()
            return
        self._close_scheduled = True
        from openjiuwen.core.common.background_tasks import start_background_task

        # Deliberately NOT added to _bg_tasks: the drain waits on that set's
        # tasks and must never wait on itself.
        start_background_task(
            self._drain_then_release(pending),
            name=f"context-evolve-close-{self._writer_key[0]}-{self._writer_key[1]}",
            group="evolution",
        )

    async def _drain_then_release(self, pending: List[Any]) -> None:
        for task in pending:
            try:
                await task.wait()
            except asyncio.CancelledError:
                if not task.done():
                    # The drain itself is being cancelled (host shutdown); the
                    # old task has no terminal state yet — propagate and keep
                    # the slot held rather than risk a second writer.
                    raise
                # The awaited task was cancelled: that IS a terminal state.
                logger.debug("[%s] drained cancelled background task", type(self).__name__)
            except Exception as exc:
                # Terminal state is all we need; the task's own error handling
                # already reported the failure.
                logger.debug("[%s] drained background task with error: %s", type(self).__name__, exc)
        self._release_writer_slot()

    def _release_writer_slot(self) -> None:
        if self._WRITER_REGISTRY.get(self._writer_key) is self:
            del self._WRITER_REGISTRY[self._writer_key]

    def uninit(self, agent: Any) -> None:
        """Remove injected prompt state and release lifecycle resources."""
        if self._system_prompt_builder is not None:
            self._system_prompt_builder.remove_section(self._section_name)
            self._system_prompt_builder = None
        super().uninit(agent)
        self._context_evolve_state.set(None)
        self.close()

    @property
    def store(self) -> ContextStore:
        """The state boundary owned by this rail."""
        return self._store

    @property
    def scope_id(self) -> str:
        """Return the state scope owned by this rail."""
        return self._scope_id

    @property
    def last_retrieval(self) -> Optional[ContextRetrievalResult]:
        """Return the current invoke's most recent retrieval result."""
        return self._invoke_state().retrieval

    # ---- algorithm hooks -------------------------------------------------

    @abstractmethod
    def build_signal_context(self, facts: Dict[str, Any]) -> Dict[str, Any]:
        """Map snapshot facts (query/outcome/evolution_context) to signal fields.

        The hook owns the algorithm fields entirely; the base adds only
        task_id and scope_id. Never merge evolution_context wholesale — the
        signal may be logged and persisted with the trajectory.
        """
        raise NotImplementedError

    def bind_config(self, state: Any) -> Dict[str, Any]:
        """Extra bind kwargs; default carries the dimension's scope_states key."""
        return {SCOPE_STATES_CONFIG_KEY: {self._scope_id: state}}

    # ---- read side -------------------------------------------------------

    def _invoke_state(self) -> _ContextEvolveInvokeState:
        """Return the current invoke state, creating one for direct hook calls."""
        state = self._context_evolve_state.get()
        if state is None:
            state = _ContextEvolveInvokeState()
            self._context_evolve_state.set(state)
        return state

    async def before_task_iteration(self, ctx: AgentCallbackContext) -> None:
        """Capture the task query and retrieve context for it (cached per scope+query)."""
        state = self._invoke_state()
        query = str(getattr(ctx.inputs, "query", None) or "")
        if not query:
            # Reset stale per-task state: an empty-query round must neither
            # inject nor evolve the previous task.
            state.query = ""
            self._invalidate_retrieval_cache(state)
            return
        state.query = query
        if not self._inject_context:
            return
        key = (self._scope_id, query)
        if state.retrieval_key == key and state.retrieval is not None:
            return
        try:
            state.retrieval = await self._retriever.retrieve(self._scope_id, query)
            state.retrieval_key = key
        except Exception as exc:
            logger.warning("[%s] retrieval failed for scope=%s: %s", type(self).__name__, self._scope_id, exc)
            state.retrieval = None
            state.retrieval_key = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject the retrieved content as a prompt section."""
        builder = getattr(getattr(ctx, "inputs", None), "system_prompt_builder", None)
        if builder is None:
            builder = getattr(getattr(ctx, "agent", None), "system_prompt_builder", None)
        if builder is None:
            builder = self._system_prompt_builder
        if builder is None:
            return
        builder.remove_section(self._section_name)
        retrieval = self._invoke_state().retrieval
        if not self._inject_context or retrieval is None:
            return
        content = retrieval.content
        if not content:
            return
        builder.add_section(
            PromptSection(
                name=self._section_name,
                content={"cn": content, "en": content},
                priority=self._section_priority,
            )
        )

    def _invalidate_retrieval_cache(self, state: Optional[_ContextEvolveInvokeState] = None) -> None:
        """Force re-retrieval next task so injections see the evolved library."""
        state = state or self._invoke_state()
        state.retrieval_key = None
        state.retrieval = None

    # ---- write side --------------------------------------------------------

    @staticmethod
    def _next_task_id(trajectory: Optional[Trajectory]) -> str:
        """Execution-unique task id from canonical trajectory identity."""
        session_id = getattr(trajectory, "session_id", None) if trajectory is not None else None
        trajectory_id = getattr(trajectory, "trajectory_id", None) if trajectory is not None else None
        return f"{session_id or trajectory_id or 'task'}-{uuid.uuid4().hex}"

    def _build_facts(self, ctx: Optional[AgentCallbackContext], trajectory: Optional[Trajectory]) -> Dict[str, Any]:
        """Deep-copied task facts, stable for the lifetime of one snapshot."""
        state = self._invoke_state()
        retrieval = state.retrieval
        return {
            "task_id": self._next_task_id(trajectory),
            "query": state.query,
            "outcome": str(self._outcome_resolver(ctx, trajectory) or "Unknown"),
            "evolution_context": copy.deepcopy(retrieval.evolution_context) if retrieval else {},
        }

    async def _prepare_evolution_input(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
    ) -> Optional[_ContextEvolvePreparedInput]:
        """Capture algorithm facts while callback state is still alive."""
        if not self._auto_evolve:
            return None
        prepared = await super()._prepare_evolution_input(trajectory, ctx)
        if prepared is None:
            return None
        return _ContextEvolvePreparedInput(
            trajectory=prepared.trajectory,
            messages=prepared.messages,
            skill_name=prepared.skill_name,
            facts=self._build_facts(ctx, trajectory),
        )

    async def run_evolution(
        self,
        prepared: _ContextEvolvePreparedInput,
    ) -> None:
        """One evolve pass over the finished task; reads snapshot facts only."""
        facts = prepared.facts
        query = str(facts.get("query") or "")
        if not query:
            logger.info("[%s] no task query captured; skipping evolution", type(self).__name__)
            return

        task_id = str(facts.get("task_id") or self._next_task_id(prepared.trajectory))
        signal = EvolutionSignal(
            signal_type=self._signal_type,
            section="",
            excerpt=query,
            context={
                "task_id": task_id,
                "scope_id": self._scope_id,
                **self.build_signal_context(facts),
            },
        )

        state = await self._store.load_state(self._scope_id)
        operators: Dict[str, Any] = {self._operator.operator_id: self._operator}
        bound = self._updater.bind(
            operators=operators,
            targets=list(self._targets),
            **self.bind_config(state),
        )
        if not bound:
            logger.warning("[%s] no operator bound; skipping evolution", type(self).__name__)
            return

        trajectories: List[Trajectory] = [prepared.trajectory]
        updates = await self._updater.process(trajectories, [signal], {})
        results = execute_updates(operators, updates)
        committed = 0
        for result in results:
            if not (result.ok and result.records):
                continue
            record = ContextEvolveRecord(
                scope_id=self._scope_id,
                algorithm=self._optimizer.algorithm_id,
                payload=result.records[0],
                # Pass algorithm/operator metadata through opaquely; the
                # framework-generated task_id wins over a same-named input.
                metadata={**dict(result.metadata), "task_id": task_id},
                entry_type=result.change_type,
            )
            await self._store.commit(record)
            committed += 1
            # Invalidate immediately after each successful commit: if a later
            # record's commit raises, the store has already changed and the
            # cache must not survive. Failed or zero-delta runs keep the cache.
            self._invalidate_retrieval_cache()
        logger.info(
            "[%s] evolution done for scope=%s: %d record(s) committed",
            type(self).__name__,
            self._scope_id,
            committed,
        )


__all__ = ["ContextEvolveRail", "default_outcome_resolver"]
