# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Facade and strategy implementation for evaluation-result analysis.

Architecture:
  DiagnosisAgentStrategy.analyze(invocation)
      → CaseReader (eval_ref / summary / case_inputs)
      → build_signal_extractor → SignalExtractor.extract
      → read-only DeepAgent per case → deterministic issue compilation
      → EvaluationResultAnalysisArtifact

EvaluationResultAnalyzer (facade):
  mkdir → strategy.analyze → write issues.yaml + analysis_ref.yaml → return path

Prompt constants live here beside their sole consumer (DiagnosisAgentStrategy),
mirroring the llm_as_judge.py convention of co-locating _JUDGE_SYSTEM_PROMPT
and build_judge_prompt with the class that uses them.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from openjiuwen.core.common.logging import logger
from openjiuwen.rsi.harness_rsi.config import (
    EvaluationResultAnalyzerConfig,
)
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.agent_runtime import (
    DiagnosisAgentExecutionError,
    DiagnosisAgentRuntime,
    run_deep_agent_text,
)
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
    CaseAnalysisInput,
    CaseReader,
    DeterministicSignals,
    EvaluationSummaryInput,
    project_execution_history,
)
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.harness_context import prepare_harness_context
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.signal_extractor import (
    build_signal_extractor,
)
from openjiuwen.rsi.harness_rsi.model_call import (
    RetryableModelOutputError,
    is_retryable_model_call_failure,
)
from openjiuwen.rsi.harness_rsi.schema import (
    EvaluationResultAnalysisArtifact,
    EvaluationResultAnalysisInvocation,
    TeamIssue,
)

if TYPE_CHECKING:
    from openjiuwen.core.single_agent.base import BaseAgent


_TEXT_SNIPPET_CHARS = 1200
_METADATA_SNIPPET_CHARS = 2000
_EXPERIENCE_SNIPPET_CHARS = 2000
_CANDIDATE_FEEDBACK_CHARS = 8000
_SIGNAL_SNIPPET_CHARS = 2500
_EVIDENCE_SUMMARY_CHARS = 6000
_AGGREGATION_DIAGNOSIS_CHARS = 1200
_AGGREGATION_SIGNAL_CHARS = 4000
_RAW_OUTPUT_CHARS = 512
_MAX_DIAGNOSES_PER_CASE = 3
_EXECUTION_HISTORY_NOTICE = (
    "Evidence access: evidence_summary.md contains excerpts, not the complete execution history. "
    "execution_history.json preserves all recorded normalized messages and tool results with "
    "their original evidence references, excluding system messages and unrelated metadata. "
    "Use read/search tools to recover earlier decisions and observations before claiming an "
    "action was never performed or selecting the earliest decisive mistake. Missing material "
    "in an excerpt does not prove the evaluated agent never saw it. Source-side truncation "
    "markers remain unknown information, not negative evidence.\n\n"
)


# ---------------------------------------------------------------------------
# Prompt constants (§4 of the design plan)
# ---------------------------------------------------------------------------

DIAGNOSIS_SYSTEM_PROMPT = """\
You diagnose observed failures of an evaluated agent or team. Identify a local
cause supported by the task, execution, and verification evidence. Your output
is an Issue for the existing Improver, not a repaired task or proof that a
future Harness intervention will succeed.

## Evidence Access
The request includes the authoritative task contract, acceptance-test contract
when available, deterministic validation/verifier inventories, and a summary.
The summary is an index, not the complete trace. Read execution_history.json
and the referenced evidence files to recover relevant decisions and results.
Do not infer that an action never happened because a summary omitted it.

current_harness identifies the evaluated roles and provides paths to complete
declared prompts and Skill instructions. Read the relevant instructions before
claiming a capability is absent, wrong, or unused. These are package
declarations, not proof of runtime activation; trace events establish what was
actually called or followed. Runtime defaults and configuration are not
included. When a Harness was not supplied, do not invent its contents.

repository/, when available, is a read-only snapshot of the evaluated
workspace, including the submitted patch. source_patch.diff records that patch.
Use source, task artifacts, trace observations, or a bounded probe to distinguish
causes; a repository is not required when the supplied evidence already does so.
Stay inside the evidence workspace. Never inspect gold/solution patches or
evaluator implementation. Supplied test assertions are acceptance evidence,
not an implementation solution. Agent-authored probes are execution evidence,
not additions to the original task requirements.

## Diagnosis
1. Anchor one diagnosis to one observed failed behavior or related failed-check
   group. Trace backward to the decision or missing operation that produced it.
   Do not claim that one local cause explains unrelated failures.
2. Check the most plausible competing explanation using the smallest relevant
   evidence lookup. If the evidence contradicts your cause, abandon or revise
   that cause. If it establishes the local mistake, stop investigating that
   branch and report it; you do not need to explain every failure in the task.
   Distinguish what was required, what the agent did, and what actually failed.
3. Assign the actual evaluated role from current_harness or the trace, not a
   role invented from the task's subject. Identify the relevant existing
   target_ref surface. Treat that surface and the recommended intervention as
   a testable proposal, not as a demonstrated cause of the model's behavior.
4. In root_cause, critical_mistake, evidence_refs, and failure_cluster, record
   observed facts. In general_mechanism, recommendation, and decision_contract,
   propose one reusable behavior change grounded in those facts: when it
   should activate, what information to use, what decision/action to change,
   and which observable would demonstrate the change. Keep identifiers and
   literal task repairs in evidence, not in the reusable procedure.
5. The Improver chooses and implements a supported local intervention using
   the current Harness. Paired evaluation establishes whether it activates
   and improves the task. Do not return unassigned merely because that
   intervention has not yet been tried or proven to improve the score.
   Do return unassigned when the local failure itself is unsupported, the
   responsible role cannot be determined, or the required repair is outside
   the Harness (such as unavailable task inputs or broken infrastructure).
6. An empty artifact or patch is an outcome, not a cause. Inspect the preceding
   behavior to distinguish a blocked environment, a wrong investigation
   decision, and failure to act on an already-supported conclusion. Attribute
   only the behavior demonstrated by the trace. Do not automatically exclude
   a behavioral failure because it occurred after investigation; record its
   true activation_phase so the Improver can choose a supported intervention.
7. Return up to three independent local diagnoses, preferably fewer. Evidence
   that refutes one cause does not invalidate a separately supported cause.
   If the remaining evidence cannot resolve another failure, leave that
   cluster unassigned rather than withholding the supported diagnosis.

## Factual Boundaries
- deterministic_validation_inventory and deterministic_verifier_inventory
  are code-derived observations. If a project test suite passed, do not say it
  was skipped. If patch_successfully_applied=true, do not blame patch
  application. Explain a local-pass/verifier-fail contradiction using the
  different behavior exercised; do not fabricate hidden test semantics.
- Use verifier_failure_output_excerpt and supplied acceptance assertions for
  the actual failed observable. A test name alone does not establish its
  semantics. A passing self-authored probe proves only the behavior it tested.
- prior_candidate_feedback compares the same case's Source and Candidate.
  candidate_behavior identifies the actual intervention and runtime access:
  Prompt context is not a callable Skill; Skill access does not prove its
  procedure was followed. Check the answer/tool results for execution evidence.
  Preserve newly passing checks, inspect regressions and still-failing checks,
  and revise a mechanism contradicted by this experiment. Do not re-diagnose
  checks that remain passing in the current evaluation or claim a zero task
  score means that no individual behavior improved. Historical success does
  not establish success in a later execution; diagnose current failures even
  when an earlier candidate passed the same check.
- Judge quality gaps are failure leads, not automatically proven causal facts.
  verification_gap describes missing evaluator confidence; it is not by itself
  a member defect. Missing or unreadable trace/artifact evidence is an analysis
  or infrastructure failure, not a request to change the agent's instructions.
- Retrieved experience supplies hypotheses, not authority. Current evidence
  must independently support the role, mechanism, and recommendation.
- Confidence describes support for the observed local cause. Never strengthen
  a tentative observation into a fact, infer other defective locations without
  evidence, or claim future score improvement as an already-verified result.

## Target Reference Semantics

Valid target_ref formats:
- member_harness.<role>.<variable>
- team_skill.<role>.<variable>
- unassigned

Decision order:
1. Identify the earliest decisive mistake from evidence_summary / normalized
   trace evidence.
2. Choose scope: member_harness, team_skill, or unassigned.
3. Identify the concrete role involved in that mistake.
4. Choose the most specific variable inside that scope.

### Scope: member_harness
Choose when the earliest decisive mistake is inside one role's own behavior:
the role would still fail even if all other roles behaved correctly, and the
fix belongs to that role's local ExpertHarness.

Variables (member_harness.<role>.<variable>):
- prompt: role identity, domain framing, behavioral style, or task interpretation is wrong.
- skill: reusable multi-step capability is not triggered, missing, misused, or procedurally flawed.
- tool: local atomic tool choice, args, schema, call format, implementation, or result handling is wrong.
- config: runtime/model/harness configuration for this role is wrong.

### Scope: team_skill
Choose when the earliest decisive mistake is in team-level coordination,
constraint handling, team workflow, or repeated capability allocation:
multiple roles interact incorrectly, or a role boundary/protocol fails, and
the fix belongs to Team Skill policy rather than one role's local harness.
<role> is the affected role: the role whose coordination, constraint,
workflow, or capability is the root cause.

Variables (team_skill.<role>.<variable>):
- role_coordination: collaboration breaks between roles, data is not passed, wrong role receives task, handoff fails, or roles disagree about shared state/context.
- constraint_violation: timeout, output format non-compliance, final output not checked against requirements, or explicit constraints are ignored.
- workflow_inefficiency: redundant calls, unnecessary steps, team stops too early, or team continues after completion.
- capability_gap: a role repeatedly fails the same sub-task or produces output below the quality bar despite correct coordination.

### unassigned
Choose when evidence is too thin to identify scope or role, when both scopes
are plausible but neither is clearly primary, or when the trace only shows the
final failed outcome instead of the causal mistake.

Scope rules:
- Do not choose team_skill just because one role produced poor output.
- Do not choose member_harness for cross-role routing, handoff, or shared-context protocol failures.
- Do choose member_harness when a judge quality gap names a concrete role,
  local missing capability, and likely surface such as prompt, skill, tool, or config,
  and the failure would remain even if the team workflow were correct.
- Do not choose member_harness for coordinator targets named team, leader,
  team_leader, or coordinator. Task-board dispatch, claim_task completion,
  final-deliverable gating, and leader/member protocol failures are Team Skill
  workflow/constraint problems.
- Never output role-less target_ref values such as member_harness.prompt,
  member_harness.skill, team_skill.role_coordination, or team_skill.handoff_protocol.

## Output (single valid JSON object, nothing outside it)
Return the JSON object immediately. Keep every string field concise
(normally <= 240 characters). Do not include markdown, prose, analysis notes,
or step-by-step reasoning outside the JSON object.

Per-case schema (one wrapper containing 1-3 diagnoses):
{
  "diagnoses": [
    {
      "issue_category": "member_harness | team_skill | unassigned",
      "severity": "high | medium | low",
      "summary": "<one sentence: the concrete root cause>",
      "failure_mode": "<short structural failure label>",
      "failure_cluster": {
        "failed_checks": ["<failed verifier/check id>"],
        "observable_behavior": "<specific runtime-visible failure>"
      },
      "root_cause": "<fundamental reason; cite trace_id+role+#index>",
      "critical_mistake": "<earliest decisive wrong turn; cite the evidence ptr>",
      "general_mechanism": "<structural (NOT task-specific) fix for this class>",
      "target_ref": "<member_harness.<role>.<variable> | team_skill.<role>.<variable> | unassigned>",
      "evidence_refs": [
        {
          "trace_id": "<id>",
          "role": "<member_role>",
          "message_index": 0,
          "step_pointer": "<step_N or empty>"
        }
      ],
      "affected_components": ["<member_role>"],
      "recommendation": "<concrete change to the target_ref variable>",
      "decision_contract": {
        "wrong_decision": "<structural decision error; keep this task's literal code in critical_mistake>",
        "causal_distinction": "<relationship that distinguishes the wrong decision from the supported one>",
        "required_action": "<reusable procedure over the NEXT task's inputs; not this task's patch or assertion>",
        "acceptance_observable": "<how the next task's own requirements will demonstrate the changed behavior>",
        "scope_boundary": ["<nearby behavior that is not an equivalent substitute>"],
        "activation_phase": "<task_start | during_investigation | post_diagnosis | pre_submission>"
      },
      "validation_observations": {
        "project_test_suite_attempted": false,
        "project_test_suite_result": "not_observed | passed | failed",
        "authoritative_verifier_result": "passed | failed | unknown",
        "contradiction_explanation": "<why local project tests and verifier differ, or empty>"
      },
      "verifier_observations": {
        "patch_successfully_applied": null,
        "failed_fail_to_pass_tests": ["<authoritative failed test id>"],
        "failed_pass_to_pass_tests": ["<authoritative regression test id>"]
      },
      "confidence": "high | medium | low"
    }
  ]
}

## Anti-vagueness rules (hard)
- issue_category MUST equal the first segment of target_ref
  (member_harness or team_skill). If target_ref is "unassigned",
  issue_category MUST be "unassigned".
- root_cause / critical_mistake MUST be supported by >=1 evidence_refs entry;
  no evidence_refs => confidence cannot exceed low.
- general_mechanism MUST be task-agnostic (a reusable rule).
- decision_contract MUST preserve one directional decision change. Its
  required_action cannot be made optional by a later alternative under the same
  trigger. It proposes a behavior change; effectiveness is tested downstream.
- activation_phase MUST name the earliest runtime phase where the evidence
  needed for required_action exists; post-diagnosis actions are not optional
  task-start methods.
- Return no more than 3 diagnoses. Every pair must have
  either different failed_checks or a materially different observable_behavior;
  paraphrases, downstream symptoms, and repeated recommendations are one
  diagnosis. Prefer fewer well-supported diagnoses over filling the limit.
- recommendation MUST name the target_ref variable and what to change.
- Prefer "unassigned" over guessing.
"""

