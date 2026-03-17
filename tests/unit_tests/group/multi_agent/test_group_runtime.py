# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for GroupRuntime."""
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.multi_agent.group_runtime.communicable_agent import CommunicableAgent
from openjiuwen.core.multi_agent.group_runtime.group_runtime import GroupRuntime, RuntimeConfig
from openjiuwen.core.multi_agent.group_runtime.message_bus import MessageBusConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_card(agent_id: str) -> AgentCard:
    return AgentCard(id=agent_id, name=agent_id, description="test agent")


class _CommAgent(CommunicableAgent):
    """Minimal CommunicableAgent for testing auto-bind."""
    pass


def _make_runner_module():
    """Return a fake openjiuwen.core.runner module with a mock Runner."""
    mod = ModuleType("openjiuwen.core.runner")
    mock_rm = MagicMock()
    mock_rm.add_agent.return_value = MagicMock(is_err=lambda: False)
    mock_runner = MagicMock()
    mock_runner.resource_mgr = mock_rm
    mod.Runner = mock_runner
    return mod, mock_rm


def _default_provider():
    """Default provider that returns a MagicMock."""
    return MagicMock(spec=[])


def _register(runtime: GroupRuntime, card: AgentCard, provider=None):
    """Register an agent with Runner injected via sys.modules."""
    if provider is None:
        provider = _default_provider
    mod, _ = _make_runner_module()
    with patch.dict(
        sys.modules, {"openjiuwen.core.runner": mod}
    ):
        runtime.register_agent(card, provider)


# ---------------------------------------------------------------------------
# RuntimeConfig
# ---------------------------------------------------------------------------

class TestRuntimeConfig:
    @staticmethod
    def test_defaults():
        cfg = RuntimeConfig()
        assert cfg.group_id == "default"
        assert cfg.message_bus is None

    @staticmethod
    def test_custom_group_id():
        cfg = RuntimeConfig(group_id="my_group")
        assert cfg.group_id == "my_group"

    @staticmethod
    def test_custom_message_bus():
        bus_cfg = MessageBusConfig(max_queue_size=50)
        cfg = RuntimeConfig(group_id="g", message_bus=bus_cfg)
        assert cfg.message_bus.max_queue_size == 50


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestGroupRuntimeLifecycle:
    @staticmethod
    @pytest.mark.asyncio
    async def test_start_sets_running():
        runtime = GroupRuntime()
        await runtime.start()
        assert runtime.is_running() is True
        await runtime.stop()

    @staticmethod
    @pytest.mark.asyncio
    async def test_stop_clears_running():
        runtime = GroupRuntime()
        await runtime.start()
        await runtime.stop()
        assert runtime.is_running() is False

    @staticmethod
    @pytest.mark.asyncio
    async def test_start_is_idempotent():
        runtime = GroupRuntime()
        await runtime.start()
        await runtime.start()
        assert runtime.is_running() is True
        await runtime.stop()

    @staticmethod
    @pytest.mark.asyncio
    async def test_stop_when_not_running_is_safe():
        runtime = GroupRuntime()
        await runtime.stop()

    @staticmethod
    @pytest.mark.asyncio
    async def test_async_context_manager_starts_and_stops():
        runtime = GroupRuntime()
        async with runtime as rt:
            assert rt.is_running() is True
        assert rt.is_running() is False


# ---------------------------------------------------------------------------
# Agent registration
# ---------------------------------------------------------------------------

