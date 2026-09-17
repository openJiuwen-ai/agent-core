# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Expose NativeHarness through the public provider-neutral protocol."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any, AsyncIterator, cast

from openjiuwen.agent_teams.harness.checkpoint import NativeCheckpoint, SavedInput, encode_contexts
from openjiuwen.agent_teams.harness.native_harness import NativeHarness
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.harness.schema.build_context import BuildContext
from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec
from openjiuwen.harness_protocol import (
    AbortMode,
    CheckpointReason,
    ContentBlock,
    DeliveryMode,
    HarnessCheckpoint,
    MessageRole,
    ResumePolicy,
    TurnMessage,
    SendReceipt,
    UnsupportedHarnessCapabilityError,
    json_value_to_builtin,
    HarnessCapability,
    HarnessCard,
    HarnessContext,
    HarnessInput,
    JsonObject,
    HarnessProtocolError,
    HarnessState,
    HarnessStateError,
    HostCapability,
    ProviderEvent,
    TurnError,
    TurnEventKind,
    TurnLifecycleEvent,
)
from openjiuwen.harness_providers.base import PendingTurn, SerializedTurnHarness, logger
from openjiuwen.harness.schema.state import DeepAgentState
from openjiuwen.harness_providers.jsonsafe import to_json_safe
from openjiuwen.harness_providers.factory import load_manifest
from openjiuwen.harness_providers.inputs import harness_input_text
from openjiuwen.harness_providers.native.harness import (
    DeepAgentHarness,
    _ObservationRail,
    _TurnState,
    _append_context_prompt,
)

_CONTROL_CHUNK = "native_protocol.control"


# Output-state fields carried across a checkpoint, in save/restore order.
_OUTPUT_STATE_FIELDS = (
    "answer_parts",
    "reasoning_parts",
    "final_output",
    "result_type",
    "tool_blocks",
    "tool_messages",
)


