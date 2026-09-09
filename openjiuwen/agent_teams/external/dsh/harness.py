# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ExternalHarnessProtocol implementation backed by the DSH Python SDK."""

from __future__ import annotations

import asyncio
import importlib
import json
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any

from openjiuwen.agent_teams.external.dsh.config import DshHarnessConfig
from openjiuwen.agent_teams.external.dsh.mapping import (
    DshTurnAccumulator,
    MappedDshEvent,
    build_queued_stop_result,
)
from openjiuwen.agent_teams.external.dsh.stream import BoundedEventBuffer
from openjiuwen.agent_teams.external.protocol import (
    PROTOCOL_VERSION,
    AbortMode,
    DeliveryMode,
    EventBufferConfig,
    EventOverflowPolicy,
    ExternalHarnessCard,
    ExternalHarnessContext,
    ExternalHarnessError,
    ExternalHarnessInput,
    ExternalHarnessProtocolError,
    ExternalHarnessStateError,
    HarnessCheckpoint,
    HarnessEvent,
    HarnessEventCursor,
    ResumePolicy,
    SendReceipt,
    StateChangedEvent,
    TurnEventKind,
    TurnLifecycleEvent,
    UnsupportedHarnessCapabilityError,
    json_value_to_builtin,
)
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.core.common.logging import team_logger

ADAPTER_VERSION = "0.1.0"


@dataclass(frozen=True, slots=True)
class _PendingTurn:
    content: ExternalHarnessInput
    message_id: str
    turn_id: str
    accepted_mode: DeliveryMode


