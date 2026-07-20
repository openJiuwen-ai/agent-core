# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Session-scoped Agent RAS monitor: detection and recovery orchestration.

Called by :class:`AgentRASRail`. Owns detectors, optional anomaly reporter,
recovery policy/executor, stream suppress state, and automatic stream recovery
(L1/L2 immediate abort; L3 Reviewer second-opinion with fail-open). Atomic side
effects live in ``recovery.operations``; kind→ops mapping in ``recovery.engine``.
Monitor does not import ``robustness_prompt`` directly.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Any, AsyncIterator, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.agent_ras.config import AgentRASConfig
from openjiuwen.harness.agent_ras.agents.base import (
    ASYNC_RECOVERY_TIMEOUT_SECONDS,
    FAULT_DOMAIN_LLM_THINKING_LOOP,
    SKILL_TIMEOUT_SECONDS,
    skill_for,
)
from openjiuwen.harness.agent_ras.detectors.base import (
    AsyncRecoveryHandler,
    is_async_recovery_detector,
)
from openjiuwen.harness.agent_ras.models import (
    AgentRASMonitorEvent,
    Anomaly,
    Signal,
    SignalKind,
)
from openjiuwen.harness.agent_ras.recovery.engine import (
    RecoveryAction,
    RecoveryExecutor,
    RecoveryPolicy,
    StreamRecoveryArgs,
    needs_immediate_apply,
)
from openjiuwen.harness.agent_ras.detectors.skill_verdicts import parse_skill_verdict
from openjiuwen.harness.agent_ras.recovery.operations import (
    apply_recovery_abnormal,
    apply_recovery_normal,
    emit_user_notice,
    suppress_and_buffer,
)
from openjiuwen.harness.agent_ras.recovery.state import PendingRecovery
from openjiuwen.harness.agent_ras.reporter import AnomalyReporter


def _async_recovery_still_in_flight(detector: Any) -> bool:
    checker = getattr(detector, "has_async_recovery_in_flight", None)
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except Exception:
        return False


def _release_async_recovery_detectors(detectors: list[Any]) -> None:
    """Call ``release_async_recovery`` on every async-capable detector."""
    for detector in detectors:
        if not is_async_recovery_detector(detector):
            continue
        try:
            detector.release_async_recovery()
        except Exception:
            logger.warning(
                "Agent RAS release_async_recovery failed detector=%s",
                getattr(detector, "name", type(detector).__name__),
                exc_info=True,
            )


def _wire_async_recovery_handlers(
    detectors: list[Any],
    handler: AsyncRecoveryHandler | None,
) -> None:
    """Set the Monitor completion handler on detectors that expose the setter."""
    for detector in detectors:
        setter = getattr(detector, "set_async_recovery_handler", None)
        if callable(setter):
            setter(handler)


async def _await_async_recovery_detectors(
    detectors: list[Any],
    timeout: float,
) -> bool:
    """Wait for async detectors; return True if work remained after timeout."""
    if timeout <= 0:
        return False
    deadline = time.monotonic() + timeout
    timed_out = False
    for detector in detectors:
        if not is_async_recovery_detector(detector):
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        try:
            await detector.await_async_recovery(remaining)
        except Exception:
            logger.warning(
                "Agent RAS await_async_recovery failed detector=%s",
                getattr(detector, "name", type(detector).__name__),
                exc_info=True,
            )
        if _async_recovery_still_in_flight(detector):
            timed_out = True
            break

    if timed_out or any(
        _async_recovery_still_in_flight(detector)
        for detector in detectors
        if is_async_recovery_detector(detector)
    ):
        logger.warning(
            "[async_recovery] timed out waiting for detectors; releasing in-flight state",
        )
        _release_async_recovery_detectors(detectors)
        return True
    return False


class RingBuffer:
    """In-process anomaly event history (newest-first via ``recent``).

    Used for metrics/debug only — not part of suppress/recovery state.
    """

    def __init__(self, capacity: int = 10000) -> None:
        if capacity <= 0:
            raise ValueError(f"capacity must be > 0, got {capacity}")
        self._capacity = capacity
        self._events: deque[Any] = deque(maxlen=capacity)
        self._lock = asyncio.Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    def size(self) -> int:
        """Return the number of buffered events."""
        return len(self._events)

    async def append(self, event: Any) -> None:
        """Append one event (drops oldest when full)."""
        async with self._lock:
            self._events.append(event)

    async def recent(self, n: int) -> list[Any]:
        """Return up to ``n`` newest events (newest first)."""
        if n <= 0:
            return []
        async with self._lock:
            count = min(n, len(self._events))
            if count == 0:
                return []
            return list(reversed(list(self._events)[-count:]))

    def clear(self) -> None:
        """Drop all buffered events."""
        self._events.clear()


