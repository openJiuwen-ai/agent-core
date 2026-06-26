# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field
from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.qa_artifact.schema import QAArtifactConfig
from openjiuwen.core.context_engine.base import ContextWindow, ModelContext
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.context.session_memory_manager import (
    SessionMemoryConfig,
    find_last_completed_api_round_end,
    find_message_index_by_context_message_id,
    group_completed_api_rounds,
    invalidate_session_memory_anchor,
)
from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.observability import write_context_trace
from openjiuwen.core.context_engine.processor.base import ContextEvent, ContextProcessor
from openjiuwen.core.context_engine.processor.compressor.util import (
    FullCompactStateReinjector,
    build_plan_mode_reinjected_content,
    build_task_status_reinjected_content,
    build_skill_reinjected_content,
    build_todo_reinjected_content,
)
from openjiuwen.core.context_engine.qa_artifact import QAArtifactManager, build_qa_artifact_manager
from openjiuwen.core.context_engine.qa_artifact.window import compute_fold_slice
from openjiuwen.core.context_engine.qa_artifact.store import QAArtifactStore
from openjiuwen.core.context_engine.qa_artifact.window import (
    build_window_qas_from_context,
    make_processor_ctx,
)
from openjiuwen.core.context_engine.qa_block.registry import load_registry
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    BaseMessage,
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.foundation.tool import ToolInfo

SUMMARY_HARD_TRUNCATE_TOKENS = 1500

NO_TOOLS_PREAMBLE = """CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

"""

DETAILED_ANALYSIS_INSTRUCTION = """Before providing your final summary, wrap your analysis in <analysis> 
tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, especially if the user told you to do something 
     differently.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly.
"""

BASE_COMPACT_PROMPT = (
    NO_TOOLS_PREAMBLE
    + """Your task is to create a detailed summary of the conversation so far, 
    paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, 
and architectural decisions that would be essential for continuing development work without losing context.

"""
    + DETAILED_ANALYSIS_INSTRUCTION
    + """
Your summary should include the following sections:

1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. 
    Pay special attention to the most recent messages and include full code snippets where applicable and 
    include a summary of why this file read or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. 
    Pay special attention to specific user feedback that you received, 
    especially if the user told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results. 
    These are critical for understanding the users' feedback and changing intent.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked to work on.
8. Current Work: Describe in detail precisely what was being worked on immediately before this summary request, 
    paying special attention to the most recent messages from both user and assistant. 
    Include file names and code snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to the most recent work you were doing. 
    IMPORTANT: ensure that this step is DIRECTLY in line with the user's most recent explicit requests, 
    and the task you were working on immediately before this summary request. If your last task was concluded, 
    then only list next steps if they are explicitly in line with the users request. 
    Do not start on tangential requests or really old requests that were already completed without 
    confirming with the user first.
    If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were
    working on and where you left off. 
    This should be verbatim to ensure there's no drift in task interpretation.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]

2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]
   - [...]

3. Files and Code Sections:
   - [File Name 1]
      - [Summary of why this file is important]
      - [Summary of the changes made to this file, if any]
      - [Important Code Snippet]
   - [File Name 2]
      - [Important Code Snippet]
   - [...]

4. Errors and fixes:
    - [Detailed description of error 1]:
      - [How you fixed the error]
      - [User feedback on the error if any]
    - [...]

5. Problem Solving:
   [Description of solved problems and ongoing troubleshooting]

6. All user messages:
    - [Detailed non tool use user message]
    - [...]

7. Pending Tasks:
   - [Task 1]
   - [Task 2]
   - [...]

8. Current Work:
   [Precise description of current work]

9. Optional Next Step:
   [Optional Next step to take]

</summary>
</example>

Please provide your summary based on the conversation so far, 
following this structure and ensuring precision and thoroughness in your response.
"""
    + "\n\nREMINDER: Do NOT call any tools. "
    "Respond with plain text only — an <analysis> block followed by a <summary> block. "
    "Tool calls will be rejected and you will fail the task."
)


class FullCompactProcessorConfig(BaseModel):
    trigger_total_tokens: int = Field(default=180000, gt=0)
    compression_call_max_tokens: int = Field(default=200000, gt=0)
    messages_to_keep: int = Field(default=10, ge=0)
    keep_tool_message_pairs: bool = Field(default=True)
    state_snapshot_max_chars: int = Field(default=4000, gt=0)
    reinject_todos: bool = Field(default=True)
    reinject_recent_skills: int = Field(default=3, ge=0)
    reinject_file_tool_names: List[str] = Field(default=["read_file", "write_file", "edit_file", "glob", "grep"])
    reinject_tool_result_hint_names: List[str] = Field(default=["read_file", "write_file", "edit_file", "glob", "grep"])
    model: ModelRequestConfig | None = Field(default=None)
    model_client: ModelClientConfig | None = Field(default=None)
    marker: str = Field(default="[FULL_COMPACT_BOUNDARY]")
    state_marker: str = Field(default="[FULL_COMPACT_STATE]")
    synthetic_user_marker: str = Field(default="[earlier conversation truncated for compaction retry]")
    summary_intro: str = Field(
        default=(
            "This session is being continued from a previous conversation that "
            "ran out of context. The summary below covers the earlier portion "
            "of the conversation."
        )
    )
    recent_messages_notice: str = Field(default="Recent messages are preserved verbatim.")
    session_memory_enabled: bool = Field(default=True)
    session_memory_marker: str = Field(default="[SESSION_MEMORY_BOUNDARY]")
    session_memory_intro: str = Field(
        default=(
            "Earlier conversation has been replaced with the session memory file. "
            "Use it as the canonical summary of prior work."
        )
    )
    qa_artifact: QAArtifactConfig | None = None


