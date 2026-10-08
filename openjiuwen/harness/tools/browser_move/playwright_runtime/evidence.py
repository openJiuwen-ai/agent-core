# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Source binding and updates for the existing browser evidence slots."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from .site_profiles import profile_detail_link_key, site_profiles_for_url

_PAGE_FIELDS = frozenset({"sort_state", "action_confirmation", "source"})


def author_is_action_label(value: Any) -> bool:
    """Reject UI commands, not plausible names, at both extraction boundaries."""
    return bool(re.fullmatch(
        r"查看(?:个人)?主页|个人主页|作者主页|关注|已关注|view\s+profile|follow|subscribe",
        str(value or "").strip(), re.IGNORECASE,
    ))


def requires_destination_page(goal: Any) -> bool:
    clauses = re.split(r"[，,。；;\n]|返回|告诉我|提取|读取|输出|给出|\breturn\b|\breport\b", str(goal or "").lower())
    for clause in clauses:
        if re.search(r"不用|不要|无需|不必|不需要|\bwithout\b|\bdo not\b|\bdon't\b", clause):
            continue
        if re.search(
            r"(?:打开|点击|进入|访问)[^。；;\n]{0,60}(?:首[条篇个项]|第[一1][条篇个项]|该结果|搜索结果|详情页)|"
            r"(?:open|click|visit|enter)[^.;\n]{0,60}(?:first\s+(?:search\s+)?(?:result|article|link)|detail\s+page)",
            clause,
        ):
            return True
    return False


def completion_contradiction(result: dict[str, Any], goal: str) -> str:
    """A small, evidence-based correction allowance, never a new verifier."""
    for slot in result.get("evidence") or []:
        if isinstance(slot, dict) and slot.get("field") == "author" and author_is_action_label(slot.get("value")):
            return "author_is_action_label"
    current = result.get("current_page") or {}
    try:
        page = urlsplit(str(current.get("url") or ""))
    except ValueError:
        return ""
    search_page = bool(re.search(r"/(?:search|s|results|all)(?:/|$)", page.path)) or bool(
        {"wd", "search_query"}.intersection(parse_qs(page.query))
    )
    if search_page and requires_destination_page(goal):
        return "destination_page_not_visited"
    return ""


def today_temperature_fields(value: Any) -> tuple[dict[str, str], str]:
    """Parse only an explicitly labelled today's temperature range, not forecasts."""
    text = re.sub(r"[\u200b-\u200f\ufeff]", "", str(value or ""))
    today = re.search(r"\btoday\b|今天|今日", text, re.IGNORECASE)
    if today is None:
        return {}, ""
    segment = re.split(r"\btomorrow\b|明天|未来|后天", text[today.start():today.start() + 180], maxsplit=1)[0]
    labelled = {}
    for field, label in (("high_temperature", r"high(?:est)?|最高(?:气温|温度|温)?"),
                         ("low_temperature", r"low(?:est)?|最低(?:气温|温度|温)?")):
        value_match = re.search(rf"(?:{label})\s*[:：]?\s*(-?\d{{1,2}})\s*(?:°\s*C|℃|C\b)",
                                segment, re.IGNORECASE)
        if value_match and -60 <= int(value_match.group(1)) <= 60:
            labelled[field] = value_match.group(1)
    if labelled:
        return labelled, segment
    match = re.search(r"(-?\d{1,2})\s*(?:°\s*C|℃)?\s*[~～–至-]\s*(-?\d{1,2})\s*(?:°\s*C|℃)", segment)
    if match is None:
        return {}, ""
    low, high = (int(number) for number in match.groups())
    if not -60 <= low <= high <= 60:
        return {}, ""
    return {"high_temperature": str(high), "low_temperature": str(low)}, segment


def evidence_subject(value: Any) -> str:
    """Keep entity identity while removing only known tracking parameters."""
    url = str(value or "").strip()
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    for profile in site_profiles_for_url(url):
        key = profile_detail_link_key(profile, url)
        if key:
            return key
    query = [
        (key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"spm", "scm", "from", "ref"}
    ]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, urlencode(sorted(query)), ""))


def same_page_url(first: Any, second: Any) -> bool:
    """Ignore tracking/order differences, but preserve routing, filters and fragments."""
    def identity(value: Any) -> tuple:
        try:
            parsed = urlsplit(str(value or ""))
        except ValueError:
            return ()
        if not parsed.netloc:
            return ()
        query = tuple(sorted((key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)
                             if not key.lower().startswith("utm_") and key.lower() not in {"spm", "scm"}))
        return parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", query, parsed.fragment
    left = identity(first)
    return bool(left) and left == identity(second)


