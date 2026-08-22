# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Context hygiene for ``browser_vision`` captures.

Delivery itself needs no rail: ``ReActAgent`` already turns a tool result's
``multimodal`` list into a UserMessage after the ToolMessages. What is missing is
eviction — an on-demand screenshot goes stale as soon as the page changes, and
nothing removes it. This rail keeps the most recent captures and replaces older
image blocks with an explicit placeholder, so context stays flat across a long run
and the model is told the old view is outdated rather than silently losing it.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
    ModelCallInputs,
)
from openjiuwen.harness.image_modality_probe import get_cached_image_support

OUTDATED_VIEW_PLACEHOLDER = (
    "A browser_vision screenshot from an earlier step is no longer attached, to save context. "
    "The page has probably changed since then; call browser_vision again if you still need to "
    "look at it. If you already extracted what you needed from that view, rely on your notes."
)

DEFAULT_CAPTURES_TO_KEEP = 1


def _has_image_block(msg) -> bool:
    if not isinstance(msg.content, list):
        return False
    return any(isinstance(block, dict) and block.get("type") == "image_url" for block in msg.content)


def _replace_image_blocks(msg) -> None:
    """Swap image blocks for placeholder text, preserving the multimodal shape."""
    next_content: list = []
    for block in msg.content:
        if isinstance(block, dict) and block.get("type") == "image_url":
            next_content.append({"type": "text", "text": OUTDATED_VIEW_PLACEHOLDER})
        else:
            next_content.append(block)
    msg.content = next_content or [{"type": "text", "text": OUTDATED_VIEW_PLACEHOLDER}]


class BrowserVisionRail(AgentRail):
    """Keep the last N screenshots attached; retire the rest to a placeholder."""

    priority: int = 85

    def __init__(self, model: Any = None, captures_to_keep: int = DEFAULT_CAPTURES_TO_KEEP) -> None:
        super().__init__()
        self._model = model
        self._captures_to_keep = max(0, captures_to_keep)
        self._warned_about_image_support = False

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, ModelCallInputs) or ctx.context is None:
            return
        self._warn_if_model_is_text_only()
        self._retire_old_captures(ctx)

    def _warn_if_model_is_text_only(self) -> None:
        """Say so once if the configured model cannot accept image input.

        The verdict is cached process-wide by DeepAgent's own probe, so this only
        reports a result someone else already paid for; it never probes itself.
        """
        if self._warned_about_image_support or self._model is None:
            return
        if get_cached_image_support(self._model) is False:
            self._warned_about_image_support = True
            logger.warning(
                "[BrowserVisionRail] the configured model does not accept image input; "
                "browser_vision captures will not be readable by it"
            )

    def _retire_old_captures(self, ctx: AgentCallbackContext) -> None:
        messages = ctx.context.get_messages()
        if not messages:
            return

        capture_indices = [index for index, msg in enumerate(messages) if msg.role == "user" and _has_image_block(msg)]
        if len(capture_indices) <= self._captures_to_keep:
            return

        stale = (
            capture_indices[: len(capture_indices) - self._captures_to_keep]
            if self._captures_to_keep
            else capture_indices
        )
        for index in stale:
            _replace_image_blocks(messages[index])

        ctx.context.set_messages(messages)
        logger.info(
            "[BrowserVisionRail] retired %s stale screenshot(s) from context",
            len(stale),
        )


__all__ = [
    "BrowserVisionRail",
    "DEFAULT_CAPTURES_TO_KEEP",
    "OUTDATED_VIEW_PLACEHOLDER",
]