@ContextEngine.register_processor()
class FullCompactProcessor(ContextProcessor):
    """Fallback compactor aligned with Claude Code's full compact flow."""

    def __init__(self, config: FullCompactProcessorConfig):
        super().__init__(config)
        self._trigger_total_tokens = config.trigger_total_tokens
        self._compression_call_max_tokens = config.compression_call_max_tokens
        self._hard_window_tokens = self._resolve_hard_window_tokens(config)
        self._messages_to_keep = config.messages_to_keep
        self._keep_tool_message_pairs = config.keep_tool_message_pairs
        self._state_snapshot_max_chars = config.state_snapshot_max_chars
        self._marker = config.marker
        self._state_marker = config.state_marker
        self._synthetic_user_marker = config.synthetic_user_marker
        self._summary_intro = config.summary_intro
        self._recent_messages_notice = config.recent_messages_notice
        self._session_memory_enabled = config.session_memory_enabled
        self._session_memory_marker = config.session_memory_marker
        self._session_memory_intro = config.session_memory_intro
        self._force_compact: bool = False
        self._get_path_force_compact: bool = False
        self._deferred_overflow_recovery: bool = False
        self._overflow_threshold_override: Optional[int] = None  # threshold_override from 413 recovery
        self._state_reinjector = FullCompactStateReinjector()
        self._state_reinjector.register_builder(
            name="skills",
            label="SKILLS",
            builder=build_skill_reinjected_content,
        )
        self._state_reinjector.register_builder(
            name="task_status",
            label="TASK_STATUS",
            builder=build_task_status_reinjected_content,
        )
        self._state_reinjector.register_builder(
            name="plan_mode",
            label="PLAN_MODE",
            builder=build_plan_mode_reinjected_content,
        )
        self._state_reinjector.register_builder(
            name="todos",
            label="TODOS",
            builder=build_todo_reinjected_content,
        )
        self._model: Model | None = None
        if config.model is not None and config.model_client is not None:
            self._model = Model(config.model_client, config.model)
        self._qa_mgr: QAArtifactManager | None = None
        qa_cfg = config.qa_artifact
        if qa_cfg is not None and qa_cfg.enabled:
            self._qa_mgr = self._build_qa_artifact_manager(config, qa_cfg)

    @staticmethod
    def _build_qa_artifact_manager(
        config: FullCompactProcessorConfig,
        qa_cfg: QAArtifactConfig,
    ) -> QAArtifactManager:
        """Consumer-side QA artifact manager (§2.5): share disk/state with rail writer."""
        sm_config = SessionMemoryConfig(
            model=config.model,
            model_client=config.model_client,
        )
        mgr = build_qa_artifact_manager(qa_cfg, sm_config)
        if config.model is not None and config.model_client is not None:
            mgr.bind_model_defaults(config.model, config.model_client)
        return mgr

    @property
    def qa_artifact_manager(self) -> QAArtifactManager | None:
        return self._qa_mgr

    @property
    def hard_window_tokens(self) -> int:
        return self._hard_window_tokens

    @property
    def config(self) -> FullCompactProcessorConfig:
        return self._config

    def set_force_compact(self, value: bool = True) -> None:
        """Set the force_compact flag for the next GET-path trigger check.

        When True, :meth:`trigger_get_context_window` will bypass all
        checks (``trigger_total_tokens``, ``_api_round``) and return
        ``True`` unconditionally.  The flag is automatically reset after
        one use.  The ADD path (``trigger_add_messages``) is **not**
        affected — force_compact only makes sense on the GET path which
        is what ``_railed_model_call`` uses during retry.

        This is a **fallback** mechanism for the context-overflow recovery
        chain: when an LLM call fails because the context actually exceeded
        the model's limit, the preventive compression threshold was not
        sufficient.  Setting this flag forces FullCompact to run on the next
        ``get_context_window`` call regardless of any threshold or round
        boundary — the context *must* be shrunk before retrying.
        """
        self._force_compact = value

    def consume_deferred_overflow_recovery(self) -> bool:
        """Return and clear proactive overflow deferral set by whole-window fallback.

        Cross-repo contract: consumed by Jiuwenclaw's
        ``ContextOverflowRecoveryRail.before_model_call``. This is a
        consume-and-clear API: once a ``True`` value is returned, subsequent
        calls return ``False`` until agent-core sets the deferral again. Do not
        change this to peek semantics without coordinating the Jiuwenclaw
        consumer.
        """
        deferred = self._deferred_overflow_recovery
        self._deferred_overflow_recovery = False
        return deferred

    def is_force_compact_pending(self) -> bool:
        """Whether the next GET-path should run force_compact recovery.

        Cross-repo contract: peeked by Jiuwenclaw's
        ``ContextOverflowRecoveryRail.before_model_call`` together with
        ``consume_deferred_overflow_recovery``. This method does not clear the
        pending flag; the FullCompact GET path owns clearing it when it runs.
        """
        return self._force_compact

    def set_overflow_threshold_override(self, threshold_override: int) -> None:
        self._overflow_threshold_override = threshold_override

    @staticmethod
    def _resolve_hard_window_tokens(config: FullCompactProcessorConfig) -> int:
        """LLM 硬窗上限：与 trigger / compression_call 口径对齐（§5.2）。"""
        candidates = [config.trigger_total_tokens]
        if config.compression_call_max_tokens:
            candidates.append(config.compression_call_max_tokens)
        return min(candidates)

    async def trigger_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> bool:
        candidate_messages = context.get_messages() + list(messages_to_add or [])
        if self._qa_mgr is not None:
            sys_operation = kwargs.get("sys_operation") or getattr(context, "_sys_operation", None)
            window_qas = self._build_window_qas(context, sys_operation=sys_operation)
            if window_qas:
                proc_ctx = make_processor_ctx(context, sys_operation=sys_operation)
                store = QAArtifactManager.build_store(proc_ctx, proc_ctx.workspace)
                if self._qa_mgr.has_pending_history(store, window_qas):
                    return True
        if not self._api_round(candidate_messages):
            return False
        system_messages = kwargs.get("system_messages") or []
        tools = kwargs.get("tools") or []
        candidate_tokens = self._count_context_window_tokens(
            system_messages,
            candidate_messages,
            tools,
            context,
        )
        return candidate_tokens > self._trigger_total_tokens

    async def trigger_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs: Any,
    ) -> bool:
        """GET path: force_compact recovery, pending QA history, or token threshold."""
        if self._force_compact:
            logger.info("[FullCompact] force_compact flag set on GET path, bypassing all trigger checks")
            self._force_compact = False
            self._get_path_force_compact = True
            return True
        self._get_path_force_compact = False
        if self._qa_mgr is not None:
            sys_operation = kwargs.get("sys_operation") or getattr(context, "_sys_operation", None)
            window_qas = self._build_window_qas(context, sys_operation=sys_operation)
            if window_qas:
                proc_ctx = make_processor_ctx(context, sys_operation=sys_operation)
                store = QAArtifactManager.build_store(proc_ctx, proc_ctx.workspace)
                if self._qa_mgr.has_pending_history(store, window_qas):
                    return True
        candidate_tokens = self._count_context_window_tokens(
            context_window.system_messages,
            context_window.context_messages,
            context_window.tools,
            context,
        )
        return candidate_tokens > self._trigger_total_tokens

    async def on_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs: Any,
    ) -> Tuple[ContextEvent | None, ContextWindow]:
        if self._get_path_force_compact:
            self._get_path_force_compact = False
            system_messages = list(context_window.system_messages or [])
            tools = context_window.tools or []
            all_messages = list(context_window.context_messages or [])

            write_context_trace(
                "context.processor.full_compact.before",
                {
                    "processor": self.processor_type(),
                    "context_id": context.context_id(),
                    "session_id": context.session_id(),
                    "trigger_total_tokens": self.config.trigger_total_tokens,
                    "message_count_before": len(all_messages),
                },
            )

            event, new_context_messages, session_memory_message = await self._build_replacement_messages(
                context,
                all_messages,
                system_messages,
                tools,
            )
            if new_context_messages is None:
                logger.warning("[FullCompact] on_get_context_window: force_compact produced no replacement")
                return None, context_window

            context.set_messages(new_context_messages)
            logger.info(
                "[FullCompact] on_get_context_window: force_compact succeeded, "
                "messages_before=%d messages_after=%d replacement_kind=%s",
                len(all_messages),
                len(new_context_messages),
                "session_memory" if session_memory_message is not None else "full_compact",
            )

            if session_memory_message is None:
                self._invalidate_session_memory_anchor(context)

            new_window = ContextWindow(
                system_messages=system_messages,
                context_messages=new_context_messages,
                tools=tools,
            )
            write_context_trace(
                "context.processor.full_compact.after",
                {
                    "processor": self.processor_type(),
                    "context_id": context.context_id(),
                    "session_id": context.session_id(),
                    "replacement_kind": (
                        "session_memory" if session_memory_message is not None else "full_compact"
                    ),
                    "message_count_after": len(new_context_messages),
                },
            )
            return event, new_window

        sys_operation = kwargs.get("sys_operation") or getattr(context, "_sys_operation", None)
        system_messages = list(context_window.system_messages or [])
        tools = list(context_window.tools or [])
        await self._run_qa_artifact_window_pass(
            context,
            system_messages=system_messages,
            tools=tools,
            sys_operation=sys_operation,
            messages_to_add=[],
        )
        context_window.context_messages = context.get_messages()
        return None, context_window

    async def _run_qa_artifact_window_pass(
        self,
        context: ModelContext,
        *,
        system_messages: List[BaseMessage],
        tools: List[Any],
        sys_operation: Any,
        messages_to_add: List[BaseMessage],
    ) -> Tuple[bool, bool, bool]:
        """Returns (compact_handled, artifact_applied, buffer_wholesale_replaced)."""
        if self._qa_mgr is None:
            return False, False, False

        proc_ctx = make_processor_ctx(context, sys_operation=sys_operation)
        window_qas = self._build_window_qas(context, sys_operation=sys_operation)
        artifact_applied = False
        buffer_wholesale_replaced = False
        if window_qas:
            artifact_applied = await self._qa_mgr.apply_artifact_to_context(
                proc_ctx,
                workspace=proc_ctx.workspace,
                window_qas=window_qas,
                context=context,
            )

        all_messages = context.get_messages() + list(messages_to_add or [])
        candidate_tokens = self._count_context_window_tokens(
            system_messages,
            all_messages,
            tools,
            context,
        )
        if candidate_tokens > self._trigger_total_tokens and window_qas:

            async def _fallback() -> bool:
                nonlocal buffer_wholesale_replaced
                ok = await self._fallback_whole_window_compact(
                    context,
                    all_messages=all_messages,
                    system_messages=system_messages,
                    tools=tools,
                )
                if ok:
                    buffer_wholesale_replaced = True
                return ok

            handled = await self._qa_mgr.compact_to_target(
                proc_ctx,
                workspace=proc_ctx.workspace,
                window_qas=window_qas,
                total_tokens=candidate_tokens,
                context=context,
                fallback=_fallback,
            )
            if handled:
                write_context_trace(
                    "context.processor.full_compact.after",
                    {
                        "processor": self.processor_type(),
                        "context_id": context.context_id(),
                        "session_id": context.session_id(),
                        "replacement_kind": "qa_artifact",
                        "message_count_after": len(context.get_messages()),
                    },
                )
                return True, artifact_applied, buffer_wholesale_replaced
        return False, artifact_applied, buffer_wholesale_replaced

    def _build_window_qas(self, context: ModelContext, *, sys_operation: Any) -> list[Any]:
        return build_window_qas_from_context(context, sys_operation=sys_operation)

    async def _archive_fold_slice_before_whole_window_fallback(
        self,
        context: ModelContext,
        all_messages: List[BaseMessage],
    ) -> None:
        registry = load_registry(context.get_session_ref())
        current_qa_id = registry.current_qa_id
        if not current_qa_id:
            return

        boundary_index = self._find_last_compaction_boundary_index(all_messages)
        _, active_messages = self._split_messages_at_compaction_boundary(
            all_messages,
            boundary_index=boundary_index,
        )
        if not active_messages:
            return

        kept = self._select_messages_to_keep(
            active_messages,
            context,
            keep_recent=self._messages_to_keep,
        )
        sys_operation = getattr(context, "_sys_operation", None)
        proc_ctx = make_processor_ctx(context, sys_operation=sys_operation)
        workspace_root = proc_ctx.workspace.root_path if proc_ctx.workspace else ""
        store = QAArtifactStore(proc_ctx.session, workspace_root, sys_operation)
        state = store.get_or_init(current_qa_id)
        fold_slice = compute_fold_slice(
            all_messages,
            active_messages,
            current_qa_id,
            state.covers_upto_message_id,
            kept,
        )
        if not fold_slice:
            return

        window_qas = self._build_window_qas(context, sys_operation=sys_operation)
        qa_ref = next(
            (item for item in window_qas if item.qa_id == current_qa_id and not item.is_history),
            None,
        )
        if qa_ref is None:
            from openjiuwen.core.context_engine.qa_ref import QARef
            from openjiuwen.core.context_engine.qa_block.messages import message_qa_id as _message_qa_id
            from openjiuwen.core.context_engine.qa_artifact.window import estimate_context_messages_tokens

            current_msgs = [msg for msg in all_messages if _message_qa_id(msg) == current_qa_id]
            token_counter = context.token_counter() if hasattr(context, "token_counter") else None
            qa_ref = QARef(
                qa_id=current_qa_id,
                tokens=estimate_context_messages_tokens(current_msgs, token_counter),
                is_history=False,
                get_messages=lambda msgs=current_msgs: list(msgs),
            )

        await self._qa_mgr.archive_fold_slice_before_fallback(
            proc_ctx,
            workspace=proc_ctx.workspace,
            qa_ref=qa_ref,
            fold_slice=fold_slice,
            all_messages=all_messages,
        )

    async def _fallback_whole_window_compact(
        self,
        context: ModelContext,
        *,
        all_messages: List[BaseMessage],
        system_messages: List[BaseMessage],
        tools: List[Any],
    ) -> bool:
        """整窗 fallback（session_memory / full_compact）；仍超硬窗时 defer 给 overflow recovery。"""
        if self._qa_mgr is not None:
            try:
                await self._archive_fold_slice_before_whole_window_fallback(
                    context,
                    all_messages,
                )
            except Exception as exc:
                logger.warning(
                    "[FullCompact] fallback archive failed, continuing compact: %s",
                    exc,
                    exc_info=True,
                )
                write_context_trace(
                    "qa_artifact.fallback_archive_failed",
                    {
                        "context_id": context.context_id(),
                        "session_id": context.session_id(),
                        "error": str(exc),
                    },
                )

        event, new_messages, session_memory_message = await self._build_replacement_messages(
            context,
            all_messages,
            system_messages,
            tools,
        )
        if new_messages is not None:
            context.set_messages(new_messages)
        remaining_tokens = self._count_context_window_tokens(
            system_messages,
            context.get_messages(),
            tools,
            context,
            use_baseline=False,
        )
        remaining_tokens_baseline = self._count_context_window_tokens(
            system_messages,
            context.get_messages(),
            tools,
            context,
            use_baseline=True,
        )
        if remaining_tokens > self._hard_window_tokens:
            logger.warning(
                "[FullCompact] whole-window compact still over hard window after fallback "
                "(actual=%s baseline=%s hard=%s messages_applied=%s); "
                "deferring to overflow recovery chain",
                remaining_tokens,
                remaining_tokens_baseline,
                self._hard_window_tokens,
                new_messages is not None,
            )
            self._deferred_overflow_recovery = True
            self.set_force_compact(True)
            write_context_trace(
                "context.processor.full_compact.deferred_overflow_recovery",
                {
                    "processor": self.processor_type(),
                    "context_id": context.context_id(),
                    "session_id": context.session_id(),
                    "replacement_kind": (
                        "session_memory" if session_memory_message is not None else "full_compact"
                    ),
                    "message_count_after": len(context.get_messages()),
                    "qa_artifact_fallback": True,
                    "remaining_tokens": remaining_tokens,
                    "remaining_tokens_baseline": remaining_tokens_baseline,
                    "hard_window_tokens": self._hard_window_tokens,
                    "messages_applied": new_messages is not None,
                },
            )
            return new_messages is not None
        if new_messages is None:
            return False
        write_context_trace(
            "context.processor.full_compact.after",
            {
                "processor": self.processor_type(),
                "context_id": context.context_id(),
                "session_id": context.session_id(),
                "replacement_kind": (
                    "session_memory" if session_memory_message is not None else "full_compact"
                ),
                "message_count_after": len(new_messages),
                "qa_artifact_fallback": True,
                "remaining_tokens": remaining_tokens,
                "remaining_tokens_baseline": remaining_tokens_baseline,
                "hard_window_tokens": self._hard_window_tokens,
            },
        )
        if session_memory_message is None:
            self._invalidate_session_memory_anchor(context)
        return True

    async def on_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        system_messages = kwargs.get("system_messages") or []
        tools = kwargs.get("tools") or []
        sys_operation = kwargs.get("sys_operation") or getattr(context, "_sys_operation", None)

        if self._qa_mgr is not None:
            compact_handled, artifact_applied, wholesale = await self._run_qa_artifact_window_pass(
                context,
                system_messages=system_messages,
                tools=tools,
                sys_operation=sys_operation,
                messages_to_add=messages_to_add,
            )
            if compact_handled:
                return None, [] if wholesale else messages_to_add
            if artifact_applied:
                write_context_trace(
                    "context.processor.full_compact.after",
                    {
                        "processor": self.processor_type(),
                        "context_id": context.context_id(),
                        "session_id": context.session_id(),
                        "replacement_kind": "qa_artifact_pending",
                        "message_count_after": len(context.get_messages()),
                    },
                )
                return None, messages_to_add

        all_messages = context.get_messages() + list(messages_to_add or [])
        system_messages = kwargs.get("system_messages") or []
        tools = kwargs.get("tools") or []
        write_context_trace(
            "context.processor.full_compact.before",
            {
                "processor": self.processor_type(),
                "context_id": context.context_id(),
                "session_id": context.session_id(),
                "trigger_total_tokens": self.config.trigger_total_tokens,
                "message_count_before": len(all_messages),
            },
        )
        event, new_context_messages, session_memory_message = await self._build_replacement_messages(
            context,
            all_messages,
            system_messages,
            tools,
        )
        if new_context_messages is None:
            return None, messages_to_add
        context.set_messages(new_context_messages)
        write_context_trace(
            "context.processor.full_compact.after",
            {
                "processor": self.processor_type(),
                "context_id": context.context_id(),
                "session_id": context.session_id(),
                "replacement_kind": "session_memory" if session_memory_message is not None else "full_compact",
                "event": (
                    {"event_type": event.event_type, "messages_to_modify": event.messages_to_modify}
                    if event is not None else None
                ),
                "message_count_after": len(new_context_messages),
            },
        )
        if session_memory_message is None:
            self._invalidate_session_memory_anchor(context)
        return event, []

    async def _build_replacement_messages(
        self,
        context: ModelContext,
        all_messages: List[BaseMessage],
        system_messages: List[BaseMessage],
        tools: List[Any],
    ) -> Tuple[ContextEvent | None, Optional[List[BaseMessage]], Optional[UserMessage]]:
        """Build replacement messages for FullCompact.

        During 413 recovery (force_compact=True), ``threshold`` comes from
        ``_overflow_threshold_override`` (= model_limit * RECOVERY_THRESHOLD_RATIO)
        instead of ``trigger_total_tokens``. This ensures the adaptive chain
        targets the model's real limit, not the (possibly smaller) preventive
        threshold.

        SessionMemory path: no force bypass — if tokens > threshold, reject
        and fall through to adaptive chain for further compression.
        """
        # 413 recovery: use threshold_override (set by RecoveryRail);
        # normal path: use trigger_total_tokens.
        # Note: _overflow_threshold_override is consumed in _truncate_for_prompt_budget
        # (which runs later and is the critical budget constraint), not here.
        threshold = self._overflow_threshold_override or self._trigger_total_tokens
        if self._overflow_threshold_override is not None:
            logger.info(
                "[FullCompact] Using overflow_threshold_override=%d as threshold "
                "(trigger_total_tokens=%d)",
                self._overflow_threshold_override,
                self._trigger_total_tokens,
            )
        boundary_index = self._find_last_compaction_boundary_index(all_messages)
        prefix, active_messages = self._split_messages_at_compaction_boundary(
            all_messages,
            boundary_index=boundary_index,
        )
        if not active_messages:
            logger.info("[FullCompact] replacement skipped: no active messages after boundary")
            return None, None, None

        session_memory_messages, session_memory_message = await self._build_session_memory_messages(
            context=context,
            prefix=prefix,
            active_messages=active_messages,
            has_compaction_boundary=boundary_index >= 0,
        )
        if session_memory_messages is not None:
            session_memory_tokens = self._count_context_window_tokens(
                system_messages,
                session_memory_messages,
                tools,
                context,
                use_baseline=False,
            )
            if session_memory_tokens <= threshold:
                logger.info("[FullCompact] using session_memory replacement, "
                            "session_memory_tokens=%d threshold=%d",
                            session_memory_tokens, threshold)
                return (
                    ContextEvent(
                        event_type=self.processor_type(),
                        messages_to_modify=list(range(len(all_messages))),
                    ),
                    session_memory_messages,
                    session_memory_message,
                )
            logger.info("[FullCompact] session_memory candidate rejected: "
                        "session_memory_tokens=%d > threshold=%d, falling through to adaptive chain",
                        session_memory_tokens, threshold)
        else:
            logger.info("[FullCompact] session_memory candidate unavailable, fallback to full_compact")

        new_context_messages = await self._try_full_compact_adaptive_chain(
            context=context,
            prefix=prefix,
            active_messages=active_messages,
            system_messages=system_messages,
            tools=tools,
            threshold=threshold,
        )
        if new_context_messages is None:
            logger.warning("[FullCompact] all replacement attempts exceeded threshold; keeping original buffer")
            return None, None, None
        logger.info(
            "[FullCompact] using full_compact replacement output_messages=%s",
            len(new_context_messages),
        )
        return (
            ContextEvent(
                event_type=self.processor_type(),
                messages_to_modify=list(range(len(all_messages))),
            ),
            new_context_messages,
            None,
        )

    async def _try_full_compact_adaptive_chain(
        self,
        *,
        context: ModelContext,
        prefix: List[BaseMessage],
        active_messages: List[BaseMessage],
        system_messages: List[BaseMessage],
        tools: List[Any],
        threshold: int,
    ) -> Optional[List[BaseMessage]]:
        compact_source = self._prepare_messages_for_prompt(self._strip_media_messages(active_messages))
        if not compact_source:
            return None

        compact_input = self._truncate_for_prompt_budget(compact_source, context)
        if not compact_input:
            return None

        summary = await self._generate_summary(compact_input, context)
        if not summary:
            logger.warning("[FullCompact] full_compact summary generation returned empty content")
            return None

        reinject_builder_names = self._reinject_builder_names()
        attempts = [
            {"messages_to_keep": self._messages_to_keep, "reinject_state": True},
            {"messages_to_keep": self._messages_to_keep, "reinject_state": False},
            {"messages_to_keep": 0, "reinject_state": False},
        ]
        for attempt_idx, attempt in enumerate(attempts):
            candidate = self._assemble_full_compact_candidate(
                context=context,
                prefix=prefix,
                active_messages=active_messages,
                summary=summary,
                messages_to_keep=attempt["messages_to_keep"],
                reinject_state=attempt["reinject_state"],
                reinject_builder_names=reinject_builder_names,
            )
            if candidate is None:
                continue
            tokens = self._count_context_window_tokens(
                system_messages,
                candidate,
                tools,
                context,
                use_baseline=False,
            )
            if tokens <= threshold:
                logger.info("[FullCompact] adaptive chain accepted attempt=%s tokens=%s", attempt_idx, tokens)
                return candidate
            logger.info(
                "[FullCompact] adaptive chain attempt=%s rejected tokens=%s threshold=%s",
                attempt_idx,
                tokens,
                threshold,
            )

        truncated_summary = self._truncate_summary_hard(summary, SUMMARY_HARD_TRUNCATE_TOKENS, context)
        boundary = SystemMessage(content=f"{self._marker}\nConversation compacted")
        summary_message = UserMessage(content=self._build_summary_message(truncated_summary, False))
        fallback_candidate = prefix + [boundary, summary_message]
        fallback_tokens = self._count_context_window_tokens(
            system_messages,
            fallback_candidate,
            tools,
            context,
            use_baseline=False,
        )
        if fallback_tokens <= threshold:
            logger.info(
                "[FullCompact] adaptive chain fallback accepted tokens=%s",
                fallback_tokens,
            )
            return fallback_candidate
        logger.info(
            "[FullCompact] adaptive chain fallback rejected tokens=%s threshold=%s",
            fallback_tokens,
            threshold,
        )
        return None

    def _assemble_full_compact_candidate(
        self,
        *,
        context: ModelContext,
        prefix: List[BaseMessage],
        active_messages: List[BaseMessage],
        summary: str,
        messages_to_keep: int,
        reinject_state: bool,
        reinject_builder_names: List[str],
    ) -> Optional[List[BaseMessage]]:
        kept = self._select_messages_to_keep(
            active_messages,
            context,
            keep_recent=messages_to_keep,
        )
        summary_message = UserMessage(content=self._build_summary_message(summary, bool(kept)))
        boundary = SystemMessage(content=f"{self._marker}\nConversation compacted")
        new_context_messages = prefix + [boundary, summary_message]
        new_context_messages.extend(kept)
        if reinject_state:
            new_context_messages.extend(
                self.build_reinjected_state_messages(
                    context=context,
                    source_messages=active_messages,
                    messages_to_keep=kept,
                    summary_message=summary_message,
                    boundary_message=boundary,
                    builder_names=reinject_builder_names,
                )
            )
        return new_context_messages

    async def _build_full_compact_messages(
        self,
        *,
        context: ModelContext,
        prefix: List[BaseMessage],
        active_messages: List[BaseMessage],
        system_messages: List[BaseMessage] | None = None,
        tools: List[Any] | None = None,
    ) -> Optional[List[BaseMessage]]:
        threshold = self._trigger_total_tokens
        return await self._try_full_compact_adaptive_chain(
            context=context,
            prefix=prefix,
            active_messages=active_messages,
            system_messages=system_messages or [],
            tools=tools or [],
            threshold=threshold,
        )

    async def _build_session_memory_messages(
        self,
        *,
        context: ModelContext,
        prefix: List[BaseMessage],
        active_messages: List[BaseMessage],
        has_compaction_boundary: bool,
    ) -> Tuple[Optional[List[BaseMessage]], Optional[UserMessage]]:
        if not self._session_memory_enabled:
            logger.info("[FullCompact] session_memory disabled")
            return None, None

        session_memory_runtime = self._load_session_memory_runtime(context)
        if session_memory_runtime.get("is_extracting"):
            logger.info("[FullCompact] session_memory extraction in progress, using latest committed notes")
        session_memory_text = self._load_session_memory_text(context, session_memory_runtime)
        if not session_memory_text:
            logger.info("[FullCompact] session_memory unavailable: empty notes content or unresolved path")
            return None, None

        preserved_messages = self._select_messages_after_session_memory(
            active_messages=active_messages,
            session_memory_runtime=session_memory_runtime,
            has_compaction_boundary=has_compaction_boundary,
        )
        if preserved_messages is None:
            logger.info(
                "[FullCompact] session_memory skipped: no valid active anchor has_boundary=%s active=%s notes_upto=%s",
                has_compaction_boundary,
                len(active_messages),
                session_memory_runtime.get("notes_upto_message_id"),
            )
            return None, None

        boundary = SystemMessage(
            content=f"{self._session_memory_marker}\nEarlier conversation replaced with session memory"
        )
        session_memory_message = UserMessage(
            content=self._build_session_memory_message(session_memory_text, bool(preserved_messages))
        )
        candidate_messages = prefix + [boundary, session_memory_message]
        candidate_messages.extend(preserved_messages)
        candidate_messages.extend(
            self.build_reinjected_state_messages(
                context=context,
                source_messages=active_messages,
                messages_to_keep=preserved_messages,
                summary_message=session_memory_message,
                boundary_message=boundary,
                builder_names=["plan"],
            )
        )
        return candidate_messages, session_memory_message

    def _reinject_builder_names(self) -> List[str]:
        names = ["plan", "plan_mode", "skills", "task_status"]
        if self.config.reinject_todos:
            names.append("todos")
        return names

    def _split_messages_at_compaction_boundary(
        self,
        messages: List[BaseMessage],
        boundary_index: Optional[int] = None,
    ) -> Tuple[List[BaseMessage], List[BaseMessage]]:
        if boundary_index is None:
            boundary_index = self._find_last_compaction_boundary_index(messages)
        prefix = messages[:boundary_index] if boundary_index > 0 else []
        active_messages = messages[boundary_index + 1:] if boundary_index >= 0 else list(messages)
        return prefix, active_messages

    async def _generate_summary(
        self,
        messages: List[BaseMessage],
        context: ModelContext,
    ) -> str:
        if self._model is None:
            return self._build_fallback_summary(messages)

        prompt_messages = [
            SystemMessage(content=BASE_COMPACT_PROMPT),
            UserMessage(content=self._serialize_messages(messages)),
        ]
        try:
            response = await self._model.invoke(messages=prompt_messages, tools=None)
            content = (response.content or "").strip()
            if not content:
                logger.warning("[FullCompact] LLM returned empty summary, falling back")
                return self._build_fallback_summary(messages)
            return self._format_summary(content)
        except Exception as exc:
            logger.warning(
                "[FullCompact] LLM summary generation failed: %s, falling back",
                exc,
                exc_info=True,
            )
            return self._build_fallback_summary(messages)

    def _truncate_for_prompt_budget(
        self,
        messages: List[BaseMessage],
        context: ModelContext,
    ) -> List[BaseMessage]:
        if self._overflow_threshold_override is not None:
            budget = self._overflow_threshold_override
            logger.info(
                "[FullCompact] Using overflow_threshold_override=%d as compression budget "
                "(config compression_call_max_tokens=%d)",
                self._overflow_threshold_override,
                self._compression_call_max_tokens,
            )
            self._overflow_threshold_override = None  # consume once
        else:
            # 如果_compression_call_max_tokens超过模型窗口大小，压缩动作自身就会溢出，因此取二者最小值
            budget = min(self._compression_call_max_tokens, self._trigger_total_tokens)
        groups = self._group_messages_by_api_round(messages)
        while groups:
            candidate = [msg for group in groups for msg in group]
            if self._count_prompt_tokens(candidate, context) <= budget:
                return candidate
            if len(groups) == 1:
                return self._truncate_messages_from_head(candidate, context, budget=budget)
            groups = groups[1:]
            if groups and isinstance(groups[0][0], AssistantMessage):
                groups[0] = [UserMessage(content=self._synthetic_user_marker), *groups[0]]
        return self._build_minimal_compact_input(messages)

    def _truncate_messages_from_head(
        self,
        messages: List[BaseMessage],
        context: ModelContext,
        *,
        budget: int | None = None,
    ) -> List[BaseMessage]:
        effective_budget = budget or self._compression_call_max_tokens
        candidate = list(messages)
        while candidate:
            if self._count_prompt_tokens(candidate, context) <= effective_budget:
                return candidate
            if self._is_synthetic_marker_message(candidate[0]):
                if len(candidate) == 1:
                    return self._build_minimal_compact_input(messages)
                candidate = candidate[2:]
            else:
                candidate = candidate[1:]
            if candidate and isinstance(candidate[0], AssistantMessage):
                candidate = [UserMessage(content=self._synthetic_user_marker), *candidate]
        return self._build_minimal_compact_input(messages)

    def _group_messages_by_api_round(self, messages: List[BaseMessage]) -> List[List[BaseMessage]]:
        return [list(messages[start:end]) for start, end in group_completed_api_rounds(messages)]

    def _build_minimal_compact_input(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        if not messages:
            return []

        tail: List[BaseMessage] = [messages[-1]]
        if isinstance(tail[0], AssistantMessage):
            return [UserMessage(content=self._synthetic_user_marker), tail[0]]
        return tail

    def _select_messages_to_keep(
        self,
        messages: List[BaseMessage],
        context=None,
        *,
        keep_recent: int | None = None,
    ) -> List[BaseMessage]:
        from openjiuwen.core.context_engine.processor._protected import (
            is_protected,
            msg_in_window,
            resolve_active_window_message_ids,
        )
        in_window_ids = resolve_active_window_message_ids(context, messages) if context else set()
        if keep_recent is None:
            keep_recent = self._messages_to_keep
        if keep_recent <= 0 or not messages:
            return [m for m in messages if is_protected(m, in_active_window=msg_in_window(m, in_window_ids))]

        start_index = max(len(messages) - keep_recent, 0)
        if self._keep_tool_message_pairs:
            start_index = self._adjust_start_index_for_tool_pairs(messages, start_index)
        kept = list(messages[start_index:])
        # Pull in any protected (active skill) messages that fall before start_index.
        kept_ids = {id(m) for m in kept}
        for m in messages[:start_index]:
            if is_protected(m, in_active_window=msg_in_window(m, in_window_ids)) and id(m) not in kept_ids:
                kept.append(m)
        return kept

    def _adjust_start_index_for_tool_pairs(
        self,
        messages: List[BaseMessage],
        start_index: int,
    ) -> int:
        if start_index <= 0 or start_index >= len(messages):
            return start_index

        adjusted = start_index
        needed_tool_ids = {
            msg.tool_call_id
            for msg in messages[start_index:]
            if isinstance(msg, ToolMessage) and getattr(msg, "tool_call_id", None)
        }
        if not needed_tool_ids:
            return adjusted

        present_tool_calls = set()
        for msg in messages[start_index:]:
            if isinstance(msg, AssistantMessage):
                for tool_call in getattr(msg, "tool_calls", None) or []:
                    tool_call_id = getattr(tool_call, "id", None)
                    if tool_call_id:
                        present_tool_calls.add(tool_call_id)

        missing_tool_calls = needed_tool_ids - present_tool_calls
        if not missing_tool_calls:
            return adjusted

        for index in range(start_index - 1, -1, -1):
            msg = messages[index]
            if not isinstance(msg, AssistantMessage):
                continue
            tool_calls = getattr(msg, "tool_calls", None) or []
            matched = False
            for tool_call in tool_calls:
                tool_call_id = getattr(tool_call, "id", None)
                if tool_call_id in missing_tool_calls:
                    missing_tool_calls.discard(tool_call_id)
                    matched = True
            if matched:
                adjusted = index
            if not missing_tool_calls:
                break
        return adjusted

    @staticmethod
    def _strip_media_messages(messages: List[BaseMessage]) -> List[BaseMessage]:
        # Media-heavy message preprocessing is pending; current behavior passes them through unchanged.
        return messages

    def _prepare_messages_for_prompt(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        result: List[BaseMessage] = []
        for msg in messages:
            if (
                self._is_boundary_message(msg)
                or self._is_state_message(msg)
                or self._is_session_memory_boundary_message(msg)
            ):
                continue
            result.append(msg)
        return result

    def _build_session_memory_message(self, session_memory_text: str, has_preserved_messages: bool) -> str:
        parts = [self._session_memory_intro, "", session_memory_text.strip()]
        if has_preserved_messages:
            parts.extend(["", self._recent_messages_notice])
        return "\n".join(parts)

    def _load_session_memory_text(
        self,
        context: ModelContext,
        session_memory_runtime: Optional[Dict[str, Any]] = None,
    ) -> str:
        session_memory_path = self._resolve_session_memory_path(
            context,
            session_memory_runtime=session_memory_runtime,
        )
        if session_memory_path is None:
            return ""
        try:
            content = session_memory_path.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
        if content:
            return content
        return ""

    def _load_session_memory_runtime(self, context: ModelContext) -> Dict[str, Any]:
        session = getattr(context, "_session_ref", None)
        if session is not None and hasattr(session, "get_state"):
            state = session.get_state("__session_memory__") or {}
            session_id = ""
            if hasattr(session, "get_session_id"):
                try:
                    session_id = session.get_session_id()
                except Exception:
                    session_id = ""
            logger.info(
                "[FullCompact] load_session_memory_runtime session_obj=%s session_id=%s state=%s",
                hex(id(session)),
                session_id,
                state if not isinstance(state, dict) else {
                    "memory_path": state.get("memory_path"),
                    "initialized": state.get("initialized"),
                    "is_extracting": state.get("is_extracting"),
                    "tokens_at_last_update": state.get("tokens_at_last_update"),
                    "tool_calls_at_last_update": state.get("tool_calls_at_last_update"),
                    "last_summarized_message_count": state.get("last_summarized_message_count"),
                    "notes_upto_message_id": state.get("notes_upto_message_id"),
                },
            )
            if isinstance(state, dict) and state:
                return dict(state)
        return {}

    def _resolve_session_memory_path(
        self,
        context: ModelContext,
        session_memory_runtime: Optional[Dict[str, Any]] = None,
    ) -> Optional[Path]:
        runtime = session_memory_runtime or self._load_session_memory_runtime(context)
        memory_path = runtime.get("memory_path")
        if not memory_path:
            return None
        try:
            return Path(memory_path)
        except Exception:
            return None

    def _select_messages_after_session_memory(
        self,
        *,
        active_messages: List[BaseMessage],
        session_memory_runtime: Dict[str, Any],
        has_compaction_boundary: bool,
    ) -> Optional[List[BaseMessage]]:
        notes_upto_message_id = session_memory_runtime.get("notes_upto_message_id")
        summarized_message_index = find_message_index_by_context_message_id(
            active_messages,
            notes_upto_message_id,
        )
        if summarized_message_index >= 0:
            if self._is_session_memory_summary_message(active_messages[summarized_message_index]):
                return list(active_messages[summarized_message_index + 1:])
            completed_end = find_last_completed_api_round_end(active_messages[: summarized_message_index + 1])
            if completed_end <= 0:
                return None
            return list(active_messages[completed_end:])

        if has_compaction_boundary:
            logger.info(
                "[FullCompact] session_memory context-id anchor missing "
                "in active segment after boundary notes_upto=%s active=%s",
                notes_upto_message_id,
                len(active_messages),
            )
            return None

        logger.info(
            "[FullCompact] session_memory no valid context-id anchor before first boundary notes_upto=%s active=%s",
            notes_upto_message_id,
            len(active_messages),
        )
        return None

    @staticmethod
    def _invalidate_session_memory_anchor(context: ModelContext) -> None:
        session = getattr(context, "_session_ref", None)
        invalidate_session_memory_anchor(session)

    def _build_summary_message(self, summary: str, has_preserved_messages: bool) -> str:
        parts = [self._summary_intro, "", summary]
        if has_preserved_messages:
            parts.extend(["", self._recent_messages_notice])
        return "\n".join(parts)

    def build_reinjected_state_messages(
        self,
        *,
        context: ModelContext,
        source_messages: List[BaseMessage],
        messages_to_keep: List[BaseMessage],
        summary_message: UserMessage,
        boundary_message: SystemMessage,
        builder_names: Optional[List[str]] = None,
    ) -> List[BaseMessage]:
        _ = context, summary_message, boundary_message
        candidate_messages = self._prepare_messages_for_prompt(source_messages)
        if not candidate_messages:
            return []

        active_builder_names = set(builder_names) if builder_names is not None else None
        state_messages: List[BaseMessage] = []
        for builder_spec in self._state_reinjector.iter_builders():
            if active_builder_names is not None and builder_spec.name not in active_builder_names:
                continue
            content = builder_spec.builder(
                self,
                context=context,
                messages=candidate_messages,
                messages_to_keep=messages_to_keep,
            )
            if isinstance(content, list):
                state_messages.extend(content)
                continue
            if content:
                state_messages.append(self._make_state_message(builder_spec.label, content))
        return state_messages

    def _make_state_message(self, label: str, content: str) -> UserMessage:
        compact_content = self.truncate_state_text(content)
        return UserMessage(content=f"{self._state_marker}\n[{label}]\n{compact_content}")

    def truncate_state_text(self, text: str) -> str:
        if len(text) <= self._state_snapshot_max_chars:
            return text
        return self._build_head_tail_truncated_text(text, self._state_snapshot_max_chars)

    def _count_prompt_tokens(self, messages: List[BaseMessage], context: ModelContext) -> int:
        prompt_messages = [
            SystemMessage(content=BASE_COMPACT_PROMPT),
            UserMessage(content=self._serialize_messages(messages)),
        ]
        token_counter = context.token_counter()
        if token_counter is not None:
            try:
                return token_counter.count_messages(prompt_messages)
            except Exception:
                return sum(self._estimate_message_tokens(message) for message in prompt_messages)
        return sum(self._estimate_message_tokens(message) for message in prompt_messages)

    @staticmethod
    def _count_tool_calls(messages: List[BaseMessage]) -> int:
        total = 0
        for message in messages:
            if isinstance(message, AssistantMessage):
                total += len(getattr(message, "tool_calls", None) or [])
        return total

    def _count_messages_with_fallback(
        self,
        token_counter: Any,
        messages: List[BaseMessage],
    ) -> int:
        if not messages:
            return 0
        if token_counter is not None:
            try:
                return token_counter.count_messages(messages)
            except Exception as exc:
                logger.warning(
                    "[FullCompact] token_counter.count_messages failed: %s, falling back to estimate",
                    exc,
                )
        return sum(self._estimate_message_tokens(message) for message in messages)

    def _count_tools_with_fallback(self, token_counter: Any, tools: List[Any]) -> int:
        if not tools:
            return 0
        tool_infos = [tool for tool in tools if isinstance(tool, ToolInfo)]
        if token_counter is not None and tool_infos:
            try:
                return token_counter.count_tools(tool_infos)
            except Exception as exc:
                logger.warning(
                    "[FullCompact] token_counter.count_tools failed: %s, falling back to estimate",
                    exc,
                )
        total = 0
        for tool in tools:
            if isinstance(tool, ToolInfo):
                payload = {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.parameters,
                }
            elif hasattr(tool, "model_dump"):
                payload = tool.model_dump()
            else:
                payload = tool
            try:
                total += ContextUtils.estimate_tokens(json.dumps(payload, ensure_ascii=False))
            except (TypeError, ValueError):
                total += ContextUtils.estimate_tokens(str(payload))
        return total

    def _estimate_messages(self, messages: List[BaseMessage], context: ModelContext) -> int:
        token_counter = context.token_counter()
        return self._count_messages_with_fallback(token_counter, messages)

    def _find_recent_usage_baseline(
        self,
        messages: List[BaseMessage],
    ) -> Tuple[int | None, int]:
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if (
                isinstance(message, AssistantMessage)
                and message.usage_metadata
                and message.usage_metadata.total_tokens > 0
            ):
                return index, message.usage_metadata.total_tokens
        return None, 0

    def _count_context_window_tokens(
        self,
        system_messages: List[BaseMessage],
        context_messages: List[BaseMessage],
        tools: List[Any],
        context: ModelContext,
        *,
        use_baseline: bool = True,
    ) -> int:
        token_counter = context.token_counter()
        full = self._count_messages_with_fallback(
            token_counter,
            list(system_messages or []) + list(context_messages or []),
        ) + self._count_tools_with_fallback(token_counter, list(tools or []))

        if not use_baseline:
            return full

        baseline_idx, baseline_tokens = self._find_recent_usage_baseline(context_messages)
        if baseline_idx is None:
            return full

        delta_messages = context_messages[baseline_idx + 1:]
        via_baseline = baseline_tokens + self._estimate_messages(delta_messages, context)
        return max(full, via_baseline)

    def _truncate_summary_hard(
        self,
        summary: str,
        target_tokens: int,
        context: ModelContext,
    ) -> str:
        token_counter = context.token_counter()
        if self._count_text_tokens(summary, token_counter) <= target_tokens:
            return summary

        low, high = 0, len(summary)
        best = ""
        while low <= high:
            mid = (low + high) // 2
            candidate = summary[:mid]
            if self._count_text_tokens(candidate, token_counter) <= target_tokens:
                best = candidate
                low = mid + 1
            else:
                high = mid - 1
        if not best:
            return summary[: max(len(summary) // 4, 1)]
        if len(best) < len(summary):
            return best.rstrip() + "\n...[TRUNCATED]..."
        return best

    @staticmethod
    def _count_text_tokens(text: str, token_counter: Any) -> int:
        if token_counter is not None:
            try:
                return token_counter.count(text)
            except Exception as exc:
                logger.warning(
                    "[FullCompact] token_counter.count failed: %s, falling back to estimate",
                    exc,
                )
        return ContextUtils.estimate_tokens(text)

    def _build_fallback_summary(self, messages: List[BaseMessage]) -> str:
        lines = []
        for idx, msg in enumerate(messages[-20:], start=max(len(messages) - 19, 1)):
            lines.append(f"[{idx}] {msg.role}: {self._message_to_text(msg)}")
        return "Summary:\n" + "\n".join(lines)

    @staticmethod
    def _format_summary(content: str) -> str:
        stripped = re.sub(r"<analysis>[\s\S]*?</analysis>", "", content).strip()
        match = re.search(r"<summary>([\s\S]*?)</summary>", stripped)
        if match:
            return "Summary:\n" + match.group(1).strip()
        return stripped

    @staticmethod
    def _serialize_messages(messages: List[BaseMessage]) -> str:
        return "\n".join(f"role={msg.role}, content={FullCompactProcessor._message_to_text(msg)}" for msg in messages)

    @staticmethod
    def _message_to_text(message: BaseMessage) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        try:
            return json.dumps(content, ensure_ascii=False)
        except TypeError:
            return str(content)

    @staticmethod
    def _estimate_message_tokens(message: BaseMessage) -> int:
        return ContextUtils.estimate_message_tokens(message)

    def _find_last_compaction_boundary_index(self, messages: List[BaseMessage]) -> int:
        for idx in range(len(messages) - 1, -1, -1):
            if self._is_boundary_message(messages[idx]) or self._is_session_memory_boundary_message(messages[idx]):
                return idx
        return -1

    def _is_boundary_message(self, message: BaseMessage) -> bool:
        return (
            isinstance(message, SystemMessage)
            and isinstance(message.content, str)
            and message.content.startswith(self._marker)
        )

    def _is_state_message(self, message: BaseMessage) -> bool:
        return (
            isinstance(message, UserMessage)
            and isinstance(message.content, str)
            and message.content.startswith(self._state_marker)
        )

    def _is_session_memory_boundary_message(self, message: BaseMessage) -> bool:
        return (
            isinstance(message, SystemMessage)
            and isinstance(message.content, str)
            and message.content.startswith(self._session_memory_marker)
        )

    def _is_session_memory_summary_message(self, message: BaseMessage) -> bool:
        return (
            isinstance(message, UserMessage)
            and isinstance(message.content, str)
            and message.content.startswith(self._session_memory_intro)
        )

    def _is_synthetic_marker_message(self, message: BaseMessage) -> bool:
        return isinstance(message, UserMessage) and message.content == self._synthetic_user_marker

    @staticmethod
    def _build_head_tail_truncated_text(text: str, kept_chars: int) -> str:
        if kept_chars <= 0:
            return "...[TRUNCATED]..."
        head_chars = max(int(kept_chars * 0.2), 0)
        tail_chars = max(kept_chars - head_chars, 0)
        head = text[:head_chars]
        tail = text[-tail_chars:] if tail_chars > 0 else ""
        if head and tail:
            return f"{head}\n...[TRUNCATED]...\n{tail}"
        return head or tail or "...[TRUNCATED]..."

    def load_state(self, state: Dict[str, Any]) -> None:
        return

    def save_state(self) -> Dict[str, Any]:
        return {}
