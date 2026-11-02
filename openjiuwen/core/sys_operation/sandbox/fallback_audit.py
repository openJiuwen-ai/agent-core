# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared audit sink for provider fallback and trusted host mode changes."""

from typing import Any, Callable, Optional

from openjiuwen.core.common.logging import agent_logger

_fallback_audit_hook: Optional[Callable[..., Any]] = None


def set_fallback_audit_hook(hook: Optional[Callable[..., Any]]) -> None:
    """Register a best-effort audit sink shared by sandbox execution routes."""
    global _fallback_audit_hook
    _fallback_audit_hook = hook


def emit_fallback_audit(**fields: Any) -> None:
    """Emit diagnostics without changing the execution decision or error."""
    if _fallback_audit_hook is None:
        return
    try:
        _fallback_audit_hook(**fields)
    except Exception:
        agent_logger.warning("[sandbox] fallback audit hook failed", exc_info=True)
