# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Prompt sections for ProgressiveToolRail."""

from __future__ import annotations

from typing import Dict, Optional

from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName

_MAX_DEFERRED_TOOL_SUMMARY_CHARS = 120


PROGRESSIVE_TOOL_RULES_HEADER: Dict[str, str] = {
    "cn": "# 渐进式工具使用规则\n",
    "en": "# Progressive Tool Usage Rules\n",
}

PROGRESSIVE_TOOL_RULES_BODY: Dict[str, str] = {
    "cn": (
        "当前启用了渐进式工具加载。工具分为两类：\n"
        "- direct 工具：可以直接调用。\n"
        "- deferred 工具：已注册且可用，但需要先通过 `tool_search` 搜索；"
        "搜索结果会提供完整 schema，但结果工具不会加入顶层 `tools`；"
        "下一轮必须通过固定的 `tool_call` 执行。\n\n"
        "当前 session 启动时的 deferred 工具初始目录列在本节末尾。"
        "后续新增、修改和删除的工具不会改写这条稳定系统提示词，而是通过新的 system attachment 提供；"
        "如果多个 attachment 都涉及工具目录，应按顺序应用，并以最新内容为准。"
        "\n"
        "## 动态工具状态优先级\n"
        "- 当前 session 启动时的 deferred 工具目录只是初始快照，不代表永久可用。\n"
        "- `deferred 工具目录更新` attachment 表示本次请求的最新工具状态，即使它在消息序列中出现在当前 user 消息之后，也必须在回答前应用。\n"
        "- 最新目录覆盖初始目录、历史消息、历史 tool_search 结果、历史 toolResult，以及 task_tool 或子代理描述中的工具名称。\n"
        "- 工具被标记为删除后，当前不可用；不得声称仍支持，不得复用旧结果，也不得通过 `tool_call`、`task_tool` 或子代理间接调用。\n"
        "- 如果工具仍在当前注册表中且没有变化，可以继续复用历史结果；如果工具描述或 schema 已修改，应重新搜索获取最新结果。\n\n"
        "## tool_search 使用规则\n"
        "1. 先判断用户真正想完成的操作。\n"
        "2. 如果 direct 工具明确支持该操作，可以直接调用。\n"
        "3. 如果没有匹配的 direct 工具，必须调用 `tool_search`，不能猜测 deferred 工具名称。\n"
        "4. 搜索时使用用户意图和所需能力描述作为 `query`。\n"
        "5. 搜索结果包含工具的完整 `parameters` schema；结果工具不会加入顶层 `tools`，"
        "下一轮必须调用固定的 `tool_call`，并把搜索结果中的准确工具名称放入 `name`，"
        "把符合 schema 的参数放入 `args`。不要直接调用搜索结果工具名称。\n"
        "6. 不要使用 `bash` 或其他 direct 工具去执行搜索到的工具。\n"
        "7. 同一用户意图不要重复调用相同或相近的搜索；若返回结果不适用，不要猜测工具名称，"
        "必要时调用 `ask_user`，否则说明当前没有合适工具。\n"
    ),
    "en": (
        "Progressive tool loading is enabled. Tools are divided into two categories:\n"
        "- Direct tools: can be called directly.\n"
        "- Deferred tools: registered and available, but must first be found through `tool_search`. "
        "The search result provides the complete schema, but result tools are not added to the top-level `tools`; "
        "execute them through the fixed `tool_call` tool in the next turn.\n\n"
        "The initial deferred-tool directory for this session is listed at the end of this stable section. "
        "Later additions, updates, and removals do not rewrite this system prompt; they are delivered as new system "
        "attachments. Apply directory updates in order and use the latest content. "
        "\n"
        "## Dynamic Tool State Priority\n"
        "- The deferred-tool directory at session startup is only an initial snapshot; "
        "it does not mean those tools remain available forever.\n"
        "- A `deferred-tool directory update` attachment is the latest tool state for this request. "
        "Even if it appears after the current user message in the serialized message sequence, "
        "apply it before answering.\n"
        "- The latest directory overrides the initial directory, historical messages, "
        "historical tool_search results, historical toolResults, and tool names described "
        "by task_tool or subagents.\n"
        "- Once a tool is marked as removed, it is unavailable. Do not claim that it is "
        "supported, reuse its old result, or invoke it through `tool_call`, `task_tool`, "
        "or a subagent.\n"
        "- A historical result may be reused only while the tool remains registered and "
        "unchanged; if its description or schema changed, search again.\n\n"
        "## tool_search usage rules\n"
        "1. First identify the actual operation the user wants to complete.\n"
        "2. If a direct tool clearly supports the operation, call it directly.\n"
        "3. If no direct tool matches, you must call `tool_search`; do not guess deferred tool names.\n"
        "4. Use the user's intent and required capability as the `query`.\n"
        "5. `tool_search` uses BM25 to match tool names, descriptions, and parameter information.\n"
        "6. Search results contain the complete `parameters` schema. Result tools are not "
        "added to the top-level `tools`; in the next turn call the fixed `tool_call` "
        "with the exact result name in `name` and schema-compatible arguments in `args`. "
        "Do not call the result tool by its own name.\n"
        "7. Do not use `bash` or another direct tool to execute a search result.\n"
        "8. Do not repeat the same or a similar search for the same user intent. "
        "If the results are not suitable, "
        "do not guess a tool name; call `ask_user` when necessary, otherwise explain "
        "that no suitable tool is available.\n"
    ),
}


