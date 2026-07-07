# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for trajectory types."""

import hashlib

from openjiuwen.agent_evolving.trajectory.semconv import TRAJECTORY_SOURCE
from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    LegacyTrajectory,
    ToolCallDetail,
    Trajectory,
    TrajectoryStep,
    UpdateKey,
    Updates,
    to_legacy_trajectory,
    trajectory_from_legacy,
)
from openjiuwen.core.foundation.llm import AssistantMessage, SystemMessage, UserMessage


def _resource_attribute_map(otlp_trace):
    attributes = otlp_trace["resourceSpans"][0]["resource"]["attributes"]
    return {item["key"]: item["value"].get("stringValue") for item in attributes}


def make_step(kind="llm", detail=None, error=None, **kwargs):
    """Factory for creating TrajectoryStep instances."""
    return TrajectoryStep(
        kind=kind,
        error=error,
        detail=detail,
        meta=kwargs.get("meta", {}),
    )


def make_llm_step(model="gpt-4", messages=None, response=None, tools=None, usage=None, **kwargs):
    """Factory for creating LLM TrajectoryStep."""
    detail = LLMCallDetail(
        model=model,
        messages=messages or [{"role": "user", "content": "hello"}],
        response=response,
        tools=tools,
        usage=usage,
    )
    return make_step(kind="llm", detail=detail, **kwargs)


def make_tool_step(
    tool_name="test_tool", call_args=None, call_result=None, tool_description=None, tool_schema=None, **kwargs
):
    """Factory for creating Tool TrajectoryStep."""
    detail = ToolCallDetail(
        tool_name=tool_name,
        call_args=call_args,
        call_result=call_result,
        tool_description=tool_description,
        tool_schema=tool_schema,
    )
    return make_step(kind="tool", detail=detail, **kwargs)


def make_trajectory(case_id="case1", steps=None, **kwargs):
    """Factory for creating LegacyTrajectory instances."""
    defaults = dict(
        execution_id="exec1",
        case_id=case_id,
        session_id=kwargs.get("session_id", case_id),
        steps=steps or [],
        cost=None,
        source="offline",
    )
    defaults.update(kwargs)
    defaults.pop("otlp_trace", None)
    return LegacyTrajectory(**defaults)


class TestLLMCallDetail:
    """Test LLMCallDetail dataclass."""

    @staticmethod
    def test_minimal_creation():
        """Create with required fields."""
        detail = LLMCallDetail(
            model="gpt-4",
            messages=[{"role": "user", "content": "hello"}],
        )
        assert detail.model == "gpt-4"
        assert len(detail.messages) == 1
        assert detail.response is None
        assert detail.tools is None
        assert detail.usage is None

    @staticmethod
    def test_full_creation():
        """Create with all fields."""
        detail = LLMCallDetail(
            model="gpt-4",
            messages=[{"role": "user", "content": "hello"}],
            response={"role": "assistant", "content": "hi"},
            tools=[{"name": "tool1"}],
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )
        assert detail.model == "gpt-4"
        assert detail.response == {"role": "assistant", "content": "hi"}
        assert detail.tools == [{"name": "tool1"}]
        assert detail.usage == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
        }


class TestToolCallDetail:
    """Test ToolCallDetail dataclass."""

    @staticmethod
    def test_minimal_creation():
        """Create with required fields."""
        detail = ToolCallDetail(tool_name="test_tool")
        assert detail.tool_name == "test_tool"
        assert detail.call_args is None
        assert detail.call_result is None
        assert detail.tool_description is None
        assert detail.tool_schema is None

    @staticmethod
    def test_full_creation():
        """Create with all fields."""
        detail = ToolCallDetail(
            tool_name="test_tool",
            call_args={"arg1": "value1"},
            call_result={"result": "success"},
            tool_description="A test tool",
            tool_schema={"type": "object"},
        )
        assert detail.tool_name == "test_tool"
        assert detail.call_args == {"arg1": "value1"}
        assert detail.call_result == {"result": "success"}
        assert detail.tool_description == "A test tool"
        assert detail.tool_schema == {"type": "object"}


