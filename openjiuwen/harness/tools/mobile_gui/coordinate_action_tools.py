# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, List

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

from openjiuwen.harness.tools.mobile_gui.config import MobileGuiRuntimeSettings
from openjiuwen.harness.tools.mobile_gui.coordinate_utils import resolve_vlm_pixel
from openjiuwen.harness.tools.mobile_gui.tool_support import (
    get_device_handle,
    get_shared_extra,
)


@dataclass(frozen=True)
class _DragCoordinateInput:
    start_x: Any
    start_y: Any
    end_x: Any
    end_y: Any
    duration: float


def _scalar_number_rule() -> str:
    return (
        "Must be one JSON number per parameter (not an array/tuple and not `[x,y]` packed "
        "into a single field)."
    )


def _one_point_contract(scale: int) -> str:
    return (
        f"Normalized VLM point on the latest screenshot—use the numeric range given in the "
        f"latest observation (default axes [0, {scale}], origin top-left). "
        f'{_scalar_number_rule()} Example: `"x": 450, "y": 145` with both keys present.'
    )


def _drag_endpoints_contract(scale: int) -> str:
    return (
        "Drag uses four independent numbers: fingertip lift-off at "
        '`start_x`/`start_y`, release at `end_x`/`end_y`. '
        f"Same normalized range as observations (default [0, {scale}], top-left origin). "
        f"{_scalar_number_rule()} "
        'Wrong: `"start_x": [100,200]`—each of the four coords must be its own field.'
    )


def _axis_coord_desc(scale: int, *, axis: str, horizontal: bool) -> str:
    plane = (
        "Distance rightward along the screenshot (x-axis)."
        if horizontal
        else "Distance downward along the screenshot (y-axis)."
    )
    return (
        f"{plane} Matches the `{axis}` field in tool arguments—one number only in that field. "
        f"Observation range typically [0, {scale}] unless the observation says otherwise. "
        f"{_scalar_number_rule()}"
    )


def build_coordinate_tool_cards(settings: MobileGuiRuntimeSettings) -> dict[str, ToolCard]:
    s = settings.vlm_coordinate_scale
    return {
        "tap": ToolCard(
            id="tool.mobile.tap_coordinate",
            name="tap_coordinate",
            description=(
                "Tap once at the pixel that corresponds to normalized VLM coordinates on the "
                "current screenshot. Aim for the interactive center of the element. "
                f"{_one_point_contract(s)}"
            ),
            input_params={
                "type": "object",
                "properties": {
                    "x": {
                        "type": "number",
                        "description": _axis_coord_desc(s, axis="x", horizontal=True),
                    },
                    "y": {
                        "type": "number",
                        "description": _axis_coord_desc(s, axis="y", horizontal=False),
                    },
                },
                "required": ["x", "y"],
            },
        ),
        "double_tap": ToolCard(
            id="tool.mobile.double_tap_coordinate",
            name="double_tap_coordinate",
            description=(
                "Double-tap quickly at one normalized screen point without moving between taps. "
                f"{_one_point_contract(s)}"
            ),
            input_params={
                "type": "object",
                "properties": {
                    "x": {
                        "type": "number",
                        "description": _axis_coord_desc(s, axis="x", horizontal=True),
                    },
                    "y": {
                        "type": "number",
                        "description": _axis_coord_desc(s, axis="y", horizontal=False),
                    },
                },
                "required": ["x", "y"],
            },
        ),
        "long_press": ToolCard(
            id="tool.mobile.long_press_coordinate",
            name="long_press_coordinate",
            description=(
                "Press and hold at one normalized coordinate for `duration` seconds (default 1.0). "
                f"{_one_point_contract(s)}"
            ),
            input_params={
                "type": "object",
                "properties": {
                    "x": {
                        "type": "number",
                        "description": _axis_coord_desc(s, axis="x", horizontal=True),
                    },
                    "y": {
                        "type": "number",
                        "description": _axis_coord_desc(s, axis="y", horizontal=False),
                    },
                    "duration": {
                        "type": "number",
                        "description": (
                            "Hold time in seconds (optional; default 1.0). Provide as a standalone number."
                        ),
                    },
                },
                "required": ["x", "y"],
            },
        ),
        "drag": ToolCard(
            id="tool.mobile.drag_coordinate",
            name="drag_coordinate",
            description=(
                "Swipe/drag gesture from normalized start point to normalized end point in one stroke. "
                f"{_drag_endpoints_contract(s)}"
            ),
            input_params={
                "type": "object",
                "properties": {
                    "start_x": {
                        "type": "number",
                        "description": (
                            "Start fingertip horizontal offset; fills `start_x` only — one decimal/integer JSON "
                            f"scalar. {_scalar_number_rule()}"
                        ),
                    },
                    "start_y": {
                        "type": "number",
                        "description": (
                            "Start fingertip vertical offset; fills `start_y` only — one decimal/integer JSON "
                            f"scalar. {_scalar_number_rule()}"
                        ),
                    },
                    "end_x": {
                        "type": "number",
                        "description": (
                            "`end_x` carries only the horizontal end component (never `[end_x,end_y]`). "
                            f"{_scalar_number_rule()}"
                        ),
                    },
                    "end_y": {
                        "type": "number",
                        "description": (
                            "`end_y` carries only the vertical end component. "
                            f"{_scalar_number_rule()}"
                        ),
                    },
                    "duration": {
                        "type": "number",
                        "description": (
                            "Gesture duration in seconds (optional; default 0.5). Standalone numeric field."
                        ),
                    },
                },
                "required": ["start_x", "start_y", "end_x", "end_y"],
            },
        ),
        "type_text": ToolCard(
            id="tool.mobile.type_text",
            name="type_text",
            description=(
                "Type text into the currently focused input field. First use "
                "tap_coordinate to focus the field, then call this tool on the next turn."
            ),
            input_params={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to enter."},
                },
                "required": ["text"],
            },
        ),
    }