def _render_deferred_tool_descriptions(
    tool_descriptions: Optional[Dict[str, str]],
    language: str,
) -> str:
    """Render the runtime deferred-tool summary for the prompt section."""
    if not tool_descriptions:
        return (
            "（当前没有可通过 tool_search 搜索的 deferred 工具。）"
            if language == "cn"
            else "(No deferred tools are currently available through tool_search.)"
        )

    rendered: list[str] = []
    for name, description in sorted(tool_descriptions.items()):
        compact_description = _compact_tool_description(description)
        rendered.append(
            f"- **{name}**: {compact_description}"
            if compact_description
            else f"- **{name}**"
        )
    return "\n".join(rendered)


def _compact_tool_description(description: str) -> str:
    """Keep only a short capability summary in the system prompt."""
    compact = " ".join(str(description or "").split())
    if len(compact) <= _MAX_DEFERRED_TOOL_SUMMARY_CHARS:
        return compact

    sentence_endings = ("。", ".", "！", "!", "？", "?")
    first_ending = min(
        (
            index
            for index, char in enumerate(compact)
            if char in sentence_endings and index > 0
        ),
        default=-1,
    )
    if 0 < first_ending < _MAX_DEFERRED_TOOL_SUMMARY_CHARS:
        return compact[: first_ending + 1]

    return compact[: _MAX_DEFERRED_TOOL_SUMMARY_CHARS - 1].rstrip() + "…"


def build_progressive_tool_rules_prompt(
    language: str = "cn",
    deferred_tool_descriptions: Optional[Dict[str, str]] = None,
) -> str:
    """Build the stable rules and this session's initial deferred-tool catalog.

    The descriptions are captured once for the session.  Later catalog changes
    are rendered separately by ``ProgressiveToolRail`` as system attachments,
    so the stable prompt prefix is not rewritten.
    """

    lang = language if language in PROGRESSIVE_TOOL_RULES_HEADER else "cn"
    prompt = PROGRESSIVE_TOOL_RULES_HEADER[lang] + PROGRESSIVE_TOOL_RULES_BODY[lang]
    if lang == "cn":
        prompt += (
            "## 当前可通过 tool_search 搜索的 deferred 工具（session 初始目录）\n\n"
            f"{_render_deferred_tool_descriptions(deferred_tool_descriptions, lang)}"
        )
    else:
        prompt += (
            "## Deferred tools available through tool_search (initial session directory)\n\n"
            f"{_render_deferred_tool_descriptions(deferred_tool_descriptions, lang)}"
        )
    return prompt


