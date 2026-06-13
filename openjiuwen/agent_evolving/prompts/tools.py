# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Metadata providers for canonical agent-facing evolution tools."""

from __future__ import annotations

import uuid
from typing import Any, Dict

from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.agent_evolving.protocols import EVOLUTION_TARGET_VALUES, SIMPLIFY_ACTION_VALUES, VALID_SECTIONS
from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider

_SECTION_VALUES = sorted(VALID_SECTIONS)
_TARGET_VALUES = list(EVOLUTION_TARGET_VALUES)
_SIMPLIFY_ACTION_VALUES = list(SIMPLIFY_ACTION_VALUES)


def _text(language: str, *, cn: str, en: str) -> str:
    return en if language == "en" else cn


def _values(values: list[str]) -> str:
    return ", ".join(values)


_SUPPORTED_KIND_ENUM = ["skill", "swarm-skill"]


def _subject_schema(language: str) -> dict[str, Any]:
    kind_description = _text(
        language,
        cn="演进对象类型。可选值：skill（常规技能）、swarm-skill（多智能体团队技能）。",
        en="Evolution subject kind. Allowed values: skill, swarm-skill.",
    )
    name_description = _text(language, cn="演进对象名称。", en="Evolution subject name.")
    return {
        "type": "object",
        "description": _text(language, cn="演进对象封装。", en="Evolution subject envelope."),
        "properties": {
            "kind": {
                "type": "string",
                "enum": list(_SUPPORTED_KIND_ENUM),
                "description": kind_description,
            },
            "name": {
                "type": "string",
                "description": name_description,
            },
            "scope": {
                "type": "object",
                "description": _text(
                    language,
                    cn="预留给未来角色或成员作用域。",
                    en="Reserved for future role/member scope.",
                ),
            },
        },
        "required": ["kind", "name"],
    }


def build_evolution_subject_schema(language: str = "cn") -> dict[str, Any]:
    """Build the subject schema for evolution subjects."""
    return _subject_schema(language)


class PrepareSkillEvolutionReviewMetadataProvider(ToolMetadataProvider):
    """Metadata for preparing a restricted evolution review scope."""

    def get_name(self) -> str:
        return "prepare_skill_evolution"

    def get_description(self, language: str = "cn") -> str:
        if language == "en":
            return (
                "Create a restricted Skill evolution review ref before drafting new experiences. "
                "Use this after the user agrees to evolve a skill from ambiguous feedback."
            )
        return "在起草新经验前创建受限技能演进 review ref；用于用户同意将模糊反馈沉淀为 skill 演进后。"

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subject": _subject_schema(language),
                "user_intent": {
                    "type": "string",
                    "description": _text(
                        language,
                        cn="用户已确认的演进意图或反馈摘要。",
                        en="User-approved evolution intent or feedback summary.",
                    ),
                },
                "user_confirmed": {
                    "type": "boolean",
                    "description": _text(
                        language,
                        cn="用户明确同意演进该 skill 后必须为 true。",
                        en="Must be true after the user explicitly agrees to evolve this skill.",
                    ),
                },
            },
            "required": ["subject", "user_confirmed"],
        }


class EvolveReviewTaskMetadataProvider(ToolMetadataProvider):
    """Metadata for launching the restricted evolution review subagent."""

    def get_name(self) -> str:
        return "evolve_review_task"

    def get_description(self, language: str = "cn") -> str:
        if language == "en":
            return "Launch the restricted evolution_reviewer subagent for a prepared evolution_review_ref."
        return "为已准备的 evolution_review_ref 启动受限 evolution_reviewer subagent。"

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "evolution_review_ref": {
                    "type": "string",
                    "description": _text(
                        language,
                        cn="prepare_skill_evolution 返回的 evolution_review_ref。",
                        en="Evolution review ref returned by prepare_skill_evolution.",
                    ),
                },
                "user_intent": {
                    "type": "string",
                    "description": _text(
                        language,
                        cn="可选；用户已确认的演进意图或反馈摘要。",
                        en="Optional user-approved evolution intent or feedback summary.",
                    ),
                },
                "subject": _subject_schema(language),
            },
            "required": ["evolution_review_ref"],
        }


