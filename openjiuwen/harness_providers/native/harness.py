# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""HarnessProtocol implementation over the in-process DeepAgent interaction loop."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Awaitable, Callable, Mapping

from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.session.stream.base import OutputSchema
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail, ToolCallInputs
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.build_context import BuildContext
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec
from openjiuwen.harness.schema.interaction import InputDispatchMode, SendInputRequest
from openjiuwen.harness_protocol import (
    PROTOCOL_VERSION,
    AbortMode,
    ContentBlock,
    HarnessCapability,
    HarnessCard,
    HarnessContext,
    HarnessInput,
    HarnessProtocolError,
    HostCapability,
    InteractionResponseStatus,
    ItemEventKind,
    ItemLifecycleEvent,
    MessageRole,
    OutputChannel,
    OutputEvent,
    OutputKind,
    OutputOperation,
    ProviderEvent,
    ResumePolicy,
    TurnError,
    TurnEventKind,
    TurnMessage,
    TurnResult,
    TurnStatus,
    UnsupportedHarnessCapabilityError,
    UserInputRequest,
    freeze_json_object,
    freeze_json_value,
    json_value_to_builtin,
)
from openjiuwen.harness_providers.base import (
    PendingTurn,
    ProviderStartupError,
    SerializedTurnHarness,
    TurnTiming,
    interrupted_result,
    logger,
)
from openjiuwen.harness_providers.inputs import harness_input_text
from openjiuwen.harness_providers.jsonsafe import to_json_object, to_json_safe

ADAPTER_VERSION = "0.1.0"
PROVIDER_NAME = "deepagent"
INTERACTIVE_INPUT_METADATA_KIND = "interactive_input"
_CONTEXT_PROMPT_SECTION = "harness_context"
_TOOL_CALL_CHUNK = "tool_call"
_TOOL_RESULT_CHUNK = "tool_result"

AgentFactory = Callable[[HarnessContext], "DeepAgent | Awaitable[DeepAgent]"]


class _ObservationRail(AgentRail):
    """Mirror tool execution onto the session stream as ``tool_call`` chunks.

    DeepAgent streams model text but not tool activity; this rail adds the
    tool lifecycle so the harness can emit ``ItemLifecycleEvent`` for it.
    """

    priority = 5

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        session = ctx.session
        inputs = ctx.inputs
        if session is None or not isinstance(inputs, ToolCallInputs):
            return
        await session.write_stream(
            OutputSchema(
                type=_TOOL_CALL_CHUNK,
                index=0,
                payload={
                    "tool_call_id": _tool_call_id(inputs),
                    "tool_name": inputs.tool_name,
                    "tool_args": _parse_args(inputs.tool_args),
                },
            )
        )

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        session = ctx.session
        inputs = ctx.inputs
        if session is None or not isinstance(inputs, ToolCallInputs):
            return
        await session.write_stream(
            OutputSchema(
                type=_TOOL_RESULT_CHUNK,
                index=0,
                payload={
                    "tool_call_id": _tool_call_id(inputs),
                    "tool_name": inputs.tool_name,
                    "tool_result": to_json_safe(inputs.tool_result),
                },
            )
        )


class _TurnState:
    """Chunk accumulation for one external turn."""

    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id
        self.answer_parts: list[str] = []
        self.reasoning_parts: list[str] = []
        self.final_output: str | None = None
        self.result_type: str | None = None
        self.error: TurnError | None = None
        self.pending_interrupts: dict[str, Any] = {}
        self.tool_blocks: list[ContentBlock] = []
        self.tool_messages: list[TurnMessage] = []
        self.emitted_output = False

    def messages(self) -> tuple[TurnMessage, ...]:
        blocks: list[ContentBlock] = []
        if self.reasoning_parts:
            blocks.append(
                ContentBlock(
                    block_id=f"{self.turn_id}:reasoning",
                    kind="reasoning",
                    content="".join(self.reasoning_parts),
                )
            )
        if self.answer_parts:
            blocks.append(
                ContentBlock(
                    block_id=f"{self.turn_id}:text",
                    kind="text",
                    content="".join(self.answer_parts),
                )
            )
        blocks.extend(self.tool_blocks)
        assistant = TurnMessage(
            message_id=f"{self.turn_id}:assistant",
            role=MessageRole.ASSISTANT,
            content=tuple(blocks),
        )
        return (assistant, *self.tool_messages)


