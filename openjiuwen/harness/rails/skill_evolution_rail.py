# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SkillEvolutionRail for online auto-evolution.

This rail inherits EvolutionRail and gains automatic trajectory collection.
The evolution logic is scheduled from after_invoke (after complete conversation)
rather than after_task_iteration (after each turn), because skill evolution needs
the full conversation context for signal detection and experience extraction.

Evolution itself runs as a background asyncio task so the main dialogue path
is not blocked by LLM-based experience generation or evaluation.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Union

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
    build_tool_call_chain,
    update_score,
)
from openjiuwen.agent_evolving.signal import (
    SignalDetector,
    EvolutionSignal,
    make_signal_fingerprint,
)
from openjiuwen.agent_evolving.skill_self_evolution import (
    resolve_skill_evolution_action,
)
from openjiuwen.agent_evolving.trajectory import (
    Trajectory,
    TrajectoryStore,
)
from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.active_skill_bodies import normalize_skill_relative_file_path
from openjiuwen.core.operator.skill_call import SkillCallOperator
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.rails.evolution_rail import EvolutionRail

_MAX_PROCESSED_SIGNAL_KEYS = 500
_EVAL_SNIPPET_MAX_MESSAGES = 20
_EVAL_SNIPPET_MAX_CONTENT_CHARS = 200
_EVAL_SNIPPET_POST_PRESENT_MAX_CHARS = 800
_UI_SUMMARY_RECORD_PREVIEW_CHARS = 400
_UI_SUMMARY_SIGNAL_PREVIEW_CHARS = 280
_UI_SUMMARY_LLM_MAX_ATTEMPTS = 3
_UI_SUMMARY_MIN_BODY_CHARS = 24
_UI_SUMMARY_MAX_BODY_CHARS = 600
_UI_SUMMARY_SNIPPET_MAX_MESSAGES = 12


def _build_ui_summary_conversation_snippet(
    messages: List[dict],
    *,
    max_messages: int = _UI_SUMMARY_SNIPPET_MAX_MESSAGES,
    content_preview_chars: int = _EVAL_SNIPPET_MAX_CONTENT_CHARS,
    language: str = "cn",
) -> str:
    """Build a compact dialogue snippet for UI summary LLM prompts."""
    if not messages:
        return ""

    def _extract_text(message: dict) -> str:
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(str(block.get("text", "")))
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(parts)
        return str(content)

    empty_label = "(无文本)" if language == "cn" else "(No text)"
    lines: List[str] = []
    for message in messages[-max_messages:]:
        role = message.get("role", "unknown")
        text = _extract_text(message).strip() or empty_label
        if len(text) > content_preview_chars:
            orig_len = len(text)
            suffix = (
                f"\n... [已截断，原始长度 {orig_len} 字符]"
                if language == "cn"
                else f"\n... [truncated, original {orig_len} chars]"
            )
            text = text[:content_preview_chars] + suffix
        lines.append(f"[{role}] {text}")
    return "\n".join(lines)

_UI_SUMMARY_RETRY_SUFFIX_CN = (
    "\n\n【重试说明】上次输出无效（为空、过短或格式不符）。"
    "请重新生成 2-4 行中文正文：说明演进的 Skill、改动要点、触发原因；"
    "不要代码块、不要 JSON、不要标题行。"
)

_UI_SUMMARY_RETRY_SUFFIX_EN = (
    "\n\n[Retry] Previous output was invalid (empty, too short, or wrong format). "
    "Regenerate 2-4 lines covering skill name, change gist, and trigger reason. "
    "No code fences, no JSON, no title line."
)

_EVOLUTION_UI_SUMMARY_PROMPT_CN = """\
你是技能演进助手。根据本轮自动保存的 Skill 演进结果，写一段**极简**中文说明，将直接展示在聊天回复脚注。

## 要求
1. 正文 2-4 行，总字数不超过 150 字（不要包含标题）
2. 必须交代清楚三件事：① 演进了哪个 Skill；② 新增/调整了什么要点（一句话概括内容，不要贴全文）；\
③ 触发原因（用户反馈、执行问题等）
3. 用自然语言，禁止出现 body/description、records_count 等技术字段
4. 可用短列表（- 开头），语气客观简洁

## 本轮演进数据（JSON）
{evolution_json}

## 相关对话片段（供理解原因）
{conversation_snippet}

只输出正文 Markdown，不要代码块，不要重复写「技能已自动演进」类标题。"""

_EVOLUTION_UI_SUMMARY_PROMPT_EN = """\
You are a skill evolution assistant. Write a **very brief** English footnote for the user \
based on this auto-saved skill evolution round.

## Requirements
1. 2-4 lines, at most 150 words (no title line)
2. Cover: which Skill evolved; what was added/changed (one-line gist); why (user feedback, failure, etc.)
3. Plain language; no technical field names like body/description
4. Short bullets allowed; objective tone

## Evolution data (JSON)
{evolution_json}

## Conversation snippet (for trigger context)
{conversation_snippet}

Output Markdown body only. No code fences. Do not repeat a "skill evolved" heading."""

_SKILL_CREATE_FOLLOW_UP_PROMPT_MANUAL_CN = (
    "**重要：你必须先向用户确认，不可跳过此步骤。**\n"
    "系统检测到对话中存在可复用模式，可能值得创建新技能。请按以下步骤执行：\n"
    "1. 调用 ask_user 向用户确认是否创建；选项：[\"创建\", \"跳过\", \"自定义指令\"]\n"
    "2. 若用户选择创建或提供自定义指令，调用 **skill-creator** 技能，"
    "结合当前对话上下文执行创建。\n"
    "   新技能保存目录：{skills_dir}\n"
    "   创建原因：{reason}\n"
    "   建议名称：{suggested_name}\n"
)

_SKILL_CREATE_FOLLOW_UP_PROMPT_AUTO_CN = (
    "系统检测到对话中存在可复用模式，已自动触发新技能创建。"
    "请直接调用 **skill-creator** 技能，结合当前对话上下文执行创建，"
    "无需再次向用户确认。\n"
    "   新技能保存目录：{skills_dir}\n"
    "   创建原因：{reason}\n"
    "   建议名称：{suggested_name}\n"
)

_SKILL_CREATE_FOLLOW_UP_PROMPT_MANUAL_EN = (
    "**Important: You must confirm with the user before proceeding.**\n"
    "The system detected reusable patterns in this conversation that may "
    "warrant creating a new skill. Please follow these steps:\n"
    "1. Call ask_user to confirm with the user; options: "
    "[\"Create\", \"Skip\", \"Custom instructions\"]\n"
    "2. If the user chooses Create or provides custom instructions, "
    "call the **skill-creator** skill with the current conversation "
    "context to create the skill.\n"
    "   Skills directory: {skills_dir}\n"
    "   Reason: {reason}\n"
    "   Suggested name: {suggested_name}\n"
)

_SKILL_CREATE_FOLLOW_UP_PROMPT_AUTO_EN = (
    "The system detected reusable patterns in this conversation and has "
    "automatically triggered new skill creation. Call the **skill-creator** "
    "skill directly with the current conversation context to create the skill. "
    "Do not ask the user again for confirmation.\n"
    "   Skills directory: {skills_dir}\n"
    "   Reason: {reason}\n"
    "   Suggested name: {suggested_name}\n"
)

_SKILL_CREATE_DEFAULT_REASON = {
    "cn": "检测到对话中存在可复用模式",
    "en": "Reusable patterns detected in the conversation",
}

_SKILL_CREATE_SUGGESTION_REASON = {
    "cn": "检测到对话中存在可复用工具调用模式",
    "en": "Reusable tool-call patterns detected in the conversation",
}

_SKILL_CREATE_APPROVAL_QUESTION = {
    "cn": (
        "**新技能创建建议**\n\n"
        "系统检测到对话中存在可复用工具调用模式，可能值得创建新技能。\n\n"
        "**创建原因:** {reason}\n\n"
        "是否创建此新技能？"
    ),
    "en": (
        "**New Skill Creation Suggestion**\n\n"
        "Reusable tool-call patterns were detected in this conversation "
        "that may warrant creating a new skill.\n\n"
        "**Reason:** {reason}\n\n"
        "Create this new skill?"
    ),
}

