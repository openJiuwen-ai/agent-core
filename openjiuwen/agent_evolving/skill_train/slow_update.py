# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ReflACT Slow Update — epoch-level longitudinal skill refinement."""

from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjiuwen.agent_evolving.skill_train.llm_client import chat_optimizer
from openjiuwen.agent_evolving.skill_train.prompts_loader import load_prompt
from openjiuwen.agent_evolving.skill_train.skill_patch import SLOW_UPDATE_END, SLOW_UPDATE_START
from openjiuwen.agent_evolving.skill_train.utils import extract_json

# Re-export markers so callers can import from this module.
__all__ = [
    "SLOW_UPDATE_START",
    "SLOW_UPDATE_END",
    "has_slow_update_field",
    "inject_empty_slow_update_field",
    "extract_slow_update_field",
    "replace_slow_update_field",
    "build_comparison_pairs",
    "save_comparison_pairs",
    "format_comparison_text",
    "run_slow_update",
]


def has_slow_update_field(skill: str) -> bool:
    return SLOW_UPDATE_START in skill and SLOW_UPDATE_END in skill


def inject_empty_slow_update_field(skill: str) -> str:
    if has_slow_update_field(skill):
        return skill
    block = f"\n\n{SLOW_UPDATE_START}\n{SLOW_UPDATE_END}\n"
    return skill.rstrip() + block


def extract_slow_update_field(skill: str) -> str:
    start = skill.find(SLOW_UPDATE_START)
    end = skill.find(SLOW_UPDATE_END)
    if start == -1 or end == -1:
        return ""
    inner_start = start + len(SLOW_UPDATE_START)
    return skill[inner_start:end].strip()


def _strip_all_slow_update_fields(skill: str) -> str:
    """Remove every SLOW_UPDATE_START/END pair (and content between) from *skill*."""
    while True:
        start = skill.find(SLOW_UPDATE_START)
        if start == -1:
            break
        end = skill.find(SLOW_UPDATE_END, start)
        if end == -1:
            skill = skill[:start] + skill[start + len(SLOW_UPDATE_START) :]
            break
        skill = skill[:start] + skill[end + len(SLOW_UPDATE_END) :]
    skill = skill.replace(SLOW_UPDATE_END, "")
    while "\n\n\n" in skill:
        skill = skill.replace("\n\n\n", "\n\n")
    return skill.rstrip()


def replace_slow_update_field(skill: str, new_content: str) -> str:
    skill = _strip_all_slow_update_fields(skill)
    block = (
        f"\n\n{SLOW_UPDATE_START}\n"
        f"{new_content.strip()}\n"
        f"{SLOW_UPDATE_END}\n"
    )
    return skill + block


def _clip_text(value: Any, limit: int | None = None) -> str:
    del limit
    if value is None:
        return ""
    return str(value)


def _read_trajectory(rollout_dir: str, task_id: str) -> str:
    """Read and format a single trajectory from a rollout directory."""
    conv_path = Path(rollout_dir) / "predictions" / task_id / "conversation.json"
    if not conv_path.exists():
        return "(trajectory not available)"
    try:
        conversation = json.loads(conv_path.read_text(encoding="utf-8"))
    except Exception:
        return "(trajectory read error)"
    if not conversation:
        return "(empty trajectory)"

    lines: List[str] = []
    for entry in conversation:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "tool_call":
            cmd = _clip_text(entry.get("cmd"))
            obs = _clip_text(entry.get("obs"))
            lines.append(f"[action] {cmd}")
            lines.append(f"[obs]    {obs}")
        elif "action" in entry and "env_feedback" in entry:
            step = entry.get("step", "?")
            reasoning = _clip_text(entry.get("reasoning"))
            action = _clip_text(entry.get("action"))
            feedback = _clip_text(entry.get("env_feedback"))
            if reasoning:
                lines.append(f"[step {step} think] {reasoning}")
            lines.append(f"[step {step} action] {action}")
            lines.append(f"[step {step} obs]    {feedback}")
        elif entry.get("role") == "system":
            msg = _clip_text(entry.get("content"))
            lines.append(f"[verification] {msg}")
        else:
            msg = _clip_text(entry.get("content"))
            role = entry.get("role", "agent")
            lines.append(f"[{role}] {msg}")

    return "\n".join(lines)


def build_comparison_pairs(
    results_prev: List[dict],
    results_curr: List[dict],
    items: List[dict],
    prev_rollout_dir: str = "",
    curr_rollout_dir: str = "",
) -> List[dict]:
    """Build a structured list of per-sample comparison entries."""
    prev_by_id = {str(r["id"]): r for r in results_prev}
    curr_by_id = {str(r["id"]): r for r in results_curr}

    pairs: List[dict] = []
    for item in items:
        tid = str(item.get("id", ""))
        prev = prev_by_id.get(tid, {})
        curr = curr_by_id.get(tid, {})
        prev_ok = bool(prev.get("hard", 0))
        curr_ok = bool(curr.get("hard", 0))

        if not prev_ok and curr_ok:
            category = "improved"
        elif prev_ok and not curr_ok:
            category = "regressed"
        elif not prev_ok and not curr_ok:
            category = "persistent_fail"
        else:
            category = "stable_success"

        pairs.append(
            {
                "id": tid,
                "task": item.get(
                    "question",
                    item.get("task_description", item.get("instruction", tid)),
                ),
                "category": category,
                "prev": {
                    "hard": int(prev_ok),
                    "soft": float(prev.get("soft", 0.0)),
                    "predicted_answer": prev.get(
                        "predicted_answer", prev.get("answer", "N/A")
                    ),
                    "fail_reason": prev.get("fail_reason", ""),
                },
                "curr": {
                    "hard": int(curr_ok),
                    "soft": float(curr.get("soft", 0.0)),
                    "predicted_answer": curr.get(
                        "predicted_answer", curr.get("answer", "N/A")
                    ),
                    "fail_reason": curr.get("fail_reason", ""),
                },
                "prev_trajectory": (
                    _read_trajectory(prev_rollout_dir, tid) if prev_rollout_dir else ""
                ),
                "curr_trajectory": (
                    _read_trajectory(curr_rollout_dir, tid) if curr_rollout_dir else ""
                ),
            }
        )

    return pairs