class ListSkillExperiencesMetadataProvider(ToolMetadataProvider):
    """Metadata for listing bounded experience index entries."""

    def get_name(self) -> str:
        return "list_skill_experiences"

    def get_description(self, language: str = "cn") -> str:
        if language == "en":
            return (
                "Query persisted evolution experience index entries for focused lookup or overflow coverage. "
                "Returns metadata only, not full content."
            )
        return "查询已持久化的演进经验索引，用于聚焦查找或覆盖溢出索引；只返回元数据，不返回全文。"

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subject": _subject_schema(language),
                "min_score": {
                    "type": "number",
                    "description": _text(language, cn="最低分数过滤。", en="Minimum score filter."),
                },
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "description": _text(language, cn="最多返回的索引条目数。", en="Maximum number of index items."),
                },
                "cursor": {
                    "type": "string",
                    "description": _text(
                        language,
                        cn="上一页溢出响应返回的游标。",
                        en="Cursor returned by a previous overflow response.",
                    ),
                },
                "target": {
                    "type": "string",
                    "enum": _TARGET_VALUES,
                    "description": _text(
                        language,
                        cn=f"结构化 target 过滤；可选值：{_values(_TARGET_VALUES)}。",
                        en=f"Structured target filter. Allowed values: {_values(_TARGET_VALUES)}.",
                    ),
                },
                "section": {
                    "type": "string",
                    "enum": _SECTION_VALUES,
                    "description": _text(
                        language,
                        cn=f"结构化 section 过滤；可选值：{_values(_SECTION_VALUES)}。",
                        en=f"Structured section filter. Allowed values: {_values(_SECTION_VALUES)}.",
                    ),
                },
                "query": {
                    "type": "string",
                    "description": _text(
                        language,
                        cn="仅在索引字段上匹配的字面量 OR 查询；多个词用 | 分隔，不是自然语言查询。",
                        en="Literal OR terms separated by | over index fields only; not natural language.",
                    ),
                },
                "sort": {
                    "type": "string",
                    "enum": ["score_desc", "updated_desc"],
                    "default": "score_desc",
                    "description": _text(language, cn="索引排序方式。", en="Index sort order."),
                },
            },
            "required": ["subject"],
        }


class ReadSkillExperiencesMetadataProvider(ToolMetadataProvider):
    """Metadata for reading selected experience records."""

    def get_name(self) -> str:
        return "read_skill_experiences"

    def get_description(self, language: str = "cn") -> str:
        if language == "en":
            return "Read full content for selected evolution experience records."
        return "读取指定演进经验记录的全文内容。"

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subject": _subject_schema(language),
                "record_ids": {
                    "type": "array",
                    "description": _text(language, cn="要读取的经验记录 ID。", en="Experience record IDs to read."),
                    "items": {"type": "string"},
                },
                "max_content_chars": {
                    "type": "integer",
                    "default": 2000,
                    "description": _text(
                        language,
                        cn="每条记录最多返回的内容字符数。",
                        en="Maximum content characters per record.",
                    ),
                },
            },
            "required": ["subject", "record_ids"],
        }


class EvolveSkillExperiencesMetadataProvider(ToolMetadataProvider):
    """Metadata for accepting reviewed evolution proposals."""

    def get_name(self) -> str:
        return "evolve_skill_experiences"

    def get_description(self, language: str = "cn") -> str:
        if language == "en":
            return "Accept reviewed Skill evolution proposals from a completed evolution review."
        return "接受已完成 evolution review 中的已审查 Skill 演进 proposals。"

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subject": _subject_schema(language),
                "evolution_review_ref": {
                    "type": "string",
                    "description": _text(
                        language,
                        cn="包含已审查 proposals 的已完成 Evolution Review Ref。",
                        en="Completed Evolution Review Ref containing reviewed proposals.",
                    ),
                },
                "selected_proposal_ids": {
                    "type": "array",
                    "description": _text(
                        language,
                        cn="从已完成 review result 中选择要接受的 proposal_id 列表；不要复制或改写 proposal 正文。",
                        en=(
                            "Proposal ids selected from the completed review result; do not copy or rewrite "
                            "proposal content."
                        ),
                    ),
                    "items": {"type": "string"},
                },
                "selection_reason": {
                    "type": "string",
                    "description": _text(
                        language,
                        cn="可选；说明为什么接受这些 proposals，仅用于审计和审批摘要。",
                        en="Optional rationale for accepting these proposals, used for audit and approval summary.",
                    ),
                },
            },
            "required": ["subject", "evolution_review_ref", "selected_proposal_ids"],
        }