async def tap_coordinate_action(x: Any, y: Any, ctx: AgentCallbackContext) -> str:
    try:
        extra = get_shared_extra(ctx)
        device, error = get_device_handle(extra)
        if error:
            return error
        coords, error = resolve_vlm_pixel(extra, x, y)
        if error:
            return error
        px, py = coords

        try:
            device.click(px, py)
            return f"Success: Tapped coordinate ({x}, {y}) at pixel ({px}, {py})"
        except Exception as e:
            return (
                f"Error: ClickFailed: Failed to tap pixel ({px}, {py}). "
                f"Details: {str(e)}"
            )
    except Exception as e:
        return f"Error: {type(e).__name__}: {str(e)}"


async def double_tap_coordinate_action(x: Any, y: Any, ctx: AgentCallbackContext) -> str:
    try:
        extra = get_shared_extra(ctx)
        device, error = get_device_handle(extra)
        if error:
            return error
        coords, error = resolve_vlm_pixel(extra, x, y)
        if error:
            return error
        px, py = coords

        try:
            device.double_click(px, py)
            return f"Success: Double tapped coordinate ({x}, {y}) at pixel ({px}, {py})"
        except Exception as e:
            return (
                f"Error: DoubleClickFailed: Failed to double tap pixel ({px}, {py}). "
                f"Details: {str(e)}"
            )
    except Exception as e:
        return f"Error: {type(e).__name__}: {str(e)}"


async def long_press_coordinate_action(
    x: Any,
    y: Any,
    duration: float,
    ctx: AgentCallbackContext,
) -> str:
    try:
        duration = float(duration)
        extra = get_shared_extra(ctx)
        device, error = get_device_handle(extra)
        if error:
            return error
        coords, error = resolve_vlm_pixel(extra, x, y)
        if error:
            return error
        px, py = coords

        try:
            device.long_click(px, py, duration)
            return (
                f"Success: Long pressed coordinate ({x}, {y}) at pixel "
                f"({px}, {py}) for {duration}s"
            )
        except Exception as e:
            return (
                f"Error: LongPressFailed: Failed to long press pixel ({px}, {py}). "
                f"Details: {str(e)}"
            )
    except Exception as e:
        return f"Error: {type(e).__name__}: {str(e)}"


async def drag_coordinate_action(
    drag: _DragCoordinateInput,
    ctx: AgentCallbackContext,
) -> str:
    try:
        duration = float(drag.duration)
        extra = get_shared_extra(ctx)
        device, error = get_device_handle(extra)
        if error:
            return error
        start_coords, error = resolve_vlm_pixel(extra, drag.start_x, drag.start_y)
        if error:
            return error
        end_coords, error = resolve_vlm_pixel(extra, drag.end_x, drag.end_y)
        if error:
            return error
        sx, sy = start_coords
        ex, ey = end_coords

        try:
            device.drag(sx, sy, ex, ey, duration)
            return (
                f"Success: Dragged from coordinate ({drag.start_x}, {drag.start_y}) to "
                f"({drag.end_x}, {drag.end_y}) over {duration}s"
            )
        except Exception as e:
            return (
                f"Error: DragFailed: Failed to drag from ({sx}, {sy}) to "
                f"({ex}, {ey}). Details: {str(e)}"
            )
    except Exception as e:
        return f"Error: {type(e).__name__}: {str(e)}"


