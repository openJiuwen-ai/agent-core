# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Record a ``HarnessProtocol`` observation stream as trajectory spans.

:class:`HarnessTrajectoryRecorder` is the host-side glue that turns the
provider-neutral event stream of any third-party harness into the same
trajectory records an in-process agent produces: one turn-rooted trace per
turn, one ``inference`` span (with its ``context.window.commit``) per
``ModelRequestEvent`` and one ``tool`` span per tool item. Every record states
the execution subject the host assigned, so a viewer files it under that
agent's lane.

Provider differences never reach this module: a provider that can observe its
model requests reports them through ``ModelRequestEvent`` and links the tool
items a request caused through envelope ``causation_ids``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, Tracer, set_span_in_context

from openjiuwen.core.common.logging import LazyLogger, LogManager
from openjiuwen.core.foundation.llm.schema.message import (
    OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER,
    OPENJIUWEN_MESSAGE_ORIGIN_METADATA,
)
from openjiuwen.extensions.observability.callback_handler import OtelCallbackHandler
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.redaction import redact_completion, redact_error_summary, redact_prompt
from openjiuwen.extensions.observability.semconv import (
    ERROR_TYPE,
    GEN_AI_AGENT_NAME,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_OPERATION_NAME,
    GEN_AI_PROVIDER_NAME,
    GEN_AI_REQUEST_CHOICE_COUNT,
    GEN_AI_REQUEST_FREQUENCY_PENALTY,
    GEN_AI_REQUEST_MAX_TOKENS,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_REQUEST_PRESENCE_PENALTY,
    GEN_AI_REQUEST_REASONING_LEVEL,
    GEN_AI_REQUEST_SEED,
    GEN_AI_REQUEST_STOP_SEQUENCES,
    GEN_AI_REQUEST_STREAM,
    GEN_AI_REQUEST_TEMPERATURE,
    GEN_AI_REQUEST_TOP_K,
    GEN_AI_REQUEST_TOP_P,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_ID,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK,
    GEN_AI_TOOL_CALL_ARGUMENTS,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_CALL_RESULT,
    GEN_AI_TOOL_DEFINITIONS,
    GEN_AI_TOOL_NAME,
    GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    GEN_AI_USAGE_REASONING_OUTPUT_TOKENS,
    OJ_AGENT_MODE,
    OJ_EXECUTION_SUBJECT_DISPLAY_NAME,
    OJ_EXECUTION_SUBJECT_ID,
    OJ_EXECUTION_SUBJECT_KIND,
    OJ_EXECUTION_SUBJECT_PARENT_ID,
    OJ_EXECUTION_SUBJECT_REQUEST_NUMBER,
    OJ_EXECUTION_SUBJECT_SESSION_ID,
    OJ_GEN_AI_RESPONSE_TOTAL_LATENCY_MS,
    OJ_GEN_AI_USAGE_TOTAL_COST,
    OJ_INFERENCE_ID,
    OJ_REQUEST_ID,
    OJ_REQUEST_PURPOSE,
    OJ_SPAN_INPUT,
    OJ_SPAN_OUTPUT,
    OJ_STEP_NUMBER,
    OJ_TOOL_AUTHORITATIVE,
    OJ_TRACE_ROOT,
    OJ_TRAJECTORY_RECORD_KIND,
    OJ_TRAJECTORY_SCHEMA_VERSION,
    OJ_TURN_ID,
    OJ_TURN_NUMBER,
    TRAJECTORY_SPAN_SCHEMA_VERSION,
)
from openjiuwen.extensions.observability.span_context import next_execution_subject_request_number
from openjiuwen.extensions.observability.tool_outcome import TOOL_REPORTED_FAILURE
from openjiuwen.extensions.observability.trajectory_events import emit_context_window_commit
from openjiuwen.harness.execution_subject import ExecutionSubject
from openjiuwen.harness_protocol import (
    ContentBlock,
    HarnessEvent,
    ItemEventKind,
    ItemLifecycleEvent,
    ModelRequestEvent,
    ModelRequestStatus,
    TurnEventKind,
    TurnLifecycleEvent,
    TurnMessage,
    TurnResult,
    json_value_to_builtin,
)