class TestTrajectoryStep:
    """Test TrajectoryStep dataclass."""

    @staticmethod
    def test_creation():
        """Create step with fields."""
        step = make_step(kind="llm")
        assert step.kind == "llm"
        assert step.error is None
        assert step.detail is None
        assert step.meta == {}

    @staticmethod
    def test_llm_step_with_detail():
        """Create LLM step with detail."""
        step = make_llm_step(
            model="gpt-4",
            messages=[{"role": "user", "content": "hello"}],
        )
        assert step.kind == "llm"
        assert step.detail is not None
        assert isinstance(step.detail, LLMCallDetail)
        assert step.detail.model == "gpt-4"

    @staticmethod
    def test_tool_step_with_detail():
        """Create Tool step with detail."""
        step = make_tool_step(
            tool_name="test_tool",
            call_args={"arg": "value"},
            call_result={"result": "success"},
        )
        assert step.kind == "tool"
        assert step.detail is not None
        assert isinstance(step.detail, ToolCallDetail)
        assert step.detail.tool_name == "test_tool"
        assert step.detail.call_args == {"arg": "value"}

    @staticmethod
    def test_step_with_meta():
        """Create step with meta fields."""
        step = make_step(
            kind="llm",
            meta={
                "operator_id": "op1",
                "agent_id": "agent1",
                "span_name": "test_span",
            },
        )
        assert step.meta["operator_id"] == "op1"
        assert step.meta["agent_id"] == "agent1"
        assert step.meta["span_name"] == "test_span"

    @staticmethod
    def test_step_with_rl_fields():
        """Create step with RL post-injection fields."""
        step = TrajectoryStep(
            kind="llm",
            reward=1.0,
            logprobs=[-0.5, -0.3],
            prompt_token_ids=[1, 2, 3],
            completion_token_ids=[101, 102, 103],
        )
        assert step.reward == 1.0
        assert step.logprobs == [-0.5, -0.3]
        assert step.prompt_token_ids == [1, 2, 3]
        assert step.completion_token_ids == [101, 102, 103]

    @staticmethod
    def test_step_with_error():
        """Create step with error."""
        step = make_step(
            kind="tool",
            error={"message": "Tool failed", "code": 500},
        )
        assert step.error == {"message": "Tool failed", "code": 500}


