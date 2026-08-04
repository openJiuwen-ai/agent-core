# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Short case-scoped trajectory paths for auto coordinating harness evaluation."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from openjiuwen.agent_evolving.trajectory.store import FileTrajectoryStore
from openjiuwen.agent_evolving.trajectory.types import Trajectory, to_legacy_trajectory

ROLE_TRAJECTORY_DIR_NAME = "tr"
TRAJECTORY_EVENTS_FILE_NAME = "trajectory_events.jsonl"
MAX_SAVED_LLM_MESSAGES = 4
MAX_SAVED_TEXT_CHARS = 1200
MAX_SAVED_TOOL_RESULT_CHARS = 1200


def _filesystem_path(path: Path) -> str:
    """Return an OS path string that can address long Windows paths."""
    raw = str(path)
    if os.name != "nt" or raw.startswith("\\\\?\\"):
        return raw
    absolute = str(path.resolve(strict=False))
    if absolute.startswith("\\\\"):
        return os.path.join("\\\\?\\UNC", absolute.lstrip("\\"))
    return os.path.join("\\\\?\\", absolute)


class RoleFileTrajectoryStore(FileTrajectoryStore):
    """Store one member's bounded latest trajectory as ``<case>/tr/<role>.jsonl``.

    ACH evaluators only need role traces for diagnosis and attribution.  The
    generic FileTrajectoryStore appends full snapshots, including repeated
    prompt histories and tool schemas; in ReAct evaluation runs that can grow to
    multi-GB files.  Keep the same JSONL shape for readers, but overwrite with a
    compact latest snapshot containing bounded messages, compact tool metadata,
    and truncated tool payloads.
    """

    def __init__(self, base_dir: Path, role_name: str) -> None:
        self.role_name = _safe_role_file_stem(role_name)
        super().__init__(base_dir)

    def _get_file_path(self, version: str | None) -> Path:
        suffix = f".{_safe_role_file_stem(version)}" if version else ""
        return self._base_dir / f"{self.role_name}{suffix}.jsonl"

    def save(self, trajectory: Trajectory, version: str | None = None) -> None:
        file_path = self._get_file_path(version)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        legacy = to_legacy_trajectory(trajectory)
        data = _bounded_trajectory_dict(FileTrajectoryStore._to_json_compatible(legacy))
        with open(_filesystem_path(file_path), "w", encoding="utf-8") as file:
            file.write(json.dumps(data, ensure_ascii=False) + "\n")


def _safe_role_file_stem(value: str | None) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown")).strip("._-")
    return stem or "unknown"


def _bounded_trajectory_dict(data: dict[str, Any]) -> dict[str, Any]:
    bounded = _truncate_json_like(data, max_text_chars=MAX_SAVED_TEXT_CHARS)
    steps = bounded.get("steps")
    if not isinstance(steps, list):
        return bounded
    for step in steps:
        if not isinstance(step, dict):
            continue
        detail = step.get("detail")
        if not isinstance(detail, dict):
            continue
        if "messages" in detail:
            _bound_llm_detail(detail)
        elif "tool_name" in detail:
            _bound_tool_detail(detail)
    return bounded


def _bound_llm_detail(detail: dict[str, Any]) -> None:
    messages = detail.get("messages")
    if isinstance(messages, list):
        detail["messages"] = _bounded_messages(messages)
        omitted = max(0, len(messages) - len(detail["messages"]))
        if omitted:
            meta = detail.setdefault("meta", {})
            if isinstance(meta, dict):
                meta["omitted_message_count"] = omitted
    tools = detail.get("tools")
    if isinstance(tools, list):
        detail["tools"] = [_tool_summary(tool) for tool in tools]
    if "response" in detail:
        detail["response"] = _truncate_json_like(
            detail.get("response"),
            max_text_chars=MAX_SAVED_TEXT_CHARS,
        )


def _bound_tool_detail(detail: dict[str, Any]) -> None:
    if "call_args" in detail:
        detail["call_args"] = _truncate_json_like(
            detail.get("call_args"),
            max_text_chars=MAX_SAVED_TOOL_RESULT_CHARS,
        )
    if "call_result" in detail:
        detail["call_result"] = _truncate_json_like(
            detail.get("call_result"),
            max_text_chars=MAX_SAVED_TOOL_RESULT_CHARS,
        )
    if "tool_description" in detail:
        detail["tool_description"] = _truncate_text(detail.get("tool_description"), MAX_SAVED_TEXT_CHARS)
    if "tool_schema" in detail:
        detail["tool_schema"] = {"omitted": True}


def _bounded_messages(messages: list[Any]) -> list[Any]:
    if len(messages) <= MAX_SAVED_LLM_MESSAGES:
        kept = messages
    else:
        head_count = MAX_SAVED_LLM_MESSAGES // 2
        tail_count = MAX_SAVED_LLM_MESSAGES - head_count
        kept = [*messages[:head_count], *messages[-tail_count:]]
    return [_truncate_json_like(message, max_text_chars=MAX_SAVED_TEXT_CHARS) for message in kept]


def _tool_summary(tool: Any) -> dict[str, Any]:
    if not isinstance(tool, dict):
        return {"name": str(tool)[:MAX_SAVED_TEXT_CHARS]}
    function = tool.get("function")
    name = None
    if isinstance(function, dict):
        name = function.get("name")
    name = name or tool.get("name") or tool.get("tool_name") or "unknown"
    summary: dict[str, Any] = {"name": str(name)}
    tool_type = tool.get("type")
    if tool_type:
        summary["type"] = str(tool_type)
    return summary


def _truncate_json_like(value: Any, *, max_text_chars: int) -> Any:
    if isinstance(value, str):
        return _truncate_text(value, max_text_chars)
    if isinstance(value, list):
        return [_truncate_json_like(item, max_text_chars=max_text_chars) for item in value]
    if isinstance(value, dict):
        return {str(key): _truncate_json_like(item, max_text_chars=max_text_chars) for key, item in value.items()}
    return value


def _truncate_text(value: Any, max_chars: int) -> Any:
    if not isinstance(value, str) or len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}...[truncated {len(value) - max_chars} chars]"