PER_CASE_DIAGNOSIS_TEMPLATE = """\
## Per-Case Diagnosis Request

### Stage Objective
{stage_instruction}

### Evidence Instruction
{evidence_instruction}

### Inline Diagnosis Input JSON
{diagnosis_input}

Diagnose the independent root causes and return the wrapped JSON object from
the system prompt. Return at most {max_diagnoses_per_case} diagnoses.
"""

AGGREGATION_SYSTEM_PROMPT = """\
You are a root-cause issue aggregator for multi-agent AI team evaluations.
All data is provided inline in the user message — do NOT call read_file or
list_files. Your only task is to group and synthesize per-case diagnoses.

## Aggregation rules
1. Merge cases with the same structural root cause into one TeamIssue.
2. Limit evidence entries to the specified evidence_limit_per_issue.
3. Merge evidence_refs from same-root-cause cases (respect the limit).
4. Pick target_ref and confidence from the highest-confidence case in each group.
5. Fill metadata.attribution for each issue from the strongest-evidence case.
6. Current evidence remains authoritative. Retrieved experience may explain
   reusable patterns and anti-patterns, but must not introduce issues that are
   absent from per-case diagnoses.
7. Do not turn evaluator/analyzer evidence-pipeline failures into optimizer
   issues. Missing/failed-to-read `trajectory_events.jsonl`,
   `normalized_trace.json`, or `evidence_summary.md` must remain
   target_ref="unassigned" with low confidence.

## Target Reference Semantics

Preserve the per-case diagnosis target_ref format:
- member_harness.<role>.<variable>
- team_skill.<role>.<variable>
- unassigned

Valid member_harness variables: prompt, skill, tool, config.
Valid team_skill variables: role_coordination, constraint_violation,
workflow_inefficiency, capability_gap.
Never output role-less target_ref values such as member_harness.prompt,
member_harness.skill, team_skill.role_coordination, or
team_skill.handoff_protocol.
Never output coordinator-as-member target_ref values such as
member_harness.team.prompt or member_harness.team_leader.prompt. Coordinator,
leader, and task-board completion failures belong to team_skill.<role>.<variable>.

## Output (single valid JSON object, nothing outside it)
{
  "issues": [
    {
      "issue_id": "<unique_id>",
      "category": "member_harness | team_coordination",
      "severity": "high | medium | low",
      "summary": "<concise description>",
      "affected_cases": ["<case_id>"],
      "affected_components": ["<member_role>"],
      "evidence": [
        {
          "case_id": "<id>",
          "failure_mode": "<label>",
          "affected_component": "<member_role or empty>"
        }
      ],
      "suspected_team_scope": "member | team_skill | both",
      "recommendation": "<actionable suggestion>",
      "confidence": "high | medium | low",
      "metadata": {
        "attribution": {
          "root_cause": "<fundamental reason>",
          "critical_mistake": "<earliest decisive wrong turn>",
          "general_mechanism": "<task-agnostic reusable fix mechanism>",
          "decision_contract": {
            "wrong_decision": "<the decision that produced the failure>",
            "causal_distinction": "<the distinction supported by current evidence>",
            "required_action": "<the selected action>",
            "acceptance_observable": "<the observable that proves completion>",
            "scope_boundary": ["<invalid substitute or excluded neighboring case>"],
            "activation_phase": "<task_start | during_investigation | post_diagnosis | pre_submission>"
          },
          "target_ref": "<member_harness.<role>.<variable> | team_skill.<role>.<variable> | unassigned>",
          "evidence_refs": [
            {
              "trace_id": "<id>",
              "role": "<member_role>",
              "message_index": 0,
              "step_pointer": "<step_N or empty>"
            }
          ],
          "confidence": "high | medium | low"
        }
      }
    }
  ]
}
"""

AGGREGATION_TEMPLATE = """\
## Aggregation Request

### Evaluation Summary
- total_cases: {total_cases}
- passed_count: {passed_count}
- failed_count: {failed_count}
- average_score: {average_score}
- evaluation_method: {evaluation_method}

### Per-Case Diagnoses
{per_case_diagnoses}

### Retrieved Experience
{retrieved_experience}

### Anchor Signals
- exec_failures: {exec_failures}
- judge_failures: {judge_failures}
- error_clusters: {error_clusters}
- method_specific: {method_specific}

### Constraints
- max_issues: {max_issues}
- evidence_limit_per_issue: {evidence_limit_per_issue}

### Stage Objective
{stage_instruction}

Group the per-case diagnoses into at most {max_issues} TeamIssue objects.
Merge cases with the same root cause.  Limit evidence entries to {evidence_limit_per_issue}
per issue.  Merge evidence_refs from same-root-cause cases (respect evidence_limit_per_issue).
Return the JSON object matching the output schema in the system prompt.
"""


def _build_diagnosis_prompt(
    *,
    case: CaseAnalysisInput,
    signals: DeterministicSignals,
    retrieved_experience: dict[str, Any] | None,
    evidence_summary_available: bool,
    source_stage: str = "",
    prior_candidate_feedback: dict[str, Any] | None = None,
    harness_context: dict[str, Any] | None = None,
) -> str:
    """Build the per-case diagnosis prompt for the DeepAgent.

    The prompt carries deterministic inputs inline.  The runtime may also hold
    an isolated ``repository/`` snapshot so the diagnosis agent can falsify
    semantic hypotheses against the evaluated code without touching it.
    """
    if evidence_summary_available:
        evidence_instruction = (
            "> Use primary_evidence.evidence_summary_text from the inline JSON.\n"
            "> It contains verifier outcome, judge quality gaps, decisive failed steps, "
            "and bounded key events.\n"
            "> If repository/ exists, inspect its relevant source and public tests and run "
            "a bounded read-only discriminator before selecting a semantic mechanism.\n"
            "> Do not read trace.json, result.json, hidden tests, gold patches, or paths "
            "outside this runtime workspace."
        )
    else:
        evidence_instruction = (
            "> No evidence_summary.md is available in this runtime workspace.\n"
            "> Start from the inline JSON. If repository/ exists, use it only to test "
            "competing hypotheses; otherwise return unassigned when semantics remain ambiguous."
        )

    prompt = PER_CASE_DIAGNOSIS_TEMPLATE.format(
        stage_instruction=_stage_instruction(source_stage),
        evidence_instruction=evidence_instruction,
        max_diagnoses_per_case=_MAX_DIAGNOSES_PER_CASE,
        diagnosis_input=_build_diagnosis_input_json(
            case=case,
            signals=signals,
            retrieved_experience=retrieved_experience,
            evidence_summary_available=evidence_summary_available,
            source_stage=source_stage,
            prior_candidate_feedback=prior_candidate_feedback,
            harness_context=harness_context,
        ),
    )
    return (_EXECUTION_HISTORY_NOTICE + prompt) if evidence_summary_available else prompt


