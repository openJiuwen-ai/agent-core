# coding: utf-8
"""Site profiles and selector cache for compact browser probes."""

from __future__ import annotations

import json
import os
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse


BUILTIN_SITE_PROFILES: List[Dict[str, Any]] = [
    {
        "id": "books_to_scrape",
        "domains": ["books.toscrape.com"],
        "route_patterns": [r"^/$", r"^/catalogue/"],
        "card_container_selectors": [
            "article.product_pod",
            "ol.row > li > article.product_pod",
        ],
        "title_selectors": [
            "h3 a[title]",
            "h3 a",
            "a[title]",
            "img[alt]",
        ],
        "price_selectors": [
            ".price_color",
            "[class*='price' i]",
        ],
        "rating_selectors": [
            "p.star-rating",
            "[class*='star-rating' i]",
            "[class*='rating' i]",
        ],
        "availability_selectors": [
            ".availability",
            "[class*='availability' i]",
            "[class*='stock' i]",
        ],
        "primary_link_selectors": [
            "h3 a[href]",
            "a[href][title]",
            "a[href]",
        ],
        "button_selectors": [
            "form button",
            "button",
            "[role='button']",
            "input[type='submit']",
        ],
    }
]


_CHROME_SELECTOR_FRAGMENTS = [
    "#nav",
    "nav-",
    "navbar",
    "breadcrumb",
    "header",
    "footer",
    "menu",
    "sidebar",
]

_CHROME_TITLES = {
    "fresh & fast",
    "sell",
    "best sellers",
    "customer service",
    "today's deals",
    "new releases",
    "help",
    "login",
    "sign in",
}


def builtin_site_profiles() -> List[Dict[str, Any]]:
    """Return a copy of built-in browser site profiles."""
    return deepcopy(BUILTIN_SITE_PROFILES)


def normalize_route_signature(url: str) -> str:
    """Return a coarse route signature for selector-cache keys."""
    parsed = urlparse(str(url or ""))
    path = parsed.path or "/"

    # Avoid caching selectors against item-specific IDs too narrowly.
    path = re.sub(r"\d+", "*", path)
    path = re.sub(r"/+", "/", path)

    if path != "/" and path.endswith("/"):
        path = path[:-1]

    return path or "/"


def domain_from_url(url: str) -> str:
    parsed = urlparse(str(url or ""))
    return parsed.hostname or ""


def _default_cache_path() -> Path:
    raw = os.environ.get("OPENJIUWEN_BROWSER_SELECTOR_CACHE", "").strip()
    if raw:
        return Path(raw).expanduser()

    return Path.home() / ".openjiuwen" / "browser_selector_cache.json"


def _unique(items: List[str], limit: int = 20) -> List[str]:
    result: List[str] = []
    seen = set()
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def _selector_is_too_broad(selector: str) -> bool:
    value = str(selector or "").strip().lower()
    if not value:
        return True

    if value in {"div", "li", "section", "article", "a", "span", "button"}:
        return True

    if any(fragment in value for fragment in _CHROME_SELECTOR_FRAGMENTS):
        return True

    return False


def _looks_like_page_chrome(card: Dict[str, Any]) -> bool:
    selector = str(card.get("selector_hint") or "").lower()
    title = str(card.get("title") or "").strip().lower()
    preview = str(card.get("text_preview") or "").strip().lower()

    if any(fragment in selector for fragment in _CHROME_SELECTOR_FRAGMENTS):
        return True

    if title in _CHROME_TITLES or preview in _CHROME_TITLES:
        return True

    return False


def _card_quality_score(card: Dict[str, Any]) -> int:
    if _looks_like_page_chrome(card):
        return 0

    score = 0

    title = str(card.get("title") or "").strip()
    preview = str(card.get("text_preview") or "").strip()
    buttons = card.get("buttons") or []

    if len(title) >= 8:
        score += 20
    if len(preview) >= 60:
        score += 15
    if card.get("primary_link"):
        score += 12
    if card.get("price"):
        score += 18
    if card.get("rating"):
        score += 14
    if card.get("review_count"):
        score += 10
    if card.get("availability"):
        score += 8
    if card.get("has_image"):
        score += 12
    if isinstance(buttons, list) and buttons:
        score += 8

    return score


def _is_cacheable_card(card: Dict[str, Any]) -> bool:
    score = _card_quality_score(card)
    if score >= 42:
        return True

    preview = str(card.get("text_preview") or "").strip()
    buttons = card.get("buttons") or []

    return (
        score >= 30
        and len(preview) >= 80
        and (bool(card.get("primary_link")) or bool(buttons))
    )


def _generalize_selector(selector: str) -> List[str]:
    """Make reusable selector variants from a specific selector_hint."""
    selector = str(selector or "").strip()
    if not selector:
        return []

    no_nth = re.sub(r":nth-of-type\(\d+\)", "", selector)
    parts = [part.strip() for part in no_nth.split(">") if part.strip()]

    candidates = []

    if parts:
        candidates.append(parts[-1])

    if len(parts) >= 2:
        candidates.append(" > ".join(parts[-2:]))

    if len(parts) >= 3:
        candidates.append(" > ".join(parts[-3:]))

    # Keep full non-nth selector as a fallback, but not first.
    if no_nth and no_nth not in candidates:
        candidates.append(no_nth)

    return [
        item
        for item in _unique(candidates, limit=4)
        if not _selector_is_too_broad(item)
    ]


