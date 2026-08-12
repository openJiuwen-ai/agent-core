#!/usr/bin/env python
# coding: utf-8
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from unittest.mock import AsyncMock

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails
from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import (
    build_screenshot_js,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import (
    BrowserAgentRuntime,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime_tools import (
    BrowserVisionTool,
    build_browser_runtime_tools,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.vision_image import (
    prepare_screenshot,
)

# 1x1 JPEG, enough to exercise decode paths without a fixture file.
_TINY_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    "AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)


def _run(coro):
    return asyncio.run(coro)


def _make_runtime() -> BrowserAgentRuntime:
    mcp_cfg = McpServerConfig(
        server_id="test-playwright-vision",
        server_name="test-playwright-vision",
        server_path="stdio://playwright",
        client_type="stdio",
        params={"cwd": str(Path.cwd())},
    )

    return BrowserAgentRuntime(
        provider="openai",
        api_key="test-key",
        api_base="https://example.invalid/v1",
        model_name="test-model",
        mcp_cfg=mcp_cfg,
        guardrails=BrowserRunGuardrails(
            max_steps=3,
            max_failures=1,
            timeout_s=30,
            retry_once=False,
        ),
    )


def _capture(**overrides):
    data = {
        "ok": True,
        "error": None,
        "url": "https://example.test/report",
        "title": "Quarterly report",
        "viewport": {"width": 1280, "height": 720, "scroll_x": 0, "scroll_y": 0},
        "full_page": False,
        "image_base64": "AAAA",
        "data_url": "data:image/jpeg;base64,AAAA",
        "image_width": 1280,
        "image_height": 720,
        "downscaled": False,
        "approx_bytes": 3,
    }
    data.update(overrides)
    return data


def test_build_screenshot_js_encodes_buffer_as_base64() -> None:
    js = build_screenshot_js(full_page=False, quality=60)

    assert "page.screenshot" in js
    assert "toString('base64')" in js
    assert '"quality": 60' in js
    assert "device_pixel_ratio" in js


def test_build_screenshot_js_clamps_quality() -> None:
    assert '"quality": 90' in build_screenshot_js(quality=500)
    assert '"quality": 20' in build_screenshot_js(quality=1)


def test_vision_tool_keeps_base64_out_of_the_tool_message() -> None:
    runtime = _make_runtime()
    runtime.capture_screenshot = AsyncMock(return_value=_capture())
    tool = BrowserVisionTool(runtime, language="en")

    result = _run(tool.invoke({"reason": "peak revenue"}))

    assert result.success is True
    # The model-visible text must describe the capture without carrying it.
    assert "data:image/jpeg" not in result.data["content"]
    assert "peak revenue" in result.data["content"]
    assert result.data["multimodal"][0]["data_url"].startswith("data:image/jpeg;base64,")

    # This is the contract that keeps the payload out of the ToolMessage.
    message_content = AbilityManager._build_tool_message_content(result)
    assert "data:image/jpeg" not in message_content
    assert message_content == result.data["content"]


def test_vision_tool_result_is_delivered_as_an_image_user_message() -> None:
    runtime = _make_runtime()
    runtime.capture_screenshot = AsyncMock(return_value=_capture())
    tool = BrowserVisionTool(runtime, language="en")

    result = _run(tool.invoke({}))
    message = ReActAgent._build_multimodal_tool_results_message([result])

    assert message is not None
    assert message.role == "user"
    image_blocks = [b for b in message.content if b.get("type") == "image_url"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_vision_tool_passes_full_page_flag_and_parses_string_form() -> None:
    runtime = _make_runtime()
    runtime.capture_screenshot = AsyncMock(return_value=_capture(full_page=True))
    tool = BrowserVisionTool(runtime, language="en")

    result = _run(tool.invoke({"full_page": "true"}))

    runtime.capture_screenshot.assert_called_once_with(full_page=True)
    assert "full scrollable page" in result.data["content"]


def test_vision_tool_reports_capture_failure() -> None:
    runtime = _make_runtime()
    runtime.capture_screenshot = AsyncMock(
        return_value={"ok": False, "error": "browser_code_executor_not_ready", "image_base64": ""}
    )
    tool = BrowserVisionTool(runtime, language="en")

    result = _run(tool.invoke({}))

    assert result.success is False
    assert result.error == "browser_code_executor_not_ready"


def test_capture_screenshot_unwraps_and_prepares_the_image() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = AsyncMock(return_value=None)
    runtime._code_executor = AsyncMock(
        return_value={
            "content": [
                {
                    "type": "text",
                    "text": (
                        '{"ok": true, "url": "https://example.test/", "title": "T", '
                        '"viewport": {"width": 800, "height": 600, "scroll_y": 0}, '
                        '"full_page": false, "image_base64": "' + _TINY_JPEG_B64 + '"}'
                    ),
                }
            ]
        }
    )

    data = _run(runtime.capture_screenshot())

    assert data["ok"] is True
    assert data["url"] == "https://example.test/"
    assert data["data_url"].startswith("data:image/jpeg;base64,")
    assert data["image_base64"] == _TINY_JPEG_B64


def test_capture_screenshot_fails_without_a_code_executor() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = AsyncMock(return_value=None)
    runtime._code_executor = None

    data = _run(runtime.capture_screenshot())

    assert data["ok"] is False
    assert data["error"] == "browser_code_executor_not_ready"


def test_capture_screenshot_reports_missing_image_data() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = AsyncMock(return_value=None)
    runtime._code_executor = AsyncMock(return_value='{"ok": true, "image_base64": ""}')

    data = _run(runtime.capture_screenshot())

    assert data["ok"] is False
    assert "no image data" in data["error"]


def test_prepare_screenshot_passes_small_captures_through() -> None:
    prepared = prepare_screenshot(_TINY_JPEG_B64, max_dimension=1280)

    assert prepared.base64_jpeg == _TINY_JPEG_B64
    assert prepared.downscaled is False
    assert prepared.data_url.startswith("data:image/jpeg;base64,")


def test_prepare_screenshot_survives_undecodable_input() -> None:
    prepared = prepare_screenshot("not-base64-at-all", max_dimension=64)

    # A slightly wrong screenshot beats no screenshot: the capture is passed through.
    assert prepared.base64_jpeg == "not-base64-at-all"
    assert prepared.downscaled is False


def test_prepare_screenshot_downscales_when_pillow_is_available() -> None:
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - exercised only where the extra is absent
        return

    import io

    buffer = io.BytesIO()
    Image.new("RGB", (2000, 1000), (10, 20, 30)).save(buffer, format="JPEG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")

    prepared = prepare_screenshot(encoded, max_dimension=500)

    assert prepared.downscaled is True
    assert prepared.width == 500
    assert prepared.height == 250
    assert len(prepared.base64_jpeg) < len(encoded)


def test_browser_vision_is_registered_as_a_runtime_tool() -> None:
    tools = build_browser_runtime_tools(_make_runtime(), language="en")
    names = [tool.card.name for tool in tools]

    assert "browser_vision" in names

    card = next(tool.card for tool in tools if tool.card.name == "browser_vision")
    # The description must keep the probes as the default path.
    assert "chart" in card.description.lower()
    assert "browser_probe_cards" in card.description


def test_vision_tool_output_is_a_tool_output() -> None:
    runtime = _make_runtime()
    runtime.capture_screenshot = AsyncMock(return_value=_capture())
    tool = BrowserVisionTool(runtime, language="en")

    result = _run(tool.invoke({}))

    assert isinstance(result, ToolOutput)