logger = LazyLogger(lambda: LogManager.get_logger("harness_providers"))

_TRACER_NAME = "openjiuwen.harness_providers.trajectory"
_TOOL_ITEM_TYPE = "tool"
_ASSISTANT_REQUEST_PURPOSE = "assistant"
# Sampling parameters a provider may state, mapped to their GenAI attribute.
_REQUEST_PARAMETER_ATTRIBUTES = {
    "temperature": GEN_AI_REQUEST_TEMPERATURE,
    "top_p": GEN_AI_REQUEST_TOP_P,
    "top_k": GEN_AI_REQUEST_TOP_K,
    "max_tokens": GEN_AI_REQUEST_MAX_TOKENS,
    "seed": GEN_AI_REQUEST_SEED,
    "choice_count": GEN_AI_REQUEST_CHOICE_COUNT,
    "presence_penalty": GEN_AI_REQUEST_PRESENCE_PENALTY,
    "frequency_penalty": GEN_AI_REQUEST_FREQUENCY_PENALTY,
    "reasoning_level": GEN_AI_REQUEST_REASONING_LEVEL,
    "stream": GEN_AI_REQUEST_STREAM,
}

AttributeValue = str | bool | int | float


@dataclass
class _TurnRecord:
    """Open spans and request identities of one recorded turn."""

    span: Span
    turn_attributes: dict[str, AttributeValue]
    request_count: int = 0
    requests: dict[str, tuple[str, int]] = field(default_factory=dict)
    tools: dict[str, Span] = field(default_factory=dict)


