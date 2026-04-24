# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""工具权限引擎与宿主注入（供 openjiuwen 与上层产品集成）。

可运行示例见仓库 ``examples/permissions/permission_demo.py``::

    uv run python examples/permissions/permission_demo.py
"""

from openjiuwen.harness.security.checker import (
    DEFAULT_PERMISSION_ENABLED_CHANNELS,
    PERMISSION_ENABLED_CHANNELS,
    TOOL_PERMISSION_CHANNEL_ID,
    assess_command_risk_static,
    assess_command_risk_with_llm,
    check_tool_permissions,
    collect_permission_rail_tool_names,
)
from openjiuwen.harness.security.core import (
    PermissionEngine,
    get_permission_engine,
    init_permission_engine,
    set_permission_engine,
)
from openjiuwen.harness.security.factory import build_permission_interrupt_rail
from openjiuwen.harness.security.host import PermissionSceneHook, ToolPermissionHost
from openjiuwen.harness.security.models import PermissionLevel, PermissionResult
from openjiuwen.harness.security.patterns import (
    build_command_allow_pattern,
    persist_cli_trusted_directory,
    persist_external_directory_allow,
    persist_permission_allow_rule,
    set_agent_config_yaml_path_provider,
)

__all__ = [
    "DEFAULT_PERMISSION_ENABLED_CHANNELS",
    "PERMISSION_ENABLED_CHANNELS",
    "PermissionSceneHook",
    "TOOL_PERMISSION_CHANNEL_ID",
    "PermissionEngine",
    "PermissionLevel",
    "PermissionResult",
    "ToolPermissionHost",
    "assess_command_risk_static",
    "assess_command_risk_with_llm",
    "build_command_allow_pattern",
    "build_permission_interrupt_rail",
    "check_tool_permissions",
    "collect_permission_rail_tool_names",
    "get_permission_engine",
    "init_permission_engine",
    "persist_cli_trusted_directory",
    "persist_external_directory_allow",
    "persist_permission_allow_rule",
    "set_agent_config_yaml_path_provider",
    "set_permission_engine",
]
