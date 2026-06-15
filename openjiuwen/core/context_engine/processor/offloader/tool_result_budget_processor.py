# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, List, Literal, Tuple

from pydantic import BaseModel, Field

from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.processor.base import ContextEvent
from openjiuwen.core.context_engine.processor.offloader.message_offloader import (
    MessageOffloader,
)
from openjiuwen.core.context_engine.processor._protected import (
    is_protected,
    msg_in_window,
    resolve_active_window_message_ids,
)
from openjiuwen.core.context_engine.observability import write_context_trace
from openjiuwen.core.context_engine.schema.messages import OffloadToolMessage
from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage
from openjiuwen.core.common.logging import logger

PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"


class BudgetDensityProfile(BaseModel):
    """Density profile for ToolResultBudgetProcessor: tools + budget_multiplier."""

    tools: list[str] = Field(default_factory=list)
    budget_multiplier: float = Field(default=1.0, gt=0)


def get_budget_density(tool_name: str, profiles: dict[str, BudgetDensityProfile] | None = None) -> dict | None:
    """Look up a tool's budget density profile by name."""
    if not profiles:
        return None
    for density, profile in profiles.items():
        if tool_name in profile.tools:
            return {**profile.model_dump(), "density": density}
    return None


class ToolResultBudgetProcessorConfig(BaseModel):
    """Per-round budget control for large tool results."""

    tokens_threshold: int = Field(default=50000, gt=0)
    large_message_threshold: int = Field(default=10000, gt=0)
    trim_size: int = Field(default=3000, gt=0)
    tool_name_allowlist: list[str] | None = Field(default=None)
    offload_message_type: list[Literal["tool"]] = Field(default=["tool"])
    messages_threshold: int | None = Field(default=None, gt=0)
    messages_to_keep: int | None = Field(default=None, gt=0)
    adaptive_per_tool_budget: bool = Field(default=False, description="Enable per-tool density adaptive budget")
    density_profiles: dict[str, BudgetDensityProfile] = Field(default_factory=dict)


