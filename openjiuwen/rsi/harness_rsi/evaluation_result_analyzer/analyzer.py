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

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import yaml

from openjiuwen.agent_teams.schema.deep_agent_spec import TeamModelConfig
from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness import create_deep_agent
from openjiuwen.rsi.harness_rsi.config import (
    EvaluationResultAnalyzerConfig,
)
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
    CaseAnalysisInput,
    CaseReader,
    DeterministicSignals,
    EvaluationSummaryInput,
)
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.evidence_compactor import (
    build_causal_evidence_digest,
    compact_candidate_feedback,
    extract_critical_evidence_spans,
    load_public_task_contract,
)
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.evidence_investigation import (
    causal_hypothesis_semantic_id,
    execute_causal_investigation,
    normalize_causal_investigation,
)
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.signal_extractor import (
    build_signal_extractor,
)
from openjiuwen.rsi.harness_rsi.member_optimizer.model_config import (
    load_model_config_ref,
    without_inner_sdk_retries,
)
from openjiuwen.rsi.harness_rsi.model_call import (
    RetryableModelOutputError,
    is_retryable_model_call_failure,
    run_model_call_with_retries,
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
_MAX_DIAGNOSES_PER_CASE = 6
_CAUSAL_INITIAL_REQUEST_LIMIT = 8
_CAUSAL_TOTAL_REQUEST_LIMIT = 20
_CAUSAL_CLOSURE_MAX_ROUNDS = 3
_TASK_VISIBLE_CAUSAL_OPERATIONS = {
    "check_relation",
    "compare_numeric_change",
    "inspect_artifact",
    "read_artifact_window",
    "read_event",
    "read_repository_file",
    "search_repository",
    "search_trace",
}
_EVIDENCE_SUPPLEMENT_MAX_EVENTS = 10
_EVIDENCE_SUPPLEMENT_EVENT_CHARS = 4_000
_EVIDENCE_SUPPLEMENT_RESPONSE_CHARS = 8_000
_HARNESS_PROMPT_CHARS = 12_000
_HARNESS_SKILL_CHARS = 3_000
_EVALUATOR_OUTCOME_DEPENDENCY_PATTERNS = (
    re.compile(
        r"\bexpected\s+(?:answer|answers|metric|metrics|outcome|outcomes|output|outputs|result|results|"
        r"value|values)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:gold|golden|reference)\s+(?:answer|answers|outcome|outcomes|output|outputs|result|results|"
        r"value|values)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\btarget\s+(?:answer|answers|outcome|outcomes|output|outputs|result|results|value|values)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:grader|judge|verifier)(?:'s)?\s+(?:answer|expectation|expectations|target|value|values)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bwhat\s+(?:the\s+)?(?:grader|judge|verifier)\s+expects\b", re.IGNORECASE),
    re.compile(r"(?:期望|预期|金标|参考|评分器目标)(?:答案|结果|输出|数值|值)", re.IGNORECASE),
)
_HARNESS_CONFIG_KEYS = {
    "name",
    "version",
    "engine",
    "engine_revision",
    "system_prompt",
    "prompt",
    "max_steps",
    "max_iterations",
    "rollout_wall_clock_seconds",
    "command_timeout_seconds",
    "tool_loop_compaction",
    "submission_checkpoint",
    "skills",
    "tools",
    "rails",
}
_SENSITIVE_CONFIG_KEY = re.compile(r"(?:api.?key|token|secret|password|credential)", re.IGNORECASE)
_ARTIFACT_SUFFIXES = {".csv", ".docx", ".html", ".json", ".md", ".pdf", ".pptx", ".txt", ".xlsx", ".xml"}


# ---------------------------------------------------------------------------
# Prompt constants (§4 of the design plan)
# ---------------------------------------------------------------------------

_LEGACY_DIAGNOSIS_SYSTEM_PROMPT = """\
You are a reverse-attribution root-cause analyst for multi-agent AI team
evaluations. Your conclusion MUST point at a concrete, optimizable
variable backed by trace evidence. Vague summaries are rejected.

## Input
The user message contains the task contract, the current case's authoritative
benchmark test contract when available, case facts, and a deterministic
`primary_evidence.causal_digest`. The digest preserves each trial, exact tool
requests, public tool schemas, final outputs, and cross-trial contrasts instead
of flattening the trajectory. `primary_evidence.evidence_summary_text` retains
verifier and validation evidence. The runtime may also contain:
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
   For repeated-trial evaluations, first compare successful and failed trials.
   Prefer the earliest behavior that covaries with the outcome over a behavior
   present in every trial. When every trial fails, compare terminal action
   variants and judge-dimension changes without inventing a successful pattern.
   A field observed only in a tool response is not a missing request field. It
   is actionable only when the public request schema or task contract declares
   it as an allowed or required request field.
3. Partition the failed outcome into at most three independent failure clusters.
   A cluster must be tied either to a different failed verifier/check group or
   to a different runtime-observable behavior. Do not split paraphrases of the
   same mistake into separate diagnoses. For each cluster, apply Target
   Reference Semantics to decide scope, role, and exactly one optimizable
   variable. Weak/ambiguous evidence => target_ref="unassigned",
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
   newly made pass, diagnose newly regressed checks first, then operations that
   still fail, and use the candidate patch/diagnosis to falsify the previous
   mechanism. Do not emit a diagnosis whose cluster contains only checks the
   candidate already fixed. Never restart from the original failure as if the
   candidate had not run. A candidate that moves some official tests from
   failure to success is partial semantic progress even though the case score
   remains zero.
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
  When it is non-empty, the public task contract is available: inspect it
  directly and do not claim it is unavailable or search the repository for a
  second copy. It may still be genuinely ambiguous; say that explicitly.
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
- skill: a reusable multi-step capability is not triggered, missing, misused,
  or procedurally flawed. Do not choose it merely because a literal local fix
  can be paraphrased in general language. The downstream planner independently
  requires cross-case support before it permits any Skill change; a one-case
  Skill hypothesis will run only as a bounded prompt experiment.
- tool: local atomic tool choice, args, schema, call format, implementation, or result handling is wrong.
- execution_budget: an observed step, wall-clock, or token limit stops otherwise
  relevant execution before the required action or verification. Name this only
  when the effective Harness exposes a writable budget and the trace reaches the
  limit; ordinary inefficiency is not budget evidence.
- rail: an already declared Harness lifecycle/control rail is misconfigured, such
  as repeated-action compaction or bailout behavior. Name this only when
  effective_harness exposes that rail and the trace demonstrates its mechanism.
- config: another package-local runtime/model/harness configuration is wrong.
  Do not use generic config for an execution-budget or Rail diagnosis.

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
  ]
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
- Return no more than 3 diagnoses. Every pair must have either different
  failed_checks or a materially different observable_behavior; paraphrases,
  downstream symptoms, and repeated recommendations are one diagnosis. Prefer
  fewer well-supported diagnoses over filling the limit.
- recommendation MUST name the target_ref variable and what to change.
- Prefer "unassigned" over guessing.
"""

GENERIC_ANALYZER_PROTOCOL_VERSION = "generic_behavior_causal_v19"

DIAGNOSIS_SYSTEM_PROMPT = """\
You are an evidence-grounded behavior analyst for an AI Harness improvement
loop. The evaluated task may involve code, documents, spreadsheets, search,
reasoning, tool use, or multi-agent work. Diagnose behavior from the supplied
contract and execution evidence; do not assume a benchmark or domain.

The controller uses two phases. When the user prompt begins with
`CAUSAL_INVESTIGATION_PHASE=plan`, do not diagnose or recommend a Harness
change. Return only the requested competing-hypothesis and evidence-request
JSON. When it begins with `CAUSAL_INVESTIGATION_PHASE=diagnose`, use the
controller-returned evidence to produce the final diagnosis JSON below.

Your output is consumed by an Improver. It must say what was required, what was
observed, what is known versus hypothesized, and which observable behavior
should change. A complete-sounding story is not evidence.

## Evidence order
Use sources in this order:
1. `authoritative_task_contract` and any explicit benchmark/verifier contract;
2. verifier results, criterion or dimension feedback, and artifact inspection;
3. `effective_harness`, which records the active Prompt, Skill, Tool, Rail, and
   execution-budget surfaces when available;
4. exact trajectory events and public tool schemas;
5. paired Source-versus-Candidate feedback;
6. repository/workspace inspection and bounded discriminating probes;
7. retrieved experience, only as a hypothesis hint.

Scores establish that an outcome failed; a score alone never establishes why.
Agent-authored commands, plans, and self-reports show attempted behavior, not
that the task contract required it or that it succeeded. Never inspect hidden
answers, evaluator implementation, or gold/solution patches. Do NOT read
case-root `trace.json` or `result.json`; use the inline causal digest,
`evidence_summary.md`, and the isolated `repository/` or artifact snapshot when
provided.

A sibling candidate that happens to pass does not prove its Prompt intervention
caused the pass. When paired feedback says behavior activation is unknown, not
instrumented, or unrelated to the changed outcome, treat the result as an
unattributed observation. Never convert "the evaluator expected answer X" into
a Harness rule. A causal correction must remain justified after removing the
score, expected label, and candidate outcome; otherwise use target_ref=unassigned.

When `authoritative_task_contract.input_excerpt` is non-empty, the public task contract is available.
Inspect that excerpt directly; do not claim it is unavailable or search the
repository for a duplicate. The supplied contract
may still be genuinely ambiguous, in which case preserve that uncertainty
rather than importing an unstated benchmark interpretation.

The causal digest may contain compact display excerpts. Any
`ANALYZER_EVIDENCE_COMPACTION` marker was inserted after task execution by the
Analyzer evidence pipeline. It was NOT visible to the task Agent and is never
evidence that a command, document, or tool result was truncated at runtime.
Use `response_evidence.critical_spans` for exact failed-requirement-linked text
and `raw_evidence_ref` for provenance. If an exact critical span conflicts with
an evaluator criterion, report the contradiction as `unassigned`; do not turn
the evidence-pipeline or benchmark inconsistency into a Prompt defect.

Before recommending a Prompt, Skill, Tool, Rail, or budget change, check
`effective_harness`. Do not add a rule that is already active. When the desired
rule is already present, distinguish failure to activate/follow it from missing
content. If the effective Harness is unavailable and target selection depends
on its contents, use `unassigned` rather than assuming a missing rule.

## Failed-requirement coverage (hard)
`deterministic_failed_requirement_inventory.items` is the authoritative list of
requirements that the available evaluator evidence says were unmet. Its IDs are
stable handles, not suggestions. First account for every inventory item; only
then search backward for causes. Do not select one salient or early runtime
error and silently drop other failed requirements.

Across the complete diagnosis set, partition all inventory IDs between
`causal_coverage.explained_requirement_ids` and
`causal_coverage.residual_requirement_ids`. Each diagnosis should list only the
IDs it causally explains or explicitly leaves unresolved; do not repeat an ID
as residual after another diagnosis has explained it. Across the diagnoses,
every inventory ID must also occur in one `failure_cluster.failed_checks`.
When evidence cannot explain a requirement, emit an `unassigned` diagnosis for
that requirement instead of omitting it.

The IDs in one diagnosis's `explained_requirement_ids` must be exactly the IDs
in that diagnosis's `failure_cluster.failed_checks`. Do not claim that a local
mechanism explains requirements outside its own failure cluster. An
insufficient/unassigned diagnosis explains no ID; its clustered IDs remain
residual until evidence establishes a cause.

An earlier event is a root cause only within an evidence-linked causal chain.
Timing alone does not make the earliest anomaly the cause of later independent
failures. Mark every causal-chain edge as `observed`, `supported`, or `unknown`.
Do not call a diagnosis `confirmed` when a material edge is unknown.

Distinguish a real local defect from a sufficient explanation of failure:
- `task_sufficient`: this mechanism explains every inventoried failure and no
  material observation remains unexplained;
- `cluster_sufficient`: it is sufficient for the named failure cluster, while
  other independent failed requirements remain;
- `local_contributor`: it caused an observed problem, but fixing it is not yet
  expected to satisfy even the whole named cluster;
- `unknown`: current evidence does not establish causal sufficiency.

Always state the counterfactual: if only this mechanism changed and everything
else stayed fixed, what next-run behavior or artifact value should change? A
plausible repair instruction is not a counterfactual prediction.

## Diagnosis procedure
For each independent failed requirement, perform these steps in order:

1. Name the failed requirement or check. If the evaluator exposes only an
   aggregate score, say that the exact failed requirement is unknown. When
   `judge_breakdown.criteria` is present, use its criterion IDs and rationales
   as the primary failure anchors; do not replace them with a more salient tool
   error unless evidence connects that error to the failed criterion.
2. Name the concrete observed behavior, including its trace pointer or artifact
   observation. Do not replace this with a capability label.
3. Build the shortest evidence-linked chain from a Harness-influenceable
   decision to the observed behavior and then to the failed requirement. Mark
   each edge observed, supported, or unknown. Do not skip an unknown edge with
   a fluent narrative.
4. Compare at least two plausible explanations. Use a repository-grounded,
   artifact-grounded, cross-trial, or paired-candidate discriminator when one is
   available. If none separates them, keep them unresolved.
5. Set `evidence_status`:
   - `confirmed`: direct evidence links the behavior decision to the failed
     requirement and separates material alternatives;
   - `supported_hypothesis`: the behavior gap is observed, but its causal effect
     or one material alternative remains unverified;
   - `insufficient`: only the outcome, score, or an ambiguous symptom is known.
6. Audit coverage and sufficiency. Partition every authoritative requirement ID
   into explained or residual, state unexplained observations, and make one
   falsifiable counterfactual prediction. A confirmed local mechanism is not a
   task-sufficient root cause when residual requirements remain.
7. Only after the behavior diagnosis, choose a target_ref. Attribution describes
   the actual failure surface; it must not be chosen merely because the current
   Improver happens to support that surface.
8. Produce one intervention contract: trigger, one behavior change, one runtime
   acceptance observable, and boundaries that prevent over-generalization.
9. Bind every assigned diagnosis to exactly one supported investigation
   hypothesis through `selected_hypothesis_id`. The root cause, decision
   contract, recommendation, and counterfactual must all implement that same
   hypothesis. Never splice the observed behavior from one hypothesis together
   with the corrective action from an unresolved alternative.
10. Separate evaluation evidence from deployable evidence. Verifier scores,
    expected values, gold/reference outcomes, and pass/fail deltas may reject a
    hypothesis or measure a candidate, but the next task Agent cannot use them
    to choose an action. An assigned intervention must be decidable from the
    public task contract, runtime trace, public repository, or task artifact.
    If removing evaluator-owned outcomes removes the decision rule, use
    target_ref="unassigned".

Use one concrete diagnosis per independent intervention. Do not compress
different decision points or different Harness surfaces into one root cause
merely because they contribute to the same final failed check. Return multiple
diagnoses only when their failed checks, causal decision points, or target
surfaces require independent interventions, up to the supplied per-Case limit.

## Paired candidate feedback
`prior_candidate_feedback` is an experiment, not another narrative:
- intended behavior did not occur: diagnose activation or routing;
- intended behavior occurred and the relevant metric improved: retain the
  intervention and diagnose only residual or regressed behavior;
- intended behavior occurred but the relevant metric did not improve: the
  intervention was not causally sufficient; do not repeat or paraphrase it;
- outcome is unchanged and activation is unknown: do not invent a new root
  cause. Preserve uncertainty and request the missing discriminator.

Do not equate the visible form of an intervention with its predicted behavior.
A table, ledger, citation, validation heading, or tool call proves only delivery.
Compare the pre-registered observable with every material decision-changing
claim in the candidate output. If the output still supports the same frozen
failure mechanism, predicted_behavior_occurred is `no` even when the requested
form is present; the causal hypothesis remains `not_tested`, not `falsified`.

A second diagnosis may replace the first only when new evidence distinguishes
the mechanisms. The same aggregate score by itself is not new causal evidence.

## Optional workspace investigation
When `repository/` or an evaluated artifact snapshot exists, inspect only the
relevant files with read-only operations and bounded probes. Repository
investigation is one possible discriminator, not a universal requirement.
Code tasks may use public tests and call chains. Document or spreadsheet tasks
may use preserved artifact structure, formulas, values, and formatting.
Search/reasoning tasks may use cited sources and final-answer evidence. If the
needed artifact or criterion detail is absent, record it as missing evidence
rather than guessing.

`authoritative_benchmark_test_contract.test_patch`, when supplied, is acceptance
evidence rather than a solution patch. When local validation passes but the
authoritative verifier fails, explain the uncovered observable instead of
claiming validation was skipped. A no-exception smoke probe proves only that an
exception disappeared, not that the required semantics were preserved.

## Requirement-classification audit
When the failed behavior is a conformance, review, eligibility, or contract
decision, do not treat every absent field, preferred practice, or possible
enhancement as a violated requirement. Reconstruct a requirement ledger before
selecting the cause:
- identify the authoritative requirement and classify it as unconditional,
  conditional, optional, or advisory;
- identify the owner of the required outcome: the evaluated target, another actor,
  a surrounding process, or an incorporated dependency. Do not infer that the target
  itself must contain a field or statement merely because the broader workflow must
  achieve the outcome;
- for a conditional requirement, establish that its trigger is true before
  treating the action as mandatory;
- distinguish a required outcome from one possible form, label, field, or
  implementation that could produce it;
- inspect task-visible cross-references and companion artifacts before calling
  a locally absent item missing; functional coverage or incorporation by
  reference may satisfy the requirement;
- map every alleged gap to the exact mandatory requirement it violates. A useful
  enhancement without that entailment cannot justify a negative classification.

Treat the reasons actually used to reach a conclusion as separate material
decision grounds. For each ground, independently record the authority, scope,
owner, conditional trigger, and claim-to-requirement entailment. Do not let a
valid ground lend authority to a different ground. When the trace directly shows
that the Agent used a material ground before this chain was established, the
narrow process hypothesis `unverified_decision_ground_used` may be supported even
if the correct final label and the validity of all other grounds remain unknown.
State only that one or more grounds were used without verification; never widen
this into "any gap" or "all grounds were wrong" unless evidence proves that.
Audit coverage from the released conclusion backward: enumerate every atomic
claim that changes a verdict, classification, recommended correction, or required
action, and verify that each has its own ground record. A ledger that covers broad
topics but omits one of those released claims is incomplete. For each negative
ground, test a minimal countermodel in which its cited evidence is true but its
claim is false because the owner, target, representation, dependency, or trigger
differs. If that countermodel remains compatible with task-visible evidence, the
entailment is not established.

The diagnosis and recommendation must preserve this derivation order. Do not select
a downstream inconsistency such as verdict wording when the trace first made an
unsupported authority, scope, ownership, trigger, or entailment decision. A proposed
repair must tell the task agent how to make that upstream decision from task-visible
evidence; a conditional rule that begins only after the agent has already classified
the requirement is not causally sufficient.

Compare two directions explicitly: false acceptance (a mandatory requirement is
unmet) and false rejection (optional, conditional, duplicated, or externally
owned content was promoted to mandatory). The earliest wrong decision is the
first unsupported requirement classification, not the later wording chosen to
make the conclusion internally consistent. Request a complete bounded window or
the next window when the decisive source is incomplete; absence from a search
excerpt is not evidence of absence from the source.

## Target reference semantics
Valid values are:
- `member_harness.<role>.prompt`: wrong or missing instruction, interpretation,
  decision rule, or verification behavior;
- `member_harness.<role>.skill`: a reusable multi-step method is missing,
  misrouted, or incorrectly executed;
- `member_harness.<role>.tool`: an atomic executable capability, public schema,
  arguments, implementation, or result handling is wrong;
- `member_harness.<role>.execution_budget`: the active step, wall-clock, or token
  limit is observed to stop otherwise relevant execution before the required
  action or verification;
- `member_harness.<role>.rail`: an already declared lifecycle/control Rail is
  observed to activate incorrectly or fail to activate;
- `member_harness.<role>.config`: another package-local model or runtime
  configuration is the evidenced cause; do not use this generic value for a
  budget or Rail diagnosis;
- `team_skill.<role>.role_coordination`, `constraint_violation`,
  `workflow_inefficiency`, or `capability_gap`: the decisive failure crosses a
  role boundary or belongs to team control;
- `unassigned`: evidence cannot yet identify one optimizable surface.

Never output role-less target_ref values. Do not convert a Tool, Skill, Config,
environment, evaluator, or evidence-pipeline defect into a Prompt defect just
because prompt editing is available. Missing evidence is not a Harness defect.
For `evidence_status="insufficient"`, target_ref must be `unassigned` and
confidence must be `low`.

## Concrete field meanings
- `summary`: failed requirement plus observed behavior, in one sentence.
- `root_cause`: the confirmed mechanism, or an explicit supported hypothesis;
  never a restatement of the score.
- `critical_mistake`: exact earliest action, omission, or decision visible in
  evidence.
- `general_mechanism`: one conditional rule that transfers beyond this case.
- `recommendation`: one change to the named target_ref, or the exact missing
  evidence/discriminating experiment when unassigned.
- `decision_contract`: the compact handoff to the Improver. Keep it concrete:
  wrong decision, evidence-backed distinction, required action, acceptance
  observable, scope boundaries, and earliest activation phase.

## Output
Return one valid JSON object and nothing else. Keep strings concise.
{
  "diagnoses": [
    {
      "issue_category": "member_harness | team_skill | unassigned",
      "severity": "high | medium | low",
      "summary": "<failed requirement plus observed behavior>",
      "failure_mode": "<short reusable label>",
      "failure_cluster": {
        "failed_checks": ["<exact ID from deterministic_failed_requirement_inventory>"],
        "observable_behavior": "<runtime or artifact observation>"
      },
      "evidence_status": "confirmed | supported_hypothesis | insufficient",
      "failed_requirement": "<known requirement, or explicitly unknown>",
      "competing_hypotheses": ["<plausible explanation A>", "<plausible explanation B>"],
      "discriminating_evidence": "<evidence that separates them, or what is missing>",
      "selected_hypothesis_id": "<one supported investigation hypothesis ID, or empty when unassigned>",
      "root_cause": "<confirmed mechanism or explicitly labeled hypothesis>",
      "critical_mistake": "<earliest observed wrong action or omission>",
      "general_mechanism": "<trigger -> reusable behavior rule>",
      "target_ref": "<member_harness.<role>.<variable> | team_skill.<role>.<variable> | unassigned>",
      "evidence_refs": [
        {"trace_id": "<id>", "role": "<role>", "message_index": 0, "step_pointer": "<optional>"}
      ],
      "affected_components": ["<role>"],
      "recommendation": "<one concrete modification or missing discriminator>",
      "decision_ground_audit": [
        {
          "ground_id": "g1",
          "ground_text": "<one observed reason used for the decision>",
          "materiality": "material | non_material | unknown",
          "used_for_decision": true,
          "authority_status": "verified | missing | contradicted | unknown",
          "scope_status": "matched | mismatched | unknown",
          "owner_status": "matched | mismatched | unknown",
          "trigger_status": "satisfied | not_satisfied | not_applicable | unknown",
          "entailment_status": "entailed | not_entailed | unknown",
          "controller_request_ids": ["<request IDs that expose the ground and its chain>"]
        }
      ],
      "causal_coverage": {
        "explained_requirement_ids": ["<inventory ID causally addressed by this diagnosis>"],
        "residual_requirement_ids": ["<inventory IDs this diagnosis explicitly leaves unresolved>"],
        "unexplained_observations": ["<material fact this mechanism does not explain>"],
        "causal_chain": [
          {
            "cause": "<decision or state>",
            "effect": "<next state or failed requirement>",
            "evidence_status": "observed | supported | unknown",
            "evidence_refs": []
          }
        ],
        "counterfactual_prediction": "<observable change if only this mechanism is fixed>",
        "sufficiency_status": "task_sufficient | cluster_sufficient | local_contributor | unknown"
      },
      "decision_contract": {
        "wrong_decision": "<observed decision>",
        "causal_distinction": "<when the decision must change>",
        "required_action": "<one selected action>",
        "acceptance_observable": "<runtime-visible proof>",
        "scope_boundary": ["<what must not be generalized>"],
        "activation_phase": "task_start | during_investigation | post_diagnosis | pre_submission"
      },
      "hypothesis_assessment": [
        {
          "hypothesis_id": "<investigation hypothesis ID>",
          "status": "supported | falsified | unresolved",
          "falsifying_condition_status": "observed | not_observed | unknown",
          "claim_follows_from_evidence": "yes | no | unknown",
          "evidence_relation": "direct_claim | direct_falsifier | correlated_output | self_consistency | unknown",
          "evidence_independence": "independent | direct_observation | same_mechanism | unknown",
          "logic_check": "<explicit arithmetic, ordering, identity, or entailment check>",
          "controller_request_ids": ["<request IDs actually used>"],
          "reason": "<comparison with controller evidence>",
          "evidence_refs": []
        }
      ],
      "prior_experiment_assessment": {
        "availability": "available | not_available",
        "intervention_activated": "yes | no | unknown",
        "predicted_behavior_occurred": "yes | no | unknown",
        "predicted_outcome_occurred": "yes | no | unknown",
        "causal_hypothesis_status": "supported | falsified | not_tested | inconclusive",
        "reason": "<experiment-grounded explanation>"
      },
      "validation_observations": {
        "project_test_suite_attempted": false,
        "project_test_suite_result": "not_observed | passed | failed",
        "authoritative_verifier_result": "passed | failed | unknown",
        "contradiction_explanation": "<explanation or empty>"
      },
      "verifier_observations": {
        "patch_successfully_applied": true,
        "failed_fail_to_pass_tests": [],
        "failed_pass_to_pass_tests": []
      },
      "confidence": "high | medium | low"
    }
  ]
}

Hard checks:
- issue_category must match target_ref scope; unassigned maps to unassigned.
- Assigned targets require evidence_refs and a concrete acceptance observable.
- Do not claim `confirmed` when material alternatives remain unresolved.
- Across the complete diagnosis set, explained and residual IDs must partition
  the failed-requirement inventory; no inventory item may disappear or occur in
  both sets.
- A diagnosis may explain only the failed checks in its own failure cluster;
  insufficient diagnoses explain none of their clustered checks.
- `task_sufficient` requires no residual requirement or unexplained observation.
- A confirmed diagnosis cannot contain an `unknown` causal-chain edge.
- Analyzer-generated compaction markers cannot support an observed causal edge
  or a claim about what the task Agent saw.
- Every assigned diagnosis must select exactly one supported investigation
  hypothesis. All causal handoff fields must follow from that selected claim.
- The selected action must be decidable without evaluator-owned expected, gold,
  reference, score, or pass/fail outcomes. Those signals validate outcomes only.
- The required action must be consistent with the authoritative public task
  contract. A scored mismatch cannot silently reverse an explicit instruction.
- Do not make required_action a menu or make it optional later.
- Prefer `unassigned` over an elegant but unsupported explanation.
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
8. Do not preserve member_harness.<role>.skill merely because a local fix sounds
   general. A Skill diagnosis must name a public or early-runtime trigger and a
   method that remains meaningful after removing case IDs, verifier checks,
   fixed expected counts, known answer values, and observed filenames. The
   downstream planner will still require independent cross-case support.

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
    effective_harness: dict[str, Any] | None = None,
) -> str:
    """Build the per-case diagnosis prompt for the DeepAgent.

    The prompt carries deterministic inputs inline.  The runtime may also hold
    an isolated ``repository/`` snapshot so the diagnosis agent can falsify
    semantic hypotheses against the evaluated code without touching it.
    """
    if evidence_summary_available:
        evidence_instruction = (
            "> Start from primary_evidence.causal_digest in the inline JSON.\n"
            "> It preserves public tool schemas, separate trial outcomes, exact selected "
            "actions, output delivery, and cross-trial contrasts. Use "
            "primary_evidence.evidence_summary_text for verifier and validation evidence.\n"
            "> Check effective_harness before claiming a rule or surface is missing, and use "
            "workspace_evidence to locate inspectable files without treating mtime as a change proof.\n"
            "> Use primary_evidence.evidence_summary_text from the inline JSON as the "
            "audit summary, not as a flattened substitute for causal_digest.\n"
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
        max_diagnoses_per_case=_MAX_DIAGNOSES_PER_CASE,
        diagnosis_input=_build_diagnosis_input_json(
            case=case,
            signals=signals,
            retrieved_experience=retrieved_experience,
            evidence_summary_available=evidence_summary_available,
            source_stage=source_stage,
            prior_candidate_feedback=prior_candidate_feedback,
            effective_harness=effective_harness,
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
    effective_harness: dict[str, Any] | None = None,
) -> str:
    """Build bounded inline JSON for a per-case diagnosis prompt."""
    judge_breakdown = _summarize_evaluation_metadata(case.evaluation_metadata)
    causal_digest = _build_causal_evidence_digest(case)
    evidence_summary_text = (
        _truncate_text(_build_evidence_summary(case), _EVIDENCE_SUMMARY_CHARS) if evidence_summary_available else ""
    )
    validation_inventory = _build_validation_inventory(case)
    verifier_inventory = _build_verifier_inventory(case)
    failed_requirement_inventory = _build_failed_requirement_inventory(
        case,
        judge_breakdown=judge_breakdown,
        verifier_inventory=verifier_inventory,
    )
    payload: dict[str, Any] = {
        "analysis_protocol": {
            "version": GENERIC_ANALYZER_PROTOCOL_VERSION,
            "objective": (
                "Separate observed failure facts from causal hypotheses, then "
                "handoff one falsifiable behavior intervention."
            ),
        },
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
        "effective_harness": effective_harness
        or {
            "availability": "not_provided",
            "policy": "Do not assume which prompt, Skill, Tool, Rail, or budget was active.",
        },
        "workspace_evidence": _build_workspace_evidence(case),
        "primary_evidence": {
            "evidence_summary_available": evidence_summary_available,
            "evidence_summary_path": "evidence_summary.md" if evidence_summary_available else "",
            "causal_digest": causal_digest,
            "evidence_summary_text": evidence_summary_text,
        },
        "deterministic_validation_inventory": validation_inventory,
        "deterministic_verifier_inventory": verifier_inventory,
        "deterministic_failed_requirement_inventory": failed_requirement_inventory,
        "prior_candidate_feedback": compact_candidate_feedback(prior_candidate_feedback),
        "prior_candidate_feedback_policy": (
            "Treat paired official test deltas as authoritative experiment "
            "evidence. Compare predicted behavior with activation and score delta. "
            "If the intended behavior did not activate, diagnose activation; if it "
            "activated without improvement, falsify the prior causal hypothesis. "
            "Preserve newly passing operations and diagnose regressions before "
            "remaining failures. Candidate diagnoses remain hypotheses unless the "
            "observed outcome independently supports them."
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
        "fallback_excerpts": {"input_excerpt": case.input},
        "retrieved_experience": _bounded_structured_value(
            _compact_retrieved_experience(retrieved_experience),
            _EXPERIENCE_SNIPPET_CHARS,
        ),
        "experience_usage_policy": _experience_usage_policy(),
    }
    # This payload is sent inline to the model. Pretty printing adds tens of
    # thousands of whitespace characters for multi-trial tool evidence without
    # adding information, so keep the audit artifact readable but compact the
    # wire representation.
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _build_causal_investigation_prompt(diagnosis_input: str) -> str:
    """Ask for discriminating evidence before permitting a causal diagnosis."""
    return f"""CAUSAL_INVESTIGATION_PHASE=plan

Do not diagnose a root cause and do not recommend a Harness change yet.
Build a bounded investigation that distinguishes competing explanations of every
item in deterministic_failed_requirement_inventory.

Return one valid JSON object only:
{{
  "causal_investigation": {{
    "hypotheses": [
      {{
        "hypothesis_id": "h1",
        "claim": "one concrete causal explanation",
        "explains_requirement_ids": ["an exact inventory ID"],
        "current_support": ["existing evidence pointer or observation"],
        "falsified_if": "an observable that would refute this explanation",
        "numeric_change_check_required": false,
        "evidence_requests": [
          {{
            "request_id": "q1",
            "operation": "search_trace | read_event | inspect_artifact | read_artifact_window | inspect_evaluation | search_repository | read_repository_file | compare_runs | check_relation | compare_numeric_change",
            "query": "terms that discriminate this hypothesis",
            "expression": "numeric expression for check_relation, using decimal literals",
            "operator": "approximately_equal | equal | not_equal | less_than | less_than_or_equal | greater_than | greater_than_or_equal",
            "expected": 0.0,
            "before_expression": "numeric baseline for compare_numeric_change",
            "after_expression": "numeric candidate for compare_numeric_change",
            "expected_delta": 0.0,
            "tolerance": 0.000000001,
            "trace_id": "required only for read_event when known",
            "message_index": 0,
            "tool_call_index": 0,
             "relative_path": "repository-relative path returned by search_repository",
             "proof_obligation": "existence | absence | coverage (inspect_artifact only)",
             "source_char_start": 0,
            "max_chars": 12000,
            "purpose": "how this result separates hypotheses"
          }}
        ]
      }}
    ],
    "ready_without_more_evidence": false
  }}
}}

Rules:
- Consider at least two materially different explanations for each failed
  requirement unless deterministic evidence already proves a source/evaluator
  contradiction.
- Request evidence that can refute a hypothesis, not merely repeat evidence that
  supports it.
- Form hypotheses at one controllable decision boundary. Do not combine an
  upstream production fault, a missing observation/validation step, and a
  missing recovery action into one explanation. When the final released output
  is observably invalid, consider the production mechanism, post-mutation
  validation of the exact released object, and recovery-after-detection as
  separate alternatives when the public evidence makes them plausible.
- A containment failure can be independently actionable even when the upstream
  physical cause remains unresolved. Test whether the exact final output was
  invalid after its last mutation, whether the Agent checked the relevant
  acceptance observable after that mutation, and whether it recovered before
  release. Do not require knowledge of the hidden scorer or the ultimate
  physical cause to test those task-visible decisions.
- An evidence request may discriminate several competing hypotheses. When that
  is already known, list every affected hypothesis in its `hypothesis_ids`;
  `hypothesis_ids` records why evidence was collected, not who owns the facts it
  reveals.
- Use search_trace to locate events, then read_event for an exact known event.
- Use inspect_artifact only for physically materialized task files. Use
  inspect_evaluation for judge/result metadata, and compare_runs for paired
  Source-versus-Candidate evidence. A metadata match never proves file content.
- inspect_artifact is a search primitive. Its matches include an exact physical
  source path, logical source name, character ranges, and window_complete. When
  the needed discriminator lies outside a returned search window, use
  read_artifact_window with that exact source path and a source_char_start from
  the returned range; do not repeat the same broad inspect_artifact query and do
  not call a truncated window complete evidence.
- When the trace already names the task artifact whose contents decide the
  hypothesis, put that task-visible logical name in inspect_artifact.relative_path.
  Do not search only by broad subject terms: a reference document that discusses
  the same topic is not a substitute for reading the assessed source artifact.
- For inspect_artifact, set proof_obligation=existence when one physical match
  proves the claim, absence when the claim requires proving that content is not
  in the bounded source, and coverage when the decision depends on the source as
  a whole. The controller stops at an existence witness, but deterministically
  reads absence/coverage sources from character zero to EOF within a strict
  source/window/character budget. Do not infer absence from a search excerpt.
- For code tasks, use search_repository to locate a bounded source or public-test
  span, then read_repository_file only with a repository-relative path returned by
  that search. The controller rejects absolute paths and traversal.
- If a hypothesis depends on a before-versus-after numeric or formula delta, it
  MUST set numeric_change_check_required=true and request
  compare_numeric_change; never compare the candidate expression directly with
  the requested delta. Set it false when the causal claim is about whether an
  action, write, invocation, branch, or artifact state occurred, even when the
  task context or downstream outcome contains numbers or percentages. For other
  arithmetic, counts, or orderings, use check_relation. Express percentages as
  decimals (1 percentage point is 0.01). Only numeric literals and +, -, *, /
  are allowed.
- Distinguish execution success and internal agreement from semantic numerical
  correctness. When a plausible hypothesis concerns convergence, approximation,
  stale state, or incomplete propagation, a final value produced by that same
  mechanism cannot falsify it merely because the run completed or related
  outputs agree. Request an independent recomputation, a bounded stability or
  perturbation comparison at the required output precision, or the public
  convergence/acceptance condition. If no such discriminator is controller-
  accessible, leave the hypothesis unresolved rather than treating self-
  consistency as a falsifier.
- Never request an absolute filesystem path, shell command, hidden test, gold
  answer, or unrestricted repository search. The controller owns all evidence
  access.
- State each hypothesis claim and falsified_if only in terms of a runtime
  mechanism and an independently observable prediction. Do not put evaluator
  expected/target values, gold/reference answers, scores, or pass/fail outcomes
  into either field. Those outcomes may appear in current_support only when
  prefixed `evaluation_only:`; they identify what failed but cannot decide which
  runtime behavior was correct.
- When a conclusion cites multiple reasons, decompose them into material decision
  grounds before proposing a broad explanation. Test authority, scope, owner,
  trigger, and claim-to-requirement entailment for each ground independently.
  Prefer the narrow observable hypothesis "at least one material decision ground
  was used before its requirement chain was verified" over a blanket claim such
  as "the Agent treats any gap as failure." Its falsifier is that every material
  ground used in the observed decision has a complete task-visible chain, or that
  the questioned ground did not contribute to the decision. Request a targeted
  trace span around the exact ground when a full event would bury that evidence.
- If prior_candidate_feedback shows an intervention experiment, request the
  evidence needed to distinguish non-activation from a false causal prediction.
- Every inventory ID must appear in at least two hypotheses so that each failed
  requirement has a real alternative, not merely a second unrelated story.
- A non-empty `authoritative_task_contract.input_excerpt` already is the public
  task contract. Do not spend an evidence request searching the repository for
  that contract or treat its absence from workspace files as missing evidence.
- Keep 2 to 4 materially distinct hypotheses and at most 8 first-pass evidence
  requests. One hypothesis may cover several requirements only when it states a
  shared mechanism. The controller reserves up to 4 requests for one immediate
  refinement.

DIAGNOSIS_INPUT:
{diagnosis_input}
"""


def _contains_evaluator_outcome_dependency(value: Any) -> bool:
    """Detect an evaluator-owned outcome being used as a causal decision rule."""
    if isinstance(value, dict):
        return any(_contains_evaluator_outcome_dependency(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_evaluator_outcome_dependency(item) for item in value)
    text = str(value or "")
    return any(pattern.search(text) for pattern in _EVALUATOR_OUTCOME_DEPENDENCY_PATTERNS)


def _causal_plan_outcome_dependency_conflicts(
    investigation: dict[str, Any] | None,
) -> list[str]:
    """Reject hypotheses whose semantics cannot be used by a future task Agent."""
    if not isinstance(investigation, dict):
        return []
    conflicts: list[str] = []
    for hypothesis in investigation.get("hypotheses", []):
        if not isinstance(hypothesis, dict):
            continue
        hypothesis_id = str(hypothesis.get("hypothesis_id", "") or "")
        for field in ("claim", "falsified_if"):
            if _contains_evaluator_outcome_dependency(hypothesis.get(field, "")):
                conflicts.append(f"hypothesis {hypothesis_id or '<unknown>'} uses evaluator-owned outcomes in {field}")
    return conflicts


def _raw_causal_plan(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the model-authored plan body before structural normalization."""
    if not isinstance(value, dict):
        return None
    raw = value.get("causal_investigation")
    if not isinstance(raw, dict):
        raw = value.get("investigation")
    return raw if isinstance(raw, dict) else value


def _task_visible_causal_request(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if str(value.get("operation", "") or "") not in _TASK_VISIBLE_CAUSAL_OPERATIONS:
        return False
    return not _contains_evaluator_outcome_dependency(value)


def _causal_plan_task_visibility_conflicts(value: dict[str, Any] | None) -> list[str]:
    """Describe hidden-evaluator dependencies outside claim/falsifier fields."""
    raw = _raw_causal_plan(value)
    if raw is None:
        return []
    conflicts: list[str] = []
    for hypothesis in raw.get("hypotheses", []):
        if not isinstance(hypothesis, dict):
            continue
        hypothesis_id = str(hypothesis.get("hypothesis_id", "") or "<unknown>")
        if _contains_evaluator_outcome_dependency(hypothesis.get("current_support", [])):
            conflicts.append(f"hypothesis {hypothesis_id} uses evaluator-owned outcomes in current_support")
        for request in hypothesis.get("evidence_requests", []):
            if isinstance(request, dict) and not _task_visible_causal_request(request):
                conflicts.append(
                    f"hypothesis {hypothesis_id} requests non-task-visible evidence via "
                    f"{str(request.get('operation', '') or '<unknown>')}"
                )
    for request in raw.get("evidence_requests", []):
        if isinstance(request, dict) and not _task_visible_causal_request(request):
            conflicts.append(
                "top-level causal request uses non-task-visible evidence via "
                f"{str(request.get('operation', '') or '<unknown>')}"
            )
    return list(dict.fromkeys(conflicts))


def _outcome_independent_causal_plan(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep valid plan siblings when another hypothesis leaks evaluator outcomes."""
    raw = _raw_causal_plan(value)
    if raw is None:
        return None
    hypotheses = raw.get("hypotheses")
    if not isinstance(hypotheses, list):
        return None

    kept: list[dict[str, Any]] = []
    for item in hypotheses:
        if (
            not isinstance(item, dict)
            or _contains_evaluator_outcome_dependency(item.get("claim", ""))
            or _contains_evaluator_outcome_dependency(item.get("falsified_if", ""))
        ):
            continue
        hypothesis = dict(item)
        # Model-authored support is never positive controller evidence. Dropping
        # it also prevents a clean claim from retaining a hidden judge label.
        hypothesis["current_support"] = []
        nested_requests = hypothesis.get("evidence_requests", [])
        hypothesis["evidence_requests"] = [
            dict(request) for request in nested_requests if _task_visible_causal_request(request)
        ]
        kept.append(hypothesis)
    kept_ids = {str(item.get("hypothesis_id", "") or "") for item in kept if str(item.get("hypothesis_id", "") or "")}
    top_level_requests: list[dict[str, Any]] = []
    for item in raw.get("evidence_requests", []):
        if not _task_visible_causal_request(item):
            continue
        request = dict(item)
        hypothesis_ids = _string_items(request.get("hypothesis_ids", []))
        if hypothesis_ids:
            request["hypothesis_ids"] = [item for item in hypothesis_ids if item in kept_ids]
            if not request["hypothesis_ids"]:
                continue
        top_level_requests.append(request)
    return {
        "causal_investigation": {
            "hypotheses": kept,
            "evidence_requests": top_level_requests,
            "ready_without_more_evidence": bool(raw.get("ready_without_more_evidence")),
        }
    }


def _merge_outcome_independent_causal_plans(
    seed: dict[str, Any] | None,
    addition: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Freeze retained clean siblings and append only distinct clean additions."""
    seed_plan = _outcome_independent_causal_plan(seed)
    addition_plan = _outcome_independent_causal_plan(addition)
    bodies = [body for body in (_raw_causal_plan(seed_plan), _raw_causal_plan(addition_plan)) if body]
    if not bodies:
        return None
    hypotheses: list[dict[str, Any]] = []
    hypothesis_ids: set[str] = set()
    semantics: set[tuple[str, str]] = set()
    for body in bodies:
        for raw_hypothesis in body.get("hypotheses", []):
            if not isinstance(raw_hypothesis, dict) or len(hypotheses) >= 4:
                continue
            hypothesis_id = str(raw_hypothesis.get("hypothesis_id", "") or "")
            semantic_key = (
                _normalize_cluster_text(str(raw_hypothesis.get("claim", "") or "")),
                _normalize_cluster_text(str(raw_hypothesis.get("falsified_if", "") or "")),
            )
            if not hypothesis_id or hypothesis_id in hypothesis_ids or semantic_key in semantics:
                continue
            hypothesis_ids.add(hypothesis_id)
            semantics.add(semantic_key)
            hypotheses.append(dict(raw_hypothesis))
    requests: list[dict[str, Any]] = []
    for body in bodies:
        for request in body.get("evidence_requests", []):
            if _task_visible_causal_request(request):
                requests.append(dict(request))
    return {
        "causal_investigation": {
            "hypotheses": hypotheses,
            "evidence_requests": requests,
            "ready_without_more_evidence": False,
        }
    }


def _normalize_outcome_independent_causal_plan(
    value: dict[str, Any] | None,
    *,
    failed_requirement_ids: list[str],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Normalize a plan and salvage clean siblings instead of rejecting it wholesale."""
    raw_conflicts = [
        *_causal_plan_outcome_dependency_conflicts(_raw_causal_plan(value)),
        *_causal_plan_task_visibility_conflicts(value),
    ]
    plan = normalize_causal_investigation(
        value,
        failed_requirement_ids=failed_requirement_ids,
        max_requests=_CAUSAL_INITIAL_REQUEST_LIMIT,
        max_hypotheses=4,
        min_hypotheses=2,
        min_hypotheses_per_requirement=2,
        require_evidence_per_hypothesis=True,
    )
    conflicts = list(dict.fromkeys([*raw_conflicts, *_causal_plan_outcome_dependency_conflicts(plan)]))
    if plan is not None and not conflicts:
        return plan, []
    if not conflicts:
        return None, []

    salvaged = normalize_causal_investigation(
        _outcome_independent_causal_plan(value),
        failed_requirement_ids=failed_requirement_ids,
        max_requests=_CAUSAL_INITIAL_REQUEST_LIMIT,
        max_hypotheses=4,
        min_hypotheses=2,
        min_hypotheses_per_requirement=2,
        require_evidence_per_hypothesis=True,
    )
    return salvaged, conflicts


def _outcome_independent_diagnosis_input(
    diagnosis_input: str,
    *,
    failed_requirement_ids: list[str],
) -> str:
    """Build a task-visible replay input after repeated label-dependent plans."""
    try:
        payload = json.loads(diagnosis_input)
    except (TypeError, ValueError):
        return json.dumps(
            {"failed_requirement_ids": failed_requirement_ids},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    if not isinstance(payload, dict):
        payload = {}

    def strip_outcomes(value: Any) -> Any:
        if isinstance(value, dict):
            hidden_keys = {
                "evaluation_passed",
                "evaluation_reason",
                "failed_trials",
                "judge_detail",
                "judge_evidence",
                "passed",
                "score",
                "score_reason",
                "successful_trials",
                "trial_evaluation",
            }
            return {key: strip_outcomes(item) for key, item in value.items() if key not in hidden_keys}
        if isinstance(value, list):
            return [strip_outcomes(item) for item in value]
        return value

    payload["authoritative_benchmark_test_contract"] = {}
    primary_evidence = payload.get("primary_evidence")
    if isinstance(primary_evidence, dict):
        primary_evidence = dict(primary_evidence)
        primary_evidence["evidence_summary_text"] = ""
        digest = primary_evidence.get("causal_digest")
        if isinstance(digest, dict):
            digest = dict(digest)
            for key in (
                "outcome",
                "failed_requirement_inventory",
                "prior_candidate_feedback",
            ):
                digest.pop(key, None)
            primary_evidence["causal_digest"] = strip_outcomes(digest)
        payload["primary_evidence"] = primary_evidence

    payload["deterministic_validation_inventory"] = {}
    payload["deterministic_verifier_inventory"] = {}
    payload["deterministic_failed_requirement_inventory"] = {
        "policy": "Opaque cluster IDs only; they identify observations to explain, not desired answers.",
        "items": [{"requirement_id": item} for item in failed_requirement_ids],
    }
    payload["prior_candidate_feedback"] = {}
    payload["prior_candidate_feedback_policy"] = "No evaluator-owned prior outcome is available in this recovery phase."
    payload["retrieved_experience"] = {}
    anchor_signals = payload.get("anchor_signals")
    payload["anchor_signals"] = (
        {key: anchor_signals[key] for key in ("method", "exec_failures", "error_clusters") if key in anchor_signals}
        if isinstance(anchor_signals, dict)
        else {}
    )
    case_facts = payload.get("case_facts")
    payload["case_facts"] = (
        {key: case_facts[key] for key in ("case_id", "status", "error") if key in case_facts}
        if isinstance(case_facts, dict)
        else {}
    )
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _build_causal_plan_correction_prompt(
    diagnosis_input: str,
    previous_output: str,
    *,
    validation_conflicts: list[str] | None = None,
) -> str:
    """Correct any response that did not satisfy the mandatory plan phase."""
    return f"""The previous response did not provide a valid mandatory
CAUSAL_INVESTIGATION_PHASE=plan. It may have skipped directly to a diagnosis,
proposed too few alternatives, or omitted a falsifying evidence request. Discard
its conclusion. Return only the bounded causal_investigation JSON requested below.
Do not emit a diagnoses object.

CONTROLLER_VALIDATION_CONFLICTS:
{json.dumps(validation_conflicts or [], ensure_ascii=False)}

Resolve every listed conflict explicitly. In particular, rewrite a rejected
claim or falsified_if as a runtime mechanism and independently observable
prediction. Moving evaluator expected/target values, gold/reference answers,
scores, or pass/fail outcomes to different wording does not resolve the conflict.
Keep evaluator facts only as `evaluation_only:` current_support. Preserve valid
independent hypotheses rather than letting one invalid explanation erase them.

PREMATURE_DIAGNOSIS_TO_TREAT_ONLY_AS_CANDIDATE_HYPOTHESES:
{_truncate_text(previous_output, 6_000)}

    {_build_causal_investigation_prompt(diagnosis_input)}
    """


def _build_outcome_independent_causal_plan_recovery_prompt(
    diagnosis_input: str,
    *,
    failed_requirement_ids: list[str],
    validation_conflicts: list[str],
    retained_plan: dict[str, Any] | None = None,
) -> str:
    """Regenerate the plan from task-visible behavior after repeated label leakage."""
    task_visible_input = _outcome_independent_diagnosis_input(
        diagnosis_input,
        failed_requirement_ids=failed_requirement_ids,
    )
    return f"""CAUSAL_INVESTIGATION_PHASE=outcome_independent_recovery

Two earlier plans depended on evaluator-owned outcomes. Start over from the
task-visible trajectory, artifact observations, public task contract, and active
Harness below. The failed-requirement IDs are opaque cluster identifiers only.
They do not reveal a desired answer, label, score, value, or recovery action.

Return the same causal_investigation JSON schema as the normal plan phase.

Hard rules:
- Produce 2 to 4 materially distinct runtime-mechanism hypotheses for every
  opaque failed-requirement ID, each with at least one executable evidence request.
- The RETAINED_TASK_VISIBLE_PLAN, when non-empty, is controller-frozen. Preserve
  those hypotheses exactly and add only the missing alternatives, coverage, or
  task-visible evidence requests needed to make the complete plan valid.
- State claims at an Agent-controlled decision boundary: evidence acquisition,
  interpretation/classification, state mutation, validation, recovery, or release.
- Claims and falsified_if fields must be decidable from task-visible evidence.
  Never infer what answer the evaluator wanted and never prescribe a case label.
- Decompose the Agent's own final conclusions and proposed actions into material
  decision grounds. A process hypothesis may test whether the Agent verified each
  ground's authority, scope, owner, trigger, and entailment before using it.
- Evidence requests must inspect exact trace events or bounded artifact/repository
  spans. Request missing discriminators instead of guessing a semantic answer.
- Only search_trace, read_event, inspect_artifact, read_artifact_window,
  search_repository, read_repository_file, check_relation, and
  compare_numeric_change are allowed. inspect_evaluation and compare_runs are
  forbidden in this recovery because they can reintroduce evaluator outcomes.
- Return one JSON object only. Do not include Markdown or prose.

REPEATED_CONTROLLER_CONFLICTS:
{json.dumps(validation_conflicts, ensure_ascii=False)}

FAILED_REQUIREMENT_IDS:
{json.dumps(failed_requirement_ids, ensure_ascii=False)}

RETAINED_TASK_VISIBLE_PLAN:
{_bounded_json(retained_plan or {}, 8_000)}

TASK_VISIBLE_DIAGNOSIS_INPUT:
{task_visible_input}
"""


def _build_investigation_diagnosis_prompt(
    *,
    original_prompt: str,
    investigation: dict[str, Any],
    evidence_results: dict[str, Any],
) -> str:
    """Bind final diagnosis to controller-returned evidence and hypothesis tests."""
    return f"""CAUSAL_INVESTIGATION_PHASE=diagnose

Complete the original diagnosis task using the causal investigation below.
For every diagnosis, add these fields to the required JSON shape:

"decision_ground_audit": [
  {{
    "ground_id": "g1",
    "ground_text": "one observed reason used for the decision",
    "materiality": "material | non_material | unknown",
    "used_for_decision": true,
    "authority_status": "verified | missing | contradicted | unknown",
    "scope_status": "matched | mismatched | unknown",
    "owner_status": "matched | mismatched | unknown",
    "trigger_status": "satisfied | not_satisfied | not_applicable | unknown",
    "entailment_status": "entailed | not_entailed | unknown",
    "controller_request_ids": ["q1"]
  }}
],

"hypothesis_assessment": [
  {{
    "hypothesis_id": "h1",
    "status": "supported | falsified | unresolved",
    "falsifying_condition_status": "observed | not_observed | unknown",
    "claim_follows_from_evidence": "yes | no | unknown",
    "evidence_relation": "direct_claim | direct_falsifier | correlated_output | self_consistency | unknown",
    "evidence_independence": "independent | direct_observation | same_mechanism | unknown",
    "logic_check": "explicit derivation; for quantitative claims cite check_relation",
    "controller_request_ids": ["q1"],
    "handoff_disposition": "selected | separate_diagnosis | subsumed | non_actionable",
    "handoff_reason": "why this supported mechanism is or is not handed off independently",
    "reason": "comparison of prediction with controller evidence",
    "evidence_refs": []
  }}
],
"prior_experiment_assessment": {{
  "availability": "available | not_available",
  "intervention_activated": "yes | no | unknown",
  "predicted_behavior_occurred": "yes | no | unknown",
  "predicted_outcome_occurred": "yes | no | unknown",
  "causal_hypothesis_status": "supported | falsified | not_tested | inconclusive",
  "reason": "why the experiment supports or refutes the previous explanation"
}}

Hard rules:
- Across the complete diagnosis set, assess every investigation hypothesis; do
  not silently discard alternatives. Each diagnosis should assess the
  hypotheses relevant to its own failed checks, not copy unrelated unresolved
  hypotheses into every diagnosis.
- Compare each assessment against that hypothesis's own `falsified_if`. If the
  falsifying observable is present, status MUST be falsified even when other
  evidence superficially supports the claim.
- Before assigning statuses, make an internal falsifier matrix: compare every
  hypothesis in one failed-requirement cluster with every available controller
  result in that cluster and with every evidence-backed `current_support`
  statement from its competing hypotheses. A request's `hypothesis_ids` records
  its collection purpose; it does not give one hypothesis exclusive ownership
  of an observed fact.
- Facts may cross hypothesis boundaries only inside an overlapping
  failed-requirement cluster. They may falsify or support any hypothesis in that
  cluster when the fact logically bears on its claim. Never transfer facts
  across unrelated requirement clusters merely because wording is similar.
- Test every necessary subclaim in a composite explanation. If any necessary
  subclaim is contradicted, the composite hypothesis cannot be supported. Keep a
  narrower explanation only when it was separately stated and independently
  supported; otherwise mark the explanation unresolved and request the missing
  discriminator.
- Treat production, validation, and recovery as distinct decision mechanisms.
  An unresolved production cause must not erase an independently supported
  containment diagnosis when controller evidence proves that the exact final
  output was invalid after its last mutation and the required post-mutation
  check or recovery did not occur. Conversely, the existence of a bad output
  alone does not prove that validation was omitted; cite the relevant trace
  evidence.
- Never say that a falsifying observable was not observed when it appears in any
  available controller result or in another hypothesis's evidence-backed
  `current_support`. Cite the request that exposed it and reassess the affected
  hypothesis.
- A supported hypothesis requires `falsifying_condition_status=not_observed`
  and `claim_follows_from_evidence=yes`. Show the actual derivation in
  `logic_check`; do not rely on keyword overlap.
- Cite the controller requests actually used in `controller_request_ids`. A
  request with availability other than `available` supplies no positive or
  falsifying fact. Never describe `not_found`, `not_available`, or `invalid`
  evidence as confirmation.
- Positive support still requires an available controller request compatible
  with the same failed-requirement cluster. A model-authored `current_support`
  sentence alone is not positive evidence, but it must be checked for a
  contradiction when it cites an available controller result.
- Classify how the cited fact bears on the hypothesis. Use `direct_claim` only
  when it observes the claimed mechanism itself and `direct_falsifier` only
  when it observes the hypothesis's pre-registered `falsified_if`. A downstream
  value that merely agrees with another output is `correlated_output`; a result
  generated and checked by the same questioned mechanism is `self_consistency`.
  Neither latter relation can falsify semantic correctness, completeness, or
  numerical convergence.
- For a falsified assessment, `evidence_independence` must be `independent` or
  `direct_observation`. Completion without an error, agreement among outputs,
  or a cached/final value produced by the questioned mechanism is
  `same_mechanism`, not an independent falsifier. When correctness depends on
  an iterative or approximate computation, require a task-visible tolerance,
  a stability/perturbation check at the output's required precision, or an
  independently recomputed counterfactual before rejecting that hypothesis.
- Quantitative and formula claims must agree with every available
  `check_relation` and `compare_numeric_change` result. Treat the controller's
  computed values as immutable.
- A verifier score, expected value, or reference outcome proves only what failed.
  Never reverse-engineer the missing input, formula, or semantic rule from the
  expected outcome and then cite that derivation as causal support. A changed
  numeric decision is actionable only when its input/provenance is observed in
  the public contract, trace, repository, or physical artifact. Otherwise keep
  the hypothesis unresolved.
- For every assigned diagnosis, set `selected_hypothesis_id` to exactly one
  investigation hypothesis whose assessment is supported. The root_cause,
  general_mechanism, decision_contract, recommendation, and counterfactual must
  be consequences of that selected claim. If several hypotheses are supported
  but the proposed action actually comes from an unresolved one, do not combine
  them: select a supported mechanism that really entails the action or return
  the diagnosis unassigned.
- Account for every supported hypothesis across the complete diagnosis set. Set
  handoff_disposition="selected" when that diagnosis selects it. Use
  "separate_diagnosis" only when another returned diagnosis actually selects
  it. Use "subsumed" only when the selected atomic mechanism logically entails
  it, and "non_actionable" only when no Harness decision can change it; both
  require a concrete handoff_reason. Never silently drop an independently
  supported mechanism because another hypothesis was ranked first.
- Mentally remove all evaluator-owned expected values, gold/reference answers,
  scores, and pass/fail labels before writing an assigned decision_contract. The
  action must still be selectable and checkable from the public task contract,
  task-visible artifact, trace, or public repository. Evaluation outcomes may
  falsify and rank hypotheses, but they cannot be the runtime decision rule.
- Treat the authoritative public task contract as a constraint, not merely
  another hypothesis. If the proposed action contradicts its explicit wording
  and only the evaluator outcome favors the contradiction, the diagnosis is
  not deployable and must remain unassigned.
- Separate a deployable decision procedure from the observed task's answer
  label. When evidence supports an upstream process error but does not
  independently entail the gold/output label, preserve the process-level
  diagnosis. Its required_action and acceptance_observable must constrain how
  future evidence is classified, scoped, owned, or validated; they must not
  prescribe this case's answer, verdict, category, or numeric result.
- For any negative conclusion based on alleged requirements, require every
  negative ground to map to task-visible evidence that the requirement is
  mandatory for the artifact, subject, or decision actually under review.
  Keep recommendations, operational conveniences, and duties owned by a
  different subject or process explicitly separate. This procedure may be
  actionable even when the final task label remains unresolved.
- For a decision with several material grounds, do not require proof that every
  ground was wrong or that removing one ground determines the final label. If a
  cited trace span shows a ground contributed to the decision and its authority,
  scope, owner, trigger, or entailment link is missing or contradicted, preserve
  the narrow supported process mechanism `unverified_decision_ground_used`.
  Populate decision_ground_audit and set causal sufficiency to local_contributor
  when other grounds remain unresolved. The required action is to verify every
  material ground before using it, exclude unsupported grounds from the decision,
  and recompute the conclusion; never prescribe the recomputed label.
  Use failure_mode exactly `unverified_decision_ground_used` and include the
  structured decision_ground_audit witness for this narrow mechanism.
- A confirmed diagnosis requires one supported hypothesis and all material
  alternatives to be falsified by controller evidence.
- A supported local mechanism may remain actionable when a different material
  hypothesis is unresolved. Preserve the supported diagnosis and emit a
  separate evidence_status="insufficient", target_ref="unassigned" residual
  diagnosis for the unresolved hypothesis and affected failed checks. Never let
  that residual erase an independently supported local Issue.
- If evidence changed behavior but the predicted outcome did not occur, mark the
  prior causal hypothesis falsified. Do not relabel it as a new downstream problem.
- If evidence cannot distinguish the hypotheses, return evidence_status="insufficient"
  and target_ref="unassigned".
- Controller omission metadata describes the Analyzer view, never what the task
  Agent observed.

CAUSAL_INVESTIGATION:
{_causal_prompt_json(investigation, evidence_results)}

ORIGINAL_DIAGNOSIS_TASK:
{original_prompt}
"""


def _build_causal_refinement_prompt(
    *,
    investigation: dict[str, Any],
    evidence_results: dict[str, Any],
    draft_diagnoses: list[dict[str, Any]],
) -> str:
    """Ask for missing discriminators or independently tested new explanations."""
    remaining = max(
        0,
        _CAUSAL_TOTAL_REQUEST_LIMIT - len(investigation.get("evidence_requests", [])),
    )
    return f"""CAUSAL_INVESTIGATION_PHASE=refine

The first evidence pass left material causal hypotheses unresolved. Stay on this
same Case and request only the smallest missing discriminators. Do not diagnose,
recommend a Harness change, rename existing hypotheses, or repeat completed
requests.

Return the same `causal_investigation` JSON shape used in the plan phase. Copy
the existing hypothesis IDs, claims, and falsified_if fields unchanged, and put
only NEW evidence requests under them. At most {remaining} new requests are
available. If the first evidence pass exposed a materially different mechanism
that no existing hypothesis states, you may add at most one new hypothesis.
Mark each with `origin="abductive_refinement"` and
`discovery_evidence_request_ids` naming the completed requests that revealed it.
Each new hypothesis must also contain at least one NEW, independently useful
request capable of falsifying or confirming it. Discovery evidence alone cannot
make the new hypothesis supported.

Treat evidence ownership as cluster-scoped, not hypothesis-exclusive. If a
completed result or a new hypothesis's evidence-backed `current_support`
contradicts an existing hypothesis's `falsified_if`, include that existing
hypothesis in any new discriminating request and make the contradiction explicit
for the next diagnosis pass. Do not preserve an earlier status merely because
the contradicting result was first requested for another explanation.

Prefer exact read_event requests when a message index is already known. When an
artifact search result has window_complete=false, continue with
read_artifact_window using its exact source and source_char_end instead of
repeating the broad search. Use compare_numeric_change for every
before-versus-after formula claim.
If no allowed controller operation can obtain the missing fact, return the same
hypotheses with empty evidence_requests and ready_without_more_evidence=true.
If completed evidence reveals a distinct production-versus-validation-versus-
recovery decision that no frozen hypothesis states, use the one permitted
abductive_refinement hypothesis for that atomic mechanism rather than widening
an existing composite claim.
If an independent audit rejects a blanket decision-rule claim but directly
observes one material decision ground whose authority/scope/owner/trigger/
entailment chain was not established, use the permitted new hypothesis for the
narrow process mechanism `unverified_decision_ground_used`. Do not keep requesting
evidence to prove the gold label, that every ground was wrong, or that the Agent
used the same shortcut universally. One targeted ground plus its missing or
contradicted chain is the bounded discriminator; unresolved sibling grounds remain
residual.

FIRST_INVESTIGATION_AND_EVIDENCE:
{_causal_prompt_json(investigation, evidence_results)}

DRAFT_UNRESOLVED_DIAGNOSES:
{_bounded_json(draft_diagnoses, 8_000)}
"""


def _causal_handoff_audit_needs_evidence(audit: dict[str, Any]) -> bool:
    """Return whether a rejected handoff names a potentially observable gap."""
    for item in audit.get("diagnosis_audits", []):
        if not isinstance(item, dict) or bool(item.get("approved")):
            continue
        if (
            str(item.get("decision_rule_source", "") or "").strip().casefold() == "none"
            or not bool(item.get("decision_rule_entailed"))
            or not bool(item.get("runtime_decidable"))
        ):
            return True
    return False


def _build_causal_handoff_evidence_prompt(
    *,
    public_task_contract: str,
    investigation: dict[str, Any],
    evidence_results: dict[str, Any],
    diagnoses: list[dict[str, Any]],
    audit: dict[str, Any],
) -> str:
    """Ask for controller-executable evidence before abandoning a handoff."""
    remaining = max(
        0,
        _CAUSAL_TOTAL_REQUEST_LIMIT - len(investigation.get("evidence_requests", [])),
    )
    return f"""CAUSAL_INVESTIGATION_PHASE=handoff_evidence_closure

The independent handoff audit found that a proposed runtime decision lacked an
authoritative source, scope rule, ownership rule, or other task-visible
discriminator. Do not repair the diagnosis to unassigned yet. First determine
whether the missing discriminator can be obtained with a controller operation.

Return the same `causal_investigation` JSON shape used in the refinement phase.
Copy every existing hypothesis ID, claim, and falsified_if field unchanged and
return only NEW evidence requests. At most {remaining} requests remain.

Evidence-source discovery rules:
- Inspect the public task files and the task Agent's trace for sources it named,
  opened, cited, relied on, or used to derive the disputed decision. Prefer the
  source that actually governed the Agent's decision, not merely the artifact
  whose outcome was scored.
- When the audit says a decision rule has no authority, request the exact public
  passage that could establish or refute its authority, scope, ownership,
  trigger, or entailment. A score, expected answer, or evaluator rationale is
  not such a source.
- If a diagnosis itself names a concrete missing probe, translate that probe
  into an allowed controller request rather than stopping at the prose
  recommendation.
- Use search_trace/read_event to recover source identity or the exact decision
  step; use inspect_artifact/read_artifact_window for task artifacts already
  available to the Agent; use repository operations only for the public
  evaluated repository. Do not search hidden evaluator or gold data.
- Do not repeat a completed request. Preserve exact source and offset continuity
  for incomplete windows.
- If no public, task-visible source or allowed operation can obtain the missing
  fact, return empty evidence_requests and ready_without_more_evidence=true.

PUBLIC_TASK_CONTRACT:
{_bounded_json({"input_excerpt": public_task_contract}, 8_000)}

FROZEN_INVESTIGATION_AND_EVIDENCE:
{_causal_prompt_json(investigation, evidence_results)}

DRAFT_DIAGNOSES:
{_bounded_json(diagnoses, 12_000)}

REJECTED_HANDOFF_AUDIT:
{_bounded_json(audit, 8_000)}
"""


def _diagnoses_need_causal_refinement(
    diagnoses: list[dict[str, Any]],
    *,
    failed_requirement_ids: list[str] | None = None,
) -> bool:
    """Return whether the same Case still lacks a causally explained failure.

    A syntactically valid local contributor is not enough when another failed
    requirement remains only residual.  Keep evidence acquisition on the
    current Case so the next Case cannot displace the unresolved mechanism.
    """
    if not diagnoses:
        return False
    explained_ids: set[str] = set()
    for diagnosis in diagnoses:
        if str(diagnosis.get("evidence_status", "") or "").strip().casefold() == "insufficient":
            return True
        coverage = diagnosis.get("causal_coverage")
        if isinstance(coverage, dict):
            explained_ids.update(_string_items(coverage.get("explained_requirement_ids", [])))
            if str(coverage.get("sufficiency_status", "") or "").strip().casefold() == "unknown":
                return True
        assessments = diagnosis.get("hypothesis_assessment", [])
        if any(
            isinstance(item, dict) and str(item.get("status", "") or "").strip().casefold() == "unresolved"
            for item in assessments
            if isinstance(assessments, list)
        ):
            return True
    required_ids = {item for item in (failed_requirement_ids or []) if item}
    return bool(required_ids - explained_ids)


def _causal_conflicts_need_more_evidence(conflicts: list[str]) -> bool:
    rendered = " ".join(conflicts).casefold()
    markers = (
        "lacks an available",
        "supported claim cites no available",
        "treated unavailable controller requests as evidence",
        "unresolved material hypotheses",
    )
    for marker in markers:
        if marker in rendered:
            return True
    return False


def _merge_causal_investigation(
    base: dict[str, Any],
    refinement: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Add one bounded set of genuinely new requests to a frozen hypothesis set."""
    base_requests = [dict(item) for item in base.get("evidence_requests", []) if isinstance(item, dict)]
    remaining = max(0, _CAUSAL_TOTAL_REQUEST_LIMIT - len(base_requests))
    if remaining == 0:
        return dict(base), []
    base_hypotheses = [dict(item) for item in base.get("hypotheses", []) if isinstance(item, dict)]
    base_hypothesis_ids = {
        str(item.get("hypothesis_id", "") or "")
        for item in base_hypotheses
        if isinstance(item, dict) and str(item.get("hypothesis_id", "") or "")
    }
    new_hypotheses: list[dict[str, Any]] = []
    for item in refinement.get("hypotheses", []):
        if not isinstance(item, dict):
            continue
        hypothesis_id = str(item.get("hypothesis_id", "") or "")
        if (
            not hypothesis_id
            or hypothesis_id in base_hypothesis_ids
            or str(item.get("origin", "") or "") != "abductive_refinement"
        ):
            continue
        new_hypotheses.append(dict(item))
        if len(base_hypotheses) + len(new_hypotheses) >= 5:
            break
    hypothesis_ids = base_hypothesis_ids | {str(item.get("hypothesis_id", "") or "") for item in new_hypotheses}

    def _fingerprint(request: dict[str, Any]) -> str:
        comparable = {key: value for key, value in request.items() if key != "request_id"}
        return json.dumps(comparable, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    seen = {_fingerprint(item) for item in base_requests}
    additions: list[dict[str, Any]] = []
    used_ids = {str(item.get("request_id", "") or "") for item in base_requests}
    for raw in refinement.get("evidence_requests", []):
        if not isinstance(raw, dict):
            continue
        request = dict(raw)
        request["hypothesis_ids"] = [
            item for item in _string_items(request.get("hypothesis_ids", [])) if item in hypothesis_ids
        ]
        if not request["hypothesis_ids"]:
            continue
        fingerprint = _fingerprint(request)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        stem = str(request.get("request_id", "") or f"q{len(additions) + 1}")
        request_id = f"refine_{stem}"
        suffix = 2
        while request_id in used_ids:
            request_id = f"refine_{stem}_{suffix}"
            suffix += 1
        request["request_id"] = request_id
        used_ids.add(request_id)
        additions.append(request)
        if len(additions) >= remaining:
            break

    referenced_new_ids: set[str] = set()
    for request in additions:
        for hypothesis_id in _string_items(request.get("hypothesis_ids", [])):
            if hypothesis_id not in base_hypothesis_ids:
                referenced_new_ids.add(hypothesis_id)
    retained_new_hypotheses: list[dict[str, Any]] = []
    for item in new_hypotheses:
        if str(item.get("hypothesis_id", "") or "") in referenced_new_ids:
            retained_new_hypotheses.append(item)
    retained_ids = base_hypothesis_ids | {str(item.get("hypothesis_id", "") or "") for item in retained_new_hypotheses}
    retained_additions: list[dict[str, Any]] = []
    for request in additions:
        retained_hypothesis_ids = []
        for hypothesis_id in _string_items(request.get("hypothesis_ids", [])):
            if hypothesis_id in retained_ids:
                retained_hypothesis_ids.append(hypothesis_id)
        retained_additions.append({**request, "hypothesis_ids": retained_hypothesis_ids})
    additions = retained_additions
    additions = [request for request in additions if request["hypothesis_ids"]]
    merged = dict(base)
    merged["hypotheses"] = [*base_hypotheses, *retained_new_hypotheses]
    merged["evidence_requests"] = [*base_requests, *additions]
    return merged, additions


def _normalize_causal_refinement(
    value: dict[str, Any] | None,
    *,
    base: dict[str, Any],
    failed_requirement_ids: list[str],
) -> dict[str, Any] | None:
    """Preserve planned hypotheses and admit independently testable discoveries."""
    if not isinstance(value, dict):
        return None
    raw = value.get("causal_investigation")
    if not isinstance(raw, dict):
        raw = value.get("investigation")
    if not isinstance(raw, dict):
        raw = value
    if not isinstance(raw, dict):
        return None
    base_hypotheses = [dict(item) for item in base.get("hypotheses", []) if isinstance(item, dict)]
    base_hypothesis_ids = [
        str(item.get("hypothesis_id", "") or "")
        for item in base_hypotheses
        if isinstance(item, dict) and str(item.get("hypothesis_id", "") or "")
    ]
    base_hypothesis_id_set = set(base_hypothesis_ids)
    base_request_ids = {
        str(item.get("request_id", "") or "")
        for item in base.get("evidence_requests", [])
        if isinstance(item, dict) and str(item.get("request_id", "") or "")
    }
    allowed_requirements = {item for item in failed_requirement_ids if item}
    requests: list[dict[str, Any]] = []
    top_level = raw.get("evidence_requests", [])
    if isinstance(top_level, list):
        for raw_request in top_level:
            if not isinstance(raw_request, dict):
                continue
            request = dict(raw_request)
            if not _string_items(request.get("hypothesis_ids", [])):
                request["hypothesis_ids"] = list(base_hypothesis_ids)
            requests.append(request)
    frozen_hypotheses = [
        {
            "hypothesis_id": str(item.get("hypothesis_id", "") or ""),
            "claim": str(item.get("claim", "") or ""),
            "explains_requirement_ids": _string_items(item.get("explains_requirement_ids", [])),
            "current_support": _string_items(item.get("current_support", [])),
            "falsified_if": str(item.get("falsified_if", "") or ""),
            "numeric_change_check_required": bool(item.get("numeric_change_check_required")),
        }
        for item in base_hypotheses
    ]
    accepted_new_hypotheses: list[dict[str, Any]] = []
    seen_semantics = {
        (
            _normalize_cluster_text(str(item.get("claim", "") or "")),
            _normalize_cluster_text(str(item.get("falsified_if", "") or "")),
        )
        for item in frozen_hypotheses
    }
    raw_hypotheses = raw.get("hypotheses", [])
    if isinstance(raw_hypotheses, list):
        for hypothesis in raw_hypotheses:
            if not isinstance(hypothesis, dict):
                continue
            hypothesis_id = str(hypothesis.get("hypothesis_id", "") or "")
            is_new = hypothesis_id not in base_hypothesis_id_set
            if is_new:
                if len(accepted_new_hypotheses) >= min(1, max(0, 5 - len(frozen_hypotheses))):
                    continue
                claim = str(hypothesis.get("claim", "") or "").strip()
                falsified_if = str(hypothesis.get("falsified_if", "") or "").strip()
                explains = _string_items(hypothesis.get("explains_requirement_ids", []))
                if allowed_requirements:
                    explains = [item for item in explains if item in allowed_requirements]
                discovery_ids = list(dict.fromkeys(_string_items(hypothesis.get("discovery_evidence_request_ids", []))))
                raw_hypothesis_requests = hypothesis.get("evidence_requests", [])
                hypothesis_requests = (
                    [item for item in raw_hypothesis_requests if isinstance(item, dict)]
                    if isinstance(raw_hypothesis_requests, list)
                    else []
                )
                semantics = (_normalize_cluster_text(claim), _normalize_cluster_text(falsified_if))
                if not hypothesis_id or not claim or not falsified_if:
                    continue
                origin_invalid = str(hypothesis.get("origin", "") or "") != "abductive_refinement"
                requirements_invalid = bool(allowed_requirements) and not explains
                if origin_invalid or requirements_invalid:
                    continue
                discovery_missing = not discovery_ids or not set(discovery_ids).issubset(base_request_ids)
                if discovery_missing or not hypothesis_requests:
                    continue
                outcome_dependent = _contains_evaluator_outcome_dependency(
                    claim
                ) or _contains_evaluator_outcome_dependency(falsified_if)
                if semantics in seen_semantics or outcome_dependent:
                    continue
                seen_semantics.add(semantics)
                accepted_new_hypotheses.append(
                    {
                        "hypothesis_id": hypothesis_id,
                        "claim": claim,
                        "explains_requirement_ids": explains,
                        "current_support": _string_items(hypothesis.get("current_support", [])),
                        "falsified_if": falsified_if,
                        "numeric_change_check_required": bool(hypothesis.get("numeric_change_check_required")),
                        "origin": "abductive_refinement",
                        "discovery_evidence_request_ids": discovery_ids,
                    }
                )
            nested_requests = hypothesis.get("evidence_requests", [])
            for request in nested_requests if isinstance(nested_requests, list) else []:
                if not isinstance(request, dict):
                    continue
                if is_new and not any(item["hypothesis_id"] == hypothesis_id for item in accepted_new_hypotheses):
                    continue
                item = dict(request)
                ids = _string_items(item.get("hypothesis_ids", []))
                if hypothesis_id:
                    ids.append(hypothesis_id)
                item["hypothesis_ids"] = list(dict.fromkeys(ids))
                requests.append(item)
    remaining = max(0, _CAUSAL_TOTAL_REQUEST_LIMIT - len(base.get("evidence_requests", [])))
    normalized = normalize_causal_investigation(
        {
            "causal_investigation": {
                "hypotheses": [*frozen_hypotheses, *accepted_new_hypotheses],
                "evidence_requests": requests,
                "ready_without_more_evidence": bool(raw.get("ready_without_more_evidence")),
            }
        },
        failed_requirement_ids=failed_requirement_ids,
        max_requests=remaining,
    )
    if normalized is None:
        return None
    normalized_requests = normalized.get("evidence_requests", [])
    covered_new_ids: set[str] = set()
    for request in normalized_requests:
        if not isinstance(request, dict):
            continue
        covered_new_ids.update(_string_items(request.get("hypothesis_ids", [])))
    accepted_by_id = {item["hypothesis_id"]: item for item in accepted_new_hypotheses}
    normalized_hypotheses: list[dict[str, Any]] = []
    retained_ids = set(base_hypothesis_ids)
    for item in normalized.get("hypotheses", []):
        if not isinstance(item, dict):
            continue
        hypothesis_id = str(item.get("hypothesis_id", "") or "")
        if hypothesis_id in accepted_by_id:
            if hypothesis_id not in covered_new_ids:
                continue
            item = {
                **item,
                "origin": "abductive_refinement",
                "discovery_evidence_request_ids": accepted_by_id[hypothesis_id]["discovery_evidence_request_ids"],
            }
            retained_ids.add(hypothesis_id)
        normalized_hypotheses.append(item)
    normalized["hypotheses"] = normalized_hypotheses
    normalized["evidence_requests"] = [
        {
            **request,
            "hypothesis_ids": [
                hypothesis_id
                for hypothesis_id in _string_items(request.get("hypothesis_ids", []))
                if hypothesis_id in retained_ids
            ],
        }
        for request in normalized_requests
        if isinstance(request, dict)
    ]
    normalized["evidence_requests"] = [
        request for request in normalized["evidence_requests"] if request["hypothesis_ids"]
    ]
    return normalized


def _merge_causal_evidence_results(
    base: dict[str, Any],
    refinement: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(base)
    merged_results = [
        *[dict(item) for item in base.get("results", []) if isinstance(item, dict)],
        *[dict(item) for item in refinement.get("results", []) if isinstance(item, dict)],
    ]
    merged["results"] = merged_results
    merged["request_count"] = len(merged_results)
    merged["completed_request_count"] = len(merged_results)
    automatic_requests = [
        *[dict(item) for item in base.get("automatic_requests", []) if isinstance(item, dict)],
        *[dict(item) for item in refinement.get("automatic_requests", []) if isinstance(item, dict)],
    ]
    merged["automatic_requests"] = automatic_requests
    merged["automatic_request_count"] = len(automatic_requests)
    closures: list[dict[str, Any]] = []
    for item in (
        base.get("artifact_evidence_closure"),
        refinement.get("artifact_evidence_closure"),
    ):
        if isinstance(item, dict) and bool(item.get("attempted")):
            closures.append(dict(item))
    if closures:
        source_rows: list[dict[str, Any]] = []
        for closure in closures:
            for source in closure.get("sources", []):
                if isinstance(source, dict):
                    source_rows.append(dict(source))
        merged["artifact_evidence_closure"] = {
            "attempted": True,
            "status": (
                "completed"
                if source_rows and all(bool(item.get("complete")) for item in source_rows)
                else "budget_exhausted"
                if any(item.get("status") == "budget_exhausted" for item in source_rows)
                else "incomplete"
            ),
            "candidate_source_count": len(source_rows),
            "completed_source_count": sum(bool(item.get("complete")) for item in source_rows),
            "automatic_window_count": len(automatic_requests),
            "read_char_count": sum(int(item.get("read_char_count", 0) or 0) for item in closures),
            "sources": source_rows,
        }
    return merged


def _adopt_automatic_evidence_requests(
    investigation: dict[str, Any],
    evidence_results: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind controller-generated continuation reads to the frozen plan."""
    automatic = [dict(item) for item in evidence_results.get("automatic_requests", []) if isinstance(item, dict)]
    if not automatic:
        return investigation
    merged = dict(investigation)
    requests = [dict(item) for item in investigation.get("evidence_requests", []) if isinstance(item, dict)]
    used_ids = {str(item.get("request_id", "") or "") for item in requests}
    seen = {
        json.dumps(
            {key: value for key, value in item.items() if key != "request_id"},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        for item in requests
    }
    for request in automatic:
        request_id = str(request.get("request_id", "") or "")
        fingerprint = json.dumps(
            {key: value for key, value in request.items() if key != "request_id"},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        if not request_id or request_id in used_ids or fingerprint in seen:
            continue
        used_ids.add(request_id)
        seen.add(fingerprint)
        requests.append(request)
    merged["evidence_requests"] = requests
    return merged


def _load_effective_harness_snapshot(harness_refs_path: str) -> dict[str, Any]:
    """Read a bounded, secret-safe snapshot of the Harness that produced the trace."""
    if not harness_refs_path:
        return {"schema_version": 1, "availability": "not_provided", "roles": []}
    refs_path = Path(harness_refs_path).expanduser()
    if not refs_path.is_file():
        return {
            "schema_version": 1,
            "availability": "missing",
            "source": str(refs_path),
            "roles": [],
        }
    try:
        payload = yaml.safe_load(refs_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return {
            "schema_version": 1,
            "availability": "invalid",
            "source": str(refs_path),
            "error": _truncate_text(str(exc), 500),
            "roles": [],
        }
    raw_refs = payload.get("harness_refs") if isinstance(payload, dict) else None
    if not isinstance(raw_refs, dict):
        return {
            "schema_version": 1,
            "availability": "invalid",
            "source": str(refs_path.resolve()),
            "roles": [],
        }

    roles = [
        _snapshot_harness_role(str(role), str(raw_ref))
        for role, raw_ref in sorted(raw_refs.items(), key=lambda item: str(item[0]))
        if str(raw_ref).strip()
    ]
    available_count = sum(1 for role in roles if role.get("availability") == "available")
    availability = (
        "available" if roles and available_count == len(roles) else "partial" if available_count else "missing"
    )
    return {
        "schema_version": 1,
        "availability": availability,
        "source": str(refs_path.resolve()),
        "policy": (
            "This is the effective Harness configuration. Check it before claiming a Prompt, Skill, Tool, "
            "Rail, or budget rule is missing."
        ),
        "roles": roles,
    }


def _snapshot_harness_role(role: str, raw_ref: str) -> dict[str, Any]:
    root = Path(raw_ref).expanduser()
    if not root.is_absolute():
        root = root.resolve()
    if not root.is_dir():
        return {"role": role, "availability": "missing", "harness_ref_path": str(root)}
    root = root.resolve()

    config_path = None
    for name in ("harness.json", "harness.yaml", "harness.yml"):
        candidate_path = root / name
        if candidate_path.is_file():
            config_path = candidate_path
            break
    raw_config: dict[str, Any] = {}
    if config_path is not None:
        try:
            if config_path.suffix == ".json":
                loaded = json.loads(config_path.read_text(encoding="utf-8"))
            else:
                loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            raw_config = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError, yaml.YAMLError):
            raw_config = {}

    config = {
        str(key): _safe_harness_value(value)
        for key, value in raw_config.items()
        if str(key) in _HARNESS_CONFIG_KEYS and not _SENSITIVE_CONFIG_KEY.search(str(key))
    }
    prompt_path, prompt_text = _effective_prompt(root, raw_config)
    skill_entries = []
    for path in sorted(root.rglob("SKILL.md"))[:12]:
        if not path.is_file():
            continue
        skill_entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": _file_digest(path),
                "content_excerpt": _truncate_text(
                    _read_text_if_exists(path, _HARNESS_SKILL_CHARS), _HARNESS_SKILL_CHARS
                ),
            }
        )

    implementation_entries = []
    implementation_candidates = [root / "harness.py", *sorted((root / "agent").glob("*.py"))[:4]]
    for path in implementation_candidates:
        if not path.is_file():
            continue
        implementation_entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": _file_digest(path),
                "content_excerpt": _truncate_text(_read_text_if_exists(path, 6_000), 6_000),
            }
        )

    surface_files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(root).as_posix()
        if not (
            path.name in {"harness.json", "harness.yaml", "harness.yml", "system_prompt.md", "SKILL.md"}
            or any(part.lower() in {"skills", "tools", "rails"} for part in path.relative_to(root).parts[:-1])
        ):
            continue
        surface_files.append({"path": relative, "size": path.stat().st_size, "sha256": _file_digest(path)})
        if len(surface_files) >= 80:
            break

    return {
        "role": role,
        "availability": "available",
        "harness_ref_path": str(root),
        "config_path": config_path.relative_to(root).as_posix() if config_path else "",
        "config": config,
        "effective_system_prompt": {
            "availability": "available" if prompt_text else "not_found",
            "path": prompt_path.relative_to(root).as_posix() if prompt_path else "",
            "sha256": _file_digest(prompt_path) if prompt_path else "",
            "character_count": len(prompt_text),
            "content": _truncate_text(prompt_text, _HARNESS_PROMPT_CHARS),
        },
        "skills": skill_entries,
        "implementation": implementation_entries,
        "surface_files": surface_files,
    }


def _effective_prompt(root: Path, config: dict[str, Any]) -> tuple[Path | None, str]:
    configured = config.get("system_prompt") or config.get("prompt")
    if isinstance(configured, str) and configured.strip():
        candidate = (root / configured).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return candidate, _read_text_if_exists(candidate, _HARNESS_PROMPT_CHARS * 2)
        if "\n" in configured:
            return None, configured
    fallback = root / "system_prompt.md"
    if fallback.is_file():
        return fallback, _read_text_if_exists(fallback, _HARNESS_PROMPT_CHARS * 2)
    return None, ""


def _safe_harness_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 5:
        return "[depth-limited]"
    if isinstance(value, dict):
        return {
            str(key): "[redacted]"
            if _SENSITIVE_CONFIG_KEY.search(str(key))
            else _safe_harness_value(item, depth=depth + 1)
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, list):
        return [_safe_harness_value(item, depth=depth + 1) for item in value[:50]]
    if isinstance(value, str):
        return _truncate_text(value, 2_000)
    return value if value is None or isinstance(value, int | float | bool) else str(value)


def _file_digest(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _build_workspace_evidence(case: CaseAnalysisInput) -> dict[str, Any]:
    """Index inspectable artifacts without claiming which files the agent changed."""
    result = _load_case_result(case)
    workspace_value = result.get("workspace_dir")
    if not isinstance(workspace_value, str) or not workspace_value.strip():
        return {
            "availability": "not_provided",
            "change_attribution": "not_available",
            "artifact_files": [],
        }
    root = Path(workspace_value).expanduser()
    if not root.is_dir():
        return {
            "availability": "missing",
            "change_attribution": "not_available",
            "artifact_files": [],
        }

    artifacts: list[tuple[float, dict[str, Any]]] = []
    suffix_counts: dict[str, int] = {}
    try:
        paths = root.rglob("*")
        for path in paths:
            if not path.is_file() or path.suffix.lower() not in _ARTIFACT_SUFFIXES:
                continue
            try:
                stat = path.stat()
                relative = path.relative_to(root).as_posix()
            except OSError:
                continue
            suffix = path.suffix.lower()
            suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
            artifacts.append(
                (
                    stat.st_mtime,
                    {
                        "path": relative,
                        "type": suffix.lstrip("."),
                        "size": stat.st_size,
                        "mtime_utc": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                    },
                )
            )
    except OSError as exc:
        logger.warning("failed to inspect analysis artifact snapshot %s: %s", root, exc)
    artifacts.sort(key=lambda item: (-item[0], item[1]["path"]))
    return {
        "availability": "available",
        "repository_snapshot_path": "repository/",
        "change_attribution": (
            "post_execution_snapshot_only; mtime ordering does not prove that the evaluated agent created or "
            "changed a file"
        ),
        "artifact_file_count": len(artifacts),
        "artifact_type_counts": dict(sorted(suffix_counts.items())),
        "artifact_files": [item for _, item in artifacts[:40]],
        "artifact_files_truncated": len(artifacts) > 40,
    }


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


def _causal_prompt_json(investigation: Mapping[str, Any], evidence_results: Mapping[str, Any]) -> str:
    """Render every controller result without letting one large result hide later requests."""
    hypotheses = []
    for item in investigation.get("hypotheses", []):
        if not isinstance(item, Mapping):
            continue
        hypothesis: dict[str, Any] = {}
        for key in (
            "hypothesis_id",
            "hypothesis_semantic_id",
            "claim",
            "explains_requirement_ids",
            "falsified_if",
            "origin",
        ):
            if item.get(key) not in (None, "", []):
                hypothesis[key] = item.get(key)
        hypotheses.append(hypothesis)
    results: list[dict[str, Any]] = []
    for item in evidence_results.get("results", []):
        if isinstance(item, Mapping):
            results.append(_compact_controller_result(item))
    payload = {
        "investigation": {
            "schema_version": investigation.get("schema_version", 1),
            "hypotheses": hypotheses,
        },
        "controller_evidence_results": {
            "request_count": evidence_results.get("request_count", len(results)),
            "completed_request_count": evidence_results.get("completed_request_count", len(results)),
            "artifact_evidence_closure": evidence_results.get("artifact_evidence_closure", {}),
            "results": results,
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def _compact_controller_result(item: Mapping[str, Any]) -> dict[str, Any]:
    """Keep high-signal evidence from each request under an independent budget."""
    compact: dict[str, Any] = {}
    large_fields = {"matches", "event", "text", "paired_feedback"}
    for key, value in item.items():
        if key in large_fields:
            continue
        compact[key] = _bounded_structured_value(value, 800)
    if isinstance(item.get("text"), str):
        # A controller window is already bounded. Truncating it again here can
        # hide the exact tail that automatic evidence closure just recovered.
        compact["text"] = _truncate_text(item["text"], 12_000)
    if isinstance(item.get("matches"), list):
        compact["matches"] = [
            {
                "source": match.get("source", ""),
                "logical_source": match.get("logical_source", ""),
                "exact_spans": [
                    {
                        **{key: value for key, value in span.items() if key != "text"},
                        "text": _truncate_text(span.get("text", ""), 1_800),
                    }
                    for span in match.get("exact_spans", [])[:2]
                    if isinstance(span, Mapping)
                ],
            }
            for match in item["matches"][:3]
            if isinstance(match, Mapping)
        ]
    if isinstance(item.get("event"), Mapping):
        event = item["event"]
        compact["event"] = {key: value for key, value in event.items() if key not in {"content", "tool_calls"}}
        compact["event"]["content"] = _truncate_text(event.get("content", ""), 1_800)
        compact["event"]["tool_calls"] = [
            {
                **{key: value for key, value in call.items() if key not in {"input", "output", "error"}},
                "input": _truncate_text(call.get("input", ""), 1_200),
                "output": _truncate_text(call.get("output", ""), 2_000),
                "error": _truncate_text(call.get("error", ""), 800),
            }
            for call in event.get("tool_calls", [])[:2]
            if isinstance(call, Mapping)
        ]
    if "paired_feedback" in item:
        compact["paired_feedback"] = _bounded_structured_value(item["paired_feedback"], 3_500)
    return compact


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


def _build_failed_requirement_inventory(
    case: CaseAnalysisInput,
    *,
    judge_breakdown: dict[str, Any] | None = None,
    verifier_inventory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build stable handles for every evaluator-observed unmet requirement.

    This inventory deliberately describes failure facts, not causes. It gives
    the diagnosis model a complete checklist and lets the validator detect when
    a salient runtime error displaced an independent failed requirement.
    """
    judge_breakdown = judge_breakdown or _summarize_evaluation_metadata(case.evaluation_metadata)
    verifier_inventory = verifier_inventory or _build_verifier_inventory(case)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _append(
        requirement_id: str,
        *,
        source: str,
        expected: str,
        observed: str,
        evidence_status: str = "observed_failed",
    ) -> None:
        identifier = requirement_id.strip()
        if not identifier or identifier in seen:
            return
        seen.add(identifier)
        items.append(
            {
                "requirement_id": identifier,
                "source": source,
                "expected": _truncate_text(expected, 500),
                "observed": _truncate_text(observed, _TEXT_SNIPPET_CHARS),
                "evidence_status": evidence_status,
            }
        )

    for group_name, key in (
        ("FAIL_TO_PASS", "failed_fail_to_pass_tests"),
        ("PASS_TO_PASS", "failed_pass_to_pass_tests"),
    ):
        for test_id in _string_items(verifier_inventory.get(key, [])):
            _append(
                f"verifier:{group_name}:{test_id}",
                source="deterministic_verifier_inventory",
                expected=f"authoritative {group_name} check {test_id} passes",
                observed="authoritative verifier reports this check failed",
            )

    criteria = judge_breakdown.get("criteria", []) if isinstance(judge_breakdown, dict) else []
    for index, criterion in enumerate(criteria, start=1):
        if not isinstance(criterion, dict):
            continue
        passed = criterion.get("passed")
        score = criterion.get("score")
        status = str(criterion.get("status") or "").strip().lower()
        failed = passed is False
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            failed = failed or float(score) < 1.0
        failed = failed or status in {"fail", "failed", "failure", "unmet", "incorrect", "error"}
        if not failed:
            continue
        criterion_id = str(criterion.get("criterion_id") or criterion.get("verifier_id") or f"criterion_{index}")
        observed_parts = []
        if score is not None:
            observed_parts.append(f"score={score}")
        if status:
            observed_parts.append(f"status={status}")
        rationale = str(criterion.get("rationale") or "").strip()
        if rationale:
            observed_parts.append(rationale)
        _append(
            f"criterion:{criterion_id}",
            source="judge_breakdown.criteria",
            expected=f"satisfy evaluator criterion {criterion_id}",
            observed="; ".join(observed_parts) or "criterion was not satisfied",
        )

    behaviors = judge_breakdown.get("behaviors", []) if isinstance(judge_breakdown, dict) else []
    for index, behavior in enumerate(behaviors, start=1):
        if not isinstance(behavior, dict):
            continue
        score = behavior.get("score")
        failure_reason = str(behavior.get("failure_reason") or "").strip()
        missing_capability = str(behavior.get("missing_capability") or "").strip()
        failed = bool(failure_reason or missing_capability)
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            failed = failed or float(score) < 1.0
        if not failed:
            continue
        behavior_id = str(behavior.get("id") or f"behavior_{index}")
        observed = failure_reason or str(behavior.get("reason") or "").strip()
        if missing_capability:
            observed = f"{observed}; missing_capability={missing_capability}".strip("; ")
        _append(
            f"behavior:{behavior_id}",
            source="judge_breakdown.behaviors",
            expected=f"satisfy evaluated behavior {behavior_id}",
            observed=observed or f"behavior score={score}",
        )

    quality_gaps = judge_breakdown.get("quality_gaps", []) if isinstance(judge_breakdown, dict) else []
    for index, gap in enumerate(quality_gaps, start=1):
        if not isinstance(gap, dict):
            continue
        gap_id = str(gap.get("id") or f"quality_gap_{index}")
        observed = str(gap.get("missing_capability") or gap.get("evidence") or "artifact quality gap")
        _append(
            f"quality_gap:{gap_id}",
            source="judge_breakdown.quality_gaps",
            expected=f"avoid evaluator-observed artifact quality gap {gap_id}",
            observed=observed,
        )

    if not items and not case.evaluation_passed:
        _append(
            "case:authoritative_outcome",
            source="case_facts",
            expected="authoritative case evaluation passes",
            observed=case.evaluation_reason or case.error or f"status={case.status}; score={case.score}",
            evidence_status="failed_detail_unavailable",
        )

    return {
        "schema_version": 1,
        "completeness": "evaluator_observed",
        "policy": (
            "Every item must be acknowledged by the diagnosis output. This inventory records unmet "
            "requirements only and does not identify their causes."
        ),
        "items": items,
    }


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


def _normalize_case_diagnoses(
    parsed: dict[str, Any],
    *,
    prior_candidate_feedback: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Expand one model response into bounded, independent diagnoses.

    New responses use ``{"diagnoses": [...]}``; a legacy single diagnosis object
    remains valid. Candidate feedback is used deterministically so regressions and
    residual failures cannot be displaced by already-fixed checks when the model
    returns more than the per-case limit.
    """
    raw_diagnoses = parsed.get("diagnoses")
    if raw_diagnoses is None:
        candidates = [parsed]
    elif isinstance(raw_diagnoses, list):
        candidates = [item for item in raw_diagnoses if isinstance(item, dict)]
    else:
        candidates = []
    expanded_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        expanded_candidates.extend(_split_supported_diagnosis_residual(candidate))
    candidates = expanded_candidates

    feedback_sets = _candidate_feedback_check_sets(prior_candidate_feedback)
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    require_cluster = isinstance(raw_diagnoses, list) and len(candidates) > 1
    for index, raw in enumerate(candidates):
        diagnosis = dict(raw)
        if not _diagnosis_has_semantic_content(diagnosis):
            continue
        failure_cluster = _diagnosis_failure_cluster(diagnosis)
        if require_cluster and not failure_cluster:
            continue
        if failure_cluster:
            diagnosis["failure_cluster"] = failure_cluster
        priority, fixed_only = _diagnosis_feedback_priority(
            diagnosis,
            feedback_sets=feedback_sets,
        )
        if fixed_only:
            continue
        ranked.append((priority, index, diagnosis))

    normalized: list[dict[str, Any]] = []
    for _, _, diagnosis in sorted(ranked, key=lambda item: (item[0], item[1])):
        if any(_diagnoses_are_duplicate(diagnosis, existing) for existing in normalized):
            continue
        normalized.append(diagnosis)
        if len(normalized) >= _MAX_DIAGNOSES_PER_CASE:
            break
    return normalized


def _split_supported_diagnosis_residual(diagnosis: dict[str, Any]) -> list[dict[str, Any]]:
    """Preserve an evidenced local Issue without discarding unresolved residue.

    Models sometimes assess all global hypotheses inside one diagnosis.  When
    one mechanism is supported but a different hypothesis remains unresolved,
    rejecting the assigned diagnosis loses useful causal evidence.  Split the
    unresolved assessment into an unassigned record instead; the Improver sees
    the local Issue while the Analyzer retains an explicit request for evidence.
    """
    evidence_status = str(diagnosis.get("evidence_status", "") or "").strip().casefold()
    target_ref = _normalize_target_ref(diagnosis.get("target_ref", ""))
    raw_assessments = diagnosis.get("hypothesis_assessment", [])
    assessments = (
        [item for item in raw_assessments if isinstance(item, dict)] if isinstance(raw_assessments, list) else []
    )
    supported = [item for item in assessments if str(item.get("status", "") or "").strip().casefold() == "supported"]
    unresolved = [item for item in assessments if str(item.get("status", "") or "").strip().casefold() == "unresolved"]
    evidence_assignable = evidence_status in {"confirmed", "supported_hypothesis"}
    diagnosis_assignable = target_ref not in {"", "unassigned"} and bool(supported)
    if not evidence_assignable or not diagnosis_assignable or not unresolved:
        return [dict(diagnosis)]

    local = dict(diagnosis)
    local["evidence_status"] = "supported_hypothesis"
    local["hypothesis_assessment"] = [
        item for item in assessments if str(item.get("status", "") or "").strip().casefold() != "unresolved"
    ]
    if not str(local.get("selected_hypothesis_id", "") or "").strip() and len(supported) == 1:
        local["selected_hypothesis_id"] = str(supported[0].get("hypothesis_id", "") or "").strip()

    coverage = diagnosis.get("causal_coverage")
    coverage = coverage if isinstance(coverage, dict) else {}
    residual_requirement_ids = _string_items(coverage.get("residual_requirement_ids", []))
    if not residual_requirement_ids:
        residual_requirement_ids = []
        for item in unresolved:
            for requirement_id in _string_items(item.get("explains_requirement_ids", [])):
                if requirement_id not in residual_requirement_ids:
                    residual_requirement_ids.append(requirement_id)
    local_coverage = dict(coverage)
    local_unexplained = _string_items(local_coverage.get("unexplained_observations", []))
    local_unexplained.append(
        "Residual investigation hypotheses remain unresolved: "
        + ", ".join(str(item.get("hypothesis_id", "") or "") for item in unresolved)
    )
    local_coverage["unexplained_observations"] = list(dict.fromkeys(local_unexplained))
    local_coverage["sufficiency_status"] = "local_contributor"
    local["causal_coverage"] = local_coverage
    if evidence_status == "confirmed":
        local["confidence"] = "medium"
    if not residual_requirement_ids:
        # An unresolved alternative is not the same thing as an unexplained
        # failed requirement. Keep it in causal_coverage for audit/refinement,
        # but do not put it back into the executable local Issue.
        return [local]
    cluster = {
        "failed_checks": residual_requirement_ids,
        "observable_behavior": (
            "; ".join(_string_items(coverage.get("unexplained_observations", [])))
            or "residual failed requirements remain causally unresolved"
        ),
    }
    unresolved_ids = [str(item.get("hypothesis_id", "") or "") for item in unresolved]
    residual = {
        **diagnosis,
        "issue_category": "unassigned",
        "severity": "low",
        "summary": "Residual causal alternatives remain unresolved after a supported local finding.",
        "failure_mode": f"{str(diagnosis.get('failure_mode', '') or 'causal_gap')}_unresolved_residual",
        "failure_cluster": cluster,
        "evidence_status": "insufficient",
        "competing_hypotheses": unresolved_ids,
        "discriminating_evidence": str(diagnosis.get("discriminating_evidence", "") or ""),
        "root_cause": "The available evidence has not separated the residual causal alternatives.",
        "critical_mistake": "No additional causal decision is established by the current evidence.",
        "general_mechanism": "Keep unresolved causal alternatives unassigned until a discriminator is observed.",
        "target_ref": "unassigned",
        "selected_hypothesis_id": "",
        "evidence_refs": [],
        "affected_components": [],
        "recommendation": "Acquire the missing discriminator before assigning another Harness change.",
        "causal_coverage": {
            "explained_requirement_ids": [],
            "residual_requirement_ids": residual_requirement_ids,
            "unexplained_observations": ["Unresolved investigation hypotheses: " + ", ".join(unresolved_ids)],
            "causal_chain": [
                {
                    "cause": "unresolved causal alternative",
                    "effect": str(cluster.get("observable_behavior", "") or "failed requirement remains unexplained"),
                    "evidence_status": "unknown",
                    "evidence_refs": [],
                }
            ],
            "counterfactual_prediction": (
                "No additional behavior prediction is justified until the residual hypotheses are distinguished."
            ),
            "sufficiency_status": "unknown",
        },
        "decision_contract": {
            "wrong_decision": "No additional wrong decision is established.",
            "causal_distinction": "The missing discriminator separates the residual hypotheses.",
            "required_action": "Collect the missing discriminator.",
            "acceptance_observable": "Each residual hypothesis is supported or falsified by available evidence.",
            "scope_boundary": ["Do not modify a Harness surface while attribution remains unresolved."],
            "activation_phase": "during_investigation",
        },
        "hypothesis_assessment": unresolved,
        "confidence": "low",
    }
    return [local, residual]


def _diagnosis_has_semantic_content(diagnosis: dict[str, Any]) -> bool:
    """Reject JSON-shaped placeholders before they bypass the repair turn."""
    target_ref = str(diagnosis.get("target_ref", "") or "").strip()
    if not target_ref:
        return False
    for key in (
        "summary",
        "failure_mode",
        "root_cause",
        "critical_mistake",
        "general_mechanism",
        "recommendation",
    ):
        if str(diagnosis.get(key, "") or "").strip():
            return True
    return False


def _candidate_feedback_check_sets(feedback: dict[str, Any] | None) -> dict[str, set[str]]:
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
    checks: set[str] = set()
    for key in keys:
        for value in _string_items(payload.get(key, [])):
            normalized = _normalize_cluster_text(value)
            if normalized:
                checks.add(normalized)
    return checks


def _diagnosis_feedback_priority(
    diagnosis: dict[str, Any],
    *,
    feedback_sets: dict[str, set[str]],
) -> tuple[int, bool]:
    checks: set[str] = set()
    for value in _diagnosis_failed_checks(diagnosis):
        normalized = _normalize_cluster_text(value)
        if normalized:
            checks.add(normalized)
    if checks & feedback_sets["regressed"]:
        return 0, False
    if checks & feedback_sets["remaining"]:
        return 1, False
    fixed_only = bool(checks and checks <= feedback_sets["fixed"])
    return 2, fixed_only


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


def _diagnoses_are_duplicate(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_checks = {_normalize_cluster_text(value) for value in _diagnosis_failed_checks(left)}
    right_checks = {_normalize_cluster_text(value) for value in _diagnosis_failed_checks(right)}
    if left_checks and right_checks and left_checks != right_checks:
        return False

    left_target = _normalize_target_ref(left.get("target_ref", ""))
    right_target = _normalize_target_ref(right.get("target_ref", ""))
    if left_target != right_target:
        return False

    left_mode = _normalize_cluster_text(left.get("failure_mode", ""))
    right_mode = _normalize_cluster_text(right.get("failure_mode", ""))
    if left_mode and right_mode and left_mode != right_mode:
        left_mistake = _normalize_cluster_text(left.get("critical_mistake", ""))
        right_mistake = _normalize_cluster_text(right.get("critical_mistake", ""))
        if left_mistake and right_mistake and not _cluster_texts_are_similar(left_mistake, right_mistake):
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
    return left_mode == right_mode


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
            "evidence_status": item.get("evidence_status", ""),
            "failed_requirement": _truncate_text(item.get("failed_requirement", ""), 500),
            "competing_hypotheses": _bounded_structured_value(
                item.get("competing_hypotheses", []),
                1000,
            ),
            "discriminating_evidence": _truncate_text(
                item.get("discriminating_evidence", ""),
                500,
            ),
            "selected_hypothesis_id": item.get("selected_hypothesis_id", ""),
            "selected_hypothesis_semantic_id": item.get("selected_hypothesis_semantic_id", ""),
            "root_cause": _truncate_text(item.get("root_cause", ""), 500),
            "critical_mistake": _truncate_text(item.get("critical_mistake", ""), 500),
            "general_mechanism": _truncate_text(item.get("general_mechanism", ""), 500),
            "decision_contract": _bounded_structured_value(
                item.get("decision_contract", {}),
                1400,
            ),
            "decision_ground_audit": _bounded_structured_value(
                item.get("decision_ground_audit", []),
                1800,
            ),
            "causal_coverage": _bounded_structured_value(
                item.get("causal_coverage", {}),
                2400,
            ),
            "hypothesis_assessment": _bounded_structured_value(
                item.get("hypothesis_assessment", []),
                1800,
            ),
            "prior_experiment_assessment": _bounded_structured_value(
                item.get("prior_experiment_assessment", {}),
                1200,
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
        affected_components: list[str] = []
        for item in items:
            for component in _string_items(item.get("affected_components", [])):
                if component not in affected_components:
                    affected_components.append(component)
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
                        "evidence_status": str(strongest.get("evidence_status", "") or ""),
                        "failed_requirement": str(strongest.get("failed_requirement", "") or ""),
                        "competing_hypotheses": list(
                            strongest.get("competing_hypotheses", [])
                            if isinstance(strongest.get("competing_hypotheses"), list)
                            else []
                        ),
                        "discriminating_evidence": str(strongest.get("discriminating_evidence", "") or ""),
                        "selected_hypothesis_id": str(strongest.get("selected_hypothesis_id", "") or ""),
                        "selected_hypothesis_semantic_id": str(
                            strongest.get("selected_hypothesis_semantic_id", "") or ""
                        ),
                        "root_cause": str(strongest.get("root_cause", "") or ""),
                        "critical_mistake": str(strongest.get("critical_mistake", "") or ""),
                        "general_mechanism": str(strongest.get("general_mechanism", "") or ""),
                        "decision_contract": dict(
                            strongest.get("decision_contract", {})
                            if isinstance(strongest.get("decision_contract"), dict)
                            else {}
                        ),
                        "causal_coverage": dict(
                            strongest.get("causal_coverage", {})
                            if isinstance(strongest.get("causal_coverage"), dict)
                            else {}
                        ),
                        "hypothesis_assessment": list(
                            strongest.get("hypothesis_assessment", [])
                            if isinstance(strongest.get("hypothesis_assessment"), list)
                            else []
                        ),
                        "prior_experiment_assessment": dict(
                            strongest.get("prior_experiment_assessment", {})
                            if isinstance(strongest.get("prior_experiment_assessment"), dict)
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
    output_format_failure = isinstance(exc, _DiagnosisOutputFormatError)
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
        "general_mechanism": (
            "The diagnosis model exhausted bounded JSON-format repair."
            if output_format_failure
            else "Retryable model-service failure during analyzer diagnosis."
        ),
        "target_ref": "unassigned",
        "evidence_refs": [],
        "affected_components": [],
        "recommendation": "Do not optimize from this case-level diagnosis; rerun analysis or use other case diagnoses.",
        "confidence": "low",
        "diagnosis_error_type": "output_format" if output_format_failure else "model_service",
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
            is_root = Path(_without_windows_long_path(directory)).resolve() == workspace_root
        except OSError:
            is_root = False
        if is_root:
            ignored.update(name for name in names if name in _DIAGNOSIS_ROOT_RUNTIME_DIRS)
        return ignored

    try:
        shutil.copytree(
            _windows_long_path(workspace_dir),
            _windows_long_path(repository_dir),
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


def _windows_long_path(path: Path) -> str:
    """Return an extended Windows path so snapshot copying survives deep run dirs."""
    resolved = str(path.resolve(strict=False))
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "".join(("\\\\?\\UNC\\", resolved.lstrip("\\")))
    return "".join(("\\\\?\\", resolved))


def _without_windows_long_path(path: str | Path) -> str:
    raw = str(path)
    if raw.startswith("\\\\?\\UNC\\"):
        return "".join(("\\\\", raw[8:]))
    if raw.startswith("\\\\?\\"):
        return raw[4:]
    return raw


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


def _build_causal_evidence_digest(case: CaseAnalysisInput) -> dict[str, Any]:
    """Build one decision-centered digest from the case's canonical artifacts."""
    case_dir = Path(case.result_path).parent
    trace_data = _read_json_if_exists(case_dir / "judge" / "normalized_trace.json")
    task_contract = load_public_task_contract(
        case_id=case.case_id,
        result_path=case.result_path,
        evaluation_metadata=case.evaluation_metadata,
        task_input=case.input,
    )
    return build_causal_evidence_digest(
        case_id=case.case_id,
        task_input=case.input,
        response=case.response,
        evaluation_passed=case.evaluation_passed,
        evaluation_score=case.score,
        evaluation_reason=case.evaluation_reason,
        evaluation_metadata=case.evaluation_metadata,
        trace_data=trace_data,
        task_contract=task_contract,
    )


def _build_evidence_summary(case: CaseAnalysisInput) -> str:
    """Build an audit summary with deterministic validation and causal evidence."""
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
    criteria = judge_breakdown.get("criteria", []) if isinstance(judge_breakdown, dict) else []
    if isinstance(criteria, list) and criteria:
        lines.extend(["", "## Authoritative Judge Criteria"])
        for criterion in criteria[:24]:
            if not isinstance(criterion, dict):
                continue
            lines.append(
                "- "
                f"criterion_id={_one_line(criterion.get('criterion_id', ''), 120)} "
                f"score={criterion.get('score')} "
                f"status={_one_line(criterion.get('status', ''), 80)} "
                f"rationale={_one_line(criterion.get('rationale', ''), 900)}"
            )
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

    causal_digest = _build_causal_evidence_digest(case)
    evidence_index = _causal_digest_evidence_index(causal_digest)
    lines.extend(
        [
            "",
            "## Decision-Centered Execution Evidence",
            "- provenance: deterministic compression of the evaluated normalized trajectory.",
            (
                "- Trial boundaries, selected exact requests, public tool schemas, and raw evidence pointers are "
                "preserved."
            ),
            (
                "- Repeated narration and duplicate payloads are removed; this section does not add model-authored "
                "conclusions."
            ),
            *evidence_index,
            "```json",
            json.dumps(causal_digest, ensure_ascii=False, indent=2),
            "```",
        ]
    )

    return "\n".join(lines).strip() + "\n"


def _causal_digest_evidence_index(digest: dict[str, Any]) -> list[str]:
    """Render a tiny human-readable pointer index beside the structured digest."""
    lines = ["", "### Selected Evidence Index"]
    for trial in digest.get("trials", []):
        if not isinstance(trial, dict):
            continue
        trace_id = str(trial.get("trial_id", ""))
        role = str(trial.get("role", ""))
        for action in trial.get("selected_actions", []):
            if not isinstance(action, dict):
                continue
            lines.append(
                "- "
                f"trace_id={trace_id} role={role} "
                f"message_index={action.get('message_index', '')} "
                f"step={action.get('step_pointer', '')} "
                f"tool={action.get('tool', '')} "
                f"error={_one_line(action.get('error', ''), 300)}"
            )
        final_output = trial.get("final_output")
        final_output = final_output if isinstance(final_output, dict) else {}
        excerpt = _one_line(final_output.get("excerpt", ""), 500)
        if excerpt:
            reference = final_output.get("evidence_ref")
            reference = reference if isinstance(reference, dict) else {}
            lines.append(
                "- "
                f"trace_id={trace_id} role={role} "
                f"message_index={reference.get('message_index', '')} "
                f"step={reference.get('step_pointer', '')} "
                f"final_output={excerpt}"
            )
    if len(lines) == 2:
        lines.append("- No normalized execution event was available.")
    return lines


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
        return [str(item) for item in failures if str(item).strip()]

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


def _failed_requirement_ids(inventory: dict[str, Any] | None) -> set[str]:
    if not isinstance(inventory, dict):
        return set()
    items = inventory.get("items")
    if not isinstance(items, list):
        return set()
    failed_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        requirement_id = str(item.get("requirement_id") or "").strip()
        evidence_status = str(item.get("evidence_status") or "").strip()
        if requirement_id and evidence_status != "failed_detail_unavailable":
            failed_ids.add(requirement_id)
    return failed_ids


def _causal_coverage_validation_conflicts(
    diagnosis: dict[str, Any],
    *,
    failed_requirement_inventory: dict[str, Any] | None,
    evidence_status: str,
) -> list[str]:
    """Require a falsifiable causal chain and complete failure accounting."""
    required_ids = _failed_requirement_ids(failed_requirement_inventory)
    if not required_ids:
        return []

    coverage = diagnosis.get("causal_coverage")
    if not isinstance(coverage, dict):
        return ["missing causal_coverage for authoritative failed-requirement inventory"]

    explained = set(_string_items(coverage.get("explained_requirement_ids", [])))
    residual = set(_string_items(coverage.get("residual_requirement_ids", [])))
    errors: list[str] = []
    unknown_ids = sorted((explained | residual) - required_ids)
    overlap = sorted(explained & residual)
    if unknown_ids:
        errors.append("causal_coverage uses unknown requirement IDs: " + ", ".join(unknown_ids))
    if overlap:
        errors.append("causal_coverage IDs cannot be both explained and residual: " + ", ".join(overlap))

    cluster_ids = set(_string_items(_diagnosis_failure_cluster(diagnosis).get("failed_checks", [])))
    if not cluster_ids:
        errors.append("failure_cluster.failed_checks must name at least one inventory ID")
    elif not cluster_ids <= required_ids:
        errors.append("failure_cluster.failed_checks contains IDs outside the authoritative inventory")
    if evidence_status == "insufficient":
        if explained:
            errors.append("insufficient diagnosis cannot claim failed requirements as causally explained")
        if cluster_ids and not cluster_ids <= residual:
            errors.append("insufficient diagnosis must keep its clustered failed checks residual")
    elif cluster_ids and explained != cluster_ids:
        errors.append(
            "causal_coverage.explained_requirement_ids must exactly match this diagnosis's "
            "failure_cluster.failed_checks"
        )

    counterfactual = str(coverage.get("counterfactual_prediction") or "").strip()
    if not counterfactual:
        errors.append("causal_coverage.counterfactual_prediction must be non-empty")

    chain = coverage.get("causal_chain")
    if not isinstance(chain, list) or not chain:
        errors.append("causal_coverage.causal_chain must contain at least one edge")
        chain = []
    has_unknown_edge = False
    for index, edge in enumerate(chain, start=1):
        if not isinstance(edge, dict):
            errors.append(f"causal_coverage.causal_chain[{index}] must be a mapping")
            continue
        if not str(edge.get("cause") or "").strip() or not str(edge.get("effect") or "").strip():
            errors.append(f"causal_coverage.causal_chain[{index}] must name cause and effect")
        edge_status = str(edge.get("evidence_status") or "").strip().lower()
        if edge_status not in {"observed", "supported", "unknown"}:
            errors.append(
                f"causal_coverage.causal_chain[{index}].evidence_status must be observed, supported, or unknown"
            )
        has_unknown_edge = has_unknown_edge or edge_status == "unknown"

    sufficiency = str(coverage.get("sufficiency_status") or "").strip().lower()
    valid_sufficiency = {
        "task_sufficient",
        "cluster_sufficient",
        "local_contributor",
        "unknown",
    }
    if sufficiency not in valid_sufficiency:
        errors.append(
            "causal_coverage.sufficiency_status must be task_sufficient, "
            "cluster_sufficient, local_contributor, or unknown"
        )
    unexplained = _string_items(coverage.get("unexplained_observations", []))
    incomplete_task_coverage = bool(residual) or bool(unexplained) or explained != required_ids
    incomplete_task_scope = cluster_ids != required_ids
    if sufficiency == "task_sufficient" and (incomplete_task_coverage or incomplete_task_scope):
        errors.append(
            "task_sufficient requires all inventory IDs in this diagnosis's failure cluster and explained, "
            "with no residual IDs or unexplained observations"
        )
    if sufficiency == "cluster_sufficient" and cluster_ids != explained:
        errors.append("cluster_sufficient requires exactly every clustered failed check to be explained")
    if sufficiency == "local_contributor" and not (residual or unexplained):
        errors.append("local_contributor must name a residual requirement or unexplained observation")
    if sufficiency == "unknown" and evidence_status == "confirmed":
        errors.append("confirmed evidence cannot have unknown causal sufficiency")
    if evidence_status == "confirmed" and has_unknown_edge:
        errors.append("confirmed evidence cannot contain an unknown causal-chain edge")
    if evidence_status == "insufficient" and sufficiency != "unknown":
        errors.append("insufficient evidence must use causal sufficiency_status=unknown")
    return errors


def _case_diagnoses_validation_conflicts(
    diagnoses: list[dict[str, Any]],
    inventory: dict[str, Any],
    verifier_inventory: dict[str, Any] | None = None,
    failed_requirement_inventory: dict[str, Any] | None = None,
) -> list[str]:
    """Validate every diagnosis while retaining its position in repair feedback."""
    conflicts: list[str] = []
    for index, diagnosis in enumerate(diagnoses, start=1):
        diagnosis_conflicts = _diagnosis_validation_conflicts(
            diagnosis,
            inventory,
            verifier_inventory,
            failed_requirement_inventory=failed_requirement_inventory,
        )
        for error in diagnosis_conflicts:
            conflicts.append(f"diagnosis[{index}]: {error}")
    required_ids = _failed_requirement_ids(failed_requirement_inventory)
    if required_ids:
        clustered_ids: set[str] = set()
        explained_ids: set[str] = set()
        residual_ids: set[str] = set()
        for diagnosis in diagnoses:
            cluster = _diagnosis_failure_cluster(diagnosis)
            clustered_ids.update(_string_items(cluster.get("failed_checks", [])))
            coverage = diagnosis.get("causal_coverage")
            if not isinstance(coverage, dict):
                continue
            explained_ids.update(_string_items(coverage.get("explained_requirement_ids", [])))
            residual_ids.update(_string_items(coverage.get("residual_requirement_ids", [])))
        missing = sorted(required_ids - clustered_ids)
        unknown = sorted(clustered_ids - required_ids)
        if missing:
            conflicts.append("diagnosis set omitted failed requirement IDs: " + ", ".join(missing))
        if unknown:
            conflicts.append(
                "diagnosis set used failed_checks outside the authoritative inventory: " + ", ".join(unknown)
            )
        missing_coverage = sorted(required_ids - explained_ids - residual_ids)
        contradictory_coverage = sorted(explained_ids & residual_ids)
        if missing_coverage:
            conflicts.append(
                "diagnosis set omitted causal coverage for failed requirement IDs: " + ", ".join(missing_coverage)
            )
        if contradictory_coverage:
            conflicts.append(
                "diagnosis set marked failed requirement IDs both explained and residual: "
                + ", ".join(contradictory_coverage)
            )
    return conflicts


def _compatible_evidence_requests(
    investigation: dict[str, Any],
) -> dict[str, set[str]]:
    """Allow discriminating evidence across hypotheses for the same failure.

    A request planned for one alternative often supplies the falsifier for its
    competitor.  Sharing it within an overlapping failed-requirement cluster is
    causally valid; sharing it across unrelated clusters is not.
    """
    hypotheses = {
        str(item.get("hypothesis_id", "") or ""): set(_string_items(item.get("explains_requirement_ids", [])))
        for item in investigation.get("hypotheses", [])
        if isinstance(item, dict) and str(item.get("hypothesis_id", "") or "")
    }
    compatible: dict[str, set[str]] = {hypothesis_id: set() for hypothesis_id in hypotheses}
    for request in investigation.get("evidence_requests", []):
        if not isinstance(request, dict):
            continue
        request_id = str(request.get("request_id", "") or "")
        if not request_id:
            continue
        scoped_ids = {
            hypothesis_id
            for hypothesis_id in _string_items(request.get("hypothesis_ids", []))
            if hypothesis_id in hypotheses
        }
        scoped_requirements: set[str] = set()
        for hypothesis_id in scoped_ids:
            scoped_requirements.update(hypotheses[hypothesis_id])
        for hypothesis_id, requirement_ids in hypotheses.items():
            if hypothesis_id in scoped_ids or (requirement_ids and requirement_ids & scoped_requirements):
                compatible[hypothesis_id].add(request_id)
    return compatible


def _causal_investigation_conflicts(
    diagnoses: list[dict[str, Any]],
    investigation: dict[str, Any],
    *,
    evidence_results: dict[str, Any],
    prior_candidate_feedback: dict[str, Any] | None,
) -> list[str]:
    """Require final diagnosis to account for planned alternatives and experiments."""
    planned = {
        str(item.get("hypothesis_id", "") or "")
        for item in investigation.get("hypotheses", [])
        if isinstance(item, dict) and str(item.get("hypothesis_id", "") or "")
    }
    compatible_requests = _compatible_evidence_requests(investigation)
    request_operations: dict[str, str] = {}
    for request in investigation.get("evidence_requests", []):
        if not isinstance(request, dict):
            continue
        request_id = str(request.get("request_id", "") or "")
        if request_id:
            request_operations[request_id] = str(request.get("operation", "") or "")
    request_availability = {
        str(item.get("request_id", "") or ""): str(item.get("availability", "") or "").casefold()
        for item in evidence_results.get("results", [])
        if isinstance(item, dict) and str(item.get("request_id", "") or "")
    }
    hypotheses_by_id = {
        str(item.get("hypothesis_id", "") or ""): item
        for item in investigation.get("hypotheses", [])
        if isinstance(item, dict) and str(item.get("hypothesis_id", "") or "")
    }
    assessed: dict[str, set[str]] = {}
    all_assessed_ids: set[str] = set()
    assessment_rows: list[tuple[int, dict[str, Any]]] = []
    for diagnosis_index, diagnosis in enumerate(diagnoses, start=1):
        values = diagnosis.get("hypothesis_assessment", [])
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            hypothesis_id = str(item.get("hypothesis_id", "") or "")
            status = str(item.get("status", "") or "").strip().casefold()
            if hypothesis_id:
                all_assessed_ids.add(hypothesis_id)
            if hypothesis_id in planned and status in {"supported", "falsified", "unresolved"}:
                assessed.setdefault(hypothesis_id, set()).add(status)
                assessment_rows.append((diagnosis_index, item))

    conflicts: list[str] = []
    missing = sorted(planned - set(assessed))
    if missing:
        conflicts.append("causal investigation hypotheses were not assessed: " + ", ".join(missing))
    extra = sorted(all_assessed_ids - planned)
    if extra:
        conflicts.append("final diagnosis introduced unplanned causal hypotheses: " + ", ".join(extra))
    for diagnosis_index, assessment in assessment_rows:
        hypothesis_id = str(assessment.get("hypothesis_id", "") or "")
        status = str(assessment.get("status", "") or "").strip().casefold()
        falsifying_status = str(assessment.get("falsifying_condition_status", "") or "").strip().casefold()
        follows = str(assessment.get("claim_follows_from_evidence", "") or "").strip().casefold()
        prefix = f"diagnosis[{diagnosis_index}] hypothesis {hypothesis_id}"
        if falsifying_status not in {"observed", "not_observed", "unknown"}:
            conflicts.append(f"{prefix}: falsifying_condition_status was not assessed")
        if follows not in {"yes", "no", "unknown"}:
            conflicts.append(f"{prefix}: claim_follows_from_evidence was not assessed")
        if not str(assessment.get("logic_check", "") or "").strip():
            conflicts.append(f"{prefix}: missing explicit logic_check")
        cited_requests = set(_string_items(assessment.get("controller_request_ids", [])))
        unknown_requests = sorted(cited_requests - set(request_availability))
        if unknown_requests:
            conflicts.append(f"{prefix}: cited unknown controller requests: {', '.join(unknown_requests)}")
        unavailable_requests = sorted(
            request_id for request_id in cited_requests if request_availability.get(request_id) != "available"
        )
        if unavailable_requests:
            conflicts.append(
                f"{prefix}: treated unavailable controller requests as evidence: {', '.join(unavailable_requests)}"
            )
        out_of_scope_requests = sorted(cited_requests - compatible_requests.get(hypothesis_id, set()))
        if out_of_scope_requests:
            conflicts.append(
                f"{prefix}: cited controller requests outside its causal requirement cluster: "
                + ", ".join(out_of_scope_requests)
            )
        available_scoped_requests = {
            request_id
            for request_id in cited_requests & compatible_requests.get(hypothesis_id, set())
            if request_availability.get(request_id) == "available"
        }
        if status == "supported" and falsifying_status != "not_observed":
            conflicts.append(f"{prefix}: supported claim requires its falsifying condition to be not_observed")
        if status == "supported" and follows != "yes":
            conflicts.append(f"{prefix}: supported claim must follow from the controller evidence")
        if status == "supported" and str(assessment.get("evidence_relation", "") or "").strip().casefold() != (
            "direct_claim"
        ):
            conflicts.append(f"{prefix}: supported claim lacks a direct claim-evidence relation")
        if status == "supported" and str(assessment.get("evidence_independence", "") or "").strip().casefold() not in {
            "independent",
            "direct_observation",
        }:
            conflicts.append(f"{prefix}: supported claim relies on non-independent evidence")
        if status == "supported" and not available_scoped_requests:
            conflicts.append(f"{prefix}: supported claim cites no available hypothesis-scoped controller request")
        if (
            status == "supported"
            and available_scoped_requests
            and not any(
                request_operations.get(request_id) in _DECISIVE_CONTROLLER_OPERATIONS
                for request_id in available_scoped_requests
            )
        ):
            conflicts.append(f"{prefix}: supported claim cites discovery evidence without an exact follow-up probe")
        if status == "supported" and bool(hypotheses_by_id.get(hypothesis_id, {}).get("numeric_change_check_required")):
            available_delta_checks: set[str] = set()
            for request_id in compatible_requests.get(hypothesis_id, set()):
                operation_matches = request_operations.get(request_id) == "compare_numeric_change"
                request_available = request_availability.get(request_id) == "available"
                if operation_matches and request_available:
                    available_delta_checks.add(request_id)
            if not available_delta_checks:
                conflicts.append(f"{prefix}: numeric-change claim lacks an available before-versus-after delta check")
        if status == "falsified" and falsifying_status != "observed" and follows != "no":
            conflicts.append(f"{prefix}: falsified claim requires an observed falsifier or failed entailment")
        if status == "falsified":
            relation = str(assessment.get("evidence_relation", "") or "").strip().casefold()
            independence = str(assessment.get("evidence_independence", "") or "").strip().casefold()
            if not available_scoped_requests:
                conflicts.append(f"{prefix}: falsified claim cites no available hypothesis-scoped controller request")
            if relation != "direct_falsifier":
                conflicts.append(f"{prefix}: cited evidence does not directly entail the pre-registered falsifier")
            if independence not in {"independent", "direct_observation"}:
                conflicts.append(f"{prefix}: falsifier is not independent of the questioned mechanism")
    for index, diagnosis in enumerate(diagnoses, start=1):
        assessment_statuses = {
            str(item.get("hypothesis_id", "") or "").strip(): str(item.get("status", "") or "").strip().casefold()
            for item in diagnosis.get("hypothesis_assessment", [])
            if isinstance(item, dict) and str(item.get("hypothesis_id", "") or "").strip()
        }
        statuses = set(assessment_statuses.values())
        evidence_status = str(diagnosis.get("evidence_status", "") or "").strip().casefold()
        target_ref = str(diagnosis.get("target_ref", "") or "").strip().casefold()
        selected_hypothesis_id = str(diagnosis.get("selected_hypothesis_id", "") or "").strip()
        assigned = target_ref not in {"", "unassigned"}
        if assigned:
            if not selected_hypothesis_id:
                conflicts.append(f"diagnosis[{index}]: assigned diagnosis must name selected_hypothesis_id")
            elif selected_hypothesis_id not in planned:
                conflicts.append(
                    f"diagnosis[{index}]: selected_hypothesis_id is not in the investigation: {selected_hypothesis_id}"
                )
            elif assessment_statuses.get(selected_hypothesis_id) != "supported":
                conflicts.append(
                    f"diagnosis[{index}]: selected_hypothesis_id must have status=supported: {selected_hypothesis_id}"
                )
            selected_hypothesis = hypotheses_by_id.get(selected_hypothesis_id, {})
            causal_handoff = {
                "selected_hypothesis_claim": selected_hypothesis.get("claim", ""),
                "selected_hypothesis_falsified_if": selected_hypothesis.get("falsified_if", ""),
                "root_cause": diagnosis.get("root_cause", ""),
                "general_mechanism": diagnosis.get("general_mechanism", ""),
                "recommendation": diagnosis.get("recommendation", ""),
                "decision_contract": diagnosis.get("decision_contract", {}),
            }
            if _contains_evaluator_outcome_dependency(causal_handoff):
                conflicts.append(
                    f"diagnosis[{index}]: causal handoff depends on evaluator-owned expected, "
                    "target, gold, or reference outcomes"
                )
        elif selected_hypothesis_id:
            conflicts.append(f"diagnosis[{index}]: unassigned diagnosis must leave selected_hypothesis_id empty")
        if evidence_status == "confirmed":
            if "supported" not in statuses:
                conflicts.append(f"diagnosis[{index}]: confirmed diagnosis has no supported investigation hypothesis")
            if "unresolved" in statuses:
                conflicts.append(f"diagnosis[{index}]: confirmed diagnosis retains an unresolved material hypothesis")
        if "unresolved" in statuses and "supported" not in statuses and target_ref not in {"", "unassigned"}:
            conflicts.append(
                f"diagnosis[{index}]: unresolved hypotheses without a supported local mechanism "
                "require target_ref=unassigned"
            )

    selected_supported_ids: set[str] = set()
    for diagnosis in diagnoses:
        target_ref = _normalize_target_ref(diagnosis.get("target_ref", ""))
        hypothesis_id = str(diagnosis.get("selected_hypothesis_id", "") or "").strip()
        if target_ref not in {"", "unassigned"} and hypothesis_id:
            selected_supported_ids.add(hypothesis_id)
    supported_rows_by_id: dict[str, list[dict[str, Any]]] = {}
    for _, assessment in assessment_rows:
        hypothesis_id = str(assessment.get("hypothesis_id", "") or "").strip()
        if hypothesis_id and str(assessment.get("status", "") or "").strip().casefold() == "supported":
            supported_rows_by_id.setdefault(hypothesis_id, []).append(assessment)
    for hypothesis_id, supported_rows in sorted(supported_rows_by_id.items()):
        if hypothesis_id in selected_supported_ids:
            continue
        explicitly_disposed = False
        for item in supported_rows:
            disposition = str(item.get("handoff_disposition", "") or "").strip().casefold()
            has_reason = bool(str(item.get("handoff_reason", "") or "").strip())
            if disposition in {"subsumed", "non_actionable"} and has_reason:
                explicitly_disposed = True
                break
        if not explicitly_disposed:
            conflicts.append(f"supported causal hypothesis was not handed off or explicitly disposed: {hypothesis_id}")

    experiments = prior_candidate_feedback.get("experiments", []) if isinstance(prior_candidate_feedback, dict) else []
    # ``h1``/``h2`` labels are local to one investigation.  Cross-round causal
    # feedback must therefore compare semantic identities, never those labels.
    prior_source_hypothesis_semantic_ids: set[str] = set()
    experiment_rows = experiments if isinstance(experiments, list) else []
    has_recorded_prediction = False
    for record in experiment_rows:
        if not isinstance(record, dict):
            continue
        has_recorded_prediction = has_recorded_prediction or bool(record.get("causal_intervention_contracts"))
        for contract in record.get("causal_intervention_contracts", []):
            if not isinstance(contract, dict):
                continue
            source_semantic_id = str(contract.get("source_causal_hypothesis_semantic_id", "") or "").strip()
            if source_semantic_id:
                prior_source_hypothesis_semantic_ids.add(source_semantic_id)
            prior_source_hypothesis_semantic_ids.update(
                _string_items(contract.get("source_causal_hypothesis_semantic_ids", []))
            )
    if has_recorded_prediction:
        planned_semantics: dict[str, str] = {}
        for item in investigation.get("hypotheses", []):
            if not isinstance(item, dict):
                continue
            hypothesis_id = str(item.get("hypothesis_id", "") or "").strip()
            if hypothesis_id:
                planned_semantics[hypothesis_id] = str(item.get("hypothesis_semantic_id", "") or "").strip()
        for index, diagnosis in enumerate(diagnoses, start=1):
            diagnosis_semantics: set[str] = set()
            for item in diagnosis.get("hypothesis_assessment", []):
                if not isinstance(item, dict):
                    continue
                status = str(item.get("status", "") or "").strip().casefold()
                if status not in {"supported", "falsified"}:
                    continue
                hypothesis_id = str(item.get("hypothesis_id", "") or "").strip()
                semantic_id = planned_semantics.get(hypothesis_id, "")
                if semantic_id:
                    diagnosis_semantics.add(semantic_id)
            if prior_source_hypothesis_semantic_ids and not diagnosis_semantics.intersection(
                prior_source_hypothesis_semantic_ids
            ):
                continue
            assessment = diagnosis.get("prior_experiment_assessment")
            if not isinstance(assessment, dict) or assessment.get("availability") != "available":
                conflicts.append(f"diagnosis[{index}]: paired causal experiment was not assessed")
                continue
            status = str(assessment.get("causal_hypothesis_status", "") or "").strip().casefold()
            if status not in {"supported", "falsified", "not_tested", "inconclusive"}:
                conflicts.append(f"diagnosis[{index}]: invalid paired causal hypothesis status")
            if status == "falsified" and prior_source_hypothesis_semantic_ids:
                newly_supported_semantics: set[str] = set()
                for item in diagnosis.get("hypothesis_assessment", []):
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("status", "") or "").strip().casefold() != "supported":
                        continue
                    hypothesis_id = str(item.get("hypothesis_id", "") or "").strip()
                    semantic_id = planned_semantics.get(hypothesis_id, "")
                    if semantic_id:
                        newly_supported_semantics.add(semantic_id)
                reused = sorted(newly_supported_semantics & prior_source_hypothesis_semantic_ids)
                if reused:
                    conflicts.append(
                        "diagnosis["
                        f"{index}]: falsified prior causal hypotheses were reused by semantic identity: "
                        f"{', '.join(reused)}"
                    )
    return conflicts


def _attach_hypothesis_semantics(
    diagnoses: list[dict[str, Any]],
    investigation: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Persist semantic causal identity after model-facing local IDs are resolved."""
    if not isinstance(investigation, dict):
        return diagnoses
    planned = {
        str(item.get("hypothesis_id", "") or "").strip(): item
        for item in investigation.get("hypotheses", [])
        if isinstance(item, dict) and str(item.get("hypothesis_id", "") or "").strip()
    }
    annotated: list[dict[str, Any]] = []
    for raw_diagnosis in diagnoses:
        diagnosis = dict(raw_diagnosis)
        assessments: list[dict[str, Any]] = []
        for raw_assessment in diagnosis.get("hypothesis_assessment", []):
            if not isinstance(raw_assessment, dict):
                continue
            assessment = dict(raw_assessment)
            hypothesis = planned.get(str(assessment.get("hypothesis_id", "") or "").strip())
            if hypothesis is not None:
                claim = str(hypothesis.get("claim", "") or "")
                falsified_if = str(hypothesis.get("falsified_if", "") or "")
                semantic_id = str(hypothesis.get("hypothesis_semantic_id", "") or "")
                if not semantic_id and claim and falsified_if:
                    semantic_id = causal_hypothesis_semantic_id(claim, falsified_if)
                if semantic_id:
                    assessment["hypothesis_semantic_id"] = semantic_id
                if claim:
                    assessment["claim"] = claim
                if falsified_if:
                    assessment["falsified_if"] = falsified_if
            assessments.append(assessment)
        diagnosis["hypothesis_assessment"] = assessments
        selected_hypothesis_id = str(diagnosis.get("selected_hypothesis_id", "") or "").strip()
        selected = planned.get(selected_hypothesis_id)
        if selected is not None:
            selected_semantic_id = str(selected.get("hypothesis_semantic_id", "") or "").strip()
            if not selected_semantic_id:
                claim = str(selected.get("claim", "") or "")
                falsified_if = str(selected.get("falsified_if", "") or "")
                if claim and falsified_if:
                    selected_semantic_id = causal_hypothesis_semantic_id(claim, falsified_if)
            if selected_semantic_id:
                diagnosis["selected_hypothesis_semantic_id"] = selected_semantic_id
        elif not selected_hypothesis_id:
            diagnosis.pop("selected_hypothesis_semantic_id", None)
        annotated.append(diagnosis)
    return annotated


def _assigned_diagnosis_indices(diagnoses: list[dict[str, Any]]) -> list[int]:
    return [
        index
        for index, diagnosis in enumerate(diagnoses, start=1)
        if _normalize_target_ref(diagnosis.get("target_ref", "")) not in {"", "unassigned"}
    ]


def _compact_causal_handoff_audit_diagnosis(
    *,
    diagnosis_index: int,
    diagnosis: dict[str, Any],
    investigation: dict[str, Any],
    evidence_results: dict[str, Any],
) -> dict[str, Any]:
    """Keep one audit record complete without forwarding unrelated evidence."""
    selected_hypothesis_id = str(diagnosis.get("selected_hypothesis_id", "") or "").strip()
    selected_hypothesis = next(
        (
            item
            for item in investigation.get("hypotheses", [])
            if isinstance(item, dict) and str(item.get("hypothesis_id", "") or "").strip() == selected_hypothesis_id
        ),
        {},
    )
    selected_assessment = next(
        (
            item
            for item in diagnosis.get("hypothesis_assessment", [])
            if isinstance(item, dict) and str(item.get("hypothesis_id", "") or "").strip() == selected_hypothesis_id
        ),
        {},
    )
    cited_request_ids = set(_string_items(selected_assessment.get("controller_request_ids", [])))
    relevant_results = [
        _bounded_structured_value(item, 2_000)
        for item in evidence_results.get("results", [])
        if isinstance(item, dict) and str(item.get("request_id", "") or "") in cited_request_ids
    ]
    return {
        "diagnosis_index": diagnosis_index,
        "selected_hypothesis_id": selected_hypothesis_id,
        "selected_hypothesis": _bounded_structured_value(selected_hypothesis, 3_000),
        "selected_assessment": _bounded_structured_value(selected_assessment, 3_000),
        "controller_evidence_used": relevant_results,
        "causal_handoff": {
            "summary": _truncate_text(diagnosis.get("summary", ""), 700),
            "failure_mode": _truncate_text(diagnosis.get("failure_mode", ""), 300),
            "root_cause": _truncate_text(diagnosis.get("root_cause", ""), 900),
            "general_mechanism": _truncate_text(diagnosis.get("general_mechanism", ""), 900),
            "recommendation": _truncate_text(diagnosis.get("recommendation", ""), 900),
            "decision_contract": _bounded_structured_value(diagnosis.get("decision_contract", {}), 3_000),
            "decision_ground_audit": _bounded_structured_value(
                diagnosis.get("decision_ground_audit", []),
                3_000,
            ),
            "causal_coverage": _bounded_structured_value(diagnosis.get("causal_coverage", {}), 1_500),
            "evidence_refs": _bounded_structured_value(diagnosis.get("evidence_refs", []), 1_500),
        },
    }


def _build_causal_handoff_audit_prompt(
    *,
    public_task_contract: str,
    diagnoses: list[dict[str, Any]],
    investigation: dict[str, Any],
    evidence_results: dict[str, Any],
) -> str:
    """Ask for an independent deployability review before emitting an Issue."""
    assigned_indices = set(_assigned_diagnosis_indices(diagnoses))
    audit_payload = {
        "authoritative_public_task_contract": _truncate_text(public_task_contract, 8_000),
        "assigned_diagnoses": [
            _compact_causal_handoff_audit_diagnosis(
                diagnosis_index=index,
                diagnosis=diagnosis,
                investigation=investigation,
                evidence_results=evidence_results,
            )
            for index, diagnosis in enumerate(diagnoses, start=1)
            if index in assigned_indices
        ],
    }
    return f"""CAUSAL_HANDOFF_PHASE=audit

You are auditing causal attribution, not improving the Harness and not solving
the observed task. Review each assigned diagnosis independently. Return only:
{{
  "diagnosis_audits": [
    {{
      "diagnosis_index": 1,
      "selected_hypothesis_id": "h1",
      "hypothesis_binding": true,
      "runtime_decidable": true,
      "public_contract_consistent": true,
      "decision_rule_entailed": true,
      "decision_rule_source": "public_task_contract | task_visible_invariant | runtime_safety_invariant | none",
      "decision_rule_evidence": "the exact public clause or visible invariant that entails the action",
      "evaluation_independent": true,
      "single_intervention": true,
      "approved": true,
      "violations": []
    }}
  ]
}}

Audit rules:
- hypothesis_binding is true only when root_cause, general_mechanism,
  recommendation, decision_contract, and counterfactual all follow from the
  exact selected investigation hypothesis whose assessment is supported. An
  observed symptom from one hypothesis cannot justify the action of another.
- runtime_decidable is true only when a future task Agent can select the action
  from its public task, task-visible artifacts, trace-visible state, public
  repository, or public tool results.
- evaluation_independent is true only after mentally deleting verifier-owned
  expected values, gold/reference answers, scores, pass/fail labels, and
  candidate outcome deltas. Such evidence may falsify or measure a hypothesis,
  but it cannot be the rule that selects the next action or decides success.
- public_contract_consistent is false when the proposed behavior reverses an
  explicit public instruction and the only support for doing so is the scored
  outcome. Do not reinterpret the contract merely to approach a target value.
- decision_rule_entailed is stricter than consistency. It is true only when the
  exact required_action and acceptance_observable are positively entailed by
  one source available to the future Agent: an explicit public-task clause, a
  task-visible artifact/repository invariant, or a general runtime-safety
  invariant such as validating the exact released object after its last
  mutation. Record that source and the concrete support in decision_rule_source
  and decision_rule_evidence. "May mean", "could mean", "perhaps intended",
  or merely being compatible with the contract is not entailment. A bad output
  can entail validation or withholding that invalid output, but it does not
  entail an arbitrary domain-specific repair.
- Do not reject an independently supported process-level diagnosis merely
  because the evidence does not entail the observed case's desired answer.
  Reject label-level handoffs, then check whether the frozen mechanism entails
  a narrower runtime procedure: each negative ground must be linked to a
  task-visible mandatory requirement with the correct scope and owner, while
  optional recommendations remain separate. Approve that procedure when its
  trigger and acceptance observable are runtime-visible; it must not force a
  particular answer label.
- For `unverified_decision_ground_used`, the deployable action is the verification
  procedure itself: enumerate every material ground, establish its authority,
  scope, owner, trigger, and entailment, exclude any ground with an incomplete or
  contradicted chain, then recompute the decision. This is runtime-decidable and
  evaluation-independent when grounded in the public task and visible sources.
  Do not demand evidence that the recomputed decision has the benchmark's label.
- single_intervention is true only when one trigger selects one behavior change
  with one runtime-visible acceptance observable. Bundled independent repairs
  must be separate diagnoses.
- approved must equal the conjunction of the six preceding booleans. It must
  be false whenever violations is non-empty. Explain every false field in
  violations, and leave violations empty only when approved is true.
- Audit every assigned diagnosis exactly once. Do not audit unassigned records,
  rewrite a diagnosis, invent evidence, or propose a replacement.

AUDIT_INPUT:
{json.dumps(audit_payload, ensure_ascii=False, separators=(",", ":"))}
"""


def _normalize_causal_handoff_audit(
    value: dict[str, Any] | None,
    *,
    diagnoses: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate model audit structure and fail closed on missing diagnoses."""
    expected_indices = _assigned_diagnosis_indices(diagnoses)
    raw_rows = value.get("diagnosis_audits") if isinstance(value, dict) else None
    if not isinstance(raw_rows, list):
        raise ValueError("causal handoff audit must contain diagnosis_audits")
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    expected_by_index = {index: diagnoses[index - 1] for index in expected_indices}
    boolean_fields = (
        "hypothesis_binding",
        "runtime_decidable",
        "public_contract_consistent",
        "decision_rule_entailed",
        "evaluation_independent",
        "single_intervention",
        "approved",
    )
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise TypeError("causal handoff audit rows must be mappings")
        index = raw.get("diagnosis_index")
        if not isinstance(index, int) or isinstance(index, bool) or index not in expected_by_index:
            raise ValueError("causal handoff audit used an unknown diagnosis_index")
        if index in seen:
            raise ValueError("causal handoff audit repeated a diagnosis_index")
        seen.add(index)
        selected = str(raw.get("selected_hypothesis_id", "") or "").strip()
        expected_selected = str(expected_by_index[index].get("selected_hypothesis_id", "") or "").strip()
        if selected != expected_selected:
            raise ValueError(f"causal handoff audit changed selected_hypothesis_id for diagnosis[{index}]")
        normalized: dict[str, Any] = {
            "diagnosis_index": index,
            "selected_hypothesis_id": selected,
        }
        for field in boolean_fields:
            field_value = raw.get(field)
            if not isinstance(field_value, bool):
                raise TypeError(f"causal handoff audit {field} must be a boolean")
            normalized[field] = field_value
        decision_rule_source = str(raw.get("decision_rule_source", "") or "").strip()
        if decision_rule_source not in {
            "public_task_contract",
            "task_visible_invariant",
            "runtime_safety_invariant",
            "none",
        }:
            raise ValueError("causal handoff audit decision_rule_source is invalid")
        decision_rule_evidence = str(raw.get("decision_rule_evidence", "") or "").strip()
        if normalized["decision_rule_entailed"] and (decision_rule_source == "none" or not decision_rule_evidence):
            raise ValueError("causal handoff audit entailed decision rule requires a concrete source and evidence")
        normalized["decision_rule_source"] = decision_rule_source
        normalized["decision_rule_evidence"] = decision_rule_evidence
        violations = raw.get("violations")
        if not isinstance(violations, list) or any(
            not isinstance(item, str) or not item.strip() for item in violations
        ):
            raise TypeError("causal handoff audit violations must be a list of non-empty strings")
        normalized["violations"] = [item.strip() for item in violations]
        expected_approval = all(normalized[field] for field in boolean_fields[:-1])
        if normalized["approved"] != expected_approval:
            raise ValueError("causal handoff audit approved field does not match its component checks")
        if normalized["approved"] == bool(normalized["violations"]):
            raise ValueError("causal handoff audit violations must be empty exactly when approved")
        rows.append(normalized)
    missing = sorted(set(expected_indices) - seen)
    for index in missing:
        expected = expected_by_index[index]
        rows.append(
            {
                "diagnosis_index": index,
                "selected_hypothesis_id": str(expected.get("selected_hypothesis_id", "") or "").strip(),
                "hypothesis_binding": False,
                "runtime_decidable": False,
                "public_contract_consistent": False,
                "decision_rule_entailed": False,
                "decision_rule_source": "none",
                "decision_rule_evidence": "",
                "evaluation_independent": False,
                "single_intervention": False,
                "approved": False,
                "violations": ["The audit response omitted this assigned diagnosis; no causal handoff was approved."],
            }
        )
    return {"diagnosis_audits": sorted(rows, key=lambda item: item["diagnosis_index"])}


def _causal_handoff_audit_approved(audit: dict[str, Any]) -> bool:
    return all(bool(item.get("approved")) for item in audit.get("diagnosis_audits", []))


def _build_causal_handoff_repair_prompt(
    *,
    public_task_contract: str,
    diagnoses: list[dict[str, Any]],
    investigation: dict[str, Any],
    evidence_results: dict[str, Any],
    audit: dict[str, Any],
) -> str:
    """Give the diagnosis model one bounded chance to repair attribution itself."""
    return f"""CAUSAL_HANDOFF_PHASE=repair

The independent causal handoff audit rejected the diagnoses listed below.
Already-approved sibling diagnoses are frozen outside this prompt and will be
preserved by the controller. Repair only the rejected diagnosis records, not the
Harness. Return only one complete
{{"diagnoses":[...]}} JSON object using the same fields as the previous
diagnosis, including selected_hypothesis_id.

Rules:
- Hypothesis claims, falsifiers, controller evidence, and the public task
  contract are frozen. Do not add or rewrite a hypothesis or invent evidence.
- Preserve each hypothesis_assessment's evidence_relation and
  evidence_independence. A falsified assessment remains valid only with
  evidence_relation="direct_falsifier" and evidence_independence equal to
  "independent" or "direct_observation"; otherwise change it to unresolved.
- An assigned diagnosis must select exactly one hypothesis already assessed as
  supported. Every causal field and the one required action must follow from it.
- Remove evaluator-owned expected/gold/reference values, scores, and pass/fail
  labels mentally. If the action is no longer decidable from task-visible
  evidence, set target_ref="unassigned", selected_hypothesis_id="",
  evidence_status="insufficient", confidence="low", and keep its failed checks
  residual rather than inventing a replacement.
- Do not reverse an explicit public instruction merely because another behavior
  would move the scored output toward an expected value.
- The repaired required action must be positively entailed by an explicit
  public-task clause, a task-visible invariant, or a general runtime-safety
  invariant. Compatibility, ambiguity, and phrases such as "may have intended"
  are not enough. If only validation is entailed, do not invent a specific
  recovery algorithm; narrow the action to validation/withholding or leave it
  unassigned.
- If a label-specific action is not entailed but the selected supported
  hypothesis proves an upstream classification, scope, ownership, or validation
  error, narrow rather than abandon the handoff. Require a process-level check
  whose observable can be evaluated before submission. For negative grounds,
  require an explicit task-visible mandatory basis for the relevant object and
  owner, and separate optional recommendations. Never write the desired answer
  label into required_action or acceptance_observable.
- When an over-broad hypothesis was rejected but evidence directly supports the
  narrower `unverified_decision_ground_used` mechanism, retain that process-level
  handoff. Its required_action must independently verify authority, scope, owner,
  trigger, and entailment for every material ground, exclude unsupported grounds,
  and recompute the decision. Its acceptance observable is the completed ledger
  and absence of unsupported grounds in the conclusion, not a prescribed label.
  Set failure_mode=`unverified_decision_ground_used` and include a
  decision_ground_audit row for every observed material ground used by the
  repaired process diagnosis; do not claim unseen grounds were invalid.
- Do not bundle two independent behavior changes into one diagnosis.
- Do not reproduce, summarize, or replace any approved sibling diagnosis. An
  omitted rejected diagnosis means that rejected causal handoff is abandoned;
  it does not remove an approved sibling.

PUBLIC_TASK_CONTRACT:
{_bounded_json({"input_excerpt": public_task_contract}, 8_000)}

FROZEN_INVESTIGATION_AND_EVIDENCE:
{_causal_prompt_json(investigation, evidence_results)}

REJECTED_DIAGNOSES:
{_bounded_json(diagnoses, 16_000)}

AUDIT:
{_bounded_json(audit, 8_000)}
"""


def _downgrade_rejected_causal_handoffs(
    diagnoses: list[dict[str, Any]],
    *,
    rejected_indices: set[int],
    violations_by_index: dict[int, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Fail closed without discarding the observed failure or its hypotheses."""
    violations_by_index = violations_by_index or {}
    downgraded: list[dict[str, Any]] = []
    for index, raw in enumerate(diagnoses, start=1):
        diagnosis = dict(raw)
        if index not in rejected_indices:
            downgraded.append(diagnosis)
            continue
        cluster_ids = _string_items(_diagnosis_failure_cluster(diagnosis).get("failed_checks", []))
        coverage = diagnosis.get("causal_coverage")
        coverage = dict(coverage) if isinstance(coverage, dict) else {}
        unexplained = _string_items(coverage.get("unexplained_observations", []))
        audit_violations = violations_by_index.get(index, [])
        unexplained.append(
            "The causal handoff was not deployable from task-visible evidence"
            + (": " + "; ".join(audit_violations) if audit_violations else ".")
        )
        coverage.update(
            {
                "explained_requirement_ids": [],
                "residual_requirement_ids": list(
                    dict.fromkeys(
                        [
                            *_string_items(coverage.get("residual_requirement_ids", [])),
                            *cluster_ids,
                        ]
                    )
                ),
                "unexplained_observations": list(dict.fromkeys(unexplained)),
                "sufficiency_status": "unknown",
            }
        )
        diagnosis.update(
            {
                "issue_category": "unassigned",
                "evidence_status": "insufficient",
                "target_ref": "unassigned",
                "selected_hypothesis_id": "",
                "confidence": "low",
                "evidence_refs": [],
                "recommendation": (
                    "Acquire a task-visible discriminator that makes one causal action deployable "
                    "without evaluator-owned outcomes."
                ),
                "causal_coverage": coverage,
            }
        )
        downgraded.append(diagnosis)
    explained_any: set[str] = set()
    for diagnosis in downgraded:
        if str(diagnosis.get("evidence_status", "") or "").strip().casefold() == "insufficient":
            continue
        coverage = diagnosis.get("causal_coverage")
        if isinstance(coverage, dict):
            explained_any.update(_string_items(coverage.get("explained_requirement_ids", [])))
    partitioned: list[dict[str, Any]] = []
    for diagnosis in downgraded:
        if str(diagnosis.get("evidence_status", "") or "").strip().casefold() != "insufficient":
            partitioned.append(diagnosis)
            continue
        cluster = _diagnosis_failure_cluster(diagnosis)
        unresolved = [item for item in _string_items(cluster.get("failed_checks", [])) if item not in explained_any]
        if not unresolved:
            continue
        cluster["failed_checks"] = unresolved
        diagnosis["failure_cluster"] = cluster
        coverage = dict(diagnosis.get("causal_coverage", {}))
        coverage["residual_requirement_ids"] = [
            item for item in _string_items(coverage.get("residual_requirement_ids", [])) if item not in explained_any
        ]
        diagnosis["causal_coverage"] = coverage
        partitioned.append(diagnosis)
    return partitioned


def _replace_rejected_causal_handoffs(
    diagnoses: list[dict[str, Any]],
    *,
    rejected_indices: set[int],
    replacements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replace rejected rows without allowing repair to erase approved siblings."""
    merged: list[dict[str, Any]] = []
    inserted = False
    for index, diagnosis in enumerate(diagnoses, start=1):
        if index not in rejected_indices:
            merged.append(dict(diagnosis))
            continue
        if not inserted:
            merged.extend(dict(item) for item in replacements)
            inserted = True
    if not inserted:
        merged.extend(dict(item) for item in replacements)
    return merged


_OUTCOME_FIT_MARKERS = (
    "back-solved",
    "backsolved",
    "derived from expected",
    "expected answer implies",
    "expected output implies",
    "implied by expected",
    "reverse-engineer",
    "reverse engineer",
    "target value implies",
    "evaluator expects",
    "evaluator requires",
    "scorer expects",
    "scorer requires",
    "rubric expects",
    "rubric requires",
    "criteria require the answer",
    "criterion requires the answer",
    "反推",
    "根据预期",
    "由期望",
)

_DECISIVE_CONTROLLER_OPERATIONS = {
    "check_relation",
    "compare_numeric_change",
    "compare_runs",
    "read_artifact_window",
    "read_event",
    "read_repository_file",
}


def _hypothesis_assessment_entailment_audit(
    diagnoses: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize whether every material hypothesis status has an entailing basis."""
    rows: list[dict[str, Any]] = []
    for diagnosis_index, diagnosis in enumerate(diagnoses, start=1):
        for raw in diagnosis.get("hypothesis_assessment", []):
            if not isinstance(raw, dict):
                continue
            status = str(raw.get("status", "") or "").strip().casefold()
            if status not in {"supported", "falsified"}:
                continue
            relation = str(raw.get("evidence_relation", "") or "").strip().casefold()
            independence = str(raw.get("evidence_independence", "") or "").strip().casefold()
            verification = str(raw.get("verification_status", "") or "").strip().casefold()
            if status == "falsified":
                passed = (
                    relation == "direct_falsifier"
                    and independence in {"independent", "direct_observation"}
                    and verification == "refuted"
                )
            else:
                passed = verification == "verified" and relation not in {
                    "correlated_output",
                    "self_consistency",
                }
            rows.append(
                {
                    "diagnosis_index": diagnosis_index,
                    "hypothesis_id": str(raw.get("hypothesis_id", "") or ""),
                    "claimed_status": status,
                    "evidence_relation": relation or "unknown",
                    "evidence_independence": independence or "unknown",
                    "verification_status": verification or "unresolved",
                    "passed": passed,
                    "missing_discriminator": (
                        ""
                        if passed
                        else "direct controller evidence that entails the claim or its pre-registered falsifier "
                        "without relying on the questioned mechanism's own output"
                    ),
                }
            )
    return {
        "attempted": bool(rows),
        "status": "approved" if rows and all(item["passed"] for item in rows) else "needs_evidence",
        "rows": rows,
    }


def _hypothesis_assessments_need_independent_audit(diagnoses: list[dict[str, Any]]) -> bool:
    for diagnosis in diagnoses:
        for assessment in diagnosis.get("hypothesis_assessment", []):
            if not isinstance(assessment, dict):
                continue
            status = str(assessment.get("status", "") or "").strip().casefold()
            if status in {"supported", "falsified"}:
                return True
    return False


_DECISION_GROUND_HYPOTHESIS_ID = "h_controller_decision_ground"


def _decision_ground_audit_candidates(diagnoses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return material grounds whose use can be independently audited.

    A diagnosis may remain unassigned precisely because the model cannot infer the
    correct outcome.  The independent audit is the controller step that can narrow
    that answer-level uncertainty to an assignable process defect, so filtering out
    unassigned diagnoses here would make that transition unreachable.
    """
    candidates: list[dict[str, Any]] = []
    for diagnosis_index, diagnosis in enumerate(diagnoses, start=1):
        rows = diagnosis.get("decision_ground_audit", [])
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            if str(row.get("materiality", "") or "").strip().casefold() != "material":
                continue
            if row.get("used_for_decision") is not True:
                continue
            ground_id = str(row.get("ground_id", "") or "").strip()
            ground_text = str(row.get("ground_text", "") or "").strip()
            if not ground_id or not ground_text:
                continue
            candidates.append(
                {
                    "diagnosis_index": diagnosis_index,
                    "ground_id": ground_id,
                    "ground_text": ground_text,
                    "model_chain": {
                        "authority_status": str(row.get("authority_status", "") or "unknown"),
                        "scope_status": str(row.get("scope_status", "") or "unknown"),
                        "owner_status": str(row.get("owner_status", "") or "unknown"),
                        "trigger_status": str(row.get("trigger_status", "") or "unknown"),
                        "entailment_status": str(row.get("entailment_status", "") or "unknown"),
                    },
                    "model_controller_request_ids": _string_items(row.get("controller_request_ids", [])),
                }
            )
    return candidates


def _supplement_decision_ground_trace_evidence(
    case: CaseAnalysisInput,
    *,
    diagnoses: list[dict[str, Any]],
    investigation: dict[str, Any],
    evidence_results: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Read each released answer exactly before auditing its decision grounds.

    Artifact searches can establish what a source says, but they cannot prove
    that the Agent materially used a reason in its released decision. The
    controller therefore adds one bounded ``read_event`` for the final
    assistant message of each trace.
    """
    if not _decision_ground_audit_candidates(diagnoses):
        return investigation, evidence_results, []

    trace_path = Path(case.result_path).parent / "judge" / "normalized_trace.json"
    trace_data = _read_json_if_exists(trace_path)
    traces = trace_data.get("traces", []) if isinstance(trace_data, dict) else []
    hypothesis_ids = [
        str(item.get("hypothesis_id", "") or "")
        for item in investigation.get("hypotheses", [])
        if isinstance(item, dict) and str(item.get("hypothesis_id", "") or "")
    ]
    if not isinstance(traces, list) or not hypothesis_ids:
        return investigation, evidence_results, []

    existing_requests = [dict(item) for item in investigation.get("evidence_requests", []) if isinstance(item, dict)]
    existing_events = {
        (
            str(item.get("trace_id", "") or ""),
            int(item.get("message_index", 0) or 0),
        )
        for item in existing_requests
        if str(item.get("operation", "") or "") == "read_event"
    }
    used_ids = {str(item.get("request_id", "") or "") for item in existing_requests}
    remaining = max(0, _CAUSAL_TOTAL_REQUEST_LIMIT - len(existing_requests))
    supplemental_requests: list[dict[str, Any]] = []
    for trace in traces:
        if remaining <= 0 or not isinstance(trace, dict):
            break
        trace_id = str(trace.get("trace_id", "") or "")
        messages = trace.get("messages", [])
        if not trace_id or not isinstance(messages, list):
            continue
        selected: tuple[int, dict[str, Any]] | None = None
        for sequence, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            if str(message.get("role", "") or "").strip().casefold() != "assistant":
                continue
            if not str(message.get("content", "") or "").strip():
                continue
            try:
                message_index = int(message.get("message_index", sequence))
            except (TypeError, ValueError):
                message_index = sequence
            selected = (message_index, message)
        if selected is None or (trace_id, selected[0]) in existing_events:
            continue
        digest = hashlib.sha256(f"{trace_id}:{selected[0]}".encode()).hexdigest()[:10]
        request_id = f"controller.final.{digest}"
        suffix = 2
        while request_id in used_ids:
            request_id = f"controller.final.{digest}.{suffix}"
            suffix += 1
        request = {
            "request_id": request_id,
            "hypothesis_ids": list(hypothesis_ids),
            "operation": "read_event",
            "trace_id": trace_id,
            "message_index": selected[0],
            "purpose": (
                "controller-owned exact read of the released answer; establish whether a "
                "questioned decision ground was materially used"
            ),
            "automatic": True,
        }
        supplemental_requests.append(request)
        used_ids.add(request_id)
        existing_events.add((trace_id, selected[0]))
        remaining -= 1

    if not supplemental_requests:
        return investigation, evidence_results, []
    supplemental = execute_causal_investigation(
        case,
        {
            "hypotheses": list(investigation.get("hypotheses", [])),
            "evidence_requests": supplemental_requests,
        },
    )
    updated_investigation = dict(investigation)
    updated_investigation["evidence_requests"] = [*existing_requests, *supplemental_requests]
    return (
        updated_investigation,
        _merge_causal_evidence_results(evidence_results, supplemental),
        [str(item["request_id"]) for item in supplemental_requests],
    )


def _expand_cited_evidence_ids(
    cited_ids: set[str],
    evidence_rows: list[dict[str, Any]],
) -> set[str]:
    """Include controller-generated exact children of cited discovery reads."""
    expanded = set(cited_ids)
    changed = True
    while changed:
        changed = False
        for item in evidence_rows:
            request_id = str(item.get("request_id", "") or "")
            parents = {
                str(item.get("parent_request_id", "") or ""),
                *_string_items(item.get("parent_request_ids", [])),
            }
            parents.discard("")
            if request_id and request_id not in expanded and parents & expanded:
                expanded.add(request_id)
                changed = True
    return expanded


def _build_decision_ground_entailment_audit_prompt(
    *,
    diagnoses: list[dict[str, Any]],
    evidence_results: dict[str, Any],
) -> str:
    """Ask an independent pass to verify ground use and one broken requirement link."""
    candidates = _decision_ground_audit_candidates(diagnoses)
    evidence_rows = [
        item
        for item in evidence_results.get("results", [])
        if isinstance(item, dict) and str(item.get("request_id", "") or "")
    ]
    evidence_by_id = {str(item.get("request_id", "") or ""): item for item in evidence_rows}
    model_cited_ids: list[str] = []
    for candidate in candidates:
        for request_id in candidate.get("model_controller_request_ids", []):
            if request_id not in model_cited_ids:
                model_cited_ids.append(request_id)
    cited_ids = _expand_cited_evidence_ids(set(model_cited_ids), evidence_rows)
    direct_trace_rows = [
        item
        for item in evidence_rows
        if str(item.get("operation", "") or "").strip().casefold() in {"read_event", "search_trace"}
    ]
    evidence_pool: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    direct_trace_ids = [str(item.get("request_id", "") or "") for item in direct_trace_rows]
    ordered_ids = list(
        dict.fromkeys(
            [
                *direct_trace_ids,
                *model_cited_ids,
                *sorted(cited_ids - set(model_cited_ids) - set(direct_trace_ids)),
            ]
        )
    )
    for item in (evidence_by_id[request_id] for request_id in ordered_ids if request_id in evidence_by_id):
        request_id = str(item.get("request_id", "") or "")
        if request_id in seen_ids:
            continue
        seen_ids.add(request_id)
        evidence_pool.append(_bounded_structured_value(item, 4_000))
    return f"""CAUSAL_ASSESSMENT_PHASE=decision_ground_audit

You are an independent decision-ground auditor. Do not decide the correct final
answer, infer a gold label, or trust the draft diagnosis's chain labels. Audit
only whether one observed reason was materially used and whether the
task-visible trace established that reason's authority, scope, owner,
conditional trigger, and claim-to-requirement entailment before using it.

Return one valid JSON object only:
{{"ground_audits":[{{"diagnosis_index":1,"ground_id":"g1","material_ground_observed":true,"used_for_decision_observed":true,"authority_status":"verified|missing|contradicted|unknown","scope_status":"matched|mismatched|unknown","owner_status":"matched|mismatched|unknown","trigger_status":"satisfied|not_satisfied|not_applicable|unknown","entailment_status":"entailed|not_entailed|unknown","direct_trace_entails":true,"exact_trace_evidence":"one bounded quote and its role in the decision","controller_request_ids":["q1"],"approved_process_defect":true,"reason":"why this proves only the narrow process defect"}}]}}

Rules:
- Preserve diagnosis_index and ground_id. Audit every candidate row.
- material_ground_observed requires direct trace evidence that the reason was
  presented as necessary, blocking, rejecting, or otherwise used to support the
  decision. Merely mentioning an idea is not material use.
- used_for_decision_observed requires the trace to connect that reason to the
  released conclusion, not merely to brainstorming.
- Approve only when direct trace evidence establishes both facts above and at
  least one chain status is independently missing/contradicted/mismatched,
  trigger not_satisfied, or not_entailed. `unknown` alone is not enough.
- Absence of a rule from a bounded excerpt is not proof that authority or
  entailment was missing from the Agent's investigation. Mark `missing` only
  when the trace explicitly records that no basis was established, or a
  controller result is identified as the complete released decision/reasoning
  span and contains the material ground without the required link.
- A ground that cites its own source as recommended, optional, illustrative, or
  advisory but presents it as a mandatory defect directly contradicts the
  authority-to-entailment chain unless another cited binding rule supplies that
  link. This is a process observation; it does not determine the final label.
- Likewise, a duty assigned to another owner, an unsatisfied conditional
  trigger, or a claim outside the cited source's scope can establish the narrow
  process defect when the trace used it materially.
- Treat factual propositions in an available controller read_event as observed
  trace facts. If the same bounded span says actor X owns a duty and then uses
  target Y's lack of that duty as a material ground, mark owner_status=mismatched
  and entailment_status=not_entailed; do not demand a second source merely to
  restate the ownership contrast already present in the controller evidence.
- Do not claim every ground was invalid. Do not claim removing this ground must
  reverse the result. The only approved claim is that at least one material
  ground was used before its requirement chain was established.
- Cite only controller_request_ids present in EVIDENCE_POOL. An exact quote and
  at least one cited request are mandatory for approval.
- When a cited inspect_artifact request has controller-generated exact child
  reads, cite the decisive child request ID rather than only the discovery
  parent. Cite the released-answer read_event when asserting material use.

CANDIDATE_GROUNDS:
{_bounded_json(candidates, 12_000)}

EVIDENCE_POOL:
{_bounded_json(evidence_pool, 32_000)}
"""


def _build_decision_ground_entailment_audit_json_repair_prompt(
    original_prompt: str,
    previous_output: str,
) -> str:
    del original_prompt
    return f"""Previous independent decision-ground audit output was not valid JSON.

FORMAT-ONLY TASK. Preserve the conclusions and convert them into:
{{"ground_audits":[{{"diagnosis_index":1,"ground_id":"g1","material_ground_observed":true,"used_for_decision_observed":true,"authority_status":"verified|missing|contradicted|unknown","scope_status":"matched|mismatched|unknown","owner_status":"matched|mismatched|unknown","trigger_status":"satisfied|not_satisfied|not_applicable|unknown","entailment_status":"entailed|not_entailed|unknown","direct_trace_entails":true,"exact_trace_evidence":"...","controller_request_ids":["q1"],"approved_process_defect":true,"reason":"..."}}]}}

Return only JSON. Do not add evidence or change a conclusion.

PREVIOUS_AUDIT:
{_truncate_text(previous_output, 8_000)}
"""


def _build_decision_ground_entailment_reaudit_prompt(
    *,
    diagnoses: list[dict[str, Any]],
    evidence_results: dict[str, Any],
    prior_audit: dict[str, Any],
) -> str:
    """Recheck an internally inconsistent ground audit without changing its scope."""
    return (
        _build_decision_ground_entailment_audit_prompt(
            diagnoses=diagnoses,
            evidence_results=evidence_results,
        )
        + f"""

The first audit established no process defect. Re-audit from the frozen evidence,
paying special attention to internal authority contradictions in the released
reasoning. When the same bounded trace presents a source item as recommended,
optional, illustrative, or advisory but then uses its absence as a necessary,
required, blocking, or rejecting ground, that trace directly establishes an
authority/entailment contradiction unless another cited binding rule supplies
the missing link. In that situation set direct_trace_entails=true,
authority_status=contradicted, entailment_status=not_entailed, and approve only
the narrow process defect. Do not infer the correct final label. If the exact
trace contains no such contradiction and no other broken link, preserve the
rejection.

FIRST_AUDIT:
{_bounded_json(prior_audit, 12_000)}
"""
    )


def _normalize_decision_ground_entailment_audit(
    value: dict[str, Any] | None,
    *,
    diagnoses: list[dict[str, Any]],
    evidence_results: dict[str, Any],
) -> dict[str, Any]:
    candidates = {
        (int(item["diagnosis_index"]), str(item["ground_id"])): item
        for item in _decision_ground_audit_candidates(diagnoses)
    }
    available_ids = {
        str(item.get("request_id", "") or "")
        for item in evidence_results.get("results", [])
        if isinstance(item, dict) and str(item.get("request_id", "") or "")
    }
    operations_by_id = {
        str(item.get("request_id", "") or ""): str(item.get("operation", "") or "")
        for item in evidence_results.get("results", [])
        if isinstance(item, dict) and str(item.get("request_id", "") or "")
    }
    raw_rows = value.get("ground_audits", []) if isinstance(value, dict) else []
    normalized: dict[tuple[int, str], dict[str, Any]] = {}
    allowed = {
        "authority_status": {"verified", "missing", "contradicted", "unknown"},
        "scope_status": {"matched", "mismatched", "unknown"},
        "owner_status": {"matched", "mismatched", "unknown"},
        "trigger_status": {"satisfied", "not_satisfied", "not_applicable", "unknown"},
        "entailment_status": {"entailed", "not_entailed", "unknown"},
    }
    for raw in raw_rows if isinstance(raw_rows, list) else []:
        if not isinstance(raw, dict):
            continue
        try:
            diagnosis_index = int(raw.get("diagnosis_index", 0) or 0)
        except (TypeError, ValueError):
            continue
        ground_id = str(raw.get("ground_id", "") or "").strip()
        key = (diagnosis_index, ground_id)
        if key not in candidates or key in normalized:
            continue
        statuses = {
            field: (
                str(raw.get(field, "") or "unknown").strip().casefold()
                if str(raw.get(field, "") or "unknown").strip().casefold() in values
                else "unknown"
            )
            for field, values in allowed.items()
        }
        cited_ids = [item for item in _string_items(raw.get("controller_request_ids", [])) if item in available_ids]
        broken_chain = (
            statuses["authority_status"] in {"missing", "contradicted"}
            or statuses["scope_status"] == "mismatched"
            or statuses["owner_status"] == "mismatched"
            or statuses["trigger_status"] == "not_satisfied"
            or statuses["entailment_status"] == "not_entailed"
        )
        exact_evidence = str(raw.get("exact_trace_evidence", "") or "").strip()
        approved = (
            raw.get("approved_process_defect") is True
            and raw.get("material_ground_observed") is True
            and raw.get("used_for_decision_observed") is True
            and raw.get("direct_trace_entails") is True
            and broken_chain
            and bool(cited_ids)
            and any(operations_by_id.get(request_id) in _DECISIVE_CONTROLLER_OPERATIONS for request_id in cited_ids)
            and bool(exact_evidence)
        )
        normalized[key] = {
            "diagnosis_index": diagnosis_index,
            "ground_id": ground_id,
            "ground_text": candidates[key]["ground_text"],
            "material_ground_observed": raw.get("material_ground_observed") is True,
            "used_for_decision_observed": raw.get("used_for_decision_observed") is True,
            **statuses,
            "direct_trace_entails": raw.get("direct_trace_entails") is True,
            "exact_trace_evidence": exact_evidence,
            "controller_request_ids": cited_ids,
            "approved_process_defect": approved,
            "reason": str(raw.get("reason", "") or "").strip(),
        }
    for key, candidate in candidates.items():
        if key in normalized:
            continue
        normalized[key] = {
            "diagnosis_index": key[0],
            "ground_id": key[1],
            "ground_text": candidate["ground_text"],
            "material_ground_observed": False,
            "used_for_decision_observed": False,
            "authority_status": "unknown",
            "scope_status": "unknown",
            "owner_status": "unknown",
            "trigger_status": "unknown",
            "entailment_status": "unknown",
            "direct_trace_entails": False,
            "exact_trace_evidence": "",
            "controller_request_ids": [],
            "approved_process_defect": False,
            "reason": "The independent audit omitted this material decision ground.",
        }
    rows = [normalized[key] for key in sorted(normalized)]
    return {
        "attempted": bool(candidates),
        "status": "approved" if any(item["approved_process_defect"] for item in rows) else "not_established",
        "ground_audits": rows,
    }


def _apply_decision_ground_entailment_audit(
    diagnoses: list[dict[str, Any]],
    investigation: dict[str, Any],
    audit: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Bind one independently verified ground to a label-free process hypothesis."""
    approved = next(
        (
            item
            for item in audit.get("ground_audits", [])
            if isinstance(item, dict) and item.get("approved_process_defect") is True
        ),
        None,
    )
    if approved is None:
        return diagnoses, investigation
    diagnosis_index = int(approved.get("diagnosis_index", 0) or 0)
    if diagnosis_index < 1 or diagnosis_index > len(diagnoses):
        return diagnoses, investigation

    updated_diagnoses = [dict(item) for item in diagnoses]
    diagnosis = updated_diagnoses[diagnosis_index - 1]
    request_ids = _string_items(approved.get("controller_request_ids", []))
    exact_evidence = str(approved.get("exact_trace_evidence", "") or "").strip()
    ground_text = str(approved.get("ground_text", "") or "").strip()
    claim = (
        "The Agent used at least one material decision ground before establishing "
        "that ground's task-visible authority, scope, owner, trigger, and "
        "claim-to-requirement entailment chain."
    )
    falsified_if = (
        "The questioned ground did not contribute to the released decision, or "
        "task-visible evidence establishes every authority, scope, owner, trigger, "
        "and entailment link before the ground was used."
    )
    cluster = _diagnosis_failure_cluster(diagnosis)
    cluster_ids = _string_items(cluster.get("failed_checks", []))
    investigation_rows = [dict(item) for item in investigation.get("hypotheses", []) if isinstance(item, dict)]
    if not any(
        str(item.get("hypothesis_id", "") or "") == _DECISION_GROUND_HYPOTHESIS_ID for item in investigation_rows
    ):
        investigation_rows.append(
            {
                "hypothesis_id": _DECISION_GROUND_HYPOTHESIS_ID,
                "hypothesis_semantic_id": "chs:unverified_decision_ground_used",
                "origin": "independent_decision_ground_audit",
                "claim": claim,
                "explains_requirement_ids": cluster_ids,
                "current_support": [exact_evidence],
                "falsified_if": falsified_if,
                "numeric_change_check_required": False,
                "evidence_requests": [
                    dict(request)
                    for request in investigation.get("evidence_requests", [])
                    if isinstance(request, dict) and str(request.get("request_id", "") or "") in set(request_ids)
                ],
            }
        )
    updated_investigation = dict(investigation)
    updated_investigation["hypotheses"] = investigation_rows

    canonical_assessment = {
        "hypothesis_id": _DECISION_GROUND_HYPOTHESIS_ID,
        "status": "supported",
        "falsifying_condition_status": "not_observed",
        "claim_follows_from_evidence": "yes",
        "evidence_relation": "direct_claim",
        "evidence_independence": "direct_observation",
        "logic_check": exact_evidence,
        "controller_request_ids": request_ids,
        "reason": (
            "Independent ground audit directly observed material use and a broken task-visible requirement-chain link."
        ),
        "evidence_refs": list(diagnosis.get("evidence_refs", [])),
        "verification_status": "verified",
        "verification_basis": "independent_decision_ground_audit",
    }
    audit_row = {
        "ground_id": str(approved.get("ground_id", "") or ""),
        "ground_text": ground_text,
        "materiality": "material",
        "used_for_decision": True,
        "authority_status": str(approved.get("authority_status", "unknown") or "unknown"),
        "scope_status": str(approved.get("scope_status", "unknown") or "unknown"),
        "owner_status": str(approved.get("owner_status", "unknown") or "unknown"),
        "trigger_status": str(approved.get("trigger_status", "unknown") or "unknown"),
        "entailment_status": str(approved.get("entailment_status", "unknown") or "unknown"),
        "controller_request_ids": request_ids,
    }
    coverage = diagnosis.get("causal_coverage")
    coverage = dict(coverage) if isinstance(coverage, dict) else {}
    unexplained = _string_items(coverage.get("unexplained_observations", []))
    unexplained.append("Other material decision grounds remain independently unresolved.")
    coverage.update(
        {
            "explained_requirement_ids": cluster_ids,
            "residual_requirement_ids": [
                item for item in _string_items(coverage.get("residual_requirement_ids", [])) if item not in cluster_ids
            ],
            "unexplained_observations": list(dict.fromkeys(unexplained)),
            "causal_chain": [
                {
                    "cause": "A material decision ground lacked a verified task-visible requirement chain.",
                    "effect": "The ground was retained in the released decision.",
                    "evidence_status": "observed",
                    "evidence_refs": list(diagnosis.get("evidence_refs", [])),
                }
            ],
            "counterfactual_prediction": (
                "The questioned ground is excluded unless its requirement chain is verified, "
                "and the conclusion is recomputed from the remaining verified grounds."
            ),
            "sufficiency_status": "local_contributor",
        }
    )
    diagnosis.update(
        {
            "issue_category": "member_harness",
            "target_ref": "member_harness.solver.prompt",
            "summary": "A material decision ground was used before its task-visible requirement chain was verified.",
            "failure_mode": "unverified_decision_ground_used",
            "evidence_status": "supported_hypothesis",
            "selected_hypothesis_id": _DECISION_GROUND_HYPOTHESIS_ID,
            "root_cause": claim,
            "critical_mistake": exact_evidence,
            "general_mechanism": (
                "Treat each material reason as an independent proof obligation instead of "
                "letting one valid reason or a final conclusion validate sibling grounds."
            ),
            "recommendation": (
                "Before releasing a decision, build a per-ground ledger for authority, scope, "
                "owner, trigger, and entailment; exclude unsupported grounds and recompute "
                "the conclusion from the remaining verified grounds."
            ),
            "decision_ground_audit": [audit_row],
            "causal_coverage": coverage,
            "decision_contract": {
                "wrong_decision": "A material ground was used before its requirement chain was established.",
                "causal_distinction": (
                    "A ground is usable only when task-visible evidence establishes its own "
                    "authority, scope, owner, trigger, and entailment."
                ),
                "required_action": (
                    "Verify every material ground independently, exclude any unsupported ground, "
                    "then recompute the conclusion from the surviving grounds."
                ),
                "acceptance_observable": (
                    "Every retained material ground has a task-visible requirement-chain record; "
                    "unsupported grounds are absent from the recomputed decision."
                ),
                "scope_boundary": [
                    "Do not prescribe the final label.",
                    "Do not infer that every other ground is invalid.",
                ],
                "requirement_classification_required": True,
                "required_decision_links": [
                    "authority",
                    "scope",
                    "owner",
                    "trigger",
                    "entailment",
                ],
                "activation_phase": "pre_submission",
            },
            "hypothesis_assessment": [canonical_assessment],
            "confidence": "medium",
        }
    )
    updated_diagnoses[diagnosis_index - 1] = diagnosis
    return updated_diagnoses, updated_investigation


def _is_canonical_decision_ground_diagnosis(diagnosis: dict[str, Any]) -> bool:
    return (
        str(diagnosis.get("failure_mode", "") or "").strip().casefold() == "unverified_decision_ground_used"
        and str(diagnosis.get("selected_hypothesis_id", "") or "").strip() == _DECISION_GROUND_HYPOTHESIS_ID
        and _normalize_target_ref(diagnosis.get("target_ref", "")) not in {"", "unassigned"}
        and any(
            isinstance(item, dict)
            and str(item.get("hypothesis_id", "") or "").strip() == _DECISION_GROUND_HYPOTHESIS_ID
            and str(item.get("status", "") or "").strip().casefold() == "supported"
            for item in diagnosis.get("hypothesis_assessment", [])
        )
    )


def _preserve_canonical_decision_ground_diagnoses(
    previous: list[dict[str, Any]],
    rediagnosed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep independently audited process diagnoses across evidence rediagnosis."""
    frozen = [dict(item) for item in previous if _is_canonical_decision_ground_diagnosis(item)]
    if not frozen:
        return rediagnosed
    retained = [dict(item) for item in rediagnosed if not _is_canonical_decision_ground_diagnosis(item)]
    return [*frozen, *retained]


async def _run_decision_ground_entailment_audit(
    agent: Any,
    *,
    diagnoses: list[dict[str, Any]],
    investigation: dict[str, Any],
    evidence_results: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Run the independent audit and apply only its controller-verifiable result."""
    if not _decision_ground_audit_candidates(diagnoses):
        return (
            diagnoses,
            investigation,
            {
                "attempted": False,
                "status": "not_applicable",
                "ground_audits": [],
            },
        )
    raw = await _run_agent(
        agent,
        _build_decision_ground_entailment_audit_prompt(
            diagnoses=diagnoses,
            evidence_results=evidence_results,
        ),
        max_retries=0,
        json_repair_prompt_builder=_build_decision_ground_entailment_audit_json_repair_prompt,
    )
    audit = _normalize_decision_ground_entailment_audit(
        _extract_json_object(raw),
        diagnoses=diagnoses,
        evidence_results=evidence_results,
    )
    if audit.get("status") != "approved":
        reaudit_raw = await _run_agent(
            agent,
            _build_decision_ground_entailment_reaudit_prompt(
                diagnoses=diagnoses,
                evidence_results=evidence_results,
                prior_audit=audit,
            ),
            max_retries=0,
            json_repair_prompt_builder=_build_decision_ground_entailment_audit_json_repair_prompt,
        )
        reaudit = _normalize_decision_ground_entailment_audit(
            _extract_json_object(reaudit_raw),
            diagnoses=diagnoses,
            evidence_results=evidence_results,
        )
        audit = {
            **reaudit,
            "reaudit_attempted": True,
            "initial_status": str(audit.get("status", "") or ""),
        }
    narrowed_diagnoses, narrowed_investigation = _apply_decision_ground_entailment_audit(
        diagnoses,
        investigation,
        audit,
    )
    return narrowed_diagnoses, narrowed_investigation, audit


def _build_hypothesis_entailment_audit_prompt(
    *,
    diagnoses: list[dict[str, Any]],
    investigation: dict[str, Any],
    evidence_results: dict[str, Any],
) -> str:
    """Ask an independent pass to verify every material status against frozen evidence."""
    hypotheses = {
        str(item.get("hypothesis_id", "") or ""): item
        for item in investigation.get("hypotheses", [])
        if isinstance(item, dict) and str(item.get("hypothesis_id", "") or "")
    }
    evidence_by_id = {
        str(item.get("request_id", "") or ""): item
        for item in evidence_results.get("results", [])
        if isinstance(item, dict) and str(item.get("request_id", "") or "")
    }
    rows: list[dict[str, Any]] = []
    for diagnosis_index, diagnosis in enumerate(diagnoses, start=1):
        for assessment in diagnosis.get("hypothesis_assessment", []):
            if not isinstance(assessment, dict):
                continue
            status = str(assessment.get("status", "") or "").strip().casefold()
            if status not in {"supported", "falsified"}:
                continue
            hypothesis_id = str(assessment.get("hypothesis_id", "") or "").strip()
            cited_ids = _string_items(assessment.get("controller_request_ids", []))
            rows.append(
                {
                    "diagnosis_index": diagnosis_index,
                    "hypothesis_id": hypothesis_id,
                    "claimed_status": status,
                    "claim": str(hypotheses.get(hypothesis_id, {}).get("claim", "") or ""),
                    "falsified_if": str(hypotheses.get(hypothesis_id, {}).get("falsified_if", "") or ""),
                    "model_logic_check": str(assessment.get("logic_check", "") or ""),
                    "model_evidence_relation": str(assessment.get("evidence_relation", "") or "unknown"),
                    "model_evidence_independence": str(assessment.get("evidence_independence", "") or "unknown"),
                    "cited_evidence": [
                        _bounded_structured_value(evidence_by_id[request_id], 3_000)
                        for request_id in cited_ids
                        if request_id in evidence_by_id
                    ],
                }
            )
    return f"""CAUSAL_ASSESSMENT_PHASE=entailment_audit

You are an independent causal-evidence auditor. Do not diagnose the task, solve
it, repair the Harness, or trust the prior model's evidence labels. The
hypothesis claim, its pre-registered falsified_if, cited controller evidence,
and claimed status are frozen. Audit every row and return only:
{{"assessment_audits":[{{"diagnosis_index":1,"hypothesis_id":"h1","claimed_status":"supported|falsified","evidence_entails_status":true,"evidence_independent":true,"exact_entailment":"the concrete fact and how it matches the claim or falsified_if","approved":true,"missing_discriminator":""}}]}}

Rules:
- For supported, evidence_entails_status is true only when cited evidence
  directly establishes the claim, not merely a correlated downstream outcome.
- Audit a narrow `unverified_decision_ground_used` claim at the process level.
  It is directly established when cited evidence shows (a) one reason was used as
  a material ground for the observed decision and (b) the task-visible reasoning
  did not establish, or independent evidence contradicts, at least one link in
  that ground's authority/scope/owner/trigger/entailment chain. Do not require
  proof of the correct final label, that this ground alone changed the outcome,
  or that every other ground was invalid. Conversely, do not approve a blanket
  shortcut claim from evidence of only one unverified ground.
- For falsified, compare cited evidence word-for-word in meaning with the frozen
  falsified_if. It must actually establish that observable, not just make the
  hypothesis seem unlikely.
- evidence_independent is false when the cited fact is only completion without
  error, agreement among outputs, or a final/cached value produced and checked
  by the same mechanism whose correctness, completeness, propagation, or
  convergence is questioned. A direct trace observation of the claimed action,
  an external acceptance condition, an independently recomputed result, or a
  stability/perturbation check can be independent.
- approved must equal evidence_entails_status AND evidence_independent. When
  false, missing_discriminator must name one bounded observable that would
  decide the status without using hidden evaluator answers.
- Preserve each diagnosis_index, hypothesis_id, and claimed_status exactly.

AUDIT_ROWS:
{_bounded_json(rows, 28_000)}
"""


def _normalize_hypothesis_entailment_audit(
    value: dict[str, Any] | None,
    *,
    diagnoses: list[dict[str, Any]],
) -> dict[str, Any]:
    expected: dict[tuple[int, str], str] = {}
    for diagnosis_index, diagnosis in enumerate(diagnoses, start=1):
        for assessment in diagnosis.get("hypothesis_assessment", []):
            if not isinstance(assessment, dict):
                continue
            status = str(assessment.get("status", "") or "").strip().casefold()
            hypothesis_id = str(assessment.get("hypothesis_id", "") or "").strip()
            if status in {"supported", "falsified"} and hypothesis_id:
                expected[(diagnosis_index, hypothesis_id)] = status
    raw_rows = value.get("assessment_audits", []) if isinstance(value, dict) else []
    normalized: dict[tuple[int, str], dict[str, Any]] = {}
    for raw in raw_rows if isinstance(raw_rows, list) else []:
        if not isinstance(raw, dict):
            continue
        try:
            diagnosis_index = int(raw.get("diagnosis_index", 0) or 0)
        except (TypeError, ValueError):
            continue
        hypothesis_id = str(raw.get("hypothesis_id", "") or "").strip()
        key = (diagnosis_index, hypothesis_id)
        if key not in expected or key in normalized:
            continue
        entails = raw.get("evidence_entails_status") is True
        independent = raw.get("evidence_independent") is True
        approved = raw.get("approved") is True and entails and independent
        missing = str(raw.get("missing_discriminator", "") or "").strip()
        if not approved and not missing:
            missing = "A direct independent discriminator for the frozen claim or falsified_if is still required."
        normalized[key] = {
            "diagnosis_index": diagnosis_index,
            "hypothesis_id": hypothesis_id,
            "claimed_status": expected[key],
            "evidence_entails_status": entails,
            "evidence_independent": independent,
            "exact_entailment": str(raw.get("exact_entailment", "") or "").strip(),
            "approved": approved,
            "missing_discriminator": missing,
        }
    for key, status in expected.items():
        if key in normalized:
            continue
        normalized[key] = {
            "diagnosis_index": key[0],
            "hypothesis_id": key[1],
            "claimed_status": status,
            "evidence_entails_status": False,
            "evidence_independent": False,
            "exact_entailment": "",
            "approved": False,
            "missing_discriminator": "The independent audit omitted this material hypothesis status.",
        }
    rows = [normalized[key] for key in sorted(normalized)]
    return {
        "attempted": bool(expected),
        "status": "approved" if rows and all(item["approved"] for item in rows) else "needs_evidence",
        "assessment_audits": rows,
    }


def _build_hypothesis_entailment_audit_json_repair_prompt(
    original_prompt: str,
    previous_output: str,
) -> str:
    del original_prompt
    return f"""Previous independent hypothesis-entailment audit output was not valid JSON.

FORMAT-ONLY TASK. Preserve the prior audit conclusions and convert them into:
{{"assessment_audits":[{{"diagnosis_index":1,"hypothesis_id":"h1","claimed_status":"supported|falsified","evidence_entails_status":true,"evidence_independent":true,"exact_entailment":"the concrete frozen-evidence derivation","approved":true,"missing_discriminator":""}}]}}

Return only JSON. Do not diagnose, add evidence, or change a conclusion.

PREVIOUS_AUDIT:
{_truncate_text(previous_output, 8_000)}
"""


def _apply_hypothesis_entailment_audit(
    diagnoses: list[dict[str, Any]],
    audit: dict[str, Any],
) -> list[dict[str, Any]]:
    rejected = {
        (int(item.get("diagnosis_index", 0) or 0), str(item.get("hypothesis_id", "") or "")): item
        for item in audit.get("assessment_audits", [])
        if isinstance(item, dict) and not bool(item.get("approved"))
    }
    updated: list[dict[str, Any]] = []
    for diagnosis_index, raw_diagnosis in enumerate(diagnoses, start=1):
        diagnosis = dict(raw_diagnosis)
        assessments: list[dict[str, Any]] = []
        missing: list[str] = []
        for raw_assessment in diagnosis.get("hypothesis_assessment", []):
            if not isinstance(raw_assessment, dict):
                continue
            assessment = dict(raw_assessment)
            key = (diagnosis_index, str(assessment.get("hypothesis_id", "") or ""))
            rejection = rejected.get(key)
            if rejection is not None:
                discriminator = str(rejection.get("missing_discriminator", "") or "").strip()
                assessment.update(
                    {
                        "status": "unresolved",
                        "falsifying_condition_status": "unknown",
                        "claim_follows_from_evidence": "unknown",
                        "evidence_relation": "unknown",
                        "evidence_independence": "unknown",
                        "logic_check": "Independent entailment audit rejected the claimed status: " + discriminator,
                        "verification_status": "unresolved",
                        "verification_basis": "independent_entailment_audit_rejected",
                    }
                )
                missing.append(discriminator)
            assessments.append(assessment)
        diagnosis["hypothesis_assessment"] = assessments
        if missing:
            diagnosis["discriminating_evidence"] = "; ".join(dict.fromkeys(missing))
            diagnosis["recommendation"] = "Acquire the bounded discriminator identified by the independent audit."
            coverage = diagnosis.get("causal_coverage")
            coverage = dict(coverage) if isinstance(coverage, dict) else {}
            observations = _string_items(coverage.get("unexplained_observations", []))
            observations.extend(missing)
            coverage["unexplained_observations"] = list(dict.fromkeys(observations))
            diagnosis["causal_coverage"] = coverage
        updated.append(diagnosis)
    return updated


def _reconcile_causal_assessments(
    diagnoses: list[dict[str, Any]],
    investigation: dict[str, Any],
    *,
    evidence_results: dict[str, Any],
    failed_requirement_inventory: dict[str, Any] | None,
    prior_candidate_feedback: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Downgrade only unsupported hypotheses instead of discarding a Case.

    This is deliberately monotonic: it may remove an unplanned assessment or
    turn a claim into ``unresolved``, but it never promotes evidence or invents
    an executable target.  Valid supported local mechanisms therefore survive
    a malformed sibling assessment while unresolved coverage remains explicit.
    """
    planned_rows = {
        str(item.get("hypothesis_id", "") or ""): item
        for item in investigation.get("hypotheses", [])
        if isinstance(item, dict) and str(item.get("hypothesis_id", "") or "")
    }
    if not diagnoses or not planned_rows:
        return diagnoses, []
    warnings: list[str] = []
    required_ids = _failed_requirement_ids(failed_requirement_inventory)
    if required_ids:
        prepared_diagnoses: list[dict[str, Any]] = []
        has_authoritative_cluster = any(
            set(_string_items(_diagnosis_failure_cluster(item).get("failed_checks", []))) & required_ids
            for item in diagnoses
        )
        for diagnosis_index, raw_diagnosis in enumerate(diagnoses, start=1):
            diagnosis = dict(raw_diagnosis)
            cluster = _diagnosis_failure_cluster(diagnosis)
            cluster_ids = _string_items(cluster.get("failed_checks", []))
            known_cluster_ids = [item for item in cluster_ids if item in required_ids]
            unknown_cluster_ids = [item for item in cluster_ids if item not in required_ids]
            if unknown_cluster_ids and not known_cluster_ids and has_authoritative_cluster:
                warnings.append(
                    f"diagnosis[{diagnosis_index}] dropped a redundant cluster containing only "
                    "non-authoritative requirement IDs: " + ", ".join(unknown_cluster_ids)
                )
                continue
            if unknown_cluster_ids and known_cluster_ids:
                cluster["failed_checks"] = known_cluster_ids
                diagnosis["failure_cluster"] = cluster
                coverage = diagnosis.get("causal_coverage")
                if isinstance(coverage, dict):
                    coverage = dict(coverage)
                    coverage["explained_requirement_ids"] = [
                        item
                        for item in _string_items(coverage.get("explained_requirement_ids", []))
                        if item in required_ids
                    ]
                    coverage["residual_requirement_ids"] = [
                        item
                        for item in _string_items(coverage.get("residual_requirement_ids", []))
                        if item in required_ids
                    ]
                    diagnosis["causal_coverage"] = coverage
                warnings.append(
                    f"diagnosis[{diagnosis_index}] removed non-authoritative requirement IDs while "
                    "preserving its authoritative cluster: " + ", ".join(unknown_cluster_ids)
                )
            prepared_diagnoses.append(diagnosis)
        if prepared_diagnoses:
            diagnoses = prepared_diagnoses
    request_operations: dict[str, str] = {}
    compatible_requests = _compatible_evidence_requests(investigation)
    for request in investigation.get("evidence_requests", []):
        if not isinstance(request, dict):
            continue
        request_id = str(request.get("request_id", "") or "")
        if request_id:
            request_operations[request_id] = str(request.get("operation", "") or "")
    request_availability = {
        str(item.get("request_id", "") or ""): str(item.get("availability", "") or "").casefold()
        for item in evidence_results.get("results", [])
        if isinstance(item, dict) and str(item.get("request_id", "") or "")
    }

    reconciled: list[dict[str, Any]] = []
    assessed_ids: set[str] = set()
    for diagnosis_index, raw_diagnosis in enumerate(diagnoses, start=1):
        diagnosis = dict(raw_diagnosis)
        raw_assessments = diagnosis.get("hypothesis_assessment", [])
        assessments: list[dict[str, Any]] = []
        for raw in raw_assessments if isinstance(raw_assessments, list) else []:
            if not isinstance(raw, dict):
                continue
            assessment = dict(raw)
            hypothesis_id = str(assessment.get("hypothesis_id", "") or "")
            if hypothesis_id not in planned_rows:
                if hypothesis_id:
                    warnings.append(f"diagnosis[{diagnosis_index}] dropped unplanned hypothesis {hypothesis_id}")
                continue
            assessed_ids.add(hypothesis_id)
            status = str(assessment.get("status", "") or "").strip().casefold()
            falsifying = str(assessment.get("falsifying_condition_status", "") or "").strip().casefold()
            follows = str(assessment.get("claim_follows_from_evidence", "") or "").strip().casefold()
            evidence_relation = str(assessment.get("evidence_relation", "") or "").strip().casefold()
            evidence_independence = str(assessment.get("evidence_independence", "") or "").strip().casefold()
            logic = str(assessment.get("logic_check", "") or "").strip()
            cited = set(_string_items(assessment.get("controller_request_ids", [])))
            scoped_requests = compatible_requests.get(hypothesis_id, set())
            available_cited = sorted(
                request_id
                for request_id in cited & scoped_requests
                if request_availability.get(request_id) == "available"
            )
            invalid_reasons: list[str] = []
            if status not in {"supported", "falsified", "unresolved"}:
                invalid_reasons.append("invalid_status")
            if status == "supported":
                if falsifying != "not_observed":
                    invalid_reasons.append("falsifier_not_cleared")
                if follows != "yes":
                    invalid_reasons.append("claim_not_entailed")
                if not logic:
                    invalid_reasons.append("missing_logic_check")
                if not available_cited:
                    invalid_reasons.append("no_available_controller_evidence")
                if evidence_relation != "direct_claim":
                    invalid_reasons.append("evidence_does_not_directly_entail_claim")
                if evidence_independence not in {"independent", "direct_observation"}:
                    invalid_reasons.append("claim_evidence_not_independent_or_direct")
                if available_cited and not any(
                    request_operations.get(request_id) in _DECISIVE_CONTROLLER_OPERATIONS
                    for request_id in available_cited
                ):
                    invalid_reasons.append("no_decisive_controller_probe")
                if bool(planned_rows[hypothesis_id].get("numeric_change_check_required")) and not any(
                    request_operations.get(request_id) == "compare_numeric_change" for request_id in available_cited
                ):
                    invalid_reasons.append("missing_numeric_delta_evidence")
                outcome_fit_text = " ".join(
                    (
                        str(planned_rows[hypothesis_id].get("claim", "") or ""),
                        logic,
                        str(assessment.get("reason", "") or ""),
                    )
                ).casefold()
                if any(marker in outcome_fit_text for marker in _OUTCOME_FIT_MARKERS):
                    invalid_reasons.append("outcome_reverse_engineering_is_not_causal_evidence")
            elif status == "falsified":
                if falsifying != "observed" and follows != "no":
                    invalid_reasons.append("falsification_not_observed")
                if not logic:
                    invalid_reasons.append("missing_logic_check")
                if not available_cited:
                    invalid_reasons.append("no_available_controller_evidence")
                if evidence_relation != "direct_falsifier":
                    invalid_reasons.append("evidence_does_not_directly_entail_falsifier")
                if evidence_independence not in {"independent", "direct_observation"}:
                    invalid_reasons.append("falsifier_not_independent_of_questioned_mechanism")
            if cited - scoped_requests:
                warnings.append(
                    f"diagnosis[{diagnosis_index}] stripped evidence outside {hypothesis_id}: "
                    + ", ".join(sorted(cited - scoped_requests))
                )
            if cited - set(available_cited):
                warnings.append(
                    f"diagnosis[{diagnosis_index}] stripped unavailable evidence from {hypothesis_id}: "
                    + ", ".join(sorted(cited - set(available_cited)))
                )

            assessment["controller_request_ids"] = available_cited
            if invalid_reasons:
                assessment.update(
                    {
                        "status": "unresolved",
                        "falsifying_condition_status": "unknown",
                        "claim_follows_from_evidence": "unknown",
                        "logic_check": (
                            "Controller reconciliation removed unsupported entailment: "
                            + ", ".join(dict.fromkeys(invalid_reasons))
                        ),
                        "reason": ("The available controller evidence does not establish this causal claim."),
                    }
                )
                warnings.append(
                    f"diagnosis[{diagnosis_index}] downgraded {hypothesis_id}: "
                    + ", ".join(dict.fromkeys(invalid_reasons))
                )
                assessment["verification_status"] = "unresolved"
                assessment["verification_basis"] = "controller_probe_did_not_establish_the_claim"
            elif status == "supported":
                has_decisive_probe = any(
                    request_operations.get(request_id) in _DECISIVE_CONTROLLER_OPERATIONS
                    for request_id in available_cited
                )
                assessment["verification_status"] = "verified" if has_decisive_probe else "unresolved"
                assessment["verification_basis"] = (
                    "controller_owned_exact_or_structured_probe"
                    if has_decisive_probe
                    else "discovery_result_requires_exact_followup"
                )
            elif status == "falsified":
                has_decisive_probe = any(
                    request_operations.get(request_id) in _DECISIVE_CONTROLLER_OPERATIONS
                    for request_id in available_cited
                )
                assessment["verification_status"] = "refuted" if has_decisive_probe else "unresolved"
                assessment["verification_basis"] = (
                    "controller_owned_direct_independent_falsifier"
                    if has_decisive_probe
                    else "discovery_result_requires_exact_falsifier_followup"
                )
            else:
                assessment["verification_status"] = "unresolved"
                assessment["verification_basis"] = "no_decisive_controller_probe"
            assessments.append(assessment)
        diagnosis["hypothesis_assessment"] = assessments
        reconciled.append(diagnosis)

    missing = sorted(set(planned_rows) - assessed_ids)
    if missing:
        destination = None
        fallback_destination = None
        for item in reconciled:
            if _is_canonical_decision_ground_diagnosis(item):
                continue
            if fallback_destination is None:
                fallback_destination = item
            if _normalize_target_ref(item.get("target_ref", "")) in {"", "unassigned"}:
                destination = item
                break
        if destination is None:
            destination = fallback_destination
        if destination is None:
            destination = reconciled[0]
            coverage = destination.get("causal_coverage")
            coverage = dict(coverage) if isinstance(coverage, dict) else {}
            unexplained = _string_items(coverage.get("unexplained_observations", []))
            unexplained.append(
                "Other planned hypotheses remain unresolved outside the independently "
                "verified decision-ground process diagnosis: " + ", ".join(missing)
            )
            coverage["unexplained_observations"] = list(dict.fromkeys(unexplained))
            destination["causal_coverage"] = coverage
            warnings.append(
                "kept omitted sibling hypotheses residual without attaching them to the "
                "independently verified decision-ground diagnosis: " + ", ".join(missing)
            )
        else:
            values = list(destination.get("hypothesis_assessment", []))
            for hypothesis_id in missing:
                values.append(
                    {
                        "hypothesis_id": hypothesis_id,
                        "status": "unresolved",
                        "falsifying_condition_status": "unknown",
                        "claim_follows_from_evidence": "unknown",
                        "logic_check": "The model omitted this planned alternative; no entailment was accepted.",
                        "controller_request_ids": [],
                        "reason": "Planned hypothesis was not validly assessed.",
                        "evidence_refs": [],
                        "verification_status": "unresolved",
                        "verification_basis": "hypothesis_was_not_assessed",
                    }
                )
                warnings.append(f"synthesized unresolved assessment for omitted hypothesis {hypothesis_id}")
            destination["hypothesis_assessment"] = values

    for diagnosis in reconciled:
        assessments = diagnosis.get("hypothesis_assessment", [])
        statuses = {
            str(item.get("status", "") or "").strip().casefold() for item in assessments if isinstance(item, dict)
        }
        supported_ids: list[str] = []
        for item in assessments:
            if not isinstance(item, dict):
                continue
            hypothesis_id = str(item.get("hypothesis_id", "") or "").strip()
            status = str(item.get("status", "") or "").strip().casefold()
            if status == "supported" and hypothesis_id:
                supported_ids.append(hypothesis_id)
        target_ref = _normalize_target_ref(diagnosis.get("target_ref", ""))
        selected_hypothesis_id = str(diagnosis.get("selected_hypothesis_id", "") or "").strip()
        if target_ref not in {"", "unassigned"} and selected_hypothesis_id:
            selected_hypothesis = planned_rows.get(selected_hypothesis_id, {})
            causal_handoff = {
                "selected_hypothesis_claim": selected_hypothesis.get("claim", ""),
                "selected_hypothesis_falsified_if": selected_hypothesis.get("falsified_if", ""),
                "root_cause": diagnosis.get("root_cause", ""),
                "general_mechanism": diagnosis.get("general_mechanism", ""),
                "recommendation": diagnosis.get("recommendation", ""),
                "decision_contract": diagnosis.get("decision_contract", {}),
            }
            if _contains_evaluator_outcome_dependency(causal_handoff):
                for assessment in assessments:
                    if not isinstance(assessment, dict):
                        continue
                    if str(assessment.get("hypothesis_id", "") or "").strip() != selected_hypothesis_id:
                        continue
                    assessment["handoff_disposition"] = "non_actionable"
                    assessment["handoff_reason"] = (
                        "The proposed runtime handoff depends on evaluator-owned outcomes; "
                        "the observed mechanism is retained but cannot define a deployable decision."
                    )
                diagnosis["target_ref"] = "unassigned"
                diagnosis["selected_hypothesis_id"] = ""
                target_ref = "unassigned"
                selected_hypothesis_id = ""
                warnings.append(
                    "downgraded evaluator-outcome-dependent causal handoff without erasing sibling diagnoses"
                )
        if target_ref in {"", "unassigned"} and selected_hypothesis_id:
            diagnosis["selected_hypothesis_id"] = ""
            selected_hypothesis_id = ""
        if target_ref not in {"", "unassigned"} and not selected_hypothesis_id and len(supported_ids) == 1:
            selected_hypothesis_id = supported_ids[0]
            diagnosis["selected_hypothesis_id"] = selected_hypothesis_id
        handoff_has_supported_binding = target_ref in {"", "unassigned"} or bool(
            selected_hypothesis_id and selected_hypothesis_id in supported_ids
        )
        coverage = diagnosis.get("causal_coverage")
        coverage = dict(coverage) if isinstance(coverage, dict) else {}
        explained = _string_items(coverage.get("explained_requirement_ids", []))
        residual = [
            item for item in _string_items(coverage.get("residual_requirement_ids", [])) if item not in explained
        ]
        coverage["residual_requirement_ids"] = residual
        if target_ref in {"", "unassigned"} or "supported" not in statuses or not handoff_has_supported_binding:
            cluster_ids = _string_items(_diagnosis_failure_cluster(diagnosis).get("failed_checks", []))
            diagnosis["evidence_status"] = "insufficient"
            diagnosis["target_ref"] = "unassigned"
            diagnosis["selected_hypothesis_id"] = ""
            diagnosis["confidence"] = "low"
            diagnosis["issue_category"] = "unassigned"
            diagnosis["evidence_refs"] = []
            coverage["explained_requirement_ids"] = []
            coverage["residual_requirement_ids"] = list(dict.fromkeys([*residual, *cluster_ids]))
            coverage["sufficiency_status"] = "unknown"
            unexplained = _string_items(coverage.get("unexplained_observations", []))
            unexplained.append(
                "No planned causal hypothesis retained valid positive support."
                if "supported" not in statuses
                else "The proposed handoff was not bound to exactly one supported causal hypothesis."
            )
            coverage["unexplained_observations"] = list(dict.fromkeys(unexplained))
        else:
            supported_hypothesis_ids: set[str] = set()
            for item in assessments:
                if not isinstance(item, dict):
                    continue
                if str(item.get("status", "") or "").strip().casefold() != "supported":
                    continue
                hypothesis_id = str(item.get("hypothesis_id", "") or "")
                if hypothesis_id:
                    supported_hypothesis_ids.add(hypothesis_id)
            supported_requirement_ids: set[str] = set()
            for hypothesis_id in supported_hypothesis_ids:
                supported_requirement_ids.update(
                    _string_items(planned_rows.get(hypothesis_id, {}).get("explains_requirement_ids", []))
                )
            cluster_ids = _string_items(_diagnosis_failure_cluster(diagnosis).get("failed_checks", []))
            explained_cluster_ids = [item for item in cluster_ids if item in supported_requirement_ids]
            if explained_cluster_ids:
                cluster = _diagnosis_failure_cluster(diagnosis)
                cluster["failed_checks"] = explained_cluster_ids
                diagnosis["failure_cluster"] = cluster
                coverage["explained_requirement_ids"] = explained_cluster_ids
                coverage["residual_requirement_ids"] = [
                    item
                    for item in _string_items(coverage.get("residual_requirement_ids", []))
                    if item not in explained_cluster_ids
                ]
            if "unresolved" in statuses and str(diagnosis.get("evidence_status", "") or "").casefold() == "confirmed":
                diagnosis["evidence_status"] = "supported_hypothesis"
                diagnosis["confidence"] = "medium"
                coverage["sufficiency_status"] = "local_contributor"
                unexplained = _string_items(coverage.get("unexplained_observations", []))
                unexplained.append("One or more planned causal alternatives remain unresolved.")
                coverage["unexplained_observations"] = list(dict.fromkeys(unexplained))
            current_explained = set(_string_items(coverage.get("explained_requirement_ids", [])))
            current_residual = _string_items(coverage.get("residual_requirement_ids", []))
            current_unexplained = _string_items(coverage.get("unexplained_observations", []))
            current_cluster_ids = set(_string_items(_diagnosis_failure_cluster(diagnosis).get("failed_checks", [])))
            task_sufficient = str(coverage.get("sufficiency_status", "") or "").casefold() == "task_sufficient"
            incomplete_evidence = bool(current_residual) or bool(current_unexplained)
            incomplete_scope = current_explained != required_ids or current_cluster_ids != required_ids
            if task_sufficient and (incomplete_evidence or incomplete_scope):
                complete_cluster = bool(current_cluster_ids) and current_explained == current_cluster_ids
                no_residual = not current_residual and not current_unexplained
                if complete_cluster and no_residual:
                    coverage["sufficiency_status"] = "cluster_sufficient"
                else:
                    coverage["sufficiency_status"] = "local_contributor"
                    if not current_residual and not current_unexplained:
                        current_unexplained.append(
                            "This mechanism does not establish every authoritative failed requirement."
                        )
                        coverage["unexplained_observations"] = current_unexplained
                warnings.append(f"diagnosis[{diagnosis_index}] downgraded unsupported task_sufficient coverage")
        diagnosis["causal_coverage"] = coverage

    fallback_experiment_assessment = _synthesized_prior_experiment_assessment(prior_candidate_feedback)
    for diagnosis_index, diagnosis in enumerate(reconciled, start=1):
        assessment = diagnosis.get("prior_experiment_assessment")
        if isinstance(assessment, dict) and assessment.get("availability") == "available":
            normalized_assessment, correction = _normalize_prior_experiment_assessment(assessment)
            normalized_assessment, semantic_correction = _reconcile_prior_behavior_with_current_failure(
                normalized_assessment,
                diagnosis=diagnosis,
                prior_candidate_feedback=prior_candidate_feedback,
            )
            diagnosis["prior_experiment_assessment"] = normalized_assessment
            if correction:
                warnings.append(f"diagnosis[{diagnosis_index}] {correction}")
            if semantic_correction:
                warnings.append(f"diagnosis[{diagnosis_index}] {semantic_correction}")
            continue
        if fallback_experiment_assessment:
            diagnosis["prior_experiment_assessment"] = dict(fallback_experiment_assessment)
            warnings.append(
                f"diagnosis[{diagnosis_index}] synthesized a conservative paired-experiment "
                "assessment from controller-owned feedback"
            )

    # An explained requirement wins over a duplicate residual claim. This is a
    # deterministic partition repair, not an evidence promotion.
    explained_any: set[str] = set()
    for diagnosis in reconciled:
        coverage = diagnosis.get("causal_coverage")
        if isinstance(coverage, dict):
            explained_any.update(_string_items(coverage.get("explained_requirement_ids", [])))
    partitioned: list[dict[str, Any]] = []
    redundant_unresolved: list[tuple[int, dict[str, Any]]] = []
    for diagnosis_index, diagnosis in enumerate(reconciled, start=1):
        coverage = diagnosis.get("causal_coverage")
        if not isinstance(coverage, dict):
            partitioned.append(diagnosis)
            continue
        cluster = _diagnosis_failure_cluster(diagnosis)
        cluster_ids = _string_items(cluster.get("failed_checks", []))
        evidence_status = str(diagnosis.get("evidence_status", "") or "").casefold()
        if evidence_status == "insufficient":
            unresolved_cluster = [item for item in cluster_ids if item not in explained_any]
            if cluster_ids and not unresolved_cluster:
                redundant_unresolved.append((diagnosis_index, diagnosis))
                continue
            if unresolved_cluster != cluster_ids:
                cluster["failed_checks"] = unresolved_cluster
                diagnosis["failure_cluster"] = cluster
        coverage["residual_requirement_ids"] = [
            item for item in _string_items(coverage.get("residual_requirement_ids", [])) if item not in explained_any
        ]
        if evidence_status == "insufficient":
            coverage["residual_requirement_ids"] = list(
                dict.fromkeys([*coverage["residual_requirement_ids"], *unresolved_cluster])
            )
        partitioned.append(diagnosis)

    for diagnosis_index, redundant in redundant_unresolved:
        redundant_cluster = set(_string_items(_diagnosis_failure_cluster(redundant).get("failed_checks", [])))
        destination = next(
            (
                item
                for item in partitioned
                if redundant_cluster & set(_string_items(_diagnosis_failure_cluster(item).get("failed_checks", [])))
            ),
            None,
        )
        if destination is None:
            partitioned.append(redundant)
            warnings.append(
                f"diagnosis[{diagnosis_index}] retained an unresolved alternative because no "
                "overlapping supported diagnosis could own its hypothesis assessments"
            )
            continue
        destination_assessments = [
            dict(item) for item in destination.get("hypothesis_assessment", []) if isinstance(item, dict)
        ]
        present_ids = {str(item.get("hypothesis_id", "") or "").strip() for item in destination_assessments}
        transferred_ids: list[str] = []
        for assessment in redundant.get("hypothesis_assessment", []):
            if not isinstance(assessment, dict):
                continue
            hypothesis_id = str(assessment.get("hypothesis_id", "") or "").strip()
            if not hypothesis_id or hypothesis_id in present_ids:
                continue
            destination_assessments.append(dict(assessment))
            present_ids.add(hypothesis_id)
            transferred_ids.append(hypothesis_id)
        destination["hypothesis_assessment"] = destination_assessments
        if (
            any(
                str(item.get("status", "") or "").strip().casefold() == "unresolved" for item in destination_assessments
            )
            and str(destination.get("evidence_status", "") or "").strip().casefold() == "confirmed"
        ):
            destination["evidence_status"] = "supported_hypothesis"
            destination["confidence"] = "medium"
            destination_coverage = destination.get("causal_coverage")
            destination_coverage = dict(destination_coverage) if isinstance(destination_coverage, dict) else {}
            destination_coverage["sufficiency_status"] = "local_contributor"
            unexplained = _string_items(destination_coverage.get("unexplained_observations", []))
            unexplained.append("One or more competing causal hypotheses remain unresolved.")
            destination_coverage["unexplained_observations"] = list(dict.fromkeys(unexplained))
            destination["causal_coverage"] = destination_coverage
        warnings.append(
            f"diagnosis[{diagnosis_index}] dropped a redundant unresolved alternative after "
            "transferring its hypothesis assessments" + (f": {', '.join(transferred_ids)}" if transferred_ids else "")
        )
    reconciled = partitioned

    covered_ids: set[str] = set()
    for diagnosis in reconciled:
        coverage = diagnosis.get("causal_coverage")
        if not isinstance(coverage, dict):
            continue
        for key in ("explained_requirement_ids", "residual_requirement_ids"):
            covered_ids.update(_string_items(coverage.get(key, [])))
    missing_coverage = sorted(required_ids - covered_ids)
    if missing_coverage:
        # Preserve the model's semantic fields, but explicitly leave uncovered
        # requirements unassigned rather than inventing a cause.
        template = reconciled[0] if reconciled else (diagnoses[0] if diagnoses else {})
        residual = dict(template)
        residual.update(
            {
                "issue_category": "unassigned",
                "severity": "low",
                "summary": "Residual failed requirements remain causally unresolved.",
                "failure_mode": "unresolved_failed_requirements",
                "failure_cluster": {
                    "failed_checks": missing_coverage,
                    "observable_behavior": "Authoritative requirements remain unmet without a supported mechanism.",
                },
                "evidence_status": "insufficient",
                "failed_requirement": ", ".join(missing_coverage),
                "root_cause": "Available evidence does not establish a causal mechanism for these requirements.",
                "critical_mistake": "No causal decision is established by current evidence.",
                "general_mechanism": "Acquire a discriminator before changing the Harness.",
                "target_ref": "unassigned",
                "selected_hypothesis_id": "",
                "evidence_refs": [],
                "affected_components": [],
                "recommendation": "Collect evidence that distinguishes the planned alternatives.",
                "confidence": "low",
                "hypothesis_assessment": [],
                "causal_coverage": {
                    "explained_requirement_ids": [],
                    "residual_requirement_ids": missing_coverage,
                    "unexplained_observations": ["No supported causal explanation is available."],
                    "causal_chain": [
                        {
                            "cause": "unknown causal mechanism",
                            "effect": "authoritative requirement remains unmet",
                            "evidence_status": "unknown",
                            "evidence_refs": [],
                        }
                    ],
                    "counterfactual_prediction": (
                        "No behavior change is predicted until a causal discriminator is observed."
                    ),
                    "sufficiency_status": "unknown",
                },
                "decision_contract": {
                    "wrong_decision": "No wrong decision is established.",
                    "causal_distinction": "A future discriminator must separate the causal alternatives.",
                    "required_action": "Collect the missing discriminator.",
                    "acceptance_observable": "Each residual hypothesis is supported or falsified by evidence.",
                    "scope_boundary": ["Do not change a Harness surface without causal support."],
                    "activation_phase": "during_investigation",
                },
            }
        )
        reconciled.append(residual)
        warnings.append("materialized unassigned coverage for omitted failed requirements")
    return reconciled, warnings


def _synthesized_prior_experiment_assessment(
    prior_candidate_feedback: dict[str, Any] | None,
) -> dict[str, str]:
    """Preserve experiment existence when the model omits its assessment.

    This fallback never claims semantic activation or causal support.  It only
    records controller-known delivery, activation instrumentation, and score
    direction so one missing JSON field cannot discard an otherwise evidenced
    diagnosis.
    """
    if not isinstance(prior_candidate_feedback, dict):
        return {}
    experiments = prior_candidate_feedback.get("experiments", [])
    records = [item for item in experiments if isinstance(item, dict)] if isinstance(experiments, list) else []
    if not records:
        return {}
    record = records[-1]
    activation = record.get("activation")
    activation = activation if isinstance(activation, dict) else {}
    activation_state = str(activation.get("state", "") or "").strip().casefold()
    if activation_state == "triggered":
        activated = "yes"
    elif activation_state == "not_triggered":
        activated = "no"
    else:
        activated = "unknown"

    observed = record.get("observed_outcome")
    observed = observed if isinstance(observed, dict) else {}
    strict_score = observed.get("strict_score")
    strict_score = strict_score if isinstance(strict_score, dict) else {}
    delta_value = strict_score.get("delta", record.get("target_score_delta"))
    try:
        delta = float(delta_value)
    except (TypeError, ValueError):
        predicted_outcome = "unknown"
    else:
        predicted_outcome = "yes" if delta > 0 else "no"

    causal_status = "not_tested" if activated == "no" else "inconclusive"
    return {
        "availability": "available",
        "intervention_activated": activated,
        "predicted_behavior_occurred": "unknown",
        "predicted_outcome_occurred": predicted_outcome,
        "causal_hypothesis_status": causal_status,
        "reason": (
            "The controller recorded a paired candidate experiment, but the diagnosis "
            "did not provide a valid semantic activation assessment. Delivery and score "
            "direction are preserved without inferring causal support."
        ),
    }


def _normalize_prior_experiment_assessment(
    assessment: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Enforce the activation-before-effect semantics of a causal experiment."""
    normalized = dict(assessment)
    activated = str(normalized.get("intervention_activated", "") or "").strip().casefold()
    behavior = str(normalized.get("predicted_behavior_occurred", "") or "").strip().casefold()
    outcome = str(normalized.get("predicted_outcome_occurred", "") or "").strip().casefold()
    status = str(normalized.get("causal_hypothesis_status", "") or "").strip().casefold()

    corrected_status = status
    correction = ""
    if activated == "no" or behavior == "no":
        corrected_status = "not_tested"
        if status != corrected_status:
            correction = (
                "corrected paired causal status to not_tested because the predicted "
                "intervention behavior did not activate"
            )
    elif activated == "yes" and behavior == "yes" and outcome == "no":
        corrected_status = "falsified"
        if status != corrected_status:
            correction = (
                "corrected paired causal status to falsified because the intervention "
                "behavior occurred without its predicted outcome"
            )
    elif activated == "yes" and behavior == "yes" and outcome == "yes":
        corrected_status = "supported"
        if status != corrected_status:
            correction = (
                "corrected paired causal status to supported because both predicted behavior and outcome occurred"
            )
    elif status not in {"supported", "falsified", "not_tested", "inconclusive"}:
        corrected_status = "inconclusive"
        correction = "corrected invalid paired causal status to inconclusive"

    normalized["causal_hypothesis_status"] = corrected_status
    if correction:
        reason = str(normalized.get("reason", "") or "").strip()
        normalized["reason"] = f"{reason} Controller causal-consistency correction: {correction}.".strip()
    return normalized, correction


def _reconcile_prior_behavior_with_current_failure(
    assessment: dict[str, Any],
    *,
    diagnosis: dict[str, Any],
    prior_candidate_feedback: dict[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    """Do not mistake visible intervention form for its promised behavior.

    A prior prompt can be visibly followed while its causal action is not. When
    the new trajectory still supports the exact controller-frozen failure
    mechanism that the intervention promised to remove, the predicted behavior
    did not occur. This comparison is label-free: it uses the pre-registered
    semantic hypothesis id and the newly observed failure mode, not the score or
    evaluator answer.
    """
    normalized = dict(assessment)
    if str(normalized.get("predicted_behavior_occurred", "") or "").strip().casefold() != "yes":
        return normalized, ""
    failure_mode = str(diagnosis.get("failure_mode", "") or "").strip().casefold()
    evidence_status = str(diagnosis.get("evidence_status", "") or "").strip().casefold()
    if not failure_mode or evidence_status not in {"confirmed", "supported_hypothesis"}:
        return normalized, ""
    if failure_mode not in _prior_causal_failure_modes(prior_candidate_feedback):
        return normalized, ""

    normalized["predicted_behavior_occurred"] = "no"
    normalized["causal_hypothesis_status"] = "not_tested"
    reason = str(normalized.get("reason", "") or "").strip()
    semantic_reason = (
        "Controller semantic replay correction: the candidate trajectory still "
        f"supports the pre-registered failure mechanism `{failure_mode}`. The "
        "intervention's visible form may have appeared, but its promised causal "
        "behavior did not occur, so the hypothesis was not tested."
    )
    normalized["reason"] = f"{reason} {semantic_reason}".strip()
    return normalized, (
        "corrected paired predicted behavior to no because the pre-registered failure mechanism remained supported"
    )


def _prior_causal_failure_modes(
    prior_candidate_feedback: dict[str, Any] | None,
) -> set[str]:
    """Return controller-frozen semantic failure modes from paired contracts."""
    if not isinstance(prior_candidate_feedback, dict):
        return set()
    experiments = prior_candidate_feedback.get("experiments", [])
    if not isinstance(experiments, list):
        return set()
    modes: set[str] = set()
    for experiment in experiments[-3:]:
        if not isinstance(experiment, dict):
            continue
        contracts = experiment.get("causal_intervention_contracts")
        if not isinstance(contracts, list):
            prediction = experiment.get("prediction")
            prediction = prediction if isinstance(prediction, dict) else {}
            contracts = prediction.get("causal_intervention_contracts", [])
        if not isinstance(contracts, list):
            continue
        for contract in contracts:
            if not isinstance(contract, dict):
                continue
            semantic_ids = [
                contract.get("source_causal_hypothesis_semantic_id"),
                *(
                    contract.get("source_causal_hypothesis_semantic_ids", [])
                    if isinstance(contract.get("source_causal_hypothesis_semantic_ids"), list)
                    else []
                ),
            ]
            for semantic_id in semantic_ids:
                value = str(semantic_id or "").strip().casefold()
                if value.startswith("chs:") and len(value) > len("chs:"):
                    modes.add(value.removeprefix("chs:"))
    return modes


def _decision_ground_audit_conflicts(diagnosis: dict[str, Any]) -> list[str]:
    """Validate the structured witness for a narrow decision-ground diagnosis."""
    failure_mode = str(diagnosis.get("failure_mode", "") or "").strip().casefold()
    if failure_mode != "unverified_decision_ground_used":
        return []
    raw_rows = diagnosis.get("decision_ground_audit", [])
    rows = [item for item in raw_rows if isinstance(item, dict)] if isinstance(raw_rows, list) else []
    if not rows:
        return ["unverified_decision_ground_used requires decision_ground_audit"]
    allowed = {
        "materiality": {"material", "non_material", "unknown"},
        "authority_status": {"verified", "missing", "contradicted", "unknown"},
        "scope_status": {"matched", "mismatched", "unknown"},
        "owner_status": {"matched", "mismatched", "unknown"},
        "trigger_status": {"satisfied", "not_satisfied", "not_applicable", "unknown"},
        "entailment_status": {"entailed", "not_entailed", "unknown"},
    }
    errors: list[str] = []
    witnessed_unverified_ground = False
    for index, row in enumerate(rows, start=1):
        if not str(row.get("ground_id", "") or "").strip():
            errors.append(f"decision_ground_audit[{index}] missing ground_id")
        if not str(row.get("ground_text", "") or "").strip():
            errors.append(f"decision_ground_audit[{index}] missing ground_text")
        for field, values in allowed.items():
            value = str(row.get(field, "") or "").strip().casefold()
            if value not in values:
                errors.append(f"decision_ground_audit[{index}].{field} is invalid")
        if not _string_items(row.get("controller_request_ids", [])):
            errors.append(f"decision_ground_audit[{index}] missing controller_request_ids")
        if row.get("used_for_decision") is True and str(row.get("materiality", "")).casefold() == "material":
            chain = {
                "authority": str(row.get("authority_status", "") or "").casefold(),
                "scope": str(row.get("scope_status", "") or "").casefold(),
                "owner": str(row.get("owner_status", "") or "").casefold(),
                "trigger": str(row.get("trigger_status", "") or "").casefold(),
                "entailment": str(row.get("entailment_status", "") or "").casefold(),
            }
            authority_invalid = chain["authority"] in {"missing", "contradicted", "unknown"}
            assignment_invalid = chain["scope"] in {"mismatched", "unknown"} or chain["owner"] in {
                "mismatched",
                "unknown",
            }
            applicability_invalid = chain["trigger"] in {"not_satisfied", "unknown"} or chain["entailment"] in {
                "not_entailed",
                "unknown",
            }
            if authority_invalid or assignment_invalid or applicability_invalid:
                witnessed_unverified_ground = True
    if not witnessed_unverified_ground:
        errors.append("unverified_decision_ground_used lacks a material ground with an incomplete chain")
    return errors


def _diagnosis_validation_conflicts(
    diagnosis: dict[str, Any],
    inventory: dict[str, Any],
    verifier_inventory: dict[str, Any] | None = None,
    *,
    public_task: str | None = None,
    failed_requirement_inventory: dict[str, Any] | None = None,
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
    evidence_status = str(diagnosis.get("evidence_status") or "").strip().lower()
    errors.extend(
        _causal_coverage_validation_conflicts(
            diagnosis,
            failed_requirement_inventory=failed_requirement_inventory,
            evidence_status=evidence_status,
        )
    )
    errors.extend(_decision_ground_audit_conflicts(diagnosis))
    if evidence_status:
        if evidence_status not in {"confirmed", "supported_hypothesis", "insufficient"}:
            errors.append("evidence_status must be confirmed, supported_hypothesis, or insufficient")
        if evidence_status == "insufficient":
            if target_ref != "unassigned":
                errors.append("insufficient evidence must use target_ref=unassigned")
            if str(diagnosis.get("confidence") or "").strip().lower() != "low":
                errors.append("insufficient evidence must use confidence=low")
        if target_ref and target_ref != "unassigned":
            if not str(diagnosis.get("failed_requirement") or "").strip():
                errors.append("assigned diagnosis must name failed_requirement")
            if not str(diagnosis.get("discriminating_evidence") or "").strip():
                errors.append("assigned diagnosis must name discriminating_evidence")
            if not list(diagnosis.get("evidence_refs") or []):
                errors.append("assigned diagnosis must include evidence_refs")
            if not str(decision_contract.get("acceptance_observable") or "").strip():
                errors.append("assigned diagnosis must include decision_contract.acceptance_observable")
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
        candidate = text[slice(match.start(), None)].strip()
        try:
            json.JSONDecoder().raw_decode(candidate)
        except json.JSONDecodeError:
            decode_succeeded = False
        else:
            decode_succeeded = True
        if decode_succeeded:
            continue

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
        structure_incomplete = bool(stack) or in_string or escaped
        if not invalid_closer and structure_incomplete:
            return True
    return False


def _unusable_diagnosis_output_error(case_id: str, outputs: list[str]) -> BaseException:
    """Classify exhausted malformed output without hiding permanent failures."""
    latest = next((value for value in reversed(outputs) if value), "")
    excerpt = _truncate_text(latest, 256)
    if any(_contains_incomplete_json_object(value) for value in outputs):
        return RetryableModelOutputError(
            f"per-case diagnosis output remained incomplete JSON after repair for {case_id}: {excerpt}"
        )
    if _contains_model_service_error_text(latest):
        return ValueError(f"per-case diagnosis output contained a model-service error for {case_id}: {excerpt}")
    return _DiagnosisOutputFormatError(f"per-case diagnosis output did not contain JSON for {case_id}: {excerpt}")


class _DiagnosisOutputFormatError(ValueError):
    """Raised after bounded JSON repair still returns ordinary prose."""


def _contains_model_service_error_text(raw: str) -> bool:
    """Keep permanent service/auth failures distinct from bad model formatting."""
    normalized = " ".join(str(raw or "").lower().split())
    markers = (
        "error code:",
        "invalid_api_key",
        "authentication failed",
        "authentication error",
        "unauthorized",
        "budget_exceeded",
        "budget has been exceeded",
    )
    for marker in markers:
        if marker in normalized:
            return True
    return False


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
        effective_harness = _load_effective_harness_snapshot(invocation.harness_refs_path)
        causal_evidence_path = output_dir / "causal_evidence.json"
        _write_json(
            causal_evidence_path,
            {
                "schema_version": 2,
                "effective_harness": effective_harness,
                "cases": [
                    {
                        "case_id": case.case_id,
                        "causal_digest": _build_causal_evidence_digest(case),
                        "failed_requirement_inventory": _build_failed_requirement_inventory(case),
                        "prior_candidate_feedback": compact_candidate_feedback(
                            _case_prior_candidate_feedback(
                                invocation.prior_candidate_feedback,
                                case.case_id,
                            )
                        ),
                    }
                    for case in diagnosis_case_inputs
                ],
            },
        )
        per_case_results = await self._per_case_diagnosis(
            diagnosis_case_inputs,
            signals,
            retrieved_experience,
            source_stage=invocation.source_stage,
            prior_candidate_feedback=invocation.prior_candidate_feedback,
            effective_harness=effective_harness,
        )
        per_case_diagnoses_path = output_dir / "per_case_diagnoses.json"
        _write_json(per_case_diagnoses_path, {"per_case_diagnoses": per_case_results})
        failed_case_ids: set[str] = set()
        evidence_supplemented_case_ids: set[str] = set()
        evidence_supplement_resolved_case_ids: set[str] = set()
        investigated_case_ids: set[str] = set()
        for item in per_case_results:
            case_id = str(item.get("case_id", "") or "")
            if item.get("analysis_failed"):
                failed_case_ids.add(case_id)
            supplement = item.get("evidence_supplement")
            if isinstance(supplement, dict):
                if supplement.get("attempted"):
                    evidence_supplemented_case_ids.add(case_id)
                if supplement.get("status") == "resolved":
                    evidence_supplement_resolved_case_ids.add(case_id)
            investigation_record = item.get("causal_investigation")
            if isinstance(investigation_record, dict) and investigation_record.get("planning_status") == "completed":
                investigated_case_ids.add(case_id)
        diagnosis_failed_count = len(failed_case_ids)
        evidence_request_count = sum(
            int(item["causal_investigation"].get("evidence_request_count", 0) or 0)
            for item in per_case_results
            if isinstance(item.get("causal_investigation"), dict)
        )

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
                "analyzer_protocol_version": GENERIC_ANALYZER_PROTOCOL_VERSION,
                "model_config_ref": self._config.diagnosis_agent_model_config_ref or self._config.model_config_ref,
                "signals_method": signals.method,
                "per_case_count": len(case_inputs),
                "diagnosed_case_count": len(diagnosis_case_inputs),
                "diagnosis_count": len(per_case_results),
                "diagnosis_failed_count": diagnosis_failed_count,
                "evidence_supplemented_case_count": len(evidence_supplemented_case_ids),
                "evidence_supplement_resolved_case_count": len(evidence_supplement_resolved_case_ids),
                "causal_investigated_case_count": len(investigated_case_ids),
                "causal_evidence_request_count": evidence_request_count,
                "per_case_diagnoses_path": str(per_case_diagnoses_path),
                "causal_evidence_path": str(causal_evidence_path),
                "effective_harness_availability": effective_harness.get("availability", "unknown"),
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
        model_data = without_inner_sdk_retries(ref_data.get("model", ref_data))
        request_data = model_data.setdefault("model_request_config", {})
        if isinstance(request_data, dict):
            configured = request_data.get("max_tokens")
            configured = configured if isinstance(configured, int) and not isinstance(configured, bool) else 0
            request_data["max_tokens"] = max(
                1024,
                configured,
                self._config.diagnosis_agent_max_tokens,
            )
        model_config = TeamModelConfig.model_validate(model_data)
        model = model_config.build()

        return create_deep_agent(
            model=model,
            card=AgentCard(name="diagnosis_agent", description="Evaluation result diagnosis agent"),
            system_prompt=system_prompt if system_prompt is not None else DIAGNOSIS_SYSTEM_PROMPT,
            workspace=workspace,
            restrict_to_work_dir=True,
            max_iterations=self._config.diagnosis_agent_max_iterations,
            auto_create_workspace=False,
            rails=[],
            enable_sys_operation=False,
        )

    async def _per_case_diagnosis(
        self,
        case_inputs: list[CaseAnalysisInput],
        signals: DeterministicSignals,
        retrieved_experience: dict[str, Any] | None,
        *,
        source_stage: str = "",
        prior_candidate_feedback: dict[str, Any] | None = None,
        effective_harness: dict[str, Any] | None = None,
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
                diagnosis_input = _build_diagnosis_input_json(
                    case=case,
                    signals=signals,
                    retrieved_experience=retrieved_experience,
                    evidence_summary_available=evidence_summary_available,
                    source_stage=source_stage,
                    prior_candidate_feedback=case_feedback,
                    effective_harness=effective_harness,
                )
                prompt = _build_diagnosis_prompt(
                    case=case,
                    signals=signals,
                    retrieved_experience=retrieved_experience,
                    evidence_summary_available=evidence_summary_available,
                    source_stage=source_stage,
                    prior_candidate_feedback=case_feedback,
                    effective_harness=effective_harness,
                )
                agent = await self._build_agent(str(runtime_dir))
                first_raw = await _run_agent(
                    agent,
                    _build_causal_investigation_prompt(diagnosis_input),
                    max_retries=self._config.diagnosis_agent_max_retries,
                )
                first_parsed = _extract_json_object(first_raw)
                failed_requirement_inventory = _build_failed_requirement_inventory(case)
                requirement_ids = [
                    str(item.get("requirement_id", "") or "")
                    for item in failed_requirement_inventory.get("items", [])
                    if isinstance(item, dict)
                ]
                investigation, plan_dependency_conflicts = _normalize_outcome_independent_causal_plan(
                    first_parsed,
                    failed_requirement_ids=requirement_ids,
                )
                retained_plan = _outcome_independent_causal_plan(first_parsed)
                strict_plan_correction_attempted = False
                outcome_independent_recovery_attempted = False
                recovery_plan_raw = ""
                recovery_plan_parsed: dict[str, Any] | None = None
                if investigation is None and self._config.causal_investigation_required:
                    strict_plan_correction_attempted = True
                    corrected_plan_raw = await _run_agent(
                        agent,
                        _build_causal_plan_correction_prompt(
                            diagnosis_input,
                            first_raw,
                            validation_conflicts=plan_dependency_conflicts,
                        ),
                        max_retries=self._config.diagnosis_agent_max_retries,
                    )
                    corrected_plan_parsed = _extract_json_object(corrected_plan_raw)
                    investigation, corrected_dependency_conflicts = _normalize_outcome_independent_causal_plan(
                        corrected_plan_parsed,
                        failed_requirement_ids=requirement_ids,
                    )
                    corrected_retained_plan = _outcome_independent_causal_plan(corrected_plan_parsed)
                    retained_plan = max(
                        (item for item in (retained_plan, corrected_retained_plan) if item is not None),
                        key=lambda item: len((_raw_causal_plan(item) or {}).get("hypotheses", [])),
                        default=None,
                    )
                    if corrected_dependency_conflicts:
                        plan_dependency_conflicts = list(
                            dict.fromkeys([*plan_dependency_conflicts, *corrected_dependency_conflicts])
                        )
                    if investigation is None:
                        outcome_independent_recovery_attempted = True
                        recovery_plan_raw = await _run_agent(
                            agent,
                            _build_outcome_independent_causal_plan_recovery_prompt(
                                diagnosis_input,
                                failed_requirement_ids=requirement_ids,
                                validation_conflicts=plan_dependency_conflicts,
                                retained_plan=retained_plan,
                            ),
                            max_retries=self._config.diagnosis_agent_max_retries,
                        )
                        recovery_plan_parsed = _extract_json_object(recovery_plan_raw)
                        raw_recovery_conflicts = [
                            *_causal_plan_outcome_dependency_conflicts(_raw_causal_plan(recovery_plan_parsed)),
                            *_causal_plan_task_visibility_conflicts(recovery_plan_parsed),
                        ]
                        merged_recovery_plan = _merge_outcome_independent_causal_plans(
                            retained_plan,
                            recovery_plan_parsed,
                        )
                        investigation, recovery_dependency_conflicts = _normalize_outcome_independent_causal_plan(
                            merged_recovery_plan,
                            failed_requirement_ids=requirement_ids,
                        )
                        if raw_recovery_conflicts or recovery_dependency_conflicts:
                            plan_dependency_conflicts = list(
                                dict.fromkeys(
                                    [
                                        *plan_dependency_conflicts,
                                        *raw_recovery_conflicts,
                                        *recovery_dependency_conflicts,
                                    ]
                                )
                            )
                    if investigation is None:
                        if first_parsed is None and corrected_plan_parsed is None and recovery_plan_parsed is None:
                            raise _unusable_diagnosis_output_error(
                                case.case_id,
                                [first_raw, corrected_plan_raw, recovery_plan_raw],
                            )
                        logger.warning(
                            "per-case causal investigation plan unavailable for %s after phase correction",
                            case.case_id,
                        )
                        result = _diagnosis_evidence_conflict_result(
                            case,
                            plan_dependency_conflicts
                            or ["diagnosis model skipped the required causal investigation phase"],
                        )
                        result["causal_investigation"] = {
                            "protocol_version": 1,
                            "planning_status": "outcome_independent_recovery_exhausted",
                            "hypothesis_count": 0,
                            "evidence_request_count": 0,
                            "completed_evidence_request_count": 0,
                            "strict_plan_correction_attempted": strict_plan_correction_attempted,
                            "outcome_independent_recovery_attempted": outcome_independent_recovery_attempted,
                            "retained_hypothesis_ids": [
                                str(item.get("hypothesis_id", "") or "")
                                for item in (_raw_causal_plan(retained_plan) or {}).get("hypotheses", [])
                                if isinstance(item, dict)
                            ],
                            "validation_conflicts": plan_dependency_conflicts,
                            "termination_reason": "no_valid_task_visible_causal_plan_after_bounded_recovery",
                        }
                        return [result]
                investigation_record: dict[str, Any] = {
                    "protocol_version": 1,
                    "planning_status": "legacy_diagnosis_fallback",
                    "hypothesis_count": 0,
                    "evidence_request_count": 0,
                    "completed_evidence_request_count": 0,
                    "strict_plan_correction_attempted": strict_plan_correction_attempted,
                    "outcome_independent_recovery_attempted": outcome_independent_recovery_attempted,
                }
                evidence_results: dict[str, Any] = {}
                if investigation is not None:
                    evidence_results = execute_causal_investigation(
                        case,
                        investigation,
                        prior_candidate_feedback=case_feedback,
                        evidence_root=runtime_dir,
                    )
                    investigation = _adopt_automatic_evidence_requests(investigation, evidence_results)
                    investigation_record.update(
                        {
                            "planning_status": "completed",
                            "refinement_attempted": False,
                            "refinement_status": "not_needed",
                            "refinement_request_count": 0,
                            "hypothesis_count": len(investigation.get("hypotheses", [])),
                            "evidence_request_count": int(evidence_results.get("request_count", 0) or 0),
                            "completed_evidence_request_count": int(
                                evidence_results.get("completed_request_count", 0) or 0
                            ),
                            "hypotheses": list(investigation.get("hypotheses", [])),
                            "request_results": [
                                {
                                    "request_id": str(item.get("request_id", "") or ""),
                                    "operation": str(item.get("operation", "") or ""),
                                    "availability": str(item.get("availability", "") or ""),
                                }
                                for item in evidence_results.get("results", [])
                                if isinstance(item, dict)
                            ],
                        }
                    )
                    diagnosis_prompt_used = _build_investigation_diagnosis_prompt(
                        original_prompt=prompt,
                        investigation=investigation,
                        evidence_results=evidence_results,
                    )
                    raw = await _run_agent(
                        agent,
                        diagnosis_prompt_used,
                        max_retries=self._config.diagnosis_agent_max_retries,
                    )
                    draft_parsed = _extract_json_object(raw)
                    draft_diagnoses = (
                        _normalize_case_diagnoses(
                            draft_parsed,
                            prior_candidate_feedback=case_feedback,
                        )
                        if draft_parsed is not None
                        else []
                    )
                    refinement_rounds: list[dict[str, Any]] = []
                    while _diagnoses_need_causal_refinement(draft_diagnoses):
                        if len(investigation.get("evidence_requests", [])) >= _CAUSAL_TOTAL_REQUEST_LIMIT:
                            investigation_record["refinement_termination_reason"] = "request_budget_exhausted"
                            break
                        if len(refinement_rounds) >= _CAUSAL_CLOSURE_MAX_ROUNDS:
                            investigation_record["refinement_termination_reason"] = "round_budget_exhausted"
                            break
                        investigation_record["refinement_attempted"] = True
                        round_record: dict[str, Any] = {"round": len(refinement_rounds) + 1}
                        refinement_raw = await _run_agent(
                            agent,
                            _build_causal_refinement_prompt(
                                investigation=investigation,
                                evidence_results=evidence_results,
                                draft_diagnoses=draft_diagnoses,
                            ),
                            max_retries=self._config.diagnosis_agent_max_retries,
                        )
                        refinement_parsed = _extract_json_object(refinement_raw)
                        refinement = _normalize_causal_refinement(
                            refinement_parsed,
                            base=investigation,
                            failed_requirement_ids=requirement_ids,
                        )
                        if refinement is None:
                            round_record["status"] = "invalid_plan"
                            refinement_rounds.append(round_record)
                            investigation_record["refinement_status"] = "invalid_plan"
                            investigation_record["refinement_termination_reason"] = "invalid_plan"
                            break
                        merged_investigation, added_requests = _merge_causal_investigation(
                            investigation,
                            refinement,
                        )
                        if not added_requests:
                            round_record["status"] = "no_new_allowed_request"
                            refinement_rounds.append(round_record)
                            investigation_record["refinement_status"] = "no_new_allowed_request"
                            investigation_record["refinement_termination_reason"] = "no_new_legal_request"
                            break
                        refinement_evidence = execute_causal_investigation(
                            case,
                            {
                                "hypotheses": merged_investigation.get("hypotheses", []),
                                "evidence_requests": added_requests,
                            },
                            prior_candidate_feedback=case_feedback,
                            evidence_root=runtime_dir,
                        )
                        merged_investigation = _adopt_automatic_evidence_requests(
                            merged_investigation,
                            refinement_evidence,
                        )
                        investigation = merged_investigation
                        evidence_results = _merge_causal_evidence_results(
                            evidence_results,
                            refinement_evidence,
                        )
                        round_record.update(
                            {
                                "status": "completed",
                                "request_count": len(added_requests),
                                "request_ids": [str(item.get("request_id", "") or "") for item in added_requests],
                            }
                        )
                        refinement_rounds.append(round_record)
                        investigation_record.update(
                            {
                                "refinement_status": "completed",
                                "refinement_request_count": sum(
                                    int(item.get("request_count", 0) or 0) for item in refinement_rounds
                                ),
                                "hypothesis_count": len(investigation.get("hypotheses", [])),
                                "hypotheses": list(investigation.get("hypotheses", [])),
                                "evidence_request_count": len(investigation.get("evidence_requests", [])),
                                "completed_evidence_request_count": int(
                                    evidence_results.get("completed_request_count", 0) or 0
                                ),
                                "request_results": [
                                    {
                                        "request_id": str(item.get("request_id", "") or ""),
                                        "operation": str(item.get("operation", "") or ""),
                                        "availability": str(item.get("availability", "") or ""),
                                    }
                                    for item in evidence_results.get("results", [])
                                    if isinstance(item, dict)
                                ],
                            }
                        )
                        diagnosis_prompt_used = _build_investigation_diagnosis_prompt(
                            original_prompt=prompt,
                            investigation=investigation,
                            evidence_results=evidence_results,
                        )
                        raw = await _run_agent(
                            agent,
                            diagnosis_prompt_used,
                            max_retries=self._config.diagnosis_agent_max_retries,
                        )
                        draft_parsed = _extract_json_object(raw)
                        draft_diagnoses = (
                            _normalize_case_diagnoses(
                                draft_parsed,
                                prior_candidate_feedback=case_feedback,
                            )
                            if draft_parsed is not None
                            else []
                        )
                    if refinement_rounds:
                        investigation_record["refinement_rounds"] = refinement_rounds
                    if not _diagnoses_need_causal_refinement(draft_diagnoses):
                        investigation_record["refinement_termination_reason"] = "diagnosis_sufficient"
                else:
                    diagnosis_prompt_used = prompt
                    raw = first_raw
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
                    raise _unusable_diagnosis_output_error(case.case_id, invalid_outputs)
                validation_inventory = _build_validation_inventory(case)
                verifier_inventory = _build_verifier_inventory(case)
                validation_conflicts = _case_diagnoses_validation_conflicts(
                    diagnoses,
                    validation_inventory,
                    verifier_inventory,
                    failed_requirement_inventory,
                )
                if investigation is not None:
                    validation_conflicts.extend(
                        _causal_investigation_conflicts(
                            diagnoses,
                            investigation,
                            evidence_results=evidence_results,
                            prior_candidate_feedback=case_feedback,
                        )
                    )
                if validation_conflicts and investigation is not None:
                    reconciled_diagnoses, reconciliation_warnings = _reconcile_causal_assessments(
                        diagnoses,
                        investigation,
                        evidence_results=evidence_results,
                        failed_requirement_inventory=failed_requirement_inventory,
                        prior_candidate_feedback=case_feedback,
                    )
                    reconciled_conflicts = _case_diagnoses_validation_conflicts(
                        reconciled_diagnoses,
                        validation_inventory,
                        verifier_inventory,
                        failed_requirement_inventory,
                    )
                    reconciled_conflicts.extend(
                        _causal_investigation_conflicts(
                            reconciled_diagnoses,
                            investigation,
                            evidence_results=evidence_results,
                            prior_candidate_feedback=case_feedback,
                        )
                    )
                    investigation_record["deterministic_reconciliation"] = {
                        "attempted": True,
                        "phase": "before_model_repair",
                        "warnings": reconciliation_warnings,
                        "remaining_conflicts": reconciled_conflicts,
                    }
                    if not reconciled_conflicts:
                        diagnoses = reconciled_diagnoses
                        validation_conflicts = []
                if validation_conflicts:
                    repair_raw = await _run_agent(
                        agent,
                        _build_evidence_conflict_repair_prompt(
                            original_prompt=diagnosis_prompt_used,
                            previous_output=raw,
                            conflicts=validation_conflicts,
                            validation_inventory=validation_inventory,
                            verifier_inventory=verifier_inventory,
                            failed_requirement_inventory=failed_requirement_inventory,
                            causal_evidence_results=evidence_results,
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
                                failed_requirement_inventory,
                            )
                            if investigation is not None:
                                repaired_conflicts.extend(
                                    _causal_investigation_conflicts(
                                        repair_diagnoses,
                                        investigation,
                                        evidence_results=evidence_results,
                                        prior_candidate_feedback=case_feedback,
                                    )
                                )
                            raw = repair_raw
                            parsed = repair_parsed
                            diagnoses = repair_diagnoses
                            if not repaired_conflicts:
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
                if validation_conflicts and investigation is not None:
                    reconciled_diagnoses, reconciliation_warnings = _reconcile_causal_assessments(
                        diagnoses,
                        investigation,
                        evidence_results=evidence_results,
                        failed_requirement_inventory=failed_requirement_inventory,
                        prior_candidate_feedback=case_feedback,
                    )
                    reconciled_conflicts = _case_diagnoses_validation_conflicts(
                        reconciled_diagnoses,
                        validation_inventory,
                        verifier_inventory,
                        failed_requirement_inventory,
                    )
                    reconciled_conflicts.extend(
                        _causal_investigation_conflicts(
                            reconciled_diagnoses,
                            investigation,
                            evidence_results=evidence_results,
                            prior_candidate_feedback=case_feedback,
                        )
                    )
                    investigation_record["deterministic_reconciliation"] = {
                        "attempted": True,
                        "phase": "after_model_repair",
                        "warnings": reconciliation_warnings,
                        "remaining_conflicts": reconciled_conflicts,
                    }
                    if not reconciled_conflicts:
                        diagnoses = reconciled_diagnoses
                        validation_conflicts = []
                can_refine = False
                if investigation is not None:
                    conflicts_need_evidence = _causal_conflicts_need_more_evidence(validation_conflicts)
                    conflicts_allow_refinement = not validation_conflicts or conflicts_need_evidence
                    refinement_not_attempted = not bool(investigation_record.get("refinement_attempted"))
                    within_request_budget = (
                        len(investigation.get("evidence_requests", [])) < _CAUSAL_TOTAL_REQUEST_LIMIT
                    )
                    diagnosis_needs_refinement = _diagnoses_need_causal_refinement(
                        diagnoses,
                        failed_requirement_ids=requirement_ids,
                    )
                    can_refine = (
                        conflicts_allow_refinement
                        and refinement_not_attempted
                        and within_request_budget
                        and (diagnosis_needs_refinement or conflicts_need_evidence)
                    )
                if can_refine:
                    investigation_record["refinement_attempted"] = True
                    refinement_raw = await _run_agent(
                        agent,
                        _build_causal_refinement_prompt(
                            investigation=investigation,
                            evidence_results=evidence_results,
                            draft_diagnoses=diagnoses,
                        ),
                        max_retries=self._config.diagnosis_agent_max_retries,
                    )
                    refinement = _normalize_causal_refinement(
                        _extract_json_object(refinement_raw),
                        base=investigation,
                        failed_requirement_ids=requirement_ids,
                    )
                    if refinement is None:
                        investigation_record["refinement_status"] = "invalid_plan"
                    else:
                        merged_investigation, added_requests = _merge_causal_investigation(
                            investigation,
                            refinement,
                        )
                        if not added_requests:
                            investigation_record["refinement_status"] = "no_new_allowed_request"
                        else:
                            refinement_evidence = execute_causal_investigation(
                                case,
                                {
                                    "hypotheses": merged_investigation.get("hypotheses", []),
                                    "evidence_requests": added_requests,
                                },
                                prior_candidate_feedback=case_feedback,
                                evidence_root=runtime_dir,
                            )
                            merged_investigation = _adopt_automatic_evidence_requests(
                                merged_investigation,
                                refinement_evidence,
                            )
                            investigation = merged_investigation
                            evidence_results = _merge_causal_evidence_results(
                                evidence_results,
                                refinement_evidence,
                            )
                            investigation_record.update(
                                {
                                    "refinement_status": "completed",
                                    "refinement_request_count": len(added_requests),
                                    "hypothesis_count": len(investigation.get("hypotheses", [])),
                                    "hypotheses": list(investigation.get("hypotheses", [])),
                                    "evidence_request_count": len(investigation.get("evidence_requests", [])),
                                    "completed_evidence_request_count": int(
                                        evidence_results.get("completed_request_count", 0) or 0
                                    ),
                                    "request_results": [
                                        {
                                            "request_id": str(item.get("request_id", "") or ""),
                                            "operation": str(item.get("operation", "") or ""),
                                            "availability": str(item.get("availability", "") or ""),
                                        }
                                        for item in evidence_results.get("results", [])
                                        if isinstance(item, dict)
                                    ],
                                }
                            )
                            refined_prompt = _build_investigation_diagnosis_prompt(
                                original_prompt=prompt,
                                investigation=investigation,
                                evidence_results=evidence_results,
                            )
                            refined_raw = await _run_agent(
                                agent,
                                refined_prompt,
                                max_retries=self._config.diagnosis_agent_max_retries,
                            )
                            refined_parsed = _extract_json_object(refined_raw)
                            refined_diagnoses = (
                                _normalize_case_diagnoses(
                                    refined_parsed,
                                    prior_candidate_feedback=case_feedback,
                                )
                                if refined_parsed is not None
                                else []
                            )
                            refined_conflicts = (
                                _case_diagnoses_validation_conflicts(
                                    refined_diagnoses,
                                    validation_inventory,
                                    verifier_inventory,
                                    failed_requirement_inventory,
                                )
                                if refined_diagnoses
                                else ["refined causal diagnosis contained no diagnoses"]
                            )
                            if refined_diagnoses:
                                refined_conflicts.extend(
                                    _causal_investigation_conflicts(
                                        refined_diagnoses,
                                        investigation,
                                        evidence_results=evidence_results,
                                        prior_candidate_feedback=case_feedback,
                                    )
                                )
                            if refined_conflicts and refined_diagnoses:
                                reconciled_refined, refined_warnings = _reconcile_causal_assessments(
                                    refined_diagnoses,
                                    investigation,
                                    evidence_results=evidence_results,
                                    failed_requirement_inventory=failed_requirement_inventory,
                                    prior_candidate_feedback=case_feedback,
                                )
                                reconciled_refined_conflicts = _case_diagnoses_validation_conflicts(
                                    reconciled_refined,
                                    validation_inventory,
                                    verifier_inventory,
                                    failed_requirement_inventory,
                                )
                                reconciled_refined_conflicts.extend(
                                    _causal_investigation_conflicts(
                                        reconciled_refined,
                                        investigation,
                                        evidence_results=evidence_results,
                                        prior_candidate_feedback=case_feedback,
                                    )
                                )
                                investigation_record["refinement_reconciliation"] = {
                                    "attempted": True,
                                    "warnings": refined_warnings,
                                    "remaining_conflicts": reconciled_refined_conflicts,
                                }
                                if not reconciled_refined_conflicts:
                                    refined_diagnoses = reconciled_refined
                                    refined_conflicts = []
                            if refined_conflicts:
                                investigation_record["refinement_status"] = "diagnosis_rejected"
                                investigation_record["refinement_rejection_reason"] = "; ".join(refined_conflicts)
                            else:
                                raw = refined_raw
                                parsed = refined_parsed
                                diagnoses = refined_diagnoses
                                diagnosis_prompt_used = refined_prompt
                                validation_conflicts = []
                decision_ground_audit_attempts: list[dict[str, Any]] = []
                if investigation is not None and not validation_conflicts:
                    investigation, evidence_results, trace_probe_ids = _supplement_decision_ground_trace_evidence(
                        case,
                        diagnoses=diagnoses,
                        investigation=investigation,
                        evidence_results=evidence_results,
                    )
                    if trace_probe_ids:
                        investigation_record.update(
                            {
                                "decision_ground_trace_request_ids": trace_probe_ids,
                                "evidence_request_count": len(investigation.get("evidence_requests", [])),
                                "completed_evidence_request_count": int(
                                    evidence_results.get("completed_request_count", 0) or 0
                                ),
                                "request_results": [
                                    {
                                        "request_id": str(item.get("request_id", "") or ""),
                                        "operation": str(item.get("operation", "") or ""),
                                        "availability": str(item.get("availability", "") or ""),
                                    }
                                    for item in evidence_results.get("results", [])
                                    if isinstance(item, dict)
                                ],
                            }
                        )
                    diagnoses, investigation, decision_ground_audit = await _run_decision_ground_entailment_audit(
                        agent,
                        diagnoses=diagnoses,
                        investigation=investigation,
                        evidence_results=evidence_results,
                    )
                    if decision_ground_audit.get("attempted"):
                        decision_ground_audit_attempts.append(decision_ground_audit)
                    if decision_ground_audit.get("status") == "approved":
                        diagnoses, ground_audit_warnings = _reconcile_causal_assessments(
                            diagnoses,
                            investigation,
                            evidence_results=evidence_results,
                            failed_requirement_inventory=failed_requirement_inventory,
                            prior_candidate_feedback=case_feedback,
                        )
                        investigation_record.update(
                            {
                                "hypothesis_count": len(investigation.get("hypotheses", [])),
                                "hypotheses": list(investigation.get("hypotheses", [])),
                                "decision_ground_reconciliation_warnings": ground_audit_warnings,
                            }
                        )
                entailment_audit_attempts: list[dict[str, Any]] = []
                if (
                    investigation is not None
                    and not validation_conflicts
                    and _hypothesis_assessments_need_independent_audit(diagnoses)
                ):
                    entailment_raw = await _run_agent(
                        agent,
                        _build_hypothesis_entailment_audit_prompt(
                            diagnoses=diagnoses,
                            investigation=investigation,
                            evidence_results=evidence_results,
                        ),
                        max_retries=0,
                        json_repair_prompt_builder=_build_hypothesis_entailment_audit_json_repair_prompt,
                    )
                    entailment_audit = _normalize_hypothesis_entailment_audit(
                        _extract_json_object(entailment_raw),
                        diagnoses=diagnoses,
                    )
                    entailment_audit_attempts.append(entailment_audit)
                    diagnoses = _apply_hypothesis_entailment_audit(diagnoses, entailment_audit)
                    diagnoses, entailment_warnings = _reconcile_causal_assessments(
                        diagnoses,
                        investigation,
                        evidence_results=evidence_results,
                        failed_requirement_inventory=failed_requirement_inventory,
                        prior_candidate_feedback=case_feedback,
                    )
                    investigation_record["entailment_reconciliation_warnings"] = entailment_warnings
                post_validation_rounds: list[dict[str, Any]] = []
                while (
                    investigation is not None
                    and not validation_conflicts
                    and _diagnoses_need_causal_refinement(
                        diagnoses,
                        failed_requirement_ids=requirement_ids,
                    )
                ):
                    if len(investigation.get("evidence_requests", [])) >= _CAUSAL_TOTAL_REQUEST_LIMIT:
                        investigation_record["closure_termination_reason"] = "request_budget_exhausted"
                        break
                    if len(post_validation_rounds) >= _CAUSAL_CLOSURE_MAX_ROUNDS:
                        investigation_record["closure_termination_reason"] = "round_budget_exhausted"
                        break
                    closure_round: dict[str, Any] = {"round": len(post_validation_rounds) + 1}
                    closure_raw = await _run_agent(
                        agent,
                        _build_causal_refinement_prompt(
                            investigation=investigation,
                            evidence_results=evidence_results,
                            draft_diagnoses=diagnoses,
                        ),
                        max_retries=self._config.diagnosis_agent_max_retries,
                    )
                    closure_refinement = _normalize_causal_refinement(
                        _extract_json_object(closure_raw),
                        base=investigation,
                        failed_requirement_ids=requirement_ids,
                    )
                    if closure_refinement is None:
                        closure_round["status"] = "invalid_plan"
                        post_validation_rounds.append(closure_round)
                        investigation_record["closure_termination_reason"] = "invalid_plan"
                        break
                    merged_investigation, added_requests = _merge_causal_investigation(
                        investigation,
                        closure_refinement,
                    )
                    if not added_requests:
                        closure_round["status"] = "no_new_allowed_request"
                        post_validation_rounds.append(closure_round)
                        investigation_record["closure_termination_reason"] = "no_new_legal_request"
                        break
                    closure_evidence = execute_causal_investigation(
                        case,
                        {
                            "hypotheses": merged_investigation.get("hypotheses", []),
                            "evidence_requests": added_requests,
                        },
                        prior_candidate_feedback=case_feedback,
                        evidence_root=runtime_dir,
                    )
                    merged_investigation = _adopt_automatic_evidence_requests(
                        merged_investigation,
                        closure_evidence,
                    )
                    investigation = merged_investigation
                    evidence_results = _merge_causal_evidence_results(
                        evidence_results,
                        closure_evidence,
                    )
                    closure_round.update(
                        {
                            "status": "evidence_executed",
                            "request_count": len(added_requests),
                            "request_ids": [str(item.get("request_id", "") or "") for item in added_requests],
                        }
                    )
                    investigation_record.update(
                        {
                            "refinement_attempted": True,
                            "refinement_status": "completed",
                            "hypotheses": list(investigation.get("hypotheses", [])),
                            "evidence_request_count": len(investigation.get("evidence_requests", [])),
                            "completed_evidence_request_count": int(
                                evidence_results.get("completed_request_count", 0) or 0
                            ),
                            "request_results": [
                                {
                                    "request_id": str(item.get("request_id", "") or ""),
                                    "operation": str(item.get("operation", "") or ""),
                                    "availability": str(item.get("availability", "") or ""),
                                }
                                for item in evidence_results.get("results", [])
                                if isinstance(item, dict)
                            ],
                        }
                    )
                    closure_diagnosis_prompt = _build_investigation_diagnosis_prompt(
                        original_prompt=prompt,
                        investigation=investigation,
                        evidence_results=evidence_results,
                    )
                    closure_diagnosis_raw = await _run_agent(
                        agent,
                        closure_diagnosis_prompt,
                        max_retries=self._config.diagnosis_agent_max_retries,
                    )
                    closure_parsed = _extract_json_object(closure_diagnosis_raw)
                    closure_diagnoses = (
                        _normalize_case_diagnoses(
                            closure_parsed,
                            prior_candidate_feedback=case_feedback,
                        )
                        if closure_parsed is not None
                        else []
                    )
                    if not closure_diagnoses:
                        closure_round["status"] = "diagnosis_invalid"
                        post_validation_rounds.append(closure_round)
                        investigation_record["closure_termination_reason"] = "diagnosis_invalid"
                        break
                    closure_diagnoses, closure_warnings = _reconcile_causal_assessments(
                        closure_diagnoses,
                        investigation,
                        evidence_results=evidence_results,
                        failed_requirement_inventory=failed_requirement_inventory,
                        prior_candidate_feedback=case_feedback,
                    )
                    if _hypothesis_assessments_need_independent_audit(closure_diagnoses):
                        closure_entailment_raw = await _run_agent(
                            agent,
                            _build_hypothesis_entailment_audit_prompt(
                                diagnoses=closure_diagnoses,
                                investigation=investigation,
                                evidence_results=evidence_results,
                            ),
                            max_retries=0,
                            json_repair_prompt_builder=_build_hypothesis_entailment_audit_json_repair_prompt,
                        )
                        closure_entailment_audit = _normalize_hypothesis_entailment_audit(
                            _extract_json_object(closure_entailment_raw),
                            diagnoses=closure_diagnoses,
                        )
                        entailment_audit_attempts.append(closure_entailment_audit)
                        closure_round["hypothesis_entailment_audit"] = closure_entailment_audit
                        closure_diagnoses = _apply_hypothesis_entailment_audit(
                            closure_diagnoses,
                            closure_entailment_audit,
                        )
                        closure_diagnoses, audit_warnings = _reconcile_causal_assessments(
                            closure_diagnoses,
                            investigation,
                            evidence_results=evidence_results,
                            failed_requirement_inventory=failed_requirement_inventory,
                            prior_candidate_feedback=case_feedback,
                        )
                        closure_warnings.extend(audit_warnings)
                    closure_diagnoses = _preserve_canonical_decision_ground_diagnoses(
                        diagnoses,
                        closure_diagnoses,
                    )
                    closure_conflicts = _case_diagnoses_validation_conflicts(
                        closure_diagnoses,
                        validation_inventory,
                        verifier_inventory,
                        failed_requirement_inventory,
                    )
                    closure_conflicts.extend(
                        _causal_investigation_conflicts(
                            closure_diagnoses,
                            investigation,
                            evidence_results=evidence_results,
                            prior_candidate_feedback=case_feedback,
                        )
                    )
                    closure_round["reconciliation_warnings"] = closure_warnings
                    if closure_conflicts:
                        closure_round["status"] = "diagnosis_rejected"
                        closure_round["conflicts"] = closure_conflicts
                        post_validation_rounds.append(closure_round)
                        investigation_record["closure_termination_reason"] = "diagnosis_conflict"
                        break
                    raw = closure_diagnosis_raw
                    parsed = closure_parsed
                    diagnoses = closure_diagnoses
                    diagnosis_prompt_used = closure_diagnosis_prompt
                    closure_round["status"] = "rediagnosed"
                    post_validation_rounds.append(closure_round)
                if post_validation_rounds:
                    investigation_record["closure_rounds"] = post_validation_rounds
                if entailment_audit_attempts:
                    investigation_record["independent_hypothesis_entailment_audit"] = {
                        "attempted": True,
                        "attempt_count": len(entailment_audit_attempts),
                        "attempts": entailment_audit_attempts,
                        "status": entailment_audit_attempts[-1]["status"],
                    }
                if decision_ground_audit_attempts:
                    investigation_record["independent_decision_ground_audit"] = {
                        "attempted": True,
                        "attempt_count": len(decision_ground_audit_attempts),
                        "attempts": decision_ground_audit_attempts,
                        "status": decision_ground_audit_attempts[-1]["status"],
                    }
                if investigation is not None and not _diagnoses_need_causal_refinement(
                    diagnoses,
                    failed_requirement_ids=requirement_ids,
                ):
                    investigation_record["closure_termination_reason"] = "diagnosis_sufficient"
                if validation_conflicts:
                    logger.warning(
                        "per-case diagnosis evidence conflict for %s after repair: %s",
                        case.case_id,
                        "; ".join(validation_conflicts),
                    )
                    conflict_result = _diagnosis_evidence_conflict_result(
                        case,
                        validation_conflicts,
                    )
                    conflict_result["causal_investigation"] = dict(investigation_record)
                    return [conflict_result]
                diagnosis_still_unresolved = _diagnoses_need_causal_refinement(
                    diagnoses,
                    failed_requirement_ids=requirement_ids,
                )
                closure = (
                    evidence_results.get("artifact_evidence_closure", {}) if isinstance(evidence_results, dict) else {}
                )
                if investigation is not None and bool(closure.get("attempted")):
                    closure_status = str(closure.get("status", "") or "incomplete")
                    evidence_supplement = {
                        "attempted": True,
                        "status": (
                            "resolved"
                            if not diagnosis_still_unresolved
                            else "budget_exhausted"
                            if closure_status == "budget_exhausted"
                            else "still_insufficient"
                        ),
                        "reason": (
                            "controller_completed_relevant_artifact_sources_before_diagnosis"
                            if not diagnosis_still_unresolved
                            else "completed_artifact_coverage_did_not_resolve_every_material_hypothesis"
                            if closure_status == "completed"
                            else "relevant_artifact_coverage_remains_incomplete"
                        ),
                        "artifact_evidence_closure": dict(closure),
                    }
                elif investigation is not None and diagnosis_still_unresolved:
                    evidence_supplement = {
                        "attempted": False,
                        "status": "not_available",
                        "reason": "no_controller_accessible_incomplete_artifact_source_was_exposed",
                    }
                else:
                    evidence_supplement = {
                        "attempted": False,
                        "status": "not_needed",
                        "reason": "initial_diagnosis_had_sufficient_evidence",
                    }
                if investigation is None and _diagnoses_need_evidence_supplement(diagnoses):
                    supplemental_evidence = _build_targeted_evidence_supplement(case, diagnoses)
                    evidence_supplement = {
                        "attempted": False,
                        "status": "not_available",
                        "reason": str(supplemental_evidence.get("reason", "") or "no_additional_trace_evidence"),
                        "selected_event_count": int(supplemental_evidence.get("selected_event_count", 0) or 0),
                    }
                    if supplemental_evidence.get("availability") == "available":
                        evidence_supplement.update(
                            {
                                "attempted": True,
                                "status": "running",
                                "reason": "initial_diagnosis_needed_raw_discriminating_evidence",
                            }
                        )
                        try:
                            supplement_raw = await _run_agent(
                                agent,
                                _build_evidence_supplement_prompt(
                                    case=case,
                                    previous_output=raw,
                                    supplemental_evidence=supplemental_evidence,
                                    validation_inventory=validation_inventory,
                                    verifier_inventory=verifier_inventory,
                                    failed_requirement_inventory=failed_requirement_inventory,
                                ),
                                max_retries=0,
                            )
                            supplement_parsed = _extract_json_object(supplement_raw)
                            supplement_diagnoses = (
                                _normalize_case_diagnoses(
                                    supplement_parsed,
                                    prior_candidate_feedback=case_feedback,
                                )
                                if supplement_parsed is not None
                                else []
                            )
                            supplement_conflicts = (
                                _case_diagnoses_validation_conflicts(
                                    supplement_diagnoses,
                                    validation_inventory,
                                    verifier_inventory,
                                    failed_requirement_inventory,
                                )
                                if supplement_diagnoses
                                else ["evidence supplement output contained no diagnoses"]
                            )
                            if supplement_diagnoses and investigation is not None:
                                supplement_conflicts.extend(
                                    _causal_investigation_conflicts(
                                        supplement_diagnoses,
                                        investigation,
                                        evidence_results=evidence_results,
                                        prior_candidate_feedback=case_feedback,
                                    )
                                )
                            if supplement_conflicts:
                                evidence_supplement.update(
                                    {
                                        "status": "rejected",
                                        "reason": "; ".join(supplement_conflicts),
                                    }
                                )
                            else:
                                raw = supplement_raw
                                parsed = supplement_parsed
                                diagnoses = supplement_diagnoses
                                evidence_supplement.update(
                                    {
                                        "status": (
                                            "still_insufficient"
                                            if _diagnoses_need_evidence_supplement(diagnoses)
                                            else "resolved"
                                        ),
                                        "reason": "targeted_trace_evidence_was_reanalyzed_before_optimization",
                                    }
                                )
                        except Exception as exc:  # preserve the valid first diagnosis
                            logger.warning(
                                "per-case evidence supplement unavailable for %s: %s",
                                case.case_id,
                                exc,
                            )
                            evidence_supplement.update(
                                {
                                    "status": "failed",
                                    "reason": _truncate_text(str(exc), 500),
                                }
                            )
                causal_handoff_audit: dict[str, Any] = {
                    "attempted": False,
                    "status": "not_applicable",
                    "attempts": [],
                }
                if investigation is not None and _assigned_diagnosis_indices(diagnoses):
                    causal_handoff_audit["attempted"] = True
                    try:
                        audit_raw = await _run_agent(
                            agent,
                            _build_causal_handoff_audit_prompt(
                                public_task_contract=case.input,
                                diagnoses=diagnoses,
                                investigation=investigation,
                                evidence_results=evidence_results,
                            ),
                            max_retries=0,
                            json_repair_prompt_builder=_build_causal_handoff_audit_json_repair_prompt,
                        )
                        audit = _normalize_causal_handoff_audit(
                            _extract_json_object(audit_raw),
                            diagnoses=diagnoses,
                        )
                        causal_handoff_audit["attempts"].append(audit)
                        handoff_closed_unassigned = False
                        handoff_evidence_rounds: list[dict[str, Any]] = []
                        while not _causal_handoff_audit_approved(audit) and _causal_handoff_audit_needs_evidence(audit):
                            if len(investigation.get("evidence_requests", [])) >= _CAUSAL_TOTAL_REQUEST_LIMIT:
                                handoff_closed_unassigned = not bool(_assigned_diagnosis_indices(diagnoses))
                                causal_handoff_audit["evidence_closure_termination_reason"] = "request_budget_exhausted"
                                break
                            if len(handoff_evidence_rounds) >= _CAUSAL_CLOSURE_MAX_ROUNDS:
                                handoff_closed_unassigned = not bool(_assigned_diagnosis_indices(diagnoses))
                                causal_handoff_audit["evidence_closure_termination_reason"] = "round_budget_exhausted"
                                break
                            closure_round: dict[str, Any] = {
                                "round": len(handoff_evidence_rounds) + 1,
                            }
                            closure_raw = await _run_agent(
                                agent,
                                _build_causal_handoff_evidence_prompt(
                                    public_task_contract=case.input,
                                    investigation=investigation,
                                    evidence_results=evidence_results,
                                    diagnoses=diagnoses,
                                    audit=audit,
                                ),
                                max_retries=self._config.diagnosis_agent_max_retries,
                            )
                            closure_refinement = _normalize_causal_refinement(
                                _extract_json_object(closure_raw),
                                base=investigation,
                                failed_requirement_ids=requirement_ids,
                            )
                            if closure_refinement is None:
                                closure_round["status"] = "invalid_plan"
                                handoff_evidence_rounds.append(closure_round)
                                causal_handoff_audit["evidence_closure_termination_reason"] = "invalid_plan"
                                break
                            merged_investigation, added_requests = _merge_causal_investigation(
                                investigation,
                                closure_refinement,
                            )
                            if not added_requests:
                                handoff_closed_unassigned = not bool(_assigned_diagnosis_indices(diagnoses))
                                closure_round["status"] = "no_new_allowed_request"
                                handoff_evidence_rounds.append(closure_round)
                                causal_handoff_audit["evidence_closure_termination_reason"] = (
                                    "no_public_controller_evidence"
                                )
                                break
                            closure_evidence = execute_causal_investigation(
                                case,
                                {
                                    "hypotheses": merged_investigation.get("hypotheses", []),
                                    "evidence_requests": added_requests,
                                },
                                prior_candidate_feedback=case_feedback,
                                evidence_root=runtime_dir,
                            )
                            merged_investigation = _adopt_automatic_evidence_requests(
                                merged_investigation,
                                closure_evidence,
                            )
                            investigation = merged_investigation
                            evidence_results = _merge_causal_evidence_results(
                                evidence_results,
                                closure_evidence,
                            )
                            closure_round.update(
                                {
                                    "status": "evidence_executed",
                                    "request_count": len(added_requests),
                                    "request_ids": [str(item.get("request_id", "") or "") for item in added_requests],
                                }
                            )
                            investigation_record.update(
                                {
                                    "hypotheses": list(investigation.get("hypotheses", [])),
                                    "evidence_request_count": len(investigation.get("evidence_requests", [])),
                                    "completed_evidence_request_count": int(
                                        evidence_results.get("completed_request_count", 0) or 0
                                    ),
                                    "request_results": [
                                        {
                                            "request_id": str(item.get("request_id", "") or ""),
                                            "operation": str(item.get("operation", "") or ""),
                                            "availability": str(item.get("availability", "") or ""),
                                        }
                                        for item in evidence_results.get("results", [])
                                        if isinstance(item, dict)
                                    ],
                                }
                            )
                            closure_diagnosis_prompt = _build_investigation_diagnosis_prompt(
                                original_prompt=prompt,
                                investigation=investigation,
                                evidence_results=evidence_results,
                            )
                            closure_diagnosis_raw = await _run_agent(
                                agent,
                                closure_diagnosis_prompt,
                                max_retries=self._config.diagnosis_agent_max_retries,
                            )
                            closure_parsed = _extract_json_object(closure_diagnosis_raw)
                            closure_diagnoses = (
                                _normalize_case_diagnoses(
                                    closure_parsed,
                                    prior_candidate_feedback=case_feedback,
                                )
                                if closure_parsed is not None
                                else []
                            )
                            if not closure_diagnoses:
                                closure_round["status"] = "diagnosis_invalid"
                                handoff_evidence_rounds.append(closure_round)
                                causal_handoff_audit["evidence_closure_termination_reason"] = "diagnosis_invalid"
                                break
                            closure_diagnoses, closure_warnings = _reconcile_causal_assessments(
                                closure_diagnoses,
                                investigation,
                                evidence_results=evidence_results,
                                failed_requirement_inventory=failed_requirement_inventory,
                                prior_candidate_feedback=case_feedback,
                            )
                            if _hypothesis_assessments_need_independent_audit(closure_diagnoses):
                                closure_entailment_raw = await _run_agent(
                                    agent,
                                    _build_hypothesis_entailment_audit_prompt(
                                        diagnoses=closure_diagnoses,
                                        investigation=investigation,
                                        evidence_results=evidence_results,
                                    ),
                                    max_retries=0,
                                    json_repair_prompt_builder=_build_hypothesis_entailment_audit_json_repair_prompt,
                                )
                                closure_entailment_audit = _normalize_hypothesis_entailment_audit(
                                    _extract_json_object(closure_entailment_raw),
                                    diagnoses=closure_diagnoses,
                                )
                                entailment_audit_attempts.append(closure_entailment_audit)
                                closure_round["hypothesis_entailment_audit"] = closure_entailment_audit
                                closure_diagnoses = _apply_hypothesis_entailment_audit(
                                    closure_diagnoses,
                                    closure_entailment_audit,
                                )
                                closure_diagnoses, audit_warnings = _reconcile_causal_assessments(
                                    closure_diagnoses,
                                    investigation,
                                    evidence_results=evidence_results,
                                    failed_requirement_inventory=failed_requirement_inventory,
                                    prior_candidate_feedback=case_feedback,
                                )
                                closure_warnings.extend(audit_warnings)
                            closure_diagnoses = _preserve_canonical_decision_ground_diagnoses(
                                diagnoses,
                                closure_diagnoses,
                            )
                            closure_conflicts = _case_diagnoses_validation_conflicts(
                                closure_diagnoses,
                                validation_inventory,
                                verifier_inventory,
                                failed_requirement_inventory,
                            )
                            closure_conflicts.extend(
                                _causal_investigation_conflicts(
                                    closure_diagnoses,
                                    investigation,
                                    evidence_results=evidence_results,
                                    prior_candidate_feedback=case_feedback,
                                )
                            )
                            closure_round["reconciliation_warnings"] = closure_warnings
                            if closure_conflicts:
                                closure_round["status"] = "diagnosis_rejected"
                                closure_round["conflicts"] = closure_conflicts
                                handoff_evidence_rounds.append(closure_round)
                                causal_handoff_audit["evidence_closure_termination_reason"] = "diagnosis_conflict"
                                break
                            raw = closure_diagnosis_raw
                            parsed = closure_parsed
                            diagnoses = closure_diagnoses
                            diagnosis_prompt_used = closure_diagnosis_prompt
                            closure_round["status"] = "rediagnosed"
                            handoff_evidence_rounds.append(closure_round)
                            if not _assigned_diagnosis_indices(diagnoses):
                                closure_round["status"] = "rediagnosed_unassigned"
                                continue
                            audit_raw = await _run_agent(
                                agent,
                                _build_causal_handoff_audit_prompt(
                                    public_task_contract=case.input,
                                    diagnoses=diagnoses,
                                    investigation=investigation,
                                    evidence_results=evidence_results,
                                ),
                                max_retries=0,
                                json_repair_prompt_builder=_build_causal_handoff_audit_json_repair_prompt,
                            )
                            audit = _normalize_causal_handoff_audit(
                                _extract_json_object(audit_raw),
                                diagnoses=diagnoses,
                            )
                            causal_handoff_audit["attempts"].append(audit)
                        if handoff_evidence_rounds:
                            causal_handoff_audit["evidence_closure_rounds"] = handoff_evidence_rounds
                            evidence_supplement["handoff_evidence_closure"] = {
                                "attempted": True,
                                "round_count": len(handoff_evidence_rounds),
                                "termination_reason": str(
                                    causal_handoff_audit.get("evidence_closure_termination_reason", "") or ""
                                ),
                            }
                        if handoff_closed_unassigned:
                            causal_handoff_audit["status"] = "evidence_closed_to_unassigned"
                        elif _causal_handoff_audit_approved(audit):
                            causal_handoff_audit["status"] = "approved"
                        else:
                            rejected_audit_rows = [
                                dict(item) for item in audit["diagnosis_audits"] if not item["approved"]
                            ]
                            rejected_indices = {int(item["diagnosis_index"]) for item in rejected_audit_rows}
                            violations_by_index = {
                                int(item["diagnosis_index"]): list(item["violations"]) for item in rejected_audit_rows
                            }
                            rejected_diagnoses = [
                                dict(diagnosis)
                                for index, diagnosis in enumerate(diagnoses, start=1)
                                if index in rejected_indices
                            ]
                            repair_raw = await _run_agent(
                                agent,
                                _build_causal_handoff_repair_prompt(
                                    public_task_contract=case.input,
                                    diagnoses=rejected_diagnoses,
                                    investigation=investigation,
                                    evidence_results=evidence_results,
                                    audit={"diagnosis_audits": rejected_audit_rows},
                                ),
                                max_retries=0,
                            )
                            repaired_parsed = _extract_json_object(repair_raw)
                            repaired_diagnoses = (
                                _normalize_case_diagnoses(
                                    repaired_parsed,
                                    prior_candidate_feedback=case_feedback,
                                )
                                if repaired_parsed is not None
                                else []
                            )
                            repair_conflicts = []
                            combined_diagnoses: list[dict[str, Any]] = []
                            if repaired_diagnoses:
                                repaired_diagnoses, repair_warnings = _reconcile_causal_assessments(
                                    repaired_diagnoses,
                                    investigation,
                                    evidence_results=evidence_results,
                                    failed_requirement_inventory=failed_requirement_inventory,
                                    prior_candidate_feedback=case_feedback,
                                )
                                causal_handoff_audit["repair_warnings"] = repair_warnings
                                combined_diagnoses = _replace_rejected_causal_handoffs(
                                    diagnoses,
                                    rejected_indices=rejected_indices,
                                    replacements=repaired_diagnoses,
                                )
                                repair_conflicts = _case_diagnoses_validation_conflicts(
                                    combined_diagnoses,
                                    validation_inventory,
                                    verifier_inventory,
                                    failed_requirement_inventory,
                                )
                                repair_conflicts.extend(
                                    _causal_investigation_conflicts(
                                        combined_diagnoses,
                                        investigation,
                                        evidence_results=evidence_results,
                                        prior_candidate_feedback=case_feedback,
                                    )
                                )
                            else:
                                repair_conflicts = ["causal handoff repair returned no diagnoses"]
                            if repair_conflicts:
                                diagnoses = _downgrade_rejected_causal_handoffs(
                                    diagnoses,
                                    rejected_indices=rejected_indices,
                                    violations_by_index=violations_by_index,
                                )
                                causal_handoff_audit.update(
                                    {
                                        "status": "rejected_fail_closed",
                                        "repair_conflicts": repair_conflicts,
                                    }
                                )
                            elif not _assigned_diagnosis_indices(repaired_diagnoses):
                                diagnoses = combined_diagnoses
                                causal_handoff_audit["status"] = "repaired_to_unassigned"
                            else:
                                second_audit_raw = await _run_agent(
                                    agent,
                                    _build_causal_handoff_audit_prompt(
                                        public_task_contract=case.input,
                                        diagnoses=repaired_diagnoses,
                                        investigation=investigation,
                                        evidence_results=evidence_results,
                                    ),
                                    max_retries=0,
                                    json_repair_prompt_builder=_build_causal_handoff_audit_json_repair_prompt,
                                )
                                second_audit = _normalize_causal_handoff_audit(
                                    _extract_json_object(second_audit_raw),
                                    diagnoses=repaired_diagnoses,
                                )
                                causal_handoff_audit["attempts"].append(second_audit)
                                if _causal_handoff_audit_approved(second_audit):
                                    diagnoses = combined_diagnoses
                                    causal_handoff_audit["status"] = "repaired_and_approved"
                                else:
                                    repaired_rejected_indices = {
                                        int(item["diagnosis_index"])
                                        for item in second_audit["diagnosis_audits"]
                                        if not item["approved"]
                                    }
                                    repaired_violations_by_index = {
                                        int(item["diagnosis_index"]): list(item["violations"])
                                        for item in second_audit["diagnosis_audits"]
                                        if not item["approved"]
                                    }
                                    downgraded_replacements = _downgrade_rejected_causal_handoffs(
                                        repaired_diagnoses,
                                        rejected_indices=repaired_rejected_indices,
                                        violations_by_index=repaired_violations_by_index,
                                    )
                                    diagnoses = _replace_rejected_causal_handoffs(
                                        diagnoses,
                                        rejected_indices=rejected_indices,
                                        replacements=downgraded_replacements,
                                    )
                                    causal_handoff_audit["status"] = "rejected_fail_closed"
                    except Exception as exc:
                        logger.warning(
                            "per-case causal handoff audit unavailable for %s: %s",
                            case.case_id,
                            exc,
                        )
                        assigned_indices = set(_assigned_diagnosis_indices(diagnoses))
                        diagnoses = _downgrade_rejected_causal_handoffs(
                            diagnoses,
                            rejected_indices=assigned_indices,
                            violations_by_index={
                                index: ["causal handoff audit was unavailable"] for index in assigned_indices
                            },
                        )
                        causal_handoff_audit.update(
                            {
                                "status": "audit_unavailable_fail_closed",
                                "error": _truncate_text(str(exc), 500),
                            }
                        )
                investigation_record["causal_handoff_audit"] = causal_handoff_audit
                if entailment_audit_attempts:
                    investigation_record["independent_hypothesis_entailment_audit"] = {
                        "attempted": True,
                        "attempt_count": len(entailment_audit_attempts),
                        "attempts": entailment_audit_attempts,
                        "status": entailment_audit_attempts[-1]["status"],
                    }
                investigation_record["hypothesis_entailment_audit"] = (
                    _hypothesis_assessment_entailment_audit(diagnoses)
                    if investigation is not None
                    else {"attempted": False, "status": "not_applicable", "rows": []}
                )
                diagnoses = _attach_hypothesis_semantics(diagnoses, investigation)
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
                        "causal_investigation": dict(investigation_record),
                        "evidence_supplement": dict(evidence_supplement),
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
                if isinstance(exc, _DiagnosisOutputFormatError):
                    logger.warning(
                        "per-case diagnosis format unavailable for %s: %s",
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
    json_repair_prompt_builder: Callable[[str, str], str] | None = None,
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

    # Service retries and output-format repair are different failure domains.
    # Replaying a verbose non-JSON answer up to 20 times in one session only
    # reinforces the prose response and grows context. Retry transient model
    # failures here, but allow exactly one bounded format-only turn.
    last_raw = await run_model_call_with_retries(
        call_once,
        operation_name="diagnosis agent",
        max_retries=max(0, int(max_retries or 0)),
    )
    if _extract_json_object(last_raw) is not None:
        return last_raw

    repair_builder = json_repair_prompt_builder or _build_json_repair_prompt
    current_prompt = repair_builder(prompt, last_raw)
    return await run_model_call_with_retries(
        call_once,
        operation_name="diagnosis JSON formatter",
        max_retries=max(0, int(max_retries or 0)),
    )


def _build_causal_handoff_audit_json_repair_prompt(
    original_prompt: str,
    previous_output: str,
) -> str:
    del original_prompt
    return f"""Previous causal handoff audit output was not valid JSON.

FORMAT-ONLY TASK. Preserve the prior audit conclusions and convert them into:
{{"diagnosis_audits":[{{"diagnosis_index":1,"selected_hypothesis_id":"h1","hypothesis_binding":true,"runtime_decidable":true,"public_contract_consistent":true,"decision_rule_entailed":true,"decision_rule_source":"public_task_contract","decision_rule_evidence":"exact clause or visible invariant","evaluation_independent":true,"single_intervention":true,"approved":true,"violations":[]}}]}}

Return only JSON. Do not rewrite a diagnosis or propose a Harness change.

PREVIOUS_AUDIT:
{_truncate_text(previous_output, 8_000)}
"""


def _build_json_repair_prompt(original_prompt: str, previous_output: str) -> str:
    """Build a small format-only turn from the completed semantic analysis."""
    del original_prompt
    return f"""Previous diagnosis output was not valid JSON.

FORMAT-ONLY TASK. Do not analyze the evidence again and do not explain your work.
Convert only the conclusions already present in PREVIOUS_ANALYSIS into this shape:
{{"diagnoses":[{{"issue_category":"member_harness|team_skill|unassigned","severity":"high|medium|low","summary":"...","failure_mode":"...","failure_cluster":{{"failed_checks":[],"observable_behavior":"..."}},"evidence_status":"confirmed|supported_hypothesis|insufficient","failed_requirement":"...","competing_hypotheses":[],"discriminating_evidence":"...","selected_hypothesis_id":"h1 or empty when unassigned","root_cause":"...","critical_mistake":"...","general_mechanism":"...","target_ref":"member_harness.<role>.<variable>|team_skill.<role>.<variable>|unassigned","evidence_refs":[],"affected_components":[],"recommendation":"...","decision_ground_audit":[{{"ground_id":"g1","ground_text":"...","materiality":"material|non_material|unknown","used_for_decision":true,"authority_status":"verified|missing|contradicted|unknown","scope_status":"matched|mismatched|unknown","owner_status":"matched|mismatched|unknown","trigger_status":"satisfied|not_satisfied|not_applicable|unknown","entailment_status":"entailed|not_entailed|unknown","controller_request_ids":["q1"]}}],"causal_coverage":{{"explained_requirement_ids":[],"residual_requirement_ids":[],"unexplained_observations":[],"causal_chain":[{{"cause":"...","effect":"...","evidence_status":"observed|supported|unknown","evidence_refs":[]}}],"counterfactual_prediction":"...","sufficiency_status":"task_sufficient|cluster_sufficient|local_contributor|unknown"}},"decision_contract":{{"wrong_decision":"...","causal_distinction":"...","required_action":"...","acceptance_observable":"...","scope_boundary":[],"activation_phase":"task_start|during_investigation|post_diagnosis|pre_submission"}},"hypothesis_assessment":[{{"hypothesis_id":"h1","status":"supported|falsified|unresolved","falsifying_condition_status":"observed|not_observed|unknown","claim_follows_from_evidence":"yes|no|unknown","evidence_relation":"direct_claim|direct_falsifier|correlated_output|self_consistency|unknown","evidence_independence":"independent|direct_observation|same_mechanism|unknown","logic_check":"...","controller_request_ids":["q1"],"reason":"...","evidence_refs":[]}}],"prior_experiment_assessment":{{"availability":"available|not_available","intervention_activated":"yes|no|unknown","predicted_behavior_occurred":"yes|no|unknown","predicted_outcome_occurred":"yes|no|unknown","causal_hypothesis_status":"supported|falsified|not_tested|inconclusive","reason":"..."}},"confidence":"high|medium|low"}}]}}

Rules:
- Return only the single valid JSON object and nothing else.
- Preserve concrete evidence pointers and conclusions; do not invent new facts.
- When the previous analysis does not support an optimizable target, use
  issue_category="unassigned", target_ref="unassigned", and confidence="low".

PREVIOUS_ANALYSIS:
{_truncate_text(previous_output, 8000)}
"""


def _build_evidence_conflict_repair_prompt(
    *,
    original_prompt: str,
    previous_output: str,
    conflicts: list[str],
    validation_inventory: dict[str, Any],
    verifier_inventory: dict[str, Any],
    failed_requirement_inventory: dict[str, Any],
    causal_evidence_results: dict[str, Any] | None = None,
) -> str:
    """Ask the diagnosis agent to reconcile only deterministic contradictions."""
    repair_payload = {
        "deterministic_validation_inventory": validation_inventory,
        "deterministic_verifier_inventory": verifier_inventory,
        "deterministic_failed_requirement_inventory": failed_requirement_inventory,
        "controller_causal_evidence_results": causal_evidence_results or {},
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


def _diagnoses_need_evidence_supplement(diagnoses: list[dict[str, Any]]) -> bool:
    """Return whether the first diagnosis needs raw discriminating evidence."""
    if any(str(item.get("evidence_status", "") or "").strip().casefold() == "insufficient" for item in diagnoses):
        return True
    rendered = json.dumps(diagnoses, ensure_ascii=False).casefold()
    compaction_claims = (
        "analyzer_evidence_compaction",
        "evidence compaction",
        "compacted portion",
        "hidden by compaction",
        "hidden by evidence compression",
    )
    return any(claim in rendered for claim in compaction_claims) or bool(
        re.search(r"(?:\.\.\.)?\[?omitted\s+\d+\s+chars", rendered)
    )


_EVIDENCE_TERM_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{3,}|\d{4}|[\u4e00-\u9fff]{2,8}")
_EVIDENCE_TERM_STOPWORDS = {
    "agent",
    "analysis",
    "available",
    "behavior",
    "cannot",
    "case",
    "criterion",
    "decision",
    "diagnosis",
    "evidence",
    "failed",
    "failure",
    "harness",
    "insufficient",
    "observed",
    "output",
    "requirement",
    "response",
    "specific",
    "task",
    "trace",
    "unknown",
}


def _diagnosis_evidence_terms(diagnoses: list[dict[str, Any]]) -> list[str]:
    """Extract bounded discriminator terms from the model's own uncertainty report."""
    values: list[str] = []
    for diagnosis in diagnoses:
        for key in (
            "failed_requirement",
            "discriminating_evidence",
            "root_cause",
            "critical_mistake",
        ):
            value = diagnosis.get(key)
            if isinstance(value, str):
                values.append(value)
        competing = diagnosis.get("competing_hypotheses")
        if isinstance(competing, list):
            values.extend(str(item) for item in competing if isinstance(item, str))
        coverage = diagnosis.get("causal_coverage")
        if isinstance(coverage, dict):
            unexplained = coverage.get("unexplained_observations")
            if isinstance(unexplained, list):
                values.extend(str(item) for item in unexplained if isinstance(item, str))

    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        for match in _EVIDENCE_TERM_PATTERN.findall(value):
            normalized = match.casefold()
            if normalized in _EVIDENCE_TERM_STOPWORDS or normalized in seen:
                continue
            seen.add(normalized)
            terms.append(match)
            if len(terms) >= 32:
                return terms
    return terms


def _build_targeted_evidence_supplement(
    case: CaseAnalysisInput,
    diagnoses: list[dict[str, Any]],
) -> dict[str, Any]:
    """Recover detailed public trajectory events omitted by first-pass compression."""
    normalized_trace_path = Path(case.result_path).parent / "judge" / "normalized_trace.json"
    trace_data = _read_json_if_exists(normalized_trace_path)
    raw_traces = trace_data.get("traces") if isinstance(trace_data, dict) else None
    if not isinstance(raw_traces, list):
        return {
            "availability": "not_available",
            "reason": "normalized_trace_not_available",
            "selected_event_count": 0,
        }

    terms = _diagnosis_evidence_terms(diagnoses)
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    sequence = 0
    for raw_trace in raw_traces:
        if not isinstance(raw_trace, dict):
            continue
        for message in raw_trace.get("messages", []):
            if not isinstance(message, dict):
                continue
            tool_calls = message.get("tool_calls")
            tool_calls = tool_calls if isinstance(tool_calls, list) else []
            content = str(message.get("content", "") or "")
            if not content and not tool_calls:
                continue
            searchable = json.dumps(message, ensure_ascii=False, separators=(",", ":")).casefold()
            relevance = sum(term.casefold() in searchable for term in terms)
            if terms and not relevance:
                continue
            compact_calls: list[dict[str, Any]] = []
            for call in tool_calls[:6]:
                if not isinstance(call, dict):
                    continue
                compact_calls.append(
                    {
                        "name": str(call.get("name", "") or ""),
                        "input": _truncate_text(call.get("input", ""), 1_500),
                        "output": _truncate_text(call.get("output", ""), _EVIDENCE_SUPPLEMENT_EVENT_CHARS),
                        "output_critical_spans": extract_critical_evidence_spans(
                            call.get("output", ""),
                            terms,
                            max_spans=3,
                            max_total_chars=6_000,
                        ),
                        "error": _truncate_text(call.get("error", ""), 1_000),
                        "step_pointer": str(call.get("step_pointer", "") or ""),
                    }
                )
            event = {
                "trace_id": str(raw_trace.get("trace_id", "") or ""),
                "step_pointer": str(message.get("step_pointer", "") or ""),
                "message_index": message.get("message_index"),
                "role": str(message.get("role", "") or ""),
                "content": _truncate_text(content, _EVIDENCE_SUPPLEMENT_EVENT_CHARS),
                "tool_calls": compact_calls,
            }
            error_bonus = 2 if any(str(call.get("error", "") or "") for call in compact_calls) else 0
            mutation_or_read_bonus = 1 if compact_calls else 0
            candidates.append((relevance + error_bonus + mutation_or_read_bonus, sequence, event))
            sequence += 1

    if not candidates:
        return {
            "availability": "not_available",
            "reason": "no_trace_event_matched_missing_discriminator",
            "selected_event_count": 0,
            "search_terms": terms,
        }

    selected = sorted(candidates, key=lambda item: (-item[0], item[1]))[:_EVIDENCE_SUPPLEMENT_MAX_EVENTS]
    selected_events = [item[2] for item in sorted(selected, key=lambda item: item[1])]
    return {
        "availability": "available",
        "source": "public_normalized_execution_trace",
        "policy": (
            "This supplement contains only task-agent-visible execution evidence. "
            "Scorer definitions, gold answers, and hidden tests are excluded."
        ),
        "search_terms": terms,
        "selected_event_count": len(selected_events),
        "selected_events": selected_events,
        "full_final_response": _truncate_text(case.response, _EVIDENCE_SUPPLEMENT_RESPONSE_CHARS),
    }


def _build_evidence_supplement_prompt(
    *,
    case: CaseAnalysisInput,
    previous_output: str,
    supplemental_evidence: dict[str, Any],
    validation_inventory: dict[str, Any],
    verifier_inventory: dict[str, Any],
    failed_requirement_inventory: dict[str, Any],
) -> str:
    """Request one immediate re-diagnosis before any candidate is generated."""
    payload = {
        "authoritative_task_input": case.input,
        "case_outcome": {
            "status": case.status,
            "score": case.score,
            "evaluation_passed": case.evaluation_passed,
            "evaluation_reason": case.evaluation_reason,
        },
        "deterministic_validation_inventory": validation_inventory,
        "deterministic_verifier_inventory": verifier_inventory,
        "deterministic_failed_requirement_inventory": failed_requirement_inventory,
        "supplemental_public_execution_evidence": supplemental_evidence,
    }
    return f"""The first diagnosis reported insufficient evidence or relied on a compacted display
excerpt as if it were a task-runtime observation. Resolve that uncertainty now,
inside the Analyzer, before any Harness candidate is generated and before analyzing the next case.

Use the additional public execution evidence below to test the competing hypotheses named in
the previous diagnosis. Return a complete replacement {{"diagnoses": [...]}} JSON object using
the same schema as your system instructions. Preserve evidence_status="insufficient" and
target_ref="unassigned" if the added evidence still cannot distinguish a mechanism. Do not
invent scorer definitions, gold answers, hidden tests, or missing facts.
ANALYZER_EVIDENCE_COMPACTION markers are post-execution display artifacts. The
task Agent never observed them. Prefer exact `output_critical_spans` when they
are present. If exact public tool evidence conflicts with the evaluator's
criterion, return target_ref="unassigned" and describe that contradiction.

Supplemental evidence payload:
{_bounded_json(payload, 30_000)}

Previous diagnosis JSON:
{_truncate_text(previous_output, 8_000)}
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
                "evidence_status",
                "failed_requirement",
                "competing_hypotheses",
                "discriminating_evidence",
                "root_cause",
                "critical_mistake",
                "general_mechanism",
                "decision_contract",
                "causal_coverage",
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
