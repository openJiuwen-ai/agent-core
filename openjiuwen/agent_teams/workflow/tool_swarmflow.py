# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The leader-facing ``swarmflow()`` tool — first async background tool.

Built on the NativeHarness async-tool framework (:class:`AsyncTool`): ``invoke``
launches the orchestration in the background and returns immediately (the
``tool_use`` closes at once, the leader's round is not blocked); the real result
is injected back as a follow-up message when the run finishes — never as a
suspended ``tool_result``.

The leader is a spectator: phase progress arrives as ``WORKFLOW_PROGRESS`` events
the ``WorkflowHandler`` narrates; the final result (or failure) is fed back by
the framework through the harness's own ``send``. The tool holds the team
resources it needs (messager for phase events, team backend / name, worker model
resolver) and reaches the harness via ``parent_agent`` — never through TeamAgent.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from openjiuwen.agent_teams.harness.async_tools import AsyncTool, render_result_text
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

# Resolve an ``agent(model=...)`` name hint to a concrete ``Model`` (or None to
# fall back to the leader's model). Built by the configurator from the team spec.
WorkerModelResolver = Callable[[str], Any]

_DESC: dict[str, str] = {
    "cn": (
        "运行一个 swarmflow 编排脚本（多 agent 工作流）。\n\n"
        "## 何时调用\n"
        "- 用户要求用 swarmflow / workflow 跑一个脚本，或给出了脚本路径；\n"
        "- 用户描述的任务需要多个 agent 并行 / 流水线编排，且已指定脚本。\n\n"
        "## 行为契约\n"
        "- 该工具**立即返回**——工作流在后台异步执行，**不要轮询**等待结果。\n"
        "- 各阶段（phase）进展会**自动**作为通知流式进入你的上下文。\n"
        "- 工作流**完成或失败时，最终结果会自动回灌**给你，无需主动查询。\n"
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
        "- When the workflow **completes or fails, the final result is fed back to you "
        "automatically** — no need to query.\n"
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


class SwarmflowTool(AsyncTool):
    """Leader tool that launches a swarmflow script as a background async tool."""

    def __init__(
        self,
        *,
        parent_agent: Any,
        messager: Any,
        team_backend: Any,
        team_name: str,
        model_resolver: WorkerModelResolver | None,
        language: str = "cn",
    ) -> None:
        lang = language if language in _DESC else "cn"
        super().__init__(
            ToolCard(
                id="team.swarmflow",
                name="swarmflow",
                description=_DESC[lang],
            ),
            parent_agent,
            language=lang,
        )
        self._messager = messager
        self._team_backend = team_backend
        self._team_name = team_name or "swarmflow"
        self._model_resolver = model_resolver
        self.card.input_params = {
            "type": "object",
            "properties": {
                "script_path": {"type": "string", "description": _SCRIPT_PARAM[lang]},
                "args": {"type": "string", "description": _ARGS_PARAM[lang]},
            },
            "required": ["script_path"],
        }

    def launched_description(self, inputs: dict[str, Any]) -> str:
        return f"swarmflow: {(inputs.get('script_path') or '').strip()}"

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        """Validate, guard against a concurrent run, then launch in background."""
        script_path = (inputs.get("script_path") or "").strip()
        if not script_path:
            return ToolOutput(success=False, error="'script_path' is required")
        if self._parent_agent.async_tool_runtime.has_running(self.card.name):
            return ToolOutput(success=False, error="A swarmflow run is already in progress")
        return await super().invoke(inputs, **kwargs)

    async def run_background(self, task_id: str, inputs: dict[str, Any]) -> str:
        """Run the swarmflow and return its final result as model-facing text."""
        from openjiuwen.agent_teams.context import get_session_id
        from openjiuwen.agent_teams.schema.events import (
            EventMessage,
            TeamEvent,
            TeamTopic,
            WorkflowProgressTeamEvent,
        )
        from openjiuwen.agent_teams.workflow.observer import WorkflowObserver, summarize_run
        from openjiuwen.agent_teams.workflow.runner import run_swarmflow

        script_path = (inputs.get("script_path") or "").strip()
        args = inputs.get("args")
        model = self._parent_agent.model
        messager = self._messager
        team_name = self._team_name
        name_box: dict[str, Any] = {"name": None}

        def _publish(progress: Any) -> None:
            if messager is None:
                return
            if progress.kind == "workflow_started":
                name_box["name"] = progress.message
            team_event = WorkflowProgressTeamEvent(
                team_name=team_name,
                kind=progress.kind,
                workflow_name=name_box["name"],
                phase=progress.phase,
                label=progress.label,
                outcome=progress.outcome,
                text=progress.message,
            )
            message = EventMessage(
                event_type=TeamEvent.WORKFLOW_PROGRESS,
                payload=team_event.model_dump(),
                sender_id="swarmflow",  # non-leader sender so kernel does not self-filter
            )
            topic = TeamTopic.TEAM.build(get_session_id(), team_name)
            try:
                asyncio.create_task(messager.publish(topic_id=topic, message=message))
            except RuntimeError:
                team_logger.debug("[swarmflow] no running loop to publish workflow progress")

        observer = WorkflowObserver(on_event=_publish)
        result = await run_swarmflow(
            script_path,
            model=model,
            observer=observer,
            args=args,
            team_backend=self._team_backend,
            team_name=team_name,
            language=self._language,
            model_resolver=self._model_resolver,
        )
        parts = [summarize_run(observer.run)]
        body = render_result_text(result)
        if body:
            parts.append(body)
        return "\n".join(parts)


__all__ = ["SwarmflowTool", "WorkerModelResolver"]
