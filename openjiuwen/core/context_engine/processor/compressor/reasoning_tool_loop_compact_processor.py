# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compact consecutive identical reasoning/tool or tool/args rounds in context."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.processor.base import ContextEvent, ContextProcessor
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    BaseMessage,
    ToolMessage,
)

_REASONING_LOOP_WARNING_TEMPLATE_CN = """\
检测到连续多轮返回思考内容相同且调用了相同的工具集。
重复的思考内容预览：
---
{reasoning_preview}
---
工具调用命令如下：
---
{tool_calls}
---
工具执行结果如下：
---
{tool_results}
---
**请跳出多轮重复执行，推进尚未完成的实质工作。**
"""

_REASONING_LOOP_WARNING_TEMPLATE_EN = """\
Detected consecutive turns with identical reasoning_content and the same tool name set.
Repeated reasoning preview:
---
{reasoning_preview}
---
Tool calls:
---
{tool_calls}
---
Tool results:
---
{tool_results}
---
**Break out of the multi-turn repeated execution, and make real progress on unfinished work.**
"""

_TOOL_ARGS_LOOP_WARNING_TEMPLATE_CN = """\
检测到连续多轮调用了相同的工具集，且每个工具的入参也相同。
工具调用命令如下：
---
{tool_calls}
---
工具执行结果如下：
---
{tool_results}
---
**请跳出多轮重复工具调用，推进尚未完成的实质工作。**
"""

_TOOL_ARGS_LOOP_WARNING_TEMPLATE_EN = """\
Detected consecutive turns calling the same tool set with identical arguments.
Tool calls:
---
{tool_calls}
---
Tool results:
---
{tool_results}
---
**Break out of the multi-turn repeated execution, and make real progress on unfinished work.**
"""

_REASONING_LOOP_WARNING_TEMPLATES = {
    "cn": _REASONING_LOOP_WARNING_TEMPLATE_CN,
    "en": _REASONING_LOOP_WARNING_TEMPLATE_EN,
}

_TOOL_ARGS_LOOP_WARNING_TEMPLATES = {
    "cn": _TOOL_ARGS_LOOP_WARNING_TEMPLATE_CN,
    "en": _TOOL_ARGS_LOOP_WARNING_TEMPLATE_EN,
}

# Session-state keys holding how many times each match rule has folded a loop
# within the current agent invoke. A rail reads these counters to decide
# whether to bail out (raise) when the model keeps looping after compaction.
LOOP_COMPACT_BAILOUT_STATE_KEY = "reasoning_tool_loop_compact_count"
TOOL_ARGS_LOOP_COMPACT_BAILOUT_STATE_KEY = "tool_call_args_loop_compact_count"

_CompactKind = Literal["reasoning_tools", "tool_args"]


class ReasoningToolLoopCompactProcessorConfig(BaseModel):
    """Detect and compact consecutive identical reasoning/tool or tool/args rounds."""

    enabled: bool = Field(
        default=True,
        description="Enable consecutive loop compaction.",
    )
    consecutive_threshold: int = Field(
        default=3,
        ge=2,
        description=(
            "Trigger compaction when this many consecutive completed tool rounds "
            "share identical reasoning_content and identical tool name set."
        ),
    )
    tool_args_consecutive_threshold: int = Field(
        default=5,
        ge=2,
        description=(
            "Trigger compaction when this many consecutive completed tool rounds "
            "share the same tool name set and the same (truncated) arguments. "
            "Independent of reasoning_content."
        ),
    )
    arguments_max_chars: int = Field(
        default=2048,
        ge=64,
        description=(
            "Max characters of each tool's canonical JSON arguments used for "
            "fingerprint matching. Larger payloads are truncated before compare."
        ),
    )
    reasoning_min_chars: int = Field(
        default=4,
        ge=1,
        description="Ignore rounds whose reasoning_content is shorter than this after strip.",
    )
    reasoning_preview_max_chars: int = Field(
        default=512,
        ge=1,
        description="Max characters of reasoning kept inside the loop warning AssistantMessage.",
    )
    language: str = Field(
        default="cn",
        description="Loop warning language ('cn' or 'en').",
    )
    bailout_threshold: int = Field(
        default=3,
        ge=0,
        description=(
            "Raise a bail-out error once this many reasoning+tool-name loop "
            "compactions have been triggered within a single agent invoke. "
            "Set to 0 to disable. The actual raise is performed by a rail."
        ),
    )
    tool_args_bailout_threshold: int = Field(
        default=2,
        ge=0,
        description=(
            "Raise a bail-out error once this many tool-set+arguments loop "
            "compactions have been triggered within a single agent invoke. "
            "Set to 0 to disable. The actual raise is performed by a rail."
        ),
    )


