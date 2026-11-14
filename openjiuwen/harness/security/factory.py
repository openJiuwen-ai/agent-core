# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""构建 :class:`openjiuwen.harness.rails.security.tool_security_rail.PermissionInterruptRail`。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    permission_key: str | None = None,
) -> "PermissionInterruptRail | None":
    """若 ``permissions.enabled`` 为真则创建护栏，否则返回 ``None``。

    配置登记由护栏构造完成（可传入 ``permission_key``，默认 ``default``）。
    进程内多个 DeepAgent 若共用同一 key，后创建的护栏会整体替换该 key 的配置。
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

    return PermissionInterruptRail(
        config=config,
        tool_names=None,
        llm=llm,
        model_name=model_name,
        host=h,
        permission_key=permission_key,
    )


__all__ = ["build_permission_interrupt_rail"]
