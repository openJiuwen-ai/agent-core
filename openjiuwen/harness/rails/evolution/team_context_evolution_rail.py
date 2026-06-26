# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Dual-layer context evolution rail for team members (Phase 2).

Manages a personal TaskMemoryService and a shared TaskMemoryService per
team member (specialist or leader).  Retrieval from both stores runs in
parallel; the merged block is injected as a single [MEMORY CONTEXT]
section.  After each task, every member — specialist or leader — writes
its own raw trajectory to its personal store and hands off the distilled
insight to an in-memory TeamInsightBuffer shared by the whole team. No
member ever writes the shared store directly — the team leader
(is_team_leader=True) additionally drains the buffer and is the shared
store's sole writer, via synthesize_team_summary.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.single_agent.rail import AgentCallbackContext
from openjiuwen.extensions.context_evolver.service import TaskMemoryService

from .context_evolution_rail import ContextEvolutionRail


# ---------------------------------------------------------------------------
# Internal data containers (in-memory only, never persisted)
# ---------------------------------------------------------------------------


@dataclass
class MergedMemoryItem:
    """One normalised memory entry produced during _merge_memories."""

    content: str
    source: str  # "personal" | "shared"
    namespace: str
    title: Optional[str] = None
    section: Optional[str] = None
    when_to_use: Optional[str] = None
    query: Optional[str] = None
    experience: Optional[List[str]] = None


@dataclass
class MergedRetrieveResult:
    """Output of _merge_memories — passed to the prompt formatter."""

    memory_string: str
    personal_items: List[MergedMemoryItem]
    shared_items: List[MergedMemoryItem]
    personal_namespace: str
    shared_namespace: str
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class TeamInsightEntry:
    """One specialist's distilled insight, tagged with provenance for the team leader.

    ``content`` is the actionable text (e.g. one ACE bullet produced while
    writing to the personal store); ``role`` / ``query`` / ``task_status``
    preserve who produced it, under what task, and with what outcome, so the
    team leader's synthesis prompt can attribute and group insights instead
    of seeing an anonymous flat list of strings.
    """

    role: str
    query: str
    content: str
    task_status: str


@dataclass
class TeamInsightBuffer:
    """In-memory hand-off of distilled specialist insights to the team leader.

    Shared by reference across every TeamContextEvolutionRail in a team
    (specialists + leader) — construct one instance and pass it to each
    rail's ``insight_buffer`` argument. Never persisted: if the process
    restarts before the leader drains it, pending insights are lost, but
    the personal store already holds each specialist's raw trajectory so
    nothing is unrecoverable.
    """

    insights: List[TeamInsightEntry] = field(default_factory=list)

    def add(self, entry: TeamInsightEntry) -> None:
        self.insights.append(entry)

    def drain(self) -> List[TeamInsightEntry]:
        items, self.insights = self.insights, []
        return items


# ---------------------------------------------------------------------------
# TeamContextEvolutionRail
# ---------------------------------------------------------------------------


