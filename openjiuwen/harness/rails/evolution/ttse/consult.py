# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``ttse_consult``: one read-only tool for FACT/TIP catalog disclosure.

``category=<id>`` → that class's FACT + TIP. Comma-separated ids (or a list)
open several related classes in one call, capped so the bank is not dumped.
No-arg still lists the catalog (compat / missing attachment) but the live
prompt already trails the listing. Paths stay inside the tool; they are
never returned or described.
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Sequence

from .catalog import render_catalog_markdown
from .categories import category_ids, normalize_category
from .render import build_section_text

TTSE_CONSULT_TOOL_NAME = "ttse_consult"
MAX_CONSULT_CATEGORIES = 3

_CONSULT_DESCRIPTION = (
    "Load previously learned FACT and TIP rules for business-scenario categories. "
    "The category listing and counts are already in the trailing prompt attachment. "
    "Set category to one listed id, or several related ids separated by commas "
    f"(max {MAX_CONSULT_CATEGORIES}). "
    "Do not call with no arguments just to re-list the catalog. "
    "Open only categories relevant to the current task. "
    "Do not use bash or read_file to scan the experience bank."
)

_SPLIT_IDS = re.compile(r"[,;|\s]+")


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


def _dedupe(ids: Sequence[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for cid in ids:
        if cid in seen:
            continue
        seen.add(cid)
        out.append(cid)
    return out


def _render_one_category(store: Any, cid: str, max_rules: int) -> str:
    facts, tips = store.records_for_category(cid)
    if max_rules > 0:
        facts = list(facts)[:max_rules]
        tips = list(tips)[:max_rules]
    if not facts and not tips:
        return f"No FACT/TIP rules in category `{cid}`."
    return build_section_text(facts, tips, retrieved=False)


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


def render_consult_result(
    store: Any,
    *,
    category: Any = "",
    max_chars: int = 8000,
    max_rules: int = 40,
) -> str:
    """Render catalog or one/several categories from the in-memory bank."""
    requested = parse_consult_categories(category)
    if not requested:
        counts = store.catalog_counts() if hasattr(store, "catalog_counts") else {}
        return _truncate(render_catalog_markdown(counts), max_chars)

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

    notes: List[str] = []
    if unknown:
        notes.append(_unknown_message(unknown))
    if omitted:
        notes.append(
            f"Opened the first {MAX_CONSULT_CATEGORIES} categories; "
            "omitted: " + ", ".join(f"`{x}`" for x in omitted) + "."
        )
    if not known:
        return "\n".join(notes)

    if len(known) == 1 and not notes:
        return _truncate(_render_one_category(store, known[0], max_rules), max_chars)

    blocks: List[str] = []
    if notes:
        blocks.append("\n".join(notes))
    for cid in known:
        blocks.append(f"## `{cid}`\n\n{_render_one_category(store, cid, max_rules).rstrip()}")
    return _truncate("\n\n".join(blocks) + "\n", max_chars)


def create_ttse_consult_tool(store: Any, *, max_chars: int = 8000, max_rules: int = 40) -> Any:
    """Build the rail-owned consult tool bound to ``store``."""
    from openjiuwen.core.foundation.tool import LocalFunction, ToolCard

    async def ttse_consult(category: Any = "") -> str:
        return render_consult_result(
            store,
            category=category,
            max_chars=max_chars,
            max_rules=max_rules,
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
                        "Opens those classes' FACT and TIP rules."
                    ),
                }
            },
            "required": [],
        },
        parallel_safe=True,
        idempotent=True,
        stateless=False,
    )
    return LocalFunction(card=card, func=ttse_consult)


def create_ttse_consult_tools(store: Any, *, max_chars: int = 8000, max_rules: int = 40) -> List[Any]:
    return [create_ttse_consult_tool(store, max_chars=max_chars, max_rules=max_rules)]


__all__ = [
    "TTSE_CONSULT_TOOL_NAME",
    "MAX_CONSULT_CATEGORIES",
    "create_ttse_consult_tool",
    "create_ttse_consult_tools",
    "parse_consult_categories",
    "render_consult_result",
]
