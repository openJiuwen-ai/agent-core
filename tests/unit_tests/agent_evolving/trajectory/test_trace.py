# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from openjiuwen.agent_evolving.trajectory.semconv import (
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OPERATION_NAME,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_TOOL_CALL_ARGUMENTS,
    GEN_AI_TOOL_CALL_RESULT,
    GEN_AI_TOOL_NAME,
    GEN_AI_USAGE_INPUT_TOKENS,
    OJ_RL_COMPLETION_TOKEN_IDS,
    OJ_RL_LOGPROBS,
    OJ_RL_PROMPT_TOKEN_IDS,
    OJ_RL_REWARD,
    OJ_SESSION_ID,
    TRAJECTORY_END_REASON,
    TRAJECTORY_ID,
    TRAJECTORY_INCOMPLETE,
    TRAJECTORY_SCHEMA_VERSION,
    TRAJECTORY_SCHEMA_VERSION_ATTR,
    TRAJECTORY_SCOPE_NAME,
    TRAJECTORY_STEP_KIND,
    TRAJECTORY_TRACE_ID,
)
from openjiuwen.agent_evolving.trajectory.span_codec import (
    JSON_SAFE_FALLBACK_MARKER,
    is_json_safe_fallback,
    json_safe,
    unwrap_nested_io,
)
from openjiuwen.agent_evolving.trajectory.trace import (
    TRAJECTORY_TRACE_AGENT_HANDLER_NAME,
    TRAJECTORY_TRACE_WORKFLOW_HANDLER_NAME,
    TrajectoryTraceAgentHandler,
    TrajectoryTraceStateManager,
    TrajectoryTraceWorkflowHandler,
    ensure_otlp_handlers_registered,
)
from openjiuwen.agent_evolving.trajectory.types import to_legacy_trajectory
from openjiuwen.core.session.tracer import TracerHandlerRegistry
from openjiuwen.core.session.tracer.span import TraceAgentSpan

pytestmark = pytest.mark.asyncio


def make_span(trace_id: str, invoke_id: str, parent_invoke_id: str | None = None) -> TraceAgentSpan:
    return TraceAgentSpan(trace_id=trace_id, invoke_id=invoke_id, parent_invoke_id=parent_invoke_id)


def otlp_spans(trajectory) -> list[dict]:
    scope_spans = trajectory.otlp_trace["resourceSpans"][0]["scopeSpans"][0]
    return scope_spans["spans"]


def otlp_value_to_python(value: dict):
    if "stringValue" in value:
        return value["stringValue"]
    if "boolValue" in value:
        return value["boolValue"]
    if "intValue" in value:
        return int(value["intValue"])
    if "doubleValue" in value:
        return value["doubleValue"]
    if "arrayValue" in value:
        return [otlp_value_to_python(item) for item in value["arrayValue"].get("values", [])]
    if "kvlistValue" in value:
        return {item["key"]: otlp_value_to_python(item["value"]) for item in value["kvlistValue"].get("values", [])}
    return None


def otlp_attrs(attributes: list[dict]) -> dict:
    return {item["key"]: otlp_value_to_python(item["value"]) for item in attributes}


