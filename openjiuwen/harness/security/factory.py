# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""构建 :class:`openjiuwen.harness.rails.security.tool_security_rail.PermissionInterruptRail`。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_shield.tool_permission import get_permission_manager

from openjiuwen.harness.security.host import ToolPermissionHost
from openjiuwen.harness.security.models import PermissionsSection

if TYPE_CHECKING:
    from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail


def build_permission_interrupt_rail(
    *,
    permissions: PermissionsSection,
    llm: Any = None,
    model_name: str | None = None,
    host: ToolPermissionHost | None = None,
    workspace_root: Path | None = None,
) -> "PermissionInterruptRail | None":
    """若 ``permissions.enabled`` 为真则创建护栏，否则返回 ``None``。

    此入口自己持有配置，所以负责把 ``permissions``（连同 ``runtime.workspace_root``）
    登记到权限管理器的 ``default``；进程内同时存在多个带不同 ``permissions`` 的
    DeepAgent 时，后者会覆盖前者。护栏本身只按 key 判定，不登记。
    """
    from openjiuwen.harness.rails.security import PermissionInterruptRail

    if not isinstance(permissions, dict) or not permissions.get("enabled", False):
        return None

    h = host or ToolPermissionHost()
    if h.resolve_workspace_dir is None and workspace_root is not None:
        root = workspace_root.resolve()

        def _root() -> Path:
            return root

        h = replace(h, resolve_workspace_dir=_root)

    config = deepcopy(permissions)
    root_for_config = None
    if h.resolve_workspace_dir is not None:
        try:
            root_for_config = h.resolve_workspace_dir()
        except Exception:
            root_for_config = None
    if root_for_config is not None:
        runtime = dict(config.get("runtime") or {})
        runtime.setdefault("workspace_root", str(root_for_config))
        config["runtime"] = runtime
    get_permission_manager().update_config(config)

    return PermissionInterruptRail(
        tool_names=None,
        llm=llm,
        model_name=model_name,
        host=h,
    )


__all__ = ["build_permission_interrupt_rail"]
