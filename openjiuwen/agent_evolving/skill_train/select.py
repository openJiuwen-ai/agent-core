# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ReflACT Select stage -- gradient clipping for skill edits.

The aggregate stage can hand back more candidate edits than the current edit
budget allows.  This stage asks the optimizer to rank the pool by expected
impact and keeps the top-L, which bounds the effective step size the same way
gradient clipping does during neural network training.

When the pool already fits the budget the patch is returned untouched, and any
optimizer trouble degrades to plain head truncation.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from openjiuwen.agent_evolving.skill_train.meta_skill import format_meta_skill_context
from openjiuwen.agent_evolving.skill_train.optimizer_io import (
    COMPACT_TOKEN_BUDGET,
    OptimizerCall,
    UserMessage,
    ask_optimizer,
)
from openjiuwen.agent_evolving.skill_train.prompts_loader import load_prompt
from openjiuwen.agent_evolving.skill_train.update_modes import (
    describe_item,
    get_payload_items,
    is_rewrite_mode,
    normalize_update_mode,
    payload_key,
    payload_label,
)

INDEX_KEY = "selected_indices"


def _pool_listing(items: Sequence[dict], mode: str) -> str:
    """Number every candidate so the optimizer can answer with indices."""
    return "\n".join(f"[{position}] {describe_item(item, mode)}" for position, item in enumerate(items))


def _ranking_message(skill_content: str, items: Sequence[dict], budget: int, mode: str) -> UserMessage:
    """Build the user message describing the pool and the budget."""
    unit = payload_label(mode)
    heading = f"{payload_label(mode, title=True)} Pool ({len(items)} {unit}, budget={budget})"
    body = (
        f"{_pool_listing(items, mode)}\n\n"
        f"Select the {budget} most important {unit}. "
        f"Return their 0-based indices in priority order."
    )
    return UserMessage().section("Current Skill", skill_content).section(heading, body)


def _picked(raw_indices: Any, items: Sequence[dict], budget: int) -> list[dict]:
    """Resolve optimizer-supplied indices into a bounded, de-duplicated list."""
    if not isinstance(raw_indices, Iterable) or isinstance(raw_indices, (str, bytes)):
        return []
    chosen: list[dict] = []
    used: set[int] = set()
    for index in raw_indices:
        if isinstance(index, int) and index not in used and 0 <= index < len(items):
            chosen.append(items[index])
            used.add(index)
        if len(chosen) >= budget:
            break
    return chosen


def _ranking_prompt(mode: str) -> str | None:
    """Load the ranking prompt for *mode*; ``None`` when no file ships for it."""
    name = "ranking_rewrite" if is_rewrite_mode(mode) else "ranking"
    try:
        return load_prompt(name)
    except FileNotFoundError:
        return None


def _annotated(patch: dict, items: list[dict], note: str, mode: str) -> dict:
    """Clone the patch header with a new payload and a provenance note."""
    return {
        "reasoning": patch.get("reasoning", "") + note,
        payload_key(mode): items,
    }


def rank_and_select(
    skill_content: str,
    patch: dict,
    max_edits: int,
    meta_skill_context: str = "",
    update_mode: str = "patch",
) -> dict:
    """Keep at most *max_edits* items from *patch*, ranked by the optimizer.

    Returns a :class:`~openjiuwen.agent_evolving.skill_train.types.Patch` dict;
    the optimizer-ranked variant additionally carries ``ranking_details``.
    """
    mode = normalize_update_mode(update_mode)
    pool = get_payload_items(patch, mode)
    if len(pool) <= max_edits:
        return patch

    system = _ranking_prompt(mode)
    if system is not None:
        message = _ranking_message(skill_content, pool, max_edits, mode)
        message.prepend(format_meta_skill_context(meta_skill_context))
        reply = ask_optimizer(
            OptimizerCall(
                stage="ranking",
                system=system,
                user=message.render(),
                max_tokens=COMPACT_TOKEN_BUDGET,
            )
        )
        body = reply.body or {}
        chosen = _picked(body.get(INDEX_KEY), pool, max_edits) if INDEX_KEY in body else []
        if chosen:
            note = f" [optimizer-ranked: selected {len(chosen)}/{len(pool)} {payload_label(mode)}]"
            ranked = _annotated(patch, chosen, note, mode)
            ranked["ranking_details"] = body
            return ranked

    note = f" [fallback truncated {len(pool)}->{max_edits} {payload_label(mode)}]"
    return _annotated(patch, pool[:max_edits], note, mode)
