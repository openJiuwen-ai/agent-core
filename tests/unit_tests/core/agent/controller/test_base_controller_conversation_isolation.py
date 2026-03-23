#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit test for BaseController conversation_id isolation"""

import asyncio
import unittest
from typing import Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.core.single_agent.legacy import AgentConfig
from openjiuwen.core.controller.legacy.controller import BaseController
from openjiuwen.core.controller.legacy import Event
from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.single_agent import Session


class SimpleController(BaseController):
    """Simple test implementation of BaseController"""

    async def handle_event(
        self, message: Event, session: Session
    ) -> Optional[Dict]:
        """Simple echo implementation"""
        return {
            "conversation_id": message.source.conversation_id,
            "content": message.content.query or "",
            "handled_by": "SimpleController"
        }


class TestBaseControllerConversationIsolation(unittest.IsolatedAsyncioTestCase):
    """Test BaseController conversation_id isolation"""

    async def asyncSetUp(self):
        """Setup test fixtures"""
        self.config = AgentConfig()
        self.context_engine = MagicMock(spec=ContextEngine)
        self.session = MagicMock(spec=Session)

    async def test_single_conversation(self):
        """Test single conversation works correctly"""
        controller = SimpleController(
            self.config,
            self.context_engine,
            self.session
        )

        inputs = {
            "conversation_id": "conv_001",
            "query": "Hello"
        }

        result = await controller.invoke(inputs, self.session)

        # Verify result
        self.assertEqual(result["conversation_id"], "conv_001")
        self.assertEqual(result["content"], "Hello")

        # Verify subscription created
        self.assertIn("conv_001", controller._subscriptions)

        # Cleanup
        await controller.cleanup_conversation("conv_001")
        self.assertNotIn("conv_001", controller._subscriptions)

    async def test_multiple_conversations_isolated(self):
        """Test multiple conversations are isolated"""
        controller = SimpleController(
            self.config,
            self.context_engine,
            self.session
        )

        # Create two conversations concurrently
        tasks = [
            controller.invoke(
                {"conversation_id": "conv_001", "query": "Event 1"},
                self.session
            ),
            controller.invoke(
                {"conversation_id": "conv_002", "query": "Event 2"},
                self.session
            ),
        ]

        results = await asyncio.gather(*tasks)

        # Verify both conversations got correct results
        self.assertEqual(results[0]["conversation_id"], "conv_001")
        self.assertEqual(results[0]["content"], "Event 1")

        self.assertEqual(results[1]["conversation_id"], "conv_002")
        self.assertEqual(results[1]["content"], "Event 2")

        # Verify two subscriptions created
        self.assertIn("conv_001", controller._subscriptions)
        self.assertIn("conv_002", controller._subscriptions)

        # Cleanup
        await controller.cleanup_conversation("conv_001")
        await controller.cleanup_conversation("conv_002")
        self.assertEqual(len(controller._subscriptions), 0)

    async def test_stop_cleanup_all_subscriptions(self):
        """Test stop() cleans up all subscriptions"""
        controller = SimpleController(
            self.config,
            self.context_engine,
            self.session
        )

        # Create multiple conversations
        await controller.invoke(
            {"conversation_id": "conv_001", "query": "Test 1"},
            self.session
        )
        await controller.invoke(
            {"conversation_id": "conv_002", "query": "Test 2"},
            self.session
        )

        # Verify subscriptions created
        self.assertEqual(len(controller._subscriptions), 2)

        # Stop controller
        await controller.stop()

        # Verify all subscriptions cleaned up
        self.assertEqual(len(controller._subscriptions), 0)

    async def test_concurrent_same_conversation(self):
        """Test concurrent calls with same conversation_id"""
        controller = SimpleController(
            self.config,
            self.context_engine,
            self.session
        )

        # Multiple concurrent calls with same conversation_id
        tasks = [
            controller.invoke(
                {"conversation_id": "conv_001", "query": f"Event {i}"},
                self.session
            )
            for i in range(5)
        ]

        results = await asyncio.gather(*tasks)

        # All should have same conversation_id
        for result in results:
            self.assertEqual(result["conversation_id"], "conv_001")

        # Only one subscription should be created
        self.assertEqual(len(controller._subscriptions), 1)
        self.assertIn("conv_001", controller._subscriptions)

        await controller.stop()


