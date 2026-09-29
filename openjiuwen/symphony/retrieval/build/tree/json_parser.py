from __future__ import annotations

import json
from typing import Optional, Union


def parse_json_from_response(
    response: str,
    default: Union[dict, list, None] = None,
) -> Union[dict, list]:
    fallback = {} if default is None else default
    if not isinstance(response, str):
        return fallback

    for candidate in _json_candidates(response):
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, (dict, list)):
            return decoded
    return fallback


def _json_candidates(response: str) -> list[str]:
    raw = response.strip()
    candidates: list[str] = []
    if raw:
        candidates.append(raw)
    fenced = _strip_wrapping_fence(raw)
    if fenced and fenced != raw:
        candidates.insert(0, fenced)
    candidates.extend(_extract_balanced_fragments(response))
    seen: set[str] = set()
    unique: list[str] = []
    for item in candidates:
        cleaned = item.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        unique.append(cleaned)
    return unique


def _strip_wrapping_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    body = text.splitlines()
    if body and body[0].startswith("```"):
        body = body[1:]
    if body and body[-1].strip() == "```":
        body = body[:-1]
    return "\n".join(body).strip()


def _extract_balanced_fragments(text: str) -> list[str]:
    fragments: list[str] = []
    for opening, closing in (("{", "}"), ("[", "]")):
        index = text.find(opening)
        while index >= 0:
            fragment = _slice_balanced(text, index, opening, closing)
            if fragment:
                fragments.append(fragment)
                break
            index = text.find(opening, index + 1)
    return fragments


def _slice_balanced(text: str, start: int, opening: str, closing: str) -> Optional[str]:
    level = 0
    inside_string = False
    escaped = False
    for cursor, char in enumerate(text[start:], start=start):
        if escaped:
            escaped = False
            continue
        if char == "\\" and inside_string:
            escaped = True
            continue
        if char == '"':
            inside_string = not inside_string
            continue
        if inside_string:
            continue
        if char == opening:
            level += 1
        elif char == closing:
            level -= 1
            if level == 0:
                fragment_end = cursor + 1
                return text[start:fragment_end]
    return None