async def type_text_action(text: str, ctx: AgentCallbackContext) -> str:
    try:
        extra = get_shared_extra(ctx)
        device, error = get_device_handle(extra)
        if error:
            return error

        text = "" if text is None else str(text)
        time.sleep(0.5)

        try:
            focused = device(focused=True)
            if focused.exists:
                try:
                    focused.clear_text()
                except Exception as exc:
                    logger.debug("[mobile_gui] focused.clear_text failed: %s", exc)
                focused.set_text(text)
            else:
                device.shell(f"input text '{text}'")
        except Exception as e_focused:
            try:
                device.clear_text()
            except Exception as exc:
                logger.debug("[mobile_gui] device.clear_text failed: %s", exc)
            try:
                device.shell(f"input text '{text}'")
            except Exception as e_shell:
                return (
                    "Error [Input Stage]: all input methods failed. "
                    f"focused error: {str(e_focused)}, shell error: {str(e_shell)}"
                )

        display_text = text if len(text) <= 50 else f"{text[:50]}..."
        return f"Success: Typed '{display_text}' into the focused input"
    except Exception as e:
        return f"Error: {type(e).__name__}: {str(e)}"


def build_coordinate_tools(settings: MobileGuiRuntimeSettings) -> List[Tool]:
    cards = build_coordinate_tool_cards(settings)

    class TapCoordinateTool(Tool):
        def __init__(self, card: ToolCard) -> None:
            super().__init__(card=card)

        async def invoke(self, inputs: Any, **kwargs: Any) -> str:
            ctx = kwargs.get("ctx")
            x = inputs.get("x") if isinstance(inputs, dict) else getattr(inputs, "x", None)
            y = inputs.get("y") if isinstance(inputs, dict) else getattr(inputs, "y", None)
            return await tap_coordinate_action(x=x, y=y, ctx=ctx)

        async def stream(self, inputs: Any, **kwargs: Any):
            yield await self.invoke(inputs, **kwargs)

    class DoubleTapCoordinateTool(Tool):
        def __init__(self, card: ToolCard) -> None:
            super().__init__(card=card)

        async def invoke(self, inputs: Any, **kwargs: Any) -> str:
            ctx = kwargs.get("ctx")
            x = inputs.get("x") if isinstance(inputs, dict) else getattr(inputs, "x", None)
            y = inputs.get("y") if isinstance(inputs, dict) else getattr(inputs, "y", None)
            return await double_tap_coordinate_action(x=x, y=y, ctx=ctx)

        async def stream(self, inputs: Any, **kwargs: Any):
            yield await self.invoke(inputs, **kwargs)

    class LongPressCoordinateTool(Tool):
        def __init__(self, card: ToolCard) -> None:
            super().__init__(card=card)

        async def invoke(self, inputs: Any, **kwargs: Any) -> str:
            ctx = kwargs.get("ctx")
            if isinstance(inputs, dict):
                x = inputs.get("x")
                y = inputs.get("y")
                duration = inputs.get("duration", 1.0)
            else:
                x = getattr(inputs, "x", None)
                y = getattr(inputs, "y", None)
                duration = getattr(inputs, "duration", 1.0)
            return await long_press_coordinate_action(
                x=x,
                y=y,
                duration=duration,
                ctx=ctx,
            )

        async def stream(self, inputs: Any, **kwargs: Any):
            yield await self.invoke(inputs, **kwargs)

    class DragCoordinateTool(Tool):
        def __init__(self, card: ToolCard) -> None:
            super().__init__(card=card)

        async def invoke(self, inputs: Any, **kwargs: Any) -> str:
            ctx = kwargs.get("ctx")
            if isinstance(inputs, dict):
                start_x = inputs.get("start_x")
                start_y = inputs.get("start_y")
                end_x = inputs.get("end_x")
                end_y = inputs.get("end_y")
                duration = inputs.get("duration", 0.5)
            else:
                start_x = getattr(inputs, "start_x", None)
                start_y = getattr(inputs, "start_y", None)
                end_x = getattr(inputs, "end_x", None)
                end_y = getattr(inputs, "end_y", None)
                duration = getattr(inputs, "duration", 0.5)
            return await drag_coordinate_action(
                _DragCoordinateInput(
                    start_x=start_x,
                    start_y=start_y,
                    end_x=end_x,
                    end_y=end_y,
                    duration=duration,
                ),
                ctx=ctx,
            )

        async def stream(self, inputs: Any, **kwargs: Any):
            yield await self.invoke(inputs, **kwargs)

    class TypeTextTool(Tool):
        def __init__(self, card: ToolCard) -> None:
            super().__init__(card=card)

        async def invoke(self, inputs: Any, **kwargs: Any) -> str:
            ctx = kwargs.get("ctx")
            text = (
                inputs.get("text", "")
                if isinstance(inputs, dict)
                else getattr(inputs, "text", "")
            )
            return await type_text_action(text=text, ctx=ctx)

        async def stream(self, inputs: Any, **kwargs: Any):
            yield await self.invoke(inputs, **kwargs)

    c = cards
    return [
        TapCoordinateTool(c["tap"]),
        DoubleTapCoordinateTool(c["double_tap"]),
        LongPressCoordinateTool(c["long_press"]),
        DragCoordinateTool(c["drag"]),
        TypeTextTool(c["type_text"]),
    ]
