# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""L3 (team-composition/spawning-strategy) reflection: ACE-style Generator/
Reflector/Curator mapped onto one already-completed episode at a time.

- Generator  = one already-completed episode (nothing to build).
- Reflector  = ``propose_actions``: given THIS episode's evidence and the
  team's CURRENT playbook for a (mode, task_category), extract candidate
  structural insights.
- Curator    = the SAME call also decides, per candidate, whether it
  matches an existing playbook item (-> reinforce/contradict its counters,
  applied by ``apply_actions``) or is genuinely new (-> add a fresh item).
  This match-against-current-state step is what makes recurrence
  detectable one episode at a time, without ever needing multiple episodes
  in one prompt.

``apply_actions`` never deletes or rewrites description text — only
counters/evidence move on a reinforce/contradict, and a "new" item's
description is set once at creation. Deprecating a consistently-
contradicted item and merging near-duplicates are both deliberately out of
scope here (a separate, occasional maintenance pass owns those, since they
are higher-stakes judgment calls than a mechanical counter bump).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.foundation.llm import JsonOutputParser, Model, SystemMessage, UserMessage
from openjiuwen.extensions.context_evolver.offline_memory import bank_io
from openjiuwen.extensions.context_evolver.offline_memory.prompts import L3_SYSTEM_PROMPT, build_l3_user_prompt

EVIDENCE_CAP = 10  # max sample_ids kept per playbook item


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class L3ActionsResult(BaseModel):
    # Each action's field set varies by "verdict" (reinforce/contradict/new
    # carry different required fields -- see L3_SYSTEM_PROMPT) -- kept as
    # loosely-typed dicts rather than a discriminated union, consumed via
    # .get() in apply_actions exactly as the raw LLM JSON would be.
    actions: list[dict[str, Any]] = Field(default_factory=list)


async def propose_actions(
    model: Model,
    *,
    task_category: str,
    mode: str,
    episode_evidence_block: str,
    playbook_block: str,
    retries: int = 3,
) -> L3ActionsResult:
    """Call ``model`` for one episode's reflect+curate step against the
    current playbook. Returns the raw action list; ``apply_actions`` does
    the actual playbook mutation.
    """
    messages = [
        SystemMessage(content=L3_SYSTEM_PROMPT),
        UserMessage(
            content=build_l3_user_prompt(
                task_category=task_category,
                mode=mode,
                episode_evidence_block=episode_evidence_block,
                playbook_block=playbook_block,
            )
        ),
    ]
    parser = JsonOutputParser()
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            response = await model.invoke(messages=messages)
            res = await parser.parse(response.content)
            return L3ActionsResult.model_validate(res)
        except (json.JSONDecodeError, ValidationError) as exc:
            last_err = exc
            memory_logger.warning("L3 propose_actions call failed on attempt %d/%d: %s", attempt + 1, retries, exc)
    raise_error(
        StatusCode.TOOLCHAIN_EVOLVING_MEMORY_LLM_GENERATION_EXECUTION_ERROR,
        reason=f"L3 propose_actions failed after {retries} attempts: {last_err}",
    )


# Without this, recommended_role_type is free text the L3 reflector can
# invent independently of role_taxonomy.yaml -- a leader that spawns a
# member exactly matching the recommendation (e.g. "fact_researcher")
# still gets it classified into whatever existing bucket the classifier
# thinks is closest (e.g. "researcher"), so that member's own L2 lookup
# silently returns generic content instead of anything actually tied to
# this recommendation. Keeping the two vocabularies in sync closes that
# gap for future episodes.
def _sync_recommended_role_type(role_type: str, description: str, taxonomy_path: Path) -> None:
    """Add a new agent_change item's recommended_role_type to the L2
    classification taxonomy if it's not already there."""
    if not role_type:
        return
    taxonomy = bank_io.load_yaml(taxonomy_path)
    if role_type in taxonomy:
        return
    taxonomy[role_type] = description.strip() or "Recommended role type (auto-synced from playbook item)."
    bank_io.save_yaml(taxonomy_path, taxonomy)
    memory_logger.info("synced new recommended_role_type %r into %s", role_type, taxonomy_path)