class TestGroupRuntimeAgentRegistration:
    @staticmethod
    def test_has_agent_false_before_registration():
        runtime = GroupRuntime()
        assert runtime.has_agent("unknown") is False

    @staticmethod
    def test_register_agent_stores_card():
        runtime = GroupRuntime()
        card = _make_card("agent_a")
        _register(runtime, card)
        assert runtime.has_agent("agent_a") is True

    @staticmethod
    def test_get_agent_card_returns_the_registered_card():
        runtime = GroupRuntime()
        card = _make_card("agent_b")
        _register(runtime, card)
        assert runtime.get_agent_card("agent_b") is card

    @staticmethod
    def test_get_agent_card_returns_none_for_unknown():
        runtime = GroupRuntime()
        assert runtime.get_agent_card("ghost") is None

    @staticmethod
    def test_get_agent_count_increments():
        runtime = GroupRuntime()
        assert runtime.get_agent_count() == 0
        _register(runtime, _make_card("a1"))
        _register(runtime, _make_card("a2"))
        assert runtime.get_agent_count() == 2

    @staticmethod
    def test_list_agents_returns_all_ids():
        runtime = GroupRuntime()
        _register(runtime, _make_card("a1"))
        _register(runtime, _make_card("a2"))
        assert set(runtime.list_agents()) == {"a1", "a2"}

    @staticmethod
    def test_unregister_agent_removes_card():
        runtime = GroupRuntime()
        card = _make_card("agent_c")
        _register(runtime, card)
        removed = runtime.unregister_agent("agent_c")
        assert removed is card
        assert runtime.has_agent("agent_c") is False

    @staticmethod
    def test_unregister_unknown_agent_returns_none():
        runtime = GroupRuntime()
        assert runtime.unregister_agent("nonexistent") is None

    @staticmethod
    def test_wrap_provider_auto_binds_communicable_agent():
        """Wrapped provider auto-binds runtime when agent is CommunicableAgent."""
        runtime = GroupRuntime()
        agent = _CommAgent()
        
        def provider():
            return agent
        
        card = _make_card("comm_agent")

        mod, mock_rm = _make_runner_module()
        with patch.dict(
            sys.modules, {"openjiuwen.core.runner": mod}
        ):
            runtime.register_agent(card, provider)

        assert mock_rm.add_agent.called
        wrapped_provider = mock_rm.add_agent.call_args[0][1]
        created = wrapped_provider()
        assert created.is_bound is True
        assert created.agent_id == "comm_agent"


# ---------------------------------------------------------------------------
# Subscriptions -- access via internal _subscription_manager
# ---------------------------------------------------------------------------

class TestGroupRuntimeSubscriptions:
    @staticmethod
    def _sub_count(runtime: GroupRuntime) -> int:
        return runtime.get_subscription_count()

    @pytest.mark.asyncio
    async def test_subscribe_increments_count(self):
        runtime = GroupRuntime()
        await runtime.subscribe("agent_a", "topic1")
        assert self._sub_count(runtime) == 1
        await runtime.stop()

    @pytest.mark.asyncio
    async def test_unsubscribe_decrements_count(self):
        runtime = GroupRuntime()
        await runtime.subscribe("agent_a", "topic1")
        await runtime.unsubscribe("agent_a", "topic1")
        assert self._sub_count(runtime) == 0
        await runtime.stop()

    @pytest.mark.asyncio
    async def test_list_subscriptions_all(self):
        runtime = GroupRuntime()
        await runtime.subscribe("agent_a", "t1")
        await runtime.subscribe("agent_b", "t2")
        result = runtime.list_subscriptions()
        assert "subscriptions" in result
        assert "t1" in result["subscriptions"]
        assert "t2" in result["subscriptions"]
        await runtime.stop()

    @pytest.mark.asyncio
    async def test_list_subscriptions_filtered_by_agent(self):
        runtime = GroupRuntime()
        await runtime.subscribe("agent_a", "t1")
        await runtime.subscribe("agent_a", "t2")
        result = runtime.list_subscriptions(
            agent_id="agent_a"
        )
        assert result["agent_id"] == "agent_a"
        assert "t1" in result["topics"]
        await runtime.stop()

    @staticmethod
    @pytest.mark.asyncio
    async def test_subscribe_empty_agent_id_raises():
        from openjiuwen.core.common.exception.errors import BaseError
        runtime = GroupRuntime()
        with pytest.raises(BaseError):
            await runtime.subscribe("", "topic")

    @staticmethod
    @pytest.mark.asyncio
    async def test_subscribe_empty_topic_raises():
        from openjiuwen.core.common.exception.errors import BaseError
        runtime = GroupRuntime()
        with pytest.raises(BaseError):
            await runtime.subscribe("agent_a", "")

    @staticmethod
    @pytest.mark.asyncio
    async def test_unsubscribe_empty_agent_id_raises():
        from openjiuwen.core.common.exception.errors import BaseError
        runtime = GroupRuntime()
        with pytest.raises(BaseError):
            await runtime.unsubscribe("", "topic")

    @staticmethod
    @pytest.mark.asyncio
    async def test_unsubscribe_empty_topic_raises():
        from openjiuwen.core.common.exception.errors import BaseError
        runtime = GroupRuntime()
        with pytest.raises(BaseError):
            await runtime.unsubscribe("agent_a", "")

    @staticmethod
    @pytest.mark.asyncio
    async def test_unregister_agent_clears_subscriptions():
        runtime = GroupRuntime()
        card = _make_card("agent_sub")
        _register(runtime, card)
        await runtime.subscribe("agent_sub", "events")
        assert TestGroupRuntimeSubscriptions._sub_count(runtime) == 1
        runtime.unregister_agent("agent_sub")
        assert TestGroupRuntimeSubscriptions._sub_count(runtime) == 0
        await runtime.stop()