def render_deferred_tool_catalog_snapshot(
    tool_descriptions: Optional[Dict[str, str]],
    *,
    language: str = "cn",
    version: int = 1,
) -> str:
    """Render a complete deferred-tool directory attachment."""

    lang = language if language in PROGRESSIVE_TOOL_RULES_HEADER else "cn"
    description_text = _render_deferred_tool_descriptions(tool_descriptions, lang)
    if lang == "cn":
        return (
            "## 当前可通过 tool_search 搜索的 deferred 工具\n\n"
            f"目录版本：{version}\n\n"
            f"{description_text}"
        )
    return (
        "## Deferred tools available through tool_search\n\n"
        f"Catalog version: {version}\n\n"
        f"{description_text}"
    )


def render_deferred_tool_catalog_delta(
    added: Optional[Dict[str, str]],
    updated: Optional[Dict[str, str]],
    removed: Optional[list[str]],
    *,
    language: str = "cn",
    version: int = 1,
) -> str:
    """Render an incremental deferred-tool directory update attachment."""

    lang = language if language in PROGRESSIVE_TOOL_RULES_HEADER else "cn"
    added = added or {}
    updated = updated or {}
    removed = sorted(removed or [])

    if lang == "cn":
        sections: list[str] = []
        if added:
            sections.append(
                "### 新增（可通过 tool_search 搜索）\n\n"
                f"{_render_deferred_tool_descriptions(added, lang)}"
            )
        if updated:
            sections.append(
                "### 修改（旧结果失效，需重新搜索）\n\n"
                f"{_render_deferred_tool_descriptions(updated, lang)}"
            )
        if removed:
            sections.append(
                "### 删除（当前不可用）\n\n"
                + "\n".join(f"- **{name}**" for name in removed)
            )
        body = "\n\n".join(sections) if sections else "本次没有变化。"
        return (
            "## deferred 工具目录更新（立即生效）\n\n"
            f"目录版本：{version}\n\n"
            "仅列出本次变化；未列出的工具保持不变。本更新覆盖旧目录和历史搜索结果。\n\n"
            f"{body}"
        )

    sections = []
    if added:
        sections.append(
            "### Added (searchable through tool_search)\n\n"
            f"{_render_deferred_tool_descriptions(added, lang)}"
        )
    if updated:
        sections.append(
            "### Updated (old results invalid; search again)\n\n"
            f"{_render_deferred_tool_descriptions(updated, lang)}"
        )
    if removed:
        sections.append(
            "### Removed (unavailable now)\n\n"
            + "\n".join(f"- **{name}**" for name in removed)
        )
    body = "\n\n".join(sections) if sections else "No changes."
    return (
        "## Deferred-tool directory update (effective immediately)\n\n"
        f"Catalog version: {version}\n\n"
        "Only changed tools are listed; omitted tools are unchanged. This update supersedes the "
        "old directory and historical search results.\n\n"
        f"{body}"
    )


def build_progressive_tool_rules_section(
    language: str = "cn",
    deferred_tool_descriptions: Optional[Dict[str, str]] = None,
) -> "PromptSection":
    return PromptSection(
        name=SectionName.PROGRESSIVE_TOOL_RULES,
        content={
            language: build_progressive_tool_rules_prompt(
                language,
                deferred_tool_descriptions,
            )
        },
        priority=75,
    )


def build_multilingual_progressive_tool_rules_section(
    deferred_tool_descriptions: Optional[Dict[str, str]] = None,
) -> "PromptSection":
    return PromptSection(
        name=SectionName.PROGRESSIVE_TOOL_RULES,
        content={
            "cn": build_progressive_tool_rules_prompt(
                "cn",
                deferred_tool_descriptions,
            ),
            "en": build_progressive_tool_rules_prompt(
                "en",
                deferred_tool_descriptions,
            ),
        },
        priority=75,
    )
