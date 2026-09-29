# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Source binding and updates for the existing browser evidence slots."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from .site_profiles import profile_detail_link_key, site_profiles_for_url

_PAGE_FIELDS = frozenset({"sort_state", "action_confirmation", "source"})

_SORT_LABELS = {
    "sales": r"销量|sales|sale-desc|sales-desc",
    "views": r"最多播放|播放量|views|most views|click",
    "latest": r"最新(?:发布|排序)?|最近发布|latest|newest|pubdate|recent",
    "comprehensive": r"综合(?:排序)?|默认排序|comprehensive|relevance|totalrank|default",
    "price_asc": r"价格从低到高|价格升序|price-asc|price ascending|low to high",
    "price_desc": r"价格从高到低|价格降序|price-desc|price descending|high to low",
}


def observed_label(control: dict[str, Any]) -> str:
    """One observed label for public controls, acceptance and decision menus."""
    return next((str(control[key]).strip() for key in ("label", "name", "text", "accessible_name")
                 if control.get(key) and str(control[key]).strip()), "")


def sort_value(value: Any) -> str:
    text = str(value or "").strip().lower()
    return next((name for name, pattern in _SORT_LABELS.items() if re.fullmatch(pattern, text)), "")


def observed_sort(source: str, value: Any = "") -> str:
    """Only observed selected state / recognized ordering parameters, never tool intent."""
    try:
        params = parse_qs(urlsplit(source).query)
    except ValueError:
        return ""
    url_sort = next((sort_value(v) for key in ("order", "sort", "sortType") for v in params.get(key, [])
                     if sort_value(v)), "")
    selected = sort_value(value)
    return "" if selected and url_sort and selected != url_sort else selected or url_sort


