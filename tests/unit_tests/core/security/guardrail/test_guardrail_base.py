# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for guardrail framework base class and backend integration.
"""

import pytest

from openjiuwen.core.security.guardrail import (
    BaseGuardrail,
    GuardrailBackend,
    GuardrailResult,
    RiskAssessment,
    RiskLevel,
)


class TestBaseGuardrail:
    """Tests for BaseGuardrail abstract base class."""

    @staticmethod
    def test_init_without_params():
        """Test BaseGuardrail initialization without parameters."""
        guardrail = CustomTestGuardrail()

        # Verify initial state through public interface
        assert guardrail.listen_events == ["test_event"]

    @staticmethod
    def test_init_with_custom_events():
        """Test BaseGuardrail initialization with custom events."""
        guardrail = CustomTestGuardrail(events=["event1", "event2"])

        assert guardrail.listen_events == ["event1", "event2"]

    @staticmethod
    def test_init_with_backend(mock_backend):
        """Test BaseGuardrail initialization with backend."""
        guardrail = CustomTestGuardrail(backend=mock_backend)

        # Verify backend is set by checking it works
        assert guardrail.get_backend() is mock_backend

    @staticmethod
    def test_init_with_events_and_backend(mock_backend):
        """Test BaseGuardrail initialization with both events and backend."""
        guardrail = CustomTestGuardrail(
            events=["custom_event"],
            backend=mock_backend
        )

        assert guardrail.listen_events == ["custom_event"]
        assert guardrail.get_backend() is mock_backend

    @staticmethod
    def test_listen_events_returns_copy():
        """Test listen_events property returns a copy."""
        guardrail = CustomTestGuardrail()
        events1 = guardrail.listen_events
        events2 = guardrail.listen_events

        assert events1 is not events2
        assert events1 == events2

    @staticmethod
    def test_with_events_chaining():
        """Test with_events() returns self for method chaining."""
        guardrail = CustomTestGuardrail()
        result = guardrail.with_events(["new_event"])

        assert result is guardrail
        assert guardrail.listen_events == ["new_event"]

    @staticmethod
    def test_set_backend_chaining(mock_backend):
        """Test set_backend() returns self for method chaining."""
        guardrail = CustomTestGuardrail()
        result = guardrail.set_backend(mock_backend)

        assert result is guardrail
        assert guardrail.get_backend() is mock_backend

    @staticmethod
    def test_combined_chaining(mock_backend):
        """Test combined with_events() and set_backend() chaining."""
        guardrail = CustomTestGuardrail()
        result = (guardrail
                  .with_events(["custom_event"])
                  .set_backend(mock_backend))

        assert result is guardrail
        assert guardrail.listen_events == ["custom_event"]
        assert guardrail.get_backend() is mock_backend

    @staticmethod
    def test_events_immutable_after_init():
        """Test that events list is copied and not shared."""
        original_events = ["event1", "event2"]
        guardrail = CustomTestGuardrail(events=original_events)

        # Modify original list
        original_events.append("event3")

        # Guardrail events should not be affected
        assert guardrail.listen_events == ["event1", "event2"]

    @staticmethod
    def test_default_events_used_when_none_provided():
        """Test DEFAULT_EVENTS is used when no events provided."""
        guardrail = CustomTestGuardrail()

        assert guardrail.listen_events == ["test_event"]

    @staticmethod
    def test_empty_events_when_no_default():
        """Test empty events when DEFAULT_EVENTS is empty."""

        class NoDefaultEventsGuardrail(BaseGuardrail):
            DEFAULT_EVENTS = []

            async def detect(self, event_name, **event_data):
                return GuardrailResult.pass_()

        guardrail = NoDefaultEventsGuardrail()

        assert guardrail.listen_events == []


class TestBaseGuardrailDetect:
    """Tests for BaseGuardrail.detect() method."""

    @pytest.mark.asyncio
    async def test_detect_without_backend_raises(self):
        """Test detect() without backend raises ValueError in base class."""
        # Create a guardrail that directly calls super().detect()
        # instead of checking for backend first
        guardrail = DirectBaseCallGuardrail()

        with pytest.raises(ValueError) as exc_info:
            await guardrail.detect("test_event", data={})

        assert "No backend configured" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_detect_with_safe_backend(self, mock_backend):
        """Test detect() with backend returning safe assessment."""
        guardrail = CustomTestGuardrail(backend=mock_backend)

        result = await guardrail.detect("test_event", data={"test": "value"})

        assert result.is_safe is True
        assert result.risk_level == RiskLevel.SAFE

    @pytest.mark.asyncio
    async def test_detect_with_risky_backend(self, risky_backend):
        """Test detect() with backend returning risky assessment."""
        guardrail = CustomTestGuardrail(backend=risky_backend)

        result = await guardrail.detect("test_event", data={"test": "value"})

        assert result.is_safe is False
        assert result.risk_level == RiskLevel.HIGH
        assert result.risk_type == "test_risk"

    @pytest.mark.asyncio
    async def test_detect_passes_event_data_to_backend(self, mock_backend):
        """Test detect() passes event data to backend analyze()."""
        guardrail = DataCaptureGuardrail(backend=mock_backend)

        await guardrail.detect("test_event", text="test content", user_id="123")

        assert guardrail.captured_data is not None
        assert "text" in guardrail.captured_data
        assert "test content" in guardrail.captured_data["text"]
        assert "user_id" in guardrail.captured_data
        assert guardrail.captured_data["user_id"] == "123"


class TestBaseGuardrailRegistration:
    """Tests for BaseGuardrail registration with callback framework."""

    @pytest.mark.asyncio
    async def test_register_with_framework(self, framework):
        """Test register() with callback framework."""
        guardrail = CustomTestGuardrail()

        await guardrail.register(framework)

        # Check callbacks are registered
        callbacks = framework.list_callbacks("test_event")
        assert len(callbacks) == 1

    @pytest.mark.asyncio
    async def test_register_sets_framework_reference(self, framework):
        """Test register() sets framework reference."""
        guardrail = CustomTestGuardrail()

        await guardrail.register(framework)

        # Verify framework is set by checking unregister works
        await guardrail.unregister()
        callbacks = framework.list_callbacks("test_event")
        assert len(callbacks) == 0

    @pytest.mark.asyncio
    async def test_register_tracks_registered_events(self, framework):
        """Test register() tracks registered events."""
        guardrail = CustomTestGuardrail(events=["event1", "event2"])

        await guardrail.register(framework)

        # Verify events are tracked by checking callbacks are registered
        callbacks1 = framework.list_callbacks("event1")
        callbacks2 = framework.list_callbacks("event2")
        assert len(callbacks1) == 1
        assert len(callbacks2) == 1

    @pytest.mark.asyncio
    async def test_unregister_removes_callbacks(self, framework):
        """Test unregister() removes registered callbacks."""
        guardrail = CustomTestGuardrail()
        await guardrail.register(framework)

        await guardrail.unregister()

        callbacks = framework.list_callbacks("test_event")
        assert len(callbacks) == 0

    @pytest.mark.asyncio
    async def test_unregister_clears_registered_events(self, framework):
        """Test unregister() clears registered events."""
        guardrail = CustomTestGuardrail()
        await guardrail.register(framework)

        await guardrail.unregister()

        # After unregister, callbacks should be removed
        callbacks = framework.list_callbacks("test_event")
        assert len(callbacks) == 0

    @pytest.mark.asyncio
    async def test_unregister_without_framework(self):
        """Test unregister() without framework does not error."""
        guardrail = CustomTestGuardrail()

        # Should not raise
        await guardrail.unregister()

    @pytest.mark.asyncio
    async def test_unregister_with_unregistered_callback(self, framework):
        """Test unregister() handles unregistered callback gracefully."""
        guardrail = CustomTestGuardrail()
        # Manually set up without proper registration
        guardrail.set_framework(framework)
        guardrail.add_registered_event("test_event")

        # Should not raise even if callback doesn't exist
        await guardrail.unregister()

    @pytest.mark.asyncio
    async def test_multiple_guards_registration(self, framework):
        """Test multiple guardrails can be registered."""
        guardrail1 = CustomTestGuardrail(events=["event1"])
        guardrail2 = CustomTestGuardrail(events=["event2"])

        await guardrail1.register(framework)
        await guardrail2.register(framework)

        callbacks1 = framework.list_callbacks("event1")
        callbacks2 = framework.list_callbacks("event2")

        assert len(callbacks1) == 1
        assert len(callbacks2) == 1


class TestGuardrailBackend:
    """Tests for GuardrailBackend abstract class."""

    @staticmethod
    def test_backend_is_abstract():
        """Test GuardrailBackend cannot be instantiated directly."""
        with pytest.raises(TypeError):
            GuardrailBackend()

    @staticmethod
    def test_backend_subclass_must_implement_analyze():
        """Test subclass must implement analyze() method."""

        class IncompleteBackend(GuardrailBackend):
            pass

        with pytest.raises(TypeError):
            IncompleteBackend()

    @staticmethod
    def test_backend_subclass_with_analyze():
        """Test subclass with analyze() can be instantiated."""

        class CompleteBackend(GuardrailBackend):
            async def analyze(self, data):
                return RiskAssessment(
                    has_risk=False,
                    risk_level=RiskLevel.SAFE
                )

        backend = CompleteBackend()
        assert backend is not None

    @pytest.mark.asyncio
    async def test_backend_analyze_receives_data(self):
        """Test backend analyze() receives event data."""
        received_data = None

        class DataCaptureBackend(GuardrailBackend):
            async def analyze(self, data):
                nonlocal received_data
                received_data = data
                return RiskAssessment(
                    has_risk=False,
                    risk_level=RiskLevel.SAFE
                )

        backend = DataCaptureBackend()
        test_data = {"text": "test", "user_id": "123"}

        await backend.analyze(test_data)

        assert received_data == test_data

    @pytest.mark.asyncio
    async def test_backend_analyze_exception_propagates(self):
        """Test backend analyze() exception propagates up."""
        # Note: The guardrail detect method does NOT catch exceptions from backend.
        # This is intentional - callers should handle exceptions if needed.

        class FailingBackend(GuardrailBackend):
            async def analyze(self, data):
                raise RuntimeError("Detection failed")

        backend = FailingBackend()
        guardrail = CustomTestGuardrail(backend=backend)

        # Exception should propagate (not be caught by guardrail)
        with pytest.raises(RuntimeError) as exc_info:
            await guardrail.detect("test_event", data={})

        assert str(exc_info.value) == "Detection failed"


# Helper classes for testing
class CustomTestGuardrail(BaseGuardrail):
    """Test guardrail implementation."""
    DEFAULT_EVENTS = ["test_event"]

    def __init__(self, backend=None, events=None):
        super().__init__(backend=backend, events=events)
        self._test_backend = None
        self._test_framework = None
        self._test_registered_events = []

    def get_backend(self):
        """Get the backend for testing."""
        return self._backend

    def set_framework(self, framework):
        """Set framework for testing."""
        self._framework = framework

    def add_registered_event(self, event):
        """Add registered event for testing."""
        self._registered_events.append(event)

    async def detect(self, event_name, **event_data):
        if self._backend:
            return await super().detect(event_name, **event_data)
        return GuardrailResult.pass_()


class DataCaptureGuardrail(BaseGuardrail):
    """Test guardrail that captures event data."""
    DEFAULT_EVENTS = ["test_event"]

    def __init__(self, backend=None):
        super().__init__(backend=backend)
        self.captured_data = None

    async def detect(self, event_name, **event_data):
        self.captured_data = event_data
        if self._backend:
            return await super().detect(event_name, **event_data)
        return GuardrailResult.pass_()


class DirectBaseCallGuardrail(BaseGuardrail):
    """Test guardrail that directly calls base detect()."""
    DEFAULT_EVENTS = ["test_event"]

    async def detect(self, event_name, **event_data):
        # Directly call base class detect without checking backend
        return await BaseGuardrail.detect(self, event_name, **event_data)
