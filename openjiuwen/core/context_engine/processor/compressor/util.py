# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from openjiuwen.core.context_engine.context.session_memory_manager import (
    group_completed_api_rounds as group_completed_api_round_ranges,
)
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage, ToolMessage, UserMessage


@dataclass(frozen=True)
class ReinjectedStateBuilderSpec:
    name: str
    label: str
    builder: Callable[..., Any]


class FullCompactStateReinjector:
    def __init__(self) -> None:
        self._builders: List[ReinjectedStateBuilderSpec] = []

    def register_builder(
        self,
        *,
        name: str,
        label: str,
        builder: Callable[..., Any],
    ) -> None:
        spec = ReinjectedStateBuilderSpec(name=name, label=label, builder=builder)
        for index, existing in enumerate(self._builders):
            if existing.name == name:
                self._builders[index] = spec
                return
        self._builders.append(spec)

    def iter_builders(self) -> Tuple[ReinjectedStateBuilderSpec, ...]:
        return tuple(self._builders)


def build_plan_reinjected_content(
    processor: Any,
    *,
    context: Any,
    messages: List[BaseMessage],
    messages_to_keep: List[BaseMessage],
) -> str:
    _ = processor, context, messages, messages_to_keep
    return ""


def build_skill_reinjected_content(
    processor: Any,
    *,
    context: Any,
    messages: List[BaseMessage],
    messages_to_keep: List[BaseMessage],
) -> List[UserMessage]:
    _ = context
    keep_signatures = {message_signature(message) for message in messages_to_keep}
    selected_rounds: List[List[BaseMessage]] = []
    seen_round_signatures: set[tuple[str, ...]] = set()

    for round_messages in reversed(group_completed_api_rounds(messages)):
        round_signatures = tuple(message_signature(message) for message in round_messages)
        if round_signatures in seen_round_signatures:
            continue
        if any(signature in keep_signatures for signature in round_signatures):
            continue
        if not round_contains_skill_read(round_messages):
            continue
        selected_rounds.append([message.model_copy(deep=True) for message in round_messages])
        seen_round_signatures.add(round_signatures)
        if len(selected_rounds) >= processor.config.reinject_recent_skills:
            break

    selected_rounds.reverse()
    reinjected_messages: List[UserMessage] = []
    for round_messages in selected_rounds:
        serialized_round = "\n".join(
            f"role={message.role}, content={message_to_text(message)}" for message in round_messages
        )
        reinjected_messages.append(
            UserMessage(
                content=(
                    f"{processor.config.state_marker}\n[SKILLS]\n{processor.truncate_state_text(serialized_round)}"
                )
            )
        )
    return reinjected_messages


def build_file_reinjected_content(
    processor: Any,
    *,
    context: Any,
    messages: List[BaseMessage],
    messages_to_keep: List[BaseMessage],
) -> str:
    _ = processor, context, messages, messages_to_keep
    return ""


def build_task_status_reinjected_content(
    processor: Any,
    *,
    context: Any,
    messages: List[BaseMessage],
    messages_to_keep: List[BaseMessage],
) -> str:
    _ = messages, messages_to_keep
    session_state = context.get_session_ref().get_state()
    try:
        iteration = int(session_state.get("task_state", {}).get("iteration") or 0)
    except Exception:
        iteration = 0
    try:
        pending_follow_ups = list(session_state.get("task_state", {}).get("pending_follow_ups") or [])
    except Exception:
        pending_follow_ups = []

    stop_condition_state = (
        session_state.get("task_state", {}).get("stop_condition_state", {})
        if isinstance(session_state.get("task_state", {}), dict)
        else None
    )
    stop_reason = stop_condition_state.get("stop_reason") if isinstance(stop_condition_state, dict) else None

    if not iteration and not pending_follow_ups and not stop_reason:
        return ""

    lines = [
        "Current task-loop status for this session:",
        f"- Completed outer-loop rounds: {iteration}.",
        f"- Pending follow-up queries: {len(pending_follow_ups)}.",
    ]
    if stop_reason:
        lines.append(f"- Last recorded stop reason: {stop_reason}.")
    return processor.truncate_state_text("\n".join(lines))


def build_plan_mode_reinjected_content(
    processor: Any,
    *,
    context: Any,
    messages: List[BaseMessage],
    messages_to_keep: List[BaseMessage],
) -> str:
    _ = messages, messages_to_keep
    session_state = context.get_session_ref().get_state()

    try:
        plan_mode = session_state.get("plan_mode", {})
    except Exception:
        plan_mode = None
    if not isinstance(plan_mode, dict):
        return ""

    mode = plan_mode.get("mode", "auto")
    pre_plan_mode = plan_mode.get("pre_plan_mode", "")
    plan_slug = plan_mode.get("plan_slug", "")

    lines = [
        "Current plan-mode status for this session:",
        f"- Active mode: {mode}.",
    ]
    if pre_plan_mode:
        lines.append(f"- Previous mode before entering plan mode: {pre_plan_mode}.")
    if plan_slug:
        lines.append(f"- Active plan identifier: {plan_slug}.")
    return processor.truncate_state_text("\n".join(lines))


def is_skill_file_path(file_path: str) -> bool:
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/").lower()
    return normalized.endswith("/skill.md") or normalized.endswith("skill.md")


def extract_skill_name_from_path(file_path: str) -> str:
    if not file_path:
        return ""
    normalized = file_path.replace("\\", "/").rstrip("/")
    parts = normalized.split("/")
    if len(parts) >= 2 and parts[-1].lower() == "skill.md":
        return parts[-2]
    return ""


