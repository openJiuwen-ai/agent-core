# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for recording a harness event stream as trajectory spans."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.semconv import (
    GEN_AI_CONVERSATION_ID,
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_SYSTEM_INSTRUCTIONS,
    GEN_AI_TOOL_CALL_RESULT,
    GEN_AI_USAGE_INPUT_TOKENS,
    OJ_AGENT_MODE,
    OJ_EXECUTION_SUBJECT_DISPLAY_NAME,
    OJ_EXECUTION_SUBJECT_ID,
    OJ_EXECUTION_SUBJECT_KIND,
    OJ_EXECUTION_SUBJECT_REQUEST_NUMBER,
    OJ_INFERENCE_ID,
    OJ_SPAN_INPUT,
    OJ_SPAN_OUTPUT,
    OJ_STEP_NUMBER,
    OJ_TOOL_AUTHORITATIVE,
    OJ_TRAJECTORY_EVENT_KIND,
    OJ_TRAJECTORY_PAYLOAD,
    OJ_TRAJECTORY_RECORD_KIND,
    OJ_TRAJECTORY_SUBJECT_SEQUENCE,
    OJ_TURN_ID,
    OJ_TURN_NUMBER,
)
from openjiuwen.extensions.observability.setup import init_observability, shutdown_observability
from openjiuwen.harness.execution_subject import ExecutionSubject
from openjiuwen.harness_protocol import (
    ContentBlock,
    HarnessEvent,
    ItemEventKind,
    ItemLifecycleEvent,
    MessageRole,
    ModelRequestEvent,
    ModelRequestStatus,
    TurnError,
    TurnEventKind,
    TurnLifecycleEvent,
    TurnMessage,
    TurnResult,
    TurnStatus,
    TurnUsage,
)
from openjiuwen.harness_providers.trajectory import HarnessTrajectoryRecorder
from tests.test_logger import logger


@pytest.fixture
def exporter() -> Iterator[InMemorySpanExporter]:
    """Initialize observability with an in-memory exporter for one test."""
    memory = InMemorySpanExporter()
    init_observability(
        ObservabilityConfig(
            service_name="harness-trajectory-test",
            exporter="console",
            redact_prompts=False,
            redact_completions=False,
        ),
        span_exporter_override=memory,
    )
    yield memory
    shutdown_observability()


def _recorder() -> HarnessTrajectoryRecorder:
    session_id = f"session-{uuid.uuid4().hex}"
    subject = ExecutionSubject(
        subject_id=f"team-member:{session_id}:team-a:coder",
        display_name="coder",
        kind="team_member",
        session_id=session_id,
    )
    recorder = HarnessTrajectoryRecorder.create(
        subject=subject,
        agent_name="coder",
        agent_mode="team",
        attributes={"agentteam.team.name": "team-a"},
    )
    assert recorder is not None
    return recorder


class _Stream:
    """Build ordered envelopes for one recorder."""

    def __init__(self, recorder: HarnessTrajectoryRecorder) -> None:
        self._recorder = recorder
        self._sequence = 0

    def emit(
        self,
        event: Any,
        *,
        timestamp: float,
        turn_id: str | None = "turn-1",
        item_id: str | None = None,
        causation_ids: tuple[str, ...] = (),
    ) -> None:
        self._sequence += 1
        self._recorder.observe(
            HarnessEvent(
                sequence=self._sequence,
                timestamp=timestamp,
                event=event,
                host_session_id=self._recorder.subject.session_id,
                agent_id="coder",
                turn_id=turn_id,
                item_id=item_id,
                causation_ids=causation_ids,
            )
        )


def _text_message(message_id: str, role: MessageRole, text: str, **data: Any) -> TurnMessage:
    return TurnMessage(
        message_id=message_id,
        role=role,
        content=(ContentBlock(block_id=f"{message_id}:0", kind="text", content=text),),
        data=data,
    )


def _request(request_id: str, *, started_at: float, ended_at: float, history: tuple[TurnMessage, ...]) -> ModelRequestEvent:
    return ModelRequestEvent(
        request_id=request_id,
        status=ModelRequestStatus.COMPLETED,
        started_at=started_at,
        ended_at=ended_at,
        model="model-a",
        provider_name="vendor-a",
        system_instructions=(ContentBlock(block_id="system-0", kind="text", content="you are coder"),),
        input_messages=history,
        input_observed=True,
        output_message=TurnMessage(
            message_id=f"{request_id}:assistant",
            role=MessageRole.ASSISTANT,
            content=(
                ContentBlock(block_id="r", kind="reasoning", content="check the tree"),
                ContentBlock(block_id="t", kind="text", content="listing"),
                ContentBlock(
                    block_id="c",
                    kind="tool_call",
                    content={"name": "ls", "arguments": {"path": "."}},
                    data={"call_id": "call-1"},
                ),
            ),
        ),
        usage=TurnUsage(input_tokens=40, output_tokens=6),
    )


