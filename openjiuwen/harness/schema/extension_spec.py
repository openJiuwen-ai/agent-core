# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plugin / AgentTemplate hot-load Specs. Path fields must be absolute."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.schema.deep_agent_spec import BuiltinToolSpec, ModelSpec, RailSpec


def _plain_data(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list):
        for item in value:
            _plain_data(item)
        return value
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("mapping keys must be strings")
            _plain_data(item)
        return value
    raise ValueError(f"{type(value).__name__} is not JSON/YAML serializable data")


def _validate_plain_mapping(value: dict[str, Any]) -> dict[str, Any]:
    _plain_data(value)
    return value


class _ExtensionSpecModel(BaseModel):
    """Strict base for Plugin / AgentTemplate leaf declarations."""

    model_config = ConfigDict(extra="forbid")


class McpServerSpec(_ExtensionSpecModel):
    """Unified MCP server declaration.

    Accepts either canonical field names (``type`` / ``server_name``) or the
    jiuwenswarm ``config.yaml`` ``mcp.servers`` aliases (``transport`` / ``name``),
    which the package loader normalizes before validation.
    """

    type: Literal["stdio", "sse", "streamable_http"] = "stdio"
    server_name: str | None = None
    server_id: str | None = None
    url: str | None = None
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    auth_headers: dict[str, str] = Field(default_factory=dict)
    auth_query_params: dict[str, str] = Field(default_factory=dict)

    @field_validator("params")
    @classmethod
    def _params_are_plain_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_plain_mapping(value)


class McpDirSpec(_ExtensionSpecModel):
    """Reference a package directory that contains ``mcps.json`` / ``mcps.yaml``."""

    dir: str


class SkillSpec(_ExtensionSpecModel):
    """Skill directory declaration relative to the package root."""

    dir: str
    mode: Literal["all", "auto_list"] = "all"
    enabled_skills: list[str] | None = None


class PromptSectionSpec(_ExtensionSpecModel):
    """Inline prompt section declaration."""

    name: str
    content: dict[str, str]
    priority: int = 100
    render_params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("render_params")
    @classmethod
    def _render_params_are_plain_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_plain_mapping(value)


class RubricSpec(_ExtensionSpecModel):
    """AgentTemplate evaluation rubric; unknown fields are preserved as-is."""

    model_config = ConfigDict(extra="allow")


class MemorySpec(_ExtensionSpecModel):
    """AgentTemplate shared memory reference; unknown fields are preserved as-is."""

    model_config = ConfigDict(extra="allow")


class AgentTemplateSpec(_ExtensionSpecModel):
    """Declarative template for one AgentTemplate role."""

    # Static agent identity parsed from manifest.agentcard
    agent_card: AgentCard

    # Optional model definitions loaded from model.json
    model: ModelSpec | None = None

    # Persona prompt sections (identity, sops, output_contract, safety, ...)
    prompt_sections: list[PromptSectionSpec] = Field(default_factory=list)

    # Template-owned capabilities (may be overlaid by Plugin capabilities)
    tools: list[BuiltinToolSpec] = Field(default_factory=list)
    mcps: list[McpServerSpec] = Field(default_factory=list)
    rails: list[RailSpec] = Field(default_factory=list)
    skills: list[SkillSpec] = Field(default_factory=list)

    # Shared memories (experience, conventions, troubleshooting knowledge)
    memories: list[MemorySpec] = Field(default_factory=list)

    # Evaluation rubrics for output / action quality
    rubrics: list[RubricSpec] = Field(default_factory=list)

    # Direct child templates materialized as runtime subagents
    subagents: list[AgentTemplateSpec] = Field(default_factory=list)

    # Discovery / presentation metadata
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _metadata_is_plain_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_plain_mapping(value)


class PluginSpec(_ExtensionSpecModel):
    """Functional capability bundle mounted onto a host DeepAgent."""

    # Identity and display metadata
    id: str
    name: str | None = None
    description: str | None = None

    # Prompt sections overlaid onto the host DeepAgent only
    prompt_sections: list[PromptSectionSpec] = Field(default_factory=list)

    # Capabilities mounted onto the host
    tools: list[BuiltinToolSpec] = Field(default_factory=list)
    mcps: list[McpServerSpec] = Field(default_factory=list)
    rails: list[RailSpec] = Field(default_factory=list)
    skills: list[SkillSpec] = Field(default_factory=list)

    # Publication / presentation metadata
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _metadata_is_plain_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_plain_mapping(value)


def _require_absolute_path(value: str, *, field_name: str) -> None:
    if not Path(value).expanduser().is_absolute():
        raise ValueError(
            f"{field_name} must already be an absolute path (the package loader or the "
            f"caller of load_plugin_spec is responsible for absolutizing it): {value!r}"
        )


def validate_plugin_paths(spec: PluginSpec | AgentTemplateSpec) -> None:
    """Ensure every path-bearing field on a Plugin / AgentTemplate spec is absolute.

    Specs never carry a package root: the package loader absolutizes paths
    before they reach these models, and ``DeepAgent.load_plugin_spec`` (no
    package root) must reject relative paths rather than resolve them against
    cwd. Recurses into ``AgentTemplateSpec.subagents`` (direct children only).
    """
    for tool in spec.tools:
        if tool.type == "harness.tool.file":
            file_path = tool.params.get("file_path")
            if file_path:
                _require_absolute_path(str(file_path), field_name="tools.params.file_path")
    for rail in spec.rails:
        if rail.type == "harness.rail.file":
            file_path = rail.params.get("file_path")
            if file_path:
                _require_absolute_path(str(file_path), field_name="rails.params.file_path")
    for mcp in spec.mcps:
        if mcp.cwd:
            _require_absolute_path(mcp.cwd, field_name="mcps.cwd")
    for skill in spec.skills:
        _require_absolute_path(skill.dir, field_name="skills.dir")
    for subagent in getattr(spec, "subagents", []):
        validate_plugin_paths(subagent)


__all__ = [
    "AgentTemplateSpec",
    "McpDirSpec",
    "McpServerSpec",
    "MemorySpec",
    "PluginSpec",
    "PromptSectionSpec",
    "RubricSpec",
    "SkillSpec",
    "validate_plugin_paths",
]
