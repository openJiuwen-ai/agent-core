from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.core.context_engine.context.session_memory_manager import (
    group_completed_api_rounds as group_completed_api_round_ranges,
)
from openjiuwen.core.context_engine.processor.compressor.reinjection.reinjector import ReinjectContext
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage, ToolMessage, UserMessage


@dataclass(frozen=True)
class ReadFileSnapshot:
    file_path: str
    content: str
    line_count: int | None
    partial: bool


def build_plan_reinjected_content(ctx: ReinjectContext) -> str:
    plan_mode = _get_plan_mode(ctx.session_state)
    plan_slug = plan_mode.get("plan_slug") if isinstance(plan_mode, dict) else None
    if not plan_slug or not ctx.workspace_root:
        return ""
    plan_path = Path(ctx.workspace_root) / ".plans" / f"{plan_slug}.md"
    if not plan_path.exists():
        return ""
    try:
        plan_text = plan_path.read_text(encoding="utf-8")
    except Exception:
        return ""
    return ctx.truncate(f"Current plan file: {plan_path}\n\n{plan_text}")


def build_skill_reinjected_content(ctx: ReinjectContext) -> list[UserMessage]:
    keep_signatures = {message_signature(message) for message in ctx.messages_to_keep}
    selected_rounds: list[list[BaseMessage]] = []
    seen_round_signatures: set[tuple[str, ...]] = set()
    max_skills = int(getattr(ctx.config, "reinject_recent_skills", 3) or 0)
    if max_skills <= 0:
        return []

    for round_messages in reversed(group_completed_api_rounds(ctx.source_messages)):
        round_signatures = tuple(message_signature(message) for message in round_messages)
        if round_signatures in seen_round_signatures:
            continue
        if any(signature in keep_signatures for signature in round_signatures):
            continue
        snapshot = extract_skill_tool_snapshot(round_messages) or extract_read_file_skill_snapshot(round_messages)
        if not snapshot:
            continue
        selected_rounds.append([UserMessage(content=f"{ctx.state_marker}\n[SKILLS]\n{ctx.truncate(snapshot)}")])
        seen_round_signatures.add(round_signatures)
        if len(selected_rounds) >= max_skills:
            break

    selected_rounds.reverse()
    return [message for messages in selected_rounds for message in messages]


def build_file_reinjected_content(ctx: ReinjectContext) -> str:
    preserved_paths = collect_read_file_paths(ctx.messages_to_keep)
    max_files = int(getattr(ctx.config, "reinject_read_file_max_files", 5) or 0)
    per_file_chars = int(getattr(ctx.config, "reinject_read_file_max_chars_per_file", 15000) or 15000)
    total_chars = int(getattr(ctx.config, "reinject_read_file_total_chars", 50000) or 50000)
    if max_files <= 0 or total_chars <= 0:
        return ""

    snapshots: list[ReadFileSnapshot] = []
    for round_messages in reversed(group_completed_api_rounds(ctx.source_messages)):
        for tool_call in iter_tool_calls(round_messages, name="read_file"):
            result_text = find_tool_result_text(round_messages, getattr(tool_call, "id", None))
            snapshot = parse_read_file_result(tool_call, result_text)
            if snapshot is None:
                continue
            if snapshot.file_path in preserved_paths:
                continue
            if is_excluded_from_reinject(snapshot.file_path):
                continue
            snapshots.append(snapshot)

    selected: list[ReadFileSnapshot] = []
    seen_paths: set[str] = set()
    remaining = total_chars
    for snapshot in snapshots:
        if snapshot.file_path in seen_paths:
            continue
        seen_paths.add(snapshot.file_path)
        selected.append(snapshot)
        remaining -= min(len(snapshot.content), per_file_chars)
        if len(selected) >= max_files or remaining <= 0:
            break
    selected.reverse()
    return render_read_file_snapshots(selected, ctx.truncate, per_file_chars)


