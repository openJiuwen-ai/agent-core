"""Summary requests must write images as text and never carry base64 pixels."""

from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.context_engine.processor.forked.compressor.support.compression_executor import (
    CompressionExecutor,
    CompressionRequest,
)
from openjiuwen.core.foundation.llm import UserMessage

_DATA_URI = "data:image/png;base64," + "A" * 2048


def _request_with_history(exclude_recent_messages: int = 0) -> CompressionRequest:
    window = ContextWindow(
        system_messages=[UserMessage(content="system prompt")],
        context_messages=[
            UserMessage(
                content=[
                    {"type": "text", "text": "look at this"},
                    {"type": "image_url", "image_url": {"url": _DATA_URI}},
                ]
            ),
            UserMessage(content="later turn"),
        ],
        tools=[],
    )
    return CompressionRequest.from_context_window(
        prompt="compress this",
        context_window=window,
        exclude_recent_messages=exclude_recent_messages,
    )


def _flattened(messages) -> str:
    parts = []
    for message in messages:
        parts.append(str(getattr(message, "content", "")))
    return "\n".join(parts)


def test_build_context_messages_writes_images_as_text():
    request = _request_with_history()

    built = CompressionExecutor.build_context_messages(request)
    text = _flattened(built)

    assert "base64" not in text
    assert "data:image" not in text
    assert "Image attached" in text
    assert "summary request" in text


def test_build_context_messages_keeps_history_untouched():
    request = _request_with_history()
    original_first = request.context_messages[0]

    CompressionExecutor.build_context_messages(request)

    content = original_first.content
    assert isinstance(content, list)
    assert any(
        isinstance(part, dict) and part.get("type") == "image_url" for part in content
    )
    url = content[1]["image_url"]["url"]
    assert url == _DATA_URI


def test_build_context_messages_keeps_stamped_dimensions():
    window = ContextWindow(
        system_messages=[],
        context_messages=[
            UserMessage(
                content=[
                    {
                        "type": "image_url",
                        "image_url": {"url": _DATA_URI},
                        "width": 1024,
                        "height": 768,
                    }
                ]
            )
        ],
        tools=[],
    )
    request = CompressionRequest.from_context_window(
        prompt="compress",
        context_window=window,
    )

    built = CompressionExecutor.build_context_messages(request)

    assert "1024x768" in _flattened(built)
    assert "base64" not in _flattened(built)


def test_build_context_messages_sanitizes_data_urls_in_plain_strings():
    window = ContextWindow(
        system_messages=[],
        context_messages=[
            UserMessage(content=f"embedded {_DATA_URI} inside prose"),
        ],
        tools=[],
    )
    request = CompressionRequest.from_context_window(
        prompt="compress",
        context_window=window,
    )

    built = CompressionExecutor.build_context_messages(request)

    text = _flattened(built)
    assert "base64" not in text
    assert "embedded" in text and "inside prose" in text


def test_build_context_messages_respects_exclude_recent():
    request = _request_with_history(exclude_recent_messages=1)

    built = CompressionExecutor.build_context_messages(request)

    # system + first context message; the trailing turn is excluded
    assert len(built) == 2
    assert "later turn" not in _flattened(built)
    assert "Image attached" in _flattened(built)


def test_build_messages_appends_prompt_and_carries_no_pixels():
    request = _request_with_history()

    built = CompressionExecutor.build_messages(request)

    assert "compress this" in _flattened(built[-1:])
    assert "base64" not in _flattened(built)
