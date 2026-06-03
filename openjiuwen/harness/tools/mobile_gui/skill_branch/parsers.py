# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple


def _extract_first_code_block(text: str) -> str:
    match = re.search(r"```(?:python|json)?\s*\n?(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text.strip()


def _parse_bool(value: object) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return None


def parse_load_skill_images_response(
    response: str,
    *,
    valid_image_ids: Optional[set[str]] = None,
    max_images: int = 4,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Parse ``LOAD_SKILL_IMAGES({...})`` from branch Stage 1."""
    if not (response or "").strip():
        return None, "Empty image-selection response."

    body = _extract_first_code_block(response)
    raw_lines = [line.strip() for line in body.splitlines() if line.strip() and not line.strip().startswith("#")]
    normalized = "\n".join(raw_lines).strip()
    match = re.fullmatch(r"LOAD_SKILL_IMAGES\((.*)\)\s*;?", normalized, re.DOTALL)
    if not match:
        return None, "Response must be a single LOAD_SKILL_IMAGES({...}) call."

    payload_text = match.group(1).strip()
    if not payload_text:
        payload_text = (
            '{"visual_reference_needed": false, "why_not_text_only": '
            '"No visual references requested.", "requests": []}'
        )

    try:
        parsed = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        return None, f"LOAD_SKILL_IMAGES payload must be valid JSON: {exc}"

    if not isinstance(parsed, dict):
        return None, "LOAD_SKILL_IMAGES payload must be a JSON object."

    visual_needed = _parse_bool(parsed.get("visual_reference_needed"))
    why_not = str(parsed.get("why_not_text_only", "") or "").strip()
    requests_raw = parsed.get("requests", [])

    if not isinstance(requests_raw, list):
        return None, "`requests` must be a JSON list."

    if visual_needed is None:
        visual_needed = bool(requests_raw)

    if visual_needed is False:
        if requests_raw:
            return None, "When visual_reference_needed is false, requests must be empty."
        if not why_not:
            return None, "When visual_reference_needed is false, why_not_text_only is required."
        return {
            "visual_reference_needed": False,
            "why_not_text_only": why_not,
            "requests": [],
        }, None

    if not why_not:
        return None, "When visual_reference_needed is true, why_not_text_only is required."
    if not requests_raw:
        return None, "When visual_reference_needed is true, requests must be non-empty."

    merged: Dict[str, Dict[str, str]] = {}
    for item in requests_raw:
        if not isinstance(item, dict):
            return None, "Each request must be a JSON object."
        image_id = str(item.get("image_id", "") or "").strip()
        if not image_id:
            return None, "Each request must include a non-empty image_id."
        if valid_image_ids is not None and image_id not in valid_image_ids:
            return None, f"Unknown image_id: {image_id}"
        reason = str(item.get("reason", "") or "").strip()
        if not reason:
            return None, f"reason is required for image_id {image_id!r}."
        merged[image_id] = {"image_id": image_id, "reason": reason}

    if len(merged) > max_images:
        return None, f"Select at most {max_images} images, got {len(merged)}."

    return {
        "visual_reference_needed": True,
        "why_not_text_only": why_not,
        "requests": list(merged.values()),
    }, None


def parse_planner_json_response(response: str) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """Parse Stage 2 planner JSON object."""
    if not (response or "").strip():
        return None, "Empty planner response."

    body = _extract_first_code_block(response)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return None, f"Planner response must be valid JSON: {exc}"

    if not isinstance(payload, dict):
        return None, "Planner response must be a JSON object."

    applicability = str(payload.get("skill_applicability", "")).strip().lower()
    if applicability not in {"effective", "ineffective", "uncertain"}:
        return None, "skill_applicability must be effective, ineffective, or uncertain."

    fields = {
        "skill_applicability": applicability,
        "subgoal": str(payload.get("subgoal", "")).strip(),
        "plan": str(payload.get("plan", "")).strip(),
        "do_not_do": str(payload.get("do_not_do", "")).strip(),
        "fallback_if_no_progress": str(payload.get("fallback_if_no_progress", "")).strip(),
        "expected_state": str(payload.get("expected_state", "")).strip(),
        "completion_scope": str(payload.get("completion_scope", "")).strip().lower(),
    }

    for key in ("subgoal", "plan", "do_not_do", "fallback_if_no_progress", "expected_state"):
        if not fields.get(key):
            return None, f"The `{key}` field must be a non-empty string."

    if fields["completion_scope"] not in {
        "local_only",
        "needs_verification",
        "maybe_complete",
    }:
        return None, (
            "completion_scope must be one of: local_only, needs_verification, maybe_complete"
        )

    return fields, None
