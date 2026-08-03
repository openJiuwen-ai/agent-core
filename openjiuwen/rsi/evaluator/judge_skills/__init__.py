# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Domain judge-skill discovery and prompt composition."""

from openjiuwen.rsi.evaluator.judge_skills.registry import (
    JudgeSkill,
    available_judge_skills,
    format_judge_skill_instructions,
    resolve_judge_skills,
    resolve_judge_skills_by_name,
    resolve_judge_skills_for_task,
)

__all__ = [
    "JudgeSkill",
    "available_judge_skills",
    "format_judge_skill_instructions",
    "resolve_judge_skills",
    "resolve_judge_skills_by_name",
    "resolve_judge_skills_for_task",
]
