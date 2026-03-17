# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for MessageRouter."""
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.multi_agent.group_runtime.envelope import MessageEnvelope
from openjiuwen.core.multi_agent.group_runtime.message_router import MessageRouter
from openjiuwen.core.multi_agent.group_runtime.subscription_manager import SubscriptionManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_p2p_envelope(
    recipient: str = "agent_b",
    sender: str = "agent_a",
    message: object = "hello",
    session_id: str = None,
) -> MessageEnvelope:
    return MessageEnvelope(
        message_id="test-p2p",
        message=message,
        sender=sender,
        recipient=recipient,
        session_id=session_id,
    )


def _make_pubsub_envelope(
    topic_id: str = "code_events",
    sender: str = "agent_a",
    message: object = "event_data",
    session_id: str = None,
) -> MessageEnvelope:
    return MessageEnvelope(
        message_id="test-pubsub",
        message=message,
        sender=sender,
        topic_id=topic_id,
        session_id=session_id,
    )


def _make_runner_mock(run_agent_side_effect=None, run_agent_return=None):
    """Build a mock Runner object and inject it into sys.modules."""
    mock_runner_module = ModuleType("openjiuwen.core.runner")
    mock_runner_cls = MagicMock()
    if run_agent_side_effect:
        mock_runner_cls.run_agent = AsyncMock(side_effect=run_agent_side_effect)
    else:
        mock_runner_cls.run_agent = AsyncMock(return_value=run_agent_return)
    mock_runner_module.Runner = mock_runner_cls
    return mock_runner_module, mock_runner_cls


# ---------------------------------------------------------------------------
# P2P routing tests
# ---------------------------------------------------------------------------

class TestMessageRouterP2P:
    def __init__(self):
        """Initialize test instance attributes."""
        self.sub_mgr = None
        self.router = None

    def setup_method(self):
        self.sub_mgr = SubscriptionManager()
        self.router = MessageRouter(self.sub_mgr)

    @pytest.mark.asyncio
    async def test_route_p2p_calls_runner_run_agent(self):
        """P2P routing should call Runner.run_agent with correct args."""
        envelope = _make_p2p_envelope(recipient="agent_b", message="ping")
        mock_module, mock_runner = _make_runner_mock(run_agent_return="pong")

        with patch.dict(sys.modules, {"openjiuwen.core.runner": mock_module}):
            result = await self.router.route_p2p_message(envelope)

        mock_runner.run_agent.assert_awaited_once_with(
            agent="agent_b",
            inputs="ping",
            session=None,
        )
        assert result == "pong"

    @pytest.mark.asyncio
    async def test_route_p2p_passes_session_id(self):
        """Session ID from envelope is forwarded to Runner.run_agent."""
        envelope = _make_p2p_envelope(session_id="session-123")
        mock_module, mock_runner = _make_runner_mock(run_agent_return="ok")

        with patch.dict(sys.modules, {"openjiuwen.core.runner": mock_module}):
            await self.router.route_p2p_message(envelope)

        _, kwargs = mock_runner.run_agent.call_args
        assert kwargs.get("session") == "session-123"

    @pytest.mark.asyncio
    async def test_route_p2p_raises_on_runner_error(self):
        """Errors from Runner.run_agent propagate wrapped as BaseError."""
        envelope = _make_p2p_envelope(recipient="bad_agent")
        mock_module, _ = _make_runner_mock(
            run_agent_side_effect=RuntimeError("agent crash")
        )

        with patch.dict(sys.modules, {"openjiuwen.core.runner": mock_module}):
            with pytest.raises(Exception):
                await self.router.route_p2p_message(envelope)

    @pytest.mark.asyncio
    async def test_route_p2p_raises_on_attribute_error(self):
        """AttributeError (Runner not ready) is caught and re-raised."""
        envelope = _make_p2p_envelope()
        mock_module, _ = _make_runner_mock(
            run_agent_side_effect=AttributeError("not available")
        )

        with patch.dict(sys.modules, {"openjiuwen.core.runner": mock_module}):
            with pytest.raises(Exception):
                await self.router.route_p2p_message(envelope)