async def test_agent_handler_builds_otlp_trace_and_legacy_steps():
    state_manager = TrajectoryTraceStateManager()
    handler = TrajectoryTraceAgentHandler(state_manager)
    trace_id = str(uuid.uuid4())
    state_manager.bind_trace(trace_id, session_id="session-1")

    llm_span = make_span(trace_id, "llm-1")
    tool_span = make_span(trace_id, "tool-1", parent_invoke_id="llm-1")

    await handler.on_llm_start(
        llm_span,
        inputs={"inputs": [{"role": "user", "content": "hello"}]},
        instance_info={"class_name": "test-model"},
    )
    await handler.on_llm_end(
        llm_span,
        outputs={
            "outputs": {
                "role": "assistant",
                "content": "hi",
                "usage": {"prompt_tokens": 2, "completion_tokens": 3},
            }
        },
    )
    await handler.on_plugin_start(
        tool_span,
        inputs={"inputs": {"path": "README.md"}},
        instance_info={"class_name": "read_file"},
    )
    await handler.on_plugin_end(tool_span, outputs={"outputs": {"content": "ok"}})

    trajectory = state_manager.build_trajectory(trace_id, session_id="session-1", finalize=True)

    assert trajectory is not None
    legacy = to_legacy_trajectory(trajectory)
    assert legacy.execution_id == trace_id
    assert trajectory.otlp_trace is not None
    resource_span = trajectory.otlp_trace["resourceSpans"][0]
    resource_attrs = otlp_attrs(resource_span["resource"]["attributes"])
    assert resource_attrs[TRAJECTORY_ID] == trace_id
    assert resource_attrs[TRAJECTORY_SCHEMA_VERSION_ATTR] == TRAJECTORY_SCHEMA_VERSION
    assert resource_attrs[OJ_SESSION_ID] == "session-1"
    assert resource_attrs[TRAJECTORY_END_REASON] == "success"
    assert resource_span["scopeSpans"][0]["scope"] == {
        "name": TRAJECTORY_SCOPE_NAME,
        "version": TRAJECTORY_SCHEMA_VERSION,
    }
    spans = otlp_spans(trajectory)
    assert len(spans) == 2
    llm_attrs = otlp_attrs(spans[0]["attributes"])
    tool_attrs = otlp_attrs(spans[1]["attributes"])
    assert llm_attrs[GEN_AI_OPERATION_NAME] == "chat"
    assert llm_attrs[GEN_AI_INPUT_MESSAGES] == [{"role": "user", "content": "hello"}]
    assert llm_attrs[GEN_AI_OUTPUT_MESSAGES][0]["content"] == "hi"
    assert llm_attrs[GEN_AI_USAGE_INPUT_TOKENS] == 2
    assert tool_attrs[GEN_AI_OPERATION_NAME] == "execute_tool"
    assert tool_attrs[GEN_AI_TOOL_NAME] == "read_file"
    assert tool_attrs[GEN_AI_TOOL_CALL_ARGUMENTS] == {"path": "README.md"}
    assert tool_attrs[GEN_AI_TOOL_CALL_RESULT] == {"content": "ok"}
    assert [step.kind for step in legacy.steps] == ["llm", "tool"]
    assert legacy.steps[0].detail.model == "test-model"
    assert legacy.steps[1].detail.tool_name == "read_file"
    assert legacy.steps[1].meta["parent_llm_call"] == "llm_0001"
    assert legacy.cost == {"input_tokens": 2, "output_tokens": 3}


async def test_agent_handler_captures_rl_token_fields():
    state_manager = TrajectoryTraceStateManager()
    handler = TrajectoryTraceAgentHandler(state_manager)
    trace_id = str(uuid.uuid4())
    state_manager.bind_trace(trace_id, session_id="session-rl")

    span = make_span(trace_id, "llm-1")
    await handler.on_llm_start(
        span,
        inputs={"inputs": [{"role": "user", "content": "hello"}]},
        instance_info={"class_name": "test-model"},
    )
    await handler.on_llm_end(
        span,
        outputs={
            "outputs": {
                "role": "assistant",
                "content": "hi",
                "prompt_token_ids": [1, 2, 3],
                "completion_token_ids": [10, 11],
                "logprobs": [-0.2, -0.3],
                "reward": 0.7,
            }
        },
    )

    trajectory = state_manager.build_trajectory(trace_id, session_id="session-rl", finalize=True)

    assert trajectory is not None
    llm_attrs = otlp_attrs(otlp_spans(trajectory)[0]["attributes"])
    assert llm_attrs[OJ_RL_PROMPT_TOKEN_IDS] == [1, 2, 3]
    assert llm_attrs[OJ_RL_COMPLETION_TOKEN_IDS] == [10, 11]
    assert llm_attrs[OJ_RL_LOGPROBS] == [-0.2, -0.3]
    assert llm_attrs[OJ_RL_REWARD] == 0.7
    step = to_legacy_trajectory(trajectory).steps[0]
    assert step.prompt_token_ids == [1, 2, 3]
    assert step.completion_token_ids == [10, 11]
    assert step.logprobs == [-0.2, -0.3]
    assert step.reward == 0.7


