# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resolve Plugin / AgentTemplate Specs into ExtensionParts.

Materializes declarations from ``schema/extension_spec`` into live objects
without mutating a DeepAgent. Binding is done by
``extension_binder.apply_extension_hot``.

Also defines LoadRecord / ResourceRef accounting types.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.tool import McpServerConfig, Tool, ToolCard
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.harness.schema.build_context import BuildContext
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.schema.deep_agent_spec import SubAgentSpec
from openjiuwen.harness.schema.extension_spec import (
    AgentTemplateSpec,
    McpServerSpec,
    PluginSpec,
    PromptSectionSpec,
    SkillSpec,
    validate_plugin_paths,
)

_TEMPLATE_PATTERN = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")


class ResourceKind(str, Enum):
    """Resource leaf categories for Plugin / AgentTemplate hot-load accounting."""

    TOOL = "tool"
    MCP = "mcp"
    RAIL = "rail"
    PROMPT_SECTION = "prompt_section"
    SKILL = "skill"
    SUBAGENT = "subagent"


class ResourceRef(BaseModel):
    """Reference to one runtime binding actually applied this load."""

    kind: ResourceKind
    identity: str
    extra: dict[str, Any] = Field(default_factory=dict)


class LoadRecord(BaseModel):
    """Facts produced by one Plugin / AgentTemplate hot load."""

    load_id: str = Field(default_factory=lambda: uuid4().hex)
    source_uri: str | None = None
    refs: list[ResourceRef] = Field(default_factory=list)


@dataclass(frozen=True)
class ResolvedSkill:
    """Resolved skill directory reference."""

    directory: str
    mode: Literal["all", "auto_list"]
    enabled_skills: list[str] | None = None


@dataclass(frozen=True)
class ResolvedPromptSection:
    """One resolved prompt section plus its host-bind conflict policy.

    ``replace_existing`` is only true for the AgentTemplate root persona
    ``identity`` section: it replaces (and snapshots) any existing same-name
    section on bind instead of failing the batch on conflict, and restores
    the snapshot on unload. Plugin prompt sections never set this.
    """

    section: PromptSection
    replace_existing: bool = False


@dataclass
class ExtensionParts:
    """Resolve -> apply handoff for one Plugin / AgentTemplate hot load."""

    tools: list[Tool | ToolCard] = field(default_factory=list)
    mcps: list[McpServerConfig] = field(default_factory=list)
    rails: list[AgentRail] = field(default_factory=list)
    prompt_sections: list[ResolvedPromptSection] = field(default_factory=list)
    skills: list[ResolvedSkill] = field(default_factory=list)
    subagents: list[SubAgentConfig] = field(default_factory=list)


def resolve_plugin_parts(spec: PluginSpec, ctx: BuildContext) -> ExtensionParts:
    """Materialize a ``PluginSpec`` into live Parts. Never produces subagents."""
    validate_plugin_paths(spec)
    language = ctx.language or "cn"
    return ExtensionParts(
        tools=_resolve_tools(spec.tools, language=language, ctx=ctx),
        mcps=[_build_mcp_server_config(mcp) for mcp in spec.mcps],
        rails=_resolve_rails(spec.rails, language=language, ctx=ctx),
        prompt_sections=[
            ResolvedPromptSection(section=_resolve_prompt_section(section, ctx), replace_existing=False)
            for section in spec.prompt_sections
        ],
        skills=[_resolve_skill(skill) for skill in spec.skills],
    )


def resolve_agent_template_parts(spec: AgentTemplateSpec, ctx: BuildContext) -> ExtensionParts:
    """Materialize an ``AgentTemplateSpec`` into live Parts.

    Overlays the root persona/capabilities and materializes direct
    ``subagents`` as ``SubAgentConfig``. The host ``agent_card`` / model are
    never touched here; the caller (DeepAgent) keeps them unchanged.
    """
    validate_plugin_paths(spec)
    language = ctx.language or "cn"
    parent_model = (ctx.extras or {}).get("_parent_model")
    if spec.subagents and parent_model is None:
        raise ValueError("AgentTemplate subagent resolve requires BuildContext.extras['_parent_model']")

    subagents: list[SubAgentConfig] = []
    for child in spec.subagents:
        subagent_spec = _adapt_agent_template_to_subagent_spec(child, ctx)
        built = subagent_spec.build(parent_model=parent_model, language=language, context=ctx)
        if not isinstance(built, SubAgentConfig):
            raise TypeError(f"SubAgentSpec.build returned {type(built).__name__}, expected SubAgentConfig")
        subagents.append(built)

    return ExtensionParts(
        tools=_resolve_tools(spec.tools, language=language, ctx=ctx),
        mcps=[_build_mcp_server_config(mcp) for mcp in spec.mcps],
        rails=_resolve_rails(spec.rails, language=language, ctx=ctx),
        prompt_sections=[
            ResolvedPromptSection(section=_resolve_prompt_section(section, ctx), replace_existing=True)
            for section in spec.prompt_sections
        ],
        skills=[_resolve_skill(skill) for skill in spec.skills],
        subagents=subagents,
    )


