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
from openjiuwen.agent_teams.tools.locales import Translator, make_translator
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

# Resolve an ``agent(model=...)`` name hint to a worker ``TeamModelConfig`` (or
# None to fall back to the worker base spec's model). Built by the configurator.
WorkerModelResolver = Callable[[str], Any]


class SwarmflowTool(AsyncTool):
    """Leader tool that launches a swarmflow script as a background async tool.

    Follows the team tools' conventions: description and parameter strings are
    resolved through the shared i18n ``Translator`` (``descs/<lang>/swarmflow.md``
    + ``swarmflow.*`` STRINGS) so the surface honours the leader's language.
    """

    def __init__(
        self,
        *,
        parent_agent: Any,
        messager: Any,
        team_backend: Any,
        team_name: str,
        model_resolver: WorkerModelResolver | None,
        worker_base_spec: Any = None,
        t: Translator | None = None,
        language: str = "cn",
    ) -> None:
        lang = language if language in ("cn", "en") else "cn"
        translator = t if t is not None else make_translator(lang)
        super().__init__(
            ToolCard(
                id="team.swarmflow",
                name="swarmflow",
                description=translator("swarmflow"),
            ),
            parent_agent,
            language=lang,
        )
        self._messager = messager
        self._team_backend = team_backend
        self._team_name = team_name or "swarmflow"
        self._model_resolver = model_resolver
        self._worker_base_spec = worker_base_spec
        self.card.input_params = {
            "type": "object",
            "properties": {
                "script_path": {"type": "string", "description": translator("swarmflow", "script_path")},
                "args": {"type": "string", "description": translator("swarmflow", "args")},
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
            worker_base_spec=self._worker_base_spec,
        )
        parts = [summarize_run(observer.run)]
        body = render_result_text(result)
        if body:
            parts.append(body)
        return "\n".join(parts)


__all__ = ["SwarmflowTool", "WorkerModelResolver"]
