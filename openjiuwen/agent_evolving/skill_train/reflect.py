# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ReflACT Reflect stage -- environment-agnostic minibatch trajectory analysis.

Rather than asking the optimizer about one trajectory at a time, trajectories
are grouped into minibatches of size ``M`` and analysed together, which is to
per-sample reflection what minibatch SGD is to per-sample SGD.

The stage is driven by two small descriptors:

``AnalystRole``
    What distinguishes the failure-side analyst from the success-side one:
    prompt file, transcript heading, and the ``source_type`` stamped on the
    resulting patch.
``MinibatchJob``
    One unit of work -- a role plus the trajectories assigned to it.  Jobs are
    planned up front, resumed from disk when their patch file already exists,
    and otherwise dispatched to a thread pool.

Public API
----------
- :func:`fmt_trajectory`               -- render one conversation as text
- :func:`fmt_minibatch_trajectories`   -- render a whole minibatch for the analyst
- :func:`run_error_analyst_minibatch`  -- one optimizer call over a group of failures
- :func:`run_success_analyst_minibatch`-- one optimizer call over a group of successes
- :func:`run_minibatch_reflect`        -- plan, run and persist the whole stage
"""

from __future__ import annotations

import json
import os
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, NamedTuple, Sequence

from openjiuwen.agent_evolving.skill_train.meta_skill import format_meta_skill_context
from openjiuwen.agent_evolving.skill_train.optimizer_io import (
    OptimizerCall,
    UserMessage,
    ask_optimizer,
    token_budget,
)
from openjiuwen.agent_evolving.skill_train.prompts_loader import load_prompt
from openjiuwen.agent_evolving.skill_train.skill_aware import (
    augment_error_prompt,
    augment_success_prompt,
    extract_appendix_notes,
    get_skill_aware_appendix_source,
    is_skill_aware_enabled,
)
from openjiuwen.agent_evolving.skill_train.trajectory_text import (
    conversation_path,
    load_conversation,
    render_conversation,
)
from openjiuwen.agent_evolving.skill_train.update_modes import (
    FULL_REWRITE_MINIBATCH_MODE,
    REWRITE_MODE,
    get_payload_items,
    is_full_rewrite_minibatch_mode,
    normalize_update_mode,
    payload_label,
    truncate_payload,
)
from openjiuwen.core.common.logging import logger

# Prompt variants tried before falling back to the mode-agnostic prompt file.
_PROMPT_SUFFIX_BY_MODE = {
    FULL_REWRITE_MINIBATCH_MODE: "_full_rewrite",
    REWRITE_MODE: "_rewrite",
}

_FULL_REWRITE_FORMAT_NOTE = (
    "Produce one complete replacement skill candidate for this minibatch. "
    "Do not output edits, patches, or revise suggestions."
)

_TASK_KEYS = ("task_description", "instruction", "question")
_TASK_TYPE_KEYS = ("task_type", "instruction_type")


# ── Trajectory rendering ─────────────────────────────────────────────────────


def fmt_trajectory(conversation: list[dict], max_chars: int | None = None) -> str:
    """Render one conversation into analyst-readable bracketed lines.

    ``max_chars`` is kept for backward compatibility and ignored: the analyst
    is always shown the full transcript.
    """
    del max_chars
    return render_conversation(conversation, keep_scalars=True)


@dataclass(frozen=True)
class Attachment:
    """One optional ``#### ...`` block appended after a trajectory header.

    The value is looked up on the rollout row first and read from
    ``<item_dir>/<filename>`` only when the row does not carry it.
    """

    heading: str
    item_key: str = ""
    filename: str = ""
    env_flag: str = ""
    strip: bool = False

    def enabled(self) -> bool:
        """Environment-gated attachments stay off unless their flag is ``1``."""
        return not self.env_flag or os.environ.get(self.env_flag, "0") == "1"

    def resolve(self, item: dict, item_dir: str) -> str:
        """Return the rendered block body, or an empty string when absent."""
        raw = item.get(self.item_key) if self.item_key else None
        if not raw and self.filename:
            path = os.path.join(item_dir, self.filename)
            if os.path.exists(path):
                with open(path, encoding="utf-8") as handle:
                    raw = handle.read()
        text = str(raw or "")
        return text.strip() if self.strip else text


#: Order matters -- this is the order the analyst sees the context in.
_ATTACHMENTS: tuple[Attachment, ...] = (
    Attachment("Hidden Reference", item_key="reference_text", strip=True),
    Attachment("Target System Prompt", item_key="target_system_prompt", filename="target_system_prompt.txt"),
    Attachment("Target User Prompt", item_key="target_user_prompt", filename="target_user_prompt.txt"),
    Attachment(
        "Codex Trace Summary",
        item_key="codex_trace_summary",
        filename="codex_trace_summary.txt",
        env_flag="REFLACT_CODEX_TRACE_TO_OPTIMIZER",
    ),
    Attachment("Codex Trace Steps", item_key="codex_probe_trace_steps", strip=True),
    # Claude Code exec backend: the SDK session trace is persisted separately,
    # so the analyst sees real tool activity rather than only the final answer.
    Attachment(
        "Claude Trace Steps",
        filename="claude_trace_steps.txt",
        env_flag="REFLACT_CLAUDE_TRACE_TO_OPTIMIZER",
        strip=True,
    ),
    Attachment("Spreadsheet Preview", item_key="spreadsheet_preview", filename="spreadsheet_preview.txt"),
)


def _first_value(item: dict, keys: Sequence[str]) -> str:
    """Return the first non-empty value among *keys*, else an empty string."""
    for key in keys:
        value = item.get(key)
        if value:
            return str(value)
    return ""


def _trajectory_header(item: dict, item_dir: str, position: int, task_id: str) -> str:
    """Build the metadata header that precedes one rendered trajectory."""
    rows = [
        f"### Trajectory {position} (id={task_id})",
        f"Task: {_first_value(item, _TASK_KEYS)}",
        f"Task type: {_first_value(item, _TASK_TYPE_KEYS)}",
    ]
    reason = item.get("fail_reason", "")
    if reason:
        rows.append(f"Failure reason: {reason}")
    rows.append(f"Steps: {item.get('n_turns', '?')}")

    header = "\n".join(rows) + "\n"
    for attachment in _ATTACHMENTS:
        if not attachment.enabled():
            continue
        body = attachment.resolve(item, item_dir)
        if body:
            header += f"\n#### {attachment.heading}\n{body}\n"
    return header


def fmt_minibatch_trajectories(items: list[dict], prediction_dir: str) -> str:
    """Render every trajectory of one minibatch into a single analyst message.

    Rows without a stored ``conversation.json`` (or with an empty one) are
    skipped; the remainder are separated by ``---``.
    """
    blocks: list[str] = []
    for position, item in enumerate(items, 1):
        task_id = str(item.get("id", ""))
        conversation = load_conversation(prediction_dir, task_id)
        if not conversation:
            continue
        item_dir = str(conversation_path(prediction_dir, task_id).parent)
        header = _trajectory_header(item, item_dir, position, task_id)
        blocks.append(header + "\n" + render_conversation(conversation, keep_scalars=True))
    return "\n\n---\n\n".join(blocks)


# ── Analyst roles ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AnalystRole:
    """Everything that differs between the failure and success analysts."""

    source_type: str
    prompt_name: str
    heading: str
    augment: Callable[[str], str]


FAILURE_ROLE = AnalystRole("failure", "analyst_error", "Failed Trajectories", augment_error_prompt)
SUCCESS_ROLE = AnalystRole("success", "analyst_success", "Successful Trajectories", augment_success_prompt)


@dataclass
class AnalystRequest:
    """Inputs for a single minibatch analyst call."""

    skill_content: str
    items: list[dict]
    prediction_dir: str
    edit_budget: int = 4
    system_prompt: str | None = None
    memory_text: str = ""
    meta_skill_context: str = ""
    update_mode: str = "patch"
    skill_aware: bool = False
    emit_notes: bool = True


def _resolve_system_prompt(custom: str | None, base_name: str, mode: str) -> str:
    """Return *custom* when supplied, else the best prompt file for *mode*."""
    if custom is not None:
        return custom
    suffix = _PROMPT_SUFFIX_BY_MODE.get(normalize_update_mode(mode), "")
    if suffix:
        try:
            return load_prompt(base_name + suffix)
        except FileNotFoundError:
            pass
    return load_prompt(base_name)


def _budget_section(message: UserMessage, mode: str, edit_budget: int) -> None:
    """Tell the analyst how much it is allowed to emit for this minibatch."""
    if is_full_rewrite_minibatch_mode(mode):
        message.section("Update Format", _FULL_REWRITE_FORMAT_NOTE)
        return
    plural = payload_label(mode)
    heading = f"{payload_label(mode, title=True)} Budget"
    message.section(heading, f"Produce at most L={edit_budget} {plural}.")


def _analyst_message(role: AnalystRole, request: AnalystRequest, transcript: str) -> str:
    """Assemble the analyst user message for one minibatch."""
    mode = normalize_update_mode(request.update_mode)
    message = UserMessage().section("Current Skill", request.skill_content)
    _budget_section(message, mode, request.edit_budget)
    if request.memory_text.strip():
        message.section("Previous Steps in This Epoch", request.memory_text)
    message.verbatim(format_meta_skill_context(request.meta_skill_context))
    return message.section(f"{role.heading} ({len(request.items)} total)", transcript).render()


def _run_analyst(role: AnalystRole, request: AnalystRequest) -> dict | None:
    """Run one minibatch analyst call and normalise its reply into a patch."""
    mode = normalize_update_mode(request.update_mode)
    whole_document = is_full_rewrite_minibatch_mode(mode)
    notes_wanted = request.skill_aware and request.emit_notes

    system = _resolve_system_prompt(request.system_prompt, role.prompt_name, mode)
    # Skill-aware reflection augments whichever prompt was resolved, so both
    # env-specific and generic analyst prompts pick up the defect/lapse
    # instruction. With the toggle off this is byte-identical to the baseline.
    if notes_wanted and not whole_document:
        system = role.augment(system)

    transcript = fmt_minibatch_trajectories(request.items, request.prediction_dir)
    if not transcript.strip():
        return None

    reply = ask_optimizer(
        OptimizerCall(
            stage="analyst",
            system=system,
            user=_analyst_message(role, request, transcript),
            max_tokens=token_budget(whole_document),
        ),
        trace=True,
    )
    body = reply.body
    if not body:
        return None

    notes = extract_appendix_notes(body) if notes_wanted else []
    if "patch" in body:
        body["source_type"] = role.source_type
        if not whole_document:
            truncate_payload(body["patch"], request.edit_budget, mode)
        if notes_wanted:
            body["appendix_notes"] = notes
        return body

    # Skill-aware runs may legitimately yield only execution-lapse notes with
    # no body edit. Emit a no-op patch so the notes still reach the trainer;
    # empty edit lists are dropped further down the body pipeline.
    if notes:
        return {
            "source_type": role.source_type,
            "patch": {"reasoning": "execution-lapse only", "edits": []},
            "appendix_notes": notes,
        }
    return None


def run_error_analyst_minibatch(request: AnalystRequest) -> dict | None:
    """Analyse a minibatch of failed trajectories in one optimizer call."""
    return _run_analyst(FAILURE_ROLE, request)


def run_success_analyst_minibatch(request: AnalystRequest) -> dict | None:
    """Analyse a minibatch of successful trajectories in one optimizer call."""
    return _run_analyst(SUCCESS_ROLE, request)


# ── Stage planning and execution ─────────────────────────────────────────────


class MinibatchJob(NamedTuple):
    """One planned analyst call: a role, its slot index and its trajectories."""

    role: AnalystRole
    slot: str
    ordinal: int
    items: list[dict]

    @property
    def tag(self) -> str:
        """Stable file/log name, also used to detect resumable work."""
        return f"minibatch_{self.slot}_{self.ordinal:03d}"


@dataclass
class ReflectRequest:
    """Inputs for the whole reflect stage."""

    results: list[dict]
    skill_content: str
    prediction_dir: str
    patches_dir: str
    workers: int
    failure_only: bool
    minibatch_size: int = 8
    edit_budget: int = 4
    random_seed: int | None = None
    error_system: str | None = None
    success_system: str | None = None
    rejection_context: str = ""
    trajectory_memory_context: str = ""
    step_buffer_context: str = ""
    meta_skill_context: str = ""
    update_mode: str = "patch"
    skill_aware_reflection: bool | None = None
    skill_aware_appendix_source: str | None = None

    def failure_memory(self) -> str:
        """Step-buffer text for the failure analyst (with legacy fallbacks)."""
        base = self.step_buffer_context or self.rejection_context or ""
        if not self.trajectory_memory_context:
            return base
        if not base:
            return self.trajectory_memory_context
        return f"{base}\n{self.trajectory_memory_context}"

    def success_memory(self) -> str:
        """Step-buffer text for the success analyst (with legacy fallback)."""
        return self.step_buffer_context or self.trajectory_memory_context or ""


@dataclass
class StagePlan:
    """The jobs a reflect stage intends to run, plus their input tallies."""

    jobs: list[MinibatchJob] = field(default_factory=list)
    n_failures: int = 0
    n_successes: int = 0

    def counts_by_slot(self, slot: str) -> int:
        return sum(1 for job in self.jobs if job.slot == slot)


def _chunk(rows: list[dict], size: int) -> list[list[dict]]:
    """Split *rows* into consecutive chunks of at most *size* entries."""
    width = max(1, int(size))
    chunks: list[list[dict]] = []
    for start in range(0, len(rows), width):
        stop = start + width
        chunks.append(rows[start:stop])
    return chunks


def _ordered(rows: list[dict], seed: int | None) -> list[dict]:
    """Deterministically shuffle when seeded, else keep the incoming order."""
    shuffled = list(rows)
    if seed is not None:
        random.Random(seed).shuffle(shuffled)
    return shuffled


def _is_failure(row: dict) -> bool:
    return not row.get("hard") or float(row.get("hard", 0)) < 1e-9


def plan_minibatches(request: ReflectRequest) -> StagePlan:
    """Split rollout rows into the failure-side and success-side jobs to run."""
    seed = request.random_seed
    failures = _ordered([r for r in request.results if _is_failure(r)], seed)
    successes: list[dict] = []
    if not request.failure_only:
        successes = _ordered(
            [r for r in request.results if r.get("hard")],
            None if seed is None else seed + 1,
        )

    plan = StagePlan(n_failures=len(failures), n_successes=len(successes))
    groups = ((FAILURE_ROLE, "fail", failures), (SUCCESS_ROLE, "succ", successes))
    for role, slot, rows in groups:
        for ordinal, chunk in enumerate(_chunk(rows, request.minibatch_size)):
            plan.jobs.append(MinibatchJob(role, slot, ordinal, chunk))
    return plan


def _analyst_request_for(job: MinibatchJob, request: ReflectRequest, skill_aware: bool, notes: bool) -> AnalystRequest:
    """Specialise the stage-wide request for one job's analyst call."""
    failure_side = job.role is FAILURE_ROLE
    return AnalystRequest(
        skill_content=request.skill_content,
        items=job.items,
        prediction_dir=request.prediction_dir,
        edit_budget=request.edit_budget,
        system_prompt=request.error_system if failure_side else request.success_system,
        memory_text=request.failure_memory() if failure_side else request.success_memory(),
        meta_skill_context=request.meta_skill_context,
        update_mode=request.update_mode,
        skill_aware=skill_aware,
        emit_notes=True if failure_side else notes,
    )