def _by_kind(exporter: InMemorySpanExporter, kind: str) -> list[Any]:
    return [span for span in exporter.get_finished_spans() if span.attributes.get(OJ_TRAJECTORY_RECORD_KIND) == kind]


def _completed(text: str) -> TurnLifecycleEvent:
    return TurnLifecycleEvent(
        kind=TurnEventKind.FINISHED,
        result=TurnResult(status=TurnStatus.COMPLETED, final_output=text),
    )


def test_turn_is_its_own_trace_under_the_execution_subject(exporter: InMemorySpanExporter) -> None:
    recorder = _recorder()
    stream = _Stream(recorder)
    recorder.record_input("turn-1", "list the files")
    stream.emit(TurnLifecycleEvent(kind=TurnEventKind.STARTED), timestamp=100.0)
    stream.emit(_completed("done"), timestamp=101.0)

    turns = _by_kind(exporter, "turn")
    logger.info("turn spans: {}", [dict(span.attributes) for span in turns])
    assert len(turns) == 1
    turn = turns[0]
    assert turn.parent is None
    assert turn.attributes[OJ_TURN_ID] == "turn-1"
    assert turn.attributes[OJ_AGENT_MODE] == "team"
    assert turn.attributes[OJ_EXECUTION_SUBJECT_ID] == recorder.subject.subject_id
    assert turn.attributes[OJ_EXECUTION_SUBJECT_KIND] == "team_member"
    assert turn.attributes[OJ_EXECUTION_SUBJECT_DISPLAY_NAME] == "coder"
    assert turn.attributes[GEN_AI_CONVERSATION_ID] == recorder.subject.session_id
    assert turn.attributes[OJ_SPAN_INPUT] == "list the files"
    assert turn.attributes[OJ_SPAN_OUTPUT] == "done"
    assert turn.start_time == 100_000_000_000
    assert turn.end_time == 101_000_000_000
    assert turn.status.status_code is StatusCode.OK


def test_host_turn_identity_is_stamped_on_every_span_of_the_turn(exporter: InMemorySpanExporter) -> None:
    recorder = _recorder()
    stream = _Stream(recorder)
    recorder.record_turn_identity("turn-1", turn_id="member-turn-7", turn_number=7)
    stream.emit(TurnLifecycleEvent(kind=TurnEventKind.STARTED), timestamp=100.0)
    stream.emit(
        _request("req-1", started_at=100.5, ended_at=101.0, history=(_text_message("u", MessageRole.USER, "hi"),)),
        timestamp=101.0,
    )
    stream.emit(
        ItemLifecycleEvent(kind=ItemEventKind.STARTED, item_type="tool", data={"name": "ls"}),
        timestamp=101.1,
        item_id="call-1",
        causation_ids=("req-1",),
    )
    stream.emit(_completed("done"), timestamp=102.0)

    spans = [span for span in exporter.get_finished_spans() if span.attributes.get(OJ_TRAJECTORY_RECORD_KIND)]
    assert {span.attributes[OJ_TRAJECTORY_RECORD_KIND] for span in spans} == {"turn", "inference", "tool", "event"}
    assert {span.attributes[OJ_TURN_ID] for span in spans} == {"member-turn-7"}
    assert {span.attributes[OJ_TURN_NUMBER] for span in spans} == {7}


