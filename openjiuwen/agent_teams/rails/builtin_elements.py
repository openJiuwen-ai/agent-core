# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Name constants for harness built-in element ``type`` strings.

The live ``@harness_element`` declarations live in
``openjiuwen.harness.manifest.builtin_elements``. This module only re-states
the string constants so team code can reference ``RailSpec.type`` values
without importing that declaration module (which eagerly pulls every rail
class — some of which may not exist yet on ENT).

Values must stay identical to ``openjiuwen.harness.manifest.builtin_elements``.
"""

from __future__ import annotations

TASK_PLANNING = "core.task_planning"
SKILL_USE = "core.skill_use"
SUBAGENT = "core.subagent"
SYS_OPERATION = "core.sys_operation"
SECURITY = "core.security"
HEARTBEAT = "core.heartbeat"
WORKTREE = "core.worktree"
LSP = "core.lsp"
TOKEN_TRACKING = "core.token_tracking"
TOOL_TRACKING = "core.tool_tracking"
ASK_USER = "core.ask_user"
CONFIRM_INTERRUPT = "core.confirm_interrupt"
WEB_SEARCH = "core.web_search"
WEB_FETCH = "core.web_fetch"
WEB_PAID_SEARCH = "core.web_paid_search"
VISION = "core.vision"
AUDIO = "core.audio"

__all__ = [
    "TASK_PLANNING",
    "SKILL_USE",
    "SUBAGENT",
    "SYS_OPERATION",
    "SECURITY",
    "HEARTBEAT",
    "WORKTREE",
    "LSP",
    "TOKEN_TRACKING",
    "TOOL_TRACKING",
    "ASK_USER",
    "CONFIRM_INTERRUPT",
    "WEB_SEARCH",
    "WEB_FETCH",
    "WEB_PAID_SEARCH",
    "VISION",
    "AUDIO",
]
