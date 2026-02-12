# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for guardrail framework builtin guardrail implementations.

Note: Currently only UserInputGuardrail is implemented.
Other guardrail tests will be added after the event triggering
mechanism is finalized.
"""

import pytest

from openjiuwen.core.security.guardrail import (
    GuardrailBackend,
    RiskAssessment,
    RiskLevel,
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