def test_model_request_becomes_inference_with_window_commit(exporter: InMemorySpanExporter) -> None:
    recorder = _recorder()
    stream = _Stream(recorder)
    user = _text_message("user-1", MessageRole.USER, "list the files", origin="external_user")
    stream.emit(TurnLifecycleEvent(kind=TurnEventKind.STARTED), timestamp=100.0)
    stream.emit(_request("req-1", started_at=100.5, ended_at=102.0, history=(user,)), timestamp=102.0)
    assistant = TurnMessage(
        message_id="req-1:assistant",
        role=MessageRole.ASSISTANT,
        content=(
            ContentBlock(
                block_id="c",
                kind="tool_call",
                content={"name": "ls", "arguments": {"path": "."}},
                data={"call_id": "call-1"},
            ),
        ),
    )
    tool_result = TurnMessage(
        message_id="tool-msg-1",
        role=MessageRole.TOOL,
        content=(ContentBlock(block_id="r", kind="tool_result", content="a.py", data={"call_id": "call-1"}),),
    )
    stream.emit(
        _request("req-2", started_at=103.0, ended_at=104.0, history=(user, assistant, tool_result)),
        timestamp=104.0,
    )
    stream.emit(_completed("done"), timestamp=105.0)

    turn = _by_kind(exporter, "turn")[0]
    inferences = sorted(_by_kind(exporter, "inference"), key=lambda span: span.start_time)
    commits = [span for span in exporter.get_finished_spans() if span.attributes.get(OJ_TRAJECTORY_EVENT_KIND)]
    assert len(inferences) == 2
    first, second = inferences
    assert first.parent.span_id == turn.context.span_id
    assert first.attributes[OJ_INFERENCE_ID] == f"{first.context.span_id:016x}"
    assert [span.attributes[OJ_STEP_NUMBER] for span in inferences] == [1, 2]
    assert [span.attributes[OJ_EXECUTION_SUBJECT_REQUEST_NUMBER] for span in inferences] == [1, 2]
    assert first.start_time == 100_500_000_000
    assert first.end_time == 102_000_000_000
    assert first.attributes[GEN_AI_USAGE_INPUT_TOKENS] == 40
    assert json.loads(first.attributes[GEN_AI_SYSTEM_INSTRUCTIONS])[0]["content"] == "you are coder"
    assert json.loads(first.attributes[GEN_AI_INPUT_MESSAGES]) == [
        {"role": "user", "parts": [{"type": "text", "content": "list the files"}]},
    ]
    output_parts = json.loads(first.attributes[GEN_AI_OUTPUT_MESSAGES])[0]["parts"]
    assert [part["type"] for part in output_parts] == ["reasoning", "text", "tool_call"]
    assert output_parts[2]["id"] == "call-1"

    assert [commit.parent.span_id for commit in commits] == [first.context.span_id, second.context.span_id]
    assert [commit.attributes[OJ_TRAJECTORY_SUBJECT_SEQUENCE] for commit in commits] == [1, 2]
    baseline = json.loads(commits[0].attributes[OJ_TRAJECTORY_PAYLOAD])
    delta = json.loads(commits[1].attributes[OJ_TRAJECTORY_PAYLOAD])
    logger.info("baseline commit: {}", baseline)
    logger.info("delta commit: {}", delta)
    assert [message["origin"] for message in baseline["messages"]] == ["harness_internal", "external_user"]
    inserted = [operation for operation in delta["delta"] if operation.get("op") == "insert"]
    assert inserted
    assert not [operation for operation in delta["delta"] if operation.get("op") == "remove"]


def test_multi_block_text_messages_are_stated_as_one_body(exporter: InMemorySpanExporter) -> None:
    recorder = _recorder()
    stream = _Stream(recorder)
    user = TurnMessage(
        message_id="user-1",
        role=MessageRole.USER,
        content=(
            ContentBlock(block_id="user-1:0", kind="text", content="<reminder>stay terse</reminder>"),
            ContentBlock(block_id="user-1:1", kind="text", content="list files"),
        ),
    )
    stream.emit(TurnLifecycleEvent(kind=TurnEventKind.STARTED), timestamp=100.0)
    stream.emit(_request("req-1", started_at=100.5, ended_at=101.0, history=(user,)), timestamp=101.0)
    stream.emit(_completed("done"), timestamp=102.0)

    inference = _by_kind(exporter, "inference")[0]
    commit = _by_kind(exporter, "event")[0]
    window = json.loads(commit.attributes[OJ_TRAJECTORY_PAYLOAD])["messages"]
    logger.info("committed window: {}", window)
    # A reader reads one body, so the blocks are joined rather than stated as
    # a JSON array of parts.
    assert window[-1]["content"] == "<reminder>stay terse</reminder>\n\nlist files"
    assert json.loads(inference.attributes[GEN_AI_INPUT_MESSAGES]) == [
        {"role": "user", "parts": [{"type": "text", "content": "<reminder>stay terse</reminder>\n\nlist files"}]},
    ]