async def test_trace_trajectory_preserves_plain_meta_fields():
    state_manager = TrajectoryTraceStateManager()
    handler = TrajectoryTraceAgentHandler(state_manager)
    trace_id = str(uuid.uuid4())
    state_manager.bind_trace(
        trace_id,
        session_id="session-meta",
        meta={
            "tenant_id": "tenant-1",
            "status": "ok",
            "started_at": 123.4,
            "custom": {"label": "keep"},
        },
    )

    await handler.on_llm_start(
        make_span(trace_id, "llm-1"),
        inputs={"inputs": [{"role": "user", "content": "hello"}]},
        instance_info={"class_name": "test-model"},
    )
    await handler.on_llm_end(
        make_span(trace_id, "llm-1"),
        outputs={"outputs": {"role": "assistant", "content": "hi"}},
    )

    trajectory = state_manager.build_trajectory(trace_id, session_id="session-meta", finalize=True)

    assert trajectory is not None
    resource_attrs = otlp_attrs(trajectory.otlp_trace["resourceSpans"][0]["resource"]["attributes"])
    assert resource_attrs["tenant_id"] == "tenant-1"
    assert resource_attrs["status"] == "ok"
    assert resource_attrs["started_at"] == 123.4
    assert resource_attrs["custom"] == {"label": "keep"}
    legacy = to_legacy_trajectory(trajectory)
    assert legacy.meta["tenant_id"] == "tenant-1"
    assert legacy.meta["status"] == "ok"
    assert legacy.meta["started_at"] == 123.4
    assert legacy.meta["custom"] == {"label": "keep"}


async def test_json_safe_logs_model_dump_failure():
    class BrokenModel:
        def model_dump(self):
            raise TypeError("broken serializer")

        def __str__(self):
            return "broken-model"

    with patch("openjiuwen.agent_evolving.trajectory.span_codec.logger") as mock_logger:
        result = json_safe(BrokenModel())

    assert is_json_safe_fallback(result)
    assert result[JSON_SAFE_FALLBACK_MARKER] is True
    assert result["repr"] == "broken-model"
    assert result["reason"] == "model_dump_failed"
    assert result["error_type"] == "TypeError"
    assert result["error"] == "broken serializer"
    mock_logger.warning.assert_called_once()
    assert "model_dump" in mock_logger.warning.call_args.args[0]


async def test_unwrap_nested_io_prefers_requested_key():
    nested = {"inputs": {"outputs": {"tool_call_id": "tc-1", "ok": True}}}
    assert unwrap_nested_io(nested, preferred="inputs") == {"tool_call_id": "tc-1", "ok": True}
    assert unwrap_nested_io({"outputs": {"reward": 1.0}}, preferred="outputs") == {"reward": 1.0}
    assert unwrap_nested_io({"leaf": 1}, preferred="inputs") == {"leaf": 1}


async def test_project_legacy_steps_warns_on_unclassified_span():
    from openjiuwen.agent_evolving.trajectory.trace_state import (
        TrajectorySpanState,
        TrajectoryTraceState,
    )

    trace_state = TrajectoryTraceState(trace_id="trace-1")
    unclassified = TrajectorySpanState(
        trace_id="trace-1",
        invoke_id="custom-1",
        name="CustomComponent",
        attributes={"gen_ai.operation.name": "custom_op"},
    )
    classified = TrajectorySpanState(
        trace_id="trace-1",
        invoke_id="llm-1",
        name="llm.call",
        attributes={GEN_AI_OPERATION_NAME: "chat"},
    )

    with patch("openjiuwen.agent_evolving.trajectory.trace_state.logger") as mock_logger:
        steps = trace_state.project_legacy_steps([unclassified, classified])

    assert len(steps) == 1
    assert steps[0].kind == "llm"
    mock_logger.warning.assert_called_once()
    assert "unable to classify step kind" in mock_logger.warning.call_args.args[0]
    assert "custom-1" in mock_logger.warning.call_args.args


async def test_state_manager_build_trajectory_respects_max_steps():
    state_manager = TrajectoryTraceStateManager()
    handler = TrajectoryTraceAgentHandler(state_manager)
    trace_id = str(uuid.uuid4())
    state_manager.bind_trace(trace_id, session_id="session-1")

    for idx in range(3):
        invoke_id = f"llm-{idx}"
        await handler.on_llm_start(
            make_span(trace_id, invoke_id),
            inputs={"inputs": [{"role": "user", "content": f"message-{idx}"}]},
            instance_info={"class_name": f"model-{idx}"},
        )
        await handler.on_llm_end(
            make_span(trace_id, invoke_id),
            outputs={"outputs": {"role": "assistant", "content": f"reply-{idx}"}},
        )

    trajectory = state_manager.build_trajectory(
        trace_id,
        session_id="session-1",
        max_steps=2,
        finalize=True,
    )

    assert trajectory is not None
    span_attrs = [otlp_attrs(span["attributes"]) for span in otlp_spans(trajectory)]
    assert [attrs[GEN_AI_REQUEST_MODEL] for attrs in span_attrs] == ["model-1", "model-2"]
    legacy = to_legacy_trajectory(trajectory)
    assert [step.detail.model for step in legacy.steps] == ["model-1", "model-2"]


