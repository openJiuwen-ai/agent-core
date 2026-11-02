# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Permission engine: evaluate effective permissions; host compose stays outside.

Layout::

    permission_engine/
      models.py / host.py / core.py
      toolguard/     command rules, tool_policy, shell AST, matching
      fileguard/     path policy, path_extract, file_tool_specs, sensitive paths
      netguard/      fetch URL policy
      approve/       HITL persist-rule suggestions + merge
"""

from openjiuwen.harness.security.permission_engine.core import (
    PermissionEngine,
    build_permission_interrupt_rail,
)
from openjiuwen.harness.security.permission_engine.host import ToolPermissionHost
from openjiuwen.harness.security.permission_engine.models import (
    PermissionLevel,
    PermissionResult,
    PermissionsSection,
)
from openjiuwen.harness.security.permission_engine.policy_sources import (
    DEFAULT_LAYER_ORDER,
    DEFAULT_SOURCE_ORDER,
    GROUP_SOURCE_BY_OWNER,
    POLICY_SOURCE_IDS,
    SOURCE_BUILTIN,
    SOURCE_BUILTIN_GROUP,
    SOURCE_CLOUD,
    SOURCE_CLOUD_GROUP,
    SOURCE_USER,
    SOURCE_USER_GROUP,
    validate_policy_sources,
    validate_source_order,
)

__all__ = [
    "DEFAULT_SOURCE_ORDER",
    "DEFAULT_LAYER_ORDER",
    "GROUP_SOURCE_BY_OWNER",
    "POLICY_SOURCE_IDS",
    "SOURCE_BUILTIN",
    "SOURCE_BUILTIN_GROUP",
    "SOURCE_CLOUD",
    "SOURCE_CLOUD_GROUP",
    "SOURCE_USER",
    "SOURCE_USER_GROUP",
    "PermissionEngine",
    "PermissionLevel",
    "PermissionResult",
    "PermissionsSection",
    "ToolPermissionHost",
    "build_permission_interrupt_rail",
    "validate_policy_sources",
    "validate_source_order",
]
