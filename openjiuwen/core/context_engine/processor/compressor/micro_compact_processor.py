# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Literal, Tuple

from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.processor.base import ContextEvent, ContextProcessor
from openjiuwen.core.foundation.llm import (
    BaseMessage,
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    SystemMessage,
    ToolMessage,
    UserMessage,
)


MICRO_COMPACT_CLEARED_MARKER = "[Old tool result content cleared]"
MICRO_COMPACT_SUMMARY_PREFIX = "[Tool result summarized] "

PER_TOOL_SUMMARY_PROMPT = """CRITICAL: You are given the historical results of one tool call series.

Produce a concise factual digest (plain text, no tool calls) that preserves:
- what was queried or executed, and against which targets;
- key findings, numbers, names and decisions the agent still needs;
- details that would be mis-invented if missing (paths, ids, contract terms).

Keep the digest under 500 characters. If the results are empty or redundant,
say so in one short sentence."""


class MicroCompactProcessorConfig(BaseModel):
    """Clear stale tool results while keeping recent ones per tool.

    ``compact_strategy`` selects between in-place marker clearing and a
    per-tool LLM summary that preserves the semantic content of removed
    results. Semantic-critical tools are never marker-cleared: in marker
    mode they are kept verbatim, in summary mode they are summarized.
    """

    trigger_threshold: int = Field(
        default=5,
        gt=0,
        description=(
            "Clear stale results only after a tool has more than this many clearable results beyond the kept tail."
        ),
    )
    compactable_tool_names: list[str] = Field(
        default=["grep", "glob", "read_file", "web_search", "web_fetch"],
        description="Tool names whose older ToolMessage contents may be cleared.",
    )
    keep_recent_per_tool: int = Field(
        default=15,
        ge=0,
        description="Number of most-recent ToolMessage contents preserved for each compactable tool.",
    )
    cleared_marker: str = Field(
        default=MICRO_COMPACT_CLEARED_MARKER,
        description="Replacement content used when clearing stale ToolMessage contents.",
    )
    compact_strategy: Literal["marker", "summary"] = Field(
        default="marker",
        description=(
            "marker replaces stale results with a fixed placeholder; "
            "summary replaces them with a per-tool LLM digest."
        ),
    )
    semantic_critical_tool_names: list[str] = Field(
        default=["ontology_context", "execute_script", "business_data_search"],
        description=(
            "Tools whose results are semantically critical: never marker-cleared, "
            "only summarized in summary mode."
        ),
    )
    summary_prefix: str = Field(
        default=MICRO_COMPACT_SUMMARY_PREFIX,
        description="Prefix prepended to LLM-generated per-tool summaries.",
    )
    summary_max_chars_per_tool: int = Field(
        default=20000,
        gt=0,
        description="Maximum characters of raw tool results fed to one per-tool summary call.",
    )
    model: ModelRequestConfig | None = Field(default=None)
    """Model request configuration used by the per-tool summarizer."""

    model_client: ModelClientConfig | None = Field(default=None)
    """Client configuration used to create the per-tool summarizer model."""