class TestTrajectory:
    """Test Trajectory dataclass."""

    @staticmethod
    def test_minimal_creation():
        """Create a legacy step view with minimal fields."""
        step = make_step(kind="llm")
        traj = make_trajectory(case_id="case1", steps=[step])
        assert traj.execution_id == "exec1"
        assert traj.case_id == "case1"
        assert traj.source == "offline"
        assert len(traj.steps) == 1

    @staticmethod
    def test_creation_with_cost():
        """Create a legacy step view with cost info."""
        traj = make_trajectory(
            case_id="case1",
            steps=[],
            cost={"input_tokens": 100, "output_tokens": 50},
        )
        assert traj.cost == {"input_tokens": 100, "output_tokens": 50}

    @staticmethod
    def test_online_trajectory():
        """Create online legacy trajectory."""
        traj = make_trajectory(
            execution_id="exec-online",
            source="online",
            session_id="session-123",
            case_id=None,
            steps=[],
        )
        assert traj.source == "online"
        assert traj.session_id == "session-123"
        assert traj.case_id is None

    @staticmethod
    def test_trajectory_does_not_inherit_legacy_view():
        """OTLP-first trajectory should stay separate from the legacy view."""
        traj = Trajectory(otlp_trace={"resourceSpans": []})

        assert not isinstance(traj, LegacyTrajectory)
        assert Trajectory.__mro__[1] is object

    @staticmethod
    def test_legacy_conversion_is_explicit():
        """Compatibility conversion should be explicit rather than inherited."""
        step = make_llm_step(model="gpt-4")
        traj = make_trajectory(steps=[step], cost={"input_tokens": 1, "output_tokens": 2})

        legacy = to_legacy_trajectory(traj)
        wrapped = trajectory_from_legacy(legacy, otlp_trace={"resourceSpans": []})

        assert isinstance(legacy, LegacyTrajectory)
        assert not isinstance(wrapped, LegacyTrajectory)
        wrapped_step = to_legacy_trajectory(wrapped).steps[0]
        assert wrapped_step.kind == step.kind
        assert wrapped_step.detail.model == step.detail.model
        assert wrapped_step.detail.messages == step.detail.messages
        assert "resourceSpans" in wrapped.otlp_trace

    @staticmethod
    def test_legacy_conversion_migrates_old_meta_source():
        """Old metadata-backed source should become the dedicated field."""
        traj = LegacyTrajectory(
            execution_id="exec-old-source",
            steps=[],
            meta={"source": "online", "label": "keep"},
        )

        legacy = to_legacy_trajectory(traj)

        assert legacy.source == "online"
        assert legacy.meta == {"label": "keep"}

    @staticmethod
    def test_legacy_to_otlp_writes_trajectory_source_attribute():
        """Legacy source should be stored as an OTLP resource attribute."""
        traj = LegacyTrajectory(
            execution_id="exec-source",
            steps=[],
            source="online",
            meta={"label": "keep"},
        )

        wrapped = trajectory_from_legacy(traj)
        attrs = _resource_attribute_map(wrapped.otlp_trace)

        assert attrs[TRAJECTORY_SOURCE] == "online"
        assert "source" not in attrs

    @staticmethod
    def test_legacy_to_otlp_normalizes_span_trace_id():
        """Legacy execution IDs should be projected to OTLP-compatible trace IDs."""
        traj = LegacyTrajectory(
            execution_id="exec1",
            steps=[make_llm_step(model="gpt-4")],
        )

        wrapped = trajectory_from_legacy(traj)
        span = wrapped.otlp_trace["resourceSpans"][0]["scopeSpans"][0]["spans"][0]

        assert span["traceId"] == hashlib.sha256(b"exec1").hexdigest()[:32]
        assert len(span["traceId"]) == 32
        assert span["traceId"] != "exec1"

    @staticmethod
    def test_legacy_otlp_roundtrip_preserves_rl_fields():
        """RL training fields should survive legacy/OTLP compatibility conversion."""
        step = TrajectoryStep(
            kind="llm",
            detail=LLMCallDetail(
                model="gpt-4",
                messages=[{"role": "user", "content": "hello"}],
                response={"role": "assistant", "content": "hi"},
            ),
            reward=0.7,
            prompt_token_ids=[1, 2, 3],
            completion_token_ids=[10, 11],
            logprobs=[-0.2, -0.3],
        )
        traj = LegacyTrajectory(execution_id="exec-rl", steps=[step])

        wrapped = trajectory_from_legacy(traj)
        roundtrip = to_legacy_trajectory(wrapped).steps[0]

        assert roundtrip.reward == 0.7
        assert roundtrip.prompt_token_ids == [1, 2, 3]
        assert roundtrip.completion_token_ids == [10, 11]
        assert roundtrip.logprobs == [-0.2, -0.3]

    @staticmethod
    def test_legacy_otlp_roundtrip_preserves_tool_reward():
        """Tool step rewards should survive legacy/OTLP compatibility conversion."""
        step = TrajectoryStep(
            kind="tool",
            detail=ToolCallDetail(
                tool_name="search",
                call_args={"query": "hello"},
                call_result={"ok": True},
            ),
            reward=0.5,
        )
        traj = LegacyTrajectory(execution_id="exec-tool-rl", steps=[step])

        wrapped = trajectory_from_legacy(traj)
        roundtrip = to_legacy_trajectory(wrapped).steps[0]

        assert roundtrip.kind == "tool"
        assert roundtrip.reward == 0.5

    @staticmethod
    def test_legacy_conversion_deep_copies_mutable_fields():
        """Compatibility conversion should detach mutable nested structures."""
        step = make_llm_step(
            model="gpt-4",
            messages=[{"role": "user", "content": "hello"}],
            response={"role": "assistant", "content": "hi"},
            usage={"prompt_tokens": 1, "completion_tokens": 2},
            meta={"attributes": {"invoke_id": "llm-1"}},
        )
        traj = make_trajectory(
            steps=[step],
            cost={"input_tokens": 1, "output_tokens": 2},
            meta={"labels": ["online"]},
        )
        otlp_trace = {"resourceSpans": [{"resource": {"attributes": []}}]}

        legacy = to_legacy_trajectory(traj)
        legacy.steps[0].detail.messages[0]["content"] = "changed"
        legacy.steps[0].meta["attributes"]["invoke_id"] = "changed"
        legacy.cost["input_tokens"] = 99
        legacy.meta["labels"].append("changed")

        assert traj.steps[0].detail.messages[0]["content"] == "hello"
        assert traj.steps[0].meta["attributes"]["invoke_id"] == "llm-1"
        assert traj.cost["input_tokens"] == 1
        assert traj.meta["labels"] == ["online"]

        wrapped = trajectory_from_legacy(legacy, otlp_trace=otlp_trace)
        wrapped_legacy = to_legacy_trajectory(wrapped)
        wrapped_legacy.steps[0].detail.messages[0]["content"] = "wrapped"
        wrapped_legacy.steps[0].meta["attributes"]["invoke_id"] = "wrapped"
        wrapped_legacy.cost["output_tokens"] = 88
        wrapped_legacy.meta["labels"].append("wrapped")
        wrapped.otlp_trace["resourceSpans"][0]["resource"]["attributes"].append(
            {"key": "changed", "value": {"stringValue": "yes"}}
        )

        assert legacy.steps[0].detail.messages[0]["content"] == "changed"
        assert legacy.steps[0].meta["attributes"]["invoke_id"] == "changed"
        assert legacy.cost["output_tokens"] == 2
        assert legacy.meta["labels"] == ["online", "changed"]
        assert otlp_trace == {"resourceSpans": [{"resource": {"attributes": []}}]}

    @staticmethod
    def test_trajectory_does_not_expose_legacy_message_view():
        """Step-view message conversion should stay on LegacyTrajectory."""
        traj = Trajectory(otlp_trace={"resourceSpans": []})

        assert not hasattr(traj, "to_messages")

    @staticmethod
    def test_legacy_to_messages_normalizes_message_objects():
        """Runtime callback message objects should be preserved as dict messages."""
        traj = make_trajectory(
            steps=[
                make_llm_step(
                    messages=[
                        SystemMessage(content="system prompt"),
                        UserMessage(content="user request"),
                        AssistantMessage(
                            content="I'll read the skill",
                            tool_calls=[
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "name": "read_file",
                                    "arguments": '{"file_path": "/skills/demo/SKILL.md"}',
                                }
                            ],
                        ),
                    ],
                    response=AssistantMessage(content="done"),
                )
            ]
        )

        messages = to_legacy_trajectory(traj).to_messages()

        assert [message["role"] for message in messages] == [
            "system",
            "user",
            "assistant",
            "assistant",
        ]
        assert messages[2]["tool_calls"][0]["name"] == "read_file"
        assert messages[2]["tool_calls"][0]["arguments"] == '{"file_path": "/skills/demo/SKILL.md"}'