class DshHarness:
    """Adapt one reusable DeepSeek Harness session to protocol v4.

    Each OpenJiuwen Turn is one serialized DSH activity interval: input
    acceptance through the next whole-agent idle.  DSH's own ``turn`` and
    ``step`` records remain internal/provider observations and do not redefine
    this public Turn boundary.
    """

    card = ExternalHarnessCard(
        name="deepseek-harness",
        implementation_version=ADAPTER_VERSION,
        protocol_version=PROTOCOL_VERSION,
        compatible_protocol_versions=frozenset({PROTOCOL_VERSION}),
    )

    def __init__(self, config: DshHarnessConfig | None = None) -> None:
        self._config = config or DshHarnessConfig()
        self._buffer_config = EventBufferConfig(
            capacity=self._config.event_buffer_capacity,
            overflow=EventOverflowPolicy.BLOCK,
        )
        self._state = HarnessState.TERMINATED
        self._context: ExternalHarnessContext | None = None
        self._session_id: str | None = None
        self._event_buffer: BoundedEventBuffer | None = None
        self._sequence = 0
        self._sdk_harness: Any = None
        self._sdk_session: Any = None
        self._pending: deque[_PendingTurn] = deque()
        self._active_turn: _PendingTurn | None = None
        self._supervisor_task: asyncio.Task[None] | None = None
        self._stop_task: asyncio.Task[None] | None = None
        self._cycle_started = False
        self._stopping = False
        self._command_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()

    @property
    def state(self) -> HarnessState:
        return self._state

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def event_buffer_config(self) -> EventBufferConfig:
        return self._buffer_config

    async def start(self, context: ExternalHarnessContext) -> None:
        """Start a fresh DSH subprocess/session cycle and settle in IDLE."""

        async with self._lifecycle_lock:
            if self._cycle_started:
                raise ExternalHarnessStateError("DeepSeek Harness adapter is already started")
            self._validate_context(context)
            # Resolve the optional dependency and pure SDK options before
            # opening an observable protocol cycle.  A missing SDK must not
            # leave a half-started buffer/session behind.
            sdk = _load_dsh_sdk()
            options = self._sdk_options(context)
            self._context = context
            self._event_buffer = BoundedEventBuffer(self._buffer_config.capacity)
            self._sequence = 0
            self._pending.clear()
            self._active_turn = None
            self._supervisor_task = None
            self._stop_task = None
            self._stopping = False
            self._session_id = f"dsh-{uuid.uuid4().hex}"

            sdk_harness = None
            try:
                sdk_harness = sdk.DeepSeekHarness(**options)
                sdk_session = await asyncio.to_thread(sdk_harness.start_session, self._session_id)
            except Exception:
                await self._rollback_start(sdk_harness)
                # SDK transport errors may include a subprocess stderr tail;
                # do not retain it as an exception cause because context.env
                # and provider credentials are explicitly sensitive.
                raise ExternalHarnessError("failed to start the DeepSeek Harness SDK runtime") from None
            except BaseException:
                # Cancellation and interpreter shutdown must still roll the
                # partially started runtime back before they propagate.
                await self._rollback_start(sdk_harness)
                raise

            self._sdk_harness = sdk_harness
            self._sdk_session = sdk_session
            self._cycle_started = True
            await self._transition(HarnessState.IDLE)

    async def _rollback_start(self, sdk_harness: Any) -> None:
        """Release a partially started runtime and clear its session state.

        Args:
            sdk_harness: The SDK runtime built before the failure, if any.
        """

        if sdk_harness is not None:
            await _close_sdk_quietly(sdk_harness)
        self._event_buffer = None
        self._context = None
        self._session_id = None

    async def stop(self) -> None:
        """Stop the DSH subprocess, terminate accepted Turns, and close events."""

        async with self._lifecycle_lock:
            if not self._cycle_started:
                return
            if self._stop_task is None:
                async with self._command_lock:
                    self._stopping = True
                self._stop_task = asyncio.create_task(self._do_stop(), name="dsh_harness_stop")
            stop_task = self._stop_task
        # One canceled waiter must not cancel the shared teardown and strand
        # the subprocess or leave the event stream open.
        await asyncio.shield(stop_task)

    def events(self) -> HarnessEventCursor:
        """Return the cycle-long single-consumer event stream."""

        buffer = self._event_buffer
        if buffer is None:
            raise ExternalHarnessStateError("DeepSeek Harness adapter has no active or completed event cycle")
        return buffer.cursor()

    def turn_events(self, turn_id: str | None = None) -> HarnessEventCursor:
        """Return the next finite external Turn from the shared event stream."""

        buffer = self._event_buffer
        if buffer is None:
            raise ExternalHarnessStateError("DeepSeek Harness adapter has no active or completed event cycle")
        return buffer.cursor(turn_id=turn_id, per_turn=True)

    async def send(
        self,
        content: ExternalHarnessInput,
        *,
        mode: DeliveryMode = DeliveryMode.AUTO,
    ) -> SendReceipt:
        """Accept input immediately and serialize it onto the DSH session."""

        if mode is DeliveryMode.STEER:
            raise UnsupportedHarnessCapabilityError("the DSH Python SDK does not support steering")
        async with self._command_lock:
            if not self._cycle_started or self._stopping or self._state is HarnessState.TERMINATED:
                raise ExternalHarnessStateError("cannot send to a stopped DeepSeek Harness adapter")
            has_earlier_turn = self._active_turn is not None or bool(self._pending)
            accepted_mode = DeliveryMode.FOLLOW_UP if mode is DeliveryMode.AUTO and has_earlier_turn else mode
            pending = _PendingTurn(
                content=content,
                message_id=f"message-{uuid.uuid4().hex}",
                turn_id=f"turn-{uuid.uuid4().hex}",
                accepted_mode=accepted_mode,
            )
            self._pending.append(pending)
            if self._supervisor_task is None or self._supervisor_task.done():
                supervisor = asyncio.create_task(
                    self._supervise_turns(),
                    name=f"dsh_turn_supervisor[{self._session_id}]",
                )
                self._supervisor_task = supervisor
                supervisor.add_done_callback(self._clear_supervisor_task)
        return SendReceipt(
            message_id=pending.message_id,
            turn_id=pending.turn_id,
            accepted_mode=pending.accepted_mode,
        )

    async def abort(self, *, mode: AbortMode = AbortMode.GRACEFUL) -> None:
        """Reject abort because the current DSH wire protocol has no cancel."""

        _ = mode
        raise UnsupportedHarnessCapabilityError("the DSH Python SDK does not support turn abort")

    async def pause(self) -> None:
        """Reject pause because the current DSH wire protocol has no pause."""

        raise UnsupportedHarnessCapabilityError("the DSH Python SDK does not support pause/resume")

    async def resume(self, *, query: ExternalHarnessInput | None = None) -> None:
        """Reject resume because the current DSH wire protocol has no resume."""

        _ = query
        raise UnsupportedHarnessCapabilityError("the DSH Python SDK does not support pause/resume")

    async def export_checkpoint(self) -> HarnessCheckpoint | None:
        """Return no checkpoint; DSH persistence is not an SDK checkpoint API."""

        return None

    async def _supervise_turns(self) -> None:
        while True:
            async with self._command_lock:
                if self._stopping:
                    queued = tuple(self._pending)
                    self._pending.clear()
                    active = None
                elif not self._pending:
                    return
                else:
                    active = self._pending.popleft()
                    queued = ()
                    self._active_turn = active

            if active is None:
                await self._abort_queued_turns(queued)
                return

            await self._transition(HarnessState.RUNNING)
            await self._emit(
                TurnLifecycleEvent(kind=TurnEventKind.STARTED),
                turn=active,
            )
            terminal_kind, result = await self._execute_turn(active)
            await self._emit(
                TurnLifecycleEvent(kind=terminal_kind, result=result),
                turn=active,
            )

            async with self._command_lock:
                self._active_turn = None
                if self._stopping:
                    queued = tuple(self._pending)
                    self._pending.clear()
                else:
                    queued = ()
                    if not self._pending:
                        # Keep the harness RUNNING across an accepted follow-up
                        # chain.  IDLE is a whole-chain quiescence signal to the
                        # team scheduler, not a gap between serialized Turns.
                        await self._transition(HarnessState.IDLE)
            if queued:
                await self._abort_queued_turns(queued)
                return
            if self._stopping:
                return

    def _clear_supervisor_task(self, task: asyncio.Task[None]) -> None:
        """Drop only the completed task that still owns the supervisor slot."""

        if self._supervisor_task is task:
            self._supervisor_task = None

    async def _execute_turn(self, turn: _PendingTurn) -> tuple[TurnEventKind, Any]:
        started_at = time.time()
        started_monotonic = time.monotonic()
        session = self._sdk_session
        session_id = self._session_id
        if session is None or session_id is None:
            raise ExternalHarnessProtocolError("DSH session disappeared during an active cycle")
        accumulator = DshTurnAccumulator(turn_id=turn.turn_id, root_session_id=session_id)
        loop = asyncio.get_running_loop()

        def on_notification(notification: object) -> None:
            future = asyncio.run_coroutine_threadsafe(
                self._handle_notification(turn, accumulator, notification),
                loop,
            )
            future.result()

        try:
            run_result = await asyncio.to_thread(
                session.run,
                _to_dsh_input(turn.content),
                on_notification=on_notification,
            )
        except Exception as exc:
            if self._stopping:
                return TurnEventKind.ABORTED, accumulator.build_stopped_result(
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                )
            return TurnEventKind.FAILED, accumulator.build_failed_result(
                exc,
                started_at=started_at,
                started_monotonic=started_monotonic,
            )
        return accumulator.build_terminal_result(
            run_result,
            started_at=started_at,
            started_monotonic=started_monotonic,
        )

    async def _handle_notification(
        self,
        turn: _PendingTurn,
        accumulator: DshTurnAccumulator,
        notification: object,
    ) -> None:
        for mapped in accumulator.consume(notification):
            await self._emit(mapped.payload, turn=turn, mapped=mapped)

    async def _abort_queued_turns(self, queued: tuple[_PendingTurn, ...]) -> None:
        for turn in queued:
            await self._emit(TurnLifecycleEvent(kind=TurnEventKind.STARTED), turn=turn)
            await self._emit(
                TurnLifecycleEvent(kind=TurnEventKind.ABORTED, result=build_queued_stop_result()),
                turn=turn,
            )

    async def _do_stop(self) -> None:
        sdk_harness = self._sdk_harness
        if sdk_harness is not None:
            await _close_sdk_quietly(sdk_harness)

        supervisor = self._supervisor_task
        if supervisor is not None and supervisor is not asyncio.current_task():
            try:
                await supervisor
            except Exception as exc:
                # A turn failure is already normalized by the supervisor.  A
                # teardown must still close the observation cycle.
                team_logger.debug("DSH supervisor task failed during stop: {}", exc)

        async with self._command_lock:
            queued = tuple(self._pending)
            self._pending.clear()
            self._active_turn = None
            self._supervisor_task = None
        if queued:
            await self._abort_queued_turns(queued)
        await self._transition(HarnessState.TERMINATED)
        buffer = self._event_buffer
        if buffer is not None:
            await buffer.close()
        self._sdk_harness = None
        self._sdk_session = None
        self._context = None
        self._cycle_started = False

    async def _transition(self, new_state: HarnessState) -> None:
        old_state = self._state
        if old_state is new_state:
            return
        self._state = new_state
        await self._emit(StateChangedEvent(old=old_state, new=new_state))

    async def _emit(
        self,
        payload: Any,
        *,
        turn: _PendingTurn | None = None,
        mapped: MappedDshEvent | None = None,
    ) -> None:
        context = self._context
        buffer = self._event_buffer
        if context is None or buffer is None:
            raise ExternalHarnessProtocolError("cannot emit a DSH event outside an active cycle")
        self._sequence += 1
        await buffer.put(
            HarnessEvent(
                sequence=self._sequence,
                timestamp=time.time(),
                event=payload,
                team_session_id=context.team_session_id,
                member_agent_id=context.member_agent_id,
                session_id=(mapped.session_id if mapped and mapped.session_id else self._session_id),
                turn_id=turn.turn_id if turn else None,
                item_id=mapped.item_id if mapped else None,
                correlation_id=turn.message_id if turn else None,
                causation_ids=(turn.message_id,) if turn else (),
            )
        )

    def _validate_context(self, context: ExternalHarnessContext) -> None:
        self.card.validate_host(
            protocol_version=context.protocol_version,
            capabilities=context.host_capabilities,
        )
        if context.resume_policy is ResumePolicy.REQUIRE_RESUME or context.checkpoint is not None:
            raise UnsupportedHarnessCapabilityError("the DSH Python SDK cannot restore protocol checkpoints")
        if context.mcp_servers:
            raise UnsupportedHarnessCapabilityError(
                "the DSH Python SDK cannot dynamically install ExternalHarnessContext MCP servers"
            )
        if context.system_prompt and self._config.system_prompt_env_var is None:
            raise ExternalHarnessProtocolError(
                "the DSH Python SDK has no native system-prompt parameter; configure system_prompt_env_var "
                "and a Cordis composition that consumes it"
            )

    def _sdk_options(self, context: ExternalHarnessContext) -> dict[str, object]:
        env = dict(self._config.env)
        env.update(context.env)
        if context.system_prompt and self._config.system_prompt_env_var is not None:
            env[self._config.system_prompt_env_var] = context.system_prompt
        values: dict[str, object | None] = {
            "provider": self._config.provider,
            "model": self._config.model,
            "max_tokens": self._config.max_tokens,
            "cwd": context.cwd or self._config.cwd,
            "runtime_cwd": self._config.runtime_cwd,
            "session_root": self._config.session_root,
            "cordis": self._config.cordis,
            "env": env,
            "runtime_bin": self._config.runtime_bin,
            "launch_args_override": self._config.launch_args_override,
            "request_timeout_seconds": self._config.request_timeout_seconds,
            "shutdown_timeout_seconds": self._config.shutdown_timeout_seconds,
            "base_url": self._config.base_url,
            "api_key": self._config.api_key,
        }
        return {key: value for key, value in values.items() if value is not None}


def _load_dsh_sdk() -> Any:
    try:
        return importlib.import_module("deepseek_harness")
    except ImportError as exc:
        raise ExternalHarnessError(
            "deepseek-harness-sdk is required for the DSH adapter; install the optional SDK before start()"
        ) from exc


async def _close_sdk_quietly(sdk_harness: Any) -> None:
    try:
        await asyncio.to_thread(sdk_harness.close)
    except Exception as exc:
        team_logger.debug("DSH SDK close failed during teardown: {}", exc)


def _to_dsh_input(content: ExternalHarnessInput) -> str | list[dict[str, object]]:
    value = json_value_to_builtin(content.content)
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(block, dict) for block in value):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


__all__ = ["ADAPTER_VERSION", "DshHarness"]
