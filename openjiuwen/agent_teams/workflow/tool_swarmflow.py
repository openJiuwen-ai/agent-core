# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The leader-facing ``swarmflow()`` tool.

Launches a swarmflow orchestration script in the background and returns
immediately. The actual run (worker spawning + phase narration) is driven by
the injected ``launcher`` — a synchronous fire-and-forget callback the hosting
``TeamAgent`` provides (``_launch_swarmflow``) that schedules
``run_swarmflow_background`` as a tracked asyncio task. Keeping the tool's only
dependency a single callback avoids threading the leader's model / messager /
state through the tool layer.

The leader is a spectator: after calling this tool it does not poll — phase
progress arrives as ``WORKFLOW_PROGRESS`` events the ``WorkflowHandler`` feeds
back as narration input.
"""
from __future__ import annotations

from typing import Any, Callable

from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

# Launcher: ``(script_path, args) -> None``. Synchronous; schedules the
# background run and returns immediately.
SwarmflowLauncher = Callable[[str, Any], None]

_DESC: dict[str, str] = {
    "cn": (
        "运行一个 swarmflow 编排脚本（多 agent 工作流）。\n\n"
        "## 何时调用\n"
        "- 用户要求用 swarmflow / workflow 跑一个脚本，或给出了脚本路径；\n"
        "- 用户描述的任务需要多个 agent 并行 / 流水线编排，且已指定脚本。\n\n"
        "## 行为契约\n"
        "- 该工具**立即返回**——工作流在后台异步执行，**不要轮询**等待结果。\n"
        "- 各阶段（phase）进展会**自动**作为通知流式进入你的上下文。\n"
        "- 你处于**旁观角色**：脚本自主编排 worker 完成工作，你只需在收到阶段"
        "进展通知时，用简洁自然语言向用户汇报当前进展。\n"
        "- **不要**自己 spawn 成员、创建任务或尝试代替脚本编排——编排完全由脚本负责。"
    ),
    "en": (
        "Run a swarmflow orchestration script (a multi-agent workflow).\n\n"
        "## When to call\n"
        "- The user asks to run a swarmflow / workflow script, or gives a script path;\n"
        "- The task needs multiple agents orchestrated in parallel / pipeline and a "
        "script is specified.\n\n"
        "## Behavior contract\n"
        "- This tool **returns immediately** — the workflow runs asynchronously in the "
        "background; **do not poll** for the result.\n"
        "- Phase progress arrives **automatically** as notifications in your context.\n"
        "- You are a **spectator**: the script orchestrates workers on its own; your job "
        "is to relay each reported phase to the user in brief natural language.\n"
        "- **Do not** spawn members, create tasks, or try to orchestrate yourself — the "
        "script owns all orchestration."
    ),
}

_SCRIPT_PARAM: dict[str, str] = {
    "cn": "swarmflow 脚本文件路径（一个含 META 与 async def run(args) 的 Python 模块）。",
    "en": "Path to the swarmflow script file (a Python module with META and async def run(args)).",
}

_ARGS_PARAM: dict[str, str] = {
    "cn": "传给脚本 run(args) 的可选参数值（如研究问题、目标路径）。",
    "en": "Optional argument value passed to the script's run(args) (e.g. a question, a target path).",
}


class SwarmflowTool(TeamTool):
    """Leader tool that launches a swarmflow script in the background."""

    def __init__(self, *, launcher: SwarmflowLauncher, language: str = "cn") -> None:
        lang = language if language in _DESC else "cn"
        super().__init__(
            ToolCard(
                id="team.swarmflow",
                name="swarmflow",
                description=_DESC[lang],
            )
        )
        self._launcher = launcher
        self.card.input_params = {
            "type": "object",
            "properties": {
                "script_path": {"type": "string", "description": _SCRIPT_PARAM[lang]},
                "args": {"type": "string", "description": _ARGS_PARAM[lang]},
            },
            "required": ["script_path"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        script_path = (inputs.get("script_path") or "").strip()
        if not script_path:
            return ToolOutput(success=False, error="'script_path' is required")
        try:
            self._launcher(script_path, inputs.get("args"))
        except Exception as exc:  # never let a launch error escape as an exception
            return ToolOutput(success=False, error=f"Internal error: {exc}")
        return ToolOutput(success=True, data={"status": "started", "script_path": script_path})

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to start swarmflow"
        return (
            f"Swarmflow started: {output.data['script_path']}. It runs in the background; "
            "phase progress will stream to you automatically — do not poll. You are a "
            "spectator: relay each reported phase to the user."
        )


__all__ = ["SwarmflowTool", "SwarmflowLauncher"]