class DeepAgentHarness(SerializedTurnHarness):
    """Adapt one DeepAgent's session-scoped interaction loop to protocol v1.

    One external Turn is one attached output stream: the input is dispatched
    through ``send_input`` and the turn ends when DeepAgent finishes the
    stream (its own task-loop continuations stay inside the turn).  An
    ``ask_user`` interrupt keeps the turn open and is resolved through the
    host ``UserInputRequest`` interaction before the round resumes.
    """

    card = HarnessCard(
        name=PROVIDER_NAME,
        implementation_version=ADAPTER_VERSION,
        protocol_version=PROTOCOL_VERSION,
        compatible_protocol_versions=frozenset({PROTOCOL_VERSION}),
        capabilities=frozenset({HarnessCapability.STEER, HarnessCapability.FORCE_ABORT}),
        optional_host_capabilities=frozenset({HostCapability.USER_INPUT}),
    )

    def __init__(
        self,
        agent_factory: AgentFactory,
        *,
        agent_template: AgentTemplateSpec | None = None,
        session_id: str | None = None,
        language: str | None = None,
        event_buffer_capacity: int = 1024,
    ) -> None:
        """Bind the agent construction recipe; the agent is built on ``start``.

        Args:
            agent_factory: Builds the (unstarted) ``DeepAgent`` for a context.
            agent_template: Optional manifest template hot-loaded onto the agent
                before the first turn (prompt sections, tools, rails, skills,
                sub-agents).
            session_id: Explicit DeepAgent session id; defaults to the host
                session id joined with the agent id.
            language: Language used when resolving the template.
            event_buffer_capacity: Bounded observation buffer capacity.
        """
        super().__init__(event_buffer_capacity=event_buffer_capacity)
        self._agent_factory = agent_factory
        self._agent_template = agent_template
        self._session_id_override = session_id
        self._language = language
        self._agent: DeepAgent | None = None
        self._agent_session: Any = None

    @property
    def agent(self) -> DeepAgent | None:
        """Return the live DeepAgent of the active cycle."""
        return self._agent

    # ------------------------------------------------------------------
    # Provider hooks
    # ------------------------------------------------------------------

    def _validate_context(self, context: HarnessContext) -> None:
        super()._validate_context(context)
        if context.resume_policy is ResumePolicy.REQUIRE_RESUME or context.checkpoint is not None:
            raise UnsupportedHarnessCapabilityError("the DeepAgent harness does not restore protocol checkpoints")
        if context.mcp_servers:
            raise UnsupportedHarnessCapabilityError(
                "declare MCP servers in the agent manifest; the DeepAgent harness does not mount context MCP servers"
            )

    async def _open_session(self, context: HarnessContext) -> str | None:
        from openjiuwen.core.session.agent import create_agent_session

        try:
            agent = self._agent_factory(context)
            if asyncio.iscoroutine(agent) or isinstance(agent, Awaitable):
                agent = await agent
            if not isinstance(agent, DeepAgent):
                raise TypeError("agent_factory must return a DeepAgent")
            agent.add_rail(_ObservationRail())
            if context.system_prompt:
                _append_context_prompt(agent, context.system_prompt, self._language)
            # The interaction loop never initializes the agent on its own: the
            # pending rails (including the observation rail) must be registered
            # and the cwd ContextVar seeded here, before the supervisor and the
            # task scheduler tasks are spawned and inherit this context.
            await agent.ensure_initialized()
            session_id = self._session_id_override or f"{context.host_session_id}:{context.agent_id}"
            session = create_agent_session(session_id=session_id, card=agent.card)
            await session.pre_run(inputs={})
            await agent.start(session=session)
            if self._agent_template is not None:
                build_context = BuildContext(
                    language=self._language or "cn",
                    workspace=agent.deep_config.workspace if agent.deep_config is not None else None,
                    member_card_id=agent.card.id,
                )
                await agent.load_agent_template_spec(self._agent_template, context=build_context)
        except Exception as exc:
            raise ProviderStartupError(
                f"DeepAgent harness startup failed: {type(exc).__name__}",
                error=TurnError(message=str(exc) or type(exc).__name__, code=type(exc).__name__, category="sdk_error"),
            ) from exc
        self._agent = agent
        self._agent_session = session
        return session_id

    async def _close_session(self) -> None:
        agent = self._agent
        session = self._agent_session
        self._agent = None
        self._agent_session = None
        if agent is not None:
            try:
                await agent.stop()
            except Exception:
                logger.exception("[deepagent] stop failed during teardown")
        if session is not None:
            try:
                await session.post_run()
            except Exception:
                logger.exception("[deepagent] session post_run failed during teardown")

    async def _execute_turn(self, turn: PendingTurn) -> tuple[TurnEventKind, TurnResult]:
        timing = TurnTiming()
        state = _TurnState(turn.turn_id)
        agent = self._agent
        if agent is None:
            raise HarnessProtocolError("DeepAgent disappeared during an active cycle")
        query: Any = _turn_query(turn.content)
        try:
            while True:
                await self._run_round(agent, turn, state, query)
                if turn.abort_requested or not state.pending_interrupts or state.error is not None:
                    break
                resume_input = await self._resolve_interrupts(turn, state)
                if resume_input is None:
                    break
                state.pending_interrupts.clear()
                query = resume_input
        except Exception as exc:
            if not turn.abort_requested:
                logger.exception("[deepagent] turn %s failed", turn.turn_id)
                state.error = TurnError(
                    message=f"DeepAgent turn failed: {exc}",
                    code=type(exc).__name__,
                    category="sdk_error",
                )
        return self._build_result(turn, state, timing)

    async def _run_round(self, agent: DeepAgent, turn: PendingTurn, state: _TurnState, query: Any) -> None:
        stream = await agent.attach_output()
        if stream is None:
            raise HarnessProtocolError("DeepAgent output stream already has a consumer")
        try:
            await agent.send_input(SendInputRequest(request_id=turn.turn_id, inputs={"query": query}))
            async for chunk in stream:
                await self._consume_chunk(turn, state, chunk)
        finally:
            await stream.close(abort_active_round=turn.abort_requested)

    async def _consume_chunk(self, turn: PendingTurn, state: _TurnState, chunk: Any) -> None:
        chunk_type = getattr(chunk, "type", None)
        payload = getattr(chunk, "payload", None)
        if isinstance(chunk, Mapping):
            chunk_type = chunk.get("type", chunk_type)
            payload = chunk.get("payload", payload)
        if chunk_type == "llm_output":
            text = _payload_text(payload)
            if text:
                state.answer_parts.append(text)
                state.emitted_output = True
                await self._emit(_delta(turn.turn_id, OutputChannel.ANSWER, text), turn=turn)
            return
        if chunk_type == "llm_reasoning":
            text = _payload_text(payload)
            if text:
                state.reasoning_parts.append(text)
                await self._emit(_delta(turn.turn_id, OutputChannel.REASONING, text), turn=turn)
            return
        if chunk_type == _TOOL_CALL_CHUNK and isinstance(payload, Mapping):
            call_id = str(payload.get("tool_call_id") or uuid.uuid4().hex)
            name = str(payload.get("tool_name") or "unknown")
            arguments = to_json_safe(payload.get("tool_args"))
            state.tool_blocks.append(
                ContentBlock(
                    block_id=f"{turn.turn_id}:tool:{call_id}",
                    kind="tool_call",
                    content={"name": name, "arguments": arguments},
                    data={"call_id": call_id},
                )
            )
            await self._emit(
                ItemLifecycleEvent(
                    kind=ItemEventKind.STARTED,
                    item_type="tool",
                    data=freeze_json_object({"name": name, "arguments": arguments}),
                ),
                turn=turn,
                item_id=call_id,
            )
            return
        if chunk_type == _TOOL_RESULT_CHUNK and isinstance(payload, Mapping):
            call_id = str(payload.get("tool_call_id") or uuid.uuid4().hex)
            name = str(payload.get("tool_name") or "unknown")
            result = to_json_safe(payload.get("tool_result"))
            state.tool_messages.append(
                TurnMessage(
                    message_id=f"{turn.turn_id}:tool-result:{call_id}",
                    role=MessageRole.TOOL,
                    content=(
                        ContentBlock(
                            block_id=f"{turn.turn_id}:tool-result:{call_id}",
                            kind="tool_result",
                            content=freeze_json_value(result),
                            data={"call_id": call_id},
                        ),
                    ),
                )
            )
            await self._emit(
                ItemLifecycleEvent(
                    kind=ItemEventKind.COMPLETED,
                    item_type="tool",
                    data=freeze_json_object({"tool_name": name, "result": result}),
                ),
                turn=turn,
                item_id=call_id,
            )
            return
        if chunk_type == "answer" and isinstance(payload, Mapping):
            output = payload.get("output")
            state.final_output = output if isinstance(output, str) else json.dumps(to_json_safe(output))
            state.result_type = str(payload.get("result_type") or "answer")
            return
        if chunk_type == INTERACTION:
            interrupt_id, value = _interaction_payload(payload)
            if interrupt_id:
                state.pending_interrupts[interrupt_id] = value
            return
        if chunk_type == "execution.error" and isinstance(payload, Mapping):
            state.error = TurnError(
                message=str(payload.get("message") or "DeepAgent execution error"),
                code=str(payload.get("code") or "execution_error"),
                category="sdk_error",
            )
            return
        await self._emit(
            ProviderEvent(
                provider=PROVIDER_NAME,
                event_type=str(chunk_type or "chunk"),
                schema_version="1",
                payload=freeze_json_object(to_json_object(payload)),
            ),
            turn=turn,
        )

    async def _resolve_interrupts(self, turn: PendingTurn, state: _TurnState) -> InteractiveInput | None:
        """Ask the host for every pending interrupt; ``None`` leaves the turn open."""

        if not self._has_host_interactions():
            return None
        interactive_input = InteractiveInput()
        for interrupt_id, value in state.pending_interrupts.items():
            request = UserInputRequest(
                request_id=interrupt_id,
                prompt=_interrupt_prompt(value),
                provider_session_id=self._session_id,
                turn_id=turn.turn_id,
                provider_data={"value": to_json_safe(value)},
            )
            response = await self._request_interaction(request)
            if response is None or response.status is not InteractionResponseStatus.COMPLETED:
                return None
            interactive_input.update(interrupt_id, json_value_to_builtin(response.content))
        return interactive_input

    def _build_result(
        self,
        turn: PendingTurn,
        state: _TurnState,
        timing: TurnTiming,
    ) -> tuple[TurnEventKind, TurnResult]:
        final_output = state.final_output if state.final_output is not None else "".join(state.answer_parts)
        messages = state.messages()
        if turn.abort_requested:
            return TurnEventKind.ABORTED, interrupted_result(
                turn,
                provider_name=PROVIDER_NAME,
                timing=timing,
                messages=messages,
                final_output=final_output,
            )
        common: dict[str, Any] = {
            "messages": messages,
            "final_output": final_output,
            "started_at": timing.started_at,
            "completed_at": timing.completed_at(),
            "duration_ms": timing.duration_ms(),
        }
        if state.error is not None:
            return TurnEventKind.FAILED, TurnResult(status=TurnStatus.FAILED, error=state.error, **common)
        provider_data: dict[str, Any] = {}
        stop_reason = state.result_type or "answer"
        if state.pending_interrupts:
            stop_reason = "interrupt"
            provider_data["pending_interrupt_ids"] = list(state.pending_interrupts)
        return TurnEventKind.FINISHED, TurnResult(
            status=TurnStatus.COMPLETED,
            stop_reason=stop_reason,
            provider_data=freeze_json_object(provider_data),
            **common,
        )

    async def _steer(self, turn: PendingTurn, content: HarnessInput) -> None:
        _ = turn
        agent = self._agent
        if agent is None:
            raise HarnessProtocolError("DeepAgent disappeared during an active cycle")
        await agent.send_input(
            SendInputRequest(
                request_id=f"steer-{uuid.uuid4().hex}",
                inputs={"query": harness_input_text(content)},
                mode=InputDispatchMode.STEER,
            )
        )

    async def _interrupt_turn(self, turn: PendingTurn, mode: AbortMode) -> None:
        _ = turn, mode
        agent = self._agent
        if agent is None:
            return
        await agent.cancel_round(reason="harness_abort")


