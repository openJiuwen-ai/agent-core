# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ExpertHarness hot-load resolve surface (canonicalize + Parts + LoadRecord).

DeepAgent owns orchestration; this module materializes declarations into
``ExpertHarnessParts`` without touching a live agent.
``apply_expert_harness_hot`` in ``expert_harness_runtime`` binds the parts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.tool import McpServerConfig, Tool, ToolCard
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.harness.schema.build_context import BuildContext
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.schema.deep_agent_spec import BuiltinToolSpec, RailSpec, SubAgentSpec
from openjiuwen.harness.schema.expert_harness_spec import (
    ExpertHarnessSpec,
    FileSectionSpec,
    McpServerSpec,
    PromptSectionSpec,
    ResourceSource,
    SkillSpec,
    validate_expert_harness_paths,
)


class ResourceKind(str, Enum):
    """Resource leaf categories for ExpertHarness hot-load accounting."""

    TOOL = "tool"
    MCP = "mcp"
    RAIL = "rail"
    PROMPT_SECTION = "prompt_section"
    FILE_SECTION = "file_section"
    SKILL = "skill"
    SUBAGENT = "subagent"

_TEMPLATE_PATTERN = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")
_SUBAGENT_RAIL_TYPE = "core.subagent"

# YAML / legacy tool-group names that become a sys_operation rail.
_SYSOP_TOOL_GROUPS = frozenset(
    {
        "core.filesystem",
        "core.shell",
        "core.bash",
        "core.powershell",
        "core.code",
        "filesystem",
        "shell",
        "bash",
        "powershell",
        "code",
    }
)

# tool type → Spec catalog rail (remove tool, ensure rail)
_TOOL_TO_RAIL_MAP = {
    "core.todo": "core.task_planning",
    "todo": "core.task_planning",
    "core.lsp": "core.lsp",
    "lsp": "core.lsp",
    # AskUserRail owns AskUserTool; YAML tools: ask_user → rail, not a tool provider.
    "core.ask_user": "core.ask_user",
    "ask_user": "core.ask_user",
    "core.ask_user_tool": "core.ask_user",
    "ask_user_tool": "core.ask_user",
}

# subagent factory / short name → core.subagent.*
_SUBAGENT_FACTORY_MAP = {
    "explore": "core.subagent.explore_agent",
    "explore_agent": "core.subagent.explore_agent",
    "core.explore_agent": "core.subagent.explore_agent",
    "plan": "core.subagent.plan_agent",
    "plan_agent": "core.subagent.plan_agent",
    "core.plan_agent": "core.subagent.plan_agent",
    "browser": "core.subagent.browser_agent",
    "browser_agent": "core.subagent.browser_agent",
    "core.browser_agent": "core.subagent.browser_agent",
    "code": "core.subagent.code_agent",
    "code_agent": "core.subagent.code_agent",
    "core.code_agent": "core.subagent.code_agent",
    "research": "core.subagent.research_agent",
    "research_agent": "core.subagent.research_agent",
    "core.research_agent": "core.subagent.research_agent",
    "verification": "core.subagent.verification_agent",
    "verification_agent": "core.subagent.verification_agent",
    "core.verification_agent": "core.subagent.verification_agent",
    "general_purpose": "core.subagent.general_purpose_agent",
    "general_purpose_agent": "core.subagent.general_purpose_agent",
    "general-purpose": "core.subagent.general_purpose_agent",
    "core.general_purpose_agent": "core.subagent.general_purpose_agent",
}


class ResourceRef(BaseModel):
    """Reference to one runtime binding actually applied this load."""

    kind: ResourceKind
    identity: str
    extra: dict[str, Any] = Field(default_factory=dict)


class LoadRecord(BaseModel):
    """Facts produced by one ExpertHarness hot load."""

    load_id: str = Field(default_factory=lambda: uuid4().hex)
    source_uri: str | None = None
    refs: list[ResourceRef] = Field(default_factory=list)