def task_observation_allowed(state: dict[str, Any], source: str) -> bool:
    """A reused tab is environment, not task evidence until it is relevant/used."""
    task = str(state.get("goal") or state.get("task") or "").lower()
    if not task or state.get("recent_actions"):
        return True
    if any(token in task for token in ("current page", "current tab", "当前页面", "当前页", "这个页面")):
        return True
    identity = evidence_subject(source)
    if identity and any(identity == evidence_subject(url) for url in state.get("known_urls") or []):
        return True
    try:
        query = parse_qs(urlsplit(source).query)
    except ValueError:
        return False
    search = next((query[key][0] for key in ("q", "wd", "query", "keyword", "search_query") if query.get(key)), "")
    terms = re.findall(r"[\w]+", search.lower())
    return bool(terms) and all(term in task for term in terms)


def merge_evidence_slot(
    state: dict[str, Any], slot: dict[str, Any], *, selected_entity: bool = False,
) -> None:
    """Update one logical slot without combining different selected entities."""
    slot = dict(slot)
    slot["query_id"] = str(state.get("query_id") or state.get("task_id") or "")
    slots = state.setdefault("evidence_slots", [])
    slots[:] = [item for item in slots if item.get("query_id", slot["query_id"]) == slot["query_id"]]
    entity = slot.get("entity_source", "")
    logical = (slot.get("entity"), slot.get("variant"))
    anchor = {}
    for item in reversed(slots):
        if (item.get("entity"), item.get("variant")) == logical and item.get("field") == "title":
            anchor = item
            break
    if not anchor:
        for item in slots:
            if (item.get("entity"), item.get("variant")) != logical:
                continue
            if item.get("field") not in _PAGE_FIELDS and item.get("entity_source"):
                anchor = item
                break
    if entity and slot.get("field") not in _PAGE_FIELDS:
        if selected_entity or (slot.get("field") == "title" and slot.get("status") == "present"):
            # A corrected first-card/title selection invalidates fields of the old card.
            retained = []
            for item in slots:
                if (
                    (item.get("entity"), item.get("variant")) != logical
                    or item.get("field") in _PAGE_FIELDS
                    or item.get("entity_source") == entity
                ):
                    retained.append(item)
            slots[:] = retained
        elif anchor.get("entity_source") and anchor.get("entity_source") != entity:
            return
    key = (*logical, slot.get("field"))
    for index, previous in enumerate(slots):
        if (previous.get("entity"), previous.get("variant"), previous.get("field")) != key:
            continue
        previous_unobserved = previous.get("observation_status") == "not_observed"
        incoming_unobserved = slot.get("observation_status") == "not_observed"
        if incoming_unobserved and not previous_unobserved:
            return
        if previous_unobserved and not incoming_unobserved:
            slots[index] = slot
            return
        detail_correction = (
            previous.get("entity_source", "") == entity and bool(entity)
            and previous.get("evidence_scope") == "listing" and slot.get("evidence_scope") == "detail"
        )
        if previous.get("evidence_scope") == "detail" and slot.get("evidence_scope") == "listing":
            return
        if previous.get("status", "present") == "present" and slot.get("status") != "present" and not detail_correction:
            return
        same_scope = all(previous.get(name, "") == slot.get(name, "") for name in ("qualifier", "date"))
        same_source = previous.get("source") == slot.get("source")
        current_part = slot.get("field") == "duration" and slot.get("qualifier") == "current_part"
        if current_part and not same_scope and previous.get("entity_source", "") == entity:
            slot["alternatives"] = [
                *previous.get("alternatives", []),
                {name: item for name, item in previous.items() if name != "alternatives"},
            ][-3:]
            slots[index] = slot
            return
        replace_source = same_source or detail_correction
        if previous.get("entity_source", "") == entity and same_scope and replace_source:
            if previous.get("alternatives"):
                slot["alternatives"] = previous["alternatives"]
            slots[index] = slot
        elif previous.get("status") != "present" or previous.get("value") == slot.get("value"):
            slots[index] = slot
        else:
            # Keep conflicting quotes/dates distinct for the model, not last-write-wins.
            alternatives = previous.setdefault("alternatives", [])
            candidate = {name: value for name, value in slot.items() if name != "alternatives"}
            if candidate not in alternatives:
                alternatives.append(candidate)
                del alternatives[:-3]
        return
    slots.append(slot)
    del slots[:-20]