def build_task_status_reinjected_content(ctx: ReinjectContext) -> str:
    lines: list[str] = []
    background_tasks = ctx.session_state.get("background_tasks") or []
    if isinstance(background_tasks, list):
        for task in background_tasks:
            if not isinstance(task, dict):
                continue
            description = task.get("description") or "background task"
            task_id = task.get("task_id") or "unknown"
            status = task.get("status") or "unknown"
            if status == "running":
                lines.append(f'Background agent "{description}" ({task_id}) is still running. Do NOT spawn a duplicate.')
            elif status in {"completed", "error", "canceled"}:
                lines.append(
                    f'Background agent "{description}" ({task_id}) status={status}. '
                    "Check the stored result/error before spawning another task."
                )

    team_status = ctx.session_state.get("team_task_status")
    if isinstance(team_status, dict):
        lines.append(f'Team "{team_status.get("team_name", "unknown")}" current collaboration state:')
        members = team_status.get("members") or []
        if members:
            lines.append("- Active members:")
            for member in members:
                if isinstance(member, dict):
                    lines.append(
                        f'  - {member.get("member_name", "unknown")}: '
                        f'role={member.get("role", "")}, status={member.get("status", "unknown")}'
                    )
        open_tasks = team_status.get("open_tasks") or []
        if open_tasks:
            lines.append("- Open tasks:")
            for task in open_tasks:
                if isinstance(task, dict):
                    assignee = task.get("assignee") or "unassigned"
                    lines.append(
                        f'  - #{task.get("task_id", "unknown")} [{task.get("status", "unknown")}] '
                        f'{task.get("title", "")} ({assignee})'
                    )
        if team_status.get("has_unread_messages"):
            lines.append("- Team has unread messages; use team messaging tools to inspect/continue.")
    return ctx.truncate("\n".join(lines))


def build_tool_result_hint_reinjected_content(ctx: ReinjectContext) -> str:
    _ = ctx
    return ""


def build_todo_reinjected_content(ctx: ReinjectContext) -> str:
    _ = ctx
    return ""


def build_plan_mode_reinjected_content(ctx: ReinjectContext) -> str:
    plan_mode = _get_plan_mode(ctx.session_state)
    if not isinstance(plan_mode, dict):
        return ""

    mode = plan_mode.get("mode") or "normal"
    pre_plan_mode = plan_mode.get("pre_plan_mode")
    plan_slug = plan_mode.get("plan_slug")

    lines = [
        "Current plan-mode status for this session:",
        f"- Active mode: {mode}.",
    ]
    if pre_plan_mode:
        lines.append(f"- Previous mode before entering plan mode: {pre_plan_mode}.")
    if plan_slug:
        lines.append(f"- Active plan identifier: {plan_slug}.")
    if mode == "plan":
        lines.extend(
            [
                "",
                "Plan-mode constraints:",
                "- Only planning is allowed; do not implement the plan yet.",
                "- Do not modify files except the active plan file.",
                "- Use read-only exploration tools unless editing the plan file.",
                "- End planning through exit_plan_mode when the plan is ready for user approval.",
                "- Use ask_user only for clarification or choosing between approaches, not for plan approval.",
            ]
        )
    return ctx.truncate("\n".join(lines))


def _get_plan_mode(session_state: dict[str, Any]) -> dict[str, Any] | None:
    plan_mode = session_state.get("plan_mode")
    if isinstance(plan_mode, dict):
        return plan_mode
    deepagent = session_state.get("deepagent")
    if isinstance(deepagent, dict) and isinstance(deepagent.get("plan_mode"), dict):
        return deepagent["plan_mode"]
    return None


def is_skill_file_path(file_path: str) -> bool:
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/").lower()
    return normalized.endswith("/skill.md") or normalized.endswith("skill.md")


def extract_skill_tool_snapshot(messages: list[BaseMessage]) -> str:
    for tool_call in iter_tool_calls(messages, name="skill_tool"):
        args = parse_tool_arguments(getattr(tool_call, "arguments", "") or "")
        relative_path = str(args.get("relative_file_path") or "SKILL.md")
        if relative_path not in {"", "SKILL.md"}:
            continue
        result = parse_jsonish_tool_result(find_tool_result_text(messages, getattr(tool_call, "id", None)))
        content = result.get("skill_content") if isinstance(result, dict) else None
        directory = result.get("skill_directory") if isinstance(result, dict) else None
        skill_name = args.get("skill_name") or Path(str(directory or "")).name
        if isinstance(content, str) and content.strip():
            return f"Skill: {skill_name}\nPath: {directory}/SKILL.md\n\n{content.strip()}"
    return ""


