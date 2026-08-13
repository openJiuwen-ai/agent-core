# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""工具权限引擎与宿主注入（供 openjiuwen 与上层产品集成）。

可运行示例见仓库 ``examples/permissions/permission_demo.py``::

    uv run python examples/permissions/permission_demo.py
"""

from openjiuwen.harness.security.core import (
    PermissionEngine,
)
from openjiuwen.harness.security.factory import (
    build_permission_interrupt_rail,
    compose_effective_permissions,
)
from openjiuwen.harness.security.host import (
    PermissionConfirmationRequest,
    PermissionConfirmationResult,
    PermissionSceneHook,
    PermissionSceneHookInput,
    RequestPermissionConfirmationHook,
    ToolPermissionHost,
)
from openjiuwen.harness.security.findings import GuardFinding, scan_shell_findings
from openjiuwen.harness.security.mode import (
    EffectivePermissions,
    resolve_sandbox,
)
from openjiuwen.harness.security.mode_controller import PermissionModeController
from openjiuwen.harness.security.mode_presets import MODE_PRESETS, get_mode_preset
from openjiuwen.harness.security.models import (
    ApprovalOverrideEntry,
    PermissionConfirmResponse,
    PermissionLevel,
    PermissionResult,
    PermissionsSection,
)
from openjiuwen.harness.security.network_guard import evaluate_network_guard

from openjiuwen.harness.security.patterns import (
    build_command_allow_pattern,
    can_persist_pattern_allow,
    merge_external_directory_allow_into_permissions,
    merge_file_guard_access_allows,
    merge_file_guard_path_rule,
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
    "EffectivePermissions",
    "GuardFinding",
    "MODE_PRESETS",
    "PermissionLevel",
    "PermissionModeController",
    "PermissionResult",
    "PermissionsSection",
    "ToolPermissionHost",
    "build_command_allow_pattern",
    "build_permission_interrupt_rail",
    "can_persist_pattern_allow",
    "compose_effective_permissions",
    "evaluate_network_guard",
    "get_mode_preset",
    "merge_external_directory_allow_into_permissions",
    "merge_file_guard_access_allows",
    "merge_file_guard_path_rule",
    "merge_permission_allow_rule_into_permissions",
    "persist_cli_trusted_directory",
    "resolve_sandbox",
    "scan_shell_findings",
    "write_permissions_section_to_agent_config_yaml",
]
