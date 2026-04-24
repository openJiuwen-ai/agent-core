# coding: utf-8
from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.core.context_engine import ContextWindow
from openjiuwen.core.context_engine.processor.multimodal.image_reference_processor import (
    ImageReferenceProcessor,
    ImageReferenceProcessorConfig,
)
from openjiuwen.core.foundation.llm import ToolMessage, UserMessage
from openjiuwen.harness.rails import ImageReferenceRail


_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
    b"\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01"
    b"\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _write_png(tmp_path: Path, name: str = "sample.png") -> Path:
    image_path = tmp_path / name
    image_path.write_bytes(_PNG_BYTES)
    return image_path


def _image_parts(message: UserMessage) -> list[dict]:
    assert isinstance(message.content, list)
    return [part for part in message.content if part.get("type") == "image_url"]


@pytest.mark.asyncio
async def test_user_image_path_is_expanded_only_in_context_window(tmp_path: Path):
    image_path = _write_png(tmp_path)
    source_message = UserMessage(content=f"Please inspect {image_path}")
    window = ContextWindow(context_messages=[source_message])
    processor = ImageReferenceProcessor(ImageReferenceProcessorConfig())

    _, processed = await processor.on_get_context_window(None, window)

    assert source_message.content == f"Please inspect {image_path}"
    user_message = processed.context_messages[0]
    assert isinstance(user_message, UserMessage)
    assert isinstance(user_message.content, list)
    assert user_message.content[0]["type"] == "text"
    assert "[image 1: sample.png]" in user_message.content[0]["text"]
    image_parts = _image_parts(user_message)
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_tool_discovered_image_path_appends_runtime_user_message(tmp_path: Path):
    image_path = _write_png(tmp_path, "from_tool.png")
    window = ContextWindow(
        context_messages=[
            UserMessage(content="Look at the images in this folder."),
            ToolMessage(content=f"Found file: {image_path}", tool_call_id="tool-call-1"),
        ]
    )
    processor = ImageReferenceProcessor(ImageReferenceProcessorConfig(scan_user_messages=False))

    _, processed = await processor.on_get_context_window(None, window)

    assert len(processed.context_messages) == 3
    runtime_message = processed.context_messages[-1]
    assert isinstance(runtime_message, UserMessage)
    assert isinstance(runtime_message.content, list)
    assert "Recent tool results referenced these image files" in runtime_message.content[0]["text"]
    image_parts = _image_parts(runtime_message)
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_tool_data_url_is_sanitized_and_reinjected_as_user_image():
    data_url = "data:image/png;base64,QUJDRA=="
    window = ContextWindow(
        context_messages=[
            UserMessage(content="What is in this image?"),
            ToolMessage(content=f"success=True data={{'content': '{data_url}'}}", tool_call_id="tool-call-1"),
        ]
    )
    processor = ImageReferenceProcessor(ImageReferenceProcessorConfig(scan_user_messages=False))

    _, processed = await processor.on_get_context_window(None, window)

    tool_message = processed.context_messages[1]
    assert isinstance(tool_message, ToolMessage)
    assert "data:image/png;base64" not in tool_message.content
    assert "image data URL omitted" in tool_message.content
    runtime_message = processed.context_messages[-1]
    image_parts = _image_parts(runtime_message)
    assert image_parts[0]["image_url"]["url"] == data_url


@pytest.mark.asyncio
async def test_remote_image_url_is_passed_through_without_download():
    url = "https://example.com/assets/chart.png"
    window = ContextWindow(context_messages=[UserMessage(content=f"Analyze {url}")])
    processor = ImageReferenceProcessor(ImageReferenceProcessorConfig(remote_url_policy="direct"))

    _, processed = await processor.on_get_context_window(None, window)

    user_message = processed.context_messages[0]
    image_parts = _image_parts(user_message)
    assert image_parts[0]["image_url"]["url"] == url


def test_image_reference_rail_installs_and_removes_processor():
    class _Config:
        context_processors = []

    class _ReactAgent:
        _config = _Config()

    class _Agent:
        react_agent = _ReactAgent()

    agent = _Agent()
    rail = ImageReferenceRail({"max_images": 2})

    rail.init(agent)
    assert agent.react_agent._config.context_processors[0][0] == "ImageReferenceProcessor"
    assert agent.react_agent._config.context_processors[0][1].max_images == 2

    rail.uninit(agent)
    assert agent.react_agent._config.context_processors == []