class HarnessTrajectoryRecorder:
    """Turn one harness's ``HarnessEvent`` stream into trajectory spans.

    The recorder is fed every envelope through :meth:`observe` in stream
    order and is not safe for concurrent use; a host drives it from the task
    that consumes the harness event stream.

    Args:
        subject: Execution subject every record is filed under. Its
            ``session_id`` is the host conversation the records belong to.
        agent_name: Display name of the agent on turn spans.
        agent_mode: Canonical ``openjiuwen.agent.mode`` of the host run.
        tracer: Tracer that emits the spans.
        config: Active observability configuration (redaction policy).
        attributes: Extra host attributes stamped on every turn span.
    """

    def __init__(
        self,
        *,
        subject: ExecutionSubject,
        agent_name: str,
        agent_mode: str,
        tracer: Tracer,
        config: ObservabilityConfig,
        attributes: Mapping[str, AttributeValue] | None = None,
    ) -> None:
        if not subject.session_id:
            raise ValueError("trajectory recorder subject requires a session_id")
        if not subject.display_name:
            raise ValueError("trajectory recorder subject requires a display_name")
        self._subject = subject
        self._agent_name = agent_name
        self._agent_mode = agent_mode
        self._tracer = tracer
        self._config = config
        self._attributes = dict(attributes or {})
        self._handler = OtelCallbackHandler(config, tracer=tracer)
        self._turns: dict[str, _TurnRecord] = {}
        self._pending_inputs: dict[str, str] = {}
        self._turn_inputs: dict[str, str] = {}
        self._pending_identities: dict[str, tuple[str, int]] = {}
        self._active_turn_id: str | None = None

    @classmethod
    def create(
        cls,
        *,
        subject: ExecutionSubject,
        agent_name: str,
        agent_mode: str,
        attributes: Mapping[str, AttributeValue] | None = None,
    ) -> "HarnessTrajectoryRecorder | None":
        """Build a recorder on the shared observability runtime.

        Returns:
            The recorder, or ``None`` when observability is not initialized.
        """
        from openjiuwen.extensions.observability.setup import get_config, get_tracer, is_initialized

        config = get_config()
        if not is_initialized() or config is None:
            return None
        return cls(
            subject=subject,
            agent_name=agent_name,
            agent_mode=agent_mode,
            tracer=get_tracer(_TRACER_NAME),
            config=config,
            attributes=attributes,
        )

    @property
    def subject(self) -> ExecutionSubject:
        """Return the execution subject records are filed under."""
        return self._subject

    # ------------------------------------------------------------------
    # Host inputs
    # ------------------------------------------------------------------

    def record_input(self, turn_id: str, text: str) -> None:
        """Remember the input text that opens ``turn_id``.

        Inputs steered into a turn that already started are not recorded
        again; the turn span states the input that started it.
        """
        if not text or turn_id in self._turns:
            return
        self._pending_inputs[turn_id] = text
        self._turn_inputs[turn_id] = text

    def record_turn_identity(self, protocol_turn_id: str, *, turn_id: str, turn_number: int) -> None:
        """Assign the host's trajectory turn identity to a protocol turn.

        A host that keeps its own turn numbering (persisted across harness
        restarts, say) states it here before the turn's ``STARTED`` event is
        observed. Without it the protocol ``turn_id`` is the trajectory turn id
        and no turn number is stated.

        Args:
            protocol_turn_id: The envelope ``turn_id`` of the protocol turn.
            turn_id: Trajectory ``openjiuwen.turn.id`` for the turn.
            turn_number: 1-based ``openjiuwen.turn.number`` for the turn.
        """
        if not protocol_turn_id or not turn_id or turn_number < 1:
            return
        self._pending_identities[protocol_turn_id] = (turn_id, turn_number)

    def record_failure(
        self,
        *,
        name: str,
        summary: str,
        attributes: Mapping[str, AttributeValue],
    ) -> None:
        """Record a host-finalized failure on the active turn.

        The event is added to the active turn span and mirrored onto its
        attributes, because some OTLP backends drop span events. Without an
        active turn (a startup failure) a zero-length failed turn is emitted
        so the failure still shows in the agent's lane.

        Args:
            name: Span event name.
            summary: Human-readable failure summary for the span status.
            attributes: Failure facts to record.
        """
        record = self._turns.get(self._active_turn_id or "")
        if record is not None and record.span.is_recording():
            span = record.span
            standalone = False
        else:
            span = self._tracer.start_span(
                name=f"invoke_agent {self._agent_name}",
                kind=SpanKind.SERVER,
                context=otel_context.Context(),
                attributes={**self._turn_attributes(input_text=""), **_turn_identity_attributes(None, None)},
            )
            standalone = True
        span.add_event(name, dict(attributes))
        for key, value in attributes.items():
            span.set_attribute(key, value)
        span.set_status(Status(StatusCode.ERROR, redact_error_summary(summary, self._config)))
        if standalone:
            span.end()

    # ------------------------------------------------------------------
    # Event stream
    # ------------------------------------------------------------------

    def observe(self, envelope: HarnessEvent) -> None:
        """Record one envelope of the harness observation stream."""
        payload = envelope.event
        if isinstance(payload, TurnLifecycleEvent):
            self._observe_turn(envelope, payload)
        elif isinstance(payload, ModelRequestEvent):
            self._observe_model_request(envelope, payload)
        elif isinstance(payload, ItemLifecycleEvent) and payload.item_type == _TOOL_ITEM_TYPE:
            self._observe_tool(envelope, payload)

    def close(self) -> None:
        """End every span still open, e.g. when the harness stopped mid-turn."""
        for turn_id in list(self._turns):
            self._finish_turn(turn_id, result=None, end_time=None)

    def _observe_turn(self, envelope: HarnessEvent, payload: TurnLifecycleEvent) -> None:
        turn_id = envelope.turn_id or ""
        if payload.kind is TurnEventKind.STARTED:
            self._start_turn(turn_id, start_time=_ns(envelope.timestamp))
        elif payload.kind in (TurnEventKind.FINISHED, TurnEventKind.ABORTED, TurnEventKind.FAILED):
            self._finish_turn(turn_id, result=payload.result, end_time=_ns(envelope.timestamp))

    def _start_turn(self, turn_id: str, *, start_time: int) -> None:
        stale = self._turns.get(turn_id)
        if stale is not None:
            self._finish_turn(turn_id, result=None, end_time=start_time)
        input_text = self._pending_inputs.pop(turn_id, "")
        trajectory_turn_id, turn_number = self._pending_identities.pop(turn_id, (turn_id, None))
        identity = _turn_identity_attributes(trajectory_turn_id, turn_number)
        span = self._tracer.start_span(
            name=f"invoke_agent {self._agent_name}",
            kind=SpanKind.SERVER,
            context=otel_context.Context(),
            start_time=start_time,
            attributes={**self._turn_attributes(input_text=input_text), **identity},
        )
        self._turns[turn_id] = _TurnRecord(span=span, turn_attributes=identity)
        self._active_turn_id = turn_id

    def _turn_attributes(self, *, input_text: str) -> dict[str, AttributeValue]:
        attributes: dict[str, AttributeValue] = {
            **self._attributes,
            GEN_AI_OPERATION_NAME: "invoke_agent",
            GEN_AI_AGENT_NAME: self._agent_name,
            OJ_TRAJECTORY_SCHEMA_VERSION: TRAJECTORY_SPAN_SCHEMA_VERSION,
            OJ_TRAJECTORY_RECORD_KIND: "turn",
            OJ_TRACE_ROOT: True,
            OJ_AGENT_MODE: self._agent_mode,
            **self._subject_attributes(),
        }
        if input_text:
            attributes[OJ_SPAN_INPUT] = redact_prompt(input_text, self._config)
        return attributes

    def _subject_attributes(self) -> dict[str, AttributeValue]:
        subject = self._subject
        attributes: dict[str, AttributeValue] = {
            GEN_AI_CONVERSATION_ID: subject.session_id,
            OJ_EXECUTION_SUBJECT_ID: subject.subject_id,
            OJ_EXECUTION_SUBJECT_DISPLAY_NAME: subject.display_name,
            OJ_EXECUTION_SUBJECT_KIND: subject.kind,
            OJ_EXECUTION_SUBJECT_SESSION_ID: subject.session_id,
        }
        if subject.parent_subject_id:
            attributes[OJ_EXECUTION_SUBJECT_PARENT_ID] = subject.parent_subject_id
        return attributes

    def _finish_turn(self, turn_id: str, *, result: TurnResult | None, end_time: int | None) -> None:
        record = self._turns.pop(turn_id, None)
        self._turn_inputs.pop(turn_id, None)
        if self._active_turn_id == turn_id:
            self._active_turn_id = None
        if record is None:
            return
        for tool_span in record.tools.values():
            if tool_span.is_recording():
                tool_span.set_status(Status(StatusCode.ERROR, "tool call did not complete before the turn ended"))
                tool_span.end(end_time=end_time)
        record.tools.clear()
        span = record.span
        if not span.is_recording():
            return
        if result is not None:
            final_output = json_value_to_builtin(result.final_output)
            if final_output not in (None, ""):
                span.set_attribute(OJ_SPAN_OUTPUT, redact_completion(_text(final_output), self._config))
            if result.error is not None:
                span.set_status(Status(StatusCode.ERROR, redact_error_summary(result.error.message, self._config)))
            elif result.termination is None:
                span.set_status(Status(StatusCode.OK))
        span.end(end_time=end_time)

    # ------------------------------------------------------------------
    # Model requests
    # ------------------------------------------------------------------

    def _observe_model_request(self, envelope: HarnessEvent, event: ModelRequestEvent) -> None:
        record = self._turns.get(envelope.turn_id or "")
        if record is None or not record.span.is_recording():
            logger.debug("trajectory: model request {} arrived outside a recorded turn", event.request_id)
            return
        step_number = record.request_count + 1
        model = event.model or ""
        attributes: dict[str, AttributeValue] = {
            GEN_AI_OPERATION_NAME: "chat",
            OJ_TRAJECTORY_SCHEMA_VERSION: TRAJECTORY_SPAN_SCHEMA_VERSION,
            OJ_TRAJECTORY_RECORD_KIND: "inference",
            OJ_REQUEST_ID: event.request_id,
            OJ_REQUEST_PURPOSE: _ASSISTANT_REQUEST_PURPOSE,
            **record.turn_attributes,
            OJ_STEP_NUMBER: step_number,
            OJ_AGENT_MODE: self._agent_mode,
            GEN_AI_AGENT_NAME: self._agent_name,
            **self._subject_attributes(),
            OJ_EXECUTION_SUBJECT_REQUEST_NUMBER: next_execution_subject_request_number(
                session_id=self._subject.session_id,
                subject_id=self._subject.subject_id,
            ),
        }
        if model:
            attributes[GEN_AI_REQUEST_MODEL] = model
            attributes[GEN_AI_RESPONSE_MODEL] = model
        if event.provider_name:
            attributes[GEN_AI_PROVIDER_NAME] = event.provider_name
        if event.response_id:
            attributes[GEN_AI_RESPONSE_ID] = event.response_id
        if event.finish_reasons:
            attributes[GEN_AI_RESPONSE_FINISH_REASONS] = json.dumps(
                list(event.finish_reasons),
                ensure_ascii=False,
            )
        attributes[OJ_GEN_AI_RESPONSE_TOTAL_LATENCY_MS] = (event.ended_at - event.started_at) * 1000
        if event.time_to_first_chunk is not None:
            attributes[GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK] = event.time_to_first_chunk
        attributes.update(_request_parameter_attributes(event))
        attributes.update(_usage_attributes(event))
        if event.cost is not None:
            attributes[OJ_GEN_AI_USAGE_TOTAL_COST] = event.cost.micros / 1_000_000
        span = self._tracer.start_span(
            name=f"chat {model}" if model else "chat",
            kind=SpanKind.CLIENT,
            context=set_span_in_context(record.span, otel_context.Context()),
            start_time=_ns(event.started_at),
            attributes=attributes,
        )
        inference_id = f"{span.get_span_context().span_id:016x}"
        span.set_attribute(OJ_INFERENCE_ID, inference_id)
        try:
            request_messages = _request_messages(event, self._turn_inputs.get(envelope.turn_id or ""))
            if request_messages:
                self._handler.record_request_input(span, request_messages)
            tool_definitions = json_value_to_builtin(event.tool_definitions)
            if tool_definitions:
                span.set_attribute(GEN_AI_TOOL_DEFINITIONS, json.dumps(tool_definitions, ensure_ascii=False))
            if event.output_message is not None:
                output_messages = _message_dicts(event.output_message)
                if output_messages:
                    self._handler.record_response_output(span, output_messages[0])
            if event.input_observed and request_messages:
                emit_context_window_commit(
                    tracer=self._tracer,
                    llm_span=span,
                    messages=self._handler.context_window_messages(request_messages),
                    request_purpose=_ASSISTANT_REQUEST_PURPOSE,
                )
        finally:
            _set_request_status(span, event, self._config)
            span.end(end_time=_ns(event.ended_at))
        record.request_count = step_number
        record.requests[event.request_id] = (inference_id, step_number)

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def _observe_tool(self, envelope: HarnessEvent, payload: ItemLifecycleEvent) -> None:
        record = self._turns.get(envelope.turn_id or self._active_turn_id or "")
        item_id = envelope.item_id
        if record is None or not record.span.is_recording() or not item_id:
            return
        data = json_value_to_builtin(payload.data)
        values = data if isinstance(data, dict) else {}
        if payload.kind is ItemEventKind.STARTED:
            self._start_tool(record, envelope, item_id, values)
        elif payload.kind is ItemEventKind.COMPLETED:
            span = record.tools.pop(item_id, None)
            if span is None:
                span = self._start_tool(record, envelope, item_id, values)
                record.tools.pop(item_id, None)
            self._finish_tool(span, values, end_time=_ns(envelope.timestamp))

    def _start_tool(
        self,
        record: _TurnRecord,
        envelope: HarnessEvent,
        item_id: str,
        values: dict[str, Any],
    ) -> Span:
        tool_name = str(values.get("name") or values.get("tool_name") or "unknown")
        arguments = redact_prompt(_text(values.get("arguments")), self._config)
        attributes: dict[str, AttributeValue] = {
            GEN_AI_OPERATION_NAME: "execute_tool",
            GEN_AI_TOOL_NAME: tool_name,
            GEN_AI_TOOL_CALL_ID: item_id,
            GEN_AI_TOOL_CALL_ARGUMENTS: arguments,
            OJ_SPAN_INPUT: arguments,
            OJ_TRAJECTORY_SCHEMA_VERSION: TRAJECTORY_SPAN_SCHEMA_VERSION,
            OJ_TRAJECTORY_RECORD_KIND: "tool",
            **record.turn_attributes,
            OJ_AGENT_MODE: self._agent_mode,
            GEN_AI_AGENT_NAME: self._agent_name,
            **self._subject_attributes(),
        }
        owner = _owning_request(record, envelope)
        if owner is not None:
            # Only a tool tied to the request that called it is authoritative;
            # the viewer drops an authoritative tool it cannot place.
            attributes[OJ_INFERENCE_ID] = owner[0]
            attributes[OJ_STEP_NUMBER] = owner[1]
            attributes[OJ_TOOL_AUTHORITATIVE] = True
        span = self._tracer.start_span(
            name=f"execute_tool {tool_name}",
            kind=SpanKind.INTERNAL,
            context=set_span_in_context(record.span, otel_context.Context()),
            start_time=_ns(envelope.timestamp),
            attributes=attributes,
        )
        record.tools[item_id] = span
        return span

    def _finish_tool(self, span: Span, values: dict[str, Any], *, end_time: int) -> None:
        if not span.is_recording():
            return
        result = redact_completion(_text(values.get("result")), self._config)
        span.set_attribute(GEN_AI_TOOL_CALL_RESULT, result)
        span.set_attribute(OJ_SPAN_OUTPUT, result)
        if values.get("is_error") is True:
            span.set_attribute(ERROR_TYPE, TOOL_REPORTED_FAILURE)
            span.set_status(Status(StatusCode.ERROR, redact_error_summary(result, self._config)))
        else:
            span.set_status(Status(StatusCode.OK))
        span.end(end_time=end_time)


