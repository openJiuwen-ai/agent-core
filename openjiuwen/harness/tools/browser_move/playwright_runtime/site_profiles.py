# coding: utf-8
"""Site profiles and selector cache for compact browser probes."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Mapping
from urllib.parse import parse_qsl, urlparse


logger = logging.getLogger(__name__)


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
    },
    {
        "id": "bilibili",
        "domains": ["bilibili.com"],
        "task_aliases": ["bilibili", "b站", "哔哩哔哩"],
        "evidence_entity": "bilibili_search_result",
    },
    {
        "id": "taobao_marketplace",
        "domains": ["taobao.com", "tmall.com"],
        "task_aliases": ["taobao", "tmall", "淘宝", "天猫"],
        "evidence_entity": "product",
        "shop_selectors": [
            "a[href*='shop.taobao.com']",
            "a[href*='shop.tmall.com']",
        ],
        "semantic_rules": {
            "detail_link": {
                "domains": ["taobao.com", "tmall.com"],
                "path_patterns": [r"/item\.htm$"],
                "query_id_params": ["id"],
                "key_prefix": "product",
                "kind": "product",
            },
            "primary_link": {
                "preferred_patterns": [
                    r"item\.taobao\.com/item\.htm",
                    r"detail\.tmall\.com/item\.htm",
                ],
                "required_query_params": ["id"],
                "excluded_patterns": [
                    r"^wangwang:",
                    r"^aliim:",
                    r"amos\.alicdn\.com",
                    r"wangwang",
                    r"/shop/",
                    r"shop\.(?:taobao|tmall)\.com",
                    r"store\.taobao\.com",
                    r"seller\.taobao\.com",
                    r"contact-seller",
                ],
            },
            "rating": {
                "shop_patterns": [
                    r"shop[-_ ]?rating",
                    r"seller[-_ ]?(?:rating|score)",
                    r"store[-_ ]?(?:rating|score)",
                    r"店铺评分|店铺动态评分|描述相符|服务态度|物流服务",
                ],
                "product_patterns": [
                    r"product[-_ ]?rating",
                    r"item[-_ ]?(?:rating|score)",
                    r"review[-_ ]?(?:rating|score)",
                    r"商品评分|宝贝评分|商品评价|累计评价",
                ],
                "unknown_without_match": True,
            },
        },
    },
    {
        "id": "zhihu",
        "domains": ["zhihu.com"],
        "task_aliases": ["zhihu", "知乎"],
        "evidence_entity": "article_or_answer",
        "semantic_rules": {
            "paid_link_patterns": [r"/market/paid_column/", r"/paid_column/"],
            "activity_text_patterns": [r"精选活动"],
            "promotion_text_patterns": [r"(?:^|\s)(?:推广|广告)(?:\s|$)"],
            "natural_result_kind": "result",
        },
    },
    {
        "id": "csdn",
        "domains": ["csdn.net"],
        "task_aliases": ["csdn"],
        "evidence_entity": "article",
    },
    {
        "id": "douban",
        "domains": ["douban.com"],
        "task_aliases": ["douban", "豆瓣"],
        "evidence_entity": "movie",
    },
    {
        "id": "ctrip_hotels",
        "domains": ["ctrip.com", "trip.com"],
        "task_aliases": ["ctrip", "trip.com", "携程"],
        "evidence_entity": "hotel",
        "semantic_rules": {
            "detail_link": {
                "domains": ["ctrip.com", "trip.com"],
                "path_patterns": [
                    r"/(?:hotels?|hotel)/(?:detail/)?(?:\d+|[^/?#]+\.html)(?:/|$)",
                    r"/hotels?/detail/?$",
                ],
                "query_id_params": ["hotelId", "hotelid"],
                "key_prefix": "hotel",
                "kind": "hotel",
            },
            "deduplicate_detail_links": True,
            "control_semantics": {
                "region": "hotel_search",
                "context_patterns": [
                    r"hotel|hotels|hotel-search|hotelsearch",
                    r"酒店|目的地|入住|退房",
                ],
                "global_search_patterns": [r"search|query|keyword|搜索"],
                "kinds": [
                    {
                        "kind": "hotel_destination",
                        "patterns": [r"目的地|城市|酒店|关键词|destination|city|hotelname"],
                        "tags": ["input", "textarea"],
                    },
                    {
                        "kind": "hotel_checkin",
                        "patterns": [r"入住|check[-_ ]?in|start[-_ ]?date"],
                    },
                    {
                        "kind": "hotel_checkout",
                        "patterns": [r"退房|离店|check[-_ ]?out|end[-_ ]?date"],
                    },
                    {
                        "kind": "hotel_search_submit",
                        "patterns": [r"搜索|查询|search|submit"],
                        "tags": ["button"],
                        "roles": ["button"],
                        "types": ["submit"],
                    },
                    {
                        "kind": "hotel_filter",
                        "patterns": [r"筛选|价格|星级|评分|filter|price|star|rating"],
                    },
                ],
            },
        },
    },
]


def _domain_matches(host: str, domains: Any) -> bool:
    normalized_host = str(host or "").strip().lower()
    return any(
        normalized_host == domain or normalized_host.endswith(f".{domain}")
        for domain in (str(item or "").strip().lower() for item in (domains or []))
        if domain
    )


def site_profile_for_host(host: str) -> Dict[str, Any] | None:
    """Return the built-in profile responsible for a host, if any."""
    for profile in BUILTIN_SITE_PROFILES:
        if _domain_matches(host, profile.get("domains")):
            return profile
    return None


def infer_profile_evidence_entity(task: str) -> str:
    """Resolve site-named task entities without coupling runtime code to sites."""
    normalized_task = str(task or "").lower()
    for profile in BUILTIN_SITE_PROFILES:
        aliases = profile.get("task_aliases") or []
        if any(str(alias or "").lower() in normalized_task for alias in aliases):
            return str(profile.get("evidence_entity") or "").strip()
    return ""


def _semantic_rules(profile: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(profile, Mapping):
        return {}
    rules = profile.get("semantic_rules")
    return rules if isinstance(rules, Mapping) else {}


def _matches_any(value: Any, patterns: Any) -> bool:
    text = str(value or "")
    for pattern in patterns or []:
        try:
            if re.search(str(pattern), text, re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def profile_detail_link_key(profile: Mapping[str, Any] | None, value: Any) -> str:
    """Return a stable entity key for a profile-defined detail link."""
    detail = _semantic_rules(profile).get("detail_link")
    if not isinstance(detail, Mapping):
        return ""
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if not _domain_matches(host, detail.get("domains") or profile.get("domains")):
        return ""
    query = {
        str(key).lower(): str(item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
    }
    identifier = next(
        (
            query.get(str(parameter or "").lower(), "").strip()
            for parameter in detail.get("query_id_params") or []
            if query.get(str(parameter or "").lower(), "").strip()
        ),
        "",
    )
    path_matches = _matches_any(parsed.path, detail.get("path_patterns"))
    if not identifier and not path_matches:
        return ""
    prefix = str(detail.get("key_prefix") or "detail").strip().lower()
    return f"{prefix}:{identifier}" if identifier else f"{host}{parsed.path}".lower()


def apply_site_card_semantics(
    card: Mapping[str, Any],
    *,
    host: str,
    region: str,
    kind: str,
    is_ad: bool,
) -> tuple[str, str, bool]:
    """Apply data-driven site semantics after generic card classification."""
    profile = site_profile_for_host(host)
    rules = _semantic_rules(profile)
    if not rules:
        return region, kind, is_ad
    href = str(card.get("primary_link") or card.get("href") or "")[:500]
    title = " ".join(str(card.get("title") or "").split())[:240]
    preview = " ".join(str(card.get("text_preview") or "").split())[:500]
    badges = " ".join(
        " ".join(str(item or "").split())[:100]
        for item in (card.get("semantic_badges") or [])
    )

    if _matches_any(href, rules.get("paid_link_patterns")):
        return "sponsored_result", "paid_column", True
    if _matches_any(f"{title} {badges}", rules.get("activity_text_patterns")):
        return "activity", "activity", True
    if _matches_any(f"{badges} {preview}", rules.get("promotion_text_patterns")):
        return "sponsored_result", "promotion", True

    detail_key = profile_detail_link_key(profile, href)
    detail = rules.get("detail_link")
    if detail_key and isinstance(detail, Mapping):
        return "main_result", str(detail.get("kind") or kind), is_ad

    natural_kind = str(rules.get("natural_result_kind") or "").strip()
    is_natural_result = (
        region == "main_result" and kind not in {"account", "hot_search", "sidebar"} and not is_ad
    )
    if natural_kind and is_natural_result:
        return "main_result", natural_kind, False
    return region, kind, is_ad


def normalize_site_card_fields(card: Dict[str, Any], *, host: str) -> None:
    """Normalize profile-owned fields without adding site branches to Probe."""
    profile = site_profile_for_host(host)
    rating_rules = _semantic_rules(profile).get("rating")
    if not isinstance(rating_rules, Mapping):
        return
    rating = card.get("rating")
    rating_kind = " ".join(str(card.get("rating_kind") or "").split()).lower()[:80]
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
            str(card.get("rating_selector_hint") or "")[:400],
            str(card.get("rating_raw_text") or "")[:400],
            str(card.get("text_preview") or "")[:600],
        )
    )
    shop_match = _matches_any(evidence, rating_rules.get("shop_patterns"))
    product_match = _matches_any(evidence, rating_rules.get("product_patterns"))
    if rating not in (None, "") and shop_match and not product_match:
        card["shop_rating"] = rating
        card["rating"] = None
        card["product_rating"] = None
        card["rating_kind"] = "shop_rating"
        return
    if rating not in (None, ""):
        card["product_rating"] = rating
        card["rating_kind"] = "product_rating"


def deduplicate_site_cards(cards: List[Any], *, host: str) -> List[Any]:
    """Apply profile-defined entity deduplication while preserving card order."""
    profile = site_profile_for_host(host)
    rules = _semantic_rules(profile)
    if not rules.get("deduplicate_detail_links"):
        return cards
    deduplicated: List[Any] = []
    seen_keys: set[str] = set()
    for card in cards:
        if not isinstance(card, Mapping):
            deduplicated.append(card)
            continue
        detail_key = profile_detail_link_key(
            profile,
            card.get("primary_link") or card.get("href"),
        )
        if detail_key and detail_key in seen_keys:
            continue
        if detail_key:
            seen_keys.add(detail_key)
        deduplicated.append(card)
    return deduplicated


_CHROME_SELECTOR_FRAGMENTS = [
    "#nav",
    "nav-",
    "navbar",
    "breadcrumb",
    "header",
    "footer",
    "menu",
    "sidebar",
    "toolbar",
    "container-right",
    "main-rt",
    "main-right",
    "right-sidebar",
    "right-side",
    "side-bar",
    "csdn-toolbar",
    "csdn-profile",
    "onlyuser",
    "passport",
    "login",
    "vip",
    "write",
    "remind",
    "message",
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
    "会员中心",
    "消息",
    "创作中心",
    "个人中心",
}


def builtin_site_profiles() -> List[Dict[str, Any]]:
    """Return a copy of built-in browser site profiles."""
    return deepcopy(BUILTIN_SITE_PROFILES)


def site_profiles_for_url(url: str) -> List[Dict[str, Any]]:
    """Return only profiles relevant to the current URL when it is known."""
    host = domain_from_url(url)
    if not host:
        return builtin_site_profiles()
    return [
        deepcopy(profile)
        for profile in BUILTIN_SITE_PROFILES
        if _domain_matches(host, profile.get("domains"))
    ]


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


_AUTHOR_SELECTOR_FRAGMENTS = [
    "a.user",
    ".user",
    "span.name-text",
    "name-text",
    "nickname",
    "nick",
    "avatar",
    "author",
    "byline",
    "profile",
    "btm-rt",
]

_ARTICLE_LINK_SELECTOR_FRAGMENTS = [
    "block-title",
    "so-item-report",
    "article/details",
    "/article/",
    "h1",
    "h2",
    "h3",
    "h4",
    "title",
    "headline",
    "subject",
]


def _selector_looks_like_author_profile(selector: str) -> bool:
    value = str(selector or "").strip().lower()
    if not value:
        return False
    if _selector_looks_like_article_link(value):
        return False
    return any(fragment in value for fragment in _AUTHOR_SELECTOR_FRAGMENTS)


def _selector_looks_like_article_link(selector: str) -> bool:
    value = str(selector or "").strip().lower()
    if not value:
        return False
    return any(fragment in value for fragment in _ARTICLE_LINK_SELECTOR_FRAGMENTS)


def _looks_like_page_chrome(card: Dict[str, Any]) -> bool:
    selector = str(card.get("selector_hint") or "").lower()
    title = str(card.get("title") or "").strip().lower()
    preview = str(card.get("text_preview") or "").strip().lower()

    link = str(card.get("primary_link") or "").strip().lower()

    if any(fragment in selector for fragment in _CHROME_SELECTOR_FRAGMENTS):
        return True

    if any(fragment in link for fragment in ["mp.csdn.net", "passport.csdn.net"]):
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
    if card.get("author"):
        score += 10
    if card.get("source"):
        score += 6
    if len(str(card.get("summary") or "").strip()) >= 40:
        score += 14
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
            with self.path.open("r", encoding="utf-8-sig") as handle:
                data = json.load(handle)
        except Exception as exc:
            logger.warning(
                "Failed to load browser selector cache from %s: %s",
                self.path,
                exc,
            )
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

        selector_source = str(result.get("selector_source") or "").strip().lower()
        min_cacheable_cards = 3 if selector_source == "generic" else 2

        if len(cacheable_cards) < min_cacheable_cards:
            logger.debug(
                "Skipping browser selector cache write for %s: only %s cacheable cards from %s source",
                url,
                len(cacheable_cards),
                selector_source or "unknown",
            )
            return

        cards = cacheable_cards

        selectors: Dict[str, List[str]] = {
            "card_container_selectors": [],
            "title_selectors": [],
            "price_selectors": [],
            "rating_selectors": [],
            "availability_selectors": [],
            "author_selectors": [],
            "source_selectors": [],
            "summary_selectors": [],
            "primary_link_selectors": [],
            "button_selectors": [],
        }

        for card in cards[:10]:
            if not isinstance(card, dict):
                continue

            selectors["card_container_selectors"].extend(
                _generalize_selector(str(card.get("selector_hint") or ""))
            )
            title_selector_hint = str(card.get("title_selector_hint") or "")
            if not _selector_looks_like_author_profile(title_selector_hint):
                selectors["title_selectors"].extend(
                    _generalize_selector(title_selector_hint)
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
            selectors["author_selectors"].extend(
                _generalize_selector(str(card.get("author_selector_hint") or ""))
            )
            selectors["source_selectors"].extend(
                _generalize_selector(str(card.get("source_selector_hint") or ""))
            )
            selectors["summary_selectors"].extend(
                _generalize_selector(str(card.get("summary_selector_hint") or ""))
            )
            primary_link_selector_hint = str(card.get("primary_link_selector_hint") or "")
            if not _selector_looks_like_author_profile(primary_link_selector_hint):
                selectors["primary_link_selectors"].extend(
                    _generalize_selector(primary_link_selector_hint)
                )

            buttons = card.get("buttons") or []
            if isinstance(buttons, list):
                for button in buttons[:4]:
                    if isinstance(button, dict):
                        button_selector_hint = str(button.get("selector_hint") or "")
                        generalized_button_selectors = _generalize_selector(button_selector_hint)
                        if _selector_looks_like_article_link(button_selector_hint):
                            selectors["primary_link_selectors"].extend(
                                generalized_button_selectors
                            )
                            selectors["title_selectors"].extend(
                                generalized_button_selectors
                            )
                        selectors["button_selectors"].extend(
                            generalized_button_selectors
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

    def record_card_probe_cache_rejection(self, result: Dict[str, Any]) -> None:
        """Record that cached card-probe selectors were tried but rejected.

        A card probe can still succeed through a site profile or generic discovery
        after cached selectors failed validation. Track that separately so stale
        cache records are deprioritized on later exports instead of being retried
        indefinitely.
        """
        if not isinstance(result, dict):
            return

        try:
            cache_records_used = int(result.get("cache_records_used", 0) or 0)
        except (TypeError, ValueError):
            cache_records_used = 0

        if cache_records_used <= 0 or result.get("cache_accepted") is True:
            return

        url = str(result.get("url") or "").strip()
        domain = domain_from_url(url)
        if not domain:
            return

        route_signature = normalize_route_signature(url)

        data = self._load()
        records = data.get("records", [])
        if not isinstance(records, list):
            return

        now = time.time()
        rejection_reason = str(
            result.get("cache_rejection_reason")
            or result.get("selector_source")
            or "cache_validation_failed"
        ).strip()

        updated = False
        for record in records:
            if not isinstance(record, dict):
                continue

            if (
                record.get("domain") == domain
                and record.get("route_signature") == route_signature
                and record.get("kind") == "card_probe"
            ):
                record["failure_count"] = int(record.get("failure_count", 0) or 0) + 1
                record["last_failure_at"] = now
                record["last_failure_reason"] = rejection_reason
                updated = True

        if updated:
            self._save(data)



_SELECTOR_CACHE: BrowserSelectorCache | None = None


def get_selector_cache() -> BrowserSelectorCache:
    global _SELECTOR_CACHE
    if _SELECTOR_CACHE is None:
        _SELECTOR_CACHE = BrowserSelectorCache()
    return _SELECTOR_CACHE
