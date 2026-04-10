# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Skill prompt section for DeepAgent (used by SkillUseRail)."""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from openjiuwen.harness.prompts.sections import SectionName

# ---------------------------------------------------------------------------
# List-skill system prompt (bilingual)
# ---------------------------------------------------------------------------
SKILL_RAIL_LIST_SKILL_SYSTEM_PROMPT_CN = """
你是一个技能选择器。
你的任务是为给定的用户任务选择最相关的技能。
仅返回一个 JSON 对象。
输出格式：
{
  "skills": ["skill_name_1", "skill_name_2"]
}
"""

SKILL_RAIL_LIST_SKILL_SYSTEM_PROMPT_EN = """
You are a list_skill selector.
Your task is to select the most relevant skills for the given user task.
Return a JSON object only.
Output format:
{
  "skills": ["skill_name_1", "skill_name_2"]
}
"""

SKILL_RAIL_LIST_SKILL_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": SKILL_RAIL_LIST_SKILL_SYSTEM_PROMPT_CN,
    "en": SKILL_RAIL_LIST_SKILL_SYSTEM_PROMPT_EN,
}

# ---------------------------------------------------------------------------
# All-mode header / instruction (bilingual)
# ---------------------------------------------------------------------------
SKILL_RAIL_ALL_MODE_HEADER_CN = (
    "# 技能\n\n"
    "执行前先用 read_file 阅读相关 SKILL.md。\n\n"
    "可用技能：\n"
)

SKILL_RAIL_ALL_MODE_HEADER_EN = (
    "# Skills\n\n"
    "Read the relevant SKILL.md using read_file before execution.\n\n"
    "Available skills:\n"
)

SKILL_RAIL_ALL_MODE_HEADER: Dict[str, str] = {
    "cn": SKILL_RAIL_ALL_MODE_HEADER_CN,
    "en": SKILL_RAIL_ALL_MODE_HEADER_EN,
}

SKILL_RAIL_ALL_MODE_INSTRUCTION_CN = (
    "\n选择最相关的技能，先阅读其 SKILL.md 再执行。"
)

SKILL_RAIL_ALL_MODE_INSTRUCTION_EN = (
    "\nSelect the most relevant skill by reading its SKILL.md first."
)

SKILL_RAIL_ALL_MODE_INSTRUCTION: Dict[str, str] = {
    "cn": SKILL_RAIL_ALL_MODE_INSTRUCTION_CN,
    "en": SKILL_RAIL_ALL_MODE_INSTRUCTION_EN,
}

# ---------------------------------------------------------------------------
# Auto-list mode prompt (bilingual)
# ---------------------------------------------------------------------------
SKILL_RAIL_AUTO_LIST_MODE_PROMPT_CN = """# 技能

需要时先调用 list_skill 查看可用技能，再用 read_file 读取相关 SKILL.md 后执行。
需要时使用 code 执行 Python 或 JavaScript，使用 bash 执行 shell 命令。
"""

SKILL_RAIL_AUTO_LIST_MODE_PROMPT_EN = """# Skills

When needed, call list_skill first to see available skills, then read the relevant SKILL.md with read_file before execution.
Use code for Python or JavaScript snippets when needed, and use bash for shell commands.
"""

SKILL_RAIL_AUTO_LIST_MODE_PROMPT: Dict[str, str] = {
    "cn": SKILL_RAIL_AUTO_LIST_MODE_PROMPT_CN,
    "en": SKILL_RAIL_AUTO_LIST_MODE_PROMPT_EN,
}

# ---------------------------------------------------------------------------
# No-skill fallback prompt (bilingual)
# ---------------------------------------------------------------------------
SKILL_RAIL_NO_SKILL_PROMPT_CN = """# 技能

当前任务没有选择任何技能。如有技能信息可用，请用 read_file 阅读相关 SKILL.md。
"""

SKILL_RAIL_NO_SKILL_PROMPT_EN = """# Skills

No skill was selected for this task. When skill information is available, read the relevant SKILL.md using read_file.
"""

SKILL_RAIL_NO_SKILL_PROMPT: Dict[str, str] = {
    "cn": SKILL_RAIL_NO_SKILL_PROMPT_CN,
    "en": SKILL_RAIL_NO_SKILL_PROMPT_EN,
}


# ---------------------------------------------------------------------------
# Helper functions (same signatures, now accept language)
# ---------------------------------------------------------------------------
def build_skill_line(
    *,
    index: int,
    skill_name: str,
    description: str,
    skill_md_path: str,
) -> str:
    """Build one rendered skill line."""
    return (
        f"{index}. {skill_name}: {description}\n"
        f"   Path: {skill_md_path}"
    )


def build_skill_lines(lines: Iterable[str]) -> str:
    """Join rendered skill lines."""
    items: List[str] = [line for line in lines if line]
    return "\n\n".join(items)


def build_all_mode_skill_prompt(skill_lines: str, language: str = "cn") -> str:
    """Build prompt for all mode."""
    text = (skill_lines or "").strip()
    if not text:
        return SKILL_RAIL_NO_SKILL_PROMPT.get(language, SKILL_RAIL_NO_SKILL_PROMPT_CN)
    header = SKILL_RAIL_ALL_MODE_HEADER.get(language, SKILL_RAIL_ALL_MODE_HEADER_CN)
    instruction = SKILL_RAIL_ALL_MODE_INSTRUCTION.get(language, SKILL_RAIL_ALL_MODE_INSTRUCTION_CN)
    return header + text + instruction


def build_auto_list_mode_skill_prompt(language: str = "cn") -> str:
    """Build prompt for auto_list mode."""
    return SKILL_RAIL_AUTO_LIST_MODE_PROMPT.get(language, SKILL_RAIL_AUTO_LIST_MODE_PROMPT_CN)


def get_list_skill_system_prompt(language: str = "cn") -> str:
    """Get the list_skill system prompt for the given language."""
    return SKILL_RAIL_LIST_SKILL_SYSTEM_PROMPT.get(language, SKILL_RAIL_LIST_SKILL_SYSTEM_PROMPT_CN)


def build_skills_section(
    skill_lines: str,
    language: str = "cn",
    mode: str = "all",
) -> Optional["PromptSection"]:
    """Build a PromptSection for skills.

    Args:
        skill_lines: Pre-rendered skill lines (only used in 'all' mode).
        language: 'cn' or 'en'.
        mode: 'all' or 'auto_list'.

    Returns:
        A PromptSection instance, or None if mode is unrecognised.
    """
    from openjiuwen.harness.prompts.builder import PromptSection

    if mode == "all":
        content = build_all_mode_skill_prompt(skill_lines, language)
    elif mode == "auto_list":
        content = build_auto_list_mode_skill_prompt(language)
    else:
        return None

    return PromptSection(
        name=SectionName.SKILLS,
        content={language: content},
        priority=40,
    )