class AgentRASMonitor:
    """Per-session detection + recovery orchestrator.

    Typical rail path: ``handle`` = ``detection`` then immediate ``recovery``.
    Stream path: ``on_stream_chunk`` may suppress output and arm automatic
    recovery (L1/L2 abort immediately; L3 starts a Reviewer background task).
    """

    _EVENT_STREAM_END: Any = object()

    def __init__(
        self,
        detectors: list[Any],
        reporter: AnomalyReporter | None,
        policy: RecoveryPolicy,
        agent_id: str = "agent",
        session_id: str = "session",
        ring_buffer_capacity: int = 10000,
        *,
        agents: Any = None,
        config: AgentRASConfig | None = None,
        executor: RecoveryExecutor | None = None,
        member_name: str | None = None,
    ) -> None:
        self._detectors = list(detectors)
        self._reporter = reporter
        self._policy = policy
        self._agent_id = agent_id
        self._session_id = session_id
        self._agents = agents
        self._config = config or AgentRASConfig()
        self._executor = executor
        self._member = member_name or agent_id
        self._ring = RingBuffer(capacity=ring_buffer_capacity)
        self._started = False
        self._stopped = False
        self._invoke_count = 0
        self._anomaly_count = 0
        self._recovery_count = 0
        self._detect_durations_ms: deque[float] = deque(maxlen=2048)
        self._subscribers: set[asyncio.Queue] = set()
        self._active_ctx: AgentCallbackContext | None = None
        self._review_task: asyncio.Task | None = None
        self._abnormal_committed = False
        self._notice_emitted_immediate: str | None = None
        self._language = "cn"

    @property
    def started(self) -> bool:
        return self._started

    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def agents(self) -> Any:
        return self._agents

    @property
    def config(self) -> AgentRASConfig:
        return self._config

    @property
    def member_name(self) -> str:
        return self._member

    @property
    def executor(self) -> RecoveryExecutor | None:
        return self._executor

    @property
    def policy(self) -> RecoveryPolicy:
        return self._policy

    @property
    def detectors(self) -> tuple[Any, ...]:
        return tuple(self._detectors)

    def bind_ctx(self, ctx: AgentCallbackContext | None) -> None:
        """Bind the active callback context used by stream/async recovery."""
        self._active_ctx = ctx
        self.bind_reporter_ctx(ctx)

    def bind_reporter_ctx(self, ctx: Any) -> None:
        """Forward ctx to reporters that support ``bind_ctx``."""
        if self._reporter is None:
            return
        binder = getattr(self._reporter, "bind_ctx", None)
        if callable(binder):
            binder(ctx)

    def set_language(self, language: str) -> None:
        """Sync recovery/steering locale from DeepAgent language (cn/en)."""
        from openjiuwen.harness.agent_ras.config import coerce_message_locale

        self._language = coerce_message_locale(language)
        if self._executor is not None:
            self._executor.set_locale(self._language)

    def _locale(self) -> str:
        return self._language

    def ops_for(self, anomaly: Anomaly) -> set[RecoveryAction]:
        """Resolve the atomic recovery op set for ``anomaly`` via policy."""
        try:
            return self._policy.ops_for(anomaly)
        except Exception:
            logger.error(
                "Agent RAS recovery planning failed member=%s session=%s kind=%s",
                self._member,
                self._session_id,
                anomaly.kind,
                exc_info=True,
            )
            return set()

    async def start(self, ctx: AgentCallbackContext | None = None) -> None:
        """Invoke start: reset executor/detectors and wire async handlers."""
        await self._cancel_review_task()
        self._abnormal_committed = False
        self._notice_emitted_immediate = None
        if ctx is not None:
            self.bind_ctx(ctx)
        if self._executor is not None:
            self._executor.reset()
        self.reset_detectors()
        self.wire_async_recovery()
        if self._started:
            return
        self._started = True
        self._stopped = False

    async def stop(self) -> None:
        """Invoke end: await async recovery, close detectors, end event streams."""
        await self._cancel_review_task()
        await self.await_async_recovery()
        if self._stopped:
            self.bind_ctx(None)
            return
        self._stopped = True
        self._started = False
        for detector in self._detectors:
            close = getattr(detector, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception as exc:
                    logger.warning(
                        "agent_ras detector close failed agent=%s session=%s "
                        "detector=%s error_type=%s",
                        self._agent_id,
                        self._session_id,
                        getattr(detector, "name", type(detector).__name__),
                        type(exc).__name__,
                        exc_info=True,
                    )
        for queue in tuple(self._subscribers):
            queue.put_nowait(self._EVENT_STREAM_END)
        # Clear per-round detector state (e.g. RepeatToolDetector._locks)
        # so stop() does not leak session-scoped resources (CR-004).
        self.reset_detectors()
        self.bind_ctx(None)

    async def detection(self, signal: Signal) -> list[Anomaly]:
        """Fan-out ``signal`` to all detectors; report/record each anomaly."""
        self._invoke_count += 1
        detect_started = time.perf_counter()
        produced: list[Anomaly] = []
        for detector in self._detectors:
            try:
                anomaly = await self._call_detector(detector, signal)
            except Exception as exc:
                logger.error(
                    "agent_ras detector failed agent=%s session=%s detector=%s "
                    "error_type=%s",
                    self._agent_id,
                    self._session_id,
                    getattr(detector, "name", repr(detector)),
                    type(exc).__name__,
                    exc_info=True,
                )
                continue
            if anomaly is None:
                continue
            produced.append(anomaly)
            await self._route(anomaly)
            self._anomaly_count += 1
            await self._record_event(anomaly)
        self._detect_durations_ms.append(
            (time.perf_counter() - detect_started) * 1000.0,
        )
        return produced

    async def recovery(
        self,
        ctx: AgentCallbackContext | None,
        anomalies: list[Anomaly],
        *,
        phase: str = "immediate",
        chunk: Any = None,
        chunk_type: str = "",
        chunk_text: str = "",
    ) -> None:
        """Apply policy ops for each anomaly (immediate side effects or stream phase)."""
        if not anomalies or self._executor is None:
            return
        target_ctx = ctx if ctx is not None else self._active_ctx
        for anomaly in anomalies:
            actions = self.ops_for(anomaly)
            try:
                if phase == "stream":
                    await self._executor.run_stream_recovery(
                        target_ctx,
                        anomaly,
                        StreamRecoveryArgs(
                            chunk_result=chunk,
                            chunk_type=chunk_type,
                            chunk_text=chunk_text,
                        ),
                        actions,
                    )
                    self.record_recovery()
                elif needs_immediate_apply(actions):
                    if target_ctx is None:
                        continue
                    await self._executor.apply(target_ctx, anomaly, actions)
                    self.record_recovery()
            except Exception:
                logger.error(
                    "Agent RAS recovery failed member=%s session=%s kind=%s phase=%s",
                    self._member,
                    self._session_id,
                    anomaly.kind,
                    phase,
                    exc_info=True,
                )

    async def handle(
        self,
        signal: Signal,
        ctx: AgentCallbackContext | None = None,
    ) -> list[Anomaly]:
        """Run ``detection`` then immediate-phase ``recovery`` (main rail entry)."""
        if ctx is not None:
            self.bind_ctx(ctx)
        anomalies = await self.detection(signal)
        await self.recovery(ctx, anomalies, phase="immediate")
        return anomalies

    async def on_stream_chunk(
        self,
        chunk_type: str,
        chunk_text: str,
        chunk: Any,
        ctx: AgentCallbackContext,
    ) -> None:
        """Stream-phase path: buffer if suppressing, else detect and auto-recover.

        L1/L2 (``text_repetition``) abort immediately. L3 (``plan_execution``)
        keeps suppressing while a Reviewer skill runs; confirmed abnormal then
        aborts, otherwise fail-open flushes the buffer.
        """
        self.bind_ctx(ctx)
        if self._executor is None:
            return
        if self._executor.should_suppress_stream(chunk_type):
            suppress_and_buffer(
                self._executor.suppress_state,
                chunk_type,
                chunk_text,
                chunk,
            )
            return
        anomalies = await self.detection(
            Signal(
                kind=SignalKind.STREAM_CHUNK,
                member_name=self._member,
                chunk_type=chunk_type,
                chunk_text=chunk_text,
            )
        )
        await self.recovery(
            ctx,
            anomalies,
            phase="stream",
            chunk=chunk,
            chunk_type=chunk_type,
            chunk_text=chunk_text,
        )
        await self._dispatch_automatic_recovery(ctx)

    async def complete_async_stream_recovery(
        self,
        anomaly: Anomaly,
        chunk_type: str,
    ) -> None:
        """Callback for async detectors (e.g. L3 thinking-loop) that finish after observe."""
        try:
            await self.record_anomaly(anomaly)
            if self._executor is not None:
                await self._executor.run_stream_recovery(
                    self._active_ctx,
                    anomaly,
                    StreamRecoveryArgs(
                        chunk_result=None,
                        chunk_type=chunk_type,
                        chunk_text="",
                    ),
                    self.ops_for(anomaly),
                )
                self.record_recovery()
            if self._active_ctx is not None:
                await self._dispatch_automatic_recovery(self._active_ctx)
        finally:
            self._release_async_recovery_for(anomaly.detector)

    def _release_async_recovery_for(self, detector_name: str) -> None:
        for detector in self._detectors:
            if getattr(detector, "name", None) != detector_name:
                continue
            release = getattr(detector, "release_async_recovery", None)
            if callable(release):
                release()
            return

    def wire_async_recovery(self) -> None:
        """Install ``complete_async_stream_recovery`` on async-capable detectors."""
        _wire_async_recovery_handlers(
            self._detectors,
            self.complete_async_stream_recovery,
        )

    async def await_async_recovery(self) -> None:
        """Block until async detectors finish or the configured timeout elapses."""
        timeout = float(ASYNC_RECOVERY_TIMEOUT_SECONDS)
        try:
            timed_out = await _await_async_recovery_detectors(
                self._detectors,
                timeout,
            )
            if timed_out:
                logger.warning(
                    "Agent RAS async recovery timed out member=%s session=%s timeout=%.1f",
                    self._member,
                    self._session_id,
                    timeout,
                )
        except Exception:
            logger.warning(
                "Agent RAS async recovery wait failed member=%s session=%s",
                self._member,
                self._session_id,
                exc_info=True,
            )

    async def _cancel_review_task(self) -> None:
        task = self._review_task
        self._review_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("Agent RAS review task cancel wait failed", exc_info=True)

    async def _dispatch_automatic_recovery(self, ctx: AgentCallbackContext) -> None:
        """Route armed suppress pending to L1/L2 abort or L3 Reviewer."""
        if self._executor is None or self._abnormal_committed:
            return
        suppress = self._executor.suppress_state
        pending = suppress.pending
        if pending is None or suppress.resolved is not None:
            return
        if suppress.review_awaiting or (
            self._review_task is not None and not self._review_task.done()
        ):
            return
        if pending.is_plan_execution:
            self._start_l3_review(ctx, pending)
            return
        await self._apply_abnormal_recovery(ctx, pending)

    def _start_l3_review(
        self,
        ctx: AgentCallbackContext,
        pending: PendingRecovery,
    ) -> None:
        """Kick off one background Reviewer skill without aborting the stream."""
        if self._executor is None:
            return
        suppress = self._executor.suppress_state
        suppress.review_awaiting = True
        self._review_task = asyncio.create_task(
            self._run_l3_review(ctx, pending),
            name=f"agent_ras_l3_review:{self._session_id}",
        )

    async def _run_l3_review(
        self,
        ctx: AgentCallbackContext,
        pending: PendingRecovery,
    ) -> None:
        try:
            await self._run_l3_review_body(ctx, pending)
        finally:
            if self._review_task is asyncio.current_task():
                self._review_task = None
            if self._executor is not None:
                self._executor.suppress_state.review_awaiting = False

    async def _run_l3_review_body(
        self,
        ctx: AgentCallbackContext,
        pending: PendingRecovery,
    ) -> None:
        """Reviewer confirm → abnormal; timeout/error/invalid/normal → fail-open."""
        if self._executor is None or self._abnormal_committed:
            return
        suppress = self._executor.suppress_state
        if suppress.pending is None or suppress.resolved is not None:
            return
        confirmed_abnormal = False
        try:
            confirmed_abnormal = await self._invoke_l3_recovery(pending)
        except Exception:
            logger.warning(
                "Agent RAS L3 recovery failed member=%s session=%s",
                self._member,
                self._session_id,
                exc_info=True,
            )
            confirmed_abnormal = False
        if self._abnormal_committed:
            return
        # Pending may have been cleared by finalize_stream_recovery fail-open.
        if suppress.pending is None or suppress.resolved is not None:
            return
        if confirmed_abnormal:
            await self._apply_abnormal_recovery(ctx, pending)
            return
        await self._apply_normal_recovery(ctx, pending, release_plan_execution=True)

    @staticmethod
    def _fault_domain_for_pending(pending: PendingRecovery) -> str:
        """Resolve fault domain for recovery skill lookup."""
        domain = str(pending.extra.get("fault_domain") or "").strip()
        if domain:
            return domain
        if pending.is_plan_execution or pending.is_text_repetition:
            return FAULT_DOMAIN_LLM_THINKING_LOOP
        return FAULT_DOMAIN_LLM_THINKING_LOOP

    async def _invoke_l3_recovery(self, pending: PendingRecovery) -> bool:
        """Return True only when recovery skill explicitly confirms abnormal."""
        agents = self._agents
        if agents is None:
            logger.warning(
                "Agent RAS L3 recovery fail-open member=%s session=%s "
                "reason=no_agents",
                self._member,
                self._session_id,
            )
            return False
        invoke = getattr(agents, "invoke_skill", None)
        if not callable(invoke):
            logger.warning(
                "Agent RAS L3 recovery fail-open member=%s session=%s "
                "reason=no_invoke_skill",
                self._member,
                self._session_id,
            )
            return False
        payload = json.dumps(
            {
                "first_verdict": {
                    "abnormal": True,
                    "primary_fault": pending.extra.get("primary_fault")
                    or "semantic_deadlock",
                    "confidence": pending.extra.get("skill_confidence"),
                    "rationale": pending.extra.get("skill_rationale") or "",
                },
                "thinking_excerpt": (
                    pending.thinking_excerpt or pending.scanned_text or ""
                ),
            },
            ensure_ascii=False,
        )
        fault_domain = self._fault_domain_for_pending(pending)
        try:
            skill_name = skill_for(fault_domain, "recovery")
        except ValueError:
            logger.warning(
                "Agent RAS L3 recovery fail-open member=%s session=%s "
                "fault_domain=%s reason=unknown_domain_or_role",
                self._member,
                self._session_id,
                fault_domain,
                exc_info=True,
            )
            return False
        result = await invoke(
            role="recovery",
            skill_name=skill_name,
            payload=payload,
            timeout=float(SKILL_TIMEOUT_SECONDS),
        )
        if not isinstance(result, dict) or not result:
            logger.warning(
                "Agent RAS L3 recovery fail-open member=%s session=%s "
                "skill=%s reason=empty_or_non_dict",
                self._member,
                self._session_id,
                skill_name,
            )
            return False
        # Re-parse so illegal / incomplete recovery payloads stay fail-open even
        # when a test double returns a raw dict (adapter already parses).
        verdict = parse_skill_verdict(skill_name, result)
        if verdict.fail_open_reason:
            logger.warning(
                "Agent RAS L3 recovery fail-open member=%s session=%s "
                "skill=%s reason=%s",
                self._member,
                self._session_id,
                skill_name,
                verdict.fail_open_reason,
            )
            return False
        if not verdict.abnormal:
            logger.info(
                "Agent RAS L3 recovery normal member=%s session=%s skill=%s",
                self._member,
                self._session_id,
                skill_name,
            )
            return False
        return True

    async def _apply_abnormal_recovery(
        self,
        ctx: AgentCallbackContext,
        pending: PendingRecovery,
    ) -> None:
        if self._executor is None or self._abnormal_committed:
            return
        # Prefer the invoke-bound ctx so abort / steering / notice stay aligned
        # (ZZP-001). Cancel before commit must leave pending intact for
        # finalize_stream_recovery fail-open (ZZP-004).
        ctx = self._active_ctx or ctx
        suppress = self._executor.suppress_state
        try:
            request_abort = getattr(ctx, "request_abort_stream", None)
            if callable(request_abort):
                request_abort()
            notice = await apply_recovery_abnormal(
                ctx, pending, locale=self._locale(),
            )
            # Emit immediately so the user sees the interrupt reason even if the
            # next model call does not run; also schedule for before_model_call
            # with dedup via consume_notice_for_emit.
            await emit_user_notice(ctx, notice)
            self._notice_emitted_immediate = notice
            self._executor.schedule_recovery_notice(notice)
            primary_fault = pending.extra.get("primary_fault") or ""
            logger.warning(
                "Agent RAS thinking-loop abort member=%s session=%s source=%s "
                "profile=%s primary_fault=%s notice=%s",
                self._member,
                self._session_id,
                pending.source,
                pending.recovery_profile,
                primary_fault,
                notice,
            )
            suppress.clear()
            suppress.mark_resolved_abnormal()
            self._abnormal_committed = True
        except asyncio.CancelledError:
            logger.warning(
                "Agent RAS abnormal recovery cancelled before commit "
                "member=%s session=%s source=%s committed=%s pending=%s",
                self._member,
                self._session_id,
                pending.source,
                self._abnormal_committed,
                suppress.pending is not None,
            )
            raise

    async def _apply_normal_recovery(
        self,
        ctx: AgentCallbackContext,
        pending: PendingRecovery,
        *,
        release_plan_execution: bool = False,
    ) -> None:
        if self._executor is None or self._abnormal_committed:
            return
        suppress = self._executor.suppress_state
        await apply_recovery_normal(
            ctx, suppress, pending, locale=self._locale(),
        )
        if release_plan_execution:
            self._release_plan_execution_latches()
        # Drop any raced abnormal notice; normal path continues the stream.
        self._executor.take_pending_recovery_notice()

    def _release_plan_execution_latches(self) -> None:
        """Allow L3 re-detection only after another full semantic_eval_chars window."""
        for detector in self._detectors:
            release = getattr(detector, "release_plan_execution_latch", None)
            if not callable(release):
                continue
            try:
                release()
            except Exception:
                logger.debug(
                    "Agent RAS release_plan_execution_latch failed detector=%s",
                    getattr(detector, "name", type(detector).__name__),
                    exc_info=True,
                )

    def prepare_for_next_model_call(self) -> None:
        """Reopen recovery gates after an abnormal turn, before the next model call.

        Abnormal recovery sets ``_abnormal_committed`` for the aborted stream.
        The same invoke may continue via steering into another model call; without
        clearing that gate, a new detect would never re-run automatic recovery.
        Does **not** consume pending recovery notice (caller emits it first).
        """
        if not self._abnormal_committed:
            return
        self._abnormal_committed = False
        if self._executor is not None:
            state = self._executor.suppress_state
            if state.resolved == "abnormal":
                state.resolved = None
            state.review_awaiting = False

    async def finalize_stream_recovery(self, ctx: AgentCallbackContext) -> None:
        """After model call: await/cancel Reviewer and fail-open if still pending.

        Preferred path resolves mid-stream. When the stream ends while Reviewer
        is still running (or pending was never resolved), treat as **normal**:
        flush buffers and release the L3 latch. Already-committed abnormal paths
        are left untouched.
        """
        if self._abnormal_committed or self._executor is None:
            return

        suppress = self._executor.suppress_state
        if suppress.resolved == "abnormal":
            self._abnormal_committed = True
            return
        if suppress.resolved == "normal":
            return

        task = self._review_task
        fail_open_reason = "still_pending"
        if task is not None and not task.done():
            timeout = float(SKILL_TIMEOUT_SECONDS)
            try:
                await asyncio.wait_for(task, timeout=timeout)
            except asyncio.TimeoutError:
                fail_open_reason = "timeout"
                logger.warning(
                    "Agent RAS L3 recovery wait timed out member=%s session=%s",
                    self._member,
                    self._session_id,
                )
                await self._cancel_review_task()
            except asyncio.CancelledError:
                fail_open_reason = "cancelled"
                await self._cancel_review_task()
            except Exception:
                fail_open_reason = "wait_failed"
                logger.warning(
                    "Agent RAS L3 recovery wait failed member=%s session=%s",
                    self._member,
                    self._session_id,
                    exc_info=True,
                )
                await self._cancel_review_task()

        # After cancel/timeout: if abnormal recovery was interrupted mid-flight,
        # pending may still be set and committed is False — fall through to
        # fail-open below (ZZP-004).
        if self._abnormal_committed or suppress.resolved is not None:
            return

        pending = suppress.pending
        if pending is None:
            return

        logger.info(
            "Agent RAS stream recovery fail-open member=%s session=%s "
            "source=%s reason=%s",
            self._member,
            self._session_id,
            pending.source,
            fail_open_reason,
        )
        await self._apply_normal_recovery(
            ctx,
            pending,
            release_plan_execution=pending.is_plan_execution,
        )

    def take_notice(self) -> str | None:
        """Pop a deferred user-visible recovery notice (emitted before next model call)."""
        if self._executor is None:
            return None
        return self._executor.take_pending_recovery_notice()

    def consume_notice_for_emit(self) -> str | None:
        """Pop deferred notice, skipping duplicate of an already-emitted immediate notice."""
        notice = self.take_notice()
        if notice is None:
            return None
        if notice == self._notice_emitted_immediate:
            self._notice_emitted_immediate = None
            return None
        self._notice_emitted_immediate = None
        return notice

    async def _call_detector(self, detector: Any, signal: Signal) -> Optional[Anomaly]:
        """Run one detector's ``observe``; await if it returns a coroutine."""
        result = detector.observe(signal)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    async def _route(self, anomaly: Anomaly) -> None:
        """Optionally forward the anomaly to the configured reporter."""
        if self._reporter is None:
            return
        actions = self._policy.actions_for(anomaly.severity, kind=anomaly.kind)
        report_actions = {
            RecoveryAction.REPORT_TO_USER,
            RecoveryAction.ESCALATE_USER,
        }
        invoke_reporter = bool(set(actions) & report_actions)
        if getattr(self._reporter, "handles_all_anomalies", False):
            invoke_reporter = True
        if not invoke_reporter:
            return
        try:
            result = self._reporter.report(anomaly)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.error(
                "agent_ras reporter failed agent=%s session=%s kind=%s error_type=%s",
                self._agent_id,
                self._session_id,
                anomaly.kind,
                type(exc).__name__,
                exc_info=True,
            )

    async def record_anomaly(self, anomaly: Anomaly) -> None:
        """Record an anomaly produced outside ``detection`` (async stream path)."""
        await self._route(anomaly)
        self._anomaly_count += 1
        await self._record_event(anomaly)

    def record_recovery(self) -> None:
        """Bump the recovery counter after a successful apply/stream recovery."""
        self._recovery_count += 1

    async def _record_event(self, anomaly: Anomaly) -> None:
        event = AgentRASMonitorEvent(
            event_type="anomaly",
            timestamp=_now_ms(),
            agent_id=self._agent_id,
            session_id=self._session_id,
            anomaly=anomaly,
        )
        await self._ring.append(event)
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    def reset_detectors(self) -> None:
        """Reset every detector's per-round state."""
        for detector in self._detectors:
            try:
                detector.reset()
            except Exception as exc:
                logger.warning(
                    "agent_ras detector reset failed agent=%s session=%s "
                    "detector=%s error_type=%s",
                    self._agent_id,
                    self._session_id,
                    getattr(detector, "name", repr(detector)),
                    type(exc).__name__,
                    exc_info=True,
                )

    def reset(self) -> None:
        """Alias for ``reset_detectors`` (historical API)."""
        self.reset_detectors()

    async def get_recent_events(self, n: int) -> list[AgentRASMonitorEvent]:
        """Return the most recent ``n`` anomaly events (newest first)."""
        return await self._ring.recent(n)

    async def get_metrics(self) -> dict[str, Any]:
        """Return basic observability counters for this session."""
        ordered = sorted(self._detect_durations_ms)
        p99_index = max(0, int(len(ordered) * 0.99) - 1)
        return {
            "invoke_count": self._invoke_count,
            "anomaly_count": self._anomaly_count,
            "recovery_count": self._recovery_count,
            "detect_p99_ms": ordered[p99_index] if ordered else 0.0,
            "buffer_size": self._ring.size(),
        }

    async def events(self) -> AsyncIterator[AgentRASMonitorEvent]:
        """Live async iterator of anomaly events until ``stop`` sends a sentinel."""
        if self._stopped:
            return
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        try:
            recent = await self._ring.recent(256)
            for event in reversed(recent):
                queue.put_nowait(event)
            while True:
                event = await queue.get()
                if event is self._EVENT_STREAM_END:
                    return
                yield event
        finally:
            self._subscribers.discard(queue)


def _now_ms() -> int:
    """Current wall time in milliseconds (Unix epoch)."""
    return int(time.time() * 1000)
