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
from openjiuwen.harness.resources.template_card import (
    AgentTemplateCard,
    load_agent_template_card,
    parse_agent_template_card,
    read_agent_template_card,
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
    AgentTemplateSpec,
    McpDirSpec,
    McpServerSpec,
    MemorySpec,
    PluginSpec,
    PromptSectionSpec,
    RubricSpec,
    SkillSpec,
)

__all__ = [
    "AgentTemplateCard",
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
    "SkillSpec",
    "find_agent_template_manifest",
    "find_plugin_manifest",
    "load_agent_template_card",
    "load_agent_template_package",
    "load_plugin_package",
    "normalize_package_mcps",
    "parse_agent_template_card",
    "read_agent_template_card",
    "resolve_agent_template_parts",
    "resolve_plugin_parts",
]