@ContextEngine.register_processor()
class ToolResultBudgetProcessor(MessageOffloader):
    """Offload oversized tool results round-by-round until each round fits budget."""

    @property
    def config(self) -> ToolResultBudgetProcessorConfig:
        return self._config

    async def trigger_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> bool:
        all_messages = context.get_messages() + messages_to_add
        return any(self._round_budget_exceeded(all_messages, context))

    async def on_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:

        self.sys_operation = kwargs.get("sys_operation")

        context_messages = context.get_messages() + messages_to_add
        write_context_trace(
            "context.processor.tool_result_budget.before",
            {
                "processor": self.processor_type(),
                "context_id": context.context_id(),
                "session_id": context.session_id(),
                "message_count_before": len(context_messages),
                "tokens_threshold": self.config.tokens_threshold,
                "large_message_threshold": self.config.large_message_threshold,
            },
        )
        context_size = len(context)
        updated_messages = list(context_messages)
        modified_indices: List[int] = []

        for round_range in self._iter_round_ranges(updated_messages):
            changed, new_indices = await self._shrink_round_to_budget(
                updated_messages,
                round_range,
                context,
            )
            if changed:
                modified_indices.extend(new_indices)

        if not modified_indices:
            return None, messages_to_add

        context.set_messages(updated_messages[:context_size])
        write_context_trace(
            "context.processor.tool_result_budget.after",
            {
                "processor": self.processor_type(),
                "context_id": context.context_id(),
                "session_id": context.session_id(),
                "modified_indices": sorted(set(modified_indices)),
                "message_count_after_context": len(updated_messages[:context_size]),
                "message_count_after_incoming": len(updated_messages[context_size:]),
            },
        )
        event = ContextEvent(
            event_type=self.processor_type(),
            messages_to_modify=sorted(set(modified_indices)),
        )
        return event, updated_messages[context_size:]

    def _round_budget_exceeded(
        self,
        messages: List[BaseMessage],
        context: ModelContext,
    ) -> List[Tuple[int, int]]:
        exceeded: List[Tuple[int, int]] = []
        for start_idx, end_idx in self._iter_round_ranges(messages):
            total_size = self._round_tool_result_size(messages, start_idx, end_idx, context)
            if total_size > self.config.tokens_threshold:
                candidates = self._collect_round_candidates(messages, start_idx, end_idx, context)
                if candidates:
                    exceeded.append((start_idx, end_idx))
        return exceeded

    def _iter_round_ranges(self, messages: List[BaseMessage]) -> List[Tuple[int, int]]:
        rounds = list(reversed(ContextUtils.find_all_dialogue_round(messages)))
        if not rounds:
            return []
        ranges: List[Tuple[int, int]] = []
        for user_idx, assistant_idx in rounds:
            start_idx = user_idx
            end_idx = assistant_idx if assistant_idx is not None else len(messages) - 1
            if start_idx is None or end_idx is None or start_idx > end_idx:
                continue
            ranges.append((start_idx, end_idx))
        return ranges

    def _round_tool_result_size(
        self,
        messages: List[BaseMessage],
        start_idx: int,
        end_idx: int,
        context: ModelContext,
    ) -> int:
        size = 0
        for idx in range(start_idx, end_idx + 1):
            msg = messages[idx]
            if isinstance(msg, ToolMessage):
                size += self._message_size(msg, context)
        return size

    def _message_size(self, message: ToolMessage, context: ModelContext) -> int:
        token_counter = context.token_counter()
        if token_counter is not None:
            try:
                return token_counter.count_messages([message])
            except Exception:
                return self._estimate_size(getattr(message, "content", ""))
        return self._estimate_size(getattr(message, "content", ""))

    @staticmethod
    def _estimate_size(content: Any) -> int:
        from openjiuwen.core.context_engine.context.context_utils import ContextUtils as _ContextUtils

        return _ContextUtils.estimate_tokens(content)

    def _density_adjusted_size(self, raw_size: int, tool_name: str | None) -> float:
        """Return message size adjusted by the tool's information density.

        When adaptive_per_tool_budget is enabled, low-density tools (verbose
        output like bash) get a smaller denominator, so their effective size
        is larger, and they are offloaded first. High-density tools (compact
        output like search) are preserved longer.

        Args:
            raw_size: The pre-computed token size of the message.
            tool_name: The tool name to look up density for.
        """
        if self.config.adaptive_per_tool_budget and tool_name:
            density = get_budget_density(tool_name, profiles=self.config.density_profiles)
            if density is not None:
                adjusted = raw_size / density["budget_multiplier"]
                logger.info(
                    "[ToolResultBudget] density sort: tool=%s, density=%s, multiplier=%.2f, raw_size=%d, adjusted=%.1f",
                    tool_name, density["density"], density["budget_multiplier"], raw_size, adjusted,
                )
                return adjusted
        return float(raw_size)

    def _allocate_per_tool_budget(
        self,
        messages: List[BaseMessage],
        start_idx: int,
        end_idx: int,
        context: ModelContext,
    ) -> dict[str, int] | None:
        """Allocate per-tool token budget when adaptive_per_tool_budget is enabled.

        Returns a dict mapping tool_name → token budget, or None when the
        feature is disabled (meaning the global tokens_threshold applies to
        all tools equally).

        The allocation is proportional to each tool's density-adjusted
        weight: low-density tools get a *smaller* share of the total budget
        (so they are squeezed earlier), while high-density tools get a
        *larger* share.
        """
        if not self.config.adaptive_per_tool_budget:
            return None

        per_tool_raw_size: dict[str, int] = defaultdict(int)
        for idx in range(start_idx, end_idx + 1):
            msg = messages[idx]
            if isinstance(msg, ToolMessage) and not self._is_already_offloaded(msg):
                tool_name = ContextUtils.resolve_tool_name_from_message(msg, messages)
                if tool_name:
                    per_tool_raw_size[tool_name] += self._message_size(msg, context)

        if not per_tool_raw_size:
            return None

        # Compute density-adjusted weight for each tool.
        # High-density → larger weight → larger budget share.
        # Low-density  → smaller weight → smaller budget share (squeezed first).
        per_tool_weight: dict[str, float] = {}
        for name, raw_size in per_tool_raw_size.items():
            density = get_budget_density(name, profiles=self.config.density_profiles)
            multiplier = density["budget_multiplier"] if density else 1.0
            # weight = raw_size * multiplier: high-density tools amplify their claim
            per_tool_weight[name] = raw_size * multiplier

        total_weight = sum(per_tool_weight.values())
        total_budget = self.config.tokens_threshold

        allocated = {
            name: max(1, int(total_budget * weight / total_weight))
            for name, weight in per_tool_weight.items()
        }

        details = []
        for name, budget in allocated.items():
            raw = per_tool_raw_size.get(name, 0)
            density = get_budget_density(name, profiles=self.config.density_profiles)
            d_label = density["density"] if density else "default"
            details.append(f"{name}({d_label}): {raw}→{budget}")
        logger.info("[ToolResultBudget] Budget allocated: total=%d, per_tool=[%s]", total_budget, ", ".join(details))

        return allocated

    async def _shrink_round_to_budget(
        self,
        messages: List[BaseMessage],
        round_range: Tuple[int, int],
        context: ModelContext,
    ) -> Tuple[bool, List[int]]:
        start_idx, end_idx = round_range
        modified_indices: List[int] = []
        changed = False

        per_tool_budget = self._allocate_per_tool_budget(messages, start_idx, end_idx, context)

        if per_tool_budget is not None:
            # adaptive mode: shrink each tool within its own budget first
            for tool_name, budget in per_tool_budget.items():
                tool_total = 0
                tool_indices: List[int] = []
                for idx in range(start_idx, end_idx + 1):
                    msg = messages[idx]
                    parse_name = ContextUtils.resolve_tool_name_from_message(msg, messages)
                    if isinstance(msg, ToolMessage) and parse_name == tool_name:
                        tool_total += self._message_size(msg, context)
                        tool_indices.append(idx)
                if tool_total <= budget:
                    continue
                # Offload this tool's largest results until within budget
                candidates = [(idx, self._message_size(messages[idx], context)) for idx in tool_indices
                              if self._should_offload_message(messages[idx], messages, context)]
                # density-adjusted sort; low-density tools offloaded first
                candidates.sort(
                    key=lambda item: self._density_adjusted_size(item[1], tool_name),
                    reverse=True,
                )
                while tool_total > budget and candidates:
                    target_idx, target_size = candidates.pop(0)
                    offloaded = await self._offload_tool_message(messages[target_idx], context)
                    messages[target_idx] = offloaded
                    modified_indices.append(target_idx)
                    tool_total -= target_size
                    changed = True

        # Global budget check: if total still exceeds threshold, offload globally
        while self._round_tool_result_size(messages, start_idx, end_idx, context) > self.config.tokens_threshold:
            candidates = self._collect_round_candidates(messages, start_idx, end_idx, context)
            if not candidates:
                break
            # density-adjusted sort using pre-computed sizes; low-density tools offloaded first
            candidates.sort(
                key=lambda item: self._density_adjusted_size(
                    item[1], ContextUtils.resolve_tool_name_from_message(messages[item[0]], messages)
                ),
                reverse=True,
            )
            target_idx, _ = candidates[0]
            offloaded = await self._offload_tool_message(messages[target_idx], context)
            messages[target_idx] = offloaded
            modified_indices.append(target_idx)
            changed = True

        return changed, modified_indices

    def _collect_round_candidates(
        self,
        messages: List[BaseMessage],
        start_idx: int,
        end_idx: int,
        context: ModelContext,
    ) -> List[Tuple[int, int]]:
        candidates: List[Tuple[int, int]] = []
        for idx in range(start_idx, end_idx + 1):
            msg = messages[idx]
            if self._should_offload_message(msg, messages, context):
                candidates.append((idx, self._message_size(msg, context)))
        return candidates

    def _should_offload_message(
        self,
        message: BaseMessage,
        context_messages: List[BaseMessage],
        context: ModelContext,
    ) -> bool:
        if not isinstance(message, ToolMessage):
            return False
        if self._is_already_offloaded(message):
            return False
        if not isinstance(getattr(message, "content", None), str):
            return False
        in_window_ids = resolve_active_window_message_ids(context, context_messages)
        if is_protected(message, in_active_window=msg_in_window(message, in_window_ids)):
            return False
        if self._is_allowlisted_tool_message(message, context_messages):
            return False
        return self._message_size(message, context) > self.config.large_message_threshold

    def _is_allowlisted_tool_message(
        self,
        message: BaseMessage,
        context_messages: List[BaseMessage],
    ) -> bool:
        allowlist = set(self.config.tool_name_allowlist or [])
        if not allowlist:
            return False
        tool_name = self._resolve_tool_name_from_message(message, context_messages)
        return bool(tool_name and tool_name in allowlist)

    @staticmethod
    def _is_already_offloaded(message: ToolMessage) -> bool:
        return isinstance(message, OffloadToolMessage)

    async def _offload_tool_message(
        self,
        message: ToolMessage,
        context: ModelContext,
    ) -> ToolMessage:
        preview = message.content[: self.config.trim_size]
        has_more = len(message.content) > self.config.trim_size
        persisted_content = self._build_persisted_output_message(
            original_size=len(message.content),
            offload_handle="pending",
            preview=preview,
            has_more=has_more,
        )

        meta = dict(getattr(message, "metadata", {}) or {})
        if meta.get("is_skill_body"):
            meta["is_skill_body"] = False
            meta["skill_body_offloaded"] = True
        offload_message = await self.offload_messages(
            role="tool",
            content=persisted_content,
            messages=[message],
            context=context,
            tool_call_id=message.tool_call_id,
            name=message.name,
            metadata=meta,
            sys_operation=self.sys_operation,
        )
        if offload_message is not None:
            actual_handle = getattr(offload_message, "offload_handle", "unknown")
            actual_offload_type = getattr(offload_message, "offload_type", "unknown")
            offload_message.content = self._build_persisted_output_message(
                original_size=len(message.content),
                offload_handle=f"[[OFFLOAD: handle={actual_handle}, type={actual_offload_type}]]",
                preview=preview,
                has_more=has_more,
            )
            return offload_message
        return message

    @staticmethod
    def _build_persisted_output_message(
        *,
        original_size: int,
        offload_handle: str,
        preview: str,
        has_more: bool,
    ) -> str:
        suffix = "\n...\n" if has_more else "\n"
        return (
            f"{PERSISTED_OUTPUT_TAG}\n"
            f"Output too large ({original_size} bytes)."
            f"Preview (first {len(preview)} chars):\n"
            f"{preview}{suffix}"
            f"{PERSISTED_OUTPUT_CLOSING_TAG}"
        )

    def load_state(self, state: Dict[str, Any]) -> None:
        return

    def save_state(self) -> Dict[str, Any]:
        return {}
