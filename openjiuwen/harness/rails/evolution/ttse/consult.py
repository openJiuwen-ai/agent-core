# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``ttse_consult``: one read-only tool for the disk_catalog inject mode.

``category=<id>`` → that class's FACT + TIP. No-arg still lists the catalog
(compat / missing attachment) but the live prompt already trails the listing.
Paths stay inside the tool; they are never returned or described.
"""

from __future__ import annotations

from typing import Any, List

from .catalog import render_catalog_markdown
from .categories import category_ids, normalize_category
from .render import build_section_text

TTSE_CONSULT_TOOL_NAME = "ttse_consult"

_CONSULT_DESCRIPTION = (
    "Load previously learned FACT and TIP rules for one business-scenario category. "
    "The category listing and counts are already in the trailing prompt attachment. "
    "Set category to one listed id to open that class. "
    "Do not call with no arguments just to re-list the catalog. "
    "Open only categories relevant to the current task. "
    "Do not use bash or read_file to scan the experience bank."
)


def _truncate(text: str, max_chars: int) -> str:
    body = text or ""
    if max_chars <= 0 or len(body) <= max_chars:
        return body
    return body[: max(0, max_chars - 20)].rstrip() + "\n… [truncated]\n"


def render_consult_result(
    store: Any,
    *,
    category: str = "",
    max_chars: int = 8000,
    max_rules: int = 40,
) -> str:
    """Render catalog or one category from the in-memory bank (authoritative)."""
    raw = str(category or "").strip()
    if not raw:
        counts = store.catalog_counts() if hasattr(store, "catalog_counts") else {}
        return _truncate(render_catalog_markdown(counts), max_chars)
    allowed = set(category_ids())
    if raw not in allowed:
        return (
            f"Unknown category `{raw}`. "
            "Use an id from the trailing catalog attachment."
        )
    cid = normalize_category(raw)
    facts, tips = store.records_for_category(cid)
    if max_rules > 0:
        facts = list(facts)[:max_rules]
        tips = list(tips)[:max_rules]
    if not facts and not tips:
        return f"No FACT/TIP rules in category `{cid}`."
    return _truncate(build_section_text(facts, tips, retrieved=False), max_chars)


def create_ttse_consult_tool(store: Any, *, max_chars: int = 8000, max_rules: int = 40) -> Any:
    """Build the rail-owned consult tool bound to ``store``."""
    from openjiuwen.core.foundation.tool import LocalFunction, ToolCard

    async def ttse_consult(category: str = "") -> str:
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
                        "Business-scenario id from the trailing catalog attachment. "
                        "Pass this to load that class's FACT and TIP rules."
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
    "create_ttse_consult_tool",
    "create_ttse_consult_tools",
    "render_consult_result",
]
