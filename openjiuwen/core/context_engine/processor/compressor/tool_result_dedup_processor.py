# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from pydantic import BaseModel, Field

from openjiuwen.core.context_engine.base import ModelContext, ContextWindow
from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.observability import write_context_trace
from openjiuwen.core.context_engine.processor.base import ContextEvent, ContextProcessor
from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage
from openjiuwen.core.common.logging import logger


class ToolResultDedupConfig(BaseModel):
    """Tool result semantic dedup & error folding configuration."""

    error_folding: bool = Field(
        default=True,
        description="Enable error result folding",
    )
    error_fold_min_consecutive: int = Field(
        default=2,
        ge=2,
        description="Minimum consecutive errors per tool to trigger folding",
    )
    error_pattern_max_length: int = Field(
        default=100,
        gt=0,
        description="Max chars for error pattern summary",
    )

    success_dedup: bool = Field(
        default=True,
        description="Enable success result semantic dedup",
    )
    dedup_similarity_threshold: float = Field(
        default=0.85,
        gt=0.5,
        le=1.0,
        description="Jaccard similarity threshold for high-overlap detection",
    )
    dedup_window_per_tool: int = Field(
        default=20,
        gt=0,
        description="Per-tool dedup window: only compare recent N results",
    )
    dedup_max_content_length: int = Field(
        default=50000,
        gt=0,
        description="Skip dedup for results longer than this (avoid expensive comparison)",
    )
    dedup_max_diff_chars: int = Field(
        default=500,
        gt=0,
        description="Max chars for diff section in high-overlap dedup marker",
    )


_ERROR_KEYWORDS = (
    "error:", "error：", "failed:", "exception:", "traceback",
    "errno", "exit code", "fatal error", "unhandled exception",
)

