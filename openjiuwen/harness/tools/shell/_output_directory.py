# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Choose a shell output location within the local filesystem sandbox."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.cwd import get_cwd, get_project_root, get_workspace

if TYPE_CHECKING:
    from openjiuwen.core.sys_operation import SysOperation


def get_output_root(operation: SysOperation | None) -> Path | None:
    """Return a sandbox-contained output root, or the legacy temp-root default."""
    if operation is None or operation.mode != OperationMode.LOCAL:
        return None
    config = operation._run_config
    if not config.restrict_to_sandbox:
        return None

    preferred_roots = [get_workspace(), get_cwd(), get_project_root()]
    sandbox_roots = config.sandbox_root or preferred_roots
    resolved_roots = [Path(root).expanduser().resolve() for root in sandbox_roots if root is not None]
    for root in [*preferred_roots, *resolved_roots]:
        if root is None:
            continue
        output_root = (Path(root).expanduser() / ".outputs").resolve()
        if any(output_root.is_relative_to(allowed_root) for allowed_root in resolved_roots):
            return output_root
    raise PermissionError("No shell output directory is available within the configured sandbox")
