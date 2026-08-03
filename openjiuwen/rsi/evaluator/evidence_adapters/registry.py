# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Select artifact evidence adapters without exposing media details to the judge."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from openjiuwen.rsi.evaluator.evidence_adapters.web import (
    WebArtifactAdapter,
)


def collect_artifact_runtime_evidence(
    artifacts_dir: Path,
    evidence_dir: Path,
    *,
    viewport_widths: list[int] | None = None,
    web_verification: dict[str, Any] | None = None,
    evidence_profiles: list[str] | None = None,
    judge_skills: list[str] | None = None,
    score_policy: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Collect bounded machine evidence requested by active judge skills."""
    cache_path = evidence_dir / "artifact_runtime_evidence.json"
    request_payload = {
        "viewport_widths": sorted(viewport_widths or []),
        "web_verification": web_verification or {},
        "evidence_profiles": sorted(evidence_profiles or []),
        "judge_skills": sorted(judge_skills or []),
        "score_policy": score_policy or {},
    }
    request_fingerprint = hashlib.sha256(
        json.dumps(request_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            request_is_legacy_default = not any(
                (viewport_widths, web_verification, evidence_profiles, judge_skills, score_policy)
            )
            fingerprint_matches = isinstance(cached, dict) and (
                cached.get("request_fingerprint") == request_fingerprint
            )
            legacy_cache_matches = (
                isinstance(cached, dict) and request_is_legacy_default and "request_fingerprint" not in cached
            )
            if fingerprint_matches or legacy_cache_matches:
                return cached
        except (OSError, json.JSONDecodeError):
            pass
    profiles = set(evidence_profiles or [])
    adapters = (("web_browser", WebArtifactAdapter()),)
    for profile, adapter in adapters:
        if (not profiles or profile in profiles) and adapter.supports(artifacts_dir):
            evidence = adapter.collect(
                artifacts_dir,
                evidence_dir,
                viewport_widths=viewport_widths,
                verification=web_verification,
            )
            evidence["evidence_profile"] = profile
            break
    else:
        evidence = {
            "adapter": "none",
            "status": "not_applicable",
            "observations": [],
        }
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence["judge_skills"] = list(judge_skills or [])
    evidence["score_policy"] = dict(score_policy or {})
    evidence["request_fingerprint"] = request_fingerprint
    cache_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence


__all__ = ["collect_artifact_runtime_evidence"]