@dataclass(frozen=True)
class ResolvedFileSection:
    """File section target and rendered content, without writing workspace."""

    filename: str
    target: Path
    content: str


@dataclass(frozen=True)
class ResolvedSkill:
    """Resolved skill directory reference."""

    directory: str
    mode: Literal["all", "auto_list"]
    enabled_skills: list[str] | None = None


@dataclass
class ExpertHarnessParts:
    """Resolve ↔ apply handoff for one ExpertHarness hot load."""

    tools: list[Tool | ToolCard] = field(default_factory=list)
    mcps: list[McpServerConfig] = field(default_factory=list)
    rails: list[AgentRail] = field(default_factory=list)
    prompt_sections: list[PromptSection] = field(default_factory=list)
    file_sections: list[ResolvedFileSection] = field(default_factory=list)
    skills: list[ResolvedSkill] = field(default_factory=list)
    subagents: list[SubAgentConfig] = field(default_factory=list)


def canonicalize_expert_harness_spec(spec: ExpertHarnessSpec) -> ExpertHarnessSpec:
    """Map legacy YAML short names onto Spec catalog names; derive subagent rail."""
    tools: list[BuiltinToolSpec] = []
    rails = list(spec.rails)
    rail_types = {rail.type for rail in rails}
    need_sys_operation = False

    for tool in spec.tools:
        raw_type = tool.type
        if raw_type in _SYSOP_TOOL_GROUPS or raw_type.removeprefix("core.") in {
            "filesystem",
            "shell",
            "bash",
            "powershell",
            "code",
        }:
            need_sys_operation = True
            continue
        if raw_type in _TOOL_TO_RAIL_MAP:
            rail_type = _TOOL_TO_RAIL_MAP[raw_type]
            if rail_type not in rail_types:
                rails.append(RailSpec(type=rail_type, params={}))
                rail_types.add(rail_type)
            continue
        tools.append(BuiltinToolSpec(type=raw_type, params=dict(tool.params)))

    if need_sys_operation and "core.sys_operation" not in rail_types:
        rails.append(RailSpec(type="core.sys_operation", params={}))
        rail_types.add("core.sys_operation")

    subagents: list[SubAgentSpec] = []
    for sub in spec.subagents:
        factory_name = sub.factory_name
        if factory_name:
            factory_name = _SUBAGENT_FACTORY_MAP.get(factory_name, factory_name)
            if factory_name.startswith("core.") and not factory_name.startswith("core.subagent."):
                short = factory_name.removeprefix("core.")
                factory_name = _SUBAGENT_FACTORY_MAP.get(short, factory_name)
                factory_name = _SUBAGENT_FACTORY_MAP.get(factory_name, factory_name)
        subagents.append(
            sub.model_copy(
                update={
                    "factory_name": factory_name,
                    "tools": [
                        BuiltinToolSpec(type=t.type, params=dict(t.params))
                        if isinstance(t, BuiltinToolSpec)
                        else t
                        for t in sub.tools
                    ],
                }
            )
        )

    if spec.config.enable_subagent and subagents and _SUBAGENT_RAIL_TYPE not in rail_types:
        rails.append(
            RailSpec(
                type=_SUBAGENT_RAIL_TYPE,
                params={
                    "enable_async_subagent": spec.config.subagent_delegate_type == "async",
                },
            )
        )

    return spec.model_copy(update={"tools": tools, "rails": rails, "subagents": subagents})


