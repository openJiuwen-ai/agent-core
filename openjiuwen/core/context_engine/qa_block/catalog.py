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


def build_catalog_text(registry: QABlockRegistry) -> str:
    """Build protected [QA_BLOCK_CATALOG] body from registry L1 rows."""
    lines = [QA_BLOCK_CATALOG_START]
    for entry in _sorted_history_entries(registry):
        handle = entry.l0_store.handle if entry.l0_store else entry.qa_id
        l1 = resolve_catalog_l1_text(entry)
        lines.append(f"- {entry.qa_id} (handle={handle}): {l1}")
    lines.append(QA_BLOCK_CATALOG_END)
    text = "\n".join(lines)
    logger.info(
        "[QABlockCatalog] built session_id=%s history_blocks=%s chars=%s",
        registry.session_id,
        len(_sorted_history_entries(registry)),
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
):
    from openjiuwen.core.single_agent.prompts.builder import PromptSection

    body = catalog_text if catalog_text is not None else build_catalog_text(registry)
    catalog_text = append_catalog_usage_footer(body, registry, lang=lang)
    return PromptSection(
        name=CATALOG_SECTION_NAME,
        content={lang: catalog_text},
        priority=CATALOG_SECTION_PRIORITY,
    )


_CHARS_PER_TOKEN_ESTIMATE = 4


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN_ESTIMATE)


def maybe_compact_catalog_l1(
    registry: QABlockRegistry,
    config: QABlockConfig | None = None,
) -> QABlockRegistry:
    """Shorten l1_text tiers when catalog exceeds budget (§2.4)."""
    cfg = config or QABlockConfig()
    catalog_text = build_catalog_text(registry)
    if _estimate_tokens(catalog_text) <= cfg.catalog_max_tokens:
        return registry

    updated_blocks = dict(registry.blocks)
    changed = False

    for qa_id, entry in registry.blocks.items():
        if not entry.is_history:
            continue
        if entry.l1_catalog_tier != "full":
            continue
        l1 = entry.l1_text or ""
        if len(l1) <= cfg.catalog_short_max_chars:
            continue
        new_entry = entry.model_copy(
            update={
                "l1_text": l1[: cfg.catalog_short_max_chars].rstrip() + "…",
                "l1_catalog_tier": "short",
            }
        )
        updated_blocks[qa_id] = new_entry
        changed = True

    if not changed:
        return registry

    compacted = registry.model_copy(update={"blocks": updated_blocks})
    after_text = build_catalog_text(compacted)
    logger.info(
        "[QABlockCatalogCompactor] compacted session_id=%s tokens_before=%s tokens_after=%s",
        registry.session_id,
        _estimate_tokens(catalog_text),
        _estimate_tokens(after_text),
    )
    return compacted
