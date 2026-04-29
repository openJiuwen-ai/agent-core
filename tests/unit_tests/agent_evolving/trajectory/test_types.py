# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for trajectory types."""

from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    ToolCallDetail,
    Trajectory,
    TrajectoryStep,
    UpdateKey,
    Updates,
)


def make_step(kind="llm", detail=None, error=None, **kwargs):
    """Factory for creating TrajectoryStep instances."""
    return TrajectoryStep(
        kind=kind,
        error=error,
        detail=detail,
        meta=kwargs.get("meta", {}),
    )


def make_llm_step(
    model="gpt-4",
    messages=None,
    response=None,
    tools=None,
    usage=None,
    **kwargs
):
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
    tool_name="test_tool",
    call_args=None,
    call_result=None,
    tool_description=None,
    tool_schema=None,
    **kwargs
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
    """Factory for creating Trajectory instances."""
    defaults = dict(
        execution_id="exec1",
        source="offline",
        case_id=case_id,
        session_id=kwargs.get("session_id", case_id),
        steps=steps or [],
        cost=None,
    )
    defaults.update(kwargs)
    return Trajectory(**defaults)


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
        """Create with minimal fields."""
        step = make_step(kind="llm")
        traj = make_trajectory(case_id="case1", steps=[step])
        assert traj.execution_id == "exec1"
        assert traj.case_id == "case1"
        assert traj.source == "offline"
        assert len(traj.steps) == 1

    @staticmethod
    def test_creation_with_cost():
        """Create with cost info."""
        traj = make_trajectory(
            case_id="case1",
            steps=[],
            cost={"input_tokens": 100, "output_tokens": 50},
        )
        assert traj.cost == {"input_tokens": 100, "output_tokens": 50}

    @staticmethod
    def test_online_trajectory():
        """Create online trajectory."""
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
