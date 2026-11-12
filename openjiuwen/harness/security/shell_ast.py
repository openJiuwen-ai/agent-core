# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shell AST pre-processing for tiered tool permissions - re-export from agent_shield.

Implementation lives in agent-security
``agent_shield.tool_permission.toolguard.shell_ast``; this module keeps the
historical ``openjiuwen.harness.security.shell_ast`` import path.
"""

from __future__ import annotations

from agent_shield.tool_permission.toolguard.shell_ast import (
    ShellAstParseResult as ShellAstParseResult,
)
from agent_shield.tool_permission.toolguard.shell_ast import (
    ShellStructureFlags as ShellStructureFlags,
)
from agent_shield.tool_permission.toolguard.shell_ast import (
    ShellSubcommand as ShellSubcommand,
)
from agent_shield.tool_permission.toolguard.shell_ast import (
    _get_tree_sitter_bash_parser as _get_tree_sitter_bash_parser,
)
from agent_shield.tool_permission.toolguard.shell_ast import (
    parse_shell_for_permission as parse_shell_for_permission,
)