def round_contains_skill_read(messages: List[BaseMessage]) -> bool:
    for message in messages:
        if not isinstance(message, AssistantMessage):
            continue
        for tool_call in getattr(message, "tool_calls", None) or []:
            tool_name = getattr(tool_call, "name", "") or ""
            if tool_name != "read_file":
                continue
            arguments_text = getattr(tool_call, "arguments", "") or ""
            parsed_arguments = parse_tool_arguments(arguments_text)
            file_path = extract_argument_value(parsed_arguments, arguments_text, ("file_path",))
            if is_skill_file_path(file_path):
                return True
    return False


def group_completed_api_rounds(messages: List[BaseMessage]) -> List[List[BaseMessage]]:
    return [list(messages[start:end]) for start, end in group_completed_api_round_ranges(messages)]


def message_signature(message: BaseMessage) -> str:
    tool_call_ids = []
    if isinstance(message, AssistantMessage):
        tool_call_ids = [
            getattr(tool_call, "id", "") or "" for tool_call in (getattr(message, "tool_calls", None) or [])
        ]
    raw = f"{message.role}|{message_to_text(message)}|{'|'.join(tool_call_ids)}"
    return raw


def extract_skill_file_content(processor: Any, result_text: str) -> str:
    if not result_text:
        return ""

    content_match = re.search(
        r'"content"\s*:\s*"(?P<content>(?:[^"\\]|\\.)*)"',
        result_text,
        re.DOTALL,
    )
    content = ""
    if content_match:
        raw_content = content_match.group("content")
        try:
            content = json.loads(f'"{raw_content}"')
        except Exception:
            content = raw_content.replace('\\"', '"').replace("\\n", "\n")
    else:
        content = result_text

    content = content.strip()
    if not content:
        return ""
    return processor.truncate_state_text(content)


def describe_tool_call(tool_name: str, arguments_text: str) -> str:
    parsed_arguments = parse_tool_arguments(arguments_text)
    if tool_name == "read_file":
        file_path = extract_argument_value(parsed_arguments, arguments_text, ("file_path",))
        return f"read_file path={file_path or '[unknown]'}"
    if tool_name == "write_file":
        file_path = extract_argument_value(parsed_arguments, arguments_text, ("file_path",))
        return f"write_file path={file_path or '[unknown]'}"
    if tool_name == "edit_file":
        file_path = extract_argument_value(parsed_arguments, arguments_text, ("file_path",))
        return f"edit_file path={file_path or '[unknown]'}"
    if tool_name == "glob":
        pattern = extract_argument_value(parsed_arguments, arguments_text, ("pattern",))
        path = extract_argument_value(parsed_arguments, arguments_text, ("path",))
        return f"glob pattern={pattern or '[unknown]'} path={path or '.'}"
    if tool_name == "grep":
        pattern = extract_argument_value(parsed_arguments, arguments_text, ("pattern",))
        path = extract_argument_value(parsed_arguments, arguments_text, ("path", "file_path"))
        return f"grep pattern={pattern or '[unknown]'} path={path or '[unknown]'}"
    return f"{tool_name} args={arguments_text}"


def parse_tool_arguments(arguments_text: str) -> Dict[str, Any]:
    if not arguments_text:
        return {}
    try:
        parsed = json.loads(arguments_text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def extract_argument_value(
    parsed_arguments: Dict[str, Any],
    arguments_text: str,
    keys: Tuple[str, ...],
) -> str:
    for key in keys:
        value = parsed_arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in keys:
        match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]+)"', arguments_text)
        if match:
            return match.group(1).strip()
    return ""


def find_tool_result_text(
    messages: List[BaseMessage],
    tool_call_id: Optional[str],
) -> str:
    if not tool_call_id:
        return ""
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and getattr(message, "tool_call_id", None) == tool_call_id:
            return message_to_text(message)
    return ""


def extract_tool_result_hint(
    tool_name: str,
    result_text: str,
    allowed_tool_names: List[str],
) -> str:
    if not result_text:
        return ""
    if tool_name not in set(allowed_tool_names):
        return ""
    if tool_name == "read_file":
        file_path_match = re.search(r'"file_path"\s*:\s*"([^"]+)"', result_text)
        line_count_match = re.search(r'"line_count"\s*:\s*(\d+)', result_text)
        parts = []
        if file_path_match:
            parts.append(f"result_path={file_path_match.group(1)}")
        if line_count_match:
            parts.append(f"lines={line_count_match.group(1)}")
        return " ".join(parts)
    if tool_name == "glob":
        count_match = re.search(r'"count"\s*:\s*(\d+)', result_text)
        if count_match:
            return f"matches={count_match.group(1)}"
    if tool_name == "grep":
        count_match = re.search(r'"count"\s*:\s*(\d+)', result_text)
        if count_match:
            return f"hits={count_match.group(1)}"
    if tool_name == "edit_file":
        replacements_match = re.search(r'"replacements"\s*:\s*(\d+)', result_text)
        if replacements_match:
            return f"replacements={replacements_match.group(1)}"
    if tool_name == "write_file":
        bytes_match = re.search(r'"bytes_written"\s*:\s*(\d+)', result_text)
        if bytes_match:
            return f"bytes_written={bytes_match.group(1)}"
    return ""


def message_to_text(message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except TypeError:
        return str(content)
