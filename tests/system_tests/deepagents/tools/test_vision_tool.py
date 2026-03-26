# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from openjiuwen.core.runner import Runner
from openjiuwen.deepagents.schema.config import VisionModelConfig
from openjiuwen.deepagents.tools import create_vision_tools


def _vision_system_tests_enabled() -> bool:
    return (os.getenv("RUN_VISION_SYSTEM_TESTS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _load_vision_repo_dotenv(override: bool = True) -> None:
    env_file = (
        Path(__file__).resolve().parents[4]
        / "openjiuwen"
        / "deepagents"
        / "tools"
        / "browser_move"
        / ".env"
    )
    if env_file.exists():
        load_dotenv(env_file, override=override)


def _write_red_png(path: Path) -> None:
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00"
        b"\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00"
        b"\xc9\xfe\x92\xef"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_create_vision_tools_register_and_invoke(monkeypatch):
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
            return "HELLO WORLD", "mock-model"
        return "The image text says HELLO WORLD.", "mock-model"

    monkeypatch.setattr(
        "openjiuwen.deepagents.tools.vision._call_vision_model",
        fake_call_vision_model,
    )

    async def _run():
        await Runner.start()
        tools = create_vision_tools(vision_model_config=vision_model_config)
        try:
            Runner.resource_mgr.add_tool(tools)
            registered_tool = Runner.resource_mgr.get_tool("VisualQuestionAnsweringTool")
            assert registered_tool is not None

            result = await registered_tool.invoke(
                {
                    "image_path_or_url": "https://example.com/image.png",
                    "question": "What text is shown?",
                }
            )
            return result, [tool.card.name for tool in tools]
        finally:
            for tool in tools:
                Runner.resource_mgr.remove_tool(tool.card.id)
            await Runner.stop()

    result, tool_names = asyncio.run(_run())

    assert tool_names == ["image_ocr", "visual_question_answering"]
    assert result.success is True
    assert result.data["ocr_text"] == "HELLO WORLD"
    assert result.data["answer"] == "The image text says HELLO WORLD."
    assert len(prompts) == 2


def test_runner_stop_clears_registered_vision_tools():
    async def _run():
        await Runner.start()
        tools = create_vision_tools()
        add_results = Runner.resource_mgr.add_tool(tools)
        assert all(result.is_ok() for result in add_results)
        assert Runner.resource_mgr.get_tool("VisualQuestionAnsweringTool") is not None
        await Runner.stop()
        return Runner.resource_mgr.get_tool("VisualQuestionAnsweringTool")

    leaked_tool = asyncio.run(_run())

    assert leaked_tool is None


@pytest.mark.skipif(
    not _vision_system_tests_enabled(),
    reason="Set RUN_VISION_SYSTEM_TESTS=1 to run live vision system tests.",
)
def test_create_vision_tools_with_real_api_from_env(tmp_path: Path):
    _load_vision_repo_dotenv(override=True)
    vision_model_config = VisionModelConfig.from_env()
    if not vision_model_config.api_key:
        pytest.skip(
            "Missing vision API configuration. Set VISION_API_KEY, OPENROUTER_API_KEY, or OPENAI_API_KEY in env."
        )

    image_path = tmp_path / "red.png"
    _write_red_png(image_path)

    async def _run():
        await Runner.start()
        tools = create_vision_tools(
            language="en",
            vision_model_config=vision_model_config,
        )
        try:
            Runner.resource_mgr.add_tool(tools)
            registered_tool = Runner.resource_mgr.get_tool("VisualQuestionAnsweringTool")
            assert registered_tool is not None

            return await registered_tool.invoke(
                {
                    "image_path_or_url": str(image_path),
                    "question": "What is the dominant color in this image?",
                    "include_ocr": False,
                }
            )
        finally:
            for tool in tools:
                Runner.resource_mgr.remove_tool(tool.card.id)
            await Runner.stop()

    result = asyncio.run(_run())

    assert result.success is True
    assert result.data["model"]
    assert result.data["answer"]
