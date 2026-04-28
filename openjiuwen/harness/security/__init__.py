# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""工具权限引擎与宿主注入（供 openjiuwen 与上层产品集成）。

可运行示例见仓库 ``examples/permissions/permission_demo.py``::

    uv run python examples/permissions/permission_demo.py
"""

from openjiuwen.harness.security.core import (
    PermissionEngine,
)
from openjiuwen.harness.security.factory import build_permission_interrupt_rail
from openjiuwen.harness.security.host import (
    PermissionConfirmationRequest,
    PermissionConfirmationResult,
    PermissionSceneHook,
    PermissionSceneHookInput,
    RequestPermissionConfirmationHook,
    ToolPermissionHost,
)
from openjiuwen.harness.security.models import (
    ApprovalOverrideEntry,
    PermissionConfirmResponse,
    PermissionLevel,
    PermissionResult,
    PermissionsSection,
)
from openjiuwen.harness.security.patterns import (
    build_command_allow_pattern,
    merge_external_directory_allow_into_permissions,
    merge_permission_allow_rule_into_permissions,
    persist_cli_trusted_directory,
    write_permissions_section_to_agent_config_yaml,
)

__all__ = [
    "PermissionConfirmationRequest",
    "PermissionConfirmationResult",
    "PermissionConfirmResponse",
    "PermissionSceneHook",
    "PermissionSceneHookInput",
    "RequestPermissionConfirmationHook",
    "PermissionEngine",
    "ApprovalOverrideEntry",
    "PermissionLevel",
    "PermissionResult",
    "PermissionsSection",
    "ToolPermissionHost",
    "build_command_allow_pattern",
    "build_permission_interrupt_rail",
    "merge_external_directory_allow_into_permissions",
    "merge_permission_allow_rule_into_permissions",
    "persist_cli_trusted_directory",
    "write_permissions_section_to_agent_config_yaml",
]
