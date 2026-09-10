# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Post-write assignment of FACT/TIP rules onto a closed category set.

Runs *after* bank mutation so the frozen induce prompt is unchanged. Shape
matches skill retrieval's assignment pass: existing items × closed ids → 1:1.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from openjiuwen.agent_evolving.optimizer.llm_resilience import (
    LLMInvokePolicy,
    invoke_text_with_retry,
)
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model

from .categories import (
    OTHER_CATEGORY,
    format_categories_for_prompt,
    normalize_category,
)

RuleRef = Tuple[str, str]  # (text, rtype)


_ASSIGNMENT_PROMPT = """TTSE category assignment pass.

Available groups:
{groups_list}

Rules awaiting placement:
{rules_list}

Rules:
- every numbered rule must appear once
- only use one of the listed group ids
- choose the best primary business scenario (not the tool/capability named in a TIP)
- if a rule spans multiple groups, prefer the broadest correct home
- if none apply, use `{other}`

Respond as JSON:
{{
  "assignments": {{
    "1": "group-id",
    "2": "group-id"
  }}
}}
"""


def _rules_list(items: Sequence[RuleRef]) -> str:
    lines = []
    for i, (text, rtype) in enumerate(items, start=1):
        tag = "FACT" if rtype == "fact" else "TIP"
        lines.append(f"{i}. [{tag}] {text}")
    return "\n".join(lines)


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.IGNORECASE)
    if fenced:
        raw = fenced.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            stop = end + 1
            raw = raw[start:stop]
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_assignments(
    text: str,
    items: Sequence[RuleRef],
    *,
    valid: Optional[Iterable[str]] = None,
) -> List[Tuple[str, str, str]]:
    """Parse LLM JSON into ``(text, rtype, category)``. Missing/illegal → other."""
    payload = _extract_json_object(text)
    raw = payload.get("assignments", payload) if payload else {}
    if not isinstance(raw, dict):
        raw = {}
    by_index: Dict[int, str] = {}
    for key, value in raw.items():
        try:
            idx = int(str(key).strip())
        except (TypeError, ValueError):
            continue
        by_index[idx] = str(value or "").strip()
    out: List[Tuple[str, str, str]] = []
    for i, (rule_text, rtype) in enumerate(items, start=1):
        out.append((rule_text, rtype, normalize_category(by_index.get(i), valid=valid)))
    return out


async def classify_rules(
    *,
    llm: Model,
    model: str,
    policy: LLMInvokePolicy,
    items: Sequence[RuleRef],
    categories: Optional[Iterable[Dict[str, Any]]] = None,
) -> List[Tuple[str, str, str]]:
    """Assign a closed-set category to each newly added rule.

    Best-effort: on empty input or LLM failure every item is ``other``.
    """
    pending = [(str(text), rtype) for text, rtype in items if str(text).strip()]
    if not pending:
        return []
    prompt = _ASSIGNMENT_PROMPT.format(
        groups_list=format_categories_for_prompt(categories),
        rules_list=_rules_list(pending),
        other=OTHER_CATEGORY,
    )
    try:
        out = await invoke_text_with_retry(llm, model, prompt, policy=policy, temperature=0.0)
    except Exception as exc:  # noqa: BLE001 - never block induction
        logger.warning("[TTSERail] category assignment failed: %s", exc)
        return [(text, rtype, OTHER_CATEGORY) for text, rtype in pending]
    assigned = parse_assignments(out, pending)
    logger.info(
        "[TTSERail] assigned categories for %s new rule(s)",
        len(assigned),
    )
    return assigned


__all__ = ["classify_rules", "parse_assignments"]
