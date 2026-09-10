# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TTSERail: Two-Track Self-Evolution as a native jiuwen rail.

Subclasses :class:`EvolutionRail` so FACT/TIP dual-track induction and
injection run on the shared trajectory-collection machinery without coupling
to the skill-body track:

  * **Track 1 (FACT)** - declarative environment facts.
  * **Track 2 (meta-TIP)** - capability selection (which skill/tool to use and
    when) plus procedures for tasks that used no skill.

Injection happens in ``before_model_call``. The frozen TTSE algorithm lives
in :mod:`prompts` / :mod:`induction`; only the I/O layer (trajectory source,
capability enumeration, persistence, prompt section) is rewired to jiuwen
async primitives.
"""

from __future__ import annotations

import asyncio
import json
import os
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.memory.lite.embeddings import EmbeddingProvider
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, RunKind
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentKind
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.evolution.evolution_rail import (
    EvolutionRail,
    PreparedEvolutionInput,
)

from .capabilities import list_capability_names, parse_capability_names_from_text, render_capabilities
from .catalog import project_catalog, render_catalog_markdown
from .classify import classify_rules
from .config import TTSEConfig
from .consult import TTSE_CONSULT_TOOL_NAME, create_ttse_consult_tools
from .dream import load_dream_state, run_dream_pass
from .induction import blame, induce, induce_batch, synthesize
from .render import (
    DISK_CATALOG_GUIDANCE_CN,
    DISK_CATALOG_GUIDANCE_EN,
    rules_numbered,
)
from .stores import shared_store
from .success import SignalBasedSuccessDetector, SuccessDetector
from .trajectory_adapter import count_tool_calls, messages_to_trajectory_text

_TTSE_CATALOG_SECTION = "ttse_catalog"
_TTSE_CATALOG_PRIORITY = 200


@dataclass(frozen=True)
class _TTSEPreparedEvolutionInput(PreparedEvolutionInput):
    """Detached TTSE evolution input with rail-local induction state."""

    ttse_capabilities: str = ""
    ttse_task_query: str = ""
    ttse_invoke_tool_calls: int = 0


class TTSERail(EvolutionRail):
    """``EvolutionRail`` + FACT/TIP dual-track induction and injection."""

    def __init__(
        self,
        *,
        llm: Model,
        model: str,
        ttse_config: Optional[TTSEConfig] = None,
        embedding: Optional[EmbeddingProvider] = None,
        success_detector: Optional[SuccessDetector] = None,
        **kwargs: Any,
    ) -> None:
        self._ttse_llm = llm
        self._ttse_model = model
        self._ttse_config = ttse_config or TTSEConfig()
        resolved = embedding if embedding is not None else self._ttse_config.embedding
        self._ttse_config.embedding = resolved
        self._ttse_store = shared_store(self._ttse_config, embedding=resolved)
        # Pluggable success detector gates the blame/synthesize pass.
        self._success_detector = success_detector or SignalBasedSuccessDetector(
            llm=llm,
            model=model,
            config=self._ttse_config,
        )
        # Serializes the whole bank-mutating reflection (blame/retire/synth/induce)
        # and Auto-dream so concurrent background jobs don't interleave.
        # Lock lives on the shared store so two sessions cannot wipe each other.
        self._evolution_lock = self._ttse_store.evolution_lock
        # Batch induce buffer: when batch_size > 1, per-task observations collect
        # here and induce as ONE call every batch_size tasks (cost amortization).
        # All access is inside _evolution_lock, so it stays race-free.
        self._batch_buffer: list[dict] = []
        # Last capability list seen during induction; reused by flush() which
        # has no ctx/agent to introspect.
        self._last_capabilities: Optional[str] = None
        self._agent: Any = None
        self._consult_tools: list = []
        self._attachment_manager: Any = None
        # Auto-dream: count non-follow-up task iterations between silent runs.
        self._dream_non_followup_count: int = 0
        self._dream_task: Optional[asyncio.Task] = None
        super().__init__(**kwargs)

    def init(self, agent) -> None:
        super().init(agent)
        self._agent = agent
        self._attachment_manager = getattr(agent, "prompt_attachment_manager", None)
        self._sync_consult_tool(agent)

    def uninit(self, agent) -> None:
        self._drop_consult_tool(agent)
        self._consult_tools = []
        self._agent = None
        self._attachment_manager = None
        super().uninit(agent)

    def update_llm(self, llm: Model, model: str) -> None:
        """Hot-update induction and success-detection LLM clients."""
        self._ttse_llm = llm
        self._ttse_model = model
        detector = self._success_detector
        update = getattr(detector, "update_llm", None)
        if callable(update):
            update(llm, model)

    def apply_runtime_config(
        self,
        *,
        store_path: str,
        evolve_enabled: bool,
        inject_enabled: bool,
    ) -> None:
        """Update live store path and evolve/inject flags without remounting."""
        cfg = self._ttse_config
        old_path = str(getattr(cfg, "store_path", "") or "")
        cfg.store_path = store_path
        cfg.evolve_enabled = evolve_enabled
        cfg.inject_enabled = inject_enabled
        if old_path != store_path:
            self._ttse_store.reload()
        self.sync_inject_mode()

    def sync_inject_mode(self) -> None:
        """Register or drop ``ttse_consult`` after a live ``inject_enabled`` change."""
        if self._agent is not None:
            self._sync_consult_tool(self._agent)

    def _sync_consult_tool(self, agent) -> None:
        want = bool(self._ttse_config.inject_enabled)
        have = bool(self._consult_tools)
        if want and not have:
            tools = create_ttse_consult_tools(
                self._ttse_store,
                max_chars=int(getattr(self._ttse_config, "consult_max_chars", 8000) or 8000),
                max_rules=int(getattr(self._ttse_config, "consult_max_rules", 40) or 40),
                default_top_k=int(getattr(self._ttse_config, "consult_top_k", 8) or 8),
                rrf_k=int(getattr(self._ttse_config, "consult_rrf_k", 60) or 60),
            )
            self._register_runtime_tools(agent, tools)
            self._consult_tools = tools
            logger.info("[TTSERail] registered %s", TTSE_CONSULT_TOOL_NAME)
        elif have and not want:
            self._drop_consult_tool(agent)

    def _drop_consult_tool(self, agent) -> None:
        if not self._consult_tools:
            return
        self._unregister_runtime_tools(agent, self._consult_tools)
        self._consult_tools = []
        logger.info("[TTSERail] unregistered %s", TTSE_CONSULT_TOOL_NAME)

    # ------------------------------------------------------------------
    # Evolution: FACT/meta-TIP induction
    # ------------------------------------------------------------------

    def _allow_evolution_trigger(self, trigger_point, ctx: AgentCallbackContext) -> bool:
        """Trigger every invoke: TTSE induces from EVERY task.

        Skip heartbeat/cron background runs so those do not grow the bank.
        """
        if not self._ttse_config.evolve_enabled:
            return False
        if ctx is not None and self._is_background_run(ctx):
            return False
        return True

    @staticmethod
    def _is_background_run(ctx: AgentCallbackContext) -> bool:
        inputs = getattr(ctx, "inputs", None)
        for method_name in ("is_heartbeat", "is_cron"):
            method = getattr(inputs, method_name, None)
            if callable(method) and method():
                return True

        run_kind = getattr(inputs, "run_kind", None)
        if run_kind is None:
            run_kind = getattr(ctx, "extra", {}).get("run_kind") if ctx is not None else None
        if run_kind in (RunKind.HEARTBEAT, RunKind.CRON):
            return True
        if isinstance(run_kind, str) and run_kind in {RunKind.HEARTBEAT.value, RunKind.CRON.value}:
            return True

        conversation_id = getattr(inputs, "conversation_id", None)
        return isinstance(conversation_id, str) and conversation_id.startswith(("heartbeat", "cron"))

    async def _prepare_evolution_input(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
    ) -> Optional[_TTSEPreparedEvolutionInput]:
        """Capture messages and TTSE-local fields while ctx is alive."""
        if not self._ttse_config.evolve_enabled:
            return None

        prepared = await super()._prepare_evolution_input(trajectory, ctx)
        if prepared is None:
            return None

        messages = list(prepared.messages)
        capabilities = ""
        agent = getattr(ctx, "agent", None)
        try:
            capabilities = await render_capabilities(agent)
        except Exception as exc:  # noqa: BLE001 - never block prepare
            logger.warning("[TTSERail] capability enumeration failed: %s", exc)

        task_query = self._extract_query(ctx)
        return _TTSEPreparedEvolutionInput(
            trajectory=prepared.trajectory,
            messages=tuple(deepcopy(messages)),
            skill_name="ttse",
            ttse_capabilities=capabilities,
            ttse_task_query=task_query,
            ttse_invoke_tool_calls=count_tool_calls(messages),
        )

    @staticmethod
    def _snapshot_from_prepared(prepared: _TTSEPreparedEvolutionInput) -> dict[str, Any]:
        """Build the dict shape expected by success detectors / induction."""
        return {
            "messages": [deepcopy(message) for message in prepared.messages],
            "ttse_capabilities": prepared.ttse_capabilities,
            "ttse_task_query": prepared.ttse_task_query,
            "ttse_invoke_tool_calls": prepared.ttse_invoke_tool_calls,
        }

    async def run_evolution(self, prepared: _TTSEPreparedEvolutionInput) -> None:
        """Run TTSE induction from one detached, immutable input."""
        if not self._ttse_config.evolve_enabled:
            logger.debug("[TTSERail] run_evolution skipped: evolve_enabled=False")
            return
        if not isinstance(prepared, PreparedEvolutionInput):
            raise TypeError("prepared must be a PreparedEvolutionInput")
        if prepared.trajectory is None:
            logger.debug("[TTSERail] run_evolution skipped: trajectory is None")
            return
        logger.info("[TTSERail] run_evolution started")
        try:
            snapshot = (
                self._snapshot_from_prepared(prepared)
                if isinstance(prepared, _TTSEPreparedEvolutionInput)
                else {
                    "messages": [deepcopy(message) for message in prepared.messages],
                }
            )
            await self._run_ttse_induction(
                prepared.trajectory,
                ctx=None,
                snapshot=snapshot,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[TTSERail] induction failed: %s", exc)

    async def _run_ttse_induction(
        self,
        trajectory,
        ctx: Optional[AgentCallbackContext] = None,
        *,
        snapshot: Optional[dict] = None,
    ) -> None:
        snapshot = snapshot or {}
        messages = snapshot.get("messages")
        if messages is None:
            messages = self._trajectory_to_messages(trajectory)

        capabilities = snapshot.get("ttse_capabilities")
        if capabilities is None:
            capabilities = await render_capabilities(getattr(ctx, "agent", None))
        # Cache so flush() (which has no ctx) can reuse the real capability list.
        self._last_capabilities = capabilities

        task_query = snapshot.get("ttse_task_query") or self._extract_query(ctx)
        if not task_query:
            task_query = self._last_user_text(messages)

        traj_text = messages_to_trajectory_text(messages, budget=self._ttse_config.traj_char_budget)
        if not traj_text:
            logger.debug("[TTSERail] induction skipped: empty trajectory text")
            return
        logger.info(
            "[TTSERail] starting induction query=%s batch_size=%s has_capabilities=%s",
            (task_query or "")[:80],
            self._ttse_config.batch_size,
            bool(capabilities),
        )

        # Inject per-dimension grader scores at the head of the trajectory so
        # induce sees WHERE the task was strong/weak, not just the pass/fail
        # outcome label.
        _dim_scores = snapshot.get("ttse_dim_scores")
        _overall = snapshot.get("ttse_score")
        if isinstance(_dim_scores, dict) and _dim_scores:
            _parts = [f"{k}={float(v):.2f}" for k, v in _dim_scores.items() if v is not None]
            if _overall is not None:
                _parts.append(f"overall={float(_overall):.2f}")
            if _parts:
                traj_text = (
                    "[GRADER SCORES 0-1 per dimension, lower = weaker] "
                    + " ".join(_parts)
                    + " — account for BOTH high dimensions (what worked) and low "
                    "dimensions (what was weak / should improve) when extracting rules." + "\n" + traj_text
                )

        # Whole-section lock: the reflection reads the bank (dedup inputs in
        # ``induce``/``induce_batch``, snapshots in ``_blame_and_retire``) and
        # then mutates it (retire/add). In async-evolution mode a fresh
        # background reflection is spawned per invoke, so two could interleave
        # and clobber each other's dedup view or retire. Serialize the whole
        # read-modify-write so one reflection's bank view is stable end to end.
        # Detect runs outside the lock: Judge LLM must not hold the bank lock.
        result = await self._success_detector.detect(trajectory, messages, ctx=ctx, snapshot=snapshot)
        outcome = result.outcome
        logger.info(
            "[TTSERail] success detect outcome=%s reason=%s score=%s",
            outcome,
            result.reason,
            result.score,
        )
        if outcome == "skip":
            logger.info("[TTSERail] induction skipped: detect outcome=skip (%s)", result.reason)
            return

        async with self._evolution_lock:
            self._ttse_store.reload_if_disk_newer()
            if self._ttse_config.batch_size <= 1:
                # Per-task mode (reference ``learn``): induce on EVERY task.
                # success -> tactics; fail -> blame/retire/synthesize -> induce.
                # partial induces without blame.
                if outcome == "fail":
                    await self._blame_and_resolve(task_query, traj_text, capabilities)
                facts, tips = await induce(
                    llm=self._ttse_llm,
                    model=self._ttse_model,
                    policy=self._ttse_config.induce_llm_policy,
                    task_prompt=task_query,
                    traj_text=traj_text,
                    capabilities=capabilities,
                    existing_facts=self._ttse_store.facts_texts(),
                    existing_tips=self._ttse_store.tips_texts(),
                    outcome=outcome,
                )
                added = await self._add_rules(facts, tips)
                if added:
                    logger.info(
                        "[TTSERail] induced %s new rule(s) (outcome=%s); bank stats=%s",
                        added,
                        outcome,
                        self._ttse_store.stats(),
                    )
                await self._maybe_project_catalog()
                return

            # Batch mode (reference ``learn_batch``): buffer this task. blame/retire
            # still run per failed task (concentrated here); synthesize + induce
            # run ONCE per batch -> N tasks amortize to a single induce LLM call.
            self._batch_buffer.append(
                {
                    "task_id": snapshot.get("task_id") or "",
                    "task_prompt": task_query,
                    "traj_text": (
                        traj_text
                        if not self._ttse_config.batch_traj_budget or self._ttse_config.batch_traj_budget <= 0
                        else traj_text[: self._ttse_config.batch_traj_budget]
                    ),
                    "outcome": outcome,
                }
            )
            if outcome == "fail":
                await self._blame_and_retire(task_query, traj_text)
            if len(self._batch_buffer) >= self._ttse_config.batch_size:
                await self._flush_batch(capabilities)

    async def _blame_and_retire(self, task_query: str, traj_text: str) -> None:
        """Fail path step 1: blame -> retire (no synthesize).

        Shared by the per-task and batch paths so blame/retire can run per
        failed task while synthesize is deferred to once-per-batch.
        """
        flat = self._ttse_store.snapshot_flat()
        if not flat:
            logger.info("[TTSERail] blame skipped: bank is empty")
            return
        logger.info("[TTSERail] blaming %s rule(s)", len(flat))
        numbered = rules_numbered(flat)
        idx, reason = await blame(
            llm=self._ttse_llm,
            model=self._ttse_model,
            policy=self._ttse_config.induce_llm_policy,
            task_prompt=task_query,
            traj_text=traj_text,
            rules_numbered=numbered,
            n_rules=len(flat),
        )
        if idx is not None and 1 <= idx <= len(flat):
            text, rtype = flat[idx - 1]
            removed = await self._ttse_store.retire(text, rtype, reason, task_id="")
            if removed:
                logger.info(
                    "[TTSERail] retired %s #%d (%s): %s",
                    rtype,
                    idx,
                    reason[:60],
                    text[:60],
                )
            await self._maybe_project_catalog()

    async def _synthesize_resolving(self, capabilities: str) -> None:
        """Fail path step 2: propose one resolving TIP when >= 2 rules remain.

        synthesize runs against the bank AFTER retire so a retired bad rule does
        not seed a contradiction. Below 2 rules there is nothing to contradict.
        """
        flat_after = self._ttse_store.snapshot_flat()
        if len(flat_after) < 2:
            return
        new_tip = await synthesize(
            llm=self._ttse_llm,
            model=self._ttse_model,
            policy=self._ttse_config.induce_llm_policy,
            rules_numbered=rules_numbered(flat_after),
            capabilities=capabilities,
        )
        if new_tip and await self._ttse_store.add_tip(new_tip):
            logger.info("[TTSERail] synthesized resolving TIP: %s", new_tip[:80])
            await self._classify_added_rules([(new_tip, "tip")])

    async def _blame_and_resolve(self, task_query: str, traj_text: str, capabilities: str) -> None:
        """Per-task fail path: blame -> retire -> synthesize (before induce)."""
        logger.info("[TTSERail] starting blame and resolve query=%s", (task_query or "")[:80])
        await self._blame_and_retire(task_query, traj_text)
        await self._synthesize_resolving(capabilities)

    async def _add_rules(self, facts: List[str], tips: List[str]) -> int:
        added_items: list[tuple[str, str]] = []
        added = 0
        for fact in facts:
            if await self._ttse_store.add_fact(fact):
                added += 1
                added_items.append((fact, "fact"))
        for tip in tips:
            if await self._ttse_store.add_tip(tip):
                added += 1
                added_items.append((tip, "tip"))
        if added_items:
            await self._classify_added_rules(added_items)
        return added

    async def _classify_added_rules(self, items: list[tuple[str, str]]) -> None:
        """Assignment pass after bank write. Does not change induce."""
        try:
            assignments = await classify_rules(
                llm=self._ttse_llm,
                model=self._ttse_model,
                policy=self._ttse_config.induce_llm_policy,
                items=items,
            )
            patched = await self._ttse_store.set_categories(assignments)
            logger.info("[TTSERail] wrote category on %s new rule(s)", patched)
        except Exception as exc:  # noqa: BLE001 - never roll back bank writes
            logger.warning("[TTSERail] category assignment skipped: %s", exc)

    def _record_needs_category(self, text: str, rtype: str) -> bool:
        """True when the bank rule still lacks an explicit category field."""
        store = self._ttse_store.facts if rtype == "fact" else self._ttse_store.tips
        for record in store:
            if record.get("text") == text:
                return "category" not in record
        return True

    async def _maybe_project_catalog(self) -> None:
        try:
            await asyncio.to_thread(project_catalog, self._ttse_store)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[TTSERail] catalog projection skipped: %s", exc)

    async def _flush_batch(self, capabilities: str) -> None:
        """Batch flush: synthesize once + ONE induce_batch over buffered tasks."""
        if not self._batch_buffer:
            return
        await self._synthesize_resolving(capabilities)
        group = list(self._batch_buffer)
        self._batch_buffer.clear()
        facts, tips = await induce_batch(
            llm=self._ttse_llm,
            model=self._ttse_model,
            policy=self._ttse_config.induce_llm_policy,
            group=group,
            capabilities=capabilities,
            existing_facts=self._ttse_store.facts_texts(),
            existing_tips=self._ttse_store.tips_texts(),
        )
        added = await self._add_rules(facts, tips)
        if added:
            logger.info(
                "[TTSERail] batch-induced %s new rule(s) over %s task(s); bank stats=%s",
                added,
                len(group),
                self._ttse_store.stats(),
            )
        await self._maybe_project_catalog()

    async def flush(self) -> None:
        """Force-induce any buffered (partial) batch.

        With ``batch_size > 1`` a reflection only induces when the buffer fills.
        Call this at a task-group boundary (or shutdown) so a trailing partial
        batch is not lost. No-op in per-task mode or when the buffer is empty.
        Acquires the evolution lock so it cannot interleave with a reflection.
        """
        if not self._batch_buffer:
            return
        async with self._evolution_lock:
            self._ttse_store.reload_if_disk_newer()
            if not self._batch_buffer:
                return
            await self._flush_batch(self._last_capabilities or await render_capabilities(None))

    # ------------------------------------------------------------------
    # Auto-dream (silent bank hygiene)
    # ------------------------------------------------------------------

    async def _on_after_task_iteration(
        self,
        ctx: AgentCallbackContext,
        trajectory: Trajectory | None,
    ) -> None:
        """Count non-follow-up iterations and schedule a silent dream run."""
        del trajectory  # Dream scheduling is iteration-count based, not traj-based.
        if not self._ttse_config.dream_enabled:
            return
        if self._dream_iteration_blocked(ctx):
            return
        self._dream_non_followup_count += 1
        interval = max(1, int(self._ttse_config.dream_interval))
        logger.info(
            "[TTSERail] dream session count=%s/%s session_id=%s",
            self._dream_non_followup_count,
            interval,
            self._catalog_session_id(ctx),
        )
        if self._dream_non_followup_count < interval:
            return
        self._dream_non_followup_count = 0
        logger.info("[TTSERail] dream interval reached; scheduling offline dream")
        self._schedule_dream(ctx)

    def _dream_iteration_blocked(self, ctx: AgentCallbackContext) -> bool:
        if self._is_background_run(ctx):
            return True
        inputs = getattr(ctx, "inputs", None)
        if bool(getattr(inputs, "is_follow_up", False)):
            return True
        extra = getattr(ctx, "extra", None) or {}
        return bool(extra.get("is_follow_up", False))

    def _schedule_dream(self, ctx: Optional[AgentCallbackContext] = None) -> None:
        """Fire-and-forget dream; skip if a prior dream task is still running."""
        if self._dream_task is not None and not self._dream_task.done():
            logger.info("[TTSERail] dream schedule skipped: prior dream still running")
            return
        capabilities = self._last_capabilities
        agent = getattr(ctx, "agent", None) if ctx is not None else None

        async def _runner() -> None:
            caps = capabilities
            if not caps:
                try:
                    caps = await render_capabilities(agent)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[TTSERail] dream capability render failed: %s", exc)
                    caps = ""
            await self.run_dream(capabilities=caps)

        self._dream_task = asyncio.create_task(_runner())
        logger.info("[TTSERail] offline dream task scheduled")

    async def run_dream(self, *, capabilities: Optional[str] = None) -> None:
        """Run Auto-dream under the evolution lock (prune → merge → purge)."""
        caps = capabilities if capabilities is not None else (self._last_capabilities or "")
        names = parse_capability_names_from_text(caps) if caps else set()
        if not names:
            logger.warning("[TTSERail] dream capabilities empty; falling back to list_capability_names(None)")
            try:
                names = await list_capability_names(None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[TTSERail] dream capability name fallback failed: %s", exc)
                names = set()

        logger.info(
            "[TTSERail] offline dream begin model=%s caps=%s bank=%s",
            self._ttse_model,
            len(names),
            self._ttse_store.stats(),
        )
        state = load_dream_state(self._ttse_config.resolved_dream_state_path())
        async with self._evolution_lock:
            self._ttse_store.reload_if_disk_newer()
            try:
                result, _ = await run_dream_pass(
                    self._ttse_store,
                    self._ttse_config,
                    llm=self._ttse_llm,
                    model=self._ttse_model,
                    capabilities=caps or "",
                    capability_names=names,
                    state=state,
                )
                if not result.skipped:
                    # Dream MERGE/REWRITE inherits the cluster category; only
                    # reclassify bank rules that still lack a closed-set id.
                    need_classify = [
                        (text, rtype) for text, rtype in result.added_items if self._record_needs_category(text, rtype)
                    ]
                    if need_classify:
                        await self._classify_added_rules(need_classify)
                    await self._maybe_project_catalog()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[TTSERail] dream failed: %s", exc)

    # ------------------------------------------------------------------
    # Injection (before_model_call)
    # ------------------------------------------------------------------

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        await super().before_model_call(ctx)
        if not self._ttse_config.inject_enabled:
            return
        builder = getattr(getattr(ctx, "inputs", None), "system_prompt_builder", None)
        if builder is None:
            builder = getattr(getattr(ctx, "agent", None), "system_prompt_builder", None)
        if builder is None:
            return
        builder.add_section(
            PromptSection(
                name=SectionName.TTSE_FACTS_TIPS,
                content={
                    "cn": DISK_CATALOG_GUIDANCE_CN,
                    "en": DISK_CATALOG_GUIDANCE_EN,
                },
                priority=45,
            )
        )
        await self._attach_disk_catalog(ctx)

    async def _attach_disk_catalog(self, ctx: AgentCallbackContext) -> None:
        """Trail the category listing as a HISTORY prompt-attachment (not SYSTEM)."""
        agent = getattr(ctx, "agent", None) or self._agent
        manager = getattr(agent, "prompt_attachment_manager", None) or self._attachment_manager
        if manager is None:
            logger.warning("[TTSERail] skip catalog attachment: no prompt_attachment_manager")
            return
        session_id = self._catalog_session_id(ctx)
        if not session_id:
            logger.warning("[TTSERail] skip catalog attachment: no session_id")
            return
        counts = self._ttse_store.catalog_counts() if hasattr(self._ttse_store, "catalog_counts") else {}
        content = render_catalog_markdown(counts)
        try:
            await manager.add_section(
                session_id=session_id,
                section=_TTSE_CATALOG_SECTION,
                content=content,
                kind=PromptAttachmentKind.TEXT,
                source="ttse_rail",
                priority=_TTSE_CATALOG_PRIORITY,
                content_kind="text/markdown",
            )
        except ValueError as exc:
            logger.warning("[TTSERail] skip catalog attachment: %s", exc)
            return
        live = {k: int(v) for k, v in (counts or {}).items() if int(v or 0) > 0}
        logger.info("[TTSERail] trailed catalog attachment session_id=%s counts=%s", session_id, live)

    @staticmethod
    def _catalog_session_id(ctx: AgentCallbackContext) -> str | None:
        """Same session key the PromptAttachmentManager window mutator will collect."""
        session = getattr(ctx, "session", None)
        if session is not None:
            getter = getattr(session, "get_session_id", None)
            if callable(getter):
                sid = getter()
                if sid:
                    return str(sid)
            sid = getattr(session, "session_id", None)
            if callable(sid):
                sid = sid()
            if sid:
                return str(sid)
        context = getattr(ctx, "context", None)
        if context is not None:
            sid = getattr(context, "session_id", None)
            if callable(sid):
                sid = sid()
            if sid:
                return str(sid)
        return None

    # ------------------------------------------------------------------
    # Query extraction helpers
    # ------------------------------------------------------------------

    def _extract_query(self, ctx: Optional[AgentCallbackContext]) -> str:
        if ctx is None:
            return ""
        inputs = getattr(ctx, "inputs", None)
        query = getattr(inputs, "query", None) or getattr(inputs, "retrieval_query", None)
        if query:
            return str(query)
        return self._last_user_text(getattr(inputs, "messages", None))

    @staticmethod
    def _last_user_text(messages) -> str:
        if not messages:
            return ""
        for msg in reversed(messages):
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
            if role == "user":
                content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
                if content:
                    return str(content)
        return ""

    # ------------------------------------------------------------------
    # Bench export: persist trajectory even when evolve_enabled=False
    # Need to be deleted before merge into main branch
    # ------------------------------------------------------------------

    async def _on_after_invoke(
        self,
        ctx: AgentCallbackContext,
        trajectory: Trajectory | None,
    ) -> None:
        """Export a JSON snapshot for WorkBuddy Bench post-score TTSE.

        ``evolve_enabled=False`` still finalizes the trajectory in the base
        rail; this hook writes messages/query so a later ``ttse-post-score``
        step can call ``_run_ttse_induction`` with grader scores.
        """
        await super()._on_after_invoke(ctx, trajectory)
        await self._export_trajectory_for_bench(ctx, trajectory)

    async def _export_trajectory_for_bench(
        self,
        ctx: AgentCallbackContext,
        trajectory: Trajectory | None = None,
    ) -> None:
        export_path = (os.environ.get("TTSE_TRAJECTORY_EXPORT_PATH") or "").strip()
        if not export_path:
            return
        try:
            messages = self._trajectory_to_messages(trajectory) if trajectory is not None else []
            if not messages:
                inputs = getattr(ctx, "inputs", None)
                raw = getattr(inputs, "messages", None) if inputs is not None else None
                messages = self._normalize_messages(raw)
            if not messages:
                logger.debug("[TTSERail] traj export skipped: no messages")
                return
            try:
                capabilities = await render_capabilities(getattr(ctx, "agent", None))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[TTSERail] traj export capabilities failed: %s", exc)
                capabilities = ""
            payload = {
                "messages": messages,
                "ttse_task_query": self._extract_query(ctx),
                "ttse_capabilities": capabilities,
                "evolve_enabled": bool(self._ttse_config.evolve_enabled),
            }
            path = Path(export_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("[TTSERail] exported trajectory snapshot to %s (%s msgs)", path, len(messages))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[TTSERail] traj export failed: %s", exc)

    @staticmethod
    def _normalize_messages(raw: Any) -> list[dict]:
        if not raw:
            return []
        out: list[dict] = []
        for msg in raw:
            if isinstance(msg, dict):
                role = msg.get("role")
                content = msg.get("content")
            else:
                role = getattr(msg, "role", None)
                content = getattr(msg, "content", None)
            if role is None:
                continue
            if not isinstance(content, str):
                content = str(content) if content is not None else ""
            out.append({"role": str(role), "content": content})
        return out


__all__ = ["TTSERail"]
