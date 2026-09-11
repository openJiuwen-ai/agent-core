# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ReflACT Aggregate stage -- hierarchical merging of independent patches.

The Reflect stage emits one patch per minibatch.  Aggregate folds that pile
down to a single coherent patch by repeatedly merging small groups through the
optimizer, level by level, until one patch remains.  Failure-driven patches and
success-driven patches are folded separately first, then combined with the
failure side taking priority.

Every optimizer hop is best-effort: when the provider errors out or answers
with something unusable, the affected level degrades to plain concatenation and
the fold continues.
"""

from __future__ import annotations

import json
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import NamedTuple

from openjiuwen.agent_evolving.skill_train.meta_skill import format_meta_skill_context
from openjiuwen.agent_evolving.skill_train.optimizer_io import (
    OptimizerCall,
    OptimizerReply,
    UserMessage,
    ask_optimizer,
    token_budget,
)
from openjiuwen.agent_evolving.skill_train.prompts_loader import load_prompt
from openjiuwen.agent_evolving.skill_train.update_modes import (
    FULL_REWRITE_MINIBATCH_MODE,
    PATCH_MODE,
    REWRITE_MODE,
    get_payload_items,
    is_full_rewrite_minibatch_mode,
    normalize_update_mode,
    payload_key,
    payload_label,
)
from openjiuwen.core.common.logging import logger

#: Merge prompts come in one flavour per update-mode family.
_PROMPT_SUFFIX_BY_MODE = {
    PATCH_MODE: "",
    REWRITE_MODE: "_rewrite",
    FULL_REWRITE_MINIBATCH_MODE: "_full_rewrite",
}

_CRASH_WARNING = "Optimizer call or parsing failed during {phase}; using fallback"
_UNUSABLE_WARNING = "Optimizer returned unusable output during {phase}; using fallback"

#: Batches smaller than two cannot shrink a level, so the fold clamps to two.
MIN_FOLD_WIDTH = 2


class MergePrompts(NamedTuple):
    """The three system prompts one aggregate run needs."""

    failure: str
    success: str
    final: str


class GroupCaption(NamedTuple):
    """Wording of the final combine step, which differs per update mode."""

    heading: str
    from_failures: str
    from_successes: str


_PATCH_CAPTION = GroupCaption(
    "Two pre-merged patch groups to combine",
    "Group 1 (failure-driven, HIGH priority): {count} edits",
    "Group 2 (success-driven, lower priority): {count} edits",
)
_CANDIDATE_CAPTION = GroupCaption(
    "Two pre-merged candidate groups to combine",
    "Group 1 (from failed trajectories): {count} {unit}",
    "Group 2 (from successful trajectories): {count} {unit}",
)


def _load_merge_prompts(mode: str) -> MergePrompts:
    """Load the failure/success/final prompt trio matching *mode*."""
    suffix = _PROMPT_SUFFIX_BY_MODE.get(mode, "")
    return MergePrompts(*(load_prompt(f"merge_{role}{suffix}") for role in MergePrompts._fields))


@dataclass(frozen=True)
class FoldContext:
    """Everything held constant while folding one group of patches."""

    skill_content: str
    system_prompt: str
    update_mode: str
    batch_size: int
    workers: int
    verbose: bool
    label: str
    meta_skill_context: str

    @property
    def key(self) -> str:
        return payload_key(self.update_mode)

    @property
    def width(self) -> int:
        return max(MIN_FOLD_WIDTH, self.batch_size)

    def call(self, message: UserMessage) -> OptimizerReply:
        """Run one optimizer hop with this context's prompt and token budget."""
        return ask_optimizer(
            OptimizerCall(
                stage="merge",
                system=self.system_prompt,
                user=message.prepend(format_meta_skill_context(self.meta_skill_context)).render(),
                max_tokens=token_budget(is_full_rewrite_minibatch_mode(self.update_mode)),
            )
        )


def _warn_fallback(reply: OptimizerReply, phase: str) -> None:
    """Explain which half of the round trip forced a fallback."""
    template = _CRASH_WARNING if reply.crashed else _UNUSABLE_WARNING
    warnings.warn(template.format(phase=phase), stacklevel=3)


def _concatenated(patches: list[dict], mode: str, level: int) -> dict:
    """Fallback merge: keep every item, tagging anything not already tagged."""
    pooled: list[dict] = []
    for patch in patches:
        for item in get_payload_items(patch, mode):
            item.setdefault("merge_level", level)
            pooled.append(item)
    return {"reasoning": "fallback concatenation", payload_key(mode): pooled}


def _fuse(ctx: FoldContext, patches: list[dict], level: int) -> dict:
    """Merge one group of sibling patches into a single patch."""
    message = UserMessage()
    message.section("Current Skill", ctx.skill_content)
    message.section(
        f"Patches to merge ({len(patches)} total, merge level {level})",
        json.dumps(patches, ensure_ascii=False, indent=2),
    )
    reply = ctx.call(message)
    items = reply.field_list(ctx.key)
    if items is None:
        _warn_fallback(reply, "batch merge")
        return _concatenated(patches, ctx.update_mode, level)
    for item in items:
        item["merge_level"] = level
    return reply.body or {}


