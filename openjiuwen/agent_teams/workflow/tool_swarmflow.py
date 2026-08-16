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

import ast
import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from openjiuwen.agent_teams.harness.async_tools import AsyncTool, render_result_text
from openjiuwen.agent_teams.i18n import STRINGS
from openjiuwen.agent_teams.id_generator import generate_id
from openjiuwen.agent_teams.tools.locales import Translator, make_translator
from openjiuwen.agent_teams.workflow.concurrency import ConcurrencyGovernor
from openjiuwen.agent_teams.workflow.engine.budget import BudgetLedger
from openjiuwen.agent_teams.workflow.engine.runtime import AbortSignal
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

# Resolve an ``agent(model=...)`` name hint to a worker ``TeamModelConfig`` (or
# None to fall back to the worker base spec's model). Built by the configurator.
WorkerModelResolver = Callable[[str], Any]

_RUN_ID_INPUT_KEY = "_run_id"
_WORKFLOW_TICKET_KEY = "_workflow_ticket"
_AGENT_GATE_KEY = "_agent_gate"
_COMPLETION_CTX_KEY = "_completion_ctx"
_OBSERVER_CTX_KEY = "observer"


def new_swarmflow_run_id() -> str:
    """Mint a unique workflow run id: ``wf_{12hex}``."""
    return f"wf_{uuid.uuid4().hex[:12]}"


