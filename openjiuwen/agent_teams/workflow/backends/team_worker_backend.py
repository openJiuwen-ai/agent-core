# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The team integration backend: maps each engine ``agent()`` call onto a worker.

This is the seam where the business-agnostic swarmflow engine meets agent_teams.
For every ``agent(prompt, schema=...)`` the engine issues, the backend:

1. mints a unique ``WORKER`` member identity (used only as the worker's card /
   owner id / workspace name — swarmflow workers are ephemeral, single-shot
   executors, NOT teammates, so they get no team-DB roster row);
2. derives a worker ``DeepAgentSpec`` from the team's *teammate* spec (or the
   leader spec when no teammate is configured) — so a worker is "a teammate
   without team tools": it keeps teammate capabilities (model / tools / skills /
   workspace / sys_operation / todo planning) but, being built straight from the
   raw spec, carries none of the team collaboration tools (those are injected
   per-member by the configurator, not present on the raw spec);
3. builds a :class:`TeamHarness` over that spec and runs it for ONE non-streaming
   execution via :meth:`TeamHarness.run_once` (a plain ``DeepAgent.invoke`` — no
   supervisor, no steer); the worker ends by calling ``structured_output``, whose
   captured arguments ARE the structured result (the harness has no native
   structured output, so the tool call is how the schema is enforced);
4. disposes the worker harness and returns an :class:`AgentResult`.

When the engine requested no schema, the worker runs without ``structured_output``
and its final free-text answer is returned. A worker that fails to submit a
structured result raises, which the engine's retry loop handles (and, after
exhaustion, surfaces as ``agent()`` returning ``None`` — a value every dw
control-flow helper already tolerates).

The actual harness execution lives in :meth:`_execute_worker` so tests can
override it without standing up a real LLM.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Sequence