class TestBaseAgentClearSession(unittest.IsolatedAsyncioTestCase):
    """Test BaseAgent.clear_session() releases session from Runner"""

    async def asyncSetUp(self):
        """Setup test fixtures"""
        self.config = AgentConfig()

    async def test_clear_session_calls_runner_release(self):
        """Test BaseAgent.clear_session() calls Runner.release with correct session_id"""
        from openjiuwen.core.single_agent.legacy.agent import BaseAgent

        class ConcreteAgent(BaseAgent):
            """Concrete implementation for testing"""

            async def invoke(self, inputs: Dict, session=None):
                return {}

            async def stream(self, inputs: Dict, session=None):
                async for item in []:
                    yield item

        agent = ConcreteAgent(agent_config=self.config)

        with patch('openjiuwen.core.runner.Runner') as mock_runner:
            mock_runner.release = AsyncMock()

            session_id = "test_session_123"
            await agent.clear_session(session_id=session_id)

            # Verify Runner.release was called with correct session_id
            mock_runner.release.assert_called_once_with(session_id=session_id)

    async def test_clear_session_default_session_id(self):
        """Test BaseAgent.clear_session() uses default_session when session_id not provided"""
        from openjiuwen.core.single_agent.legacy.agent import BaseAgent

        class ConcreteAgent(BaseAgent):
            """Concrete implementation for testing"""

            async def invoke(self, inputs: Dict, session=None):
                return {}

            async def stream(self, inputs: Dict, session=None):
                async for item in []:
                    yield item

        agent = ConcreteAgent(agent_config=self.config)

        with patch('openjiuwen.core.runner.Runner') as mock_runner:
            mock_runner.release = AsyncMock()

            # Call without session_id argument
            await agent.clear_session()

            # Verify Runner.release was called with default session_id
            mock_runner.release.assert_called_once_with(
                session_id="default_session"
            )


class TestControllerAgentClearSession(unittest.IsolatedAsyncioTestCase):
    """Test ControllerAgent.clear_session() calls parent clear_session()"""

    async def asyncSetUp(self):
        """Setup test fixtures"""
        self.config = AgentConfig()
        self.context_engine = MagicMock()
        self.context_engine.clear_context = AsyncMock()
        self.controller = MagicMock(spec=BaseController)
        self.controller.controller_type = "test"
        self.controller.event_source = MagicMock()

    async def test_clear_session_calls_parent_clear_session(self):
        """Test ControllerAgent.clear_session() calls parent's clear_session()"""
        from openjiuwen.core.single_agent.legacy.agent import ControllerAgent, BaseAgent

        self.controller.cleanup_conversation = AsyncMock()

        agent = ControllerAgent(agent_config=self.config)
        agent.controller = self.controller

        mock_parent_clear = AsyncMock()

        with patch.object(BaseAgent, 'clear_session', mock_parent_clear):
            with patch.object(type(agent), 'context_engine', self.context_engine):
                session_id = "test_session_123"
                await agent.clear_session(session_id=session_id)

                # Verify controller's cleanup methods were called
                self.context_engine.clear_context.assert_called_once_with(
                    session_id=session_id
                )
                self.controller.cleanup_conversation.assert_called_once_with(session_id)

                # Verify parent's clear_session was called
                mock_parent_clear.assert_called_once_with(session_id=session_id)

    async def test_clear_session_default_session_id(self):
        """Test clear_session uses default_session when session_id not provided"""
        from openjiuwen.core.single_agent.legacy.agent import ControllerAgent, BaseAgent

        self.controller.cleanup_conversation = AsyncMock()

        agent = ControllerAgent(agent_config=self.config)
        agent.controller = self.controller

        mock_parent_clear = AsyncMock()

        with patch.object(BaseAgent, 'clear_session', mock_parent_clear):
            with patch.object(type(agent), 'context_engine', self.context_engine):
                # Call without session_id argument
                await agent.clear_session()

                # Verify controller's cleanup methods were called with default session_id
                self.context_engine.clear_context.assert_called_once_with(
                    session_id="default_session"
                )
                self.controller.cleanup_conversation.assert_called_once_with("default_session")

                # Verify parent's clear_session was called with default session_id
                mock_parent_clear.assert_called_once_with(session_id="default_session")


if __name__ == "__main__":
    unittest.main()