# ---------------------------------------------------------------------------
# send / publish validation
# ---------------------------------------------------------------------------

class TestGroupRuntimeSendPublishValidation:
    @staticmethod
    @pytest.mark.asyncio
    async def test_send_raises_when_sender_empty():
        from openjiuwen.core.common.exception.errors import BaseError
        runtime = GroupRuntime()
        with pytest.raises(BaseError):
            await runtime.send(message="msg", recipient="agent_b", sender="")

    @staticmethod
    @pytest.mark.asyncio
    async def test_send_raises_when_recipient_empty():
        from openjiuwen.core.common.exception.errors import BaseError
        runtime = GroupRuntime()
        with pytest.raises(BaseError):
            await runtime.send(message="msg", recipient="", sender="agent_a")

    @staticmethod
    @pytest.mark.asyncio
    async def test_send_raises_when_recipient_not_registered():
        from openjiuwen.core.common.exception.errors import BaseError
        runtime = GroupRuntime()
        with pytest.raises(BaseError):
            await runtime.send(message="msg", recipient="ghost", sender="agent_a")

    @staticmethod
    @pytest.mark.asyncio
    async def test_publish_raises_when_sender_empty():
        from openjiuwen.core.common.exception.errors import BaseError
        runtime = GroupRuntime()
        with pytest.raises(BaseError):
            await runtime.publish(message="msg", topic_id="events", sender="")

    @staticmethod
    @pytest.mark.asyncio
    async def test_publish_raises_when_topic_id_empty():
        from openjiuwen.core.common.exception.errors import BaseError
        runtime = GroupRuntime()
        with pytest.raises(BaseError):
            await runtime.publish(message="msg", topic_id="", sender="agent_a")

    @staticmethod
    @pytest.mark.asyncio
    async def test_send_routes_through_message_bus():
        """send() delegates to message bus after validation passes."""
        runtime = GroupRuntime()
        card = _make_card("agent_b")
        _register(runtime, card)

        with patch.object(runtime, '_message_bus') as mock_bus:
            mock_bus.start = AsyncMock()
            mock_bus.stop = AsyncMock()
            mock_bus.send = AsyncMock(return_value="hello_response")
            mock_bus.add_subscription = AsyncMock()
            await runtime.start()
            result = await runtime.send(
                message="hello",
                recipient="agent_b",
                sender="agent_a",
            )
            assert result == "hello_response"
            await runtime.stop()

    @staticmethod
    @pytest.mark.asyncio
    async def test_publish_routes_through_message_bus():
        """publish() delegates to message bus after validation passes."""
        runtime = GroupRuntime()
        with patch.object(runtime, '_message_bus') as mock_bus:
            mock_bus.publish = AsyncMock(return_value=None)
            mock_bus.start = AsyncMock()
            mock_bus.stop = AsyncMock()
            await runtime.start()
            await runtime.publish(
                message="event",
                topic_id="my_topic",
                sender="agent_a",
            )
            mock_bus.publish.assert_awaited_once()
            await runtime.stop()
