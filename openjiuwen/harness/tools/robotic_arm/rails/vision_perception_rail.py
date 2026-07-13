# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import base64
import io
from typing import Any

from PIL import Image

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail, ModelCallInputs
from openjiuwen.harness.tools.robotic_arm.config import RoboticArmRuntimeSettings


class VisionPerceptionRail(AgentRail):
    """Capture a fresh photo before every model call and inject it as the observation.

    Mirrors ``mobile_gui.VlmGroundingPerceptionRail``'s "re-perceive every turn"
    design, adapted for a physical arm: the raw (uncompressed) frame is kept on
    ``ctx.extra["vlm_raw_frame"]`` for ``StepExecutorRail``'s ``SubTaskExecutor``
    to consume, while a resized/compressed copy is what actually gets sent to the
    model. The same message also carries the goal and the last reported plan, so
    both stay visible even if the rolling context window truncates older turns.

    ``settings.step_executor`` is expected to already be resolved (see
    ``rails_factory._resolve_step_executor``) and validated (see
    ``StepExecutorRail``) by the time this rail is constructed -- this rail only
    ever touches the inner ReActAgent's own callback context, it never needs the
    outer DeepAgent's ``before_invoke``.
    """

    priority: int = 90

    def __init__(self, settings: RoboticArmRuntimeSettings, *, model_name: str = "") -> None:
        super().__init__()
        self._step_executor = settings.step_executor
        self._max_width = settings.vlm_grounding_max_width
        self._jpeg_quality = settings.vlm_grounding_jpeg_quality
        self._coordinate_scale = settings.vlm_coordinate_scale
        del model_name  # reserved for future per-model image sizing, mirroring mobile_gui

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, ModelCallInputs):
            return

        self._ensure_pinned_goal(ctx)

        if self._step_executor is None:
            logger.warning("[VisionPerceptionRail] step_executor missing; skipping photo capture")
            return

        raw_frame, displayed_frame, frame_base64 = self._capture_frame(self._step_executor)
        if raw_frame is None:
            return

        ctx.extra["vlm_raw_frame"] = raw_frame

        observation_msg = self._build_observation_message(ctx, frame_base64, raw_frame)
        await self._inject_observation_message(ctx, observation_msg)

        logger.info(
            "[VisionPerceptionRail] frame=%sx%s displayed=%sx%s msgs=%s",
            raw_frame.width,
            raw_frame.height,
            displayed_frame.width,
            displayed_frame.height,
            len(ctx.inputs.messages),
        )

    def _ensure_pinned_goal(self, ctx: AgentCallbackContext) -> None:
        """Cache the original user goal in ``ctx.extra`` on first use.

        ``ctx.extra`` persists across every event within one inner ReActAgent
        invoke, so this only runs the message-history scan once per invoke --
        no outer-layer ``before_invoke`` hook is needed to seed it.
        """
        if ctx.extra.get("pinned_user_goal") or ctx.context is None:
            return
        first_user_msg = next((m for m in ctx.context.get_messages() if m.role == "user"), None)
        if first_user_msg is not None:
            ctx.extra["pinned_user_goal"] = self._extract_text(first_user_msg)

    def _capture_frame(self, step_executor: Any) -> tuple[Image.Image | None, Image.Image | None, str]:
        try:
            frame = step_executor.capture()
        except Exception:
            logger.exception("[VisionPerceptionRail] capture failed")
            return None, None, ""

        if frame.mode != "RGB":
            frame = frame.convert("RGB")
        displayed = self._resize_to_max_width(frame)
        return frame, displayed, self._pil_to_base64(displayed)

    def _resize_to_max_width(self, img: Image.Image) -> Image.Image:
        if img.width <= self._max_width:
            return img
        ratio = self._max_width / img.width
        return img.resize((self._max_width, int(img.height * ratio)), Image.LANCZOS)

    def _pil_to_base64(self, img: Image.Image) -> str:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self._jpeg_quality)
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    def _build_observation_message(
        self,
        ctx: AgentCallbackContext,
        frame_base64: str,
        raw_frame: Image.Image,
    ) -> UserMessage:
        pinned_goal = ctx.extra.get("pinned_user_goal")
        last_plan = ctx.extra.get("last_plan_summary")

        lines = [
            f"[Task Goal] {pinned_goal}" if pinned_goal else "",
            f"[Last Reported Plan]\n{last_plan}" if last_plan else "[Last Reported Plan] none yet -- call report_plan.",
            f"Photo resolution: {raw_frame.width}x{raw_frame.height}.",
            f"Coordinates are normalized numbers in [0, {self._coordinate_scale}] for both axes; "
            "(0, 0) is top-left, max is bottom-right.",
            "Call report_plan with the FULL sub-task list every turn. The sub-task marked "
            "in_progress is executed automatically right after you submit the plan -- there is "
            "no separate action tool to call.",
        ]
        body = "\n".join(line for line in lines if line)

        content: list[dict[str, Any]] = [{"type": "text", "text": body}]
        if frame_base64:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{frame_base64}", "detail": "high"},
                }
            )
        return UserMessage(content=content)

    async def _inject_observation_message(self, ctx: AgentCallbackContext, observation_msg: UserMessage) -> None:
        if not ctx.context:
            ctx.inputs.messages = list(ctx.inputs.messages) + [observation_msg]
            return

        ctx_messages = ctx.context.get_messages()
        last_msg = ctx_messages[-1] if ctx_messages else None
        if last_msg is not None and last_msg.role == "user":
            popped = ctx.context.pop_messages(1)
            last_user_msg = popped[0]
            merged = self._to_content_blocks(last_user_msg.content) + self._to_content_blocks(observation_msg.content)
            last_user_msg.content = merged
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

    @staticmethod
    def _extract_text(msg: Any) -> str:
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


__all__ = ["VisionPerceptionRail"]