def _append_context_prompt(agent: DeepAgent, system_prompt: str, language: str | None) -> None:
    builder = agent.system_prompt_builder
    if builder is None:
        return
    builder.add_section(
        PromptSection(
            name=_CONTEXT_PROMPT_SECTION,
            content={language or "cn": system_prompt, "en": system_prompt, "cn": system_prompt},
            priority=10,
        )
    )
    agent.apply_prompt_builder_to_react_agent()


def _tool_call_id(inputs: ToolCallInputs) -> str:
    tool_call = inputs.tool_call
    call_id = getattr(tool_call, "id", None)
    if isinstance(call_id, str) and call_id:
        return call_id
    return f"{inputs.tool_name}:{inputs.react_iteration}:{uuid.uuid4().hex[:8]}"


def _parse_args(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return to_json_safe(value)


def _payload_text(payload: Any) -> str:
    if isinstance(payload, Mapping):
        content = payload.get("content")
        return content if isinstance(content, str) else ""
    return payload if isinstance(payload, str) else ""


def _delta(turn_id: str, channel: OutputChannel, text: str) -> OutputEvent:
    return OutputEvent(
        output_id=f"deepagent-output:{turn_id}:{channel.value}",
        kind=OutputKind.TEXT,
        content=text,
        operation=OutputOperation.DELTA,
        channel=channel,
    )


def _interaction_payload(payload: Any) -> tuple[str | None, Any]:
    if isinstance(payload, tuple) and len(payload) == 2:
        return (str(payload[0]) if payload[0] is not None else None), payload[1]
    interrupt_id = getattr(payload, "id", None)
    if isinstance(interrupt_id, str):
        return interrupt_id, getattr(payload, "value", None)
    if isinstance(payload, Mapping) and isinstance(payload.get("id"), str):
        return payload["id"], payload.get("value")
    return None, payload


def _interrupt_prompt(value: Any) -> str:
    """Render an ask-user interrupt value as a host-facing prompt."""

    data = to_json_safe(value)
    if isinstance(data, Mapping):
        questions = data.get("questions")
        if isinstance(questions, list):
            lines = [str(item.get("question") or "") for item in questions if isinstance(item, Mapping)]
            rendered = "\n".join(line for line in lines if line)
            if rendered:
                return rendered
        message = data.get("message")
        if isinstance(message, str) and message:
            return message
    return json.dumps(data, ensure_ascii=False) if data is not None else "The agent is waiting for your input."


def _turn_query(content: HarnessInput) -> Any:
    """Return the DeepAgent query for a protocol input.

    Inputs tagged with ``metadata.kind == "interactive_input"`` carry a
    serialized ``InteractiveInput`` so a host without an interaction handler
    can still resume a pending interrupt as a new input.
    """

    if content.metadata.get("kind") == INTERACTIVE_INPUT_METADATA_KIND:
        return InteractiveInput.model_validate(json_value_to_builtin(content.content))
    return harness_input_text(content)


__all__ = ["ADAPTER_VERSION", "AgentFactory", "DeepAgentHarness", "INTERACTIVE_INPUT_METADATA_KIND", "PROVIDER_NAME"]
