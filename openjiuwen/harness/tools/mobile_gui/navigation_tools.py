# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
from typing import Any, List, Tuple

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

from openjiuwen.harness.tools.mobile_gui.config import MobileGuiRuntimeSettings
from openjiuwen.harness.tools.mobile_gui.tool_support import (
    get_device_handle,
    get_shared_extra,
)

_VALID_DIRECTIONS = {"up", "down", "left", "right"}


def build_scroll_tool(settings: MobileGuiRuntimeSettings) -> Tool:
    default_w = settings.scroll_default_width
    default_h = settings.scroll_default_height
    dur_default = settings.scroll_duration_ms_default

    scroll_card = ToolCard(
        id="tool.mobile.scroll",
        name="scroll",
        description=(
            "Scroll the screen to view content outside the current viewport.\n"
            "Direction semantics:\n"
            '- "down": see content below (swipe up)\n'
            '- "up": see content above (swipe down)\n'
            '- "left" / "right": horizontal\n'
            'More content at bottom → use direction="down".'
        ),
        input_params={
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["up", "down", "left", "right"],
                    "description": "Scroll direction.",
                },
                "duration_ms": {
                    "type": "integer",
                    "description": f"Animation duration in ms (default {dur_default}).",
                    "default": dur_default,
                },
            },
            "required": ["direction"],
        },
    )

    def _get_screen_resolution(device: Any) -> Tuple[int, int]:
        try:
            w, h = device.window_size()
            return w, h
        except Exception as e:
            logger.warning(
                "[mobile_gui.scroll] window_size failed: %s; using %sx%s",
                e,
                default_w,
                default_h,
            )
            return default_w, default_h

    async def scroll_action(
        direction: str,
        ctx: AgentCallbackContext,
        duration_ms: int | None = None,
    ) -> str:
        try:
            if duration_ms is None:
                duration_ms = dur_default
            extra = get_shared_extra(ctx)
            device, error = get_device_handle(extra)
            if error:
                return error
            direction = direction.strip().lower()
            if direction not in _VALID_DIRECTIONS:
                return (
                    f"Error: InvalidDirection: '{direction}'. "
                    "Use up, down, left, right."
                )
            width, height = _get_screen_resolution(device)
            center_x = width // 2
            center_y = height // 2
            if direction == "down":
                start_x, start_y = center_x, int(height * 0.8)
                end_x, end_y = center_x, int(height * 0.2)
            elif direction == "up":
                start_x, start_y = center_x, int(height * 0.2)
                end_x, end_y = center_x, int(height * 0.8)
            elif direction == "left":
                start_x, start_y = int(width * 0.8), center_y
                end_x, end_y = int(width * 0.2), center_y
            else:
                start_x, start_y = int(width * 0.2), center_y
                end_x, end_y = int(width * 0.8), center_y
            try:
                device.swipe(start_x, start_y, end_x, end_y, duration=duration_ms / 1000.0)
                return f"Success: Scrolled {direction} ({width}x{height}, {duration_ms}ms)"
            except Exception as e:
                return f"Error [Swipe Stage]: {e}"
        except Exception as e:
            return f"Error: {type(e).__name__}: {str(e)}"

    class ScrollTool(Tool):
        def __init__(self) -> None:
            super().__init__(card=scroll_card)

        async def invoke(self, inputs: Any, **kwargs: Any) -> str:
            ctx = kwargs.get("ctx")
            if isinstance(inputs, dict):
                direction = inputs.get("direction", "down")
                duration_ms = inputs.get("duration_ms", dur_default)
            else:
                direction = getattr(inputs, "direction", "down")
                duration_ms = getattr(inputs, "duration_ms", dur_default)
            return await scroll_action(direction=direction, ctx=ctx, duration_ms=duration_ms)

        async def stream(self, inputs: Any, **kwargs: Any):
            yield await self.invoke(inputs, **kwargs)

    return ScrollTool()


def build_press_back_tool() -> Tool:
    card = ToolCard(
        id="tool.mobile.press_back",
        name="press_back",
        description="Press Android Back key.",
        input_params={"type": "object", "properties": {}},
    )

    async def action(ctx: AgentCallbackContext) -> str:
        try:
            extra = get_shared_extra(ctx)
            device, error = get_device_handle(extra)
            if error:
                return error
            try:
                device.press("back")
                return "Success: Pressed BACK key"
            except Exception as e:
                return f"Error [PressKey Stage]: {e}"
        except Exception as e:
            return f"Error: {type(e).__name__}: {str(e)}"

    class PressBackTool(Tool):
        def __init__(self) -> None:
            super().__init__(card=card)

        async def invoke(self, inputs: Any, **kwargs: Any) -> str:
            return await action(kwargs.get("ctx"))

        async def stream(self, inputs: Any, **kwargs: Any):
            yield await self.invoke(inputs, **kwargs)

    return PressBackTool()


