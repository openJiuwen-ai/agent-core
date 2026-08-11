# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.qa_block.config import QABlockConfig
from openjiuwen.core.context_engine.qa_block.schema import (
    QA_BLOCK_CATALOG_END,
    QA_BLOCK_CATALOG_START,
    QABlockEntry,
    QABlockRegistry,
)

CATALOG_SECTION_NAME = "qa_block_catalog"
CATALOG_SECTION_PRIORITY = 78

CATALOG_USAGE_FOOTER_CN = (
    "[QA_BLOCK_CATALOG_USAGE]\n"
    "本目录为会话级 QA 块索引（L1 摘要）。与 Selector（框架自动 preload 相关块）不同：\n"
    "· 需查看某一历史 QA **块内**的概览、逐 message 目录或句柄时，调用 load_qa_index(qa_id)。\n"
    '· 目录中出现 [[OFFLOAD: handle=…]] 时，用 reload_original_context_messages(handle, "filesystem") 取回该 round 原文。\n'
    "· 勿用 read_file 直接读 session_memory/qa_*.md 代替上述工具。\n"
    "[END_QA_BLOCK_CATALOG_USAGE]"
)

CATALOG_USAGE_FOOTER_EN = (
    "[QA_BLOCK_CATALOG_USAGE]\n"
    "This is the session-level QA block index (L1 summaries). Unlike Selector (framework preload), "
    "use load_qa_index(qa_id) to expand a block's overview + per-message catalog and handles.\n"
    'For [[OFFLOAD: handle=…]] markers, call reload_original_context_messages(handle, "filesystem").\n'
    "Do not use read_file on session_memory/qa_*.md instead of these tools.\n"
    "[END_QA_BLOCK_CATALOG_USAGE]"
)

_CHARS_PER_TOKEN_ESTIMATE = 4


def _sorted_history_entries(registry: QABlockRegistry) -> list[QABlockEntry]:
    return sorted(
        (entry for entry in registry.blocks.values() if entry.is_history),
        key=lambda item: item.qa_index,
    )


def resolve_catalog_l1_text(entry: QABlockEntry) -> str:
    """Catalog/Selector display text: real L1, or excerpt fallback when L1 is not ready."""
    l1 = (entry.l1_text or "").strip()
    if l1:
        return l1

    query_excerpt = (entry.user_query_excerpt or "").strip()
    answer_excerpt = (entry.final_answer_excerpt or "").strip()
    if query_excerpt and answer_excerpt:
        return f"Q: {query_excerpt}\nA: {answer_excerpt}"
    if query_excerpt:
        return f"Q: {query_excerpt}"
    if answer_excerpt:
        return f"A: {answer_excerpt}"
    return "(no summary)"


def build_catalog_text(
    registry: QABlockRegistry,
    *,
    max_tokens: int | None = None,
) -> str:
    """Build protected [QA_BLOCK_CATALOG] body from registry L1 rows.

    When *max_tokens* is set, apply LRU eviction: include the most recent
    history entries first (newest → oldest) until the token budget is
    exhausted.  Evicted entries remain in the registry and are still
    selectable by the selector via explicit qa_id references.
    """
    entries = _sorted_history_entries(registry)
    lines = [QA_BLOCK_CATALOG_START]
    evicted = 0

    if max_tokens is not None and entries:
        max_chars = max_tokens * _CHARS_PER_TOKEN_ESTIMATE
        reserved = len(QA_BLOCK_CATALOG_START) + len(QA_BLOCK_CATALOG_END) + 4
        budget = max(0, max_chars - reserved)

        included: list[str] = []
        total = 0
        for entry in reversed(entries):
            handle = entry.l0_store.handle if entry.l0_store else entry.qa_id
            l1 = resolve_catalog_l1_text(entry)
            line = f"- {entry.qa_id} (handle={handle}): {l1}"
            cost = len(line) + 1
            if total + cost > budget and included:
                break
            included.append(line)
            total += cost

        evicted = len(entries) - len(included)
        lines.extend(reversed(included))
        if evicted > 0:
            lines.append(
                f"- (... {evicted} earlier QA blocks evicted from catalog; "
                f"use load_qa_index(qa_id) to explore)"
            )
    else:
        for entry in entries:
            handle = entry.l0_store.handle if entry.l0_store else entry.qa_id
            l1 = resolve_catalog_l1_text(entry)
            lines.append(f"- {entry.qa_id} (handle={handle}): {l1}")

    lines.append(QA_BLOCK_CATALOG_END)
    text = "\n".join(lines)
    logger.info(
        "[QABlockCatalog] built session_id=%s history_blocks=%s included=%s evicted=%s chars=%s",
        registry.session_id,
        len(entries),
        len(entries) - evicted,
        evicted,
        len(text),
    )
    return text


def append_catalog_usage_footer(catalog_text: str, registry: QABlockRegistry, *, lang: str = "cn") -> str:
    """Append block-internal index tool hints when history QA blocks exist."""
    if not _sorted_history_entries(registry):
        return catalog_text
    footer = CATALOG_USAGE_FOOTER_CN if (lang or "cn").lower().startswith("cn") else CATALOG_USAGE_FOOTER_EN
    return f"{catalog_text}\n\n{footer}"


def build_catalog_section(
    registry: QABlockRegistry,
    *,
    lang: str = "cn",
    catalog_text: str | None = None,
    max_tokens: int | None = None,
):
    from openjiuwen.core.single_agent.prompts.builder import PromptSection

    body = (
        catalog_text
        if catalog_text is not None
        else build_catalog_text(registry, max_tokens=max_tokens)
    )
    catalog_text = append_catalog_usage_footer(body, registry, lang=lang)
    return PromptSection(
        name=CATALOG_SECTION_NAME,
        content={lang: catalog_text},
        priority=CATALOG_SECTION_PRIORITY,
    )


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN_ESTIMATE)


def maybe_compact_catalog_l1(
    registry: QABlockRegistry,
    config: QABlockConfig | None = None,
) -> QABlockRegistry:
    """Deprecated no-op: LRU eviction is now handled in build_catalog_text(max_tokens=...).

    The old strategy truncated ALL L1 texts to ``catalog_short_max_chars`` when the
    catalog exceeded ``catalog_max_tokens``.  This was replaced by LRU eviction
    (newest-first inclusion) in ``build_catalog_text(max_tokens=...)``, which is
    called with ``config.catalog_max_tokens`` by the assembly rail.

    This function is kept as a no-op for backward compatibility with any external
    callers.  ``catalog_short_max_chars`` is now a dead config field (kept for
    backward config file compatibility).

    Returns registry unchanged.
    """
    return registry
