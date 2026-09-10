# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``ttse_consult``: one read-only tool for FACT/TIP catalog disclosure.

``category=<id>`` → that class's FACT + TIP. With ``query=`` the class is
ranked (BM25, or BM25+embedding hybrid) and clipped to ``top_k`` per track.
Comma-separated ids (or a list) open several related classes in one call,
capped so the bank is not dumped. No-arg still lists the catalog (compat /
missing attachment) but the live prompt already trails the listing. Paths
stay inside the tool; they are never returned or described.
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Sequence

from .catalog import render_catalog_markdown
from .categories import category_ids, normalize_category
from .render import build_section_text
from .retrieval import DEFAULT_RRF_K, DEFAULT_TOP_K, clamp_top_k, retrieve_rules

TTSE_CONSULT_TOOL_NAME = "ttse_consult"
MAX_CONSULT_CATEGORIES = 3

_CONSULT_DESCRIPTION = (
    "Load previously learned FACT and TIP rules for business-scenario categories. "
    "The category listing and counts are already in the trailing prompt attachment. "
    "Set category to one listed id, or several related ids separated by commas "
    f"(max {MAX_CONSULT_CATEGORIES}). "
    "Pass query as an experience-style retrieval sentence (When <situation>: use "
    "<capability> …, or an environment constraint) — not the raw user message. "
    "Optional top_k limits FACT and TIP hits per category (server default applies). "
    "Do not call with no arguments just to re-list the catalog. "
    "Open only categories relevant to the current task. "
    "Do not use bash or read_file to scan the experience bank."
)

_SPLIT_IDS = re.compile(r"[,;|\s]+")

_QUERY_NEEDS_CATEGORY = (
    "query requires category. Use an id from the trailing catalog attachment."
)


def _truncate(text: str, max_chars: int) -> str:
    body = text or ""
    if max_chars <= 0 or len(body) <= max_chars:
        return body
    return body[: max(0, max_chars - 20)].rstrip() + "\n… [truncated]\n"


def parse_consult_categories(category: Any) -> List[str]:
    """Split ``category`` into unique ids, preserving order.

    Accepts a string (one id, comma/semicolon/whitespace separated, or a JSON
    array) or a list/tuple of strings.
    """
    if category is None:
        return []
    if isinstance(category, (list, tuple)):
        parts = [str(x).strip().strip("`") for x in category]
        return _dedupe([p for p in parts if p])
    raw = str(category).strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            return parse_consult_categories(parsed)
    parts = [_p.strip().strip("`") for _p in _SPLIT_IDS.split(raw)]
    return _dedupe([p for p in parts if p])


def parse_consult_query(query: Any) -> str:
    if query is None:
        return ""
    return str(query).strip()


