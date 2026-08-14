# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Prompt sections for ProgressiveToolRail."""

from __future__ import annotations

from typing import Dict, Iterable, List

from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName


PROGRESSIVE_TOOL_NAVIGATION_HEADER: Dict[str, str] = {
    "cn": (
        "## 工具导航\n"
        "下面的内容用于说明当前 session 中的工具。延迟工具需要先通过 `tool_search` 找到；"
        "搜索结果会包含完整参数 schema，下一轮可以直接调用。\n"
    ),
    "en": (
        "## Tool Navigation\n"
        "The entries below describe tools available in the current session. Deferred "
        "tools become callable after `tool_search` returns them, with their complete "
        "parameter schemas.\n"
    ),
}

PROGRESSIVE_TOOL_NAVIGATION_EMPTY: Dict[str, str] = {
    "cn": "- （当前没有可展示的导航条目）",
    "en": "- (no navigation entries available)",
}

PROGRESSIVE_TOOL_RULES_HEADER: Dict[str, str] = {
    "cn": "## 渐进式工具使用规则\n",
    "en": "## Progressive Tool Usage Rules\n",
}

PROGRESSIVE_TOOL_RULES_BODY: Dict[str, str] = {
    "cn": (
        "你正在一个渐进式工具环境中工作。\n"
        "1. 先判断用户要完成的实际操作；只有 direct 工具明确支持该操作时才能直接调用，"
        "不要用通用工具替代专用能力，不能用读文件、写文件、记忆或 todo 工具替代其他专用能力。\n"
        "2. 如果没有匹配的 direct 工具，必须根据用户意图调用 `tool_search`，不能猜测 deferred 工具名称。\n"
        "3. 信息不足时可以先调用 `ask_user`；信息完整后必须先搜索，搜索结果会包含完整 schema，下一轮直接调用结果工具。\n"
        "4. 只有真实目标工具返回成功，才能向用户声称操作已完成。\n"
    ),
    "en": (
        "You are operating in a progressive tool environment.\n"
        "1. First identify the actual operation the user needs. "
        "Call a direct tool only when its description explicitly supports that operation; "
        "do not use generic tools as substitutes for specialized capabilities; "
        "do not read file, substitute file, memory, or todo tools for another specialized capability.\n"
        "2. If no direct tool matches, call `tool_search` based on the user's "
        "intent; do not guess a deferred tool name.\n"
        "3. If information is missing, you may call `ask_user` first. Once the information is complete, search first; "
        "the result includes the complete schema, and you must call the result tool directly in the next turn.\n"
        "4. Claim success only after the actual target tool returns success.\n"
    ),
}


def build_progressive_tool_rules_prompt(language: str = "cn") -> str:
    lang = language if language in PROGRESSIVE_TOOL_RULES_HEADER else "cn"
    return PROGRESSIVE_TOOL_RULES_HEADER[lang] + PROGRESSIVE_TOOL_RULES_BODY[lang]


def build_navigation_prompt(
    entries: Iterable[str],
    language: str = "cn",
) -> str:
    lang = language if language in PROGRESSIVE_TOOL_NAVIGATION_HEADER else "cn"
    items: List[str] = [item for item in entries if item]
    header = PROGRESSIVE_TOOL_NAVIGATION_HEADER[lang]
    if not items:
        return header + "\n" + PROGRESSIVE_TOOL_NAVIGATION_EMPTY[lang]
    return header + "\n" + "\n".join(items)


def build_navigation_section(
    entries: Iterable[str],
    language: str = "cn",
) -> "PromptSection":
    return PromptSection(
        name=SectionName.TOOL_NAVIGATION,
        content={language: build_navigation_prompt(entries, language)},
        priority=70,
    )


def build_progressive_tool_rules_section(
    language: str = "cn",
) -> "PromptSection":
    return PromptSection(
        name=SectionName.PROGRESSIVE_TOOL_RULES,
        content={language: build_progressive_tool_rules_prompt(language)},
        priority=75,
    )


def build_navigation_entry(
    *,
    name: str,
    group: str,
    status: str,
    summary: str,
    language: str = "cn",
) -> str:
    if language == "en":
        return f"- {name} [{group}, {status}]: {summary}"
    return f"- {name} [{group}, {status}]：{summary}"


def build_multilingual_navigation_section(
    entries_cn: Iterable[str],
    entries_en: Iterable[str],
) -> "PromptSection":
    return PromptSection(
        name=SectionName.TOOL_NAVIGATION,
        content={
            "cn": build_navigation_prompt(entries_cn, "cn"),
            "en": build_navigation_prompt(entries_en, "en"),
        },
        priority=70,
    )


def build_multilingual_progressive_tool_rules_section() -> "PromptSection":
    return PromptSection(
        name=SectionName.PROGRESSIVE_TOOL_RULES,
        content={
            "cn": build_progressive_tool_rules_prompt("cn"),
            "en": build_progressive_tool_rules_prompt("en"),
        },
        priority=75,
    )
