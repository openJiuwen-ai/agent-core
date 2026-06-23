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
import uuid
from typing import Any, Callable

from openjiuwen.agent_teams.harness.async_tools import AsyncTool, render_result_text
from openjiuwen.agent_teams.id_generator import generate_id
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
        team_name: str,
        model_resolver: WorkerModelResolver | None,
        worker_base_spec: Any = None,
        human_base_spec: Any = None,
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
        self._team_name = team_name or "swarmflow"
        self._model_resolver = model_resolver
        self._worker_base_spec = worker_base_spec
        self._human_base_spec = human_base_spec
        # Four script sources mirror the reference tool's surface
        # (script_path / script / name / resume_id). "At least one" is enforced
        # in ``invoke`` rather than via JSON-Schema ``required`` because the rule
        # is a one-of, not a fixed key. Today only ``script_path`` is wired to
        # execution; the rest are accepted and rejected with a clear message.
        self.card.input_params = {
            "type": "object",
            "properties": {
                "script_path": {"type": "string", "description": translator("swarmflow", "script_path")},
                "script": {"type": "string", "description": translator("swarmflow", "script")},
                "name": {"type": "string", "description": translator("swarmflow", "name")},
                "resume_id": {"type": "string", "description": translator("swarmflow", "resume_id")},
                "args": {"type": "string", "description": translator("swarmflow", "args")},
            },
        }

    def launched_description(self, inputs: dict[str, Any]) -> str:
        return f"swarmflow: {(inputs.get('script_path') or '').strip()}"

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        """Validate the script source, guard a concurrent run, then launch.

        Accepts the reference tool's four script sources but only runs
        ``script_path`` today. ``script`` / ``name`` / ``resume_id`` are
        recognised and rejected with an explicit "not supported yet" message
        (never a silent no-op), so the surface is honest about what is wired.
        """
        script_path = (inputs.get("script_path") or "").strip()
        script = (inputs.get("script") or "").strip()
        name = (inputs.get("name") or "").strip()
        resume_id = (inputs.get("resume_id") or "").strip()
        if not any((script_path, script, name, resume_id)):
            return ToolOutput(
                success=False,
                error="one of 'script_path' / 'script' / 'name' / 'resume_id' is required",
            )
        if not script_path:
            pending = [n for n, v in (("script", script), ("name", name), ("resume_id", resume_id)) if v]
            return ToolOutput(
                success=False,
                error=f"{pending[0]!r} is not supported yet; provide 'script_path' to run a script from disk",
            )
        if self._parent_agent.async_tool_runtime.has_running(self.card.name):
            return ToolOutput(success=False, error="A swarmflow run is already in progress")
        return await super().invoke(inputs, **kwargs)

    async def run_background(self, task_id: str, inputs: dict[str, Any]) -> str:
        """Run the swarmflow and return its final result as model-facing text."""
        from openjiuwen.agent_teams.context import get_session_id
        from openjiuwen.agent_teams.runtime.background_task_controller import SwarmflowRunHandle
        from openjiuwen.agent_teams.schema.events import (
            EventMessage,
            TeamEvent,
            TeamTopic,
            WorkflowProgressTeamEvent,
        )
        from openjiuwen.agent_teams.workflow.engine.errors import WorkflowAborted
        from openjiuwen.agent_teams.workflow.observer import WorkflowObserver, summarize_run
        from openjiuwen.agent_teams.workflow.runner import run_swarmflow

        script_path = (inputs.get("script_path") or "").strip()
        args = inputs.get("args")
        model = self._parent_agent.model
        messager = self._messager
        team_name = self._team_name
        run_id = f"wf_{uuid.uuid4().hex[:12]}"
        name_box: dict[str, Any] = {"name": None, "description": None}
        # Capture the session once. A resume relaunch runs from an external
        # coroutine (the controller) that lacks the leader's session contextvar,
        # so ``_relaunch`` restores it — otherwise the resumed run would publish
        # progress on the wrong topic and resume from the wrong journal path.
        session_id = get_session_id()

        controller = getattr(self._parent_agent, "background_task_controller", None)
        abort_event = asyncio.Event()

        def _on_backend_ready(backend: Any) -> None:
            """Register this run's control handle once its backend exists (pause path)."""
            if controller is None:
                return
            controller.register(
                SwarmflowRunHandle(
                    task_id=task_id,
                    abort_event=abort_event,
                    backend=backend,
                    native=self._parent_agent,
                    relaunch=lambda: self._relaunch(inputs, session_id),
                )
            )

        def _publish(progress: Any) -> None:
            if messager is None:
                return
            if progress.kind == "workflow_started":
                name_box["name"] = progress.name
                name_box["description"] = progress.description
            # When the engine's progress.model is None (no model hint on
            # agent_started), fall back to the parent agent's own model.
            resolved_model = progress.model if progress.model is not None else self._parent_agent.model
            team_event = WorkflowProgressTeamEvent(
                team_name=team_name,
                kind=progress.kind,
                run_id=run_id,
                workflow_name=name_box["name"],
                description=name_box.get("description"),
                phase=progress.phase,
                label=progress.label,
                prompt=progress.prompt,
                model=resolved_model,
                outcome=progress.outcome,
                text=progress.message,
                phases=progress.phases,
                correlation_id=progress.correlation_id,
            )
            message = EventMessage(
                event_type=TeamEvent.WORKFLOW_PROGRESS,
                payload=team_event.model_dump(),
                sender_id="swarmflow",  # non-leader sender so kernel does not self-filter
            )
            topic = TeamTopic.TEAM.build(session_id, team_name)
            try:
                team_logger.debug("[swarmflow] workflow progress message: {}", message)
                asyncio.create_task(messager.publish(topic_id=topic, message=message))
            except RuntimeError:
                team_logger.debug("[swarmflow] no running loop to publish workflow progress")

        observer = WorkflowObserver(on_event=_publish)
        try:
            result = await run_swarmflow(
                script_path,
                model=model,
                observer=observer,
                args=args,
                team_name=team_name,
                language=self._language,
                model_resolver=self._model_resolver,
                worker_base_spec=self._worker_base_spec,
                human_base_spec=self._human_base_spec,
                build_context=getattr(self._parent_agent, "build_context", None),
                messager=messager,
                session_id=session_id,
                abort_event=abort_event,
                on_backend_ready=_on_backend_ready,
            )
        except WorkflowAborted:
            # Paused at an abort checkpoint: the WAL holds the completed prefix.
            # Re-raise as CancelledError so the async-tool runtime treats it as a
            # silent cancellation (no completion injected) — matching the cancel
            # the controller triggers as pause's third step.
            raise asyncio.CancelledError()
        finally:
            if controller is not None:
                controller.deregister(task_id)
        parts = [summarize_run(observer.run)]
        body = render_result_text(result)
        if body:
            parts.append(body)
        return "\n".join(parts)

    def _relaunch(self, inputs: dict[str, Any], session_id: str) -> None:
        """Re-launch the paused swarmflow with the SAME inputs (resume path).

        A fresh task id + a new background task; the journal path is unchanged
        (same team / session / name), so the completed prefix is a cache hit and
        only the interrupted call reruns live. Bypasses ``invoke`` — resume is a
        control-plane action, not a new tool_use decided by the LLM.

        Restores the original ``session_id`` contextvar before launching: resume
        is driven from an external coroutine that lacks the leader's session
        context, and the new task inherits the context at ``create_task`` time —
        so without this the resumed run resolves an empty session (wrong progress
        topic + wrong journal path, i.e. no cache hit).
        """
        from openjiuwen.agent_teams.context import reset_session_id, set_session_id

        new_task_id = generate_id(self.card.name)
        token = set_session_id(session_id) if session_id else None
        try:
            self._parent_agent.launch_async_tool(
                new_task_id,
                lambda: self.run_background(new_task_id, inputs),
                tool_name=self.card.name,
                description=f"{self.launched_description(inputs)} (resumed)",
            )
        finally:
            if token is not None:
                reset_session_id(token)


__all__ = ["SwarmflowTool", "WorkerModelResolver"]
