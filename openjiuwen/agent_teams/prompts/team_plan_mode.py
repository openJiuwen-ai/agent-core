# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Team.plan leader prompt-section builders.

The generic plan-mode prompt belongs to ``openjiuwen.harness``. This module
owns only the team.plan overlay used by TeamAgent rails.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openjiuwen.agent_teams.prompts.loader import load_template
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.harness.prompts import resolve_language
from openjiuwen.harness.prompts.sections import SectionName

if TYPE_CHECKING:
    from openjiuwen.core.session.agent import Session
    from openjiuwen.harness.deep_agent import DeepAgent


def build_team_plan_mode_prompt_template(language: str) -> str:
    """Load the team.plan prompt template from ``prompts/<lang>``."""
    resolved_language = resolve_language(language)
    return str(load_template("team_plan_mode", resolved_language).content).strip()


TEAM_PLAN_MODE_PROMPT_CN = build_team_plan_mode_prompt_template("cn")
TEAM_PLAN_MODE_PROMPT_EN = build_team_plan_mode_prompt_template("en")


def get_team_plan_mode_prompt(language: str) -> str:
    """Return the team.plan leader prompt template."""
    resolved_language = resolve_language(language)
    return TEAM_PLAN_MODE_PROMPT_EN if resolved_language == "en" else TEAM_PLAN_MODE_PROMPT_CN


def _build_enter_plan_mode_status(
    agent: "DeepAgent",
    session: "Session",
    language: str,
) -> str:
    plan_path = agent.get_plan_file_path(session)
    messages = {
        ("en", True): "enter_plan_mode has been called. Proceed with the workflow.",
        ("en", False): "You have NOT called enter_plan_mode yet. Call it NOW as your first action.",
        ("cn", True): "enter_plan_mode 已调用完成。请继续工作流。",
        ("cn", False): "你尚未调用 enter_plan_mode。请立即调用它作为你的第一个操作。",
    }
    lang = "en" if resolve_language(language) == "en" else "cn"
    fallback = "你尚未调用 enter_plan_mode。请立即调用它作为你的第一个操作。"
    return messages.get((lang, bool(plan_path)), fallback)


def _build_plan_file_info(
    agent: "DeepAgent",
    session: "Session",
    language: str,
) -> str:
    plan_path = agent.get_plan_file_path(session)
    lang = "en" if resolve_language(language) == "en" else "cn"
    if not plan_path:
        no_plan_messages = {
            "en": "No plan file yet. Call enter_plan_mode first to create one.",
            "cn": "暂无 plan 文件。请先调用 enter_plan_mode 创建。",
        }
        return no_plan_messages.get(lang, "暂无 plan 文件。请先调用 enter_plan_mode 创建。")

    path_str = str(plan_path)
    templates = {
        ("en", True): (
            "A plan file already exists at {path}. You can read it and make incremental edits using the edit_file tool."
        ),
        ("en", False): ("No plan file exists yet. You should create your plan at {path} using the write_file tool."),
        ("cn", True): "计划文件已存在于 {path}。你可以使用 edit_file 工具读取并增量编辑它。",
        ("cn", False): "计划文件尚不存在。你应该使用 write_file 工具在 {path} 创建计划。",
    }
    fallback = "计划文件尚不存在。你应该使用 write_file 工具在 {path} 创建计划。"
    return templates.get((lang, plan_path.exists()), fallback).format(path=path_str)


def build_team_plan_mode_prompt(
    language: str,
    *,
    enter_plan_mode_status: str,
    plan_file_info: str,
) -> str:
    """Render the team.plan prompt from the markdown template."""
    return get_team_plan_mode_prompt(language).format(
        enter_plan_mode_status=enter_plan_mode_status,
        plan_file_info=plan_file_info,
    )


def build_team_plan_mode_section(
    *,
    language: str,
    agent: "DeepAgent",
    session: "Session",
) -> PromptSection:
    """Build the team.plan MODE_INSTRUCTIONS section for a Leader."""
    resolved_language = resolve_language(language)
    content = build_team_plan_mode_prompt(
        resolved_language,
        enter_plan_mode_status=_build_enter_plan_mode_status(agent, session, resolved_language),
        plan_file_info=_build_plan_file_info(agent, session, resolved_language),
    )
    return PromptSection(
        name=SectionName.MODE_INSTRUCTIONS,
        content={resolved_language: content},
        priority=85,
    )


__all__ = [
    "TEAM_PLAN_MODE_PROMPT_CN",
    "TEAM_PLAN_MODE_PROMPT_EN",
    "build_team_plan_mode_prompt",
    "build_team_plan_mode_prompt_template",
    "build_team_plan_mode_section",
    "get_team_plan_mode_prompt",
]
