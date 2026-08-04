# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LLM-as-judge evaluation and isolated judge-agent runtime."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.rsi.evaluator.evidence_adapters import (
    collect_artifact_runtime_evidence,
)
from openjiuwen.rsi.evaluator.judge_skills import (
    format_judge_skill_instructions,
    resolve_judge_skills,
)
from openjiuwen.rsi.evaluator.judge_skills.registry import (
    combined_runtime_policy,
)
from openjiuwen.rsi.evaluator.judger.base import (
    EvaluationJudger,
    JudgeResult,
)
from openjiuwen.rsi.evaluator.judger.scoring import (
    aggregate_score,
    compute_dimensions,
    normalize_behaviors,
    normalize_forbidden,
    parse_judge_output,
)
from openjiuwen.rsi.evaluator.trajectory_paths import (
    ROLE_TRAJECTORY_DIR_NAME,
    TRAJECTORY_EVENTS_FILE_NAME,
)
from openjiuwen.rsi.member_optimizer.agents.factory import (
    load_member_optimizer_model,
)
from openjiuwen.rsi.model_call import (
    DEFAULT_MODEL_CALL_MAX_RETRIES,
    run_model_call_with_retries,
)

if TYPE_CHECKING:
    from openjiuwen.core.single_agent.base import BaseAgent
    from openjiuwen.rsi.config import EvaluatorConfig
    from openjiuwen.rsi.evaluator.case_backend import (
        CaseExecutionResult,
    )

_MAX_RETRIES = DEFAULT_MODEL_CALL_MAX_RETRIES
_JUDGE_AGENT_MAX_ITERATIONS = 8
_JUDGE_MODEL_CALL_MAX_RETRIES = 2
_JUDGE_JSON_PARSE_MAX_RETRIES = 1
_JUDGE_MODEL_MAX_TOKENS = 8192
_JUDGE_TRACE_MAX_CHARS = 20_000
_JUDGE_ARTIFACT_MAX_CHARS = 12_000
_JUDGE_ARTIFACT_TOTAL_MAX_CHARS = 60_000
_JUDGE_PRIMARY_ARTIFACT_MAX_CHARS = _JUDGE_ARTIFACT_TOTAL_MAX_CHARS
_PRIMARY_ARTIFACT_NAMES = {
    "index.html",
    "styles.css",
    "game.js",
    "app.js",
    "main.js",
    "script.js",
    "content_brief.md",
}
_SUPPORT_ARTIFACT_DIR_NAMES = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
}
_SELF_REPORTED_ARTIFACT_STEMS = {
    "final_delivery_summary",
    "review_conclusion",
    "review_summary",
    "rules_design",
    "test_report",
    "ui_spec",
    "validation_report",
}

_JUDGE_SYSTEM_PROMPT = """\
You are a strict, evidence-grounded evaluator for AI agent task results.

Responsibilities:
1. Inspect the case input, the agent response, and available evidence files.
2. Score every required behavior independently from 0.0 to 1.0.
3. Check whether any forbidden behavior was triggered.
4. Return only valid compact JSON, with no markdown fences and no text
   outside the JSON object.

Scoring principles:
- Judge only from visible evidence. Do not complete the task for the agent.
- If evidence is missing or ambiguous, assign a low score.
- Each behavior must be scored independently.
- Before reading the agent's self-reported summaries or generated plans, derive
  an independent task quality contract from the user's original request. Use it
  to decide what real end-user success requires for this task family.
- Treat agent-generated plans, rules, QA reports, summaries, and acceptance
  notes as supporting evidence. They cannot redefine the user's quality bar.
- A behavior score of 1.0 means the final deliverable fully satisfies that
  behavior with concrete artifact or trace evidence, has no material defect,
  and could be used by the user as-is for that behavior.
- Use 0.85-0.95 for strong but imperfect delivery, 0.60-0.84 for usable but
  incomplete or fragile delivery, 0.30-0.59 for partial delivery, and below
  0.30 for absent or broken behavior.
- Treat each required behavior as a task capability contract supplied by the
  case. The case-specific description and rubric define what capability should
  be judged.
- End-to-end quality axes:
  - functional_effectiveness: the delivered artifact actually achieves the
    user's goal, not just a subset of listed checks.
  - interaction_or_effect_quality: interactive flows, feedback, effects, or
    state changes feel clear, coherent, and useful for the task.
    When deterministic interaction_closure evidence is available, judge whether
    user action entries connect to state transitions, visible feedback, and a
    terminal or recovery path.
  - user_visible_output_quality: the visible artifact is polished, organized,
    understandable, and appropriate for the requested domain.
  - coherence_and_domain_reasoning: content, rules, logic, calculations, or
    design decisions are internally consistent and support the intended
    outcome.
  - runtime_correctness_and_validation: the artifact runs or can be validated
    through the expected environment without material errors.
  - completion_and_acceptance_contract: the final deliverable matches the
    user's requested files, format, scope, and completion criteria.
- Required behaviors are minimum task capability contracts. After behavior
  scoring, review the final deliverable against these end-to-end quality axes
  and emit quality_gaps for material weaknesses that limit real user value.
- First build a task-derived quality contract from the user's request. Name the
  concrete qualities that would make the final artifact excellent for that task
  family, then judge the artifacts against those qualities. Do not stop at file
  presence or smoke-test validation when the user asked for an experience,
  analysis, design, implementation, or other end-to-end outcome.
- Separate two kinds of gaps:
  - artifact_quality_gap: visible evidence shows the delivered artifact is weak,
    shallow, incoherent, incomplete, or below the task's quality bar.
  - verification_gap: evidence is missing, truncated, or too shallow to confirm
    quality. Prefer artifact_quality_gap when the evidence itself shows a real
    user-facing weakness.
- Apply score ceilings from core quality evidence:
  - If the core user goal or core task semantics are materially broken, the
    overall score should not exceed 0.65.
  - If the artifact runs but the main user experience or effect quality is weak,
    the overall score should not exceed 0.75.
- If validation evidence only covers smoke checks while core behavior remains
  unverified, the overall score should not exceed 0.85.
- Artifact adapter evidence is machine-collected. Treat browser load, runtime
  errors, and observed interaction effects as stronger than self-reported QA.
  Respect its explicit evidence_limitations: a browser smoke pass does not prove
  domain semantics, deep workflows, or successful task completion.
- If machine evidence shows a runtime error or a required control is unreachable,
  score the affected behavior from that observation rather than code appearance.
- For web interaction and touch-target requirements, distinguish an interactive
  control from its visual descendants. Treat an element as interactive only when
  evidence identifies a native control, an explicit interaction role/tab stop,
  an event-listener target, or a machine-probed control. A badge, icon, label,
  cost marker, statistic, or other descendant is not a separate touch target
  merely because it is small or appears inside a clickable parent.
- Never lower a behavior score from speculative interaction language such as
  "potentially clickable", "may be interactive", or "could be a target". Missing
  proof of clickability is not proof that a decorative element violates a touch
  target requirement; emit a verification gap when the interactive inventory is
  incomplete.
  - Reserve scores above 0.90 for deliveries with strong functional semantics,
    user-visible quality, and validation depth.
- Artifact existence is supporting evidence, not a substitute for satisfying
  the case-specific behavior rubric.
- Self-reported QA, delivery reports, or completion statements are supporting
  evidence only; they are not enough for a perfect score unless the artifacts
  themselves independently demonstrate the behavior.
- If files exist but the behavior rubric is not satisfied, assign a low score
  for that behavior.
- For each low-scored behavior, fill failure_reason, missing_capability, and
  suggested_surface_hint from visible evidence. This is only a diagnosis hint,
  not an optimization decision.
- Summarize low-scored behaviors into quality_gaps. Each quality gap should
  describe the end-to-end capability that needs more training signal.
- Include quality_gaps for passed-but-imperfect deliveries when the artifact is
  usable but still below excellent end-to-end quality.
- Propose dataset_budget from the quality gaps. Use enough cases to cover each
  distinct gap family; broad, repeated, or high-severity gaps should receive
  more cases, and narrow gaps should receive fewer cases.
- dataset_budget must cover artifact_quality_gap entries only. verification_gap
  entries describe evidence collection work and should not receive case_groups.
- The final answer must be parseable JSON.
- Keep every string concise: overall_reason <= 240 chars; behavior reason,
  failure_reason, and evidence <= 240 chars; missing_capability <= 160 chars;
  why_it_matters and data_needed_to_fix <= 240 chars. Emit at most 4
  quality_gaps and at most 4 dataset_budget.case_groups.

Surface hint guidelines:
- Use prompt_section when the failure is caused by role framing, task
  interpretation, completion policy, or a reusable written procedure.
- Use skill when the failure is caused by a missing or weak reusable
  multi-step method inside a role.
- Use tool when the failure requires a deterministic executable capability,
  structured tool call, static check, runtime check, external query,
  calculation, parser, schema handling, or repeatable validation. Keep tool as
  a likely surface when the evidence says the agent needed a check or operation
  that prose guidance alone would not reliably perform.
- likely_surfaces may include multiple candidates when evidence supports them.

File-reading discipline (context budget):
- Skim: for each path the user gave, `read_file` with a small `limit` to peek
  the head and learn the rough shape (system / user / assistant / tool turn
  pattern, error markers, whether `trace_id` is set).
- Artifact files live in the absolute path given by the prompt. Use
  `list_files` on that directory to discover them, then `read_file` each file
  you need. The tool enforces its own size and token limits; binary files are
  rejected automatically — treat such rejections as evidence limitations.
- Do NOT read case-root files such as trace.json or result.json — they are
  unbounded and will exhaust the context.

Required output schema for the complete judge result:
{
  "overall_reason": "brief overall assessment",
  "behaviors": [
    {
      "id": "behavior_id",
      "score": 0.8,
      "reason": "why this score was assigned",
      "failure_reason": "if score is low, the concrete missing or wrong behavior; otherwise empty",
      "missing_capability": "if score is low, the capability the agent lacked; otherwise empty",
      "suggested_surface_hint": "optional hint: skill|tool|prompt_section|empty",
      "evidence": "relevant evidence path or empty string"
    }
  ],
  "forbidden_hits": [
    {
      "id": "forbidden_id",
      "triggered": false,
      "reason": "why it was or was not triggered"
    }
  ],
  "quality_gaps": [
    {
      "id": "short_gap_id",
      "gap_type": "artifact_quality_gap|verification_gap",
      "dimension": "end-to-end capability area",
      "severity": "low|medium|high",
      "affected_roles": ["role name if visible, otherwise empty"],
      "likely_surfaces": ["skill|tool|prompt_section"],
      "evidence": "behavior id or evidence path",
      "why_it_matters": "how this gap reduces end-user task success",
      "missing_capability": "capability that should be improved",
      "training_signal_priority": "low|medium|high",
      "data_needed_to_fix": "what kind of synthetic cases would train this gap"
    }
  ],
  "dataset_budget": {
    "total_cases": 1,
    "case_groups": [
      {
        "source_gap": "short_gap_id",
        "case_count": 1,
        "target_roles": ["role name if visible, otherwise empty"],
        "target_surfaces": ["skill|tool|prompt_section"]
      }
    ]
  }
}
"""


