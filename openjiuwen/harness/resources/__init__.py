# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plugin / AgentTemplate package load and Spec resolve."""

from openjiuwen.harness.resources.extension_loader import (
    find_agent_template_manifest,
    find_plugin_manifest,
    load_agent_template_package,
    load_plugin_package,
    normalize_package_mcps,
)
from openjiuwen.harness.resources.extension_resolver import (
    ExtensionParts,
    LoadRecord,
    ResolvedPromptSection,
    ResolvedSkill,
    ResourceKind,
    ResourceRef,
    resolve_agent_template_parts,
    resolve_plugin_parts,
)
from openjiuwen.harness.schema.extension_spec import (
    AgentRuntimeSpec,
    AgentTemplateSpec,
    McpDirSpec,
    McpServerSpec,
    MemorySpec,
    PluginSpec,
    PromptSectionSpec,
    RubricSpec,
    RuntimeSpec,
    SkillSpec,
)

__all__ = [
    "AgentRuntimeSpec",
    "AgentTemplateSpec",
    "ExtensionParts",
    "LoadRecord",
    "McpDirSpec",
    "McpServerSpec",
    "MemorySpec",
    "PluginSpec",
    "PromptSectionSpec",
    "ResolvedPromptSection",
    "ResolvedSkill",
    "ResourceKind",
    "ResourceRef",
    "RubricSpec",
    "RuntimeSpec",
    "SkillSpec",
    "find_agent_template_manifest",
    "find_plugin_manifest",
    "load_agent_template_package",
    "load_plugin_package",
    "normalize_package_mcps",
    "resolve_agent_template_parts",
    "resolve_plugin_parts",
]
