# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""工具权限引擎与宿主注入（供 openjiuwen 与上层产品集成）。

可运行示例见仓库 ``examples/permissions/permission_demo.py``::

    uv run python examples/permissions/permission_demo.py
"""

from openjiuwen.harness.security.permission_engine.toolguard.builtin_rules import (
    get_package_builtin_rules_path,
    inline_package_command_rules,
    load_package_command_rules,
)
from openjiuwen.harness.security.permission_engine.fileguard.sensitive_paths import (
    load_package_sensitive_paths,
    merge_package_sensitive_paths,
)
from openjiuwen.harness.security.permission_engine.core import (
    PermissionEngine,
    build_permission_interrupt_rail,
)
from openjiuwen.harness.security.permission_engine.host import (
    PermissionConfirmationRequest,
    PermissionConfirmationResult,
    PermissionSceneHook,
    PermissionSceneHookInput,
    RequestPermissionConfirmationHook,
    ToolPermissionHost,
)
from openjiuwen.harness.security.permission_engine.models import (
    ApprovalOverrideEntry,
    PermissionConfirmResponse,
    PermissionLevel,
    PermissionResult,
    PermissionsSection,
)

from openjiuwen.harness.security.permission_engine.approve.persist_rule_merge import (
    merge_external_directory_allow_into_permissions,
    merge_file_guard_access_allows,
    merge_file_guard_path_rule,
    merge_permission_allow_rule_into_permissions,
    persist_cli_trusted_directory,
    write_permissions_section_to_agent_config_yaml,
)
from openjiuwen.harness.security.permission_engine.toolguard.pattern_matchers import (
    build_command_allow_pattern,
)

__all__ = [
    "get_package_builtin_rules_path",
    "inline_package_command_rules",
    "load_package_command_rules",
    "load_package_sensitive_paths",
    "merge_package_sensitive_paths",
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
    "merge_file_guard_access_allows",
    "merge_file_guard_path_rule",
    "merge_permission_allow_rule_into_permissions",
    "persist_cli_trusted_directory",
    "write_permissions_section_to_agent_config_yaml",
]