@ContextEngine.register_processor()
class MicroCompactProcessor(ContextProcessor):
    def __init__(self, config: MicroCompactProcessorConfig):
        super().__init__(config)
        self._critical_names = set(config.semantic_critical_tool_names)
        self._model: Model | None = None
        if config.model is not None and config.model_client is not None:
            self._model = Model(config.model_client, config.model)
        for tool_name in set(config.compactable_tool_names) & self._critical_names:
            logger.warning(
                "[MicroCompact] semantic-critical tool %r registered in compactable_tool_names; "
                "it is protected from marker clearing",
                tool_name,
            )

    @property
    def config(self) -> MicroCompactProcessorConfig:
        return self._config

    def rebind_model(
        self,
        *,
        model: Any = None,
        model_config: Any = None,
        model_client_config: Any = None,
    ) -> bool:
        """Refresh the model used for per-tool summary compaction."""
        return self._rebind_model_reference(
            model=model,
            model_config=model_config,
            model_client_config=model_client_config,
        )

    async def trigger_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> bool:
        all_messages = context.get_messages() + messages_to_add
        if not self._api_round(all_messages):
            return False
        return self._has_any_tool_exceed_threshold(all_messages)

    async def on_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        all_messages = context.get_messages() + messages_to_add
        indices_to_clear = self._collect_flat_indices_for_compact(
            all_messages,
            force=kwargs.get("force", False),
        )

        if not indices_to_clear:
            return None, messages_to_add

        if self.config.compact_strategy == "summary":
            modified_indices = await self._apply_summary_compaction(all_messages, indices_to_clear)
        else:
            modified_indices = self._apply_marker_compaction(all_messages, indices_to_clear)

        if not modified_indices:
            return None, messages_to_add

        context.set_messages(all_messages)
        return ContextEvent(event_type=self.processor_type(), messages_to_modify=modified_indices), []

    def _is_summarized_content(self, content: Any) -> bool:
        """Return whether a ToolMessage content is an already-written per-tool digest.

        ``BaseMessage.content`` may be a list for multimodal tool results; only
        string contents can carry the summary prefix (same convention as the
        sibling compressors, see ``compressor/util.py``).
        """
        return isinstance(content, str) and content.startswith(self.config.summary_prefix)

    def _apply_marker_compaction(
        self,
        messages: List[BaseMessage],
        indices_to_clear: List[int],
    ) -> List[int]:
        modified_indices: List[int] = []
        for index in indices_to_clear:
            message = messages[index]
            if not isinstance(message, ToolMessage):
                continue
            if message.content == self.config.cleared_marker:
                continue
            if self._is_summarized_content(message.content):
                continue
            messages[index] = message.model_copy(update={"content": self.config.cleared_marker})
            modified_indices.append(index)
        return modified_indices

    async def _apply_summary_compaction(
        self,
        messages: List[BaseMessage],
        indices_to_clear: List[int],
    ) -> List[int]:
        grouped = self._group_indices_by_tool(messages, indices_to_clear)
        modified_indices: List[int] = []
        for tool_name, tool_indices in grouped.items():
            contents = [messages[index].content for index in tool_indices]
            summary = await self._generate_tool_summary(tool_name, contents)
            if summary is None and tool_name in self._critical_names:
                # Semantic-critical results are never marker-cleared; keep them verbatim.
                continue
            # Write the digest once, on the most recent cleared message, and use a
            # short placeholder for the rest: duplicating the digest N times would
            # inflate the context instead of shrinking it.
            anchor_index = tool_indices[-1]
            for index in tool_indices:
                message = messages[index]
                if not isinstance(message, ToolMessage):
                    continue
                if message.content == self.config.cleared_marker:
                    continue
                if self._is_summarized_content(message.content):
                    continue
                if summary is not None and index == anchor_index:
                    content = self.config.summary_prefix + summary
                else:
                    content = self.config.cleared_marker
                messages[index] = message.model_copy(update={"content": content})
                modified_indices.append(index)
        return modified_indices

    async def _generate_tool_summary(self, tool_name: str, contents: List[str]) -> str | None:
        if self._model is None:
            logger.warning(
                "[MicroCompact] summary strategy needs model/model_client config; cannot summarize %s",
                tool_name,
            )
            return None
        serialized = self._serialize_tool_contents(tool_name, contents)
        if not serialized:
            return None
        prompt_messages = [
            SystemMessage(content=PER_TOOL_SUMMARY_PROMPT),
            UserMessage(content=serialized),
        ]
        try:
            response = await self._model.invoke(messages=prompt_messages, tools=None)
            content = (response.content or "").strip()
            return content or None
        except Exception as exc:
            logger.warning(
                "[MicroCompact] per-tool summary generation failed for %s: %s",
                tool_name,
                exc,
                exc_info=True,
            )
            return None

    def _serialize_tool_contents(self, tool_name: str, contents: List[str]) -> str:
        parts = [f"Tool: {tool_name}", f"Historical results ({len(contents)} entries, oldest first):"]
        # Keep the most recent entries when the budget is exceeded: newer results
        # are the ones the agent is most likely to still need.
        used = 0
        kept: List[Tuple[int, str]] = []
        for index in range(len(contents) - 1, -1, -1):
            content = contents[index]
            head = content[: self.config.summary_max_chars_per_tool]
            used += len(head) + 32
            kept.append((index, head))
            if used >= self.config.summary_max_chars_per_tool:
                break
        for index, head in reversed(kept):
            parts.append(f"[{index + 1}] {head}")
        return "\n".join(parts)

    def _group_indices_by_tool(
        self,
        messages: List[BaseMessage],
        indices: List[int],
    ) -> dict[str, List[int]]:
        grouped: dict[str, List[int]] = defaultdict(list)
        for index in indices:
            message = messages[index]
            if not isinstance(message, ToolMessage):
                continue
            tool_name = ContextUtils.resolve_tool_name_from_message(message, messages)
            grouped[tool_name].append(index)
        return dict(grouped)

    def _collect_compactable_indices_by_tool(
        self,
        messages: List[BaseMessage],
    ) -> dict[str, List[int]]:
        allowed_names = set(self.config.compactable_tool_names)
        summary_mode = self.config.compact_strategy == "summary"
        result: dict[str, List[int]] = defaultdict(list)

        for index, message in enumerate(messages):
            if not isinstance(message, ToolMessage):
                continue
            if not isinstance(message.content, str):
                # Multimodal tool results (list content) never participate in
                # compaction, matching the sibling compressors' convention.
                continue
            if message.content == self.config.cleared_marker:
                continue
            if self._is_summarized_content(message.content):
                continue
            tool_name = ContextUtils.resolve_tool_name_from_message(message, messages)
            if tool_name not in allowed_names:
                continue
            if tool_name in self._critical_names and not summary_mode:
                # Semantic-critical tools are protected from marker clearing.
                continue
            result[tool_name].append(index)

        return dict(result)

    def _has_any_tool_exceed_threshold(
        self,
        messages: List[BaseMessage],
    ) -> bool:
        grouped_indices = self._collect_compactable_indices_by_tool(messages)
        return any(
            len(indices) > self._config.trigger_threshold + self._config.keep_recent_per_tool
            for indices in grouped_indices.values()
        )

    def _collect_flat_indices_for_compact(
        self,
        messages: List[BaseMessage],
        *,
        force: bool = False,
    ) -> List[int]:
        grouped = self._collect_compactable_indices_by_tool(messages)
        result = []
        for indices in grouped.values():
            threshold = self._config.keep_recent_per_tool if force else (
                self._config.trigger_threshold + self._config.keep_recent_per_tool
            )
            if len(indices) > threshold:
                result.extend(indices[:-self._config.keep_recent_per_tool] if self._config.keep_recent_per_tool
                              else indices)
        return result

    def load_state(self, state: Dict[str, Any]) -> None:
        pass

    def save_state(self) -> Dict[str, Any]:
        return {}
