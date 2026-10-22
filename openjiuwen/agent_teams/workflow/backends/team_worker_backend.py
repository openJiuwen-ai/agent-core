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
   without team tools": it keeps teammate capabilities (model / tools / Skill
   view / workspace / sys_operation / todo planning) but, being built straight
   from the raw spec, carries none of the team collaboration tools (those are
   injected per-member by the configurator, not present on the raw spec). Its
   Skill view is the one shared library narrowed by its own visibility
   declaration, mounted here rather than inherited from the raw spec;
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

import hashlib
import json
import re
from typing import Any, Callable, Mapping, Sequence

from openjiuwen.agent_teams.kv_cache import kv_cache_harness_session_lifecycle_hook
from openjiuwen.agent_teams.schema.deep_agent_spec import WorkspaceSpec
from openjiuwen.agent_teams.schema.team import ExternalCliModelConfig, TeamRole
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.tools.structured_output_tool import (
    StructuredOutputFinishRail,
    StructuredOutputTool,
)
from openjiuwen.agent_teams.workflow.backends._result_text import (
    prefer_natural_or_structured_text,
)
from openjiuwen.agent_teams.workflow.backends.activity_emitter import build_activity_event
from openjiuwen.agent_teams.workflow.backends.budget_rail import SwarmflowBudgetRail
from openjiuwen.agent_teams.workflow.backends.external_turns import ExternalMemberTurns, ExternalTurnOutcome
from openjiuwen.agent_teams.workflow.engine.backends.base import AgentBackend, AgentResult
from openjiuwen.agent_teams.workflow.engine.errors import BackendError
from openjiuwen.agent_teams.workflow.worktree import SwarmflowWorkerWorktrees
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.single_agent.ability_manager import resolve_tool_result_text
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    AgentRail,
    ToolCallInputs,
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _tool_call_id(inputs: ToolCallInputs) -> str | None:
    """Return the native tool-call id on one tool execution, or ``None``."""
    tool_call = getattr(inputs, "tool_call", None)
    if tool_call is None:
        return None
    call_id = getattr(tool_call, "id", None)
    return str(call_id) if call_id else None


def _tool_args_preview(args: Any) -> str | None:
    """Render tool arguments as a compact preview for the activity event.

    Raw JSON-string arguments (how ``before_tool_call`` sees them) are parsed
    and re-dumped on one line; structured dicts dump as-is. Plain text passes
    through untouched. ``None`` (no arguments) stays ``None``.
    """
    if args is None:
        return None
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except (ValueError, TypeError):
            return args
        args = parsed
    if isinstance(args, str) or isinstance(args, (int, float, bool)):
        return str(args)
    return json.dumps(args, ensure_ascii=False, default=str)


def _text_from_invoke_result(result: Any, *, member_name: str) -> str:
    """Map a DeepAgent ``invoke`` / task-loop result dict to worker output text.

    Task-loop failures resolve ``wait_completion()`` with ``{"error": "..."}``
    (no ``output``). Treat that as a backend error so SwarmFlow emits
    ``agent_failed`` instead of ``agent_completed`` with an empty outcome.
    A bare empty ``output`` with no error remains a valid success.
    """
    if isinstance(result, dict):
        err = result.get("error")
        if err or result.get("result_type") == "error":
            msg = str(err or result.get("output") or "worker task failed")
            raise BackendError(f"worker '{member_name}' failed: {msg}")
        return str(result.get("output", ""))
    return str(result)