from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.schema.deep_agent_spec import WorkspaceSpec
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.workspace_layout import ensure_team_member_workspace_link
from openjiuwen.agent_teams.tools.structured_output_tool import (
    StructuredOutputFinishRail,
    StructuredOutputTool,
)
from openjiuwen.agent_teams.workflow.engine.backends.base import AgentBackend, AgentResult
from openjiuwen.agent_teams.workflow.engine.errors import BackendError
from openjiuwen.agent_teams.workflow.worktree import SwarmflowWorkerWorktrees
from openjiuwen.core.common.logging import team_logger

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class TeamWorkerBackend(AgentBackend):
    """Engine ``AgentBackend`` that executes each call as a single-shot worker.

    Args:
        model: Legacy default ``Model`` (kept for construction compatibility;
            the harness path resolves a worker's model from its spec / the
            per-call config resolver instead).
        team_name: Team name used to namespace worker member ids.
        language: Prompt language hint (drives the structured-output tool i18n).
        max_iterations: Reserved hard cap on a worker's turns.
        model_resolver: Optional callback mapping an ``agent(model=...)`` name
            hint (or ``None`` for the default) to a worker ``TeamModelConfig``.
            Returns ``None`` when the name is unknown / no pool is configured, in
            which case the worker inherits its base spec's model. Keeps the
            pool/allocator lookup in the team layer — the backend only asks
            "name in, config out".
        worker_base_spec: The base ``DeepAgentSpec`` each worker derives from
            (the team's teammate spec, or the leader spec). Required by the
            harness path; ``None`` is only valid when :meth:`_execute_worker` is
            overridden (tests).
        build_context: Optional ``BuildContext`` forwarded to the worker harness
            build (worker specs carry no team rails, so this is usually unused).
    """

    def __init__(
        self,
        *,
        model: Any,
        team_name: str = "swarmflow",
        language: str = "cn",
        max_iterations: int = 6,
        model_resolver: Callable[[str], Any] | None = None,
        worker_base_spec: Any = None,
        human_base_spec: Any = None,
        build_context: Any = None,
        messager: Any = None,
        session_id: str | None = None,
        on_human_prompt: Callable[[str, str, str], None] | None = None,
        on_human_replied: Callable[[str, str], None] | None = None,
        run_id: str | None = None,
    ) -> None:
        self._model = model
        self._team_name = team_name
        self._language = language
        self._max_iterations = max_iterations
        self._model_resolver = model_resolver
        self._worker_base_spec = worker_base_spec
        self._human_base_spec = human_base_spec
        self._build_context = build_context
        self._messager = messager
        self._session_id = session_id
        self._on_human_prompt = on_human_prompt
        self._on_human_replied = on_human_replied
        self._run_id = run_id
        self._run_prefix = self._run_id_prefix(run_id)
        self._worktrees = SwarmflowWorkerWorktrees(
            team_name=team_name,
            build_context=build_context,
            session_id=session_id,
        )
        self._t = make_translator(language if language in ("cn", "en") else "cn")
        self._counter = 0
        # Stateful agent_session / human_session manager, built on first use so a
        # workflow that only uses single-shot agent() never pays for it.
        self._session_mgr: Any = None

    async def run(self, prompt: str, opts: dict, schema_json: dict | None) -> AgentResult:
        member_name = self._next_member_name(opts)
        model = self._resolve_model(opts.get("model"))
        try:
            await self._worktrees.ensure(member_name, opts)
            if schema_json is not None:
                # The harness's ability manager re-qualifies the tool id per owner
                # (``structured_output_{worker_owner_id}``), so concurrent workers
                # never collide and no per-call id is needed here.
                submit_tool = StructuredOutputTool(schema_json, self._t)
                text = await self._execute_worker(
                    prompt,
                    [submit_tool],
                    member_name=member_name,
                    has_schema=True,
                    model=model,
                )
                if not (submit_tool.called and submit_tool.captured is not None):
                    raise BackendError(
                        f"worker '{member_name}' did not submit a structured result via structured_output"
                    )
                return AgentResult(
                    text=text,
                    structured=submit_tool.captured,
                    tokens=self._estimate_tokens(prompt, submit_tool.captured),
                )
            text = await self._execute_worker(
                prompt,
                [],
                member_name=member_name,
                has_schema=False,
                model=model,
            )
            return AgentResult(text=text, tokens=self._estimate_tokens(prompt, text))
        finally:
            await self._worktrees.finalize(member_name)

    # ------------------------------------------------------------------
    # Stateful sessions (agent_session / human_session) — delegated
    # ------------------------------------------------------------------

    def _sessions(self) -> Any:
        """Lazily build the avatar-session manager (only when a session is opened)."""
        if self._session_mgr is None:
            from openjiuwen.agent_teams.workflow.backends.avatar_session_backend import (
                AvatarSessionManager,
            )

            self._session_mgr = AvatarSessionManager(
                worker_base_spec=self._worker_base_spec,
                human_base_spec=self._human_base_spec,
                team_name=self._team_name,
                language=self._language,
                model_resolver=self._model_resolver,
                build_context=self._build_context,
                t=self._t,
                messager=self._messager,
                session_id=self._session_id,
                on_human_prompt=self._on_human_prompt,
                on_human_replied=self._on_human_replied,
            )
        return self._session_mgr

    async def open_session(self, *, kind: str, instructions: str | None, opts: dict) -> str:
        """Open a stateful session (see :class:`AvatarSessionManager`)."""
        return await self._sessions().open_session(kind=kind, instructions=instructions, opts=opts)

    async def send_turn(
        self,
        session_id: str,
        prompt: str,
        opts: dict,
        schema_json: dict | None,
        *,
        history: Sequence[dict] = (),
        correlation_id: str | None = None,
    ) -> AgentResult:
        """Advance one turn on an open session."""
        return await self._sessions().send_turn(
            session_id, prompt, opts, schema_json, history=history, correlation_id=correlation_id
        )

    async def close_session(self, session_id: str) -> None:
        """Close one open session (no-op when no session was ever opened)."""
        if self._session_mgr is not None:
            await self._session_mgr.close_session(session_id)

    async def aclose(self) -> None:
        """Dispose every session opened during the run (run-end teardown)."""
        if self._session_mgr is not None:
            await self._session_mgr.aclose()

    async def abort_sessions(self) -> None:
        """Abort every live avatar session's in-flight turn (pause path).

        Delegates to the session manager when one exists; a workflow that used
        only single-shot ``agent()`` has none, so this is a no-op there. The
        single-shot ``run_once`` workers are NOT touched here — they have no
        supervisor and are stopped by the top-level task cancel instead.
        """
        if self._session_mgr is not None:
            await self._session_mgr.abort_all()

    def _resolve_model(self, model_name: str | None) -> Any:
        """Resolve a per-call ``model`` hint to a worker ``TeamModelConfig``.

        Args:
            model_name: The ``agent(model=...)`` hint for this call, or ``None``.

        Returns:
            A ``TeamModelConfig`` when the injected resolver maps the hint (or the
            default) to a pool entry; otherwise ``None`` so the worker inherits
            its base spec's model.
        """
        if self._model_resolver is None:
            return None
        return self._model_resolver(model_name) if model_name else self._model_resolver(None)

    # ------------------------------------------------------------------
    # Worker execution (override point for tests)
    # ------------------------------------------------------------------

    async def _execute_worker(
        self,
        prompt: str,
        tools: Sequence[Any],
        *,
        member_name: str,
        has_schema: bool,
        model: Any,
    ) -> str:
        """Build a worker ``TeamHarness`` and run it for one execution.

        Args:
            prompt: The task text for this worker.
            tools: Extra tool instances to expose (the per-call
                ``StructuredOutputTool`` on the schema path, empty otherwise).
            member_name: Minted ``WORKER`` identity for this call.
            has_schema: Whether structured output via ``structured_output`` is
                required.
            model: The resolved ``TeamModelConfig`` for this worker, or ``None``
                to inherit the base spec's model (see :meth:`_resolve_model`).

        Returns:
            The worker's final free-text output (only meaningful when no schema
            was requested). Structured output is read off the
            ``StructuredOutputTool`` instance by :meth:`run`, not from this value.
        """
        from openjiuwen.agent_teams.harness.team_harness import TeamHarness
        from openjiuwen.agent_teams.workflow.backends._member_spec import (
            derive_member_build_context,
            derive_member_spec,
        )

        if self._worker_base_spec is None:
            raise BackendError(
                "TeamWorkerBackend requires a worker_base_spec to build a worker harness"
            )

        try:
            # Worker = teammate without team tools: per-call model (else inherit),
            # single-shot prompt, base tools + the per-call structured_output
            # instance. enable_task_loop / enable_task_planning ride along from
            # the base spec, so DeepAgent todo planning is preserved.
            worker_spec = derive_member_spec(
                self._worker_base_spec,
                team_name=self._team_name,
                member_name=member_name,
                system_prompt=(
                    self._t("swarmflow_worker", key="schema")
                    if has_schema
                    else self._t("swarmflow_worker", key="free")
                ),
                model=model,
                extra_tools=tools,
                description="swarmflow worker",
            )
            # Worker gets its own workspace, not the teammate's.
            worker_workspace = self._setup_worker_workspace(member_name)
            worker_spec = worker_spec.model_copy(update={"workspace": worker_workspace})
            worker_build_context = derive_member_build_context(
                self._build_context,
                team_name=self._team_name,
                member_name=member_name,
                language=self._language,
            )
            harness = TeamHarness.build(
                agent_spec=worker_spec,
                role=TeamRole.WORKER,
                member_name=member_name,
                build_context=worker_build_context,
            )
            if has_schema:
                # End the round as soon as structured_output is captured, so the
                # model can't loop re-calling it (the ack carries no stop signal).
                harness.add_rail(StructuredOutputFinishRail())
        except Exception as e:
            team_logger.exception("worker harness build failed for %s", member_name)
            raise BackendError(f"worker harness build failed for {member_name}: {e}") from e

        try:
            # When schema is required, append a reminder to the user prompt so
            # the LLM is prompted three times (system / tool desc / user) to
            # call structured_output.
            user_prompt = prompt
            if has_schema:
                user_prompt = f"{prompt}\n\n{self._t('structured_output', key='reminder')}"
            result = await harness.run_once(user_prompt)
        except Exception as e:
            team_logger.exception("worker harness run_once failed for %s", member_name)
            raise BackendError(f"worker harness run_once failed for {member_name}: {e}") from e
        finally:
            # Single-shot worker teardown. The harness owns its tool lifecycle:
            # ``run_once`` already dropped the worker's owner-qualified tools (the
            # structured_output instance included) via ``teardown_tools``; here we
            # only release the remaining per-agent resource (sys_operation).
            try:
                await harness.dispose()
            except Exception:
                team_logger.debug("worker harness dispose failed for %s", member_name)
        if isinstance(result, dict):
            return str(result.get("output", ""))
        return str(result)

    # ------------------------------------------------------------------
    # Worker workspace setup
    # ------------------------------------------------------------------

    def _setup_worker_workspace(self, member_name: str) -> WorkspaceSpec:
        """Compute, link, and mount the worker's independent workspace.

        Mirrors the layout used by ``agent_configurator`` for stable_base
        members: each worker gets its own workspace at
        ``{agent_teams_home}/{team_name}/workspaces/{member}_workspace/``.
        It lives under the team home, which ``agent_configurator`` already
        registers for team cleanup — so the worker workspace is removed with
        the team and needs no per-worker cleanup registration. Also mounts the
        team shared workspace into the worker's tree (``.team/{team_name}/``).

        Returns:
            A ``WorkspaceSpec`` with the worker's resolved root_path.
        """
        worktree = self._worktrees.get(member_name)
        workspace_is_worktree = worktree is not None
        # Compute worker's workspace path. With ``agent(options={"isolation": "worktree"})``,
        # the worker starts directly inside the owner-scoped worktree.
        ws_root = (
            worktree.worktree_path
            if worktree is not None
            else ensure_team_member_workspace_link(self._team_name, member_name)
        )

        if self._worker_base_spec.workspace is not None:
            # Inherit language / stable_base from the base spec, only override root_path.
            worker_workspace = self._worker_base_spec.workspace.model_copy(
                update={"root_path": ws_root, "stable_base": not workspace_is_worktree}
            )
        else:
            # Base spec has no workspace — create a fresh one for this worker.
            worker_workspace = WorkspaceSpec(
                root_path=ws_root,
                language=self._language,
                stable_base=not workspace_is_worktree,
            )

        # Mount team workspace into worker workspace so it can access shared
        # files via .team/{team_name}/ — mirrors agent_configurator.
        from openjiuwen.agent_teams.rails.team_context import get_workspace_manager
        workspace_manager = get_workspace_manager(self._build_context)
        if workspace_manager is not None:
            workspace_manager.mount_into_workspace(ws_root)

        return worker_workspace

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _next_member_name(self, opts: dict) -> str:
        """Mint a unique worker member name from the call label and run prefix.

        ``{run_prefix}-{label-slug}-{n}`` (or ``wf-{label-slug}-{n}`` when no
        run id is set) — lowercase ASCII, the leading run/``wf-`` prefix
        guarantees it starts with a letter (satisfies member-name routing/path
        constraints). ``n`` is a per-backend counter; the synchronous
        read-increment between awaits keeps it collision-free under the
        engine's concurrent fan-out.
        """
        n = self._counter
        self._counter += 1
        label = str(opts.get("label") or "worker")
        slug = _SLUG_RE.sub("-", label.lower()).strip("-") or "worker"
        if self._run_prefix:
            return f"{self._run_prefix}-{slug}-{n}"
        return f"wf-{slug}-{n}"

    @staticmethod
    def _estimate_tokens(prompt: str, result: Any) -> int:
        try:
            payload = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        except Exception:
            payload = str(result)
        return len(prompt) // 4 + len(payload) // 4

    @staticmethod
    def _run_id_prefix(run_id: str | None) -> str | None:
        """Slug the full run id for worker member-name prefixing."""
        if not run_id:
            return None
        slug = _SLUG_RE.sub("-", run_id.lower()).strip("-")
        return slug or None


__all__ = ["TeamWorkerBackend"]
