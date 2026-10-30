# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Trusted goal projection and literal bindings; never infer values from page text."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from openjiuwen.core.foundation.llm.utils.request_sanitizer import clean_unicode

# The host renders the same envelope with a Chinese or an English prefix (preferred_response_language).
_ENVELOPE_PREFIXES = ("你收到一条消息：", "You receive a new message:")
_QUOTES = re.compile(r'"([^"\n]*)"|“([^”\n]*)”|「([^」\n]*)」|『([^』\n]*)』')


def _is_user_message(value: dict) -> bool:
    from_user = value.get("type") == "user input" or value.get("origin_kind") == "external_user_authored"
    return "source" in value and from_user


def normalize_goal(value: Any) -> str:
    """Unwrap only the host's recognized user-message envelope, never arbitrary JSON."""
    for _ in range(3):
        if isinstance(value, dict):
            if isinstance(value.get("content"), str) and _is_user_message(value):
                value = value["content"]
                continue
            return ""
        text = clean_unicode(str(value or "")).strip()
        prefix = next((item for item in _ENVELOPE_PREFIXES if text.startswith(item)), "")
        wrapped = bool(prefix)
        candidate = text[len(prefix):].strip() if wrapped else text
        if candidate.startswith("{"):
            try:
                parsed = json.loads(candidate)
            except ValueError:
                # A truncated host envelope must not produce metadata fill values.
                return "" if wrapped else text
            if isinstance(parsed, dict) and _is_user_message(parsed):
                value = parsed
                continue
        return text
    return clean_unicode(value).strip() if isinstance(value, str) else ""


def task_literals(original: Any, intent: Any) -> tuple[list[str], list[str]]:
    """Search terms and quoted values from the user's own request, never from a model rewrite.

    A rewrite quotes plan fragments and example output as if they were values. Only literals the
    current intent still names are kept, so a subtask never inherits another part's value.
    """
    original, intent = normalize_goal(original), normalize_goal(intent)
    if not original or original == intent:
        return search_values(intent), goal_values(intent)
    return ([value for value in search_values(original) if value in intent],
            [value for value in goal_values(original) if value in intent])


def goal_values(goal: str) -> list[str]:
    goal = normalize_goal(goal)
    values = [next(part for part in match.groups() if part is not None) for match in _QUOTES.finditer(goal)]
    values = [value for value in values if 0 < len(value) <= 200]
    for pattern in (r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", r"\b\d{4}-\d{2}-\d{2}\b"):
        values.extend(match.group() for match in re.finditer(pattern, goal))
    for match in re.finditer(r"(?:搜索|查询|search for)\s*[:：]?\s*([^，。；;\n]+)", goal, re.I):
        value = match.group(1).strip()
        if value.startswith(('"', "“", "「", "『")):
            continue  # Balanced pairs above already remove both delimiters.
        value = re.split(
            r"(?:然后|并返回|并点击|并按|,|\s+and\s+(?:then\s+)?(?:return|click|open|press|summari[sz]e)\b)",
            value, maxsplit=1, flags=re.I,
        )[0].strip()
        if 0 < len(value) <= 200:
            values.append(value)
    return list(dict.fromkeys(value for value in values if value and value in goal))[:20]


def search_values(goal: str) -> list[str]:
    """Bind literals by their search role; quoted ordering labels are not queries."""
    goal = normalize_goal(goal)
    values = []
    for match in re.finditer(
        r"(?:搜索(?!结果|按钮|框|栏|页)|查询(?!结果|按钮|框)|"
        r"search(?!\s*(?:results?\b|box\b))(?: for)?|look up)\s*[:：]?\s*",
        goal, re.I,
    ):
        tail = goal[match.end():]
        quoted = _QUOTES.match(tail)
        if quoted:
            values.append(next(part for part in quoted.groups() if part is not None))
            rest = tail[quoted.end():]
            # Two search literals joined by a conjunction remain ambiguous.
            while True:
                conjunction = re.match(r"\s*(?:和|以及|、|and|or)\s*", rest, re.I)
                if not conjunction:
                    break
                rest = rest[conjunction.end():]
                quoted = _QUOTES.match(rest)
                if not quoted:
                    break
                values.append(next(part for part in quoted.groups() if part is not None))
                rest = rest[quoted.end():]
        else:
            value = re.split(
                r"[，。；;,\n]|然后|并(?:返回|点击|按|切换|打开)|再(?:按|点击|切换)|"
                r"\s+and\s+(?:then\s+)?(?:return|click|open|press|sort|switch)\b",
                tail, maxsplit=1, flags=re.I,
            )[0].strip()
            if value and not value.startswith(('"', '“', '”', '「', '」', '『', '』')):
                values.append(value)
    return list(dict.fromkeys(v for v in values if 0 < len(v) <= 200))[:20]


def explicit_urls(goal: str) -> list[str]:
    urls = []
    for match in re.finditer(r'https?://[^\s<>`"“”「」，。；]+', normalize_goal(goal)):
        url = match.group().rstrip(".,;)")
        try:
            parsed = urlsplit(url)
            if parsed.hostname and not parsed.username and not parsed.password:
                urls.append(url)
        except ValueError:
            continue
    return list(dict.fromkeys(urls))[:4]
