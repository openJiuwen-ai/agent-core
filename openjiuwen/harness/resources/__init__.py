# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plugin / AgentTemplate package load and Spec resolve."""

from openjiuwen.harness.resources.extension_loader import (
    aload_agent_template_package,
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
    render_agent_template_system_prompt,
    render_prompt_content,
    resolve_agent_template_parts,
    resolve_plugin_parts,
)
from openjiuwen.harness.resources.template_card import (
    AgentTemplateCard,
    LocalizedText,
    load_agent_template_card,
    localized_text,
    parse_agent_template_card,
    read_agent_template_card,
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
    "AgentTemplateCard",
    "AgentTemplateSpec",
    "ExtensionParts",
    "LoadRecord",
    "LocalizedText",
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
    "aload_agent_template_package",
    "find_agent_template_manifest",
    "find_plugin_manifest",
    "load_agent_template_card",
    "load_agent_template_package",
    "load_plugin_package",
    "localized_text",
    "normalize_package_mcps",
    "parse_agent_template_card",
    "read_agent_template_card",
    "render_agent_template_system_prompt",
    "render_prompt_content",
    "resolve_agent_template_parts",
    "resolve_plugin_parts",
]
