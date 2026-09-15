# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-call ``extra.paths``: tool schema, extraction, and invoke-scoped payload.

The model declares host paths on fs/shell/code tools. A non-empty list is
checked before invoke: paths not already explicitly allowed (workspace,
trusted dirs, or persisted ``file_guard.paths``) trigger the existing
permission popup. Defaults-allow does **not** skip that popup. After
approval the same payload is forwarded to box-server.
"""

from __future__ import annotations

import functools
import inspect
from contextvars import ContextVar, Token
from typing import Any, Literal, Mapping

from openjiuwen.harness.security.permission_engine.models import (
    PermissionLevel,
    PermissionResult,
)

_TOOL_ACCESS_EXTRA: ContextVar[dict[str, list[str]] | None] = ContextVar(
    "tool_access_extra", default=None,
)

FileAction = Literal["read", "write", "exec"]

_EXTRA_WRITE_TOOLS = frozenset({
    "write_file",
    "edit_file",
    "write_text_file",
    "write",
    "search_replace",
    "bash",
    "powershell",
    "core.powershell",
    "mcp_exec_command",
    "create_terminal",
    "code",
})

_EXTRA_PATHS_DESC = {
    "cn": (
        "先分析本次指令需要访问的宿主路径（文件或目录），全部填入本列表。"
        "执行前若 extra.paths 非空，尚未被用户授权的路径会弹窗审批，通过后才执行。"
        "工作区根和用户已记住的路径不必再批。"
    ),
    "en": (
        "Analyze which host paths (files or directories) this call needs, "
        "and list them all here. A non-empty extra.paths that includes paths "
        "not yet approved by the user triggers a popup; the tool runs only "
        "after approval. Workspace roots and remembered paths are skipped."
    ),
}


def extract_extra_paths(tool_args: Mapping[str, Any] | None) -> list[str]:
    """Return de-duplicated ``extra.paths`` strings from tool arguments."""
    if not isinstance(tool_args, Mapping):
        return []
    extra = tool_args.get("extra")
    if not isinstance(extra, dict):
        return []
    raw = extra.get("paths")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text:
            continue
        key = text.replace("\\", "/").rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def extra_options_from_inputs(inputs: Mapping[str, Any] | None) -> dict[str, Any] | None:
    paths = extract_extra_paths(inputs)
    if not paths:
        return None
    return {"extra": {"paths": paths}}


def extra_paths_file_action(tool_name: str) -> FileAction:
    """Write-ish tools declare extra.paths as write; others as read."""
    return "write" if tool_name in _EXTRA_WRITE_TOOLS else "read"


def extra_paths_ask_result(paths: list[str]) -> PermissionResult:
    """HITL ASK for model-declared extra.paths (file_guard defaults do not skip)."""
    sample = paths[0] if paths else ""
    return PermissionResult(
        permission=PermissionLevel.ASK,
        reason=f"extra.paths requires approval: {sample}",
        matched_rule="extra.paths",
        external_paths=list(paths),
    )


def extra_paths_schema_property(language: str = "cn") -> dict[str, Any]:
    lang = language if language in ("cn", "en") else "cn"
    desc = _EXTRA_PATHS_DESC[lang]
    return {
        "type": "object",
        "additionalProperties": False,
        "description": desc,
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": desc,
            },
        },
        "required": ["paths"],
    }


def attach_extra_paths_param(schema: dict[str, Any], language: str = "cn") -> dict[str, Any]:
    """Add ``extra.paths`` to a tool JSON schema (in place and returned)."""
    props = schema.setdefault("properties", {})
    if not isinstance(props, dict):
        props = {}
        schema["properties"] = props
    props["extra"] = extra_paths_schema_property(language)
    return schema


def bind_tool_access_extra(inputs: Mapping[str, Any] | None) -> Token:
    return _TOOL_ACCESS_EXTRA.set(extra_options_from_inputs(inputs))


def current_tool_access_extra() -> dict[str, Any] | None:
    return _TOOL_ACCESS_EXTRA.get()


def reset_tool_access_extra(token: Token) -> None:
    _TOOL_ACCESS_EXTRA.reset(token)


def with_access_extra(fn):  # noqa: ANN001
    """Bind ``extra.paths`` from tool inputs for the duration of invoke/stream."""

    if inspect.isasyncgenfunction(fn):
        @functools.wraps(fn)
        async def gen_wrapper(self, inputs, *args, **kwargs):  # noqa: ANN001
            token = bind_tool_access_extra(inputs if isinstance(inputs, dict) else {})
            try:
                async for item in fn(self, inputs, *args, **kwargs):
                    yield item
            finally:
                reset_tool_access_extra(token)

        return gen_wrapper

    @functools.wraps(fn)
    async def wrapper(self, inputs, *args, **kwargs):  # noqa: ANN001
        token = bind_tool_access_extra(inputs if isinstance(inputs, dict) else {})
        try:
            return await fn(self, inputs, *args, **kwargs)
        finally:
            reset_tool_access_extra(token)

    return wrapper
