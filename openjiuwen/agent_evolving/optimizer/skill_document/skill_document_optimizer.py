# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ReflACT skill document optimizer.

Self-managing optimizer that runs the full ReflACT pipeline:
  rollout -> reflect -> aggregate -> select -> apply

Extensible via inheritance: override _rollout / _format_batch /
_format_single / _reflect / _aggregate / _select for customization.

Called by SingleDimUpdater.update() -> backward() -> _backward().
_step() returns base/candidate updates for Trainer validation selection.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from typing import TYPE_CHECKING, Any, Dict, List, Union

from openjiuwen.agent_evolving.dataset import Case, CaseLoader, EvaluatedCase
from openjiuwen.agent_evolving.dataset.case_loader import shuffle_cases
from openjiuwen.agent_evolving.optimizer.base import BaseOptimizer
from openjiuwen.agent_evolving.optimizer.llm_resilience import (
    LLMInvokePolicy,
    invoke_text_with_retry,
)
from openjiuwen.agent_evolving.optimizer.skill_document.artifact_exporter import ArtifactExporter
from openjiuwen.agent_evolving.optimizer.skill_document.edit_apply import apply_patch_with_report
from openjiuwen.agent_evolving.optimizer.skill_document.prompts import load_skill_opt_prompt
from openjiuwen.agent_evolving.optimizer.skill_document.scheduler import build_scheduler
from openjiuwen.agent_evolving.optimizer.skill_document.types import Edit, Patch, RawPatch
from openjiuwen.agent_evolving.optimizer.skill_document.update_modes import (
    normalize_update_mode,
)
from openjiuwen.agent_evolving.protocols import SKILL_CONTENT_TARGET
from openjiuwen.agent_evolving.trajectory import TracerTrajectoryExtractor
from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    ToolCallDetail,
    Trajectory,
)
from openjiuwen.core.common.logging import logger

if TYPE_CHECKING:
    from openjiuwen.core.foundation.llm.model import Model
    from openjiuwen.core.single_agent.base import BaseAgent
    from openjiuwen.agent_evolving.evaluator.evaluator import BaseEvaluator

_VALID_OPS = frozenset({"append", "insert_after", "replace", "delete"})

_REFLECT_POLICY = LLMInvokePolicy(
    attempt_timeout_secs=180,
    total_budget_secs=600,
    max_attempts=2,
)
_AGGREGATE_POLICY = LLMInvokePolicy(
    attempt_timeout_secs=180,
    total_budget_secs=600,
    max_attempts=2,
)
_RANKING_POLICY = LLMInvokePolicy(
    attempt_timeout_secs=120,
    total_budget_secs=300,
    max_attempts=2,
)


# ── JSON helpers (private to this module) ──────────────────────────────────


def _fix_json_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text.strip()


