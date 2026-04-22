# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pydantic schema models for harness_config.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, Field


class MetaSchema(BaseModel):
    """Governance metadata — display and permission management, not used at runtime."""

    owner: str = ""
    tags: List[str] = []
    visibility: str = "internal"


class SectionSchema(BaseModel):
    """A single prompt section entry.

    Without ``file``: inline YAML content, assembled statically by HarnessConfigBuilder.
    With ``file``: content written to ``workspace/{file}`` by HarnessConfigBuilder,
        read back dynamically by ContextEngineeringRail at each model call.
    """

    name: str
    priority: Optional[int] = None
    file: Optional[str] = None
    content: Optional[Union[Dict[str, str], str]] = None

    model_config = {"populate_by_name": True}


class ToolResourceSchema(BaseModel):
    """Tool resource specification."""

    type: str  # builtin | package | entry_point
    names: Optional[List[str]] = None  # builtin: multiple groups
    name: Optional[str] = None  # builtin: single group / entry_point name
    package: Optional[str] = None  # package: pip package name (informational)
    module: Optional[str] = None  # package: dotted module path
    class_name: Optional[str] = Field(None, alias="class")

    model_config = {"populate_by_name": True}


class RailResourceSchema(BaseModel):
    """Rail resource specification."""

    type: str  # builtin | package | entry_point
    name: Optional[str] = None
    package: Optional[str] = None
    module: Optional[str] = None
    class_name: Optional[str] = Field(None, alias="class")

    model_config = {"populate_by_name": True}


class SkillsSchema(BaseModel):
    """Skills configuration."""

    dirs: List[str] = []
    mode: str = "all"  # all | auto_list


class McpResourceSchema(BaseModel):
    """MCP server specification."""

    type: str = "stdio"  # stdio | sse | streamable_http
    command: str = ""
    args: List[str] = []
    env: Dict[str, str] = {}


class ResourcesSchema(BaseModel):
    """All runtime resources: tools, rails, skills, MCPs."""

    tools: List[ToolResourceSchema] = []
    rails: List[RailResourceSchema] = []
    skills: Optional[SkillsSchema] = None
    mcps: List[McpResourceSchema] = []


class PromptsSchema(BaseModel):
    """Prompt section declarations."""

    sections: List[SectionSchema] = []


class WorkspaceSchema(BaseModel):
    """Workspace (file operation root directory)."""

    root_path: str = "./"


class HarnessConfig(BaseModel):
    """Top-level harness_config.yaml schema."""

    schema_version: str = "harness_config.v0.1"
    meta: Optional[MetaSchema] = None

    # Agent identity → AgentCard.id / .name / .description
    id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None

    workspace: Optional[WorkspaceSchema] = None
    prompts: Optional[PromptsSchema] = None
    resources: Optional[ResourcesSchema] = None

    # Execution control → DeepAgentConfig fields
    language: str = "cn"
    max_iterations: Optional[int] = None
    completion_timeout: Optional[float] = None

    model_config = {"populate_by_name": True, "extra": "allow"}

    def to_yaml(self, output_path: Optional[Union[str, Path]] = None) -> str:
        """Serialize this ``HarnessConfig`` to a YAML string.

        Args:
            output_path: If given, also write the yaml to this file path.

        Returns:
            YAML string.
        """
        import yaml

        data = self.model_dump(exclude_none=True, by_alias=True)
        yaml_str: str = yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
        if output_path is not None:
            Path(output_path).write_text(yaml_str, encoding="utf-8")
        return yaml_str


__all__ = [
    "HarnessConfig",
    "McpResourceSchema",
    "MetaSchema",
    "PromptsSchema",
    "RailResourceSchema",
    "ResourcesSchema",
    "SectionSchema",
    "SkillsSchema",
    "ToolResourceSchema",
    "WorkspaceSchema",
]