class BrowserSelectorCache:
    """Small JSON selector cache for repeated browser probe discoveries."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _default_cache_path()

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "records": []}

        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return {"version": 1, "records": []}

        if not isinstance(data, dict):
            return {"version": 1, "records": []}

        records = data.get("records")
        if not isinstance(records, list):
            data["records"] = []

        data.setdefault("version", 1)
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")

        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)

        tmp_path.replace(self.path)

    def export_for_probe(self, *, max_records: int = 100) -> List[Dict[str, Any]]:
        """Export compact cache records that can be embedded in probe JS."""
        data = self._load()
        records = data.get("records", [])
        if not isinstance(records, list):
            return []

        records = sorted(
            records,
            key=lambda item: (
                float(item.get("quality_score", 0.0) or 0.0),
                int(item.get("success_count", 0)),
                -int(item.get("failure_count", 0)),
                float(item.get("last_success_at", 0.0)),
            ),
            reverse=True,
        )

        return deepcopy(records[:max_records])

    def record_card_probe_result(self, result: Dict[str, Any]) -> None:
        """Record reusable selectors from a successful card probe result."""
        if not isinstance(result, dict) or not result.get("ok"):
            return

        url = str(result.get("url") or "").strip()
        domain = domain_from_url(url)
        if not domain:
            return

        route_signature = normalize_route_signature(url)
        cards = result.get("cards") or []
        if not isinstance(cards, list) or not cards:
            return

        cacheable_cards = [
            card
            for card in cards
            if isinstance(card, dict) and _is_cacheable_card(card)
        ]

        if len(cacheable_cards) < 2:
            return

        cards = cacheable_cards

        selectors: Dict[str, List[str]] = {
            "card_container_selectors": [],
            "title_selectors": [],
            "price_selectors": [],
            "rating_selectors": [],
            "availability_selectors": [],
            "primary_link_selectors": [],
            "button_selectors": [],
        }

        for card in cards[:10]:
            if not isinstance(card, dict):
                continue

            selectors["card_container_selectors"].extend(
                _generalize_selector(str(card.get("selector_hint") or ""))
            )
            selectors["title_selectors"].extend(
                _generalize_selector(str(card.get("title_selector_hint") or ""))
            )
            selectors["price_selectors"].extend(
                _generalize_selector(str(card.get("price_selector_hint") or ""))
            )
            selectors["rating_selectors"].extend(
                _generalize_selector(str(card.get("rating_selector_hint") or ""))
            )
            selectors["availability_selectors"].extend(
                _generalize_selector(str(card.get("availability_selector_hint") or ""))
            )
            selectors["primary_link_selectors"].extend(
                _generalize_selector(str(card.get("primary_link_selector_hint") or ""))
            )

            buttons = card.get("buttons") or []
            if isinstance(buttons, list):
                for button in buttons[:4]:
                    if isinstance(button, dict):
                        selectors["button_selectors"].extend(
                            _generalize_selector(str(button.get("selector_hint") or ""))
                        )

        selectors = {
            key: _unique(value, limit=20)
            for key, value in selectors.items()
            if _unique(value, limit=20)
        }

        if not selectors:
            return

        quality_scores = [_card_quality_score(card) for card in cards]
        quality_score = min(
            1.0,
            sum(quality_scores) / max(1, len(quality_scores)) / 100.0,
        )

        data = self._load()
        records = data.setdefault("records", [])
        if not isinstance(records, list):
            records = []
            data["records"] = records

        key = {
            "domain": domain,
            "route_signature": route_signature,
            "kind": "card_probe",
        }

        existing = None
        for record in records:
            if not isinstance(record, dict):
                continue
            if (
                record.get("domain") == key["domain"]
                and record.get("route_signature") == key["route_signature"]
                and record.get("kind") == key["kind"]
            ):
                existing = record
                break

        now = time.time()

        if existing is None:
            records.append(
                {
                    **key,
                    "selectors": selectors,
                    "success_count": 1,
                    "failure_count": 0,
                    "last_success_at": now,
                    "quality_score": quality_score,
                    "sample_card_count": len(cards),
                }
            )
        else:
            old_selectors = existing.setdefault("selectors", {})
            if not isinstance(old_selectors, dict):
                old_selectors = {}
                existing["selectors"] = old_selectors

            for name, values in selectors.items():
                old_selectors[name] = _unique(
                    list(old_selectors.get(name) or []) + values,
                    limit=20,
                )

            old_quality = float(existing.get("quality_score", 0.0) or 0.0)
            existing["success_count"] = int(existing.get("success_count", 0)) + 1
            existing["last_success_at"] = now
            existing["quality_score"] = max(old_quality, quality_score)
            existing["sample_card_count"] = max(
                int(existing.get("sample_card_count", 0) or 0),
                len(cards),
            )

        # Keep file small.
        data["records"] = records[-200:]
        self._save(data)


_SELECTOR_CACHE: BrowserSelectorCache | None = None


def get_selector_cache() -> BrowserSelectorCache:
    global _SELECTOR_CACHE
    if _SELECTOR_CACHE is None:
        _SELECTOR_CACHE = BrowserSelectorCache()
    return _SELECTOR_CACHE
