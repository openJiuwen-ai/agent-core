#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved
import unittest
from datetime import datetime, timezone

from openjiuwen.agent_builder.nl_to_agent.common.context_manager import ContextManager, DialogueMessage


class TestContextManager(unittest.TestCase):
    def setUp(self):
        self.context_manager = ContextManager()

    def test_get_latest_k_messages(self):
        self.context_manager._dialogue_history._history = [
            DialogueMessage(content=str(i), role="user", timestamp=datetime.now(timezone.utc)) for i in range(10)
        ]
        messages = self.context_manager.get_latest_k_messages(5)
        expect_result = [dict(content=str(i), role="user") for i in range(5, 10)]
        self.assertEqual(messages, expect_result)

        messages = self.context_manager.get_latest_k_messages(10)
        expect_result = [dict(content=str(i), role="user") for i in range(10)]
        self.assertEqual(messages, expect_result)

        messages = self.context_manager.get_latest_k_messages(11)
        expect_result = [dict(content=str(i), role="user") for i in range(10)]
        self.assertEqual(messages, expect_result)


    def test_get_history(self):
        self.context_manager._dialogue_history._history = [
            DialogueMessage(content=str(i), role="user", timestamp=datetime.now(timezone.utc)) for i in range(10)
        ]
        messages = self.context_manager.get_history()
        expect_result = [dict(content=str(i), role="user") for i in range(10)]
        self.assertEqual(messages, expect_result)

    def test_add_message(self):
        timestamp = datetime.now(timezone.utc)
        self.context_manager.add_message("test_add", "user", timestamp)
        self.assertIn(
            DialogueMessage(content="test_add", role="user", timestamp=timestamp),
            self.context_manager._dialogue_history._history
        )

    def test_clear(self):
        self.context_manager._dialogue_history._history = [
            DialogueMessage(content=str(i), role="user", timestamp=datetime.now(timezone.utc)) for i in range(10)
        ]
        self.context_manager.clear()
        self.assertEqual(len(self.context_manager._dialogue_history._history), 0)


if __name__ == "__main__":
    unittest.main()