@dataclass
class _StructuralDiff:
    """AST-level structural delta between two script sources.

    ``changed_nodes`` lists await-call node names present in one source but not
    the other (added / removed). Prompt-string differences are intentionally
    NOT compared.
    """

    changed_nodes: list[str]


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
        concurrency_governor: ConcurrencyGovernor | None = None,
        budget: BudgetLedger | None = None,
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
        self._governor = concurrency_governor
        self._budget = budget
        # Four script sources mirror the reference tool's surface
        # (script_path / script / name / resume_id). "At least one" is enforced
        # in ``invoke`` rather than via JSON-Schema ``required`` because the rule
        # is a one-of, not a fixed key. ``script_path`` (disk) and ``script``
        # (inline source, materialised to disk) are wired to execution; ``name``
        # is accepted and rejected with a clear message. ``resume_id`` doubles as
        # the control entry (with ``action``) into pause/resume/stop.
        self.card.input_params = {
            "type": "object",
            "properties": {
                "script_path": {"type": "string", "description": translator("swarmflow", "script_path")},
                "script": {"type": "string", "description": translator("swarmflow", "script")},
                "name": {"type": "string", "description": translator("swarmflow", "name")},
                "resume_id": {"type": "string", "description": translator("swarmflow", "resume_id")},
                "action": {
                    "type": "string",
                    "enum": ["pause", "resume", "stop"],
                    "description": "Control action on an existing run (requires resume_id).",
                },
                "args": {"type": "string", "description": translator("swarmflow", "args")},
            },
        }

    def launched_description(self, inputs: dict[str, Any]) -> str:
        # `invoke` resolves inline `script` to a concrete script_path before
        # this runs, so the description always reflects the on-disk path.
        return f"swarmflow: {(inputs.get('script_path') or '').strip()}"

    def format_launched_message(self, run_id: str, task_id: str, script_path: str) -> str:
        """Synchronous launch receipt for the leader's current tool round.

        Includes the resolved absolute ``script_path`` so a later re-run /
        iteration can pass that path instead of resending the source (an inline
        ``script`` has been materialised to this path by ``invoke``).
        """
        return self._local_t(
            "swarmflow.launched",
            run_id=run_id,
            task_id=task_id,
            script_path=script_path,
        )

    def format_completed_injection(
        self,
        result: Any,
        *,
        run_id: str,
        completion_ctx: dict[str, Any] | None = None,
    ) -> str:
        """Terminal completion text injected after the background run succeeds."""
        from openjiuwen.agent_teams.workflow.observer import summarize_run

        parts: list[str] = []
        observer = None
        if completion_ctx is not None:
            observer = completion_ctx.get(_OBSERVER_CTX_KEY)
        if observer is not None:
            parts.append(summarize_run(observer.run))
        body = render_result_text(result)
        if body:
            parts.append(body)
        summary = "\n".join(parts)
        return self._local_t(
            "swarmflow.completed",
            run_id=run_id,
            result=summary,
        )

    def format_failed_injection(self, error: str, *, run_id: str) -> str:
        """Terminal failure text injected after the background run fails."""
        return self._local_t(
            "swarmflow.failed",
            run_id=run_id,
            error=error,
        )

    @staticmethod
    def _format_early_return(reply: str | None, edit_hints: str | None, *, run_id: str) -> str:
        parts = [f"[swarmflow {run_id}] 用户要求修改脚本后重跑。"]
        if edit_hints:
            parts.append(f"编辑要点：{edit_hints}")
        if reply:
            parts.append(f"原回复：{reply}")
        parts.append("请据此编辑盘上脚本（勿改 META.name），然后用相同 script_path 重新发起 swarmflow。")
        return "\n".join(parts)

    @staticmethod
    def _format_stopped(*, run_id: str) -> str:
        return (
            f"[swarmflow {run_id}] workflow 已停止。\n"
            "session 仍可用，可继续对话，或用相同 script_path 重新发起 swarmflow（命中已跑 agent 前缀）。"
        )

    def _lint_rerun(self, *, old_source: str, new_source: str) -> None:
        """Pre-launch lint on a re-run: hard-block a ``META.name`` change.

        Renaming would orphan the journal and invalidate the entire cache
        prefix, so it is rejected before launch.
        """
        from openjiuwen.agent_teams.workflow.engine.loader import extract_workflow_meta

        old_meta = extract_workflow_meta(old_source)
        new_meta = extract_workflow_meta(new_source)
        if new_meta.get("name") != old_meta.get("name"):
            from openjiuwen.agent_teams.workflow.engine.errors import MetaError

            raise MetaError(
                f"Cannot change META.name ({old_meta.get('name')!r} → {new_meta.get('name')!r}): "
                "it would orphan the journal and invalidate the entire cache prefix."
            )

    def _compute_structural_diff(self, old_source: str, new_source: str) -> "_StructuralDiff":
        """AST-level structural comparison; does NOT compare prompt strings."""
        def _call_nodes(src: str) -> list[str]:
            try:
                tree = ast.parse(src)
            except SyntaxError:
                return []
            nodes: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
                    fn = node.value.func
                    name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "?")
                    nodes.append(name)
            return nodes

        old_nodes = _call_nodes(old_source)
        new_nodes = _call_nodes(new_source)
        changed = [n for n in new_nodes if n not in old_nodes] or \
                  [n for n in old_nodes if n not in new_nodes]
        return _StructuralDiff(changed_nodes=changed)

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        """Validate the script source, admit via governor, launch background run.

        Runs a script from ``script_path`` (disk) or inline ``script`` source.
        An inline ``script`` is materialised to disk **here** (synchronously in
        the launch round) so the launched receipt can report the resolved
        absolute ``script_path``; the resolved path is threaded into the
        background inputs, so ``run_background`` and any resume relaunch load
        from disk uniformly. ``resume_id`` + ``action`` routes to the
        background-task controller (pause / resume / stop) instead of the
        launch path; ``resume_id`` alone, and ``name``, are rejected with an
        explicit message (never a silent no-op), so the surface is honest about
        what is wired.
        """
        script_path = (inputs.get("script_path") or "").strip()
        script = (inputs.get("script") or "").strip()
        name = (inputs.get("name") or "").strip()
        resume_id = (inputs.get("resume_id") or "").strip()
        action = (inputs.get("action") or "").strip()
        if resume_id:
            if not action:
                return ToolOutput(success=False, error="'action' is required with 'resume_id'")
            controller = getattr(self._parent_agent, "background_task_controller", None)
            if controller is None:
                return ToolOutput(success=False, error="Runtime controller not configured")
            if action == "pause":
                ok = await controller.pause(resume_id)
            elif action == "resume":
                ok = await controller.resume(resume_id)
            elif action == "stop":
                ok = await controller.stop(resume_id)
            else:
                return ToolOutput(success=False, error=f"unknown action {action!r}")
            return ToolOutput(success=ok, data={"run_id": resume_id, "action": action, "status": "done" if ok else "not_found"})
        if not any((script_path, script, name, resume_id)):
            return ToolOutput(
                success=False,
                error="one of 'script_path' / 'script' / 'name' / 'resume_id' is required",
            )
        if not script_path and not script:
            pending = [n for n, v in (("name", name), ("resume_id", resume_id)) if v]
            return ToolOutput(
                success=False,
                error=f"{pending[0]!r} is not supported yet; provide 'script_path' or inline 'script'",
            )
        if self._governor is None:
            return ToolOutput(success=False, error="Swarmflow concurrency governor is not configured")

        admission = await self._governor.admit_workflow()
        if admission is None:
            snap = self._governor.snapshot()
            return ToolOutput(
                success=False,
                error=(
                    f"Swarmflow concurrent limit reached "
                    f"({snap.active_workflows}/{snap.max_workflows})"
                ),
            )

        ticket = admission.ticket
        agent_gate = admission.agent_gate

        # Inline `script`: materialise to disk now so the launched receipt can
        # report the absolute script_path (a later re-run passes that path
        # instead of resending the source). Release the ticket if it fails.
        if not script_path and script:
            from openjiuwen.agent_teams.context import get_session_id
            from openjiuwen.agent_teams.workflow.runner import materialize_swarmflow_script

            try:
                script_path = await materialize_swarmflow_script(
                    script,
                    team_name=self._team_name,
                    session_id=get_session_id(),
                )
            except Exception as exc:  # noqa: BLE001 - never escape as an exception
                await self._governor.release_workflow(ticket)
                return ToolOutput(success=False, error=f"Failed to materialise inline script: {exc}")

        # Re-run lint: only bites when a disk script already exists AND an
        # inline edit is being applied (a plain script_path, or a freshly
        # materialised inline script, has old == new and trivially passes).
        # Hard-blocks META.name changes (they would orphan the journal and
        # invalidate the whole cache prefix) and logs a structural-impact
        # preview otherwise. Release the ticket on any failure so a rejected
        # re-run does not leak a governor slot.
        if script_path and Path(script_path).exists():
            try:
                old_source = Path(script_path).read_text(encoding="utf-8")
                new_source = script if script else old_source
                self._lint_rerun(old_source=old_source, new_source=new_source)
                diff = self._compute_structural_diff(old_source, new_source)
                if diff.changed_nodes:
                    team_logger.info(
                        "[swarmflow] re-run impact: structural changes at %s",
                        diff.changed_nodes,
                    )
            except Exception as exc:  # noqa: BLE001 - never escape as an exception
                await self._governor.release_workflow(ticket)
                return ToolOutput(success=False, error=f"Re-run lint failed: {exc}")

        run_id = new_swarmflow_run_id()
        task_id = generate_id(self.card.name)

        enriched = dict(inputs)
        enriched["script_path"] = script_path
        enriched[_RUN_ID_INPUT_KEY] = run_id
        enriched[_WORKFLOW_TICKET_KEY] = ticket
        enriched[_AGENT_GATE_KEY] = agent_gate
        completion_ctx: dict[str, Any] = {}
        enriched[_COMPLETION_CTX_KEY] = completion_ctx

        def _format_completed(result: Any) -> str:
            return self.format_completed_injection(
                result,
                run_id=run_id,
                completion_ctx=completion_ctx,
            )

        def _format_failed(error: str) -> str:
            return self.format_failed_injection(error, run_id=run_id)

        try:
            self._parent_agent.launch_async_tool(
                task_id,
                lambda: self.run_background(task_id, enriched),
                tool_name=self.card.name,
                description=self.launched_description(enriched),
                format_completed=_format_completed,
                format_failed=_format_failed,
            )
        except Exception as exc:  # noqa: BLE001 - never escape as an exception
            await self._governor.release_workflow(ticket)
            return ToolOutput(success=False, error=f"Internal error: {exc}")

        return ToolOutput(
            success=True,
            data={"status": "launched", "task_id": task_id, "run_id": run_id, "script_path": script_path},
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to launch async tool"
        data = output.data or {}
        return self.format_launched_message(
            run_id=str(data.get("run_id") or ""),
            task_id=str(data.get("task_id") or ""),
            script_path=str(data.get("script_path") or ""),
        )

    async def run_background(self, task_id: str, inputs: dict[str, Any]) -> Any:
        """Run the swarmflow script and return the raw script result."""
        from openjiuwen.agent_teams.context import get_session_id
        from openjiuwen.agent_teams.runtime.background_task_controller import SwarmflowRunHandle
        from openjiuwen.agent_teams.schema.events import (
            EventMessage,
            TeamEvent,
            TeamTopic,
            WorkflowProgressTeamEvent,
        )
        from openjiuwen.agent_teams.workflow.engine.errors import (
            BackendError,
            BudgetExhausted,
            WorkflowAborted,
        )
        from openjiuwen.agent_teams.workflow.observer import WorkflowObserver
        from openjiuwen.agent_teams.workflow.runner import run_swarmflow

        run_id = inputs[_RUN_ID_INPUT_KEY]
        agent_gate = inputs[_AGENT_GATE_KEY]
        ticket = inputs[_WORKFLOW_TICKET_KEY]
        completion_ctx = inputs.get(_COMPLETION_CTX_KEY) or {}
        # `invoke` already resolved this (inline `script` materialised to disk).
        script_path = (inputs.get("script_path") or "").strip()
        args = inputs.get("args")
        model = self._parent_agent.model
        messager = self._messager
        team_name = self._team_name
        name_box: dict[str, Any] = {"name": None, "description": None}
        # Capture the session once. A resume relaunch runs from an external
        # coroutine (the controller) that lacks the leader's session contextvar,
        # so ``_relaunch`` restores it — otherwise the resumed run would publish
        # progress on the wrong topic and resume from the wrong journal path.
        session_id = get_session_id()

        controller = getattr(self._parent_agent, "background_task_controller", None)
        abort_event = AbortSignal()

        def _on_backend_ready(backend: Any) -> None:
            """Register this run's control handle once its backend exists (pause path)."""
            if controller is None:
                return
            controller.register(
                SwarmflowRunHandle(
                    task_id=task_id,
                    run_id=run_id,
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
                node_type=progress.node_type,
                agent_id=progress.agent_id,
                answer=progress.answer,
                tokens=progress.tokens,
                budget=progress.budget,
                phase_type=progress.phase_type,
                nested_phase=progress.nested_phase,
                parent_phase=progress.parent_phase,
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
        completion_ctx[_OBSERVER_CTX_KEY] = observer
        try:
            return await run_swarmflow(
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
                run_id=run_id,
                agent_gate=agent_gate,
                budget=self._budget,
            )
        except BudgetExhausted as exc:
            # Terminal, unlike a pause: re-raise as an ordinary exception so the
            # async-tool runtime injects a failure the leader can read and act on
            # (a BaseException would kill the task silently). Not resumable —
            # relaunching would only hit the same gate.
            raise BackendError(str(exc)) from exc
        except WorkflowAborted as exc:
            if exc.reason == "early_return":
                msg = self._format_early_return(exc.reply, exc.edit_hints, run_id=run_id)
                raise BackendError(msg) from exc
            if exc.reason == "stop":
                msg = self._format_stopped(run_id=run_id)
                raise BackendError(msg) from exc
            # reason == "pause" (default): silent cancel, controller relaunches on resume.
            # Re-raise as CancelledError so the async-tool runtime treats it as a
            # silent cancellation (no completion injected) — matching the cancel
            # the controller triggers as pause's third step.
            raise asyncio.CancelledError() from exc
        finally:
            if controller is not None:
                controller.deregister(run_id)
            if self._governor is not None:
                await self._governor.release_workflow(ticket)

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

    def _message_lang(self) -> str:
        return self._language if self._language in ("cn", "en") else "cn"

    def _local_t(self, key: str, **kwargs: object) -> str:
        raw = STRINGS[self._message_lang()][key]
        return raw.format_map(kwargs) if kwargs else raw


__all__ = ["SwarmflowTool", "WorkerModelResolver"]
