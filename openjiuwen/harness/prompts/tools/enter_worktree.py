# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Metadata provider for the ``enter_worktree`` tool."""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider

DESCRIPTION: Dict[str, str] = {
    "cn": (
        "创建一个隔离的 git worktree 并将当前会话切换到其中。\n\n"
        "## 何时使用\n\n"
        "- 需要在独立副本中修改代码,避免与其他成员的分支冲突和文件竞争\n"
        "- 需要在不影响主仓库的前提下进行实验性修改\n\n"
        "## 何时不使用\n\n"
        "- 仅需要创建分支或切换分支 -- 使用 git 命令\n"
        "- 不涉及并行修改同一仓库的场景\n\n"
        "## 前置条件\n\n"
        "- 当前必须在一个 git 仓库中\n"
        "- 不能已经在一个 worktree 会话中(需先 exit_worktree)\n\n"
        "## 行为\n\n"
        "- 在 agent workspace 下的 `.worktrees/` 目录基于 HEAD 创建新分支和 worktree\n"
        "- 将会话的工作目录(CWD)切换到新 worktree\n"
        "- 所有后续文件操作和 shell 命令在 worktree 内执行,不影响主仓库\n"
        "- 使用 exit_worktree 离开(keep 保留或 remove 删除)\n\n"
        "## 参数\n\n"
        "- `name`(可选):worktree 名称。不提供则自动生成随机名称。"
    ),
    "en": (
        "Create an isolated git worktree and switch the current session into it.\n\n"
        "## When to Use\n\n"
        "- Need to modify code in an independent copy, avoiding branch conflicts and file"
        " contention with other members\n"
        "- Need to make experimental changes without affecting the main repository\n\n"
        "## When NOT to Use\n\n"
        "- Only need to create or switch branches -- use git commands\n"
        "- No parallel modification of the same repository involved\n\n"
        "## Requirements\n\n"
        "- Must be inside a git repository\n"
        "- Must not already be in a worktree session (exit_worktree first)\n\n"
        "## Behavior\n\n"
        "- Creates a new branch and worktree under the agent workspace's `.worktrees/`"
        " directory based on HEAD\n"
        "- Switches the session's working directory (CWD) to the new worktree\n"
        "- All subsequent file operations and shell commands execute inside the worktree,"
        " leaving the main repo unaffected\n"
        "- Use exit_worktree to leave (keep to retain or remove to delete)\n\n"
        "## Parameters\n\n"
        "- `name` (optional): Worktree name. A random name is generated if not provided."
    ),
}

PARAMS_NAME: Dict[str, str] = {
    "cn": (
        '可选的 worktree 名称。每个 "/" 分隔的段只能包含字母、数字、点、下划线和短横线;'
        "总长度最多 64 字符。不提供则自动生成随机名称"
    ),
    "en": (
        "Optional name for the worktree. "
        'Each "/"-separated segment may contain only letters, '
        "digits, dots, underscores, and dashes; max 64 chars total. "
        "A random name is generated if not provided"
    ),
}


def get_enter_worktree_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the JSON Schema for ``enter_worktree`` input_params."""
    return {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": PARAMS_NAME.get(language, PARAMS_NAME["cn"]),
            },
        },
        "required": [],
    }


class EnterWorktreeMetadataProvider(ToolMetadataProvider):
    """Metadata provider for the ``enter_worktree`` tool."""

    def get_name(self) -> str:
        return "enter_worktree"

    def get_description(self, language: str = "cn") -> str:
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_enter_worktree_input_params(language)