def _dedupe(ids: Sequence[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for cid in ids:
        if cid in seen:
            continue
        seen.add(cid)
        out.append(cid)
    return out


def _slice_category(store: Any, cid: str, max_rules: int):
    facts, tips = store.records_for_category(cid)
    if max_rules > 0:
        facts = list(facts)[:max_rules]
        tips = list(tips)[:max_rules]
    return facts, tips


def _format_category_body(facts, tips, *, cid: str, retrieved: bool) -> str:
    if not facts and not tips:
        return f"No FACT/TIP rules in category `{cid}`."
    return build_section_text(facts, tips, retrieved=retrieved)


def _maybe_mark_injected(store: Any, facts, tips) -> None:
    marker = getattr(store, "mark_injected", None)
    if not callable(marker):
        return
    marker([*(facts or []), *(tips or [])])


def _render_one_category(store: Any, cid: str, max_rules: int) -> str:
    facts, tips = _slice_category(store, cid, max_rules)
    return _format_category_body(facts, tips, cid=cid, retrieved=False)


async def _render_one_category_async(
    store: Any,
    cid: str,
    *,
    query: str,
    top_k: int,
    max_rules: int,
    rrf_k: int,
    mark_injected: bool,
) -> str:
    if query:
        result = await retrieve_rules(
            store,
            category=cid,
            query=query,
            top_k=top_k,
            rrf_k=rrf_k,
        )
        facts, tips = list(result.facts), list(result.tips)
        if max_rules > 0:
            facts = facts[:max_rules]
            tips = tips[:max_rules]
        retrieved = True
    else:
        facts, tips = _slice_category(store, cid, max_rules)
        retrieved = False
    if mark_injected:
        _maybe_mark_injected(store, facts, tips)
    return _format_category_body(facts, tips, cid=cid, retrieved=retrieved)


def _unknown_message(unknown: Sequence[str]) -> str:
    if len(unknown) == 1:
        return (
            f"Unknown category `{unknown[0]}`. "
            "Use an id from the trailing catalog attachment."
        )
    listed = ", ".join(f"`{u}`" for u in unknown)
    return (
        f"Unknown category {listed}. "
        "Use an id from the trailing catalog attachment."
    )


def _resolve_categories(category: Any) -> tuple[List[str], List[str], List[str]]:
    """Return (known_ids, unknown_raw, omitted_raw)."""
    requested = parse_consult_categories(category)
    omitted = requested[MAX_CONSULT_CATEGORIES:]
    requested = requested[:MAX_CONSULT_CATEGORIES]
    allowed = set(category_ids())
    known: List[str] = []
    unknown: List[str] = []
    for raw in requested:
        if raw not in allowed:
            unknown.append(raw)
            continue
        known.append(normalize_category(raw))
    return known, unknown, omitted


def _notes_prefix(unknown: Sequence[str], omitted: Sequence[str]) -> List[str]:
    notes: List[str] = []
    if unknown:
        notes.append(_unknown_message(unknown))
    if omitted:
        notes.append(
            f"Opened the first {MAX_CONSULT_CATEGORIES} categories; "
            "omitted: " + ", ".join(f"`{x}`" for x in omitted) + "."
        )
    return notes


def _join_blocks(notes: Sequence[str], bodies: Sequence[tuple[str, str]], *, max_chars: int) -> str:
    if len(bodies) == 1 and not notes:
        return _truncate(bodies[0][1], max_chars)
    blocks: List[str] = []
    if notes:
        blocks.append("\n".join(notes))
    for cid, body in bodies:
        blocks.append(f"## `{cid}`\n\n{body.rstrip()}")
    return _truncate("\n\n".join(blocks) + "\n", max_chars)


def render_consult_result(
    store: Any,
    *,
    category: Any = "",
    max_chars: int = 8000,
    max_rules: int = 40,
) -> str:
    """Render catalog or one/several categories from the in-memory bank.

    Dump-only (no query). Prefer :func:`render_consult_result_async` from the
    tool so ``query`` / ``top_k`` hybrid recall can run.
    """
    requested = parse_consult_categories(category)
    if not requested:
        counts = store.catalog_counts() if hasattr(store, "catalog_counts") else {}
        return _truncate(render_catalog_markdown(counts), max_chars)

    known, unknown, omitted = _resolve_categories(category)
    notes = _notes_prefix(unknown, omitted)
    if not known:
        return "\n".join(notes)

    bodies = [(cid, _render_one_category(store, cid, max_rules)) for cid in known]
    return _join_blocks(notes, bodies, max_chars=max_chars)


async def render_consult_result_async(
    store: Any,
    *,
    category: Any = "",
    query: Any = "",
    top_k: Any = None,
    max_chars: int = 8000,
    max_rules: int = 40,
    default_top_k: int = DEFAULT_TOP_K,
    rrf_k: int = DEFAULT_RRF_K,
    mark_injected: bool = True,
) -> str:
    """Catalog, whole-class dump, or in-category hybrid/BM25 recall."""
    query_text = parse_consult_query(query)
    requested = parse_consult_categories(category)
    if query_text and not requested:
        return _QUERY_NEEDS_CATEGORY
    if not requested:
        counts = store.catalog_counts() if hasattr(store, "catalog_counts") else {}
        return _truncate(render_catalog_markdown(counts), max_chars)

    known, unknown, omitted = _resolve_categories(category)
    notes = _notes_prefix(unknown, omitted)
    if not known:
        return "\n".join(notes)

    limit = clamp_top_k(top_k, default=default_top_k, max_rules=max_rules)
    bodies: List[tuple[str, str]] = []
    for cid in known:
        body = await _render_one_category_async(
            store,
            cid,
            query=query_text,
            top_k=limit,
            max_rules=max_rules,
            rrf_k=rrf_k,
            mark_injected=mark_injected,
        )
        bodies.append((cid, body))
    return _join_blocks(notes, bodies, max_chars=max_chars)


def create_ttse_consult_tool(
    store: Any,
    *,
    max_chars: int = 8000,
    max_rules: int = 40,
    default_top_k: int = DEFAULT_TOP_K,
    rrf_k: int = DEFAULT_RRF_K,
) -> Any:
    """Build the rail-owned consult tool bound to ``store``."""
    from openjiuwen.core.foundation.tool import LocalFunction, ToolCard

    async def ttse_consult(
        category: Any = "",
        query: Any = "",
        top_k: Any = None,
    ) -> str:
        return await render_consult_result_async(
            store,
            category=category,
            query=query,
            top_k=top_k,
            max_chars=max_chars,
            max_rules=max_rules,
            default_top_k=default_top_k,
            rrf_k=rrf_k,
        )

    card = ToolCard(
        id=TTSE_CONSULT_TOOL_NAME,
        name=TTSE_CONSULT_TOOL_NAME,
        description=_CONSULT_DESCRIPTION,
        input_params={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": (
                        "One business-scenario id from the trailing catalog, or several "
                        f"related ids separated by commas (max {MAX_CONSULT_CATEGORIES}). "
                        "Required when query is set."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Experience-style retrieval sentence in FACT/TIP language "
                        "(When <situation>: use <capability> …, or an environment "
                        "constraint). Do not paste the raw user message. Omit to dump "
                        "the whole class (small classes / fallback)."
                    ),
                },
                "top_k": {
                    "type": "integer",
                    "description": (
                        "Max FACT hits and max TIP hits to return per category. "
                        f"Defaults to {default_top_k}; capped by the server."
                    ),
                },
            },
            "required": [],
        },
        parallel_safe=True,
        idempotent=True,
        stateless=False,
    )
    return LocalFunction(card=card, func=ttse_consult)


def create_ttse_consult_tools(
    store: Any,
    *,
    max_chars: int = 8000,
    max_rules: int = 40,
    default_top_k: int = DEFAULT_TOP_K,
    rrf_k: int = DEFAULT_RRF_K,
) -> List[Any]:
    return [
        create_ttse_consult_tool(
            store,
            max_chars=max_chars,
            max_rules=max_rules,
            default_top_k=default_top_k,
            rrf_k=rrf_k,
        )
    ]


__all__ = [
    "TTSE_CONSULT_TOOL_NAME",
    "MAX_CONSULT_CATEGORIES",
    "create_ttse_consult_tool",
    "create_ttse_consult_tools",
    "parse_consult_categories",
    "parse_consult_query",
    "render_consult_result",
    "render_consult_result_async",
]
