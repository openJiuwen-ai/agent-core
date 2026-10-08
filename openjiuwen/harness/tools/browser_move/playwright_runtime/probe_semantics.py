#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Normalize structured Probe semantics after browser-side extraction."""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping
from urllib.parse import urlsplit

from .evidence import author_is_action_label, today_temperature_fields
from .page_state import CARD_EVIDENCE_FIELDS
from .site_profiles import (
    apply_site_card_semantics,
    deduplicate_site_cards,
    normalize_site_card_fields,
)

_PROMOTION_TOKEN_RE = re.compile(
    r"(?:^|[\s_\-/])(ad|ads|sponsored|promoted|promotion)(?:$|[\s_\-/])|广告|推广",
    re.IGNORECASE,
)
_ACTIVITY_TOKEN_RE = re.compile(r"精选活动|(?:^|[\s_\-/])(activity|campaign|event)(?:$|[\s_\-/])", re.IGNORECASE)
_HOT_SEARCH_TOKEN_RE = re.compile(
    r"(?:^|[\s_\-/])(hot[-_ ]?(?:search|list|rank|topic)|hotlist|hotrank|toplist|trending)"
    r"(?:$|[\s_\-/])|热搜|热榜",
    re.IGNORECASE,
)


def _text(value: Any, limit: int = 1000) -> str:
    return " ".join(str(value or "").split())[:limit]


def _host(value: Any) -> str:
    try:
        return (urlsplit(str(value or "")).hostname or "").lower()
    except ValueError:
        return ""


def _generic_card_semantics(card: Mapping[str, Any]) -> tuple[str, str, bool]:
    href = str(card.get("primary_link") or card.get("href") or "").lower()
    selector = _text(card.get("selector_hint"), 500).lower()
    badges = " ".join(_text(item, 100) for item in (card.get("semantic_badges") or []))
    title = _text(card.get("title"), 240)
    preview = _text(card.get("text_preview"), 500)
    explicit = f"{selector} {badges}".lower()

    kind = str(card.get("kind") or "result").strip().lower()
    region = str(card.get("region") or "main_result").strip().lower()
    is_ad = bool(card.get("is_ad"))

    if _ACTIVITY_TOKEN_RE.search(f"{explicit} {title}"):
        region, kind = "activity", "activity"
    elif _PROMOTION_TOKEN_RE.search(explicit):
        region, kind, is_ad = "sponsored_result", "promotion", True
    elif re.search(r"/(?:paid|premium|sponsored|promotion)(?:[_\-/]|$)", href):
        region, kind, is_ad = "sponsored_result", "paid_result", True
    elif _HOT_SEARCH_TOKEN_RE.search(f"{explicit} {preview}"):
        region, kind = "hot_search", "hot_search"
    else:
        if re.search(r"(?:^|[\s_\-/])(sidebar|aside|right-rail|right-panel)(?:$|[\s_\-/])", selector):
            region = "sidebar"
        if card.get("primary_link") and re.search(r"/(?:item|product|goods)(?:[./]|$)", href):
            kind = "product"
        if region in {"navigation", "header", "footer"}:
            kind = "navigation_link"
        elif region in {"ai_answer", "ai_overview"}:
            kind = region
        if not title and not preview and kind == "result":
            kind = "unknown"
    return region, kind, is_ad


