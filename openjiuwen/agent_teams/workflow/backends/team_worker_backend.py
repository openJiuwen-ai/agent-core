# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The team integration backend: maps each engine ``agent()`` call onto a worker.

This is the seam where the business-agnostic swarmflow engine meets agent_teams.
For every ``agent(prompt, schema=...)`` the engine issues, the backend:

1. mints a unique ``WORKER`` member identity and (best-effort) opens a roster row;
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
4. tears the worker down (status → SHUTDOWN) and returns an :class:`AgentResult`.

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
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.workflow.backends.structured_output_tool import StructuredOutputTool
from openjiuwen.agent_teams.workflow.engine.backends.base import AgentBackend, AgentResult
from openjiuwen.agent_teams.workflow.engine.errors import BackendError
from openjiuwen.core.common.logging import team_logger

_SLUG_RE = re.compile(r"[^a-z0-9]+")

_SYS_PROMPT_SCHEMA = (
    "You are a single-shot swarmflow worker. Read the task in the user message, "
    "do the work, then call the `structured_output` tool EXACTLY ONCE with the "
    "structured result conforming to its input schema. Do NOT write the result "
    "as plain text — it is only captured through that tool call. After calling "
    "structured_output, stop."
)
_SYS_PROMPT_FREE = (
    "You are a single-shot swarmflow worker. Read the task in the user message, "
    "do the work, and return the answer as your final message."
)


class TeamWorkerBackend(AgentBackend):
    """Engine ``AgentBackend`` that executes each call as a single-shot worker.

    Args:
        model: Legacy default ``Model`` (kept for construction compatibility;
            the harness path resolves a worker's model from its spec / the
            per-call config resolver instead).
        team_backend: Optional ``TeamBackend`` used to open/close the worker's
            roster row. ``None`` runs workers without a DB identity (used in
            tests and headless dry-runs).
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
        team_backend: Any = None,
        team_name: str = "swarmflow",
        language: str = "cn",
        max_iterations: int = 6,
        model_resolver: Callable[[str], Any] | None = None,
        worker_base_spec: Any = None,
        build_context: Any = None,
    ) -> None:
        self._model = model
        self._team_backend = team_backend
        self._team_name = team_name
        self._language = language
        self._max_iterations = max_iterations
        self._model_resolver = model_resolver
        self._worker_base_spec = worker_base_spec
        self._build_context = build_context
        self._t = make_translator(language if language in ("cn", "en") else "cn")
        self._counter = 0

    async def run(self, prompt: str, opts: dict, schema_json: dict | None) -> AgentResult:
        member_name = self._next_member_name(opts)
        model = self._resolve_model(opts.get("model"))
        await self._open_worker_row(member_name, opts)
        try:
            if schema_json is not None:
                # The harness's ability manager re-qualifies the tool id per owner
                # (``structured_output_{worker_owner_id}``), so concurrent workers
                # never collide and no per-call id is needed here.
                submit_tool = StructuredOutputTool(schema_json, self._t)
                await self._execute_worker(
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
            await self._close_worker_row(member_name)

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
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard

        if self._worker_base_spec is None:
            raise BackendError(
                "TeamWorkerBackend requires a worker_base_spec to build a worker harness"
            )

        try:
            worker_spec = self._worker_base_spec.model_copy(
                update={
                    "card": AgentCard(
                        id=f"{self._team_name}_{member_name}",
                        name=member_name,
                        description="swarmflow worker",
                    ),
                    # Per-call model config when resolved, else inherit teammate's.
                    "model": model or self._worker_base_spec.model,
                    "system_prompt": _SYS_PROMPT_SCHEMA if has_schema else _SYS_PROMPT_FREE,
                    # Append the per-call structured_output instance; the base spec
                    # already carries teammate tools/skills. enable_task_loop /
                    # enable_task_planning are inherited (DeepAgent todo planning kept).
                    "tools": list(self._worker_base_spec.tools or []) + list(tools),
                }
            )
            worker_build_context = None
            if self._build_context is not None:
                worker_build_context = self._build_context.derive(
                    member_name=member_name,
                    role=TeamRole.WORKER.value,
                    member_card_id=f"{self._team_name}_{member_name}",
                    language=self._language,
                )
                worker_build_context.extras = dict(worker_build_context.extras)
            harness = TeamHarness.build(
                agent_spec=worker_spec,
                role=TeamRole.WORKER,
                member_name=member_name,
                build_context=worker_build_context,
            )
        except Exception as e:
            team_logger.exception("worker harness build failed for %s", member_name)
            raise BackendError(f"worker harness build failed for {member_name}: {e}") from e

        try:
            result = await harness.run_once(prompt)
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
    # Worker roster identity (best-effort)
    # ------------------------------------------------------------------

    async def _open_worker_row(self, member_name: str, opts: dict) -> None:
        """Open a WORKER roster row for visibility. Best-effort; never fatal."""
        if self._team_backend is None:
            return
        from openjiuwen.agent_teams.schema.status import ExecutionStatus, MemberMode, MemberStatus
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard

        desc = str(opts.get("label") or "swarmflow worker")
        try:
            card = AgentCard(
                id=f"{self._team_backend.team_name}_{member_name}",
                name=member_name,
                description=desc,
            )
            await self._team_backend.spawn_member(
                member_name=member_name,
                display_name=member_name,
                agent_card=card,
                desc=desc,
                status=MemberStatus.BUSY,
                execution_status=ExecutionStatus.RUNNING,
                mode=MemberMode.BUILD_MODE,
                role=TeamRole.WORKER,
            )
        except Exception as exc:
            team_logger.debug("worker row open failed for %s: %s", member_name, exc)

    async def _close_worker_row(self, member_name: str) -> None:
        """Mark the worker row SHUTDOWN once its single turn is done. Best-effort."""
        if self._team_backend is None:
            return
        from openjiuwen.agent_teams.schema.status import MemberStatus

        try:
            await self._team_backend.db.member.update_member_status(
                member_name,
                self._team_backend.team_name,
                MemberStatus.SHUTDOWN.value,
            )
        except Exception as exc:
            team_logger.debug("worker row close failed for %s: %s", member_name, exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _next_member_name(self, opts: dict) -> str:
        """Mint a unique, pattern-valid worker member name from the call label.

        ``wf-<label-slug>-<n>`` — lowercase ASCII, leading letter (the ``wf-``
        prefix guarantees it), so it satisfies the member-name routing/path
        constraints. ``n`` is a per-backend counter; the synchronous
        read-increment between awaits keeps it collision-free under the
        engine's concurrent fan-out.
        """
        n = self._counter
        self._counter += 1
        label = str(opts.get("label") or "worker")
        slug = _SLUG_RE.sub("-", label.lower()).strip("-") or "worker"
        return f"wf-{slug}-{n}"

    @staticmethod
    def _estimate_tokens(prompt: str, result: Any) -> int:
        try:
            payload = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        except Exception:
            payload = str(result)
        return len(prompt) // 4 + len(payload) // 4


__all__ = ["TeamWorkerBackend"]
