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
from typing import TYPE_CHECKING, Any, Callable

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

if TYPE_CHECKING:
    from openjiuwen.agent_teams.workflow.engine.errors import BudgetExhausted

# Resolve an ``agent(model=...)`` name hint to a worker ``TeamModelConfig`` (or
# None to fall back to the worker base spec's model). Built by the configurator.
WorkerModelResolver = Callable[[str], Any]

_RUN_ID_INPUT_KEY = "_run_id"
_RELAUNCH_KIND_KEY = "_relaunch_kind"
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
                    "description": translator("swarmflow", "action"),
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
        """Terminal completion text injected after the background run succeeds.

        A completed run may still have finished OVER budget (the rail
        force-finishes in-flight agents past the ceiling), in which case an
        overrun block (tallies + top phases + guidance) is appended.
        """
        from openjiuwen.agent_teams.workflow.observer import summarize_run

        observer = (completion_ctx or {}).get(_OBSERVER_CTX_KEY)
        parts: list[str] = []
        if observer is not None:
            parts.append(summarize_run(observer.run))
        body = render_result_text(result)
        if body:
            parts.append(body)
        message = self._local_t(
            "swarmflow.completed",
            run_id=run_id,
            result="\n".join(parts),
        )
        overrun_fields = _budget_overrun_fields(
            getattr(observer, "events", None), self._language
        )
        if overrun_fields is not None:
            message += "\n" + self._local_t(
                "swarmflow.budget_overrun", run_id=run_id, **overrun_fields
            )
        return message

    def format_failed_injection(
        self,
        error: "BudgetExhausted | str",
        *,
        run_id: str,
    ) -> str:
        """Terminal failure text injected after the background run fails.

        When the run hit a token ceiling, ``error`` is the ``BudgetExhausted``
        itself (the async-tool runtime passes the exception through so no
        structured field is lost): the message is generated from its fields
        — ``scope`` picks the ceiling kind, ``spent``/``total`` the trigger
        layer's tally, ``top_phases`` the heaviest phases, and
        ``workflow_spent``/``workflow_total`` the per-run contrast when the
        *session* layer tripped. Any other failure arrives as a plain ``str``.
        """
        from openjiuwen.agent_teams.workflow.engine.errors import BudgetExhausted

        if isinstance(error, BudgetExhausted):
            return self._local_t(
                "swarmflow.budget_exhausted",
                run_id=run_id,
                **_budget_exhausted_fields(error, self._language),
            )
        return self._local_t(
            "swarmflow.failed",
            run_id=run_id,
            error=str(error),
        )

    @staticmethod
    def _format_early_return(reply: str | None, edit_hints: str | None, *, run_id: str) -> str:
        parts = [f"[swarmflow {run_id}] The user asked to edit the script and re-run."]
        if edit_hints:
            parts.append(f"Edit points: {edit_hints}")
        if reply:
            parts.append(f"Original reply: {reply}")
        parts.append(
            "Edit the on-disk script accordingly (do not change META.name). "
            "Edit minimally and reuse the existing script content as much as possible, "
            "refining it rather than rewriting. Then re-launch swarmflow with "
            f"resume_id={run_id} and the same script_path — resume_id reuses this "
            "run's journal cache (unchanged agents replay free) and re-bills its "
            "per-run budget from the cache hits."
        )
        return "\n".join(parts)

    @staticmethod
    def _format_stopped(*, run_id: str) -> str:
        return (
            f"[swarmflow {run_id}] workflow stopped by the user.\n"
            "Do NOT re-run the script. Acknowledge the stop and wait for the user's "
            "next instruction."
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

        ``resume_id`` + ``action`` routes to the background-task controller
        (pause / resume / stop); everything else is the launch path.
        """
        script_path = (inputs.get("script_path") or "").strip()
        script = (inputs.get("script") or "").strip()
        name = (inputs.get("name") or "").strip()
        resume_id = (inputs.get("resume_id") or "").strip()
        action = (inputs.get("action") or "").strip()

        if resume_id and action:
            return await self._control_run(resume_id, action)
        # resume_id WITHOUT action = re-launch of an interrupted run: fall
        # through and reuse it as run_id so journal cache hits replay.
        error = self._launch_input_error(script_path, script, name, resume_id)
        if error is not None:
            return ToolOutput(success=False, error=error)
        if self._governor is None:
            return ToolOutput(success=False, error="Swarmflow concurrency governor is not configured")

        admission = await self._governor.admit_workflow()
        if admission is None:
            snap = self._governor.snapshot()
            return ToolOutput(
                success=False,
                error=f"Swarmflow concurrent limit reached ({snap.active_workflows}/{snap.max_workflows})",
            )
        ticket = admission.ticket

        # Steps below hold the governor ticket: every failure path releases it.
        if not script_path and script:
            # Materialise inline source now so the receipt reports the absolute
            # path (a later re-run passes it instead of resending the source).
            try:
                script_path = await self._materialise_script(script)
            except Exception as exc:  # noqa: BLE001 - never escape as an exception
                await self._governor.release_workflow(ticket)
                return ToolOutput(success=False, error=f"Failed to materialise inline script: {exc}")

        try:
            self._check_rerun(script_path, script)
        except Exception as exc:  # noqa: BLE001 - never escape as an exception
            await self._governor.release_workflow(ticket)
            return ToolOutput(success=False, error=f"Re-run lint failed: {exc}")

        resume_id = await self._seal_guard(script_path, resume_id)

        enriched = self._enriched_inputs(
            inputs,
            script_path=script_path,
            resume_id=resume_id,
            ticket=ticket,
            agent_gate=admission.agent_gate,
        )
        run_id = enriched[_RUN_ID_INPUT_KEY]
        completion_ctx = enriched[_COMPLETION_CTX_KEY]
        task_id = generate_id(self.card.name)

        try:
            self._parent_agent.launch_async_tool(
                task_id,
                lambda: self.run_background(task_id, enriched),
                tool_name=self.card.name,
                description=self.launched_description(enriched),
                format_completed=lambda result: self.format_completed_injection(
                    result, run_id=run_id, completion_ctx=completion_ctx
                ),
                format_failed=lambda error: self.format_failed_injection(error, run_id=run_id),
            )
        except Exception as exc:  # noqa: BLE001 - never escape as an exception
            await self._governor.release_workflow(ticket)
            return ToolOutput(success=False, error=f"Internal error: {exc}")

        return ToolOutput(
            success=True,
            data={"status": "launched", "task_id": task_id, "run_id": run_id, "script_path": script_path},
        )

    async def _control_run(self, resume_id: str, action: str) -> ToolOutput:
        """Route ``resume_id`` + ``action`` into the background-task controller."""
        controller = getattr(self._parent_agent, "background_task_controller", None)
        if controller is None:
            return ToolOutput(success=False, error="Runtime controller not configured")
        ops = {"pause": controller.pause, "resume": controller.resume, "stop": controller.stop}
        op = ops.get(action)
        if op is None:
            return ToolOutput(success=False, error=f"unknown action {action!r}")
        ok = await op(resume_id)
        return ToolOutput(
            success=ok,
            data={"run_id": resume_id, "action": action, "status": "done" if ok else "not_found"},
        )

    @staticmethod
    def _launch_input_error(script_path: str, script: str, name: str, resume_id: str) -> str | None:
        """Source validation: one of the four keys required, only scripts wired."""
        if not any((script_path, script, name, resume_id)):
            return "one of 'script_path' / 'script' / 'name' / 'resume_id' is required"
        if not script_path and not script:
            pending = [n for n, v in (("name", name), ("resume_id", resume_id)) if v]
            return f"{pending[0]!r} is not supported yet; provide 'script_path' or inline 'script'"
        return None

    async def _materialise_script(self, script: str) -> str:
        """Write an inline ``script`` to disk; returns the absolute path."""
        from openjiuwen.agent_teams.context import get_session_id
        from openjiuwen.agent_teams.workflow.runner import materialize_swarmflow_script

        return await materialize_swarmflow_script(
            script, team_name=self._team_name, session_id=get_session_id()
        )

    def _check_rerun(self, script_path: str, script: str) -> None:
        """Re-run gate on an existing disk script.

        Only bites when a disk script exists AND an inline edit is applied (a
        plain re-pass has old == new and trivially passes): hard-blocks
        ``META.name`` changes, logs a structural-impact preview otherwise.
        """
        if not (script_path and Path(script_path).exists()):
            return
        old_source = Path(script_path).read_text(encoding="utf-8")
        new_source = script or old_source
        self._lint_rerun(old_source=old_source, new_source=new_source)
        diff = self._compute_structural_diff(old_source, new_source)
        if diff.changed_nodes:
            team_logger.info(
                "[swarmflow] re-run impact: structural changes at %s", diff.changed_nodes
            )

    async def _seal_guard(self, script_path: str, resume_id: str) -> str:
        """Blank out a resume_id that points at a TERMINAL (sealed) run.

        Relaunching under it would wrongly replay the sealed cache. Best-effort:
        failures only debug-log, never block the launch.
        """
        if not (resume_id and script_path):
            return resume_id
        try:
            from openjiuwen.agent_teams.context import get_session_id
            from openjiuwen.agent_teams.workflow.engine.journal import Journal
            from openjiuwen.agent_teams.workflow.runner import _resolve_journal_path

            journal_path = _resolve_journal_path(script_path, self._team_name, get_session_id())
            journal = await Journal.load(journal_path, wal_path=f"{journal_path}.wal")
            if journal.find_run_record(resume_id, "seal") is not None:
                team_logger.warning(
                    "[swarmflow] resume_id %s is terminal (sealed); forcing a fresh run_id", resume_id
                )
                return ""
        except Exception as exc:  # noqa: BLE001 - guard is best-effort
            team_logger.debug("[swarmflow] seal guard skipped: %s", exc)
        return resume_id

    def _enriched_inputs(
        self,
        inputs: dict[str, Any],
        *,
        script_path: str,
        resume_id: str,
        ticket: Any,
        agent_gate: Any,
    ) -> dict[str, Any]:
        """Launch inputs: resolved path, run_id, relaunch flag, governor
        ticket/gate, and the completion ctx the finished-run injection reads."""
        enriched = dict(inputs)
        enriched["script_path"] = script_path
        enriched[_RUN_ID_INPUT_KEY] = resume_id or new_swarmflow_run_id()
        # resume_id without action = script-edit relaunch (same run_id): the
        # frontend resets the phase/agent tree, unlike a fresh launch.
        if resume_id:
            enriched[_RELAUNCH_KIND_KEY] = "relaunch"
        enriched[_WORKFLOW_TICKET_KEY] = ticket
        enriched[_AGENT_GATE_KEY] = agent_gate
        enriched[_COMPLETION_CTX_KEY] = {}
        return enriched

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
        from openjiuwen.agent_teams.workflow.engine.errors import BackendError, WorkflowAborted
        from openjiuwen.agent_teams.workflow.engine.progress import (
            ProgressKind,
            WorkflowProgressEvent,
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
                relaunch_kind=inputs.get(_RELAUNCH_KIND_KEY),
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
                workflow_budget=progress.workflow_budget,
                budget_exhausted_scope=progress.budget_exhausted_scope,
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
        # BudgetExhausted is a BaseException: no ``except`` here on purpose —
        # wrapping it would discard its structured fields, and the async-tool
        # runtime renders the leader-facing message from them directly.
        except WorkflowAborted as exc:
            if exc.reason == "early_return":
                msg = self._format_early_return(exc.reply, exc.edit_hints, run_id=run_id)
                # Resumable pause (edit & re-run under the same run_id): flip
                # the Monitor card to paused, then surface the edit guidance.
                _publish(
                    WorkflowProgressEvent(
                        kind=ProgressKind.WORKFLOW_PAUSED,
                        message="workflow paused for script edit",
                    )
                )
                raise BackendError(msg) from exc
            if exc.reason == "stop":
                # A control-state change, not a leader failure: announce it on
                # the team topic BEFORE surfacing the stopped message, so the
                # Monitor can flip the workflow card to stopped.
                _publish(
                    WorkflowProgressEvent(
                        kind=ProgressKind.WORKFLOW_STOPPED,
                        message="workflow stopped",
                    )
                )
                msg = self._format_stopped(run_id=run_id)
                raise BackendError(msg) from exc
            # reason == "pause" (default): silent cancel, controller relaunches on resume.
            # Announce the pause on the team topic first, so the Monitor can flip
            # the workflow card to paused; then re-raise as CancelledError so the
            # async-tool runtime treats it as a silent cancellation (no completion
            # injected) — matching the cancel the controller triggers as pause's
            # third step.
            _publish(
                WorkflowProgressEvent(
                    kind=ProgressKind.WORKFLOW_PAUSED,
                    message="workflow paused",
                )
            )
            raise asyncio.CancelledError() from exc
        except asyncio.CancelledError as exc:
            # Controller-cancel path: the task died mid-call before reaching an
            # abort checkpoint, so emit the status event from the AbortSignal
            # reason. External cancels (signal unset) stay silent.
            if abort_event.is_set():
                if abort_event.reason == "stop":
                    _publish(
                        WorkflowProgressEvent(
                            kind=ProgressKind.WORKFLOW_STOPPED,
                            message="workflow stopped",
                        )
                    )
                    raise BackendError(self._format_stopped(run_id=run_id)) from exc
                # pause (default reason): silent cancel, controller relaunches on resume.
                _publish(
                    WorkflowProgressEvent(
                        kind=ProgressKind.WORKFLOW_PAUSED,
                        message="workflow paused",
                    )
                )
            raise
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

        inputs[_RELAUNCH_KIND_KEY] = "resume"  # normal pause→resume: tree continues
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


def _final_budget_snapshots(events: Any) -> tuple[dict | None, dict | None]:
    """Last session / workflow ledger snapshots carried on the event stream.

    Snapshots ride agent / terminal events only, so a backward scan for the
    last non-``None`` payload of each ledger yields the final tally.
    """
    budget: dict | None = None
    wf_budget: dict | None = None
    for ev in reversed(events):
        if wf_budget is None and getattr(ev, "workflow_budget", None) is not None:
            wf_budget = ev.workflow_budget
        if budget is None and getattr(ev, "budget", None) is not None:
            budget = ev.budget
        if budget is not None and wf_budget is not None:
            break
    return budget, wf_budget


def _phase_token_ranking(events: Any) -> list[tuple[str, int]] | None:
    """Top-3 phases by token consumption, folded from agent events.

    The observer-side twin of the ledger's ``phase_tokens`` tally; ``None``
    when no agent reported a token count.
    """
    from openjiuwen.agent_teams.workflow.engine.progress import ProgressKind

    acc: dict[str, int] = {}
    for ev in events:
        if ev.kind not in (ProgressKind.AGENT_COMPLETED, ProgressKind.AGENT_FAILED):
            continue
        tokens = ev.tokens
        if not tokens:
            continue
        phase = ev.phase or "(unphased)"
        acc[phase] = acc.get(phase, 0) + tokens
    if not acc:
        return None
    return sorted(acc.items(), key=lambda kv: kv[1], reverse=True)[:3]


def _budget_overrun_fields(events: Any, language: str) -> dict | None:
    """Placeholder dict for ``swarmflow.budget_overrun``, or ``None`` when within budget.

    A completed run never raises, so the failed-path feedback stays silent for
    it; this derives the overrun from the final ledger snapshots instead.
    Session wins over workflow when both overran (matches the engine gate).
    """
    if not events:
        return None
    budget, wf_budget = _final_budget_snapshots(events)
    if budget is not None and budget.get("total") is not None and budget.get("exhausted"):
        scope, snap = "session", budget
    elif (
        wf_budget is not None
        and wf_budget.get("total") is not None
        and wf_budget.get("exhausted")
    ):
        scope, snap = "workflow", wf_budget
    else:
        return None
    return _budget_feedback_fields(
        scope=scope,
        spent=snap.get("spent"),
        total=snap.get("total"),
        workflow_spent=wf_budget.get("spent") if scope == "session" else None,
        workflow_total=wf_budget.get("total") if scope == "session" else None,
        top_phases=_phase_token_ranking(events),
        language=language,
        guidance_key="swarmflow.budget_overrun",
    )


def _format_top_phases(top_phases: list[tuple[str, int]] | None) -> str:
    """Render the top-3 phase list as ``phase(tokens), phase(tokens)`` or empty."""
    if not top_phases:
        return ""
    return ", ".join(f"{name}({tokens})" for name, tokens in top_phases)


_TRIGGER_LAYER_LABELS = {
    ("workflow", "cn"): "workflow（单次额度）",
    ("workflow", "en"): "workflow (per-run)",
    ("session", "cn"): "session（会话总额）",
    ("session", "en"): "session (shared)",
}


def _budget_feedback_fields(
    *,
    scope: str,
    spent: int | None,
    total: int | None,
    workflow_spent: int | None,
    workflow_total: int | None,
    top_phases: list[tuple[str, int]] | None,
    language: str,
    guidance_key: str,
) -> dict:
    """Shared i18n placeholders for the budget overrun / exhausted templates.

    ``workflow_contrast`` pairs the per-run tally with a session-scope trigger
    (a workflow-scope trigger's spent/total already IS the per-run tally);
    ``guidance`` resolves the scope-paired advice text.
    """
    lang = language if language in ("cn", "en") else "cn"
    if scope == "session" and workflow_spent is not None and workflow_total is not None:
        workflow_contrast = (
            f"Run 级对照：spent={workflow_spent}/{workflow_total}。"
            if lang == "cn"
            else f"Run-level tally: spent={workflow_spent}/{workflow_total}. "
        )
    else:
        workflow_contrast = ""
    return {
        "spent": spent if spent is not None else "?",
        "total": total if total is not None else "?",
        "trigger_layer": _TRIGGER_LAYER_LABELS[(scope, lang)],
        "workflow_contrast": workflow_contrast,
        "top_phases": _format_top_phases(top_phases) or ("（无）" if lang == "cn" else "(none)"),
        "guidance": STRINGS[lang][f"{guidance_key}.{scope}_guidance"],
    }


def _budget_exhausted_fields(exc: "BudgetExhausted", language: str) -> dict:
    """Placeholder dict for ``swarmflow.budget_exhausted``, from the exception's fields."""
    fields = _budget_feedback_fields(
        scope=getattr(exc, "scope", "session") or "session",
        spent=getattr(exc, "spent", None),
        total=getattr(exc, "total", None),
        workflow_spent=getattr(exc, "workflow_spent", None),
        workflow_total=getattr(exc, "workflow_total", None),
        top_phases=getattr(exc, "top_phases", None),
        language=language,
        guidance_key="swarmflow.budget_exhausted",
    )
    fields["detail"] = str(exc)
    return fields


__all__ = ["SwarmflowTool", "WorkerModelResolver"]
