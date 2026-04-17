# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""CommitTool — guarded commit tool for auto-harness."""

from __future__ import annotations

from typing import Any, AsyncIterator, Callable, Dict, Optional

from openjiuwen.core.foundation.tool import Tool
from openjiuwen.auto_harness.infra.commit_guard import (
    validate_commit_plan,
)
from openjiuwen.auto_harness.infra.git_operations import (
    GitOperations,
)
from openjiuwen.auto_harness.schema import (
    CommitFacts,
    CommitPlan,
)
from openjiuwen.harness.prompts.sections.tools import (
    build_tool_card,
    register_tool_provider,
)
from openjiuwen.harness.prompts.sections.tools.base import (
    ToolMetadataProvider,
)
from openjiuwen.harness.tools.base_tool import ToolOutput


_DESCRIPTION = {
    "cn": (
        "受控提交工具。基于当前 commit facts 校验 message/files，"
        "仅在通过 commit_guard 后才执行 git add 与 git commit。"
    ),
    "en": (
        "Guarded commit tool. Validates message/files against current "
        "commit facts and only executes git add/git commit after "
        "commit_guard approves the plan."
    ),
}

_PARAMS = {
    "message": {
        "cn": "提交消息",
        "en": "Commit message",
    },
    "files": {
        "cn": "要提交的文件路径列表",
        "en": "List of file paths to commit",
    },
    "rationale": {
        "cn": "为何这些文件应进入本次提交",
        "en": "Why these files belong in this commit",
    },
}


class CommitToolMetadataProvider(
    ToolMetadataProvider,
):
    """Metadata provider for CommitTool."""

    def get_name(self) -> str:
        return "commit_tool"

    def get_description(
        self,
        language: str = "cn",
    ) -> str:
        return _DESCRIPTION.get(language, _DESCRIPTION["cn"])

    def get_input_params(
        self,
        language: str = "cn",
    ) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": _PARAMS["message"].get(
                        language,
                        _PARAMS["message"]["cn"],
                    ),
                },
                "files": {
                    "type": "array",
                    "description": _PARAMS["files"].get(
                        language,
                        _PARAMS["files"]["cn"],
                    ),
                    "items": {
                        "type": "string",
                        "description": _PARAMS["files"].get(
                            language,
                            _PARAMS["files"]["cn"],
                        ),
                    },
                },
                "rationale": {
                    "type": "string",
                    "description": _PARAMS["rationale"].get(
                        language,
                        _PARAMS["rationale"]["cn"],
                    ),
                },
            },
            "required": ["message", "files"],
        }


register_tool_provider(
    CommitToolMetadataProvider()
)


class CommitTool(Tool):
    """Commit via commit_guard + GitOperations."""

    def __init__(
        self,
        git: GitOperations,
        facts_provider: Callable[[], CommitFacts],
        *,
        language: str = "cn",
        agent_id: Optional[str] = None,
    ) -> None:
        super().__init__(
            build_tool_card(
                "commit_tool",
                "CommitTool",
                language,
                agent_id=agent_id,
            )
        )
        self._git = git
        self._facts_provider = facts_provider
        self.last_output: ToolOutput | None = None

    async def invoke(
        self,
        inputs: Dict[str, Any],
        **kwargs: Any,
    ) -> ToolOutput:
        plan = CommitPlan(
            message=str(inputs.get("message", "")),
            files=list(inputs.get("files", []) or []),
            rationale=str(inputs.get("rationale", "")),
        )
        facts = self._facts_provider()
        guard = validate_commit_plan(
            facts,
            plan,
        )
        if not guard.allowed:
            self.last_output = ToolOutput(
                success=False,
                error=guard.reason,
                data={
                    "blocked_files": guard.blocked_files,
                    "warnings": guard.warnings,
                },
            )
            return self.last_output

        result = await self._git.commit(
            plan.message,
            guard.normalized_files,
        )
        if not result.get("success"):
            self.last_output = ToolOutput(
                success=False,
                error=result.get(
                    "output",
                    "commit failed",
                ),
                data={
                    "committed_files": guard.normalized_files,
                    "warnings": guard.warnings,
                    "error_code": result.get("error_code"),
                },
            )
            return self.last_output

        self.last_output = ToolOutput(
            success=True,
            data={
                "commit_sha": result.get("commit_sha", ""),
                "committed_files": guard.normalized_files,
                "warnings": guard.warnings,
            },
        )
        return self.last_output

    async def stream(
        self,
        inputs: Dict[str, Any],
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, **kwargs)
