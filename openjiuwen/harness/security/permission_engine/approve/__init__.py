# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""HITL persist-rule suggestions + merge (pure functions; product YAML I/O stays with Host)."""

from openjiuwen.harness.security.permission_engine.approve.ask_presentation import (
    PermissionAskPresentation,
    build_permission_ask_presentation,
    render_ask_presentation_message,
)
from openjiuwen.harness.security.permission_engine.approve.persist_rule_merge import (
    merge_external_directory_allow_into_permissions,
    merge_file_guard_access_allows,
    merge_file_guard_path_rule,
    merge_permission_allow_rule_into_permissions,
    persist_cli_trusted_directory,
    write_permissions_section_to_agent_config_yaml,
)
from openjiuwen.harness.security.permission_engine.approve.persist_rule_suggestions import (
    PermissionSuggestion,
    build_permission_suggestions,
    build_shell_permission_suggestions,
)

__all__ = [
    "PermissionAskPresentation",
    "build_permission_ask_presentation",
    "render_ask_presentation_message",
    "PermissionSuggestion",
    "build_permission_suggestions",
    "build_shell_permission_suggestions",
    "merge_external_directory_allow_into_permissions",
    "merge_file_guard_access_allows",
    "merge_file_guard_path_rule",
    "merge_permission_allow_rule_into_permissions",
    "persist_cli_trusted_directory",
    "write_permissions_section_to_agent_config_yaml",
]
