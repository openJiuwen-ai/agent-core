# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Epoch-level meta skill: optimizer-side memory for future edit generation.

Pure async function. Caller (SkillDocumentOptimizer._run_meta_skill) stores
the result in _meta_skill_context. Does not modify the skill document.
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
from openjiuwen.core.common.logging import logger

if TYPE_CHECKING:
    from openjiuwen.core.foundation.llm.model import Model

_META_SKILL_POLICY = LLMInvokePolicy(
    attempt_timeout_secs=180,
    total_budget_secs=600,
    max_attempts=2,
)


def _parse_meta_skill_response(raw: str) -> str:
    """Parse LLM response and extract meta_skill_content. Returns empty string on failure."""
    raw = raw.strip()
    if not raw:
        return ""

    parsed = None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE).strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass

    if not isinstance(parsed, dict):
        return ""

    return str(parsed.get("meta_skill_content", "") or "")


async def run_meta_skill(
    llm: "Model",
    model: str,
    *,
    prev_skill: str,
    curr_skill: str,
    comparison_text: str,
    prev_meta_skill: str = "",
) -> str:
    """Call meta_skill LLM and return optimizer-side memory content.

    Returns the meta_skill_content string from the LLM JSON response.
    Returns empty string on failure (graceful degradation).
    """
    template = load_skill_opt_prompt("meta_skill")
    prompt = (
        f"{template}\n\n"
        f"## Previous Epoch Skill\n```markdown\n{prev_skill}\n```\n\n"
        f"## Current Epoch Skill\n```markdown\n{curr_skill}\n```\n\n"
        f"{comparison_text}\n\n"
    )
    if prev_meta_skill:
        prompt += f"## Previous Meta Skill\n{prev_meta_skill}\n\n"

    try:
        raw = await invoke_text_with_retry(
            llm,
            model,
            prompt,
            policy=_META_SKILL_POLICY,
        )
    except Exception:
        logger.warning("meta_skill LLM call failed", exc_info=True)
        return ""

    return _parse_meta_skill_response(raw)