async def test_workflow_non_llm_tool_stays_out_of_legacy_steps_by_default():
    state_manager = TrajectoryTraceStateManager()
    handler = TrajectoryTraceWorkflowHandler(state_manager)
    trace_id = str(uuid.uuid4())
    state_manager.bind_trace(trace_id, session_id="session-1")

    await handler.on_call_start(
        invoke_id="chain-1",
        metadata={
            "component_id": "chain-1",
            "component_type": "CHAIN",
            "component_name": "chain",
            TRAJECTORY_TRACE_ID: trace_id,
        },
        inputs={"query": "hello"},
    )
    await handler.on_call_done("chain-1", outputs={"ok": True})

    trajectory = state_manager.build_trajectory(trace_id, session_id="session-1", finalize=True)

    assert trajectory is not None
    assert len(otlp_spans(trajectory)) == 1
    assert to_legacy_trajectory(trajectory).steps == []


async def test_explicit_step_kind_projects_workflow_span_to_legacy_steps():
    state_manager = TrajectoryTraceStateManager()
    handler = TrajectoryTraceWorkflowHandler(state_manager)
    trace_id = str(uuid.uuid4())
    state_manager.bind_trace(trace_id, session_id="session-1")

    await handler.on_call_start(
        invoke_id="custom-tool",
        metadata={
            "component_id": "custom-tool",
            "component_type": "CHAIN",
            "component_name": "custom-tool",
            TRAJECTORY_STEP_KIND: "tool",
            TRAJECTORY_TRACE_ID: trace_id,
        },
        inputs={"arg": 1},
    )
    await handler.on_call_done("custom-tool", outputs={"result": 2})

    trajectory = state_manager.build_trajectory(trace_id, session_id="session-1", finalize=True)

    assert trajectory is not None
    legacy = to_legacy_trajectory(trajectory)
    assert len(legacy.steps) == 1
    assert legacy.steps[0].kind == "tool"
    assert legacy.steps[0].detail.tool_name == "custom-tool"


async def test_finalize_marks_unfinished_span_incomplete():
    state_manager = TrajectoryTraceStateManager()
    handler = TrajectoryTraceAgentHandler(state_manager)
    trace_id = str(uuid.uuid4())
    state_manager.bind_trace(trace_id, session_id="session-1")

    await handler.on_plugin_start(
        make_span(trace_id, "tool-1"),
        inputs={"inputs": {"path": "README.md"}},
        instance_info={"class_name": "read_file"},
    )

    trajectory = state_manager.build_trajectory(trace_id, session_id="session-1", finalize=True)

    assert trajectory is not None
    assert to_legacy_trajectory(trajectory).steps[0].meta["attributes"][TRAJECTORY_INCOMPLETE] is True
    assert otlp_spans(trajectory)[0]["endTimeUnixNano"] is not None


async def test_snapshot_states_is_shallow_and_isolates_finalize_mutations():
    from openjiuwen.agent_evolving.trajectory.trace_state import TrajectoryTraceState

    payloads = [{"role": "user", "content": "hello"}]
    live_inputs = {"inputs": payloads}
    live = TrajectoryTraceState(trace_id="trace-1")
    with live.lock:
        state = live.upsert_span("llm-1", name="llm.call")
        state.inputs = live_inputs
        state.attributes[GEN_AI_INPUT_MESSAGES] = payloads
        state.attributes[GEN_AI_OPERATION_NAME] = "chat"

    snapshots = live.snapshot_states(finalize=True)
    snapshot = snapshots[0]

    assert snapshot is not state
    assert snapshot.attributes is not state.attributes
    assert snapshot.inputs is live_inputs
    assert snapshot.attributes[GEN_AI_INPUT_MESSAGES] is payloads
    assert snapshot.status == "incomplete"
    assert snapshot.attributes[TRAJECTORY_INCOMPLETE] is True
    assert state.status != "incomplete"
    assert TRAJECTORY_INCOMPLETE not in state.attributes
    assert state.end_time_unix_nano is None


