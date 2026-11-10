# coding: utf-8
"""Round-boundary probe for harness-injected tool-image user messages.

The ReAct multimodal tool-image bridge is a user-role message with list
content. Compressors must not mistake it for a new genuine user turn: the
origin marker written by ``_build_multimodal_tool_results_message`` makes
the boundary fall on the real user message instead.
"""

from __future__ import annotations

from openjiuwen.core.context_engine.processor.forked.compressor.base import (
    _is_real_user_message,
    find_last_real_user_message_index,
)
from openjiuwen.core.context_engine.processor.forked.compressor.current_round_compressor import (
    CurrentRoundCompressor,
    CurrentRoundCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor import (
    DialogueCompressor,
    DialogueCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.recall.archive import (
    _is_real_user_message as _archive_is_real_user_message,
)
from openjiuwen.core.foundation.llm import (
    OPENJIUWEN_MESSAGE_ORIGIN_HARNESS_INTERNAL,
    OPENJIUWEN_MESSAGE_ORIGIN_METADATA,
    OPENJIUWEN_MESSAGE_SOURCE_KIND_METADATA,
    AssistantMessage,
    UserMessage,
)
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.harness.tools.base_tool import ToolOutput


def _tool_image_user_message() -> UserMessage:
    result = ToolOutput(
        success=True,
        data={
            "content": "Image file read: /tmp/spec.png",
            "multimodal": [
                {
                    "type": "image",
                    "source_path": "/tmp/spec.png",
                    "data_url": "data:image/png;base64,abc",
                }
            ],
        },
    )
    message = ReActAgent._build_multimodal_tool_results_message([result])
    assert message is not None
    return message


def test_tool_image_bridge_marks_harness_internal_origin() -> None:
    message = _tool_image_user_message()
    assert message.role == "user"
    assert isinstance(message.content, list)
    assert message.metadata[OPENJIUWEN_MESSAGE_ORIGIN_METADATA] == OPENJIUWEN_MESSAGE_ORIGIN_HARNESS_INTERNAL
    assert message.metadata[OPENJIUWEN_MESSAGE_SOURCE_KIND_METADATA] == "tool_media"


def test_origin_marker_survives_round_trip() -> None:
    message = _tool_image_user_message()
    restored = UserMessage.model_validate(message.model_dump())
    assert restored.metadata[OPENJIUWEN_MESSAGE_ORIGIN_METADATA] == OPENJIUWEN_MESSAGE_ORIGIN_HARNESS_INTERNAL
    assert restored.metadata[OPENJIUWEN_MESSAGE_SOURCE_KIND_METADATA] == "tool_media"


def test_is_real_user_message_rejects_origin_marked_bridge() -> None:
    bridge = _tool_image_user_message()
    assert not _is_real_user_message(bridge)
    assert not _archive_is_real_user_message(bridge)


def test_is_real_user_message_keeps_genuine_and_legacy_behavior() -> None:
    assert _is_real_user_message(UserMessage(content="检查设备是否合格"))
    # Legacy bridge messages without the marker keep the original behavior.
    assert _is_real_user_message(UserMessage(content=[{"type": "text", "text": "loaded"}]))
    assert not _is_real_user_message(UserMessage(content="<system-reminder>内部注入"))
    assert not _is_real_user_message(AssistantMessage(content="ok"))


def test_find_last_real_user_message_index_ignores_tool_image_bridge() -> None:
    # 定向探针：真实 user、assistant、工具图片 user 三条，轮边界必须是 0
    #（修复前桥接图把边界推到 2）。
    messages = [
        UserMessage(content="检查设备是否合格"),
        AssistantMessage(content="我来读规格图"),
        _tool_image_user_message(),
    ]
    assert find_last_real_user_message_index(messages) == 0


def test_current_round_span_covers_bridge_work_within_round() -> None:
    compressor = CurrentRoundCompressor(CurrentRoundCompressorConfig())
    messages = [
        UserMessage(content="检查设备是否合格"),
        AssistantMessage(content="我来读规格图"),
        _tool_image_user_message(),
        AssistantMessage(content="根据阈值判断"),
    ]
    span = compressor._build_span(messages)
    # 当前轮从真实用户消息之后开始，桥接图片段属于可压缩区间，
    # 真实用户指令保留在 prefix 里。
    assert span.preserved_prefix == [messages[0]]
    assert messages[2] in span.messages_to_compress
    assert messages[3] in span.messages_to_compress


def test_dialogue_span_protects_round_started_by_real_user() -> None:
    compressor = DialogueCompressor(DialogueCompressorConfig())
    messages = [
        UserMessage(content="检查设备是否合格"),
        AssistantMessage(content="我来读规格图"),
        _tool_image_user_message(),
        AssistantMessage(content="根据阈值判断"),
    ]
    span = compressor._build_span(messages)
    # 单轮对话：整段都是当前轮，没有历史轮可压。修复前桥接图把轮起点
    # 推到图片处，用户真实指令会被划进可压缩历史。
    assert not span.has_target
    assert span.protected_tail == messages
