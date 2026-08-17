# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""构建 :class:`openjiuwen.harness.rails.security.tool_security_rail.PermissionInterruptRail`。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openjiuwen.core.common.logging import logger
from openjiuwen.harness.security.engine.core import PermissionEngine
from openjiuwen.harness.security.host import ToolPermissionHost
from openjiuwen.harness.security.models import PermissionsSection

if TYPE_CHECKING:
    from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail


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
    """Build a rail from **already-baked** permissions. Does not compose product modes.

    ``enabled: false`` → no rail. Overlay kwargs are ignored (Host must merge first).
    """
    from openjiuwen.harness.rails.security import PermissionInterruptRail

    if not isinstance(permissions, dict):
        return None
    if user_permissions or session_permissions:
        logger.warning(
            "[PermissionEngine] permission.factory.overlays_ignored "
            "pass baked permissions from the host"
        )
    if not permissions.get("enabled", False):
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