def _turn_identity_attributes(turn_id: str | None, turn_number: int | None) -> dict[str, AttributeValue]:
    """Return the trajectory turn attributes every span of a turn carries."""
    attributes: dict[str, AttributeValue] = {}
    if turn_id:
        attributes[OJ_TURN_ID] = turn_id
    if turn_number is not None:
        attributes[OJ_TURN_NUMBER] = turn_number
    return attributes


def _owning_request(record: _TurnRecord, envelope: HarnessEvent) -> tuple[str, int] | None:
    """Return the inference id and step of the request that caused an item."""
    for causation_id in envelope.causation_ids:
        owner = record.requests.get(causation_id)
        if owner is not None:
            return owner
    return None


def _set_request_status(span: Span, event: ModelRequestEvent, config: ObservabilityConfig) -> None:
    if event.status is ModelRequestStatus.COMPLETED:
        span.set_status(Status(StatusCode.OK))
        return
    if event.error is not None:
        reason = event.error.message
        if event.error.category:
            span.set_attribute(ERROR_TYPE, event.error.category)
    else:
        reason = event.status.value
    span.set_status(Status(StatusCode.ERROR, redact_error_summary(reason, config)))


def _request_parameter_attributes(event: ModelRequestEvent) -> dict[str, AttributeValue]:
    """Return the sampling parameters a viewer states as request options."""
    attributes: dict[str, AttributeValue] = {}
    parameters = json_value_to_builtin(event.request_parameters)
    if not isinstance(parameters, dict):
        return attributes
    for name, key in _REQUEST_PARAMETER_ATTRIBUTES.items():
        value = parameters.get(name)
        if isinstance(value, (str, bool, int, float)):
            attributes[key] = value
    stop_sequences = parameters.get("stop_sequences")
    if isinstance(stop_sequences, list) and stop_sequences:
        attributes[GEN_AI_REQUEST_STOP_SEQUENCES] = json.dumps(stop_sequences, ensure_ascii=False)
    return attributes


