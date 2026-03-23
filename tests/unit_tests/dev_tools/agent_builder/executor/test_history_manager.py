# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from datetime import datetime, timezone

import pytest

from openjiuwen.dev_tools.agent_builder.executor.history_manager import (
    DialogueMessage,
    HistoryCache,
    HistoryManager,
)
from openjiuwen.dev_tools.agent_builder.utils.constants import DEFAULT_MAX_HISTORY_SIZE


class TestDialogueMessage:
    @staticmethod
    def test_dialogue_message_creation():
        timestamp = datetime.now(timezone.utc)
        message = DialogueMessage(
            content="Hello",
            role="user",
            timestamp=timestamp
        )
        assert message.content == "Hello"
        assert message.role == "user"
        assert message.timestamp == timestamp

    @staticmethod
    def test_dialogue_message_to_dict():
        message = DialogueMessage(
            content="Hello",
            role="user",
            timestamp=datetime.now(timezone.utc)
        )
        result = message.to_dict()
        assert result == {"role": "user", "content": "Hello"}
        assert "timestamp" not in result

    @staticmethod
    def test_dialogue_message_assistant():
        message = DialogueMessage(
            content="Hi there!",
            role="assistant",
            timestamp=datetime.now(timezone.utc)
        )
        assert message.role == "assistant"
        result = message.to_dict()
        assert result["role"] == "assistant"


class TestHistoryCache:
    @staticmethod
    def test_history_cache_creation():
        cache = HistoryCache()
        assert cache.get_history() == []
        assert cache.max_history_size == DEFAULT_MAX_HISTORY_SIZE

    @staticmethod
    def test_history_cache_custom_size():
        cache = HistoryCache(max_history_size=10)
        assert cache.max_history_size == 10

    @staticmethod
    def test_get_history_empty():
        cache = HistoryCache()
        result = cache.get_history()
        assert result == []

    @staticmethod
    def test_add_message():
        cache = HistoryCache()
        message = DialogueMessage(
            content="Test",
            role="user",
            timestamp=datetime.now(timezone.utc)
        )
        cache.add_message(message)
        
        history = cache.get_history()
        assert len(history) == 1
        assert history[0].content == "Test"

    @staticmethod
    def test_get_messages_with_limit():
        cache = HistoryCache(max_history_size=10)
        for i in range(5):
            cache.add_message(DialogueMessage(
                content=f"Message {i}",
                role="user",
                timestamp=datetime.now(timezone.utc)
            ))
        
        result = cache.get_messages(3)
        assert len(result) == 3
        assert result[0]["content"] == "Message 2"
        assert result[1]["content"] == "Message 3"
        assert result[2]["content"] == "Message 4"

    @staticmethod
    def test_get_messages_all():
        cache = HistoryCache()
        for i in range(3):
            cache.add_message(DialogueMessage(
                content=f"Message {i}",
                role="user",
                timestamp=datetime.now(timezone.utc)
            ))
        
        result = cache.get_messages(-1)
        assert len(result) == 3

    @staticmethod
    def test_max_history_size_enforcement():
        cache = HistoryCache(max_history_size=3)
        for i in range(5):
            cache.add_message(DialogueMessage(
                content=f"Message {i}",
                role="user",
                timestamp=datetime.now(timezone.utc)
            ))
        
        history = cache.get_history()
        assert len(history) == 3
        assert history[0].content == "Message 2"
        assert history[1].content == "Message 3"
        assert history[2].content == "Message 4"

    @staticmethod
    def test_clear():
        cache = HistoryCache()
        cache.add_message(DialogueMessage(
            content="Test",
            role="user",
            timestamp=datetime.now(timezone.utc)
        ))
        cache.clear()
        assert cache.get_history() == []


class TestHistoryManager:
    @staticmethod
    def test_history_manager_creation():
        manager = HistoryManager()
        assert manager.dialogue_history is not None

    @staticmethod
    def test_add_message():
        manager = HistoryManager()
        manager.add_message("Hello", "user")
        
        history = manager.get_history()
        assert len(history) == 1
        assert history[0]["content"] == "Hello"
        assert history[0]["role"] == "user"

    @staticmethod
    def test_add_assistant_message():
        manager = HistoryManager()
        manager.add_assistant_message("Hi there!")
        
        history = manager.get_history()
        assert len(history) == 1
        assert history[0]["role"] == "assistant"

    @staticmethod
    def test_add_user_message():
        manager = HistoryManager()
        manager.add_user_message("Hello")
        
        history = manager.get_history()
        assert len(history) == 1
        assert history[0]["role"] == "user"

    @staticmethod
    def test_get_latest_k_messages():
        manager = HistoryManager()
        for i in range(5):
            manager.add_user_message(f"Message {i}")
        
        result = manager.get_latest_k_messages(3)
        assert len(result) == 3
        assert result[0]["content"] == "Message 2"
        assert result[2]["content"] == "Message 4"

    @staticmethod
    def test_get_history():
        manager = HistoryManager()
        manager.add_user_message("Hello")
        manager.add_assistant_message("Hi!")
        
        history = manager.get_history()
        assert len(history) == 2

    @staticmethod
    def test_clear():
        manager = HistoryManager()
        manager.add_user_message("Hello")
        manager.clear()
        
        assert manager.get_history() == []

    @staticmethod
    def test_custom_timestamp():
        manager = HistoryManager()
        custom_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        manager.add_message("Test", "user", timestamp=custom_time)
        
        history = manager.dialogue_history.get_history()
        assert history[0].timestamp == custom_time

    @staticmethod
    def test_multiple_sessions_independent():
        manager1 = HistoryManager()
        manager2 = HistoryManager()
        
        manager1.add_user_message("Session 1")
        manager2.add_user_message("Session 2")
        
        assert len(manager1.get_history()) == 1
        assert len(manager2.get_history()) == 1
        assert manager1.get_history()[0]["content"] == "Session 1"
        assert manager2.get_history()[0]["content"] == "Session 2"
