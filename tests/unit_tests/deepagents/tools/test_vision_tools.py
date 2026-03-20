# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.deepagents.schema.config import VisionModelConfig
from openjiuwen.deepagents.prompts.sections.tools import build_tool_card
from openjiuwen.deepagents.tools.vision import (
    ImageOCRTool,
    VisualQuestionAnsweringTool,
    create_vision_tools,
)


def _write_test_png(path: Path) -> None:
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00"
        b"\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc`\x00\x00\x00\x02\x00\x01"
        b"\xe2!\xbc3"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_image_ocr_tool_encodes_local_image(tmp_path: Path, monkeypatch):
    image_path = tmp_path / "sample.png"
    _write_test_png(image_path)
    vision_model_config = VisionModelConfig(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="mock-model",
    )

    calls = []

    def fake_invoke_chat_completion(config, prompt, image_content):
        calls.append(
            {
                "config": config,
                "prompt": prompt,
                "image_content": image_content,
            }
        )
        return "detected text"

    monkeypatch.setattr(
        "openjiuwen.deepagents.tools.vision._invoke_chat_completion",
        fake_invoke_chat_completion,
    )

    async def _run():
        await Runner.start()
        try:
            tool = ImageOCRTool(vision_model_config=vision_model_config)
            return await tool.invoke({"image_path_or_url": str(image_path)})
        finally:
            await Runner.stop()

    result = asyncio.run(_run())

    assert result.success is True
    assert result.data["text"] == "detected text"
    assert calls[0]["image_content"]["image_url"]["url"].startswith("data:image/png;base64,")


def test_visual_question_answering_tool_uses_ocr_context(monkeypatch):
    prompts = []
    vision_model_config = VisionModelConfig(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="mock-model",
    )

    async def fake_call_vision_model(
        image_path_or_url: str,
        prompt: str,
        configured_model: VisionModelConfig | None,
    ):
        _ = image_path_or_url
        prompts.append(prompt)
        assert configured_model is vision_model_config
        if len(prompts) == 1:
            return "SALE 50% OFF", "mock-model"
        return "The sign says SALE 50% OFF.", "mock-model"

    monkeypatch.setattr(
        "openjiuwen.deepagents.tools.vision._call_vision_model",
        fake_call_vision_model,
    )

    async def _run():
        await Runner.start()
        try:
            tool = VisualQuestionAnsweringTool(
                vision_model_config=vision_model_config,
            )
            return await tool.invoke(
                {
                    "image_path_or_url": "https://example.com/image.png",
                    "question": "What does the sign say?",
                }
            )
        finally:
            await Runner.stop()

    result = asyncio.run(_run())

    assert result.success is True
    assert result.data["ocr_text"] == "SALE 50% OFF"
    assert result.data["answer"] == "The sign says SALE 50% OFF."
    assert len(prompts) == 2
    assert "SALE 50% OFF" in prompts[1]
    assert "What does the sign say?" in prompts[1]


def test_visual_question_answering_tool_can_skip_ocr(monkeypatch):
    prompts = []
    vision_model_config = VisionModelConfig(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="mock-model",
    )

    async def fake_call_vision_model(
        image_path_or_url: str,
        prompt: str,
        configured_model: VisionModelConfig | None,
    ):
        _ = image_path_or_url
        prompts.append(prompt)
        assert configured_model is vision_model_config
        return "A black cat is sitting on a chair.", "mock-model"

    monkeypatch.setattr(
        "openjiuwen.deepagents.tools.vision._call_vision_model",
        fake_call_vision_model,
    )

    async def _run():
        await Runner.start()
        try:
            tool = VisualQuestionAnsweringTool(
                vision_model_config=vision_model_config,
            )
            return await tool.invoke(
                {
                    "image_path_or_url": "https://example.com/image.png",
                    "question": "Describe the image.",
                    "include_ocr": False,
                }
            )
        finally:
            await Runner.stop()

    result = asyncio.run(_run())

    assert result.success is True
    assert result.data["ocr_text"] is None
    assert result.data["answer"] == "A black cat is sitting on a chair."
    assert prompts == ["Describe the image."]


def test_create_vision_tools_supports_language():
    vision_model_config = VisionModelConfig(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="mock-model",
    )
    tools = create_vision_tools(
        language="en",
        vision_model_config=vision_model_config,
    )

    assert [tool.card.name for tool in tools] == [
        "image_ocr",
        "visual_question_answering",
    ]
    assert tools[0].card == build_tool_card("image_ocr", "ImageOCRTool", "en")
    assert tools[1].card == build_tool_card(
        "visual_question_answering",
        "VisualQuestionAnsweringTool",
        "en",
    )
    assert tools[0].vision_model_config is vision_model_config
    assert tools[1].vision_model_config is vision_model_config


def test_image_ocr_tool_returns_clear_error_without_vision_config():
    async def _run():
        await Runner.start()
        try:
            tool = ImageOCRTool()
            return await tool.invoke(
                {"image_path_or_url": "https://example.com/image.png"}
            )
        finally:
            await Runner.stop()

    result = asyncio.run(_run())

    assert result.success is False
    assert "Vision model config is not set" in result.error


def test_vision_model_config_from_env(monkeypatch):
    monkeypatch.setenv("VISION_API_KEY", "vision-key")
    monkeypatch.setenv("VISION_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.delenv("VISION_MODEL", raising=False)

    config = VisionModelConfig.from_env()

    assert config.api_key == "vision-key"
    assert config.base_url == "https://openrouter.ai/api/v1"
    assert config.model == "google/gemini-2.5-pro"
