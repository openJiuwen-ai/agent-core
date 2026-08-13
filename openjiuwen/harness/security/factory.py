# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""构建 :class:`openjiuwen.harness.rails.security.tool_security_rail.PermissionInterruptRail`。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.mode import EffectivePermissions
from openjiuwen.harness.security.mode_controller import PermissionModeController
from openjiuwen.harness.security.models import PermissionsSection
from openjiuwen.harness.security.host import ToolPermissionHost

if TYPE_CHECKING:
    from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail

_MODE_CONTROLLER = PermissionModeController()


def compose_effective_permissions(
    permissions: PermissionsSection | dict[str, Any] | None,
    *,
    user_permissions: dict[str, Any] | None = None,
    session_permissions: dict[str, Any] | None = None,
) -> EffectivePermissions:
    """将 Global（及可选 User/Session）合成为 EffectivePermissions。"""
    raw = permissions if isinstance(permissions, dict) else {}
    return _MODE_CONTROLLER.compose(raw, user_permissions, session_permissions)


def build_permission_interrupt_rail(
    *,
    permissions: PermissionsSection,
    llm: Any = None,
    model_name: str | None = None,
    engine: PermissionEngine | None = None,
    host: ToolPermissionHost | None = None,
    workspace_root: Path | None = None,
    user_permissions: dict[str, Any] | None = None,
    session_permissions: dict[str, Any] | None = None,
) -> "PermissionInterruptRail | None":
    """迁移 + 合成 mode preset 后，若 ``enabled`` 为真则创建护栏。

    旧 ``enabled: false``（Web 完全访问）会迁移为 ``mode=full_access`` 且仍挂载权限轨。
    """
    from openjiuwen.harness.rails.security import PermissionInterruptRail

    if not isinstance(permissions, dict):
        return None

    effective = compose_effective_permissions(
        permissions,
        user_permissions=user_permissions,
        session_permissions=session_permissions,
    )
    if not effective.permissions.get("enabled", False):
        return None

    h = host or ToolPermissionHost()
    if h.resolve_workspace_dir is None and workspace_root is not None:
        root = workspace_root.resolve()

        def _root() -> Path:
            return root

        h = replace(h, resolve_workspace_dir=_root)

    return PermissionInterruptRail(
        config=deepcopy(effective.permissions),
        engine=engine,
        tool_names=None,
        llm=llm,
        model_name=model_name,
        host=h,
        sandbox_intent=effective.sandbox_intent,
        permission_mode=effective.mode,
    )


__all__ = ["build_permission_interrupt_rail", "compose_effective_permissions"]
