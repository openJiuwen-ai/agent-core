# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for CommunicableAgent mixin."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.multi_agent.group_runtime.communicable_agent import CommunicableAgent


# ---------------------------------------------------------------------------
# Concrete subclass for testing
# ---------------------------------------------------------------------------

class SimpleAgent(CommunicableAgent):
    """Minimal concrete agent for unit testing."""
    pass


def _make_runtime(send_return="response") -> MagicMock:
    """Build a mock GroupRuntime."""
    runtime = MagicMock()
    runtime.send = AsyncMock(return_value=send_return)
    runtime.publish = AsyncMock(return_value=None)
    runtime.subscribe = AsyncMock(return_value=None)
    runtime.unsubscribe = AsyncMock(return_value=None)
    return runtime


# ---------------------------------------------------------------------------
# bind_runtime / property tests
# ---------------------------------------------------------------------------

class TestCommunicableAgentBinding:
    """Tests for bind_runtime, is_bound, runtime, agent_id properties."""

    @staticmethod
    def test_is_bound_false_before_binding():
        agent = SimpleAgent()
        assert agent.is_bound is False

    @staticmethod
    def test_is_bound_true_after_bind_runtime():
        agent = SimpleAgent()
        runtime = _make_runtime()
        agent.bind_runtime(runtime, "agent_x")
        assert agent.is_bound is True

    @staticmethod
    def test_runtime_property_returns_bound_runtime():
        agent = SimpleAgent()
        runtime = _make_runtime()
        agent.bind_runtime(runtime, "agent_x")
        assert agent.runtime is runtime

    @staticmethod
    def test_agent_id_property_returns_bound_id():
        agent = SimpleAgent()
        runtime = _make_runtime()
        agent.bind_runtime(runtime, "my_agent")
        assert agent.agent_id == "my_agent"

    @staticmethod
    def test_runtime_property_raises_when_not_bound():
        agent = SimpleAgent()
        from openjiuwen.core.common.exception.errors import BaseError
        with pytest.raises(BaseError):
            _ = agent.runtime

    @staticmethod
    def test_agent_id_property_raises_when_not_bound():
        agent = SimpleAgent()
        from openjiuwen.core.common.exception.errors import BaseError
        with pytest.raises(BaseError):
            _ = agent.agent_id

    @staticmethod
    def test_bind_runtime_idempotent_same_runtime_same_id():
        """Binding the same runtime+id twice should silently succeed."""
        agent = SimpleAgent()
        runtime = _make_runtime()
        agent.bind_runtime(runtime, "agent_x")
        agent.bind_runtime(runtime, "agent_x")  # should not raise
        assert agent.agent_id == "agent_x"

    @staticmethod
    def test_bind_runtime_rebind_different_runtime_warns(caplog):
        """Rebinding to a different runtime emits a warning."""
        import logging
        agent = SimpleAgent()
        runtime1 = _make_runtime()
        runtime2 = _make_runtime()
        agent.bind_runtime(runtime1, "agent_x")
        with caplog.at_level(logging.WARNING):
            agent.bind_runtime(runtime2, "agent_y")
        # After rebind the new values should be active
        assert agent.agent_id == "agent_y"
        assert agent.runtime is runtime2


# ---------------------------------------------------------------------------
# send / publish / subscribe / unsubscribe tests (synchronous verification)
# ---------------------------------------------------------------------------

class TestCommunicableAgentMessaging:
    """Tests for send, publish, subscribe, unsubscribe method signatures."""

    agent: SimpleAgent
    runtime: MagicMock

    def setup_method(self):
        self.agent = SimpleAgent()
        self.runtime = _make_runtime(send_return="ack")
        self.agent.bind_runtime(self.runtime, "sender_agent")

    def test_send_method_accepts_session_id_parameter(self):
        """Verify send() method signature includes session_id parameter."""
        import inspect
        sig = inspect.signature(self.agent.send)
        assert "session_id" in sig.parameters
        assert sig.parameters["session_id"].default is None

    def test_send_method_accepts_timeout_parameter(self):
        """Verify send() method signature includes timeout parameter."""
        import inspect
        sig = inspect.signature(self.agent.send)
        assert "timeout" in sig.parameters
        assert sig.parameters["timeout"].default is None

    def test_publish_method_accepts_session_id_parameter(self):
        """Verify publish() method signature includes session_id parameter."""
        import inspect
        sig = inspect.signature(self.agent.publish)
        assert "session_id" in sig.parameters
        assert sig.parameters["session_id"].default is None

    def test_agent_has_send_method(self):
        """Verify agent has send method."""
        assert hasattr(self.agent, "send")
        assert callable(self.agent.send)

    def test_agent_has_publish_method(self):
        """Verify agent has publish method."""
        assert hasattr(self.agent, "publish")
        assert callable(self.agent.publish)

    def test_agent_has_subscribe_method(self):
        """Verify agent has subscribe method."""
        assert hasattr(self.agent, "subscribe")
        assert callable(self.agent.subscribe)

    def test_agent_has_unsubscribe_method(self):
        """Verify agent has unsubscribe method."""
        assert hasattr(self.agent, "unsubscribe")
        assert callable(self.agent.unsubscribe)
