# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""分层工具权限策略（tiered_policy）。

同步入口 ``evaluate_tiered_policy`` 由本包 ``tiered_compat`` 提供；
其余历史符号仍从 ``openjiuwen.harness.security.tiered_compat`` 兼容导出，
保证既有调用方零改动。
"""

from __future__ import annotations

# Types historically visible through this module.
from agent_shield.tool_permission.fileguard.unix_bash_literal import (
    LiteralCommand as LiteralCommand,
)
from agent_shield.tool_permission.models import PermissionLevel as PermissionLevel
from agent_shield.tool_permission.toolguard.shell_ast import (
    ShellAstParseResult as ShellAstParseResult,
)

from openjiuwen.harness.security.patterns import PathMatcher as PathMatcher
from openjiuwen.harness.security.patterns import match_wildcard as match_wildcard
from openjiuwen.harness.security.tiered_compat import (
    _APPROVAL_OVERRIDES_PREFIX as _APPROVAL_OVERRIDES_PREFIX,
)
from openjiuwen.harness.security.tiered_compat import (
    _BUILTIN_RULES_CACHE as _BUILTIN_RULES_CACHE,
)
from openjiuwen.harness.security.tiered_compat import (
    _MR as _MR,
)
from openjiuwen.harness.security.tiered_compat import (
    _NETWORK_TOOLS as _NETWORK_TOOLS,
)
from openjiuwen.harness.security.tiered_compat import (
    _PATH_ARG_KEYS as _PATH_ARG_KEYS,
)
from openjiuwen.harness.security.tiered_compat import (
    _PATH_TOOLS as _PATH_TOOLS,
)
from openjiuwen.harness.security.tiered_compat import (
    _SHELL_OPERATORS_RE as _SHELL_OPERATORS_RE,
)
from openjiuwen.harness.security.tiered_compat import (
    _SHELL_TOOLS as _SHELL_TOOLS,
)
from openjiuwen.harness.security.tiered_compat import (
    _STRICT_ORDER as _STRICT_ORDER,
)
from openjiuwen.harness.security.tiered_compat import (
    _TIERED_PATH_MATCHER as _TIERED_PATH_MATCHER,
)
from openjiuwen.harness.security.tiered_compat import (
    _aggregate_subcommand_results as _aggregate_subcommand_results,
)
from openjiuwen.harness.security.tiered_compat import (
    _apply_shell_ast_floor as _apply_shell_ast_floor,
)
from openjiuwen.harness.security.tiered_compat import (
    _baseline_level as _baseline_level,
)
from openjiuwen.harness.security.tiered_compat import (
    _collect_approval_override_hits as _collect_approval_override_hits,
)
from openjiuwen.harness.security.tiered_compat import (
    _collect_param_rule_hits as _collect_param_rule_hits,
)
from openjiuwen.harness.security.tiered_compat import (
    _command_text as _command_text,
)
from openjiuwen.harness.security.tiered_compat import (
    _evaluate_policy_with_shell_evidence as _evaluate_policy_with_shell_evidence,  # noqa: F401
)
from openjiuwen.harness.security.tiered_compat import (
    _evaluate_single_invocation as _evaluate_single_invocation,
)
from openjiuwen.harness.security.tiered_compat import (
    _evaluate_tiered_policy_legacy as _evaluate_tiered_policy_legacy,
)
from openjiuwen.harness.security.tiered_compat import (
    _finalize_hits as _finalize_hits,
)
from openjiuwen.harness.security.tiered_compat import (
    _iter_path_strings as _iter_path_strings,
)
from openjiuwen.harness.security.tiered_compat import (
    _literal_explicit_allow as _literal_explicit_allow,
)
from openjiuwen.harness.security.tiered_compat import (
    _package_builtin_rules_path as _package_builtin_rules_path,
)
from openjiuwen.harness.security.tiered_compat import (
    _parse_level as _parse_level,
)
from openjiuwen.harness.security.tiered_compat import (
    _path_pattern_matches as _path_pattern_matches,
)
from openjiuwen.harness.security.tiered_compat import (
    _resolve_builtin_rules_yaml_path as _resolve_builtin_rules_yaml_path,
)
from openjiuwen.harness.security.tiered_compat import (
    _shell_ast_floor as _shell_ast_floor,
)
from openjiuwen.harness.security.tiered_compat import (
    _shell_pattern_matches as _shell_pattern_matches,
)
from openjiuwen.harness.security.tiered_compat import (
    _TieredInvocationContext as _TieredInvocationContext,
)
from openjiuwen.harness.security.tiered_compat import (
    _tool_arg_value_looks_like_path as _tool_arg_value_looks_like_path,
)
from openjiuwen.harness.security.tiered_compat import (
    _tool_category as _tool_category,
)
from openjiuwen.harness.security.tiered_compat import (
    _with_shell_command as _with_shell_command,
)
from openjiuwen.harness.security.tiered_compat import (
    evaluate_tiered_policy as evaluate_tiered_policy,
)
from openjiuwen.harness.security.tiered_compat import (
    get_builtin_security_rules as get_builtin_security_rules,
)
from openjiuwen.harness.security.tiered_compat import (
    get_package_builtin_rules_path as get_package_builtin_rules_path,
)
from openjiuwen.harness.security.tiered_compat import (
    logger as logger,
)
from openjiuwen.harness.security.tiered_compat import (
    matched_rule_uses_approval_override as matched_rule_uses_approval_override,
)
from openjiuwen.harness.security.tiered_compat import (
    maybe_escalate_shell_operators as maybe_escalate_shell_operators,
)
from openjiuwen.harness.security.tiered_compat import (
    rule_tools_category_consistent as rule_tools_category_consistent,
)
from openjiuwen.harness.security.tiered_compat import (
    severity_to_decision as severity_to_decision,
)
from openjiuwen.harness.security.tiered_compat import (
    strictest as strictest,
)
from openjiuwen.harness.security.tiered_compat import (
    tiered_policy_rule_matches as tiered_policy_rule_matches,
)