class LlmAsJudgeJudger(EvaluationJudger):
    """Score one case with an LLM judge over structured behavior rubrics."""

    method = "llm_as_judge"

    def __init__(self, config: EvaluatorConfig) -> None:
        self._config = config

    async def judge(
        self,
        *,
        case: dict[str, Any],
        execution_result: CaseExecutionResult,
        output_dir: str = "",
    ) -> JudgeResult:
        """Score one response with behavior-level LLM-as-judge output."""
        if execution_result.execution_status != "passed":
            return self._failure_result(execution_result.error)

        reference = case.get("reference")
        if not isinstance(reference, dict):
            reference = {}
        behaviors = normalize_behaviors(reference.get("required_behaviors", []))
        if not behaviors:
            return JudgeResult(
                method=self.method,
                score=0.0,
                passed=False,
                reason="reference.required_behaviors is required for llm_as_judge",
            )

        forbidden = normalize_forbidden(reference.get("forbidden_behaviors", []))
        case_dir = Path(output_dir).expanduser().resolve() if output_dir else None
        artifacts_dir = "artifacts" if case_dir else ""
        trace_path = "judge/normalized_trace.json" if case_dir else "normalized_trace.json"
        prompt = build_judge_prompt(
            case=case,
            response=execution_result.response,
            behaviors=behaviors,
            forbidden=forbidden,
            trace_path=trace_path,
            artifacts_dir=artifacts_dir,
        )
        if case_dir is not None:
            web_verification = reference.get("web_verification")
            if isinstance(web_verification, dict) and web_verification.get("steps"):
                judge_dir = case_dir / "judge"
                judge_dir.mkdir(parents=True, exist_ok=True)
                (judge_dir / "web_verification.json").write_text(
                    json.dumps(web_verification, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        last_raw = ""
        max_retries = _JUDGE_JSON_PARSE_MAX_RETRIES
        for attempt in range(max_retries + 1):
            raw = await self._run_judge(output_dir=output_dir, prompt=prompt)
            last_raw = raw
            parsed = parse_judge_output(raw)
            if parsed is None:
                continue

            behavior_results = parsed.get("behaviors")
            if not isinstance(behavior_results, list):
                continue

            parsed = _discard_unanchored_contradictory_gaps(parsed)

            forbidden_hits = _attach_forbidden_penalties(
                parsed.get("forbidden_hits", []),
                forbidden,
            )
            overall = aggregate_score(behavior_results, forbidden_hits, behaviors)
            gap_ceiling = _quality_gap_score_ceiling(parsed)
            if gap_ceiling is not None:
                overall = min(overall, gap_ceiling)
            runtime_evidence = _read_artifact_runtime_evidence(case_dir)
            runtime_ceiling = _artifact_runtime_score_ceiling(runtime_evidence)
            if runtime_ceiling is not None:
                overall = min(overall, runtime_ceiling)
            dimensions = compute_dimensions(behavior_results)
            parsed_out = {
                **parsed,
                "overall_score": overall,
                "forbidden_hits": forbidden_hits,
                "dimensions": dimensions,
            }
            if gap_ceiling is not None:
                parsed_out["quality_gap_score_ceiling"] = overall
            if runtime_ceiling is not None:
                parsed_out["artifact_runtime_score_ceiling"] = runtime_ceiling
            pass_threshold = _case_success_threshold(case, self._config)
            return JudgeResult(
                method=self.method,
                score=overall,
                passed=overall >= pass_threshold,
                reason=str(parsed.get("overall_reason", "")),
                metadata={
                    "parsed": parsed_out,
                    "dimensions": dimensions,
                    "raw_output": raw,
                    "attempt": attempt,
                    "pass_threshold": pass_threshold,
                    "artifact_runtime_evidence": runtime_evidence,
                },
            )

        return JudgeResult(
            method=self.method,
            score=0.0,
            passed=False,
            reason="failed to parse llm_as_judge output",
            metadata={
                "raw_output": last_raw,
                "judge_error": True,
                "judge_error_type": "parse_failed",
                "raw_output_truncated": _judge_output_looks_truncated(last_raw),
            },
        )

    async def _run_judge(self, *, output_dir: str, prompt: str) -> str:
        """Run the configured judge agent in an isolated runtime workspace."""
        return await run_llm_judge(self._config, output_dir, prompt)


def build_judge_agent(config: EvaluatorConfig, workspace_dir: str) -> BaseAgent:
    """Create a judge DeepAgent whose workspace is *workspace_dir* (case dir).

    The judge reads ``judge/normalized_trace.json`` and ``artifacts/`` through
    workspace-relative paths while ``restrict_to_work_dir`` remains enabled.
    """
    model = load_member_optimizer_model(_judge_model_config_ref(config))
    return create_deep_agent(
        model=model,
        card=AgentCard(name="judge_agent", description="LLM-as-judge evaluator"),
        system_prompt=_JUDGE_SYSTEM_PROMPT,
        rails=[SysOperationRail()],
        workspace=workspace_dir,
        restrict_to_work_dir=True,
        max_iterations=_JUDGE_AGENT_MAX_ITERATIONS,
        auto_create_workspace=False,
    )


def _judge_model_config_ref(config: EvaluatorConfig) -> str:
    """Return the model config dedicated to LLM judge execution."""
    return str(getattr(config, "judge_model_config_ref", "") or config.model_config_ref)


def _case_success_threshold(case: dict[str, Any], config: EvaluatorConfig) -> float:
    """Return the case-specific pass threshold, falling back to evaluator config."""
    reference = case.get("reference")
    if isinstance(reference, dict):
        rubric = reference.get("judge_rubric")
        if isinstance(rubric, dict):
            threshold = rubric.get("pass_threshold")
            if isinstance(threshold, int | float) and not isinstance(threshold, bool):
                return float(threshold)
    return float(getattr(config, "success_score", 1.0))


async def run_judge_agent(agent: BaseAgent, prompt: str) -> str:
    """Run the judge agent once and return raw text output."""
    from openjiuwen.core.runner import Runner

    result = await Runner.run_agent(agent=agent, inputs={"query": prompt})
    if isinstance(result, dict):
        fallback = json.dumps(result, ensure_ascii=False)
        return str(result.get("output", result.get("answer", fallback)))
    return str(result)


def build_judge_prompt(
    *,
    case: dict[str, Any],
    response: Any,
    behaviors: list[dict[str, Any]],
    forbidden: list[dict[str, Any]],
    trace_path: str = "normalized_trace.json",
    artifacts_dir: str = "",
) -> str:
    """Construct the instruction given to the judge agent.

    Args:
        case: Case definition dict containing input, reference, etc.
        response: Agent response to evaluate.
        behaviors: Normalised required-behavior dicts.
        forbidden: Normalised forbidden-behavior dicts.
        trace_path: Workspace-relative path to the structured trace evidence.
        artifacts_dir: Workspace-relative path to the case artifacts directory.
    """
    case_input = _extract_case_input(case)
    behavior_lines = "\n".join(
        "  - id: {id}\n    description: {description}\n    weight: {weight}".format(
            id=item["id"],
            description=item["description"],
            weight=item.get("weight", 1.0),
        )
        + (f"\n    rubric: {item['rubric']}" if item.get("rubric") else "")
        for item in behaviors
    )
    forbidden_lines = _format_forbidden_lines(forbidden)
    return (
        "## Judge task\n\n"
        "### Case input\n"
        f"{_safe_str(case_input)}\n\n"
        "### Agent response\n"
        f"{_safe_str(response)}\n\n"
        "### Required behaviors\n"
        f"{behavior_lines}\n\n"
        "### Forbidden behaviors\n"
        f"{forbidden_lines}\n\n"
        "### Evidence\n"
        "Treat each required behavior as a task capability contract. The behavior description and rubric "
        "define the capability to score.\n"
        "Artifact existence is supporting evidence, not a substitute for satisfying the behavior rubric.\n"
        "If files exist but the behavior rubric is not satisfied, assign a low score for that behavior.\n"
        "self-reported QA or delivery reports are useful context but not enough for a perfect score.\n"
        "A score of 1.0 requires independent evidence that the final user-facing deliverable is complete, "
        "usable, polished, and materially defect-free for that behavior.\n"
        "If a behavior is mostly good but still has visible usability, coherence, interaction, visual, "
        "correctness, or completion gaps, score it below 1.0 and explain the gap.\n"
        "\n### End-to-end quality review\n"
        "Required behaviors are minimum task capability contracts. After behavior scoring, review whether "
        "the final deliverable achieves the user's end-to-end goal.\n"
        "First derive an independent task quality contract from the original user request: what success "
        "means, what a real user would value, and what failures would make the artifact only formally complete.\n"
        "Use that task quality contract to identify artifact_quality_gap items when the artifact itself is "
        "shallow, weak, incoherent, low-quality, or missing important user-facing capabilities.\n"
        "Use verification_gap only when the main issue is missing or insufficient evidence rather than a "
        "visible artifact weakness.\n"
        "Use the original request as the scoring authority. Treat agent-generated plans, rules, QA reports, "
        "or summaries as supporting evidence only; they cannot redefine the user's quality bar.\n"
        "Score 1.0 only when the deliverable is complete, usable, polished, coherent, and materially "
        "defect-free for the requested task.\n"
        "Review generic quality axes: functional effectiveness, interaction or effect quality when applicable, "
        "user-visible output quality, coherence and domain reasoning, runtime correctness and validation, "
        "and completion of the acceptance contract.\n"
        "Apply score ceilings when evidence shows a core weakness: core user goal or task semantics broken "
        "caps the score at 0.65; runnable but weak main experience caps it at 0.75; shallow smoke-test "
        "validation caps it at 0.85.\n"
        "For each material weakness, emit quality_gaps that explain the missing capability, why it matters "
        "to user success, the evidence, and the synthetic data needed to train it.\n"
        "For a passed-but-imperfect seed result, keep the pass decision if appropriate but still emit "
        "quality_gaps and dataset_budget so downstream generation can target the remaining weaknesses.\n\n"
        f"Read ``{trace_path}`` first (containing structured trace: messages / tool_calls[].error / step_pointer).\n"
        f"All artifacts are saved at ``{artifacts_dir}`` and can be read directly, read them when needed.\n"
        f"Use ``{artifacts_dir}/`` as the canonical artifact root. Common deliverables may be mirrored at "
        f"``{artifacts_dir}/index.html``, ``{artifacts_dir}/styles.css``, and "
        f"``{artifacts_dir}/content_brief.md``; original nested files may also remain under "
        f"``{artifacts_dir}/code/`` and ``{artifacts_dir}/docs/``.\n"
        "Do not look for deliverables in the case root; score artifact existence from the artifact root above.\n"
        "Do NOT read case-root files such as trace.json or result.json — "
        "they are unbounded and will exhaust the context.\n"
        "Return only the required JSON object."
    )


async def run_llm_judge(config: EvaluatorConfig, output_dir: str, prompt: str) -> str:
    """Run a bounded LLM judge call for one case output directory.

    ``normalized_trace.json`` is materialised into ``output_dir/judge`` as
    primary evidence.  Artifact snippets are embedded into the model prompt so
    judging does not depend on a long-running ReAct/tool loop.
    """
    case_dir = Path(output_dir).expanduser().resolve()
    judge_dir = case_dir / "judge"
    judge_dir.mkdir(parents=True, exist_ok=True)
    _prepare_judge_evidence(case_dir=case_dir, judge_dir=judge_dir)
    evidence_prompt = _build_direct_judge_prompt(base_prompt=prompt, case_dir=case_dir)

    async def call_behavior_scores() -> str:
        return await _invoke_judge_model(
            config,
            _build_behavior_scores_segment_prompt(evidence_prompt),
        )

    behavior_raw = await run_model_call_with_retries(
        call_behavior_scores,
        operation_name="llm judge",
        max_retries=int(
            getattr(
                config,
                "judge_model_call_max_retries",
                _JUDGE_MODEL_CALL_MAX_RETRIES,
            )
        ),
    )
    behavior_payload = parse_judge_output(behavior_raw)
    if not behavior_payload or not behavior_payload.get("behaviors"):
        return behavior_raw

    async def call_gap_budget() -> str:
        return await _invoke_judge_model(
            config,
            _build_gap_budget_segment_prompt(
                evidence_prompt=evidence_prompt,
                behavior_payload=behavior_payload,
            ),
        )

    gap_raw = await run_model_call_with_retries(
        call_gap_budget,
        operation_name="llm judge gap_budget",
        max_retries=int(
            getattr(
                config,
                "judge_model_call_max_retries",
                _JUDGE_MODEL_CALL_MAX_RETRIES,
            )
        ),
    )
    gap_payload = _parse_json_object_output(gap_raw)
    if gap_payload is None:
        return behavior_raw

    merged = dict(behavior_payload)
    merged["quality_gaps"] = gap_payload.get("quality_gaps", [])
    merged["dataset_budget"] = gap_payload.get("dataset_budget", {})
    if "recommended_synthetic_tasks" in gap_payload:
        merged["recommended_synthetic_tasks"] = gap_payload.get(
            "recommended_synthetic_tasks",
            [],
        )
    return json.dumps(merged, ensure_ascii=False, separators=(",", ":"))


async def _invoke_judge_model(config: EvaluatorConfig, prompt: str) -> str:
    """Invoke the configured judge model directly with bounded evidence."""
    model = load_member_optimizer_model(_judge_model_config_ref(config))
    response = await model.invoke(
        messages=[
            {
                "role": "system",
                "content": _JUDGE_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        tools=None,
        temperature=0.0,
        max_tokens=int(getattr(config, "judge_model_max_tokens", _JUDGE_MODEL_MAX_TOKENS)),
    )
    return _extract_model_text(response)


def _build_behavior_scores_segment_prompt(evidence_prompt: str) -> str:
    """Build the compact behavior-scoring judge segment prompt."""
    return (
        f"{evidence_prompt}\n\n"
        "### Judge output segment\n"
        "behavior_scores\n\n"
        "Score required behaviors and forbidden behaviors only. Return compact JSON "
        "with exactly these top-level fields: overall_reason, behaviors, forbidden_hits. "
        "Do not include quality_gaps, dataset_budget, markdown fences, or explanatory "
        "text outside the JSON object."
    )


def _build_gap_budget_segment_prompt(
    *,
    evidence_prompt: str,
    behavior_payload: dict[str, Any],
) -> str:
    """Build the compact gap-and-budget judge segment prompt."""
    behavior_json = json.dumps(
        {
            "overall_reason": behavior_payload.get("overall_reason", ""),
            "behaviors": behavior_payload.get("behaviors", []),
            "forbidden_hits": behavior_payload.get("forbidden_hits", []),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        f"{evidence_prompt}\n\n"
        "### Judge output segment\n"
        "gap_budget\n\n"
        "Use the accepted behavior score JSON below and the evidence above to emit "
        "only the remaining optimization signal. Return compact JSON with exactly "
        "these top-level fields: quality_gaps, dataset_budget. Each quality gap "
        "must include gap_type set to artifact_quality_gap or verification_gap. "
        "The accepted behavior scores are authoritative. Do not emit a gap whose "
        "severity implies a score below the lowest accepted behavior score; revise "
        "the behavior scores in the scoring segment instead of contradicting them "
        "from this downstream segment. "
        "Prefer artifact_quality_gap when evidence shows the artifact itself is "
        "weak, shallow, incoherent, incomplete, or below the task quality contract; "
        "use verification_gap when evidence is missing or too shallow to confirm "
        "quality. If the only support for a gap is artifact truncation, evidence "
        "coverage, missing runtime evidence, or an unseen file section, that gap "
        "must be verification_gap unless visible artifact evidence independently "
        "proves the delivered artifact is broken or low-quality. raw excerpt "
        "truncation alone is not missing evidence when deterministic full-file "
        "summaries are available; use those summaries before emitting a "
        "verification_gap. dataset_budget "
        "case_groups must reference artifact_quality_gap ids only; do not allocate "
        "synthetic training cases to verification_gap ids. Optionally include "
        "recommended_synthetic_tasks. Do not repeat behavior scores. Do not include "
        "markdown fences or explanatory text outside the JSON object.\n\n"
        "### Accepted behavior score JSON\n"
        f"{behavior_json}"
    )


def _parse_json_object_output(raw: str) -> dict[str, Any] | None:
    """Parse a JSON object from plain or fenced model output."""
    payload = _extract_json_object_text(raw)
    if payload is None:
        return None
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_json_object_text(raw: str) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            text = "\n".join(lines[1:])
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].strip()

    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                payload_end = index + 1
                return text[start:payload_end]
    return None


def _judge_output_looks_truncated(raw: str) -> bool:
    """Return whether a model output appears cut before a complete JSON object."""
    text = str(raw or "").strip()
    if not text:
        return False
    if text.startswith("```") and text.count("```") == 1:
        return True
    return "{" in text and _parse_json_object_output(text) is None


def _extract_model_text(response: Any) -> str:
    """Extract text content from Model.invoke response variants."""
    content = getattr(response, "content", response)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", item.get("content", ""))))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content or "")


def _build_direct_judge_prompt(*, base_prompt: str, case_dir: Path) -> str:
    """Append bounded trace and artifact evidence to the judge prompt."""
    judge_trace = case_dir / "judge" / "normalized_trace.json"
    artifacts_dir = case_dir / "artifacts"
    judge_skills = resolve_judge_skills(artifacts_dir)
    judge_skill_prompt = format_judge_skill_instructions(judge_skills)
    sections = [
        base_prompt,
        judge_skill_prompt,
        "\n\n### Bounded Evidence Bundle",
        "\n#### normalized_trace.json (judge-safe summary)",
        _read_judge_trace_excerpt(judge_trace, max_chars=_JUDGE_TRACE_MAX_CHARS),
        "\n#### artifacts",
    ]
    if not artifacts_dir.is_dir():
        sections.append("(artifacts directory missing)")
        return "\n".join(sections)

    contract_evidence = _build_deterministic_artifact_contract_evidence(artifacts_dir)
    if contract_evidence:
        sections.extend(
            [
                "\n#### Deterministic artifact contract evidence",
                json.dumps(contract_evidence, ensure_ascii=False, indent=2),
            ]
        )
    interaction_evidence = _build_deterministic_interaction_evidence(artifacts_dir)
    if interaction_evidence:
        sections.extend(
            [
                "\n#### Deterministic interaction/effect evidence",
                json.dumps(interaction_evidence, ensure_ascii=False, indent=2),
            ]
        )
    runtime_evidence = collect_artifact_runtime_evidence(
        artifacts_dir,
        case_dir / "judge" / "evidence",
        viewport_widths=_extract_requested_viewport_widths(base_prompt),
        web_verification=_read_web_verification(case_dir),
        evidence_profiles=[skill.evidence_profile for skill in judge_skills if skill.evidence_profile],
        judge_skills=[skill.name for skill in judge_skills],
        score_policy=combined_runtime_policy(judge_skills),
    )
    if runtime_evidence.get("status") != "not_applicable":
        sections.extend(
            [
                "\n#### Machine-collected artifact runtime evidence",
                json.dumps(runtime_evidence, ensure_ascii=False, indent=2),
            ]
        )
    supporting_evidence = _build_deterministic_supporting_artifact_evidence(artifacts_dir)
    if supporting_evidence:
        sections.extend(
            [
                "\n#### Deterministic supporting artifact evidence",
                json.dumps(supporting_evidence, ensure_ascii=False, indent=2),
            ]
        )
    coverage_evidence = _build_deterministic_artifact_evidence_coverage(artifacts_dir)
    if coverage_evidence:
        sections.extend(
            [
                "\n#### Deterministic artifact evidence coverage",
                json.dumps(coverage_evidence, ensure_ascii=False, indent=2),
            ]
        )

    primary_paths: list[Path] = []
    omitted_paths: list[str] = []
    for path in sorted(
        (p for p in artifacts_dir.rglob("*") if p.is_file()),
        key=lambda item: _artifact_priority_key(item, artifacts_dir),
    ):
        rel = path.relative_to(artifacts_dir).as_posix()
        if _is_support_artifact_path(path, artifacts_dir):
            continue
        if _is_self_reported_artifact(path):
            omitted_paths.append(rel)
            continue
        primary_paths.append(path)

    if omitted_paths:
        sections.append("\n#### omitted self-reported/supporting artifacts")
        sections.append("\n".join(f"- artifacts/{rel}" for rel in omitted_paths))

    remaining = _JUDGE_ARTIFACT_TOTAL_MAX_CHARS
    for path in primary_paths:
        rel = path.relative_to(artifacts_dir).as_posix()
        if remaining <= 0:
            sections.append("\n[artifact evidence budget exhausted]")
            break
        body_max_chars = _artifact_body_max_chars(
            path,
            artifacts_dir,
            remaining=remaining,
        )
        excerpt = _read_text_excerpt(path, max_chars=body_max_chars)
        remaining -= min(len(excerpt), body_max_chars)
        sections.append(f"\n##### artifacts/{rel}\n{excerpt}")
    return "\n".join(sections)


def _read_web_verification(case_dir: Path) -> dict[str, Any] | None:
    path = case_dir / "judge" / "web_verification.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _extract_requested_viewport_widths(case_or_prompt: Any) -> list[int]:
    """Extract bounded CSS viewport widths explicitly named by the case."""
    text = _safe_str(case_or_prompt)
    widths = {
        int(match)
        for match in re.findall(r"(?<!\d)(\d{3,4})\s*px\b", text, flags=re.IGNORECASE)
        if 240 <= int(match) <= 4096
    }
    return sorted(widths)[:4]


def _is_self_reported_artifact(path: Path) -> bool:
    """Return true for agent-authored proof-of-work files, not final deliverables."""
    stem = path.stem.lower().replace("-", "_")
    return stem in _SELF_REPORTED_ARTIFACT_STEMS


def _is_support_artifact_path(path: Path, artifacts_dir: Path) -> bool:
    """Return true for dependency/cache files that should not consume judge budget."""
    try:
        parts = path.relative_to(artifacts_dir).parts
    except ValueError:
        parts = path.parts
    return any(part.lower() in _SUPPORT_ARTIFACT_DIR_NAMES for part in parts)


def _artifact_priority_key(path: Path, artifacts_dir: Path) -> tuple[int, str]:
    """Sort final deliverables before supporting files for bounded judge evidence."""
    try:
        rel = path.relative_to(artifacts_dir).as_posix()
    except ValueError:
        rel = path.as_posix()
    name = path.name.lower()
    if name in _PRIMARY_ARTIFACT_NAMES:
        return (0, rel)
    if path.suffix.lower() in {".html", ".css", ".js"}:
        return (1, rel)
    if path.suffix.lower() in {".md", ".json", ".yaml", ".yml", ".txt"}:
        return (2, rel)
    return (3, rel)


def _build_deterministic_artifact_evidence_coverage(artifacts_dir: Path) -> dict[str, Any]:
    """Build generic structure and coverage evidence for bounded judge context."""
    if not artifacts_dir.is_dir():
        return {}

    manifest: list[dict[str, Any]] = []
    files_truncated: list[str] = []
    omitted_self_reported: list[str] = []
    html_summaries: list[dict[str, Any]] = []
    js_summaries: list[dict[str, Any]] = []
    css_summaries: list[dict[str, Any]] = []

    for path in sorted(
        (item for item in artifacts_dir.rglob("*") if item.is_file()),
        key=lambda item: _artifact_priority_key(item, artifacts_dir),
    ):
        if _is_support_artifact_path(path, artifacts_dir):
            continue
        rel = path.relative_to(artifacts_dir).as_posix()
        if _is_self_reported_artifact(path):
            omitted_self_reported.append(f"artifacts/{rel}")
            continue

        text = _read_artifact_text(path)
        char_count = len(text) if text is not None else None
        body_max_chars = _artifact_body_max_chars(
            path,
            artifacts_dir,
            remaining=_JUDGE_ARTIFACT_TOTAL_MAX_CHARS,
        )
        excerpt_truncated = bool(text is not None and len(text) > body_max_chars)
        if excerpt_truncated:
            files_truncated.append(f"artifacts/{rel}")
        manifest.append(
            {
                "artifact": f"artifacts/{rel}",
                "bytes": _safe_file_size(path),
                "chars": char_count,
                "suffix": path.suffix.lower(),
                "text_readable": text is not None,
                "excerpt_truncated": excerpt_truncated,
                "full_file_static_summary_available": bool(
                    text is not None and path.suffix.lower() in {".html", ".htm", ".js", ".css"}
                ),
                "raw_body_supporting_only": _is_self_reported_artifact(path),
            }
        )
        if text is None:
            continue
        suffix = path.suffix.lower()
        if suffix in {".html", ".htm"}:
            html_summaries.append(_summarize_html_artifact(text, rel))
        elif suffix == ".js":
            js_summaries.append(_summarize_js_artifact(text, rel))
        elif suffix == ".css":
            css_summaries.append(_summarize_css_artifact(text, rel))

    confidence = "high"
    if any(not item.get("text_readable") for item in manifest):
        confidence = "medium"
    if files_truncated:
        confidence = "medium"
    evidence: dict[str, Any] = {
        "artifact_manifest": manifest[:40],
        "coverage": {
            "files_truncated": files_truncated[:20],
            "omitted_self_reported_artifacts": omitted_self_reported[:20],
            "evidence_confidence": confidence,
            "truncation_routing_rule": (
                "Raw excerpt truncation means the prompt body is clipped. For readable "
                "HTML/CSS/JS files, deterministic summaries are computed from the full "
                "file and should be used as evidence before emitting verification_gap."
            ),
        },
        "runtime_evidence": {
            "browser_or_app_execution": "not_collected_by_static_evidence_collector",
            "console_errors": "not_collected",
        },
    }
    web_structure: dict[str, Any] = {}
    if html_summaries:
        web_structure["html"] = html_summaries[:8]
    if js_summaries:
        web_structure["javascript"] = js_summaries[:8]
    if css_summaries:
        web_structure["css"] = css_summaries[:8]
    if web_structure:
        evidence["web_structure_summary"] = web_structure
    return evidence


def _build_deterministic_supporting_artifact_evidence(
    artifacts_dir: Path,
) -> dict[str, Any]:
    """Summarize self-reported/supporting artifacts without embedding raw claims."""
    if not artifacts_dir.is_dir():
        return {}

    summaries: list[dict[str, Any]] = []
    for path in sorted(
        (item for item in artifacts_dir.rglob("*") if item.is_file()),
        key=lambda item: _artifact_priority_key(item, artifacts_dir),
    ):
        if _is_support_artifact_path(path, artifacts_dir):
            continue
        if not _is_self_reported_artifact(path):
            continue
        text = _read_artifact_text(path)
        rel = path.relative_to(artifacts_dir).as_posix()
        summary: dict[str, Any] = {
            "artifact": f"artifacts/{rel}",
            "bytes": _safe_file_size(path),
            "text_readable": text is not None,
            "raw_body_embedded": False,
            "evidence_role": (
                "supporting_only; use as evidence of reported checks or claims, "
                "not as independent proof of artifact quality"
            ),
        }
        if text is not None:
            summary.update(
                {
                    "chars": len(text),
                    "headings": _extract_markdown_headings(text)[:20],
                    "validation_terms": _present_terms(
                        text,
                        (
                            "test",
                            "tests",
                            "qa",
                            "validation",
                            "validate",
                            "check",
                            "checked",
                            "browser",
                            "headless",
                            "playwright",
                            "screenshot",
                            "console",
                            "node",
                            "lint",
                            "syntax",
                            "unit",
                            "e2e",
                            "验证",
                            "测试",
                            "检查",
                            "浏览器",
                            "截图",
                            "控制台",
                            "语法",
                        ),
                    )[:30],
                    "issue_terms": _present_terms(
                        text,
                        (
                            "bug",
                            "defect",
                            "issue",
                            "error",
                            "fail",
                            "failed",
                            "fixed",
                            "warning",
                            "blocking",
                            "缺陷",
                            "错误",
                            "失败",
                            "修复",
                            "阻塞",
                            "警告",
                        ),
                    )[:30],
                }
            )
        summaries.append(summary)

    if not summaries:
        return {}
    return {
        "supporting_artifacts": summaries[:20],
        "raw_body_policy": (
            "Raw self-reported report bodies are not embedded. Use these summaries "
            "to know what evidence exists, then rely on primary artifacts and "
            "deterministic checks for scoring."
        ),
    }


def _artifact_body_max_chars(
    path: Path,
    artifacts_dir: Path,
    *,
    remaining: int,
) -> int:
    if _is_primary_judge_artifact(path, artifacts_dir):
        return max(0, min(_JUDGE_PRIMARY_ARTIFACT_MAX_CHARS, remaining))
    return max(0, min(_JUDGE_ARTIFACT_MAX_CHARS, remaining))


def _is_primary_judge_artifact(path: Path, artifacts_dir: Path) -> bool:
    if _is_support_artifact_path(path, artifacts_dir):
        return False
    if _is_self_reported_artifact(path):
        return False
    name = path.name.lower()
    if name in _PRIMARY_ARTIFACT_NAMES:
        return True
    return path.suffix.lower() in {".html", ".htm", ".css", ".js"}


def _safe_file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _summarize_html_artifact(text: str, rel: str) -> dict[str, Any]:
    return {
        "artifact": f"artifacts/{rel}",
        "ids": sorted(_extract_html_ids(text))[:80],
        "scripts": sorted(_extract_html_script_srcs(text))[:30],
        "stylesheets": sorted(_extract_html_stylesheet_hrefs(text))[:30],
        "interactive_controls": _extract_interactive_controls(text)[:20],
        "headings": _extract_html_headings(text)[:20],
    }


def _summarize_js_artifact(text: str, rel: str) -> dict[str, Any]:
    function_names = _extract_js_function_names(text)
    event_types = _extract_event_types("", text)
    dom_refs = _extract_js_dom_id_refs(text)
    return {
        "artifact": f"artifacts/{rel}",
        "function_count": len(function_names),
        "function_names": function_names[:80],
        "event_types": sorted(set(event_types))[:30],
        "event_handler_count": len(event_types),
        "dom_id_refs": sorted(dom_refs)[:80],
        "choice_or_target_terms": _present_terms(
            text,
            ("select", "selected", "target", "choose", "choice", "option", "active", "focus"),
        )[:20],
        "state_or_outcome_terms": _present_terms(
            text,
            (
                "state",
                "phase",
                "turn",
                "status",
                "result",
                "score",
                "win",
                "lose",
                "progress",
                "step",
            ),
        )[:20],
    }


def _summarize_css_artifact(text: str, rel: str) -> dict[str, Any]:
    selectors = _extract_css_selectors(text)
    return {
        "artifact": f"artifacts/{rel}",
        "selector_count": len(selectors),
        "selector_sample": selectors[:80],
        "media_queries": _extract_css_media_queries(text)[:40],
        "pseudo_classes": _extract_css_pseudo_classes(text)[:40],
        "keyframes": _extract_css_keyframes(text)[:40],
    }


def _extract_html_stylesheet_hrefs(text: str) -> set[str]:
    stylesheets: set[str] = set()
    for attrs in re.findall(r"""<link\b([^>]*)>""", text, re.I | re.S):
        rel = _extract_attr(attrs, "rel").lower()
        href = _extract_attr(attrs, "href")
        if href and "stylesheet" in rel:
            stylesheets.add(href)
    return stylesheets


def _extract_html_headings(text: str) -> list[str]:
    headings: list[str] = []
    for match in re.finditer(r"""<h([1-6])\b[^>]*>(.*?)</h\1>""", text, re.I | re.S):
        heading = _strip_html_tags(match.group(2))
        if heading:
            headings.append(f"h{match.group(1)}: {_clip_line(heading, 120)}")
    return headings


def _extract_markdown_headings(text: str) -> list[str]:
    headings: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        heading = stripped.lstrip("#").strip()
        if heading:
            headings.append(_clip_line(heading, 120))
    return headings


def _extract_js_function_names(text: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name and name not in seen:
            seen.add(name)
            names.append(name)

    for name in re.findall(r"""\bfunction\s+([A-Za-z_$][\w$]*)\s*\(""", text):
        add(name)
    for name, _params, _body in _iter_js_function_bodies(text):
        add(name)
    for name in re.findall(
        r"""\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?"""
        r"""(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)""",
        text,
    ):
        add(name)
    for name in re.findall(
        r"""\b([A-Za-z_$][\w$]*)\s*:\s*(?:async\s*)?"""
        r"""(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)""",
        text,
    ):
        add(name)
    return names


def _extract_css_media_queries(text: str) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()
    for query in re.findall(r"""@media\s+([^{]+)\{""", text, re.I):
        normalized = " ".join(query.split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            queries.append(normalized)
    return queries


def _extract_css_selectors(text: str) -> list[str]:
    selectors: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"""(^|[{}])\s*([^@{}][^{}]{0,180}?)\s*\{""", text, re.M):
        raw = " ".join(match.group(2).split())
        if not raw:
            continue
        for selector in raw.split(","):
            normalized = selector.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                selectors.append(normalized)
    return selectors


def _extract_css_pseudo_classes(text: str) -> list[str]:
    return sorted(set(re.findall(r""":{1,2}([A-Za-z-]+)""", text)))


def _extract_css_keyframes(text: str) -> list[str]:
    return sorted(set(re.findall(r"""@keyframes\s+([A-Za-z_-][\w-]*)""", text, re.I)))


def _read_judge_trace_excerpt(path: Path, *, max_chars: int) -> str:
    """Read a judge-safe trace summary without self-reported artifact bodies."""
    if not path.is_file():
        return f"[missing: {path.name}]"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return f"[unreadable normalized trace summary: {exc}]"
    summary = _sanitize_normalized_trace_for_judge(data)
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n[truncated: kept {max_chars} of {len(text)} chars]"


def _sanitize_normalized_trace_for_judge(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"trace_summary_error": "normalized_trace is not a JSON object"}
    traces = data.get("traces")
    if not isinstance(traces, list):
        return {"trace_summary_error": "normalized_trace.traces is not a list"}

    summarized_traces: list[dict[str, Any]] = []
    for trace in traces[:20]:
        if not isinstance(trace, dict):
            continue
        summarized: dict[str, Any] = {}
        for key in (
            "trace_id",
            "member_id",
            "member_role",
            "execution_id",
            "step_count",
            "message_count",
        ):
            if key in trace:
                summarized[key] = trace.get(key)
        messages = trace.get("messages")
        if isinstance(messages, list):
            message_summary = _summarize_trace_messages_for_judge(messages)
            if message_summary:
                summarized["message_summary"] = message_summary
        summarized_traces.append(summarized)
    return {"traces": summarized_traces}


def _summarize_trace_messages_for_judge(messages: list[Any]) -> dict[str, Any]:
    artifact_harvests: list[str] = []
    errors: list[str] = []
    omitted_self_report_messages = 0

    for message in messages[:80]:
        if not isinstance(message, dict):
            continue
        content = str(message.get("content") or "")
        if "artifact_harvest:" in content:
            artifact_harvests.append(_clip_line(content, 500))
            continue
        if _contains_self_report_reference(content):
            omitted_self_report_messages += 1
            continue
        if _looks_like_error_message(content):
            errors.append(_clip_line(content, 500))
        error = message.get("error")
        if error:
            errors.append(_clip_line(json.dumps(error, ensure_ascii=False), 500))

    result: dict[str, Any] = {}
    if artifact_harvests:
        result["artifact_harvests"] = artifact_harvests[:5]
    if errors:
        result["errors"] = errors[:10]
    if omitted_self_report_messages:
        result["omitted_self_report_message_count"] = omitted_self_report_messages
    return result


def _contains_self_report_reference(text: str) -> bool:
    normalized = text.lower().replace("-", "_")
    return any(f"{stem}.md" in normalized or stem in normalized for stem in _SELF_REPORTED_ARTIFACT_STEMS)


def _looks_like_error_message(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ("error", "exception", "traceback", "failed"))


def _clip_line(text: str, max_chars: int) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars] + f"... [truncated {len(compact) - max_chars} chars]"


def _build_deterministic_artifact_contract_evidence(artifacts_dir: Path) -> dict[str, Any]:
    """Build lightweight deterministic evidence about final artifact consistency."""
    html_files = sorted(artifacts_dir.rglob("*.html"))
    js_files = sorted(artifacts_dir.rglob("*.js"))
    if not html_files:
        return {}

    html_ids: set[str] = set()
    scripts: set[str] = set()
    for html_path in html_files:
        text = _read_artifact_text(html_path)
        if text is None:
            continue
        html_ids.update(_extract_html_ids(text))
        scripts.update(_extract_html_script_srcs(text))

    evidence: dict[str, Any] = {
        "html_id_count": len(html_ids),
    }
    missing_script_files = []
    for script in scripts:
        if script and not _is_external_url(script) and not (artifacts_dir / script).is_file():
            missing_script_files.append(script)
    missing_script_files.sort()
    if missing_script_files:
        evidence["missing_script_files"] = missing_script_files

    web_contracts: list[dict[str, Any]] = []
    for js_path in js_files:
        text = _read_artifact_text(js_path)
        if text is None:
            continue
        referenced_ids = _extract_js_dom_id_refs(text)
        missing_ids = sorted(item for item in referenced_ids if item not in html_ids)
        if not referenced_ids and not missing_ids:
            continue
        web_contracts.append(
            {
                "artifact": js_path.relative_to(artifacts_dir).as_posix(),
                "referenced_dom_ids": sorted(referenced_ids)[:40],
                "missing_dom_ids": missing_ids[:40],
                "status": "material_runtime_blocker" if missing_ids else "consistent",
            }
        )

    if web_contracts:
        evidence["web_dom_contracts"] = web_contracts
    return evidence


def _build_deterministic_interaction_evidence(artifacts_dir: Path) -> dict[str, Any]:
    """Build generic static evidence about interactive/effect surface depth."""
    html_files = sorted(
        path for path in artifacts_dir.rglob("*.html") if not _is_support_artifact_path(path, artifacts_dir)
    )
    js_files = sorted(
        path for path in artifacts_dir.rglob("*.js") if not _is_support_artifact_path(path, artifacts_dir)
    )
    if not html_files and not js_files:
        return {}

    html_parts: list[str] = []
    for path in html_files:
        text = _read_artifact_text(path)
        if text is not None:
            html_parts.append(text)
    html_text = "\n".join(html_parts)

    js_parts: list[str] = []
    for path in js_files:
        text = _read_artifact_text(path)
        if text is not None:
            js_parts.append(text)
    js_text = "\n".join(js_parts)
    if not html_text and not js_text:
        return {}

    controls = _extract_interactive_controls(html_text)
    event_types = _extract_event_types(html_text, js_text)
    effect_handler_count = _count_effect_handlers(js_text)
    selection_terms = _present_terms(
        js_text,
        ("select", "selected", "target", "choose", "choice", "option", "active", "focus"),
    )
    state_terms = _present_terms(
        js_text,
        ("state", "phase", "turn", "status", "result", "score", "win", "lose", "progress", "step"),
    )
    closure = _build_interaction_closure_evidence(
        controls=controls,
        html_text=html_text,
        js_text=js_text,
    )

    evidence: dict[str, Any] = {
        "interactive_control_count": len(controls),
        "interactive_controls_sample": controls[:12],
        "event_handler_count": len(event_types),
        "event_types": sorted(set(event_types))[:12],
        "effect_handler_count": effect_handler_count,
        "state_or_outcome_terms": state_terms[:12],
        "choice_or_target_terms": selection_terms[:12],
        "interaction_closure": closure,
        "decision_surface_hint": _interaction_decision_surface_hint(
            control_count=len(controls),
            event_handler_count=len(event_types),
            selection_terms=selection_terms,
            state_terms=state_terms,
        ),
    }
    return evidence


def _build_interaction_closure_evidence(
    *,
    controls: list[str],
    html_text: str,
    js_text: str,
) -> dict[str, Any]:
    handler_windows = _extract_event_handler_windows(js_text)
    state_mutation_paths = [window for window in handler_windows if _contains_state_mutation(window)]
    dom_update_paths = [window for window in handler_windows if _contains_dom_update(window)]
    render_call_paths = [window for window in handler_windows if _contains_render_call(window)]
    terminal_outcome_paths = [window for window in handler_windows if _contains_terminal_outcome(window)]
    potential_noop_controls = _potential_noop_controls(
        controls=controls,
        html_text=html_text,
        js_text=js_text,
    )
    closure = {
        "event_entry_count": len(handler_windows),
        "state_mutation_path_count": len(state_mutation_paths),
        "dom_update_path_count": len(dom_update_paths),
        "render_call_path_count": len(render_call_paths),
        "terminal_outcome_path_count": len(terminal_outcome_paths),
        "potential_noop_controls": potential_noop_controls[:12],
        "phase_lock_signals": _present_terms(
            js_text,
            (
                "disabled",
                "phase",
                "turn",
                "lock",
                "locked",
                "busy",
                "pending",
                "processing",
                "loading",
                "pointerevents",
            ),
        )[:12],
        "selection_signals": _present_terms(
            js_text,
            ("select", "selected", "target", "choice", "choose", "active", "focus"),
        )[:12],
    }
    closure["closure_hint"] = _interaction_closure_hint(
        control_count=len(controls),
        event_entry_count=len(handler_windows),
        state_mutation_path_count=len(state_mutation_paths),
        dom_update_path_count=len(dom_update_paths),
        terminal_outcome_path_count=len(terminal_outcome_paths),
        potential_noop_controls=potential_noop_controls,
    )
    return closure


def _extract_event_handler_windows(js_text: str) -> list[str]:
    windows: list[str] = []
    function_bodies = {name: body for name, _params, body in _iter_js_function_bodies(js_text)}
    event_pattern = re.compile(r"""addEventListener\(\s*["'][A-Za-z][\w:-]*["']""")
    for match in event_pattern.finditer(js_text):
        window_start = match.start()
        window_end = min(len(js_text), window_start + 1400)
        window = js_text[window_start:window_end]
        windows.append(_expand_called_function_bodies(window, function_bodies))
    return windows


def _expand_called_function_bodies(
    seed: str,
    function_bodies: dict[str, str],
    *,
    max_depth: int = 3,
) -> str:
    expanded = seed
    included: set[str] = set()
    for _ in range(max_depth):
        added = False
        for name, body in function_bodies.items():
            if name in included:
                continue
            if not re.search(rf"""\b{re.escape(name)}\s*\(""", expanded):
                continue
            included.add(name)
            expanded += f"\n/* function {name} */\n{body}"
            added = True
        if not added:
            break
    return expanded


def _contains_state_mutation(text: str) -> bool:
    mutation_patterns = (
        r"""\b[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+\s*(?:[+\-*/%]?=|\+\+|--)""",
        r"""\b[A-Za-z_$][\w$]*\[[^\]]+\]\s*(?:[+\-*/%]?=|\+\+|--)""",
        r"""\.(?:push|pop|shift|unshift|splice|sort|reverse|set|add|delete)\s*\(""",
    )
    lowered = text.lower()
    if any(term in lowered for term in ("state", "phase", "turn", "status", "score")):
        return any(re.search(pattern, text) for pattern in mutation_patterns)
    return any(re.search(pattern, text) for pattern in mutation_patterns)


def _contains_dom_update(text: str) -> bool:
    terms = (
        ".textContent",
        ".innerHTML",
        ".classList",
        ".disabled",
        ".style",
        ".value",
        "appendChild",
        "removeChild",
        "setAttribute",
        "removeAttribute",
    )
    return any(term in text for term in terms)


def _contains_render_call(text: str) -> bool:
    return bool(
        re.search(
            r"""\b(?:render|update|draw|refresh|sync|show|hide)[A-Za-z0-9_$]*\s*\(""",
            text,
            re.I,
        )
    )


def _contains_terminal_outcome(text: str) -> bool:
    return bool(
        re.search(
            r"""\b(?:win|lose|lost|won|success|failure|failed|complete|completed|done|finish|finished|"""
            r"""result|restart|reset)\b""",
            text,
            re.I,
        )
    )


def _potential_noop_controls(
    *,
    controls: list[str],
    html_text: str,
    js_text: str,
) -> list[str]:
    del html_text
    noop: list[str] = []
    for control in controls:
        identifier = _control_identifier(control)
        if identifier and _control_identifier_is_bound(identifier, js_text):
            continue
        noop.append(control)
    return noop


def _control_identifier(control: str) -> str:
    match = re.search(r"""\b(?:id|name)=([^\s]+)""", control)
    return match.group(1) if match else ""


def _control_identifier_is_bound(identifier: str, js_text: str) -> bool:
    escaped = re.escape(identifier)
    patterns = (
        rf"""getElementById\(\s*["']{escaped}["']\s*\)""",
        rf"""\$\(\s*["']{escaped}["']\s*\)""",
        rf"""querySelector(?:All)?\(\s*["']#{escaped}["']""",
        rf"""["']#{escaped}["']""",
        rf"""\b{escaped}\.addEventListener\(""",
    )
    return any(re.search(pattern, js_text) for pattern in patterns)


def _interaction_closure_hint(
    *,
    control_count: int,
    event_entry_count: int,
    state_mutation_path_count: int,
    dom_update_path_count: int,
    terminal_outcome_path_count: int,
    potential_noop_controls: list[str],
) -> str:
    if control_count > 0 and potential_noop_controls:
        return "controls_without_event_binding"
    if control_count > 0 and event_entry_count == 0:
        return "controls_without_event_binding"
    if event_entry_count == 0:
        return "no_event_handlers_detected"
    if state_mutation_path_count and dom_update_path_count and terminal_outcome_path_count:
        return "action_state_feedback_outcome_detected"
    if state_mutation_path_count and dom_update_path_count:
        return "action_state_feedback_detected"
    if state_mutation_path_count:
        return "state_without_visible_feedback"
    if dom_update_path_count:
        return "effect_without_state_transition"
    return "shallow_interaction_surface"


def _extract_interactive_controls(html_text: str) -> list[str]:
    controls: list[str] = []
    for match in re.finditer(r"<button\b([^>]*)>(.*?)</button>", html_text, re.I | re.S):
        attrs = match.group(1)
        text = _strip_html_tags(match.group(2))
        controls.append(_format_control_label("button", attrs, text))
    for tag in ("input", "select", "textarea"):
        for match in re.finditer(rf"<{tag}\b([^>]*)>", html_text, re.I):
            controls.append(_format_control_label(tag, match.group(1), ""))
    for match in re.finditer(r"<a\b([^>]*)>(.*?)</a>", html_text, re.I | re.S):
        attrs = match.group(1)
        if "href" not in attrs.lower():
            continue
        controls.append(_format_control_label("link", attrs, _strip_html_tags(match.group(2))))
    return [item for item in controls if item]


def _format_control_label(kind: str, attrs: str, text: str) -> str:
    attr_id = _extract_attr(attrs, "id")
    attr_name = _extract_attr(attrs, "name")
    attr_type = _extract_attr(attrs, "type")
    parts = [kind]
    if attr_type:
        parts.append(f"type={attr_type}")
    if attr_id:
        parts.append(f"id={attr_id}")
    elif attr_name:
        parts.append(f"name={attr_name}")
    if text:
        parts.append(f"text={_clip_line(text, 80)}")
    return " ".join(parts)


def _extract_attr(attrs: str, name: str) -> str:
    match = re.search(rf"""\b{name}\s*=\s*["']([^"']+)["']""", attrs, re.I)
    return match.group(1).strip() if match else ""


def _strip_html_tags(text: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", text).split())


def _extract_event_types(html_text: str, js_text: str) -> list[str]:
    events: list[str] = []
    events.extend(event.lower() for event in re.findall(r"""addEventListener\(\s*["']([A-Za-z][\w:-]*)["']""", js_text))
    events.extend(event.lower() for event in re.findall(r"""\son([a-z]+)\s*=""", html_text, re.I))
    return events


def _count_effect_handlers(js_text: str) -> int:
    event_pattern = re.compile(r"""addEventListener\(\s*["'][A-Za-z][\w:-]*["']""")
    effect_terms = (
        ".textContent",
        ".innerHTML",
        ".classList",
        ".disabled",
        ".style",
        ".value",
        "appendChild",
        "removeChild",
        "setAttribute",
        "removeAttribute",
    )
    count = 0
    for match in event_pattern.finditer(js_text):
        window_start = match.start()
        window_end = min(len(js_text), window_start + 700)
        window = js_text[window_start:window_end]
        if any(term in window for term in effect_terms):
            count += 1
    return count


def _present_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    return [term for term in terms if re.search(rf"\b{re.escape(term)}\w*\b", lowered)]


def _interaction_decision_surface_hint(
    *,
    control_count: int,
    event_handler_count: int,
    selection_terms: list[str],
    state_terms: list[str],
) -> str:
    if control_count == 0 and event_handler_count == 0:
        return "no interactive controls or event handlers detected"
    if control_count > 0 and event_handler_count == 0:
        return "controls exist but no JavaScript event handlers were detected"
    if not state_terms:
        return "event handlers exist but little explicit state or outcome vocabulary was detected"
    if not selection_terms and control_count <= 3:
        return "limited explicit choice, selection, or target affordance detected"
    return "interactive controls, event handlers, and state/effect vocabulary detected"


def _read_artifact_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _extract_html_ids(text: str) -> set[str]:
    return set(re.findall(r"""\bid\s*=\s*["']([^"']+)["']""", text))


def _extract_html_script_srcs(text: str) -> set[str]:
    return set(re.findall(r"""<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""", text, re.I))


def _is_external_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith(("http://", "https://", "//", "data:"))


def _extract_js_dom_id_refs(text: str) -> set[str]:
    ids: set[str] = set()
    ids.update(re.findall(r"""(?:getElementById|\$)\(\s*["']([^"'`]+)["']\s*\)""", text))
    ids.update(re.findall(r"""querySelector(?:All)?\(\s*["']#([A-Za-z][\w:.-]*)""", text))
    ids.update(_extract_template_dom_id_refs(text))
    return {item for item in ids if item and not _is_external_url(item)}


def _extract_template_dom_id_refs(text: str) -> set[str]:
    refs: set[str] = set()
    for func_name, params, body in _iter_js_function_bodies(text):
        if not params:
            continue
        first_param = params[0]
        suffixes = set(
            re.findall(
                rf"`\$\{{\s*{re.escape(first_param)}\s*\}}([^`$]+)`",
                body,
            )
        )
        if not suffixes:
            continue
        literal_args = set(
            re.findall(
                rf"""\b{re.escape(func_name)}\(\s*["']([^"']+)["']""",
                text,
            )
        )
        for arg in literal_args:
            for suffix in suffixes:
                refs.add(f"{arg}{suffix}")
    return refs


def _iter_js_function_bodies(text: str) -> list[tuple[str, list[str], str]]:
    functions: list[tuple[str, list[str], str]] = []
    pattern = re.compile(r"\bfunction\s+(\w+)\s*\(([^)]*)\)\s*\{")
    for match in pattern.finditer(text):
        start = match.end() - 1
        end = _find_matching_brace(text, start)
        if end is None:
            continue
        params = [item.strip() for item in match.group(2).split(",") if item.strip()]
        body_start = start + 1
        functions.append((match.group(1), params, text[body_start:end]))
    return functions


def _find_matching_brace(text: str, start: int) -> int | None:
    depth = 0
    in_string: str | None = None
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == in_string:
                in_string = None
            continue
        if char in ("'", '"', "`"):
            in_string = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _read_text_excerpt(path: Path, *, max_chars: int) -> str:
    """Read a UTF-8 text excerpt without locale fallback."""
    if not path.is_file():
        return f"[missing: {path.name}]"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return f"[unreadable: {path.name}: {exc}]"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return f"[non-utf8 or binary artifact omitted: {path.name}; bytes={len(raw)}]"
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n[truncated: kept {max_chars} of {len(text)} chars]"


def _format_forbidden_lines(forbidden: list[dict[str, Any]]) -> str:
    if not forbidden:
        return "  (none)"
    return "\n".join(
        "  - id: {id}\n    description: {description}\n    penalty: {penalty}".format(
            id=item["id"],
            description=item["description"],
            penalty=item.get("penalty", 0.3),
        )
        for item in forbidden
    )


def _extract_case_input(case: dict[str, Any]) -> Any:
    for key in ("input", "inputs", "task_input", "query", "prompt"):
        if key in case:
            value = case[key]
            if key == "input" and isinstance(value, dict) and set(value) == {"user_message"}:
                return value["user_message"]
            return value
    return case


def _safe_str(obj: Any) -> str:
    if isinstance(obj, str):
        return obj
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(obj)


def _attach_forbidden_penalties(
    raw_hits: Any,
    forbidden: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(raw_hits, list):
        return []
    penalty_map = {str(item["id"]): float(item.get("penalty", 0.3)) for item in forbidden}
    hits: list[dict[str, Any]] = []
    for hit in raw_hits:
        if not isinstance(hit, dict):
            continue
        item = dict(hit)
        item_id = str(item.get("id", ""))
        item.setdefault("penalty", penalty_map.get(item_id, 0.3))
        hits.append(item)
    return hits


def _quality_gap_score_ceiling(parsed: dict[str, Any]) -> float | None:
    """Return an overall-score ceiling implied by judge-reported quality gaps."""
    gaps = parsed.get("quality_gaps")
    if not isinstance(gaps, list):
        return None

    ceiling: float | None = None
    severity_ceilings = {
        "high": 0.74,
        "medium": 0.89,
        "low": 0.95,
    }
    for gap in gaps:
        if not isinstance(gap, dict):
            continue
        if not str(gap.get("id", "") or gap.get("dimension", "") or "").strip():
            continue
        severity = str(gap.get("severity", "") or "").strip().lower()
        if severity == "high" and _is_core_quality_gap(gap):
            gap_ceiling = 0.65
        else:
            gap_ceiling = severity_ceilings.get(severity, 0.92)
        ceiling = gap_ceiling if ceiling is None else min(ceiling, gap_ceiling)

    return ceiling


def _discard_unanchored_contradictory_gaps(
    parsed: dict[str, Any],
) -> dict[str, Any]:
    """Remove gap-budget claims that contradict the accepted score segment.

    Behavior scoring and gap generation are separate model calls. The latter may
    discover additional weaknesses, but it must not silently overrule every
    accepted behavior score. A severity ceiling below the minimum behavior score
    is internally inconsistent and is retained only for audit.
    """
    behaviors = parsed.get("behaviors")
    gaps = parsed.get("quality_gaps")
    if not isinstance(behaviors, list) or not isinstance(gaps, list):
        return parsed

    scored: list[tuple[str, float]] = []
    behavior_by_id: dict[str, dict[str, Any]] = {}
    for item in behaviors:
        if not isinstance(item, dict):
            continue
        behavior_id = str(item.get("id", "")).strip()
        score = item.get("score")
        if behavior_id:
            behavior_by_id[behavior_id] = item
        if behavior_id and isinstance(score, (int, float)):
            scored.append((behavior_id, float(score)))
    if not scored:
        return parsed

    minimum_behavior_score = min(score for _, score in scored)
    kept: list[dict[str, Any]] = []
    discarded: list[dict[str, Any]] = []
    severity_normalized = False
    severity_ceilings = {"high": 0.74, "medium": 0.89, "low": 0.95}
    for gap in gaps:
        if not isinstance(gap, dict):
            continue
        severity = str(gap.get("severity", "")).strip().lower()
        implied_ceiling = (
            0.65 if severity == "high" and _is_core_quality_gap(gap) else severity_ceilings.get(severity, 0.92)
        )
        if implied_ceiling < minimum_behavior_score:
            anchored_behavior = behavior_by_id.get(str(gap.get("dimension", "")).strip())
            normalized_severity = _normalized_gap_severity(
                gap,
                minimum_behavior_score=minimum_behavior_score,
            )
            if (
                anchored_behavior is not None
                and _behavior_supports_quality_gap(anchored_behavior)
                and normalized_severity is not None
            ):
                normalized = dict(gap)
                normalized["original_severity"] = severity
                normalized["severity"] = normalized_severity
                normalized["consistency_status"] = "severity_normalized_to_behavior_scores"
                normalized["consistency_reason"] = (
                    f"severity reduced from {severity} to {normalized_severity} "
                    f"to remain consistent with minimum accepted behavior score "
                    f"{minimum_behavior_score:.2f}"
                )
                kept.append(normalized)
                severity_normalized = True
                continue
            rejected = dict(gap)
            rejected["consistency_status"] = "discarded_unanchored_contradiction"
            rejected["consistency_reason"] = (
                f"gap ceiling {implied_ceiling:.2f} is below minimum accepted "
                f"behavior score {minimum_behavior_score:.2f}"
            )
            discarded.append(rejected)
            continue
        kept.append(gap)

    if not discarded and not severity_normalized:
        return parsed

    sanitized = dict(parsed)
    sanitized["quality_gaps"] = kept
    if not discarded:
        return sanitized
    sanitized["discarded_quality_gaps"] = discarded
    sanitized["discarded_overall_reason"] = str(parsed.get("overall_reason", ""))
    sanitized["overall_reason"] = "Accepted behavior scores retained; contradictory gap-budget claims were discarded."
    budget = parsed.get("dataset_budget")
    if isinstance(budget, dict):
        discarded_ids = {str(item.get("id", "")) for item in discarded}
        groups = budget.get("case_groups")
        if isinstance(groups, list):
            next_groups = []
            for item in groups:
                if not isinstance(item, dict):
                    next_groups.append(item)
                elif str(item.get("source_gap", "")) not in discarded_ids:
                    next_groups.append(item)
            next_budget = dict(budget)
            next_budget["case_groups"] = next_groups
            total_cases = 0
            for item in next_groups:
                if isinstance(item, dict):
                    total_cases += max(0, int(item.get("case_count", 0)))
            next_budget["total_cases"] = total_cases
            sanitized["dataset_budget"] = next_budget
    return sanitized


def _behavior_supports_quality_gap(behavior: dict[str, Any]) -> bool:
    """Return whether an accepted behavior explicitly records a real weakness."""
    score = behavior.get("score")
    if not isinstance(score, (int, float)) or float(score) >= 1.0:
        return False
    return any(str(behavior.get(field, "") or "").strip() for field in ("failure_reason", "missing_capability"))


def _normalized_gap_severity(
    gap: dict[str, Any],
    *,
    minimum_behavior_score: float,
) -> str | None:
    """Find a no-more-severe label compatible with accepted behavior scores."""
    severity = str(gap.get("severity", "") or "").strip().lower()
    ordered = ["high", "medium", "low"]
    try:
        start = ordered.index(severity)
    except ValueError:
        start = 1
    candidate_start = start + 1
    for candidate in ordered[candidate_start:]:
        candidate_gap = {**gap, "severity": candidate}
        ceiling = _quality_gap_score_ceiling({"quality_gaps": [candidate_gap]})
        if ceiling is not None and ceiling >= minimum_behavior_score:
            return candidate
    return None


def _read_artifact_runtime_evidence(case_dir: Path | None) -> dict[str, Any]:
    if case_dir is None:
        return {}
    path = case_dir / "judge" / "evidence" / "artifact_runtime_evidence.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _artifact_runtime_score_ceiling(evidence: dict[str, Any]) -> float | None:
    """Apply deterministic ceilings without mistaking a smoke pass for E2E proof."""
    policy = evidence.get("score_policy")
    if not isinstance(policy, dict) or not policy:
        return None
    observations = evidence.get("observations", [])
    if not isinstance(observations, list):
        return None
    statuses = {
        str(item.get("type", "")): str(item.get("status", "")) for item in observations if isinstance(item, dict)
    }
    if statuses.get("browser_execution") == "failed" or statuses.get("runtime_errors") == "failed":
        value = policy.get("failure_ceiling")
        return float(value) if isinstance(value, (int, float)) else None
    if evidence.get("status") == "collected":
        value = policy.get("smoke_only_ceiling")
        return float(value) if isinstance(value, (int, float)) else None
    return None


def _is_core_quality_gap(gap: dict[str, Any]) -> bool:
    """Return true when a gap points at the task's core user value."""
    text_parts = []
    for key in (
        "id",
        "dimension",
        "why_it_matters",
        "missing_capability",
        "data_needed_to_fix",
    ):
        text_parts.append(str(gap.get(key, "") or ""))
    text = " ".join(text_parts).lower()
    core_terms = (
        "core_task_semantics",
        "user_goal_fulfillment",
        "functional_effectiveness",
        "core user goal",
        "core task",
        "main user experience",
    )
    return any(term in text for term in core_terms)


def _prepare_judge_evidence(*, case_dir: Path, judge_dir: Path) -> None:
    """Write ``normalized_trace.json`` into *judge_dir*.

    The judge agent reads artifact files directly from ``case_dir/artifacts``
    via absolute paths supplied in the prompt; no file copying is performed.
    """
    normalized_trace_path = judge_dir / "normalized_trace.json"
    if _has_existing_normalized_trace(normalized_trace_path):
        return
    norm_trace = _build_normalized_trace(case_dir.name, case_dir)
    normalized_trace_path.write_text(
        json.dumps(norm_trace, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _has_existing_normalized_trace(path: Path) -> bool:
    """Return true when CaseRunner already wrote bounded trace evidence."""
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    traces = data.get("traces") if isinstance(data, dict) else None
    return isinstance(traces, list) and bool(traces)


# ---------------------------------------------------------------------------
# Normalized-trace budget constants
# ---------------------------------------------------------------------------

_NT_SYS_BUDGET_CHARS = 1500  # system message kept once, first N chars
_NT_USER_BUDGET_BYTES = 4 * 1024  # user message per turn
_NT_ASST_BUDGET_BYTES = 2 * 1024  # assistant content per turn
_NT_TOOL_IN_BUDGET_BYTES = 1024  # tool call input
_NT_TOOL_OUT_BUDGET_BYTES = 1024  # tool call output (error portion NOT clipped)


# ---------------------------------------------------------------------------
# _extract_tool_error: 4-level normalization — never drops an error
# ---------------------------------------------------------------------------


def _extract_tool_error(step: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any] | None:
    """Return a normalised error dict, or None when no error is detected.

    Priority (first non-empty wins):

    1. ``step["error"]`` — framework-level hard failure (dict or str).
    2. ``detail["call_result"]["error"]`` — tool-returned soft failure dict/str.
    3. ``detail["call_result"]["success"] is False`` — explicit success=false.
    4. ``detail["call_result"]`` is a str that starts with "Error" / "Exception".

    Error content is **never truncated** so diagnosis agents keep full fidelity.
    """
    # Level 1: step-level hard failure
    step_err = step.get("error")
    if step_err:
        if isinstance(step_err, dict) and step_err:
            msg = str(step_err.get("message") or step_err.get("msg") or json.dumps(step_err, ensure_ascii=False))
            typ = str(step_err.get("type") or step_err.get("code") or "")
            return {"message": msg, "type": typ, "source": "step_error"}
        if isinstance(step_err, str) and step_err.strip():
            return {"message": step_err.strip(), "type": "", "source": "step_error"}

    call_result = detail.get("call_result")

    # Level 2: call_result.error dict/str
    if isinstance(call_result, dict):
        cr_err = call_result.get("error")
        if cr_err:
            if isinstance(cr_err, dict) and cr_err:
                msg = str(cr_err.get("message") or cr_err.get("msg") or json.dumps(cr_err, ensure_ascii=False))
                typ = str(cr_err.get("type") or cr_err.get("code") or "")
                return {"message": msg, "type": typ, "source": "call_result"}
            if isinstance(cr_err, str) and cr_err.strip():
                return {"message": cr_err.strip(), "type": "", "source": "call_result"}

        # Level 3: explicit success=false
        if call_result.get("success") is False:
            msg_val = (
                call_result.get("message") or call_result.get("msg") or json.dumps(call_result, ensure_ascii=False)
            )
            return {"message": str(msg_val), "type": "success_false", "source": "call_result"}

    # Level 4: call_result is a string that looks like an error
    if isinstance(call_result, str):
        stripped = call_result.strip()
        lower = stripped.lower()
        if lower.startswith("error") or lower.startswith("exception"):
            return {"message": stripped, "type": "", "source": "call_result_str"}

    return None


# ---------------------------------------------------------------------------
# _parse_trajectory_records: handles both compact (one-per-line) and
# pretty-printed JSON objects in a trajectory JSONL file.
# ---------------------------------------------------------------------------


def _parse_trajectory_records(path: Path) -> list[dict[str, Any]]:
    """Return all Trajectory JSON objects from *path*.

    Handles both compact (one JSON object per line) and pretty-printed
    (multi-line) formats by using JSONDecoder.raw_decode on the full text.
    Silently skips any malformed segments.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    dec = json.JSONDecoder()
    idx = 0
    records: list[dict[str, Any]] = []
    while idx < len(text):
        # Skip whitespace / newlines between records
        while idx < len(text) and text[idx] in " \t\r\n":
            idx += 1
        if idx >= len(text):
            break
        try:
            obj, end = dec.raw_decode(text, idx)
            if isinstance(obj, dict):
                records.append(obj)
            idx = end
        except json.JSONDecodeError:
            # Advance past the current line and try again
            nl = text.find("\n", idx)
            if nl == -1:
                break
            idx = nl + 1
    return records


# ---------------------------------------------------------------------------
# _normalize_one_record: single Trajectory record → {messages, meta}
# ---------------------------------------------------------------------------


def _clip_str(text: str, max_bytes: int) -> str:
    """Bytes-aware string truncation with a marker (reuses _clip logic)."""
    return _clip(text, max_bytes)


def _normalize_one_record(record: dict[str, Any]) -> dict[str, Any]:
    """Convert one Trajectory record (step flow) into a normalised message trace.

    Step flow rules:

    * ``kind="llm"`` — extract the *new* messages from ``detail.messages``
      relative to what we have already accumulated (prefix-diff, skip
      ``role="tool"`` / ``role="function"`` messages).  Then append an
      ``assistant`` turn built from ``detail.response``.
    * ``kind="tool"`` steps immediately following an ``llm`` step are merged
      into that assistant turn's ``tool_calls`` list.

    Budget limits (per turn):

    * ``system``: kept exactly once, first ``_NT_SYS_BUDGET_CHARS`` chars.
    * ``user``: ``_NT_USER_BUDGET_BYTES`` bytes.
    * ``assistant.content``: ``_NT_ASST_BUDGET_BYTES`` bytes.
    * ``tool.input`` / ``tool.output``: ``_NT_TOOL_IN/OUT_BUDGET_BYTES`` bytes.
    * ``tool.error``: **never truncated**.
    """
    meta = record.get("meta") or {}
    member_id: str = str(meta.get("member_id") or "unknown")
    member_role: str = str(meta.get("member_role") or meta.get("role") or "")
    execution_id: str = str(record.get("execution_id") or "")
    case_id_hint: str = str(record.get("case_id") or "")

    # Derive a stable trace_id
    short_exec = execution_id[:8] if execution_id else ""
    trace_id = f"{case_id_hint}__{member_id}__{short_exec}" if case_id_hint else f"{member_id}__{short_exec}"

    steps: list[dict[str, Any]] = record.get("steps") or []
    accumulated: list[dict[str, Any]] = []  # output messages list
    system_added = False
    step_count = len(steps)

    i = 0
    while i < len(steps):
        step = steps[i]
        kind = step.get("kind", "")
        detail: dict[str, Any] = step.get("detail") or {}
        if not isinstance(detail, dict):
            i += 1
            continue

        if kind != "llm":
            i += 1
            continue

        # --- Extract new messages relative to accumulated ---
        incoming: list[dict[str, Any]] = detail.get("messages") or []
        # Visible messages: drop role=tool / role=function (tool results)
        skip_roles = {"tool", "function"}
        incoming_visible = [m for m in incoming if isinstance(m, dict) and m.get("role") not in skip_roles]

        # Find how many leading messages of incoming_visible we already have.
        # Compare by (role, content) on the accumulated side, ignoring our
        # extra fields (message_index, tool_calls, etc.).
        def _msg_role(m: dict[str, Any]) -> str:
            return str(m.get("role") or "")

        def _msg_content(m: dict[str, Any]) -> str:
            c = m.get("content")
            if c is None:
                return ""
            return str(c) if isinstance(c, str) else json.dumps(c, ensure_ascii=False)

        match_up_to = 0
        max_check = min(len(accumulated), len(incoming_visible))
        for k in range(max_check, 0, -1):
            if all(
                _msg_role(accumulated[idx2]) == _msg_role(incoming_visible[idx2])
                and _msg_content(accumulated[idx2])[:120] == _msg_content(incoming_visible[idx2])[:120]
                for idx2 in range(k)
            ):
                match_up_to = k
                break

        # Append genuinely new non-tool messages
        for msg in incoming_visible[match_up_to:]:
            role = _msg_role(msg)
            if role == "system":
                if system_added:
                    continue
                system_added = True
                content = str(msg.get("content") or "")[:_NT_SYS_BUDGET_CHARS]
                accumulated.append({"role": "system", "content": content})
                continue
            if role == "user":
                content = _clip_str(str(msg.get("content") or ""), _NT_USER_BUDGET_BYTES)
                accumulated.append({"role": "user", "content": content})
                continue
            if role == "assistant":
                # Intermediate assistant turns already in history — add compactly
                content = _clip_str(str(msg.get("content") or ""), _NT_ASST_BUDGET_BYTES)
                accumulated.append({"role": "assistant", "content": content, "tool_calls": []})
                continue
            # Skip unknown roles

        # --- Build the new assistant turn from response ---
        response: Any = detail.get("response") or {}
        if not isinstance(response, dict):
            response = {}

        asst_content = _clip_str(str(response.get("content") or ""), _NT_ASST_BUDGET_BYTES)
        asst_turn: dict[str, Any] = {
            "role": "assistant",
            "content": asst_content,
            "message_index": len(accumulated),
            "tool_calls": [],
        }
        accumulated.append(asst_turn)

        # --- Consume following tool steps into asst_turn.tool_calls ---
        j = i + 1
        while j < len(steps) and steps[j].get("kind") == "tool":
            tstep = steps[j]
            td: dict[str, Any] = tstep.get("detail") or {}
            if not isinstance(td, dict):
                j += 1
                continue

            # Parse call_args
            raw_args: Any = td.get("call_args")
            if isinstance(raw_args, str):
                try:
                    raw_args = json.loads(raw_args)
                except json.JSONDecodeError:
                    pass  # keep as string

            tool_in: Any
            if isinstance(raw_args, dict):
                clipped_args = {k: v for k, v in raw_args.items()}
                try:
                    in_text = json.dumps(clipped_args, ensure_ascii=False)
                    if len(in_text.encode()) > _NT_TOOL_IN_BUDGET_BYTES:
                        in_text = (
                            in_text.encode()[:_NT_TOOL_IN_BUDGET_BYTES].decode("utf-8", errors="replace")
                            + "...[truncated]"
                        )
                        tool_in = {"__truncated__": in_text}
                    else:
                        tool_in = clipped_args
                except (TypeError, ValueError):
                    tool_in = str(raw_args)
            else:
                s = str(raw_args or "")
                tool_in = _clip_str(s, _NT_TOOL_IN_BUDGET_BYTES)

            # Tool output (clip but preserve error sub-key)
            call_result: Any = td.get("call_result")
            tool_out: Any
            if isinstance(call_result, dict):
                try:
                    out_text = json.dumps(call_result, ensure_ascii=False)
                    if len(out_text.encode()) > _NT_TOOL_OUT_BUDGET_BYTES:
                        out_text = (
                            out_text.encode()[:_NT_TOOL_OUT_BUDGET_BYTES].decode("utf-8", errors="replace")
                            + "...[truncated]"
                        )
                        tool_out = {"__truncated__": out_text}
                    else:
                        tool_out = call_result
                except (TypeError, ValueError):
                    tool_out = str(call_result)
            else:
                s = str(call_result or "")
                tool_out = _clip_str(s, _NT_TOOL_OUT_BUDGET_BYTES)

            # 4-level error extraction — never truncated
            tool_error = _extract_tool_error(tstep, td)

            tc: dict[str, Any] = {
                "id": str(td.get("tool_call_id") or f"step_{j + 1}"),
                "name": str(td.get("tool_name") or ""),
                "input": tool_in,
                "output": tool_out,
                "error": tool_error,
                "step_pointer": f"step_{j + 1}",
            }
            asst_turn["tool_calls"].append(tc)
            j += 1

        i = j  # advance past consumed tool steps

    return {
        "trace_id": trace_id,
        "member_id": member_id,
        "member_role": member_role,
        "execution_id": execution_id,
        "step_count": step_count,
        "message_count": len(accumulated),
        "messages": accumulated,
    }


# ---------------------------------------------------------------------------
# _build_normalized_trace: traverse trajectories dir → {case_id, traces}
# ---------------------------------------------------------------------------


def _build_normalized_trace(case_id: str, case_dir: Path) -> dict[str, Any]:
    """Build a normalised trace object from all member trajectory files.

    Reads every ``tr/<member>.jsonl`` file under
    *case_dir*, converts each step flow to a message flow, and returns:

    .. code-block:: json

        {
          "case_id": "<case_id>",
          "traces": [
            {
              "trace_id": "...",
              "member_id": "...",
              "member_role": "...",
              "execution_id": "...",
              "step_count": N,
              "message_count": M,
              "messages": [...]
            }
          ]
        }

    Args:
        case_id: Case identifier used in the top-level key.
        case_dir: Resolved case output directory; must contain a ``tr/`` sub-directory.

    Returns:
        Dict ready for ``json.dumps``.
    """
    traj_root = case_dir / ROLE_TRAJECTORY_DIR_NAME
    traces: list[dict[str, Any]] = []

    if traj_root.is_dir():
        for jf in sorted(path for path in traj_root.glob("*.jsonl") if path.name != TRAJECTORY_EVENTS_FILE_NAME):
            records = _parse_trajectory_records(jf)
            for record in records:
                trace = _normalize_one_record(record)
                # Back-fill case_id into trace_id if the record didn't have one
                if trace["trace_id"].startswith("__") or trace["trace_id"].startswith("unknown__"):
                    short_exec = trace["execution_id"][:8]
                    trace["trace_id"] = f"{case_id}__{trace['member_id']}__{short_exec}"
                traces.append(trace)

    result: dict[str, Any] = {"case_id": case_id, "traces": traces}

    return result


def _clip(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="replace") + "\n...[truncated]"


__all__ = [
    "LlmAsJudgeJudger",
]
