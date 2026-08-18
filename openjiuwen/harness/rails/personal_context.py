# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rail that attaches the published PersonalContext context description for one model call."""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ModelCallInputs,
)
from openjiuwen.harness.prompts import PromptAttachmentKind, PromptAttachmentManager
from openjiuwen.harness.rails.base import DeepAgentRail

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent


_SECTION = "personal_context"
_SOURCE = "personal_context_rail"
_CONFIG_FILENAME = "personal_context.yaml"
_MAX_CONFIG_BYTES = 4 * 1024 * 1024
_MAX_DESCRIPTION_CHARS = 3000
_TRUNCATION_NOTICE = "本次仅载入前 3000 个字符；根说明文件更大，请按 description_path 继续读取。"


def _warn(operation: str, exc: BaseException | None = None) -> None:
    """Write a bounded warning without exposing file contents or session data."""

    if exc is None:
        logger.warning("[PersonalContextRail] %s", operation)
    else:
        logger.warning("[PersonalContextRail] %s failed (%s)", operation, type(exc).__name__)


def _runtime_enabled(config_path: Path) -> bool:
    """Read the fixed runtime switch without following unsafe config paths."""

    try:
        current = config_path
        while True:
            if current.is_symlink():
                return False
            parent = current.parent
            if parent == current:
                break
            current = parent

        with config_path.open("rb") as file:
            opened = os.fstat(file.fileno())
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > _MAX_CONFIG_BYTES:
                return False
            payload = file.read(_MAX_CONFIG_BYTES + 1)
        if len(payload) > _MAX_CONFIG_BYTES:
            return False
        loaded = yaml.safe_load(payload.decode("utf-8"))
        if not isinstance(loaded, dict):
            return False
        enabled = loaded.get("enabled")
        return isinstance(enabled, bool) and enabled
    except Exception:
        return False


def _messages_are_contiguous(messages: list[Any]) -> bool:
    """Check that every tool-call group remains adjacent to its tool results."""

    seen_ids: set[str] = set()
    pending_ids: set[str] = set()

    for message in messages:
        if pending_ids:
            if not isinstance(message, ToolMessage):
                return False
            tool_call_id = message.tool_call_id
            if not isinstance(tool_call_id, str) or not tool_call_id.strip():
                return False
            if tool_call_id not in pending_ids:
                return False
            pending_ids.remove(tool_call_id)
            continue

        if isinstance(message, ToolMessage):
            return False
        if not isinstance(message, AssistantMessage):
            continue

        tool_calls = message.tool_calls
        if not tool_calls:
            continue
        if not isinstance(tool_calls, (list, tuple)):
            return False

        current_ids: list[str] = []
        for tool_call in tool_calls:
            tool_call_id = getattr(tool_call, "id", None)
            if not isinstance(tool_call_id, str) or not tool_call_id.strip():
                return False
            if tool_call_id in seen_ids or tool_call_id in current_ids:
                return False
            current_ids.append(tool_call_id)
        seen_ids.update(current_ids)
        pending_ids.update(current_ids)

    return not pending_ids


def _read_description(path: Path) -> tuple[str, int] | None:
    """Read a regular UTF-8 description file, retaining one extra char for truncation detection."""

    path_stat = path.stat()
    if not stat.S_ISREG(path_stat.st_mode) or path.is_symlink():
        raise OSError("description path is not a regular file")
    with path.open("r", encoding="utf-8", errors="strict") as file:
        content = file.read(_MAX_DESCRIPTION_CHARS + 1)
    if not content.strip():
        return None
    return content, path_stat.st_size


def _render_content(
    context_root: Path,
    description_path: Path,
    description: str,
    *,
    description_size_bytes: int,
) -> str:
    """Render the fixed, non-user-request attachment wrapper."""

    truncated = len(description) > _MAX_DESCRIPTION_CHARS
    body = description[:_MAX_DESCRIPTION_CHARS] if truncated else description
    sources_description_path = context_root / "sources" / "description.md"
    if truncated:
        body = f"{body}\n\n[{_TRUNCATION_NOTICE}]"
    return (
        "# 主动上下文\n\n"
        "这是当前模型调用的临时运行时附件，不是新的用户请求；仅在与当前任务相关时使用。\n\n"
        f"- context_root: `{context_root}`\n"
        f"- description_path: `{description_path}`\n"
        f"- description_size_bytes: `{description_size_bytes}`\n"
        f"- sources_description_path: `{sources_description_path}`\n"
        "- filesystem access: 从顶层 description.md 开始，按其中相对链接继续读取。\n\n"
        "## 当前上下文说明\n\n"
        f"{body}"
    )


class PersonalContextRail(DeepAgentRail):
    """Read ``description.md`` and attach it temporarily before a model call."""

    priority = 40

    def __init__(self, home: str | Path) -> None:
        super().__init__()
        self._home = Path(home).expanduser().resolve()
        self._config_path = self._home / _CONFIG_FILENAME
        self._context_root = self._home / "workspace" / "context"
        self._description_path = self._context_root / "description.md"
        self._attachment_manager: PromptAttachmentManager | None = None

    def init(self, agent: "DeepAgent") -> None:
        """Save the existing agent attachment manager for this rail."""

        try:
            manager = getattr(agent, "prompt_attachment_manager", None)
        except Exception as exc:
            self._attachment_manager = None
            _warn("read attachment manager", exc)
            return
        if isinstance(manager, PromptAttachmentManager):
            self._attachment_manager = manager
        else:
            self._attachment_manager = None
            _warn("attachment manager unavailable")

    def uninit(self, agent: "DeepAgent") -> None:
        """Drop the manager reference; synchronous rail teardown does no I/O."""

        del agent
        self._attachment_manager = None

    async def _clear_section(self, ctx: AgentCallbackContext) -> bool:
        manager = self._attachment_manager
        if manager is None:
            return False
        try:
            writer = manager.bind_context(ctx)
            await writer.clear_section(_SECTION)
            return True
        except Exception as exc:
            _warn("clear attachment section", exc)
            return False

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Clear stale context and attach the current description when safe."""

        manager = self._attachment_manager
        if manager is None:
            return
        try:
            writer = manager.bind_context(ctx)
            await writer.clear_section(_SECTION)
        except Exception as exc:
            _warn("clear attachment section", exc)
            return

        try:
            runtime_enabled = await asyncio.to_thread(_runtime_enabled, self._config_path)
        except Exception as exc:
            _warn("read runtime switch", exc)
            return
        if not runtime_enabled:
            return

        inputs = ctx.inputs
        if not isinstance(inputs, ModelCallInputs):
            return
        messages = inputs.messages
        if not isinstance(messages, list) or not messages or not _messages_are_contiguous(messages):
            return

        try:
            description_result = await asyncio.to_thread(_read_description, self._description_path)
        except Exception as exc:
            _warn("read description", exc)
            return
        if description_result is None:
            return
        description, description_size_bytes = description_result

        content = _render_content(
            self._context_root,
            self._description_path,
            description,
            description_size_bytes=description_size_bytes,
        )
        try:
            await writer.add_section(
                section=_SECTION,
                content=content,
                kind=PromptAttachmentKind.RUNTIME,
                source=_SOURCE,
                priority=self.priority,
                content_kind="text/markdown",
            )
        except Exception as exc:
            _warn("add attachment section", exc)

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Clear the temporary attachment after a model call."""

        await self._clear_section(ctx)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Clear the temporary attachment when the whole invoke finishes."""

        await self._clear_section(ctx)


__all__ = ["PersonalContextRail"]