def first_organic_result(cards: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [card for card in cards if card.get("result_index") == 1 and card.get("order_known") is True
                  and not card.get("is_ad") and card.get("title")
                  and card.get("region") in {"main_result", "primary_result", "main_results"}]
    return candidates[0] if len(candidates) == 1 else None


def _same_observation_scope(left: dict[str, Any], right: dict[str, Any]) -> bool:
    a, b = left.get("observation_scope"), right.get("observation_scope")
    return bool(a and a == b and a.get("page_id") and a.get("generation_id")
                and type(a.get("interaction_revision")) is int
                and left.get("query_id") == right.get("query_id")
                and left.get("source") == right.get("source"))


def explicit_acceptance(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Small source-bound checks for explicit requests; not a general task planner.

    IDs depend on logical requirements, not DOM nodes or optional phase versions.
    All evidence is already produced by the normal observation/extraction lifecycle.
    """
    goal = str(state.get("goal") or state.get("task") or "")
    # Remove literal search values, not quotes around requested ordering labels.
    from ..decision.intent import search_values

    scope = goal.lower()
    for literal in search_values(goal):
        scope = scope.replace(literal.lower(), "")
    optional = r"不必|不用|无需|不要|若有|如果有|可选|不限|optional|if available|without|do not"
    clauses = [part for part in re.split(r"[，,。；;\n]", scope) if not re.search(optional, part)]
    ordering = [part for part in clauses if re.search(r"排序|切换|按|先|再|sort|order|switch", part)]
    requested = [name for name, pattern in _SORT_LABELS.items()
                 if any(re.search(pattern, part) for part in ordering)]
    records = [r for r in state.get("structured_evidence", []) if isinstance(r, dict)]
    task_id = str(state.get("query_id") or state.get("task_id") or "")
    witnesses: dict[str, list[dict[str, Any]]] = {name: [] for name in requested}
    for record in records:
        if record.get("query_id", task_id) != task_id:
            continue
        values = record.get("values") or {}
        provenance = (record.get("provenance") or {}).get("sort_state") or {}
        source = str(record.get("source") or record.get("url") or provenance.get("source") or "")
        if not source:
            for item in (record.get("cards") or [])[:1]:
                source = str(((item.get("provenance") or item.get("field_provenance") or {})
                              .get("title") or {}).get("source") or "")
        if not evidence_subject(source) or not task_observation_allowed({**state, "recent_actions": []}, source):
            continue
        selected = values.get("sort_state") if provenance or record.get("kind") == "interactive_probe" else ""
        if provenance.get("selection_source") == "first_result_change":
            selected = ""  # A changed result may accompany many filters, not just the requested ordering.
        cards = record.get("cards") or []
        if not selected and cards and record.get("kind") in {"card_probe", "ordered_results"}:
            selected = cards[0].get("sort_state") or ""
        order = observed_sort(source, selected)
        if order not in witnesses:
            continue
        witnesses[order].append({"source": source, "record": record,
                                 "evidence_ref": {"source": source, "kind": record.get("kind"),
                                                  "generation": record.get("generation_id")}})
    result = []
    first = bool(re.search(
        r"首[条个项篇]|第[一1][条个项篇]|first\s+(?:natural\s+)?(?:result|product|item|video)", scope
    ))
    for expected in requested:
        matches = witnesses[expected]
        result.append({"id": f"sort:{expected}", "kind": "sort", "expected": expected,
                       "status": "satisfied" if matches else "unknown",
                       "evidence_ref": matches[-1]["evidence_ref"] if matches else None})
        if first:
            selected = []
            for witness in matches:
                associated = [witness["record"], *[
                    record for record in records if record.get("kind") in {"card_probe", "ordered_results"}
                    and _same_observation_scope(record, witness["record"])
                    and sort_value((record.get("cards") or [{}])[0].get("sort_state")) in {"", expected}
                    and observed_sort(str(record.get("source") or ""),
                                      ((record.get("cards") or [{}])[0].get("sort_state"))) in {"", expected}
                ]]
                for record in associated:
                    card = first_organic_result(record.get("cards") or [])
                    if card:
                        selected.append({**witness["evidence_ref"], "title": card["title"],
                                         "entity_source": card.get("primary_link") or card.get("href"),
                                         "card_kind": record.get("kind"),
                                         "observation_scope": record.get("observation_scope")})
            result.append({"id": f"first_result:{expected}", "kind": "first_result", "expected": expected,
                           "status": "satisfied" if selected else "unknown",
                           "evidence_ref": selected[-1] if selected else None})
    for field, pattern in (("product_rating", r"商品评分|产品评分|product rating"),
                           ("shop_rating", r"店铺评分|卖家评分|shop rating|seller rating|store rating")):
        if not any(re.search(pattern, part) for part in clauses):
            continue
        slots = [s for s in state.get("evidence_slots", []) if s.get("field") == field
                 and s.get("query_id") == task_id and s.get("status") == "present"
                 and s.get("observation_status") != "not_observed" and evidence_subject(s.get("source"))
                 and s.get("value") not in (None, "", "unknown")]
        result.append({"id": f"field:{field}", "kind": "field", "expected": field,
                       "status": "satisfied" if slots else "unknown",
                       "evidence_ref": {k: slots[-1].get(k) for k in ("source", "entity_source", "value")}
                       if slots else None})
    saved = state.get("acceptance_evidence") or {}
    for item in result:
        previous = saved.get(item["id"]) or {}
        if item["kind"] != "field" and item["status"] != "satisfied" and previous.get("query_id") == task_id:
            item.update(status="satisfied", evidence_ref=previous["evidence_ref"])
    for item in result:
        if item["status"] != "satisfied":
            item["reason"] = {
                "sort": "selected_order_not_observed",
                "first_result": "ordered_first_result_not_bound_to_selected_order",
                "field": "requested_field_not_observed",
            }[item["kind"]]
    return result


def retain_acceptance(state: dict[str, Any]) -> None:
    """Retain task-bound proofs when the generic recent-observation window rolls over."""
    task_id = str(state.get("query_id") or state.get("task_id") or "")
    saved = state.setdefault("acceptance_evidence", {})
    for item in explicit_acceptance(state):
        if item["status"] == "satisfied" and item["kind"] != "field":
            saved[item["id"]] = {"query_id": task_id, "evidence_ref": item["evidence_ref"]}


def observe_acceptance(state: dict[str, Any], observation: dict[str, Any]) -> None:
    """Consume the existing fresh capture; no extra browser or model call."""
    source = observation.get("url") or ""
    if not observation.get("capture_id") or not evidence_subject(source):
        return
    selected = [c for c in observation.get("controls", []) if c.get("selected") is True
                and str(c.get("kind") or "").startswith("sort") and sort_value(observed_label(c))]
    label = observed_label(selected[0]) if len(selected) == 1 else ""
    if observed_sort(source, label):
        record = {"kind": "sort_observation", "source": source, "values": {"sort_state": label},
                  "query_id": str(state.get("query_id") or state.get("task_id") or ""),
                  "provenance": {"sort_state": {"source": source, "capture_id": observation["capture_id"]}}}
        page = observation.get("page") or {}
        if page.get("page_id") and page.get("generation_id") and type(page.get("interaction_revision")) is int:
            record["observation_scope"] = {
                key: page[key] for key in ("page_id", "generation_id", "interaction_revision")
            }
        records = state.setdefault("structured_evidence", [])
        records[:] = [r for r in records if not (r.get("kind") == "sort_observation" and r.get("source") == source)]
        records.append(record)
        retain_acceptance(state)
        del records[:-20]


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
