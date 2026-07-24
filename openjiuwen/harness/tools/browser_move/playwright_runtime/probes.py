# coding: utf-8
"""Compact browser page probes backed by a shared browser-side page index."""

from __future__ import annotations

from typing import Any, Optional

from .page_structure_index import build_page_index_probe_js


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def build_interactive_probe_js(
    *,
    max_items: int = 50,
    viewport_only: bool = True,
    query: str = "",
    scope_group_id: str = "",
    scope_item_index: Optional[int] = None,
) -> str:
    """Build a shared-index query for compact interactive-element probing."""
    return build_page_index_probe_js(
        {
            "mode": "interactives",
            "params": {
                "max_items": _clamp_int(
                    max_items,
                    default=50,
                    minimum=1,
                    maximum=100,
                ),
                "viewport_only": bool(viewport_only),
                "query": str(query or "").strip(),
                "scope_group_id": str(scope_group_id or "").strip(),
                "scope_item_index": (
                    max(0, int(scope_item_index))
                    if scope_item_index is not None
                    else None
                ),
            },
        }
    )


def build_card_probe_js(
    *,
    max_cards: int = 20,
    viewport_only: bool = True,
    include_buttons: bool = True,
    query: str = "",
    diagnostics_level: str = "compact",
    configuration_revision: str = "",
) -> str:
    """Build a group-first shared-index query for repeated cards/listings."""
    return build_page_index_probe_js(
        {
            "mode": "cards",
            "params": {
                "max_cards": _clamp_int(
                    max_cards,
                    default=20,
                    minimum=1,
                    maximum=50,
                ),
                "viewport_only": bool(viewport_only),
                "include_buttons": bool(include_buttons),
                "query": str(query or "").strip(),
                "diagnostics_level": (
                    str(diagnostics_level or "compact").strip().lower()
                    if str(diagnostics_level or "compact").strip().lower()
                    in {"compact", "standard", "debug"}
                    else "compact"
                ),
                "configuration_revision": str(configuration_revision or "").strip(),
            },
        }
    )
