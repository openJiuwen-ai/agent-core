# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ExpertHarnessSpec — hot-load capability declaration (not part of DeepAgentSpec).

Tool / rail / subagent leaves reuse deep Spec types so hot resolve can call the
same ``leaf.build(params, BuildContext)`` path as cold ``DeepAgentSpec.build``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openjiuwen.harness.schema.deep_agent_spec import (
    BuiltinToolSpec,
    RailSpec,
    SubAgentSpec,
)


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


class _ExpertSpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResourceSource(_ExpertSpecModel):
    """Canonical resource package location."""

    uri: str | None = None
    root: str | None = None


class McpServerSpec(_ExpertSpecModel):
    """Unified MCP server declaration."""

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


class SkillSpec(_ExpertSpecModel):
    """Skill directory declaration relative to ResourceSource.root."""

    dir: str
    mode: Literal["all", "auto_list"] = "all"
    enabled_skills: list[str] | None = None


class PromptSectionSpec(_ExpertSpecModel):
    """Inline prompt section declaration."""

    name: str
    content: dict[str, str]
    priority: int = 100
    render_params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("render_params")
    @classmethod
    def _render_params_are_plain_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_plain_mapping(value)


class FileSectionSpec(_ExpertSpecModel):
    """File-backed prompt section declaration."""

    filename: str
    content: dict[str, str]
    render_params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("render_params")
    @classmethod
    def _render_params_are_plain_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_plain_mapping(value)


class ExpertHarnessConfigSpec(_ExpertSpecModel):
    """ExpertHarness capability policy."""

    enable_subagent: bool = False
    subagent_delegate_type: Literal["sync", "async"] = "sync"


class ExpertHarnessSpec(_ExpertSpecModel):
    """Canonical pure-data ExpertHarness capability declaration."""

    schema_version: Literal["expert_harness.v1"] = "expert_harness.v1"
    id: str
    source: ResourceSource | None = None
    name: str | None = None
    description: str | None = None
    config: ExpertHarnessConfigSpec = Field(default_factory=ExpertHarnessConfigSpec)
    tools: list[BuiltinToolSpec] = Field(default_factory=list)
    mcps: list[McpServerSpec] = Field(default_factory=list)
    rails: list[RailSpec] = Field(default_factory=list)
    prompt_sections: list[PromptSectionSpec] = Field(default_factory=list)
    file_sections: list[FileSectionSpec] = Field(default_factory=list)
    skills: list[SkillSpec] = Field(default_factory=list)
    subagents: list[SubAgentSpec] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _metadata_is_plain_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_plain_mapping(value)


_RELATIVE_PATH_HINT = (
    "Relative paths require ExpertHarnessSpec.source.root. Load package-backed "
    "harnesses through load_expert_harness_spec(), or use absolute paths."
)


def _require_resolvable_path(
    path_value: str,
    *,
    field_name: str,
    spec: ExpertHarnessSpec,
) -> None:
    if not Path(path_value).expanduser().is_absolute():
        if spec.source is not None and spec.source.root:
            return
        raise ValueError(
            f"ExpertHarnessSpec field '{field_name}' uses relative path {path_value!r} "
            f"but source.root is missing. {_RELATIVE_PATH_HINT}"
        )


def validate_expert_harness_paths(spec: ExpertHarnessSpec) -> None:
    """Ensure relative ExpertHarnessSpec paths have a bound source root."""

    for tool in spec.tools:
        if tool.type == "harness.tool.file":
            file_path = tool.params.get("file_path")
            if file_path:
                _require_resolvable_path(str(file_path), field_name="tools.params.file_path", spec=spec)
    for rail in spec.rails:
        if rail.type == "harness.rail.file":
            file_path = rail.params.get("file_path")
            if file_path:
                _require_resolvable_path(str(file_path), field_name="rails.params.file_path", spec=spec)
    for mcp in spec.mcps:
        if mcp.cwd:
            _require_resolvable_path(mcp.cwd, field_name="mcps.cwd", spec=spec)
    for skill in spec.skills:
        _require_resolvable_path(skill.dir, field_name="skills.dir", spec=spec)
    for subagent in spec.subagents:
        if subagent.workspace is not None:
            _require_resolvable_path(
                subagent.workspace.root_path,
                field_name="subagents.workspace.root_path",
                spec=spec,
            )
        for tool in subagent.tools:
            if isinstance(tool, BuiltinToolSpec) and tool.type == "harness.tool.file":
                file_path = tool.params.get("file_path")
                if file_path:
                    _require_resolvable_path(
                        str(file_path),
                        field_name="subagents.tools.params.file_path",
                        spec=spec,
                    )
        for rail in subagent.rails or []:
            if rail.type == "harness.rail.file":
                file_path = rail.params.get("file_path")
                if file_path:
                    _require_resolvable_path(
                        str(file_path),
                        field_name="subagents.rails.params.file_path",
                        spec=spec,
                    )


__all__ = [
    "BuiltinToolSpec",
    "ExpertHarnessConfigSpec",
    "ExpertHarnessSpec",
    "FileSectionSpec",
    "McpServerSpec",
    "PromptSectionSpec",
    "RailSpec",
    "ResourceSource",
    "SkillSpec",
    "SubAgentSpec",
    "validate_expert_harness_paths",
]
