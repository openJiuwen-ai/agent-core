# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Optimizer-side meta skill memory for cross-epoch optimization guidance."""

from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjiuwen.agent_evolving.skill_train.llm_client import chat_optimizer
from openjiuwen.agent_evolving.skill_train.prompts_loader import load_prompt
from openjiuwen.agent_evolving.skill_train.slow_update import format_comparison_text
from openjiuwen.agent_evolving.skill_train.utils import extract_json


def format_meta_skill_context(meta_skill_content: str) -> str:
    """Render optimizer memory into a prompt-ready context block."""
    content = (meta_skill_content or "").strip()
    if not content:
        return ""
    return (
        "## Optimizer Meta Skill\n"
        "This is optimizer-side memory distilled from prior epoch transitions in "
        "this environment. Use it to improve how you propose, merge, and rank "
        "skill edits. Prefer it when the current evidence is ambiguous, but do "
        "not force it if the current trajectories clearly contradict it.\n\n"
        f"{content}"
    )


def run_meta_skill(
    prev_skill: str,
    curr_skill: str,
    comparison_pairs: List[dict],
    *,
    prev_meta_skill_content: str = "",
    system_prompt: str | None = None,
) -> Optional[Dict[str, Any]]:
    """Produce updated optimizer-side meta skill from adjacent epochs."""
    actual_system = system_prompt if system_prompt is not None else load_prompt("meta_skill")

    prev_meta_section = (
        prev_meta_skill_content.strip()
        if prev_meta_skill_content and prev_meta_skill_content.strip()
        else "(No previous optimizer meta skill — this is the first update.)"
    )

    comparison_text = format_comparison_text(comparison_pairs)
    user = (
        f"## Previous Epoch Last-Step Skill\n{prev_skill}\n\n"
        f"## Current Epoch Last-Step Skill\n{curr_skill}\n\n"
        f"## Previous Optimizer Meta Skill\n"
        f"The following optimizer memory was available during the current epoch. "
        f"Reflect on whether it improved or harmed the quality of edits.\n\n"
        f"{prev_meta_section}\n\n"
        f"## Longitudinal Comparison (same tasks, two last-step skills)\n"
        f"{comparison_text}"
    )

    try:
        response, _ = chat_optimizer(
            system=actual_system,
            user=user,
            max_completion_tokens=16384,
            retries=3,
            stage="meta_skill",
        )
        result = extract_json(response)
        if result and result.get("meta_skill_content"):
            return {
                "reasoning": str(result.get("reasoning", "")).strip(),
                "meta_skill_content": str(result["meta_skill_content"]).strip(),
            }
    except Exception:  # noqa: BLE001
        traceback.print_exc()

    return None


def load_meta_skill_content(out_root: str, display_epoch: int) -> str:
    """Load meta skill written at end of SkillOpt-style 1-based *display_epoch*."""
    if display_epoch <= 0:
        return ""
    path = (
        Path(out_root)
        / "meta_skill"
        / f"epoch_{display_epoch:02d}"
        / "meta_skill_result.json"
    )
    if not path.exists():
        return ""
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return str(result.get("meta_skill_content", "")).strip()
    except Exception:
        return ""


def save_meta_skill_result(
    out_root: str,
    display_epoch: int,
    result: Dict[str, Any],
) -> Path:
    """Persist meta skill result JSON under ``meta_skill/epoch_XX/``."""
    meta_dir = Path(out_root) / "meta_skill" / f"epoch_{display_epoch:02d}"
    meta_dir.mkdir(parents=True, exist_ok=True)
    path = meta_dir / "meta_skill_result.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