def _build_diagnosis_input_json(
    *,
    case: CaseAnalysisInput,
    signals: DeterministicSignals,
    retrieved_experience: dict[str, Any] | None,
    evidence_summary_available: bool,
    source_stage: str = "",
    prior_candidate_feedback: dict[str, Any] | None = None,
    harness_context: dict[str, Any] | None = None,
) -> str:
    """Build bounded inline JSON for a per-case diagnosis prompt."""
    judge_breakdown = _summarize_evaluation_metadata(case.evaluation_metadata)
    evidence_summary_text = (
        _truncate_text(_build_evidence_summary(case), _EVIDENCE_SUMMARY_CHARS) if evidence_summary_available else ""
    )
    validation_inventory = _build_validation_inventory(case)
    verifier_inventory = _build_verifier_inventory(case)
    payload: dict[str, Any] = {
        "current_harness": harness_context or {"status": "not_provided", "roles": []},
        "authoritative_task_contract": {
            "provenance": "case.input",
            "input_excerpt": case.input,
            "policy": (
                "Only this excerpt establishes what the original task or reproduction "
                "contains. Agent-authored commands and probes are execution evidence, "
                "not task-contract facts."
            ),
        },
        "authoritative_benchmark_test_contract": case.benchmark_test_contract,
        "primary_evidence": {
            "evidence_summary_available": evidence_summary_available,
            "evidence_summary_path": "evidence_summary.md" if evidence_summary_available else "",
            "evidence_summary_text": evidence_summary_text,
        },
        "deterministic_validation_inventory": validation_inventory,
        "deterministic_verifier_inventory": verifier_inventory,
        "prior_candidate_feedback": _bounded_structured_value(
            prior_candidate_feedback or {},
            _CANDIDATE_FEEDBACK_CHARS,
        ),
        "prior_candidate_feedback_policy": (
            "Treat paired official test deltas as authoritative experiment "
            "evidence. Preserve newly passing operations. Diagnose newly regressed "
            "checks first, then remaining failures. A historical candidate win "
            "does not override failure or missing output in the current evaluation. Candidate "
            "diagnoses are hypotheses unless the verifier delta independently "
            "supports them."
        ),
        "analysis_stage": source_stage or "unknown",
        "anchor_signals": {
            "method": signals.method,
            "exec_failures": _case_scoped_list(signals.exec_failures, case.case_id),
            "judge_failures": _case_scoped_list(signals.judge_failures, case.case_id),
            "error_clusters": _bounded_structured_value(
                _case_scoped_error_clusters(signals.error_clusters, case.case_id),
                _SIGNAL_SNIPPET_CHARS,
            ),
            "method_specific": _bounded_structured_value(
                _case_scoped_method_specific(signals.method_specific, case.case_id),
                _SIGNAL_SNIPPET_CHARS,
            ),
        },
        "case_facts": {
            "case_id": case.case_id,
            "status": case.status,
            "score": case.score,
            "evaluation_passed": case.evaluation_passed,
            "evaluation_reason": _truncate_text(case.evaluation_reason, _TEXT_SNIPPET_CHARS),
            "error": _truncate_text(case.error, _TEXT_SNIPPET_CHARS),
            "judge_breakdown": judge_breakdown,
            "training_signal": _bounded_structured_value(
                case.training_signal,
                _METADATA_SNIPPET_CHARS,
            ),
        },
        "fallback_excerpts": {
            "input_excerpt": case.input,
            "response_excerpt": _truncate_text(case.response, _TEXT_SNIPPET_CHARS),
        },
        "retrieved_experience": _bounded_structured_value(
            _compact_retrieved_experience(retrieved_experience),
            _EXPERIENCE_SNIPPET_CHARS,
        ),
        "experience_usage_policy": _experience_usage_policy(),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _build_aggregation_prompt(
    *,
    summary: EvaluationSummaryInput,
    per_case_diagnoses: list[dict[str, Any]],
    signals: DeterministicSignals,
    retrieved_experience: dict[str, Any] | None,
    max_issues: int,
    evidence_limit_per_issue: int,
    source_stage: str = "",
) -> str:
    """Build the aggregation prompt for the DeepAgent."""
    return AGGREGATION_TEMPLATE.format(
        total_cases=summary.total_cases,
        passed_count=summary.passed_count,
        failed_count=summary.failed_count,
        average_score=summary.average_score,
        evaluation_method=summary.evaluation_method,
        per_case_diagnoses=_bounded_json(
            _compact_per_case_diagnoses(per_case_diagnoses),
            _AGGREGATION_DIAGNOSIS_CHARS * max(1, len(per_case_diagnoses)),
        ),
        retrieved_experience=_bounded_json(
            {
                "policy": _experience_usage_policy(),
                "retrieved_experience": _compact_retrieved_experience(retrieved_experience),
            },
            _EXPERIENCE_SNIPPET_CHARS,
        ),
        exec_failures=_bounded_json(signals.exec_failures, _AGGREGATION_SIGNAL_CHARS),
        judge_failures=_bounded_json(signals.judge_failures, _AGGREGATION_SIGNAL_CHARS),
        error_clusters=_bounded_json(signals.error_clusters, _AGGREGATION_SIGNAL_CHARS),
        method_specific=_bounded_json(signals.method_specific, _AGGREGATION_SIGNAL_CHARS),
        max_issues=max_issues,
        evidence_limit_per_issue=evidence_limit_per_issue,
        stage_instruction=_stage_instruction(source_stage),
    )


def _stage_instruction(source_stage: str) -> str:
    """Return the stage-specific attribution objective used by analyzer prompts."""
    if source_stage == "single_harness_candidate_failure":
        return (
            "Analyze why the evaluated candidate did not finish the target case. "
            "Compare it with prior_candidate_feedback: preserve official operations "
            "that moved from failure to success, identify the remaining failed "
            "operation, and falsify the previous mechanism against the candidate "
            "patch. Do not re-diagnose the original Source in isolation. This is a "
            "standalone single-Harness run with no Team Skill optimization surface. "
            "Attribute reusable defects only to member_harness.<role>.<variable>; use "
            "unassigned only when the evidence does not support a Harness change. "
            "Treat candidate_behavior.gate_reason and failure_class as machine-observed "
            "facts. If a delivered Skill or Tool was not invoked, diagnose activation "
            "control rather than proposing another equivalent static capability."
        )
    if source_stage == "single_harness_residual_repair":
        return (
            "Re-analyze the still-failing standalone Harness using the prior candidate "
            "feedback as a falsification result. Preserve behavior that improved an "
            "official check. If a capability was delivered but not invoked in time, "
            "attribute the remaining defect to a runtime control surface rather than "
            "requesting another equivalent static instruction. Valid target_ref values "
            'are member_harness.<role>.<variable> or "unassigned".'
        )
    if source_stage.startswith("single_harness_"):
        return (
            "Analyze the concrete reusable defect in this standalone Harness. There "
            "is no Team Skill optimization surface in this run. Valid target_ref "
            'values are member_harness.<role>.<variable> or "unassigned". Do not emit '
            "team_skill targets."
        )
    if source_stage == "member_stage":
        return (
            "Analyze concrete member harness capability under the current Team Skill. "
            "Attribute to a specific role's prompt, skill, or tool. Valid target_ref "
            'values are member_harness.<role>.<variable> or "unassigned".'
        )
    if source_stage == "team_skill_stage":
        return (
            "Analyze team organization, role boundaries, collaboration flow, and "
            "deliverable contract. Attribute Team Skill issues to team_skill.<role>.<variable>; "
            "use member_harness.<role>.<variable> only for local member capability gaps."
        )
    return (
        "Analyze the concrete optimizable variable supported by current evidence. "
        'Use member_harness.<role>.<variable>, team_skill.<role>.<variable>, or "unassigned".'
    )


def _truncate_text(value: Any, limit: int) -> str:
    """Return a bounded text representation with a truncation marker."""
    text = str(value or "")
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n...[truncated {omitted} chars]"


def _bounded_json(value: Any, limit: int = _SIGNAL_SNIPPET_CHARS) -> str:
    """Serialize a value to JSON and bound the serialized length."""
    return _truncate_text(json.dumps(value, ensure_ascii=False), limit)


def _bounded_structured_value(value: Any, limit: int) -> Any:
    """Keep a structured value intact unless its JSON form exceeds the limit."""
    text = json.dumps(value, ensure_ascii=False)
    if len(text) <= limit:
        return value
    return {"truncated_json": _truncate_text(text, limit)}


def _case_scoped_list(items: list[str], case_id: str) -> list[str]:
    """Keep only the current case marker from a case-id list."""
    return [case_id] if case_id in items else []


def _case_scoped_error_clusters(clusters: list[dict[str, Any]], case_id: str) -> list[dict[str, Any]]:
    """Filter error clusters to the current case."""
    scoped: list[dict[str, Any]] = []
    for cluster in clusters:
        cases = cluster.get("cases", [])
        if isinstance(cases, list) and case_id in cases:
            scoped.append(
                {
                    "fingerprint": cluster.get("fingerprint", ""),
                    "cases": [case_id],
                }
            )
    return scoped


def _case_scoped_method_specific(metadata: dict[str, Any], case_id: str) -> dict[str, Any]:
    """Filter method-specific signal maps to the current case where possible."""
    scoped: dict[str, Any] = {}
    for key, value in metadata.items():
        scoped[key] = _case_scoped_value(value, case_id)
    return scoped


def _case_scoped_value(value: Any, case_id: str) -> Any:
    if isinstance(value, dict):
        if case_id in value:
            return {case_id: value[case_id]}
        return {
            key: _case_scoped_value(item, case_id) for key, item in value.items() if _value_mentions_case(item, case_id)
        }
    if isinstance(value, list):
        if case_id in value:
            return [case_id]
        return [item for item in value if isinstance(item, dict) and _value_mentions_case(item, case_id)]
    return value


def _value_mentions_case(value: Any, case_id: str) -> bool:
    if isinstance(value, dict):
        return any(_value_mentions_case(item, case_id) for item in value.values())
    if isinstance(value, list):
        return any(_value_mentions_case(item, case_id) for item in value)
    return value == case_id


def _summarize_evaluation_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Extract judge breakdown from evaluation metadata.

    Returns an empty dict when no recognized LLM-as-judge fields are present.
    Never writes ``raw_output`` or full evaluation metadata into the result.
    Each behavior reason is bounded by ``_TEXT_SNIPPET_CHARS``.
    """
    parsed = metadata.get("parsed", {})
    if not isinstance(parsed, dict):
        return {}

    behaviors_raw = parsed.get("behaviors", [])
    behaviors: list[dict[str, Any]] = []
    if isinstance(behaviors_raw, list):
        for entry in behaviors_raw:
            if not isinstance(entry, dict):
                continue
            behaviors.append(
                {
                    "id": entry.get("id", ""),
                    "score": entry.get("score"),
                    "description": entry.get("description", ""),
                    "weight": entry.get("weight"),
                    "reason": _truncate_text(entry.get("reason", ""), _TEXT_SNIPPET_CHARS),
                    "failure_reason": _truncate_text(entry.get("failure_reason", ""), _TEXT_SNIPPET_CHARS),
                    "missing_capability": _truncate_text(entry.get("missing_capability", ""), _TEXT_SNIPPET_CHARS),
                    "suggested_surface_hint": _truncate_text(entry.get("suggested_surface_hint", ""), 80),
                    "evidence": entry.get("evidence", ""),
                }
            )

    overall_reason = parsed.get("overall_reason", "")
    forbidden_hits = parsed.get("forbidden_hits", [])
    quality_gaps = _compact_quality_gaps(parsed.get("quality_gaps", []))
    dataset_budget = _compact_dataset_budget(parsed.get("dataset_budget", {}))
    dimensions = _compact_judge_dimensions(parsed.get("dimensions", {}))
    criteria = _compact_judge_criteria(metadata)

    if not any((behaviors, overall_reason, forbidden_hits, quality_gaps, criteria)):
        return {}

    result: dict[str, Any] = {
        "behaviors": behaviors,
        "overall_reason": _truncate_text(overall_reason, _TEXT_SNIPPET_CHARS),
        "forbidden_hits": forbidden_hits if isinstance(forbidden_hits, list) else [],
    }
    if quality_gaps:
        result["quality_gaps"] = quality_gaps
    if dataset_budget:
        result["dataset_budget"] = dataset_budget
    if dimensions:
        result["dimensions"] = dimensions
    if criteria:
        result["criteria"] = criteria
    if "quality_gap_score_ceiling" in parsed:
        result["quality_gap_score_ceiling"] = parsed.get("quality_gap_score_ceiling")
    if "overall_score" in parsed:
        result["overall_score"] = parsed.get("overall_score")
    for key in ("base_score", "penalty_mode", "total_deduction"):
        if key in parsed:
            result[key] = parsed[key]
    return result


def _compact_judge_criteria(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = metadata.get("judge_evidence")
    raw_criteria = normalized.get("criteria") if isinstance(normalized, dict) else None
    if not isinstance(raw_criteria, list):
        raw_detail = metadata.get("judge_detail")
        raw_criteria = raw_detail.get("criteria") if isinstance(raw_detail, dict) else None
    if not isinstance(raw_criteria, list):
        return []

    criteria: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_criteria, start=1):
        if not isinstance(raw, dict):
            continue
        criterion_id = raw.get("criterion_id") or raw.get("verifier_id") or f"criterion_{index}"
        criteria.append(
            {
                "criterion_id": str(criterion_id),
                "verifier_id": str(raw.get("verifier_id") or ""),
                "score": raw.get("score"),
                "passed": raw.get("passed") if isinstance(raw.get("passed"), bool) else None,
                "status": str(raw.get("status") or ""),
                "rationale": _truncate_text(raw.get("rationale", ""), _TEXT_SNIPPET_CHARS),
            }
        )
    return criteria


def _compact_quality_gaps(value: Any) -> list[dict[str, Any]]:
    """Keep artifact defects as diagnosis anchors, not verifier limitations.

    ``verification_gap`` is retained in the judge result for observability, but
    the member/team optimizer cannot repair evaluator evidence coverage. Passing
    it to diagnosis caused adapter-owned tools such as ``interaction_smoke`` to
    be misattributed as member harness tools and produced impossible tool actions.
    """
    if not isinstance(value, list):
        return []
    gaps: list[dict[str, Any]] = []
    for entry in value[:8]:
        if not isinstance(entry, dict):
            continue
        gap_type = str(entry.get("gap_type", "") or "").strip().lower()
        if gap_type == "verification_gap":
            continue
        gaps.append(
            {
                "id": entry.get("id", ""),
                "gap_type": entry.get("gap_type", ""),
                "dimension": entry.get("dimension", ""),
                "severity": entry.get("severity", ""),
                "affected_roles": _string_list(entry.get("affected_roles"), 8),
                "likely_surfaces": _string_list(entry.get("likely_surfaces"), 8),
                "evidence": _truncate_text(entry.get("evidence", ""), _TEXT_SNIPPET_CHARS),
                "missing_capability": _truncate_text(entry.get("missing_capability", ""), _TEXT_SNIPPET_CHARS),
                "why_it_matters": _truncate_text(entry.get("why_it_matters", ""), _TEXT_SNIPPET_CHARS),
                "data_needed_to_fix": _truncate_text(entry.get("data_needed_to_fix", ""), _TEXT_SNIPPET_CHARS),
                "training_signal_priority": entry.get("training_signal_priority", ""),
            }
        )
    return gaps


def _compact_dataset_budget(value: Any) -> dict[str, Any]:
    """Keep dataset-budget routing hints bounded."""
    if not isinstance(value, dict):
        return {}
    groups = value.get("case_groups", [])
    compact_groups: list[dict[str, Any]] = []
    if isinstance(groups, list):
        for group in groups[:8]:
            if not isinstance(group, dict):
                continue
            compact_groups.append(
                {
                    "source_gap": group.get("source_gap", ""),
                    "case_count": group.get("case_count"),
                    "target_roles": _string_list(group.get("target_roles"), 8),
                    "target_surfaces": _string_list(group.get("target_surfaces"), 8),
                }
            )
    result: dict[str, Any] = {}
    if "total_cases" in value:
        result["total_cases"] = value.get("total_cases")
    if compact_groups:
        result["case_groups"] = compact_groups
    return result


def _compact_judge_dimensions(value: Any) -> dict[str, Any]:
    """Keep score diagnostics that help diagnosis without copying raw output."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key in ("low_score_behaviors", "avg_behavior_score", "behavior_count", "pass_count", "fail_count"):
        if key in value:
            result[key] = value.get(key)
    if "triggered_forbidden_behaviors" in value:
        result["triggered_forbidden_behaviors"] = value["triggered_forbidden_behaviors"]
    per_behavior_scores = value.get("per_behavior_scores")
    if isinstance(per_behavior_scores, dict):
        result["per_behavior_scores"] = {str(key): score for key, score in list(per_behavior_scores.items())[:12]}
    return result


def _string_list(value: Any, limit: int) -> list[str]:
    """Return a bounded list of strings."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value[:limit]]


def _compact_retrieved_experience(retrieved_experience: dict[str, Any] | None) -> dict[str, Any]:
    """Keep compact experience hints in structured form."""
    if not retrieved_experience:
        return {}
    matches = retrieved_experience.get("matches", [])
    compact_matches = []
    if isinstance(matches, list):
        for item in matches[:3]:
            if isinstance(item, dict):
                compact_matches.append(
                    {
                        "experience_id": item.get("experience_id", item.get("id", "")),
                        "component_layer": item.get("component_layer", ""),
                        "failure_signature": item.get("failure_signature", ""),
                        "mechanism_type": item.get("mechanism_type", ""),
                        "learning_status": item.get("learning_status", ""),
                        "summary": _truncate_text(item.get("summary", item.get("content", "")), 500),
                        "experience": _bounded_structured_value(
                            item.get("experience", {}),
                            800,
                        ),
                        "metadata": item.get("metadata", {}),
                    }
                )
    return {
        "stage": retrieved_experience.get("stage", ""),
        "matches": compact_matches,
        "metadata": retrieved_experience.get("metadata", {}),
    }


def _experience_usage_policy() -> dict[str, Any]:
    """Structured rules for how analyzer agents may use retrieved experience."""
    return {
        "must_use_current_evidence_first": True,
        "principle": "Current evidence remains authoritative; experience is only a bounded hint.",
        "rules": [
            (
                "Do not copy a historical target_ref, component_layer, or "
                "recommendation unless current trace/verifier evidence supports it."
            ),
            "Use retrieved general_principles only as reusable hypotheses to check.",
            "Use retrieved anti_patterns as repairs to avoid, not as new facts.",
            "If experience conflicts with current evidence, ignore the experience.",
            "Experience cannot raise confidence without current evidence_refs.",
        ],
    }


def _normalize_case_diagnoses(
    parsed: dict[str, Any],
    *,
    prior_candidate_feedback: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Expand one model response into bounded, independent diagnoses.

    New responses use ``{"diagnoses": [...]}``; a legacy single diagnosis object
    remains valid. Historical feedback prioritizes regressions and residual
    failures within the per-case limit; it cannot veto a current diagnosis.
    """
    raw_diagnoses = parsed.get("diagnoses")
    if raw_diagnoses is None:
        candidates = [parsed]
    elif isinstance(raw_diagnoses, list):
        candidates = [item for item in raw_diagnoses if isinstance(item, dict)]
    else:
        candidates = []

    feedback_sets = _candidate_feedback_check_sets(prior_candidate_feedback)
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    require_cluster = isinstance(raw_diagnoses, list) and len(candidates) > 1
    for index, raw in enumerate(candidates):
        diagnosis = dict(raw)
        failure_cluster = _diagnosis_failure_cluster(diagnosis)
        if require_cluster and not failure_cluster:
            continue
        if failure_cluster:
            diagnosis["failure_cluster"] = failure_cluster
        priority = _diagnosis_feedback_priority(
            diagnosis,
            feedback_sets=feedback_sets,
        )
        ranked.append((priority, index, diagnosis))

    normalized: list[dict[str, Any]] = []
    for _, _, diagnosis in sorted(ranked, key=lambda item: (item[0], item[1])):
        if any(_diagnoses_are_duplicate(diagnosis, existing) for existing in normalized):
            continue
        normalized.append(diagnosis)
        if len(normalized) >= _MAX_DIAGNOSES_PER_CASE:
            break
    return normalized


def _candidate_feedback_check_sets(
    feedback: dict[str, Any] | None,
) -> dict[str, set[str]]:
    """Return check sets from the most recent paired candidate experiment."""
    if not isinstance(feedback, dict):
        return {"regressed": set(), "remaining": set(), "fixed": set()}
    experiments = feedback.get("experiments", [])
    if not isinstance(experiments, list):
        return {"regressed": set(), "remaining": set(), "fixed": set()}
    for experiment in reversed(experiments):
        if not isinstance(experiment, dict):
            continue
        delta = experiment.get("verifier_delta", {})
        if not isinstance(delta, dict):
            continue
        regressed = _normalized_check_set(
            delta,
            "regressed_fail_to_pass",
            "regressed_pass_to_pass",
            "regressed_atomic_checks",
        )
        remaining = _normalized_check_set(
            delta,
            "remaining_failed_fail_to_pass",
            "remaining_failed_atomic_checks",
        )
        fixed = _normalized_check_set(
            delta,
            "newly_passed_fail_to_pass",
            "newly_passed_atomic_checks",
        )
        return {
            "regressed": regressed,
            "remaining": remaining,
            "fixed": fixed - regressed - remaining,
        }
    return {"regressed": set(), "remaining": set(), "fixed": set()}


def _normalized_check_set(payload: dict[str, Any], *keys: str) -> set[str]:
    normalized_values = set()
    for key in keys:
        for value in _string_items(payload.get(key, [])):
            normalized = _normalize_cluster_text(value)
            if normalized:
                normalized_values.add(normalized)
    return normalized_values


def _diagnosis_feedback_priority(
    diagnosis: dict[str, Any],
    *,
    feedback_sets: dict[str, set[str]],
) -> int:
    checks = {
        _normalize_cluster_text(value)
        for value in _diagnosis_failed_checks(diagnosis)
        if _normalize_cluster_text(value)
    }
    if checks & feedback_sets["regressed"]:
        return 0
    if checks & feedback_sets["remaining"]:
        return 1
    # A prior win is not evidence that a later execution still passes.
    if checks and checks <= feedback_sets["fixed"]:
        return 3
    return 2


def _diagnosis_failure_cluster(diagnosis: dict[str, Any]) -> dict[str, Any]:
    raw_cluster = diagnosis.get("failure_cluster", {})
    cluster = dict(raw_cluster) if isinstance(raw_cluster, dict) else {}
    checks = _diagnosis_failed_checks(diagnosis)
    observable = str(cluster.get("observable_behavior", "") or "").strip()
    if not observable:
        contract = diagnosis.get("decision_contract", {})
        if isinstance(contract, dict):
            observable = str(contract.get("acceptance_observable", "") or "").strip()
    if not checks and not observable:
        return {}
    return {
        "failed_checks": checks,
        "observable_behavior": observable,
    }


def _diagnosis_failed_checks(diagnosis: dict[str, Any]) -> list[str]:
    cluster = diagnosis.get("failure_cluster", {})
    checks = _string_items(cluster.get("failed_checks", []) if isinstance(cluster, dict) else [])
    if checks:
        return list(dict.fromkeys(checks))
    verifier = diagnosis.get("verifier_observations", {})
    if not isinstance(verifier, dict):
        return []
    return list(
        dict.fromkeys(
            [
                *_string_items(verifier.get("failed_fail_to_pass_tests", [])),
                *_string_items(verifier.get("failed_pass_to_pass_tests", [])),
            ]
        )
    )


def _diagnoses_are_duplicate(
    left: dict[str, Any],
    right: dict[str, Any],
) -> bool:
    left_checks = {_normalize_cluster_text(value) for value in _diagnosis_failed_checks(left)}
    right_checks = {_normalize_cluster_text(value) for value in _diagnosis_failed_checks(right)}
    if left_checks and right_checks and left_checks != right_checks:
        return False

    left_cluster = _diagnosis_failure_cluster(left)
    right_cluster = _diagnosis_failure_cluster(right)
    left_observable = _normalize_cluster_text(left_cluster.get("observable_behavior", ""))
    right_observable = _normalize_cluster_text(right_cluster.get("observable_behavior", ""))
    if left_observable and right_observable:
        if not _cluster_texts_are_similar(left_observable, right_observable):
            return False
        return True
    if left_checks and right_checks:
        return True
    return _normalize_target_ref(left.get("target_ref", "")) == _normalize_target_ref(
        right.get("target_ref", "")
    ) and _normalize_cluster_text(left.get("failure_mode", "")) == _normalize_cluster_text(
        right.get("failure_mode", "")
    )


def _normalize_cluster_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _cluster_texts_are_similar(left: str, right: str) -> bool:
    if left == right:
        return True
    left_tokens = set(re.findall(r"[\w:.+-]+", left))
    right_tokens = set(re.findall(r"[\w:.+-]+", right))
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens) >= 0.8


