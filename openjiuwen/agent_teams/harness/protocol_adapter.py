# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Expose NativeHarness through the public provider-neutral protocol."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any, AsyncIterator, cast

from openjiuwen.agent_teams.harness.native_harness import NativeHarness
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.harness.schema.build_context import BuildContext
from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec
from openjiuwen.harness_protocol import (
    AbortMode,
    HarnessCapability,
    HarnessCard,
    HarnessContext,
    HarnessInput,
    HarnessProtocolError,
    HarnessState,
    HarnessStateError,
    HostCapability,
    ProviderEvent,
    TurnError,
    TurnEventKind,
    TurnLifecycleEvent,
)
from openjiuwen.harness_providers.base import PendingTurn
from openjiuwen.harness_providers.factory import load_manifest
from openjiuwen.harness_providers.inputs import harness_input_text
from openjiuwen.harness_providers.native.harness import (
    DeepAgentHarness,
    _ObservationRail,
    _TurnState,
    _append_context_prompt,
)

_CONTROL_CHUNK = "native_protocol.control"


class NativeHarnessProtocolAdapter(DeepAgentHarness):
    """Adapt NativeHarness controls while reusing DeepAgent output mapping.

    The protocol owns input queuing. Only one accepted input is sent to the
    native runtime at a time, so its follow-up batching cannot merge receipts.
    Native inner rounds, including pause/resume continuations, remain inside
    the same protocol Turn. IDLE ends a turn only after its output has drained.
    """

    card = HarnessCard(
        name="native-harness",
        implementation_version="0.1.0",
        capabilities=frozenset({
            HarnessCapability.STEER,
            HarnessCapability.FORCE_ABORT,
            HarnessCapability.GRACEFUL_ABORT,
            HarnessCapability.PAUSE_RESUME,
        }),
        optional_host_capabilities=frozenset({HostCapability.USER_INPUT}),
    )

    def __init__(
        self,
        agent_spec: DeepAgentSpec,
        *,
        build_context: BuildContext | None = None,
        event_buffer_capacity: int = 1024,
    ) -> None:
        """Retain the existing NativeHarness construction recipe.

        Args:
            agent_spec: Native spec, including its serialized template snapshot.
            build_context: Runtime dependencies passed to NativeHarness unchanged.
            event_buffer_capacity: Capacity of the protocol observation buffer.
        """
        self._spec = agent_spec.model_copy(deep=True)
        self._build_context = build_context
        self._dispatched = asyncio.Event()
        self._dispatched_turn_id: str | None = None
        self._native_outputs: AsyncIterator[Any] | None = None
        self._paused_observed = asyncio.Event()
        self._resumed_observed = asyncio.Event()
        super().__init__(self._build_native, event_buffer_capacity=event_buffer_capacity)

    def _build_native(self, context: HarnessContext) -> NativeHarness:
        spec = self._spec
        if context.cwd is not None:
            spec = spec.model_copy(update={"cwd": context.cwd})
        return NativeHarness(spec, build_context=self._build_context)

    @property
    def native_harness(self) -> NativeHarness | None:
        """Return the native instance for this start/stop cycle."""
        return cast(NativeHarness | None, self._agent)

    async def _open_session(self, context: HarnessContext) -> str:
        self._dispatched.clear()
        self._dispatched_turn_id = None
        self._paused_observed.clear()
        self._resumed_observed.clear()
        agent = self._build_native(context)
        # Assign resources before awaits so failed starts are rolled back.
        self._agent = agent
        agent.add_rail(_ObservationRail())
        if context.system_prompt:
            _append_context_prompt(agent, context.system_prompt, self._spec.language)
        session_id = f"{context.host_session_id}:{context.agent_id}"
        session = create_agent_session(session_id=session_id, card=agent.card)
        self._agent_session = session
        await session.pre_run(inputs={})
        await agent.subscribe(on_state=self._on_native_state, on_round=self._on_native_round)
        # NativeHarness.start owns _prepare, including manifest loading.
        await agent.start(session=session)
        self._native_outputs = agent.outputs()
        return session_id

    async def _on_native_state(self, *, new: HarnessState) -> None:
        if self.active_turn is not None:
            await self._write_control({"state": new.value})

    async def _on_native_round(self, *, kind: str, result: Any = None) -> None:
        if self.active_turn is not None and kind in {"started", "failed"}:
            await self._write_control({"round": kind, "result": result})

    async def _write_control(self, payload: dict[str, Any]) -> None:
        # Use the same session FIFO as model/tool output. A callback alone can
        # overtake the output forwarder and prematurely terminate a public Turn.
        session = self._agent_session
        if session is not None:
            await session.write_stream(OutputSchema(type=_CONTROL_CHUNK, index=0, payload=payload))

    async def _run_round(self, agent: NativeHarness, turn: PendingTurn, state: _TurnState, query: Any) -> None:
        self._dispatched.clear()
        try:
            if turn.abort_requested:
                return
            await agent.send(query)
            self._dispatched_turn_id = turn.turn_id
            self._dispatched.set()
            outputs = self._native_outputs
            if outputs is None:
                raise HarnessProtocolError("NativeHarness output stream is unavailable")
            async for chunk in outputs:
                if getattr(chunk, "type", None) != _CONTROL_CHUNK:
                    await self._consume_chunk(turn, state, chunk)
                    continue
                payload = chunk.payload
                if payload.get("round") == "started":
                    # Native may retry an internal round before reaching IDLE.
                    state.error = None
                elif payload.get("round") == "failed":
                    state.error = TurnError(message="NativeHarness round failed", category="sdk_error")
                phase = payload.get("state")
                if phase == HarnessState.PAUSING.value:
                    await self._transition(HarnessState.PAUSING)
                elif phase == HarnessState.PAUSED.value:
                    self._resumed_observed.clear()
                    await self._transition(HarnessState.PAUSED)
                    await self._emit(TurnLifecycleEvent(kind=TurnEventKind.PAUSED), turn=turn)
                    self._paused_observed.set()
                elif phase == HarnessState.RUNNING.value:
                    was_paused = self.state is HarnessState.PAUSED
                    self._paused_observed.clear()
                    await self._transition(HarnessState.RUNNING)
                    if was_paused:
                        await self._emit(TurnLifecycleEvent(kind=TurnEventKind.RESUMED), turn=turn)
                        self._resumed_observed.set()
                elif phase == HarnessState.IDLE.value:
                    return
            if not turn.abort_requested:
                raise HarnessProtocolError("NativeHarness output closed before reaching IDLE")
        finally:
            self._dispatched_turn_id = turn.turn_id
            self._dispatched.set()

    async def _emit(
        self,
        payload: Any,
        *,
        turn: PendingTurn | None = None,
        item_id: str | None = None,
        provider_session_id: str | None = None,
    ) -> None:
        if isinstance(payload, ProviderEvent):
            payload = replace(payload, provider=self.card.name)
        await super()._emit(payload, turn=turn, item_id=item_id, provider_session_id=provider_session_id)

    async def _ready_native(self, turn: PendingTurn) -> NativeHarness:
        if self._dispatched_turn_id != turn.turn_id:
            self._dispatched.clear()
            await self._dispatched.wait()
        agent = self.native_harness
        if self.active_turn is not turn or agent is None:
            raise HarnessStateError("the native turn is no longer active")
        return agent

    async def _steer(self, turn: PendingTurn, content: HarnessInput) -> None:
        agent = await self._ready_native(turn)
        if agent.state is not HarnessState.RUNNING:
            raise HarnessStateError("the native turn is not running")
        await agent.send(harness_input_text(content), immediate=True)

    async def _interrupt_turn(self, turn: PendingTurn, mode: AbortMode) -> None:
        agent = await self._ready_native(turn)
        await agent.abort(immediate=mode is AbortMode.FORCE)

    async def pause(self) -> None:
        """Wait for NativeHarness to park at an inner iteration boundary."""
        self._require_accepting()
        turn = self.active_turn
        if turn is None:
            return
        agent = await self._ready_native(turn)
        await agent.pause()
        if agent.state is HarnessState.PAUSED:
            await self._paused_observed.wait()

    async def resume(self, *, query: HarnessInput | None = None) -> None:
        """Continue the paused protocol Turn without adding a user input."""
        self._require_accepting()
        if query is not None:
            raise HarnessStateError("cold resume requires a restored session; protocol checkpoints are unsupported")
        agent = self.native_harness
        if agent is not None and agent.state is HarnessState.PAUSED:
            await agent.resume()
            await self._resumed_observed.wait()


def create_native_harness_protocol(
    manifest: AgentTemplateSpec | str | Path,
    *,
    agent_spec: DeepAgentSpec | None = None,
    build_context: BuildContext | None = None,
    event_buffer_capacity: int = 1024,
) -> NativeHarnessProtocolAdapter:
    """Construct an unstarted adapter using NativeHarness's template loader.

    Args:
        manifest: Existing AgentTemplate or its manifest.json/package path.
        agent_spec: Base native configuration; explicit model/card take priority.
        build_context: Dependencies used by the existing native constructor.
        event_buffer_capacity: Protocol observation capacity.
    """
    template = load_manifest(manifest)
    spec = (agent_spec or DeepAgentSpec()).model_copy(deep=True)
    spec = spec.model_copy(update={
        "card": spec.card or template.agent_card,
        "model": spec.model or template.model,
        "agent_template_spec": template.model_dump(mode="json"),
    })
    return NativeHarnessProtocolAdapter(
        spec,
        build_context=build_context,
        event_buffer_capacity=event_buffer_capacity,
    )


__all__ = ["NativeHarnessProtocolAdapter", "create_native_harness_protocol"]
