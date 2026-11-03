# coding: utf-8

from types import SimpleNamespace

from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.harness.tools.base_tool import ToolOutput


def test_tool_message_content_omits_multimodal_payload() -> None:
    result = ToolOutput(
        success=True,
        data={
            "content": "Image file read: /tmp/a.png",
            "multimodal": [
                {
                    "type": "image",
                    "data_url": "data:image/png;base64,abc",
                }
            ],
        },
    )

    assert AbilityManager._build_tool_message_content(result) == "Image file read: /tmp/a.png"


def test_react_agent_builds_multimodal_user_message_from_tool_result() -> None:
    result = ToolOutput(
        success=True,
        data={
            "content": "Image file read: /tmp/a.png",
            "multimodal": [
                {
                    "type": "image",
                    "source_path": "/tmp/a.png",
                    "data_url": "data:image/png;base64,abc",
                }
            ],
        },
    )

    messages = ReActAgent._build_multimodal_tool_result_messages(result)

    assert len(messages) == 1
    assert messages[0].role == "user"
    assert messages[0].content[0]["type"] == "text"
    assert messages[0].content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,abc"},
    }


def test_react_agent_batches_multiple_multimodal_tool_results_into_one_user_message() -> None:
    first = ToolOutput(
        success=True,
        data={
            "content": "Image file read: /tmp/a.png",
            "multimodal": [
                {
                    "type": "image",
                    "source_path": "/tmp/a.png",
                    "data_url": "data:image/png;base64,aaa",
                }
            ],
        },
    )
    second = ToolOutput(
        success=True,
        data={
            "content": "Image file read: /tmp/b.jpg",
            "multimodal": [
                {
                    "type": "image",
                    "source_path": "/tmp/b.jpg",
                    "data_url": "data:image/jpeg;base64,bbb",
                }
            ],
        },
    )

    message = ReActAgent._build_multimodal_tool_results_message([first, second])

    assert message is not None
    assert message.role == "user"
    assert len(message.content) == 5
    assert message.content[0]["type"] == "text"
    assert "/tmp/a.png" in message.content[0]["text"]
    assert "/tmp/b.jpg" in message.content[0]["text"]
    assert message.content[2] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,aaa"},
    }
    assert message.content[4] == {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,bbb"},
    }


def test_react_agent_detects_image_input_in_messages() -> None:
    result = ToolOutput(
        success=True,
        data={
            "content": "Image file read: /tmp/a.png",
            "multimodal": [
                {
                    "type": "image",
                    "source_path": "/tmp/a.png",
                    "data_url": "data:image/png;base64,abc",
                }
            ],
        },
    )
    messages = ReActAgent._build_multimodal_tool_result_messages(result)

    assert ReActAgent._messages_contain_image_input(messages) is True


def test_react_agent_detects_nested_image_input_in_messages() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,abc"},
                        }
                    ],
                }
            ],
        }
    ]

    assert ReActAgent._messages_contain_image_input(messages) is True


def test_react_agent_detects_image_input_unsupported_errors() -> None:
    exc = RuntimeError(
        "NotFoundError: Error code: 404 - No endpoints found that support image input"
    )

    assert ReActAgent._is_image_input_unsupported_error(exc) is True


def test_react_agent_detects_structured_image_input_unsupported_errors() -> None:
    class ProviderError(Exception):
        body = {
            "error": {
                "code": "image_input_unsupported",
                "message": "The selected model cannot consume the supplied media.",
            }
        }

    assert ReActAgent._is_image_input_unsupported_error(ProviderError()) is True


def test_react_agent_does_not_treat_audio_error_as_image_unsupported() -> None:
    exc = RuntimeError("Error code: 400 - Only text and image_url are supported.")

    assert ReActAgent._is_image_input_unsupported_error(exc) is False


def _img_block(b64: str) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}


def _txt_block(text: str) -> dict:
    return {"type": "text", "text": text}


def _policy_agent(supported=None, max_bytes=None, max_images=None) -> ReActAgent:
    """Build a ReActAgent shell with only the fields the image policy touches.

    The policy helpers are instance methods (they read ``self._config`` and call
    sibling helpers through ``self``), so a bare ``SimpleNamespace`` fake cannot
    exercise them.  ``object.__new__`` skips the heavy ``__init__`` (LLM/session
    wiring) while keeping the real method resolution.
    """
    config = SimpleNamespace(
        image_input_supported_resolver=(
            (lambda: supported) if supported is not None else None
        ),
        max_image_bytes_per_request=max_bytes,
        max_images_per_request=max_images,
    )
    agent = object.__new__(ReActAgent)
    agent._config = config
    return agent


def test_react_agent_content_block_is_image() -> None:
    assert ReActAgent._content_block_is_image(_img_block("aaa")) is True
    assert ReActAgent._content_block_is_image({"type": "image"}) is True
    assert ReActAgent._content_block_is_image(_txt_block("hi")) is False


def test_react_agent_content_block_is_image_accepts_input_image() -> None:
    """Responses 风格 input_image 也必须判为图片，否则剥离/预算/计价全部漏计。"""
    assert ReActAgent._content_block_is_image(
        {"type": "input_image", "image_url": "data:image/png;base64,abc"}
    ) is True
    assert ReActAgent._content_block_is_image(
        {"input_image": {"url": "data:image/png;base64,abc"}}
    ) is True
    assert ReActAgent._content_block_is_image(_txt_block("hi")) is False