def _extract_json(raw: str) -> Any | None:
    """Best-effort JSON extraction from LLM output."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    fixed = _fix_json_text(raw)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    for pattern in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        matched = re.search(pattern, fixed)
        if matched:
            try:
                return json.loads(matched.group(0))
            except json.JSONDecodeError:
                try:
                    return json.loads(_fix_json_text(matched.group(0)))
                except json.JSONDecodeError:
                    pass
    return None


def _extract_json_with_error(raw: str) -> tuple[Any | None, str]:
    """Like _extract_json but returns (result, error_msg)."""
    raw = raw.strip()
    if not raw:
        return None, "empty response"
    result = _extract_json(raw)
    if result is not None:
        return result, ""
    return None, "failed to parse JSON from LLM response"


# ── Trajectory formatting helpers ─────────────────────────────────────────


def _clip_text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    return str(value)[:limit]


def _extract_content(msg: Any) -> str:
    """Extract text content from a message (dict or object)."""
    if isinstance(msg, dict):
        return str(msg.get("content", "") or "")
    if hasattr(msg, "content"):
        return str(msg.content or "")
    return str(msg or "")


def _extract_task_description(case: Case) -> str:
    """Extract a task description from a Case for formatting."""
    inputs = case.inputs or {}
    for key in ("task_description", "instruction", "question", "query"):
        if key in inputs:
            return str(inputs[key])[:500]
    return str(inputs)[:500]


# ── Main optimizer class ──────────────────────────────────────────────────


class SkillDocumentOptimizer(BaseOptimizer):
    """ReflACT skill document optimizer.

    Self-managing: requires_forward_data()=False.
    Extensible: override _rollout / _format_batch / _reflect /
    _aggregate / _select in subclasses to customize per-scenario.

    SingleDimUpdater.update() -> backward() -> _backward() runs the full epoch:
      for step in steps_per_epoch:
        for a in accumulation:
          rollout -> format -> reflect
        aggregate -> select -> apply
    Then _step() returns base/candidate updates for Trainer validation.
    """

    domain = "skill_document"

    def __init__(
        self,
        *,
        agent: "BaseAgent",
        evaluator: "BaseEvaluator",
        extractor: TracerTrajectoryExtractor | None = None,
        num_parallel: int = 4,
        llm: "Model",
        model: str,
        train_cases: CaseLoader,
        batch_size: int = 40,
        accumulation: int = 2,
        steps_per_epoch: int | None = None,
        minibatch_size: int = 8,
        edit_budget: int = 10,
        scheduler_mode: str = "constant",
        update_mode: str = "patch",
        score_threshold: float = 0.5,
        parallelism: int = 4,
        max_chars_per_traj: int = 12_000,
        max_msg_chars: int = 500,
        max_tool_result_chars: int = 800,
        use_slow_update: bool = True,
        use_meta_skill: bool = True,
        artifact_dir: str | None = None,
        artifact_export_trajectories: bool = True,
    ):
        super().__init__()

        # Validate
        if update_mode != "patch":
            raise NotImplementedError(f"update_mode={update_mode!r} not yet supported (Phase 1: patch only)")
        if scheduler_mode == "autonomous":
            raise NotImplementedError("scheduler_mode='autonomous' not yet supported")

        n_cases = len(train_cases.get_cases()) if train_cases else 0
        if n_cases and batch_size * accumulation > n_cases:
            import warnings

            warnings.warn(
                f"batch_size({batch_size}) * accumulation({accumulation}) = "
                f"{batch_size * accumulation} > train_cases({n_cases}). "
                "Each rollout round will use all cases.",
                stacklevel=2,
            )

        # Store dependencies
        self._agent = agent
        self._evaluator = evaluator
        self._extractor = extractor or TracerTrajectoryExtractor()
        self._num_parallel = num_parallel
        self._train_cases = train_cases
        self._llm = llm
        self._model = model

        # Hyperparameters
        self._batch_size = batch_size
        self._accumulation = accumulation
        self._minibatch_size = minibatch_size
        self._update_mode = normalize_update_mode(update_mode)
        self._score_threshold = score_threshold
        self._semaphore = asyncio.Semaphore(parallelism)

        # Trajectory formatting params
        self._max_chars_per_traj = max_chars_per_traj
        self._max_msg_chars = max_msg_chars
        self._max_tool_result_chars = max_tool_result_chars

        # Compute steps_per_epoch
        self._steps_per_epoch = steps_per_epoch or (
            math.ceil(n_cases / (batch_size * accumulation)) if n_cases and batch_size and accumulation else 1
        )

        self._scheduler = build_scheduler(
            mode=scheduler_mode,
            max_lr=edit_budget,
            total_steps=self._steps_per_epoch,
        )

        # Cross-step state
        self._global_step = 0
        self._step_buffer: list[dict] = []
        self._meta_skill_context = ""

        # Output of _backward(), consumed by _step()
        self._ranked_patch: Patch | None = None
        self._current_skill_content = ""
        self._epoch_base_skill_content = ""
        self._last_candidate_skill_content = ""

        # Epoch-level state (for slow_update)
        self._use_slow_update = use_slow_update
        self._use_meta_skill = use_meta_skill
        self._prev_epoch_skill: str = ""
        self._prev_epoch_comparison: list[dict] = []
        self._curr_epoch_comparison: list[dict] = []

        # Artifact export config
        self._artifact_dir = artifact_dir
        self._artifact_export_trajectories = artifact_export_trajectories
        self._artifact_exporter = ArtifactExporter(
            artifact_dir,
            export_trajectories=artifact_export_trajectories,
        )
        self._artifact_epoch = -1

    @staticmethod
    def requires_forward_data() -> bool:
        return False

    @staticmethod
    def default_targets() -> list[str]:
        return [SKILL_CONTENT_TARGET]

    # ── Case sampling ────────────────────────────────────────────────────

    def _sample_cases(self, n: int, seed: int = 0) -> list[Case]:
        all_cases = self._train_cases.get_cases()
        shuffled = shuffle_cases(all_cases, seed=seed)
        return shuffled[:n]

    # ── Trajectory formatting ────────────────────────────────────────────

    def _format_batch(
        self,
        batch: list[tuple[Trajectory, EvaluatedCase, Case]],
    ) -> str:
        """Format a minibatch of trajectories into analyst-readable text."""
        parts: list[str] = []
        for idx, (traj, eval_case, case) in enumerate(batch, 1):
            traj_text = self._format_single(traj, eval_case, case)
            header = (
                f"### Trajectory {idx} (id={case.case_id})\n"
                f"Task: {_extract_task_description(case)}\n"
                f"Score: {eval_case.score:.2f}\n"
            )
            if eval_case.reason:
                header += f"Reason: {_clip_text(eval_case.reason, 500)}\n"
            parts.append(header + "\n" + traj_text)
        return "\n\n---\n\n".join(parts)

    def _format_single(
        self,
        trajectory: Trajectory,
        evaluated_case: EvaluatedCase,
        case: Case,
    ) -> str:
        """Format a single trajectory with LLM/tool step rendering."""
        max_chars = self._max_chars_per_traj
        lines: list[str] = []
        for step in trajectory.steps:
            if step.kind == "llm" and isinstance(step.detail, LLMCallDetail):
                for msg in step.detail.messages:
                    role = msg.get("role", "unknown") if isinstance(msg, dict) else getattr(msg, "role", "unknown")
                    if role == "system":
                        continue
                    content = _extract_content(msg)
                    lines.append(f"[{role}] {_clip_text(content, self._max_msg_chars)}")
                if step.detail.response:
                    resp_content = _extract_content(step.detail.response)
                    lines.append(f"[assistant] {_clip_text(resp_content, self._max_msg_chars)}")
            elif step.kind == "tool" and isinstance(step.detail, ToolCallDetail):
                lines.append(
                    f"[action] {step.detail.tool_name}: {_clip_text(step.detail.call_args, self._max_msg_chars)}"
                )
                lines.append(f"[obs]    {_clip_text(step.detail.call_result, self._max_tool_result_chars)}")
        text = "\n".join(lines)
        if len(text) > max_chars:
            half = max_chars // 2
            text = text[:half] + "\n...[middle truncated]...\n" + text[-half:]
        return text

    # ── Rollout ──────────────────────────────────────────────────────────

    async def _rollout(
        self,
        cases: list[Case],
        skill_content: str,
    ) -> tuple[list[EvaluatedCase], list[Trajectory]]:
        """Run agent on cases, return evaluated results + trajectories."""
        from openjiuwen.core.session.agent import create_agent_session

        async def run_one(case: Case, sem: asyncio.Semaphore) -> tuple[dict, Any]:
            async with sem:
                session = create_agent_session()
                try:
                    res = await self._agent.invoke(
                        {**case.inputs, "conversation_id": case.case_id},
                        session=session,
                    )
                except Exception as exc:
                    logger.warning(
                        "[skill_doc_opt] rollout error case=%s: %s",
                        case.case_id,
                        exc,
                    )
                    res = {"error": str(exc)}
                return res, session

        sem = asyncio.Semaphore(
            min(self._num_parallel, len(cases)) if cases else 1,
        )
        results = await asyncio.gather(*[run_one(c, sem) for c in cases])
        predicts = [r[0] for r in results]
        sessions = [r[1] for r in results]

        evaluated = self._evaluator.batch_evaluate(
            cases,
            predicts,
            num_parallel=self._num_parallel,
        )
        trajectories = [self._extractor.extract(sess, case_id=case.case_id) for case, sess in zip(cases, sessions)]
        return evaluated, trajectories

    # ── Reflect ──────────────────────────────────────────────────────────

    async def _reflect(
        self,
        formatted_batch: str,
        skill_content: str,
        score_threshold: float,
        batch_data: list[tuple[Trajectory, EvaluatedCase, Case]] | None = None,
    ) -> list[RawPatch]:
        """Analyze formatted trajectories, produce edit suggestions.

        Splits by score_threshold into failure/success, groups into
        minibatches, runs LLM analysts in parallel.
        """
        if batch_data is not None:
            failure_batch = [item for item in batch_data if item[1].score < score_threshold]
            success_batch = [item for item in batch_data if item[1].score >= score_threshold]
            failure_text = self._format_batch(failure_batch) if failure_batch else ""
            success_text = self._format_batch(success_batch) if success_batch else ""
        else:
            failure_text = formatted_batch
            success_text = formatted_batch

        if not failure_text.strip() and not success_text.strip():
            return []

        # Build prompt context
        step_buffer_ctx = self._format_step_buffer()
        meta_ctx = self._format_meta_skill_context()

        # We treat the entire formatted_batch as a single group for analysis.
        # For multi-minibatch scenarios, the _backward loop handles splitting.
        raw_patches: list[RawPatch] = []

        tasks: list[Any] = []

        if failure_text.strip():
            error_prompt = self._build_analyst_prompt(
                "analyst_error",
                skill_content,
                failure_text,
                step_buffer_ctx,
                meta_ctx,
            )
            tasks.append(("failure", error_prompt))

        if success_text.strip():
            success_prompt = self._build_analyst_prompt(
                "analyst_success",
                skill_content,
                success_text,
                step_buffer_ctx,
                meta_ctx,
            )
            tasks.append(("success", success_prompt))

        # Run LLM calls in parallel with semaphore
        async def run_analyst(source_type: str, prompt: str) -> RawPatch | None:
            async with self._semaphore:
                try:
                    raw = await invoke_text_with_retry(
                        self._llm,
                        self._model,
                        prompt,
                        policy=_REFLECT_POLICY,
                    )
                    return self._parse_reflect_response(raw, source_type)
                except Exception as exc:
                    logger.warning(
                        "[skill_doc_opt] reflect %s failed: %s",
                        source_type,
                        exc,
                    )
                    return None

        results = await asyncio.gather(*[run_analyst(st, p) for st, p in tasks])

        for r in results:
            if r is not None:
                raw_patches.append(r)

        return raw_patches

    def _build_analyst_prompt(
        self,
        template_name: str,
        skill_content: str,
        trajectories_text: str,
        step_buffer_context: str,
        meta_skill_context: str,
    ) -> str:
        """Build the full prompt for an analyst LLM call."""
        system = load_skill_opt_prompt(template_name)
        user = f"## Current Skill\n{skill_content}\n\n"
        user += f"## Edits Budget\nProduce at most L={self._scheduler.max_lr} edits.\n\n"
        if step_buffer_context.strip():
            user += f"## Previous Steps in This Epoch\n{step_buffer_context}\n\n"
        if meta_skill_context.strip():
            user += f"## Optimizer Memory\n{meta_skill_context}\n\n"

        if "error" in template_name:
            user += f"## Failed Trajectories\n{trajectories_text}"
        else:
            user += f"## Successful Trajectories\n{trajectories_text}"

        return f"{system}\n\n{user}"

    def _parse_reflect_response(
        self,
        raw: str,
        source_type: str,
    ) -> RawPatch | None:
        """Parse and validate an analyst LLM response into a RawPatch."""
        result, error = _extract_json_with_error(raw)
        if result is None:
            logger.warning(
                "[skill_doc_opt] reflect JSON parse failed (%s): %s",
                source_type,
                error,
            )
            return None

        if not isinstance(result, dict):
            return None

        # Extract patch from response
        patch_data = result.get("patch", result)
        if not isinstance(patch_data, dict):
            return None

        edits_data = patch_data.get("edits", [])
        if not isinstance(edits_data, list):
            return None

        # R1 validation: filter to valid edits
        valid_edits: list[Edit] = []
        for ed in edits_data:
            if not isinstance(ed, dict):
                continue
            op = ed.get("op", "")
            if op not in _VALID_OPS:
                continue
            valid_edits.append(
                Edit(
                    op=op,
                    content=str(ed.get("content", "")),
                    target=str(ed.get("target", "")),
                    support_count=int(ed.get("support_count", 0) or 0),
                    source_type=source_type,
                )
            )

        if not valid_edits:
            # Empty patch is a valid sentinel (no changes suggested)
            return RawPatch(
                patch=Patch(edits=[], reasoning="no valid edits"),
                source_type=source_type,
                failure_summary=str(result.get("failure_summary", "")),
            )

        reasoning = str(result.get("reasoning", patch_data.get("reasoning", "")))
        failure_summary = str(result.get("failure_summary", ""))

        return RawPatch(
            patch=Patch(edits=valid_edits, reasoning=reasoning),
            source_type=source_type,
            failure_summary=failure_summary,
        )

    # ── Aggregate ────────────────────────────────────────────────────────

    async def _aggregate(
        self,
        patches: list[RawPatch],
        skill_content: str,
    ) -> Patch:
        """Merge patches from multiple minibatches.

        Three-stage LLM merge: failure -> success -> final.
        P5: <=3 patches use rule-based dedup (skip LLM).
        Fallback: simple concatenation on LLM merge failure.
        """
        if not patches:
            return Patch(edits=[], reasoning="no patches")

        failure_patches = [p for p in patches if p.source_type == "failure"]
        success_patches = [p for p in patches if p.source_type == "success"]

        f_edits = [e for p in failure_patches for e in p.patch.edits]
        s_edits = [e for p in success_patches for e in p.patch.edits]

        all_edits = f_edits + s_edits
        total = len(all_edits)

        # P5: small number of patches -> rule-based dedup
        if total <= 3:
            deduped = self._rule_dedup_edits(all_edits)
            return Patch(edits=deduped, reasoning="rule-based dedup (<=3 edits)")

        # Three-stage LLM merge
        meta_ctx = self._format_meta_skill_context()

        # Stage 1: merge failure patches
        failure_merged = f_edits
        if len(f_edits) > 1:
            failure_merged = await self._llm_merge_edits(
                f_edits,
                "merge_failure",
                skill_content,
                meta_ctx,
            )

        # Stage 2: merge success patches
        success_merged = s_edits
        if len(s_edits) > 1:
            success_merged = await self._llm_merge_edits(
                s_edits,
                "merge_success",
                skill_content,
                meta_ctx,
            )

        # Stage 3: final merge
        combined = failure_merged + success_merged
        if not combined:
            return Patch(edits=[], reasoning="no edits after merge")

        if len(combined) <= 3:
            return Patch(edits=combined, reasoning="final: <=3 edits after stages")

        final_edits = await self._llm_merge_edits(
            combined,
            "merge_final",
            skill_content,
            meta_ctx,
        )
        return Patch(edits=final_edits, reasoning="three-stage LLM merge")

    async def _llm_merge_edits(
        self,
        edits: list[Edit],
        template_name: str,
        skill_content: str,
        meta_skill_context: str,
    ) -> list[Edit]:
        """Call LLM to merge edits, with fallback to concatenation."""
        edits_dicts = [
            {
                "op": e.op,
                "content": e.content,
                "target": e.target,
                "support_count": e.support_count,
                "source_type": e.source_type,
            }
            for e in edits
        ]
        system = load_skill_opt_prompt(template_name)
        user = f"## Current Skill\n{skill_content}\n\n"
        if meta_skill_context.strip():
            user += f"## Optimizer Memory\n{meta_skill_context}\n\n"
        user += f"## Edits to merge ({len(edits)} total)\n{json.dumps(edits_dicts, ensure_ascii=False, indent=2)}"
        prompt = f"{system}\n\n{user}"

        try:
            async with self._semaphore:
                raw = await invoke_text_with_retry(
                    self._llm,
                    self._model,
                    prompt,
                    policy=_AGGREGATE_POLICY,
                )
            result = _extract_json(raw)
            if result and isinstance(result, dict) and "edits" in result:
                merged = []
                for ed in result["edits"]:
                    if not isinstance(ed, dict):
                        continue
                    op = ed.get("op", "")
                    if op not in _VALID_OPS:
                        continue
                    merged.append(
                        Edit(
                            op=op,
                            content=str(ed.get("content", "")),
                            target=str(ed.get("target", "")),
                            support_count=int(ed.get("support_count", 0) or 0),
                            source_type=str(ed.get("source_type", "failure")),
                        )
                    )
                if merged:
                    return merged
        except Exception as exc:
            logger.warning(
                "[skill_doc_opt] aggregate %s failed, using fallback: %s",
                template_name,
                exc,
            )

        # Fallback: return original edits unchanged
        return edits

    @staticmethod
    def _rule_dedup_edits(edits: list[Edit]) -> list[Edit]:
        """Rule-based dedup for small edit sets (no LLM needed)."""
        seen: set[tuple[str, str, str]] = set()
        deduped: list[Edit] = []
        for e in edits:
            key = (e.op, e.content, e.target)
            if key not in seen:
                seen.add(key)
                deduped.append(e)
        return deduped

    # ── Select ───────────────────────────────────────────────────────────

    async def _select(
        self,
        edits: list[Edit],
        budget: int,
        skill_content: str,
    ) -> list[Edit]:
        """Rank edits and select top-k within budget.

        If edits <= budget, return unchanged. Otherwise use LLM ranking.
        """
        if len(edits) <= budget:
            return edits

        meta_ctx = self._format_meta_skill_context()

        # Build edit pool description
        edits_desc = []
        for i, edit in enumerate(edits):
            desc = f"[{i}] op={edit.op}"
            if edit.target:
                desc += f"  target={edit.target!r}"
            desc += f"  content={edit.content[:200]!r}"
            edits_desc.append(desc)

        system = load_skill_opt_prompt("ranking")
        user = f"## Current Skill\n{skill_content}\n\n"
        if meta_ctx.strip():
            user += f"## Optimizer Memory\n{meta_ctx}\n\n"
        user += (
            f"## Edits Pool ({len(edits)} edits, budget={budget})\n"
            + "\n".join(edits_desc)
            + f"\n\nSelect the {budget} most important edits. "
            f"Return their 0-based indices in priority order."
        )
        prompt = f"{system}\n\n{user}"

        try:
            async with self._semaphore:
                raw = await invoke_text_with_retry(
                    self._llm,
                    self._model,
                    prompt,
                    policy=_RANKING_POLICY,
                )
            result = _extract_json(raw)
            if result and isinstance(result, dict) and "selected_indices" in result:
                indices = result["selected_indices"]
                selected: list[Edit] = []
                seen: set[int] = set()
                for idx in indices:
                    if isinstance(idx, int) and 0 <= idx < len(edits) and idx not in seen:
                        selected.append(edits[idx])
                        seen.add(idx)
                    if len(selected) >= budget:
                        break
                if selected:
                    return selected
        except Exception as exc:
            logger.warning(
                "[skill_doc_opt] select ranking failed, fallback truncation: %s",
                exc,
            )

        # Fallback: simple truncation
        return edits[:budget]

    # ── _backward: the full epoch orchestrator ───────────────────────────

    async def _backward(self, signals: list) -> None:
        """Full epoch: rollout -> reflect -> aggregate -> select -> apply.

        Called by SingleDimUpdater.process() -> BaseOptimizer.backward().
        signals is empty (requires_forward_data=False), ignored.
        """
        # Read current skill from bound operator
        self._artifact_epoch += 1
        artifact_epoch = self._artifact_epoch
        self._current_skill_content = self._read_skill_from_operator()
        self._epoch_base_skill_content = self._current_skill_content
        self._last_candidate_skill_content = ""
        self._curr_epoch_comparison.clear()
        self._artifact_exporter.export_skill_snapshot(
            artifact_epoch,
            0,
            self._epoch_base_skill_content,
            "before",
        )

        for step in range(self._steps_per_epoch):
            self._global_step += 1
            all_patches: list[RawPatch] = []
            step_before_skill = self._current_skill_content
            step_eval_results: list[EvaluatedCase] = []
            step_trajectories: list[Trajectory] = []
            step_cases: list[Case] = []
            step_case_count = 0

            # Accumulation loop
            for a in range(self._accumulation):
                try:
                    batch_cases = self._sample_cases(
                        self._batch_size,
                        seed=self._global_step * 100 + a,
                    )

                    # 1. Rollout
                    batch_evaluated, batch_trajectories = await self._rollout(
                        cases=batch_cases,
                        skill_content=self._current_skill_content,
                    )
                    step_eval_results.extend(batch_evaluated)
                    step_trajectories.extend(batch_trajectories)
                    step_cases.extend(batch_cases)
                    step_case_count += len(batch_cases)

                    # 2. Format trajectories
                    batch_data = list(zip(batch_trajectories, batch_evaluated, batch_cases))
                    formatted_batch = self._format_batch(batch_data)

                    # 3. Reflect
                    batch_patches = await self._reflect(
                        formatted_batch=formatted_batch,
                        skill_content=self._current_skill_content,
                        score_threshold=self._score_threshold,
                        batch_data=batch_data,
                    )
                    all_patches.extend(batch_patches)

                    # Track comparison pairs for slow_update (last step only)
                    if step == self._steps_per_epoch - 1:
                        for case, eval_case in zip(batch_cases, batch_evaluated):
                            self._curr_epoch_comparison.append(
                                {
                                    "case_id": case.case_id,
                                    "curr_score": eval_case.score,
                                    "curr_reason": eval_case.reason,
                                }
                            )
                except Exception as exc:
                    logger.warning(
                        "[skill_doc_opt] accumulation round %d/%d in step %d failed: %s",
                        a + 1,
                        self._accumulation,
                        step + 1,
                        exc,
                    )
                    continue

            self._artifact_exporter.export_trajectories(
                artifact_epoch,
                step,
                step_trajectories,
                step_eval_results,
            )
            self._artifact_exporter.export_eval_results(
                artifact_epoch,
                step,
                step_eval_results,
                step_cases,
            )
            self._artifact_exporter.export_raw_patches(
                artifact_epoch,
                step,
                0,
                all_patches,
            )

            # 4. Aggregate
            merged = await self._aggregate(
                patches=all_patches,
                skill_content=self._current_skill_content,
            )
            self._artifact_exporter.export_merged_patch(
                artifact_epoch,
                step,
                merged,
            )

            # 5. Select
            budget = self._scheduler.step()
            selected_edits = await self._select(
                edits=merged.edits,
                budget=budget,
                skill_content=self._current_skill_content,
            )
            self._ranked_patch = Patch(
                edits=selected_edits,
                reasoning=merged.reasoning,
            )
            self._artifact_exporter.export_selected_edits(
                artifact_epoch,
                step,
                selected_edits,
                self._extract_rejected_edits(),
                budget,
            )

            # 6. Apply: update skill for next step's rollout
            if self._ranked_patch and self._ranked_patch.edits:
                updated_skill, _ = apply_patch_with_report(
                    self._current_skill_content,
                    self._ranked_patch,
                )
                self._current_skill_content = updated_skill
                self._sync_skill_to_operator(self._current_skill_content)
            self._artifact_exporter.export_skill_snapshot(
                artifact_epoch,
                step,
                self._current_skill_content,
                "after",
            )
            self._artifact_exporter.export_skill_diff(
                artifact_epoch,
                step,
                step_before_skill,
                self._current_skill_content,
            )
            scores = [case.score for case in step_eval_results]
            self._artifact_exporter.export_metrics(
                artifact_epoch,
                step,
                {
                    "global_step": self._global_step,
                    "step": step,
                    "n_cases": step_case_count,
                    "n_raw_patches": len(all_patches),
                    "n_merged_edits": len(merged.edits),
                    "n_selected_edits": len(selected_edits),
                    "avg_score": sum(scores) / len(scores) if scores else 0.0,
                },
            )

            # Record step buffer entry
            self._step_buffer.append(self._build_step_buffer_entry(step))

        # Store final skill for _step() candidate generation
        self._last_candidate_skill_content = self._current_skill_content
        param = next(iter(self._parameters.values()), None)
        if param is not None:
            param.set_gradient(SKILL_CONTENT_TARGET, self._current_skill_content)

    # ── _step: return base/candidate for Trainer gate ────────────────────

    def _step(
        self,
    ) -> Union[
        Dict[tuple[str, str], Any],
        List[Dict[tuple[str, str], Any]],
    ]:
        """Return base/candidate updates for validation selection.

        R3: base == candidate -> only return base (skip redundant validation).
        """
        candidates: list[dict] = []
        for op_id, param in self._parameters.items():
            final_skill = param.get_gradient(SKILL_CONTENT_TARGET)
            if not final_skill:
                continue
            base_update = {(op_id, SKILL_CONTENT_TARGET): self._epoch_base_skill_content}
            if final_skill == self._epoch_base_skill_content:
                # R3: no change, only return base
                candidates.append(base_update)
                continue
            candidates.append(base_update)
            candidates.append({(op_id, SKILL_CONTENT_TARGET): final_skill})
        return candidates

    # ── Skill document I/O ───────────────────────────────────────────────

    def _read_skill_from_operator(self) -> str:
        for op in self._operators.values():
            state = op.get_state()
            return state.get("skill_content", "")
        return ""

    def _sync_skill_to_operator(self, skill_content: str) -> None:
        """Make intermediate skill visible to agent before next rollout."""
        for op in self._operators.values():
            op.set_parameter(SKILL_CONTENT_TARGET, skill_content)

    # ── Step buffer ──────────────────────────────────────────────────────

    def _build_step_buffer_entry(self, step: int) -> dict:
        return {
            "step": self._global_step,
            "n_edits": len(self._ranked_patch.edits) if self._ranked_patch else 0,
            "failure_patterns": self._extract_failure_patterns(),
            "rejected_edits": self._extract_rejected_edits(),
        }

    def _extract_failure_patterns(self) -> list[str]:
        """Extract common failure patterns from the current step."""
        if not self._ranked_patch:
            return []
        return [e.content[:100] for e in self._ranked_patch.edits if e.source_type == "failure"][:3]

    def _extract_rejected_edits(self) -> list[str]:
        """Extract edits rejected by the ranking stage."""
        # For now, return empty — ranking rejection details
        # are available when _select tracks them.
        return []

    def _format_step_buffer(self) -> str:
        if not self._step_buffer:
            return ""
        lines = []
        for entry in self._step_buffer:
            lines.append(f"Step {entry['step']}: {entry['n_edits']} edits applied")
            if entry.get("failure_patterns"):
                lines.append(f"  Failure patterns: {entry['failure_patterns']}")
            if entry.get("rejected_edits"):
                lines.append(f"  Rejected edits: {entry['rejected_edits']}")
        return "\n".join(lines)

    # ── Meta skill context ───────────────────────────────────────────────

    def _format_meta_skill_context(self) -> str:
        if not self._meta_skill_context:
            return ""
        return self._meta_skill_context

    @staticmethod
    def _mean_eval_score(eval_results: list[EvaluatedCase]) -> float | None:
        if not eval_results:
            return None
        return sum(result.score for result in eval_results) / len(eval_results)

    def _infer_gate_decision(self, committed_skill: str) -> str:
        if self._epoch_base_skill_content and committed_skill == self._epoch_base_skill_content:
            return "base"
        if self._last_candidate_skill_content and committed_skill == self._last_candidate_skill_content:
            return "candidate"
        return "unknown"

    # ── Epoch-level: run_epoch_end ───────────────────────────────────────

    async def run_epoch_end(self, epoch: int, val_results: list[EvaluatedCase] | None = None) -> None:
        """Called by SkillDocumentCallbacks.on_train_epoch_end().

        slow_update modifies skill_content in-place (force-inject into markers).
        meta_skill only updates optimizer-internal state.
        """
        if self._operators:
            self._current_skill_content = self._read_skill_from_operator()
        selected_score = self._mean_eval_score(val_results or [])
        decision = self._infer_gate_decision(self._current_skill_content)
        base_score = selected_score if decision == "base" else None
        candidate_score = selected_score if decision == "candidate" else None
        self._artifact_exporter.export_gate_result(
            epoch,
            base_score=base_score,
            candidate_score=candidate_score,
            decision=decision,
        )

        if self._use_slow_update and epoch >= 1:
            await self._run_slow_update(epoch)
        if self._use_meta_skill and epoch >= 1:
            await self._run_meta_skill(epoch)
        self._prev_epoch_skill = self._current_skill_content
        self._prev_epoch_comparison = list(self._curr_epoch_comparison)
        self._curr_epoch_comparison.clear()
        self._step_buffer.clear()

    async def _run_slow_update(self, epoch: int) -> None:
        """Slow update: epoch-level strategic guidance for the protected region."""
        if not self._prev_epoch_comparison:
            return

        from openjiuwen.agent_evolving.optimizer.skill_document.edit_apply import (
            extract_slow_update_content,
            replace_slow_update_field,
        )
        from openjiuwen.agent_evolving.optimizer.skill_document.slow_update import (
            build_comparison_text,
            run_slow_update,
        )

        comparison_text = build_comparison_text(
            self._prev_epoch_comparison,
            self._curr_epoch_comparison,
        )
        if not comparison_text:
            return

        prev_guidance = extract_slow_update_content(self._current_skill_content)

        result = await run_slow_update(
            self._llm,
            self._model,
            prev_skill=self._prev_epoch_skill,
            curr_skill=self._current_skill_content,
            comparison_text=comparison_text,
            prev_guidance=prev_guidance,
        )

        if result.slow_update_content:
            self._current_skill_content = replace_slow_update_field(
                self._current_skill_content,
                result.slow_update_content,
            )
            self._sync_skill_to_operator(self._current_skill_content)

    async def _run_meta_skill(self, epoch: int) -> None:
        """Meta skill: optimizer-side memory update (does not modify skill document)."""
        if not self._prev_epoch_skill:
            return

        from openjiuwen.agent_evolving.optimizer.skill_document.meta_skill import run_meta_skill
        from openjiuwen.agent_evolving.optimizer.skill_document.slow_update import build_comparison_text

        comparison_text = build_comparison_text(
            self._prev_epoch_comparison,
            self._curr_epoch_comparison,
        )

        content = await run_meta_skill(
            self._llm,
            self._model,
            prev_skill=self._prev_epoch_skill,
            curr_skill=self._current_skill_content,
            comparison_text=comparison_text,
            prev_meta_skill=self._meta_skill_context,
        )

        if content:
            self._meta_skill_context = content

    # ── State serialization ──────────────────────────────────────────────

    def get_state(self) -> Dict[str, Any]:
        """Serializable optimizer state for checkpoint resume."""
        return {
            "global_step": self._global_step,
            "step_buffer": self._step_buffer,
            "meta_skill_context": self._meta_skill_context,
            "scheduler": self._scheduler.state_dict(),
            "prev_epoch_skill": self._prev_epoch_skill,
            "prev_epoch_comparison": self._prev_epoch_comparison,
        }

    def load_state(self, state: Dict[str, Any]) -> None:
        """Restore optimizer state from checkpoint."""
        self._global_step = state.get("global_step", 0)
        self._step_buffer = state.get("step_buffer", [])
        self._meta_skill_context = state.get("meta_skill_context", "")
        sched_state = state.get("scheduler", {})
        if sched_state:
            self._scheduler.load_state_dict(sched_state)
        self._prev_epoch_skill = state.get("prev_epoch_skill", "")
        self._prev_epoch_comparison = state.get("prev_epoch_comparison", [])


__all__ = ["SkillDocumentOptimizer"]
