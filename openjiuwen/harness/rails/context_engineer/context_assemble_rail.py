# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rail that injects workspace and context sections into system prompt builder."""
from __future__ import annotations

import hashlib
from typing import Any, Iterable, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PromptAttachmentKind,
)
from openjiuwen.harness.prompts.sections.workspace import build_workspace_section as _build_workspace
from openjiuwen.harness.prompts.sections.context import (
    build_context_file_sections,
    build_tools_section,
    normalize_tool_name_list,
)


_SYSTEM_CONTEXT_SECTIONS = frozenset({
    "context.agent",
    "context.soul",
    "context.identity",
    "context.user",
})

_ATTACHMENT_CONTEXT_SECTIONS = frozenset({
    "context.heartbeat",
})

_ALL_SPLIT_CONTEXT_SECTIONS = _SYSTEM_CONTEXT_SECTIONS | _ATTACHMENT_CONTEXT_SECTIONS


def _normalize_tool_names(names: Iterable[str] | None) -> set[str]:
    """Keep non-empty string tool names only (ignore None / non-str noise)."""
    return set(normalize_tool_name_list(names))


class ContextAssembleRail(DeepAgentRail):
    """Rail that injects workspace directory structure and context files into system prompt.

    In ``init``, captures references to ``system_prompt_builder`` and ``ability_manager``.

    In ``before_model_call``, builds and injects workspace/context/tools sections
    into the system prompt builder.

    ``disabled_tools`` (constructor / ``update_disabled_tools``) are omitted from
    the ``# 可用工具`` prompt section even when those cards are still briefly
    visible on ``ability_manager``. Callers (e.g. product adapters) own the
    blacklist data flow; this rail does not scan sibling rails.

    ``tool_name_allowlist`` (constructor / ``set_tool_name_allowlist``) optionally
    restricts the tools section to a fixed ordered name list (typically progressive
    eager tools). Membership filters deferred MCP cards; list order stabilizes
    leftover bullets in ``# 可用工具`` so ability_manager registration churn does
    not rewrite the system prefix. Combined with content fingerprinting, this
    keeps the tools section stable mid-task.
    """

    priority = 85

    def __init__(
        self,
        disabled_tools: Iterable[str] | None = None,
        tool_name_allowlist: Iterable[str] | None = None,
    ):
        super().__init__()
        self.system_prompt_builder = None
        self.attachment_manager = None
        self._ability_manager = None
        self._agent: Any | None = None
        self._disabled_tools: set[str] = _normalize_tool_names(disabled_tools)
        # None = unrestricted (legacy). Empty list = allowlist enabled but empty.
        if tool_name_allowlist is None:
            self._tool_name_allowlist: Optional[list[str]] = None
        else:
            self._tool_name_allowlist = normalize_tool_name_list(tool_name_allowlist)
        self._tools_section_fingerprint: str | None = None

    def update_disabled_tools(self, disabled_tools: Iterable[str] | None) -> None:
        """Replace the local blacklist mirror used for the tools prompt section."""
        self._disabled_tools = _normalize_tool_names(disabled_tools)
        # Force one rebuild so hide-list changes appear in the prompt.
        self._tools_section_fingerprint = None

    def set_tool_name_allowlist(self, tool_names: Iterable[str] | None) -> None:
        """Restrict tools prompt section to these names in order (None = unrestricted)."""
        if tool_names is None:
            new_allowlist: Optional[list[str]] = None
        else:
            new_allowlist = normalize_tool_name_list(tool_names)
        if new_allowlist != self._tool_name_allowlist:
            self._tool_name_allowlist = new_allowlist
            self._tools_section_fingerprint = None

    def init(self, agent) -> None:
        """Capture references to system_prompt_builder and ability_manager."""
        self._agent = agent
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self._ability_manager = getattr(agent, "ability_manager", None)
        self.attachment_manager = getattr(agent, "prompt_attachment_manager", None)

    def uninit(self, agent) -> None:
        """Remove workspace, context, and tools sections from system prompt builder."""
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section("workspace")
            self.system_prompt_builder.remove_section("context")
            for section in _ALL_SPLIT_CONTEXT_SECTIONS:
                self.system_prompt_builder.remove_section(section)
            self.system_prompt_builder.remove_section("tools")
            self.system_prompt_builder = None
        self.attachment_manager = None
        self._agent = None
        self._tools_section_fingerprint = None

    async def _upsert_attachment_section(self, writer, section, *, kind) -> None:
        try:
            await writer.add_from_prompt_section(
                prompt_section=section,
                kind=kind,
                source="agent_core.context_assemble_rail",
                language=self.system_prompt_builder.language,
                content_kind="text/markdown",
            )
        except ValueError as exc:
            logger.warning("[ContextAssembleRail] skip prompt attachment section=%s: %s", section.name, exc)

    async def _clear_attachment_section(self, writer, section: str) -> None:
        try:
            await writer.clear_section(section)
        except ValueError as exc:
            logger.warning("[ContextAssembleRail] skip clearing prompt attachment section=%s: %s", section, exc)

    @staticmethod
    def _section_fingerprint(section: Any) -> str:
        content = getattr(section, "content", None)
        if isinstance(content, dict):
            payload = "|".join(f"{key}={content[key]}" for key in sorted(content))
        else:
            payload = str(content or "")
        return hashlib.sha256(payload.encode("utf-8", "ignore")).hexdigest()

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject workspace directory structure and context files into messages before model call."""
        # Bridge path: ctx.agent is the inner ReActAgent. Refresh managers from it,
        # but keep self._agent as the DeepAgent captured in init().
        ctx_agent = getattr(ctx, "agent", None)
        if ctx_agent is not None:
            ability = getattr(ctx_agent, "ability_manager", None)
            if ability is not None:
                self._ability_manager = ability
            spb = getattr(ctx_agent, "system_prompt_builder", None)
            if spb is not None:
                self.system_prompt_builder = spb
            pam = getattr(ctx_agent, "prompt_attachment_manager", None)
            if pam is not None:
                self.attachment_manager = pam

        if self.system_prompt_builder is None:
            return
        writer = None
        if self.attachment_manager is None:
            logger.warning("[ContextAssembleRail] prompt attachment manager is unavailable; skip attachment sections")
        else:
            writer = self.attachment_manager.bind_context(ctx)
        workspace = self.workspace

        if workspace is None:
            self.system_prompt_builder.remove_section("workspace")
            self.system_prompt_builder.remove_section("context")
            for section in _ALL_SPLIT_CONTEXT_SECTIONS:
                self.system_prompt_builder.remove_section(section)
            self.system_prompt_builder.remove_section("tools")
            self._tools_section_fingerprint = None
            if writer is not None:
                await self._clear_attachment_section(writer, "context")
                for section in _ATTACHMENT_CONTEXT_SECTIONS:
                    await self._clear_attachment_section(writer, section)
            return

        lang = self.system_prompt_builder.language
        workspace_section = await _build_workspace(
            self.sys_operation,
            workspace,
            lang,
        )
        tools_section = build_tools_section(
            self._ability_manager,
            lang,
            hidden_tools=self._disabled_tools,
            allowed_tools=self._tool_name_allowlist,
        )
        context_sections = await build_context_file_sections(
            self.sys_operation,
            workspace,
            lang,
        )

        if workspace_section is not None:
            self.system_prompt_builder.add_section(workspace_section)
        else:
            self.system_prompt_builder.remove_section("workspace")

        if tools_section is not None:
            fingerprint = self._section_fingerprint(tools_section)
            if (
                fingerprint != self._tools_section_fingerprint
                or not self.system_prompt_builder.has_section("tools")
            ):
                self.system_prompt_builder.add_section(tools_section)
                self._tools_section_fingerprint = fingerprint
        else:
            self.system_prompt_builder.remove_section("tools")
            self._tools_section_fingerprint = None

        self.system_prompt_builder.remove_section("context")
        if writer is not None:
            await self._clear_attachment_section(writer, "context")

        for section_name in _SYSTEM_CONTEXT_SECTIONS:
            section = context_sections.get(section_name)
            if section is not None:
                self.system_prompt_builder.add_section(section)
            else:
                self.system_prompt_builder.remove_section(section_name)

        for section_name in _ATTACHMENT_CONTEXT_SECTIONS:
            section = context_sections.get(section_name)
            if section is not None:
                if writer is not None:
                    await self._upsert_attachment_section(
                        writer,
                        section,
                        kind=PromptAttachmentKind.FILE
                    )
            else:
                if writer is not None:
                    await self._clear_attachment_section(writer, section_name)