def test_tool_items_are_owned_by_the_request_that_caused_them(exporter: InMemorySpanExporter) -> None:
    recorder = _recorder()
    stream = _Stream(recorder)
    user = _text_message("user-1", MessageRole.USER, "list the files")
    stream.emit(TurnLifecycleEvent(kind=TurnEventKind.STARTED), timestamp=100.0)
    stream.emit(_request("req-1", started_at=100.5, ended_at=102.0, history=(user,)), timestamp=102.0)
    stream.emit(
        ItemLifecycleEvent(kind=ItemEventKind.STARTED, item_type="tool", data={"name": "ls", "arguments": {"path": "."}}),
        timestamp=102.1,
        item_id="call-1",
        causation_ids=("req-1",),
    )
    stream.emit(
        ItemLifecycleEvent(
            kind=ItemEventKind.COMPLETED,
            item_type="tool",
            data={"tool_name": "ls", "result": "a.py", "is_error": False},
        ),
        timestamp=102.6,
        item_id="call-1",
    )
    stream.emit(
        ItemLifecycleEvent(kind=ItemEventKind.STARTED, item_type="tool", data={"name": "rm", "arguments": {}}),
        timestamp=103.0,
        item_id="call-2",
    )
    stream.emit(
        ItemLifecycleEvent(
            kind=ItemEventKind.COMPLETED,
            item_type="tool",
            data={"tool_name": "rm", "result": "denied", "is_error": True},
        ),
        timestamp=103.5,
        item_id="call-2",
    )
    stream.emit(_completed("done"), timestamp=104.0)

    inference = _by_kind(exporter, "inference")[0]
    tools = {span.attributes["gen_ai.tool.name"]: span for span in _by_kind(exporter, "tool")}
    owned = tools["ls"]
    assert owned.attributes[OJ_INFERENCE_ID] == inference.attributes[OJ_INFERENCE_ID]
    assert owned.attributes[OJ_STEP_NUMBER] == 1
    assert owned.attributes[OJ_TOOL_AUTHORITATIVE] is True
    assert owned.attributes[GEN_AI_TOOL_CALL_RESULT] == "a.py"
    assert (owned.start_time, owned.end_time) == (102_100_000_000, 102_600_000_000)
    assert owned.status.status_code is StatusCode.OK
    unowned = tools["rm"]
    assert OJ_INFERENCE_ID not in unowned.attributes
    assert OJ_TOOL_AUTHORITATIVE not in unowned.attributes
    assert unowned.status.status_code is StatusCode.ERROR


def test_unobserved_request_records_output_without_committing_a_window(exporter: InMemorySpanExporter) -> None:
    recorder = _recorder()
    stream = _Stream(recorder)
    stream.emit(TurnLifecycleEvent(kind=TurnEventKind.STARTED), timestamp=100.0)
    stream.emit(
        ModelRequestEvent(
            request_id="req-1",
            status=ModelRequestStatus.FAILED,
            started_at=100.0,
            ended_at=101.0,
            output_message=_text_message("req-1:assistant", MessageRole.ASSISTANT, "partial"),
            error=TurnError(message="overloaded", category="server_unavailable"),
        ),
        timestamp=101.0,
    )
    stream.emit(
        TurnLifecycleEvent(
            kind=TurnEventKind.FAILED,
            result=TurnResult(status=TurnStatus.FAILED, error=TurnError(message="overloaded")),
        ),
        timestamp=101.5,
    )

    inference = _by_kind(exporter, "inference")[0]
    assert GEN_AI_INPUT_MESSAGES not in inference.attributes
    assert json.loads(inference.attributes[GEN_AI_OUTPUT_MESSAGES])[0]["parts"][0]["content"] == "partial"
    assert inference.status.status_code is StatusCode.ERROR
    assert not _by_kind(exporter, "event")
    assert _by_kind(exporter, "turn")[0].status.status_code is StatusCode.ERROR


def test_turn_end_closes_unfinished_tools_and_failure_is_recorded(exporter: InMemorySpanExporter) -> None:
    recorder = _recorder()
    stream = _Stream(recorder)
    stream.emit(TurnLifecycleEvent(kind=TurnEventKind.STARTED), timestamp=100.0)
    stream.emit(
        ItemLifecycleEvent(kind=ItemEventKind.STARTED, item_type="tool", data={"name": "sleep"}),
        timestamp=100.5,
        item_id="call-1",
    )
    recorder.record_failure(
        name="external_runtime.failed",
        summary="coder turn failed",
        attributes={"external_runtime.failure_id": "failure-1"},
    )
    stream.emit(_completed(""), timestamp=101.0)

    tool = _by_kind(exporter, "tool")[0]
    turn = _by_kind(exporter, "turn")[0]
    assert tool.status.status_code is StatusCode.ERROR
    assert tool.end_time == 101_000_000_000
    assert turn.attributes["external_runtime.failure_id"] == "failure-1"
    assert [event.name for event in turn.events] == ["external_runtime.failed"]


def test_failure_without_active_turn_emits_a_failed_turn(exporter: InMemorySpanExporter) -> None:
    recorder = _recorder()
    recorder.record_failure(
        name="external_runtime.failed",
        summary="startup failed",
        attributes={"external_runtime.phase": "startup"},
    )

    turn = _by_kind(exporter, "turn")[0]
    assert turn.status.status_code is StatusCode.ERROR
    assert turn.attributes["external_runtime.phase"] == "startup"
    assert turn.attributes[OJ_EXECUTION_SUBJECT_ID] == recorder.subject.subject_id


def test_create_returns_none_without_observability() -> None:
    subject = ExecutionSubject(subject_id="s", display_name="coder", kind="team_member", session_id="session")
    assert HarnessTrajectoryRecorder.create(subject=subject, agent_name="coder", agent_mode="team") is None
