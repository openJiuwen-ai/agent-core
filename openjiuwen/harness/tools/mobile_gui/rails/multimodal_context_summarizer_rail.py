# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
    ModelCallInputs,
)
from openjiuwen.harness.tools.mobile_gui.rails.multimodal_skill_read_rail import (
    USER_MESSAGES_PROTECTED_FROM_SCREENSHOT_ARCHIVE,
)

ARCHIVED_SCREEN_PLACEHOLDER = (
    "Screenshot from an earlier step is no longer attached to save context. "
    "The UI may have changed since then; rely on the latest user message with an image for the current screen. "
    "If you need to reason about that past state, summarize what you already inferred from it in prior turns."
)


def _has_image_url(msg) -> bool:
    if not isinstance(msg.content, list):
        return False
    return any(
        isinstance(b, dict) and b.get("type") == "image_url"
        for b in msg.content
    )


def _replace_archived_screenshot_images(msg) -> None:
    """Swap removed screenshot blobs for explicit placeholder text (keeps multimodal shape)."""
    if not isinstance(msg.content, list):
        return
    next_content: list = []
    for block in msg.content:
        if isinstance(block, dict) and block.get("type") == "image_url":
            next_content.append({"type": "text", "text": ARCHIVED_SCREEN_PLACEHOLDER})
        else:
            next_content.append(block)
    if not next_content:
        next_content = [{"type": "text", "text": ARCHIVED_SCREEN_PLACEHOLDER}]
    msg.content = next_content


class MultimodalContextSummarizerRail(AgentRail):
    """Replace image_url on older screenshot user turns with a fixed placeholder."""

    priority: int = 85

    def __init__(self, screenshots_to_keep: int = 3) -> None:
        super().__init__()
        self._screenshots_to_keep = screenshots_to_keep

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, ModelCallInputs):
            return
        if ctx.context is None:
            return
        self._archive_old_screenshot_images(ctx)

    def _archive_old_screenshot_images(self, ctx: AgentCallbackContext) -> None:
        all_msgs = ctx.context.get_messages()
        if not all_msgs:
            return

        screenshot_indices: list[int] = []
        for i, msg in enumerate(all_msgs):
            if msg.role != "user":
                continue
            if not _has_image_url(msg):
                continue
            if getattr(msg, "name", None) in USER_MESSAGES_PROTECTED_FROM_SCREENSHOT_ARCHIVE:
                continue
            screenshot_indices.append(i)

        if len(screenshot_indices) <= self._screenshots_to_keep:
            return

        old_indices = screenshot_indices[:-self._screenshots_to_keep]
        replaced = 0

        for ss_idx in old_indices:
            user_msg = all_msgs[ss_idx]
            if not _has_image_url(user_msg):
                continue
            _replace_archived_screenshot_images(user_msg)
            replaced += 1

        if replaced:
            ctx.context.set_messages(all_msgs)
            logger.info(
                "[MultimodalContextSummarizerRail] replaced screenshot images in %s older turn(s)",
                replaced,
            )
