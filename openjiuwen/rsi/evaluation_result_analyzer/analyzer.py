# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Facade and strategy implementation for evaluation-result analysis.

Architecture:
  DiagnosisAgentStrategy.analyze(invocation)
      → experience_learner.retrieve
      → CaseReader (eval_ref / summary / case_inputs)
      → build_signal_extractor → SignalExtractor.extract
      → DeepAgent two-phase (per-case ‖ aggregation)
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
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from openjiuwen.agent_teams.schema.deep_agent_spec import TeamModelConfig
from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness import create_deep_agent
from openjiuwen.rsi.config import (
    EvaluationResultAnalyzerConfig,
)
from openjiuwen.rsi.evaluation_result_analyzer.case_reader import (
    CaseAnalysisInput,
    CaseReader,
    DeterministicSignals,
    EvaluationSummaryInput,
)
from openjiuwen.rsi.evaluation_result_analyzer.signal_extractor import (
    build_signal_extractor,
)
from openjiuwen.rsi.evaluator.runtime_adapters import RSISysOperationRail
from openjiuwen.rsi.member_optimizer.model_config import (
    load_model_config_ref,
    without_inner_sdk_retries,
)
from openjiuwen.rsi.model_call import (
    is_retryable_model_call_failure,
    run_model_call_with_retries,
)
from openjiuwen.rsi.schema import (
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


_SIGNAL_SNIPPET_CHARS = 2500
_EVIDENCE_SUMMARY_CHARS = 6000
_AGGREGATION_DIAGNOSIS_CHARS = 1200
_AGGREGATION_SIGNAL_CHARS = 4000
_RAW_OUTPUT_CHARS = 512


# ---------------------------------------------------------------------------
# Prompt constants (§4 of the design plan)
# ---------------------------------------------------------------------------

DIAGNOSIS_SYSTEM_PROMPT = """\
You are a reverse-attribution root-cause analyst for multi-agent AI team
evaluations. Your conclusion MUST point at a concrete, optimizable
variable backed by trace evidence. Vague summaries are rejected.

## Input
The user message contains the task contract, the current case's authoritative
benchmark test contract when available, case facts, and inline
`primary_evidence.evidence_summary_text`. The summary is extracted from
normalized trace and verifier outputs by code. The runtime may also contain:
- `evidence_summary.md`: an audit copy of the inline evidence;
- `repository/`: an isolated snapshot of the exact workspace evaluated for the
  case, including the solver's patch but excluding evaluator internals;
- `source_patch.diff`: the submitted patch, when available.
Cite trajectory evidence with structured `evidence_refs`: trace_id, role,
message_index, and optional step_pointer.

## Repository Investigation Protocol (strict)
1. Start with the authoritative task contract, benchmark test contract,
   verifier inventory, and inline evidence summary. They define the observed
   failure; repository inspection must explain it rather than replace it.
2. When `repository/` exists, inspect the relevant implementation, ownership or
   call chain, and public tests before assigning a member/team root cause. Use
   read-only file/search commands and bounded probes in the isolated snapshot.
   The snapshot is disposable, but do not edit it.
3. State at least two plausible mechanisms internally and run the smallest
   repository-grounded discriminator that separates them. Select a mechanism
   only when task text, source structure, or an observable probe rules out the
   alternative. A syntax pattern alone is not a causal discriminator.
4. If `repository/` is absent, or no available evidence separates the competing
   mechanisms, return target_ref="unassigned". Do not turn uncertainty into a
   reusable Skill recommendation.
5. Do NOT read case-root trace.json or result.json and do not explore paths
   outside the runtime workspace. Never inspect benchmark gold/solution patches
   or evaluator implementation. The supplied
   `authoritative_benchmark_test_contract.test_patch` is acceptance evidence,
   not a solution patch; use all of its assertions and branches when present.
6. Tool commands and probes created during diagnosis are hypothesis evidence,
   not additions to the original task contract.

## Experience Use Protocol (strict)
1. Current evaluation evidence is authoritative. Retrieved experience is only a
   hint for reusable failure patterns and component selection.
2. Do not copy a historical target_ref, component_layer, or recommendation
   unless current evidence independently supports it.
3. Use experience anti_patterns to avoid known bad repairs.
4. If retrieved experience conflicts with trace/verifier evidence, ignore the
   experience and cite current evidence.
5. Weak current evidence still means target_ref="unassigned"; history cannot
   raise confidence by itself.

## Reverse attribution method
1. Start from the failing outcome (low score / failed behavior / error).
   For LLM-as-judge cases, high/medium `judge_breakdown.quality_gaps`
   are primary failure anchors: use their affected_roles, likely_surfaces,
   missing_capability, and evidence fields to identify the optimizable
   member/team variable, then use trace evidence to support that attribution.
2. Walk BACKWARD to the EARLIEST decisive turn that caused it (first failed tool
   call, malformed handoff, wrong decision the later failures depend on).
3. Apply Target Reference Semantics to decide scope, role, and exactly one
   optimizable variable. Weak/ambiguous evidence => target_ref="unassigned",
   confidence=low. Never fabricate evidence/variables.
4. Falsify the proposed mechanism before naming it as root cause. Repository
   investigation is the mechanism-selection step, not optional corroboration.
   Do not infer semantic intent from `getattr`, `and`, `or`, fallback, exception,
   or protocol syntax without tracing the value owner/caller and observing the
   competing behavior. When a local
   probe or repository test passes but the authoritative verifier still fails,
   the diagnosis must explain that contradiction and predict the verifier's
   failed observable. Distinguish suppressing the reported exception from
   preserving the required value, owner, propagation, ordering, or lifecycle
   semantics. Consider at least two competing explanations and select one only
   when the supplied evidence separates them.
   `deterministic_validation_inventory` is code-derived. When it says a project
   test suite passed, you MUST NOT claim that the agent skipped project tests or
   ran only a self-authored smoke probe. Instead, explain why the locally passing
   suite did not exercise the verifier's semantic contract.
   `deterministic_verifier_inventory` is also code-derived. When it says
   `patch_successfully_applied=true`, you MUST NOT claim that workspace dirt,
   untracked files, or mode changes prevented patch application. Treat failed
   FAIL_TO_PASS tests as the primary unresolved semantic contract.
   `prior_candidate_feedback` is a paired Source-versus-Candidate experiment
   for this same case. It is stronger than a repeated explanation of the
   Source failure: preserve every FAIL_TO_PASS operation that the candidate
   newly made pass, diagnose the operations that still fail, and use the
   candidate patch/diagnosis to falsify the previous mechanism. Never restart
   from the original failure as if the candidate had not run. A candidate that
   moves some official tests from failure to success is partial semantic
   progress even though the case score remains zero.
   When it says `empty_patch=true`, treat that only as the terminal execution
   outcome, never as the semantic root cause or causal discriminator. Walk back
   through the trace and make exactly one of two determinations: (a) if a
   concrete edit site and change were already justified, the failure is a
   post-diagnosis runtime action transition outside Prompt/Skill optimization,
   so use target_ref="unassigned"; (b) otherwise identify the earliest wrong
   investigation decision that prevented the solver from discovering a
   justified edit. Do not recommend generic "produce a patch", "stop
   investigating", or "make a persistent edit" instructions.
   A failed test identifier names the contract surface, not its complete
   semantics. Use `verifier_failure_output_excerpt`, when present, as the
   authoritative observable for assertion, exception, ordering, and lifecycle
   details. When that excerpt is absent and task/repository evidence does not
   separate competing semantics, do not invent the missing boundary or call a
   generic "run more tests" procedure a causal Skill. Use target_ref="unassigned"
   or recommend a bounded hypothesis test instead.
   When failed checks form a state or option matrix, reconstruct every observed
   combination and its expected observable. Listing the check names or proving
   one representative combination is not a complete contract; preserve the
   combinations that protect behavior while another option changes.
   When an attribute/config lookup fails on an intermediate container, wrapper,
   or parent object, do not assume that falling back to a local/default value is
   semantically correct. Trace the existing parent/root ownership path first.
   The recommendation must distinguish inherited/root-owner propagation from a
   local default with a positive override case and a boundary default case. If
   repository evidence does not rule out an upstream owner, use
   target_ref="unassigned" rather than prescribing a fallback.
   For iterator or other stateful protocol failures, reconstruct the lifecycle
   before proposing a reusable Skill: behavior before initialization, the
   initialization transition, repeated successful operations, exhaustion or
   terminal behavior, and reset/reuse when the task or verifier supplies it.
   A broad protocol-family label such as "make it iterable" is not a causal
   discriminator when the authoritative failed operation is direct `next`, a
   specific exception, or a state transition. The recommendation must preserve
   the exact failed operation and boundary observable; do not offer a later
   branch that makes that operation optional.
   For lookups, unpacking, fallback chains, and other candidate-scanning
   algorithms, distinguish an element-local failure from terminal search
   failure. A failed candidate may need to be skipped so a later valid candidate
   can succeed. Do not translate the first internal exception into the public
   exception unless supplied evidence proves that the search is terminal. The
   positive case must include a later valid candidate after an earlier failed
   one, and the boundary case must cover exhaustion of all candidates.
   For file creation/replacement failures, distinguish character encoding from
   transactional safety. A routine named or tested as safe replacement must
   write separately and replace the destination only after a successful write;
   an encoding or write failure must leave an existing destination unchanged.
   Do not prescribe a global encoding merely because it suppresses the first
   exception unless the task contract establishes that encoding.
   Record the resulting decision change in `decision_contract`. This is the
   semantic handoff to optimization: name the wrong decision, the distinction
   that falsifies it, the action selected by current evidence, the observable
   that proves the action, and boundaries that must not be generalized away.
   Express this contract as runtime-observable behavior rather than evaluator
   provenance: keep case IDs, test IDs, FAIL_TO_PASS/PASS_TO_PASS labels, trace
   pointers, and optimizer rationale in the existing evidence/observation fields.
   Public API names and concrete behavioral values may remain when they are part
   of the semantic distinction the runtime method must teach.
   Do not put competing alternatives in `required_action`; unresolved choices
   mean the diagnosis is not yet ready to become a reusable runtime method.
   Also record `activation_phase`: the earliest point at which the required
   action is knowable (`task_start`, `during_investigation`, `post_diagnosis`,
   or `pre_submission`). Do not label a post-diagnosis transition as a
   task-start Skill trigger.
5. Evidence-pipeline failures are NOT member/team defects. If the decisive
   error is missing/failed-to-read `trajectory_events.jsonl`,
   `normalized_trace.json`, or `evidence_summary.md`, return
   target_ref="unassigned", confidence=low and recommend fixing evaluator /
   analyzer evidence plumbing outside the optimization loop.
6. Judge `verification_gap` entries describe missing evaluator confidence, not
   a demonstrated member/team behavior defect. They are runtime/evidence
   blockers owned outside this optimizer and MUST NOT be attributed to a
   member harness tool. Diagnose only retained `artifact_quality_gap` entries
   and independently failed/low-scored behaviors.

## Epistemic boundary (hard)
- `authoritative_task_contract.input_excerpt` is the only supplied record of
  what the user/benchmark originally asked and reproduced.
  `authoritative_benchmark_test_contract` is the authoritative acceptance-test
  contract when present, but it is not an implementation solution. Tool
  commands, scripts, examples, and probes in Key Events were authored by the
  evaluated agent; they are execution evidence, not task-contract facts. Never
  claim the original reproduction contained an input, assertion, or variant
  merely because an agent-generated command tested it.
- Treat words such as "may", "suggests", and "likely" in upstream diagnosis
  as hypotheses, not facts. Do not strengthen them during aggregation.
- Do not claim that sibling locations share a defect unless trace or verifier
  evidence identifies those locations or demonstrates the shared mechanism.
- A passing no-exception smoke probe proves only that the exception disappeared;
  it does not prove preservation of inherited/default/root-owner semantics.
- If the evidence cannot distinguish the semantic mechanism, use
  target_ref="unassigned" with low confidence or recommend a hypothesis-testing
  procedure. Never invent a reusable causal discriminator.

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

Per-case schema:
{
  "issue_category": "member_harness | team_skill | unassigned",
  "severity": "high | medium | low",
  "summary": "<one sentence: the concrete root cause>",
  "failure_mode": "<short label, e.g. repeated_failed_tool_call>",
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
    "wrong_decision": "<the decision that produced the failed observable>",
    "causal_distinction": "<the evidence-backed distinction that changes the decision>",
    "required_action": "<the action selected by that distinction, not a menu of alternatives>",
    "acceptance_observable": "<runtime behavior that demonstrates the action is correct>",
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
    "patch_successfully_applied": true,
    "failed_fail_to_pass_tests": ["<authoritative failed test id>"],
    "failed_pass_to_pass_tests": ["<authoritative regression test id>"]
  },
  "confidence": "high | medium | low"
}

Aggregation schema:
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
          "root_cause": "<fundamental reason; cite evidence_refs>",
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
          "target_ref": "<scope.variable or unassigned>",
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

## Anti-vagueness rules (hard)
- issue_category MUST equal the first segment of target_ref
  (member_harness or team_skill). If target_ref is "unassigned",
  issue_category MUST be "unassigned".
- root_cause / critical_mistake MUST be supported by >=1 evidence_refs entry;
  no evidence_refs => confidence cannot exceed low.
- general_mechanism MUST be task-agnostic (a reusable rule).
- decision_contract MUST preserve one directional decision change. Its
  required_action cannot be made optional by a later alternative under the same
  trigger. If evidence does not select an action, use target_ref="unassigned".
- activation_phase MUST name the earliest runtime phase where the evidence
  needed for required_action exists; post-diagnosis actions are not optional
  task-start methods.
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

Diagnose the root cause and return the JSON object from the system prompt.
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

    return PER_CASE_DIAGNOSIS_TEMPLATE.format(
        stage_instruction=_stage_instruction(source_stage),
        evidence_instruction=evidence_instruction,
        diagnosis_input=_build_diagnosis_input_json(
            case=case,
            signals=signals,
            retrieved_experience=retrieved_experience,
            evidence_summary_available=evidence_summary_available,
            source_stage=source_stage,
            prior_candidate_feedback=prior_candidate_feedback,
        ),
    )


def _build_diagnosis_input_json(
    *,
    case: CaseAnalysisInput,
    signals: DeterministicSignals,
    retrieved_experience: dict[str, Any] | None,
    evidence_summary_available: bool,
    source_stage: str = "",
    prior_candidate_feedback: dict[str, Any] | None = None,
) -> str:
    """Build bounded inline JSON for a per-case diagnosis prompt."""
    judge_breakdown = _summarize_evaluation_metadata(case.evaluation_metadata)
    evidence_summary_text = (
        _truncate_text(_build_evidence_summary(case), _EVIDENCE_SUMMARY_CHARS) if evidence_summary_available else ""
    )
    validation_inventory = _build_validation_inventory(case)
    verifier_inventory = _build_verifier_inventory(case)
    payload: dict[str, Any] = {
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
            "evidence. Preserve newly passing operations and diagnose only the "
            "remaining failures; candidate diagnoses are hypotheses unless the "
            "verifier delta independently supports them."
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
            "patch. Do not re-diagnose the original Source in isolation. Attribute "
            "only a reusable harness defect; use unassigned for a post-diagnosis "
            "runtime no-action transition."
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

    if not any((behaviors, overall_reason, forbidden_hits, quality_gaps)):
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
    if "quality_gap_score_ceiling" in parsed:
        result["quality_gap_score_ceiling"] = parsed.get("quality_gap_score_ceiling")
    if "overall_score" in parsed:
        result["overall_score"] = parsed.get("overall_score")
    return result


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
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in per_case_results:
        if item.get("analysis_failed"):
            continue
        target_ref = _normalize_target_ref(item.get("target_ref", ""))
        if not target_ref or target_ref == "unassigned":
            continue
        issue_category = str(item.get("issue_category", item.get("category", "")) or "")
        failure_mode = str(item.get("failure_mode", "") or "")
        groups.setdefault((target_ref, failure_mode), []).append(item)

    ranked_groups = sorted(
        groups.items(),
        key=lambda entry: (
            -max(_severity_rank(item.get("severity")) for item in entry[1]),
            -max(_confidence_rank(item.get("confidence")) for item in entry[1]),
            entry[0][0],
            entry[0][1],
        ),
    )
    issues: list[TeamIssue] = []
    for index, ((target_ref, failure_mode), items) in enumerate(ranked_groups[: max(0, max_issues)], start=1):
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
                }
            )
        affected_components = list(
            dict.fromkeys(
                component for item in items for component in _string_items(item.get("affected_components", []))
            )
        )
        issue_category = _issue_category_from_target_ref(target_ref)
        affected_cases: list[str] = []
        for item in items:
            case_id = item.get("case_id")
            if case_id:
                affected_cases.append(str(case_id))
        issue = _dict_to_team_issue(
            {
                "issue_id": f"issue_{index:03d}",
                "category": "member_harness" if issue_category == "member_harness" else "team_coordination",
                "severity": str(strongest.get("severity", "medium") or "medium"),
                "summary": str(strongest.get("summary", "") or ""),
                "affected_cases": affected_cases,
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
                        "target_ref": target_ref,
                        "evidence_refs": list(strongest.get("evidence_refs") or []),
                        "confidence": str(strongest.get("confidence", "") or ""),
                    }
                },
            }
        )
        issues.append(_apply_g5_mapping(issue))
    return issues


def _diagnosis_unavailable_result(
    case: CaseAnalysisInput,
    exc: BaseException,
) -> dict[str, Any]:
    error = str(exc)
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
        "general_mechanism": "Retryable model-service failure during analyzer diagnosis.",
        "target_ref": "unassigned",
        "evidence_refs": [],
        "affected_components": [],
        "recommendation": "Do not optimize from this case-level diagnosis; rerun analysis or use other case diagnoses.",
        "confidence": "low",
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
    lowered_parts: list[str] = []
    for part in text_parts:
        lowered_parts.append(str(part).lower())
    text = "\n".join(lowered_parts)
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
        strings: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            stripped = item.strip()
            if stripped:
                strings.append(stripped)
        return strings
    return []


def _make_diagnosis_runtime_dir(runtime_root: Path, case_id: str) -> Path:
    """Create a unique case-scoped diagnosis runtime directory path."""
    safe_case_id = _safe_path_segment(case_id) or "case"
    return runtime_root / f"{safe_case_id}-{uuid.uuid4().hex}"


_DIAGNOSIS_COPY_IGNORES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
}
_DIAGNOSIS_ROOT_RUNTIME_DIRS = {
    "agents",
    "context",
    "memory",
    "messages",
    "todo",
}


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
    result = _load_case_result(case)
    workspace_value = result.get("workspace_dir")
    if not isinstance(workspace_value, str) or not workspace_value.strip():
        return False
    workspace_dir = Path(workspace_value).expanduser()
    if not workspace_dir.is_dir():
        return False

    repository_dir = runtime_dir / "repository"
    workspace_root = workspace_dir.resolve()

    def _ignore_runtime_noise(directory: str, names: list[str]) -> set[str]:
        ignored = {name for name in names if name in _DIAGNOSIS_COPY_IGNORES}
        try:
            is_root = Path(directory).resolve() == workspace_root
        except OSError:
            is_root = False
        if is_root:
            ignored.update(name for name in names if name in _DIAGNOSIS_ROOT_RUNTIME_DIRS)
        return ignored

    try:
        shutil.copytree(
            workspace_dir,
            repository_dir,
            ignore=_ignore_runtime_noise,
            symlinks=False,
        )
    except OSError as exc:
        logger.warning(
            "failed to prepare diagnosis repository snapshot for %s: %s",
            case.case_id,
            exc,
        )
        _remove_path(repository_dir)
        return False

    evaluation = result.get("evaluation")
    metadata = evaluation.get("metadata") if isinstance(evaluation, dict) else None
    patch_value = metadata.get("model_patch_path") if isinstance(metadata, dict) else None
    if isinstance(patch_value, str) and patch_value.strip():
        patch_path = Path(patch_value).expanduser()
        if patch_path.is_file():
            try:
                shutil.copy2(patch_path, runtime_dir / "source_patch.diff")
            except OSError as exc:
                logger.warning(
                    "failed to copy source patch for diagnosis case %s: %s",
                    case.case_id,
                    exc,
                )
    return True


def _prepare_diagnosis_evidence(*, case: CaseAnalysisInput, runtime_dir: Path) -> bool:
    """Write evidence and an isolated evaluated-repository snapshot."""
    _remove_path(runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    _prepare_repository_snapshot(case=case, runtime_dir=runtime_dir)
    summary = _build_evidence_summary(case)
    if not summary.strip():
        return False
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
        output_parts = (
            str(record.get("stdout_excerpt") or ""),
            str(record.get("stderr_excerpt") or ""),
        )
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
    decision_contract = diagnosis.get("decision_contract")
    decision_contract = decision_contract if isinstance(decision_contract, dict) else {}
    target_ref = str(diagnosis.get("target_ref") or "").strip()
    if verifier_inventory.get("empty_patch") is True:
        diagnosis_text = _joined_diagnosis_text(diagnosis)
        required_action = str(decision_contract.get("required_action") or "").lower()
        activation_phase = str(decision_contract.get("activation_phase") or "").strip().lower()
        if activation_phase in {"post_diagnosis", "pre_submission"} and target_ref != "unassigned":
            errors.append(
                "empty-patch post-diagnosis action transition must be "
                "target_ref=unassigned rather than reusable Prompt/Skill"
            )
        edit_was_already_justified = _contains_any_phrase(
            diagnosis_text,
            (
                "already identified",
                "already justified",
                "completed investigation",
                "completing diagnosis",
                "correctly diagnosed",
                "concrete edit site",
                "evidence needed to implement",
                "had already reproduced",
            ),
        )
        direct_edit_action = _contains_any_phrase(
            required_action,
            (
                "apply the edit",
                "implement the fix",
                "make a persistent edit",
                "modify the source",
                "produce a patch",
                "write a concrete edit",
                "write the edit",
            ),
        )
        if edit_was_already_justified and direct_edit_action:
            if activation_phase not in {"post_diagnosis", "pre_submission"}:
                errors.append(
                    "empty-patch diagnosis says the concrete edit was already "
                    "justified, so activation_phase must be post_diagnosis or "
                    "pre_submission"
                )
            if target_ref != "unassigned" and activation_phase not in {"post_diagnosis", "pre_submission"}:
                errors.append(
                    "empty-patch diagnosis attributes a post-diagnosis action "
                    "transition to a reusable harness instruction"
                )

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
                if verifier_observations.get(key) != value:
                    errors.append(f"verifier_observations.{key} must equal {value!r}")
        diagnosis_text = _joined_diagnosis_text(diagnosis)
        patch_failure_claims = (
            "patch application to fail",
            "patch application failed",
            "prevented patch application",
            "patch failed to apply",
            "could not apply the patch",
        )
        if any(claim in diagnosis_text for claim in patch_failure_claims):
            errors.append("diagnosis contradicts authoritative successful patch application")

    failed_fail_to_pass = []
    for item in verifier_inventory.get("failed_fail_to_pass_tests", []):
        failed_fail_to_pass.append(str(item).lower())
    verifier_output = str(verifier_inventory.get("verifier_failure_output_excerpt") or "").lower()
    protocol_text = _joined_diagnosis_text(diagnosis)
    if not errors and any("test_next" in test_name for test_name in failed_fail_to_pass):
        lifecycle_terms = (
            "state",
            "lifecycle",
            "initializ",
            "exhaust",
            "stopiteration",
            "transition",
        )
        if not any(term in protocol_text for term in ("__next__", "direct next", "next(")) or not any(
            term in protocol_text for term in lifecycle_terms
        ):
            errors.append(
                "test_next attribution must preserve the direct operation and its stateful iterator lifecycle"
            )
        if "attributeerror" in verifier_output or "not initialized" in verifier_output:
            pre_init_terms = (
                "attributeerror",
                "before iter",
                "before __iter__",
                "pre-init",
                "preinit",
                "uninitialized",
                "initializ",
            )
            if not any(term in protocol_text for term in pre_init_terms):
                errors.append("test_next attribution omits the verifier's pre-initialization boundary")

    safe_replace_tests = []
    for test_name in failed_fail_to_pass:
        if "safe" in test_name and "replace" in test_name:
            safe_replace_tests.append(test_name)
    if not errors and safe_replace_tests:
        scope_boundary = decision_contract.get("scope_boundary")
        if isinstance(scope_boundary, list):
            scope_boundary = " ".join(str(item) for item in scope_boundary)
        decision_values = (
            decision_contract.get("causal_distinction"),
            decision_contract.get("required_action"),
            decision_contract.get("acceptance_observable"),
            scope_boundary,
        )
        decision_text = " ".join(str(value or "").lower() for value in decision_values)
        transaction_terms = (
            "atomic",
            "existing file",
            "original file",
            "preserve the old",
            "preserve the original",
            "temporary file",
            "temp file",
            "replace only after",
            "unchanged on failure",
        )
        if not any(term in decision_text for term in transaction_terms):
            errors.append(
                "safe file-replacement attribution must preserve the transactional "
                "boundary: write separately and leave the existing file unchanged "
                "when encoding or writing fails"
            )
    return errors


def _joined_diagnosis_text(diagnosis: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "summary",
        "root_cause",
        "critical_mistake",
        "general_mechanism",
        "recommendation",
    ):
        parts.append(str(diagnosis.get(key) or "").lower())
    return " ".join(parts)


def _contains_any_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    for phrase in phrases:
        if phrase in text:
            return True
    return False


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
    """Scan for the outermost JSON object in raw agent output."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    payload_end = i + 1
                    return json.loads(text[start:payload_end])
                except json.JSONDecodeError:
                    return None
    return None


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

    Per-case diagnosis DeepAgents use temporary runtime workspaces containing
    only ``evidence_summary.md``.  Raw case directories stay outside the agent
    workspace.  Aggregation uses ``output_dir``.
    """

    name: str = "diagnosis_agent"

    def __init__(
        self,
        config: EvaluationResultAnalyzerConfig,
    ) -> None:
        self._config = config
        self._case_reader = CaseReader()

    async def analyze(
        self,
        invocation: EvaluationResultAnalysisInvocation,
    ) -> EvaluationResultAnalysisArtifact:
        """Run full analysis pipeline from raw invocation to structured issues.

        Steps:
        1. Retrieve optimization experience (zero-LLM).
        2. Read eval_ref, summary, and per-case inputs.
        3. Dispatch method-aware SignalExtractor (zero-LLM).
        4. Run DeepAgent per-case diagnosis concurrently (workspace = case_dir).
        5. Run DeepAgent aggregation to produce TeamIssues (workspace = output_dir).

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

        diagnosis_case_inputs = [case for case in case_inputs if not case.evaluation_passed]
        per_case_results = await self._per_case_diagnosis(
            diagnosis_case_inputs,
            signals,
            retrieved_experience,
            source_stage=invocation.source_stage,
            prior_candidate_feedback=invocation.prior_candidate_feedback,
        )
        per_case_diagnoses_path = output_dir / "per_case_diagnoses.json"
        _write_json(per_case_diagnoses_path, {"per_case_diagnoses": per_case_results})
        diagnosis_failed_count = sum(1 for item in per_case_results if item.get("analysis_failed"))

        issues = await self._aggregate_diagnosis(
            per_case_results,
            summary,
            signals,
            retrieved_experience,
            output_dir=output_dir,
            source_stage=invocation.source_stage,
        )
        analysis_status = "completed"

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
                "diagnosis_failed_count": diagnosis_failed_count,
                "per_case_diagnoses_path": str(per_case_diagnoses_path),
                "retrieved_experience": retrieved_experience,
            },
        )

    async def _build_agent(self, workspace: str, *, system_prompt: str | None = None) -> "BaseAgent":
        """Resolve model config ref and construct a read-only DeepAgent.

        Args:
            workspace: Directory used as the agent's working directory.
            system_prompt: Override system prompt.  Defaults to
                ``DIAGNOSIS_SYSTEM_PROMPT`` (per-case trace reader).
                Pass ``AGGREGATION_SYSTEM_PROMPT`` for the aggregation agent
                to prevent it from attempting to read trace files.
        """
        ref_path = self._config.diagnosis_agent_model_config_ref or self._config.model_config_ref
        if not ref_path:
            raise ValueError("model_config_ref must be set")

        ref_data = load_model_config_ref(ref_path)
        model_data = ref_data.get("model", ref_data)
        model_config = TeamModelConfig.model_validate(without_inner_sdk_retries(model_data))
        model = model_config.build()

        return create_deep_agent(
            model=model,
            card=AgentCard(name="diagnosis_agent", description="Evaluation result diagnosis agent"),
            system_prompt=system_prompt if system_prompt is not None else DIAGNOSIS_SYSTEM_PROMPT,
            workspace=workspace,
            restrict_to_work_dir=True,
            max_iterations=self._config.diagnosis_agent_max_iterations,
            auto_create_workspace=False,
            rails=[RSISysOperationRail(read_only=True, bash_pipefail=True)],
        )

    async def _per_case_diagnosis(
        self,
        case_inputs: list[CaseAnalysisInput],
        signals: DeterministicSignals,
        retrieved_experience: dict[str, Any] | None,
        *,
        source_stage: str = "",
        prior_candidate_feedback: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Run diagnosis for each case in a stable deterministic order.

        Each case gets a temporary runtime workspace containing bounded
        diagnosis evidence and, when available, an isolated copy of the exact
        evaluated repository. The raw evaluator case directory is never exposed.
        """
        runtime_root = Path(tempfile.mkdtemp(prefix="ach_analyzer_"))

        async def _diagnose_one(case: CaseAnalysisInput) -> dict[str, Any]:
            runtime_dir = _make_diagnosis_runtime_dir(runtime_root, case.case_id)
            try:
                evidence_summary_available = _prepare_diagnosis_evidence(
                    case=case,
                    runtime_dir=runtime_dir,
                )
                prompt = _build_diagnosis_prompt(
                    case=case,
                    signals=signals,
                    retrieved_experience=retrieved_experience,
                    evidence_summary_available=evidence_summary_available,
                    source_stage=source_stage,
                    prior_candidate_feedback=_case_prior_candidate_feedback(
                        prior_candidate_feedback,
                        case.case_id,
                    ),
                )
                agent = await self._build_agent(str(runtime_dir))
                raw = await _run_agent(
                    agent,
                    prompt,
                    max_retries=self._config.diagnosis_agent_max_retries,
                )
                parsed = _extract_json_object(raw)
                if parsed is None:
                    repair_raw = await _run_agent(
                        agent,
                        _build_json_repair_prompt(prompt, raw),
                        max_retries=0,
                    )
                    repair_parsed = _extract_json_object(repair_raw)
                    if repair_parsed is not None:
                        raw = repair_raw
                        parsed = repair_parsed
                if parsed is None:
                    raise ValueError(f"per-case diagnosis output did not contain JSON for {case.case_id}: {raw[:256]}")
                validation_inventory = _build_validation_inventory(case)
                verifier_inventory = _build_verifier_inventory(case)
                validation_conflicts = _diagnosis_validation_conflicts(
                    parsed,
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
                        repaired_conflicts = _diagnosis_validation_conflicts(
                            repair_parsed,
                            validation_inventory,
                            verifier_inventory,
                        )
                        if not repaired_conflicts:
                            raw = repair_raw
                            parsed = repair_parsed
                            validation_conflicts = []
                        else:
                            validation_conflicts = repaired_conflicts
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
                    return _diagnosis_evidence_conflict_result(
                        case,
                        validation_conflicts,
                    )
                return {
                    "case_id": case.case_id,
                    "score": case.score,
                    "evaluation_passed": case.evaluation_passed,
                    "evaluation_reason": case.evaluation_reason,
                    **parsed,
                    "verifier_failure_output_excerpt": str(
                        verifier_inventory.get(
                            "verifier_failure_output_excerpt",
                            "",
                        )
                        or ""
                    ),
                }
            except Exception as exc:
                if is_retryable_model_call_failure(exc):
                    logger.warning(
                        "per-case diagnosis unavailable for %s: %s",
                        case.case_id,
                        exc,
                    )
                    return _diagnosis_unavailable_result(case, exc)
                logger.exception("per-case diagnosis failed for %s", case.case_id)
                raise
            finally:
                _remove_path(runtime_dir)

        try:
            results: list[dict[str, Any]] = []
            for case in case_inputs:
                results.append(await _diagnose_one(case))
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
    """Run the agent with retries and return its last text output."""
    from openjiuwen.core.runner import Runner

    session_id = f"diagnosis_{uuid.uuid4().hex}"
    current_prompt = prompt

    async def call_once() -> str:
        result = await Runner.run_agent(
            agent=agent,
            inputs={"query": current_prompt},
            session=session_id,
        )
        if isinstance(result, dict):
            fallback = json.dumps(result, ensure_ascii=False)
            return str(result.get("output", result.get("answer", fallback)))
        return str(result)

    last_raw = ""
    attempts = max(1, int(max_retries or 0) + 1)
    model_call_retries = 1 if attempts > 1 else 0
    for attempt in range(attempts):
        retry_after_error = False
        try:
            last_raw = await run_model_call_with_retries(
                call_once,
                operation_name="diagnosis agent",
                max_retries=model_call_retries,
            )
        except BaseException as exc:
            if attempt >= attempts - 1 or not is_retryable_model_call_failure(exc):
                raise
            retry_after_error = True
        if retry_after_error:
            continue
        if _extract_json_object(last_raw) is not None:
            return last_raw
        current_prompt = _build_json_repair_prompt(prompt, last_raw)
    return last_raw


def _build_json_repair_prompt(original_prompt: str, previous_output: str) -> str:
    """Build a second-pass prompt that repairs format without changing evidence."""
    return f"""Previous diagnosis output was not valid JSON.

You must convert the diagnosis into the required single valid JSON object.
Do not include Markdown, prose, analysis notes, or text before/after the JSON.
Preserve the original task evidence and target_ref semantics from the original prompt.

Original diagnosis prompt:
{_truncate_text(original_prompt, 6000)}

Previous invalid output:
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
    """Create a DiagnosisAgentStrategy from config.

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

        issues_dicts = [_backfill_issue_evidence_refs(asdict(issue), invocation) for issue in artifact.issues]
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
        "created_at": datetime.now(UTC).astimezone().isoformat(),
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
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("failed to read case result %s: %s", result_path, exc)
            result = None
        if not isinstance(result, dict):
            continue
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