def _usage_attributes(event: ModelRequestEvent) -> dict[str, AttributeValue]:
    usage = event.usage
    if usage is None:
        return {}
    pairs = (
        (GEN_AI_USAGE_INPUT_TOKENS, usage.input_tokens),
        (GEN_AI_USAGE_OUTPUT_TOKENS, usage.output_tokens),
        (GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS, usage.cached_input_tokens),
        (GEN_AI_USAGE_REASONING_OUTPUT_TOKENS, usage.reasoning_output_tokens),
    )
    return {key: value for key, value in pairs if value is not None}


def _request_messages(event: ModelRequestEvent, host_input: str | None) -> list[dict[str, Any]]:
    """Return the request as framework-shaped message dicts, system slot first.

    ``host_input`` is the text the host sent into the turn. A provider states
    its conversation without saying which message carries it, so the last user
    message that contains it is marked as the external user's, the way an
    in-process agent marks the input it was given.
    """
    messages: list[dict[str, Any]] = []
    system_parts = [_content_part(block) for block in event.system_instructions]
    if system_parts:
        messages.append({"role": "system", "content": _message_content(system_parts)})
    for message in event.input_messages:
        messages.extend(_message_dicts(message))
    _mark_host_input(messages, host_input)
    return messages


def _mark_host_input(messages: list[dict[str, Any]], host_input: str | None) -> None:
    text = (host_input or "").strip()
    if not text:
        return
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and text in content:
            metadata = dict(message.get("metadata") or {})
            metadata[OPENJIUWEN_MESSAGE_ORIGIN_METADATA] = OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER
            message["metadata"] = metadata
            return


