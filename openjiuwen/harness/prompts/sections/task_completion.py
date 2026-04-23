# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Task completion system prompt section.

Provides the completion-signal guidance injected into the system prompt when
``TaskCompletionRail`` is active.
"""
from __future__ import annotations

from typing import Dict

from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName

# ---------------------------------------------------------------------------
# Completion-signal guidance
# ---------------------------------------------------------------------------

_PROMISE_GUIDANCE: Dict[str, str] = {
    "cn": (
        "\n\n## 完成信号\n"
        "任务完全完成后，在回复的最后一行输出 "
        "<promise>{promise}</promise>。\n"
        "在确认任务完成前，不要输出此标签。"
    ),
    "en": (
        "\n\n## Completion Signal\n"
        "When the task is fully completed, output "
        "<promise>{promise}</promise> as the final "
        "line of your response. Do not output this "
        "tag until you are confident the task is "
        "complete."
    ),
}

_COMPLETION_SIGNAL_PRIORITY = 85


def build_completion_signal_section(
    language: str,
    completion_promise: str,
) -> PromptSection:
    """Build the completion-signal prompt section.

    Args:
        language: Prompt language code, usually ``"cn"`` or ``"en"``.
        completion_promise: Token the model must emit inside
            ``<promise>...</promise>`` to signal completion.

    Returns:
        A ``PromptSection`` ready to inject into the system prompt.
    """
    template = _PROMISE_GUIDANCE.get(language, _PROMISE_GUIDANCE["cn"])
    return PromptSection(
        name=SectionName.COMPLETION_SIGNAL,
        content={language: template.format(promise=completion_promise)},
        priority=_COMPLETION_SIGNAL_PRIORITY,
    )


__all__ = ["build_completion_signal_section"]