def extract_read_file_skill_snapshot(messages: list[BaseMessage]) -> str:
    for tool_call in iter_tool_calls(messages, name="read_file"):
        args_text = getattr(tool_call, "arguments", "") or ""
        args = parse_tool_arguments(args_text)
        file_path = extract_argument_value(args, args_text, ("file_path", "path"))
        if not is_skill_file_path(file_path):
            continue
        result_text = find_tool_result_text(messages, getattr(tool_call, "id", None))
        result = parse_jsonish_tool_result(result_text)
        content = result.get("content") if isinstance(result, dict) else result_text
        if isinstance(content, str) and content.strip():
            return f"Skill: {Path(file_path).parent.name}\nPath: {file_path}\n\n{content.strip()}"
    return ""


def group_completed_api_rounds(messages: list[BaseMessage]) -> list[list[BaseMessage]]:
    return [list(messages[start:end]) for start, end in group_completed_api_round_ranges(messages)]


def message_signature(message: BaseMessage) -> str:
    tool_call_ids = []
    if isinstance(message, AssistantMessage):
        tool_call_ids = [
            getattr(tool_call, "id", "") or "" for tool_call in (getattr(message, "tool_calls", None) or [])
        ]
    return f"{message.role}|{message_to_text(message)}|{'|'.join(tool_call_ids)}"


def iter_tool_calls(messages: list[BaseMessage], name: str | None = None):
    for message in messages:
        if not isinstance(message, AssistantMessage):
            continue
        for tool_call in getattr(message, "tool_calls", None) or []:
            tool_name = getattr(tool_call, "name", "") or ""
            if name is not None and tool_name != name:
                continue
            yield tool_call


def find_tool_result_text(messages: list[BaseMessage], tool_call_id: str | None) -> str:
    if not tool_call_id:
        return ""
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and getattr(message, "tool_call_id", None) == tool_call_id:
            return message_to_text(message)
    return ""


def collect_read_file_paths(messages: list[BaseMessage]) -> set[str]:
    paths: set[str] = set()
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        result = parse_jsonish_tool_result(message_to_text(message))
        if isinstance(result, dict) and isinstance(result.get("file_path"), str):
            paths.add(result["file_path"])
    return paths


def parse_read_file_result(tool_call: Any, result_text: str) -> ReadFileSnapshot | None:
    args_text = getattr(tool_call, "arguments", "") or ""
    args = parse_tool_arguments(args_text)
    result = parse_jsonish_tool_result(result_text)
    if not isinstance(result, dict):
        return None
    file_path = result.get("file_path") or extract_argument_value(args, args_text, ("file_path", "path"))
    content = result.get("content")
    if not isinstance(file_path, str) or not isinstance(content, str):
        return None
    line_count = result.get("line_count")
    offset = args.get("offset")
    limit = args.get("limit")
    partial = offset is not None or limit is not None
    return ReadFileSnapshot(
        file_path=file_path,
        content=content,
        line_count=line_count if isinstance(line_count, int) else None,
        partial=partial,
    )


def is_excluded_from_reinject(file_path: str) -> bool:
    normalized = file_path.replace("\\", "/").lower()
    return any(part in normalized for part in ("/.git/", "/node_modules/", "/__pycache__/"))


def render_read_file_snapshots(
    snapshots: list[ReadFileSnapshot],
    truncate: Any,
    per_file_chars: int,
) -> str:
    blocks: list[str] = []
    for snapshot in snapshots:
        content = snapshot.content
        if len(content) > per_file_chars:
            head = content[: max(per_file_chars // 2, 0)]
            tail = content[-max(per_file_chars - len(head), 0):]
            content = f"{head}\n...[READ_FILE_TRUNCATED]...\n{tail}"
        lines = [
            f"Recently read file: {snapshot.file_path}",
            f"Lines returned: {snapshot.line_count if snapshot.line_count is not None else 'unknown'}",
            f"Partial read: {str(snapshot.partial).lower()}",
            "",
            content,
        ]
        blocks.append(truncate("\n".join(lines)))
    return "\n\n".join(blocks)


def parse_tool_arguments(arguments_text: str) -> dict[str, Any]:
    if not arguments_text:
        return {}
    try:
        parsed = json.loads(arguments_text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def parse_jsonish_tool_result(result_text: str) -> Any:
    if not result_text:
        return {}
    try:
        return json.loads(result_text)
    except Exception:
        return {}


def extract_argument_value(parsed_arguments: dict[str, Any], arguments_text: str, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = parsed_arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in keys:
        match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]+)"', arguments_text)
        if match:
            return match.group(1).strip()
    return ""


def message_to_text(message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except TypeError:
        return str(content)
