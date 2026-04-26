# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for ``processor._protected`` helper functions."""
from __future__ import annotations

import pytest

from openjiuwen.core.context_engine.processor._protected import (
    is_protected,
    msg_in_window,
    resolve_active_window_message_ids,
)
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
)


@pytest.mark.unit
class TestIsProtected:
    def test_active_skill_pin_always_protected(self):
        msg = SystemMessage(content="x", metadata={"active_skill_pin": True})
        assert is_protected(msg, in_active_window=False) is True

    def test_skill_body_stub_never_protected(self):
        msg = ToolMessage(
            content="[SKILL LOADED] ...",
            tool_call_id="t1",
            metadata={"skill_body_stub": True, "skill_name": "x"},
        )
        assert is_protected(msg, in_active_window=True) is False

    def test_full_skill_body_protected_only_in_window(self):
        msg = ToolMessage(
            content="full body",
            tool_call_id="t1",
            metadata={"is_skill_body": True, "skill_name": "x"},
        )
        assert is_protected(msg, in_active_window=True) is True
        assert is_protected(msg, in_active_window=False) is False

    def test_plain_message_not_protected(self):
        assert is_protected(UserMessage(content="hi")) is False
        assert is_protected(AssistantMessage(content="hi")) is False


@pytest.mark.unit
class TestWindowResolution:
    def test_no_default_window_means_all_messages_in_window(self):
        ctx = type("Ctx", (), {})()
        msgs = [UserMessage(content="a"), AssistantMessage(content="b")]
        ids = resolve_active_window_message_ids(ctx, msgs)
        # Without _default_window_size / _default_dialogue_round, every message
        # is considered in-window.
        assert all(msg_in_window(m, ids) for m in msgs)

    def test_window_size_excludes_oldest(self):
        ctx = type("Ctx", (), {"_default_window_size": 1, "_default_dialogue_round": None})()
        old = UserMessage(content="old")
        recent = UserMessage(content="recent")
        ids = resolve_active_window_message_ids(ctx, [old, recent])
        assert not msg_in_window(old, ids)
        assert msg_in_window(recent, ids)

    def test_empty_in_window_set_means_in_window(self):
        # Empty set is the "no info" sentinel and treats everything as in-window.
        m = UserMessage(content="x")
        assert msg_in_window(m, set()) is True