def _compact_per_case_diagnoses(per_case_diagnoses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep aggregation inputs concise; group attribution fields into a nested sub-dict.

    Per-case LLM output has flat attribution fields (root_cause / critical_mistake /
    general_mechanism / decision_contract / target_ref / evidence_refs / confidence).
    This function packages them into an ``attribution`` sub-dict so aggregation
    receives the complete decision-level causal handoff.
    """
    compact: list[dict[str, Any]] = []
    for item in per_case_diagnoses:
        attribution: dict[str, Any] = {
            "root_cause": _truncate_text(item.get("root_cause", ""), 500),
            "critical_mistake": _truncate_text(item.get("critical_mistake", ""), 500),
            "general_mechanism": _truncate_text(item.get("general_mechanism", ""), 500),
            "decision_contract": _bounded_structured_value(
                item.get("decision_contract", {}),
                1400,
            ),
            "failure_cluster": _bounded_structured_value(
                _diagnosis_failure_cluster(item),
                1000,
            ),
            "target_ref": item.get("target_ref", ""),
            "evidence_refs": item.get("evidence_refs", []),
            "confidence": item.get("confidence", ""),
        }
        compact.append(
            {
                "case_id": item.get("case_id", ""),
                "analysis_failed": bool(item.get("analysis_failed", False)),
                "issue_category": item.get("issue_category", item.get("category", "")),
                "severity": item.get("severity", ""),
                "summary": _truncate_text(item.get("summary", ""), 500),
                "failure_mode": item.get("failure_mode", ""),
                "affected_components": item.get("affected_components", []),
                "recommendation": _truncate_text(item.get("recommendation", ""), 500),
                "attribution": attribution,
            }
        )
    return compact


def _aggregate_structured_diagnoses(
    *,
    per_case_results: list[dict[str, Any]],
    max_issues: int,
    evidence_limit_per_issue: int,
) -> list[TeamIssue]:
    """Group canonical per-case diagnoses without another model call."""
    base_counts: dict[tuple[str, str, str], int] = {}
    for item in per_case_results:
        if item.get("analysis_failed"):
            continue
        target_ref = _normalize_target_ref(item.get("target_ref", ""))
        if not target_ref or target_ref == "unassigned":
            continue
        failure_mode = str(item.get("failure_mode", "") or "")
        base = (str(item.get("case_id", "") or ""), target_ref, failure_mode)
        base_counts[base] = base_counts.get(base, 0) + 1
    collision_bases = {
        (target_ref, failure_mode) for (_, target_ref, failure_mode), count in base_counts.items() if count > 1
    }

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in per_case_results:
        if item.get("analysis_failed"):
            continue
        target_ref = _normalize_target_ref(item.get("target_ref", ""))
        if not target_ref or target_ref == "unassigned":
            continue
        failure_mode = str(item.get("failure_mode", "") or "")
        discriminator = _diagnosis_group_discriminator(item) if (target_ref, failure_mode) in collision_bases else ""
        groups.setdefault((target_ref, failure_mode, discriminator), []).append(item)

    ranked_groups = sorted(
        groups.items(),
        key=lambda entry: (
            -max(_severity_rank(item.get("severity")) for item in entry[1]),
            -max(_confidence_rank(item.get("confidence")) for item in entry[1]),
            entry[0][0],
            entry[0][1],
            entry[0][2],
        ),
    )
    issues: list[TeamIssue] = []
    for index, ((target_ref, failure_mode, _), items) in enumerate(
        ranked_groups[: max(0, max_issues)],
        start=1,
    ):
        strongest = max(
            items,
            key=lambda item: (
                _severity_rank(item.get("severity")),
                _confidence_rank(item.get("confidence")),
            ),
        )
        evidence: list[dict[str, Any]] = []
        for item in items:
            components = _string_items(item.get("affected_components", []))
            evidence.append(
                {
                    "case_id": str(item.get("case_id", "")),
                    "failure_mode": str(item.get("failure_mode", failure_mode)),
                    "affected_component": components[0] if components else "",
                    "failure_cluster": _diagnosis_failure_cluster(item),
                }
            )
        affected_components = list(
            dict.fromkeys(
                component for item in items for component in _string_items(item.get("affected_components", []))
            )
        )
        issue_category = _issue_category_from_target_ref(target_ref)
        issue = _dict_to_team_issue(
            {
                "issue_id": f"issue_{index:03d}",
                "category": "member_harness" if issue_category == "member_harness" else "team_coordination",
                "severity": str(strongest.get("severity", "medium") or "medium"),
                "summary": str(strongest.get("summary", "") or ""),
                "affected_cases": [str(item["case_id"]) for item in items if item.get("case_id")],
                "affected_components": affected_components,
                "evidence": evidence[: max(1, evidence_limit_per_issue)],
                "suspected_team_scope": "member" if issue_category == "member_harness" else "team_skill",
                "recommendation": str(strongest.get("recommendation", "") or ""),
                "metadata": {
                    "attribution": {
                        "root_cause": str(strongest.get("root_cause", "") or ""),
                        "critical_mistake": str(strongest.get("critical_mistake", "") or ""),
                        "general_mechanism": str(strongest.get("general_mechanism", "") or ""),
                        "decision_contract": dict(
                            strongest.get("decision_contract", {})
                            if isinstance(strongest.get("decision_contract"), dict)
                            else {}
                        ),
                        "failure_cluster": _diagnosis_failure_cluster(strongest),
                        "target_ref": target_ref,
                        "evidence_refs": list(strongest.get("evidence_refs") or []),
                        "confidence": str(strongest.get("confidence", "") or ""),
                    }
                },
            }
        )
        issues.append(_apply_g5_mapping(issue))
    return issues


def _diagnosis_group_discriminator(diagnosis: dict[str, Any]) -> str:
    """Keep distinct diagnoses from one case separate during aggregation."""
    cluster = _diagnosis_failure_cluster(diagnosis)
    checks = sorted(_normalize_cluster_text(value) for value in _string_items(cluster.get("failed_checks", [])))
    observable = _normalize_cluster_text(cluster.get("observable_behavior", ""))
    if checks or observable:
        return json.dumps(
            {"checks": checks, "observable": observable},
            ensure_ascii=False,
            sort_keys=True,
        )
    return _normalize_cluster_text(diagnosis.get("root_cause") or diagnosis.get("summary") or "")


def _diagnosis_unavailable_result(
    case: CaseAnalysisInput,
    exc: BaseException,
) -> dict[str, Any]:
    error = str(exc)
    if isinstance(exc, _DiagnosisOutputFormatError):
        error_type = "output_format"
        mechanism = "The diagnosis model exhausted bounded JSON-format repair."
    elif isinstance(exc, _DiagnosisContentError):
        error_type = "diagnosis_content"
        mechanism = "Valid JSON contained no usable diagnosis entries after bounded repair."
    elif isinstance(exc, DiagnosisAgentExecutionError):
        error_type = "agent_runtime"
        mechanism = "Diagnosis agent execution failed before completing an answer."
    else:
        error_type = "model_service"
        mechanism = "Retryable model-service failure during analyzer diagnosis."
    return {
        "case_id": case.case_id,
        "score": case.score,
        "evaluation_passed": case.evaluation_passed,
        "evaluation_reason": case.evaluation_reason,
        "analysis_failed": True,
        "diagnosis_status": "unavailable",
        "issue_category": "unassigned",
        "severity": "low",
        "summary": "Per-case diagnosis was unavailable because the diagnosis model returned no usable output.",
        "failure_mode": "diagnosis_unavailable",
        "root_cause": "Diagnosis model call did not produce usable output for this case.",
        "critical_mistake": "No case-level attribution was produced.",
        "general_mechanism": mechanism,
        "target_ref": "unassigned",
        "evidence_refs": [],
        "affected_components": [],
        "recommendation": "Do not optimize from this case-level diagnosis; rerun analysis or use other case diagnoses.",
        "confidence": "low",
        "diagnosis_error_type": error_type,
        "error": error,
    }


def _case_prior_candidate_feedback(
    feedback: dict[str, Any] | None,
    case_id: str,
) -> dict[str, Any]:
    """Return only paired candidate experiments for the diagnosed case."""
    if not isinstance(feedback, dict):
        return {}
    by_case = feedback.get("by_case", {})
    if not isinstance(by_case, dict):
        return {}
    records = by_case.get(case_id, [])
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        return {}
    return {
        "case_id": case_id,
        "experiments": [dict(record) for record in records[-3:] if isinstance(record, dict)],
    }


def _normalize_target_ref(value: Any) -> str:
    return str(value or "").strip().replace("-", "_")


def _issue_category_from_target_ref(target_ref: str) -> str:
    scope = _target_scope_from_target_ref(target_ref.lower())
    return scope if scope in {"member_harness", "team_skill"} else ""


def _severity_rank(value: Any) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(str(value or "").lower(), 0)


def _confidence_rank(value: Any) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(str(value or "").lower(), 0)


# ---------------------------------------------------------------------------
# G5: suspected_team_scope + category → optimization_target / target_members
# ---------------------------------------------------------------------------


def _apply_g5_mapping(issue: TeamIssue) -> TeamIssue:
    """Fill optimization_target and target_members from suspected_team_scope + category.

    ``target_ref`` in ``metadata.attribution`` is a downstream hint for optimizers.
    When it is explicitly ``unassigned`` or points to evaluator/analyzer evidence
    plumbing, the issue is kept out of optimizer gates.
    """
    target_ref = _issue_target_ref(issue)
    if target_ref == "unassigned" or _is_evidence_pipeline_failure(issue):
        return replace(issue, optimization_target="", target_members=[])

    target_scope = _target_scope_from_target_ref(target_ref)
    if target_scope == "member_harness":
        coordinator_issue = _coordinator_member_issue_as_team_skill(issue, target_ref)
        if coordinator_issue is not None:
            return coordinator_issue
        optimization_target = "member_harness"
        target_members = _target_members_from_issue(issue)
        if issue.optimization_target == optimization_target and issue.target_members == target_members:
            return issue
        return replace(issue, optimization_target=optimization_target, target_members=target_members)
    if target_scope == "team_skill":
        optimization_target = "team_skill"
        target_members = []
        if issue.optimization_target == optimization_target and issue.target_members == target_members:
            return issue
        return replace(issue, optimization_target=optimization_target, target_members=target_members)

    scope = issue.suspected_team_scope
    category = issue.category

    if scope == "member" or category == "member_harness":
        optimization_target = "member_harness"
        target_members = _target_members_from_issue(issue)
    elif scope == "team_skill" or category == "team_coordination":
        optimization_target = "team_skill"
        target_members = []
    else:
        optimization_target = "team_skill"
        target_members = []

    if issue.optimization_target == optimization_target and issue.target_members == target_members:
        return issue
    return replace(issue, optimization_target=optimization_target, target_members=target_members)


def _issue_target_ref(issue: TeamIssue) -> str:
    """Return the normalized attribution target_ref, if present."""
    attribution = issue.metadata.get("attribution")
    if not isinstance(attribution, dict):
        return ""
    return str(attribution.get("target_ref", "") or "").strip().lower().replace("-", "_")


def _coordinator_member_issue_as_team_skill(
    issue: TeamIssue,
    target_ref: str,
) -> TeamIssue | None:
    """Route coordinator/team protocol targets to Team Skill optimization.

    ``team`` / ``team_leader`` is the Agent Team coordinator, not a business
    member harness.  If Analyzer emits ``member_harness.team.*`` for a
    coordination or completion-gate failure, keeping it as member_harness makes
    MemberOptimizer fail with ``no_targets`` because no such member exists.
    """
    role = _target_member_from_target_ref(target_ref)
    if not is_team_coordinator_role(role):
        return None
    team_target_ref = _coordinator_team_skill_target_ref(issue)
    metadata = _with_attribution_target_ref(issue.metadata, team_target_ref)
    affected_components = _string_items(metadata.get("affected_components"))
    if not affected_components:
        metadata["affected_components"] = ["team_leader"]
    return replace(
        issue,
        category="team_coordination",
        suspected_team_scope="team_skill",
        optimization_target="team_skill",
        target_members=[],
        metadata=metadata,
    )


def _coordinator_team_skill_target_ref(issue: TeamIssue) -> str:
    variable = "constraint_violation" if _looks_like_completion_contract_issue(issue) else "role_coordination"
    return f"team_skill.team_leader.{variable}"


def _looks_like_completion_contract_issue(issue: TeamIssue) -> bool:
    text_parts = [
        issue.summary,
        issue.recommendation,
        json.dumps(issue.evidence, ensure_ascii=False),
        json.dumps(issue.metadata, ensure_ascii=False),
    ]
    text = "\n".join(str(part).lower() for part in text_parts)
    completion_markers = (
        "artifact",
        "claim_task",
        "complete",
        "completion",
        "deliverable",
        "file",
        "output",
        "required",
        "status",
        "verify",
        "verification",
    )
    return any(marker in text for marker in completion_markers)


def _with_attribution_target_ref(metadata: dict[str, Any], target_ref: str) -> dict[str, Any]:
    updated = dict(metadata)
    attribution = updated.get("attribution")
    if not isinstance(attribution, dict):
        attribution = {}
    else:
        attribution = dict(attribution)
    attribution["target_ref"] = target_ref
    updated["attribution"] = attribution
    return updated


def _is_evidence_pipeline_failure(issue: TeamIssue) -> bool:
    """Return whether the issue describes analyzer/evaluator evidence plumbing."""
    text_parts = [
        issue.summary,
        issue.recommendation,
        json.dumps(issue.evidence, ensure_ascii=False),
        json.dumps(issue.metadata, ensure_ascii=False),
    ]
    text = "\n".join(str(part).lower() for part in text_parts)
    evidence_artifact_markers = (
        "trajectory_events.jsonl",
        "normalized_trace.json",
        "evidence_summary.md",
    )
    missing_markers = (
        "no such file",
        "not found",
        "missing",
        "failed to read",
        "failed to load",
    )
    return any(marker in text for marker in evidence_artifact_markers) and any(
        marker in text for marker in missing_markers
    )


def _target_members_from_issue(issue: TeamIssue) -> list[str]:
    """Extract member targets from explicit issue evidence; never invent members."""
    candidates: list[str] = []
    candidates.extend(_string_items(issue.target_members))
    target_ref_member = _target_member_from_target_ref(_issue_target_ref(issue))
    if target_ref_member:
        candidates.append(target_ref_member)
    candidates.extend(_string_items(issue.metadata.get("affected_components", [])))
    for evidence in issue.evidence:
        if not isinstance(evidence, dict):
            continue
        candidates.extend(_string_items(evidence.get("affected_components", [])))
        candidates.extend(_string_items(evidence.get("affected_component", "")))
    return list(dict.fromkeys(candidates))


def _target_scope_from_target_ref(target_ref: str) -> str:
    parts = target_ref.split(".")
    if parts and parts[0] in {"member_harness", "team_skill"}:
        return parts[0]
    return ""


def _target_member_from_target_ref(target_ref: str) -> str:
    parts = target_ref.split(".")
    if len(parts) >= 3 and parts[0] == "member_harness" and parts[1]:
        return parts[1]
    return ""


def _string_items(value: Any) -> list[str]:
    """Normalize a string or string list into non-empty strings."""
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        strings = [item.strip() for item in value if isinstance(item, str)]
        return [item for item in strings if item]
    return []


def _make_diagnosis_runtime_dir(runtime_root: Path, case_id: str) -> Path:
    """Create a unique case-scoped diagnosis runtime directory path."""
    safe_case_id = _safe_path_segment(case_id) or "case"
    return runtime_root / f"{safe_case_id}-{uuid.uuid4().hex}"


def _load_case_result(case: CaseAnalysisInput) -> dict[str, Any]:
    """Read the evaluator-owned result payload without exposing it to the agent."""
    result_path = Path(case.result_path)
    if not result_path.is_file():
        return {}
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _prepare_repository_snapshot(*, case: CaseAnalysisInput, runtime_dir: Path) -> bool:
    """Copy the evaluated workspace into an isolated diagnosis snapshot."""
    from .repository_snapshot import prepare_repository_snapshot

    result = _load_case_result(case)
    workspace_value = result.get("workspace_dir")
    evaluation = result.get("evaluation")
    metadata = evaluation.get("metadata") if isinstance(evaluation, dict) else None
    patch_value = metadata.get("model_patch_path") if isinstance(metadata, dict) else None
    manifest = prepare_repository_snapshot(
        workspace=workspace_value.strip() if isinstance(workspace_value, str) else None,
        patch=patch_value.strip() if isinstance(patch_value, str) else None,
        runtime_dir=runtime_dir,
    )
    if manifest["errors"]:
        logger.warning(
            "diagnosis snapshot for %s: repository=%s, patch=%s, copy_errors=%d; see repository_snapshot.json",
            case.case_id,
            manifest["repository"],
            manifest["patch"],
            len(manifest["errors"]),
        )
    return manifest["repository"] == "complete"


def _prepare_diagnosis_evidence(*, case: CaseAnalysisInput, runtime_dir: Path) -> bool:
    """Write evidence and an isolated evaluated-repository snapshot."""
    _remove_path(runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    _prepare_repository_snapshot(case=case, runtime_dir=runtime_dir)
    history = project_execution_history(
        _read_json_if_exists(Path(case.result_path).parent / "judge" / "normalized_trace.json")
    )
    (runtime_dir / "execution_history.json").write_text(
        json.dumps(history, ensure_ascii=True, indent=2), encoding="utf-8"
    )
    summary = _build_evidence_summary(case)
    if not summary.strip():
        return False
    summary += (
        "\n\n## Repository Snapshot Availability\n"
        "Read repository_snapshot.json before repository probes. It lists any missing "
        "or unreadable files. Readable repository files and source_patch.diff are retained "
        "independently. A missing snapshot file is not evidence of missing source code. "
        "Do not search outside this diagnosis workspace for omitted evidence.\n"
    )
    (runtime_dir / "evidence_summary.md").write_text(summary, encoding="utf-8")
    return True


def _build_evidence_summary(case: CaseAnalysisInput) -> str:
    """Build a bounded evidence summary from normalized trace and verifier outputs."""
    case_dir = Path(case.result_path).parent
    verifier_dir = case_dir / "verifier"
    normalized_trace_path = case_dir / "judge" / "normalized_trace.json"

    lines: list[str] = [
        "# Analyzer Evidence Summary",
        "",
        "## Authoritative Task Contract",
        "- provenance: case.input (user/benchmark supplied)",
        "- Only this section establishes what the original reproduction contains.",
        "- Commands and probes in Agent-Generated Execution Evidence are not task facts.",
        "```text",
        case.input,
        "```",
    ]
    benchmark_test_contract = case.benchmark_test_contract
    if benchmark_test_contract:
        lines.extend(
            [
                "",
                "## Authoritative Benchmark Test Contract",
                f"- provenance: {benchmark_test_contract.get('provenance', '')}",
                "- This is acceptance-test evidence, not the benchmark solution patch.",
                "- FAIL_TO_PASS:",
                "```json",
                json.dumps(
                    benchmark_test_contract.get("fail_to_pass", []),
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
                "- PASS_TO_PASS:",
                "```json",
                json.dumps(
                    benchmark_test_contract.get("pass_to_pass", []),
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
                "- test_patch:",
                "```diff",
                str(benchmark_test_contract.get("test_patch", "")),
                "```",
            ]
        )
    lines.extend(
        [
            "",
            "## Case Facts",
            f"- case_id: {case.case_id}",
            f"- status: {case.status}",
            f"- score: {case.score}",
            f"- evaluation_passed: {case.evaluation_passed}",
            f"- evaluation_method: {case.evaluation_method}",
            f"- evaluation_reason: {_one_line(case.evaluation_reason, 500)}",
        ]
    )
    if case.error:
        lines.append(f"- execution_error: {_one_line(case.error, 500)}")

    judge_breakdown = _summarize_evaluation_metadata(case.evaluation_metadata)
    quality_gaps = judge_breakdown.get("quality_gaps", []) if isinstance(judge_breakdown, dict) else []
    if isinstance(quality_gaps, list) and quality_gaps:
        lines.extend(["", "## Judge Quality Gaps"])
        for gap in quality_gaps[:8]:
            if not isinstance(gap, dict):
                continue
            lines.append(
                "- "
                f"id={_one_line(gap.get('id', ''), 120)} "
                f"severity={_one_line(gap.get('severity', ''), 80)} "
                f"roles={_one_line(gap.get('affected_roles', []), 180)} "
                f"surfaces={_one_line(gap.get('likely_surfaces', []), 180)} "
                f"missing_capability={_one_line(gap.get('missing_capability', ''), 300)} "
                f"evidence={_one_line(gap.get('evidence', ''), 500)}"
            )
            why_it_matters = _one_line(gap.get("why_it_matters", ""), 300)
            if why_it_matters:
                lines.append(f"  why_it_matters={why_it_matters}")

    behaviors = judge_breakdown.get("behaviors", []) if isinstance(judge_breakdown, dict) else []
    low_behaviors = [
        behavior for behavior in behaviors if isinstance(behavior, dict) and _safe_float(behavior.get("score")) < 0.8
    ]
    if low_behaviors:
        lines.extend(["", "## Low-Score Judge Behaviors"])
        for behavior in low_behaviors[:8]:
            lines.append(
                "- "
                f"id={_one_line(behavior.get('id', ''), 120)} "
                f"score={behavior.get('score')} "
                f"failure_reason={_one_line(behavior.get('failure_reason', ''), 400)} "
                f"missing_capability={_one_line(behavior.get('missing_capability', ''), 300)} "
                f"surface_hint={_one_line(behavior.get('suggested_surface_hint', ''), 80)} "
                f"evidence={_one_line(behavior.get('evidence', ''), 300)}"
            )

    trace_data = _read_json_if_exists(normalized_trace_path)
    trace_events = _summarize_normalized_trace(trace_data)
    validation_events = _validation_events_from_result(case.result_path) or trace_events
    validation_inventory = _validation_inventory_from_events(
        validation_events,
        verifier_passed=case.evaluation_passed,
    )
    lines.extend(["", "## Deterministic Validation Inventory"])
    lines.append(f"- project_test_suite_attempted: {str(validation_inventory['project_test_suite_attempted']).lower()}")
    lines.append(f"- project_test_suite_result: {validation_inventory['project_test_suite_result']}")
    lines.append(f"- authoritative_verifier_result: {validation_inventory['authoritative_verifier_result']}")
    for event in validation_inventory["project_test_events"]:
        lines.append(
            "- project_test_event: "
            f"command={_one_line(event['command'], 500)} "
            f"result={event['result']} output_tail="
            f"{_one_line(str(event['output'])[-700:], 700)}"
        )
    if (
        validation_inventory["project_test_suite_result"] == "passed"
        and validation_inventory["authoritative_verifier_result"] == "failed"
    ):
        lines.append(
            "- hard_fact: local project tests passed while the authoritative verifier failed; "
            "do not diagnose skipped project testing or smoke-only validation."
        )

    verifier_inventory = _build_verifier_inventory(case)
    if verifier_inventory:
        lines.extend(["", "## Deterministic Verifier Inventory"])
        if "empty_patch" in verifier_inventory:
            lines.append(f"- empty_patch: {str(verifier_inventory['empty_patch']).lower()}")
        if "patch_successfully_applied" in verifier_inventory:
            lines.append(
                f"- patch_successfully_applied: {str(verifier_inventory['patch_successfully_applied']).lower()}"
            )
            lines.append(f"- resolved: {str(verifier_inventory['resolved']).lower()}")
            lines.append(
                "- failed_fail_to_pass_tests: "
                f"{json.dumps(verifier_inventory['failed_fail_to_pass_tests'], ensure_ascii=False)}"
            )
            lines.append(
                "- failed_pass_to_pass_tests: "
                f"{json.dumps(verifier_inventory['failed_pass_to_pass_tests'], ensure_ascii=False)}"
            )
        failure_output = str(verifier_inventory.get("verifier_failure_output_excerpt", "") or "").strip()
        if failure_output:
            lines.extend(
                [
                    "",
                    "### Authoritative failure output excerpt",
                    "```text",
                    _truncate_text(failure_output, 3500),
                    "```",
                ]
            )
        if verifier_inventory.get("patch_successfully_applied") is True and verifier_inventory.get("resolved") is False:
            lines.append(
                "- hard_fact: the patch applied successfully; do not attribute the unresolved "
                "result to patch-application failure or working-tree contamination."
            )

    reward = _read_text_if_exists(verifier_dir / "reward.txt", 80).strip()
    stdout = _read_text_if_exists(verifier_dir / "stdout.log", 1500)
    stderr = _read_text_if_exists(verifier_dir / "stderr.log", 1500)
    if reward or stdout or stderr:
        lines.extend(["", "## Verifier Outcome"])
        if reward:
            lines.append(f"- reward={_one_line(reward, 80)}")
        if stderr:
            lines.extend(["", "### stderr excerpt", "```text", _truncate_text(stderr, 1500), "```"])
        if stdout:
            lines.extend(["", "### stdout excerpt", "```text", _truncate_text(stdout, 1500), "```"])

    if trace_events:
        failed = [event for event in trace_events if event.get("error")]
        lines.extend(["", "## Agent-Generated Execution Evidence"])
        lines.append("- provenance: evaluated agent trajectory; commands/probes below were agent-authored.")
        lines.append("- Do not infer original task inputs or expected semantics from these probes.")
        lines.extend(["", "### Decisive Failed Steps"])
        if failed:
            for event in failed[:8]:
                lines.append(_format_trace_event(event))
        else:
            lines.append("- No failed tool call was present in the bounded normalized trace.")
        lines.extend(["", "### Key Events"])
        for event in trace_events[-12:]:
            lines.append(_format_trace_event(event))

    return "\n".join(lines).strip() + "\n"


def _build_validation_inventory(case: CaseAnalysisInput) -> dict[str, Any]:
    case_dir = Path(case.result_path).parent
    trace_data = _read_json_if_exists(case_dir / "judge" / "normalized_trace.json")
    trace_events = _summarize_normalized_trace(trace_data)
    return _validation_inventory_from_events(
        _validation_events_from_result(case.result_path) or trace_events,
        verifier_passed=case.evaluation_passed,
    )


def _build_verifier_inventory(case: CaseAnalysisInput) -> dict[str, Any]:
    """Extract authoritative patch-application and test outcomes."""
    metadata = case.evaluation_metadata
    empty_patch = metadata.get("empty_patch") if isinstance(metadata, dict) else None
    raw_reports = metadata.get("instance_report") if isinstance(metadata, dict) else None
    if not isinstance(raw_reports, dict) or not raw_reports:
        return {"empty_patch": empty_patch} if empty_patch is not None else {}
    report = raw_reports.get(case.case_id)
    if not isinstance(report, dict):
        report = next((item for item in raw_reports.values() if isinstance(item, dict)), None)
    if not isinstance(report, dict):
        return {}

    tests_status = report.get("tests_status")
    tests_status = tests_status if isinstance(tests_status, dict) else {}

    def _failures(group_name: str) -> list[str]:
        group = tests_status.get(group_name)
        if not isinstance(group, dict):
            return []
        failures = group.get("failure")
        if not isinstance(failures, list):
            return []
        return [str(item) for item in failures[:24] if str(item).strip()]

    return {
        "empty_patch": empty_patch,
        "patch_exists": report.get("patch_exists"),
        "patch_successfully_applied": report.get("patch_successfully_applied"),
        "resolved": report.get("resolved"),
        "failed_fail_to_pass_tests": _failures("FAIL_TO_PASS"),
        "failed_pass_to_pass_tests": _failures("PASS_TO_PASS"),
        "verifier_failure_output_excerpt": _truncate_text(
            str(metadata.get("test_output_excerpt") or ""),
            8000,
        ),
    }


def _validation_events_from_result(result_path: str) -> list[dict[str, Any]]:
    """Read full command-result excerpts retained by the execution backend."""
    result_data = _read_json_if_exists(Path(result_path))
    metadata = result_data.get("metadata") if isinstance(result_data, dict) else None
    execution = metadata.get("execution") if isinstance(metadata, dict) else None
    command_log = execution.get("command_log") if isinstance(execution, dict) else None
    if not isinstance(command_log, list):
        return []
    events: list[dict[str, Any]] = []
    for record in command_log:
        if not isinstance(record, dict):
            continue
        output_parts = (str(record.get("stdout_excerpt") or ""), str(record.get("stderr_excerpt") or ""))
        output = "\n".join(part for part in output_parts if part)
        exit_code = record.get("exit_code")
        error = "" if exit_code in {None, 0, "0"} else f"exit_code={exit_code}"
        events.append(
            {
                "tool": "command_log",
                "input": str(record.get("command") or ""),
                "output": _one_line(output, 300),
                "output_tail": _one_line(output[-1200:], 1200),
                "error": error,
                "validation_result": _validation_result_signal(output, error),
            }
        )
    return events


def _validation_inventory_from_events(
    events: list[dict[str, Any]],
    *,
    verifier_passed: bool,
) -> dict[str, Any]:
    """Extract project-test facts before an LLM can reinterpret the trajectory."""
    project_events: list[dict[str, str]] = []
    for event in events:
        command = str(event.get("input") or "")
        lowered = " ".join(command.lower().split())
        is_pytest_suite = "pytest" in lowered and any(marker in f" {lowered} " for marker in (" tests/ ", " ./tests/ "))
        is_project_suite = is_pytest_suite or any(
            marker in lowered for marker in ("make test", " tox", "tox ", " nox", "nox ", "npm test", "pnpm test")
        )
        if not is_project_suite:
            continue
        output = str(event.get("output_tail") or event.get("output") or "")
        result = str(event.get("validation_result") or "unknown")
        project_events.append(
            {
                "command": command,
                "output": output,
                "result": result,
            }
        )
    suite_result = "not_observed"
    if project_events:
        suite_result = (
            "passed"
            if any(event["result"] == "passed" for event in project_events)
            else "failed"
            if any(event["result"] == "failed" for event in project_events)
            else "not_observed"
        )
    return {
        "project_test_suite_attempted": bool(project_events),
        "project_test_suite_result": suite_result,
        "authoritative_verifier_result": "passed" if verifier_passed else "failed",
        "project_test_events": project_events[-4:],
    }


def _case_diagnoses_validation_conflicts(
    diagnoses: list[dict[str, Any]],
    inventory: dict[str, Any],
    verifier_inventory: dict[str, Any] | None = None,
) -> list[str]:
    """Validate every diagnosis while retaining its position in repair feedback."""
    conflicts: list[str] = []
    for index, diagnosis in enumerate(diagnoses, start=1):
        diagnosis_conflicts = _diagnosis_validation_conflicts(diagnosis, inventory, verifier_inventory)
        conflicts.extend(f"diagnosis[{index}]: {error}" for error in diagnosis_conflicts)
    return conflicts


def _verifier_test_ids_match(observed: Any, expected: list[str], failure_output: str) -> bool:
    """Match report IDs, allowing only uniquely corroborated truncated names."""
    if not isinstance(observed, list) or any(not isinstance(item, str) for item in observed):
        return False
    if len(observed) != len(expected):
        return False
    if observed == expected:
        return True

    # Some verifier reports retain only the first whitespace-delimited token.
    # A shared prefix alone is not evidence: require a unique FAILED log record.
    failures: dict[str, set[str]] = {}
    for line in failure_output.splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) == 2 and fields[0] == "FAILED":
            body = fields[1]
            failures.setdefault(body.split(maxsplit=1)[0], set()).add(body)

    for name, reported in zip(observed, expected):
        if name == reported:
            continue
        if "[" not in reported or reported.endswith("]"):
            return False
        records = failures.get(reported, set())
        if len(records) != 1:
            return False
        record = next(iter(records))
        # Normalize control-character representations only when log-grounded.
        escaped = name
        for character, escape in (("\n", r"\n"), ("\r", r"\r"), ("\t", r"\t")):
            escaped = escaped.replace(character, escape).replace("\\" + escape, escape)
        if not any(
            variant.endswith("]") and (record == variant or record.startswith(variant + " - "))
            for variant in {name, escaped}
        ):
            return False
    return True


def _diagnosis_validation_conflicts(
    diagnosis: dict[str, Any],
    inventory: dict[str, Any],
    verifier_inventory: dict[str, Any] | None = None,
    *,
    public_task: str | None = None,
) -> list[str]:
    """Reject diagnoses that contradict deterministic test and verifier facts."""
    errors: list[str] = []
    if (
        inventory.get("project_test_suite_result") == "passed"
        and inventory.get("authoritative_verifier_result") == "failed"
    ):
        observations = diagnosis.get("validation_observations")
        if not isinstance(observations, dict):
            errors.append("missing validation_observations for local-pass/verifier-fail contradiction")
        else:
            expected = {
                "project_test_suite_attempted": True,
                "project_test_suite_result": "passed",
                "authoritative_verifier_result": "failed",
            }
            for key, value in expected.items():
                if observations.get(key) != value:
                    errors.append(f"validation_observations.{key} must equal {value!r}")
            if not str(observations.get("contradiction_explanation") or "").strip():
                errors.append("validation_observations.contradiction_explanation must be non-empty")
        recommendation = " ".join(str(diagnosis.get("recommendation") or "").lower().split())
        invalid_recommendations = (
            "require running the project's existing test suite",
            "must run the project's own test suite",
            "rather than only a self-authored smoke",
            "instead of only a self-authored smoke",
        )
        if any(phrase in recommendation for phrase in invalid_recommendations):
            errors.append("recommendation contradicts observed successful project-suite execution")

    verifier_inventory = verifier_inventory or {}
    if verifier_inventory.get("patch_successfully_applied") is True and verifier_inventory.get("resolved") is False:
        verifier_observations = diagnosis.get("verifier_observations")
        if not isinstance(verifier_observations, dict):
            errors.append("missing verifier_observations for applied-but-unresolved patch")
        else:
            expected_verifier = {
                "patch_successfully_applied": True,
                "failed_fail_to_pass_tests": verifier_inventory.get("failed_fail_to_pass_tests", []),
                "failed_pass_to_pass_tests": verifier_inventory.get("failed_pass_to_pass_tests", []),
            }
            for key, value in expected_verifier.items():
                observed = verifier_observations.get(key)
                matches = (
                    _verifier_test_ids_match(
                        observed,
                        value,
                        str(verifier_inventory.get("verifier_failure_output_excerpt") or ""),
                    )
                    if isinstance(value, list)
                    else observed == value
                )
                if not matches:
                    errors.append(f"verifier_observations.{key} must equal {value!r}")
        diagnosis_fields = ("summary", "root_cause", "critical_mistake", "general_mechanism", "recommendation")
        diagnosis_text = " ".join(str(diagnosis.get(key) or "").lower() for key in diagnosis_fields)
        patch_failure_claims = (
            "patch application to fail",
            "patch application failed",
            "prevented patch application",
            "patch failed to apply",
            "could not apply the patch",
        )
        if any(claim in diagnosis_text for claim in patch_failure_claims):
            errors.append("diagnosis contradicts authoritative successful patch application")

    return errors


def _diagnosis_evidence_conflict_result(
    case: CaseAnalysisInput,
    errors: list[str],
) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "score": case.score,
        "evaluation_passed": case.evaluation_passed,
        "evaluation_reason": case.evaluation_reason,
        "analysis_failed": True,
        "diagnosis_status": "evidence_conflict",
        "issue_category": "unassigned",
        "severity": "low",
        "summary": "Diagnosis contradicted deterministic evaluation evidence.",
        "failure_mode": "diagnosis_evidence_conflict",
        "root_cause": "; ".join(errors),
        "critical_mistake": "The diagnosis model contradicted code-derived test or verifier evidence.",
        "general_mechanism": "Do not optimize from a diagnosis that contradicts deterministic evidence.",
        "target_ref": "unassigned",
        "evidence_refs": [],
        "affected_components": [],
        "recommendation": "Rerun diagnosis with deterministic validation and verifier inventories enforced.",
        "confidence": "low",
    }


def _safe_float(value: Any) -> float:
    """Parse a score for evidence-summary filtering."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0


def _summarize_normalized_trace(trace_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract bounded tool-call events from normalized_trace.json."""
    events: list[dict[str, Any]] = []
    traces = trace_data.get("traces") if isinstance(trace_data, dict) else []
    if not isinstance(traces, list):
        return events
    for trace in traces:
        if not isinstance(trace, dict):
            continue
        trace_id = str(trace.get("trace_id", ""))
        role = str(trace.get("member_role", trace.get("role", "")))
        messages = trace.get("messages", [])
        if not isinstance(messages, list):
            continue
        for message in messages:
            if not isinstance(message, dict):
                continue
            message_index = message.get("message_index", "")
            content = str(message.get("content") or "").strip()
            if content:
                events.append(
                    {
                        "trace_id": trace_id,
                        "role": role,
                        "message_index": message_index,
                        "step_pointer": str(message.get("step_pointer", "")),
                        "tool": "",
                        "input": "",
                        "output": _one_line(content, 500),
                        "error": "",
                    }
                )
            tool_calls = message.get("tool_calls", [])
            if not isinstance(tool_calls, list):
                continue
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                raw_output = str(call.get("output") or "")
                raw_error = str(call.get("error") or "")
                events.append(
                    {
                        "trace_id": trace_id,
                        "role": role,
                        "message_index": message_index,
                        "step_pointer": str(call.get("step_pointer", "")),
                        "tool": str(call.get("name", "")),
                        "input": _one_line(call.get("input", ""), 300),
                        "output": _one_line(raw_output, 300),
                        "output_tail": _one_line(raw_output[-300:], 300),
                        "error": _one_line(raw_error, 500),
                        "validation_result": _validation_result_signal(
                            raw_output,
                            raw_error,
                        ),
                    }
                )
    return events


def _validation_result_signal(output: str, error: str) -> str:
    """Preserve test-result evidence before display excerpts truncate the tail."""
    if error.strip():
        return "failed"
    lowered = output.lower()
    failed_counts = [int(match) for match in re.findall(r"\b(\d+)\s+failed\b", lowered)]
    if any(count > 0 for count in failed_counts):
        return "failed"
    passed_counts = [int(match) for match in re.findall(r"\b(\d+)\s+passed\b", lowered)]
    if any(count > 0 for count in passed_counts):
        return "passed"
    return "unknown"


def _format_trace_event(event: dict[str, Any]) -> str:
    status = "err" if event.get("error") else "ok"
    parts = [
        f"- [{status}] trace_id={event.get('trace_id', '')}",
        f"role={event.get('role', '')}",
        f"message_index={event.get('message_index', '')}",
        f"step={event.get('step_pointer', '')}",
        f"tool={event.get('tool', '')}",
    ]
    if event.get("input"):
        parts.append(f"input={event['input']}")
    if event.get("error"):
        parts.append(f"error={event['error']}")
    elif event.get("output"):
        parts.append(f"output={event['output']}")
    return " ".join(parts)


def _one_line(value: Any, limit: int) -> str:
    return " ".join(_truncate_text(value, limit).split())


def _read_text_if_exists(path: Path, limit: int) -> str:
    if not path.is_file():
        return ""
    try:
        return _truncate_text(path.read_text(encoding="utf-8", errors="replace"), limit)
    except OSError:
        return ""


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _safe_path_segment(value: str) -> str:
    """Return a conservative path segment for runtime workspace names."""
    cleaned = [ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value.strip()]
    return "".join(cleaned).strip("._")


def _remove_path(path: Path) -> None:
    """Remove a file or directory if it exists."""
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# JSON extraction helper (mirrors scoring.py parse_judge_output bracket scan)
# ---------------------------------------------------------------------------


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Decode the first JSON object, ignoring prose and braces in strings."""
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            return None
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(value, dict):
            return value
        index = start + 1
    return None


def _contains_incomplete_json_object(text: str) -> bool:
    """Return whether output contains a JSON object cut off at end of text."""
    for match in re.finditer(r'\{\s*"', str(text or "")):
        start = match.start()
        candidate = text[start:].strip()
        try:
            json.JSONDecoder().raw_decode(candidate)
            continue
        except json.JSONDecodeError:
            pass

        stack: list[str] = []
        in_string = False
        escaped = False
        invalid_closer = False
        for char in candidate:
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in "{[":
                stack.append(char)
            elif char in "}]":
                if not stack:
                    invalid_closer = True
                    break
                opening = stack.pop()
                if (opening, char) not in {("{", "}"), ("[", "]")}:
                    invalid_closer = True
                    break
        incomplete = bool(stack) or in_string or escaped
        if not invalid_closer and incomplete:
            return True
    return False


def _unusable_diagnosis_output_error(
    case_id: str,
    outputs: list[str],
) -> BaseException:
    """Classify exhausted malformed output without hiding permanent failures."""
    latest = next((value for value in reversed(outputs) if value), "")
    excerpt = _truncate_text(latest, 256)
    if _contains_model_service_error_text(latest):
        return ValueError(f"per-case diagnosis output contained a model-service error for {case_id}: {excerpt}")
    if _extract_json_object(latest) is not None:
        return _DiagnosisContentError(
            f"per-case diagnosis output contained JSON but no usable diagnosis entries for {case_id}: {excerpt}"
        )
    if any(_contains_incomplete_json_object(value) for value in outputs):
        return RetryableModelOutputError(
            f"per-case diagnosis output remained incomplete JSON after repair for {case_id}: {excerpt}"
        )
    return _DiagnosisOutputFormatError(f"per-case diagnosis output did not contain JSON for {case_id}: {excerpt}")


class _DiagnosisOutputFormatError(ValueError):
    """Raised after bounded JSON repair still returns ordinary prose."""


def _contains_model_service_error_text(raw: str) -> bool:
    """Keep permanent service/auth failures distinct from bad model formatting."""
    normalized = " ".join(str(raw or "").lower().split())
    service_error_markers = (
        "error code:",
        "invalid_api_key",
        "authentication failed",
        "authentication error",
        "unauthorized",
        "budget_exceeded",
        "budget has been exceeded",
    )
    return any(marker in normalized for marker in service_error_markers)


# ---------------------------------------------------------------------------
# DiagnosisAgentStrategy
# ---------------------------------------------------------------------------


class DiagnosisAgentStrategy:
    """Full-pipeline DeepAgent strategy: owns reading, extraction, and diagnosis.

    Receives only the raw ``invocation`` in ``analyze``, matching the
    ``EvaluationResultAnalysisStrategy`` Protocol contract.  All data
    preparation (experience retrieval, CaseReader, SignalExtractor) runs
    inside this class so that alternative implementations are not forced to
    accept internal pipeline types.

    Per-case DeepAgents use isolated evidence and evaluated Harness declarations.
    Raw case directories stay outside the agent workspace. Issue compilation
    is deterministic and does not call a second model.
    """

    name: str = "diagnosis_agent"

    def __init__(
        self,
        config: EvaluationResultAnalyzerConfig,
    ) -> None:
        self._config = config
        self._case_reader = CaseReader()
        self._agent_runtime = DiagnosisAgentRuntime(config)

    async def analyze(
        self,
        invocation: EvaluationResultAnalysisInvocation,
    ) -> EvaluationResultAnalysisArtifact:
        """Run full analysis pipeline from raw invocation to structured issues.

        Steps:
        1. Retrieve optimization experience (zero-LLM).
        2. Read eval_ref, summary, and per-case inputs.
        3. Dispatch method-aware SignalExtractor (zero-LLM).
        4. Diagnose each case with its evaluated Harness and isolated evidence.
        5. Compile supported diagnoses into TeamIssues without another model.

        Args:
            invocation: Analyzer invocation with input paths and output directory.

        Returns:
            EvaluationResultAnalysisArtifact with issues and metadata.
        """
        retrieved_experience: dict[str, Any] = {}

        eval_ref = self._case_reader.read_eval_ref(invocation.eval_ref_path)
        summary = self._case_reader.read_summary(eval_ref.get("summary_path", ""))
        case_inputs = self._case_reader.read_case_inputs(invocation.case_results_dir)

        if not case_inputs:
            return EvaluationResultAnalysisArtifact(
                analysis_id=Path(invocation.output_dir).name,
                analysis_ref_path="",
                issues=[],
                metadata={
                    "analysis_status": "empty_case_results",
                    "model_config_ref": self._config.model_config_ref,
                    "retrieved_experience": retrieved_experience,
                },
            )

        eval_method = summary.evaluation_method or "default"
        extractor = build_signal_extractor(eval_method)
        signals = extractor.extract(summary, case_inputs)

        model_config_ref = self._config.diagnosis_agent_model_config_ref or self._config.model_config_ref
        if not model_config_ref:
            return self._partial_artifact(invocation, "model_config_ref must be set", retrieved_experience)

        output_dir = Path(invocation.output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        diagnosis_case_inputs = [
            case for case in case_inputs
            if not case.evaluation_passed or (case.evaluation_method != "llm_as_judge" and case.score < 1.0)
        ]
        per_case_results = await self._per_case_diagnosis(
            diagnosis_case_inputs,
            signals,
            retrieved_experience,
            source_stage=invocation.source_stage,
            prior_candidate_feedback=invocation.prior_candidate_feedback,
            eval_ref_path=invocation.eval_ref_path,
            harness_refs_path=invocation.harness_refs_path,
        )
        per_case_diagnoses_path = output_dir / "per_case_diagnoses.json"
        _write_json(per_case_diagnoses_path, {"per_case_diagnoses": per_case_results})
        diagnosis_failed_count = len(
            {str(item.get("case_id", "") or "") for item in per_case_results if item.get("analysis_failed")}
        )

        issues = await self._aggregate_diagnosis(
            per_case_results,
            summary,
            signals,
            retrieved_experience,
            output_dir=output_dir,
            source_stage=invocation.source_stage,
        )
        analysis_status = (
            "failed"
            if diagnosis_failed_count == len(diagnosis_case_inputs) and diagnosis_failed_count
            else "partial"
            if diagnosis_failed_count
            else "completed"
        )

        issues = [_apply_g5_mapping(issue) for issue in issues]
        issues = issues[: self._config.max_issues]

        return EvaluationResultAnalysisArtifact(
            analysis_id=Path(invocation.output_dir).name,
            analysis_ref_path="",
            issues=issues,
            metadata={
                "analysis_status": analysis_status,
                "strategy": self.name,
                "model_config_ref": self._config.diagnosis_agent_model_config_ref or self._config.model_config_ref,
                "signals_method": signals.method,
                "per_case_count": len(case_inputs),
                "diagnosed_case_count": len(diagnosis_case_inputs),
                "diagnosis_count": len(per_case_results),
                "diagnosis_failed_count": diagnosis_failed_count,
                "per_case_diagnoses_path": str(per_case_diagnoses_path),
                "retrieved_experience": retrieved_experience,
            },
        )

    async def _build_agent(self, workspace: str, *, system_prompt: str | None = None) -> "BaseAgent":
        """Delegate construction of a read-only DeepAgent to its runtime owner.

        Args:
            workspace: Directory used as the agent's working directory.
            system_prompt: Override system prompt.  Defaults to
                ``DIAGNOSIS_SYSTEM_PROMPT`` (per-case trace reader).
                Pass ``AGGREGATION_SYSTEM_PROMPT`` for the aggregation agent
                to prevent it from attempting to read trace files.
        """
        return await self._agent_runtime.build_agent(
            workspace,
            system_prompt=(system_prompt if system_prompt is not None else DIAGNOSIS_SYSTEM_PROMPT),
        )

    async def _per_case_diagnosis(
        self,
        case_inputs: list[CaseAnalysisInput],
        signals: DeterministicSignals,
        retrieved_experience: dict[str, Any] | None,
        *,
        source_stage: str = "",
        prior_candidate_feedback: dict[str, Any] | None = None,
        eval_ref_path: str = "",
        harness_refs_path: str = "",
    ) -> list[dict[str, Any]]:
        """Run diagnosis for each case in a stable deterministic order.

        Each case gets a temporary runtime workspace containing bounded
        diagnosis evidence and, when available, an isolated copy of the exact
        evaluated repository. The raw evaluator case directory is never exposed.
        """
        runtime_root = Path(tempfile.mkdtemp(prefix="ach_analyzer_"))

        async def _diagnose_one(case: CaseAnalysisInput) -> list[dict[str, Any]]:
            runtime_dir = _make_diagnosis_runtime_dir(runtime_root, case.case_id)
            try:
                evidence_summary_available = _prepare_diagnosis_evidence(
                    case=case,
                    runtime_dir=runtime_dir,
                )
                case_feedback = _case_prior_candidate_feedback(
                    prior_candidate_feedback,
                    case.case_id,
                )
                harness_context = (
                    prepare_harness_context(
                        eval_ref_path=eval_ref_path,
                        harness_refs_path=harness_refs_path,
                        runtime_dir=runtime_dir,
                    )
                    if eval_ref_path
                    else None
                )
                prompt = _build_diagnosis_prompt(
                    case=case,
                    signals=signals,
                    retrieved_experience=retrieved_experience,
                    evidence_summary_available=evidence_summary_available,
                    source_stage=source_stage,
                    prior_candidate_feedback=case_feedback,
                    harness_context=harness_context,
                )
                agent = await self._build_agent(str(runtime_dir))
                raw = await _run_agent(
                    agent,
                    prompt,
                    max_retries=self._config.diagnosis_agent_max_retries,
                )
                invalid_outputs = [raw]
                parsed = _extract_json_object(raw)
                diagnoses = (
                    _normalize_case_diagnoses(
                        parsed,
                        prior_candidate_feedback=case_feedback,
                    )
                    if parsed is not None
                    else []
                )
                if not diagnoses:
                    repair_raw = await _run_agent(
                        agent,
                        _build_json_repair_prompt(prompt, raw),
                        max_retries=0,
                    )
                    invalid_outputs.append(repair_raw)
                    repair_parsed = _extract_json_object(repair_raw)
                    if repair_parsed is not None:
                        repair_diagnoses = _normalize_case_diagnoses(
                            repair_parsed,
                            prior_candidate_feedback=case_feedback,
                        )
                        if repair_diagnoses:
                            raw = repair_raw
                            parsed = repair_parsed
                            diagnoses = repair_diagnoses
                if not diagnoses:
                    raise _unusable_diagnosis_output_error(
                        case.case_id,
                        invalid_outputs,
                    )
                validation_inventory = _build_validation_inventory(case)
                verifier_inventory = _build_verifier_inventory(case)
                validation_conflicts = _case_diagnoses_validation_conflicts(
                    diagnoses,
                    validation_inventory,
                    verifier_inventory,
                )
                if validation_conflicts:
                    repair_raw = await _run_agent(
                        agent,
                        _build_evidence_conflict_repair_prompt(
                            original_prompt=prompt,
                            previous_output=raw,
                            conflicts=validation_conflicts,
                            validation_inventory=validation_inventory,
                            verifier_inventory=verifier_inventory,
                        ),
                        max_retries=0,
                    )
                    repair_parsed = _extract_json_object(repair_raw)
                    if repair_parsed is not None:
                        repair_diagnoses = _normalize_case_diagnoses(
                            repair_parsed,
                            prior_candidate_feedback=case_feedback,
                        )
                        if repair_diagnoses:
                            repaired_conflicts = _case_diagnoses_validation_conflicts(
                                repair_diagnoses,
                                validation_inventory,
                                verifier_inventory,
                            )
                            if not repaired_conflicts:
                                raw = repair_raw
                                parsed = repair_parsed
                                diagnoses = repair_diagnoses
                                validation_conflicts = []
                            else:
                                validation_conflicts = repaired_conflicts
                        else:
                            validation_conflicts = [
                                *validation_conflicts,
                                "evidence-conflict repair output contained no diagnoses",
                            ]
                    else:
                        validation_conflicts = [
                            *validation_conflicts,
                            "evidence-conflict repair output did not contain JSON",
                        ]
                if validation_conflicts:
                    logger.warning(
                        "per-case diagnosis evidence conflict for %s after repair: %s",
                        case.case_id,
                        "; ".join(validation_conflicts),
                    )
                    return [
                        _diagnosis_evidence_conflict_result(
                            case,
                            validation_conflicts,
                        )
                    ]
                diagnosis_count = len(diagnoses)
                return [
                    {
                        "case_id": case.case_id,
                        "diagnosis_index": index,
                        "diagnosis_count": diagnosis_count,
                        "score": case.score,
                        "evaluation_passed": case.evaluation_passed,
                        "evaluation_reason": case.evaluation_reason,
                        **diagnosis,
                        "verifier_failure_output_excerpt": str(
                            verifier_inventory.get(
                                "verifier_failure_output_excerpt",
                                "",
                            )
                            or ""
                        ),
                    }
                    for index, diagnosis in enumerate(diagnoses, start=1)
                ]
            except Exception as exc:
                if isinstance(exc, (_DiagnosisOutputFormatError, _DiagnosisContentError, DiagnosisAgentExecutionError)):
                    logger.warning(
                        "per-case diagnosis unavailable for %s: %s",
                        case.case_id,
                        exc,
                    )
                    return [_diagnosis_unavailable_result(case, exc)]
                if is_retryable_model_call_failure(exc):
                    logger.warning(
                        "per-case diagnosis unavailable for %s: %s",
                        case.case_id,
                        exc,
                    )
                    return [_diagnosis_unavailable_result(case, exc)]
                logger.exception("per-case diagnosis failed for %s", case.case_id)
                raise
            finally:
                _remove_path(runtime_dir)

        try:
            results: list[dict[str, Any]] = []
            for case in case_inputs:
                results.extend(await _diagnose_one(case))
            return results
        finally:
            _remove_path(runtime_root)

    async def _aggregate_diagnosis(
        self,
        per_case_results: list[dict[str, Any]],
        summary: EvaluationSummaryInput,
        signals: DeterministicSignals,
        retrieved_experience: dict[str, Any] | None,
        *,
        output_dir: Path,
        source_stage: str = "",
    ) -> list[TeamIssue]:
        """Run single aggregation pass and parse the issues list.

        Per-case diagnosis already returns canonical attribution fields
        (target_ref, evidence_refs, recommendation).  Aggregation is therefore
        deterministic: group optimizable diagnoses by target_ref and keep
        unassigned/evaluator-pipeline gaps out of the optimizer loop.
        """
        return _aggregate_structured_diagnoses(
            per_case_results=per_case_results,
            max_issues=self._config.max_issues,
            evidence_limit_per_issue=self._config.evidence_limit_per_issue,
        )

    def _partial_artifact(
        self,
        invocation: EvaluationResultAnalysisInvocation,
        reason: str,
        retrieved_experience: dict[str, Any],
    ) -> EvaluationResultAnalysisArtifact:
        return EvaluationResultAnalysisArtifact(
            analysis_id=Path(invocation.output_dir).name,
            analysis_ref_path="",
            issues=[],
            metadata={
                "analysis_status": "partial",
                "strategy": self.name,
                "failure_reason": reason,
                "retrieved_experience": retrieved_experience,
            },
        )


# ---------------------------------------------------------------------------
# Agent runner helper
# ---------------------------------------------------------------------------


async def _run_agent(
    agent: "BaseAgent",
    prompt: str,
    *,
    max_retries: int,
) -> str:
    """Run the diagnosis DeepAgent and perform one bounded JSON repair."""
    model_call_retries = max(0, int(max_retries or 0))
    last_raw = await run_deep_agent_text(
        agent,
        prompt,
        operation_name="diagnosis agent",
        max_retries=model_call_retries,
    )
    if _extract_json_object(last_raw) is not None or model_call_retries == 0:
        return last_raw

    return await run_deep_agent_text(
        agent,
        _build_json_repair_prompt(prompt, last_raw),
        operation_name="diagnosis agent JSON repair",
        max_retries=model_call_retries,
    )


def _build_json_repair_prompt(original_prompt: str, previous_output: str) -> str:
    """Build a second-pass prompt that repairs format without changing evidence."""
    problem = (
        "Previous diagnosis output contained JSON but no usable diagnosis entries. "
        "Provide a diagnoses list of objects grounded in the current evidence; "
        "use target_ref=unassigned when the cause is unsupported."
        if _extract_json_object(previous_output) is not None
        else "Previous diagnosis output was not valid JSON."
    )
    return f"""{problem}

You must convert the diagnosis into the required single valid JSON object.
Do not include Markdown, prose, analysis notes, or text before/after the JSON.
Preserve the original task evidence and target_ref semantics from the original prompt.

Original diagnosis prompt:
{_truncate_text(original_prompt, 6000)}

Previous output:
{_truncate_text(previous_output, 2000)}

Return only the single valid JSON object required by the original prompt.
"""


def _build_evidence_conflict_repair_prompt(
    *,
    original_prompt: str,
    previous_output: str,
    conflicts: list[str],
    validation_inventory: dict[str, Any],
    verifier_inventory: dict[str, Any],
) -> str:
    """Ask the diagnosis agent to reconcile only deterministic contradictions."""
    repair_payload = {
        "deterministic_validation_inventory": validation_inventory,
        "deterministic_verifier_inventory": verifier_inventory,
        "validation_conflicts": conflicts,
    }
    return f"""The previous diagnosis was valid JSON but contradicted deterministic evidence.

Correct the causal diagnosis, not just its wording. Treat the inventories below as
immutable observations. Preserve supported evidence and change any unsupported
root cause, decision contract, target_ref, or recommendation. If the inventories
do not distinguish a mechanism, return target_ref="unassigned" with low confidence.
Return one valid JSON object only; do not include Markdown or prose.

Deterministic conflict payload:
{_bounded_json(repair_payload, 5000)}

Original diagnosis prompt:
{_truncate_text(original_prompt, 6000)}

Previous conflicting JSON:
{_truncate_text(previous_output, 4000)}
"""


def _dict_to_team_issue(data: dict[str, Any]) -> TeamIssue:
    """Convert a raw agent-output dict into a TeamIssue, enforcing category lock.

    Attribution is extracted from ``metadata.attribution`` (nested, preferred) or
    assembled from flat top-level fields (fallback for per-case compatible output).
    The result is always written to ``TeamIssue.metadata["attribution"]``.
    """
    raw_category = str(data.get("category", data.get("issue_category", "team_coordination")))
    category = raw_category if raw_category in {"member_harness", "team_coordination"} else "team_coordination"
    metadata = dict(data.get("metadata") or {})

    if "attribution" not in metadata:
        nested = (data.get("metadata") or {}).get("attribution")
        if nested and isinstance(nested, dict):
            metadata["attribution"] = nested
        else:
            flat_keys = {
                "root_cause",
                "critical_mistake",
                "general_mechanism",
                "decision_contract",
                "failure_cluster",
                "target_ref",
                "evidence_refs",
                "confidence",
            }
            flat = {k: data[k] for k in flat_keys if k in data}
            if flat:
                metadata["attribution"] = flat

    affected_components = _string_items(data.get("affected_components", []))
    if affected_components:
        metadata["affected_components"] = affected_components
    return TeamIssue(
        issue_id=str(data.get("issue_id", f"issue_{id(data)}")),
        category=category,
        severity=str(data.get("severity", "medium")),
        summary=str(data.get("summary", "")),
        affected_cases=list(data.get("affected_cases") or []),
        evidence=list(data.get("evidence") or []),
        suspected_team_scope=str(data.get("suspected_team_scope", "both")),
        target_members=_string_items(data.get("target_members", [])),
        recommendation=str(data.get("recommendation", "")),
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_analysis_strategy(
    config: EvaluationResultAnalyzerConfig,
) -> DiagnosisAgentStrategy:
    """Create a DiagnosisAgentStrategy from config and optional experience learner.

    The strategy defers DeepAgent construction to the first ``analyze`` call,
    so this function always succeeds even when ``model_config_ref`` is empty.

    Args:
        config: Analyzer configuration.

    Returns:
        DiagnosisAgentStrategy instance.
    """
    return DiagnosisAgentStrategy(config)


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------


class EvaluationResultAnalyzer:
    """Thin facade: creates output directory, delegates to strategy, writes artifacts."""

    def __init__(
        self,
        config: EvaluationResultAnalyzerConfig,
    ) -> None:
        self.config = config
        self._strategy = build_analysis_strategy(config)

    async def analyze(self, invocation: EvaluationResultAnalysisInvocation) -> str:
        """Analyze evaluation results and return the analysis artifact reference path.

        Args:
            invocation: Analyzer invocation with input paths and output directory.

        Returns:
            Path to the written ``analysis_ref.yaml`` file.
        """
        output_dir = Path(invocation.output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        issues_path = output_dir / self.config.output_filename
        analysis_ref_path = output_dir / "analysis_ref.yaml"

        artifact = await self._strategy.analyze(invocation)

        issues_dicts = [
            _backfill_issue_evidence_refs(
                asdict(_constrain_issue_to_invocation(issue, invocation)),
                invocation,
            )
            for issue in artifact.issues
        ]
        _write_yaml(issues_path, {"issues": issues_dicts})
        _write_yaml(
            analysis_ref_path,
            _build_analysis_ref_dict(
                output_dir=output_dir,
                invocation=invocation,
                issues_path=issues_path,
                issues_dicts=issues_dicts,
                artifact_metadata=artifact.metadata,
            ),
        )
        return str(analysis_ref_path)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _constrain_issue_to_invocation(
    issue: TeamIssue,
    invocation: EvaluationResultAnalysisInvocation,
) -> TeamIssue:
    """Keep Analyzer targets inside the optimization surfaces exposed by a run.

    A standalone Harness invocation deliberately has a Harness refs path and no
    Team Skill refs path. The model may still describe a cross-step behavior as
    ``team_skill``; persisting that value makes the member-only compiler silently
    discard an otherwise actionable issue. Preserve the diagnosis and remap only
    its ownership boundary to the available member Harness.
    """
    is_single_harness = bool(invocation.harness_refs_path) and not bool(invocation.team_skill_ref_path)
    target_ref = _issue_target_ref(issue)
    targets_team_skill = issue.optimization_target == "team_skill" or target_ref.startswith("team_skill.")
    if not is_single_harness or not targets_team_skill:
        return issue

    if target_ref.startswith("team_skill."):
        target_ref = f"member_harness.{target_ref.removeprefix('team_skill.')}"
    elif target_ref != "unassigned":
        explicit_members = _target_members_from_issue(issue)
        target_ref = f"member_harness.{explicit_members[0]}.prompt_section" if explicit_members else "unassigned"
    if target_ref == "unassigned":
        return replace(issue, optimization_target="", target_members=[])
    metadata = _with_attribution_target_ref(issue.metadata, target_ref)
    remapped = replace(
        issue,
        category="member_harness",
        suspected_team_scope="member",
        optimization_target="member_harness",
        metadata=metadata,
    )
    return replace(remapped, target_members=_target_members_from_issue(remapped))


def _build_analysis_ref_dict(
    *,
    output_dir: Path,
    invocation: EvaluationResultAnalysisInvocation,
    issues_path: Path,
    issues_dicts: list[dict[str, Any]],
    artifact_metadata: dict[str, Any],
) -> dict[str, Any]:
    # retrieved_experience is promoted to a top-level key for backward compat
    retrieved_experience = artifact_metadata.get("retrieved_experience", {})
    core_metadata = {k: v for k, v in artifact_metadata.items() if k != "retrieved_experience"}
    return {
        "analysis_id": output_dir.name,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "source_eval_ref_path": invocation.eval_ref_path,
        "case_results_dir": invocation.case_results_dir,
        "case_traces_dir": invocation.case_traces_dir,
        "team_skill_ref_path": invocation.team_skill_ref_path,
        "harness_refs_path": invocation.harness_refs_path,
        "issues_path": str(issues_path),
        "issues": issues_dicts,
        "retrieved_experience": retrieved_experience,
        "metadata": core_metadata,
    }


def _backfill_issue_evidence_refs(
    issue: dict[str, Any],
    invocation: EvaluationResultAnalysisInvocation,
) -> dict[str, Any]:
    """Attach concrete case artifact refs when model output omitted evidence_refs."""
    metadata = dict(issue.get("metadata") or {})
    attribution = dict(metadata.get("attribution") or {})
    existing_refs = attribution.get("evidence_refs")
    if isinstance(existing_refs, list) and existing_refs:
        return issue

    affected_cases = [str(case_id) for case_id in issue.get("affected_cases", []) if str(case_id).strip()]
    if not affected_cases:
        return issue

    case_index = _case_artifact_index(invocation.case_results_dir)
    refs = [case_index[case_id] for case_id in affected_cases if case_id in case_index]
    if not refs:
        return issue

    attribution["evidence_refs"] = refs[:3]
    metadata["attribution"] = attribution
    issue["metadata"] = metadata
    return issue


def _case_artifact_index(case_results_dir: str) -> dict[str, dict[str, str]]:
    root = Path(case_results_dir).expanduser().resolve()
    if not root.is_dir():
        return {}
    index: dict[str, dict[str, str]] = {}
    for result_path in sorted(root.glob("*/result.json")):
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Cannot read case evidence artifact: {result_path}") from exc
        case_id = str(result.get("case_id", "") or "").strip()
        if not case_id:
            continue
        case_dir = result_path.parent
        ref = {
            "case_id": case_id,
            "result_path": str(result_path.resolve()),
        }
        trace_path = case_dir / "trace.json"
        if trace_path.is_file():
            ref["trace_path"] = str(trace_path.resolve())
        normalized_trace_path = case_dir / "judge" / "normalized_trace.json"
        if normalized_trace_path.is_file():
            ref["normalized_trace_path"] = str(normalized_trace_path.resolve())
        index.setdefault(case_id, ref)
    return index


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


__all__ = [
    "DiagnosisAgentStrategy",
    "EvaluationResultAnalyzer",
    "build_analysis_strategy",
]


_COORDINATOR_ROLE_KEYS = {
    "coordinator",
    "lead",
    "leader",
    "team",
    "team_coordinator",
    "team_leader",
}


def is_team_coordinator_role(*values: str | None) -> bool:
    return any(
        (value or "").strip().lower().replace("-", "_").replace(" ", "_") in _COORDINATOR_ROLE_KEYS for value in values
    )


class _DiagnosisContentError(ValueError):
    """Parsed JSON contains no usable diagnosis after bounded content repair."""