def test_react_agent_image_block_byte_size() -> None:
    assert ReActAgent._image_block_byte_size(_img_block("abc")) == 2
    assert ReActAgent._image_block_byte_size(_txt_block("hi")) == 0


def test_react_agent_image_block_byte_size_counts_inline_payload_shapes() -> None:
    """Anthropic source.base64 / input_image 的 data URL 都要计入字节预算。"""
    anthropic = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "abcd"},
    }
    responses = {"type": "input_image", "image_url": "data:image/png;base64,abcd"}

    assert ReActAgent._image_block_byte_size(anthropic) == 3
    assert ReActAgent._image_block_byte_size(responses) == 3
    # 远端引用没有 payload，计 0 字节。
    assert ReActAgent._image_block_byte_size(
        {"type": "input_image", "image_url": "https://example.com/a.png"}
    ) == 0


def test_react_agent_strips_image_blocks_non_destructively() -> None:
    message = UserMessage(content=[_txt_block("query"), _img_block("abc")])
    messages = [message]

    stripped = ReActAgent._strip_image_blocks(messages)

    assert stripped is not messages
    assert stripped[0].content[0] == _txt_block("query")
    assert stripped[0].content[1]["type"] == "text"
    assert "does not support image input" in stripped[0].content[1]["text"]
    # Original history keeps its base64.
    assert message.content[1] == _img_block("abc")


def test_react_agent_policy_strips_when_model_cannot_see_images() -> None:
    agent = _policy_agent(supported=False)
    message = UserMessage(content=[_txt_block("query"), _img_block("abc")])

    result = ReActAgent._apply_image_input_policy(agent, [message])

    assert result[0].content[1]["type"] == "text"
    assert message.content[1] == _img_block("abc")
    assert ReActAgent._messages_contain_image_input(result) is False


def test_react_agent_policy_keeps_images_when_model_supports_images() -> None:
    agent = _policy_agent(supported=True)
    message = UserMessage(content=[_txt_block("query"), _img_block("abc")])
    messages = [message]

    result = ReActAgent._apply_image_input_policy(agent, messages)

    assert result is messages
    assert result[0].content[1] == _img_block("abc")


def test_react_agent_policy_keeps_images_when_support_is_unknown() -> None:
    """Probe 未回时（None）不得把用户上传的图整体剥离。"""
    agent = _policy_agent()
    agent._config.image_input_supported_resolver = lambda: None
    message = UserMessage(content=[_txt_block("query"), _img_block("abc")])
    messages = [message]

    result = ReActAgent._apply_image_input_policy(agent, messages)

    assert ReActAgent._resolve_image_input_supported(agent) is None
    assert result is messages
    assert result[0].content[1] == _img_block("abc")
    assert ReActAgent._messages_contain_image_input(result) is True


def test_react_agent_evicts_oldest_images_over_count_budget() -> None:
    agent = _policy_agent(max_images=1)
    message = UserMessage(content=[
        _txt_block("a"),
        _img_block("aaa"),
        _txt_block("b"),
        _img_block("bbb"),
    ])
    messages = [message]

    result = ReActAgent._evict_images_over_budget(agent, messages)

    assert result is not messages
    assert result[0] is not message
    assert result[0].content[1] == {
        "type": "text",
        "text": "[image omitted: per-request image budget exceeded]",
    }
    assert result[0].content[3] == _img_block("bbb")
    # 与剥离路径一致：历史保留原始 base64，预算只影响本次请求副本。
    assert message.content[1] == _img_block("aaa")


def test_react_agent_evicts_over_byte_budget_for_anthropic_blocks() -> None:
    """Anthropic source.base64 必须计入字节预算，否则预算可被绕过。"""
    agent = _policy_agent(max_bytes=3)
    first = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abcd"}}
    second = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abcd"}}
    message = UserMessage(content=[first, second])

    result = ReActAgent._evict_images_over_budget(agent, [message])

    assert result[0].content[0]["type"] == "text"
    assert result[0].content[1] == second
    assert message.content[0] == first


def test_react_agent_policy_evicts_input_image_over_count_budget() -> None:
    agent = _policy_agent(max_images=1)
    first = {"type": "input_image", "image_url": "data:image/png;base64,aaa"}
    second = {"type": "input_image", "image_url": "data:image/png;base64,bbb"}
    message = UserMessage(content=[first, second])

    result = ReActAgent._apply_image_input_policy(agent, [message])

    assert result[0].content[0]["type"] == "text"
    assert result[0].content[1] == second
    assert message.content[0] == first


def test_react_agent_does_not_evict_within_budget() -> None:
    agent = _policy_agent(max_images=2)
    message = UserMessage(content=[_img_block("aaa"), _img_block("bbb")])
    messages = [message]

    result = ReActAgent._evict_images_over_budget(agent, messages)

    assert result is messages
    assert message.content[0] == _img_block("aaa")
    assert message.content[1] == _img_block("bbb")


def test_react_agent_does_not_evict_when_no_budget_configured() -> None:
    """预算未配置（两个字段都为 None）时卸图为 opt-in，请求原样转发。"""
    agent = _policy_agent()
    message = UserMessage(content=[_img_block("aaa"), _img_block("bbb")])
    messages = [message]

    result = ReActAgent._evict_images_over_budget(agent, messages)

    assert result is messages
    assert message.content[0] == _img_block("aaa")
    assert message.content[1] == _img_block("bbb")
