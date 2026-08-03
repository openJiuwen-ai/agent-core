# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Extract completed Tool and Skill usage from structured trajectories."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def collect_successful_tool_names(value: Any, names: set[str]) -> None:
    """Add Tool names only for calls that reached a successful completion."""
    for _, detail in _successful_tool_steps(value):
        tool_name = str(detail.get("tool_name", "") or "").strip()
        if tool_name:
            names.add(tool_name)


def collect_successful_skill_names(value: Any, names: set[str]) -> None:
    """Add Skill names loaded by successfully completed ``skill_tool`` calls."""
    for _, detail in _successful_tool_steps(value):
        tool_name = str(detail.get("tool_name", "") or "").strip()
        if _canonical_tool_name(tool_name) != "skill":
            continue
        for key in ("call_args", "arguments", "tool_input", "input"):
            _add_skill_name_from_args(detail.get(key), names)


def collect_jsonl_successful_usage(
    path: Path,
    *,
    tool_names: set[str] | None = None,
    skill_names: set[str] | None = None,
) -> None:
    """Collect completed usage from one native Team member trajectory file."""
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if tool_names is not None:
                    collect_successful_tool_names(payload, tool_names)
                if skill_names is not None:
                    collect_successful_skill_names(payload, skill_names)
    except OSError:
        return


def collect_pre_edit_successful_usage(
    value: Any,
    *,
    tool_names: set[str] | None = None,
    skill_names: set[str] | None = None,
) -> int | None:
    """Collect successful usage that happened before the first workspace edit.

    The returned index is the first successful persistent-edit step. A capability
    used after that point may validate an existing patch, but cannot be credited
    with influencing the patch decision.
    """
    first_edit_step: int | None = None
    for step_index, step in enumerate(_ordered_steps(value)):
        if not _tool_step_succeeded(step):
            continue
        if first_edit_step is None:
            detail = step["detail"]
            if tool_names is not None:
                tool_name = str(detail.get("tool_name", "") or "").strip()
                if tool_name:
                    tool_names.add(tool_name)
            if skill_names is not None:
                _collect_skill_names_from_detail(detail, skill_names)
        if first_edit_step is None and _is_persistent_edit_step(step):
            first_edit_step = step_index
    return first_edit_step


def collect_jsonl_pre_edit_successful_usage(
    path: Path,
    *,
    tool_names: set[str] | None = None,
    skill_names: set[str] | None = None,
) -> int | None:
    """Collect pre-edit usage from a native trajectory JSONL stream."""
    payloads: list[Any] = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    payloads.append(json.loads(line))
                except (TypeError, ValueError):
                    continue
    except OSError:
        return None
    return collect_pre_edit_successful_usage(
        {"steps": [step for payload in payloads for step in _ordered_steps(payload)]},
        tool_names=tool_names,
        skill_names=skill_names,
    )


def collect_pre_investigation_successful_skill_usage(
    value: Any,
    *,
    skill_names: set[str],
) -> int | None:
    """Collect Skills loaded before the first successful evidence-gathering call.

    A Skill loaded after the solver has started reading files, running probes, or
    otherwise gathering evidence cannot be credited with shaping the initial
    hypothesis.  This boundary is deliberately stricter than the pre-edit gate:
    the first successful non-Skill Tool completion starts investigation.
    """
    first_investigation_step: int | None = None
    for step_index, step in enumerate(_ordered_steps(value)):
        if not _tool_step_succeeded(step):
            continue
        detail = step["detail"]
        tool_name = _canonical_tool_name(str(detail.get("tool_name", "") or ""))
        if tool_name == "skill" and first_investigation_step is None:
            _collect_skill_names_from_detail(detail, skill_names)
            continue
        if tool_name != "skill" and first_investigation_step is None:
            first_investigation_step = step_index
    return first_investigation_step


def collect_jsonl_pre_investigation_successful_skill_usage(
    path: Path,
    *,
    skill_names: set[str],
) -> int | None:
    """Collect pre-investigation Skill usage from a trajectory JSONL stream."""
    payloads: list[Any] = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    payloads.append(json.loads(line))
                except (TypeError, ValueError):
                    continue
    except OSError:
        return None
    return collect_pre_investigation_successful_skill_usage(
        {"steps": [step for payload in payloads for step in _ordered_steps(payload)]},
        skill_names=skill_names,
    )