class SwarmflowActivityRail(AgentRail):
    """Emit live tool activity from a worker to the run's progress stream.

    A single-shot worker executes as one opaque ``run_once``, so without this
    rail the run's only visibility into it is started/completed. This rail
    hooks every tool execution twice — before and after — and forwards an
    unthrottled ``agent_activity`` event per hook so a spectator UI (e.g. the
    Swarm Map) can show what a worker is doing mid-run instead of just
    start/finish. The two events of one tool execution pair on
    ``tool_call_id``; the payload is structured (``activity_type`` /
    ``tool_name`` / ``content``), ``message="tool: <name>"`` stays for legacy
    consumers.
    """

    priority: int = 500

    #: ``after_tool_call`` reads the tool message other rails may still rewrite
    #: (e.g. ``StructuredOutputFinishRail``), so that one hook runs after every
    #: rail in its chain while ``init`` keeps the plain priority order.
    _AFTER_TOOL_CALL_READER_OFFSET = 1_000_000

    def __init__(self, emit: Callable[[str, str | None, str | None, str | None], None]) -> None:
        super().__init__()
        self._emit = emit

    def callback_priority(self, event: AgentCallbackEvent) -> int:
        """Run after_tool_call after every rail that produces the tool result."""
        if event == AgentCallbackEvent.AFTER_TOOL_CALL:
            return self.priority - self._AFTER_TOOL_CALL_READER_OFFSET
        return self.priority

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return
        tool_name = (getattr(inputs, "tool_name", "") or "").strip()
        if not tool_name:
            return
        self._emit("tool_call", _tool_call_id(inputs), tool_name, _tool_args_preview(inputs.tool_args))

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return
        tool_name = (getattr(inputs, "tool_name", "") or "").strip()
        if not tool_name:
            return
        # The text the model reads is the single fact source; a non-tool
        # exception (no AbilityExecutionError tool message) falls back to its
        # own text, and a bare raw result (no tool_msg) to its repr — so the
        # tool_result event always previews what the call produced.
        content = resolve_tool_result_text(inputs, ctx.exception)
        if content is None and ctx.exception is not None:
            content = str(ctx.exception)
        if content is None and inputs.tool_result is not None:
            content = str(inputs.tool_result)
        self._emit("tool_result", _tool_call_id(inputs), tool_name, content)


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
            Returns ``None`` when the name is unknown / no pool is configured —
            :meth:`_resolve_model` then raises a non-retryable pool-miss error
            instead of silently inheriting. Keeps the pool/allocator lookup in
            the team layer — the backend only asks "name in, config out".
        worker_base_spec: The base ``DeepAgentSpec`` each worker derives from
            (the team's teammate spec, or the leader spec). Required by the
            harness path; ``None`` is only valid when :meth:`_execute_worker` is
            overridden (tests).
        build_context: Optional ``BuildContext`` forwarded to the worker harness
            build (worker specs carry no team rails, so this is usually unused).
        team_backend: The host team backend handle — source of the team model
            pool for third-party model planning and native pool-miss messages.
            ``None`` (tests / a backend without a team) leaves the pool empty.
        expert_specs: AgentGroup members a script may pick via ``agent_template``
            (member name → ``SwarmflowExpertEntry``). Empty when no group is bound.
    """

    #: Options this backend understands beyond the engine's own set.
    KNOWN_OPTIONS = frozenset({"agent_template"})

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
        on_human_replied: Callable[[str, str, str | None], None] | None = None,
        run_id: str | None = None,
        workflow_name: str | None = None,
        external_cli_agents: Sequence[Any] = (),
        team_backend: Any = None,
        expert_specs: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self._model = model
        self._team_name = team_name
        self._language = language
        self._max_iterations = max_iterations
        self._model_resolver = model_resolver
        self._worker_base_spec = worker_base_spec
        self._human_base_spec = human_base_spec
        self._build_context = build_context
        self._team_backend = team_backend
        self._messager = messager
        self._session_id = session_id
        from openjiuwen.core.session import get_current_session

        current_session = get_current_session()
        get_runtime = getattr(current_session, "get_kv_cache_runtime", None)
        self._kv_cache_runtime = get_runtime() if callable(get_runtime) else None
        self._on_human_prompt = on_human_prompt
        self._on_human_replied = on_human_replied
        self._run_id = run_id
        self._workflow_name = workflow_name
        self._external_cli_agents: tuple[Any, ...] = tuple(external_cli_agents)
        self._expert_specs: dict[str, Any] = dict(expert_specs or {})
        self._run_prefix = self._run_id_prefix(run_id)
        self._worktrees = SwarmflowWorkerWorktrees(
            team_name=team_name,
            build_context=build_context,
            session_id=session_id,
        )
        # Bind the team's resident WorkspaceCache when the build context
        # exposes a workspace manager, so worker prompts (swarmflow_worker /
        # structured_output) resolve evolved values; None (no team workspace)
        # keeps the framework defaults. The worker path has no TeamBackend
        # object (it runs outside the member shell), so manager-direct access
        # is the one declared exception to the "read cache off the backend"
        # contract.
        cache = None
        if build_context is not None:
            from openjiuwen.agent_teams.rails.team_context import get_workspace_manager

            ws_mgr = get_workspace_manager(build_context)
            if ws_mgr is not None:
                cache = ws_mgr.workspace_cache
        self._t = make_translator(
            language if language in ("cn", "en") else "cn",
            ws_cache=cache,
        )
        self._counter = 0
        # Stateful agent_session / human_session manager, built on first use so a
        # workflow that only uses single-shot agent() never pays for it.
        self._session_mgr: Any = None

    async def run(
        self, prompt: str, opts: dict, schema_json: dict | None, *, call_key: str | None = None
    ) -> AgentResult:
        member_name = self._next_member_name(opts, call_key)
        agent_type = opts.get("agent_type")
        # Expert pick (AgentGroup member). Gated before any resource is touched,
        # like agent_type: a bad member name is a script error, not a retry.
        # It runs FIRST so a script naming both knobs is told they are mutually
        # exclusive, instead of hearing about whichever CLI it also named.
        expert = self._gate_agent_template(opts)
        expert_kw = {"expert": expert} if expert is not None else {}
        # Gate before any resource is touched: a bad agent_type is a script
        # error; the engine still retries it (cheap, deterministic) before
        # AGENT_FAILED reaches the leader.
        entry = self._gate_agent_type(agent_type) if agent_type else None
        # The engine tags the worker's started/completed events with the
        # deterministic call-path key; reuse it so live activity events resolve
        # to the same worker node downstream.
        agent_id = opts.get("agent_id")
        phase = opts.get("phase")
        label = opts.get("label")
        # One rail per call: it bills this worker's model calls to the run's
        # shared ledger (and cuts the worker short once that ledger is dry),
        # while its own tally is what this ``agent()`` call reports as its cost.
        budget_rail = SwarmflowBudgetRail(self.budget, workflow_budget=self.workflow_budget)
        try:
            # Third-party calls validate their model options BEFORE any resource
            # is touched: a rule violation (pool miss, mutual exclusion, bad
            # effort) is a script error that must not have spent a worktree slot.
            model_config = None
            if entry is not None:
                # Imported lazily: external_model lives under agent_teams.external,
                # whose package __init__ eagerly imports tool_gateway ->
                # team_workspace.tools, which re-enters this module mid-init
                # through workflow/__init__ (same reason tool_member defers it).
                from openjiuwen.agent_teams.workflow.backends.external_model import plan_external_model

                model_config = plan_external_model(
                    cli_agent=agent_type,
                    model_hint=opts.get("model"),
                    builtin_hint=opts.get("builtin_model"),
                    effort_hint=opts.get("effort"),
                    entry=entry,
                    team_backend=self._team_backend,
                ).primary
            elif opts.get("builtin_model") is not None or opts.get("effort") is not None:
                # A native call offering builtin_model / effort is a script error
                # (only external CLI agents run models on their own login); the
                # resolver's cli_agent=None rule raises it non-retryably before
                # any resource is spent. Plain native calls skip this entirely.
                from openjiuwen.agent_teams.workflow.backends.external_model import plan_external_model

                plan_external_model(
                    cli_agent=None,
                    model_hint=opts.get("model"),
                    builtin_hint=opts.get("builtin_model"),
                    effort_hint=opts.get("effort"),
                    entry=None,
                    team_backend=self._team_backend,
                )
            await self._worktrees.ensure(member_name, opts)
            if entry is not None:
                outcome = await self._execute_external_worker(
                    prompt,
                    member_name=member_name,
                    entry=entry,
                    agent_type=agent_type,
                    opts=opts,
                    schema_json=schema_json,
                    budget_rail=budget_rail,
                    model_config=model_config,
                )
                return AgentResult(
                    text=outcome.text,
                    structured=outcome.structured,
                    tokens=budget_rail.call_tokens,
                    cache_tokens=budget_rail.call_cache_tokens or None,
                    input_tokens=budget_rail.call_input_tokens or None,
                    output_tokens=budget_rail.call_output_tokens or None,
                    model=outcome.model,
                )
            model = self._resolve_model(opts.get("model"))
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
                    budget_rail=budget_rail,
                    agent_id=agent_id,
                    phase=phase,
                    label=label,
                    **expert_kw,
                )
                if not (submit_tool.called and submit_tool.captured is not None):
                    raise BackendError(
                        f"worker '{member_name}' did not submit a structured result via structured_output"
                    )
                # Prefer free-text narration; fall back to JSON of the capture.
                text = prefer_natural_or_structured_text(text, submit_tool.captured)
                return AgentResult(
                    text=text,
                    structured=submit_tool.captured,
                    tokens=budget_rail.call_tokens,
                    cache_tokens=budget_rail.call_cache_tokens or None,
                    input_tokens=budget_rail.call_input_tokens or None,
                    output_tokens=budget_rail.call_output_tokens or None,
                )
            text = await self._execute_worker(
                prompt,
                [],
                member_name=member_name,
                has_schema=False,
                model=model,
                budget_rail=budget_rail,
                agent_id=agent_id,
                phase=phase,
                label=label,
                **expert_kw,
            )
            return AgentResult(
                text=text,
                tokens=budget_rail.call_tokens,
                cache_tokens=budget_rail.call_cache_tokens or None,
                input_tokens=budget_rail.call_input_tokens or None,
                output_tokens=budget_rail.call_output_tokens or None,
            )
        except Exception as e:
            # Attach this call's rail tally so a failed/budget-exhausted agent's
            # real consumption still reaches the AGENT_FAILED event tokens (the
            # ledger already billed it; without this the run's token sum would
            # drop it while Team budget stayed correct).
            call_tokens = budget_rail.call_tokens or None
            if isinstance(e, BackendError):
                if e.tokens is None:
                    e.tokens = call_tokens
            elif call_tokens is not None:
                # Wrap a non-backend error so _attempt_calls can still read .tokens
                e = BackendError(str(e), tokens=call_tokens)
            raise
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
                budget=self.budget,
                workflow_budget=self.workflow_budget,
                messager=self._messager,
                session_id=self._session_id,
                run_id=self._run_id,
                workflow_name=self._workflow_name,
                on_human_prompt=self._on_human_prompt,
                on_human_replied=self._on_human_replied,
                kv_cache_runtime=self._kv_cache_runtime,
                skill_visibility_fn=self._apply_worker_skill_visibility,
                external_cli_agents=self._external_cli_agents,
                gate_agent_type=self._gate_agent_type,
                gate_agent_template=self._gate_agent_template,
                external_cwd=self._external_cwd,
                team_backend=self._team_backend,
                progress_sink=self.progress_sink,
            )
        return self._session_mgr

    async def capture_fork(self, session_id: str, *, keep_rounds: int | None, fork_mode: str) -> dict | None:
        """Snapshot a session's context for forking (see :class:`AvatarSessionManager`)."""
        return await self._sessions().capture_fork(session_id, keep_rounds=keep_rounds, fork_mode=fork_mode)

    async def ensure_member_name(self, *, kind: str, opts: dict) -> str:
        """Reserve a session's member identity without building its avatar."""
        return await self._sessions().ensure_member_name(kind=kind, opts=opts)

    async def open_session(
        self,
        *,
        kind: str,
        instructions: str | None,
        opts: dict,
        fork_data: dict | None = None,
        member_name: str | None = None,
    ) -> str:
        """Open a stateful session (see :class:`AvatarSessionManager`)."""
        return await self._sessions().open_session(
            kind=kind,
            instructions=instructions,
            opts=opts,
            fork_data=fork_data,
            member_name=member_name,
        )

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
            default) to a pool entry; ``None`` for an absent hint so the worker
            inherits its base spec's model.

        Raises:
            BackendError: a non-empty hint the resolver cannot map is a pool
                miss — deterministic, so it is non-retryable and names the pool's
                compatible models instead of silently falling back.
        """
        if self._model_resolver is None:
            return None
        if model_name:
            resolved = self._model_resolver(model_name)
            if resolved is None:
                raise BackendError(self._pool_miss_message(model_name), retryable=False)
            return resolved
        return self._model_resolver(None)

    def _pool_miss_message(self, model_name: str) -> str:
        """Compose the native pool-miss error: the hint plus the pool's model names."""
        pool = self._team_backend.get_model_pool() if self._team_backend is not None else []
        names = sorted({getattr(e, "model_name", "") for e in pool if getattr(e, "model_name", "")})
        if names:
            return (
                f"model '{model_name}' is not offered by the team model pool; "
                f"the team model pool offers: {', '.join(names)}"
            )
        return (
            f"model '{model_name}' is not offered by the team model pool; "
            "the team model pool has no compatible model"
        )

    def model_pool_names(self) -> list[str] | None:
        """Accepted ``model`` hint names, for the engine's fail-fast check.

        Probes the injected resolver for a ``pool_names`` capability (the
        configurator's ``_SwarmflowModelResolver`` exposes one). An old-style
        closure resolver has no such attribute and degrades to ``None`` —
        no validation, exactly the pre-validation behavior.
        """
        if self._model_resolver is None:
            return None
        pool_names = getattr(self._model_resolver, "pool_names", None)
        return list(pool_names()) if callable(pool_names) else None

    def describe_base_model(self) -> str | None:
        """Name the worker base spec's model (same extraction as the converter)."""
        config = getattr(self._worker_base_spec, "model", None) if self._worker_base_spec is not None else None
        request = getattr(config, "model_request_config", None)
        name = getattr(request, "model_name", None) or getattr(request, "model", None)
        return str(name) if name else None

    def describe_model(self, opts: dict) -> str | None:
        """Return the effective model string for an external agent call, or None for native.

        Third-party calls resolve the same plan the launch uses, so the display
        tracks the *validated* model (pool hit name, catalog builtin). An
        invalid combo never raises here — it surfaces loudly at launch — so the
        fallback is the best-known hint.
        """
        agent_type = opts.get("agent_type")
        if not agent_type:
            return None
        from openjiuwen.agent_teams.workflow.backends.external_model import plan_external_model

        entry = next((e for e in self._external_cli_agents if e.cli_agent == agent_type), None)
        try:
            return plan_external_model(
                cli_agent=agent_type,
                model_hint=opts.get("model"),
                builtin_hint=opts.get("builtin_model"),
                effort_hint=opts.get("effort"),
                entry=entry,
                team_backend=self._team_backend,
            ).display
        except BackendError:
            return str(opts.get("model") or opts.get("builtin_model") or "") or None

    def _gate_agent_type(self, agent_type: str) -> Any:
        """Validate ``agent_type`` against the SDK registry and team declaration.

        Two independent allow-lists: the engine must know how to drive the
        backend (SDK kind only in this version), and the team must have
        declared it — the declaration carries the launch config and is the
        operator's consent to spend on that CLI.
        """
        from openjiuwen.agent_teams.external.cli_agent.backends import _SDK_BACKENDS, backend_for

        registry = ", ".join(sorted(_SDK_BACKENDS))
        declared_names = [e.cli_agent for e in self._external_cli_agents]
        declared = ", ".join(declared_names) or "(none)"
        backend = backend_for(agent_type)
        if backend is None:
            raise BackendError(
                f"agent_type {agent_type!r} is not a known backend; SDK backends: [{registry}]; "
                f"team declared external_cli_agents: [{declared}]. Use one of the SDK backends."
            )
        if backend.kind != "sdk":
            raise BackendError(
                f"agent_type {agent_type!r} is an adapter backend, not an SDK backend; "
                f"swarmflow v1 supports SDK backends only; SDK backends: [{registry}]; "
                f"team declared external_cli_agents: [{declared}]."
            )
        if agent_type not in declared_names:
            hint = (
                "enable it in the team settings (external_cli_agents is empty)"
                if not declared_names
                else f"enable {agent_type} in the team settings or pick one of [{declared}]"
            )
            raise BackendError(
                f"agent_type {agent_type!r} is not declared by this team; SDK backends: [{registry}]; "
                f"team declared external_cli_agents: [{declared}]. To fix: {hint}."
            )
        return next(e for e in self._external_cli_agents if e.cli_agent == agent_type)

    def _gate_agent_template(self, opts: dict) -> Any | None:
        """Resolve ``opts["agent_template"]`` against the bound AgentGroup, or None.

        Raises a non-retryable ``BackendError`` for a prefixed name, an unknown
        member, a missing group, a clash with ``agent_type`` (the template owns
        the runtime), or an external-runtime member (not run by swarmflow yet).
        """
        name = opts.get("agent_template")
        if not name:
            return None
        if opts.get("agent_type"):
            raise BackendError(
                "agent_template and agent_type are mutually exclusive: "
                "the template decides the runtime; drop agent_type",
                retryable=False,
            )
        if not isinstance(name, str) or "/" in name:
            raise BackendError(
                f"agent_template {name!r} must be the bare member name of the bound AgentGroup "
                "(a string without a 'group/' prefix)",
                retryable=False,
            )
        entry = self._expert_specs.get(name)
        if entry is None:
            if name == "leader":
                # The Leader already drives the conversation that launched this
                # workflow; its persona is about delegating, not doing a node's work.
                raise BackendError(
                    "agent_template 'leader' is the Team Leader, not a worker; "
                    "pick a worker member of the bound AgentGroup",
                    retryable=False,
                )
            if self._expert_specs:
                hint = f"available members: {sorted(self._expert_specs)}"
            else:
                hint = "no AgentGroup is bound to this session; bind one (agent_group_name) before using agent_template"
            raise BackendError(
                f"agent_template {name!r} is not a member of the bound AgentGroup; {hint}",
                retryable=False,
            )
        if entry.is_external:
            raise BackendError(
                f"agent_template {name!r} declares an external runtime; "
                "swarmflow does not run external-runtime experts yet",
                retryable=False,
            )
        return entry

    def describe_agent_template(self, opts: dict) -> str | None:
        entry = self._expert_specs.get(opts.get("agent_template") or "")
        return entry.display_name if entry is not None else None

    def _external_cwd(self, member_name: str, entry: Any) -> str | None:
        """Return the working directory for an external CLI worker.

        Uses an active worktree path when present, falls back to the declared
        entry's ``cwd``, then the build context's project directory.
        """
        info = self._worktrees.get(member_name)
        if info is not None:
            return str(info.worktree_path)
        if getattr(entry, "cwd", None):
            return entry.cwd
        if self._build_context is not None:
            project_dir = getattr(self._build_context, "project_dir", None)
            if project_dir:
                return str(project_dir)
        return None

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
        budget_rail: SwarmflowBudgetRail | None = None,
        agent_id: str | None = None,
        phase: str | None = None,
        label: str | None = None,
        expert: Any = None,
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
            budget_rail: This call's token rail, mounted on the worker harness so
                the run's ceiling binds the worker's own loop. ``None`` (tests
                overriding this method) leaves the worker unmetered.
            expert: The resolved expert entry when the call named an
                ``agent_template``; its spec replaces the worker base and its
                persona is prepended to the engine worker prompt.

        Returns:
            The worker's final free-text output (only meaningful when no schema
            was requested). Structured output is read off the
            ``StructuredOutputTool`` instance by :meth:`run`, not from this value.
        """
        from openjiuwen.agent_teams.agent.team_agent import team_member_execution_subject
        from openjiuwen.agent_teams.harness.team_harness import TeamHarness
        from openjiuwen.agent_teams.workflow.backends._member_spec import (
            derive_member_build_context,
            derive_member_spec,
        )
        from openjiuwen.harness.execution_subject import execution_subject_scope

        # Checked before the expert substitution: even an expert-backed worker
        # draws its workspace from the base spec (_setup_worker_workspace).
        if self._worker_base_spec is None:
            raise BackendError("TeamWorkerBackend requires a worker_base_spec to build a worker harness")
        base_spec = expert.spec if expert is not None else self._worker_base_spec
        worker_key = "schema" if has_schema else "free"
        worker_text = self._t("swarmflow_worker", key=worker_key)
        # Persona first (who / rules), engine text last (single-shot delivery
        # protocol) — the engine text is kept verbatim so the structured_output
        # contract the backend checks afterwards is never weakened.
        system_prompt = f"{expert.persona}\n\n{worker_text}" if expert is not None and expert.persona else worker_text

        try:
            # Worker = teammate without team tools: per-call model (else inherit),
            # single-shot prompt, base tools + the per-call structured_output
            # instance. enable_task_loop / enable_task_planning ride along from
            # the base spec, so DeepAgent todo planning is preserved.
            worker_spec = derive_member_spec(
                base_spec,
                team_name=self._team_name,
                member_name=member_name,
                system_prompt=system_prompt,
                model=model,
                extra_tools=tools,
                description="swarmflow worker",
            )
            # Worker gets its own workspace, not the teammate's.
            worker_workspace, worker_cwd = self._setup_worker_workspace(member_name)
            worker_spec = worker_spec.model_copy(update={"workspace": worker_workspace, "cwd": worker_cwd})
            worker_spec = self._apply_worker_skill_visibility(worker_spec, member_name)
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
            if budget_rail is not None:
                harness.add_rail(budget_rail)
            kv_cache_harness_session_lifecycle_hook.configure_harness_session_hooks(
                harness,
                product_session_id=self._session_id,
                evict_on_finish=True,
            )
            if agent_id:
                harness.add_rail(
                    SwarmflowActivityRail(
                        emit=self._make_activity_emitter(agent_id, phase, label),
                    )
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
            from openjiuwen.core.session.agent_team import Session as TeamSession

            team_session = TeamSession(
                session_id=self._session_id,
                team_id=self._team_name,
                kv_cache_runtime=self._kv_cache_runtime,
            )
            # Run under the worker's own trajectory subject so its records group
            # into a lane of their own instead of the leader's (the engine runs
            # inside the leader's tool call and would otherwise inherit it).
            worker_subject = team_member_execution_subject(
                session_id=self._session_id or "",
                team_name=self._team_name,
                member_name=member_name,
                display_name=label or member_name,
            )
            with execution_subject_scope(worker_subject):
                result = await harness.run_once(user_prompt, team_session=team_session)
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
            finally:
                kv_cache_harness_session_lifecycle_hook.clear_harness_session_hooks(harness)
        return _text_from_invoke_result(result, member_name=member_name)

    async def _execute_external_worker(
        self,
        prompt: str,
        *,
        member_name: str,
        entry: Any,
        agent_type: str,
        opts: dict,
        schema_json: dict | None,
        budget_rail: SwarmflowBudgetRail,
        model_config: ExternalCliModelConfig | None = None,
    ) -> ExternalTurnOutcome:
        """Run one prompt on a fresh external CLI member and tear it down.

        Single-shot like a native worker: build, one turn, stop. Usage arrives
        with the terminal turn result and is billed after the fact.
        ``model_config`` is the already-resolved third-party model plan (see
        :meth:`run` — it plans before any resource is touched).
        Override in tests to record calls without launching a real CLI.
        """
        from openjiuwen.agent_teams.external.cli_agent.spawn import build_cli_workflow_runtime
        from openjiuwen.core.session.agent_team import Session as TeamSession

        try:
            runtime = await build_cli_workflow_runtime(
                cli_agent=agent_type,
                entry=entry,
                member_name=member_name,
                member_agent_id=f"{self._team_name}_{member_name}",
                session_id=self._session_id or "",
                team_name=self._team_name,
                system_prompt=self._t("swarmflow_worker", key="free"),
                cwd=self._external_cwd(member_name, entry),
                model_config=model_config,
            )
        except Exception as e:
            team_logger.exception("external worker build failed for %s", member_name)
            raise BackendError(f"external worker build failed for {member_name}: {e}") from e
        # Lazy: model_compat lives under agent_teams.external, whose package
        # __init__ re-enters this module mid-init (same reason plan_external_model
        # is deferred). The pool names back the auth-failure workaround list.
        from openjiuwen.agent_teams.external.cli_agent.model_compat import compatible_pool_model_names

        pool = self._team_backend.get_model_pool() if self._team_backend is not None else []
        # Mirrors the native rail's ``if agent_id:`` gate so third-party workers
        # emit AGENT_ACTIVITY events too; single-shot carries no correlation id.
        if opts.get("agent_id"):
            runtime.bind_activity_sink(
                self.progress_sink,
                agent_id=opts.get("agent_id"),
                phase=opts.get("phase"),
                label=opts.get("label"),
            )
        turns = ExternalMemberTurns(
            runtime,
            member_name=member_name,
            auth_hint_models=compatible_pool_model_names(agent_type, pool),
        )
        team_session = TeamSession(
            session_id=self._session_id,
            team_id=self._team_name,
            kv_cache_runtime=self._kv_cache_runtime,
        )
        try:
            await turns.start(team_session)
            outcome = await turns.run_turn(prompt, schema_json=schema_json)
        except BackendError as e:
            budget_rail.bill(e.tokens or 0)
            # Attribute the failed turn to the model the CLI reported, so
            # AGENT_FAILED names the real endpoint instead of the option hint.
            if e.model is None:
                e.model = getattr(turns, "reported_model", None)
            raise
        finally:
            try:
                await turns.stop()
            except Exception:
                team_logger.debug("external worker stop failed for %s", member_name, exc_info=True)
        budget_rail.bill(
            outcome.tokens,
            cache_tokens=outcome.cache_tokens or 0,
            input_tokens=outcome.input_tokens or 0,
            output_tokens=outcome.output_tokens or 0,
        )
        outcome.model = getattr(turns, "reported_model", None)
        return outcome

    # ------------------------------------------------------------------
    # Worker workspace setup
    # ------------------------------------------------------------------

    def _setup_worker_workspace(self, member_name: str) -> tuple[WorkspaceSpec, str | None]:
        """Compute, link, and mount the worker's independent workspace.

        Mirrors the layout used by ``agent_configurator`` for stable_base
        members: each worker gets its own workspace at
        the member link position ``workspaces/{member}_workspace`` (a junction
        to ``.agent_teams/<team>#<member>`` for dynamic workers, a real
        in-team dir for the leader). It lives under the team home, which
        ``agent_configurator`` already registers for team cleanup — so the
        worker workspace is removed with the team and needs no per-worker
        cleanup registration. Also mounts the team shared workspace into the
        worker's tree as an opt-in ``.team/{team_name}/`` symlink.

        The workspace is always the worker's own directory. With
        ``agent(options={"isolation": "worktree"})`` only the *cwd* moves into
        the owner-scoped worktree — otherwise the worker's artifacts and its
        Skill visibility declaration would live inside an ephemeral checkout and
        vanish with it.

        Returns:
            The worker's ``WorkspaceSpec`` and its cwd (the worktree path when
            isolated, else ``None`` meaning "use the workspace root").
        """
        worktree = self._worktrees.get(member_name)
        worker_cwd = worktree.worktree_path if worktree is not None else None
        ws_root = self._member_workspace_root(member_name)

        if self._worker_base_spec.workspace is not None:
            # Inherit language / stable_base from the base spec, only override root_path.
            worker_workspace = self._worker_base_spec.workspace.model_copy(
                update={"root_path": ws_root, "stable_base": True}
            )
        else:
            # Base spec has no workspace — create a fresh one for this worker.
            worker_workspace = WorkspaceSpec(
                root_path=ws_root,
                language=self._language,
                stable_base=True,
            )

        # Mount team workspace into worker workspace as an opt-in .team/
        # symlink — mirrors agent_configurator. Shared deliverables are
        # addressed by absolute path under the outputs directory; this
        # symlink is convenience navigation, not the access path.
        from openjiuwen.agent_teams.rails.team_context import get_workspace_manager

        workspace_manager = get_workspace_manager(self._build_context)
        if workspace_manager is not None:
            workspace_manager.mount_into_workspace(ws_root)

        return worker_workspace, worker_cwd

    def _apply_worker_skill_visibility(self, worker_spec: Any, member_name: str) -> Any:
        """Give the worker the team Skill rail instead of the generic one.

        A worker derives from the raw teammate spec, so it inherits that spec's
        ``skills`` list and would have the DeepAgent factory auto-add the
        generic ``SkillUseRail`` over its own workspace ``skills/`` node plus
        every mounted team directory. Workers own no Skill library either: they
        read the one shared library narrowed by their own visibility
        declaration, exactly like a teammate. The inherited ``skills`` names are
        not lost — they seed that declaration the first time it is written.

        Args:
            worker_spec: The derived worker ``DeepAgentSpec``.
            member_name: The minted worker identity.

        Returns:
            A spec copy with Skill discovery replaced by the team Skill rail.
        """
        from openjiuwen.agent_teams.skill.rail_spec import (
            build_team_skill_rail_spec,
            complete_declared_team_skill_rails,
        )

        team_workspace_path = self._team_workspace_path()
        # The base spec may already declare a bare team Skill rail (an embedder
        # blueprint owns the exposure mode but cannot know the minted worker
        # identity); complete it before deciding whether one has to be added.
        declared_rails = complete_declared_team_skill_rails(
            list(worker_spec.rails or []),
            team_name=self._team_name,
            member_name=member_name,
            config_skills=worker_spec.skills,
            team_workspace_path=team_workspace_path,
        )
        declared_rails = list(declared_rails)
        skill_rail_spec = build_team_skill_rail_spec(
            team_name=self._team_name,
            member_name=member_name,
            config_skills=worker_spec.skills,
            declared_rails=declared_rails,
            team_workspace_path=team_workspace_path,
        )
        if skill_rail_spec is not None:
            declared_rails.append(skill_rail_spec)
        return worker_spec.model_copy(
            update={
                "rails": declared_rails,
                "skills": [],
                "enable_skill_discovery": False,
            },
        )

    def _team_workspace_path(self) -> str | None:
        """Return the shared team workspace root, or None when there is none."""
        from openjiuwen.agent_teams.rails.team_context import get_workspace_manager

        workspace_manager = get_workspace_manager(self._build_context)
        if workspace_manager is None:
            return None
        return workspace_manager.workspace_path

    def _member_workspace_root(self, member_name: str) -> str:
        """Resolve the worker's workspace root.

        Returns the member link path (``workspaces/<member>_workspace`` — the
        legacy ``team_member_workspace_dir`` shape restored by 301, a junction
        to the flattened real directory for dynamic workers, the in-team real
        dir for the leader).
        """
        from openjiuwen.agent_teams.workspace_layout import ensure_team_member_workspace_link

        return ensure_team_member_workspace_link(self._team_name, member_name)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_activity_emitter(
        self, agent_id: str, phase: str | None, label: str | None
    ) -> Callable[[str, str | None, str | None, str | None], None]:
        """Return a sink that forwards one worker tool activity to the progress stream.

        The rail calls this on every tool execution (before and after); the
        event carries the node's ``agent_id`` / ``phase`` / ``label`` (mirroring
        ``agent_started``), the structured ``activity_type`` / ``tool_call_id`` /
        ``tool_name`` / ``content`` payload, and a short ``tool: <name>``
        narration in ``message`` for legacy consumers. ``content`` is truncated
        here — the one engine-side cap every consumer can rely on. The closure
        no-ops when the backend has no bound progress sink (e.g. tests).
        """
        sink = self.progress_sink

        def emit(
            activity_type: str,
            tool_call_id: str | None,
            tool_name: str | None,
            content: str | None,
        ) -> None:
            if sink is None:
                return
            try:
                sink(
                    build_activity_event(
                        activity_type=activity_type,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        content=content,
                        agent_id=agent_id,
                        phase=phase,
                        label=label,
                    )
                )
            except Exception:
                team_logger.debug("[swarmflow] worker activity emit failed: agent_id=%s", agent_id, exc_info=True)

        return emit

    def _next_member_name(self, opts: dict, call_key: str | None = None) -> str:
        """Mint a unique worker member name from the call label and run prefix.

        ``{run_prefix}-{label-slug}-{ident}`` (or ``wf-{label-slug}-{ident}``
        when no run id is set) — lowercase ASCII, the leading run/``wf-``
        prefix guarantees it starts with a letter (satisfies member-name
        routing/path constraints).

        ``ident`` is the hash of the engine's call-path key when available:
        the key is deterministic across replays of the same script (cache
        hits consume their key slot too), so a paused-and-resumed run mints
        the same name for the same call site and the worker's worktree slug —
        hence its kept-dirty worktree — is found again via the create
        fast-recovery path. The per-backend counter remains only for callers
        that invoke the backend without a call key (direct tests); it would
        drift across a resume because hit calls never reach the backend.
        """
        label = str(opts.get("label") or "worker")
        slug = _SLUG_RE.sub("-", label.lower()).strip("-") or "worker"
        if call_key:
            ident = hashlib.sha256(call_key.encode("utf-8")).hexdigest()[:12]
        else:
            ident = str(self._counter)
            self._counter += 1
        if self._run_prefix:
            return f"{self._run_prefix}-{slug}-{ident}"
        return f"wf-{slug}-{ident}"

    @staticmethod
    def _run_id_prefix(run_id: str | None) -> str | None:
        """Slug the full run id for worker member-name prefixing."""
        if not run_id:
            return None
        slug = _SLUG_RE.sub("-", run_id.lower()).strip("-")
        return slug or None


__all__ = ["TeamWorkerBackend"]
