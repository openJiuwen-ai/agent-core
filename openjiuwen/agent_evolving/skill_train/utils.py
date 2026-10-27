# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Helpers for ReflACT skill training."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

from openjiuwen.agent_evolving.utils import TuneUtils

SKILL_MARK = "<<<SKILL>>>"


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Parse a JSON object from an LLM response.

    A full replacement skill may follow the JSON object after ``<<<SKILL>>>``.
    That body is installed as ``new_skill`` so the document does not have to
    be escaped inside a JSON string.
    """
    marked = _skill_after_mark(text)
    parsed = TuneUtils.parse_json_from_llm_response(_json_head(text))
    if isinstance(parsed, dict):
        if marked:
            _install_new_skill(parsed, marked)
        return parsed
    return _repair_skill_reply(text, marked)


def _json_head(text: str) -> str:
    if SKILL_MARK not in (text or ""):
        return text
    return text.split(SKILL_MARK, 1)[0]


def _skill_after_mark(text: str) -> str:
    if SKILL_MARK not in (text or ""):
        return ""
    body = text.split(SKILL_MARK, 1)[1].strip()
    fenced = re.fullmatch(r"```(?:markdown|md)?\s*(.*?)\s*```", body, re.DOTALL)
    if fenced:
        body = fenced.group(1).strip()
    return body


def _looks_like_skill(text: str) -> bool:
    body = text.strip()
    return body.startswith("---") and "name:" in body[:500] and len(body) > 40


def _json_slice(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return ""
    stop = end + 1
    return text[start:stop]


def _escape_controls_in_strings(text: str) -> str:
    """Turn raw newlines inside JSON strings into escapes."""
    out: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
                out.append(char)
                continue
            if char == "\\":
                escaped = True
                out.append(char)
                continue
            if char == '"':
                in_string = False
                out.append(char)
                continue
            if char == "\n":
                out.append("\\n")
                continue
            if char == "\r":
                out.append("\\r")
                continue
            if char == "\t":
                out.append("\\t")
                continue
            out.append(char)
            continue
        if char == '"':
            in_string = True
        out.append(char)
    return "".join(out)


def _loads_object(text: str) -> Optional[Dict[str, Any]]:
    blob = _json_slice(text)
    if not blob:
        return None
    for candidate in (blob, _escape_controls_in_strings(blob)):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _embedded_skill(text: str) -> str:
    """Lift a SKILL.md that was pasted inside a broken ``new_skill`` string."""
    key = text.find('"new_skill"')
    if key < 0:
        return ""
    rest = text[key:]
    match = re.search(r"(?m)^---\s*$", rest)
    if match is None:
        return ""
    body_start = match.start()
    body = rest[body_start:].strip()
    body = re.sub(r'"\s*(?:\}\s*)+$', "", body).strip()
    return body if _looks_like_skill(body) else ""


def _install_new_skill(data: Dict[str, Any], skill: str) -> None:
    text = skill.strip()
    if not text:
        return
    if not text.endswith("\n"):
        text += "\n"
    patch = data.get("patch")
    containers: list[dict] = []
    if isinstance(patch, dict):
        containers.append(patch)
    containers.append(data)
    for container in containers:
        items = container.get("skill_candidates")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            items[0]["new_skill"] = text
            return
        if isinstance(items, list):
            container["skill_candidates"] = [
                {"title": "rewritten-skill", "change_summary": [], "new_skill": text}
            ]
            return
    target = patch if isinstance(patch, dict) else data
    target["skill_candidates"] = [
        {"title": "rewritten-skill", "change_summary": [], "new_skill": text}
    ]


def _skill_envelope(skill: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "patch": {
            "reasoning": "Recovered a complete skill document from the reply.",
            "skill_candidates": [],
        }
    }
    _install_new_skill(data, skill)
    return data


def _repair_skill_reply(text: str, marked: str) -> Optional[Dict[str, Any]]:
    parsed = _loads_object(_json_head(text))
    if isinstance(parsed, dict):
        if marked:
            _install_new_skill(parsed, marked)
        return parsed
    embedded = marked or _embedded_skill(text)
    if _looks_like_skill(embedded):
        return _skill_envelope(embedded)
    bare = text.strip()
    fenced = re.fullmatch(r"```(?:markdown|md)?\s*(.*?)\s*```", bare, re.DOTALL)
    if fenced:
        bare = fenced.group(1).strip()
    if _looks_like_skill(bare):
        return _skill_envelope(bare)
    return None


def safe_fs_id(value: str) -> str:
    """Make item ids safe as Windows directory names (e.g. strip ':')."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")
    return safe[:120] or "item"


def prediction_item_dir(prediction_dir: str, task_id: str) -> str:
    """Resolve ``predictions/<id>/``, trying raw then sanitized id (Windows)."""
    raw = os.path.join(prediction_dir, str(task_id))
    if os.path.isdir(raw):
        return raw
    safe = os.path.join(prediction_dir, safe_fs_id(task_id))
    if os.path.isdir(safe):
        return safe
    return raw
