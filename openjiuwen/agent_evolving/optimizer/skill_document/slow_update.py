# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Epoch-level slow update: strategic guidance for the skill document's protected region.

Pure async functions. Caller (SkillDocumentOptimizer._run_slow_update) handles
injection into the skill document and operator sync.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from openjiuwen.agent_evolving.optimizer.llm_resilience import (
    LLMInvokePolicy,
    invoke_text_with_retry,
)
from openjiuwen.agent_evolving.optimizer.skill_document.prompts import load_skill_opt_prompt
from openjiuwen.agent_evolving.optimizer.skill_document.types import SlowUpdateResult
from openjiuwen.core.common.logging import logger

if TYPE_CHECKING:
    from openjiuwen.core.foundation.llm.model import Model

_SLOW_UPDATE_POLICY = LLMInvokePolicy(
    attempt_timeout_secs=180,
    total_budget_secs=600,
    max_attempts=2,
)

_RE_THRESHOLD = 0.1  # score delta threshold for regression/improvement
_FAILURE_THRESHOLD = 0.5  # below this = failure
_SUCCESS_THRESHOLD = 0.7  # above this = success


def build_comparison_text(
    prev_epoch_comparison: list[dict],
    curr_epoch_comparison: list[dict],
) -> str:
    """Build longitudinal comparison text for the slow_update/meta_skill LLM prompt.

    Matches cases by case_id across epochs and categorizes into:
    - Regressions: score dropped by >= _RE_THRESHOLD
    - Improvements: score improved by >= _RE_THRESHOLD
    - Persistent failures: both epochs scored below _FAILURE_THRESHOLD
    - Stable successes: both epochs scored >= _SUCCESS_THRESHOLD

    Returns structured markdown for LLM consumption. Empty string when
    either side has no data.
    """
    if not prev_epoch_comparison or not curr_epoch_comparison:
        return ""

    prev_by_id = {c["case_id"]: c for c in prev_epoch_comparison}
    curr_by_id = {c["case_id"]: c for c in curr_epoch_comparison}

    matched_ids = set(prev_by_id) & set(curr_by_id)
    if not matched_ids:
        return ""

    regressions: list[str] = []
    improvements: list[str] = []
    persistent_failures: list[str] = []
    stable_successes: list[str] = []

    for cid in sorted(matched_ids):
        prev_score = prev_by_id[cid].get("curr_score", 0.0)
        curr_score = curr_by_id[cid].get("curr_score", 0.0)
        curr_reason = curr_by_id[cid].get("curr_reason", "")
        delta = curr_score - prev_score

        entry = f"- {cid}: {prev_score:.2f} → {curr_score:.2f} (Δ={delta:+.2f})"
        if curr_reason:
            entry += f"  reason: {curr_reason[:200]}"

        if delta <= -_RE_THRESHOLD:
            regressions.append(entry)
        elif delta >= _RE_THRESHOLD:
            improvements.append(entry)
        elif prev_score < _FAILURE_THRESHOLD and curr_score < _FAILURE_THRESHOLD:
            persistent_failures.append(entry)
        elif prev_score >= _SUCCESS_THRESHOLD and curr_score >= _SUCCESS_THRESHOLD:
            stable_successes.append(entry)
        # Cases with small delta and mixed thresholds are uncategorized (noise)

    lines = [f"## Longitudinal Comparison ({len(matched_ids)} matched cases)\n"]

    lines.append(f"### Regressions ({len(regressions)})")
    lines.extend(regressions if regressions else ["- None"])

    lines.append(f"\n### Improvements ({len(improvements)})")
    lines.extend(improvements if improvements else ["- None"])

    lines.append(f"\n### Persistent Failures ({len(persistent_failures)})")
    lines.extend(persistent_failures if persistent_failures else ["- None"])

    lines.append(f"\n### Stable Successes ({len(stable_successes)})")
    lines.extend(stable_successes if stable_successes else ["- None"])

    return "\n".join(lines)


def _parse_slow_update_response(raw: str) -> SlowUpdateResult:
    """Parse LLM response into SlowUpdateResult. Graceful on failure."""
    raw = raw.strip()
    if not raw:
        return SlowUpdateResult(reasoning="", slow_update_content="", action="empty_response")

    # Try direct JSON parse, then fallback extraction
    parsed = None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Try stripping markdown fences
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE).strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            # Try extracting JSON object
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass

    if not isinstance(parsed, dict):
        return SlowUpdateResult(reasoning="", slow_update_content="", action="parse_failed")

    return SlowUpdateResult(
        reasoning=str(parsed.get("reasoning", "")),
        slow_update_content=str(parsed.get("slow_update_content", "")),
        action="success" if parsed.get("slow_update_content") else "missing_content",
    )


async def run_slow_update(
    llm: "Model",
    model: str,
    *,
    prev_skill: str,
    curr_skill: str,
    comparison_text: str,
    prev_guidance: str = "",
) -> SlowUpdateResult:
    """Call slow_update LLM and return strategic guidance.

    Pure async function. Caller handles injection into skill document.
    Returns SlowUpdateResult with empty content on failure (graceful degradation).
    """
    template = load_skill_opt_prompt("slow_update")
    prompt = (
        f"{template}\n\n"
        f"## Previous Epoch Skill\n```markdown\n{prev_skill}\n```\n\n"
        f"## Current Epoch Skill\n```markdown\n{curr_skill}\n```\n\n"
        f"{comparison_text}\n\n"
    )
    if prev_guidance:
        prompt += f"## Previous Slow Update Guidance\n{prev_guidance}\n\n"

    try:
        raw = await invoke_text_with_retry(
            llm,
            model,
            prompt,
            policy=_SLOW_UPDATE_POLICY,
        )
    except Exception:
        logger.warning("slow_update LLM call failed", exc_info=True)
        return SlowUpdateResult(reasoning="", slow_update_content="", action="llm_error")

    return _parse_slow_update_response(raw)
