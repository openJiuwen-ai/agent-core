# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The team integration backend: maps each engine ``agent()`` call onto a worker.

This is the seam where the business-agnostic swarmflow engine meets agent_teams.
For every ``agent(prompt, schema=...)`` the engine issues, the backend:

1. mints a unique ``WORKER`` member identity and (best-effort) opens a roster row;
2. builds a fresh, single-shot DeepAgent (``create_deep_agent``) — no coordination
   loop, no mailbox, no multi-turn — whose only non-default tool is a
   :class:`SubmitResultTool` carrying the requested JSON Schema;
3. runs that DeepAgent for one turn; the worker ends by calling ``submit_result``,
   whose captured arguments ARE the structured result (the harness has no native
   structured output, so the tool call is how the schema is enforced);
4. tears the worker down (status → SHUTDOWN) and returns an
   :class:`AgentResult`.

When the engine requested no schema, the worker is run without ``submit_result``
and its final free-text answer is returned. A worker that fails to submit a
structured result raises, which the engine's retry loop handles (and, after
exhaustion, surfaces as ``agent()`` returning ``None`` — a value every dw
control-flow helper already tolerates).

The actual DeepAgent turn lives in :meth:`_execute_worker` so tests can override
it without standing up a real LLM.
"""
from __future__ import annotations

import json
import re
from typing import Any, Sequence

from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.workflow.backends.submit_result_tool import SubmitResultTool
from openjiuwen.agent_teams.workflow.engine.backends.base import AgentBackend, AgentResult
from openjiuwen.agent_teams.workflow.engine.errors import BackendError
from openjiuwen.core.common.logging import team_logger

_SLUG_RE = re.compile(r"[^a-z0-9]+")

_SYS_PROMPT_SCHEMA = (
    "You are a single-shot swarmflow worker. Read the task in the user message, "
    "do the work, then call the `submit_result` tool EXACTLY ONCE with the "
    "structured result conforming to its input schema. Do NOT write the result "
    "as plain text — it is only captured through that tool call. After calling "
    "submit_result, stop."
)
_SYS_PROMPT_FREE = (
    "You are a single-shot swarmflow worker. Read the task in the user message, "
    "do the work, and return the answer as your final message."
)


class TeamWorkerBackend(AgentBackend):
    """Engine ``AgentBackend`` that executes each call as a single-shot worker.

    Args:
        model: The LLM ``Model`` each worker DeepAgent runs on (workers reuse the
            leader's model by default; see ``run_swarmflow``).
        team_backend: Optional ``TeamBackend`` used to open/close the worker's
            roster row. ``None`` runs workers without a DB identity (used in
            tests and headless dry-runs).
        team_name: Team name used to namespace worker member ids.
        language: Prompt language hint (reserved; worker prompts are English MVP).
        max_iterations: Hard cap on the worker DeepAgent's task-loop turns.
    """

    def __init__(
        self,
        *,
        model: Any,
        team_backend: Any = None,
        team_name: str = "swarmflow",
        language: str = "cn",
        max_iterations: int = 6,
    ) -> None:
        self._model = model
        self._team_backend = team_backend
        self._team_name = team_name
        self._language = language
        self._max_iterations = max_iterations
        self._counter = 0

    async def run(self, prompt: str, opts: dict, schema_json: dict | None) -> AgentResult:
        member_name = self._next_member_name(opts)
        await self._open_worker_row(member_name, opts)
        try:
            if schema_json is not None:
                submit_tool = SubmitResultTool(
                    schema_json,
                    tool_id=f"swarmflow.submit_result.{member_name}",
                )
                await self._execute_worker(prompt, [submit_tool], member_name=member_name, has_schema=True)
                if not (submit_tool.called and submit_tool.captured is not None):
                    raise BackendError(
                        f"worker '{member_name}' did not submit a structured result via submit_result"
                    )
                return AgentResult(
                    structured=submit_tool.captured,
                    tokens=self._estimate_tokens(prompt, submit_tool.captured),
                )
            text = await self._execute_worker(prompt, [], member_name=member_name, has_schema=False)
            return AgentResult(text=text, tokens=self._estimate_tokens(prompt, text))
        finally:
            await self._close_worker_row(member_name)

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
    ) -> str:
        """Build a single-shot DeepAgent and run it for one turn.

        Returns the worker's final free-text output (only meaningful when no
        schema was requested). Structured output is read off the
        ``SubmitResultTool`` instance by :meth:`run`, not from this return value.
        """
        from openjiuwen.core.runner.runner import Runner
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard
        from openjiuwen.harness.factory import create_deep_agent

        worker = create_deep_agent(
            model=self._model,
            card=AgentCard(
                id=f"{self._team_name}_{member_name}",
                name=member_name,
                description="swarmflow worker",
            ),
            system_prompt=_SYS_PROMPT_SCHEMA if has_schema else _SYS_PROMPT_FREE,
            tools=list(tools),
            enable_task_loop=True,
            max_iterations=self._max_iterations,
        )
        try:
            result = await Runner.run_agent(worker, {"query": prompt})
        finally:
            # Drop the per-call submit_result tool from the process-global
            # resource manager so concurrent / subsequent workers do not leak
            # or collide on it.
            for tool in tools:
                tool_id = getattr(getattr(tool, "card", None), "id", None)
                if tool_id:
                    try:
                        Runner.resource_mgr.remove_tool(tool_id)
                    except Exception:
                        team_logger.debug("worker tool cleanup failed for %s", tool_id)
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