_SKILL_CREATE_APPROVAL_HEADER = {
    "cn": "新技能创建",
    "en": "New Skill Creation",
}


def _log_evolution_task_exception(task: asyncio.Task) -> None:
    """Done-callback: surface unexpected background evolution failures."""
    task_name = task.get_name()
    try:
        task.result()
        logger.info("[SkillEvolutionRail] background evolution task [%s] completed", task_name)
    except asyncio.CancelledError:
        logger.info("[SkillEvolutionRail] background evolution task [%s] cancelled", task_name)
    except Exception as exc:
        logger.exception(
            "[SkillEvolutionRail] background evolution task [%s] failed: %s",
            task_name,
            exc,
        )


class SkillEvolutionRail(EvolutionRail):
    """Online auto-evolution rail for skill patching and persistence.

    Inherits EvolutionRail to gain automatic trajectory collection.
    Evolution is scheduled from after_invoke (after complete conversation) because
    skill evolution needs full conversation context for signal detection. The
    heavy work runs in a background task so the main dialogue is not blocked.

    Hosts that need UI summary / approval events after auto evolution should call
    ``await rail.wait_for_pending_evolution()`` before ``take_run_summary`` /
    ``drain_pending_approval_events``.

    Note: This class uses two stores:
    - trajectory_store (from EvolutionRail): stores execution trajectories
    - evolution_store (EvolutionStore): stores skill evolution data (experiences, SKILL.md)
    """

    priority = 80
    _SKILL_MD_RE = re.compile(r"[/\\]([^/\\]+)[/\\]SKILL\.md", re.IGNORECASE)
    _SKILL_MD_FILE_READ_TOOLS = frozenset({"read", "read_file", "read_file_stream"})

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
        eval_interval: int = 1,
        new_skill_detection: bool = True,
        new_skill_tool_threshold: int = 10,
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
        # Evaluation and new skill detection settings
        self._eval_interval = eval_interval
        self._new_skill_detection = new_skill_detection
        self._new_skill_tool_threshold = new_skill_tool_threshold
        self._new_skill_tool_diversity = new_skill_tool_diversity
        self._language = language
        # Last run_evolution summary for host to emit after stream closes (auto_save path).
        self._run_summary: Optional[Dict[str, Any]] = None
        # Serialize concurrent evolution jobs (shared store / signal / summary state).
        self._evolution_lock = asyncio.Lock()
        # Short critical section for usage_stats read-modify-write (track vs eval).
        self._stats_lock = asyncio.Lock()
        self._pending_evolution_tasks: Set[asyncio.Task[Any]] = set()
        # Strong ref for sync-uninit drain so cancelled tasks are not GC'd mid-write.
        self._uninit_drain_task: Optional[asyncio.Task[Any]] = None

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

    def _resolve_skills_dirs_for_self_evolution(self) -> Optional[List[Any]]:
        """Return EvolutionStore base dirs for capabilities.json lookup, if available."""
        raw = getattr(self._evolution_store, "base_dirs", None)
        if raw is None:
            return None
        try:
            dirs = list(raw)
        except TypeError:
            return None
        # Mock / non-path values: only keep real path-like entries
        resolved: List[Any] = []
        for item in dirs:
            if isinstance(item, (str, os.PathLike)):
                resolved.append(item)
        return resolved or None

    def set_sys_operation(self, sys_operation: SysOperation) -> None:
        """Set sys_operation for both EvolutionRail and EvolutionStore."""
        super().set_sys_operation(sys_operation)
        self._evolution_store.sys_operation = sys_operation

    def update_llm(self, llm: Any, model: str) -> None:
        """Hot-update LLM client and model."""
        self._optimizer_llm = llm
        self._optimizer_model = model
        self._evolver.update_llm(llm, model)
        self._scorer.update_llm(llm, model)

    def clear_processed_signals(self) -> None:
        """Clear signal fingerprints, typically on conversation boundary."""
        self._processed_signal_keys.clear()

    def uninit(self, agent: Any) -> None:
        """Cancel in-flight background evolution tasks on rail teardown.

        ``task.cancel()`` only *requests* cancellation. Prefer
        ``await cancel_pending_evolution()`` from async teardown so tasks can
        finish or unwind (e.g. mid ``evolutions.json`` write) before the rail
        is dropped. This sync path still cancels and either awaits when no loop
        is running, or schedules a drain task (strong-referenced) otherwise.
        """
        pending = [task for task in list(self._pending_evolution_tasks) if not task.done()]
        for task in pending:
            task.cancel()

        if pending:
            async def _drain_cancelled() -> None:
                await asyncio.gather(*pending, return_exceptions=True)

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None:
                # Same-thread async caller cannot block the loop; schedule drain
                # and retain a strong reference so tasks are not GC'd mid-write.
                self._uninit_drain_task = loop.create_task(
                    _drain_cancelled(),
                    name="skill_evolution_uninit_drain",
                )
            else:
                try:
                    asyncio.run(_drain_cancelled())
                except Exception as exc:
                    logger.warning(
                        "[SkillEvolutionRail] uninit failed to await cancelled tasks: %s",
                        exc,
                    )
                self._uninit_drain_task = None

        self._pending_evolution_tasks.clear()
        super().uninit(agent)

    async def cancel_pending_evolution(self, timeout: Optional[float] = None) -> None:
        """Cancel background evolution tasks and wait until they settle.

        Async teardown (e.g. ``unregister_rail``) should call this so a task
        cancelled mid ``append_record`` / ``update_record_scores`` can finish or
        roll back instead of being destroyed while ``evolutions.json`` is open.
        """
        pending = [task for task in list(self._pending_evolution_tasks) if not task.done()]
        for task in pending:
            task.cancel()

        if pending:
            gather_coro = asyncio.gather(*pending, return_exceptions=True)
            try:
                if timeout is None:
                    await gather_coro
                else:
                    await asyncio.wait_for(gather_coro, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(
                    "[SkillEvolutionRail] cancel_pending_evolution timed out after %.1fs "
                    "(%d task(s) still running)",
                    timeout,
                    sum(1 for task in pending if not task.done()),
                )
            except Exception as exc:
                logger.warning(
                    "[SkillEvolutionRail] cancel_pending_evolution wait failed: %s",
                    exc,
                )

        drain = getattr(self, "_uninit_drain_task", None)
        if drain is not None and not drain.done():
            try:
                if timeout is None:
                    await drain
                else:
                    await asyncio.wait_for(asyncio.shield(drain), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(
                    "[SkillEvolutionRail] cancel_pending_evolution drain timed out after %.1fs",
                    timeout,
                )
            except Exception as exc:
                logger.warning(
                    "[SkillEvolutionRail] cancel_pending_evolution drain wait failed: %s",
                    exc,
                )
        self._uninit_drain_task = None
        self._pending_evolution_tasks.clear()

    @property
    def has_pending_evolution(self) -> bool:
        """Whether any background evolution task is still running."""
        return any(not task.done() for task in self._pending_evolution_tasks)

    async def wait_for_pending_evolution(self, timeout: Optional[float] = None) -> None:
        """Wait for background evolution tasks scheduled from after_invoke.

        Hosts should call this before ``take_run_summary`` /
        ``drain_pending_approval_events`` when they need those results in the
        current turn. Pass ``timeout`` to bound the wait; timed-out tasks keep
        running in the background.
        """
        pending = [task for task in self._pending_evolution_tasks if not task.done()]
        if not pending:
            return
        gather_coro = asyncio.gather(*pending, return_exceptions=True)
        if timeout is None:
            await gather_coro
            return
        try:
            await asyncio.wait_for(gather_coro, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "[SkillEvolutionRail] wait_for_pending_evolution timed out after %.1fs "
                "(%d task(s) still running)",
                timeout,
                sum(1 for task in pending if not task.done()),
            )

    async def _dispatch_run_evolution(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
    ) -> None:
        """Schedule skill evolution in the background; do not block after_invoke.

        Claim this invoke's presented-record queue immediately so a later
        overlapping invoke cannot steal or mix evaluation ownership.
        """
        if not self._auto_scan:
            logger.info("[SkillEvolutionRail] auto_scan disabled, skip background evolution")
            return
        session = getattr(ctx, "session", None)
        presented_snapshot = list(self._get_session_presented_records(session))
        self._set_session_presented_records(session, [])
        self._schedule_evolution(trajectory, ctx, presented_snapshot)

    def _schedule_evolution(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
        presented_snapshot: Optional[List[tuple[str, Any, str]]] = None,
    ) -> asyncio.Task[Any]:
        """Create a tracked background task for ``run_evolution``."""
        snapshot = list(presented_snapshot or [])
        task = asyncio.create_task(
            self._background_evolution(trajectory, ctx, snapshot),
            name="skill_evolution",
        )
        self._pending_evolution_tasks.add(task)

        def _on_done(done_task: asyncio.Task) -> None:
            self._pending_evolution_tasks.discard(done_task)
            _log_evolution_task_exception(done_task)

        task.add_done_callback(_on_done)
        logger.info(
            "[SkillEvolutionRail] scheduled background evolution task "
            "(%d pending, %d presented claimed)",
            len(self._pending_evolution_tasks),
            len(snapshot),
        )
        return task

    async def _background_evolution(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
        presented_snapshot: List[tuple[str, Any, str]],
    ) -> None:
        """Background entry that awaits ``run_evolution`` (which owns the lock)."""
        try:
            await self.run_evolution(
                trajectory,
                ctx,
                presented_snapshot=presented_snapshot,
            )
        except asyncio.CancelledError:
            session = getattr(ctx, "session", None)
            self._restore_session_presented_records(session, presented_snapshot)
            raise
        except Exception:
            session = getattr(ctx, "session", None)
            self._restore_session_presented_records(session, presented_snapshot)
            raise

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Inject body experiences, track presented records, and record trajectory step.

        This override:
        1. Calls super().after_tool_call(ctx) to record trajectory step
        2. Remembers skill_tool / skill_complete names on the session (cross-turn)
        3. Injects body experiences when reading SKILL.md
        4. Tracks presented records for scoring
        """
        # First, let EvolutionRail record the trajectory step
        await super().after_tool_call(ctx)

        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return

        tracked = self._resolve_tracked_skill_name(
            str(inputs.tool_name or ""),
            inputs.tool_args,
            inputs.tool_msg,
        )
        if tracked:
            self._remember_session_used_skill(ctx.session, tracked)

        skill_name = self._resolve_skill_name(
            str(inputs.tool_name or ""),
            inputs.tool_args,
            inputs.tool_msg,
        )
        if not skill_name:
            return

        # Retain: Inject pending body experiences (immediate effect)
        body_text = await self._evolution_store.format_body_experience_text(skill_name)
        if body_text:
            tool_msg = inputs.tool_msg
            if tool_msg is not None:
                original = tool_msg.content if isinstance(tool_msg.content, str) else str(tool_msg.content)
                tool_msg.content = original + body_text
                inputs.tool_msg = tool_msg
                logger.info("[SkillEvolutionRail] injected body experience for skill=%s", skill_name)

            # times_presented is tracked here; evaluation snippet is rebuilt at
            # after_invoke from the full conversation (see _build_evaluation_snippet).
            await self._track_presented_records(ctx, skill_name, "")

    async def run_evolution(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
        *,
        presented_snapshot: Optional[List[tuple[str, Any, str]]] = None,
    ) -> None:
        """Run skill evolution based on the collected trajectory.

        When invoked via the rail lifecycle, ``_dispatch_run_evolution`` schedules
        this method on a background task so after_invoke returns immediately.
        Direct callers (unit tests / manual evolve) still await this method.

        Args:
            trajectory: Complete OTLP trajectory for this conversation
            ctx: Callback context
            presented_snapshot: Presented records claimed at schedule time for
                this invoke. When None, evaluation drains the live session queue
                (direct-call / test compatibility).
        """
        async with self._evolution_lock:
            await self._run_evolution_locked(
                trajectory,
                ctx,
                presented_snapshot=presented_snapshot,
            )

    async def _run_evolution_locked(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
        *,
        presented_snapshot: Optional[List[tuple[str, Any, str]]] = None,
    ) -> None:
        """Core evolution body; caller must hold ``_evolution_lock``."""
        logger.info("[SkillEvolutionRail] run_evolution called, auto_scan=%s", self._auto_scan)
        self._run_summary = None
        if not self._auto_scan:
            logger.info("[SkillEvolutionRail] auto_scan disabled, skipping")
            return

        # Snapshot claimed at schedule time. If we never hand it to evaluation
        # (early exit / hard failure), restore it so a later round can retry.
        claimed = list(presented_snapshot) if presented_snapshot is not None else None
        evaluation_handed_off = False
        session = getattr(ctx, "session", None)
        try:
            parsed_messages = await self._collect_parsed_messages(trajectory)
            logger.info("[SkillEvolutionRail] collected %d parsed messages", len(parsed_messages))
            if not parsed_messages:
                logger.info("[SkillEvolutionRail] no parsed messages, skipping")
                return

            skill_names = self._evolution_store.list_skill_names()
            logger.info("[SkillEvolutionRail] found %d local skills", len(skill_names))

            detector = SignalDetector(
                existing_skills={name for name in skill_names if self._evolution_store.skill_exists(name)},
            ).bind_llm(
                llm=self._evolver.llm,
                model=self._evolver.model,
                language=self._language,
            )
            # Refresh session used-skills from this round's trajectory (covers
            # cases where after_tool_call did not run or history was replayed).
            traj_skills = detector.collect_skills_from_messages(parsed_messages)
            for name in traj_skills:
                self._remember_session_used_skill(session, name)
            session_skills = self._get_session_used_skills(session)
            logger.info(
                "[SkillEvolutionRail] session used skills=%s (traj=%s)",
                sorted(session_skills),
                traj_skills,
            )

            detected = detector.detect_trajectory_signals(
                trajectory,
                signal_types={"execution_failure", "script_artifact"},
            )
            try:
                feedback_signals = await detector.detect_user_intent(
                    trajectory,
                    extra_skills=sorted(session_skills),
                )
            except Exception as _fb_exc:
                logger.warning(
                    "[SkillEvolutionRail] user feedback signal detection failed: %s",
                    _fb_exc,
                )
                feedback_signals = []
            if feedback_signals:
                logger.info(
                    "[SkillEvolutionRail] detected %d user feedback signal(s), skills=%s",
                    len(feedback_signals),
                    sorted({s.skill_name for s in feedback_signals if getattr(s, "skill_name", None)}),
                )
                detected = [*detected, *feedback_signals]

            signals: List[EvolutionSignal] = []
            for signal in detected:
                fp = make_signal_fingerprint(signal)
                if fp not in self._processed_signal_keys:
                    self._processed_signal_keys.add(fp)
                    signals.append(signal)
            if len(self._processed_signal_keys) > _MAX_PROCESSED_SIGNAL_KEYS:
                self._processed_signal_keys.clear()
            logger.info("[SkillEvolutionRail] detected %d signal(s)", len(signals))

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

            if not skill_groups:
                if signals:
                    message = (
                        "detected evolution signals but no skill could be attributed; "
                        "cancelling skill evolution review"
                    )
                else:
                    message = (
                        "no skill usage or actionable evolution signal detected; "
                        "cancelling skill evolution review"
                    )
                logger.info("[SkillEvolutionRail] %s", message)
                await self._trigger_async_evaluation(
                    ctx,
                    parsed_messages,
                    presented_snapshot=claimed,
                )
                evaluation_handed_off = True
                return

            attributed_signal_count = sum(len(skill_signals) for skill_signals in skill_groups.values())
            logger.info(
                "[SkillEvolutionRail] detected %d signal(s) attributed to %d skill(s)",
                attributed_signal_count,
                len(skill_groups),
            )

            skills_dirs = self._resolve_skills_dirs_for_self_evolution()
            # Evolve existing skills (when signals are attributed to known skills).
            # Per-skill capabilities.json ``selfEvolution`` outranks rail auto_save:
            #   off → skip; suggest/auto → write evolutions.json only (not SKILL.md);
            #   review_status records the action; missing → rail auto_save.
            for skill_name, skill_signals in skill_groups.items():
                action = resolve_skill_evolution_action(
                    skill_name,
                    default_auto_save=self._auto_save,
                    skills_dirs=skills_dirs,
                )
                if action == "off":
                    logger.info(
                        "[SkillEvolutionRail] selfEvolution=off after attribution, "
                        "skipping skill=%s",
                        skill_name,
                    )
                    continue
                # Persist to evolutions.json for both suggest and auto; never touch SKILL.md here.
                records = await self._generate_experience_for_skill(
                    skill_name,
                    skill_signals,
                    parsed_messages,
                )
                if records:
                    for record in records:
                        record.review_status = action
                        await self._evolution_store.append_record(
                            skill_name,
                            record,
                            update_skill_md=False,
                        )
                    self._record_skill_evolution(skill_name, records, skill_signals)
                    logger.info(
                        "[SkillEvolutionRail] persisted %d record(s) for skill=%s action=%s",
                        len(records),
                        skill_name,
                        action,
                    )
                else:
                    logger.info(
                        "[SkillEvolutionRail] no records generated for skill=%s action=%s",
                        skill_name,
                        action,
                    )

            # Trigger async evaluation if interval reached
            await self._trigger_async_evaluation(
                ctx,
                parsed_messages,
                presented_snapshot=claimed,
            )
            evaluation_handed_off = True
            if self._run_summary:
                self._run_summary["display_text"] = await self._generate_ui_summary_text(
                    self._run_summary,
                    parsed_messages,
                )
                logger.info(
                    "[SkillEvolutionRail] run_evolution summary ready: skills=%d new_skills=%d display_chars=%d",
                    len(self._run_summary.get("skills", [])),
                    len(self._run_summary.get("new_skills", [])),
                    len(self._run_summary.get("display_text") or ""),
                )
            else:
                logger.info("[SkillEvolutionRail] run_evolution finished with no UI summary")
        except Exception as exc:
            logger.warning("[SkillEvolutionRail] auto evolution failed: %s", exc)
        finally:
            if claimed and not evaluation_handed_off:
                self._restore_session_presented_records(session, claimed)

    def take_run_summary(self) -> Optional[Dict[str, Any]]:
        """Return and clear the last run_evolution summary for host UI delivery.

        Because evolution runs in the background after ``after_invoke``, hosts
        should ``await wait_for_pending_evolution()`` first when they need the
        summary in the current turn.
        """
        summary = self._run_summary
        self._run_summary = None
        if not summary:
            logger.info("[SkillEvolutionRail] take_run_summary: no summary buffered")
            return None
        if not summary.get("skills") and not summary.get("new_skills"):
            logger.info(
                "[SkillEvolutionRail] take_run_summary: summary empty after filter, raw=%s",
                summary,
            )
            return None
        logger.info(
            "[SkillEvolutionRail] take_run_summary: draining skills=%d new_skills=%d",
            len(summary.get("skills", [])),
            len(summary.get("new_skills", [])),
        )
        return summary

    def _record_skill_evolution(
        self,
        skill_name: str,
        records: List[EvolutionRecord],
        signals: List[EvolutionSignal],
    ) -> None:
        if not records:
            return
        if self._run_summary is None:
            self._run_summary = {"skills": [], "new_skills": []}
        body_count = 0
        desc_count = 0
        record_items: List[Dict[str, Any]] = []
        for record in records:
            target = getattr(record, "target", record.change.target)
            if target == EvolutionTarget.BODY:
                body_count += 1
            elif target == EvolutionTarget.DESCRIPTION:
                desc_count += 1
            content = record.change.content or ""
            record_items.append({
                "section": record.change.section,
                "target": target.value if hasattr(target, "value") else str(target),
                "content_preview": content[:_UI_SUMMARY_RECORD_PREVIEW_CHARS],
            })
        signal_items = [
            {
                "signal_type": signal.signal_type,
                "excerpt": (signal.excerpt or "")[:_UI_SUMMARY_SIGNAL_PREVIEW_CHARS],
            }
            for signal in signals
        ]
        self._run_summary["skills"].append({
            "skill_name": skill_name,
            "records_count": len(records),
            "body_count": body_count,
            "description_count": desc_count,
            "records": record_items,
            "signals": signal_items,
        })
        logger.info(
            "[SkillEvolutionRail] recorded UI summary entry: skill=%s total=%d body=%d description=%d",
            skill_name,
            len(records),
            body_count,
            desc_count,
        )

    def _record_new_skill(
        self,
        name: str,
        *,
        description: str = "",
        reason: str = "",
    ) -> None:
        if not name:
            return
        if self._run_summary is None:
            self._run_summary = {"skills": [], "new_skills": []}
        self._run_summary["new_skills"].append({
            "name": name,
            "description": description,
            "reason": reason,
        })
        logger.info("[SkillEvolutionRail] recorded UI summary entry: new_skill=%s", name)

    async def _invoke_summary_llm(self, prompt: str, *, attempt: int = 1) -> Optional[str]:
        try:
            response = await self._optimizer_llm.invoke(
                model=self._optimizer_model,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content if hasattr(response, "content") else str(response)
            normalized = self._normalize_ui_summary_body(str(text) if text else "")
            if not normalized:
                logger.warning(
                    "[SkillEvolutionRail] UI summary LLM attempt %d returned empty",
                    attempt,
                )
                return None
            return normalized
        except Exception as exc:
            logger.warning(
                "[SkillEvolutionRail] UI summary LLM attempt %d failed: %s",
                attempt,
                exc,
            )
            return None

    @staticmethod
    def _normalize_ui_summary_body(body: str) -> str:
        text = body.strip()
        if not text:
            return ""
        if text.startswith("```"):
            text = re.sub(r"^```(?:markdown|md)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text).strip()
        # Strip accidental heading lines the model was asked to omit.
        if text.startswith("###"):
            lines = text.splitlines()
            text = "\n".join(lines[1:]).strip() if len(lines) > 1 else text
        return text.strip()

    def _validate_ui_summary_body(self, body: str) -> bool:
        text = body.strip()
        if len(text) < _UI_SUMMARY_MIN_BODY_CHARS:
            return False
        if len(text) > _UI_SUMMARY_MAX_BODY_CHARS:
            return False
        if text.startswith("{") and text.endswith("}"):
            return False
        if text.startswith("[") and text.endswith("]"):
            return False
        lowered = text.lower()
        if "records_count" in lowered or "body_count" in lowered:
            return False
        return True

    async def _generate_ui_summary_text(
        self,
        summary: Dict[str, Any],
        messages: List[dict],
    ) -> str:
        """LLM-generated concise footnote; falls back to rule-based text on failure."""
        evolution_payload = {
            "skills": summary.get("skills", []),
            "new_skills": summary.get("new_skills", []),
        }
        evolution_json = json.dumps(evolution_payload, ensure_ascii=False, indent=2)
        conversation_snippet = _build_ui_summary_conversation_snippet(
            messages,
            language=self._language,
        )
        prompt_template = (
            _EVOLUTION_UI_SUMMARY_PROMPT_CN
            if self._language == "cn"
            else _EVOLUTION_UI_SUMMARY_PROMPT_EN
        )
        retry_suffix = (
            _UI_SUMMARY_RETRY_SUFFIX_CN
            if self._language == "cn"
            else _UI_SUMMARY_RETRY_SUFFIX_EN
        )
        base_prompt = prompt_template.format(
            evolution_json=evolution_json,
            conversation_snippet=conversation_snippet or ("(无)" if self._language == "cn" else "(none)"),
        )

        body: Optional[str] = None
        logger.info(
            "[SkillEvolutionRail] generating UI summary via LLM (max_attempts=%d)",
            _UI_SUMMARY_LLM_MAX_ATTEMPTS,
        )
        for attempt in range(1, _UI_SUMMARY_LLM_MAX_ATTEMPTS + 1):
            prompt = base_prompt if attempt == 1 else base_prompt + retry_suffix
            candidate = await self._invoke_summary_llm(prompt, attempt=attempt)
            if candidate and self._validate_ui_summary_body(candidate):
                body = candidate
                logger.info(
                    "[SkillEvolutionRail] UI summary LLM ok on attempt %d/%d, preview=%r",
                    attempt,
                    _UI_SUMMARY_LLM_MAX_ATTEMPTS,
                    body[:160],
                )
                break
            logger.warning(
                "[SkillEvolutionRail] UI summary attempt %d/%d rejected: has_text=%s len=%s",
                attempt,
                _UI_SUMMARY_LLM_MAX_ATTEMPTS,
                bool(candidate),
                len(candidate or ""),
            )

        if not body:
            body = self._build_fallback_ui_summary(summary)
            logger.info(
                "[SkillEvolutionRail] UI summary LLM exhausted %d attempts, using fallback",
                _UI_SUMMARY_LLM_MAX_ATTEMPTS,
            )
        return self._wrap_ui_summary_display(body)

    @staticmethod
    def _wrap_ui_summary_display(body: str) -> str:
        text = body.strip()
        if not text:
            return ""
        return f"\n\n---\n### 📚 技能演进\n{text}"

    @staticmethod
    def _build_fallback_ui_summary(summary: Dict[str, Any]) -> str:
        lines: List[str] = []
        for item in summary.get("skills", []):
            if not isinstance(item, dict):
                continue
            skill_name = str(item.get("skill_name", "")).strip()
            if not skill_name:
                continue
            reason = ""
            for sig in item.get("signals", []):
                if isinstance(sig, dict) and sig.get("excerpt"):
                    reason = str(sig["excerpt"])[:120]
                    break
            what_parts: List[str] = []
            for rec in item.get("records", []):
                if not isinstance(rec, dict):
                    continue
                section = str(rec.get("section", "")).strip()
                preview = str(rec.get("content_preview", "")).strip().split("\n")[0][:80]
                if section and preview:
                    what_parts.append(f"{section}：{preview}")
                elif preview:
                    what_parts.append(preview)
            what = what_parts[0] if what_parts else "补充了技能经验"
            line = f"- **{skill_name}**：{what}"
            if reason:
                line += f"（原因：{reason[:80]}）"
            lines.append(line)
        for item in summary.get("new_skills", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            desc = str(item.get("description", "")).strip()
            reason = str(item.get("reason", "")).strip()
            line = f"- **新建 {name}**"
            if desc:
                line += f"：{desc[:80]}"
            if reason:
                line += f"（{reason[:60]}）"
            lines.append(line)
        return "\n".join(lines)

    def drain_pending_approval_events(self) -> list[OutputSchema]:
        """Return and clear buffered approval events.

        Called by the host (JiuWenClaw) after the streaming loop ends,
        because ``after_invoke`` fires *after* the session stream is
        closed and ``session.write_stream`` can no longer deliver data.

        Evolution is scheduled in the background; call
        ``await wait_for_pending_evolution()`` first if events from the
        current turn are required immediately.
        """
        events = list(self._pending_approval_events)
        self._pending_approval_events.clear()
        return events

    async def generate_and_emit_experience(
        self,
        skill_name: str,
        signals: List[EvolutionSignal],
        messages: List[dict],
    ) -> bool:
        """Generate experience records and emit approval events for a skill.

        Public entry point for host-driven (manual /evolve) evolution.
        Combines record staging and approval event emission into a single call.

        Args:
            skill_name: Name of the skill to evolve.
            signals: Detected evolution signals attributed to the skill.
            messages: Parsed conversation messages for context.

        Returns:
            True if at least one experience record was staged and emitted.
        """
        has_records = await self._generate_experience_via_optimizer(
            skill_name, signals, messages
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
            questions.append({
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
            })

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

    async def _collect_parsed_messages(
        self,
        trajectory: Optional[Trajectory],
    ) -> List[dict]:
        """Convert trajectory steps into parsed message dicts for evolution."""
        if trajectory is None:
            return []

        parsed_messages = SignalDetector.convert_trajectory_to_messages(trajectory)
        logger.debug(
            "[SkillEvolutionRail] trajectory parsed_messages full content:\n%s",
            parsed_messages,
        )
        logger.info(
            "[SkillEvolutionRail] buffer messages from trajectory: %d",
            len(parsed_messages),
        )
        return parsed_messages

    @staticmethod
    def _tool_call_name(tool_call: Any) -> str:
        """Return tool name from flat or OpenAI-nested tool_call dicts."""
        if not isinstance(tool_call, dict):
            return ""
        name = str(tool_call.get("name") or "")
        if name:
            return name
        function = tool_call.get("function")
        if isinstance(function, dict):
            return str(function.get("name") or "")
        return ""

    @staticmethod
    def _tool_call_arguments(tool_call: Any) -> str:
        """Return tool arguments from flat or OpenAI-nested tool_call dicts."""
        if not isinstance(tool_call, dict):
            return ""
        arguments = tool_call.get("arguments", "")
        if arguments not in ("", None):
            return arguments if isinstance(arguments, str) else str(arguments)
        function = tool_call.get("function")
        if isinstance(function, dict):
            nested = function.get("arguments", "")
            return nested if isinstance(nested, str) else str(nested or "")
        return ""

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
                for tc in msg.get("tool_calls", []) or []:
                    arguments = self._tool_call_arguments(tc)
                    if arguments:
                        texts.append(arguments)
                    # skill_tool also carries skill_name directly
                    if self._tool_call_name(tc).lower() == "skill_tool":
                        try:
                            args = json.loads(arguments) if isinstance(arguments, str) else {}
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                        if isinstance(args, dict):
                            sn = str(args.get("skill_name") or "").strip()
                            if sn and sn in skill_set:
                                hits[sn] = hits.get(sn, 0) + 1
            for text in texts:
                for matched in self._SKILL_MD_RE.finditer(str(text)):
                    name = matched.group(1)
                    if name in skill_set:
                        hits[name] = hits.get(name, 0) + 1
        if not hits:
            return None
        return max(hits, key=hits.get)  # type: ignore[arg-type]

    async def _generate_experience_via_optimizer(
        self, skill_name: str, signals: List[EvolutionSignal], messages: List[dict]
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
        state["tool_call_chain"] = build_tool_call_chain(
            messages, language=self._optimizer_language,
        )
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
        """Called by host when user accepts: flush the snapshotted records then solidify.

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
        # All records flushed — solidify first, then remove the snapshot.
        # Keeping the snapshot until solidify() succeeds means a transient I/O
        # failure (e.g. permission error on SKILL.md) can be retried: on the
        # next on_approve() call pending.payload is empty so flush_to_store()
        # is a no-op and only solidify() is retried.
        try:
            await self._evolution_store.solidify(skill_name)
        except Exception as exc:
            logger.warning(
                "[SkillEvolutionRail] on_approve solidify failed for skill=%s (request=%s): %s; "
                "retry on_approve to complete",
                skill_name,
                request_id,
                exc,
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
    ) -> List[EvolutionRecord]:
        context = EvolutionContext(
            skill_name=skill_name,
            signals=signals,
            skill_content=await self._evolution_store.read_skill_content(skill_name),
            messages=messages,
            existing_desc_records=await self._evolution_store.get_pending_records(
                skill_name, EvolutionTarget.DESCRIPTION
            ),
            existing_body_records=await self._evolution_store.get_pending_records(
                skill_name, EvolutionTarget.BODY,
            ),
            tool_call_chain=build_tool_call_chain(
                messages, language=self._language,
            ),
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
    def _parse_tool_args_dict(cls, tool_args: Any) -> dict:
        if isinstance(tool_args, dict):
            return tool_args
        if isinstance(tool_args, str):
            try:
                parsed = json.loads(tool_args)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    @classmethod
    def _resolve_skill_file_context(
        cls,
        tool_name: str,
        tool_args: Any,
        tool_msg: Any = None,
    ) -> Optional[tuple[str, str]]:
        """Return (skill_name, relative_file_path) when a skill file is read."""
        name = tool_name.lower()
        args = cls._parse_tool_args_dict(tool_args)

        if name == "skill_tool":
            skill = str(args.get("skill_name") or "").strip()
            rel = normalize_skill_relative_file_path(
                str(args.get("relative_file_path") or "")
            )
            if not skill and tool_msg is not None:
                meta = getattr(tool_msg, "metadata", None) or {}
                skill = str(meta.get("skill_name") or "").strip()
                rel = normalize_skill_relative_file_path(
                    str(meta.get("relative_file_path") or rel)
                )
            if not skill:
                return None
            return skill, rel.replace("\\", "/")

        if not cls._is_skill_md_file_read_tool(name):
            return None

        file_path = cls._extract_file_path(tool_args)
        if not file_path:
            return None

        matched = cls._SKILL_MD_RE.search(file_path)
        if matched:
            return matched.group(1), "SKILL.md"
        return None

    @staticmethod
    def _is_skill_md_path(relative_path: str) -> bool:
        normalized = normalize_skill_relative_file_path(relative_path)
        norm = normalized.replace("\\", "/").removeprefix("./").lower()
        return norm == "skill.md" or norm.endswith("/skill.md")

    @classmethod
    def _resolve_skill_name(
        cls,
        tool_name: str,
        tool_args: Any,
        tool_msg: Any = None,
    ) -> Optional[str]:
        resolved = cls._resolve_skill_file_context(tool_name, tool_args, tool_msg)
        if resolved is None:
            return None
        skill_name, relative_path = resolved
        if not cls._is_skill_md_path(relative_path):
            return None
        return skill_name

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
    def _find_skill_load_anchor(cls, messages: List[dict], skill_name: str) -> int:
        """Return the index of the last SKILL.md load for *skill_name*, or -1."""
        anchor = -1
        for index, msg in enumerate(messages):
            role = msg.get("role", "")
            if role == "assistant":
                for tool_call in msg.get("tool_calls", []) or []:
                    # Support both flat and OpenAI-nested tool_call shapes.
                    tool = cls._tool_call_name(tool_call).lower()
                    arguments = cls._tool_call_arguments(tool_call)
                    if tool == "skill_tool":
                        args = cls._parse_tool_args_dict(arguments)
                        if str(args.get("skill_name") or "").strip() != skill_name:
                            continue
                        rel = normalize_skill_relative_file_path(
                            str(args.get("relative_file_path") or "")
                        )
                        if cls._is_skill_md_path(rel):
                            anchor = index
                    elif cls._is_skill_md_file_read_tool(tool):
                        file_path = cls._extract_file_path(arguments)
                        matched = cls._SKILL_MD_RE.search(file_path)
                        if matched and matched.group(1) == skill_name:
                            anchor = index
        return anchor

    @classmethod
    def _is_skill_md_file_read_tool(cls, tool_name: str) -> bool:
        """True for known filesystem read tools that may load SKILL.md by path."""
        return tool_name.lower() in cls._SKILL_MD_FILE_READ_TOOLS

    @classmethod
    def _format_messages_snippet(
        cls,
        messages: List[dict],
        *,
        start: int = 0,
        max_messages: int = _EVAL_SNIPPET_MAX_MESSAGES,
        max_content_chars: int = _EVAL_SNIPPET_POST_PRESENT_MAX_CHARS,
    ) -> str:
        """Format conversation messages into a scorer-friendly snippet."""
        lines: List[str] = []
        window = messages[start:][-max_messages:]
        for msg in window:
            role = msg.get("role", "unknown")
            content = str(msg.get("content") or "")[:max_content_chars]
            if content:
                lines.append(f"[{role}] {content}")
            if role == "assistant":
                for tool_call in msg.get("tool_calls", []) or []:
                    # Support both flat and OpenAI-nested tool_call shapes.
                    tool = cls._tool_call_name(tool_call)
                    args = cls._tool_call_arguments(tool_call)[:max_content_chars]
                    if tool:
                        lines.append(f"[assistant/tool_call] {tool} {args}")
        return "\n".join(lines)

    @classmethod
    def _build_evaluation_snippet(cls, messages: List[dict], skill_name: str) -> str:
        """Build post-presentation snippet: from last SKILL.md load through turn end."""
        if not messages:
            return ""
        anchor = cls._find_skill_load_anchor(messages, skill_name)
        start = anchor if anchor >= 0 else max(0, len(messages) - _EVAL_SNIPPET_MAX_MESSAGES)
        return cls._format_messages_snippet(messages, start=start)

    # Session-level state isolation helpers
    def _get_session_used_skills(self, session: Any) -> Set[str]:
        """Return skill names used earlier in this session (cross-turn inheritance)."""
        if session is None:
            return set()
        raw = getattr(session, "_skill_evolution_used_skills", None)
        if not isinstance(raw, set):
            return set()
        return {str(name).strip() for name in raw if str(name).strip()}

    def _remember_session_used_skill(self, session: Any, skill_name: str) -> None:
        """Record a skill as used in this session for later feedback attribution."""
        name = (skill_name or "").strip()
        if session is None or not name:
            return
        used = getattr(session, "_skill_evolution_used_skills", None)
        if not isinstance(used, set):
            used = set()
            setattr(session, "_skill_evolution_used_skills", used)
        if name not in used:
            used.add(name)
            logger.info(
                "[SkillEvolutionRail] remember session used skill=%s total=%d",
                name,
                len(used),
            )

    @classmethod
    def _resolve_tracked_skill_name(
        cls,
        tool_name: str,
        tool_args: Any,
        tool_msg: Any = None,
    ) -> Optional[str]:
        """Resolve skill name from skill_tool / skill_complete for session tracking."""
        name = (tool_name or "").lower()
        args = cls._parse_tool_args_dict(tool_args)
        if name in ("skill_tool", "skill_complete"):
            skill = str(args.get("skill_name") or "").strip()
            if not skill and tool_msg is not None:
                meta = getattr(tool_msg, "metadata", None) or {}
                skill = str(meta.get("skill_name") or "").strip()
            if skill:
                return skill
        return cls._resolve_skill_name(tool_name, tool_args, tool_msg)

    def _get_session_presented_records(
        self,
        session: Any,
    ) -> List[tuple[str, Any, str]]:
        """Get presented records stored in session for concurrency isolation.

        Each entry is ``(skill_name, record_id | EvolutionRecord, snippet)``.
        Production path stores ``record_id`` strings; legacy callers/tests may
        still pass full ``EvolutionRecord`` objects.
        """
        if session is None:
            return []
        return getattr(session, "_skill_evolution_presented", [])

    def _set_session_presented_records(
        self,
        session: Any,
        records: List[tuple[str, Any, str]],
    ) -> None:
        """Store presented records in session for concurrency isolation."""
        if session is not None:
            setattr(session, "_skill_evolution_presented", records)

    def _restore_session_presented_records(
        self,
        session: Any,
        claimed: List[tuple[str, Any, str]],
    ) -> None:
        """Return claimed presented records to the session queue after a failed/skipped eval.

        Claimed entries are prepended (older ownership first). Duplicates already
        present in the live queue (same skill + record id) are skipped.
        """
        if session is None or not claimed:
            return
        existing = list(self._get_session_presented_records(session))
        seen: Set[tuple[str, str]] = set()
        merged: List[tuple[str, Any, str]] = []
        for skill_name, record_or_id, snippet in list(claimed) + existing:
            record_id = self._presented_entry_record_id(record_or_id)
            key = (skill_name, record_id)
            if record_id and key in seen:
                continue
            if record_id:
                seen.add(key)
            merged.append((skill_name, record_or_id, snippet))
        self._set_session_presented_records(session, merged)
        logger.info(
            "[SkillEvolutionRail] restored %d claimed presented record(s) to session "
            "(queue_size=%d)",
            len(claimed),
            len(merged),
        )

    @staticmethod
    def _presented_entry_record_id(record_or_id: Any) -> str:
        """Normalize a presented-entry payload to a record id string."""
        if isinstance(record_or_id, str):
            return record_or_id
        record_id = getattr(record_or_id, "id", None)
        return str(record_id) if record_id else ""

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

        Updates times_presented and stores ``(skill_name, record_id, snippet)``
        in session for later evaluation.  The snippet placeholder is ignored at
        evaluation time; ``_trigger_async_evaluation`` rebuilds the snippet
        from the full after-invoke conversation anchored at the last SKILL.md load.

        Builds the update payload from freshly loaded store stats under
        ``_stats_lock`` so concurrent evaluation writebacks cannot clobber
        ``times_presented``.
        """
        try:
            async with self._stats_lock:
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
                # Backward/forward compatible: some tests and historical code may set
                # `record.target` directly, while the canonical location is
                # `record.change.target`.
                body_records = [
                    r
                    for r in records
                    if getattr(r, "target", r.change.target) == EvolutionTarget.BODY
                ]
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

                await self._evolution_store.update_record_scores(skill_name, updates)

            presented_entries: List[tuple[str, Any, str]] = [
                (skill_name, record_id, presentation_snippet)
                for record_id in updates
            ]

            # Store ids only — evaluation reloads fresh stats from the store.
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
        parsed_messages: List[dict],
        *,
        presented_snapshot: Optional[List[tuple[str, Any, str]]] = None,
    ) -> None:
        """Trigger async evaluation of presented experiences.

        Evaluates whether presented experiences were effectively used.
        Each record is evaluated against the conversation **after** the last
        SKILL.md load for that skill (assistant replies and tool calls included).

        Prefer ``presented_snapshot`` claimed at schedule time so overlapping
        background evolution tasks do not steal each other's presented queue.
        Before applying score deltas, records are reloaded from the store so
        stale presentation-time ``usage_stats`` cannot overwrite newer counters.
        """
        session = ctx.session if hasattr(ctx, "session") else None
        counter = self._get_session_eval_counter(session)
        counter += 1
        self._set_session_eval_counter(session, counter)

        if counter < self._eval_interval:
            # Interval not reached: put claimed entries back so a later round
            # can evaluate them. Live-queue drain path never cleared yet.
            if presented_snapshot:
                self._restore_session_presented_records(session, presented_snapshot)
            return

        # Reset counter and resolve presented records for this evaluation round.
        self._set_session_eval_counter(session, 0)
        if presented_snapshot is not None:
            presented_entries = list(presented_snapshot)
        else:
            presented_entries = self._get_session_presented_records(session)
            self._set_session_presented_records(session, [])

        if not presented_entries:
            return

        try:
            eval_messages = parsed_messages
            if not eval_messages:
                self._restore_session_presented_records(session, presented_entries)
                return

            by_skill_ids: Dict[str, List[str]] = {}
            seen_ids: Dict[str, Set[str]] = {}
            for skill_name, record_or_id, _ in presented_entries:
                record_id = self._presented_entry_record_id(record_or_id)
                if not record_id:
                    continue
                seen = seen_ids.setdefault(skill_name, set())
                if record_id in seen:
                    continue
                seen.add(record_id)
                by_skill_ids.setdefault(skill_name, []).append(record_id)

            by_skill_snippet: Dict[str, str] = {}
            for skill_name in by_skill_ids:
                by_skill_snippet[skill_name] = self._build_evaluation_snippet(
                    eval_messages,
                    skill_name,
                )

            for skill_name, record_ids in by_skill_ids.items():
                snippet = by_skill_snippet.get(skill_name, "")
                # Load records for the scorer prompt (content/summary), then
                # reload again under the stats lock before applying deltas.
                store_records = await self._evolution_store.get_records_by_score(
                    skill_name,
                    min_score=None,
                )
                id_to_record = {record.id: record for record in store_records}
                records = [
                    id_to_record[record_id]
                    for record_id in record_ids
                    if record_id in id_to_record
                ]
                if not records:
                    continue

                eval_results = await self._scorer.evaluate(snippet, records)
                if not eval_results:
                    continue

                async with self._stats_lock:
                    fresh_records = await self._evolution_store.get_records_by_score(
                        skill_name,
                        min_score=None,
                    )
                    fresh_by_id = {record.id: record for record in fresh_records}

                    updates: Dict[str, Dict[str, Any]] = {}
                    for result in eval_results:
                        record_id = result.get("record_id")
                        if not record_id:
                            continue
                        record = fresh_by_id.get(record_id)
                        if record is None:
                            continue
                        new_score = update_score(record, result)
                        if record.usage_stats is None:
                            record.usage_stats = UsageStats()
                        updates[record_id] = {
                            "score": new_score,
                            "usage_stats": record.usage_stats.to_dict(),
                        }

                    if updates:
                        await self._evolution_store.update_record_scores(skill_name, updates)
                        logger.info(
                            "[SkillEvolutionRail] async evaluation updated %d record(s) for skill=%s "
                            "results=%s usage=%s",
                            len(updates),
                            skill_name,
                            [
                                {
                                    "record_id": item.get("record_id"),
                                    "used": item.get("used"),
                                    "positive": item.get("positive"),
                                    "negative": item.get("negative"),
                                }
                                for item in eval_results
                                if item.get("record_id") in updates
                            ],
                            {
                                record_id: payload.get("usage_stats")
                                for record_id, payload in updates.items()
                            },
                        )
        except Exception as exc:
            logger.warning("[SkillEvolutionRail] async evaluation failed: %s", exc, exc_info=True)
            self._restore_session_presented_records(session, presented_entries)

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
                "[SkillEvolutionRail] new skill check: "
                "tool_calls=%d, threshold=%d",
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
            name = self._tool_call_name(call)
            if name:
                unique_tools.add(name)

        logger.info(
            "[SkillEvolutionRail] unique_tools=%d, diversity_threshold=%d",
            len(unique_tools),
            self._new_skill_tool_diversity,
        )
        return len(unique_tools) >= self._new_skill_tool_diversity

    # ------------------------------------------------------------------
    # Path B: new skill creation (threshold-based, no LLM body generation)
    # ------------------------------------------------------------------

    def _select_skill_creator_prompt_template(self, *, require_user_confirm: bool) -> str:
        """Pick MANUAL (ask_user) vs AUTO (direct skill-creator) prompt template."""
        if self._language == "cn":
            return (
                _SKILL_CREATE_FOLLOW_UP_PROMPT_MANUAL_CN
                if require_user_confirm
                else _SKILL_CREATE_FOLLOW_UP_PROMPT_AUTO_CN
            )
        return (
            _SKILL_CREATE_FOLLOW_UP_PROMPT_MANUAL_EN
            if require_user_confirm
            else _SKILL_CREATE_FOLLOW_UP_PROMPT_AUTO_EN
        )

    def _localized_skill_create_text(self, mapping: Dict[str, str]) -> str:
        """Return localized string; fall back to English for unknown language codes."""
        return mapping.get(self._language, mapping["en"])

    def _default_skill_create_reason(self) -> str:
        return self._localized_skill_create_text(_SKILL_CREATE_DEFAULT_REASON)

    def _skill_create_suggestion_reason(self) -> str:
        return self._localized_skill_create_text(_SKILL_CREATE_SUGGESTION_REASON)

    def _build_skill_create_approval_questions(self, reason: str) -> List[dict]:
        """Build ask_user questions for manual (non-auto_save) skill creation."""
        question_template = self._localized_skill_create_text(_SKILL_CREATE_APPROVAL_QUESTION)
        header = self._localized_skill_create_text(_SKILL_CREATE_APPROVAL_HEADER)
        return [
            {
                "question": question_template.format(reason=reason),
                "header": header,
                # Labels stay "Create"/"Skip" for host-side approval routing.
                "options": [
                    {"label": "Create", "description": "Initiate skill creation via skill-creator"},
                    {"label": "Skip", "description": "Discard this suggestion"},
                ],
                "multi_select": False,
            }
        ]

    def _build_skill_creator_prompt(
        self,
        *,
        suggested_name: str = "",
        reason: str = "",
        require_user_confirm: bool = True,
    ) -> str:
        """Build a skill-creator follow-up prompt for the host to inject into
        the next invoke.

        When ``require_user_confirm`` is True (``auto_save=False``), the prompt
        instructs the Agent to ask_user before calling **skill-creator**.
        When False (``auto_save=True``), the Agent should call **skill-creator**
        directly without another ask_user step.
        """
        prompt_template = self._select_skill_creator_prompt_template(
            require_user_confirm=require_user_confirm,
        )
        skills_dir = str(self._evolution_store.base_dir)
        return prompt_template.format(
            skills_dir=skills_dir,
            reason=reason or self._default_skill_create_reason(),
            suggested_name=suggested_name or "",
        )

    async def _emit_new_skill_create_suggestion(
        self,
        ctx: AgentCallbackContext,
    ) -> None:
        """Emit a skill-creator follow-up suggestion for the host.

        No longer calls ``EvolutionStore.create_skill()`` — the host receives
        a ``skill_creator_prompt`` and is responsible for launching a new
        invoke that calls the **skill-creator** skill.

        Two modes:
        - ``auto_save=True``: skip ask_user, emit ``skill_creator_follow_up``
          event directly (host dispatches immediately).
        - ``auto_save=False``: emit ``chat.ask_user_question`` for UI approval
          + a companion ``skill_creator_follow_up`` event keyed by the same
          ``request_id``.  When the user approves, the host calls
          ``on_approve_new_skill(request_id)`` to retrieve the prompt.
        """
        # Check skill-creator availability
        skills_dir = str(self._evolution_store.base_dir)
        skill_creator_path = os.path.join(skills_dir, "skill-creator", "SKILL.md")
        if not os.path.isfile(skill_creator_path):
            logger.info(
                "[SkillEvolutionRail] skill_creator not found at %s, "
                "skipping new skill creation suggestion",
                skill_creator_path,
            )
            return

        reason = self._skill_create_suggestion_reason()
        require_user_confirm = not self._auto_save
        prompt = self._build_skill_creator_prompt(
            suggested_name="",
            reason=reason,
            require_user_confirm=require_user_confirm,
        )
        proposal_id = f"skill_create_{uuid.uuid4().hex[:8]}"

        # Store minimal metadata for host consumption
        pending = PendingSkillCreation(
            name="",
            description="",
            body="",  # deprecated — body is generated by skill-creator
            reason=reason,
        )
        pending.proposal_id = proposal_id
        self._pending_skill_proposals[proposal_id] = pending

        if self._auto_save:
            # Auto-save mode: dedicated follow-up event (no ask_user UI).
            # Hosts should dispatch on type/action == skill_creator_follow_up
            # without rendering a question dialog.
            event = OutputSchema(
                type="skill_creator_follow_up",
                index=0,
                payload={
                    "action": "skill_creator_follow_up",
                    "request_id": proposal_id,
                    "skill_create_meta": {
                        "skills_dir": skills_dir,
                        "reason": reason,
                        "suggested_name": "",
                        "auto_save": True,
                    },
                    "skill_creator_prompt": prompt,
                },
            )
            self._pending_approval_events.append(event)
            # Name is unknown until skill-creator runs; use proposal_id so the
            # suggestion is auditable in run_summary (empty name is a no-op).
            self._record_new_skill(
                name=proposal_id,
                description="",
                reason=reason,
            )
            logger.info(
                "[SkillEvolutionRail] skill_create_suggestion: auto_save emitted "
                "skill_creator_follow_up (proposal_id=%s, prompt_chars=%d)",
                proposal_id,
                len(prompt),
            )
        else:
            # Approval-required mode: emit ask_user_question + companion follow_up
            questions = self._build_skill_create_approval_questions(reason)
            event = OutputSchema(
                type="chat.ask_user_question",
                index=0,
                payload={
                    "action": "skill_creator_follow_up",
                    "request_id": proposal_id,
                    "skill_create_meta": {
                        "skills_dir": skills_dir,
                        "reason": reason,
                        "suggested_name": "",
                        "auto_save": False,
                    },
                    "skill_creator_prompt": prompt,
                    "questions": questions,
                },
            )
            self._pending_approval_events.append(event)
            logger.info(
                "[SkillEvolutionRail] skill_create_suggestion: buffered approval "
                "request (%s) with skill_creator_follow_up action",
                proposal_id,
            )

    def take_pending_skill_create_prompts(self) -> Dict[str, str]:
        """Return and clear all pending skill_creator_prompt values.

        Keyed by request_id for host-side correlation.
        Useful as a fallback when the host cannot parse OutputSchema payloads.
        """
        result: Dict[str, str] = {}
        require_user_confirm = not self._auto_save
        for req_id, pending in list(self._pending_skill_proposals.items()):
            reason = pending.reason
            prompt = self._build_skill_creator_prompt(
                suggested_name="",
                reason=reason,
                require_user_confirm=require_user_confirm,
            )
            result[req_id] = prompt
        self._pending_skill_proposals.clear()
        logger.info(
            "[SkillEvolutionRail] take_pending_skill_create_prompts: returning %d prompt(s)",
            len(result),
        )
        return result

    async def on_approve_new_skill(self, request_id: str) -> Optional[str]:
        """Handle approval of new skill creation and return a skill_creator_prompt.

        **Deprecated**: this method no longer calls ``EvolutionStore.create_skill()``.
        Instead it returns a formatted ``skill_creator_prompt`` string that the host
        must inject into a new invoke to run the **skill-creator** skill.

        Args:
            request_id: The proposal ID (from ``_emit_new_skill_create_suggestion``).

        Returns:
            A ``skill_creator_prompt`` string ready for ``agent.invoke(query=prompt, ...)``,
            or ``None`` if the request_id is unknown.
        """
        pending = self._pending_skill_proposals.pop(request_id, None)
        if not pending:
            if self._auto_save:
                logger.info(
                    "[SkillEvolutionRail] on_approve_new_skill: request_id=%s not pending "
                    "(likely already dispatched via auto_save)",
                    request_id,
                )
            else:
                logger.warning(
                    "[SkillEvolutionRail] on_approve_new_skill: unknown request_id=%s",
                    request_id,
                )
            return None

        try:
            # User already approved via ask_user_question; use AUTO template
            # so the follow-up invoke does not ask for confirmation again.
            prompt = self._build_skill_creator_prompt(
                suggested_name="",
                reason=pending.reason,
                require_user_confirm=False,
            )
            logger.info(
                "[SkillEvolutionRail] on_approve_new_skill: returning "
                "skill_creator_prompt (%d chars) for request_id=%s",
                len(prompt),
                request_id,
            )
            return prompt
        except Exception as exc:
            logger.error(
                "[SkillEvolutionRail] on_approve_new_skill failed for %s: %s",
                request_id,
                exc,
            )
            return None

    async def on_reject_new_skill(self, request_id: str) -> None:
        """Handle rejection of new skill creation."""
        pending = self._pending_skill_proposals.pop(request_id, None)
        if pending:
            logger.info(
                "[SkillEvolutionRail] user rejected new skill creation: %s",
                pending.name or request_id,
            )

    async def rollback_skill(self, skill_name: str, version: Optional[str] = None) -> bool:
        """Rollback skill to an archived version (no approval required).

        ``version`` may be a body archive filename (``SKILL.v1.0.0.md``) or a bare
        SemVer string (``1.0.0``). When omitted, the newest body archive by mtime
        is used.
        """
        store = self._evolution_store
        archive = store.get_skill_archive_dir(skill_name)
        if archive is None:
            logger.warning("[SkillEvolutionRail] no archive dir for %s", skill_name)
            return False

        if version:
            body_name = store.normalize_body_archive_name(version)
            if body_name is None:
                logger.warning("[SkillEvolutionRail] invalid archive version for %s: %s", skill_name, version)
                return False
            body_archive = store.get_skill_archive_file(skill_name, body_name)
            if body_archive is None:
                logger.warning("[SkillEvolutionRail] invalid archive version for %s: %s", skill_name, version)
                return False
        else:
            body_files = sorted(
                [f for f in archive.iterdir() if f.is_file() and store.is_body_archive_filename(f.name)],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not body_files:
                logger.warning("[SkillEvolutionRail] no archived body for %s", skill_name)
                return False
            body_archive = body_files[0]

        evo_archive = store.resolve_paired_evolution_archive(skill_name, body_archive.name)

        old_body = await store.read_archive_text(skill_name, body_archive.name)
        if not old_body:
            logger.warning("[SkillEvolutionRail] archived body is empty for %s: %s", skill_name, body_archive.name)
            return False

        await store.archive_current_state(skill_name)
        await store.write_skill_content(skill_name, old_body)

        if evo_archive is not None:
            restored = await store.restore_evolution_log_from_archive(skill_name, evo_archive.name)
            if not restored:
                return False
        else:
            await store.clear_evolutions(skill_name)

        await store.render_evolution_markdown(skill_name)

        deleted = await store.delete_archive_version(skill_name, body_archive.name)
        if not deleted:
            logger.warning(
                "[SkillEvolutionRail] rollback succeeded but failed to remove target archive for %s: %s",
                skill_name,
                body_archive.name,
            )

        logger.info("[SkillEvolutionRail] rollback completed for %s -> %s", skill_name, body_archive.name)
        return True


__all__ = ["SkillEvolutionRail"]