def resolve_expert_harness_parts(spec: ExpertHarnessSpec, ctx: BuildContext) -> ExpertHarnessParts:
    """Materialize an ExpertHarnessSpec into live objects (no agent mutation)."""
    spec = canonicalize_expert_harness_spec(spec)
    validate_expert_harness_paths(spec)
    source = spec.source or ResourceSource(uri=None, root=".")
    language = ctx.language or "cn"
    workspace = ctx.workspace

    tools: list[Tool | ToolCard] = []
    for tool_spec in spec.tools:
        built = tool_spec.build(language=language, context=ctx)
        tools.extend(_as_built_list(built))

    rails: list[AgentRail] = []
    for rail_spec in spec.rails:
        built = rail_spec.build(language=language, workspace=workspace, context=ctx)
        rails.extend(_as_built_list(built))

    parent_model = (ctx.extras or {}).get("_parent_model")
    subagents: list[SubAgentConfig] = []
    for sub_spec in spec.subagents:
        if parent_model is None and sub_spec.factory_name:
            raise ValueError(
                "ExpertHarness subagent resolve requires BuildContext.extras['_parent_model'] "
                f"(factory_name={sub_spec.factory_name!r})"
            )
        built = sub_spec.build(
            parent_model=parent_model,
            language=language,
            context=ctx,
        )
        if not isinstance(built, SubAgentConfig):
            raise TypeError(
                f"SubAgentSpec.build returned {type(built).__name__}, expected SubAgentConfig"
            )
        subagents.append(built)

    return ExpertHarnessParts(
        tools=tools,
        mcps=[_resolve_mcp(mcp, source) for mcp in spec.mcps],
        rails=rails,
        prompt_sections=[_resolve_prompt_section(section, ctx) for section in spec.prompt_sections],
        file_sections=[_resolve_file_section(section, source, ctx) for section in spec.file_sections],
        skills=[_resolve_skill(skill, source) for skill in spec.skills],
        subagents=subagents,
    )


def _as_built_list(built: Any) -> list:
    if built is None:
        return []
    if isinstance(built, list):
        return [item for item in built if item is not None]
    return [built]


def _source_root(source: ResourceSource) -> Path:
    return Path(source.root or ".").expanduser().resolve()


def _resolve_package_path(value: str | Path, source: ResourceSource) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (_source_root(source) / path).resolve()


def _resolve_mcp(spec: McpServerSpec, source: ResourceSource | None = None) -> McpServerConfig:
    params = dict(spec.params or {})
    if spec.type == "stdio":
        if spec.command:
            params.setdefault("command", spec.command)
        if spec.args:
            params.setdefault("args", list(spec.args))
        if spec.env:
            params.setdefault("env", dict(spec.env))
        if spec.cwd:
            cwd = str(_resolve_package_path(spec.cwd, source)) if source is not None else spec.cwd
            params.setdefault("cwd", cwd)
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


def _resolve_file_section(
    spec: FileSectionSpec,
    source: ResourceSource,
    ctx: BuildContext,
) -> ResolvedFileSection:
    workspace_root = _workspace_root(ctx.workspace, source)
    target = (workspace_root / spec.filename).resolve()
    if not _is_relative_to(target, workspace_root):
        raise ValueError(f"File-backed prompt section escapes workspace: {spec.filename}")

    params = _render_params(spec.render_params, ctx)
    raw_content = _select_content(spec.content, ctx.language or "cn")
    return ResolvedFileSection(
        filename=spec.filename,
        target=target,
        content=_render_template(raw_content, params),
    )


def _resolve_skill(spec: SkillSpec, source: ResourceSource) -> ResolvedSkill:
    directory = _resolve_package_path(spec.dir, source)
    return ResolvedSkill(
        directory=str(directory),
        mode=spec.mode,
        enabled_skills=spec.enabled_skills,
    )


def _workspace_root(workspace: Any, source: ResourceSource) -> Path:
    root = getattr(workspace, "root_path", None)
    if root:
        return Path(str(root)).expanduser().resolve()
    return _source_root(source)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


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
    "ExpertHarnessParts",
    "LoadRecord",
    "ResolvedFileSection",
    "ResolvedSkill",
    "ResourceKind",
    "ResourceRef",
    "canonicalize_expert_harness_spec",
    "resolve_expert_harness_parts",
]
