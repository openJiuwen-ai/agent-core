# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""构建 :class:`openjiuwen.harness.rails.security_rail.tool_security_rail.PermissionInterruptRail`。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.host import ToolPermissionHost


def build_permission_interrupt_rail(
    *,
    permissions: dict[str, Any],
    llm: Any = None,
    model_name: str | None = None,
    engine: PermissionEngine | None = None,
    host: ToolPermissionHost | None = None,
    workspace_root: Path | None = None,
) -> "PermissionInterruptRail | None":
    """若 ``permissions.enabled`` 为真则创建护栏，否则返回 ``None``。"""
    from openjiuwen.harness.rails.security_rail.tool_security_rail import PermissionInterruptRail

    if not isinstance(permissions, dict) or not permissions.get("enabled", False):
        return None

    h = host or ToolPermissionHost()
    if h.resolve_workspace_dir is None and workspace_root is not None:
        root = workspace_root.resolve()

        def _root() -> Path:
            return root

        h = replace(h, resolve_workspace_dir=_root)

    return PermissionInterruptRail(
        config=deepcopy(permissions),
        engine=engine,
        tool_names=None,
        llm=llm,
        model_name=model_name,
        host=h,
    )


__all__ = ["build_permission_interrupt_rail"]
