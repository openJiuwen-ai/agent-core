# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Structured skill output schemas and parsers (detector layer)."""
from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, Field, field_validator


class ThinkingLoopFault(str, Enum):
    """Primary fault types for llm-loop-detection."""

    NONE = "none"
    SEMANTIC_DEADLOCK = "semantic_deadlock"
    TEXT_DEGRADATION = "text_degradation"
    OVERTHINKING = "overthinking"


class SkillVerdict(BaseModel):
    """Base structured skill result."""

    abnormal: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)
    # Non-empty when parser rejected input and returned fail-open normal.
    fail_open_reason: str = ""


class LlmLoopDetectionVerdict(SkillVerdict):
    """Verdict for llm-loop-detection skill."""

    primary_fault: ThinkingLoopFault = ThinkingLoopFault.NONE

    @field_validator("primary_fault", mode="before")
    @classmethod
    def _coerce_fault(cls, value: Any) -> ThinkingLoopFault:
        if isinstance(value, ThinkingLoopFault):
            return value
        text = str(value or "").strip().lower()
        try:
            return ThinkingLoopFault(text)
        except ValueError as e:
            raise ValueError(f"unknown primary_fault: {value!r}") from e

    def model_post_init(self, __context: Any) -> None:
        if self.fail_open_reason:
            return
        abnormal = self.abnormal
        fault_abnormal = self.primary_fault != ThinkingLoopFault.NONE
        if abnormal != fault_abnormal:
            raise ValueError(
                f"abnormal ({abnormal}) inconsistent with primary_fault ({self.primary_fault})"
            )


def extract_json_object_from_text(raw: str) -> dict[str, Any] | None:
    """Parse a JSON object from agent output (bare JSON or ```json fence)."""
    text = str(raw or "").strip()
    if not text:
        return None
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        json_str = match.group(1)
    elif text.startswith("{") and text.endswith("}"):
        json_str = text
    else:
        obj_match = re.search(r"\{.*\}", text, re.DOTALL)
        if not obj_match:
            return None
        json_str = obj_match.group(0)
    try:
        parsed = json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def extract_invoke_output_payload(result: Any) -> dict[str, Any] | None:
    """Extract structured JSON from a DeepAgent invoke result (auto_harness style)."""
    if isinstance(result, dict):
        output = result.get("output")
        if isinstance(output, str) and output.strip():
            return extract_json_object_from_text(output)
        return extract_report_payload(result)
    if isinstance(result, str):
        return extract_json_object_from_text(result) or extract_report_payload(result)
    return None


def extract_report_payload(raw: str | dict[str, Any]) -> dict[str, Any] | None:
    """Extract a JSON object from skill_complete report or top-level payload."""
    if isinstance(raw, dict):
        data: dict[str, Any] = raw
        for key in ("report", "response", "text", "content"):
            val = data.get(key)
            if isinstance(val, dict):
                return val
            if isinstance(val, str) and val.strip():
                try:
                    parsed = json.loads(val.strip())
                    if isinstance(parsed, dict):
                        return parsed
                except (json.JSONDecodeError, TypeError):
                    continue
        return data if data else None

    text = str(raw or "").strip()
    if not text:
        return None
    fenced = extract_json_object_from_text(text)
    if fenced is not None:
        return fenced
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def fail_open_verdict(reason: str = "invalid_or_empty") -> LlmLoopDetectionVerdict:
    return LlmLoopDetectionVerdict(
        abnormal=False,
        primary_fault=ThinkingLoopFault.NONE,
        confidence=0.0,
        rationale="",
        raw={},
        fail_open_reason=reason,
    )


def parse_llm_loop_verdict(raw: str | dict[str, Any]) -> LlmLoopDetectionVerdict:
    """Parse llm-loop-detection output; invalid or legacy formats fail-open."""
    payload = extract_report_payload(raw)
    if payload is None:
        return fail_open_verdict("unparseable_payload")

    required = {"abnormal", "primary_fault"}
    if not required.issubset(payload.keys()):
        return fail_open_verdict("missing_required_fields")

    try:
        verdict = LlmLoopDetectionVerdict(
            abnormal=bool(payload.get("abnormal")),
            primary_fault=payload.get("primary_fault"),
            confidence=float(payload.get("confidence") or 0.0),
            rationale=str(payload.get("rationale") or ""),
            raw=dict(payload),
        )
    except (ValueError, TypeError):
        return fail_open_verdict("schema_validation_failed")

    if verdict.primary_fault != ThinkingLoopFault.NONE and not verdict.abnormal:
        return fail_open_verdict("abnormal_primary_fault_inconsistent")
    return verdict


SKILL_VERDICT_PARSERS: dict[str, Callable[[str | dict[str, Any]], SkillVerdict]] = {
    "llm-loop-detection": parse_llm_loop_verdict,
    "llm-loop-review": parse_llm_loop_verdict,
}


def parse_skill_verdict(skill_name: str, raw: str | dict[str, Any]) -> SkillVerdict:
    parser = SKILL_VERDICT_PARSERS.get(skill_name)
    if parser is None:
        return fail_open_verdict("unknown_skill")
    return parser(raw)


def verdict_to_dict(verdict: SkillVerdict) -> dict[str, Any]:
    """Serialize a verdict for adapter/detector consumption."""
    data: dict[str, Any] = {
        "abnormal": verdict.abnormal,
        "confidence": verdict.confidence,
        "rationale": verdict.rationale,
    }
    if verdict.fail_open_reason:
        data["fail_open_reason"] = verdict.fail_open_reason
    if isinstance(verdict, LlmLoopDetectionVerdict):
        data["primary_fault"] = verdict.primary_fault.value
    if verdict.raw:
        data.update({k: v for k, v in verdict.raw.items() if k not in data})
    return data