async def test_state_manager_keeps_concurrent_trace_state_isolated():
    state_manager = TrajectoryTraceStateManager()
    handler = TrajectoryTraceAgentHandler(state_manager)
    trace_id1 = str(uuid.uuid4())
    trace_id2 = str(uuid.uuid4())
    state_manager.bind_trace(trace_id1, session_id="s1")
    state_manager.bind_trace(trace_id2, session_id="s2")

    await handler.on_llm_start(
        make_span(trace_id1, "llm-1"),
        inputs={"inputs": [{"role": "user", "content": "one"}]},
        instance_info={"class_name": "model-one"},
    )
    await handler.on_llm_start(
        make_span(trace_id2, "llm-2"),
        inputs={"inputs": [{"role": "user", "content": "two"}]},
        instance_info={"class_name": "model-two"},
    )

    trajectory1 = state_manager.build_trajectory(trace_id1, session_id="s1", finalize=True)
    trajectory2 = state_manager.build_trajectory(trace_id2, session_id="s2", finalize=True)

    legacy1 = to_legacy_trajectory(trajectory1)
    legacy2 = to_legacy_trajectory(trajectory2)
    assert legacy1.execution_id == trace_id1
    assert legacy2.execution_id == trace_id2
    assert legacy1.steps[0].detail.model == "model-one"
    assert legacy2.steps[0].detail.model == "model-two"


async def test_multiple_registered_handler_managers_filter_to_bound_trace():
    state_manager1 = TrajectoryTraceStateManager()
    state_manager2 = TrajectoryTraceStateManager()
    handler1 = TrajectoryTraceAgentHandler(state_manager1)
    handler2 = TrajectoryTraceAgentHandler(state_manager2)
    trace_id1 = str(uuid.uuid4())
    trace_id2 = str(uuid.uuid4())
    state_manager1.bind_trace(trace_id1, session_id="s1")
    state_manager2.bind_trace(trace_id2, session_id="s2")

    for handler in (handler1, handler2):
        await handler.on_llm_start(
            make_span(trace_id1, "llm-1"),
            inputs={"inputs": [{"role": "user", "content": "one"}]},
            instance_info={"class_name": "model-one"},
        )
        await handler.on_llm_start(
            make_span(trace_id2, "llm-2"),
            inputs={"inputs": [{"role": "user", "content": "two"}]},
            instance_info={"class_name": "model-two"},
        )

    trajectory1 = state_manager1.build_trajectory(trace_id1, session_id="s1", finalize=True)
    trajectory2 = state_manager2.build_trajectory(trace_id2, session_id="s2", finalize=True)

    assert trajectory1 is not None
    assert trajectory2 is not None
    assert state_manager1.build_trajectory(trace_id2, session_id="s2", finalize=True) is None
    assert state_manager2.build_trajectory(trace_id1, session_id="s1", finalize=True) is None
    assert to_legacy_trajectory(trajectory1).steps[0].detail.model == "model-one"
    assert to_legacy_trajectory(trajectory2).steps[0].detail.model == "model-two"


async def test_handler_ignores_unbound_trace_state():
    state_manager = TrajectoryTraceStateManager()
    handler = TrajectoryTraceAgentHandler(state_manager)
    trace_id = str(uuid.uuid4())

    await handler.on_llm_start(
        make_span(trace_id, "llm-1"),
        inputs={"inputs": [{"role": "user", "content": "ignored"}]},
        instance_info={"class_name": "model"},
    )

    assert state_manager.build_trajectory(trace_id, session_id="session-1", finalize=True) is None


async def test_clear_trace_drops_bound_runtime_state():
    state_manager = TrajectoryTraceStateManager()
    handler = TrajectoryTraceAgentHandler(state_manager)
    trace_id = str(uuid.uuid4())
    state_manager.bind_trace(trace_id, session_id="session-1")

    await handler.on_llm_start(
        make_span(trace_id, "llm-1"),
        inputs={"inputs": [{"role": "user", "content": "hello"}]},
        instance_info={"class_name": "model"},
    )
    assert state_manager.build_trajectory(trace_id, session_id="session-1", finalize=True) is not None

    state_manager.clear_trace(trace_id)

    assert not state_manager.is_bound_trace(trace_id)
    assert state_manager.build_trajectory(trace_id, session_id="session-1", finalize=True) is None