def save_comparison_pairs(pairs: List[dict], out_path: str) -> None:
    """Persist comparison pairs to JSON (without trajectory text to save space)."""
    slim = []
    for p in pairs:
        slim.append(
            {
                "id": p["id"],
                "task": p["task"],
                "category": p["category"],
                "prev": p["prev"],
                "curr": p["curr"],
            }
        )
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(
        json.dumps(slim, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def format_comparison_text(pairs: List[dict]) -> str:
    """Format structured comparison pairs into optimizer-readable text."""
    by_cat: Dict[str, List[dict]] = {
        "regressed": [],
        "persistent_fail": [],
        "improved": [],
        "stable_success": [],
    }
    for p in pairs:
        by_cat.setdefault(p["category"], []).append(p)

    total = len(pairs)
    parts = [
        f"## Longitudinal Comparison Summary\n"
        f"Total samples: {total}\n"
        f"- Improved (wrong→right): {len(by_cat['improved'])}\n"
        f"- Regressed (right→wrong): {len(by_cat['regressed'])}\n"
        f"- Persistent failures (wrong→wrong): {len(by_cat['persistent_fail'])}\n"
        f"- Stable successes (right→right): {len(by_cat['stable_success'])}\n"
    ]

    categories = [
        ("regressed", "Regressions (right→wrong) — HIGHEST PRIORITY", True),
        ("persistent_fail", "Persistent Failures (wrong→wrong)", True),
        ("improved", "Improvements (wrong→right)", True),
        ("stable_success", "Stable Successes (right→right)", False),
    ]

    for cat_key, label, show_traj in categories:
        entries = by_cat[cat_key]
        if not entries:
            parts.append(f"### {label}\n(none)\n")
            continue

        lines = [f"### {label}"]
        for e in entries:
            prev = e["prev"]
            curr = e["curr"]
            lines.append(
                f"\n#### Task {e['id']}: {e['task']}\n"
                f"- Prev epoch: {'PASS' if prev['hard'] else 'FAIL'} "
                f"(soft={prev['soft']:.2f}) — answer: {str(prev['predicted_answer'])}\n"
                f"- Curr epoch: {'PASS' if curr['hard'] else 'FAIL'} "
                f"(soft={curr['soft']:.2f}) — answer: {str(curr['predicted_answer'])}"
            )
            if curr.get("fail_reason"):
                lines.append(f"- Curr fail reason: {curr['fail_reason']}")
            if prev.get("fail_reason") and not prev["hard"]:
                lines.append(f"- Prev fail reason: {prev['fail_reason']}")

            if show_traj:
                if e.get("prev_trajectory"):
                    lines.append(
                        f"\n**Previous epoch trajectory:**\n```\n{e['prev_trajectory']}\n```"
                    )
                if e.get("curr_trajectory"):
                    lines.append(
                        f"\n**Current epoch trajectory:**\n```\n{e['curr_trajectory']}\n```"
                    )

        parts.append("\n".join(lines))

    return "\n\n".join(parts)


def run_slow_update(
    skill_content: str,
    results_prev: List[dict],
    results_curr: List[dict],
    items: List[dict],
    *,
    prev_skill: str = "",
    prev_slow_update_content: str = "",
    prev_rollout_dir: str = "",
    curr_rollout_dir: str = "",
    comparison_pairs: List[dict] | None = None,
    system_prompt: str | None = None,
) -> Optional[Dict[str, Any]]:
    """Run the slow update optimizer call for one epoch boundary."""
    actual_system = system_prompt if system_prompt is not None else load_prompt("slow_update")

    pairs = comparison_pairs
    if pairs is None:
        pairs = build_comparison_pairs(
            results_prev,
            results_curr,
            items,
            prev_rollout_dir=prev_rollout_dir,
            curr_rollout_dir=curr_rollout_dir,
        )
    comparison_text = format_comparison_text(pairs)

    prev_guidance_section = (
        prev_slow_update_content.strip()
        if prev_slow_update_content and prev_slow_update_content.strip()
        else "(No previous guidance — this is the first slow update.)"
    )

    user = (
        f"## Previous Epoch's Skill\n{prev_skill}\n\n"
        f"## Current Epoch's Skill\n{skill_content}\n\n"
        f"## Previous Slow Update Guidance\n"
        f"The following guidance was active during the current epoch. "
        f"Reflect on its effectiveness before writing the new version.\n\n"
        f"{prev_guidance_section}\n\n"
        f"## Longitudinal Comparison (same 20 tasks, two skill versions)\n"
        f"{comparison_text}"
    )

    try:
        response, _ = chat_optimizer(
            system=actual_system,
            user=user,
            max_completion_tokens=16384,
            retries=3,
            stage="slow_update",
        )
        result = extract_json(response)
        if result and result.get("slow_update_content"):
            return {
                "reasoning": str(result.get("reasoning", "")).strip(),
                "slow_update_content": str(result["slow_update_content"]).strip(),
            }
    except Exception:  # noqa: BLE001
        traceback.print_exc()

    return None