def apply_actions(
    playbook: dict, actions: list[dict], sample_id: str, task_category: str, taxonomy_path: Path | None = None
) -> tuple[dict, dict[str, int]]:
    """Mutate ``playbook`` in place per ``actions`` (from ``propose_actions``).
    Pure bank-state mutation — no LLM calls, no knowledge of episode/trace
    format. Counters + evidence + last_updated only — never description
    text (that's deliberately left untouched here; see module docstring).
    """
    items = playbook.setdefault("items", {})
    counts = {"reinforced": 0, "contradicted": 0, "added": 0, "skipped": 0}
    now = _now()

    for act in actions:
        verdict = act.get("verdict")

        if verdict in ("reinforce", "contradict"):
            item_id = act.get("item_id")
            item = items.get(item_id)
            if item is None:
                memory_logger.warning("action references unknown item_id %r, skipping", item_id)
                counts["skipped"] += 1
                continue
            key = "support_count" if verdict == "reinforce" else "contradict_count"
            item[key] = item.get(key, 0) + 1
            item["evidence_sample_ids"] = (item.get("evidence_sample_ids", []) + [sample_id])[-EVIDENCE_CAP:]
            item["last_updated"] = now
            counts["reinforced" if verdict == "reinforce" else "contradicted"] += 1

        elif verdict == "new":
            item_id = bank_io.new_item_id(items)
            items[item_id] = {
                "category": act.get("category", ""),
                "description": act.get("description", ""),
                # agent_change fields: a spawning-strategy heuristic
                # (trigger_condition -> recommended_role_type), NOT a
                # literal add/remove of a permanent named agent — see
                # prompts.py's L3_SYSTEM_PROMPT for why that framing
                # doesn't fit either mode.
                "action": act.get("action", ""),
                "recommended_role_type": act.get("recommended_role_type", ""),
                "trigger_condition": act.get("trigger_condition", ""),
                "expertise": act.get("expertise", ""),
                "support_count": 1,
                "contradict_count": 0,
                "evidence_sample_ids": [sample_id],
                "task_category": task_category,
                "first_seen": now,
                "last_updated": now,
                "status": "active",
            }
            if items[item_id]["category"] == "agent_change" and taxonomy_path is not None:
                _sync_recommended_role_type(
                    items[item_id]["recommended_role_type"],
                    items[item_id]["expertise"] or items[item_id]["description"],
                    taxonomy_path,
                )
            counts["added"] += 1

        else:
            memory_logger.warning("dropping action with unknown verdict %r: %s", verdict, act)
            counts["skipped"] += 1

    return playbook, counts


# ---------------------------------------------------------------------------
# Playbook maintenance: deprecation (deterministic) + dedup (one LLM call).
# The slow, deliberate half of the ACE-style design above, meant to run on
# an occasional cadence (not per-episode) rather than automatically —
# ``apply_actions`` only ever does cheap, mechanical counter bumps and never
# deletes or rewrites description text; the higher-stakes judgment calls
# (deprecating a consistently-contradicted item, merging near-duplicates)
# live here instead.

MIN_SAMPLES_FOR_DEPRECATION = 3

_DEDUP_SYSTEM_PROMPT = """You are consolidating a playbook of structural team-improvement insights \
for a multi-agent system. You are shown every currently active item (id, category, description, \
support/contradict counts). Some items may be near-duplicates — the same underlying insight, phrased \
differently, possibly proposed independently across different episodes.

Group items that represent the SAME underlying insight (same category, same core claim) — even if \
worded differently or with slightly different specifics — into merge groups. Do NOT merge items that \
are genuinely different insights just because they share a category.

For each merge group, pick the clearest existing id as "keep_id", list the OTHER ids in "merge_ids", \
and write one consolidated "merged_description" capturing the shared insight (prefer the clearer or \
more complete existing wording over inventing new phrasing).

If nothing should be merged, return an empty "merges" list — this is expected and fine when the \
playbook is already clean; do not force a merge to produce output.

Respond with a JSON object:
{"merges": [{"keep_id": "...", "merge_ids": ["...", "..."], "merged_description": "..."}]}
"""


class L3MergeGroup(BaseModel):
    keep_id: str = Field(default="")
    merge_ids: list[str] = Field(default_factory=list)
    merged_description: str = Field(default="")


class L3MergesResult(BaseModel):
    merges: list[L3MergeGroup] = Field(default_factory=list)


def format_items_for_dedup(items: dict) -> str:
    active = {k: v for k, v in items.items() if v.get("status", "active") == "active"}
    if not active:
        return "(no active items)"
    lines = [
        f"- id={item_id} [{item.get('category', '')}] "
        f"support={item.get('support_count', 0)} contradict={item.get('contradict_count', 0)}: "
        f"{item.get('description', '')}"
        for item_id, item in sorted(active.items())
    ]
    return "\n".join(lines)


