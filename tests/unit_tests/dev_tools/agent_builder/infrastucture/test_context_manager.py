#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import unittest
from datetime import datetime, timezone

from openjiuwen.dev_tools.agent_builder.infrastructure.context import ContextManager


class TestContextManager(unittest.TestCase):
    """Tests for ContextManager class."""

    def setUp(self):
        self.context_manager = ContextManager()

    def test_init_with_default_max_size(self):
        self.assertEqual(self.context_manager.max_history_size, 50)

    def test_init_with_custom_max_size(self):
        manager = ContextManager(max_history_size=20)
        self.assertEqual(manager.max_history_size, 20)

    def test_get_latest_k_messages(self):
        # Add messages first
        for i in range(10):
            self.context_manager.add_message(str(i), "user")
        messages = self.context_manager.get_latest_k_messages(5)
        expect_result = [{'content': str(i), 'role': 'user'} for i in range(5, 10)]
        self.assertEqual(messages, expect_result)

        messages = self.context_manager.get_latest_k_messages(10)
        expect_result = [{'content': str(i), 'role': 'user'} for i in range(10)]
        self.assertEqual(messages, expect_result)

    def test_get_latest_k_messages_when_k_exceeds_history_size(self):
        for i in range(10):
            self.context_manager.add_message(str(i), "user")
        messages = self.context_manager.get_latest_k_messages(11)
        expect_result = [{'content': str(i), 'role': 'user'} for i in range(10)]
        self.assertEqual(messages, expect_result)

    def test_get_latest_k_messages_with_zero(self):
        for i in range(5):
            self.context_manager.add_message(str(i), "user")
        messages = self.context_manager.get_latest_k_messages(0)
        # Should return all messages (up to max_history_size)
        self.assertEqual(len(messages), 5)

    def test_get_history(self):
        for i in range(10):
            self.context_manager.add_message(str(i), "user")
        messages = self.context_manager.get_history()
        expect_result = [{'content': str(i), 'role': 'user'} for i in range(10)]
        self.assertEqual(messages, expect_result)

    def test_add_message_with_timestamp(self):
        timestamp = datetime.now(timezone.utc)
        self.context_manager.add_message("test_add", "user", timestamp)
        self.assertEqual(self.context_manager.count_messages(), 1)
        messages = self.context_manager.get_history()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["content"], "test_add")
        self.assertEqual(messages[0]["role"], "user")

    def test_add_message_without_timestamp(self):
        self.context_manager.add_message("test_no_ts", "assistant")
        messages = self.context_manager.get_history()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["content"], "test_no_ts")
        self.assertEqual(messages[0]["role"], "assistant")

    def test_add_assistant_message(self):
        self.context_manager.add_assistant_message("I am here to help.")
        self.assertEqual(self.context_manager.count_messages(), 1)
        messages = self.context_manager.get_history()
        self.assertEqual(messages[0]["content"], "I am here to help.")
        self.assertEqual(messages[0]["role"], "assistant")

    def test_add_user_message(self):
        self.context_manager.add_user_message("Hello, I need help.")
        self.assertEqual(self.context_manager.count_messages(), 1)
        messages = self.context_manager.get_history()
        self.assertEqual(messages[0]["content"], "Hello, I need help.")
        self.assertEqual(messages[0]["role"], "user")

    def test_clear(self):
        for i in range(10):
            self.context_manager.add_message(str(i), "user")
        self.assertEqual(self.context_manager.count_messages(), 10)
        self.context_manager.clear()
        self.assertEqual(self.context_manager.count_messages(), 0)

    def test_conversation_flow(self):
        """Test a realistic conversation flow."""
        self.context_manager.add_user_message("Hello")
        self.context_manager.add_assistant_message("Hi there!")
        self.context_manager.add_user_message("Can you help me?")
        self.context_manager.add_assistant_message("Of course!")

        history = self.context_manager.get_history()
        self.assertEqual(len(history), 4)
        self.assertEqual(history[0]["content"], "Hello")
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[1]["content"], "Hi there!")
        self.assertEqual(history[1]["role"], "assistant")
        self.assertEqual(history[2]["content"], "Can you help me?")
        self.assertEqual(history[2]["role"], "user")
        self.assertEqual(history[3]["content"], "Of course!")
        self.assertEqual(history[3]["role"], "assistant")

        # Test getting only latest 2 messages
        latest_2 = self.context_manager.get_latest_k_messages(2)
        self.assertEqual(len(latest_2), 2)
        self.assertEqual(latest_2[0]["content"], "Can you help me?")
        self.assertEqual(latest_2[1]["content"], "Of course!")


if __name__ == "__main__":
    unittest.main()