def _attach_field_contract(card: Dict[str, Any], generation_id: str, source_url: str) -> None:
    statuses = card.get("field_status")
    field_status = dict(statuses) if isinstance(statuses, Mapping) else {}
    provenance = card.get("field_provenance")
    field_provenance = dict(provenance) if isinstance(provenance, Mapping) else {}

    for field_name in CARD_EVIDENCE_FIELDS:
        value = card.get(field_name)
        present = value not in (None, "", [], {})
        field_status[field_name] = "present" if present else str(field_status.get(field_name) or "missing")
        existing = field_provenance.get(field_name)
        entry = dict(existing) if isinstance(existing, Mapping) else {}
        selector = card.get(f"{field_name}_selector_hint")
        if field_name in {"product_rating", "shop_rating"}:
            selector = selector or card.get("rating_selector_hint")
        if field_name == "primary_link":
            selector = selector or card.get("primary_link_selector_hint")
        raw_text = card.get(f"{field_name}_raw_text")
        if field_name in {"product_rating", "shop_rating"}:
            raw_text = raw_text or card.get("rating_raw_text")
        if field_name == "primary_link":
            raw_text = raw_text or card.get("primary_link_text")
        if raw_text in (None, "") and present:
            raw_text = value
        if not present and (field_status[field_name] != "unknown" or raw_text in (None, "")):
            continue
        entry.update(
            {
                "selector": str(selector or "").strip(),
                "raw_text": _text(raw_text, 600),
                "generation_id": generation_id,
                "source": source_url,
            }
        )
        if field_name == "duration":
            entry["scope"] = str(card.get("duration_scope") or "unknown")
        field_provenance[field_name] = entry

    card["field_status"] = field_status
    card["field_provenance"] = field_provenance


def _normalize_today_temperatures(card: Dict[str, Any]) -> None:
    text = " ".join(str(card.get(key) or "") for key in ("title", "summary", "text_preview"))
    values, raw_text = today_temperature_fields(text)
    for field_name, value in values.items():
        if card.get(field_name) in (None, ""):
            card[field_name] = value
            card[f"{field_name}_raw_text"] = raw_text
            card[f"{field_name}_selector_hint"] = card.get("summary_selector_hint") or card.get("selector_hint") or ""


def normalize_card_probe_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Make card semantics, missing fields, and provenance deterministic."""
    cards = payload.get("cards")
    if not isinstance(cards, list):
        return payload
    host = _host(payload.get("url"))
    cards = deduplicate_site_cards(cards, host=host)
    payload["cards"] = cards
    generation_id = str(payload.get("generation_id") or "g0")
    result_index = 0
    observed_identities: set[str] = set()
    classifications: Dict[str, set[tuple[str, str, bool]]] = {}
    for card in cards:
        if not isinstance(card, dict):
            continue
        region, kind, is_ad = _generic_card_semantics(card)
        region, kind, is_ad = apply_site_card_semantics(
            card,
            host=host,
            region=region,
            kind=kind,
            is_ad=is_ad,
        )
        card["region"] = region
        card["kind"] = kind
        card["is_ad"] = is_ad
        card["ad_status"] = "ad" if is_ad else "unknown"
        card.setdefault("classification_reason", f"page_region:{region}")
        normalize_site_card_fields(card, host=host)
        if author_is_action_label(card.get("author")):
            card["author_raw_text"] = card["author"]
            card["author"] = ""
            card.setdefault("field_status", {})["author"] = "unknown"
        _normalize_today_temperatures(card)
        _attach_field_contract(card, generation_id, str(payload.get("url") or ""))

        is_natural_result = region == "main_result" and kind in {"result", "product", "hotel"} and not is_ad
        if is_natural_result and card.get("order_known") is not False:
            result_index += 1
            card["result_index"] = result_index
        else:
            card["result_index"] = None
        identity = str(card.get("primary_link") or card.get("href") or "").strip()
        identity = identity or _text(card.get("title"), 500).lower()
        if identity:
            classifications.setdefault(identity, set()).add((region, kind, is_ad))
            if is_natural_result:
                observed_identities.add(identity)
    conflicts = [
        {
            "identity": identity[:240],
            "classifications": [
                {"region": region, "kind": kind, "is_ad": is_ad}
                for region, kind, is_ad in sorted(values)
            ],
        }
        for identity, values in classifications.items()
        if len(values) > 1
    ][:5]
    diagnostics = payload.get("diagnostics")
    diagnostics = dict(diagnostics) if isinstance(diagnostics, Mapping) else {}
    diagnostics.update(
        {
            "observed_count": len(observed_identities),
            "ranked_count": result_index,
            "classification_conflict": bool(conflicts),
        }
    )
    if conflicts:
        diagnostics["classification_conflicts"] = conflicts
        diagnostics["recommended_fallback"] = "one_precise_probe"
    payload["observed_count"] = len(observed_identities)
    payload["diagnostics"] = diagnostics
    return payload


__all__ = ["normalize_card_probe_payload"]
