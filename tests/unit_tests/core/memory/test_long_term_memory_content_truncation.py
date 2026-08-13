# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Input truncation in :class:`LongTermMemory`, first unit coverage.

Stage 5 of the typed content-parts refactor. ``long_term_memory`` capped a
message by slicing ``content`` directly, which on list content truncates by
*element count* instead of length — dropping whole parts while leaving a
single oversized one untouched. See
``docs/dev/message-content-parts-refactor.md`` §3, Stage 5.

Only system tests existed for this module before, so the ``str`` cases here
are as much of the point as the list ones.
"""

import base64

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage, ToolMessage, UserMessage
from openjiuwen.core.memory.config.config import MemoryEngineConfig
from openjiuwen.core.memory.long_term_memory import LongTermMemory

_MAX_LEN = 32


def _image_block() -> dict:
    data_url = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\xa5" * 4096).decode("ascii")
    return {"type": "image_url", "image_url": {"url": data_url}}


@pytest.fixture
def memory():
    """``LongTermMemory`` is a process-wide singleton — restore what we change."""
    instance = LongTermMemory()
    previous = instance._sys_mem_config
    instance._sys_mem_config = MemoryEngineConfig(input_msg_max_len=_MAX_LEN)
    yield instance
    instance._sys_mem_config = previous


class TestContentTruncation:
    def test_truncation_on_str_content(self, memory):
        """The common path must stay a plain character-count truncation."""
        message = AssistantMessage(content="a" * 100)

        memory._truncate_content(message)

        assert message.content == "a" * _MAX_LEN

    def test_short_str_content_is_untouched(self, memory):
        message = AssistantMessage(content="short")

        memory._truncate_content(message)

        assert message.content == "short"

    def test_truncation_on_list_content_does_not_corrupt(self, memory):
        """Slicing a list keeps ``_MAX_LEN`` *elements*, not characters.

        With two parts the old code was a no-op — the oversized text survived
        whole, and an image survived with it, defeating the cap entirely.
        """
        message = AssistantMessage(content=["b" * 100, _image_block()])

        memory._truncate_content(message)

        assert message.content == "b" * _MAX_LEN

    def test_truncation_joins_multiple_text_parts(self, memory):
        message = AssistantMessage(content=["one", "two"])

        memory._truncate_content(message)

        assert message.content == "one\ntwo"

    def test_truncation_preserves_message_validity(self, memory):
        """The result must still round-trip as a ``BaseMessage``."""
        message = ToolMessage(tool_call_id="tc-1", content=["c" * 100, _image_block()])

        memory._truncate_content(message)

        restored = ToolMessage.model_validate(message.model_dump())
        assert isinstance(restored, BaseMessage)
        assert restored.content == "c" * _MAX_LEN
        assert restored.tool_call_id == "tc-1"


class TestCheckMessages:
    """``_check_messages`` is the caller that applies the cap."""

    def test_user_messages_are_reported_and_left_intact(self, memory):
        user = UserMessage(content="u" * 100)

        has_human_msg, out_messages = memory._check_messages([user])

        assert has_human_msg is True
        assert out_messages[0].content == "u" * 100, "user messages are exempt from the cap"

    def test_non_user_messages_are_truncated(self, memory):
        messages = [
            UserMessage(content="hello"),
            AssistantMessage(content="a" * 100),
            ToolMessage(tool_call_id="tc-1", content=["b" * 100, _image_block()]),
        ]

        has_human_msg, out_messages = memory._check_messages(messages)

        assert has_human_msg is True
        assert out_messages[1].content == "a" * _MAX_LEN
        assert out_messages[2].content == "b" * _MAX_LEN

    def test_reports_absent_human_message(self, memory):
        has_human_msg, out_messages = memory._check_messages([AssistantMessage(content="a")])

        assert has_human_msg is False
        assert len(out_messages) == 1
