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
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionRecord,
    EvolutionContext,
    EvolutionTarget,
    PendingChange,
    PendingSkillCreation,
    UsageStats,
)
from openjiuwen.agent_evolving.optimizer.skill_call import (
    SkillExperienceOptimizer,
    ExperienceScorer,
    SkillRewriter,
    SkillRewriteResult,
    update_score,
)
from openjiuwen.agent_evolving.signal import (
    SignalDetector,
    EvolutionSignal,
    EvolutionCategory,
    make_signal_fingerprint,
)
from openjiuwen.agent_evolving.trajectory import Trajectory, TrajectoryStore
from openjiuwen.core.common.logging import logger
from openjiuwen.core.operator.skill_call import SkillCallOperator
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.rails.evolution_rail import EvolutionRail

_MAX_PROCESSED_SIGNAL_KEYS = 500
_EVAL_SNIPPET_MAX_MESSAGES = 20
_EVAL_SNIPPET_MAX_CONTENT_CHARS = 200


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

    def __init__(
        self,
        skills_dir: Union[str, List[str]],
        *,
        llm: Any,
        model: str,
        auto_scan: bool = True,
        auto_save: bool = True,
        language: str = "cn",
        trajectory_store: Optional[TrajectoryStore] = None,
        eval_interval: int = 5,
        new_skill_detection: bool = True,
        new_skill_tool_threshold: int = 5,
        new_skill_tool_diversity: int = 2,
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
            new_skill_detection: Whether to enable new skill creation detection
            new_skill_tool_threshold: Minimum tool calls to trigger new skill detection
            new_skill_tool_diversity: Minimum unique tool count for new skill detection
        """
        # Validate configuration parameters
        if eval_interval < 1:
            raise ValueError("eval_interval must be >= 1")
        if new_skill_tool_threshold < 1:
            raise ValueError("new_skill_tool_threshold must be >= 1")
        if new_skill_tool_diversity < 1:
            raise ValueError("new_skill_tool_diversity must be >= 1")

        super().__init__(trajectory_store=trajectory_store)
        self._evolution_store = EvolutionStore(skills_dir)
        self._evolver = SkillExperienceOptimizer(llm, model, language)
        self._scorer = ExperienceScorer(llm, model, language)
        self._auto_scan = auto_scan
        self._processed_signal_keys: set[tuple[str, ...]] = set()
        self._auto_save = auto_save
        self._pending_approval_events: list[OutputSchema] = []
        # Optimizer path (for _auto_save=False): memory-staged records until user approval
        self._skill_ops: Dict[str, SkillCallOperator] = {}  # skill_name → operator
        # Store constructor args so _generate_experience_via_optimizer can create a
        # fresh SkillExperienceOptimizer per call, avoiding race conditions when
        # concurrent after_invoke calls share a single optimizer instance.
        self._optimizer_llm = llm
        self._optimizer_model = model
        self._optimizer_language = language
        # request_id → PendingChange: stable per-approval-prompt snapshot batches
        self._pending_approval_snapshots: Dict[str, PendingChange] = {}
        # New skill proposal storage: request_id → PendingSkillCreation
        self._pending_skill_proposals: Dict[str, PendingSkillCreation] = {}
        # Governance staging: request_id → {kind, skill_name, actions/new_body}
        self._pending_governance: Dict[str, Dict[str, Any]] = {}
        # Evaluation and new skill detection settings
        self._eval_interval = eval_interval
        self._new_skill_detection = new_skill_detection
        self._new_skill_tool_threshold = new_skill_tool_threshold
        self._new_skill_tool_diversity = new_skill_tool_diversity
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
    def processed_signal_keys(self) -> set[tuple[str, ...]]:
        """Get processed signal fingerprints."""
        return self._processed_signal_keys

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

    def update_llm(self, llm: Any, model: str) -> None:
        """Hot-update LLM client and model."""
        self._evolver.update_llm(llm, model)
        self._scorer.update_llm(llm, model)

    def clear_processed_signals(self) -> None:
        """Clear signal fingerprints, typically on conversation boundary."""
        self._processed_signal_keys.clear()

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Inject body experiences, track presented records, and record trajectory step.

        This override:
        1. Calls super().after_tool_call(ctx) to record trajectory step
        2. Injects body experiences when reading SKILL.md
        3. Tracks presented records for scoring
        """
        # First, let EvolutionRail record the trajectory step
        await super().after_tool_call(ctx)

        # Then, inject body experiences if reading SKILL.md
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

    async def run_evolution(self, trajectory: Trajectory, ctx: AgentCallbackContext) -> None:
        """Run skill evolution based on the collected trajectory.

        This is called by EvolutionRail.after_invoke after the trajectory is saved.
        SkillEvolutionRail implements evolution logic here instead of in after_invoke
        to align with EvolutionRail's design pattern.

        Args:
            trajectory: Complete trajectory for this conversation
            ctx: Callback context
        """
        logger.info("[SkillEvolutionRail] run_evolution called, auto_scan=%s", self._auto_scan)
        if not self._auto_scan:
            logger.info("[SkillEvolutionRail] auto_scan disabled, skipping")
            return

        try:
            parsed_messages = await self._collect_parsed_messages(ctx)
            logger.info("[SkillEvolutionRail] collected %d parsed messages", len(parsed_messages))
            if not parsed_messages:
                logger.info("[SkillEvolutionRail] no parsed messages, skipping")
                return

            skill_names = self._evolution_store.list_skill_names()
            logger.info("[SkillEvolutionRail] found %d local skills", len(skill_names))

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

            # Branch 1: Evolve existing skills (when signals are attributed to known skills)
            existing_skill_processed = False
            for skill_name, skill_signals in skill_groups.items():
                existing_skill_processed = True
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

            # Branch 2: Detect new skill creation (only when no existing skill was processed)
            logger.info(
                "[SkillEvolutionRail] existing_skill_processed=%s, new_skill_detection=%s",
                existing_skill_processed,
                self._new_skill_detection,
            )
            if not existing_skill_processed and self._new_skill_detection:
                logger.info("[SkillEvolutionRail] checking for new skill creation opportunity")
                should_propose = await self._should_propose_new_skill(parsed_messages)
                logger.info("[SkillEvolutionRail] new_skill_conditions_met=%s", should_propose)
                if should_propose:
                    proposal = await self._generate_new_skill_proposal(parsed_messages)
                    if proposal:
                        overlap = await self._check_skill_overlap(
                            proposal.get("name", ""),
                            proposal.get("description", ""),
                        )
                        if not overlap:
                            if self._auto_save:
                                # Auto-save path: create skill immediately without user approval
                                skill_name = proposal.get("name", "")
                                created_path = await self._evolution_store.create_skill(
                                    name=skill_name,
                                    description=proposal.get("description", ""),
                                    body=proposal.get("body", ""),
                                )
                                if created_path:
                                    logger.info(
                                        "[SkillEvolutionRail] auto-saved new skill '%s' at %s",
                                        skill_name,
                                        created_path,
                                    )
                                else:
                                    logger.warning(
                                        "[SkillEvolutionRail] failed to auto-save new skill '%s'",
                                        skill_name,
                                    )
                            else:
                                # Approval-required path: stage proposal and emit approval request
                                await self._emit_new_skill_approval(ctx, proposal)

            # Trigger async evaluation if interval reached
            await self._trigger_async_evaluation(ctx, parsed_messages)
        except Exception as exc:
            logger.warning("[SkillEvolutionRail] auto evolution failed: %s", exc)

    def drain_pending_approval_events(self) -> list[OutputSchema]:
        """Return and clear buffered approval events.

        Called by the host (JiuWenClaw) after the streaming loop ends,
        because ``after_invoke`` fires *after* the session stream is
        closed and ``session.write_stream`` can no longer deliver data.
        """
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
        ctx: AgentCallbackContext,
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
        messages: List[Any] = []

        # 1) preferred path: context directly carried on callback context
        if ctx.context is not None:
            try:
                messages = list(ctx.context.get_messages())
            except Exception as exc:
                logger.debug("[SkillEvolutionRail] read ctx.context messages failed: %s", exc)

        # 2) fallback for DeepAgent outer AFTER_INVOKE hooks: load from inner context engine
        if not messages and ctx.session is not None:
            agent_obj = ctx.agent
            inner_agent = getattr(agent_obj, "_react_agent", None)
            if inner_agent is not None and hasattr(inner_agent, "context_engine"):
                try:
                    context = await inner_agent.context_engine.create_context(session=ctx.session)
                    messages = list(context.get_messages())
                except Exception as exc:
                    logger.debug(
                        "[SkillEvolutionRail] load messages from inner context_engine failed: %s",
                        exc,
                    )

        return self._parse_messages(messages)

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
        skill_set = set(skill_names)
        hits: dict[str, int] = {}
        for msg in parsed_messages:
            role = msg.get("role", "")
            texts: list[str] = []
            if role in ("tool", "function"):
                texts.append(msg.get("content", ""))
            elif role == "assistant":
                texts.extend(tc.get("arguments", "") for tc in msg.get("tool_calls", []))
            for text in texts:
                for matched in self._SKILL_MD_RE.finditer(text):
                    name = matched.group(1)
                    if name in skill_set:
                        hits[name] = hits.get(name, 0) + 1
        if not hits:
            return None
        return max(hits, key=hits.get)  # type: ignore[arg-type]

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
        optimizer = SkillExperienceOptimizer(self._optimizer_llm, self._optimizer_model, self._optimizer_language)
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
        Records are re-enqueued into ``SkillCallOperator._staged_records`` and written via
        ``flush_to_store()``, which pops each record only after a successful write.  On
        partial failure the unwritten tail is preserved and the ``PendingChange`` entry is
        kept so the host can retry by calling ``on_approve`` again with the same id.
        """
        pending = self._pending_approval_snapshots.get(request_id)
        if not pending:
            logger.warning("[SkillEvolutionRail] on_approve: unknown request_id=%s", request_id)
            return
        skill_name = pending.skill_name
        skill_op = self._skill_ops.setdefault(skill_name, SkillCallOperator(skill_name))
        # Re-enqueue the snapshot so flush_to_store can use its retry-safe write loop.
        # Prepend rather than replace to preserve any records staged after the snapshot.
        skill_op.prepend_staged_records(pending.payload)
        flushed = await skill_op.flush_to_store(self._evolution_store)
        if skill_op.staged_records:
            # Partial failure: keep PendingChange for retry; update payload to remainder
            pending.payload[:] = skill_op.staged_records
            skill_op.discard_staged()
            logger.warning(
                "[SkillEvolutionRail] on_approve partial failure: %d/%d record(s) written for "
                "skill=%s (request=%s); retry on_approve to complete",
                flushed,
                flushed + len(pending.payload),
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

    async def _trigger_async_evaluation(
        self,
        ctx: AgentCallbackContext,
        parsed_messages: List[dict],  # noqa: ARG002  kept for API compatibility
    ) -> None:
        """Trigger async evaluation of presented experiences.

        Evaluates whether presented experiences were effectively used.
        Each record is evaluated against the snippet captured when it was
        presented, not the current conversation, to avoid cross-turn data leakage.
        """
        session = ctx.session if hasattr(ctx, "session") else None
        counter = self._get_session_eval_counter(session)
        counter += 1
        self._set_session_eval_counter(session, counter)

        if counter < self._eval_interval:
            return

        # Reset counter and get presented records
        self._set_session_eval_counter(session, 0)
        presented_entries = self._get_session_presented_records(session)
        self._set_session_presented_records(session, [])

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

                # Update records with evaluation results
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

    async def _should_propose_new_skill(
        self,
        parsed_messages: List[dict],
    ) -> bool:
        """Check if conditions are met for new skill proposal.

        Returns True if:
        - Tool call count meets threshold
        - Tool diversity meets threshold
        """
        tool_calls: List[dict] = []
        for msg in parsed_messages:
            if msg.get("role") == "assistant":
                calls = msg.get("tool_calls", [])
                if isinstance(calls, list):
                    tool_calls.extend(calls)

        logger.info(
            "[SkillEvolutionRail] new skill check: tool_calls=%d, threshold=%d",
            len(tool_calls),
            self._new_skill_tool_threshold,
        )

        if len(tool_calls) < self._new_skill_tool_threshold:
            logger.info(
                "[SkillEvolutionRail] tool call count %d below threshold %d",
                len(tool_calls),
                self._new_skill_tool_threshold,
            )
            return False

        # Check tool diversity
        unique_tools = set()
        for call in tool_calls:
            if isinstance(call, dict):
                name = call.get("name", "")
                if name:
                    unique_tools.add(name)

        logger.info(
            "[SkillEvolutionRail] unique_tools=%d, diversity_threshold=%d",
            len(unique_tools),
            self._new_skill_tool_diversity,
        )
        return len(unique_tools) >= self._new_skill_tool_diversity

    async def _generate_new_skill_proposal(
        self,
        parsed_messages: List[dict],
    ) -> Optional[Dict[str, Any]]:
        """Generate new skill proposal using optimizer."""
        existing_skills = self._evolution_store.list_skill_names()
        return await self._evolver.generate_new_skill_proposal(
            parsed_messages,
            existing_skills,
        )

    async def _check_skill_overlap(
        self,
        proposal_name: str,
        proposal_desc: str,
    ) -> bool:
        """Check if proposed skill overlaps with existing skills.

        Returns True if overlap detected (should not propose).
        """
        existing_names = self._evolution_store.list_skill_names()
        proposal_lower = proposal_name.lower()

        for existing in existing_names:
            # Simple name containment check
            if proposal_lower in existing.lower() or existing.lower() in proposal_lower:
                logger.info(
                    "[SkillEvolutionRail] new skill proposal '%s' overlaps with existing '%s'",
                    proposal_name,
                    existing,
                )
                return True

        return False

    async def _emit_new_skill_approval(
        self,
        ctx: AgentCallbackContext,
        proposal: Dict[str, Any],
    ) -> None:
        """Emit approval event for new skill creation."""
        pending = PendingSkillCreation(
            name=proposal.get("name", ""),
            description=proposal.get("description", ""),
            body=proposal.get("body", ""),
            reason=proposal.get("reason", ""),
        )
        proposal_id = pending.proposal_id
        self._pending_skill_proposals[proposal_id] = pending

        questions = [
            {
                "question": (
                    f"**New Skill Proposal: '{pending.name}'**\n\n"
                    f"{pending.description}\n\n"
                    f"**Reason:** {pending.reason}\n\n"
                    "Create this new skill?"
                ),
                "header": "New Skill Creation",
                "options": [
                    {"label": "Create", "description": "Create the new skill with proposed content"},
                    {"label": "Skip", "description": "Discard this proposal"},
                ],
                "multi_select": False,
            }
        ]
        event = OutputSchema(
            type="chat.ask_user_question",
            index=0,
            payload={
                "request_id": proposal_id,
                "_new_skill_data": {
                    "name": pending.name,
                    "description": pending.description,
                    "body": pending.body,
                },
                "questions": questions,
            },
        )
        self._pending_approval_events.append(event)
        logger.info(
            "[SkillEvolutionRail] buffered new skill approval request (%s) for '%s'",
            proposal_id,
            pending.name,
        )

    async def on_approve_new_skill(self, request_id: str) -> Optional[str]:
        """Handle approval of new skill creation.

        Args:
            request_id: The proposal ID

        Returns:
            Name of created skill, or None if failed
        """
        pending = self._pending_skill_proposals.pop(request_id, None)
        if not pending:
            logger.warning("[SkillEvolutionRail] on_approve_new_skill: unknown request_id=%s", request_id)
            return None

        try:
            result = await self._evolution_store.create_skill(
                name=pending.name,
                description=pending.description,
                body=pending.body,
            )
            if result:
                logger.info(
                    "[SkillEvolutionRail] user approved new skill creation: %s",
                    pending.name,
                )
                return pending.name
            else:
                logger.error(
                    "[SkillEvolutionRail] failed to create skill: %s",
                    pending.name,
                )
                return None
        except Exception as exc:
            logger.error(
                "[SkillEvolutionRail] on_approve_new_skill failed for %s: %s",
                pending.name,
                exc,
            )
            return None

    async def on_reject_new_skill(self, request_id: str) -> None:
        """Handle rejection of new skill creation."""
        pending = self._pending_skill_proposals.pop(request_id, None)
        if pending:
            logger.info(
                "[SkillEvolutionRail] user rejected new skill creation: %s",
                pending.name,
            )

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
    ) -> Optional[str]:
        """Stage a rebuild proposal and emit an approval event.

        Uses SkillRewriter to generate a new body by integrating evolution
        experiences, then stages it for user approval.

        Returns request_id on success, None if skill not found or rewrite fails.
        """
        store = self._evolution_store
        if not store.skill_exists(skill_name):
            return None

        rewriter = SkillRewriter(
            self._optimizer_llm,
            self._optimizer_model,
            self._optimizer_language,
        )
        result = await rewriter.rewrite(
            skill_name=skill_name,
            store=store,
            min_score=0.0,
            dry_run=True,
            user_query=user_intent or "",
        )
        if result is None:
            return None

        request_id = f"evolve_rebuild_{uuid.uuid4().hex[:8]}"
        self._pending_governance[request_id] = {
            "kind": "rebuild",
            "skill_name": skill_name,
            "new_body": result.rewritten_content,
            "consumed_ids": result.consumed_record_ids,
        }

        preview = result.rewritten_content[:2000]
        event = OutputSchema(
            type="chat.ask_user_question",
            index=0,
            payload={
                "request_id": request_id,
                "questions": [
                    {
                        "question": (
                            f"**重建 Skill '{skill_name}' 的 Body**\n\n"
                            f"```markdown\n{preview}\n```\n\n"
                            f"共整合 {len(result.consumed_record_ids)} 条经验。"
                            "⚠️ 重建将归档旧 body + 旧 evolutions 并清零经验。\n\n"
                            "是否执行？"
                        ),
                        "header": "Skill 重建审批",
                        "options": [
                            {"label": "重建", "description": "用新 body 替换"},
                            {"label": "取消", "description": "保持现状"},
                        ],
                        "multi_select": False,
                    }
                ],
            },
        )
        self._pending_approval_events.append(event)
        return request_id

    async def on_approve_rebuild(self, request_id: str) -> bool:
        """Execute a staged rebuild: archive old → write new → clear evolutions."""
        gov = self._pending_governance.pop(request_id, None)
        if not gov:
            return False

        store = self._evolution_store
        name = gov["skill_name"]
        await store.archive_skill_body(name)
        await store.archive_evolutions(name)
        await store.write_skill_content(name, gov["new_body"])
        await store.clear_evolutions(name)
        logger.info("[SkillEvolutionRail] rebuild completed for %s", name)
        return True

    async def on_reject_rebuild(self, request_id: str) -> None:
        gov = self._pending_governance.pop(request_id, None)
        if gov:
            logger.info("[SkillEvolutionRail] rebuild rejected for %s", gov["skill_name"])

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
