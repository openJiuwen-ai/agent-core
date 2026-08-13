# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""产品权限模式类型与沙箱 intent 解析（agent-core 侧）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

PermissionMode = Literal["full_access", "auto", "strict"]
SandboxIntent = Literal["optional", "required"]
SeverityMap = Literal["normal", "strict"]
SandboxResolve = Literal["sandbox", "host"]

VALID_PERMISSION_MODES: frozenset[str] = frozenset({"full_access", "auto", "strict"})


@dataclass(frozen=True)
class EffectivePermissions:
    """ModeController 合成结果：引擎用 ``permissions``，Host/SysOp 用 intent。"""

    mode: PermissionMode
    permissions: dict[str, Any]
    severity_map: SeverityMap
    sandbox_intent: SandboxIntent


def resolve_sandbox(
    intent: SandboxIntent,
    *,
    enabled: bool,
    available: bool,
) -> tuple[SandboxResolve, bool]:
    """根据 mode intent + 用户开关 + 可用性决定执行面。

    Returns:
        ``(resolve, warning)``：``warning=True`` 表示 ``required`` 但沙箱不可用（Fail-Open）。
    """
    if intent == "required":
        if available:
            return "sandbox", False
        return "host", True
    # optional：尊重 sandbox.enabled
    if enabled and available:
        return "sandbox", False
    return "host", False


__all__ = [
    "VALID_PERMISSION_MODES",
    "EffectivePermissions",
    "PermissionMode",
    "SandboxIntent",
    "SandboxResolve",
    "SeverityMap",
    "resolve_sandbox",
]