class TestUpdateKey:
    """Test UpdateKey type alias."""

    @staticmethod
    def test_tuple_creation():
        """UpdateKey is a tuple."""
        key: UpdateKey = ("op1", "system_prompt")
        assert key == ("op1", "system_prompt")
        assert key[0] == "op1"
        assert key[1] == "system_prompt"


class TestUpdates:
    """Test Updates type alias."""

    @staticmethod
    def test_dict_creation():
        """Updates is a dict."""
        updates: Updates = {
            ("op1", "system_prompt"): "new prompt",
            ("op1", "user_prompt"): "new user",
        }
        assert ("op1", "system_prompt") in updates


class TestNoFirstPhaseNormalizedTrajectoryModel:
    """Guardrails for the passive first phase trajectory scope."""

    @staticmethod
    def test_no_normalized_public_read_model_types_are_added():
        import openjiuwen.agent_evolving.trajectory.types as trajectory_types

        forbidden_names = [
            "LLMCallNode",
            "ToolIntentNode",
            "ToolExecutionNode",
            "MessageNode",
            "TrajectoryEdge",
            "TrajectoryRun",
            "TrajectoryView",
        ]
        for name in forbidden_names:
            assert not hasattr(trajectory_types, name)

    @staticmethod
    def test_trajectory_keeps_existing_step_only_shape():
        from openjiuwen.agent_evolving.trajectory.types import Trajectory

        trajectory = Trajectory(otlp_trace={"resourceSpans": []})

        assert not hasattr(trajectory, "schema_version")
        assert not hasattr(trajectory, "read_model")
        assert not hasattr(trajectory, "steps")
