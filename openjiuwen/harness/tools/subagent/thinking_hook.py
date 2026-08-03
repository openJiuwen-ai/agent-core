# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Optional host hook for subagent thinking control after TaskTool creates a child.

Product layers (e.g. jiuwenswarm) may register a callable that attaches a
BEFORE_MODEL_CALL rail writing ``ctx.extra['llm_call_kwargs']``. Core never
depends on a product rail: when no hook is registered, TaskTool is a no-op
for the ``thinking`` parameter.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from openjiuwen.core.common.logging import logger

# Signature: (subagent, *, thinking: str, model: Any) -> None
SubagentThinkingHook = Callable[..., None]

_hook: Optional[SubagentThinkingHook] = None


def register_subagent_thinking_hook(hook: Optional[SubagentThinkingHook]) -> None:
    """Register or clear the process-wide subagent thinking hook."""
    global _hook
    _hook = hook


def get_subagent_thinking_hook() -> Optional[SubagentThinkingHook]:
    """Return the registered hook, or None."""
    return _hook


def apply_subagent_thinking(subagent: Any, *, thinking: str, model: Any = None) -> None:
    """Invoke the registered hook if present; never raise into TaskTool."""
    hook = _hook
    if hook is None:
        return
    try:
        hook(subagent, thinking=thinking, model=model)
    except Exception as exc:
        # Product hook must not break task_tool.
        logger.debug(
            "Subagent thinking hook raised an exception, ignoring: %s",
            exc,
            exc_info=True,
        )


__all__ = [
    "SubagentThinkingHook",
    "apply_subagent_thinking",
    "get_subagent_thinking_hook",
    "register_subagent_thinking_hook",
]