class SimplifySkillExperiencesMetadataProvider(ToolMetadataProvider):
    """Metadata for applying simplify actions."""

    def get_name(self) -> str:
        return "simplify_skill_experiences"

    def get_description(self, language: str = "cn") -> str:
        if language == "en":
            return "Apply delete, merge, or refine actions to existing evolution experiences."
        return "对已有演进经验执行删除、合并或改写动作。"

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subject": _subject_schema(language),
                "actions": {
                    "type": "array",
                    "description": _text(language, cn="要执行的精简动作。", en="Simplify actions to apply."),
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": _SIMPLIFY_ACTION_VALUES,
                                "description": _text(
                                    language,
                                    cn=f"动作类型；可选值：{_values(_SIMPLIFY_ACTION_VALUES)}。",
                                    en=f"Action type. Allowed values: {_values(_SIMPLIFY_ACTION_VALUES)}.",
                                ),
                            },
                            "record_id": {
                                "type": "string",
                                "description": _text(language, cn="主记录 ID。", en="Primary record ID."),
                            },
                            "merge_remove_ids": {
                                "type": "array",
                                "description": _text(
                                    language,
                                    cn="MERGE 时被移除的记录 ID。",
                                    en="Record IDs removed during MERGE.",
                                ),
                                "items": {"type": "string"},
                            },
                            "new_content": {
                                "type": "string",
                                "description": _text(
                                    language,
                                    cn="MERGE 或 REFINE 使用的替换内容。",
                                    en="Replacement content for MERGE or REFINE.",
                                ),
                            },
                            "reason": {
                                "type": "string",
                                "description": _text(language, cn="执行该动作的原因。", en="Reason for the action."),
                            },
                        },
                        "required": ["action", "record_id"],
                    },
                },
            },
            "required": ["subject", "actions"],
        }


_PROVIDERS: list[ToolMetadataProvider] = [
    PrepareSkillEvolutionReviewMetadataProvider(),
    EvolveReviewTaskMetadataProvider(),
    ListSkillExperiencesMetadataProvider(),
    ReadSkillExperiencesMetadataProvider(),
    EvolveSkillExperiencesMetadataProvider(),
    SimplifySkillExperiencesMetadataProvider(),
]
_REGISTRY: dict[str, ToolMetadataProvider] = {provider.get_name(): provider for provider in _PROVIDERS}


def get_evolution_tool_description(name: str, language: str = "cn") -> str:
    """Look up metadata for agent evolution tools."""
    provider = _REGISTRY.get(name)
    if provider is None:
        raise KeyError(f"Evolution tool '{name}' not registered. Available: {sorted(_REGISTRY.keys())}")
    return provider.get_description(language)


def get_evolution_tool_input_params(name: str, language: str = "cn") -> Dict[str, Any]:
    """Look up input schema for agent evolution tools."""
    provider = _REGISTRY.get(name)
    if provider is None:
        raise KeyError(f"Evolution tool '{name}' not registered. Available: {sorted(_REGISTRY.keys())}")
    return provider.get_input_params(language)


def build_evolution_tool_card(
    name: str,
    tool_id: str,
    language: str = "cn",
    *,
    agent_id: str | None = None,
) -> ToolCard:
    """Build a ToolCard from evolution-owned tool metadata."""
    final_tool_id = f"{tool_id}_{agent_id}" if agent_id else f"{tool_id}_{uuid.uuid4().hex}"
    return ToolCard(
        id=final_tool_id,
        name=name,
        description=get_evolution_tool_description(name, language),
        input_params=get_evolution_tool_input_params(name, language),
    )


__all__ = [
    "EvolveReviewTaskMetadataProvider",
    "EvolveSkillExperiencesMetadataProvider",
    "ListSkillExperiencesMetadataProvider",
    "PrepareSkillEvolutionReviewMetadataProvider",
    "ReadSkillExperiencesMetadataProvider",
    "SimplifySkillExperiencesMetadataProvider",
    "build_evolution_tool_card",
    "build_evolution_subject_schema",
    "get_evolution_tool_description",
    "get_evolution_tool_input_params",
]
