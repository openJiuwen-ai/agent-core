# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PersonalContext Rail (Wiki + profile inject) and IM Search tool registration."""

from __future__ import annotations

import asyncio
import os
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ModelCallInputs,
)
from openjiuwen.harness.personal_context.distill import resolve_current_profile
from openjiuwen.harness.prompts import PromptAttachmentKind, PromptAttachmentManager
from openjiuwen.harness.rails.base import DeepAgentRail

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent


_SECTION = "personal_context"
_SOURCE = "personal_context_rail"
_CONFIG_FILENAME = "personal_context.yaml"
_MAX_CONFIG_BYTES = 4 * 1024 * 1024
_MAX_DESCRIPTION_CHARS = 3000
_MAX_PROFILE_CHARS = 3000
_TRUNCATION_NOTICE = "本次仅载入前 3000 个字符；根说明文件更大，请按 description_path 继续读取。"
_PROFILE_TRUNCATION_NOTICE = "本次仅载入前 3000 个字符。"


def _warn(operation: str, exc: BaseException | None = None) -> None:
    """Write a bounded warning without exposing file contents or session data."""

    if exc is None:
        logger.warning("[PersonalContextRail] %s", operation)
    else:
        logger.warning("[PersonalContextRail] %s failed (%s)", operation, type(exc).__name__)


def _agent_use_enabled(config_path: Path) -> bool:
    """Read the fixed Agent-use switch without following unsafe config paths."""

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
        enabled = loaded.get("agent_use_enabled")
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


def _clip_profile_text(text: str, *, limit: int = _MAX_PROFILE_CHARS) -> str:
    value = text or ""
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n\n[{_PROFILE_TRUNCATION_NOTICE}]"


def _render_attachment(
    context_root: Path,
    description_path: Path,
    *,
    description: str | None,
    description_size_bytes: int | None,
    profile: dict[str, Any] | None,
) -> str:
    """Render Wiki and/or current profile into one RUNTIME attachment."""

    parts: list[str] = [
        "# 主动上下文",
        "",
        "这是当前模型调用的临时运行时附件，不是新的用户请求；仅在与当前任务相关时使用。",
        "",
    ]

    if description is not None and description_size_bytes is not None:
        truncated = len(description) > _MAX_DESCRIPTION_CHARS
        body = description[:_MAX_DESCRIPTION_CHARS] if truncated else description
        if truncated:
            body = f"{body}\n\n[{_TRUNCATION_NOTICE}]"
        parts.extend(
            [
                f"- context_root: `{context_root}`",
                f"- description_path: `{description_path}`",
                f"- description_size_bytes: `{description_size_bytes}`",
                "- filesystem access: 从顶层 description.md 开始，按其中相对链接继续读取。",
                "",
                "## 当前上下文说明",
                "",
                body,
            ]
        )

    if profile is not None:
        if description is not None:
            parts.append("")
        job_id = str(profile.get("job_id") or "").strip()
        source = str(profile.get("source") or "").strip()
        persona = _clip_profile_text(str(profile.get("persona_md") or ""))
        work = _clip_profile_text(str(profile.get("work_md") or ""))
        parts.extend(
            [
                "## 现行用户画像",
                "",
                f"- job_id: `{job_id}`",
                f"- source: `{source}`",
                "",
                "### Persona",
                "",
                persona.rstrip() + ("\n" if persona else ""),
                "### Work",
                "",
                work.rstrip() + ("\n" if work else ""),
            ]
        )

    return "\n".join(parts).rstrip() + "\n"


def register_im_search_tools(agent: Any, tools: Sequence[Any]) -> None:
    """Register caller-provided IM Search tools on the agent ability surface.

    ``tools`` must already be constructed by the upstream search module (or the
    caller). This function only mounts them via ``ability_manager.add_ability``;
    it does not implement search, indexing, or tool construction.
    """

    if not tools:
        return
    ability_manager = getattr(agent, "ability_manager", None)
    if ability_manager is None or not hasattr(ability_manager, "add_ability"):
        _warn("register im search tools: ability_manager unavailable")
        return
    for tool in tools:
        try:
            tool_card = getattr(tool, "card", None)
            if tool_card is None:
                _warn("register im search tools: tool missing card")
                continue
            ability_manager.add_ability(tool_card, tool)
        except Exception as exc:
            _warn("register im search tool", exc)


class PersonalContextRail(DeepAgentRail):
    """Attach Wiki description and/or current distilled profile before a model call."""

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
        """Clear stale context and attach Wiki and/or current profile when safe."""

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
            agent_use_enabled = await asyncio.to_thread(_agent_use_enabled, self._config_path)
        except Exception as exc:
            _warn("read runtime switch", exc)
            return
        if not agent_use_enabled:
            return

        inputs = ctx.inputs
        if not isinstance(inputs, ModelCallInputs):
            return
        messages = inputs.messages
        if not isinstance(messages, list) or not messages or not _messages_are_contiguous(messages):
            return

        description: str | None = None
        description_size_bytes: int | None = None
        try:
            description_result = await asyncio.to_thread(_read_description, self._description_path)
        except Exception as exc:
            _warn("read description", exc)
        else:
            if description_result is not None:
                description, description_size_bytes = description_result

        try:
            profile = await asyncio.to_thread(resolve_current_profile, str(self._home))
        except Exception as exc:
            _warn("resolve current profile", exc)
            profile = None

        if description is None and profile is None:
            return

        content = _render_attachment(
            self._context_root,
            self._description_path,
            description=description,
            description_size_bytes=description_size_bytes,
            profile=profile,
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


__all__ = ["PersonalContextRail", "register_im_search_tools"]
