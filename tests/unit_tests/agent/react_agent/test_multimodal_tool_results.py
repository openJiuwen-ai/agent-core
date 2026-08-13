# coding: utf-8

from openjiuwen.core.foundation.llm import ImagePart, TextPart
from openjiuwen.core.foundation.llm.model_clients.anthropic_model_client import (
    AnthropicModelClient,
    _convert_message_schemas,
)
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
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
    assert isinstance(messages[0].content[0], TextPart)
    assert messages[0].content[1] == ImagePart(mime_type="image/png", data="abc")


def test_multimodal_message_content_is_image_part() -> None:
    """The payload's ``data_url`` becomes a typed part, not a raw dict."""
    result = ToolOutput(
        success=True,
        data={
            "content": "Image file read: /tmp/a.png",
            "multimodal": [
                {
                    "type": "image",
                    "source_path": "/tmp/a.png",
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64,abc",
                    "width": 640,
                    "height": 480,
                }
            ],
        },
    )

    message = ReActAgent._build_multimodal_tool_results_message([result])

    image = message.content[1]
    assert isinstance(image, ImagePart)
    assert image.mime_type == "image/png"
    assert image.data == "abc"
    # Dimensions ride along so token estimation does not fall back to a constant.
    assert (image.width, image.height) == (640, 480)


def test_multimodal_message_survives_all_client_renders() -> None:
    """Every provider must render the produced message into its own dialect."""
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
    message = ReActAgent._build_multimodal_tool_results_message([result])

    openai_content = BaseModelClient._convert_messages_to_dict([message])[0]["content"]
    assert openai_content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,abc"},
    }

    anthropic_payload = AnthropicModelClient._convert_messages_to_dict([message])
    _, anthropic_messages = _convert_message_schemas(anthropic_payload)
    assert anthropic_messages[0]["content"][1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "abc"},
    }


def test_multimodal_message_text_reads_back() -> None:
    """``.text`` must skip the image and keep the caption."""
    result = ToolOutput(
        success=True,
        data={
            "content": "Image file read: /tmp/a.png",
            "multimodal": [
                {
                    "type": "image",
                    "source": "read_file",
                    "source_path": "/tmp/a.png",
                    "data_url": "data:image/png;base64,abc",
                }
            ],
        },
    )

    message = ReActAgent._build_multimodal_tool_results_message([result])

    assert message.text == "Image loaded from read_file: /tmp/a.png"


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
    assert isinstance(message.content[0], TextPart)
    assert "/tmp/a.png" in message.content[0].text
    assert "/tmp/b.jpg" in message.content[0].text
    assert message.content[2] == ImagePart(mime_type="image/png", data="aaa")
    assert message.content[4] == ImagePart(mime_type="image/jpeg", data="bbb")


def test_react_agent_labels_mcp_sourced_images() -> None:
    from openjiuwen.core.foundation.tool import McpToolResult

    result = McpToolResult(
        data={
            "content": "tree summary\n\n1 image(s) attached as multimodal input.",
            "multimodal": [
                {
                    "type": "image",
                    "source": "mcp",
                    "source_path": "get_window_state",
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64,abc",
                }
            ],
        },
    )

    messages = ReActAgent._build_multimodal_tool_result_messages(result)

    assert len(messages) == 1
    assert messages[0].content[0] == TextPart(text="Image loaded from mcp: get_window_state")
    assert messages[0].content[1] == ImagePart(mime_type="image/png", data="abc")


def test_multimodal_image_without_a_source_falls_back_to_a_generic_label() -> None:
    """Only ``read_file``/MCP stamp ``source`` today; a third producer must not be mislabelled."""
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

    message = ReActAgent._build_multimodal_tool_results_message([result])

    assert message.content[0] == TextPart(text="Image loaded from tool: /tmp/a.png")


def test_tool_message_content_uses_mcp_tool_result_content() -> None:
    from openjiuwen.core.foundation.tool import McpToolResult

    result = McpToolResult(
        data={
            "content": "tree summary",
            "multimodal": [{"type": "image", "data_url": "data:image/png;base64,abc"}],
        },
    )

    assert AbilityManager._build_tool_message_content(result) == "tree summary"


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


def test_non_base64_data_url_is_carried_as_a_url() -> None:
    """A non-base64 attachment reached the provider before; it still must."""
    result = ToolOutput(
        success=True,
        data={
            "content": "Image file read: /tmp/a.svg",
            "multimodal": [
                {
                    "type": "image",
                    "source_path": "/tmp/a.svg",
                    "data_url": "data:image/svg+xml,%3Csvg%2F%3E",
                }
            ],
        },
    )

    message = ReActAgent._build_multimodal_tool_results_message([result])

    image = message.content[1]
    assert image.data is None
    assert image.url == "data:image/svg+xml,%3Csvg%2F%3E"