def _message_dicts(message: TurnMessage) -> list[dict[str, Any]]:
    """Convert one protocol message into framework-shaped message dicts.

    Tool results become separate ``tool`` messages keyed by their call id,
    the shape the framework's trajectory helpers read; the rest of the
    message keeps the original ``message_id``.
    """
    role = message.role.value
    parts: list[dict[str, Any]] = []
    reasoning: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    for block in message.content:
        call_id = _block_call_id(block)
        content = json_value_to_builtin(block.content)
        if block.kind == "reasoning":
            reasoning.append(_text(content))
        elif block.kind == "tool_call":
            call = content if isinstance(content, dict) else {}
            tool_calls.append({
                "id": call_id,
                "name": str(call.get("name") or ""),
                "arguments": _text(call.get("arguments")),
            })
        elif block.kind == "tool_result":
            tool_results.append({
                "role": "tool",
                "message_id": f"{message.message_id}:{call_id}" if call_id else message.message_id,
                "tool_call_id": call_id,
                "content": _text(content),
            })
        else:
            parts.append(_content_part(block))
    metadata = _message_metadata(message)
    result: list[dict[str, Any]] = []
    if parts or reasoning or tool_calls or not tool_results:
        item: dict[str, Any] = {
            "role": role,
            "message_id": message.message_id,
            "content": _message_content(parts),
        }
        if reasoning:
            item["reasoning_content"] = "".join(reasoning)
        if tool_calls:
            item["tool_calls"] = tool_calls
        if metadata:
            item["metadata"] = metadata
        result.append(item)
    result.extend(tool_results)
    return result