def _adapt_agent_template_to_subagent_spec(template: AgentTemplateSpec, ctx: BuildContext) -> SubAgentSpec:
    """Internal adapter: direct child ``AgentTemplateSpec`` -> ``SubAgentSpec``.

    Only used for the root template's direct subagents (already non-recursive
    by construction: the child loader fixes ``subagents=[]``). Public package
    schemas never see ``SubAgentSpec``.
    """
    language = ctx.language or "cn"
    ordered_sections = sorted(template.prompt_sections, key=lambda item: item.priority)
    rendered = [_render_prompt_section_text(section, ctx, language) for section in ordered_sections]
    return SubAgentSpec(
        agent_card=template.agent_card,
        system_prompt="\n\n".join(rendered),
        tools=list(template.tools),
        mcps=[_build_mcp_server_config(mcp) for mcp in template.mcps],
        model=template.model,
        rails=list(template.rails) or None,
        skills=[skill.dir for skill in template.skills] or None,
        language=language,
    )


def _resolve_tools(specs: list[Any], *, language: str, ctx: BuildContext) -> list[Tool | ToolCard]:
    tools: list[Tool | ToolCard] = []
    for tool_spec in specs:
        tools.extend(_as_built_list(tool_spec.build(language=language, context=ctx)))
    return tools


def _resolve_rails(specs: list[Any], *, language: str, ctx: BuildContext) -> list[AgentRail]:
    rails: list[AgentRail] = []
    for rail_spec in specs:
        rails.extend(_as_built_list(rail_spec.build(language=language, workspace=ctx.workspace, context=ctx)))
    return rails


def _as_built_list(built: Any) -> list:
    if built is None:
        return []
    if isinstance(built, list):
        return [item for item in built if item is not None]
    return [built]


def _build_mcp_server_config(spec: McpServerSpec) -> McpServerConfig:
    """Convert an already-absolutized ``McpServerSpec`` into a runtime config.

    No relative-path resolution here: the package loader guarantees every
    path field (including stdio ``cwd``) is already absolute.
    """
    params = dict(spec.params or {})
    if spec.type == "stdio":
        if spec.command:
            params.setdefault("command", spec.command)
        if spec.args:
            params.setdefault("args", list(spec.args))
        if spec.env:
            params.setdefault("env", dict(spec.env))
        if spec.cwd:
            params.setdefault("cwd", spec.cwd)
        server_path = spec.command or spec.server_name or "stdio"
    else:
        server_path = spec.url or spec.command

    config_kwargs: dict[str, Any] = {
        "server_name": spec.server_name or spec.command or "mcp_server",
        "server_path": server_path,
        "client_type": spec.type,
        "params": params,
        "auth_headers": dict(spec.auth_headers or {}),
        "auth_query_params": dict(spec.auth_query_params or {}),
    }
    if spec.server_id:
        config_kwargs["server_id"] = spec.server_id
    return McpServerConfig(**config_kwargs)


def _resolve_prompt_section(spec: PromptSectionSpec, ctx: BuildContext) -> PromptSection:
    params = _render_params(spec.render_params, ctx)
    content = {language: _render_template(text, params) for language, text in spec.content.items()}
    return PromptSection(name=spec.name, content=content, priority=spec.priority)


def _render_prompt_section_text(spec: PromptSectionSpec, ctx: BuildContext, language: str) -> str:
    content = _select_content(spec.content, language)
    return _render_template(content, _render_params(spec.render_params, ctx))


def _resolve_skill(spec: SkillSpec) -> ResolvedSkill:
    return ResolvedSkill(directory=spec.dir, mode=spec.mode, enabled_skills=spec.enabled_skills)


def _select_content(content: dict[str, str], language: str) -> str:
    if language in content:
        return content[language]
    if "en" in content:
        return content["en"]
    if "cn" in content:
        return content["cn"]
    if content:
        return next(iter(content.values()))
    return ""


def _render_params(render_params: dict[str, Any], ctx: BuildContext) -> dict[str, Any]:
    values: dict[str, Any] = {
        "language": ctx.language or "cn",
        "workspace": getattr(ctx.workspace, "root_path", None) or "",
    }
    values.update(dict(render_params or {}))
    return values


def _render_template(text: str, params: dict[str, Any]) -> str:
    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in params:
            return match.group(0)
        return str(params[key])

    return _TEMPLATE_PATTERN.sub(_replace, text)


__all__ = [
    "ExtensionParts",
    "LoadRecord",
    "ResolvedPromptSection",
    "ResolvedSkill",
    "ResourceKind",
    "ResourceRef",
    "resolve_agent_template_parts",
    "resolve_plugin_parts",
]
