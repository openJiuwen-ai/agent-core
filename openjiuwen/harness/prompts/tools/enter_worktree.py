# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Metadata provider for the ``enter_worktree`` tool."""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider

DESCRIPTION: Dict[str, str] = {
    "cn": (
        "创建或恢复一个隔离的 git worktree 并将当前会话切换到其中。\n\n"
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
        "- 指定 `name` 时定位 agent workspace 下 `.worktrees/<name>`; 若已存在则直接进入,否则基于 HEAD 创建\n"
        "- 未指定 `name` 时使用当前 session 的默认 worktree 名称; 第一次自动生成,后续未指定时复用同一个名称\n"
        "- 跨 session 不继承默认名称; 要进入其他 session 保留的 worktree 时必须显式传入 `name`\n"
        "- 将会话的工作目录(CWD)切换到新 worktree\n"
        "- 所有后续文件操作和 shell 命令在 worktree 内执行,不影响主仓库\n"
        "- 使用 exit_worktree 离开(keep 保留或 remove 删除)\n\n"
        "## 参数\n\n"
        "- `name`(可选):worktree 名称。传入已保留的名称可重新进入该 worktree; 不提供则使用当前 session 的默认名称。"
    ),
    "en": (
        "Create or resume an isolated git worktree and switch the current session into it.\n\n"
        "## When to Use\n\n"
        "- Use this tool only when the user explicitly asks to work in a worktree\n"
        "- The user says \"worktree\" (for example: start a worktree, work in a worktree,"
        " create a worktree, use a worktree)\n\n"
        "## When NOT to Use\n\n"
        "- Only need to create or switch branches -- use git commands\n"
        "- The user asks to fix a bug or work on a feature -- use the normal workflow"
        " unless they specifically mention worktrees\n"
        "- Never use this tool unless the user explicitly mentions worktree\n\n"
        "## Requirements\n\n"
        "- Must be inside a git repository\n"
        "- Must not already be in a worktree session (exit_worktree first)\n\n"
        "## Behavior\n\n"
        "- When `name` is provided, resolves `.worktrees/<name>` under the agent"
        " workspace; enters it if it already exists, otherwise creates a new"
        " branch and worktree from HEAD\n"
        "- When `name` is omitted, uses the current session's default worktree"
        " name; the first unnamed call generates it, and later unnamed calls"
        " reuse the same name\n"
        "- Default names do not cross sessions; to enter a worktree retained by"
        " another session, pass `name` explicitly\n"
        "- Switches the session's working directory (CWD) to the new worktree\n"
        "- All subsequent file operations and shell commands execute inside the worktree,"
        " leaving the main repo unaffected\n"
        "- Use exit_worktree to leave (keep to retain or remove to delete)\n\n"
        "## Parameters\n\n"
        "- `name` (optional): Worktree name. Pass a retained name to re-enter that"
        " worktree; if omitted, the current session's default name is used."
    ),
}

PARAMS_NAME: Dict[str, str] = {
    "cn": (
        '可选的 worktree 名称。每个 "/" 分隔的段只能包含字母、数字、点、下划线和短横线;'
        "总长度最多 64 字符。若该名称对应的 worktree 已存在则直接进入; "
        "不提供则使用当前 session 的默认名称,首次未指定时自动生成"
    ),
    "en": (
        "Optional name for the worktree. "
        'Each "/"-separated segment may contain only letters, '
        "digits, dots, underscores, and dashes; max 64 chars total. "
        "If a worktree with this name already exists, it is re-entered. "
        "If omitted, the current session's default worktree name is used; "
        "the first unnamed call generates it"
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