class TeamContextEvolutionRail(ContextEvolutionRail):
    """Single rail that manages dual-layer (personal + shared) memory for one team member.

    Replaces the two-rail workaround from Phase 1 with a coordinated abstraction
    that merges personal and shared retrieve results (each algorithm's own
    retrieve() already decides how many/which entries to return — this rail
    does not rescore, deduplicate, or cap them) and applies the G-Memory
    distillation pattern before writing to the shared store.
    """

    priority: int = 50

    def __init__(
        self,
        team_id: str,
        agent_role: str,
        personal_service: Optional[TaskMemoryService] = None,
        shared_service: Optional[TaskMemoryService] = None,
        *,
        memory_dir: str = "./memories",
        personal_algo: str = "ace",
        shared_algo: str = "cognition",
        is_team_leader: bool = False,
        insight_buffer: Optional[TeamInsightBuffer] = None,
        inject_memories_in_context: bool = True,
        auto_summarize: bool = True,
    ) -> None:
        # Bypass ContextEvolutionRail.__init__ (single-store oriented) and
        # call DeepAgentRail.__init__ directly.
        super(ContextEvolutionRail, self).__init__()

        self._team_id = team_id
        self._agent_role = agent_role
        self._personal_ns = f"{team_id}:{agent_role}"
        self._shared_ns = team_id

        # Auto-create services when not provided.
        # Personal store:  ./memories/{team_id}/{role}.json
        # Shared store:    ./memories/{team_id}/shared.json
        if personal_service is None:
            personal_service = TaskMemoryService(
                persist_type="json",
                persist_path=f"{memory_dir}/{team_id}/{agent_role}.json",
                retrieval_algo=personal_algo,
                summary_algo=personal_algo,
            )
        if shared_service is None:
            shared_service = TaskMemoryService(
                persist_type="json",
                persist_path=f"{memory_dir}/{team_id}/shared.json",
                retrieval_algo=shared_algo,
                summary_algo=shared_algo,
            )

        self._personal_service = personal_service
        self._shared_service = shared_service
        self._is_team_leader = is_team_leader
        self._insight_buffer = insight_buffer
        self.inject_memories_in_context = inject_memories_in_context
        self.auto_summarize = auto_summarize

        # Per-iteration state (mirrors ContextEvolutionRail where needed for extract_trajectory)
        self._current_query: str = ""
        self._agent: Optional[Any] = None
        self.memories_used: int = 0
        self.original_prompt_template: Optional[List[Dict]] = None
        # task_status is not carried by TaskIterationInputs; callers may pre-set it
        # via set_task_status() before each Runner.run_agent() call.
        self._next_task_status: Optional[str] = None

        # Load persisted memories for both namespaces into their respective vector stores
        personal_service.load_memories(self._personal_ns)
        shared_service.load_memories(self._shared_ns)

        logger.info(
            "TeamContextEvolutionRail initialised for personal_ns=%s, shared_ns=%s",
            self._personal_ns,
            self._shared_ns,
        )

    def set_task_status(self, status: str) -> None:
        """Pre-configure the task_status used in the next after_task_iteration.

        The task_loop framework does not forward arbitrary runner-input keys
        into TaskIterationInputs, so callers must set this explicitly before
        each Runner.run_agent() call when they want G-Memory distillation to
        trigger on the shared store.

        Args:
            status: ``"resolved"`` | ``"failed"`` | ``"unknown"``
        """
        self._next_task_status = status

    # ------------------------------------------------------------------
    # BEFORE_TASK_ITERATION — parallel fetch + merge + inject
    # ------------------------------------------------------------------

    async def before_task_iteration(self, ctx: AgentCallbackContext) -> None:
        self.memories_used = 0
        self.original_prompt_template = None

        if self._agent is None:
            self._agent = ctx.agent

        query = getattr(ctx.inputs, "query", None) or ""
        if not query:
            return

        self._current_query = query

        # Fetch from both stores concurrently
        try:
            personal_result, shared_result = await asyncio.gather(
                self._personal_service.retrieve(user_id=self._personal_ns, query=query),
                self._shared_service.retrieve(user_id=self._shared_ns, query=query),
            )
        except Exception as exc:
            logger.error("TeamContextEvolutionRail: dual retrieval failed: %s", exc)
            return

        merge_result = self._merge_memories(personal_result, shared_result)
        self.memories_used = len(merge_result.personal_items) + len(merge_result.shared_items)

        if not merge_result.memory_string or not self.inject_memories_in_context:
            return

        # Inject merged block into agent system prompt
        agent = ctx.agent
        inner_agent = getattr(agent, "react_agent", agent)
        if not (
            inner_agent is not None
            and hasattr(inner_agent, "config")
            and hasattr(inner_agent.config, "prompt_template")
        ):
            logger.warning(
                "TeamContextEvolutionRail: agent has no config.prompt_template — skipping injection"
            )
            return

        self.original_prompt_template = [
            dict(msg) for msg in inner_agent.config.prompt_template
        ]

        memory_block = f"[MEMORY CONTEXT]\n\n{merge_result.memory_string}\n"
        new_template: List[Dict] = []
        for msg in inner_agent.config.prompt_template:
            if msg.get("role") == "system":
                new_template.append({
                    "role": "system",
                    "content": (msg.get("content", "") + f"\n\n{memory_block}").strip(),
                })
            else:
                new_template.append(dict(msg))

        inner_agent.config.prompt_template = new_template
        logger.debug(
            "TeamContextEvolutionRail: injected merged block (%d personal + %d shared items)",
            len(merge_result.personal_items),
            len(merge_result.shared_items),
        )

    # ------------------------------------------------------------------
    # AFTER_TASK_ITERATION — personal write always; shared write conditionally
    # ------------------------------------------------------------------

    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        agent = ctx.agent
        inner_agent = getattr(agent, "react_agent", agent)

        # Restore original prompt template
        if self.original_prompt_template is not None:
            if (
                inner_agent is not None
                and hasattr(inner_agent, "config")
                and hasattr(inner_agent.config, "prompt_template")
            ):
                inner_agent.config.prompt_template = self.original_prompt_template
            self.original_prompt_template = None

        # Attach memories_used to the result dict (mirrors ContextEvolutionRail behaviour)
        result = getattr(ctx.inputs, "result", None)
        if isinstance(result, dict):
            result["memories_used"] = self.memories_used

        if not (self.auto_summarize and self._current_query):
            return

        # task_status: prefer pre-configured value (set via set_task_status()) because
        # the task_loop does not propagate arbitrary runner-input keys into TaskIterationInputs.
        task_status: str = (
            self._next_task_status
            or getattr(ctx.inputs, "task_status", None)
            or "unknown"
        )
        self._next_task_status = None  # consume after use

        # Every team member — specialist or leader — writes its own trajectory
        # to its personal store and hands off the distilled insight to the
        # team's in-memory buffer the same way.
        await self._write_personal_and_handoff(ctx, task_status)

        # Team leader only: additionally drain the whole buffer (including the
        # entry it just added above) and synthesize it into the shared store.
        # The leader is the shared store's sole writer; specialists never write
        # to it directly. Guard here so specialist rails skip the call outright
        # instead of hitting (and logging) the is_team_leader check inside it.
        if self._is_team_leader:
            await self.synthesize_team_summary(task_status)

    async def _write_personal_and_handoff(self, ctx: AgentCallbackContext, task_status: str) -> None:
        """Write this rail's own trajectory to its personal store, then hand off
        the distilled insight (the personal store's own algorithm output) to the
        team's in-memory insight buffer.

        Used identically by specialists and the team leader — every team member's
        personal experience is captured the same way; only the leader additionally
        drains the buffer afterward (see after_task_iteration).
        """
        trajectory = self.extract_trajectory(ctx)
        if not trajectory:
            return

        personal_result: Optional[Dict[str, Any]] = None
        try:
            is_correct = True if task_status == "resolved" else (
                False if task_status == "failed" else None
            )
            # Pass both is_correct and score as single-element lists aligned with
            # trajectories=[trajectory]: Cognition's SolutionClassifyOp indexes
            # is_correct like a list (a bare bool crashes it), and the ReMe/RefCon/
            # DivCon preprocessor buckets trajectories via a numeric score list
            # (it ignores is_correct entirely, so without `score` every trajectory
            # is silently dropped into neither the success nor failure bucket).
            # ACE and ReasoningBank's flows don't read either kwarg, so this is safe
            # for them too.
            personal_result = await self._personal_service.summarize(
                user_id=self._personal_ns,
                matts="none",
                query=self._current_query,
                trajectories=[trajectory],
                is_correct=[is_correct],
                score=[1.0 if is_correct else 0.0],
            )
            logger.debug(
                "TeamContextEvolutionRail: personal store written (ns=%s)", self._personal_ns
            )
        except Exception as exc:
            logger.error(
                "TeamContextEvolutionRail: personal store write failed (ns=%s): %s",
                self._personal_ns,
                exc,
            )

        # Conditionally hand off the distilled insight to the team buffer.
        # Skip for task_status="unknown" — same quality gate as before.
        if task_status not in ("resolved", "failed"):
            logger.debug(
                "TeamContextEvolutionRail: task_status=%s — insight hand-off skipped", task_status
            )
            return

        if personal_result is None or self._insight_buffer is None:
            return

        # ReasoningBank wraps its items one level deeper (top-level entries expose
        # `.memory`/["memory"], a list of the actual items, instead of `.content`
        # directly) — flatten that one level so every algorithm's items end up in
        # the same flat list below.
        raw_memories = personal_result.get("memory", []) or []
        flat_items: List[Any] = []
        for entry in raw_memories:
            nested = entry.get("memory") if isinstance(entry, dict) else getattr(entry, "memory", None)
            if isinstance(nested, list):
                flat_items.extend(nested)
            else:
                flat_items.append(entry)

        added = 0
        for mem in flat_items:
            if isinstance(mem, dict):
                content = mem.get("content") or ""
                experience = mem.get("experience")
            else:
                content = getattr(mem, "content", "") or ""
                experience = getattr(mem, "experience", None)
            # Cognition memories carry `experience` (list of strings) instead of `content`.
            if not content and experience:
                content = "; ".join(str(e) for e in experience if e) if isinstance(experience, list) else str(
                    experience
                )
            if content:
                self._insight_buffer.add(
                    TeamInsightEntry(
                        role=self._agent_role,
                        query=self._current_query,
                        content=content,
                        task_status=task_status,
                    )
                )
                added += 1

        if added:
            logger.info(
                "TeamContextEvolutionRail: handed off %d insight(s) to team buffer (ns=%s, status=%s)",
                added,
                self._personal_ns,
                task_status,
            )

    async def synthesize_team_summary(self, task_status: str, query: Optional[str] = None) -> None:
        """Drain the in-memory insight buffer and write a consolidated synthesis to shared store.

        Only the team leader writes to the shared store; specialists hand off distilled
        insights to the shared TeamInsightBuffer instead. after_task_iteration() calls this
        automatically once per iteration on the leader's rail; orchestration code may also
        call it directly for setups where the leader rail is not attached to any agent's
        task loop (e.g. a flat team with no hierarchy).

        Args:
            task_status: ``"resolved"`` | ``"failed"`` | ``"unknown"``. Synthesis only
                runs for ``"resolved"`` or ``"failed"``.
            query: Query to attribute the synthesis to. Required for callers outside
                after_task_iteration, where ``self._current_query`` is not yet set;
                omit to reuse the rail's current query.
        """
        if not self._is_team_leader:
            logger.warning(
                "TeamContextEvolutionRail: synthesize_team_summary() called but is_team_leader=False (ns=%s)",
                self._shared_ns,
            )
            return
        if task_status not in ("resolved", "failed"):
            return
        if query is not None:
            self._current_query = query

        if self._insight_buffer is None:
            logger.debug(
                "TeamContextEvolutionRail: no insight buffer configured — nothing to synthesize (ns=%s)",
                self._shared_ns,
            )
            return

        try:
            entries = self._insight_buffer.drain()
            if not entries:
                logger.debug(
                    "TeamContextEvolutionRail: no pending specialist insights to synthesize (ns=%s)",
                    self._shared_ns,
                )
                return

            insight_lines = []
            for entry in entries:
                query_snippet = entry.query[:80] + ("..." if len(entry.query) > 80 else "")
                insight_lines.append(
                    f'- [{entry.role}] (status={entry.task_status}, task: "{query_snippet}") {entry.content}'
                )
            synthesis_text = (
                f"Team synthesis — consolidated from {len(insight_lines)} specialist contributions:\n"
                + "\n".join(insight_lines)
            )

            await self._shared_service.summarize(
                user_id=self._shared_ns,
                matts="none",
                query=self._current_query,
                trajectories=[synthesis_text],
                task_status=task_status,
            )
            roles = sorted({entry.role for entry in entries})
            logger.info(
                "TeamContextEvolutionRail: leader synthesized %d insight(s) from %d role(s) %s "
                "into shared store (ns=%s, status=%s)",
                len(entries),
                len(roles),
                roles,
                self._shared_ns,
                task_status,
            )
        except Exception as exc:
            logger.error(
                "TeamContextEvolutionRail: team leader synthesis failed (ns=%s): %s",
                self._shared_ns,
                exc,
            )

    # ------------------------------------------------------------------
    # _merge_memories
    # ------------------------------------------------------------------

    def _merge_memories(
        self,
        personal_result: Dict[str, Any],
        shared_result: Dict[str, Any],
    ) -> MergedRetrieveResult:
        """Normalise and merge both retrieve results.

        No scoring, deduplication, or count capping here — each algorithm's
        own retrieve() already decides how many entries to return (e.g. ACE
        loads its whole playbook by design, ReasoningBank/ReMe apply their
        own top_k). This just concatenates both pools as-is.
        """

        personal_raw = personal_result.get("retrieved_memory", [])
        shared_raw = shared_result.get("retrieved_memory", [])

        personal_items = [self._to_merged_item(mem, "personal", self._personal_ns) for mem in personal_raw]
        shared_items = [self._to_merged_item(mem, "shared", self._shared_ns) for mem in shared_raw]

        return MergedRetrieveResult(
            memory_string=self._format_block(personal_items, shared_items),
            personal_items=personal_items,
            shared_items=shared_items,
            personal_namespace=self._personal_ns,
            shared_namespace=self._shared_ns,
        )

    @staticmethod
    def _to_merged_item(
        mem: Any,
        source: str,
        namespace: str,
    ) -> MergedMemoryItem:
        """Convert one algorithm-specific retrieved item (dict or object) to MergedMemoryItem."""
        if isinstance(mem, dict):
            content = mem.get("content") or ""
            title = mem.get("title")
            section = mem.get("section")
            when_to_use = mem.get("when_to_use")
            query = mem.get("query")
            experience = mem.get("experience")
        else:
            content = getattr(mem, "content", "") or ""
            title = getattr(mem, "title", None)
            section = getattr(mem, "section", None)
            when_to_use = getattr(mem, "when_to_use", None)
            query = getattr(mem, "query", None)
            experience = getattr(mem, "experience", None)

        # Cognition stores knowledge in experience list; surface it as content
        if not content and experience:
            if isinstance(experience, list):
                content = "; ".join(str(e) for e in experience if e)
            else:
                content = str(experience)

        return MergedMemoryItem(
            content=content or "",
            source=source,
            namespace=namespace,
            title=title,
            section=section,
            when_to_use=when_to_use,
            query=query,
            experience=experience if isinstance(experience, list) else None,
        )

    def _format_block(
        self,
        personal_items: List[MergedMemoryItem],
        shared_items: List[MergedMemoryItem],
    ) -> str:
        lines: List[str] = []

        if personal_items:
            lines.append(f"Personal Experience [{self._agent_role}]:")
            for item in personal_items:
                lines.append(f"  • {item.content}")
            lines.append("")

        if shared_items:
            lines.append("Team Insights:")
            for item in shared_items:
                lines.append(f"  • {item.content}")
            lines.append("")

        return "\n".join(lines).rstrip()

__all__ = [
    "TeamContextEvolutionRail",
    "MergedMemoryItem",
    "MergedRetrieveResult",
    "TeamInsightBuffer",
    "TeamInsightEntry",
]
