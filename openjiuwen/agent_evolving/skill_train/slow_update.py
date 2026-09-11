# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ReflACT Slow Update -- epoch-level longitudinal skill refinement.

Two responsibilities live here:

1. The protected ``SLOW_UPDATE`` region inside a skill document -- injecting,
   reading and replacing it without disturbing the rest of the body.
2. Longitudinal comparison -- running the same tasks against two consecutive
   skill versions, bucketing each task into one of four outcome categories,
   and rendering that into an optimizer-readable report.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional

from openjiuwen.agent_evolving.skill_train.optimizer_io import (
    COMPACT_TOKEN_BUDGET,
    OptimizerCall,
    UserMessage,
    ask_optimizer,
)
from openjiuwen.agent_evolving.skill_train.prompts_loader import load_prompt
from openjiuwen.agent_evolving.skill_train.skill_patch import SLOW_UPDATE_END, SLOW_UPDATE_START
from openjiuwen.agent_evolving.skill_train.trajectory_text import read_trajectory

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
    "SlowUpdateInputs",
    "run_slow_update",
]

_FIELD_RE = re.compile(f"{re.escape(SLOW_UPDATE_START)}.*?{re.escape(SLOW_UPDATE_END)}", re.DOTALL)
_BLANK_RUN_RE = re.compile(r"\n{3,}")

_NO_PRIOR_GUIDANCE = "(No previous guidance — this is the first slow update.)"


# ── Protected slow-update region ─────────────────────────────────────────────


def has_slow_update_field(skill: str) -> bool:
    """Report whether *skill* already carries a slow-update region."""
    return SLOW_UPDATE_START in skill and SLOW_UPDATE_END in skill


def inject_empty_slow_update_field(skill: str) -> str:
    """Append an empty slow-update region, unless one is already present."""
    if has_slow_update_field(skill):
        return skill
    return f"{skill.rstrip()}\n\n{SLOW_UPDATE_START}\n{SLOW_UPDATE_END}\n"


def extract_slow_update_field(skill: str) -> str:
    """Return the text inside the first slow-update region, or empty string."""
    opened = skill.find(SLOW_UPDATE_START)
    closed = skill.find(SLOW_UPDATE_END)
    if opened < 0 or closed < 0:
        return ""
    inner = opened + len(SLOW_UPDATE_START)
    return skill[inner:closed].strip()


def _without_slow_update_fields(skill: str) -> str:
    """Strip every slow-update region plus any orphaned marker."""
    body = _FIELD_RE.sub("", skill)
    body = body.replace(SLOW_UPDATE_START, "").replace(SLOW_UPDATE_END, "")
    return _BLANK_RUN_RE.sub("\n\n", body).rstrip()


def replace_slow_update_field(skill: str, new_content: str) -> str:
    """Rewrite the slow-update region so it holds exactly *new_content*."""
    body = _without_slow_update_fields(skill)
    return f"{body}\n\n{SLOW_UPDATE_START}\n{new_content.strip()}\n{SLOW_UPDATE_END}\n"


# ── Longitudinal comparison ──────────────────────────────────────────────────


class CategorySpec(NamedTuple):
    """How one outcome bucket is counted and rendered."""

    key: str
    tally: str
    heading: str
    with_trajectories: bool


_IMPROVED = CategorySpec("improved", "Improved (wrong→right)", "Improvements (wrong→right)", True)
_REGRESSED = CategorySpec(
    "regressed",
    "Regressed (right→wrong)",
    "Regressions (right→wrong) — HIGHEST PRIORITY",
    True,
)
_PERSISTENT = CategorySpec(
    "persistent_fail",
    "Persistent failures (wrong→wrong)",
    "Persistent Failures (wrong→wrong)",
    True,
)
_STABLE = CategorySpec("stable_success", "Stable successes (right→right)", "Stable Successes (right→right)", False)

#: Summary tallies read chronologically: what got better, then what got worse.
_CATEGORIES: tuple[CategorySpec, ...] = (_IMPROVED, _REGRESSED, _PERSISTENT, _STABLE)

#: Detail sections are ordered by how much the optimizer should care.
_DETAIL_ORDER: tuple[CategorySpec, ...] = (_REGRESSED, _PERSISTENT, _IMPROVED, _STABLE)

_TASK_KEYS = ("question", "task_description", "instruction")


def _category_of(passed_before: bool, passed_now: bool) -> str:
    """Bucket one task by how its verdict moved between the two skill versions."""
    if passed_before == passed_now:
        return _STABLE.key if passed_now else _PERSISTENT.key
    return _IMPROVED.key if passed_now else _REGRESSED.key


def _task_label(item: dict, fallback: str) -> str:
    for key in _TASK_KEYS:
        if key in item:
            return item[key]
    return fallback


def _side(record: dict) -> dict:
    """Summarise one skill version's outcome for a single task."""
    passed = bool(record.get("hard", 0))
    answer = record.get("predicted_answer", record.get("answer", "N/A"))
    return {
        "hard": int(passed),
        "soft": float(record.get("soft", 0.0)),
        "predicted_answer": answer,
        "fail_reason": record.get("fail_reason", ""),
    }


def build_comparison_pairs(
    results_prev: List[dict],
    results_curr: List[dict],
    items: List[dict],
    prev_rollout_dir: str = "",
    curr_rollout_dir: str = "",
) -> List[dict]:
    """Join two rollout runs over the same items into per-task comparisons."""
    before = {str(row.get("id", "")): row for row in results_prev}
    after = {str(row.get("id", "")): row for row in results_curr}

    pairs: List[dict] = []
    for item in items:
        task_id = str(item.get("id", ""))
        prev = _side(before.get(task_id, {}))
        curr = _side(after.get(task_id, {}))
        pairs.append(
            {
                "id": task_id,
                "task": _task_label(item, task_id),
                "category": _category_of(bool(prev.get("hard")), bool(curr.get("hard"))),
                "prev": prev,
                "curr": curr,
                "prev_trajectory": read_trajectory(prev_rollout_dir, task_id) if prev_rollout_dir else "",
                "curr_trajectory": read_trajectory(curr_rollout_dir, task_id) if curr_rollout_dir else "",
            }
        )
    return pairs


