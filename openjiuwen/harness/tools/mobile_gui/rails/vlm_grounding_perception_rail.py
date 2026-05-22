# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import base64
import io
from typing import Any

from PIL import Image

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
    InvokeInputs,
    ModelCallInputs,
)
from openjiuwen.harness.tools.mobile_gui.config import MobileGuiRuntimeSettings
from openjiuwen.harness.tools.mobile_gui.rails.vlm_rail_utils import (
    GOAL_ANCHOR_KEY,
    VLM_OBSERVATION_META_EXTRA_KEY,
    append_vlm_observation_meta_footer,
)
from openjiuwen.harness.tools.mobile_gui.state import mobile_gui_shared
from openjiuwen.harness.tools.mobile_gui.tool_support import ensure_mobile_gui_session_bridge


class VlmGroundingPerceptionRail(AgentRail):
    """Inject a raw screenshot before each model call for coordinate grounding."""

    priority: int = 90

    def __init__(
        self,
        settings: MobileGuiRuntimeSettings,
        *,
        model_name: str = "",
    ) -> None:
        super().__init__()
        self._settings = settings
        self._max_width = settings.vlm_grounding_max_width
        self._jpeg_quality = settings.vlm_grounding_jpeg_quality
        self._ui_settle_seconds = settings.vlm_grounding_ui_settle_seconds
        self._coordinate_scale = settings.vlm_coordinate_scale
        self._claude_size = (
            settings.vlm_claude_image_width,
            settings.vlm_claude_image_height,
        )
        self._opus_max_dim = settings.vlm_claude_opus_max_dimension
        mn = (model_name or "").lower()
        self._use_adaptive_resize = "opus-4" in mn or "opus_4" in mn
        self._use_claude_size = "claude" in mn and not self._use_adaptive_resize
        self._use_unit_scale = "kimi-k" in mn
        self._only_settle_after_tools = settings.vlm_grounding_only_settle_after_tools

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        if isinstance(ctx.inputs, InvokeInputs) and ctx.inputs.query:
            q = ctx.inputs.query
            ctx.extra["pinned_user_goal"] = q
            mobile_gui_shared["pinned_user_goal"] = q

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, ModelCallInputs):
            return

        ensure_mobile_gui_session_bridge(ctx)
        device = ctx.extra.get("device_handle")
        if device is None:
            logger.warning(
                "[VlmGroundingPerceptionRail] device_handle missing; skipping screenshot"
            )
            return

        await self._maybe_wait_ui_settle(ctx)

        screenshot, displayed_screenshot, screenshot_base64 = self._capture_raw_screenshot(
            device
        )
        if screenshot is None:
            return

        scale_x, scale_y = self._active_coordinate_scale(displayed_screenshot)

        foreground_app = self._get_foreground_app(device)
        self._publish_perception_outputs(
            ctx=ctx,
            screenshot=screenshot,
            displayed_screenshot=displayed_screenshot,
            screenshot_base64=screenshot_base64,
            foreground_app=foreground_app,
            scale_x=scale_x,
            scale_y=scale_y,
        )

        observation_msg = self._build_observation_message(
            screenshot_base64=screenshot_base64,
            foreground_app=foreground_app,
            width=screenshot.width,
            height=screenshot.height,
            displayed_width=displayed_screenshot.width,
            displayed_height=displayed_screenshot.height,
            scale_x=scale_x,
            scale_y=scale_y,
        )
        await self._inject_observation_message(ctx, observation_msg)
        self._publish_goal_anchor(ctx)

        logger.info(
            "[VlmGroundingPerceptionRail] screen=%sx%s sent=%sx%s scale=(%s,%s) app=%s msgs=%s",
            screenshot.width,
            screenshot.height,
            displayed_screenshot.width,
            displayed_screenshot.height,
            scale_x,
            scale_y,
            foreground_app,
            len(ctx.inputs.messages),
        )

    async def _maybe_wait_ui_settle(self, ctx: AgentCallbackContext) -> None:
        if self._ui_settle_seconds <= 0:
            return
        if self._only_settle_after_tools:
            msgs = ctx.inputs.messages if isinstance(ctx.inputs, ModelCallInputs) else []
            if not any(getattr(m, "role", None) == "tool" for m in msgs):
                return
        logger.info(
            "[VlmGroundingPerceptionRail] UI settle wait: %ss",
            self._ui_settle_seconds,
        )
        await asyncio.sleep(self._ui_settle_seconds)

    def _capture_raw_screenshot(
        self,
        device: Any,
    ) -> tuple[Image.Image | None, Image.Image | None, str]:
        try:
            img: Image.Image = device.screenshot(format="pillow")
        except Exception as e:
            logger.warning("[VlmGroundingPerceptionRail] screenshot failed: %s", e)
            return None, None, ""

        displayed_img = self._prepare_image_for_model(img)
        return img, displayed_img, self._pil_to_base64(displayed_img)

    def _publish_perception_outputs(
        self,
        ctx: AgentCallbackContext,
        *,
        screenshot: Image.Image,
        displayed_screenshot: Image.Image,
        screenshot_base64: str,
        foreground_app: str,
        scale_x: int,
        scale_y: int,
    ) -> None:
        metadata = {
            "foreground_app": foreground_app,
            "has_screenshot": bool(screenshot_base64),
            "mode": "vlm_grounding",
            "screen_width": screenshot.width,
            "screen_height": screenshot.height,
            "displayed_width": displayed_screenshot.width,
            "displayed_height": displayed_screenshot.height,
            "coordinate_scale_x": scale_x,
            "coordinate_scale_y": scale_y,
        }
        ctx.extra["foreground_app"] = foreground_app
        ctx.extra["vlm_grounding_base64"] = screenshot_base64
        ctx.extra["vlm_screen_width"] = screenshot.width
        ctx.extra["vlm_screen_height"] = screenshot.height
        ctx.extra["vlm_displayed_width"] = displayed_screenshot.width
        ctx.extra["vlm_displayed_height"] = displayed_screenshot.height
        ctx.extra["vlm_coordinate_scale_x"] = scale_x
        ctx.extra["vlm_coordinate_scale_y"] = scale_y
        ctx.extra["vlm_coordinate_scale"] = scale_x if scale_x == scale_y else None
        ctx.extra[VLM_OBSERVATION_META_EXTRA_KEY] = metadata

        mobile_gui_shared["foreground_app"] = foreground_app
        mobile_gui_shared["vlm_screen_width"] = screenshot.width
        mobile_gui_shared["vlm_screen_height"] = screenshot.height
        mobile_gui_shared["vlm_displayed_width"] = displayed_screenshot.width
        mobile_gui_shared["vlm_displayed_height"] = displayed_screenshot.height
        mobile_gui_shared["vlm_coordinate_scale_x"] = scale_x
        mobile_gui_shared["vlm_coordinate_scale_y"] = scale_y
        mobile_gui_shared["vlm_coordinate_scale"] = scale_x if scale_x == scale_y else None

    def _build_observation_message(
        self,
        *,
        screenshot_base64: str,
        foreground_app: str,
        width: int,
        height: int,
        displayed_width: int,
        displayed_height: int,
        scale_x: int,
        scale_y: int,
    ) -> UserMessage:
        coordinate_text = self._coordinate_instruction(scale_x, scale_y)
        body = (
            f"Current foreground app: {foreground_app}\n"
            f"Original screen size: {width}x{height}\n"
            f"Screenshot sent to the model: {displayed_width}x{displayed_height}\n"
            f"{coordinate_text}\n"
            "Use the coordinate tools directly; choosing the coordinate is part of "
            "this same model step, not a separate grounding call."
        )
        observation_text = append_vlm_observation_meta_footer(
            base_text=body,
            foreground_app=foreground_app,
        )

        multimodal_content: list[dict[str, Any]] = [
            {"type": "text", "text": observation_text},
        ]
        if screenshot_base64:
            multimodal_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{screenshot_base64}",
                        "detail": "high",
                    },
                }
            )

        return UserMessage(content=multimodal_content)

    async def _inject_observation_message(
        self,
        ctx: AgentCallbackContext,
        observation_msg: UserMessage,
    ) -> None:
        if not ctx.context:
            ctx.inputs.messages = list(ctx.inputs.messages) + [observation_msg]
            return

        ctx_messages = ctx.context.get_messages()
        last_msg = ctx_messages[-1] if ctx_messages else None
        if last_msg is not None and last_msg.role == "user":
            popped = ctx.context.pop_messages(1)
            last_user_msg = popped[0]
            merged = self._to_content_blocks(last_user_msg.content)
            obs_blocks = self._to_content_blocks(observation_msg.content)
            last_user_msg.content = merged + obs_blocks
            await ctx.context.add_messages(last_user_msg)

            new_inputs = list(ctx.inputs.messages)
            for i in range(len(new_inputs) - 1, -1, -1):
                if new_inputs[i].role == "user":
                    new_inputs[i] = last_user_msg
                    break
            ctx.inputs.messages = new_inputs
            return

        await ctx.context.add_messages(observation_msg)
        ctx.inputs.messages = list(ctx.inputs.messages) + [observation_msg]

    def _publish_goal_anchor(self, ctx: AgentCallbackContext) -> None:
        pinned_goal = ctx.extra.get("pinned_user_goal") or mobile_gui_shared.get(
            "pinned_user_goal"
        )
        if not pinned_goal:
            ctx.extra.pop(GOAL_ANCHOR_KEY, None)
            return
        goal_norm = str(pinned_goal).strip()
        if not goal_norm:
            ctx.extra.pop(GOAL_ANCHOR_KEY, None)
            return
        if ctx.context is None:
            ctx.extra.pop(GOAL_ANCHOR_KEY, None)
            return

        dialogue_round = self._settings.context_default_window_round_num
        windowed = self._messages_in_round_window(
            ctx.context.get_messages(), dialogue_round
        )
        if self._goal_text_in_user_messages(goal_norm, windowed):
            ctx.extra.pop(GOAL_ANCHOR_KEY, None)
        else:
            ctx.extra[GOAL_ANCHOR_KEY] = UserMessage(
                content=f"[Task Goal] Original query: {pinned_goal}"
            )

    @staticmethod
    def _messages_in_round_window(messages: list[Any], dialogue_round: int) -> list[Any]:
        if dialogue_round is None or dialogue_round <= 0:
            return list(messages)
        rounds = ContextUtils.find_all_dialogue_round(messages)
        if not rounds:
            return list(messages)
        round_index = ContextUtils.find_last_n_dialogue_round(messages, dialogue_round)
        if round_index < 0:
            return list(messages)
        return list(messages[round_index:])

    @staticmethod
    def _goal_text_in_user_messages(goal: str, messages: list[Any]) -> bool:
        for msg in messages:
            if getattr(msg, "role", None) != "user":
                continue
            if goal in VlmGroundingPerceptionRail._flatten_message_text(msg):
                return True
        return False

    @staticmethod
    def _flatten_message_text(msg: Any) -> str:
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
            return "\n".join(parts)
        return str(content or "")

    @staticmethod
    def _to_content_blocks(content: Any) -> list[dict[str, Any]]:
        if isinstance(content, str):
            return [{"type": "text", "text": content}]
        if isinstance(content, list):
            return list(content)
        return [{"type": "text", "text": str(content)}]

    def _prepare_image_for_model(self, img: Image.Image) -> Image.Image:
        if self._use_adaptive_resize:
            return self._adaptive_resize(img, self._opus_max_dim)
        if self._use_claude_size:
            return img.resize(self._claude_size, Image.LANCZOS)
        return self._resize_to_max_width(img)

    def _active_coordinate_scale(self, displayed_img: Image.Image) -> tuple[int, int]:
        if self._use_adaptive_resize or self._use_claude_size:
            return displayed_img.size
        if self._use_unit_scale:
            return (1, 1)
        return (self._coordinate_scale, self._coordinate_scale)

    def _coordinate_instruction(self, scale_x: int, scale_y: int) -> str:
        if scale_x == scale_y:
            return (
                f"Coordinates are normalized numbers in [0, {scale_x}] for both axes; "
                "(0, 0) is top-left and max scale is bottom-right."
            )
        return (
            f"Coordinates are pixel positions on the screenshot sent to the model: "
            f"x in [0, {scale_x}], y in [0, {scale_y}]."
        )

    def _resize_to_max_width(self, img: Image.Image) -> Image.Image:
        if img.mode != "RGB":
            img = img.convert("RGB")

        if img.width > self._max_width:
            ratio = self._max_width / img.width
            new_size = (self._max_width, int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)

        return img

    @staticmethod
    def _adaptive_resize(img: Image.Image, max_dimension: int) -> Image.Image:
        max_dim = max(img.width, img.height)
        if max_dim <= max_dimension:
            return img

        scale = max_dimension / max_dim
        new_size = (int(img.width * scale), int(img.height * scale))
        return img.resize(new_size, Image.LANCZOS)

    def _pil_to_base64(self, img: Image.Image) -> str:
        if img.mode != "RGB":
            img = img.convert("RGB")

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self._jpeg_quality)
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    @staticmethod
    def _get_foreground_app(device: Any) -> str:
        try:
            info = device.app_current()
            return info.get("package", "Unknown")
        except Exception as e:
            logger.warning("[VlmGroundingPerceptionRail] foreground app: %s", e)
            return "Unknown"
