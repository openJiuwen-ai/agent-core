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


def test_tool_message_content_sanitizes_multimodal_payload_in_fallback() -> None:
    """多文件 read_file 结果没有顶层 ``content`` 键,兜底 ``str(result)`` 会把
    ``multimodal`` 里的 base64 以纯文本带进上下文/请求(2026-11-07 事故:5 张图
    5.4MB 文本触发端点 input token limit 拒收)。兜底分支必须把数据 URL 载荷
    替换为占位符,同时保留其余结构(路径、逐文件文本)供模型与下游解析使用。"""

    big_payload = "A" * 1000
    result = ToolOutput(
        success=True,
        data={
            "files": [
                {
                    "file_path": "/tmp/a.png",
                    "content": "Image file read: /tmp/a.png",
                    "type": "image",
                    "multimodal": [
                        {
                            "type": "image",
                            "source": "read_file",
                            "source_path": "/tmp/a.png",
                            "mime_type": "image/png",
                            "data_url": f"data:image/png;base64,{big_payload}",
                        }
                    ],
                },
                {
                    "file_path": "/tmp/b.jpg",
                    "content": "Image file read: /tmp/b.jpg",
                    "type": "image",
                    "multimodal": [
                        {
                            "type": "image",
                            "data_url": f"data:image/jpeg;base64,{big_payload}",
                        }
                    ],
                },
            ],
            # flash 多文件路径的顶层聚合(真实产出形态,flash_read_tool._invoke_multi):
            # 消毒守卫按它判定「载荷存在正规通道,文本副本是冗余的」。
            "multimodal": [
                {
                    "type": "image",
                    "source_path": "/tmp/a.png",
                    "data_url": f"data:image/png;base64,{big_payload}",
                },
                {
                    "type": "image",
                    "data_url": f"data:image/jpeg;base64,{big_payload}",
                },
            ],
        },
    )

    content = AbilityManager._build_tool_message_content(result)

    # base64 载荷绝不以文本身份出现在消息里
    assert "base64," not in content
    assert big_payload not in content
    # 占位符替换了载荷:真实形态里每张图的载荷字符串化后出现两次
    # (files 里的逐文件 multimodal + 顶层聚合 multimodal),2 张图 = 4 处替换
    assert content.count("<multimodal payload omitted>") == 4
    # 其余结构保留:路径与逐文件文本仍可见
    assert "/tmp/a.png" in content
    assert "/tmp/b.jpg" in content
    assert "Image file read: /tmp/a.png" in content
    # 体量必须显著小于原始字符串化(载荷被替换,结构保留)
    assert len(content) < len(str(result))


def test_tool_message_content_fallback_without_payload_is_unchanged() -> None:
    """无 data URL 载荷的结果走兜底时必须与原 ``str(result)`` 逐字节一致——
    消毒只允许影响带 base64 的形态,纯文本结果零变化。"""

    result = ToolOutput(
        success=True,
        data={"files": ["a.txt", "b.txt"], "count": 2},
    )

    assert AbilityManager._build_tool_message_content(result) == str(result)


def test_tool_message_content_preserves_data_urls_without_multimodal_channel() -> None:
    """普通工具输出里的 data URL 是**数据本身**,不是多模态载荷的冗余副本——
    无 multimodal 字段的结果不做任何替换,逐字保留。

    stdout 输出 ``data:text/plain;base64,...``(构造 data URI 嵌 HTML、base64
    编解码任务等)没有其他多模态副本,清掉即丢数据(检视:清理范围过大,
    丢失普通工具的有效输出);纯文本会话同样受影响,故按字段守卫收窄。"""

    text_plain = ToolOutput(
        success=True, data={"stdout": "data:text/plain;base64,aGVsbG8="}
    )
    assert AbilityManager._build_tool_message_content(text_plain) == str(text_plain)

    image_uri = ToolOutput(success=True, data={"note": "data:image/png;base64,XYZ=="})
    assert AbilityManager._build_tool_message_content(image_uri) == str(image_uri)

    # 裸 base64(无 data: 前缀)不受影响,同样原样。
    bare = ToolOutput(success=True, data={"note": "XYZ=="})
    assert AbilityManager._build_tool_message_content(bare) == str(bare)


def test_tool_message_content_sanitizes_text_data_urls_with_multimodal_channel() -> None:
    """结果携带 multimodal 正规通道时,文本字段里字面出现的 data URL 视为
    冗余副本,整体替换为占位符;其余字段结构保留。"""

    result = ToolOutput(
        success=True,
        data={
            "note": "echo data:image/png;base64,XYZ==",
            "multimodal": [
                {"type": "image", "data_url": "data:image/png;base64,XYZ=="}
            ],
        },
    )

    content = AbilityManager._build_tool_message_content(result)
    assert "<multimodal payload omitted>" in content
    assert "base64," not in content
    assert "'note'" in content


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