def _successful_tool_steps(value: Any):
    if isinstance(value, dict):
        if _tool_step_succeeded(value):
            detail = value.get("detail")
            if isinstance(detail, dict):
                yield value, detail
        for nested in value.values():
            yield from _successful_tool_steps(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _successful_tool_steps(nested)


def _ordered_steps(value: Any):
    """Yield top-level trajectory steps once, preserving execution order."""
    if isinstance(value, dict):
        steps = value.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, dict):
                    yield step
            return
        if "kind" in value and "detail" in value:
            yield value
            return
        for nested in value.values():
            yield from _ordered_steps(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _ordered_steps(nested)


def _tool_step_succeeded(step: dict[str, Any]) -> bool:
    """Return whether a structured Tool step represents a real successful run."""
    if str(step.get("kind", "") or "").strip().lower() != "tool":
        return False
    detail = step.get("detail")
    if not isinstance(detail, dict):
        return False
    if not str(detail.get("tool_name", "") or "").strip():
        return False
    if "call_result" not in detail or step.get("error"):
        return False

    call_result = detail.get("call_result")
    if call_result is None:
        return False
    if isinstance(call_result, dict):
        if call_result.get("success") is False or call_result.get("error"):
            return False
        status = str(call_result.get("status", "") or "").strip().lower()
        if status in {"error", "failed", "failure", "skipped"}:
            return False
    elif isinstance(call_result, str):
        result = call_result.strip().lower()
        if result.startswith(("error", "exception", "failed", "[reliability]")):
            return False
    return True


def _is_persistent_edit_step(step: dict[str, Any]) -> bool:
    detail = step.get("detail")
    if not isinstance(detail, dict):
        return False
    tool_name = _canonical_tool_name(str(detail.get("tool_name", "") or ""))
    if tool_name in {
        "apply_patch",
        "code_edit",
        "create_file",
        "edit_file",
        "file_edit",
        "patch_file",
        "replace_in_file",
        "str_replace_editor",
        "write_file",
    }:
        return True
    if tool_name not in {"bash", "shell", "shell_command"}:
        return False
    command = _command_from_args(detail)
    if not command:
        return False
    mutation_patterns = (
        r"(?:^|[;&|]\s*)apply_patch(?:\s|$)",
        r"(?:^|[;&|]\s*)git\s+apply(?:\s|$)",
        r"(?:^|[;&|]\s*)patch(?:\s|$)",
        r"(?:^|[;&|]\s*)(?:sed|perl)\s+[^\n;&|]*-(?:i|pi)\b",
        r"(?:^|[;&|]\s*)(?:cp|mv|rm|touch|truncate)\s+",
        r"(?:^|[;&|]\s*)tee\s+",
        r"(?:^|\s)(?:>|>>)\s*(?![&]|/dev/(?:null|stdout|stderr))\S+",
        r"\bopen\s*\([^\n)]*,\s*['\"](?:w|a|x|[rwa]b|[rwa]\+)['\"]",
        r"\.(?:write_text|write_bytes|touch|unlink|rename|replace)\s*\(",
        r"\bshutil\.(?:copy|copy2|copyfile|move|rmtree)\s*\(",
    )
    return any(re.search(pattern, command, flags=re.IGNORECASE) for pattern in mutation_patterns)


def _command_from_args(detail: dict[str, Any]) -> str:
    for key in ("call_args", "arguments", "tool_input", "input"):
        payload = detail.get(key)
        if isinstance(payload, str):
            try:
                decoded = json.loads(payload)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                payload = decoded
            elif key in {"tool_input", "input"}:
                return payload
        if isinstance(payload, dict):
            command = payload.get("command") or payload.get("cmd")
            if isinstance(command, str):
                return command
    return ""


def _collect_skill_names_from_detail(
    detail: dict[str, Any],
    names: set[str],
) -> None:
    tool_name = str(detail.get("tool_name", "") or "").strip()
    if _canonical_tool_name(tool_name) != "skill":
        return
    for key in ("call_args", "arguments", "tool_input", "input"):
        _add_skill_name_from_args(detail.get(key), names)


def _add_skill_name_from_args(raw_args: Any, names: set[str]) -> None:
    payload = raw_args
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return
    if not isinstance(payload, dict):
        return
    skill_name = str(payload.get("skill_name", "") or "").strip()
    if skill_name:
        names.add(skill_name)


def _canonical_tool_name(value: str) -> str:
    return value.strip().lower().replace("-", "_").removesuffix("_tool")
