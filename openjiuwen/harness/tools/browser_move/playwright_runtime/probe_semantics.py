#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Normalize structured Probe semantics after browser-side extraction."""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping
from urllib.parse import parse_qsl, urlsplit

from .page_state import CARD_EVIDENCE_FIELDS

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
_SHOP_RATING_RE = re.compile(
    r"shop[-_ ]?rating|seller[-_ ]?(?:rating|score)|store[-_ ]?(?:rating|score)|"
    r"店铺评分|店铺动态评分|描述相符|服务态度|物流服务",
    re.IGNORECASE,
)
_PRODUCT_RATING_RE = re.compile(
    r"product[-_ ]?rating|item[-_ ]?(?:rating|score)|review[-_ ]?(?:rating|score)|"
    r"商品评分|宝贝评分|商品评价|累计评价",
    re.IGNORECASE,
)


def _text(value: Any, limit: int = 1000) -> str:
    return " ".join(str(value or "").split())[:limit]


def _host(value: Any) -> str:
    try:
        return (urlsplit(str(value or "")).hostname or "").lower()
    except ValueError:
        return ""


def _is_taobao_family(host: str) -> bool:
    return host == "taobao.com" or host.endswith(".taobao.com") or host == "tmall.com" or host.endswith(".tmall.com")


def _is_ctrip_family(host: str) -> bool:
    return host in {"ctrip.com", "trip.com"} or host.endswith((".ctrip.com", ".trip.com"))


def _ctrip_hotel_detail_key(value: Any) -> str:
    try:
        parsed = urlsplit(str(value or ""))
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if not _is_ctrip_family(host):
        return ""
    query = {str(key).lower(): str(item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)}
    hotel_id = query.get("hotelid", "").strip()
    detail_path = bool(
        re.search(r"/(?:hotels?|hotel)/(?:detail/)?(?:\d+|[^/?#]+\.html)(?:/|$)", parsed.path, re.IGNORECASE)
        or re.search(r"/hotels?/detail/?$", parsed.path, re.IGNORECASE)
    )
    if not hotel_id and not detail_path:
        return ""
    return f"hotel:{hotel_id}" if hotel_id else f"{host}{parsed.path}".lower()


def _generic_card_semantics(card: Mapping[str, Any]) -> tuple[str, str, bool]:
    href = _text(card.get("primary_link") or card.get("href"), 500).lower()
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
        if region not in {"main_result", "sidebar", "hot_search", "chat", "activity", "sponsored_result"}:
            region = "main_result"
        if not title and not preview and kind == "result":
            kind = "unknown"
    return region, kind, is_ad


def _apply_site_semantics(
    card: Mapping[str, Any],
    *,
    host: str,
    region: str,
    kind: str,
    is_ad: bool,
) -> tuple[str, str, bool]:
    """Apply narrow site rules only after the generic classifier."""
    href = _text(card.get("primary_link") or card.get("href"), 500).lower()
    title = _text(card.get("title"), 240)
    preview = _text(card.get("text_preview"), 500)
    badges = " ".join(_text(item, 100) for item in (card.get("semantic_badges") or []))

    if host == "zhihu.com" or host.endswith(".zhihu.com"):
        if "/market/paid_column/" in href or "/paid_column/" in href:
            return "sponsored_result", "paid_column", True
        if "精选活动" in f"{title} {badges}" or title.strip() == "精选活动":
            return "activity", "activity", True
        if _PROMOTION_TOKEN_RE.search(badges) or re.match(r"^\s*(推广|广告)\b", preview):
            return "sponsored_result", "promotion", True
        if region == "main_result" and kind not in {"account", "hot_search", "sidebar"} and not is_ad:
            return "main_result", "result", False

    if _is_ctrip_family(host) and _ctrip_hotel_detail_key(href):
        return "main_result", "hotel", is_ad

    return region, kind, is_ad


def _normalize_taobao_rating(card: Dict[str, Any], host: str) -> None:
    if not _is_taobao_family(host):
        return

    rating = card.get("rating")
    rating_kind = _text(card.get("rating_kind"), 80).lower()
    if rating_kind == "unknown":
        statuses = card.get("field_status")
        field_status = dict(statuses) if isinstance(statuses, Mapping) else {}
        field_status["rating"] = "unknown"
        field_status["product_rating"] = "unknown"
        card["field_status"] = field_status
        card["rating"] = None
        card["product_rating"] = None
        return
    evidence = " ".join(
        (
            rating_kind,
            _text(card.get("rating_selector_hint"), 400),
            _text(card.get("rating_raw_text"), 400),
            _text(card.get("text_preview"), 600),
        )
    )
    shop_match = bool(_SHOP_RATING_RE.search(evidence))
    product_match = bool(_PRODUCT_RATING_RE.search(evidence))
    if rating not in (None, "") and shop_match and not product_match:
        card["shop_rating"] = rating
        card["rating"] = None
        card["product_rating"] = None
        card["rating_kind"] = "shop_rating"
        return
    if rating not in (None, ""):
        card["product_rating"] = rating
        card["rating_kind"] = "product_rating"


def _attach_field_contract(card: Dict[str, Any], generation_id: str) -> None:
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
                "selector": _text(selector, 600),
                "raw_text": _text(raw_text, 600),
                "generation_id": generation_id,
                "source": "browser_probe_cards",
            }
        )
        field_provenance[field_name] = entry

    card["field_status"] = field_status
    card["field_provenance"] = field_provenance


def normalize_card_probe_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Make card semantics, missing fields, and provenance deterministic."""
    cards = payload.get("cards")
    if not isinstance(cards, list):
        return payload
    host = _host(payload.get("url"))
    if _is_ctrip_family(host):
        unique_cards: list[Any] = []
        seen_hotel_links: set[str] = set()
        for card in cards:
            if not isinstance(card, Mapping):
                unique_cards.append(card)
                continue
            hotel_key = _ctrip_hotel_detail_key(card.get("primary_link") or card.get("href"))
            if hotel_key and hotel_key in seen_hotel_links:
                continue
            if hotel_key:
                seen_hotel_links.add(hotel_key)
            unique_cards.append(card)
        cards = unique_cards
        payload["cards"] = cards
    generation_id = str(payload.get("generation_id") or "g0")
    result_index = 0
    for card in cards:
        if not isinstance(card, dict):
            continue
        region, kind, is_ad = _generic_card_semantics(card)
        region, kind, is_ad = _apply_site_semantics(
            card,
            host=host,
            region=region,
            kind=kind,
            is_ad=is_ad,
        )
        card["region"] = region
        card["kind"] = kind
        card["is_ad"] = is_ad
        _normalize_taobao_rating(card, host)
        _attach_field_contract(card, generation_id)

        is_natural_result = region == "main_result" and kind in {"result", "product", "hotel"} and not is_ad
        if is_natural_result:
            result_index += 1
            card["result_index"] = result_index
        else:
            card["result_index"] = None
    return payload


__all__ = ["normalize_card_probe_payload"]