_NORMALIZE_PATTERNS = [
    (re.compile(r'/[\w/.-]+'), '<path>'),
    (re.compile(r'\d{4,}'), '<num>'),
    (re.compile(r'0x[0-9a-f]+'), '<hex>'),
    (re.compile(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}'), '<ip>'),
]


@ContextEngine.register_processor()
class ToolResultDedupProcessor(ContextProcessor):
    """Tool result semantic dedup & error folding processor.

    Processing order: a (error folding) then b (success dedup).
    Trigger: API round boundary (``_api_round`` gate), aligned with S1.
    Chain position: after MicroCompact, before FullCompact.
    """

    @property
    def config(self) -> ToolResultDedupConfig:
        return self._config

    async def trigger_add_messages(
            self,
            context: ModelContext,
            messages_to_add: List[BaseMessage],
            **kwargs: Any,
    ) -> bool:
        all_messages = context.get_messages() + messages_to_add
        if not self._api_round(all_messages):
            return False
        has_errors = self._has_foldable_errors(all_messages)
        has_dedup = self._has_dedup_candidates(all_messages)
        if has_errors or has_dedup:
            logger.info(
                "trigger_add_messages=True: has_foldable_errors=%s has_dedup_candidates=%s "
                "error_folding=%s success_dedup=%s message_count=%s",
                has_errors, has_dedup,
                self.config.error_folding, self.config.success_dedup,
                len(all_messages),
            )
        return has_errors or has_dedup

    async def on_add_messages(
            self,
            context: ModelContext,
            messages_to_add: List[BaseMessage],
            **kwargs: Any,
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        all_messages = context.get_messages() + messages_to_add
        logger.info("on_add_messages enter: context_id=%s session_id=%s message_count=%d error_folding=%s "
                    "success_dedup=%s", context.context_id(), context.session_id(), len(all_messages),
                    self.config.error_folding, self.config.success_dedup)

        write_context_trace(
            "context.processor.tool_result_dedup.before",
            {
                "processor": self.processor_type(),
                "context_id": context.context_id(),
                "session_id": context.session_id(),
                "message_count_before": len(all_messages),
                "error_folding": self.config.error_folding,
                "success_dedup": self.config.success_dedup,
            },
        )

        modified_indices: List[int] = []

        if self.config.error_folding:
            error_indices = self._fold_error_results(all_messages)
            modified_indices.extend(error_indices)

        if self.config.success_dedup:
            dedup_indices = self._dedup_success_results(all_messages)
            modified_indices.extend(dedup_indices)

        if not modified_indices:
            logger.info("on_add_messages: no modifications needed")
            return None, messages_to_add

        context.set_messages(all_messages)
        logger.info("on_add_messages: modified %d messages (error_fold=%d, success_dedup=%d)",
                    len(modified_indices), len(error_indices) if self.config.error_folding else 0,
                    len(dedup_indices) if self.config.success_dedup else 0)
        write_context_trace(
            "context.processor.tool_result_dedup.after",
            {
                "processor": self.processor_type(),
                "context_id": context.context_id(),
                "session_id": context.session_id(),
                "modified_indices": modified_indices,
                "message_count_after": len(all_messages),
            },
        )
        event = ContextEvent(
            event_type=self.processor_type(),
            messages_to_modify=modified_indices,
        )
        return event, []

    # ==================== GET path (context window construction) ====================

    async def trigger_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs: Any,
    ) -> bool:
        """Check for foldable errors / dedup candidates on the GET path.

        This catches cases where the ADD path was skipped due to
        ``_api_round`` gating (e.g. messages added mid-round during
        chained tool calls).  The GET path has no round restriction
        since we are about to send the window to the LLM.
        """
        messages = list(context_window.context_messages or [])
        has_errors = self._has_foldable_errors(messages)
        has_dedup = self._has_dedup_candidates(messages)
        if has_errors or has_dedup:
            logger.info("trigger_get_context_window=True: has_foldable_errors=%s has_dedup_candidates=%s "
                        "error_folding=%s success_dedup=%s message_count=%s", has_errors, has_dedup,
                        self.config.error_folding, self.config.success_dedup, len(messages))
        return has_errors or has_dedup

    async def on_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs: Any,
    ) -> Tuple[ContextEvent | None, ContextWindow]:
        """Execute dedup / folding on the GET path.

        Operates on ``context_window.context_messages`` directly and
        returns a new ``ContextWindow`` with the modified messages.
        Also updates the persistent context via ``context.set_messages``
        so the changes persist across subsequent GET calls.
        """
        messages = list(context_window.context_messages or [])
        if not messages:
            return None, context_window

        logger.info("on_get_context_window enter: context_id=%s session_id=%s message_count=%d  error_folding=%s "
                    "success_dedup=%s", context.context_id(), context.session_id(), len(messages),
                    self.config.error_folding, self.config.success_dedup)

        modified_indices: List[int] = []

        if self.config.error_folding:
            error_indices = self._fold_error_results(messages)
            modified_indices.extend(error_indices)

        if self.config.success_dedup:
            dedup_indices = self._dedup_success_results(messages)
            modified_indices.extend(dedup_indices)

        if not modified_indices:
            logger.info("on_get_context_window: no modifications needed")
            return None, context_window

        context.set_messages(messages)
        logger.info("on_get_context_window: modified %d messages (error_fold=%d, success_dedup=%d)",
            len(modified_indices),
            len(error_indices) if self.config.error_folding else 0,
            len(dedup_indices) if self.config.success_dedup else 0,
        )
        new_window = ContextWindow(
            system_messages=list(context_window.system_messages or []),
            context_messages=messages,
            tools=context_window.tools or [],
        )
        event = ContextEvent(
            event_type=self.processor_type(),
            messages_to_modify=modified_indices,
        )
        return event, new_window

    # ==================== a: Error Folding ====================

    def _has_foldable_errors(self, messages: List[BaseMessage]) -> bool:
        for _, indices in self._group_errors_by_tool(messages).items():
            if len(indices) >= self.config.error_fold_min_consecutive:
                return True
        return False

    def _fold_error_results(self, messages: List[BaseMessage]) -> List[int]:
        modified: List[int] = []
        for tool_name, error_indices in self._group_errors_by_tool(messages).items():
            if len(error_indices) < self.config.error_fold_min_consecutive:
                continue

            total = len(error_indices)
            earlier_indices = error_indices[:-1]
            latest_idx = error_indices[-1]

            earlier_msgs = [messages[i] for i in earlier_indices]
            patterns = self._extract_error_patterns(earlier_msgs)

            for rank, idx in enumerate(earlier_indices, start=1):
                msg = messages[idx]
                pattern = self._match_error_pattern(msg.content, patterns)
                marker = f"[Earlier error attempt {rank}/{total}: {pattern}]"
                if msg.content != marker:
                    logger.info(
                        "Fold error: tool=%s attempt %d/%d tool_call_id=%s original_len=%d -> marker_len=%d",
                        tool_name, rank, total, getattr(msg, "tool_call_id", "?"),
                        len(msg.content) if isinstance(msg.content, str) else 0, len(marker),
                    )
                    messages[idx] = msg.model_copy(update={"content": marker})
                    modified.append(idx)

            latest_msg = messages[latest_idx]
            prefix = f"[Latest error (attempt {total}/{total}):] "
            if not latest_msg.content.startswith(prefix):
                new_content = prefix + latest_msg.content
                logger.info(
                    "Keep latest error: tool=%s attempt %d/%d tool_call_id=%s content_len=%d",
                    tool_name, total, total, getattr(latest_msg, "tool_call_id", "?"),
                    len(latest_msg.content) if isinstance(latest_msg.content, str) else 0,
                )
                messages[latest_idx] = latest_msg.model_copy(update={"content": new_content})
                modified.append(latest_idx)

        return modified

    def _group_errors_by_tool(self, messages: List[BaseMessage]) -> Dict[str, List[int]]:
        """Group all error ToolMessage indices by tool name.

        Note: this collects ALL errors for a tool regardless of whether
        they are consecutive in the message list (interspersed success
        results from other tools don't break the grouping). This is
        intentional — all errors from the same tool should be folded
        together, not just strictly consecutive ones.

        Skips messages already replaced by MicroCompact (cleared_marker)
        since those no longer carry useful error content.
        """
        groups: Dict[str, List[int]] = defaultdict(list)
        for i, msg in enumerate(messages):
            if not isinstance(msg, ToolMessage):
                continue
            if self._is_cleared_marker(msg.content):
                continue
            tool_name = ContextUtils.resolve_tool_name_from_message(msg, messages)
            if not tool_name:
                continue
            if self._is_error_tool_message(msg):
                groups[tool_name].append(i)
        return dict(groups)

    @staticmethod
    def _is_error_content(content: str) -> bool:
        """Heuristic check whether a ToolMessage content represents an error result.

        Detection priority:
        1. Already-folded markers → False (don't re-collect)
        2. Keyword matching → True if any error keyword found

        Known limitations:
        - Pure keyword matching can produce false positives (e.g. code content
          containing "error:") and false negatives (e.g. non-English errors).
        - When ``AbilityExecutionError`` starts populating ``ToolMessage.metadata["is_error"]``
          in a future agent-core release, this method should be updated to check
          metadata first and fall back to keywords only when metadata is absent.
        """
        if not isinstance(content, str):
            return False
        if content.startswith("[Earlier error") or content.startswith("[Latest error"):
            return False
        lower = content[:500].lower()
        return any(kw in lower for kw in _ERROR_KEYWORDS)

    @staticmethod
    def _is_error_tool_message(msg: ToolMessage) -> bool:
        """Check whether a ToolMessage represents an error result.

        Detection priority:
        1. ``metadata["is_error"]`` → definitive signal (set by AbilityExecutionError)
        2. Already-folded markers → False
        3. Keyword matching on content → heuristic fallback
        """
        metadata = getattr(msg, "metadata", None)
        if isinstance(metadata, dict):
            if metadata.get("is_error") is True:
                return True
            if metadata.get("is_error") is False:
                return False
        return ToolResultDedupProcessor._is_error_content(msg.content)

    def _extract_error_patterns(self, error_messages: List[ToolMessage]) -> List[str]:
        patterns: List[str] = []
        seen: set[str] = set()
        max_len = self.config.error_pattern_max_length
        for msg in error_messages:
            lines = msg.content.strip().split("\n")
            core = lines[0][:max_len] if lines else msg.content[:max_len]
            normalized = core
            for regex, replacement in _NORMALIZE_PATTERNS:
                normalized = regex.sub(replacement, normalized)
            if normalized not in seen:
                seen.add(normalized)
                patterns.append(normalized)
        return patterns

    @staticmethod
    def _normalize_error_core(content: str, max_len: int = 100) -> str:
        """Normalize the core error line for pattern matching."""
        lines = content.strip().split("\n")
        core = lines[0][:max_len] if lines else content[:max_len]
        for regex, replacement in _NORMALIZE_PATTERNS:
            core = regex.sub(replacement, core)
        return core

    def _match_error_pattern(self, content: str, patterns: List[str]) -> str:
        """Find the best-matching pattern for the given error content."""
        if not patterns:
            return "unknown error"
        normalized = self._normalize_error_core(content, self.config.error_pattern_max_length)
        if normalized in patterns:
            return normalized
        return patterns[0]

    # ==================== b: Success Dedup ====================

    def _has_dedup_candidates(self, messages: List[BaseMessage]) -> bool:
        for _, indices in self._group_success_by_tool(messages).items():
            if len(indices) >= 2:
                return True
        return False

    def _dedup_success_results(self, messages: List[BaseMessage]) -> List[int]:
        """Dedup success results: keep the **latest** complete, fold earlier ones.

        When duplicates are found, earlier results are replaced with reference
        markers pointing to the latest result — consistent with error folding
        where the latest error is kept complete and earlier ones are folded.
        This ensures LLM always sees the most current state.
        """
        modified: List[int] = []
        for tool_name, tool_indices in self._group_success_by_tool(messages).items():
            window = tool_indices[-self.config.dedup_window_per_tool:]
            if len(window) < 2:
                continue

            latest_idx = window[-1]
            latest_msg = messages[latest_idx]
            if (not isinstance(latest_msg.content, str) or
                    len(latest_msg.content) > self.config.dedup_max_content_length):
                continue

            for j in range(len(window) - 1):
                earlier_idx = window[j]
                earlier_msg = messages[earlier_idx]
                if self._is_already_deduped(earlier_msg.content):
                    continue
                if (not isinstance(earlier_msg.content, str) or
                        len(earlier_msg.content) > self.config.dedup_max_content_length):
                    continue

                similarity = self._compute_similarity(earlier_msg.content, latest_msg.content)

                if similarity == 1.0:
                    latest_call_id = getattr(latest_msg, "tool_call_id", "?")
                    marker = f"[Earlier same result as tool_call_{latest_call_id}, {tool_name}]"
                    if earlier_msg.content != marker:
                        messages[earlier_idx] = earlier_msg.model_copy(update={"content": marker})
                        modified.append(earlier_idx)
                        logger.info(
                            "Exact dedup (fold earlier): tool=%s, earlier_call_id=%s -> ref latest tool_call_%s",
                            tool_name, getattr(earlier_msg, "tool_call_id", "?"), latest_call_id,
                        )

                elif similarity >= self.config.dedup_similarity_threshold:
                    latest_call_id = getattr(latest_msg, "tool_call_id", "?")
                    pct = int(similarity * 100)
                    diff = self._extract_diff(
                        latest_msg.content, earlier_msg.content,
                        max_diff_chars=self.config.dedup_max_diff_chars,
                    )
                    marker = (
                        f"[Earlier similar result to tool_call_{latest_call_id} "
                        f"({pct}% overlap). Diff:\n{diff}]"
                    )
                    if earlier_msg.content != marker:
                        messages[earlier_idx] = earlier_msg.model_copy(update={"content": marker})
                        modified.append(earlier_idx)
                        logger.info(
                            "Similar dedup (fold earlier): tool=%s, earlier_call_id=%s -> ref latest tool_call_%s,"
                            "similarity=%.2f", tool_name, getattr(earlier_msg, "tool_call_id", "?"),
                            latest_call_id, similarity)

        return modified

    def _group_success_by_tool(self, messages: List[BaseMessage]) -> Dict[str, List[int]]:
        """Group non-error, non-folded, non-cleared ToolMessage indices by tool name.

        Skips messages already replaced by MicroCompact (cleared_marker)
        since those no longer carry useful result content.
        """
        groups: Dict[str, List[int]] = defaultdict(list)
        for i, msg in enumerate(messages):
            if not isinstance(msg, ToolMessage):
                continue
            if self._is_cleared_marker(msg.content):
                continue
            tool_name = ContextUtils.resolve_tool_name_from_message(msg, messages)
            if not tool_name:
                continue
            if self._is_error_tool_message(msg):
                continue
            if self._is_folded_marker(msg.content):
                continue
            groups[tool_name].append(i)
        return dict(groups)

    @staticmethod
    def _is_already_deduped(content: str) -> bool:
        if not isinstance(content, str):
            return False
        return (
            content.startswith("[Earlier same result as")
            or content.startswith("[Earlier similar result to")
            or content.startswith("[Same result as")
            or content.startswith("[Similar to")
        )

    @staticmethod
    def _is_folded_marker(content: str) -> bool:
        """Check if content is a folded error marker from S8a."""
        if not isinstance(content, str):
            return False
        return content.startswith("[Earlier error") or content.startswith("[Latest error")

    @staticmethod
    def _is_cleared_marker(content: str) -> bool:
        """Check if content is a cleared marker from MicroCompact.

        MicroCompact replaces old tool results with markers like
        ``[Old tool result content cleared]``.  S8 should skip these
        since they no longer carry useful content for dedup or folding.
        """
        if not isinstance(content, str):
            return False
        return content.startswith("[Old tool result") or "content cleared" in content

    @staticmethod
    def _compute_similarity(content_a: str, content_b: str) -> float:
        if not content_a or not content_b:
            return 0.0
        if content_a == content_b:
            return 1.0
        len_a, len_b = len(content_a), len(content_b)
        if max(len_a, len_b) > min(len_a, len_b) * 3:
            return 0.0
        lines_a = set(content_a.splitlines())
        lines_b = set(content_b.splitlines())
        intersection = len(lines_a & lines_b)
        union = len(lines_a | lines_b)
        return intersection / union if union > 0 else 0.0

    @staticmethod
    def _extract_diff(content_new: str, content_old: str, max_diff_chars: int = 500) -> str:
        old_lines = set(content_old.splitlines())
        new_lines = set(content_new.splitlines())
        added = new_lines - old_lines
        removed = old_lines - new_lines

        diff_parts: List[str] = []
        for line in sorted(added)[:10]:
            diff_parts.append(f"  + {line[:100]}")
        for line in sorted(removed)[:10]:
            diff_parts.append(f"  - {line[:100]}")

        diff_text = "\n".join(diff_parts)
        if len(diff_text) > max_diff_chars:
            diff_text = diff_text[:max_diff_chars] + "\n  ..."
        return diff_text

    def load_state(self, state: Dict[str, Any]) -> None:
        pass

    def save_state(self) -> Dict[str, Any]:
        return {}
