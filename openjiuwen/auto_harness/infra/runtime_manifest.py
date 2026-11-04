# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Auto-harness-owned schema + loader for runtime ``harness_config.yaml``.

This is the canonical home for the legacy ``harness_config.yaml`` manifest
format that auto-harness emits for generated runtime extensions.  It is a
verbatim, self-contained copy of the validation contract previously provided
by ``openjiuwen.harness.harness_config``; auto-harness owns this intermediate
artifact format, so the schema lives here rather than under ``harness/``.

Only the parse-and-validate surface needed by the runtime extension loader,
merger, and activate preview is provided: a single manifest file is read and
validated (no sidecar merging), preserving the exact legacy behavior.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml
from pydantic import BaseModel, Field, ValidationError


class MetaSchema(BaseModel):
    """Governance metadata — display and permission management."""

    owner: str = ""
    tags: List[str] = Field(default_factory=list)
    visibility: str = "internal"


class SectionSchema(BaseModel):
    """A single prompt section entry."""

    name: str
    priority: Optional[int] = None
    file: Optional[str] = None
    content: Optional[Union[Dict[str, str], str]] = None

    model_config = {"populate_by_name": True}


class ToolResourceSchema(BaseModel):
    """Tool resource specification."""

    type: str  # builtin | package | entry_point
    names: Optional[List[str]] = None
    name: Optional[str] = None
    package: Optional[str] = None
    module: Optional[str] = None
    class_name: Optional[str] = Field(None, alias="class")
    kwargs: Dict[str, Any] = Field(default_factory=dict)
    params: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class RailResourceSchema(BaseModel):
    """Rail resource specification."""

    type: str  # builtin | package | entry_point
    name: Optional[str] = None
    package: Optional[str] = None
    module: Optional[str] = None
    class_name: Optional[str] = Field(None, alias="class")
    kwargs: Dict[str, Any] = Field(default_factory=dict)
    params: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class SkillsSchema(BaseModel):
    """Skills configuration."""

    dirs: List[str] = Field(default_factory=list)
    mode: str = "all"  # all | auto_list


class McpResourceSchema(BaseModel):
    """MCP server specification."""

    type: str = "stdio"
    name: Optional[str] = None
    server_name: Optional[str] = None
    server_id: Optional[str] = None
    url: Optional[str] = None
    command: str = ""
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    cwd: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    auth_headers: Dict[str, str] = Field(default_factory=dict)
    auth_query_params: Dict[str, str] = Field(default_factory=dict)


class ResourcesSchema(BaseModel):
    """All runtime resources: tools, rails, skills, MCPs."""

    tools: List[ToolResourceSchema] = Field(default_factory=list)
    rails: List[RailResourceSchema] = Field(default_factory=list)
    skills: Optional[SkillsSchema] = None
    mcps: List[McpResourceSchema] = Field(default_factory=list)


class PromptsSchema(BaseModel):
    """Prompt section declarations."""

    sections: List[SectionSchema] = Field(default_factory=list)


class WorkspaceSchema(BaseModel):
    """Workspace (file operation root directory)."""

    root_path: str = "./"


class RuntimeHarnessManifest(BaseModel):
    """Top-level ``harness_config.yaml`` schema for runtime extensions."""

    schema_version: str = "harness_config.v0.1"
    meta: Optional[MetaSchema] = None

    id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None

    workspace: Optional[WorkspaceSchema] = None
    prompts: Optional[PromptsSchema] = None
    resources: Optional[ResourcesSchema] = None

    language: str = "cn"
    max_iterations: Optional[int] = None
    completion_timeout: Optional[float] = None
    enable_task_loop: Optional[bool] = None
    enable_async_subagent: Optional[bool] = None
    add_general_purpose_agent: Optional[bool] = None
    enable_task_planning: Optional[bool] = None
    restrict_to_work_dir: Optional[bool] = None
    prompt_mode: Optional[str] = None
    default_mode: Optional[str] = None
    permissions: Optional[Dict[str, Any]] = None
    progressive_tool_enabled: Optional[bool] = None
    progressive_tool_always_visible_tools: List[str] = Field(default_factory=list)
    progressive_tool_default_visible_tools: List[str] = Field(default_factory=list)
    progressive_tool_max_loaded_tools: Optional[int] = None

    subagents: Optional[List[Dict[str, Any]]] = None
    context: Optional[Dict[str, Any]] = None
    stop_eval_conditions: Optional[Dict[str, Any]] = None

    model_config = {"populate_by_name": True, "extra": "allow"}


def load_runtime_manifest(path: Union[str, Path]) -> RuntimeHarnessManifest:
    """Parse and validate a single ``harness_config.yaml`` file.

    Reads only the declared manifest (no sidecar merging) and validates it
    against the runtime manifest schema, matching the legacy loader contract.

    Raises:
        FileNotFoundError: File does not exist.
        ValueError: Schema validation failed.
    """
    manifest_path = Path(path).resolve()
    if not manifest_path.exists():
        raise FileNotFoundError(f"HarnessConfig file not found: {manifest_path}")

    data: Dict[str, Any] = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    try:
        return RuntimeHarnessManifest.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"HarnessConfig validation failed in '{manifest_path}': {exc}") from exc


__all__ = [
    "McpResourceSchema",
    "MetaSchema",
    "PromptsSchema",
    "RailResourceSchema",
    "ResourcesSchema",
    "RuntimeHarnessManifest",
    "SectionSchema",
    "SkillsSchema",
    "ToolResourceSchema",
    "WorkspaceSchema",
    "load_runtime_manifest",
]