def _content_part(block: ContentBlock) -> dict[str, Any]:
    content = json_value_to_builtin(block.content)
    if block.kind == "text":
        return {"type": "text", "text": _text(content)}
    if isinstance(content, dict):
        return {"type": block.kind, **content}
    return {"type": block.kind, "content": content}


def _message_content(parts: list[dict[str, Any]]) -> Any:
    """Return the message body: text when it is all text, the parts otherwise.

    A provider often splits one message into several text blocks (an injected
    reminder plus the message itself, say). A reader reads them as one body,
    and keeping the split would state the message as a JSON array wherever a
    view renders it as text, so text-only blocks are joined. A message with
    non-text parts (images, documents) keeps them.
    """
    if not parts:
        return ""
    if _is_text_only(parts):
        return "\n\n".join(str(part.get("text") or "") for part in parts)
    return parts


def _is_text_only(parts: list[dict[str, Any]]) -> bool:
    return all(part.get("type") == "text" for part in parts)


def _block_call_id(block: ContentBlock) -> str:
    data = json_value_to_builtin(block.data)
    if not isinstance(data, dict):
        return ""
    return str(data.get("call_id") or "")


def _message_metadata(message: TurnMessage) -> dict[str, Any]:
    data = json_value_to_builtin(message.data)
    if isinstance(data, dict) and data.get("origin") == OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER:
        return {OPENJIUWEN_MESSAGE_ORIGIN_METADATA: OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER}
    return {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _ns(timestamp: float) -> int:
    return int(timestamp * 1_000_000_000)


__all__ = ["HarnessTrajectoryRecorder"]
