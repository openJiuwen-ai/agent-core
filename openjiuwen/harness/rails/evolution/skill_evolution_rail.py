# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SkillEvolutionRail for online auto-evolution.

This rail inherits EvolutionRail and gains automatic trajectory collection.
The evolution logic runs in after_invoke (after complete conversation) rather
than after_task_iteration (after each turn), because skill evolution needs
the full conversation context for signal detection and experience extraction.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionRecord,
    EvolutionContext,
    EvolutionTarget,
    PendingChange,
    UsageStats,
)
from openjiuwen.agent_evolving.optimizer.skill_call import (
    SkillExperienceOptimizer,
    ExperienceScorer,
    SkillRewriter,
    SkillRewriteResult,
    update_score,
)
from openjiuwen.agent_evolving.optimizer.skill_call.experience_optimizer import (
    GENERATE_RECORDS_LLM_POLICY,
)
from openjiuwen.agent_evolving.optimizer.skill_call.experience_scorer import (
    EVALUATE_LLM_POLICY,
    SIMPLIFY_LLM_POLICY,
)
from openjiuwen.agent_evolving.signal import (
    SignalDetector,
    EvolutionSignal,
    EvolutionCategory,
    make_signal_fingerprint,
)
from openjiuwen.agent_evolving.trajectory import Trajectory, TrajectoryStore
from openjiuwen.agent_evolving.utils import infer_skill_from_texts
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.operator.skill_call import SkillCallOperator
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.core.memory.lite.frontmatter import parse_frontmatter
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail
from openjiuwen.agent_evolving.optimizer.llm_resilience import LLMInvokePolicy

_MAX_PROCESSED_SIGNAL_KEYS = 500
_EVAL_SNIPPET_MAX_MESSAGES = 20
_EVAL_SNIPPET_MAX_CONTENT_CHARS = 200
_DEFAULT_EVOLUTION_TOTAL_TIMEOUT_SECS = 600.0