def build_press_home_tool() -> Tool:
    card = ToolCard(
        id="tool.mobile.press_home",
        name="press_home",
        description="Press Android Home key.",
        input_params={"type": "object", "properties": {}},
    )

    async def action(ctx: AgentCallbackContext) -> str:
        try:
            extra = get_shared_extra(ctx)
            device, error = get_device_handle(extra)
            if error:
                return error
            try:
                device.press("home")
                return "Success: Pressed HOME key"
            except Exception as e:
                return f"Error [PressKey Stage]: {e}"
        except Exception as e:
            return f"Error: {type(e).__name__}: {str(e)}"

    class PressHomeTool(Tool):
        def __init__(self) -> None:
            super().__init__(card=card)

        async def invoke(self, inputs: Any, **kwargs: Any) -> str:
            return await action(kwargs.get("ctx"))

        async def stream(self, inputs: Any, **kwargs: Any):
            yield await self.invoke(inputs, **kwargs)

    return PressHomeTool()


def build_press_enter_tool() -> Tool:
    card = ToolCard(
        id="tool.mobile.press_enter",
        name="press_enter",
        description="Press Android Enter / Search key.",
        input_params={"type": "object", "properties": {}},
    )

    async def action(ctx: AgentCallbackContext) -> str:
        try:
            extra = get_shared_extra(ctx)
            device, error = get_device_handle(extra)
            if error:
                return error
            try:
                device.press("enter")
                return "Success: Pressed ENTER key"
            except Exception as e:
                return f"Error [PressKey Stage]: {e}"
        except Exception as e:
            return f"Error: {type(e).__name__}: {str(e)}"

    class PressEnterTool(Tool):
        def __init__(self) -> None:
            super().__init__(card=card)

        async def invoke(self, inputs: Any, **kwargs: Any) -> str:
            return await action(kwargs.get("ctx"))

        async def stream(self, inputs: Any, **kwargs: Any):
            yield await self.invoke(inputs, **kwargs)

    return PressEnterTool()


def build_wait_gui_load_tool(settings: MobileGuiRuntimeSettings) -> Tool:
    mn = settings.wait_gui_load_min_seconds
    mx = settings.wait_gui_load_max_seconds
    default_s = settings.wait_gui_load_default_seconds

    card = ToolCard(
        id="tool.mobile.wait_gui_load",
        name="wait_gui_load",
        description=(
            "Wait briefly when the screenshot shows loading UI "
            "(spinner, progress, skeleton). Do not use when UI is stable."
        ),
        input_params={
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "number",
                    "description": f"Seconds to wait ({mn}–{mx}), default {default_s}.",
                    "default": default_s,
                },
            },
        },
    )

    async def action(ctx: AgentCallbackContext, seconds: float | None) -> str:
        try:
            extra = get_shared_extra(ctx)
            _d, error = get_device_handle(extra)
            if error:
                return error
            if seconds is None:
                wait_s = default_s
            else:
                try:
                    wait_s = float(seconds)
                except (TypeError, ValueError):
                    return f"Error: InvalidSeconds: {seconds!r}"
            wait_s = max(mn, min(mx, wait_s))
            await asyncio.sleep(wait_s)
            return f"Success: Waited {wait_s:g}s for GUI load."
        except Exception as e:
            return f"Error: {type(e).__name__}: {str(e)}"

    class WaitTool(Tool):
        def __init__(self) -> None:
            super().__init__(card=card)

        async def invoke(self, inputs: Any, **kwargs: Any) -> str:
            ctx = kwargs.get("ctx")
            sec = None
            if isinstance(inputs, dict):
                sec = inputs.get("seconds")
            elif inputs is not None:
                sec = getattr(inputs, "seconds", None)
            return await action(ctx, sec)

        async def stream(self, inputs: Any, **kwargs: Any):
            yield await self.invoke(inputs, **kwargs)

    return WaitTool()


def build_navigation_tools(settings: MobileGuiRuntimeSettings) -> List[Tool]:
    return [
        build_scroll_tool(settings),
        build_press_back_tool(),
        build_press_home_tool(),
        build_press_enter_tool(),
        build_wait_gui_load_tool(settings),
    ]