def _patch_path(patches_dir: str, tag: str) -> str:
    return os.path.join(patches_dir, f"{tag}.json")


def _load_cached_patch(patches_dir: str, tag: str) -> dict | None:
    """Return a previously persisted patch for *tag*, if the run is resuming."""
    path = _patch_path(patches_dir, tag)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _store_patch(patches_dir: str, tag: str, patch: dict) -> None:
    with open(_patch_path(patches_dir, tag), "w", encoding="utf-8") as handle:
        json.dump(patch, handle, ensure_ascii=False, indent=2)


def _resolve_skill_aware(request: ReflectRequest) -> tuple[bool, bool]:
    """Resolve the skill-aware toggle and whether success notes are emitted.

    Explicit kwargs win; otherwise the process-wide config switch set by the
    trainer applies, keeping the feature env-independent.
    """
    enabled = request.skill_aware_reflection
    if enabled is None:
        enabled = is_skill_aware_enabled()
    source = request.skill_aware_appendix_source
    if source is None:
        source = get_skill_aware_appendix_source()
    return bool(enabled), source != "failure_only"


def run_minibatch_reflect(request: ReflectRequest) -> list[dict | None]:
    """Run the whole reflect stage: plan → resume → parallel analysts → save."""
    os.makedirs(request.patches_dir, exist_ok=True)
    skill_aware, success_notes = _resolve_skill_aware(request)

    plan = plan_minibatches(request)
    logger.info(
        "[2/6 REFLECT minibatch] failure=%s→%s groups  success=%s→%s groups  (M=%s, L=%s, workers=%s)",
        plan.n_failures,
        plan.counts_by_slot("fail"),
        plan.n_successes,
        plan.counts_by_slot("succ"),
        request.minibatch_size,
        request.edit_budget,
        request.workers,
    )

    raw_patches: list[dict | None] = []
    pending: list[MinibatchJob] = []
    for job in plan.jobs:
        cached = _load_cached_patch(request.patches_dir, job.tag)
        if cached is None:
            pending.append(job)
        else:
            raw_patches.append(cached)

    if not pending:
        return raw_patches

    def _execute(job: MinibatchJob) -> dict | None:
        analyst_request = _analyst_request_for(job, request, skill_aware, success_notes)
        return _run_analyst(job.role, analyst_request)

    with ThreadPoolExecutor(max_workers=request.workers) as pool:
        futures = {pool.submit(_execute, job): job for job in pending}
        for done, future in enumerate(as_completed(futures), 1):
            job = futures[future]
            patch = future.result()
            if patch:
                _store_patch(request.patches_dir, job.tag, patch)
                raw_patches.append(patch)
            payload = patch.get("patch", {}) if patch else {}
            logger.info(
                "[analyst] %s/%s %s (%s trajs) → %s %s",
                done,
                len(pending),
                job.tag,
                len(job.items),
                len(get_payload_items(payload, request.update_mode)),
                payload_label(request.update_mode),
            )

    return raw_patches