class SkillEvolutionRail(EvolutionRail):
    """Online auto-evolution rail for skill patching and persistence.

    Inherits EvolutionRail to gain automatic trajectory collection.
    Evolution logic runs in after_invoke (after complete conversation) because
    skill evolution needs full conversation context for signal detection.

    Note: This class uses two stores:
    - trajectory_store (from EvolutionRail): stores execution trajectories
    - evolution_store (EvolutionStore): stores skill evolution data (experiences, SKILL.md)
    """

    priority = 80
    _SKILL_MD_RE = re.compile(r"[/\\]([^/\\]+)[/\\]SKILL\.md", re.IGNORECASE)

    _REBUILD_PROMPT_TEMPLATE_CN = (
        "你收到了一个技能的重建请求。旧版本已归档，请执行以下步骤：\n\n"
        "## 已筛选的历史演进经验（score >= {min_score}）\n\n"
        "{evolution_records}\n\n"
        "## 用户意图\n\n"
        "{user_intent}\n\n"
        "## 执行要求\n\n"
        "请调用 skill-creator 技能：\n"
        "1. 基于以上历史经验和用户意图，生成新的 SKILL.md\n"
        "2. 重置 evolutions.json 为空列表\n\n"
        "旧版本已归档至 archive/ 目录，可直接创建新版本。"
    )

    _REBUILD_PROMPT_TEMPLATE_EN = (
        "You received a skill rebuild request. Old version has been archived. Please follow these steps:\n\n"
        "## Filtered Historical Evolution Records (score >= {min_score})\n\n"
        "{evolution_records}\n\n"
        "## User Intent\n\n"
        "{user_intent}\n\n"
        "## Execution Requirements\n\n"
        "Please invoke the skill-creator skill:\n"
        "1. Generate new SKILL.md based on the historical records and user intent above\n"
        "2. Reset evolutions.json to empty list\n\n"
        "Old version has been archived to archive/ directory, you can directly create the new version."
    )

    def __init__(
        self,
        skills_dir: Union[str, List[str]],
        *,
        llm: Model,
        model: str,
        auto_scan: bool = True,
        auto_save: bool = True,
        language: str = "cn",
        trajectory_store: Optional[TrajectoryStore] = None,
        team_trajectory_store: Optional[TrajectoryStore] = None,
        eval_interval: int = 5,
        evolution_total_timeout_secs: float = _DEFAULT_EVOLUTION_TOTAL_TIMEOUT_SECS,
        generate_records_llm_policy: LLMInvokePolicy = GENERATE_RECORDS_LLM_POLICY,
        evaluate_llm_policy: LLMInvokePolicy = EVALUATE_LLM_POLICY,
        simplify_llm_policy: LLMInvokePolicy = SIMPLIFY_LLM_POLICY,
    ) -> None:
        """Initialize SkillEvolutionRail.

        Args:
            skills_dir: Directory or list of directories containing skill definitions
            llm: LLM client for experience generation
            model: Model name for experience generation
            auto_scan: Whether to auto-detect evolution signals
            auto_save: Whether to auto-save generated experiences
            language: Language for experience generation ("cn" or "en")
            trajectory_store: Optional trajectory store (inherited from EvolutionRail)
            eval_interval: Number of conversations between async evaluations
        """
        if eval_interval < 1:
            raise ValueError("eval_interval must be >= 1")

        super().__init__(
            trajectory_store=trajectory_store,
            team_trajectory_store=team_trajectory_store
        )
        self._evolution_store = EvolutionStore(skills_dir)
        self._evolver = SkillExperienceOptimizer(
            llm,
            model,
            language,
            generate_records_llm_policy=generate_records_llm_policy,
        )
        self._scorer = ExperienceScorer(
            llm,
            model,
            language,
            evaluate_llm_policy=evaluate_llm_policy,
            simplify_llm_policy=simplify_llm_policy,
        )
        self._auto_scan = auto_scan
        self._processed_signal_keys: set[tuple[str, ...]] = set()
        self._auto_save = auto_save
        # Optimizer path (for _auto_save=False): memory-staged records until user approval
        self._skill_ops: Dict[str, SkillCallOperator] = {}  # skill_name → operator
        # Store constructor args so _generate_experience_via_optimizer can create a
        # fresh SkillExperienceOptimizer per call, avoiding race conditions when
        # concurrent after_invoke calls share a single optimizer instance.
        self._optimizer_llm = llm
        self._optimizer_model = model
        self._optimizer_language = language
        self._generate_records_llm_policy = generate_records_llm_policy
        self._evaluate_llm_policy = evaluate_llm_policy
        self._simplify_llm_policy = simplify_llm_policy
        self._evolution_total_timeout_secs = evolution_total_timeout_secs
        # request_id → PendingChange: stable per-approval-prompt snapshot batches
        self._pending_approval_snapshots: Dict[str, PendingChange] = {}
        # Governance staging: request_id → {kind, skill_name, actions/new_body}
        self._pending_governance: Dict[str, Dict[str, Any]] = {}
        # Evaluation settings
        self._eval_interval = eval_interval
        self._language = language

    @property
    def store(self) -> EvolutionStore:
        """Deprecated: Use evolution_store instead. Kept for backward compatibility."""
        return self._evolution_store

    @property
    def evolution_store(self) -> EvolutionStore:
        """Get the evolution store (for skill data, not trajectories)."""
        return self._evolution_store

    @property
    def scorer(self) -> ExperienceScorer:
        """Get the experience scorer."""
        return self._scorer

    @property
    def evolver(self) -> SkillExperienceOptimizer:
        """Get the experience optimizer."""
        return self._evolver

    @property
    def generate_records_llm_policy(self) -> LLMInvokePolicy:
        """Get the configured record generation policy."""
        return self._generate_records_llm_policy

    @property
    def evaluate_llm_policy(self) -> LLMInvokePolicy:
        """Get the configured experience evaluation policy."""
        return self._evaluate_llm_policy

    @property
    def simplify_llm_policy(self) -> LLMInvokePolicy:
        """Get the configured experience maintenance policy."""
        return self._simplify_llm_policy

    @property
    def evolution_total_timeout_secs(self) -> float:
        """Get the configured background evolution timeout budget."""
        return self._evolution_total_timeout_secs

    @property
    def evolution_config(self) -> Dict[str, Any]:
        """Get the effective evolution configuration."""
        return {
            "generate_records_llm_policy": self.generate_records_llm_policy,
            "evaluate_llm_policy": self.evaluate_llm_policy,
            "simplify_llm_policy": self.simplify_llm_policy,
            "evolution_total_timeout_secs": self.evolution_total_timeout_secs,
        }

    @property
    def processed_signal_keys(self) -> set[tuple[str, ...]]:
        """Get processed signal fingerprints."""
        return self._processed_signal_keys

    async def drain_pending_approval_events(
        self,
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> list[OutputSchema]:
        """Use evolution timeout as the default wait budget for approval draining."""
        if wait and timeout is None:
            timeout = self._evolution_total_timeout_secs
        return await super().drain_pending_approval_events(wait=wait, timeout=timeout)

    @property
    def auto_save(self) -> bool:
        """Whether auto-save is enabled."""
        return self._auto_save

    @auto_save.setter
    def auto_save(self, value: bool) -> None:
        self._auto_save = value

    @property
    def auto_scan(self) -> bool:
        """Whether auto-scan is enabled."""
        return self._auto_scan

    @auto_scan.setter
    def auto_scan(self, value: bool) -> None:
        self._auto_scan = value

    def set_sys_operation(self, sys_operation: SysOperation) -> None:
        """Set sys_operation for both EvolutionRail and EvolutionStore."""
        super().set_sys_operation(sys_operation)
        self._evolution_store.sys_operation = sys_operation

    def update_llm(self, llm: Model, model: str) -> None:
        """Hot-update LLM client and model."""
        self._evolver.update_llm(llm, model)
        self._scorer.update_llm(llm, model)
        self._optimizer_llm = llm
        self._optimizer_model = model

    def clear_processed_signals(self) -> None:
        """Clear signal fingerprints, typically on conversation boundary."""
        self._processed_signal_keys.clear()

    async def _on_after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Inject body experiences and track presented records.

        Base class after_tool_call has already recorded the trajectory step.
        This extension point handles SKILL.md-specific logic only.
        """
        # Inject body experiences if reading SKILL.md
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return

        tool_name = str(inputs.tool_name or "")
        if "read" not in tool_name.lower() and "file" not in tool_name.lower():
            return

        file_path = self._extract_file_path(inputs.tool_args)
        if not file_path:
            return

        matched = self._SKILL_MD_RE.search(file_path)
        if not matched:
            return

        skill_name = matched.group(1)

        # Retain: Inject pending body experiences (immediate effect)
        body_text = await self._evolution_store.format_body_experience_text(skill_name)
        if body_text:
            tool_msg = inputs.tool_msg
            if tool_msg is not None:
                original = tool_msg.content if isinstance(tool_msg.content, str) else str(tool_msg.content)
                tool_msg.content = original + body_text
                inputs.tool_msg = tool_msg
                logger.info("[SkillEvolutionRail] injected body experience for skill=%s", skill_name)

            # Track only the body records that were actually injected into this tool result.
            # Build a lightweight snippet bound to the current conversation so the scorer
            # evaluates each record against the context in which it was shown.
            current_messages = await self._collect_parsed_messages(ctx)
            presentation_snippet = "\n".join(
                f"[{m.get('role', 'unknown')}] {m.get('content', '')[:_EVAL_SNIPPET_MAX_CONTENT_CHARS]}"
                for m in current_messages[-_EVAL_SNIPPET_MAX_MESSAGES:]
            )
            await self._track_presented_records(ctx, skill_name, presentation_snippet)

    async def run_evolution(
        self,
        trajectory: Trajectory,
        ctx: Optional[AgentCallbackContext] = None,
        *,
        snapshot: Optional[dict] = None,
    ) -> None:
        """Run skill evolution based on the collected trajectory.

        In async mode: ctx=None, snapshot contains data captured by _snapshot_for_evolution.
        In sync mode: ctx is active, snapshot=None (backward-compatible).
        """
        logger.info("[SkillEvolutionRail] run_evolution called, auto_scan=%s", self._auto_scan)
        if not self._auto_scan:
            logger.info("[SkillEvolutionRail] auto_scan disabled, skipping")
            return

        try:
            # Async path: read from snapshot
            if snapshot is not None:
                parsed_messages = snapshot["parsed_messages"]
                presented_entries = snapshot.get("presented_entries", [])
            # Sync path: read from ctx (backward-compatible)
            elif ctx is not None:
                parsed_messages = await self._collect_parsed_messages(ctx)
                presented_entries = self._consume_session_eval_state(ctx)
            else:
                return

            logger.info("[SkillEvolutionRail] collected %d parsed messages", len(parsed_messages))
            if not parsed_messages:
                logger.info("[SkillEvolutionRail] no parsed messages, skipping")
                return

            all_skill_names = self._evolution_store.list_skill_names()
            skill_names = [name for name in all_skill_names if self._is_regular_skill(name)]
            logger.info(
                "[SkillEvolutionRail] found %d regular skills (filtered from %d local skills)",
                len(skill_names),
                len(all_skill_names),
            )

            signals = self._detect_signals(parsed_messages, skill_names)
            logger.info("[SkillEvolutionRail] detected %d signal(s)", len(signals))

            if not signals:
                primary_skill = self._infer_primary_skill(parsed_messages, skill_names)
                logger.info("[SkillEvolutionRail] no signals, inferred primary skill: %s", primary_skill)
                if primary_skill:
                    signals = [
                        EvolutionSignal(
                            signal_type="conversation_review",
                            evolution_type=EvolutionCategory.SKILL_EXPERIENCE,
                            section="",
                            excerpt="[Auto] No rule-based signals. Analyze conversation for implicit experiences.",
                            skill_name=primary_skill,
                        )
                    ]
                # Continue to new skill detection when no signals and no primary skill

            attributed_skills = {s.skill_name for s in signals if s.skill_name}
            unattributed = [s for s in signals if not s.skill_name]
            if len(attributed_skills) == 1 and unattributed:
                fallback_skill = next(iter(attributed_skills))
                for s in unattributed:
                    s.skill_name = fallback_skill

            skill_groups: dict[str, List[EvolutionSignal]] = {}
            for signal in signals:
                if not signal.skill_name:
                    continue
                skill_groups.setdefault(signal.skill_name, []).append(signal)

            # Evolve existing skills (when signals are attributed to known skills)
            for skill_name, skill_signals in skill_groups.items():
                if self._auto_save:
                    # Auto-save path: persist immediately without user approval
                    records = await self._generate_experience_for_skill(
                        skill_name,
                        skill_signals,
                        parsed_messages,
                    )
                    if records:
                        for record in records:
                            await self._evolution_store.append_record(skill_name, record)
                        logger.info(
                            "[SkillEvolutionRail] persisted %d record(s) for skill=%s",
                            len(records),
                            skill_name,
                        )
                else:
                    # Approval-required path: stage records and emit approval request
                    has_staged = await self._generate_experience_via_optimizer(
                        skill_name, skill_signals, parsed_messages
                    )
                    if has_staged:
                        await self._emit_generated_records(ctx, skill_name)

            # Trigger async evaluation
            await self._trigger_async_evaluation(presented_entries)
        except Exception as exc:
            logger.warning("[SkillEvolutionRail] auto evolution failed: %s", exc)

    # ===== Shared: drain approval events =====

    def _collect_pending_approval_events(self) -> list[OutputSchema]:
        """Return and clear the skill evolution event buffer."""
        events = list(self._pending_approval_events)
        self._pending_approval_events.clear()
        return events

    async def generate_and_emit_experience(
        self,
        skill_name: str,
        signals: List[EvolutionSignal],
        messages: List[dict],
        user_query: str = "",
    ) -> bool:
        """Generate experience records and emit approval events for a skill.

        Public entry point for host-driven (manual /evolve) evolution.
        Combines record staging and approval event emission into a single call.

        Args:
            skill_name: Name of the skill to evolve.
            signals: Detected evolution signals attributed to the skill.
            messages: Parsed conversation messages for context.
            user_query: Optional user-specified optimization direction.

        Returns:
            True if at least one experience record was staged and emitted.
        """
        # If user_query is non-empty, create a synthetic signal
        if user_query:
            signals = list(signals)  # avoid mutating the caller's list
            signals.append(
                EvolutionSignal(
                    signal_type="user_query",
                    evolution_type=EvolutionCategory.SKILL_EXPERIENCE,
                    section="",
                    excerpt=user_query,
                    skill_name=skill_name,
                )
            )

        # If messages is empty but user_query is non-empty, construct a synthetic user message
        if not messages and user_query:
            messages = [{"role": "user", "content": user_query}]

        has_records = await self._generate_experience_via_optimizer(
            skill_name,
            signals,
            messages,
            user_query=user_query,
        )
        if not has_records:
            return False
        await self._emit_generated_records(None, skill_name)
        return True

    async def _emit_generated_records(
        self,
        ctx: Optional[AgentCallbackContext],
        skill_name: str,
    ) -> None:
        """Buffer an approval-request OutputSchema for later delivery.

        The event is stored in ``_pending_approval_events`` because
        ``after_invoke`` runs after the session stream is already
        closed; writing to the stream at this point would be lost.
        The host drains these events via ``drain_pending_approval_events``.

        The staged records are snapshotted into a ``PendingChange`` and moved
        out of the operator queue at this point, keyed by ``change_id`` which
        doubles as the ``request_id`` returned to the host.  Concurrent
        approval prompts for the same skill each own a disjoint batch.
        """
        skill_op = self._skill_ops.get(skill_name)
        if not skill_op or not skill_op.staged_records:
            return

        # Atomically snapshot: move records from operator queue into PendingChange
        pending = PendingChange.make(skill_name, skill_op.take_snapshot())
        request_id = pending.change_id
        self._pending_approval_snapshots[request_id] = pending

        questions = []
        for record in pending.payload:
            content_preview = record.change.content[:1000]
            section = record.change.section
            target_tag = record.change.target.value
            questions.append(
                {
                    "question": (
                        f"**Skill '{skill_name}' 演进生成了新经验：**\n\n"
                        f"- **目标**: {target_tag}\n"
                        f"- **章节**: {section}\n\n"
                        f"{content_preview}"
                    ),
                    "header": "技能演进审批",
                    "options": [
                        {"label": "接收", "description": "保留此演进经验"},
                        {"label": "拒绝", "description": "丢弃此演进经验"},
                    ],
                    "multi_select": False,
                }
            )

        event = OutputSchema(
            type="chat.ask_user_question",
            index=0,
            payload={
                "request_id": request_id,
                "_evolution_meta": {"skill_name": skill_name, "request_id": request_id},
                "questions": questions,
            },
        )
        self._pending_approval_events.append(event)
        logger.info(
            "[SkillEvolutionRail] buffered approval request (%s) with %d record(s) for skill=%s",
            request_id,
            len(pending.payload),
            skill_name,
        )

    async def _collect_parsed_messages(self, ctx: AgentCallbackContext) -> List[dict]:
        return await self._collect_parsed_messages_from_ctx(ctx)

    def _detect_signals(
        self,
        parsed_messages: List[dict],
        skill_names: List[str],
    ) -> List[EvolutionSignal]:
        existing_skills = {name for name in skill_names if self._evolution_store.skill_exists(name)}
        detector = SignalDetector(existing_skills=existing_skills)
        detected = detector.detect(parsed_messages)

        new_signals: List[EvolutionSignal] = []
        for signal in detected:
            fp = make_signal_fingerprint(signal)
            if fp not in self._processed_signal_keys:
                self._processed_signal_keys.add(fp)
                new_signals.append(signal)

        if len(self._processed_signal_keys) > _MAX_PROCESSED_SIGNAL_KEYS:
            self._processed_signal_keys.clear()

        if new_signals:
            logger.info(
                "[SkillEvolutionRail] detected %d new signal(s), filtered=%d",
                len(new_signals),
                len(detected) - len(new_signals),
            )
        return new_signals

    def _infer_primary_skill(
        self,
        parsed_messages: List[dict],
        skill_names: List[str],
    ) -> Optional[str]:
        """Infer the most likely active skill from SKILL.md read traces in the conversation."""
        skill_tool_payloads: list[Any] = []
        texts: list[str] = []
        for msg in parsed_messages:
            role = msg.get("role", "")
            if role in ("tool", "function"):
                texts.append(str(msg.get("content", "")))
            elif role == "assistant":
                for tool_call in msg.get("tool_calls", []):
                    texts.append(str(tool_call.get("arguments", "")))
                    if tool_call.get("name") == "skill_tool":
                        skill_tool_payloads.append(tool_call.get("arguments"))

        return infer_skill_from_texts(
            skill_names,
            skill_tool_payloads=skill_tool_payloads,
            texts=texts,
        )

    def _is_regular_skill(self, name: str) -> bool:
        """Exclude team skills from 1D skill evolution detection.

        If the skill directory cannot be resolved from a mocked or incomplete
        store, keep the skill eligible rather than silently disabling
        evolution for it.
        """
        skill_dir = self._evolution_store.resolve_skill_dir(name)
        if skill_dir is None:
            return True

        if isinstance(skill_dir, str):
            skill_dir = Path(skill_dir)
        elif isinstance(skill_dir, os.PathLike):
            skill_dir = Path(skill_dir)
        elif not isinstance(skill_dir, Path):
            return True

        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return True

        try:
            text = skill_md.read_text(encoding="utf-8")
            frontmatter = parse_frontmatter(text) or {}
            return frontmatter.get("kind") != "team-skill"
        except Exception:
            return True

    async def _generate_experience_via_optimizer(
        self,
        skill_name: str,
        signals: List[EvolutionSignal],
        messages: List[dict],
        user_query: str = "",
    ) -> bool:
        """Stage experience records in memory; no disk write until on_approve().

        Returns True if at least one record was staged.
        """
        if skill_name not in self._skill_ops:
            self._skill_ops[skill_name] = SkillCallOperator(skill_name)
        skill_op = self._skill_ops[skill_name]
        await skill_op.refresh_state(self._evolution_store)
        # Inject current conversation messages so the optimizer can use them for context
        state = skill_op.get_state()
        state["messages"] = messages
        if user_query:
            state["user_query"] = user_query
        skill_op.load_state(state)

        # bind() expects Dict[str, Operator], not a list
        # Create a fresh optimizer per call to avoid race conditions when concurrent
        # after_invoke calls share a single instance (bind() resets internal state).
        optimizer = SkillExperienceOptimizer(
            self._optimizer_llm,
            self._optimizer_model,
            self._optimizer_language,
            generate_records_llm_policy=self._generate_records_llm_policy,
        )
        optimizer.bind({skill_op.operator_id: skill_op})
        await optimizer.backward(signals)
        updates = optimizer.step()

        for (op_id, target), value in updates.items():
            if target == "experiences" and value:
                skill_op.set_parameter("experiences", value)

        return bool(skill_op.staged_records)

    async def on_approve(self, request_id: str) -> None:
        """Called by host when user accepts: flush the snapshotted records.

        The ``request_id`` matches ``payload["request_id"]`` from the approval event.
        Only the records captured in this approval snapshot are written. On
        partial failure the unwritten tail is preserved in the same
        ``PendingChange`` so the host can retry by calling ``on_approve``
        again with the same id.
        """
        pending = self._pending_approval_snapshots.get(request_id)
        if not pending:
            logger.warning("[SkillEvolutionRail] on_approve: unknown request_id=%s", request_id)
            return
        skill_name = pending.skill_name
        skill_op = self._skill_ops.setdefault(skill_name, SkillCallOperator(skill_name))
        result = await skill_op.flush_records_to_store(self._evolution_store, pending.payload)
        flushed = result.flushed_count
        if result.remaining_records:
            pending.payload[:] = result.remaining_records
            logger.warning(
                "[SkillEvolutionRail] on_approve partial failure: %d/%d record(s) written for "
                "skill=%s (request=%s); retry on_approve to complete",
                flushed,
                flushed + len(result.remaining_records),
                skill_name,
                request_id,
            )
            return
        self._pending_approval_snapshots.pop(request_id)
        logger.info(
            "[SkillEvolutionRail] user approved %d record(s) for skill=%s (request=%s)",
            flushed,
            skill_name,
            request_id,
        )

    async def on_reject(self, request_id: str) -> None:
        """Called by host when user rejects: discard the snapshotted records, no disk write.

        The ``request_id`` matches ``payload["request_id"]`` from the approval event.
        """
        pending = self._pending_approval_snapshots.pop(request_id, None)
        if not pending:
            logger.warning("[SkillEvolutionRail] on_reject: unknown request_id=%s", request_id)
            return
        logger.info(
            "[SkillEvolutionRail] user rejected %d record(s) for skill=%s (request=%s)",
            len(pending.payload),
            pending.skill_name,
            request_id,
        )

    async def _generate_experience_for_skill(
        self,
        skill_name: str,
        signals: List[EvolutionSignal],
        messages: List[dict],
        user_query: str = "",
    ) -> List[EvolutionRecord]:
        context = EvolutionContext(
            skill_name=skill_name,
            signals=signals,
            skill_content=await self._evolution_store.read_skill_content(skill_name),
            messages=messages,
            existing_desc_records=await self._evolution_store.get_pending_records(
                skill_name, EvolutionTarget.DESCRIPTION
            ),
            existing_body_records=await self._evolution_store.get_pending_records(skill_name, EvolutionTarget.BODY),
            user_query=user_query,
        )
        try:
            return await self._evolver.generate_records(context)
        except Exception as exc:
            logger.warning(
                "[SkillEvolutionRail] generate failed (skill=%s): %s",
                skill_name,
                exc,
            )
            return []

    @classmethod
    def _extract_file_path(cls, tool_args: Any) -> str:
        if isinstance(tool_args, dict):
            args = tool_args
        elif isinstance(tool_args, str):
            try:
                parsed = json.loads(tool_args)
                args = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                args = {}
        else:
            args = {}
        file_path = args.get("file_path", "")
        return str(file_path) if file_path else ""

    @classmethod
    def _parse_messages(cls, messages: List[Any]) -> List[dict]:
        result: List[dict] = []
        for message in messages:
            if isinstance(message, dict):
                result.append(message)
                continue

            role = getattr(message, "role", "")
            content = str(getattr(message, "content", "") or "")

            item: dict = {"role": role, "content": content}

            tool_calls = getattr(message, "tool_calls", None)
            if tool_calls:
                item["tool_calls"] = [
                    {
                        "id": getattr(tool_call, "id", ""),
                        "name": getattr(tool_call, "name", ""),
                        "arguments": getattr(tool_call, "arguments", ""),
                    }
                    for tool_call in tool_calls
                ]

            name = getattr(message, "name", None)
            if name:
                item["name"] = name

            result.append(item)
        return result

    # Session-level state isolation helpers
    def _get_session_presented_records(
        self,
        session: Any,
    ) -> List[tuple[str, EvolutionRecord, str]]:
        """Get presented records stored in session for concurrency isolation.

        Each entry is (skill_name, record, snippet) where snippet is the
        conversation context captured at the time of presentation.
        """
        if session is None:
            return []
        return getattr(session, "_skill_evolution_presented", [])

    def _set_session_presented_records(
        self,
        session: Any,
        records: List[tuple[str, EvolutionRecord, str]],
    ) -> None:
        """Store presented records in session for concurrency isolation."""
        if session is not None:
            setattr(session, "_skill_evolution_presented", records)

    def _get_session_eval_counter(self, session: Any) -> int:
        """Get evaluation counter from session."""
        if session is None:
            return 0
        return getattr(session, "_skill_evolution_eval_counter", 0)

    def _set_session_eval_counter(self, session: Any, value: int) -> None:
        """Set evaluation counter in session."""
        if session is not None:
            setattr(session, "_skill_evolution_eval_counter", value)

    async def _track_presented_records(
        self,
        ctx: AgentCallbackContext,
        skill_name: str,
        presentation_snippet: str,
    ) -> None:
        """Track presented records for scoring statistics.

        Updates times_presented and stores (skill_name, record, snippet) in
        session for later evaluation.  The snippet is captured at presentation
        time so that the scorer always evaluates each record against the
        conversation in which it was actually shown, not a later one.

        Builds the update payload from snapshots of current stats to avoid
        mutating records before a successful DB write.
        """
        try:
            records = await self._evolution_store.get_records_by_score(
                skill_name,
                min_score=0.5,
            )
            if not records:
                return

            # Only track BODY records: those are the ones actually injected into
            # the SKILL.md tool result.  DESCRIPTION records are surfaced through
            # the index block and are not individually injected, so inflating their
            # times_presented here would produce misleading utilisation scores.
            body_records = [r for r in records if r.target == EvolutionTarget.BODY]
            if not body_records:
                return

            updates: Dict[str, Dict[str, Any]] = {}
            now = datetime.now(tz=timezone.utc).isoformat()

            # Build update payload without touching the in-memory records yet
            for record in body_records[:5]:  # Track top 5 body records
                existing_stats = record.usage_stats or UsageStats()
                new_stats = UsageStats(
                    times_presented=existing_stats.times_presented + 1,
                    times_used=existing_stats.times_used,
                    times_positive=existing_stats.times_positive,
                    times_negative=existing_stats.times_negative,
                    last_presented_at=now,
                    last_evaluated_at=existing_stats.last_evaluated_at,
                )
                updates[record.id] = {
                    "score": record.score,
                    "usage_stats": new_stats.to_dict(),
                }

            # Persist first; only update in-memory objects and session after success
            await self._evolution_store.update_record_scores(skill_name, updates)

            presented_entries: List[tuple[str, EvolutionRecord, str]] = []
            for record in body_records[:5]:
                if record.id in updates:
                    # Now safe to apply to in-memory object
                    record.usage_stats = UsageStats.from_dict(updates[record.id]["usage_stats"])
                    presented_entries.append((skill_name, record, presentation_snippet))

            # Store in session for concurrency isolation
            session = ctx.session if hasattr(ctx, "session") else None
            existing = self._get_session_presented_records(session)
            self._set_session_presented_records(session, existing + presented_entries)

            logger.debug(
                "[SkillEvolutionRail] tracked %d presented records for skill=%s",
                len(presented_entries),
                skill_name,
            )
        except Exception as exc:
            logger.debug("[SkillEvolutionRail] track presented records failed: %s", exc)

    def _consume_session_eval_state(
        self, ctx: AgentCallbackContext,
    ) -> List[tuple[str, EvolutionRecord, str]]:
        """Consume eval counter and presented records from session.

        In snapshot phase (ctx alive): reads counter, increments it,
        and if threshold reached, consumes presented_records.
        Resets session state so background task doesn't need session access.
        """
        session = ctx.session if hasattr(ctx, "session") else None
        counter = self._get_session_eval_counter(session)
        counter += 1
        presented_entries: List[tuple[str, EvolutionRecord, str]] = []
        if counter >= self._eval_interval:
            presented_entries = self._get_session_presented_records(session)
            self._set_session_presented_records(session, [])
            self._set_session_eval_counter(session, 0)
        else:
            self._set_session_eval_counter(session, counter)
        return presented_entries

    async def _snapshot_for_evolution(
        self, trajectory: Trajectory, ctx: AgentCallbackContext,
    ) -> Optional[dict]:
        """Phase 1: Collect messages and consume session state while ctx is alive."""
        if not self._auto_scan:
            return None

        snapshot = await super()._snapshot_for_evolution(trajectory, ctx)
        if snapshot is None:
            return None

        parsed_messages = snapshot["parsed_messages"]
        if not parsed_messages:
            return None

        session_id = ctx.inputs.conversation_id if ctx.inputs else ""

        # Consume session state before ctx becomes invalid
        presented_entries = self._consume_session_eval_state(ctx)

        snapshot.update(
            {
                "session_id": session_id,
                "presented_entries": presented_entries,
                "skill_name": "skill-evolution",
            }
        )
        return snapshot

    async def _trigger_async_evaluation(
        self,
        presented_entries: List[tuple[str, EvolutionRecord, str]],
    ) -> None:
        """Trigger async evaluation of presented experiences.

        In async mode: presented_entries comes from snapshot (consumed in _snapshot_for_evolution).
        In sync mode: presented_entries comes from _consume_session_eval_state in run_evolution.
        """
        if not presented_entries:
            return

        try:
            # Group by (skill_name, snippet) so each batch is evaluated against
            # the conversation in which those records were actually presented.
            by_skill_snippet: Dict[tuple[str, str], List[EvolutionRecord]] = {}
            for skill_name, record, snippet in presented_entries:
                key = (skill_name, snippet)
                by_skill_snippet.setdefault(key, []).append(record)

            for (skill_name, snippet), records in by_skill_snippet.items():
                eval_results = await self._scorer.evaluate(snippet, records)
                if not eval_results:
                    continue

                updates: Dict[str, Dict[str, Any]] = {}
                for result in eval_results:
                    record_id = result.get("record_id")
                    if not record_id:
                        continue
                    for record in records:
                        if record.id == record_id:
                            new_score = update_score(record, result)
                            if record.usage_stats is None:
                                record.usage_stats = UsageStats()
                            updates[record_id] = {
                                "score": new_score,
                                "usage_stats": record.usage_stats.to_dict(),
                            }
                            break

                if updates:
                    await self._evolution_store.update_record_scores(skill_name, updates)
                    logger.info(
                        "[SkillEvolutionRail] async evaluation updated %d record(s) for skill=%s",
                        len(updates),
                        skill_name,
                    )
        except Exception as exc:
            logger.warning("[SkillEvolutionRail] async evaluation failed: %s", exc, exc_info=True)

    # ── Governance commands (shared by 1D and team skills) ──

    async def request_simplify(
        self,
        skill_name: str,
        user_intent: Optional[str] = None,
    ) -> Optional[str]:
        """Stage a simplify proposal and emit an approval event.

        Returns request_id on success, None if no actions proposed.
        """
        store = self._evolution_store
        if not store.skill_exists(skill_name):
            return None

        evo_log = await store.load_full_evolution_log(skill_name)
        records = evo_log.entries
        if not records:
            return None

        content = await store.read_skill_content(skill_name)
        summary = store.extract_description_from_skill_md(content)

        actions = await self._scorer.simplify(
            skill_name=skill_name,
            skill_summary=summary,
            records=records,
            user_intent=user_intent,
        )
        if not actions:
            return None

        request_id = f"evolve_simplify_{uuid.uuid4().hex[:8]}"
        self._pending_governance[request_id] = {
            "kind": "simplify",
            "skill_name": skill_name,
            "actions": actions,
        }

        preview = "\n".join(
            f"- **{a.get('action', '?')}** `{a.get('record_id', '?')}`: {a.get('reason', '')}" for a in actions[:10]
        )
        event = OutputSchema(
            type="chat.ask_user_question",
            index=0,
            payload={
                "request_id": request_id,
                "questions": [
                    {
                        "question": (
                            f"**精简 Skill '{skill_name}' 的演进经验**\n\n"
                            f"共 {len(actions)} 项操作：\n{preview}\n\n"
                            "是否执行？"
                        ),
                        "header": "Skill 精简审批",
                        "options": [
                            {"label": "执行", "description": "执行精简操作"},
                            {"label": "取消", "description": "放弃本次精简"},
                        ],
                        "multi_select": False,
                    }
                ],
            },
        )
        self._pending_approval_events.append(event)
        return request_id

    async def on_approve_simplify(self, request_id: str) -> Dict[str, int]:
        """Execute a previously staged simplify proposal."""
        gov = self._pending_governance.pop(request_id, None)
        if not gov:
            return {}

        result = await self._scorer.execute_simplify_actions(
            store=self._evolution_store,
            skill_name=gov["skill_name"],
            actions=gov["actions"],
        )
        logger.info("[SkillEvolutionRail] simplify executed for %s: %s", gov["skill_name"], result)
        return result

    async def on_reject_simplify(self, request_id: str) -> None:
        gov = self._pending_governance.pop(request_id, None)
        if gov:
            logger.info("[SkillEvolutionRail] simplify rejected for %s", gov["skill_name"])

    async def request_rebuild(
        self,
        skill_name: str,
        user_intent: Optional[str] = None,
        min_score: float = 0.5,
    ) -> Optional[str]:
        """Build a rebuild prompt for skill.

        Archive old version FIRST, then return prompt containing filtered
        evolution records. The caller (slash command handler or host) injects
        this prompt into agent loop, and agent executes skill-creator to
        generate new SKILL.md.

        Args:
            skill_name: Name of the skill to rebuild.
            user_intent: Optional user-specified optimization direction.
            min_score: Minimum score threshold for evolution records to include.
                Default 0.5 filters out low-quality experiences.

        Returns the followup prompt text on success, None if skill not found.
        """
        store = self._evolution_store
        if not store.skill_exists(skill_name):
            return None

        # Step 1: Archive current SKILL.md and evolutions.json BEFORE rebuild
        evo_archive: Optional[str] = None
        try:
            body_archive = await store.archive_skill_body(skill_name)
            if body_archive:
                logger.info(
                    "[SkillEvolutionRail] archived SKILL.md -> %s for '%s'",
                    body_archive,
                    skill_name,
                )
        except Exception as exc:
            logger.warning(
                "[SkillEvolutionRail] skill body archive failed for '%s': %s",
                skill_name,
                exc,
            )

        try:
            evo_archive = await store.archive_evolutions(skill_name)
            if evo_archive:
                logger.info(
                    "[SkillEvolutionRail] archived evolutions.json -> %s for '%s'",
                    evo_archive,
                    skill_name,
                )
                logger.info("[SkillEvolutionRail] archived old version for '%s'", skill_name)
        except Exception as exc:
            logger.warning(
                "[SkillEvolutionRail] evolutions archive failed for '%s': %s",
                skill_name,
                exc,
            )

        # Step 2: Load and filter evolution records
        records_log = await store.load_full_evolution_log(skill_name)
        filtered_records = []
        for record in records_log.entries:
            score = getattr(record, "score", 0.0)
            if score < min_score:
                continue
            change = getattr(record, "change", None)
            skip_reason = getattr(change, "skip_reason", None) if change else None
            if skip_reason:
                continue
            filtered_records.append(record)

        # Step 3: Format evolution records as readable text
        evolution_text = self._format_evolution_records(filtered_records)
        if self._optimizer_language == "cn":
            intent = user_intent or "根据以上演进经验，对技能进行全面优化和重建。"
        else:
            intent = user_intent or (
                "Based on the evolution records above, perform a comprehensive rebuild "
                "of the skill."
            )

        template = (
            self._REBUILD_PROMPT_TEMPLATE_CN if self._optimizer_language == "cn"
            else self._REBUILD_PROMPT_TEMPLATE_EN
        )

        logger.info(
            "[SkillEvolutionRail] rebuild prompt built: skill=%s, "
            "total_records=%d, filtered_records=%d, min_score=%.2f",
            skill_name,
            len(records_log.entries),
            len(filtered_records),
            min_score,
        )

        followup_text = template.format(
            evolution_records=evolution_text,
            user_intent=intent,
            min_score=min_score,
        )

        # Reset active evolutions only after the prompt is successfully built.
        if evo_archive:
            await store.clear_evolutions(skill_name)
            logger.info(
                "[SkillEvolutionRail] cleared evolutions.json after rebuild request for '%s'",
                skill_name,
            )

        return followup_text

    @staticmethod
    def _format_evolution_records(records: List[EvolutionRecord]) -> str:
        """Format evolution records as readable markdown text."""
        lines: List[str] = []
        for idx, record in enumerate(records, 1):
            change = record.change
            section = getattr(change, "section", "?")
            content = getattr(change, "content", "")
            source = getattr(record, "source", "unknown")
            timestamp = getattr(record, "timestamp", "")
            score = getattr(record, "score", 0.0)
            lines.append(
                f"### 经验 #{idx} [{timestamp}] - source: {source}, score: {score:.2f}\n"
                f"- Section: {section}\n"
                f"- 内容: {content}"
            )
        return "\n\n".join(lines) if lines else "（无演进经验）"

    async def rollback_skill(self, skill_name: str, version: Optional[str] = None) -> bool:
        """Rollback skill to an archived version (no approval required)."""
        store = self._evolution_store
        skill_dir = store.resolve_skill_dir(skill_name)
        if skill_dir is None:
            return False

        archive = skill_dir / "archive"
        if not archive.is_dir():
            logger.warning("[SkillEvolutionRail] no archive dir for %s", skill_name)
            return False

        if version:
            body_archive = archive / version
            evo_version = version.replace("SKILL.", "evolutions.").replace(".md", ".json")
            evo_archive = archive / evo_version
        else:
            body_files = sorted(
                [f for f in archive.iterdir() if f.name.startswith("SKILL.v")],
                key=lambda p: p.name,
                reverse=True,
            )
            if not body_files:
                logger.warning("[SkillEvolutionRail] no archived body for %s", skill_name)
                return False
            body_archive = body_files[0]
            evo_version = body_archive.name.replace("SKILL.", "evolutions.").replace(".md", ".json")
            evo_archive = archive / evo_version

        # Archive current state first
        await store.archive_skill_body(skill_name)
        await store.archive_evolutions(skill_name)

        old_body = await store.read_file_text(body_archive)
        await store.write_skill_content(skill_name, old_body)

        if evo_archive.is_file():
            evo_content = await store.read_file_text(evo_archive)
            evo_path = skill_dir / "evolutions.json"
            await store.write_file_text(evo_path, evo_content)
        else:
            await store.clear_evolutions(skill_name)

        await store.render_evolution_markdown(skill_name)
        logger.info("[SkillEvolutionRail] rollback completed for %s -> %s", skill_name, body_archive.name)
        return True

    def should_hint_simplify_or_rebuild(self, skill_name: str) -> bool:
        """Check if a skill has enough evolutions to suggest simplify/rebuild."""
        store = self._evolution_store
        skill_dir = store.resolve_skill_dir(skill_name)
        if skill_dir is None:
            return False
        evo_path = skill_dir / "evolutions.json"
        if not evo_path.is_file():
            return False
        try:
            data = json.loads(evo_path.read_text(encoding="utf-8"))
            entries = data.get("entries", [])
            return len(entries) >= 10
        except Exception:
            return False

    async def rewrite_skill(
        self,
        skill_name: str,
        *,
        min_score: float = 0.0,
        dry_run: bool = False,
        user_query: str = "",
    ) -> Optional[SkillRewriteResult]:
        """Rewrite SKILL.md by integrating evolution experiences.

        This is a public API for callers to actively trigger skill rewriting.
        This method uses LLM to deeply integrate experiences into the
        SKILL.md body for a more natural, coherent document.

        Args:
            skill_name: Name of the skill to rewrite
            min_score: Minimum score threshold for experiences to include
            dry_run: If True, only return result without writing to disk
            user_query: Optional user-specified optimization direction

        Returns:
            SkillRewriteResult on success, None if no valid experiences or rewrite not needed

        Example:
            result = await rail.rewrite_skill("my-skill", min_score=0.5)
            if result:
                print(result.summary)
        """
        rewriter = SkillRewriter(
            self._optimizer_llm,
            self._optimizer_model,
            self._optimizer_language,
        )
        result = await rewriter.rewrite(
            skill_name,
            self._evolution_store,
            min_score=min_score,
            dry_run=dry_run,
            user_query=user_query,
        )
        # The rewriter already deletes consumed records in rewrite().
        return result


__all__ = ["SkillEvolutionRail"]