def save_comparison_pairs(pairs: List[dict], out_path: str) -> None:
    """Persist comparison pairs to JSON, dropping transcripts to save space."""
    keep = ("id", "task", "category", "prev", "curr")
    slim = [{field: pair.get(field) for field in keep} for pair in pairs]
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")


def _bucket(pairs: List[dict]) -> Dict[str, List[dict]]:
    """Group pairs by category, keeping every known bucket present."""
    grouped: Dict[str, List[dict]] = {spec.key: [] for spec in _CATEGORIES}
    for pair in pairs:
        grouped.setdefault(str(pair.get("category", "")), []).append(pair)
    return grouped


def _summary_block(pairs: List[dict], grouped: Dict[str, List[dict]]) -> str:
    rows = [f"Total samples: {len(pairs)}"]
    rows += [f"- {spec.tally}: {len(grouped.get(spec.key, []))}" for spec in _CATEGORIES]
    return "## Longitudinal Comparison Summary\n" + "\n".join(rows) + "\n"


def _verdict(side: dict) -> str:
    return "PASS" if side.get("hard") else "FAIL"


def _entry_lines(pair: dict, with_trajectories: bool) -> List[str]:
    """Render one task's before/after comparison."""
    prev = pair.get("prev", {})
    curr = pair.get("curr", {})
    lines = [
        f"\n#### Task {pair.get('id', '')}: {pair.get('task', '')}\n"
        f"- Prev epoch: {_verdict(prev)} "
        f"(soft={float(prev.get('soft', 0.0)):.2f}) — answer: {str(prev.get('predicted_answer'))}\n"
        f"- Curr epoch: {_verdict(curr)} "
        f"(soft={float(curr.get('soft', 0.0)):.2f}) — answer: {str(curr.get('predicted_answer'))}"
    ]
    if curr.get("fail_reason"):
        lines.append(f"- Curr fail reason: {curr['fail_reason']}")
    if prev.get("fail_reason") and not prev.get("hard"):
        lines.append(f"- Prev fail reason: {prev['fail_reason']}")
    if with_trajectories:
        for label, key in (("Previous", "prev_trajectory"), ("Current", "curr_trajectory")):
            text = pair.get(key)
            if text:
                lines.append(f"\n**{label} epoch trajectory:**\n```\n{text}\n```")
    return lines


def format_comparison_text(pairs: List[dict]) -> str:
    """Render structured comparison pairs into an optimizer-readable report."""
    grouped = _bucket(pairs)

    blocks = [_summary_block(pairs, grouped)]
    for spec in _DETAIL_ORDER:
        entries = grouped.get(spec.key, [])
        if not entries:
            blocks.append(f"### {spec.heading}\n(none)\n")
            continue
        lines = [f"### {spec.heading}"]
        for pair in entries:
            lines.extend(_entry_lines(pair, spec.with_trajectories))
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


# ── Optimizer call ───────────────────────────────────────────────────────────


@dataclass
class SlowUpdateInputs:
    """Everything the epoch-boundary slow update needs to build its prompt."""

    skill_content: str
    results_prev: List[dict]
    results_curr: List[dict]
    items: List[dict]
    prev_skill: str = ""
    prev_slow_update_content: str = ""
    prev_rollout_dir: str = ""
    curr_rollout_dir: str = ""
    comparison_pairs: Optional[List[dict]] = None
    system_prompt: Optional[str] = None

    def pairs(self) -> List[dict]:
        """Reuse pre-built comparison pairs, or derive them from the rollouts."""
        if self.comparison_pairs is not None:
            return self.comparison_pairs
        return build_comparison_pairs(
            self.results_prev,
            self.results_curr,
            self.items,
            prev_rollout_dir=self.prev_rollout_dir,
            curr_rollout_dir=self.curr_rollout_dir,
        )

    def prior_guidance(self) -> str:
        text = (self.prev_slow_update_content or "").strip()
        return text or _NO_PRIOR_GUIDANCE


def run_slow_update(inputs: SlowUpdateInputs) -> Optional[Dict[str, Any]]:
    """Ask the optimizer for the next epoch's slow-update guidance.

    Returns ``None`` when the optimizer fails or produces no guidance.
    """
    message = UserMessage()
    message.section("Previous Epoch's Skill", inputs.prev_skill)
    message.section("Current Epoch's Skill", inputs.skill_content)
    message.section(
        "Previous Slow Update Guidance",
        "The following guidance was active during the current epoch. "
        "Reflect on its effectiveness before writing the new version.\n\n"
        f"{inputs.prior_guidance()}",
    )
    message.section(
        "Longitudinal Comparison (same 20 tasks, two skill versions)",
        format_comparison_text(inputs.pairs()),
    )

    system = inputs.system_prompt if inputs.system_prompt is not None else load_prompt("slow_update")
    reply = ask_optimizer(
        OptimizerCall(
            stage="slow_update",
            system=system,
            user=message.render(),
            max_tokens=COMPACT_TOKEN_BUDGET,
        ),
        trace=True,
    )
    body = reply.body or {}
    guidance = body.get("slow_update_content")
    if not guidance:
        return None
    return {
        "reasoning": str(body.get("reasoning", "")).strip(),
        "slow_update_content": str(guidance).strip(),
    }