# ---------------------------------------------------------------------------
# Pub-Sub routing tests
# ---------------------------------------------------------------------------

class TestMessageRouterPubSub:
    def __init__(self):
        """Initialize test instance attributes."""
        self.sub_mgr = None
        self.router = None

    def setup_method(self):
        self.sub_mgr = SubscriptionManager()
        self.router = MessageRouter(self.sub_mgr)

    @pytest.mark.asyncio
    async def test_route_pubsub_no_subscribers_does_not_raise(self):
        """Pub-Sub with zero subscribers completes silently."""
        envelope = _make_pubsub_envelope(topic_id="empty_topic")
        await self.router.route_pubsub_message(envelope)

    @pytest.mark.asyncio
    async def test_route_pubsub_invokes_all_subscribers(self):
        """All matching subscribers receive the message."""
        self.sub_mgr.subscribe("agent_a", "code_events")
        self.sub_mgr.subscribe("agent_b", "code_events")

        envelope = _make_pubsub_envelope(topic_id="code_events")
        call_log = []

        async def fake_run(agent, inputs, session=None):
            call_log.append(agent)

        mock_module = ModuleType("openjiuwen.core.runner")
        mock_runner = MagicMock()
        mock_runner.run_agent = fake_run
        mock_module.Runner = mock_runner

        with patch.dict(sys.modules, {"openjiuwen.core.runner": mock_module}):
            await self.router.route_pubsub_message(envelope)

        assert set(call_log) == {"agent_a", "agent_b"}

    @pytest.mark.asyncio
    async def test_route_pubsub_wildcard_subscriber(self):
        """Wildcard subscriber receives the pub-sub message."""
        self.sub_mgr.subscribe("listener", "code_*")
        envelope = _make_pubsub_envelope(topic_id="code_review")
        received = []

        async def fake_run(agent, inputs, session=None):
            received.append(agent)

        mock_module = ModuleType("openjiuwen.core.runner")
        mock_runner = MagicMock()
        mock_runner.run_agent = fake_run
        mock_module.Runner = mock_runner

        with patch.dict(sys.modules, {"openjiuwen.core.runner": mock_module}):
            await self.router.route_pubsub_message(envelope)

        assert "listener" in received

    @pytest.mark.asyncio
    async def test_route_pubsub_one_failing_subscriber_does_not_abort_others(self):
        """A failing subscriber should not stop delivery to other subscribers."""
        self.sub_mgr.subscribe("good_agent", "events")
        self.sub_mgr.subscribe("bad_agent", "events")

        call_log = []

        async def fake_run(agent, inputs, session=None):
            if agent == "bad_agent":
                raise RuntimeError("subscriber failed")
            call_log.append(agent)

        mock_module = ModuleType("openjiuwen.core.runner")
        mock_runner = MagicMock()
        mock_runner.run_agent = fake_run
        mock_module.Runner = mock_runner

        envelope = _make_pubsub_envelope(topic_id="events")
        with patch.dict(sys.modules, {"openjiuwen.core.runner": mock_module}):
            await self.router.route_pubsub_message(envelope)

        assert "good_agent" in call_log

    @pytest.mark.asyncio
    async def test_route_pubsub_passes_session_id_to_subscribers(self):
        """session_id from envelope is forwarded to each subscriber."""
        self.sub_mgr.subscribe("agent_c", "task_events")
        envelope = _make_pubsub_envelope(topic_id="task_events", session_id="sess-99")
        received_sessions = []

        async def fake_run(agent, inputs, session=None):
            received_sessions.append(session)

        mock_module = ModuleType("openjiuwen.core.runner")
        mock_runner = MagicMock()
        mock_runner.run_agent = fake_run
        mock_module.Runner = mock_runner

        with patch.dict(sys.modules, {"openjiuwen.core.runner": mock_module}):
            await self.router.route_pubsub_message(envelope)

        assert "sess-99" in received_sessions
