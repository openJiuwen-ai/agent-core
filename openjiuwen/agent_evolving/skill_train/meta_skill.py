# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Optimizer-side meta skill: cross-epoch memory about *how* to edit skills.

The slow update writes guidance into the target skill; the meta skill instead
accumulates advice for the optimizer itself.  It is distilled at each epoch
boundary from the same longitudinal comparison and fed back into the reflect,
aggregate and select stages as an extra context block.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjiuwen.agent_evolving.skill_train.optimizer_io import (
    COMPACT_TOKEN_BUDGET,
    OptimizerCall,
    UserMessage,
    ask_optimizer,
)
from openjiuwen.agent_evolving.skill_train.prompts_loader import load_prompt
from openjiuwen.agent_evolving.skill_train.slow_update import format_comparison_text

RESULT_FILENAME = "meta_skill_result.json"
CONTENT_KEY = "meta_skill_content"

_NO_PRIOR_MEMORY = "(No previous optimizer meta skill — this is the first update.)"

_CONTEXT_PREAMBLE = (
    "This is optimizer-side memory distilled from prior epoch transitions in "
    "this environment. Use it to improve how you propose, merge, and rank "
    "skill edits. Prefer it when the current evidence is ambiguous, but do "
    "not force it if the current trajectories clearly contradict it."
)


# ── On-disk layout ───────────────────────────────────────────────────────────


def _result_path(out_root: str, display_epoch: int) -> Path:
    """Locate ``meta_skill/epoch_XX/meta_skill_result.json`` under *out_root*."""
    return Path(out_root) / "meta_skill" / f"epoch_{display_epoch:02d}" / RESULT_FILENAME


def save_meta_skill_result(out_root: str, display_epoch: int, result: Dict[str, Any]) -> Path:
    """Persist a meta skill result for the given 1-based display epoch."""
    path = _result_path(out_root, display_epoch)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_meta_skill_content(out_root: str, display_epoch: int) -> str:
    """Read back the memory written at the end of *display_epoch*.

    Missing, unreadable or contentless results all collapse to an empty string
    so callers can treat "no memory yet" uniformly.
    """
    if display_epoch <= 0:
        return ""
    path = _result_path(out_root, display_epoch)
    if not path.exists():
        return ""
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a damaged memory file just means "none"
        return ""
    if not isinstance(stored, dict):
        return ""
    return str(stored.get(CONTENT_KEY, "")).strip()


# ── Prompt context ───────────────────────────────────────────────────────────


def format_meta_skill_context(meta_skill_content: str) -> str:
    """Render optimizer memory into a prompt-ready block, or empty when absent."""
    content = (meta_skill_content or "").strip()
    if not content:
        return ""
    return f"## Optimizer Meta Skill\n{_CONTEXT_PREAMBLE}\n\n{content}"


# ── Optimizer call ───────────────────────────────────────────────────────────


def run_meta_skill(
    prev_skill: str,
    curr_skill: str,
    comparison_pairs: List[dict],
    *,
    prev_meta_skill_content: str = "",
    system_prompt: str | None = None,
) -> Optional[Dict[str, Any]]:
    """Distil updated optimizer memory from two adjacent epochs.

    Returns ``None`` when the optimizer fails or yields no memory text.
    """
    prior = (prev_meta_skill_content or "").strip() or _NO_PRIOR_MEMORY

    message = UserMessage()
    message.section("Previous Epoch Last-Step Skill", prev_skill)
    message.section("Current Epoch Last-Step Skill", curr_skill)
    message.section(
        "Previous Optimizer Meta Skill",
        "The following optimizer memory was available during the current epoch. "
        f"Reflect on whether it improved or harmed the quality of edits.\n\n{prior}",
    )
    message.section(
        "Longitudinal Comparison (same tasks, two last-step skills)",
        format_comparison_text(comparison_pairs),
    )

    reply = ask_optimizer(
        OptimizerCall(
            stage="meta_skill",
            system=system_prompt if system_prompt is not None else load_prompt("meta_skill"),
            user=message.render(),
            max_tokens=COMPACT_TOKEN_BUDGET,
        ),
        trace=True,
    )
    body = reply.body or {}
    memory = body.get(CONTENT_KEY)
    if not memory:
        return None
    return {
        "reasoning": str(body.get("reasoning", "")).strip(),
        CONTENT_KEY: str(memory).strip(),
    }