class NativeHarnessProtocolAdapter(DeepAgentHarness):
    """Adapt NativeHarness controls while reusing DeepAgent output mapping.

    The protocol owns input queuing. Only one accepted input is sent to the
    native runtime at a time, so its follow-up batching cannot merge receipts.
    Native inner rounds, including pause/resume continuations, remain inside
    the same protocol Turn. IDLE ends a turn only after its output has drained.
    """

    card = HarnessCard(
        name="native_v2",
        implementation_version="0.2.0",
        capabilities=frozenset({
            HarnessCapability.STEER,
            HarnessCapability.FORCE_ABORT,
            HarnessCapability.GRACEFUL_ABORT,
            HarnessCapability.PAUSE_RESUME,
            HarnessCapability.CHECKPOINT,
            HarnessCapability.PERSISTENT_SESSION,
        }),
        optional_host_capabilities=frozenset({HostCapability.USER_INPUT, HostCapability.CHECKPOINT_SINK}),
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
        self._cold_restore: NativeCheckpoint | None = None
        self._cold_turn: NativeCheckpoint | None = None
        self._current_output_state: _TurnState | None = None
        self._checkpoint_lock = asyncio.Lock()
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

    def _checkpoint_cwd(self, context: HarnessContext) -> str:
        workspace = self._spec.workspace
        root = context.cwd or self._spec.cwd or (workspace.root_path if workspace else ".")
        return str(Path(root).expanduser().resolve())

    def _validate_context(self, context: HarnessContext) -> None:
        SerializedTurnHarness._validate_context(self, context)
        if context.mcp_servers:
            raise UnsupportedHarnessCapabilityError("declare native MCP servers in the manifest")
        if context.resume_policy is ResumePolicy.REQUIRE_RESUME and context.checkpoint is None:
            raise HarnessProtocolError("NativeHarness requires a checkpoint to resume")
        checkpoint = context.checkpoint
        if checkpoint is not None and context.resume_policy is not ResumePolicy.NEW:
            if checkpoint.schema_version != "1":
                raise HarnessProtocolError("unsupported native checkpoint version")
            if (
                checkpoint.provider != self.card.name
                or checkpoint.agent_id != context.agent_id
                or checkpoint.host_session_id != context.host_session_id
            ):
                raise HarnessProtocolError("native checkpoint scope does not match the context")
            try:
                snapshot = NativeCheckpoint.model_validate(json_value_to_builtin(checkpoint.data))
                snapshot.decode_contexts()
                if snapshot.session_id != f"{context.host_session_id}:{context.agent_id}":
                    raise ValueError("native session id changed")
                if snapshot.paused_input is None and snapshot.queued:
                    raise ValueError("idle snapshot must not contain queued turns")
                self._restore_output_state(_TurnState("validate"), snapshot.output_state)
                DeepAgentState.from_session_dict(snapshot.deepagent)
                if snapshot.paused_input is not None and snapshot.paused_query is None:
                    raise ValueError("paused checkpoint requires its original query")
                ids = [item.turn_id for item in snapshot.queued]
                if snapshot.paused_input is not None:
                    ids.append(snapshot.paused_input.turn_id)
                if len(set(ids)) != len(ids):
                    raise ValueError("duplicate checkpoint turn ids")
                expected_card = self._spec.card
                if expected_card is not None and snapshot.card_id != expected_card.id:
                    raise ValueError("native agent card changed")
                if snapshot.cwd != self._checkpoint_cwd(context):
                    raise ValueError("native working directory changed")
            except (ValueError, TypeError, KeyError) as exc:
                raise HarnessProtocolError("invalid native checkpoint") from exc

    async def _open_session(self, context: HarnessContext) -> str:
        self._dispatched.clear()
        self._dispatched_turn_id = None
        self._paused_observed.clear()
        self._resumed_observed.clear()
        self._cold_restore = None
        self._cold_turn = None
        self._current_output_state = None
        restored = None
        if context.checkpoint is not None and context.resume_policy is not ResumePolicy.NEW:
            data = self._restored_checkpoint_data(context)
            restored = NativeCheckpoint.model_validate(json_value_to_builtin(data))
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
        session.update_state({"context": None, "deepagent": None})
        if restored is not None:
            session.update_state({"context": restored.decode_contexts()})
            agent.save_state(session, DeepAgentState.from_session_dict(restored.deepagent))
            self._latest_checkpoint = context.checkpoint
            if restored.paused_input is not None:
                self._cold_restore = restored
        await agent.subscribe(on_state=self._on_native_state, on_round=self._on_native_round)
        # NativeHarness.start owns _prepare, including manifest loading.
        await agent.start(session=session)
        if restored is not None:
            agent.loop_coordinator.load_state(restored.deepagent.get("stop_condition_state"))
        self._native_outputs = agent.outputs()
        return session_id

    async def _close_session(self) -> None:
        async with self._checkpoint_lock:
            await super()._close_session()

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
        self._current_output_state = state
        try:
            if turn.abort_requested:
                return
            cold = self._cold_turn
            if cold is not None and cold.paused_input.turn_id == turn.turn_id:
                self._cold_turn = None
                self._restore_output_state(state, cold.output_state)
                await self._transition(HarnessState.PAUSED)
                await self._emit(TurnLifecycleEvent(kind=TurnEventKind.PAUSED), turn=turn)
                await agent.resume(query=cold.paused_query)
            else:
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
                    try:
                        await self.export_checkpoint()
                    except Exception:
                        logger.exception("[native-harness] checkpoint capture failed at pause")
                    self._paused_observed.set()
                elif phase == HarnessState.RUNNING.value:
                    was_paused = self.state is HarnessState.PAUSED
                    self._paused_observed.clear()
                    await self._transition(HarnessState.RUNNING)
                    if was_paused:
                        await self._emit(TurnLifecycleEvent(kind=TurnEventKind.RESUMED), turn=turn)
                        self._resumed_observed.set()
                elif phase == HarnessState.IDLE.value:
                    if not self._pending and not turn.stop_requested:
                        try:
                            await self._capture_checkpoint(paused=False)
                        except Exception:
                            logger.exception("[native-harness] checkpoint capture failed at turn end")
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
        async with self._checkpoint_lock:
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

    async def send(self, content: HarnessInput, *, mode: DeliveryMode = DeliveryMode.AUTO) -> SendReceipt:
        if self._cold_restore is not None:
            raise HarnessStateError("resume the restored paused turn before sending new input")
        return await super().send(content, mode=mode)

    async def resume(self, *, query: HarnessInput | None = None) -> None:
        """Resume warm state or a paused Turn restored from a checkpoint."""
        self._require_accepting()
        async with self._command_lock:
            restored = self._cold_restore
            if restored is not None:
                if query is not None and harness_input_text(query) != restored.paused_query:
                    raise HarnessStateError("resume query does not match the checkpoint")
                self._cold_restore = None
                self._cold_turn = restored
                self._pending.extend([restored.paused_input.restore(), *(item.restore() for item in restored.queued)])
                self._supervisor_task = asyncio.create_task(self._supervise_turns(), name="native_protocol_cold_resume")
                self._supervisor_task.add_done_callback(self._clear_supervisor_task)
                return
        if query is not None:
            raise HarnessStateError("cold resume requires a paused checkpoint")
        agent = self.native_harness
        if agent is not None and agent.state is HarnessState.PAUSED:
            async with self._checkpoint_lock:
                await agent.resume()
            await self._resumed_observed.wait()

    async def export_checkpoint(self) -> HarnessCheckpoint | None:
        """Capture a paused/idle boundary; never snapshot executing tools."""
        if self._cold_restore is not None or not self._cycle_started:
            return self._latest_checkpoint
        agent = self.native_harness
        if agent is None:
            return self._latest_checkpoint
        if self.state not in {HarnessState.IDLE, HarnessState.PAUSED} or (
            self.state is HarnessState.IDLE and self._pending
        ):
            raise HarnessStateError("pause the native turn before exporting its checkpoint")
        return await self._capture_checkpoint(paused=self.state is HarnessState.PAUSED)

    async def _capture_checkpoint(self, *, paused: bool) -> HarnessCheckpoint:
        async with self._checkpoint_lock:
            agent = self.native_harness
            session = self._agent_session
            if agent is None or session is None:
                raise HarnessStateError("native session is unavailable")
            if agent.state not in {HarnessState.IDLE, HarnessState.PAUSED}:
                raise HarnessStateError("native session left the checkpoint boundary")
            if any(record.status == "running" for record in agent.async_tool_runtime.registry.values()):
                raise HarnessStateError("wait for native background tools before checkpointing")
            contexts = await agent.react_agent.context_engine.save_contexts(session)
            state = agent.load_state(session)
            deepagent = state.to_session_dict()
            deepagent["stop_condition_state"] = agent.loop_coordinator.get_state()
            if self._current_output_state is not None and self._current_output_state.pending_interrupts:
                raise HarnessStateError("resolve pending user interactions before checkpointing")
            active = self.active_turn if paused else None
            if paused and (active is None or agent.paused_query is None):
                raise HarnessStateError("paused native turn has no resumable query")
            snapshot = NativeCheckpoint(
                session_id=session.get_session_id(), card_id=agent.card.id,
                cwd=self._checkpoint_cwd(self.context),
                contexts=encode_contexts(contexts or {}), deepagent=deepagent,
                paused_input=SavedInput.capture(active) if active is not None else None,
                paused_query=agent.paused_query if paused else None,
                queued=[SavedInput.capture(item) for item in self._pending] if paused else [],
                output_state=self._save_output_state() if paused else {},
            )
            return await self._publish_checkpoint(snapshot.model_dump(mode="json"),
                reason=CheckpointReason.STATE_CHANGED if paused else CheckpointReason.TURN_COMPLETED)

    def _save_output_state(self) -> dict[str, Any]:
        state = self._current_output_state
        if state is None:
            return {}
        return {name: to_json_safe(getattr(state, name)) for name in _OUTPUT_STATE_FIELDS}

    @staticmethod
    def _restore_output_state(state: _TurnState, values: dict[str, Any]) -> None:
        state.answer_parts = list(values.get("answer_parts", []))
        state.reasoning_parts = list(values.get("reasoning_parts", []))
        state.final_output = values.get("final_output")
        state.result_type = values.get("result_type")
        state.tool_blocks = [ContentBlock(**item) for item in values.get("tool_blocks", [])]
        state.tool_messages = [TurnMessage(message_id=item["message_id"], role=MessageRole(item["role"]),
            content=tuple(ContentBlock(**block) for block in item["content"]), data=item["data"])
            for item in values.get("tool_messages", [])]


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


class NativeV2HarnessProvider:
    """Build the NativeHarness adapter through the common provider factory."""

    @property
    def card(self) -> HarnessCard:
        return NativeHarnessProtocolAdapter.card

    @staticmethod
    def create(config: JsonObject) -> NativeHarnessProtocolAdapter:
        values = json_value_to_builtin(config)
        if not isinstance(values, dict):
            raise TypeError("native_v2 configuration must be an object")
        unknown = set(values) - {"deep_agent", "agent_template", "language", "event_buffer_capacity"}
        if unknown:
            raise ValueError(f"unknown native_v2 configuration fields: {', '.join(sorted(unknown))}")
        spec = DeepAgentSpec.model_validate(values.get("deep_agent") or {})
        language = values.get("language")
        if language is not None:
            spec = DeepAgentSpec.model_validate({**spec.model_dump(), "language": language})
        capacity = values.get("event_buffer_capacity", 1024)
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("event_buffer_capacity must be a positive integer")
        template = values.get("agent_template")
        if template is None:
            return NativeHarnessProtocolAdapter(spec, event_buffer_capacity=capacity)
        return create_native_harness_protocol(
            AgentTemplateSpec.model_validate(template),
            agent_spec=spec,
            event_buffer_capacity=capacity,
        )


__all__ = ["NativeHarnessProtocolAdapter", "NativeV2HarnessProvider", "create_native_harness_protocol"]
