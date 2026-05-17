# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Metadata provider for the ``exit_worktree`` tool."""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider

DESCRIPTION: Dict[str, str] = {
    "cn": (
        "退出由 enter_worktree 创建的 worktree 会话,将工作目录恢复到原始位置。\n\n"
        "## 作用范围\n\n"
        "仅操作当前会话中由 enter_worktree 创建的 worktree。不会触碰:\n"
        "- 手动通过 `git worktree add` 创建的 worktree\n"
        "- 其他成员的 worktree\n"
        "- 从未调用 enter_worktree 时当前所在的目录\n\n"
        "在 enter_worktree 会话之外调用为空操作(no-op)。\n\n"
        "## 何时使用\n\n"
        "- 任务完成,需要退出 worktree\n"
        "- 需要切换到其他工作上下文\n\n"
        "## 参数\n\n"
        '- `action`(必填):`"keep"` 或 `"remove"`\n'
        '  - `"keep"` -- 保留 worktree 目录和分支在磁盘上,后续可再次进入\n'
        '  - `"remove"` -- 删除 worktree 目录及其分支,适用于工作已完成或已放弃\n'
        '- `discard_changes`(可选,默认 false):仅在 `action="remove"` 时有意义。'
        "当 worktree 有未提交文件或未合并提交时,工具会拒绝删除并列出变更,需设为 true 确认丢弃\n\n"
        "## 行为\n\n"
        "- 恢复会话工作目录到 enter_worktree 之前的位置\n"
        "- action=remove 时,先检测未提交变更和新提交,有变更则拒绝(除非 discard_changes=true)\n"
        "- 退出后可再次调用 enter_worktree 创建新的 worktree"
    ),
    "en": (
        "Exit a worktree session created by enter_worktree and restore the working"
        " directory to its original location.\n\n"
        "## Scope\n\n"
        "Only operates on the worktree created by enter_worktree in the current session."
        " Will NOT touch:\n"
        "- Worktrees created manually with `git worktree add`\n"
        "- Other members' worktrees\n"
        "- The current directory if enter_worktree was never called\n\n"
        "Calling outside an enter_worktree session is a no-op.\n\n"
        "## When to Use\n\n"
        "- Task is complete and you need to leave the worktree\n"
        "- Need to switch to a different working context\n\n"
        "## Parameters\n\n"
        '- `action` (required): `"keep"` or `"remove"`\n'
        '  - `"keep"` -- leave the worktree directory and branch on disk for later use\n'
        '  - `"remove"` -- delete the worktree directory and its branch; use when work is'
        " done or abandoned\n"
        "- `discard_changes` (optional, default false): only meaningful with `action:"
        ' "remove"`. When the worktree has uncommitted files or unmerged commits, the tool'
        " refuses to remove and lists changes; set to true to confirm discard\n\n"
        "## Behavior\n\n"
        "- Restores the session's working directory to where it was before enter_worktree\n"
        "- On action=remove, detects uncommitted changes and new commits first; refuses"
        " unless discard_changes=true\n"
        "- After exit, enter_worktree can be called again to create a fresh worktree"
    ),
}

PARAMS_ACTION: Dict[str, str] = {
    "cn": '"keep" 保留 worktree 目录和分支在磁盘上;"remove" 删除目录和分支',
    "en": '"keep" leaves the worktree and branch on disk; "remove" deletes both',
}

PARAMS_DISCARD: Dict[str, str] = {
    "cn": (
        '仅在 action="remove" 且 worktree 有未提交文件或未合并提交时需设为 true。'
        "工具会先拒绝并列出变更,确认后再设此参数重新调用"
    ),
    "en": (
        'Required true when action is "remove" and '
        "the worktree has uncommitted files or unmerged commits. "
        "The tool will refuse and list them otherwise"
    ),
}


def get_exit_worktree_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the JSON Schema for ``exit_worktree`` input_params."""
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["keep", "remove"],
                "description": PARAMS_ACTION.get(language, PARAMS_ACTION["cn"]),
            },
            "discard_changes": {
                "type": "boolean",
                "description": PARAMS_DISCARD.get(language, PARAMS_DISCARD["cn"]),
            },
        },
        "required": ["action"],
    }


class ExitWorktreeMetadataProvider(ToolMetadataProvider):
    """Metadata provider for the ``exit_worktree`` tool."""

    def get_name(self) -> str:
        return "exit_worktree"

    def get_description(self, language: str = "cn") -> str:
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_exit_worktree_input_params(language)