@dataclass(frozen=True)
class _ToolRound:
    start: int
    end: int
    # (reasoning, tool_names) when reasoning is long enough; else None.
    reasoning_fingerprint: Optional[Tuple[str, Tuple[str, ...]]]
    # Sorted (tool_name, truncated_args_fingerprint) pairs.
    args_fingerprint: Tuple[Tuple[str, str], ...]
    reasoning: str
    tool_names: Tuple[str, ...]


@ContextEngine.register_processor()
class ReasoningToolLoopCompactProcessor(ContextProcessor):
    """Fold consecutive identical reasoning/tool or tool/args rounds.

    Two independent match rules:

    1. reasoning + tool names (legacy):
         - reasoning_content identical (after strip, and long enough)
         - tool name set identical (arguments ignored)
         - threshold: ``consecutive_threshold`` (default 3)

    2. tool names + arguments:
         - tool name multiset identical
         - each tool's arguments identical after canonical JSON + truncation
         - reasoning ignored
         - threshold: ``tool_args_consecutive_threshold`` (default 5)

    Compaction runs on the ADD path only (after tool results are committed):
      - delete all matched duplicated rounds
      - insert one AssistantMessage warning with the latest round's calls/results
    """

    @property
    def config(self) -> ReasoningToolLoopCompactProcessorConfig:
        return self._config

    async def trigger_add_messages(
            self,
            context: ModelContext,
            messages_to_add: List[BaseMessage],
            **kwargs: Any,
    ) -> bool:
        if not self.config.enabled:
            return False
        all_messages = context.get_messages() + messages_to_add
        if not self._api_round(all_messages):
            return False
        return self._find_compact_range(all_messages) is not None

    async def on_add_messages(
            self,
            context: ModelContext,
            messages_to_add: List[BaseMessage],
            **kwargs: Any,
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        all_messages = context.get_messages() + messages_to_add
        compacted, kind = self._compact_messages(all_messages)
        if compacted is None or kind is None:
            return None, messages_to_add

        context.set_messages(compacted)
        self._record_bailout_signal(context, kind)
        logger.info(
            "[ReasoningToolLoopCompact] compacted consecutive identical "
            "%s rounds on ADD path: before=%d after=%d",
            kind,
            len(all_messages),
            len(compacted),
        )
        return ContextEvent(
            event_type=self.processor_type(),
            compact_summary=f"reasoning_tool_loop_compacted:{kind}",
        ), []

    def load_state(self, state: Dict[str, Any]) -> None:
        return

    def save_state(self) -> Dict[str, Any]:
        return {}

    def _record_bailout_signal(self, context: ModelContext, kind: _CompactKind) -> None:
        """Increment the shared loop-compaction counter for the matched rule."""
        if kind == "reasoning_tools":
            state_key = LOOP_COMPACT_BAILOUT_STATE_KEY
            threshold = self.config.bailout_threshold
        else:
            state_key = TOOL_ARGS_LOOP_COMPACT_BAILOUT_STATE_KEY
            threshold = self.config.tool_args_bailout_threshold

        if threshold <= 0:
            return
        get_session_ref = getattr(context, "get_session_ref", None)
        session = get_session_ref() if callable(get_session_ref) else None
        if session is None:
            return
        try:
            current = int(session.get_state(state_key) or 0)
            session.update_state({state_key: current + 1})
        except Exception as exc:  # best-effort signal; never break compaction
            logger.warning(
                "[ReasoningToolLoopCompact] failed to record bail-out signal: %s",
                exc,
            )

    def _compact_messages(
            self,
            messages: List[BaseMessage],
    ) -> Tuple[Optional[List[BaseMessage]], Optional[_CompactKind]]:
        compact_range = self._find_compact_range(messages)
        if compact_range is None:
            return None, None

        fold_start, _fold_end, rounds, kind = compact_range
        latest = rounds[-1]
        tool_calls_text, tool_results_text = self._collect_call_and_result_sets(
            messages[latest.start:latest.end]
        )
        summary = AssistantMessage(
            content=self._build_warning_content(
                kind=kind,
                reasoning=latest.reasoning,
                tool_calls=tool_calls_text,
                tool_results=tool_results_text,
            )
        )
        return messages[:fold_start] + [summary], kind

    def _find_compact_range(
            self,
            messages: Sequence[BaseMessage],
    ) -> Optional[Tuple[int, int, List[_ToolRound], _CompactKind]]:
        rounds = self._collect_tool_rounds(messages)

        # Prefer the legacy reasoning+tools rule when both could match.
        reasoning_hit = self._find_trailing_match(
            rounds,
            fingerprint_attr="reasoning_fingerprint",
            threshold=self.config.consecutive_threshold,
        )
        if reasoning_hit is not None:
            fold_start, fold_end, trailing = reasoning_hit
            return fold_start, fold_end, trailing, "reasoning_tools"

        args_hit = self._find_trailing_match(
            rounds,
            fingerprint_attr="args_fingerprint",
            threshold=self.config.tool_args_consecutive_threshold,
        )
        if args_hit is not None:
            fold_start, fold_end, trailing = args_hit
            return fold_start, fold_end, trailing, "tool_args"

        return None

    @staticmethod
    def _find_trailing_match(
            rounds: Sequence[_ToolRound],
            *,
            fingerprint_attr: str,
            threshold: int,
    ) -> Optional[Tuple[int, int, List[_ToolRound]]]:
        if threshold < 2 or len(rounds) < threshold:
            return None

        # Walk from the end; skip rounds whose fingerprint for this rule is None.
        eligible: List[_ToolRound] = []
        for round_info in reversed(rounds):
            fingerprint = getattr(round_info, fingerprint_attr)
            if fingerprint is None:
                if eligible:
                    break
                continue
            if not eligible:
                eligible.append(round_info)
                continue
            if getattr(eligible[-1], fingerprint_attr) != fingerprint:
                break
            eligible.append(round_info)

        eligible.reverse()
        if len(eligible) < threshold:
            return None

        return eligible[0].start, eligible[-1].end, eligible

    def _collect_tool_rounds(self, messages: Sequence[BaseMessage]) -> List[_ToolRound]:
        rounds: List[_ToolRound] = []
        index = 0
        total = len(messages)
        max_chars = self.config.arguments_max_chars
        while index < total:
            message = messages[index]
            if not isinstance(message, AssistantMessage):
                index += 1
                continue

            tool_calls = getattr(message, "tool_calls", None) or []
            if not tool_calls:
                index += 1
                continue

            pending_ids = {
                str(getattr(tool_call, "id", "") or "")
                for tool_call in tool_calls
                if getattr(tool_call, "id", None)
            }
            if not pending_ids:
                index += 1
                continue

            call_fps: List[Tuple[str, str]] = []
            tool_names_list: List[str] = []
            for tool_call in tool_calls:
                name = str(getattr(tool_call, "name", "") or "").strip()
                if not name:
                    continue
                tool_names_list.append(name)
                call_fps.append(
                    (
                        name,
                        _fingerprint_tool_arguments(
                            getattr(tool_call, "arguments", None),
                            max_chars=max_chars,
                        ),
                    )
                )
            if not call_fps:
                index += 1
                continue

            tool_names = tuple(sorted(tool_names_list))
            reasoning = self._normalize_reasoning(getattr(message, "reasoning_content", None))
            reasoning_fingerprint = (reasoning, tool_names) if reasoning is not None else None

            cursor = index + 1
            while cursor < total and pending_ids:
                next_message = messages[cursor]
                if not isinstance(next_message, ToolMessage):
                    break
                tool_call_id = str(getattr(next_message, "tool_call_id", "") or "")
                if tool_call_id in pending_ids:
                    pending_ids.discard(tool_call_id)
                    cursor += 1
                    continue
                break

            if pending_ids:
                index += 1
                continue

            rounds.append(
                _ToolRound(
                    start=index,
                    end=cursor,
                    reasoning_fingerprint=reasoning_fingerprint,
                    args_fingerprint=tuple(sorted(call_fps)),
                    reasoning=reasoning or "",
                    tool_names=tool_names,
                )
            )
            index = cursor

        return rounds

    def _collect_call_and_result_sets(
            self,
            folded_messages: Sequence[BaseMessage],
    ) -> Tuple[str, str]:
        """Serialize latest-round tool calls/results as structured plain-text JSON."""
        call_items: List[Dict[str, Any]] = []
        result_items: List[Dict[str, Any]] = []
        tool_name_by_id: Dict[str, str] = {}
        max_chars = self.config.arguments_max_chars

        for message in folded_messages:
            if isinstance(message, AssistantMessage):
                for tool_call in getattr(message, "tool_calls", None) or []:
                    name = str(getattr(tool_call, "name", "") or "").strip() or "(unknown)"
                    tool_call_id = str(getattr(tool_call, "id", "") or "")
                    if tool_call_id:
                        tool_name_by_id[tool_call_id] = name
                    call_items.append({
                        "name": name,
                        "arguments": _truncate_for_display(
                            _parse_tool_arguments(getattr(tool_call, "arguments", None)),
                            max_chars=max_chars,
                        ),
                    })
            elif isinstance(message, ToolMessage):
                content = message.content
                if not isinstance(content, str) or not content.strip():
                    continue
                tool_call_id = str(getattr(message, "tool_call_id", "") or "")
                result_items.append({
                    "name": tool_name_by_id.get(tool_call_id, "(unknown)"),
                    "content": content.strip(),
                })

        return (
            _dumps_json_list(call_items),
            _dumps_json_list(result_items),
        )

    def _build_warning_content(
            self,
            *,
            kind: _CompactKind,
            reasoning: str,
            tool_calls: str,
            tool_results: str,
    ) -> str:
        language = _normalize_language(self.config.language)
        if kind == "tool_args":
            template = _TOOL_ARGS_LOOP_WARNING_TEMPLATES.get(
                language, _TOOL_ARGS_LOOP_WARNING_TEMPLATE_CN
            )
            return template.format(
                tool_calls=tool_calls,
                tool_results=tool_results,
            ).strip()

        preview = reasoning
        max_chars = self.config.reasoning_preview_max_chars
        if len(preview) > max_chars:
            preview = preview[:max_chars] + "\n...(truncated)"
        template = _REASONING_LOOP_WARNING_TEMPLATES.get(
            language, _REASONING_LOOP_WARNING_TEMPLATE_CN
        )
        return template.format(
            tool_calls=tool_calls,
            tool_results=tool_results,
            reasoning_preview=preview,
        ).strip()

    def _normalize_reasoning(self, raw: Any) -> Optional[str]:
        if not isinstance(raw, str):
            return None
        normalized = raw.strip()
        if len(normalized) < self.config.reasoning_min_chars:
            return None
        return normalized


def _parse_tool_arguments(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def _canonical_arguments_text(raw: Any) -> str:
    parsed = _parse_tool_arguments(raw)
    try:
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(parsed)


def _fingerprint_tool_arguments(raw: Any, *, max_chars: int) -> str:
    text = _canonical_arguments_text(raw)
    if len(text) > max_chars:
        return text[:max_chars]
    return text


def _truncate_for_display(value: Any, *, max_chars: int) -> Any:
    """Keep warning payloads bounded; mirror fingerprint truncation for strings."""
    if isinstance(value, str) and len(value) > max_chars:
        return value[:max_chars] + "...(truncated)"
    if isinstance(value, dict):
        text = _canonical_arguments_text(value)
        if len(text) > max_chars:
            return {"_truncated": text[:max_chars] + "...(truncated)"}
        return value
    return value


def _dumps_json_list(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "[]"
    return json.dumps(items, ensure_ascii=False, indent=2)


def _normalize_language(language: str | None) -> str:
    normalized = (language or "cn").strip().lower()
    if normalized.startswith("zh") or normalized == "cn":
        return "cn"
    if normalized == "en":
        return "en"
    return "cn"


__all__ = [
    "LOOP_COMPACT_BAILOUT_STATE_KEY",
    "TOOL_ARGS_LOOP_COMPACT_BAILOUT_STATE_KEY",
    "ReasoningToolLoopCompactProcessor",
    "ReasoningToolLoopCompactProcessorConfig",
]
