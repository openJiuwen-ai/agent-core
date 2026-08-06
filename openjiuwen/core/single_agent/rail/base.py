# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rail & Callback base definitions for Agent lifecycle hooks.

Main classes included:
 - AgentCallbackEvent: lifecycle event types
 - AgentCallbackContext: Unified callback context
 - AgentRail: Class-based rail with tools/skills support
 - rail: Decorator for before/after/on_exception events

Created on: 2025-11-25
"""
from __future__ import annotations

import asyncio
import sys
import time
from abc import ABC
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import (
    Any,
    Union,
    Dict,
    List,
    Optional,
    Callable,
    Awaitable,
    TYPE_CHECKING,
)

from openjiuwen.core.common.logging import logger

from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.agent import Session

if TYPE_CHECKING:
    from openjiuwen.core.single_agent.base import BaseAgent

# Above this, a rail chain is reported at INFO so a slow hook shows up without
# having to enable debug logging. A chain that does real work -- memory
# prefetch, context processing -- routinely runs for a few hundred
# milliseconds, so the bar sits well above that: an INFO line here means
# something is wrong, not that the agent is busy. Everything below still gets a
# DEBUG line for profiling runs.
SLOW_RAIL_CHAIN_SECONDS = 1.0

# Above this, a single rail's init is reported at INFO. Init is one-off setup
# rather than per-round glue, so the bar is lower than the rail-chain one: a
# rail that spends 100ms building tools or loading skills is worth naming even
# on a healthy start-up.
SLOW_RAIL_INIT_SECONDS = 0.1

# Above this, a whole batch of rail inits is reported at INFO. A dozen-plus
# rails each doing modest setup add up without any one of them being at fault,
# so the batch bar sits higher than the per-rail one.
SLOW_RAIL_INIT_BATCH_SECONDS = 0.25


def init_rail(rail_instance: "AgentRail", agent: Any) -> float:
    """Run one rail's ``init`` and report what it cost.

    ``init`` is a plain synchronous call rather than a callback-framework
    hook, and necessarily so: it takes the agent itself (not a context), and
    it is the step that registers the rail's hooks *into* that framework, so
    it has to run before the framework knows the rail exists. That leaves it
    outside the chain timing in :meth:`AgentCallbackContext.fire` even though
    init is where rails do their heaviest work — building tools, loading
    skills, assembling prompt sections. This is the matching half of that
    timing, and the single place every init call site should go through.

    The elapsed time is recorded even when ``init`` raises, so a rail that
    fails slowly is still attributable.

    Args:
        rail_instance: Rail whose ``init`` should run. Named this way rather
            than ``rail`` so it does not shadow the module-level ``@rail``
            decorator.
        agent: Agent handed to ``init``; also the owner the rail registers
            its tools and prompt sections against.

    Returns:
        Elapsed seconds, so a batch caller can assemble a breakdown.
    """
    started_at = time.monotonic()
    try:
        rail_instance.init(agent)
    finally:
        elapsed = time.monotonic() - started_at
        log = logger.info if elapsed >= SLOW_RAIL_INIT_SECONDS else logger.debug
        log(
            "[RailInit] %s finished, elapsed_ms=%.1f",
            type(rail_instance).__name__,
            elapsed * 1000,
        )
    return elapsed


def log_rail_init_breakdown(entries: List[tuple]) -> None:
    """Report what a batch of rail inits cost, slowest rail first.

    A single total across a dozen-plus rails says nothing about which one to
    look at, so the per-rail split is the point of this line.

    Args:
        entries: ``(rail class name, elapsed seconds)`` in initialization
            order; an empty list logs nothing.
    """
    if not entries:
        return
    total = sum(elapsed for _, elapsed in entries)
    ranked = sorted(entries, key=lambda item: item[1], reverse=True)
    breakdown = " ".join("%s=%.1f" % (name, elapsed * 1000) for name, elapsed in ranked)
    log = logger.info if total >= SLOW_RAIL_INIT_BATCH_SECONDS else logger.debug
    log(
        "[RailInit] %d rails initialized, total_ms=%.1f %s",
        len(entries),
        total * 1000,
        breakdown,
    )


class RunKind(Enum):
    """Run kind enumeration for different execution modes."""
    NORMAL = "normal"
    HEARTBEAT = "heartbeat"
    CRON = "cron"
    GOAL = "goal"


class HeartbeatReason(Enum):
    """Heartbeat trigger reason."""
    INTERVAL = "interval"
    MANUAL = "manual"


@dataclass
class RunContext:
    """Structured runtime context for heartbeat."""
    reason: Optional[HeartbeatReason] = None
    session_id: Optional[str] = None
    context_mode: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


# ================================================================
# Typed Event Inputs
# ================================================================
@dataclass
class InvokeInputs:
    """Data for BEFORE/AFTER_INVOKE events.

    Before: query + conversation_id filled.
    After: result also filled.

    Attributes:
        query: User query string
        conversation_id: Optional conversation/session ID
        result: Agent invoke result (filled after invoke)
        run_kind: Run kind (normal or heartbeat)
        run_context: Structured runtime context
        parent_session_id: Optional parent session id for lineage-aware runtimes
    """
    query: Optional[str, InteractiveInput]
    conversation_id: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    run_kind: Optional[RunKind] = None
    run_context: Optional[RunContext] = None
    parent_session_id: Optional[str] = None

    def is_heartbeat(self) -> bool:
        """Check if this is a heartbeat run."""
        return self.run_kind == RunKind.HEARTBEAT

    def is_lightweight_context(self) -> bool:
        """Check if lightweight context mode is enabled."""
        if self.run_context and self.run_context.context_mode:
            return self.run_context.context_mode == "lightweight"
        return False

    def is_cron(self) -> bool:
        """Check if this is a cron run."""
        return self.run_kind == RunKind.CRON


@dataclass
class ModelCallInputs:
    """Input data for BEFORE/AFTER_MODEL_CALL events.

    Attributes:
        messages: Preview message list before the final LLM window is rebuilt
        tools: Optional tool definitions
        model_context: Current ModelContext used to build the final LLM window
        response: LLM response (filled after call)
    """
    messages: List[Any] = field(default_factory=list)
    tools: Optional[List[Any]] = None
    model_context: Optional[ModelContext] = None
    response: Optional[Any] = None


@dataclass
class ToolCallInputs:
    """Input data for BEFORE/AFTER_TOOL_CALL events.

    Attributes:
        tool_call: Raw tool call object
        tool_name: Name of the tool to execute
        tool_args: Arguments for the tool
        tool_result: Tool execution result (filled after call)
        tool_msg: Tool message (filled after call)
    """
    tool_call: Optional[Any] = None
    tool_name: str = ""
    tool_args: Any = None
    tool_result: Optional[Any] = None
    tool_msg: Optional[Any] = None


@dataclass
class TaskIterationInputs:
    """Input data for task-iteration lifecycle events.

    Used by agents that support an outer task loop
    (for example DeepAgent extensions).

    Attributes:
        iteration: 1-based outer-loop iteration index
        loop_event: Event object that triggered this iteration
        conversation_id: Optional conversation/session ID
        result: Iteration result (filled after iteration)
        query: Effective query for this iteration.  Rails may
            modify this field in ``before_task_iteration`` to
            alter the query sent to the inner agent.
        is_follow_up: True when this iteration was triggered by
            a controller follow-up rather than the original user
            query.  ``task_instruction`` templates should not be
            applied to follow-up queries.
        run_kind: Run kind propagated from task metadata
            (e.g. ``RunKind.GOAL``).  Set by the executor so
            that rails like ``TaskCompletionRail`` can identify
            goal rounds without reading task metadata directly.
        run_context: Structured runtime context propagated from
            task metadata.  May be a ``RunContext`` dataclass or
            a plain dict depending on the caller.
    """
    iteration: int
    loop_event: Any
    conversation_id: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    query: Optional[str] = None
    is_follow_up: bool = False
    run_kind: Any = None
    run_context: Any = None


@dataclass
class UserMessageInputs:
    """Input data for the ON_USER_MESSAGE event.

    Fired once per batch of consumed inputs, *before* they are joined into the
    single ``UserMessage`` that enters the conversation. This is the only point
    at which a rail can act on inputs *as inputs*: afterwards they are one
    ordinary history message that may be compacted, summarized or dropped, and
    reaching back to it by position is not safe.

    Attributes:
        parts: The queued inputs, oldest first, as a **mutable** list. Rails
            edit it in place: drop an entry that a later one supersedes, or
            ``insert(0, ...)`` context the model should read first. Whatever
            survives is joined with newlines into the message body, so an entry
            is a whole input — dropping one costs nothing to the rest.
        source: Where the batch came from — ``"query"`` (a new round or a
            follow-up), ``"steering"`` (injected mid-round), or ``"resume"``
            (a workflow interrupt being resumed).
    """
    parts: list[str] = field(default_factory=list)
    source: str = "query"


@dataclass
class RetryRequest:
    """Retry directive produced by on_exception rails."""

    delay_seconds: float = 0.0


@dataclass
class ForceFinishRequest:
    """Signal to terminate the agent loop and return a result immediately."""

    result: Dict[str, Any]


#: Union type for all typed event inputs
EventInputs = Union[
    InvokeInputs,
    ModelCallInputs,
    ToolCallInputs,
    TaskIterationInputs,
    UserMessageInputs,
    Dict[str, Any],
]


# ================================================================
# Agent Callback Event Types
# ================================================================
class AgentCallbackEvent(str, Enum):
    """Agent callback event types for agent lifecycle.

    Lifecycle Callbacks:
        BEFORE_INVOKE: Before agent.invoke() starts
        AFTER_INVOKE: After agent.invoke() completes
        BEFORE_TASK_ITERATION: Before one outer task-loop iteration starts
        AFTER_TASK_ITERATION: After one outer task-loop iteration completes
        AFTER_REACT_ITERATION: After one inner ReAct iteration completes
            (LLM + all tool calls + ToolMessage writes). Only fires on
            fully successful iterations, not on any break path.

    Input Callbacks:
        ON_USER_MESSAGE: Before one consumed input (a new round's query, a
            follow-up, a steering message, or a resumed workflow interrupt)
            is written into the conversation. Rails may rewrite its content;
            see :class:`UserMessageInputs`.

    Model Interaction Callbacks:
        BEFORE_MODEL_CALL: Before LLM is called
        AFTER_MODEL_CALL: After LLM response is received
        ON_MODEL_EXCEPTION: When LLM call raises

    Tool Execution Callbacks:
        BEFORE_TOOL_CALL: Before a tool is executed
        AFTER_TOOL_CALL: After a tool execution completes
        ON_TOOL_EXCEPTION: When tool execution raises
    """
    BEFORE_INVOKE = "before_invoke"
    AFTER_INVOKE = "after_invoke"
    BEFORE_TASK_ITERATION = "before_task_iteration"
    AFTER_TASK_ITERATION = "after_task_iteration"
    AFTER_REACT_ITERATION = "after_react_iteration"
    ON_USER_MESSAGE = "on_user_message"
    BEFORE_MODEL_CALL = "before_model_call"
    AFTER_MODEL_CALL = "after_model_call"
    ON_MODEL_EXCEPTION = "on_model_exception"
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    ON_TOOL_EXCEPTION = "on_tool_exception"


@dataclass
class RetryRecord:
    """Immutable record of a single failed attempt inside the @rail retry loop.

    Collected on ``AgentCallbackContext.retry_history`` so the final
    error message can tell the LLM (and the operator) exactly how many
    times the call was retried, what failed each time, and how long
    the whole sequence took.
    """
    attempt_index: int
    exception_type: str
    exception_message: str
    timestamp: float


# ================================================================
# Agent Callback Context
# ================================================================
@dataclass
class AgentCallbackContext:
    """Unified context object passed to rail/callback hooks.

    Attributes:
        agent: Reference to the BaseAgent instance
        event: Current callback event (set by fire())
        inputs: Current event input data (changes per event)
        config: Runtime configuration
        session: Current Session object
        context: Current ModelContext
        extra: Cross-rail communication dict (persists
            across events within a single invoke)
        exception: Exception object (set on error events)
        retry_attempt: Current failed-attempt index
        retry_history: Chronological list of every failed attempt
            inside the @rail retry loop for this invoke.
    """
    agent: 'BaseAgent'
    event: Optional[AgentCallbackEvent] = None
    inputs: EventInputs = field(default_factory=dict)
    config: Any = None
    session: Optional[Session] = None
    context: Optional[ModelContext] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    exception: Optional[Exception] = None
    retry_attempt: int = 0
    retry_history: List[RetryRecord] = field(default_factory=list)
    invoke_start_time: float = 0.0
    _retry_request: Optional[RetryRequest] = field(
        default=None, init=False, repr=False
    )
    _force_finish_request: Optional[ForceFinishRequest] = field(
        default=None, init=False, repr=False
    )
    _steering_queue: Optional[asyncio.Queue] = field(
        default=None, init=False, repr=False
    )

    async def fire(
        self, event: AgentCallbackEvent
    ) -> None:
        """Trigger all registered callbacks for an event.

        Args:
            event: The event to fire
        """
        self.event = event
        logger.debug("[RailChain] %s started", event)
        started_at = time.monotonic()
        try:
            await self.agent.agent_callback_manager.execute(
                event, self
            )
        finally:
            elapsed = time.monotonic() - started_at
            # A rail chain is expected to be cheap glue around the model call;
            # anything slower is worth surfacing without turning on debug logs.
            if elapsed >= SLOW_RAIL_CHAIN_SECONDS:
                logger.info(
                    "[RailChain] %s finished, elapsed_ms=%.1f",
                    event,
                    elapsed * 1000,
                )
            else:
                logger.debug(
                    "[RailChain] %s finished, elapsed_ms=%.1f",
                    event,
                    elapsed * 1000,
                )

    def request_retry(self, delay_seconds: float = 0.0) -> None:
        """Request the wrapped rail method to retry once more.

        This method is intended to be called inside
        ``on_model_exception`` / ``on_tool_exception`` hooks.

        Args:
            delay_seconds: Sleep duration before next attempt
        """
        if delay_seconds < 0:
            delay_seconds = 0.0
        self._retry_request = RetryRequest(
            delay_seconds=delay_seconds
        )

    def consume_retry_request(self) -> Optional[RetryRequest]:
        """Read and clear pending retry request."""
        request = self._retry_request
        self._retry_request = None
        return request

    def request_force_finish(self, result: Dict[str, Any]) -> None:
        """Request the agent loop to terminate and return *result* immediately.

        Can be called in any hook (e.g. before_model_call, after_tool_call).
        The agent loop checks this signal after every railed operation.
        If called in a ``before`` hook, the decorated method body is skipped.
        """
        self._force_finish_request = ForceFinishRequest(result=result)

    def consume_force_finish(self) -> Optional[ForceFinishRequest]:
        """Read and clear a pending force-finish request."""
        request = self._force_finish_request
        self._force_finish_request = None
        return request

    @property
    def has_force_finish_request(self) -> bool:
        """Check whether a force-finish request is pending."""
        return self._force_finish_request is not None

    # ---- Steering runtime control ----

    def bind_steering_queue(
        self, queue: asyncio.Queue,
    ) -> None:
        """Bind an external steering queue.

        Wires the same ``asyncio.Queue`` that the
        EventHandler writes to, so the inner agent loop
        can drain pending steering messages before each
        model call.

        Args:
            queue: The shared asyncio.Queue instance.
        """
        self._steering_queue = queue

    def push_steering(self, msg: str) -> None:
        """Push a steering message into the queue.

        Safe no-op if no queue is bound.

        Args:
            msg: Steering instruction text.
        """
        if self._steering_queue is not None:
            self._steering_queue.put_nowait(msg)

    def drain_steering(self) -> List[str]:
        """Drain all pending steering messages.

        Returns:
            List of steering message strings,
            empty if no queue bound or queue empty.
        """
        if self._steering_queue is None:
            return []
        msgs: List[str] = []
        while not self._steering_queue.empty():
            try:
                msgs.append(
                    self._steering_queue.get_nowait()
                )
            except asyncio.QueueEmpty:
                break
        return msgs

    def has_pending_steering(self) -> bool:
        """Check whether steering messages are pending.

        Returns:
            True if a queue is bound and non-empty.
        """
        if self._steering_queue is None:
            return False
        return not self._steering_queue.empty()

    @property
    def steering_queue(self) -> Optional[asyncio.Queue]:
        """Return the bound steering queue, or None."""
        return self._steering_queue

    @asynccontextmanager
    async def lifecycle(
        self,
        before: AgentCallbackEvent,
        after: AgentCallbackEvent,
    ):
        """Async context manager for before/after event pairs.

        Fires ``before`` on entry, ``after`` in finally block.
        Automatically saves and restores ``self.inputs`` so
        that inner steps (model_call, tool_call) can freely
        overwrite it without affecting the after event.

        Args:
            before: Event to fire on entry
            after: Event to fire on exit (always)
        """
        saved_inputs = self.inputs
        await self.fire(before)
        exc_to_raise = None
        try:
            yield self
        except Exception as exc:
            exc_to_raise = exc
            self.exception = exc
            raise
        finally:
            self.inputs = saved_inputs
            try:
                await self.fire(after)
            except Exception as callback_exc:
                if exc_to_raise is not None:
                    logger.error(
                        f"{after.value} callback error "
                        f"(masking original "
                        f"{type(exc_to_raise).__name__}): {callback_exc}",
                        exc_info=True
                    )
                else:
                    raise


# ================================================================
# Callback Type Aliases
# ================================================================
AgentCallback = Callable[
    [AgentCallbackContext], Awaitable[None]
]
SyncAgentCallback = Callable[
    [AgentCallbackContext], None
]
AnyAgentCallback = Union[AgentCallback, SyncAgentCallback]


# ================================================================
# Event → Method Name Mapping
# ================================================================
EVENT_METHOD_MAP: Dict[AgentCallbackEvent, str] = {
    AgentCallbackEvent.BEFORE_INVOKE: "before_invoke",
    AgentCallbackEvent.AFTER_INVOKE: "after_invoke",
    AgentCallbackEvent.BEFORE_MODEL_CALL: "before_model_call",
    AgentCallbackEvent.AFTER_MODEL_CALL: "after_model_call",
    AgentCallbackEvent.ON_MODEL_EXCEPTION: "on_model_exception",
    AgentCallbackEvent.BEFORE_TOOL_CALL: "before_tool_call",
    AgentCallbackEvent.AFTER_TOOL_CALL: "after_tool_call",
    AgentCallbackEvent.ON_TOOL_EXCEPTION: "on_tool_exception",
    AgentCallbackEvent.BEFORE_TASK_ITERATION: "before_task_iteration",
    AgentCallbackEvent.AFTER_TASK_ITERATION: "after_task_iteration",
    AgentCallbackEvent.AFTER_REACT_ITERATION: "after_react_iteration",
    AgentCallbackEvent.ON_USER_MESSAGE: "on_user_message",
}


# ================================================================
# AgentRail Base Class
# ================================================================
class AgentRail(ABC):
    """Base class for agent rails.

    Rails provide class-based lifecycle hooks with:
    - State management across callback invocations
    - Tools/skills that are auto-registered on the agent
    - Priority-based ordering, for both ``init`` and callbacks (higher = first)

    Attributes:
        priority: Execution priority (higher runs first). It orders two things
            that are really one question: when this rail's ``init`` runs
            relative to other rails', and when its callbacks run within a hook
            chain. ``init`` is where a rail registers its tools and prompt
            sections, so "my hook runs after that rail's" and "that rail's
            tools exist when I initialize" both follow from one number. Rails
            sharing a priority keep the order they were added in.

    Example::

        class LogRail(AgentRail):
            async def before_model_call(self, ctx):
                print("calling LLM...")

            async def after_model_call(self, ctx):
                print("LLM responded")

        await agent.register_rail(LogRail())
    """

    priority: int = 50

    def init(self, agent):
        pass

    def uninit(self, agent):
        pass

    # -- hook methods (override to activate) --

    async def before_invoke(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called before agent.invoke() starts."""
        pass

    async def after_invoke(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called after agent.invoke() completes."""
        pass

    async def on_user_message(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called before one consumed input is written into the conversation.

        ``ctx.inputs`` is a :class:`UserMessageInputs`; rails may rewrite
        ``ctx.inputs.message.content`` in place.
        """
        pass

    async def before_model_call(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called before LLM is invoked with preview messages and model_context."""
        pass

    async def after_model_call(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called after LLM response is received."""
        pass

    async def on_model_exception(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called when LLM call raises an exception."""
        pass

    async def before_tool_call(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called before a tool is executed."""
        pass

    async def after_tool_call(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called after a tool execution completes."""
        pass

    async def on_tool_exception(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called when tool execution raises."""
        pass

    async def before_task_iteration(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called before each task-loop iteration."""
        pass

    async def after_task_iteration(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called after each task-loop iteration."""
        pass

    def get_callbacks(
        self,
    ) -> Dict[AgentCallbackEvent, AgentCallback]:
        """Extract overridden hook methods.

        Returns:
            Dict mapping event to the bound method,
            only for methods actually overridden by
            the subclass.
        """
        callbacks: Dict[
            AgentCallbackEvent, AgentCallback
        ] = {}
        for event, method_name in EVENT_METHOD_MAP.items():
            method = getattr(self, method_name, None)
            if method and not self._is_base_method(
                method_name
            ):
                callbacks[event] = method
        return callbacks

    def _is_base_method(self, method_name: str) -> bool:
        """Check if method is the base AgentRail no-op."""
        method = getattr(
            self.__class__, method_name, None
        )
        base_method = getattr(
            AgentRail, method_name, None
        )
        return method is base_method


# ================================================================
# @rail Decorator
# ================================================================
def rail(
    before: Optional[AgentCallbackEvent] = None,
    after: Optional[AgentCallbackEvent] = None,
    on_exception: Optional[AgentCallbackEvent] = None,
):
    """Decorator to fire lifecycle events around a method.

    Args:
        before: Event fired before the method body
        after: Event fired in finally (always runs)
        on_exception: Event fired when an exception occurs

    Usage::

        @rail(
            before=AgentCallbackEvent.BEFORE_MODEL_CALL,
            after=AgentCallbackEvent.AFTER_MODEL_CALL,
            on_exception=AgentCallbackEvent.ON_MODEL_EXCEPTION,
        )
        async def _do_model_call(self, ctx):
            ...
    """
    def decorator(fn):
        @wraps(fn)
        async def wrapper(self, ctx, *args, **kwargs):
            ctx.invoke_start_time = time.monotonic()
            attempt = 0
            while True:
                # Drop stale requests from previous attempts.
                ctx.consume_retry_request()
                ctx.retry_attempt = attempt
                ctx.exception = None
                exc_to_raise = None
                will_retry = False
                try:
                    if before:
                        await ctx.fire(before)
                    # If a before hook requested force_finish, skip the method body.
                    if ctx.has_force_finish_request:
                        ff = ctx.consume_force_finish()
                        return ff.result if ff is not None else None
                    return await fn(self, ctx, *args, **kwargs)
                except Exception as e:
                    exc_to_raise = e
                    ctx.exception = e
                    # Record this failed attempt so the final error message
                    # can tell the LLM how many retries happened and why.
                    ctx.retry_history.append(
                        RetryRecord(
                            attempt_index=attempt,
                            exception_type=type(e).__name__,
                            exception_message=str(e),
                            timestamp=time.monotonic(),
                        )
                    )
                    if on_exception:
                        try:
                            await ctx.fire(on_exception)
                        except Exception as callback_exc:
                            logger.error(
                                f"{on_exception.value} callback error "
                                f"(masking original "
                                f"{type(exc_to_raise).__name__}): {callback_exc}",
                                exc_info=True
                            )

                    retry_request = ctx.consume_retry_request()
                    if not retry_request:
                        raise

                    if retry_request.delay_seconds > 0:
                        await asyncio.sleep(
                            retry_request.delay_seconds
                        )
                    exc_to_raise = None
                    will_retry = True
                    attempt += 1
                finally:
                    # 跳过 after 回调当：
                    # 1. 函数被 asyncio.CancelledError 中断时；
                    # 2. tool call 即将进入重试时（避免 on_tool_exception 写入的
                    #    中间态 tool_result 被 after 回调当作最终结果消费）。
                    # model call 重试时不跳过 —— after_model_call 契约要求每次
                    # model 调用后（含失败+即将重试）都触发，CancellationRail
                    # 等回调依赖此做取消检测。
                    # CancelledError 是 BaseException 不是 Exception（Python 3.9+），
                    # 会跳过上面的 except 块直接进入 finally。
                    is_cancelled = isinstance(
                        sys.exc_info()[1] if sys.exc_info()[1] is not None else None,
                        asyncio.CancelledError,
                    )
                    skip_after = will_retry and after is AgentCallbackEvent.AFTER_TOOL_CALL
                    if after and not is_cancelled and not skip_after:
                        try:
                            await ctx.fire(after)
                        except Exception as callback_exc:
                            if exc_to_raise is not None:
                                logger.error(
                                    f"{after.value} callback error "
                                    f"(masking original "
                                    f"{type(exc_to_raise).__name__}): {callback_exc}",
                                    exc_info=True
                                )
                            else:
                                raise
        events = (before, after, on_exception)
        wrapper.rail_events = events
        return wrapper
    return decorator
