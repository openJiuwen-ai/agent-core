# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compatibility shim. Prefer ``toolguard.pattern_matchers`` (match) and ``approve.persist_rule_merge`` (HITL)."""

from openjiuwen.harness.security.permission_engine.approve.persist_rule_merge import (
    merge_external_directory_allow_into_permissions,
    merge_file_guard_access_allows,
    merge_file_guard_path_rule,
    merge_permission_allow_rule_into_permissions,
    persist_cli_trusted_directory,
    write_permissions_section_to_agent_config_yaml,
)
from openjiuwen.harness.security.permission_engine.toolguard.pattern_matchers import (
    CommandMatcher,
    PathMatcher,
    PatternMatcher,
    URLMatcher,
    build_command_allow_pattern,
    contains_path,
    match_wildcard,
)

__all__ = [
    "CommandMatcher",
    "PathMatcher",
    "PatternMatcher",
    "URLMatcher",
    "build_command_allow_pattern",
    "contains_path",
    "match_wildcard",
    "merge_external_directory_allow_into_permissions",
    "merge_file_guard_access_allows",
    "merge_file_guard_path_rule",
    "merge_permission_allow_rule_into_permissions",
    "persist_cli_trusted_directory",
    "write_permissions_section_to_agent_config_yaml",
]
