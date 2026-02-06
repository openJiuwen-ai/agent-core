# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for guardrail framework builtin guardrail implementations.
"""

import pytest

from openjiuwen.core.security.guardrail import (
    GuardrailBackend,
    LLMInputGuardrail,
    LLMOutputGuardrail,
    PlanningGuardrail,
    RiskAssessment,
    RiskLevel,
    ToolGuardrail,
    UserInputGuardrail,
)


class TestUserInputGuardrail:
    """Tests for UserInputGuardrail."""

    @staticmethod
    def test_default_events():
        """Test UserInputGuardrail has correct default events."""
        guardrail = UserInputGuardrail()
        assert guardrail.listen_events == ["user_input"]

    @pytest.mark.asyncio
    async def test_empty_text_returns_safe(self):
        """Test empty text returns safe result."""
        guardrail = UserInputGuardrail()

        result = await guardrail.detect("user_input", text="")

        assert result.is_safe is True
        assert result.details == {"empty_input": True}

    @pytest.mark.asyncio
    async def test_non_string_text_returns_safe(self):
        """Test non-string text returns safe result."""
        guardrail = UserInputGuardrail()

        result = await guardrail.detect("user_input", text=123)

        assert result.is_safe is True
        assert result.details == {"empty_input": True}

    @pytest.mark.asyncio
    async def test_valid_text_without_backend(self):
        """Test valid text returns safe result without backend."""
        guardrail = UserInputGuardrail()

        result = await guardrail.detect("user_input", text="Hello, world!")

        assert result.is_safe is True

    @staticmethod
    def test_listen_events_copy():
        """Test listen_events returns a copy."""
        guardrail = UserInputGuardrail()
        events1 = guardrail.listen_events
        events2 = guardrail.listen_events

        assert events1 is not events2
        assert events1 == events2


class TestLLMInputGuardrail:
    """Tests for LLMInputGuardrail."""

    @staticmethod
    def test_default_events():
        """Test LLMInputGuardrail has correct default events."""
        guardrail = LLMInputGuardrail()
        assert guardrail.listen_events == ["llm_input"]

    @pytest.mark.asyncio
    async def test_empty_prompt_and_messages(self):
        """Test empty prompt and messages returns safe."""
        guardrail = LLMInputGuardrail()

        result = await guardrail.detect("llm_input", prompt="", messages=[])

        assert result.is_safe is True
        assert result.details == {"empty_input": True}

    @pytest.mark.asyncio
    async def test_valid_prompt_without_backend(self):
        """Test valid prompt returns safe result without backend."""
        guardrail = LLMInputGuardrail()

        result = await guardrail.detect(
            "llm_input",
            prompt="Hello, world!",
            messages=[{"role": "user", "content": "Hello"}],
            model="gpt-4"
        )

        assert result.is_safe is True

    @pytest.mark.asyncio
    async def test_only_prompt(self):
        """Test with only prompt provided."""
        guardrail = LLMInputGuardrail()

        result = await guardrail.detect("llm_input", prompt="Hello, world!")

        assert result.is_safe is True

    @pytest.mark.asyncio
    async def test_only_messages(self):
        """Test with only messages provided."""
        guardrail = LLMInputGuardrail()
        messages = [{"role": "user", "content": "Hi"}]

        result = await guardrail.detect("llm_input", messages=messages)

        assert result.is_safe is True


class TestLLMOutputGuardrail:
    """Tests for LLMOutputGuardrail."""

    @staticmethod
    def test_default_events():
        """Test LLMOutputGuardrail has correct default events."""
        guardrail = LLMOutputGuardrail()
        assert guardrail.listen_events == ["llm_output"]

    @pytest.mark.asyncio
    async def test_empty_content_and_tool_calls(self):
        """Test empty content and tool_calls returns safe."""
        guardrail = LLMOutputGuardrail()

        result = await guardrail.detect("llm_output", content="", tool_calls=[])

        assert result.is_safe is True
        assert result.details == {"empty_output": True}

    @pytest.mark.asyncio
    async def test_valid_output_without_backend(self):
        """Test valid output returns safe result without backend."""
        guardrail = LLMOutputGuardrail()

        result = await guardrail.detect(
            "llm_output",
            content="Hello, world!",
            tool_calls=[],
            model="gpt-4"
        )

        assert result.is_safe is True

    @pytest.mark.asyncio
    async def test_only_content(self):
        """Test with only content provided."""
        guardrail = LLMOutputGuardrail()

        result = await guardrail.detect("llm_output", content="Hello, world!")

        assert result.is_safe is True

    @pytest.mark.asyncio
    async def test_only_tool_calls(self):
        """Test with only tool_calls provided."""
        guardrail = LLMOutputGuardrail()
        tool_calls = [{"name": "search", "args": {"query": "test"}}]

        result = await guardrail.detect("llm_output", tool_calls=tool_calls)

        assert result.is_safe is True


class TestToolGuardrail:
    """Tests for ToolGuardrail."""

    @staticmethod
    def test_default_events():
        """Test ToolGuardrail has correct default events."""
        guardrail = ToolGuardrail()
        assert guardrail.listen_events == ["tool_input", "tool_output"]

    @pytest.mark.asyncio
    async def test_no_tool_name_returns_safe(self):
        """Test missing tool_name returns safe result."""
        guardrail = ToolGuardrail()

        result = await guardrail.detect("tool_input", tool_input={})

        assert result.is_safe is True
        assert result.details == {"no_tool_name": True}

    @pytest.mark.asyncio
    async def test_tool_input_event(self):
        """Test tool_input event detection."""
        guardrail = ToolGuardrail()

        result = await guardrail.detect(
            "tool_input",
            tool_name="search",
            tool_input={"query": "weather"},
            caller_context={"user_id": "user123"}
        )

        assert result.is_safe is True

    @pytest.mark.asyncio
    async def test_tool_output_event(self):
        """Test tool_output event detection."""
        guardrail = ToolGuardrail()

        result = await guardrail.detect(
            "tool_output",
            tool_name="search",
            tool_output={"result": "Sunny"},
            execution_time=0.5
        )

        assert result.is_safe is True

    @staticmethod
    def test_custom_events():
        """Test guardrail with custom events."""
        guardrail = ToolGuardrail(events=["custom_event"])

        assert guardrail.listen_events == ["custom_event"]


class TestPlanningGuardrail:
    """Tests for PlanningGuardrail."""

    @staticmethod
    def test_default_events():
        """Test PlanningGuardrail has correct default events."""
        guardrail = PlanningGuardrail()
        assert guardrail.listen_events == ["planning_start", "planning_complete"]

    @pytest.mark.asyncio
    async def test_empty_task_at_start(self):
        """Test empty task at planning start."""
        guardrail = PlanningGuardrail()

        result = await guardrail.detect("planning_start", task="")

        assert result.is_safe is True
        assert result.details == {"empty_task": True}

    @pytest.mark.asyncio
    async def test_empty_plan_and_steps_at_complete(self):
        """Test empty plan and steps at planning complete."""
        guardrail = PlanningGuardrail()

        result = await guardrail.detect("planning_complete", plan=None, steps=[])

        assert result.is_safe is True
        assert result.details == {"empty_plan": True}

    @pytest.mark.asyncio
    async def test_valid_planning_start(self):
        """Test valid planning start."""
        guardrail = PlanningGuardrail()

        result = await guardrail.detect(
            "planning_start",
            task="Find Italian restaurant nearby"
        )

        assert result.is_safe is True

    @pytest.mark.asyncio
    async def test_valid_planning_complete(self):
        """Test valid planning complete."""
        guardrail = PlanningGuardrail()

        result = await guardrail.detect(
            "planning_complete",
            plan={"steps": [{"action": "search", "params": {"query": "Italian"}}]},
            steps=[{"action": "search", "params": {"query": "Italian"}}]
        )

        assert result.is_safe is True

    @pytest.mark.asyncio
    async def test_plan_without_steps(self):
        """Test plan without steps."""
        guardrail = PlanningGuardrail()
        plan = {"strategy": "sequential"}

        result = await guardrail.detect("planning_complete", plan=plan, steps=[])

        assert result.is_safe is True

    @pytest.mark.asyncio
    async def test_steps_without_plan(self):
        """Test steps without plan."""
        guardrail = PlanningGuardrail()
        steps = [{"action": "test"}]

        result = await guardrail.detect("planning_complete", plan=None, steps=steps)

        assert result.is_safe is True


class TestBuiltinGuardrailsChaining:
    """Tests for builtin guardrail chaining capabilities."""

    @staticmethod
    def test_with_events_chaining():
        """Test with_events() returns self for chaining."""
        guardrail = UserInputGuardrail()
        result = guardrail.with_events(["custom_event"])

        assert result is guardrail
        assert guardrail.listen_events == ["custom_event"]

    @staticmethod
    def test_set_backend_chaining():
        """Test set_backend() returns self for chaining."""
        guardrail = UserInputGuardrail()

        class DummyBackend(GuardrailBackend):
            async def analyze(self, data):
                return RiskAssessment(has_risk=False, risk_level=RiskLevel.SAFE)

        backend = DummyBackend()
        result = guardrail.set_backend(backend)

        assert result is guardrail
        # Verify backend is set by checking detection works
        assert guardrail.get_backend() is backend

    @staticmethod
    def test_combined_chaining():
        """Test combined with_events() and set_backend() chaining."""
        guardrail = UserInputGuardrail()

        class DummyBackend(GuardrailBackend):
            async def analyze(self, data):
                return RiskAssessment(has_risk=False, risk_level=RiskLevel.SAFE)

        backend = DummyBackend()
        result = (guardrail
                  .with_events(["custom_user_input"])
                  .set_backend(backend))

        assert result is guardrail
        assert guardrail.listen_events == ["custom_user_input"]
        assert guardrail.get_backend() is backend
