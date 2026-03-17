# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for guardrail framework base class and backend integration.
"""

import pytest

from openjiuwen.core.security.guardrail import (
    BaseGuardrail,
    GuardrailBackend,
    GuardrailContext,
    GuardrailContentType,
    GuardrailError,
    GuardrailResult,
    RiskAssessment,
    RiskLevel,
)
from openjiuwen.core.common.exception.codes import StatusCode


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

            def extract_context(self, event, *args, **kwargs):
                return GuardrailContext(
                    content_type=GuardrailContentType.TEXT,
                    content="",
                    event=str(event),
                    metadata={}
                )

            async def detect(self, event_name, *args, **kwargs):
                return GuardrailResult.pass_()

        guardrail = NoDefaultEventsGuardrail()

        assert guardrail.listen_events == []


class TestBaseGuardrailDetect:
    """Tests for BaseGuardrail.detect() method."""

    @pytest.mark.asyncio
    async def test_detect_without_backend_raises(self):
        """Test _detect_callback() without backend raises ValueError in base class."""
        # Create a guardrail that directly calls super().detect()
        # instead of checking for backend first
        guardrail = DirectBaseCallGuardrail()

        with pytest.raises(ValueError) as exc_info:
            await guardrail.call_detect_callback("test_event", data={})

        assert "No backend configured" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_detect_with_safe_backend(self, mock_backend):
        """Test detect() with backend returning safe assessment."""
        guardrail = CustomTestGuardrail(backend=mock_backend)

        result = await guardrail.detect("test_event", data={"test": "value"})

        assert result is not None
        assert result.is_safe is True
        assert result.risk_level == RiskLevel.SAFE

    @pytest.mark.asyncio
    async def test_detect_with_risky_backend(self, risky_backend):
        """Test detect() with backend returning risky assessment."""
        guardrail = CustomTestGuardrail(backend=risky_backend)

        result = await guardrail.detect("test_event", data={"test": "value"})

        assert result is not None
        assert result.is_safe is False
        assert result.risk_level == RiskLevel.HIGH
        assert result.risk_type == "test_risk"

    @pytest.mark.asyncio
    async def test_detect_passes_kwargs_to_backend(self, mock_backend):
        """Test detect() passes event data to backend analyze()."""
        guardrail = DataCaptureGuardrail(backend=mock_backend)

        result = await guardrail.detect("test_event", text="test content", user_id="123")

        assert result is not None
        assert guardrail.captured_data is not None
        assert "text" in guardrail.captured_data
        assert guardrail.captured_data["text"] == "test content"
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

    @pytest.mark.asyncio
    async def test_get_registered_events_returns_copy(self, framework):
        """Test get_registered_events() returns a copy."""
        guardrail = CustomTestGuardrail(events=["event1", "event2"])
        await guardrail.register(framework)

        events1 = guardrail.get_registered_events()
        events2 = guardrail.get_registered_events()

        assert events1 is not events2
        assert events1 == events2
        assert set(events1) == {"event1", "event2"}

        await guardrail.unregister()

    @pytest.mark.asyncio
    async def test_get_registered_events_empty_before_registration(self):
        """Test get_registered_events() returns empty list before registration."""
        guardrail = CustomTestGuardrail()

        assert guardrail.get_registered_events() == []

    @pytest.mark.asyncio
    async def test_get_registered_events_after_unregister(self, framework):
        """Test get_registered_events() returns empty list after unregister."""
        guardrail = CustomTestGuardrail()
        await guardrail.register(framework)
        await guardrail.unregister()

        assert guardrail.get_registered_events() == []

    @pytest.mark.asyncio
    async def test_is_event_registered_true(self, framework):
        """Test is_event_registered() returns True for registered event."""
        guardrail = CustomTestGuardrail(events=["event1", "event2"])
        await guardrail.register(framework)

        assert guardrail.is_event_registered("event1") is True
        assert guardrail.is_event_registered("event2") is True

        await guardrail.unregister()

    @pytest.mark.asyncio
    async def test_is_event_registered_false(self, framework):
        """Test is_event_registered() returns False for unregistered event."""
        guardrail = CustomTestGuardrail(events=["event1"])
        await guardrail.register(framework)

        assert guardrail.is_event_registered("event1") is True
        assert guardrail.is_event_registered("not_registered") is False

        await guardrail.unregister()

    @pytest.mark.asyncio
    async def test_is_event_registered_before_registration(self):
        """Test is_event_registered() returns False before registration."""
        guardrail = CustomTestGuardrail(events=["event1"])

        assert guardrail.is_event_registered("event1") is False


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
            await guardrail.call_detect_callback("test_event", data={})

        assert str(exc_info.value) == "Detection failed"


class TestDetectCallback:
    """Tests for BaseGuardrail.detect() method."""

    @pytest.mark.asyncio
    async def test_detect_callback_safe_no_exception(self, mock_backend):
        """Test _detect_callback() does not raise when result is safe."""
        guardrail = CustomTestGuardrail(backend=mock_backend)

        # Should not raise any exception
        result = await guardrail.call_detect_callback("test_event", data={"test": "value"})

        # Result should be None (callback doesn't return GuardrailResult)
        assert result is None

    @pytest.mark.asyncio
    async def test_detect_callback_risky_raises_guardrail_error(self, risky_backend):
        """Test _detect_callback() raises GuardrailError when risk detected."""
        guardrail = CustomTestGuardrail(backend=risky_backend)

        with pytest.raises(GuardrailError) as exc_info:
            await guardrail.call_detect_callback("test_event", data={"test": "value"})

        error = exc_info.value
        assert error.status == StatusCode.GUARDRAIL_BLOCKED
        assert "prompt_injection" in error.message or "blocked" in error.message.lower()

    @pytest.mark.asyncio
    async def test_detect_callback_error_contains_risk_info(self, risky_backend):
        """Test GuardrailError contains correct risk information in details."""
        guardrail = CustomTestGuardrail(backend=risky_backend)

        with pytest.raises(GuardrailError) as exc_info:
            await guardrail.call_detect_callback("user_input_event", data={"text": "test"})

        error = exc_info.value
        assert error.details is not None
        assert error.details["risk_type"] == "test_risk"
        assert error.details["risk_level"] == "HIGH"
        assert error.details["event"] == "user_input_event"

    @pytest.mark.asyncio
    async def test_detect_callback_error_with_details(self, risky_backend_with_details):
        """Test GuardrailError includes backend details in error details."""
        guardrail = CustomTestGuardrail(backend=risky_backend_with_details)

        with pytest.raises(GuardrailError) as exc_info:
            await guardrail.call_detect_callback("llm_input_event", data={"prompt": "test"})

        error = exc_info.value
        assert error.details is not None
        # Check core risk info
        assert error.details["risk_type"] == "prompt_injection"
        assert error.details["risk_level"] == "HIGH"
        assert error.details["event"] == "llm_input_event"
        # Check backend-specific details are merged
        assert "matched_pattern" in error.details
        assert error.details["matched_pattern"] == "ignore previous instructions"
        assert "confidence" in error.details
        assert error.details["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_detect_callback_unknown_risk_type(self):
        """Test _detect_callback() handles unknown risk type gracefully."""
        class UnknownRiskBackend(GuardrailBackend):
            async def analyze(self, data):
                return RiskAssessment(
                    has_risk=True,
                    risk_level=RiskLevel.MEDIUM,
                    risk_type=None,  # No risk type specified
                    details=None
                )

        guardrail = CustomTestGuardrail(backend=UnknownRiskBackend())

        with pytest.raises(GuardrailError) as exc_info:
            await guardrail.call_detect_callback("test_event", data={})

        error = exc_info.value
        assert error.details["risk_type"] == "unknown"
        assert error.details["risk_level"] == "MEDIUM"

    @pytest.mark.asyncio
    async def test_detect_callback_integration_with_framework(self, framework, risky_backend):
        """Test _detect_callback() integration with callback framework handles error gracefully.

        Note: The callback framework catches exceptions from callbacks and continues execution.
        The GuardrailError is logged but not re-raised. This is by design - the framework
        should not let one failing callback stop the entire event processing.
        """
        guardrail = CustomTestGuardrail(backend=risky_backend)
        await guardrail.register(framework)

        # Trigger the event - callback framework catches the GuardrailError internally
        # and continues execution (does not re-raise)
        results = await framework.trigger("test_event", data={"text": "malicious input"})

        # The callback was executed (but exception was caught by framework)
        # Results will be empty because the callback raised an exception
        assert results == []

    @pytest.mark.asyncio
    async def test_guardrail_called_via_framework(self, framework):
        """Test guardrail detect() is actually called when event is triggered via framework."""
        # Use a spy guardrail to track if detect() was called
        spy_guardrail = SpyGuardrail()
        await spy_guardrail.register(framework)

        # Trigger the event
        await framework.trigger("test_event", text="test input", user_id="123")

        # Verify detect() was actually called
        assert spy_guardrail.detect_called is True
        assert spy_guardrail.detected_event_name == "test_event"
        assert "text" in spy_guardrail.detected_kwargs
        assert spy_guardrail.detected_kwargs["text"] == "test input"

    @pytest.mark.asyncio
    async def test_guardrail_safe_flow_via_framework(self, framework, mock_backend):
        """Test complete safe flow: guardrail registered -> event triggered -> no exception."""
        guardrail = CustomTestGuardrail(backend=mock_backend)
        await guardrail.register(framework)

        # Trigger event - should complete without raising exception
        results = await framework.trigger("test_event", data={"safe": "content"})

        # Safe callback returns None, which is collected in results
        # Note: callback framework collects all callback return values
        assert results == [None]  # detect returns None when safe

    @pytest.mark.asyncio
    async def test_guardrail_receives_correct_kwargs(self, framework):
        """Test guardrail receives correct event data when triggered via framework."""
        spy_guardrail = SpyGuardrail()
        await spy_guardrail.register(framework)

        # Trigger with specific data (pass as individual kwargs)
        await framework.trigger(
            "test_event",
            prompt="test prompt",
            user_id="user123",
            metadata={"source": "web"}
        )

        # Verify all data was passed correctly
        assert spy_guardrail.detected_kwargs["prompt"] == "test prompt"
        assert spy_guardrail.detected_kwargs["user_id"] == "user123"
        assert spy_guardrail.detected_kwargs["metadata"] == {"source": "web"}

    @pytest.mark.asyncio
    async def test_multiple_events_trigger_correct_guardrail(self, framework):
        """Test triggering different events invokes the correct guardrail callbacks."""
        spy_guardrail1 = SpyGuardrail(events=["event1"])
        spy_guardrail2 = SpyGuardrail(events=["event2"])

        await spy_guardrail1.register(framework)
        await spy_guardrail2.register(framework)

        # Trigger event1
        await framework.trigger("event1", data={"event": "1"})

        # Only guardrail1 should be called
        assert spy_guardrail1.detect_called is True
        assert spy_guardrail2.detect_called is False
        assert spy_guardrail1.detected_event_name == "event1"

        # Reset and trigger event2
        spy_guardrail1.detect_called = False
        await framework.trigger("event2", data={"event": "2"})

        # Now only guardrail2 should be called
        assert spy_guardrail1.detect_called is False
        assert spy_guardrail2.detect_called is True
        assert spy_guardrail2.detected_event_name == "event2"


# Helper classes for testing
class SpyGuardrail(BaseGuardrail):
    """Spy guardrail that tracks if detect() was called and with what data."""
    DEFAULT_EVENTS = ["test_event"]

    def __init__(self, backend=None, events=None):
        super().__init__(backend=backend, events=events)
        self.detect_called = False
        self.detected_event_name = None
        self.detected_kwargs = None

    def extract_context(self, event, *args, **kwargs):
        return GuardrailContext(
            content_type=GuardrailContentType.TEXT,
            content=kwargs.get("text", "") or kwargs.get("prompt", ""),
            event=str(event),
            metadata=kwargs
        )

    async def detect(self, event_name, *args, **kwargs):
        self.detect_called = True
        self.detected_event_name = event_name
        self.detected_kwargs = kwargs
        return GuardrailResult.pass_()


class CustomTestGuardrail(BaseGuardrail):
    """Test guardrail implementation."""
    DEFAULT_EVENTS = ["test_event"]

    def __init__(self, backend=None, events=None):
        super().__init__(backend=backend, events=events)
        self._test_backend = None
        self._test_framework = None
        self._test_registered_events = []

    def extract_context(self, event, *args, **kwargs):
        return GuardrailContext(
            content_type=GuardrailContentType.TEXT,
            content=kwargs.get("text", "") or kwargs.get("prompt", "") or kwargs.get("data", ""),
            event=str(event),
            metadata=kwargs
        )

    def get_backend(self):
        """Get the backend for testing."""
        return self._backend

    def set_framework(self, framework):
        """Set framework for testing."""
        self._framework = framework

    def add_registered_event(self, event):
        """Add registered event for testing."""
        self._registered_events.append(event)

    async def detect(self, event_name, *args, **kwargs):
        if self._backend:
            return await super().detect(event_name, *args, **kwargs)
        return GuardrailResult.pass_()

    async def call_detect_callback(self, event_name, **kwargs):
        """Public wrapper for testing _detect_callback."""
        return await self._detect_callback(event_name, **kwargs)


class DataCaptureGuardrail(BaseGuardrail):
    """Test guardrail that captures event data."""
    DEFAULT_EVENTS = ["test_event"]

    def __init__(self, backend=None):
        super().__init__(backend=backend)
        self.captured_data = None

    def extract_context(self, event, *args, **kwargs):
        return GuardrailContext(
            content_type=GuardrailContentType.TEXT,
            content=kwargs.get("text", "") or kwargs.get("prompt", ""),
            event=str(event),
            metadata=kwargs
        )

    async def detect(self, event_name, *args, **kwargs):
        self.captured_data = kwargs
        if self._backend:
            return await super().detect(event_name, *args, **kwargs)
        return GuardrailResult.pass_()

    async def call_detect_callback(self, event_name, **kwargs):
        """Public wrapper for testing _detect_callback."""
        return await self._detect_callback(event_name, **kwargs)


class DirectBaseCallGuardrail(BaseGuardrail):
    """Test guardrail that directly calls base detect()."""
    DEFAULT_EVENTS = ["test_event"]

    def extract_context(self, event, *args, **kwargs):
        return GuardrailContext(
            content_type=GuardrailContentType.TEXT,
            content=kwargs.get("text", "") or kwargs.get("data", ""),
            event=str(event),
            metadata=kwargs
        )

    async def detect(self, event_name, *args, **kwargs):
        # Directly call base class detect without checking backend
        return await BaseGuardrail.detect(self, event_name, *args, **kwargs)

    async def call_detect_callback(self, event_name, **kwargs):
        """Public wrapper for testing _detect_callback."""
        return await self._detect_callback(event_name, **kwargs)