async def propose_merges(model: Model, *, task_category: str, items_block: str, retries: int = 3) -> L3MergesResult:
    """Call ``model`` to group near-duplicate active playbook items into
    merge proposals. ``apply_merges`` does the actual playbook mutation.
    """
    messages = [
        SystemMessage(content=_DEDUP_SYSTEM_PROMPT),
        UserMessage(content=f"Task category: {task_category}\n\nActive items:\n{items_block}"),
    ]
    parser = JsonOutputParser()
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            response = await model.invoke(messages=messages)
            res = await parser.parse(response.content)
            return L3MergesResult.model_validate(res)
        except (json.JSONDecodeError, ValidationError) as exc:
            last_err = exc
            memory_logger.warning("L3 propose_merges call failed on attempt %d/%d: %s", attempt + 1, retries, exc)
    raise_error(
        StatusCode.TOOLCHAIN_EVOLVING_MEMORY_LLM_GENERATION_EXECUTION_ERROR,
        reason=f"L3 propose_merges failed after {retries} attempts: {last_err}",
    )


def apply_deprecation_pass(items: dict) -> list[str]:
    """Deterministic, no LLM, no cost: an active item with
    contradict_count >= support_count AND at least MIN_SAMPLES_FOR_DEPRECATION
    total data points gets status="deprecated". A single contradiction can
    never do this alone -- apply_actions only ever increments
    contradict_count and leaves the item active; this is the one place that
    count is actually acted on.
    """
    deprecated = []
    for item_id, item in items.items():
        if item.get("status", "active") != "active":
            continue
        support = item.get("support_count", 0)
        contradict = item.get("contradict_count", 0)
        if contradict >= support and (support + contradict) >= MIN_SAMPLES_FOR_DEPRECATION:
            item["status"] = "deprecated"
            item["last_updated"] = _now()
            deprecated.append(item_id)
    return deprecated


def _merge_value(merge: Any, key: str, default: Any = None) -> Any:
    if isinstance(merge, dict):
        return merge.get(key, default)
    return getattr(merge, key, default)


def apply_merges(items: dict, merges: list[dict | L3MergeGroup]) -> list[tuple[str, list[str]]]:
    """A merge sums the group's counters, unions evidence_sample_ids, and
    adopts the LLM's consolidated description -- the ONE place in this
    whole pipeline a description is allowed to change after creation,
    since this is a deliberate, reviewed step rather than a per-episode
    side effect. Merged-away items are set to status="deprecated" with a
    merged_into pointer, never hard-deleted, so history stays traceable.
    """
    applied = []
    for merge in merges:
        keep_id = _merge_value(merge, "keep_id")
        keep_item = items.get(keep_id)
        if keep_item is None or keep_item.get("status", "active") != "active":
            memory_logger.warning("merge keep_id %r not found or not active, skipping this group", keep_id)
            continue

        valid_merge_ids = []
        for merge_id in _merge_value(merge, "merge_ids", []):
            if merge_id == keep_id:
                continue
            merged_item = items.get(merge_id)
            if merged_item is None or merged_item.get("status", "active") != "active":
                continue

            keep_item["support_count"] = keep_item.get("support_count", 0) + merged_item.get("support_count", 0)
            keep_item["contradict_count"] = keep_item.get("contradict_count", 0) + merged_item.get(
                "contradict_count", 0
            )
            seen = set(keep_item.get("evidence_sample_ids", []))
            for sid in merged_item.get("evidence_sample_ids", []):
                if sid not in seen:
                    keep_item.setdefault("evidence_sample_ids", []).append(sid)
                    seen.add(sid)
            keep_item["evidence_sample_ids"] = keep_item["evidence_sample_ids"][-EVIDENCE_CAP:]

            merged_item["status"] = "deprecated"
            merged_item["merged_into"] = keep_id
            merged_item["last_updated"] = _now()
            valid_merge_ids.append(merge_id)

        if valid_merge_ids:
            new_desc = (_merge_value(merge, "merged_description") or "").strip()
            if new_desc:
                keep_item["description"] = new_desc
            keep_item["last_updated"] = _now()
            applied.append((keep_id, valid_merge_ids))

    return applied