class _Group(NamedTuple):
    """One slice of the current level: where it started and what it holds."""

    offset: int
    patches: list[dict]


def _slice_level(patches: list[dict], width: int) -> list[_Group]:
    """Cut the current level into consecutive groups of at most *width*."""
    groups: list[_Group] = []
    for start in range(0, len(patches), width):
        stop = start + width
        groups.append(_Group(start, patches[start:stop]))
    return groups


def _fold(ctx: FoldContext, patches: list[dict], level: int = 0) -> dict:
    """Recursively halve the patch pool until a single patch is left."""
    if not patches:
        return {"reasoning": "no patches", ctx.key: []}
    if len(patches) == 1:
        return patches[0]

    level += 1
    groups = _slice_level(patches, ctx.width)
    if ctx.verbose:
        logger.info(
            "[aggregate %s] level=%s  %s patches → %s batches (parallel, batch_size=%s)",
            ctx.label,
            level,
            len(patches),
            len(groups),
            ctx.width,
        )

    merged: list[dict] = [group.patches[0] for group in groups]
    todo = [idx for idx, group in enumerate(groups) if len(group.patches) > 1]
    if todo:
        with ThreadPoolExecutor(max_workers=ctx.workers) as pool:
            futures = {pool.submit(_fuse, ctx, groups[idx].patches, level): idx for idx in todo}
            for future in as_completed(futures):
                idx = futures[future]
                merged[idx] = future.result()
                if ctx.verbose:
                    group = groups[idx]
                    logger.info(
                        "[aggregate %s] level=%s batch [%s:%s] → 1 patch (%s %s)",
                        ctx.label,
                        level,
                        group.offset,
                        group.offset + len(group.patches),
                        len(get_payload_items(merged[idx], ctx.update_mode)),
                        payload_label(ctx.update_mode),
                    )

    return _fold(ctx, merged, level)


def _combine_message(ctx: FoldContext, failure_merged: dict, success_merged: dict) -> UserMessage:
    """Build the user message for the final failure-vs-success combine."""
    unit = payload_label(ctx.update_mode)
    whole_document = is_full_rewrite_minibatch_mode(ctx.update_mode)
    caption = _CANDIDATE_CAPTION if whole_document else _PATCH_CAPTION
    n_failure = len(get_payload_items(failure_merged, ctx.update_mode))
    n_success = len(get_payload_items(success_merged, ctx.update_mode))
    body = "\n".join(
        [
            caption.from_failures.format(count=n_failure, unit=unit),
            caption.from_successes.format(count=n_success, unit=unit),
            "",
            json.dumps([failure_merged, success_merged], ensure_ascii=False, indent=2),
        ]
    )
    return UserMessage().section("Current Skill", ctx.skill_content).section(caption.heading, body)


@dataclass(frozen=True)
class MergeSettings:
    """Tunables shared by both halves of one aggregate run."""

    batch_size: int = 8
    workers: int = 16
    verbose: bool = True
    update_mode: str = PATCH_MODE
    meta_skill_context: str = ""


def merge_patches(
    skill_content: str,
    failure_patches: list[dict],
    success_patches: list[dict],
    settings: MergeSettings | None = None,
) -> dict:
    """Fold failure and success patches into one patch, failure side first.

    Returns a merged :class:`~openjiuwen.agent_evolving.skill_train.types.Patch`
    dict carrying the mode's payload list plus a ``reasoning`` string.
    """
    opts = settings or MergeSettings()
    mode = normalize_update_mode(opts.update_mode)
    if opts.verbose:
        logger.info(
            "[3/6 AGGREGATE] failure=%s success=%s (parallel, workers=%s)",
            len(failure_patches),
            len(success_patches),
            opts.workers,
        )

    prompts = _load_merge_prompts(mode)

    def _context(system_prompt: str, label: str) -> FoldContext:
        return FoldContext(
            skill_content=skill_content,
            system_prompt=system_prompt,
            update_mode=mode,
            batch_size=opts.batch_size,
            workers=opts.workers,
            verbose=opts.verbose,
            label=label,
            meta_skill_context=opts.meta_skill_context,
        )

    failure_ctx = _context(prompts.failure, "failure")
    failure_merged = _fold(failure_ctx, failure_patches)
    success_merged = _fold(_context(prompts.success, "success"), success_patches)

    failure_items = get_payload_items(failure_merged, mode)
    success_items = get_payload_items(success_merged, mode)
    if not failure_items and not success_items:
        return {"reasoning": "no updates from either group", payload_key(mode): []}
    if not success_items:
        return failure_merged
    if not failure_items:
        return success_merged

    final_ctx = _context(prompts.final, "final")
    reply = final_ctx.call(_combine_message(final_ctx, failure_merged, success_merged))
    items = reply.field_list(payload_key(mode))
    if items is None:
        _warn_fallback(reply, "final merge")
        return {
            "reasoning": "fallback: failure first, then success",
            payload_key(mode): failure_items + success_items,
        }

    if opts.verbose:
        logger.info(
            "[aggregate final] %s+%s → %s %s",
            len(failure_items),
            len(success_items),
            len(items),
            payload_label(mode),
        )
    return reply.body or {}