async def test_release_trace_keeps_state_until_last_consumer_releases():
    state_manager = TrajectoryTraceStateManager()
    handler = TrajectoryTraceAgentHandler(state_manager)
    trace_id = str(uuid.uuid4())
    state_manager.bind_trace(trace_id, session_id="session-1", consumer_id="rail-1")
    state_manager.bind_trace(trace_id, consumer_id="rail-2")

    await handler.on_llm_start(
        make_span(trace_id, "llm-1"),
        inputs={"inputs": [{"role": "user", "content": "hello"}]},
        instance_info={"class_name": "model"},
    )

    state_manager.release_trace(trace_id, consumer_id="rail-1")

    assert state_manager.is_bound_trace(trace_id)
    assert state_manager.build_trajectory(trace_id, session_id="session-1", finalize=True) is not None

    state_manager.release_trace(trace_id, consumer_id="rail-2")

    assert not state_manager.is_bound_trace(trace_id)
    assert state_manager.build_trajectory(trace_id, session_id="session-1", finalize=True) is None


async def test_agent_span_uses_core_start_and_end_time():
    state_manager = TrajectoryTraceStateManager()
    handler = TrajectoryTraceAgentHandler(state_manager)
    trace_id = str(uuid.uuid4())
    state_manager.bind_trace(trace_id, session_id="session-1")
    span = make_span(trace_id, "llm-1")
    span.start_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    span.end_time = datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)

    await handler.on_llm_start(
        span,
        inputs={"inputs": [{"role": "user", "content": "hello"}]},
        instance_info={"class_name": "model"},
    )
    await handler.on_llm_end(span, outputs={"outputs": {"role": "assistant", "content": "hi"}})

    trajectory = state_manager.build_trajectory(trace_id, session_id="session-1", finalize=True)

    assert trajectory is not None
    otlp_span = otlp_spans(trajectory)[0]
    assert otlp_span["startTimeUnixNano"] == "1767225600000000000"
    assert otlp_span["endTimeUnixNano"] == "1767225601000000000"


async def test_ensure_otlp_handlers_registered_is_idempotent():
    state_manager = ensure_otlp_handlers_registered()
    agent_handler = TracerHandlerRegistry.get_agent_handlers()[TRAJECTORY_TRACE_AGENT_HANDLER_NAME]
    workflow_handler = TracerHandlerRegistry.get_workflow_handlers()[TRAJECTORY_TRACE_WORKFLOW_HANDLER_NAME]

    assert ensure_otlp_handlers_registered() is state_manager
    assert TracerHandlerRegistry.get_agent_handlers()[TRAJECTORY_TRACE_AGENT_HANDLER_NAME] is agent_handler
    assert TracerHandlerRegistry.get_workflow_handlers()[TRAJECTORY_TRACE_WORKFLOW_HANDLER_NAME] is workflow_handler


async def test_bind_trace_warns_when_soft_max_exceeded():
    from unittest.mock import patch

    state_manager = TrajectoryTraceStateManager(max_bound_traces=1)
    with patch("openjiuwen.agent_evolving.trajectory.trace.logger") as mock_logger:
        state_manager.bind_trace("trace-1", session_id="s1")
        mock_logger.warning.assert_not_called()
        state_manager.bind_trace("trace-2", session_id="s2")
        mock_logger.warning.assert_called_once()
        assert "bound traces" in mock_logger.warning.call_args.args[0]
    assert state_manager.bound_trace_count == 2


async def test_clear_process_trajectory_state_drops_bound_traces():
    from openjiuwen.agent_evolving.trajectory.trace import clear_process_trajectory_state

    state_manager = ensure_otlp_handlers_registered()
    trace_id = str(uuid.uuid4())
    state_manager.bind_trace(trace_id, session_id="session-1", consumer_id="test")
    assert state_manager.is_bound_trace(trace_id)

    clear_process_trajectory_state()

    assert state_manager.bound_trace_count == 0
    assert not state_manager.is_bound_trace(trace_id)
    assert ensure_otlp_handlers_registered() is state_manager
