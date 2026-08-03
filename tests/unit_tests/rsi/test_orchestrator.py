# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for ``OptimizationOrchestrator`` data preparation."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from openjiuwen.rsi import OptimizationOrchestrator
from openjiuwen.rsi.config import (
    DatasetGeneratorConfig,
    EvaluatorConfig,
)
from openjiuwen.rsi.evaluator import DEFAULT_TEAM_SPEC_FILENAME
from openjiuwen.rsi.evaluator.team_factory import TeamSkillTeamFactory
from openjiuwen.rsi.orchestrator.context import OrchestratorContextStore
from openjiuwen.rsi.orchestrator.orchestrator import (
    _analysis_has_member_issue,
    _analysis_has_team_skill_issue,
    _build_run_report,
    _collect_optimization_consumption_report,
    _eval_ref_has_inconclusive_cases,
    _eval_target_behavior_delta,
    _preferred_issue_member_candidate,
    _scan_consumption_trace_file,
    _seed_case_from_task,
    _seed_feedback_from_eval,
)
from openjiuwen.rsi.schema import (
    BatchOptimizationResult,
    CurrentArtifactRefs,
    DatasetArtifact,
    EvaluationHistoryItem,
    OrchestratorHistory,
    OrchestratorPhase,
    OrchestratorRunContext,
)


def test_restricted_team_issue_prefers_owner_named_by_original_target_ref() -> None:
    issue = {
        "evidence": [{"affected_component": "js-developer"}],
    }

    selected = _preferred_issue_member_candidate(
        issue,
        ["js-developer", "qa-tester"],
        target_ref="team_skill.qa_tester.constraint_violation",
    )

    assert selected == "qa-tester"


def test_target_delta_compares_quality_gap_burden_for_changed_role(tmp_path: Path) -> None:
    def write_eval(name: str, gaps: list[dict[str, Any]]) -> str:
        eval_dir = tmp_path / name
        eval_dir.mkdir()
        result_path = eval_dir / "result.json"
        result_path.write_text(
            json.dumps(
                {
                    "evaluation": {
                        "metadata": {
                            "parsed": {
                                "quality_gaps": gaps,
                                "behaviors": [{"id": "core", "score": 0.9}],
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        eval_ref_path = eval_dir / "eval_ref.yaml"
        eval_ref_path.write_text(
            yaml.safe_dump({"cases": [{"result_path": str(result_path)}]}),
            encoding="utf-8",
        )
        return str(eval_ref_path)

    source = write_eval(
        "source",
        [
            {
                "id": "missing_binding",
                "severity": "medium",
                "affected_roles": ["frontend-engineer"],
            },
            {
                "id": "unrelated_qa_gap",
                "severity": "high",
                "affected_roles": ["qa-tester"],
            },
        ],
    )
    improved = write_eval(
        "improved",
        [
            {
                "id": "unrelated_qa_gap",
                "severity": "high",
                "affected_roles": ["qa-tester"],
            }
        ],
    )
    regressed = write_eval(
        "regressed",
        [
            {
                "id": "replacement_gap_a",
                "severity": "low",
                "affected_roles": ["frontend-engineer"],
            },
            {
                "id": "replacement_gap_b",
                "severity": "medium",
                "affected_roles": ["frontend-engineer"],
            },
        ],
    )

    assert _eval_target_behavior_delta(source, improved, target_roles={"frontend-engineer"}) == 1.0
    assert _eval_target_behavior_delta(source, regressed, target_roles={"frontend-engineer"}) == -0.5


class _FakeEvaluator:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def evaluate_batch(
        self,
        cases: list[dict[str, Any]],
        team_skill_ref_path: str,
        harness_refs_path: str,
        output_dir: str,
        context_path: str | None = None,
        dataset=None,
    ) -> str:
        eval_dir = Path(output_dir)
        eval_dir.mkdir(parents=True, exist_ok=True)
        eval_ref_path = str((eval_dir / "eval_ref.yaml").resolve())
        (eval_dir / "eval_ref.yaml").write_text(
            yaml.safe_dump({"eval_id": eval_dir.name}, allow_unicode=True),
            encoding="utf-8",
        )
        self.calls.append(
            {
                "cases": cases,
                "team_skill_ref_path": team_skill_ref_path,
                "harness_refs_path": harness_refs_path,
                "output_dir": output_dir,
                "context_path": context_path,
                "dataset": dataset,
            }
        )
        if context_path:
            store = OrchestratorContextStore(context_path)
            context = store.load()
            store.save(
                replace(
                    context,
                    current=replace(context.current, eval_ref_path=eval_ref_path),
                )
            )
        return eval_ref_path


class _ScoredFakeEvaluator(_FakeEvaluator):
    def __init__(self, score: float) -> None:
        super().__init__()
        self.score = score

    async def evaluate_batch(
        self,
        cases: list[dict[str, Any]],
        team_skill_ref_path: str,
        harness_refs_path: str,
        output_dir: str,
        context_path: str | None = None,
        dataset=None,
    ) -> str:
        eval_ref_path = await super().evaluate_batch(
            cases=cases,
            team_skill_ref_path=team_skill_ref_path,
            harness_refs_path=harness_refs_path,
            output_dir=output_dir,
            context_path=context_path,
            dataset=dataset,
        )
        eval_dir = Path(eval_ref_path).parent
        summary_path = eval_dir / "summary.json"
        summary_path.write_text(
            json.dumps({"average_score": self.score}, ensure_ascii=False),
            encoding="utf-8",
        )
        Path(eval_ref_path).write_text(
            yaml.safe_dump(
                {
                    "eval_id": eval_dir.name,
                    "summary_path": str(summary_path.resolve()),
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        return eval_ref_path


class _QueuedScoredFakeEvaluator(_FakeEvaluator):
    def __init__(self, scores: list[float]) -> None:
        super().__init__()
        self.scores = list(scores)

    async def evaluate_batch(
        self,
        cases: list[dict[str, Any]],
        team_skill_ref_path: str,
        harness_refs_path: str,
        output_dir: str,
        context_path: str | None = None,
        dataset=None,
    ) -> str:
        score = self.scores.pop(0) if self.scores else 1.0
        eval_ref_path = await super().evaluate_batch(
            cases=cases,
            team_skill_ref_path=team_skill_ref_path,
            harness_refs_path=harness_refs_path,
            output_dir=output_dir,
            context_path=context_path,
            dataset=dataset,
        )
        eval_dir = Path(eval_ref_path).parent
        case_results_dir = eval_dir / "cases"
        case_results_dir.mkdir(parents=True, exist_ok=True)
        case_refs = []
        for index, case in enumerate(cases, start=1):
            case_dir = case_results_dir / f"c{index:03d}"
            case_dir.mkdir(parents=True, exist_ok=True)
            result_path = case_dir / "result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "case_id": str(case["case_id"]),
                        "status": "passed" if score >= 0.8 else "failed",
                        "score": score,
                        "evaluation": {
                            "method": "llm_as_judge",
                            "passed": score >= 0.8,
                            "reason": "good delivery" if score >= 0.8 else "quality gap",
                            "metadata": {
                                "parsed": {
                                    "quality_gaps": [
                                        {
                                            "id": "end_to_end_quality_gap",
                                            "dimension": "end_to_end_quality",
                                            "score": score,
                                            "severity": "high",
                                            "affected_roles": ["builder"],
                                            "likely_surfaces": ["skill"],
                                            "data_needed_to_fix": "Generate targeted delivery quality cases.",
                                        }
                                    ],
                                    "dataset_budget": {
                                        "total_cases": 3,
                                        "case_groups": [
                                            {
                                                "source_gap": "end_to_end_quality_gap",
                                                "case_count": 3,
                                                "target_roles": ["builder"],
                                                "target_surfaces": ["skill"],
                                            }
                                        ],
                                    },
                                }
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            case_refs.append(
                {
                    "case_id": str(case["case_id"]),
                    "case_index": index,
                    "result_path": str(result_path.resolve()),
                    "status": "passed" if score >= 0.8 else "failed",
                    "score": score,
                }
            )
        summary_path = eval_dir / "summary.json"
        summary_path.write_text(
            json.dumps({"average_score": score}, ensure_ascii=False),
            encoding="utf-8",
        )
        Path(eval_ref_path).write_text(
            yaml.safe_dump(
                {
                    "eval_id": eval_dir.name,
                    "summary_path": str(summary_path.resolve()),
                    "cases": case_refs,
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        return eval_ref_path


class _HarnessScoredFakeEvaluator(_FakeEvaluator):
    def __init__(self, scores_by_harness_ref: dict[str, float]) -> None:
        super().__init__()
        self.scores_by_harness_ref = scores_by_harness_ref

    async def evaluate_batch(
        self,
        cases: list[dict[str, Any]],
        team_skill_ref_path: str,
        harness_refs_path: str,
        output_dir: str,
        context_path: str | None = None,
        dataset=None,
    ) -> str:
        eval_ref_path = await super().evaluate_batch(
            cases=cases,
            team_skill_ref_path=team_skill_ref_path,
            harness_refs_path=harness_refs_path,
            output_dir=output_dir,
            context_path=context_path,
            dataset=dataset,
        )
        eval_dir = Path(eval_ref_path).parent
        score = self.scores_by_harness_ref.get(harness_refs_path, 0.0)
        summary_path = eval_dir / "summary.json"
        summary_path.write_text(
            json.dumps({"average_score": score}, ensure_ascii=False),
            encoding="utf-8",
        )
        Path(eval_ref_path).write_text(
            yaml.safe_dump(
                {
                    "eval_id": eval_dir.name,
                    "summary_path": str(summary_path.resolve()),
                    "cases": [
                        {
                            "case_id": str(case["case_id"]),
                            "case_path": str(case.get("case_path", "")),
                            "case_index": int(case.get("case_index", index)),
                            "status": "passed",
                            "score": score,
                        }
                        for index, case in enumerate(cases, start=1)
                    ],
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return eval_ref_path


class _BehaviorScoredFakeEvaluator(_FakeEvaluator):
    def __init__(
        self,
        *,
        average_scores_by_harness_ref: dict[str, float],
        behavior_scores_by_harness_ref: dict[str, dict[str, float]],
        failed_machine_evidence_by_harness_ref: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.average_scores_by_harness_ref = average_scores_by_harness_ref
        self.behavior_scores_by_harness_ref = behavior_scores_by_harness_ref
        self.failed_machine_evidence_by_harness_ref = failed_machine_evidence_by_harness_ref or {}

    async def evaluate_batch(
        self,
        cases: list[dict[str, Any]],
        team_skill_ref_path: str,
        harness_refs_path: str,
        output_dir: str,
        context_path: str | None = None,
        dataset=None,
    ) -> str:
        eval_dir = Path(output_dir)
        eval_dir.mkdir(parents=True, exist_ok=True)
        cases_dir = eval_dir / "cases"
        cases_dir.mkdir(parents=True, exist_ok=True)
        average_score = self.average_scores_by_harness_ref.get(harness_refs_path, 0.0)
        behavior_scores = self.behavior_scores_by_harness_ref.get(harness_refs_path, {})
        case_refs = []
        for index, case in enumerate(cases, start=1):
            case_id = str(case["case_id"])
            case_dir = cases_dir / f"c{index:03d}"
            case_dir.mkdir(parents=True, exist_ok=True)
            result_path = case_dir / "result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "case_id": case_id,
                        "status": "failed" if average_score < 0.8 else "passed",
                        "score": average_score,
                        "evaluation": {
                            "method": "llm_as_judge",
                            "passed": average_score >= 0.8,
                            "metadata": {
                                "parsed": {
                                    "behaviors": [
                                        {
                                            "id": behavior_id,
                                            "score": behavior_score,
                                            "failure_reason": ("still weak" if behavior_score < 1.0 else ""),
                                        }
                                        for behavior_id, behavior_score in behavior_scores.items()
                                    ]
                                },
                                "artifact_runtime_evidence": {
                                    "observations": [
                                        {
                                            "type": self.failed_machine_evidence_by_harness_ref[harness_refs_path],
                                            "status": "failed",
                                        }
                                    ]
                                }
                                if harness_refs_path in self.failed_machine_evidence_by_harness_ref
                                else {},
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            case_refs.append(
                {
                    "case_id": case_id,
                    "case_path": str(case.get("case_path", "")),
                    "case_index": int(case.get("case_index", index)),
                    "result_path": str(result_path.resolve()),
                    "status": "failed" if average_score < 0.8 else "passed",
                    "score": average_score,
                }
            )
        summary_path = eval_dir / "summary.json"
        summary_path.write_text(
            json.dumps({"average_score": average_score}, ensure_ascii=False),
            encoding="utf-8",
        )
        eval_ref_path = eval_dir / "eval_ref.yaml"
        eval_ref_path.write_text(
            yaml.safe_dump(
                {
                    "eval_id": eval_dir.name,
                    "summary_path": str(summary_path.resolve()),
                    "team_skill_ref_path": team_skill_ref_path,
                    "cases": case_refs,
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        self.calls.append(
            {
                "cases": cases,
                "team_skill_ref_path": team_skill_ref_path,
                "harness_refs_path": harness_refs_path,
                "output_dir": output_dir,
                "context_path": context_path,
                "dataset": dataset,
            }
        )
        return str(eval_ref_path.resolve())


class _ReplayMiningFakeEvaluator(_FakeEvaluator):
    async def evaluate_batch(
        self,
        cases: list[dict[str, Any]],
        team_skill_ref_path: str,
        harness_refs_path: str,
        output_dir: str,
        context_path: str | None = None,
        dataset=None,
    ) -> str:
        eval_dir = Path(output_dir)
        case_results_dir = eval_dir / "case_results"
        case_results_dir.mkdir(parents=True, exist_ok=True)
        case_refs = []
        for case in cases:
            case_id = str(case["case_id"])
            score = 0.0 if case_id == "failed_tb" else 1.0
            case_dir = case_results_dir / case_id
            case_dir.mkdir(parents=True, exist_ok=True)
            result_path = case_dir / "result.json"
            trace_path = case_dir / "trace.json"
            result_path.write_text(
                json.dumps(
                    {
                        "case_id": case_id,
                        "score": score,
                        "evaluation": {
                            "method": "external_verifier",
                            "passed": score >= 1.0,
                            "reason": "failed" if score < 1.0 else "passed",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            trace_path.write_text(json.dumps({"case_id": case_id}), encoding="utf-8")
            case_refs.append(
                {
                    "case_id": case_id,
                    "case_path": case["case_path"],
                    "case_index": case["case_index"],
                    "result_path": str(result_path.resolve()),
                    "trace_path": str(trace_path.resolve()),
                    "status": "passed",
                    "score": score,
                }
            )

        summary_path = eval_dir / "summary.json"
        average_score = sum(float(case["score"]) for case in case_refs) / len(case_refs)
        summary_path.write_text(
            json.dumps({"average_score": average_score}, ensure_ascii=False),
            encoding="utf-8",
        )
        eval_ref_path = eval_dir / "eval_ref.yaml"
        eval_ref_path.write_text(
            yaml.safe_dump(
                {
                    "eval_id": eval_dir.name,
                    "eval_dir": str(eval_dir.resolve()),
                    "summary_path": str(summary_path.resolve()),
                    "case_results_dir": str(case_results_dir.resolve()),
                    "cases": case_refs,
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        self.calls.append(
            {
                "cases": cases,
                "team_skill_ref_path": team_skill_ref_path,
                "harness_refs_path": harness_refs_path,
                "output_dir": output_dir,
                "context_path": context_path,
                "dataset": dataset,
            }
        )
        return str(eval_ref_path.resolve())


class _FakeMemberOptimizer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def optimize(
        self,
        eval_ref_path: str,
        analysis_result_path: str,
        harness_refs_path: str,
        output_dir: str,
    ) -> str:
        index = len(self.calls) + 1
        run_dir = Path(output_dir) / f"member_optimization_{index:03d}"
        optimized_harness = run_dir / "optimized_harness"
        optimized_harness.mkdir(parents=True, exist_ok=True)
        plan_path = run_dir / "plan.yaml"
        execution_result_path = run_dir / "execution_results.json"
        verification_path = run_dir / "verification.json"
        fix_result_path = run_dir / "fix_result.json"
        plan_path.write_text(yaml.safe_dump({"plan_id": "plan"}, allow_unicode=True), encoding="utf-8")
        execution_result_path.write_text(json.dumps({"results": []}), encoding="utf-8")
        verification_path.write_text(json.dumps({"passed": True}), encoding="utf-8")
        fix_result_path.write_text(json.dumps({"status": "not_needed"}), encoding="utf-8")
        ref_path = run_dir / "member_optimization_ref.yaml"
        ref_path.write_text(
            yaml.safe_dump(
                {
                    "optimization_id": run_dir.name,
                    "optimized_harness_refs_path": str(optimized_harness.resolve()),
                    "role": "team_leader",
                    "plan_path": str(plan_path.resolve()),
                    "execution_result_path": str(execution_result_path.resolve()),
                    "verification_path": str(verification_path.resolve()),
                    "fix_result_path": str(fix_result_path.resolve()),
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        self.calls.append(
            {
                "eval_ref_path": eval_ref_path,
                "analysis_result_path": analysis_result_path,
                "harness_refs_path": harness_refs_path,
                "output_dir": output_dir,
            }
        )
        return str(ref_path.resolve())


class _FakeTeamSkillOptimizer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def optimize(
        self,
        eval_ref_path: str,
        analysis_result_path: str,
        team_skill_ref_path: str,
        output_dir: str,
    ) -> str:
        self.calls.append(
            {
                "eval_ref_path": eval_ref_path,
                "analysis_result_path": analysis_result_path,
                "team_skill_ref_path": team_skill_ref_path,
                "output_dir": output_dir,
            }
        )
        optimized_dir = Path(output_dir) / f"team_skill_{len(self.calls):03d}"
        optimized_dir.mkdir(parents=True, exist_ok=True)
        return str(optimized_dir.resolve())


class _NoopTeamSkillOptimizer(_FakeTeamSkillOptimizer):
    async def optimize(
        self,
        eval_ref_path: str,
        analysis_result_path: str,
        team_skill_ref_path: str,
        output_dir: str,
    ) -> str:
        self.calls.append(
            {
                "eval_ref_path": eval_ref_path,
                "analysis_result_path": analysis_result_path,
                "team_skill_ref_path": team_skill_ref_path,
                "output_dir": output_dir,
            }
        )
        return team_skill_ref_path


class _FakeDataLoader:
    def __init__(self, batches: list[list[dict[str, Any]]], batch_plan_path: str) -> None:
        self.batches = batches
        self.batch_plan_path = batch_plan_path
        self.calls: list[str] = []

    def load(self, dataset_dir: str, epoch: int = 1):
        self.calls.append({"dataset_dir": dataset_dir, "epoch": epoch})
        yield from self.batches


class _FakeDatasetGenerator:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.config = DatasetGeneratorConfig()

    async def generate(self, task: str, output_dir: str) -> DatasetArtifact:
        self.calls.append(
            {
                "task": task,
                "output_dir": output_dir,
                "known_failures_ref": str(getattr(getattr(self, "config", None), "known_failures_ref", "")),
                "min_cases": getattr(getattr(self, "config", None), "min_cases", None),
            }
        )
        dataset_dir = Path(output_dir).resolve()
        dataset_dir.mkdir(parents=True, exist_ok=True)
        dataset_file = dataset_dir / "synthetic_cases.json"
        dataset_file.write_text(
            json.dumps(
                {
                    "dataset_id": dataset_dir.name,
                    "source": "llm_synthetic_evaluation_dataset",
                    "task": task,
                    "cases": [
                        {
                            "case_id": "generated_case_001",
                            "input": {"user_message": task},
                            "reference": {
                                "required_behaviors": [
                                    {
                                        "id": "solves_task",
                                        "description": "Solves the task.",
                                        "weight": 1.0,
                                    }
                                ],
                                "judge_rubric": {"pass_threshold": 0.8},
                            },
                            "metadata": {"judgeable": True},
                        },
                        {
                            "case_id": "generated_case_002",
                            "input": {"user_message": task},
                            "reference": {
                                "required_behaviors": [
                                    {
                                        "id": "verifies_task",
                                        "description": "Verifies the result.",
                                        "weight": 1.0,
                                    }
                                ],
                                "judge_rubric": {"pass_threshold": 0.8},
                            },
                            "metadata": {"judgeable": True},
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return DatasetArtifact(
            dataset_id=dataset_dir.name,
            dataset_dir=str(dataset_dir),
            dataset_files=[str(dataset_file)],
        )


class _FakeTeamSkillGenerator:
    def can_generate(self) -> bool:
        return True

    async def generate(self, task: str, output_dir: str | Path) -> str:
        team_skill_dir = Path(output_dir) / "web_creation_team"
        team_skill_dir.mkdir(parents=True, exist_ok=True)
        (team_skill_dir / "SKILL.md").write_text(
            """---
kind: team-skill
name: web_creation_team
roles:
  - id: planner
    kind: ai_agent
    purpose: Plan the page structure and acceptance checks.
    skills: []
    tools: []
  - id: builder
    kind: ai_agent
    purpose: Implement the page files and visual layout.
    skills: []
    tools: []
---

# Web Creation Team
""",
            encoding="utf-8",
        )
        roles_dir = team_skill_dir / "roles"
        roles_dir.mkdir()
        (roles_dir / "planner.md").write_text(
            """# Role: Planner

## Inline Persona for Teammate

```
ROLE: Planner.
You turn the user request into a concrete page plan and acceptance checklist.
```
""",
            encoding="utf-8",
        )
        (roles_dir / "builder.md").write_text(
            """# Role: Builder

## Inline Persona for Teammate

```
ROLE: Builder.
You implement the planned page and keep outputs inspectable.
```
""",
            encoding="utf-8",
        )
        return str(team_skill_dir)


class _MemberOnlyAnalyzer:
    async def analyze(self, invocation) -> str:
        output_dir = Path(invocation.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        analysis_ref_path = output_dir / "analysis_ref.yaml"
        analysis_ref_path.write_text(
            yaml.safe_dump(
                {
                    "analysis_id": output_dir.name,
                    "source_eval_ref_path": invocation.eval_ref_path,
                    "issues": [
                        {
                            "optimization_target": "member_harness",
                            "category": "member_harness",
                            "suspected_team_scope": "member",
                        }
                    ],
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        return str(analysis_ref_path.resolve())


class _TeamOnlyAnalyzer:
    async def analyze(self, invocation) -> str:
        output_dir = Path(invocation.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        analysis_ref_path = output_dir / "analysis_ref.yaml"
        analysis_ref_path.write_text(
            yaml.safe_dump(
                {
                    "analysis_id": output_dir.name,
                    "source_eval_ref_path": invocation.eval_ref_path,
                    "issues": [
                        {
                            "optimization_target": "team_skill",
                            "category": "team_skill",
                            "suspected_team_scope": "team",
                        }
                    ],
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        return str(analysis_ref_path.resolve())


class _AdaptableTeamOnlyAnalyzer:
    async def analyze(self, invocation) -> str:
        output_dir = Path(invocation.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        analysis_ref_path = output_dir / "analysis_ref.yaml"
        analysis_ref_path.write_text(
            yaml.safe_dump(
                {
                    "analysis_id": output_dir.name,
                    "source_eval_ref_path": invocation.eval_ref_path,
                    "issues": [
                        {
                            "optimization_target": "team_skill",
                            "category": "team_coordination",
                            "suspected_team_scope": "team_skill",
                            "recommendation": "Require a cross-file integration check.",
                            "metadata": {
                                "affected_components": [
                                    "team_leader",
                                    "builder",
                                ],
                                "attribution": {
                                    "target_ref": "team_skill.team_leader.constraint_violation",
                                },
                            },
                        }
                    ],
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        return str(analysis_ref_path.resolve())


class _TeamAndMemberAnalyzer:
    async def analyze(self, invocation) -> str:
        output_dir = Path(invocation.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        analysis_ref_path = output_dir / "analysis_ref.yaml"
        analysis_ref_path.write_text(
            yaml.safe_dump(
                {
                    "analysis_id": output_dir.name,
                    "source_eval_ref_path": invocation.eval_ref_path,
                    "issues": [
                        {
                            "optimization_target": "team_skill",
                            "category": "team_coordination",
                            "suspected_team_scope": "team_skill",
                        },
                        {
                            "optimization_target": "member_harness",
                            "category": "member_harness",
                            "suspected_team_scope": "member",
                        },
                    ],
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        return str(analysis_ref_path.resolve())


class _NoIssueAnalyzer:
    async def analyze(self, invocation) -> str:
        output_dir = Path(invocation.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        analysis_ref_path = output_dir / "analysis_ref.yaml"
        analysis_ref_path.write_text(
            yaml.safe_dump(
                {
                    "analysis_id": output_dir.name,
                    "source_eval_ref_path": invocation.eval_ref_path,
                    "issues": [],
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        return str(analysis_ref_path.resolve())


class _SequencedAnalyzer:
    def __init__(self, targets: list[str]) -> None:
        self.targets = targets
        self.calls = 0

    async def analyze(self, invocation) -> str:
        output_dir = Path(invocation.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        target = self.targets[self.calls] if self.calls < len(self.targets) else ""
        self.calls += 1
        issues = []
        if target:
            issues.append(
                {
                    "optimization_target": target,
                    "category": target,
                }
            )
        analysis_ref_path = output_dir / "analysis_ref.yaml"
        analysis_ref_path.write_text(
            yaml.safe_dump(
                {
                    "analysis_id": output_dir.name,
                    "source_eval_ref_path": invocation.eval_ref_path,
                    "issues": issues,
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        return str(analysis_ref_path.resolve())


def test_run_report_collects_skill_and_tool_consumption_evidence(tmp_path: Path) -> None:
    """Consumption report should reflect active resources and actual trace calls."""
    harness_dir = tmp_path / "harnesses" / "builder"
    skill_dir = harness_dir / "skills" / "turn_state_check"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: turn-state-check",
                "description: Use when validating turn state transitions before final delivery.",
                "---",
                "# Turn State Check",
            ]
        ),
        encoding="utf-8",
    )
    tools_dir = harness_dir / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "tools.yaml").write_text(
        yaml.safe_dump(
            {
                "tools": [
                    {
                        "file": "tools/state_checker.py",
                        "class_name": "StateChecker",
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (tools_dir / "state_checker.py").write_text("# test tool\n", encoding="utf-8")

    eval_dir = tmp_path / "eval"
    case_dir = eval_dir / "cases" / "c001"
    trace_dir = case_dir / "tr"
    trace_dir.mkdir(parents=True)
    trace = {
        "messages": [
            {
                "tool_calls": [
                    {
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps(
                                {"path": str(skill_dir / "SKILL.md")},
                                ensure_ascii=False,
                            ),
                        }
                    },
                    {
                        "function": {
                            "name": "state_checker",
                            "arguments": "{}",
                        }
                    },
                ]
            }
        ]
    }
    (trace_dir / "builder.jsonl").write_text(
        json.dumps(trace, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    result_path = case_dir / "result.json"
    result_path.write_text("{}", encoding="utf-8")
    eval_ref = eval_dir / "eval_ref.yaml"
    eval_ref.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "case_id": "case_001",
                        "result_path": str(result_path.resolve()),
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    context = OrchestratorRunContext(
        task_id="task",
        task="task",
        context_path=str(tmp_path / "context.yaml"),
        checkpoint_dir=str(tmp_path / "checkpoints"),
        current=CurrentArtifactRefs(
            harness_refs={"builder": str(harness_dir.resolve())},
            eval_ref_path=str(eval_ref.resolve()),
        ),
        history=OrchestratorHistory(
            evaluations=[
                EvaluationHistoryItem(
                    eval_ref_path=str(eval_ref.resolve()),
                    phase="member_optimization",
                    score=0.5,
                )
            ]
        ),
    )

    report = _collect_optimization_consumption_report(context)

    role = report["roles"]["builder"]
    assert role["harness_exists"] is True
    assert role["skills"][0]["name"] == "turn_state_check"
    assert role["skills"][0]["observed"]["skill_file_read"] is True
    assert role["tools"][0]["name"] == "state_checker"
    assert role["tools"][0]["observed"]["tool_called"] is True


def test_run_report_ignores_stale_team_skill_attempts_when_seed_stops(
    tmp_path: Path,
) -> None:
    """A seed-only run report must not surface optimization refs from older runs."""
    team_skill_dir = tmp_path / "team_skills"
    stale_attempt_dir = team_skill_dir / "tso_001"
    stale_attempt_dir.mkdir(parents=True)
    (stale_attempt_dir / "team_skill_optimization_ref.yaml").write_text(
        yaml.safe_dump(
            {
                "optimization_id": "tso_001",
                "status": "success",
                "source_eval_ref_path": str((tmp_path / "old_eval_ref.yaml").resolve()),
                "optimized_team_skill_ref_path": str((tmp_path / "old_skill").resolve()),
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    current_eval_ref = str((tmp_path / "seed" / "eval_ref.yaml").resolve())
    context = OrchestratorRunContext(
        task_id="task_seed_only",
        task="seed only",
        context_path=str(tmp_path / "orchestrator_context.yaml"),
        checkpoint_dir=str(tmp_path / "checkpoints"),
        phase=OrchestratorPhase.COMPLETED,
        epoch=0,
        current=CurrentArtifactRefs(eval_ref_path=current_eval_ref),
        history=OrchestratorHistory(
            evaluations=[
                EvaluationHistoryItem(
                    eval_ref_path=current_eval_ref,
                    phase="initializing",
                    score=0.1875,
                )
            ],
        ),
        metadata={
            "seed_optimization_confirmation": {
                "continue": False,
                "reason": "user_declined",
            }
        },
    )

    report = _build_run_report(
        context=context,
        team_skill_optimization_dir=team_skill_dir,
        member_optimization_dir=tmp_path / "member_optimizations",
    )

    assert report["team_skill_optimization_attempts"] == []


def test_consumption_trace_scan_streams_jsonl_without_read_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Large trace scans should stream lines instead of loading the full file."""
    skill_path = tmp_path / "skills" / "turn_state_check" / "SKILL.md"
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        json.dumps(
            {
                "tool_calls": [
                    {
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": str(skill_path)}),
                        }
                    },
                    {"function": {"name": "state_checker", "arguments": "{}"}},
                ]
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    def fail_read_text(self, *args, **kwargs):
        raise AssertionError("trace scanner must not load full files with read_text")

    monkeypatch.setattr(Path, "read_text", fail_read_text)
    observations = {"skill_reads": set(), "tool_calls": {}}

    _scan_consumption_trace_file(trace_path, observations)

    assert "turn_state_check" in observations["skill_reads"]
    assert observations["tool_calls"]["read_file"] == 1
    assert observations["tool_calls"]["state_checker"] == 1


def test_consumption_trace_scan_counts_skill_tool_by_skill_name(tmp_path: Path) -> None:
    """skill_tool loads a skill by name and does not include a SKILL.md path."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        json.dumps(
            {
                "tool_calls": [
                    {
                        "function": {
                            "name": "skill_tool",
                            "arguments": json.dumps({"skill_name": "post_mutation_termination_check"}),
                        }
                    }
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    observations = {"skill_reads": set(), "tool_calls": {}}

    _scan_consumption_trace_file(trace_path, observations)

    assert "post_mutation_termination_check" in observations["skill_reads"]
    assert observations["tool_calls"]["skill_tool"] == 1


def test_analysis_gate_prefers_optimization_target_over_category(tmp_path: Path) -> None:
    """optimization_target is the authoritative orchestrator gate field."""
    analysis_ref_path = tmp_path / "analysis_ref.yaml"
    analysis_ref_path.write_text(
        yaml.safe_dump(
            {
                "issues": [
                    {
                        "optimization_target": "member_harness",
                        "category": "team_skill",
                        "suspected_team_scope": "team",
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    assert not _analysis_has_team_skill_issue(str(analysis_ref_path))
    assert _analysis_has_member_issue(str(analysis_ref_path))


def test_team_stage_role_contract_issue_routes_to_team_skill(tmp_path: Path) -> None:
    """A generated-role contract defect belongs to Team Skill, not only the member."""
    analysis_ref_path = tmp_path / "analysis_ref.yaml"
    analysis_ref_path.write_text(
        yaml.safe_dump(
            {
                "issues": [
                    {
                        "optimization_target": "member_harness",
                        "target_ref": "member_harness.content-curator.skill",
                        "summary": "The role output schema merged two required sections.",
                        "recommendation": "Update the Team Skill role schema.",
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    assert _analysis_has_team_skill_issue(
        str(analysis_ref_path),
        source_stage="team_skill_stage",
    )


def test_analysis_gate_requires_optimization_target(tmp_path: Path) -> None:
    """category and scope do not trigger optimizer gates without optimization_target."""
    analysis_ref_path = tmp_path / "analysis_ref.yaml"
    analysis_ref_path.write_text(
        yaml.safe_dump(
            {
                "issues": [
                    {
                        "category": "team_skill",
                        "suspected_team_scope": "team",
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    assert not _analysis_has_team_skill_issue(str(analysis_ref_path))
    assert not _analysis_has_member_issue(str(analysis_ref_path))


def _write_config(
    path: Path,
    workspace_dir: Path,
    *,
    batch_size: int = 2,
    max_epochs: int = 1,
    success_score: float = 1.0,
    team_spec_config_ref: str = "",
    dataset_model_config_ref: str = "",
    full_evaluation_enabled: bool = True,
    seed_evaluation_enabled: bool = False,
) -> None:
    evaluator_config = {
        "evaluation_method": "exact_match",
        "success_score": success_score,
    }
    if team_spec_config_ref:
        evaluator_config["team_spec_config_ref"] = team_spec_config_ref
    path.write_text(
        yaml.safe_dump(
            {
                "workspace_dir": str(workspace_dir),
                "max_epochs": max_epochs,
                "data_loader": {
                    "file_pattern": "*.json",
                    "batch_size": batch_size,
                },
                "dataset_generator": {
                    "min_cases": 1,
                    "coverage_dimensions": ["reasoning"],
                    "model_config_ref": dataset_model_config_ref,
                },
                "evaluator": evaluator_config,
                "scheduling": {
                    "evaluation_strategy": "hybrid",
                    "coordination_strategy": "team_first_single_pass",
                    "promotion_policy": "epoch_full_evaluation",
                    "full_evaluation_enabled": full_evaluation_enabled,
                },
                "seed_evaluation": {
                    "enabled": seed_evaluation_enabled,
                    "pass_threshold": 0.8,
                    "max_cases": 20,
                },
            },
            allow_unicode=True,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _write_dataset(path: Path, case_ids: list[str]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "cases.json").write_text(
        json.dumps(
            [{"case_id": case_id} for case_id in case_ids],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_judgeable_dataset(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "cases.json").write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "failed_tb",
                        "input": "fix repo",
                        "dimension": "git",
                        "difficulty": "medium",
                        "source": "external_adapter",
                        "task_type": "coding",
                        "verification_contract": {
                            "must_pass": ["repository_check"],
                        },
                        "reference": {
                            "success_criteria": ["resolves the failing repository task"],
                        },
                        "training_signal": {
                            "target_capabilities": ["repo_failure_diagnosis"],
                            "capability_combination": "diagnose_patch_verify",
                            "target_surfaces": ["skill", "tool"],
                            "expected_failure_modes": ["stops after shallow log reading"],
                            "capability_gap": "agent needs targeted repo debugging data",
                        },
                    },
                    {
                        "case_id": "passed_tb",
                        "input": "already ok",
                        "dimension": "git",
                        "difficulty": "easy",
                        "source": "external_adapter",
                        "task_type": "coding",
                        "verification_contract": {
                            "must_pass": ["repository_check"],
                        },
                    },
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_team_skill_spec(
    team_skill_dir: Path,
    *,
    team_name: str = "default_team",
    roles: list[dict[str, Any]] | None = None,
) -> None:
    team_skill_dir.mkdir(parents=True, exist_ok=True)
    frontmatter: dict[str, Any] = {"name": team_name}
    if roles is not None:
        frontmatter["kind"] = "team-skill"
        frontmatter["roles"] = roles
    (team_skill_dir / "SKILL.md").write_text(
        "---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False) + "---\n",
        encoding="utf-8",
    )
    (team_skill_dir / DEFAULT_TEAM_SPEC_FILENAME).write_text(
        yaml.safe_dump(
            {
                "agents": {"leader": {}},
                "team_name": team_name,
                "spawn_mode": "inprocess",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_member_analysis_fallback_uses_stage_analysis_dir(tmp_path: Path) -> None:
    """Fallback member analysis handoff stays beside the source evaluation stage."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace("")
    eval_dir = (
        workspace_dir
        / "default_team"
        / "evaluations"
        / "epoch_001"
        / "batch_001"
        / "member_optimization"
        / "evaluation"
    )
    eval_dir.mkdir(parents=True)
    eval_ref_path = eval_dir / "eval_ref.yaml"
    eval_ref_path.write_text(yaml.safe_dump({"eval_id": "eval"}), encoding="utf-8")

    analysis_ref_path = Path(orchestrator._write_member_analysis_input(str(eval_ref_path)))

    assert analysis_ref_path.parent == eval_dir.parent / "a"
    assert not (workspace_dir / "default_team" / "analysis").exists()


def test_team_factory_uses_team_skill_roles_as_predefined_members(tmp_path: Path) -> None:
    """Team Skill roles become predefined members in the local team spec."""
    team_skill_dir = tmp_path / "team_skill"
    _write_team_skill_spec(
        team_skill_dir,
        team_name="web_creation_team",
        roles=[
            {
                "id": "planner",
                "kind": "ai_agent",
                "purpose": "Plan the page structure.",
            },
            {
                "id": "builder",
                "kind": "ai_agent",
                "purpose": "Implement the page.",
            },
        ],
    )
    roles_dir = team_skill_dir / "roles"
    roles_dir.mkdir()
    (roles_dir / "planner.md").write_text(
        """# Role: Planner

## Inline Persona for Teammate

```
ROLE: Planner.
Make a concise page plan.
```
""",
        encoding="utf-8",
    )

    spec = TeamSkillTeamFactory(config=EvaluatorConfig()).create_team_spec(
        team_skill_ref_path=str(team_skill_dir),
        output_dir=tmp_path / "case",
    )

    assert spec.team_name == "web_creation_team"
    assert set(spec.agents) >= {"leader", "teammate"}
    assert [member.member_name for member in spec.predefined_members] == [
        "planner",
        "builder",
    ]
    assert spec.predefined_members[0].prompt
    assert "ROLE: Planner." in spec.predefined_members[0].prompt


@pytest.mark.asyncio
async def test_team_skill_optimization_advances_current_ref(tmp_path: Path) -> None:
    """Team Skill optimizer output becomes the current Team Skill ref."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace("")
    orchestrator.context_store.save(orchestrator.context_store.create("test task"))
    fake_optimizer = _FakeTeamSkillOptimizer()
    orchestrator.team_skill_optimizer = fake_optimizer

    eval_ref_path = tmp_path / "eval_ref.yaml"
    eval_ref_path.write_text(yaml.safe_dump({"eval_id": "eval"}), encoding="utf-8")
    analysis_ref_path = tmp_path / "analysis_ref.yaml"
    analysis_ref_path.write_text(
        yaml.safe_dump({"issues": [{"optimization_target": "team_skill"}]}, allow_unicode=True),
        encoding="utf-8",
    )
    orchestrator.analysis_ref_by_eval_ref_path[str(eval_ref_path)] = str(analysis_ref_path)

    current_ref = await orchestrator._maybe_optimize_team_skill(
        baseline_eval_ref_path=str(eval_ref_path),
        team_skill_ref_path="team_skills/current",
    )

    assert Path(current_ref) == Path(fake_optimizer.calls[0]["output_dir"]) / "team_skill_001"
    assert orchestrator.optimized_team_skill_ref_path == current_ref


@pytest.mark.asyncio
async def test_team_skill_optimization_emits_progress_phase(tmp_path: Path) -> None:
    """Observers must see Team Skill optimization instead of staying on evaluation."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace("")
    orchestrator.context_store.save(orchestrator.context_store.create("test task"))
    orchestrator.team_skill_optimizer = _FakeTeamSkillOptimizer()
    progress_events: list[Any] = []
    orchestrator._progress_callback = progress_events.append

    eval_ref_path = tmp_path / "eval_ref.yaml"
    eval_ref_path.write_text(yaml.safe_dump({"eval_id": "eval"}), encoding="utf-8")
    analysis_ref_path = tmp_path / "analysis_ref.yaml"
    analysis_ref_path.write_text(
        yaml.safe_dump({"issues": [{"optimization_target": "team_skill"}]}, allow_unicode=True),
        encoding="utf-8",
    )
    orchestrator.analysis_ref_by_eval_ref_path[str(eval_ref_path)] = str(analysis_ref_path)

    await orchestrator._maybe_optimize_team_skill(
        baseline_eval_ref_path=str(eval_ref_path),
        team_skill_ref_path="team_skills/current",
    )

    assert OrchestratorPhase.OPTIMIZING_TEAM_SKILL.value in {event.phase for event in progress_events}


@pytest.mark.asyncio
async def test_member_optimization_advances_current_harness_refs(tmp_path: Path) -> None:
    """Member optimizer output becomes the current harness refs path."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace("")
    orchestrator.context_store.save(orchestrator.context_store.create("test task"))
    fake_optimizer = _FakeMemberOptimizer()
    orchestrator.member_optimizer = fake_optimizer

    eval_ref_path = tmp_path / "eval_ref.yaml"
    eval_ref_path.write_text(yaml.safe_dump({"eval_id": "eval"}), encoding="utf-8")
    analysis_ref_path = tmp_path / "analysis_ref.yaml"
    analysis_ref_path.write_text(
        yaml.safe_dump({"issues": [{"optimization_target": "member_harness"}]}, allow_unicode=True),
        encoding="utf-8",
    )
    orchestrator.analysis_ref_by_eval_ref_path[str(eval_ref_path)] = str(analysis_ref_path)

    current_refs = await orchestrator._maybe_optimize_member_harness(
        eval_ref_paths=[str(eval_ref_path)],
        harness_refs_path="expert_harnesses/harness_refs.yaml",
    )

    member_info = yaml.safe_load(Path(orchestrator.member_optimization_ref_path).read_text(encoding="utf-8"))
    assert current_refs == member_info["optimized_harness_refs_path"]
    assert orchestrator.optimized_harness_refs_path == current_refs


@pytest.mark.asyncio
async def test_member_optimization_emits_progress_phase(tmp_path: Path) -> None:
    """Observers must see member harness optimization instead of staying on evaluation."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace("")
    orchestrator.context_store.save(orchestrator.context_store.create("test task"))
    orchestrator.member_optimizer = _FakeMemberOptimizer()
    progress_events: list[Any] = []
    orchestrator._progress_callback = progress_events.append

    eval_ref_path = tmp_path / "eval_ref.yaml"
    eval_ref_path.write_text(yaml.safe_dump({"eval_id": "eval"}), encoding="utf-8")
    analysis_ref_path = tmp_path / "analysis_ref.yaml"
    analysis_ref_path.write_text(
        yaml.safe_dump({"issues": [{"optimization_target": "member_harness"}]}, allow_unicode=True),
        encoding="utf-8",
    )
    orchestrator.analysis_ref_by_eval_ref_path[str(eval_ref_path)] = str(analysis_ref_path)

    await orchestrator._maybe_optimize_member_harness(
        eval_ref_paths=[str(eval_ref_path)],
        harness_refs_path="expert_harnesses/harness_refs.yaml",
    )

    assert OrchestratorPhase.OPTIMIZING_MEMBER.value in {event.phase for event in progress_events}


def test_epoch_checkpoint_emits_progress_phase(tmp_path: Path) -> None:
    """Observers must see checkpoint saving instead of staying on optimization."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace("")
    context = orchestrator.context_store.create("test task")
    eval_ref_path = tmp_path / "eval_ref.yaml"
    eval_ref_path.write_text(yaml.safe_dump({"eval_id": "eval"}), encoding="utf-8")
    orchestrator.context_store.save(
        replace(
            context,
            current=replace(context.current, eval_ref_path=str(eval_ref_path)),
        )
    )
    progress_events: list[Any] = []
    orchestrator._progress_callback = progress_events.append

    orchestrator._save_epoch_checkpoint(
        epoch=1,
        eval_ref_path=str(eval_ref_path),
        score=0.75,
    )

    assert OrchestratorPhase.SAVING_CHECKPOINT.value in {event.phase for event in progress_events}


@pytest.mark.asyncio
async def test_member_optimization_candidate_gate_rejects_non_improving_candidate(tmp_path: Path) -> None:
    """Member harness candidate must improve the source batch before becoming current."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace("")
    orchestrator.context_store.save(orchestrator.context_store.create("test task"))
    fake_optimizer = _FakeMemberOptimizer()
    orchestrator.member_optimizer = fake_optimizer

    eval_dir = tmp_path / "source_eval"
    eval_dir.mkdir()
    summary_path = eval_dir / "summary.json"
    summary_path.write_text(json.dumps({"average_score": 0.5}), encoding="utf-8")
    eval_ref_path = eval_dir / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "eval_id": "eval",
                "summary_path": str(summary_path.resolve()),
                "cases": [
                    {
                        "case_id": "case_001",
                        "case_path": str(tmp_path / "cases.json"),
                        "case_index": 1,
                    }
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    analysis_ref_path = tmp_path / "analysis_ref.yaml"
    analysis_ref_path.write_text(
        yaml.safe_dump({"issues": [{"optimization_target": "member_harness"}]}, allow_unicode=True),
        encoding="utf-8",
    )
    orchestrator.analysis_ref_by_eval_ref_path[str(eval_ref_path)] = str(analysis_ref_path)

    initial_refs = "expert_harnesses/harness_refs.yaml"
    candidate_refs = str(
        workspace_dir / "default_team" / "member_optimizations" / "member_optimization_001" / "optimized_harness"
    )
    orchestrator.evaluator = _HarnessScoredFakeEvaluator(
        {
            initial_refs: 0.5,
            candidate_refs: 0.5,
        }
    )

    current_refs = await orchestrator._maybe_optimize_member_harness(
        eval_ref_paths=[str(eval_ref_path)],
        harness_refs_path=initial_refs,
    )

    assert current_refs == initial_refs
    assert orchestrator.optimized_harness_refs_path == initial_refs
    context = orchestrator.context_store.load()
    gate = context.metadata["member_candidate_gates"][0]
    assert gate["status"] == "rejected"
    assert gate["source_score"] == 0.5
    assert gate["candidate_score"] == 0.5
    assert context.history.member_optimizations == []


@pytest.mark.asyncio
async def test_member_optimization_candidate_gate_accepts_improving_candidate(tmp_path: Path) -> None:
    """Member harness candidate is promoted when it improves the source batch."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace("")
    orchestrator.context_store.save(orchestrator.context_store.create("test task"))
    fake_optimizer = _FakeMemberOptimizer()
    orchestrator.member_optimizer = fake_optimizer

    eval_dir = tmp_path / "source_eval"
    eval_dir.mkdir()
    summary_path = eval_dir / "summary.json"
    summary_path.write_text(json.dumps({"average_score": 0.0}), encoding="utf-8")
    eval_ref_path = eval_dir / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "eval_id": "eval",
                "summary_path": str(summary_path.resolve()),
                "cases": [
                    {
                        "case_id": "case_001",
                        "case_path": str(tmp_path / "cases.json"),
                        "case_index": 1,
                    }
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    analysis_ref_path = tmp_path / "analysis_ref.yaml"
    analysis_ref_path.write_text(
        yaml.safe_dump({"issues": [{"optimization_target": "member_harness"}]}, allow_unicode=True),
        encoding="utf-8",
    )
    orchestrator.analysis_ref_by_eval_ref_path[str(eval_ref_path)] = str(analysis_ref_path)

    initial_refs = "expert_harnesses/harness_refs.yaml"
    candidate_refs = str(
        workspace_dir / "default_team" / "member_optimizations" / "member_optimization_001" / "optimized_harness"
    )
    orchestrator.evaluator = _HarnessScoredFakeEvaluator(
        {
            initial_refs: 0.0,
            candidate_refs: 1.0,
        }
    )

    current_refs = await orchestrator._maybe_optimize_member_harness(
        eval_ref_paths=[str(eval_ref_path)],
        harness_refs_path=initial_refs,
    )

    assert current_refs == candidate_refs
    assert orchestrator.optimized_harness_refs_path == candidate_refs
    context = orchestrator.context_store.load()
    gate = context.metadata["member_candidate_gates"][0]
    assert gate["status"] == "accepted"
    assert gate["source_score"] == 0.0
    assert gate["candidate_score"] == 1.0
    assert len(context.history.member_optimizations) == 1


@pytest.mark.asyncio
async def test_member_optimization_context_uses_published_role_refs(tmp_path: Path) -> None:
    """Accepted member optimization stores role refs, not the refs artifact path per role."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace("")
    orchestrator.context_store.save(orchestrator.context_store.create("test task"))

    before_solver = tmp_path / "before" / "solver"
    before_reviewer = tmp_path / "before" / "reviewer"
    before_solver.mkdir(parents=True)
    before_reviewer.mkdir(parents=True)
    initial_refs_path = tmp_path / "initial_harness_refs.yaml"
    initial_refs_path.write_text(
        yaml.safe_dump(
            {
                "harness_refs": {
                    "solver": str(before_solver.resolve()),
                    "reviewer": str(before_reviewer.resolve()),
                }
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    eval_dir = tmp_path / "source_eval"
    eval_dir.mkdir()
    summary_path = eval_dir / "summary.json"
    summary_path.write_text(json.dumps({"average_score": 0.0}), encoding="utf-8")
    eval_ref_path = eval_dir / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "eval_id": "eval",
                "summary_path": str(summary_path.resolve()),
                "cases": [
                    {
                        "case_id": "case_001",
                        "case_path": str(tmp_path / "cases.json"),
                        "case_index": 1,
                    }
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    analysis_ref_path = tmp_path / "analysis_ref.yaml"
    analysis_ref_path.write_text(
        yaml.safe_dump({"issues": [{"optimization_target": "member_harness"}]}, allow_unicode=True),
        encoding="utf-8",
    )
    orchestrator.analysis_ref_by_eval_ref_path[str(eval_ref_path)] = str(analysis_ref_path)

    after_solver = tmp_path / "after" / "solver"
    after_reviewer = tmp_path / "after" / "reviewer"
    after_solver.mkdir(parents=True)
    after_reviewer.mkdir(parents=True)
    candidate_refs_path = tmp_path / "candidate_current_harness_refs.yaml"
    candidate_refs_path.write_text(
        yaml.safe_dump(
            {
                "harness_refs": {
                    "solver": str(after_solver.resolve()),
                    "reviewer": str(after_reviewer.resolve()),
                },
                "published_roles": ["solver", "reviewer"],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    class MultiRoleMemberOptimizer:
        async def optimize(
            self,
            eval_ref_path: str,
            analysis_result_path: str,
            harness_refs_path: str,
            output_dir: str,
        ) -> str:
            run_dir = Path(output_dir) / "member_optimization_001"
            run_dir.mkdir(parents=True, exist_ok=True)
            plan_path = run_dir / "plan.yaml"
            execution_path = run_dir / "execution.json"
            verification_path = run_dir / "verification.json"
            fix_path = run_dir / "fix.json"
            plan_path.write_text(yaml.safe_dump({"actions": []}), encoding="utf-8")
            execution_path.write_text(json.dumps({"results": []}), encoding="utf-8")
            verification_path.write_text(json.dumps({"passed": True}), encoding="utf-8")
            fix_path.write_text(json.dumps({"status": "not_needed"}), encoding="utf-8")
            ref_path = run_dir / "member_optimization_ref.yaml"
            ref_path.write_text(
                yaml.safe_dump(
                    {
                        "optimization_id": run_dir.name,
                        "status": "success",
                        "optimized_harness_refs_path": str(candidate_refs_path.resolve()),
                        "published_roles": ["solver", "reviewer"],
                        "plan_path": str(plan_path.resolve()),
                        "execution_result_path": str(execution_path.resolve()),
                        "verification_path": str(verification_path.resolve()),
                        "fix_result_path": str(fix_path.resolve()),
                    },
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            return str(ref_path.resolve())

    orchestrator.member_optimizer = MultiRoleMemberOptimizer()
    orchestrator.evaluator = _HarnessScoredFakeEvaluator(
        {
            str(initial_refs_path.resolve()): 0.0,
            str(candidate_refs_path.resolve()): 1.0,
        }
    )

    current_refs = await orchestrator._maybe_optimize_member_harness(
        eval_ref_paths=[str(eval_ref_path)],
        harness_refs_path=str(initial_refs_path.resolve()),
    )

    assert current_refs == str(candidate_refs_path.resolve())
    context = orchestrator.context_store.load()
    assert context.current.harness_refs == {
        "solver": str(after_solver.resolve()),
        "reviewer": str(after_reviewer.resolve()),
    }
    assert context.history.member_optimizations[-1].role == "solver,reviewer"
    assert context.history.member_optimizations[-1].after_role_harness_ref_path == (
        f"solver={after_solver.resolve()};reviewer={after_reviewer.resolve()}"
    )


@pytest.mark.asyncio
async def test_member_optimization_candidate_gate_replays_original_dataset_cases(tmp_path: Path) -> None:
    """Candidate gate re-evaluates original cases, not slim eval_ref case records."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace("")
    orchestrator.context_store.save(orchestrator.context_store.create("test task"))

    dataset_path = tmp_path / "cases.json"
    dataset_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "case_001",
                        "input": {"user_message": "make a deck"},
                        "reference": {
                            "required_behaviors": [
                                {
                                    "id": "deck_quality",
                                    "description": "deck is high quality",
                                    "weight": 1.0,
                                    "rubric": "score 1-5",
                                }
                            ]
                        },
                        "metadata": {"dimension": "storyline"},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    eval_dir = tmp_path / "source_eval"
    eval_dir.mkdir()
    summary_path = eval_dir / "summary.json"
    summary_path.write_text(json.dumps({"average_score": 0.2}), encoding="utf-8")
    eval_ref_path = eval_dir / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "eval_id": "eval",
                "summary_path": str(summary_path.resolve()),
                "team_skill_ref_path": "team_skills/current",
                "cases": [
                    {
                        "case_id": "case_001",
                        "case_path": str(dataset_path.resolve()),
                        "case_index": 1,
                        "status": "passed",
                        "score": 0.2,
                    }
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    initial_refs = "expert_harnesses/harness_refs.yaml"
    candidate_refs = "workspace/member_optimizations/current_harness_refs.yaml"
    evaluator = _HarnessScoredFakeEvaluator({candidate_refs: 0.8})
    orchestrator.evaluator = evaluator

    gate = await orchestrator._evaluate_member_candidate_gate(
        source_eval_ref_path=str(eval_ref_path),
        before_harness_refs_path=initial_refs,
        candidate_harness_refs_path=candidate_refs,
    )

    assert gate["status"] == "accepted"
    replayed_case = evaluator.calls[0]["cases"][0]
    assert replayed_case["case_id"] == "case_001"
    assert replayed_case["input"]["user_message"] == "make a deck"
    assert replayed_case["reference"]["required_behaviors"][0]["id"] == "deck_quality"
    assert replayed_case["metadata"]["dimension"] == "storyline"
    assert replayed_case["case_path"] == str(dataset_path.resolve())
    assert replayed_case["case_index"] == 1


@pytest.mark.asyncio
async def test_member_candidate_gate_accepts_target_behavior_improvement(tmp_path: Path) -> None:
    """Candidate gate accepts a candidate that improves the failed behavior without lowering score."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace("")
    orchestrator.context_store.save(orchestrator.context_store.create("test task"))

    dataset_path = tmp_path / "cases.json"
    dataset_path.write_text(
        json.dumps({"cases": [{"case_id": "case_001", "input": {}, "reference": {}}]}),
        encoding="utf-8",
    )
    source_eval_dir = tmp_path / "source_eval"
    source_eval_dir.mkdir()
    source_case_dir = source_eval_dir / "cases" / "c001"
    source_case_dir.mkdir(parents=True)
    source_result_path = source_case_dir / "result.json"
    source_result_path.write_text(
        json.dumps(
            {
                "case_id": "case_001",
                "status": "failed",
                "score": 0.7,
                "evaluation": {
                    "method": "llm_as_judge",
                    "passed": False,
                    "metadata": {
                        "parsed": {
                            "behaviors": [
                                {"id": "visual_weight", "score": 0.2, "failure_reason": "flat"},
                                {"id": "deliverable_contract", "score": 1.0},
                            ]
                        }
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    source_summary_path = source_eval_dir / "summary.json"
    source_summary_path.write_text(json.dumps({"average_score": 0.7}), encoding="utf-8")
    source_eval_ref_path = source_eval_dir / "eval_ref.yaml"
    source_eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "eval_id": "source_eval",
                "summary_path": str(source_summary_path.resolve()),
                "team_skill_ref_path": "team_skill",
                "cases": [
                    {
                        "case_id": "case_001",
                        "case_path": str(dataset_path.resolve()),
                        "case_index": 1,
                        "result_path": str(source_result_path.resolve()),
                        "status": "failed",
                        "score": 0.7,
                    }
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    initial_refs = "initial_refs.yaml"
    candidate_refs = "candidate_refs.yaml"
    orchestrator.evaluator = _BehaviorScoredFakeEvaluator(
        average_scores_by_harness_ref={candidate_refs: 0.7},
        behavior_scores_by_harness_ref={
            candidate_refs: {"visual_weight": 1.0, "deliverable_contract": 1.0},
        },
    )

    gate = await orchestrator._evaluate_member_candidate_gate(
        source_eval_ref_path=str(source_eval_ref_path),
        before_harness_refs_path=initial_refs,
        candidate_harness_refs_path=candidate_refs,
    )

    assert gate["accepted"] is True
    assert gate["status"] == "accepted"
    assert gate["reason"] == "candidate_improved_target_behavior"
    assert gate["source_score"] == 0.7
    assert gate["candidate_score"] == 0.7
    assert gate["target_behavior_delta"] > 0

    missing_tool_gate = await orchestrator._evaluate_member_candidate_gate(
        source_eval_ref_path=str(source_eval_ref_path),
        before_harness_refs_path=initial_refs,
        candidate_harness_refs_path=candidate_refs,
        expected_tool_names=["headless_browser_validator"],
        capabilities=[
            {
                "role": "qa-tester",
                "action_group": "tool",
                "operation": "add",
                "runtime_name": "headless_browser_validator",
            }
        ],
    )

    assert missing_tool_gate["accepted"] is False
    assert missing_tool_gate["reason"] == "expected_tool_not_invoked"
    assert missing_tool_gate["missing_expected_tool_names"] == ["headless_browser_validator"]

    orchestrator.evaluator = _BehaviorScoredFakeEvaluator(
        average_scores_by_harness_ref={candidate_refs: 0.7},
        behavior_scores_by_harness_ref={
            candidate_refs: {"visual_weight": 1.0, "deliverable_contract": 1.0},
        },
        failed_machine_evidence_by_harness_ref={
            candidate_refs: "case_web_verification",
        },
    )
    failed_machine_gate = await orchestrator._evaluate_member_candidate_gate(
        source_eval_ref_path=str(source_eval_ref_path),
        before_harness_refs_path=initial_refs,
        candidate_harness_refs_path=candidate_refs,
    )

    assert failed_machine_gate["accepted"] is False
    assert failed_machine_gate["reason"] == "candidate_machine_evidence_failed"
    assert failed_machine_gate["failed_machine_evidence"] == ["case_001:case_web_verification:failed"]

    orchestrator.evaluator = _BehaviorScoredFakeEvaluator(
        average_scores_by_harness_ref={candidate_refs: 0.7},
        behavior_scores_by_harness_ref={
            candidate_refs: {"visual_weight": 1.0, "deliverable_contract": 1.0},
        },
    )

    async def failed_machine_holdout(**_: Any) -> dict[str, Any]:
        return {
            "status": "completed",
            "case_count": 1,
            "score_delta": 0.1,
            "candidate_failed_machine_evidence": ["holdout_001:case_web_verification:failed"],
        }

    orchestrator._evaluate_member_candidate_holdout = failed_machine_holdout  # type: ignore[method-assign]
    failed_holdout_machine_gate = await orchestrator._evaluate_member_candidate_gate(
        source_eval_ref_path=str(source_eval_ref_path),
        before_harness_refs_path=initial_refs,
        candidate_harness_refs_path=candidate_refs,
    )

    assert failed_holdout_machine_gate["accepted"] is False
    assert failed_holdout_machine_gate["reason"] == ("candidate_holdout_machine_evidence_failed")


def test_rejected_capability_equivalence_ignores_generated_file_name() -> None:
    from openjiuwen.rsi.orchestrator.orchestrator import (
        _capabilities_equivalent,
    )

    first = {
        "role": "qa-tester",
        "action_group": "tool",
        "operation": "add",
        "runtime_name": "browser_smoke_test",
        "capability_tokens": [
            "browser",
            "capture",
            "console",
            "errors",
            "headless",
            "runtime",
            "validation",
        ],
    }
    second = {
        "role": "qa-tester",
        "action_group": "tool",
        "operation": "add",
        "runtime_name": "headless_browser_validator",
        "capability_tokens": [
            "browser",
            "console",
            "errors",
            "headless",
            "runtime",
            "state",
            "validation",
        ],
    }

    assert _capabilities_equivalent(first, second) is True


def test_eval_invoked_tool_names_includes_member_trajectory(tmp_path: Path) -> None:
    from openjiuwen.rsi.orchestrator.orchestrator import (
        _eval_invoked_tool_names,
    )

    trajectory_dir = tmp_path / "tr"
    trajectory_dir.mkdir()
    (trajectory_dir / "game-logic-engineer.jsonl").write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "kind": "tool",
                        "error": None,
                        "detail": {
                            "tool_name": "runtime_smoke_validator",
                            "call_result": {"success": True},
                        },
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(
        json.dumps({"trajectory_dir": str(trajectory_dir)}),
        encoding="utf-8",
    )
    eval_ref_path = tmp_path / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "cases": [{"case_id": "case_001", "trace_path": str(trace_path)}],
            }
        ),
        encoding="utf-8",
    )

    assert _eval_invoked_tool_names(str(eval_ref_path)) == {"runtime_smoke_validator"}


def test_eval_invoked_tool_names_excludes_pre_execution_skip(tmp_path: Path) -> None:
    from openjiuwen.rsi.orchestrator.orchestrator import (
        _eval_invoked_tool_names,
    )

    trajectory_dir = tmp_path / "tr"
    trajectory_dir.mkdir()
    (trajectory_dir / "frontend-coder.jsonl").write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "kind": "tool",
                        "error": None,
                        "detail": {
                            "tool_name": "pre_delivery_validator",
                            "call_args": '{"content":"unterminated',
                            "call_result": (
                                "[reliability] Tool call skipped before execution because "
                                "its arguments are not a complete JSON object."
                            ),
                        },
                    },
                    {
                        "kind": "tool",
                        "error": None,
                        "detail": {
                            "tool_name": "unavailable_validator",
                            "call_result": None,
                        },
                    },
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(
        json.dumps({"trajectory_dir": str(trajectory_dir)}),
        encoding="utf-8",
    )
    eval_ref_path = tmp_path / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "cases": [{"case_id": "case_001", "trace_path": str(trace_path)}],
            }
        ),
        encoding="utf-8",
    )

    assert _eval_invoked_tool_names(str(eval_ref_path)) == set()


@pytest.mark.asyncio
async def test_member_candidate_gate_filters_inconclusive_source_cases(tmp_path: Path) -> None:
    """Source error cases do not block member gate when comparable judged cases remain."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace("")
    orchestrator.context_store.save(orchestrator.context_store.create("test task"))

    dataset_path = tmp_path / "cases.json"
    dataset_path.write_text(
        json.dumps(
            {
                "cases": [
                    {"case_id": "timeout_case", "input": "bad", "reference": {}},
                    {"case_id": "judged_case", "input": "good", "reference": {}},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    source_eval_dir = tmp_path / "source_eval"
    source_eval_dir.mkdir()
    cases_dir = source_eval_dir / "cases"
    cases_dir.mkdir()
    timeout_dir = cases_dir / "c001"
    judged_dir = cases_dir / "c002"
    timeout_dir.mkdir()
    judged_dir.mkdir()
    timeout_result_path = timeout_dir / "result.json"
    judged_result_path = judged_dir / "result.json"
    timeout_result_path.write_text(
        json.dumps(
            {
                "case_id": "timeout_case",
                "status": "error",
                "score": 0.0,
                "evaluation": {"method": "error", "passed": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    judged_result_path.write_text(
        json.dumps(
            {
                "case_id": "judged_case",
                "status": "passed",
                "score": 0.9,
                "evaluation": {"method": "llm_as_judge", "passed": True},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    source_summary_path = source_eval_dir / "summary.json"
    source_summary_path.write_text(json.dumps({"average_score": 0.45}), encoding="utf-8")
    source_eval_ref_path = source_eval_dir / "eval_ref.yaml"
    source_eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "eval_id": "source_eval",
                "summary_path": str(source_summary_path.resolve()),
                "team_skill_ref_path": "team_skill",
                "cases": [
                    {
                        "case_id": "timeout_case",
                        "case_path": str(dataset_path.resolve()),
                        "case_index": 1,
                        "result_path": str(timeout_result_path.resolve()),
                        "status": "error",
                        "score": 0.0,
                    },
                    {
                        "case_id": "judged_case",
                        "case_path": str(dataset_path.resolve()),
                        "case_index": 2,
                        "result_path": str(judged_result_path.resolve()),
                        "status": "passed",
                        "score": 0.9,
                    },
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    initial_refs = "initial_refs.yaml"
    candidate_refs = "candidate_refs.yaml"
    evaluator = _HarnessScoredFakeEvaluator({candidate_refs: 1.0})
    orchestrator.evaluator = evaluator

    gate = await orchestrator._evaluate_member_candidate_gate(
        source_eval_ref_path=str(source_eval_ref_path),
        before_harness_refs_path=initial_refs,
        candidate_harness_refs_path=candidate_refs,
    )

    assert gate["accepted"] is True
    assert gate["status"] == "accepted"
    assert gate["source_score"] == 0.9
    assert [case["case_id"] for case in evaluator.calls[0]["cases"]] == ["judged_case"]
    assert gate["filtered_source_cases"] == 1


@pytest.mark.asyncio
async def test_member_candidate_holdout_generates_transfer_case_when_dataset_is_exhausted(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator.config = replace(
        orchestrator.config,
        member_optimizer=replace(
            orchestrator.config.member_optimizer,
            candidate_holdout_cases=1,
        ),
    )
    orchestrator._configure_team_workspace("")
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    dataset_file = dataset_dir / "cases.json"
    dataset_file.write_text("{}", encoding="utf-8")
    context = orchestrator.context_store.create("test transfer behavior")
    orchestrator.context_store.save(
        replace(
            context,
            current=replace(
                context.current,
                dataset=DatasetArtifact(
                    dataset_id="dataset",
                    dataset_dir=str(dataset_dir),
                    dataset_files=[str(dataset_file)],
                ),
            ),
        )
    )
    source_case = {"case_id": "source_case", "input": {"user_message": "source"}}
    orchestrator.data_loader = _FakeDataLoader([[source_case]], str(tmp_path / "batch_plan.yaml"))
    evaluator = _HarnessScoredFakeEvaluator({"before.yaml": 0.6, "candidate.yaml": 0.8})
    orchestrator.evaluator = evaluator

    async def generate_holdout(**_: Any):
        return (
            [{"case_id": "transfer_case", "input": {"user_message": "transfer"}}],
            {"generated": True, "generated_seed_ref": "seed.json"},
        )

    orchestrator._generate_member_candidate_holdout_cases = generate_holdout  # type: ignore[method-assign]
    result = await orchestrator._evaluate_member_candidate_holdout(
        source_cases=[source_case],
        source_eval={"team_skill_ref_path": "team_skill"},
        before_harness_refs_path="before.yaml",
        candidate_harness_refs_path="candidate.yaml",
        gate_root=tmp_path / "gate",
        capabilities=[{"role": "builder", "expected_effect": "transfer behavior"}],
    )

    assert result["status"] == "completed"
    assert result["generated"] is True
    assert result["case_ids"] == ["transfer_case"]
    assert result["score_delta"] == pytest.approx(0.2)
    assert result["candidate_failed_machine_evidence"] == []
    assert [call["harness_refs_path"] for call in evaluator.calls] == [
        "before.yaml",
        "candidate.yaml",
    ]


@pytest.mark.asyncio
async def test_member_candidate_holdout_reports_failed_candidate_machine_evidence(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator.config = replace(
        orchestrator.config,
        member_optimizer=replace(
            orchestrator.config.member_optimizer,
            candidate_holdout_cases=1,
        ),
    )
    orchestrator._configure_team_workspace("")
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    dataset_file = dataset_dir / "cases.json"
    holdout_case = {"case_id": "holdout_case", "input": {"user_message": "holdout"}}
    dataset_file.write_text(
        json.dumps({"cases": [holdout_case]}),
        encoding="utf-8",
    )
    context = orchestrator.context_store.create("test holdout evidence")
    orchestrator.context_store.save(
        replace(
            context,
            current=replace(
                context.current,
                dataset=DatasetArtifact(
                    dataset_id="dataset",
                    dataset_dir=str(dataset_dir),
                    dataset_files=[str(dataset_file)],
                ),
            ),
        )
    )
    orchestrator.data_loader = _FakeDataLoader(
        [[holdout_case]],
        str(tmp_path / "batch_plan.yaml"),
    )
    orchestrator.evaluator = _BehaviorScoredFakeEvaluator(
        average_scores_by_harness_ref={"before.yaml": 0.6, "candidate.yaml": 0.8},
        behavior_scores_by_harness_ref={"before.yaml": {}, "candidate.yaml": {}},
        failed_machine_evidence_by_harness_ref={
            "candidate.yaml": "case_web_verification",
        },
    )

    result = await orchestrator._evaluate_member_candidate_holdout(
        source_cases=[{"case_id": "source_case", "input": {"user_message": "source"}}],
        source_eval={"team_skill_ref_path": "team_skill"},
        before_harness_refs_path="before.yaml",
        candidate_harness_refs_path="candidate.yaml",
        gate_root=tmp_path / "gate",
        capabilities=[],
    )

    assert result["status"] == "completed"
    assert result["candidate_failed_machine_evidence"] == ["holdout_case:case_web_verification:failed"]


@pytest.mark.asyncio
async def test_accepted_candidate_gate_becomes_terminal_promotion_evaluation(tmp_path: Path) -> None:
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace("")
    context = orchestrator.context_store.create("test task")
    candidate_refs = str((tmp_path / "candidate_refs.yaml").resolve())
    evaluator = _HarnessScoredFakeEvaluator({candidate_refs: 0.85})
    candidate_eval_ref = await evaluator.evaluate_batch(
        cases=[{"case_id": "case_001", "input": {"user_message": "test"}}],
        team_skill_ref_path="team_skill",
        harness_refs_path=candidate_refs,
        output_dir=str(tmp_path / "candidate_eval"),
    )
    orchestrator.context_store.save(
        replace(
            context,
            metadata={
                **context.metadata,
                "member_candidate_gates": [
                    {
                        "status": "accepted",
                        "candidate_harness_refs_path": candidate_refs,
                        "candidate_eval_ref_path": candidate_eval_ref,
                    }
                ],
            },
        )
    )

    assert (
        orchestrator._accepted_candidate_eval_ref_path(
            harness_refs_path=candidate_refs,
        )
        == candidate_eval_ref
    )


@pytest.mark.asyncio
async def test_member_optimizer_allows_actionable_issue_on_passed_source_batch(tmp_path: Path) -> None:
    """Actionable member analysis may still produce a candidate when the source batch passed."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace("")
    orchestrator.context_store.save(orchestrator.context_store.create("test task"))
    fake_optimizer = _FakeMemberOptimizer()
    orchestrator.member_optimizer = fake_optimizer

    eval_dir = tmp_path / "source_eval"
    eval_dir.mkdir()
    result_path = eval_dir / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "score": 1.0,
                "evaluation": {"method": "llm_as_judge", "passed": True},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    summary_path = eval_dir / "summary.json"
    summary_path.write_text(json.dumps({"average_score": 1.0}), encoding="utf-8")
    eval_ref_path = eval_dir / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "eval_id": "eval",
                "summary_path": str(summary_path.resolve()),
                "cases": [
                    {
                        "case_id": "case_001",
                        "result_path": str(result_path.resolve()),
                        "status": "passed",
                        "score": 1.0,
                    }
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    analysis_ref_path = tmp_path / "analysis_ref.yaml"
    analysis_ref_path.write_text(
        yaml.safe_dump(
            {
                "issues": [
                    {
                        "issue_id": "weak_visual_feedback",
                        "optimization_target": "member_harness",
                        "target_ref": "member_harness.ui-designer.prompt",
                        "summary": "hover feedback is weak despite the case passing",
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    orchestrator.analysis_ref_by_eval_ref_path[str(eval_ref_path)] = str(analysis_ref_path)
    initial_refs = "initial_refs.yaml"
    candidate_refs = str(
        workspace_dir / "default_team" / "member_optimizations" / "member_optimization_001" / "optimized_harness"
    )
    orchestrator.evaluator = _HarnessScoredFakeEvaluator(
        {
            initial_refs: 1.0,
            candidate_refs: 1.0,
        }
    )

    current_refs = await orchestrator._maybe_optimize_member_harness(
        eval_ref_paths=[str(eval_ref_path)],
        harness_refs_path=initial_refs,
    )

    assert current_refs == initial_refs
    assert len(fake_optimizer.calls) == 1
    gate = orchestrator.context_store.load().metadata["member_candidate_gates"][0]
    assert gate["status"] == "rejected"
    assert gate["reason"] == "candidate_did_not_improve_source_batch"


@pytest.mark.asyncio
async def test_member_optimizer_consumes_earlier_failing_member_issue(tmp_path: Path) -> None:
    """A later passed member-stage analysis must not hide an earlier failing member issue."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace("")
    orchestrator.context_store.save(orchestrator.context_store.create("test task"))
    fake_optimizer = _FakeMemberOptimizer()
    orchestrator.member_optimizer = fake_optimizer

    def write_eval(stage_name: str, case_id: str, *, passed: bool, score: float) -> tuple[str, str]:
        eval_dir = tmp_path / stage_name / "evaluation"
        eval_dir.mkdir(parents=True)
        result_path = eval_dir / f"{case_id}_result.json"
        result_path.write_text(
            json.dumps(
                {
                    "status": "passed" if passed else "failed",
                    "score": score,
                    "evaluation": {"method": "llm_as_judge", "passed": passed},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        summary_path = eval_dir / "summary.json"
        summary_path.write_text(json.dumps({"average_score": score}), encoding="utf-8")
        eval_ref_path = eval_dir / "eval_ref.yaml"
        eval_ref_path.write_text(
            yaml.safe_dump(
                {
                    "eval_id": stage_name,
                    "summary_path": str(summary_path.resolve()),
                    "cases": [
                        {
                            "case_id": case_id,
                            "result_path": str(result_path.resolve()),
                            "status": "passed" if passed else "failed",
                            "score": score,
                        }
                    ],
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        analysis_ref_path = eval_dir / "analysis_ref.yaml"
        analysis_ref_path.write_text(
            yaml.safe_dump(
                {
                    "issues": [
                        {
                            "issue_id": f"{case_id}_member_issue",
                            "optimization_target": "member_harness",
                            "target_ref": "content-author.prompt",
                            "summary": "member prompt lacks concrete delivery steps",
                        }
                    ]
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        orchestrator.analysis_ref_by_eval_ref_path[str(eval_ref_path)] = str(analysis_ref_path)
        return str(eval_ref_path), str(analysis_ref_path)

    failing_team_eval_ref_path, _ = write_eval(
        "team_skill_optimization",
        "case_team_failed",
        passed=False,
        score=0.0,
    )
    passed_member_eval_ref_path, _ = write_eval(
        "member_optimization",
        "case_member_passed",
        passed=True,
        score=1.0,
    )

    initial_refs = "initial_refs.yaml"
    candidate_refs = str(
        workspace_dir / "default_team" / "member_optimizations" / "member_optimization_001" / "optimized_harness"
    )
    orchestrator.evaluator = _HarnessScoredFakeEvaluator(
        {
            initial_refs: 0.0,
            candidate_refs: 1.0,
        }
    )

    await orchestrator._maybe_optimize_member_harness(
        eval_ref_paths=[failing_team_eval_ref_path, passed_member_eval_ref_path],
        harness_refs_path=initial_refs,
    )

    assert len(fake_optimizer.calls) == 1
    assert fake_optimizer.calls[0]["eval_ref_path"] == failing_team_eval_ref_path


@pytest.mark.asyncio
async def test_batch_reuses_team_stage_evidence_for_member_issue_without_team_issue(
    tmp_path: Path,
) -> None:
    """Member-only analysis should not force a second batch execution."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace("")
    orchestrator.context_store.save(orchestrator.context_store.create("test task"))
    fake_evaluator = _FakeEvaluator()
    fake_member_optimizer = _FakeMemberOptimizer()
    orchestrator.evaluator = fake_evaluator
    orchestrator.member_optimizer = fake_member_optimizer
    orchestrator.evaluation_result_analyzer = _MemberOnlyAnalyzer()

    result = await orchestrator._run_batch_optimization(
        batch=[{"case_id": "case_001"}],
        batch_index=1,
        epoch=1,
        team_skill_ref_path="team_skills/current",
        harness_refs_path="harness_refs.yaml",
        dataset=DatasetArtifact(
            dataset_id="dataset",
            dataset_dir=str(tmp_path / "dataset"),
            dataset_files=[],
        ),
    )

    assert len(fake_evaluator.calls) == 1
    assert Path(fake_evaluator.calls[0]["output_dir"]).parts[-4:] == (
        "evaluations",
        "e001",
        "b001",
        "ts",
    )
    assert len(fake_member_optimizer.calls) == 1
    assert fake_member_optimizer.calls[0]["eval_ref_path"] == result.eval_ref_paths[0]
    assert Path(fake_member_optimizer.calls[0]["analysis_result_path"]).parts[-2:] == (
        "a",
        "analysis_ref.yaml",
    )
    assert result.eval_ref_paths == [fake_member_optimizer.calls[0]["eval_ref_path"]]


@pytest.mark.asyncio
async def test_batch_evaluation_emits_batch_progress_metadata(tmp_path: Path) -> None:
    """Observers can render epoch/batch progress while batch evaluation runs."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace("")
    orchestrator.context_store.save(orchestrator.context_store.create("test task"))
    orchestrator.evaluator = _ScoredFakeEvaluator(0.42)
    orchestrator.evaluation_result_analyzer = _NoIssueAnalyzer()
    progress_events: list[Any] = []
    orchestrator._progress_callback = progress_events.append

    await orchestrator._evaluate_batch(
        batch=[{"case_id": "case_001"}, {"case_id": "case_002"}],
        batch_index=2,
        epoch=1,
        optimization_stage="member_optimization",
        team_skill_ref_path="team_skills/current",
        harness_refs_path="harness_refs.yaml",
        dataset=DatasetArtifact(
            dataset_id="dataset",
            dataset_dir=str(tmp_path / "dataset"),
            dataset_files=[],
            cases=5,
        ),
        phase="epoch_1:batch_2:member_current",
    )

    batch_events = [
        event for event in progress_events if event.stage == "member_optimization" and event.batch_index == 2
    ]
    assert [event.message for event in batch_events] == [
        "member_optimization batch 2 evaluation started",
        "member_optimization batch 2 evaluation completed",
    ]
    assert all(event.epoch == 1 for event in batch_events)
    assert all(event.metrics["case_count"] == 2 for event in batch_events)
    assert all(event.metrics["batch_total"] == 3 for event in batch_events)
    assert batch_events[-1].score == 0.42


@pytest.mark.asyncio
async def test_batch_reruns_member_stage_after_team_skill_issue(tmp_path: Path) -> None:
    """Team Skill issues are handled before taking fresh member-stage evidence."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace("")
    orchestrator.context_store.save(orchestrator.context_store.create("test task"))
    fake_evaluator = _FakeEvaluator()
    fake_team_optimizer = _FakeTeamSkillOptimizer()
    fake_member_optimizer = _FakeMemberOptimizer()
    orchestrator.evaluator = fake_evaluator
    orchestrator.team_skill_optimizer = fake_team_optimizer
    orchestrator.member_optimizer = fake_member_optimizer
    orchestrator.evaluation_result_analyzer = _SequencedAnalyzer(
        [
            "team_skill",
            "member_harness",
        ]
    )

    result = await orchestrator._run_batch_optimization(
        batch=[{"case_id": "case_001"}],
        batch_index=1,
        epoch=1,
        team_skill_ref_path="team_skills/current",
        harness_refs_path="harness_refs.yaml",
        dataset=DatasetArtifact(
            dataset_id="dataset",
            dataset_dir=str(tmp_path / "dataset"),
            dataset_files=[],
        ),
    )

    assert len(fake_evaluator.calls) == 2
    assert Path(fake_evaluator.calls[0]["output_dir"]).parts[-4:] == (
        "evaluations",
        "e001",
        "b001",
        "ts",
    )
    assert Path(fake_evaluator.calls[1]["output_dir"]).parts[-4:] == (
        "evaluations",
        "e001",
        "b001",
        "mh",
    )
    assert len(fake_team_optimizer.calls) == 1
    assert len(fake_member_optimizer.calls) == 1
    assert fake_member_optimizer.calls[0]["eval_ref_path"] == result.eval_ref_paths[-1]
    assert result.eval_ref_paths == [
        str((Path(fake_evaluator.calls[0]["output_dir"]) / "eval_ref.yaml").resolve()),
        str((Path(fake_evaluator.calls[1]["output_dir"]) / "eval_ref.yaml").resolve()),
    ]


@pytest.mark.asyncio
async def test_member_stage_does_not_reenter_team_skill_optimizer(tmp_path: Path) -> None:
    """Member stage analysis is scoped to member harness optimization, not a second Team Skill pass."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace("")
    orchestrator.context_store.save(orchestrator.context_store.create("test task"))
    fake_evaluator = _FakeEvaluator()
    fake_team_optimizer = _FakeTeamSkillOptimizer()
    fake_member_optimizer = _FakeMemberOptimizer()
    orchestrator.evaluator = fake_evaluator
    orchestrator.team_skill_optimizer = fake_team_optimizer
    orchestrator.member_optimizer = fake_member_optimizer
    orchestrator.evaluation_result_analyzer = _SequencedAnalyzer(
        [
            "team_skill",
            "team_skill",
        ]
    )

    result = await orchestrator._run_batch_optimization(
        batch=[{"case_id": "case_001"}],
        batch_index=1,
        epoch=1,
        team_skill_ref_path="team_skills/current",
        harness_refs_path="harness_refs.yaml",
        dataset=DatasetArtifact(
            dataset_id="dataset",
            dataset_dir=str(tmp_path / "dataset"),
            dataset_files=[],
        ),
    )

    assert len(fake_evaluator.calls) == 2
    assert Path(fake_evaluator.calls[0]["output_dir"]).parts[-4:] == (
        "evaluations",
        "e001",
        "b001",
        "ts",
    )
    assert Path(fake_evaluator.calls[1]["output_dir"]).parts[-4:] == (
        "evaluations",
        "e001",
        "b001",
        "mh",
    )
    assert len(fake_team_optimizer.calls) == 1
    assert fake_member_optimizer.calls == []
    assert result.eval_ref_paths == [
        str((Path(fake_evaluator.calls[0]["output_dir"]) / "eval_ref.yaml").resolve()),
        str((Path(fake_evaluator.calls[1]["output_dir"]) / "eval_ref.yaml").resolve()),
    ]
    context = orchestrator.context_store.load()
    assert context.history.team_skill_optimizations[0].eval_ref_path == result.eval_ref_paths[0]
    assert len(context.history.team_skill_optimizations) == 1


@pytest.mark.asyncio
async def test_run_loads_existing_dataset_when_dataset_dir_is_provided(tmp_path: Path) -> None:
    """run uses DataLoader directly when caller provides dataset_dir."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    dataset_dir = tmp_path / "existing_dataset"
    _write_config(config_path, workspace_dir, batch_size=2)
    _write_dataset(dataset_dir, ["case_001", "case_002", "case_003"])

    orchestrator = OptimizationOrchestrator(str(config_path))
    fake_evaluator = _FakeEvaluator()
    fake_member_optimizer = _FakeMemberOptimizer()
    orchestrator.evaluator = fake_evaluator
    orchestrator.member_optimizer = fake_member_optimizer
    loaded_dir = await orchestrator.run(
        "evaluate math agent",
        dataset_dir=str(dataset_dir),
        team_skill_ref_path="team_skills/v1/team_skill_ref.yaml",
        harness_refs_path="expert_harnesses/harness_refs.yaml",
    )

    assert loaded_dir == str(dataset_dir.resolve())
    assert orchestrator.current_dataset_artifact is None
    assert not hasattr(orchestrator, "current_dataset_batches")
    context = orchestrator.context_store.load()
    assert context.current.dataset is not None
    assert context.current.dataset.dataset_dir == str(dataset_dir.resolve())
    assert context.current.dataset.dataset_files == [str((dataset_dir / "cases.json").resolve())]
    assert context.current.harness_refs_path == "expert_harnesses/harness_refs.yaml"
    assert orchestrator.optimized_harness_refs_path is None
    assert context.current.harness_refs == {}
    assert not (workspace_dir / "expert_harnesses").exists()
    assert context.current.eval_ref_path == orchestrator.optimized_eval_ref_paths[-1]
    assert context.current.team_skill_ref_path == "team_skills/v1/team_skill_ref.yaml"
    assert orchestrator.optimized_team_skill_ref_path is None
    assert context.strategy.evaluation_strategy == "hybrid"
    assert context.strategy.coordination_strategy == "team_first_single_pass"
    assert context.strategy.promotion_policy == "epoch_full_evaluation"
    assert context.strategy.strategy_name == "hybrid_team_first_single_pass"
    assert len(fake_evaluator.calls) == 3
    assert [[case["case_id"] for case in call["cases"]] for call in fake_evaluator.calls] == [
        ["case_001", "case_002"],
        ["case_003"],
        ["case_001", "case_002", "case_003"],
    ]
    assert Path(fake_evaluator.calls[0]["output_dir"]).parts[-4:] == (
        "evaluations",
        "e001",
        "b001",
        "ts",
    )
    assert Path(fake_evaluator.calls[1]["output_dir"]).parts[-4:] == (
        "evaluations",
        "e001",
        "b002",
        "ts",
    )
    assert Path(fake_evaluator.calls[2]["output_dir"]).parts[-3:] == (
        "evaluations",
        "e001",
        "full",
    )
    assert len(orchestrator.analysis_ref_by_eval_ref_path) == len(fake_evaluator.calls) - 1
    for eval_ref_path, analysis_ref_path in orchestrator.analysis_ref_by_eval_ref_path.items():
        assert Path(analysis_ref_path).is_file()
        assert Path(analysis_ref_path).parent.name == "a"
        assert Path(analysis_ref_path).parent.parent == Path(eval_ref_path).parent
        analysis_ref = yaml.safe_load(Path(analysis_ref_path).read_text(encoding="utf-8"))
        issues_path = Path(analysis_ref_path).parent / "issues.yaml"
        assert issues_path.is_file()
        assert analysis_ref["issues_path"] == str(issues_path)
        issues = yaml.safe_load(issues_path.read_text(encoding="utf-8"))["issues"]
        expected_target = "team_skill" if Path(analysis_ref_path).parent.parent.name == "ts" else "member_harness"
        for issue in issues:
            assert issue["optimization_target"] == expected_target
        eval_ref = yaml.safe_load(Path(eval_ref_path).read_text(encoding="utf-8"))
        assert eval_ref["analysis_ref_path"] == analysis_ref_path
    assert context.history.team_skill_optimizations == []
    assert context.history.member_optimizations == []
    assert context.best.eval_ref_path == orchestrator.optimized_eval_ref_paths[-1]
    assert context.metadata["latest_checkpoint_epoch"] == 1
    assert context.metadata["best_checkpoint_epoch"] == 1
    assert context.metadata["checkpoint_scope"] == "epoch"
    assert context.metadata["source_eval_ref_path"] == context.best.eval_ref_path
    assert context.metadata["strategy_name"] == context.strategy.strategy_name
    assert context.metadata["evaluation_strategy"] == context.strategy.evaluation_strategy
    assert context.metadata["coordination_strategy"] == context.strategy.coordination_strategy
    assert context.metadata["promotion_policy"] == context.strategy.promotion_policy
    assert Path(context.metadata["best_checkpoint_path"], "orchestrator_context.yaml").is_file()
    assert orchestrator.experience_ref_paths == []
    assert not (workspace_dir / "default_team" / "optimization_experiences").exists()
    assert fake_member_optimizer.calls == []
    team_datasets_dir = workspace_dir / "default_team" / "datasets"
    assert team_datasets_dir.is_dir()
    assert not list(team_datasets_dir.glob("dataset_*"))


@pytest.mark.asyncio
async def test_run_progress_callback_errors_do_not_break_optimization(tmp_path: Path) -> None:
    """A TUI observer must never become part of the optimization failure path."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    dataset_dir = tmp_path / "existing_dataset"
    _write_config(config_path, workspace_dir, batch_size=10, full_evaluation_enabled=False)
    _write_dataset(dataset_dir, ["case_001"])

    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator.evaluator = _FakeEvaluator()
    orchestrator.member_optimizer = _FakeMemberOptimizer()
    observed_phases: list[str] = []

    def _raising_callback(event: Any) -> None:
        observed_phases.append(event.phase)
        raise RuntimeError("observer failed")

    loaded_dir = await orchestrator.run(
        "evaluate math agent",
        dataset_dir=str(dataset_dir),
        team_skill_ref_path="team_skills/v1/team_skill_ref.yaml",
        harness_refs_path="expert_harnesses/harness_refs.yaml",
        progress_callback=_raising_callback,
    )

    assert loaded_dir == str(dataset_dir.resolve())
    assert observed_phases
    assert orchestrator.context_store.load().current.dataset is not None


@pytest.mark.asyncio
async def test_run_can_skip_epoch_full_evaluation(tmp_path: Path) -> None:
    """When full evaluation is disabled, publish the final batch result directly."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    dataset_dir = tmp_path / "existing_dataset"
    _write_config(
        config_path,
        workspace_dir,
        batch_size=2,
        full_evaluation_enabled=False,
    )
    _write_dataset(dataset_dir, ["case_001", "case_002", "case_003"])

    orchestrator = OptimizationOrchestrator(str(config_path))
    fake_evaluator = _FakeEvaluator()
    orchestrator.evaluator = fake_evaluator
    loaded_dir = await orchestrator.run(
        "evaluate math agent",
        dataset_dir=str(dataset_dir),
        team_skill_ref_path="team_skills/v1/team_skill_ref.yaml",
        harness_refs_path="expert_harnesses/harness_refs.yaml",
    )

    assert loaded_dir == str(dataset_dir.resolve())
    assert len(fake_evaluator.calls) == 2
    assert [[case["case_id"] for case in call["cases"]] for call in fake_evaluator.calls] == [
        ["case_001", "case_002"],
        ["case_003"],
    ]
    assert not (workspace_dir / "default_team" / "evaluations" / "e001" / "full").exists()
    context = orchestrator.context_store.load()
    assert context.strategy.full_evaluation_enabled is False
    assert context.metadata["full_evaluation_enabled"] is False
    assert context.metadata["full_evaluation_skipped_epochs"] == [1]
    assert context.current.eval_ref_path == str(
        (Path(fake_evaluator.calls[-1]["output_dir"]) / "eval_ref.yaml").resolve()
    )
    assert context.best.eval_ref_path == context.current.eval_ref_path
    assert context.best.team_skill_ref_path == context.current.team_skill_ref_path
    assert context.best.harness_refs_path
    assert context.best.score == 0.0
    assert context.metadata["best_checkpoint_epoch"] == 1
    assert context.metadata["best_promotion_source"] == "batch_terminal_without_full_evaluation"
    assert Path(context.metadata["best_checkpoint_path"], "orchestrator_context.yaml").is_file()
    assert "dataset_curation_refs" not in context.metadata


@pytest.mark.asyncio
async def test_run_can_start_from_latest_best_without_resuming_dataset(tmp_path: Path) -> None:
    """A new run can reuse the latest optimized Team refs without resuming old batches."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    dataset_dir = tmp_path / "existing_dataset"
    _write_config(
        config_path,
        workspace_dir,
        batch_size=2,
        full_evaluation_enabled=False,
    )
    _write_dataset(dataset_dir, ["case_001", "case_002"])
    best_harness_refs_path = tmp_path / "best_harness_refs.yaml"
    best_harness_refs_path.write_text(
        yaml.safe_dump(
            {"harness_refs": {"team_leader": str((tmp_path / "best_harness").resolve())}},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    best_team_skill_path = "team_skills/optimized"
    previous = OrchestratorRunContext(
        task_id="previous",
        task="previous task",
        context_path=str(workspace_dir / "default_team" / "orchestrator_context.yaml"),
        checkpoint_dir=str(workspace_dir / "default_team" / "checkpoints"),
        phase=OrchestratorPhase.COMPLETED,
        epoch=1,
        best=replace(
            OrchestratorRunContext(
                task_id="inner",
                task="inner",
                context_path="",
                checkpoint_dir="",
            ).best,
            team_skill_ref_path=best_team_skill_path,
            harness_refs_path=str(best_harness_refs_path.resolve()),
            harness_refs={"team_leader": str((tmp_path / "best_harness").resolve())},
            eval_ref_path=str((tmp_path / "best_eval_ref.yaml").resolve()),
            score=0.88,
        ),
    )
    previous_path = workspace_dir / "default_team" / "orchestrator_context.yaml"
    previous_path.parent.mkdir(parents=True, exist_ok=True)
    OrchestratorContextStore(str(previous_path)).save(previous)

    orchestrator = OptimizationOrchestrator(str(config_path))
    fake_evaluator = _FakeEvaluator()
    orchestrator.evaluator = fake_evaluator

    await orchestrator.run(
        "new task",
        dataset_dir=str(dataset_dir),
        reuse_best_context=True,
        workspace_run_id="sch_new_run",
    )

    assert fake_evaluator.calls
    assert all(call["team_skill_ref_path"] == best_team_skill_path for call in fake_evaluator.calls)
    assert all(call["harness_refs_path"] == str(best_harness_refs_path.resolve()) for call in fake_evaluator.calls)
    context = orchestrator.context_store.load()
    assert Path(context.context_path).parent.name == "default_team--sch_new_run"
    assert context.metadata["workspace_run_id"] == "sch_new_run"
    assert previous_path.read_text(encoding="utf-8")
    assert context.current.team_skill_ref_path == best_team_skill_path
    assert context.current.harness_refs_path == str(best_harness_refs_path.resolve())
    assert context.metadata["reused_best_context_path"] == str(previous_path.resolve())
    assert context.current.dataset is not None
    assert context.current.dataset.dataset_dir == str(dataset_dir.resolve())


@pytest.mark.asyncio
async def test_run_reuses_legacy_context_without_publishing_an_unchanged_run(tmp_path: Path) -> None:
    """A reuse-only run must not replace the published profile without an accepted change."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    dataset_dir = tmp_path / "existing_dataset"
    _write_config(
        config_path,
        workspace_dir,
        batch_size=2,
        full_evaluation_enabled=False,
    )
    _write_dataset(dataset_dir, ["case_001"])
    legacy_team_path = str((tmp_path / "legacy_team_skill").resolve())
    legacy_harness_path = str((tmp_path / "legacy_harness_refs.yaml").resolve())
    Path(legacy_harness_path).write_text(
        yaml.safe_dump({"harness_refs": {}}, allow_unicode=True),
        encoding="utf-8",
    )
    published_path = tmp_path / "published" / "team_context.yaml"
    legacy = OrchestratorRunContext(
        task_id="legacy",
        task="legacy optimized task",
        context_path=str((tmp_path / "legacy" / "orchestrator_context.yaml").resolve()),
        checkpoint_dir=str((tmp_path / "legacy" / "checkpoints").resolve()),
        phase=OrchestratorPhase.COMPLETED,
        epoch=1,
        current=CurrentArtifactRefs(
            team_skill_ref_path=legacy_team_path,
            harness_refs_path=legacy_harness_path,
        ),
    )
    OrchestratorContextStore(str(published_path)).save(legacy)

    orchestrator = OptimizationOrchestrator(str(config_path))
    fake_evaluator = _FakeEvaluator()
    orchestrator.evaluator = fake_evaluator
    await orchestrator.run(
        "new task",
        dataset_dir=str(dataset_dir),
        reuse_best_context=True,
        published_context_path=str(published_path),
    )

    assert fake_evaluator.calls[0]["team_skill_ref_path"] == legacy_team_path
    assert fake_evaluator.calls[0]["harness_refs_path"] == legacy_harness_path
    published = OrchestratorContextStore(str(published_path)).load()
    assert published.context_path == legacy.context_path
    assert not published.best.team_skill_ref_path
    context = orchestrator.context_store.load()
    assert context.metadata["optimization_outcome"] == "no_accepted_change"
    assert context.metadata["accepted_change_count"] == 0
    assert context.metadata["published_optimization"] is False


@pytest.mark.asyncio
async def test_run_without_full_evaluation_accepts_batch_experiences(tmp_path: Path) -> None:
    """Batch optimizations are publishable experience when no full eval gate is configured."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    dataset_dir = tmp_path / "existing_dataset"
    published_path = tmp_path / "published" / "team_context.yaml"
    _write_config(
        config_path,
        workspace_dir,
        batch_size=2,
        full_evaluation_enabled=False,
    )
    _write_dataset(dataset_dir, ["case_001", "case_002"])

    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator.evaluator = _ScoredFakeEvaluator(score=0.72)
    orchestrator.evaluation_result_analyzer = _TeamAndMemberAnalyzer()
    orchestrator.team_skill_optimizer = _FakeTeamSkillOptimizer()
    orchestrator.member_optimizer = _FakeMemberOptimizer()

    await orchestrator.run(
        "evaluate math agent",
        dataset_dir=str(dataset_dir),
        team_skill_ref_path="team_skills/v1/team_skill_ref.yaml",
        harness_refs_path="expert_harnesses/harness_refs.yaml",
        published_context_path=str(published_path),
    )

    assert orchestrator.experience_ref_paths
    experience_index = yaml.safe_load(
        (workspace_dir / "optimization_experiences" / "index.yaml").read_text(encoding="utf-8")
    )
    assert {item["learning_status"] for item in experience_index["experiences"]} == {"accepted"}
    context = orchestrator.context_store.load()
    assert context.metadata["latest_experience_status_updates"]
    assert context.metadata["latest_experience_confirmation_mode"] == "batch_terminal_without_full_evaluation"
    experience_ref = context.metadata["latest_experience_status_updates"][0]
    assert experience_ref["reason"] == "batch_terminal_without_full_evaluation_improved_best"
    assert context.metadata["optimization_outcome"] == "accepted_change"
    assert context.metadata["accepted_change_count"] > 0
    assert context.metadata["published_optimization"] is True
    published = OrchestratorContextStore(str(published_path)).load()
    assert published.metadata["published_from_context_path"] == context.context_path


@pytest.mark.asyncio
async def test_run_curates_failed_epoch_cases_into_replay_dataset(tmp_path: Path) -> None:
    """Epoch full evaluation failures are mined into a replay dataset artifact."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    dataset_dir = tmp_path / "existing_dataset"
    _write_config(config_path, workspace_dir, batch_size=2)
    _write_judgeable_dataset(dataset_dir)

    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator.evaluator = _ReplayMiningFakeEvaluator()
    orchestrator.evaluation_result_analyzer = _NoIssueAnalyzer()

    await orchestrator.run(
        "mine replay cases",
        dataset_dir=str(dataset_dir),
        harness_refs_path="expert_harnesses/harness_refs.yaml",
    )

    context = orchestrator.context_store.load()
    curation_refs = context.metadata["dataset_curation_refs"]
    assert len(curation_refs) == 1
    curation = curation_refs[0]
    assert curation["accepted_cases"] == 1
    replay_path = Path(curation["dataset_file"])
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    assert [case["case_id"] for case in replay["cases"]] == ["replay_failed_tb"]
    assert replay["cases"][0]["metadata"]["provenance"]["source_case_id"] == "failed_tb"
    assert Path(curation["report_path"]).is_file()


@pytest.mark.asyncio
async def test_run_records_targeted_seed_without_generating_dataset_inline(
    tmp_path: Path,
) -> None:
    """Curated failure seeds are recorded without running another dataset generation stage."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    dataset_dir = tmp_path / "existing_dataset"
    _write_config(
        config_path,
        workspace_dir,
        batch_size=2,
        dataset_model_config_ref="models/dataset.yaml",
    )
    _write_judgeable_dataset(dataset_dir)

    generated: list[dict[str, Any]] = []

    class _RecordingTargetedDatasetGenerator:
        async def generate(self, task: str, output_dir: str) -> DatasetArtifact:
            dataset_dir_path = Path(output_dir).resolve()
            dataset_dir_path.mkdir(parents=True, exist_ok=True)
            dataset_file = dataset_dir_path / "synthetic_cases.json"
            dataset_file.write_text(
                json.dumps(
                    {
                        "dataset_id": dataset_dir_path.name,
                        "task": task,
                        "cases": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            generated.append({"task": task, "output_dir": output_dir})
            return DatasetArtifact(
                dataset_id=dataset_dir_path.name,
                dataset_dir=str(dataset_dir_path),
                dataset_files=[str(dataset_file)],
            )

    generator = _RecordingTargetedDatasetGenerator()
    captured_configs = []

    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator.evaluator = _ReplayMiningFakeEvaluator()
    orchestrator.evaluation_result_analyzer = _NoIssueAnalyzer()
    orchestrator._new_dataset_generator = lambda config: (  # type: ignore[attr-defined]
        captured_configs.append(config) or generator
    )

    await orchestrator.run(
        "mine targeted replay cases",
        dataset_dir=str(dataset_dir),
        harness_refs_path="expert_harnesses/harness_refs.yaml",
    )

    assert generated == []
    assert captured_configs == []
    context = orchestrator.context_store.load()
    seed_path = Path(context.metadata["latest_targeted_dataset_seed_file"])
    assert seed_path.name == "targeted_dataset_seed.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    assert seed["recommended_synthetic_tasks"][0]["source_case_id"] == "failed_tb"
    assert "targeted_dataset_generations" not in context.metadata
    assert "latest_targeted_dataset_generation" not in context.metadata
    assert "latest_targeted_dataset_file" not in context.metadata


@pytest.mark.asyncio
async def test_run_records_batch_plan_path_from_data_loader(tmp_path: Path) -> None:
    """Orchestrator records DataLoader's batch plan ref instead of deriving it."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    dataset_dir = tmp_path / "existing_dataset"
    batch_plan_path = tmp_path / "loader-owned-plan.yaml"
    _write_config(config_path, workspace_dir, batch_size=2)
    _write_dataset(dataset_dir, ["case_001"])

    orchestrator = OptimizationOrchestrator(str(config_path))
    fake_loader = _FakeDataLoader(
        batches=[[{"case_id": "case_001", "case_path": str(dataset_dir / "cases.json"), "case_index": 1}]],
        batch_plan_path=str(batch_plan_path),
    )
    orchestrator.data_loader = fake_loader
    orchestrator.evaluator = _FakeEvaluator()
    orchestrator.member_optimizer = _FakeMemberOptimizer()

    await orchestrator.run(
        "evaluate math agent",
        dataset_dir=str(dataset_dir),
        team_skill_ref_path="team_skills/v1/team_skill_ref.yaml",
        harness_refs_path="expert_harnesses/harness_refs.yaml",
    )

    context = orchestrator.context_store.load()
    assert fake_loader.calls == [
        {"dataset_dir": str(dataset_dir.resolve()), "epoch": 1},
        {"dataset_dir": str(dataset_dir.resolve()), "epoch": 1},
    ]
    assert context.metadata["batch_plan_path"] == str(batch_plan_path)


@pytest.mark.asyncio
async def test_orchestrator_does_not_materialize_loaded_batches(tmp_path: Path) -> None:
    """Orchestrator consumes DataLoader as an iterator without keeping full batch state."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    dataset_dir = tmp_path / "existing_dataset"
    batch_plan_path = tmp_path / "loader-owned-plan.yaml"
    _write_config(config_path, workspace_dir, batch_size=2, max_epochs=2)
    _write_dataset(dataset_dir, ["case_001", "case_002"])

    orchestrator = OptimizationOrchestrator(str(config_path))
    fake_loader = _FakeDataLoader(
        batches=[
            [
                {"case_id": "case_001", "case_path": str(dataset_dir / "cases.json"), "case_index": 1},
                {"case_id": "case_002", "case_path": str(dataset_dir / "cases.json"), "case_index": 2},
            ]
        ],
        batch_plan_path=str(batch_plan_path),
    )
    orchestrator.data_loader = fake_loader
    orchestrator.evaluator = _FakeEvaluator()
    orchestrator.member_optimizer = _FakeMemberOptimizer()

    await orchestrator.run(
        "evaluate math team",
        dataset_dir=str(dataset_dir),
        team_skill_ref_path="team_skills/v1/team_skill_ref.yaml",
        harness_refs_path="expert_harnesses/harness_refs.yaml",
    )

    assert fake_loader.calls == [
        {"dataset_dir": str(dataset_dir.resolve()), "epoch": 1},
        {"dataset_dir": str(dataset_dir.resolve()), "epoch": 1},
        {"dataset_dir": str(dataset_dir.resolve()), "epoch": 2},
        {"dataset_dir": str(dataset_dir.resolve()), "epoch": 2},
    ]
    assert not hasattr(orchestrator, "current_dataset_batches")


@pytest.mark.asyncio
async def test_team_stage_member_issue_is_recorded_for_member_routing(tmp_path: Path) -> None:
    """A member issue found during Team stage is routed, not silently skipped."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    dataset_dir = tmp_path / "existing_dataset"
    _write_config(config_path, workspace_dir, batch_size=2)
    _write_dataset(dataset_dir, ["case_001", "case_002"])

    orchestrator = OptimizationOrchestrator(str(config_path))
    fake_team_optimizer = _FakeTeamSkillOptimizer()
    orchestrator.evaluator = _FakeEvaluator()
    orchestrator.evaluation_result_analyzer = _MemberOnlyAnalyzer()
    orchestrator.team_skill_optimizer = fake_team_optimizer
    orchestrator.member_optimizer = _FakeMemberOptimizer()

    await orchestrator.run(
        "evaluate math agent",
        dataset_dir=str(dataset_dir),
        team_skill_ref_path="team_skills/v1/team_skill_ref.yaml",
        harness_refs_path="expert_harnesses/harness_refs.yaml",
    )

    context = orchestrator.context_store.load()
    assert fake_team_optimizer.calls == []
    assert context.history.team_skill_optimizations == []
    routed = context.metadata["optimization_issue_routes"][0]
    assert routed["source_stage"] == "team_skill_stage"
    assert routed["target_scope"] == "member_harness"
    assert routed["status"] == "deferred"
    assert routed["route"] == "member_optimizer"


@pytest.mark.asyncio
async def test_frozen_team_issue_can_be_adapted_to_restricted_member_skill(tmp_path: Path) -> None:
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    dataset_dir = tmp_path / "existing_dataset"
    _write_config(config_path, workspace_dir, batch_size=2)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["team_skill_optimizer"] = {"freeze": True}
    config["member_optimizer"] = {"adapt_frozen_team_issues": True}
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    _write_dataset(dataset_dir, ["case_001", "case_002"])

    orchestrator = OptimizationOrchestrator(str(config_path))
    fake_member_optimizer = _FakeMemberOptimizer()
    orchestrator.evaluator = _FakeEvaluator()
    orchestrator.evaluation_result_analyzer = _AdaptableTeamOnlyAnalyzer()
    orchestrator.member_optimizer = fake_member_optimizer

    await orchestrator.run(
        "evaluate math agent",
        dataset_dir=str(dataset_dir),
        team_skill_ref_path="team_skills/v1/team_skill_ref.yaml",
        harness_refs_path="expert_harnesses/harness_refs.yaml",
    )

    assert len(fake_member_optimizer.calls) == 1
    adapted_path = Path(fake_member_optimizer.calls[0]["analysis_result_path"])
    adapted = yaml.safe_load(adapted_path.read_text(encoding="utf-8"))
    issue = adapted["issues"][0]
    assert issue["optimization_target"] == "member_harness"
    assert issue["target_members"] == ["builder"]
    assert issue["target_ref"] == "member_harness.builder.skill"
    assert issue["metadata"]["restricted_scope_adaptation"]["source_scope"] == "team_skill"
    context = orchestrator.context_store.load()
    assert any(
        route["status"] == "adapted"
        and route["reason"] == "team_skill_issue_adapted_because_team_optimizer_unavailable"
        for route in context.metadata["optimization_issue_routes"]
    )


@pytest.mark.asyncio
async def test_member_stage_without_member_issue_skips_member_optimizer(tmp_path: Path) -> None:
    """Member optimizer runs only when current analysis identifies a member harness issue."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    dataset_dir = tmp_path / "existing_dataset"
    _write_config(config_path, workspace_dir, batch_size=2)
    _write_dataset(dataset_dir, ["case_001", "case_002"])

    orchestrator = OptimizationOrchestrator(str(config_path))
    fake_member_optimizer = _FakeMemberOptimizer()
    orchestrator.evaluator = _FakeEvaluator()
    orchestrator.evaluation_result_analyzer = _TeamOnlyAnalyzer()
    orchestrator.team_skill_optimizer = _FakeTeamSkillOptimizer()
    orchestrator.member_optimizer = fake_member_optimizer

    await orchestrator.run(
        "evaluate math agent",
        dataset_dir=str(dataset_dir),
        team_skill_ref_path="team_skills/v1/team_skill_ref.yaml",
        harness_refs_path="expert_harnesses/harness_refs.yaml",
    )

    context = orchestrator.context_store.load()
    assert fake_member_optimizer.calls == []
    assert context.history.member_optimizations == []


def test_eval_ref_with_error_case_is_inconclusive(tmp_path: Path) -> None:
    result_path = tmp_path / "case" / "result.json"
    result_path.parent.mkdir()
    result_path.write_text(
        json.dumps(
            {
                "status": "error",
                "score": 0.0,
                "evaluation": {
                    "method": "error",
                    "passed": False,
                    "reason": "judge timeout",
                },
            }
        ),
        encoding="utf-8",
    )
    eval_ref_path = tmp_path / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "case_id": "case_001",
                        "result_path": str(result_path),
                        "status": "error",
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    assert _eval_ref_has_inconclusive_cases(str(eval_ref_path))


def test_select_member_input_combines_multiple_member_analyses(tmp_path: Path) -> None:
    """Team-stage deferred member issues and member-stage issues are one optimizer input."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))

    eval_paths: list[str] = []
    for stage, issue_id in [
        ("team_skill_optimization", "content_curator_schema"),
        ("member_optimization", "web_designer_validation"),
    ]:
        eval_dir = tmp_path / "evaluations" / stage / "evaluation"
        eval_dir.mkdir(parents=True)
        eval_ref_path = eval_dir / f"{issue_id}_eval_ref.yaml"
        eval_ref_path.write_text(
            yaml.safe_dump({"eval_id": issue_id}, allow_unicode=True),
            encoding="utf-8",
        )
        analysis_path = eval_dir.parent / "a" / f"{issue_id}_analysis.yaml"
        analysis_path.parent.mkdir(parents=True)
        analysis_path.write_text(
            yaml.safe_dump(
                {
                    "analysis_id": issue_id,
                    "issues": [
                        {
                            "issue_id": issue_id,
                            "optimization_target": "member_harness",
                            "category": "member_harness",
                        }
                    ],
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        orchestrator.analysis_ref_by_eval_ref_path[str(eval_ref_path)] = str(analysis_path)
        eval_paths.append(str(eval_ref_path))

    eval_ref_path, analysis_result_path = orchestrator._select_member_optimization_input(eval_paths)

    assert eval_ref_path == eval_paths[-1]
    merged = yaml.safe_load(Path(analysis_result_path).read_text(encoding="utf-8"))
    assert [issue["issue_id"] for issue in merged["issues"]] == [
        "content_curator_schema",
        "web_designer_validation",
    ]
    assert merged["source_analysis_ref_paths"] == [
        orchestrator.analysis_ref_by_eval_ref_path[eval_paths[0]],
        orchestrator.analysis_ref_by_eval_ref_path[eval_paths[1]],
    ]


@pytest.mark.asyncio
async def test_team_stage_member_issue_runs_member_optimizer(tmp_path: Path) -> None:
    """A member issue found before Member stage still reaches MemberOptimizer."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    dataset_dir = tmp_path / "existing_dataset"
    _write_config(config_path, workspace_dir, batch_size=2)
    _write_dataset(dataset_dir, ["case_001", "case_002"])

    orchestrator = OptimizationOrchestrator(str(config_path))
    fake_member_optimizer = _FakeMemberOptimizer()
    orchestrator.evaluator = _FakeEvaluator()
    orchestrator.evaluation_result_analyzer = _SequencedAnalyzer(["member_harness", ""])
    orchestrator.team_skill_optimizer = _FakeTeamSkillOptimizer()
    orchestrator.member_optimizer = fake_member_optimizer

    await orchestrator.run(
        "evaluate math agent",
        dataset_dir=str(dataset_dir),
        team_skill_ref_path="team_skills/v1/team_skill_ref.yaml",
        harness_refs_path="expert_harnesses/harness_refs.yaml",
    )

    assert fake_member_optimizer.calls
    assert "ts" in Path(fake_member_optimizer.calls[0]["eval_ref_path"]).parts
    context = orchestrator.context_store.load()
    assert any(
        route["target_scope"] == "member_harness"
        and route["route"] == "member_optimizer"
        and route["status"] == "handled"
        for route in context.metadata["optimization_issue_routes"]
    )


@pytest.mark.asyncio
async def test_member_stage_team_issue_does_not_rerun_team_skill_optimizer(tmp_path: Path) -> None:
    """Member stage stays scoped to member harness analysis after the Team Skill pass."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    dataset_dir = tmp_path / "existing_dataset"
    _write_config(config_path, workspace_dir, batch_size=2)
    _write_dataset(dataset_dir, ["case_001", "case_002"])

    orchestrator = OptimizationOrchestrator(str(config_path))
    fake_team_optimizer = _FakeTeamSkillOptimizer()
    orchestrator.evaluator = _FakeEvaluator()
    orchestrator.evaluation_result_analyzer = _SequencedAnalyzer(["team_skill", "team_skill"])
    orchestrator.team_skill_optimizer = fake_team_optimizer
    orchestrator.member_optimizer = _FakeMemberOptimizer()

    await orchestrator.run(
        "evaluate math agent",
        dataset_dir=str(dataset_dir),
        team_skill_ref_path="team_skills/v1/team_skill_ref.yaml",
        harness_refs_path="expert_harnesses/harness_refs.yaml",
    )

    assert len(fake_team_optimizer.calls) == 1
    assert "ts" in Path(fake_team_optimizer.calls[0]["eval_ref_path"]).parts
    context = orchestrator.context_store.load()
    assert context.metadata["member_optimizer_skips"][-1]["reason"] == (
        "analysis_did_not_identify_member_harness_issue"
    )


@pytest.mark.asyncio
async def test_run_skips_dataset_generation_when_seed_task_passes(tmp_path: Path) -> None:
    """A perfect seed delivery ends the run before synthetic dataset generation."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    team_skill_dir = tmp_path / "team_skill"
    _write_team_skill_spec(team_skill_dir, team_name="seed-pass-team")
    _write_config(
        config_path,
        workspace_dir,
        team_spec_config_ref=str(team_skill_dir / DEFAULT_TEAM_SPEC_FILENAME),
        full_evaluation_enabled=False,
        seed_evaluation_enabled=True,
    )

    orchestrator = OptimizationOrchestrator(str(config_path))
    fake_evaluator = _ScoredFakeEvaluator(1.0)
    fake_dataset_generator = _FakeDatasetGenerator()
    orchestrator.evaluator = fake_evaluator
    orchestrator.dataset_generator = fake_dataset_generator
    orchestrator.evaluation_result_analyzer = _NoIssueAnalyzer()

    result_dir = await orchestrator.run(
        "build a high quality deliverable",
        team_skill_ref_path=str(team_skill_dir),
        harness_refs_path="expert_harnesses/harness_refs.yaml",
    )

    assert result_dir == str((workspace_dir / "seed-pass-team").resolve())
    assert len(fake_evaluator.calls) == 1
    assert fake_evaluator.calls[0]["cases"][0]["case_id"] == "seed_task"
    assert fake_dataset_generator.calls == []
    context = orchestrator.context_store.load()
    assert context.phase.value == "completed"
    assert context.metadata["seed_evaluation"]["status"] == "passed"
    assert context.metadata["seed_evaluation"]["dataset_generation_skipped"] is True
    assert context.current.dataset is None


def test_seed_case_scores_independent_end_to_end_quality_contract() -> None:
    """Seed evaluation should judge real task quality, not only completion claims."""
    seed_case = _seed_case_from_task(
        "build a browser card game",
        pass_threshold=0.8,
    )

    behaviors = seed_case["reference"]["required_behaviors"]
    behavior_ids = {item["id"] for item in behaviors}

    assert {
        "user_goal_fulfillment",
        "core_task_semantics",
        "user_experience_quality",
        "validation_depth",
    }.issubset(behavior_ids)


@pytest.mark.asyncio
async def test_run_generates_dataset_when_seed_task_passes_but_has_quality_gaps(tmp_path: Path) -> None:
    """A passed but imperfect seed delivery still drives targeted dataset generation."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    team_skill_dir = tmp_path / "team_skill"
    _write_team_skill_spec(team_skill_dir, team_name="seed-gap-team")
    _write_config(
        config_path,
        workspace_dir,
        batch_size=2,
        team_spec_config_ref=str(team_skill_dir / DEFAULT_TEAM_SPEC_FILENAME),
        full_evaluation_enabled=False,
        seed_evaluation_enabled=True,
    )

    orchestrator = OptimizationOrchestrator(str(config_path))
    fake_evaluator = _QueuedScoredFakeEvaluator([0.95, 1.0, 1.0])
    fake_dataset_generator = _FakeDatasetGenerator()
    orchestrator.evaluator = fake_evaluator
    orchestrator.dataset_generator = fake_dataset_generator
    orchestrator.evaluation_result_analyzer = _NoIssueAnalyzer()
    orchestrator.team_skill_optimizer = _FakeTeamSkillOptimizer()
    orchestrator.member_optimizer = _FakeMemberOptimizer()

    loaded_dir = await orchestrator.run(
        "build a high quality deliverable",
        team_skill_ref_path=str(team_skill_dir),
        harness_refs_path="expert_harnesses/harness_refs.yaml",
    )

    expected_dataset_dir = workspace_dir / "seed-gap-team" / "datasets" / "dataset_001"
    assert loaded_dir == str(expected_dataset_dir.resolve())
    assert len(fake_dataset_generator.calls) == 1
    seed_ref = Path(fake_dataset_generator.calls[0]["known_failures_ref"])
    assert seed_ref.is_file()
    seed = json.loads(seed_ref.read_text(encoding="utf-8"))
    assert seed["seed_score"] == 0.95
    assert seed["quality_gaps"][0]["id"] == "end_to_end_quality_gap"
    context = orchestrator.context_store.load()
    assert context.metadata["seed_evaluation"]["status"] == "passed"
    assert context.metadata["seed_evaluation"]["dataset_generation_skipped"] is False
    assert context.metadata["seed_evaluation"]["targeted_dataset_seed_file"] == str(seed_ref)


@pytest.mark.asyncio
async def test_run_uses_failed_seed_gaps_to_drive_dataset_generation(tmp_path: Path) -> None:
    """A failed seed delivery writes a targeted seed consumed by DatasetGenerator."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    team_skill_dir = tmp_path / "team_skill"
    _write_team_skill_spec(team_skill_dir, team_name="seed-fail-team")
    _write_config(
        config_path,
        workspace_dir,
        batch_size=2,
        team_spec_config_ref=str(team_skill_dir / DEFAULT_TEAM_SPEC_FILENAME),
        full_evaluation_enabled=False,
        seed_evaluation_enabled=True,
    )

    orchestrator = OptimizationOrchestrator(str(config_path))
    fake_evaluator = _QueuedScoredFakeEvaluator([0.35, 1.0, 1.0])
    fake_dataset_generator = _FakeDatasetGenerator()
    orchestrator.evaluator = fake_evaluator
    orchestrator.dataset_generator = fake_dataset_generator
    orchestrator.evaluation_result_analyzer = _NoIssueAnalyzer()
    orchestrator.team_skill_optimizer = _FakeTeamSkillOptimizer()
    orchestrator.member_optimizer = _FakeMemberOptimizer()
    progress_events = []

    loaded_dir = await orchestrator.run(
        "build a high quality deliverable",
        team_skill_ref_path=str(team_skill_dir),
        harness_refs_path="expert_harnesses/harness_refs.yaml",
        progress_callback=progress_events.append,
    )

    expected_dataset_dir = workspace_dir / "seed-fail-team" / "datasets" / "dataset_001"
    assert loaded_dir == str(expected_dataset_dir.resolve())
    assert len(fake_evaluator.calls) >= 2
    assert fake_evaluator.calls[0]["cases"][0]["case_id"] == "seed_task"
    assert len(fake_dataset_generator.calls) == 1
    seed_ref = Path(fake_dataset_generator.calls[0]["known_failures_ref"])
    assert seed_ref.is_file()
    seed = json.loads(seed_ref.read_text(encoding="utf-8"))
    assert seed["source"] == "seed_evaluation"
    assert seed["seed_score"] == 0.35
    assert seed["dataset_budget"]["total_cases"] == 3
    assert seed["quality_gaps"][0]["id"] == "end_to_end_quality_gap"
    context = orchestrator.context_store.load()
    assert context.metadata["seed_evaluation"]["status"] == "failed"
    assert context.metadata["seed_evaluation"]["targeted_dataset_seed_file"] == str(seed_ref)
    seed_events = [event for event in progress_events if event.stage == "seed_evaluation"]
    assert [event.message for event in seed_events] == [
        "seed evaluation started",
        "seed evaluation completed: score=0.3500 status=failed",
    ]
    assert seed_events[-1].phase == "evaluating"
    assert seed_events[-1].score == 0.35
    assert seed_events[-1].metrics["seed_score"] == 0.35
    assert seed_events[-1].metrics["dataset_generation_skipped"] is False
    assert seed_events[-1].artifacts["targeted_dataset_seed_file"] == str(seed_ref)


@pytest.mark.asyncio
async def test_batch_reuses_evidence_when_team_skill_optimization_is_noop(tmp_path: Path) -> None:
    """A frozen or failed Team Skill optimizer must not trigger an identical rerun."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace("")
    orchestrator.context_store.save(orchestrator.context_store.create("test task"))
    fake_evaluator = _FakeEvaluator()
    fake_team_optimizer = _NoopTeamSkillOptimizer()
    fake_member_optimizer = _FakeMemberOptimizer()
    orchestrator.evaluator = fake_evaluator
    orchestrator.team_skill_optimizer = fake_team_optimizer
    orchestrator.member_optimizer = fake_member_optimizer
    orchestrator.evaluation_result_analyzer = _SequencedAnalyzer(["team_skill"])

    result = await orchestrator._run_batch_optimization(
        batch=[{"case_id": "case_001"}],
        batch_index=1,
        epoch=1,
        team_skill_ref_path="team_skills/current",
        harness_refs_path="harness_refs.yaml",
        dataset=DatasetArtifact(
            dataset_id="dataset",
            dataset_dir=str(tmp_path / "dataset"),
            dataset_files=[],
        ),
    )

    assert len(fake_evaluator.calls) == 1
    assert len(fake_team_optimizer.calls) == 1
    assert result.eval_ref_paths == [str((Path(fake_evaluator.calls[0]["output_dir"]) / "eval_ref.yaml").resolve())]


@pytest.mark.asyncio
async def test_run_can_stop_after_seed_when_optimization_not_confirmed(tmp_path: Path) -> None:
    """A lightweight UI confirmation can stop after the seed task before optimization."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    team_skill_dir = tmp_path / "team_skill"
    _write_team_skill_spec(team_skill_dir, team_name="seed-confirm-team")
    _write_config(
        config_path,
        workspace_dir,
        batch_size=2,
        team_spec_config_ref=str(team_skill_dir / DEFAULT_TEAM_SPEC_FILENAME),
        full_evaluation_enabled=False,
        seed_evaluation_enabled=True,
    )

    orchestrator = OptimizationOrchestrator(str(config_path))
    fake_evaluator = _QueuedScoredFakeEvaluator([0.35])
    fake_dataset_generator = _FakeDatasetGenerator()
    orchestrator.evaluator = fake_evaluator
    orchestrator.dataset_generator = fake_dataset_generator
    orchestrator.evaluation_result_analyzer = _NoIssueAnalyzer()
    progress_events = []

    result_dir = await orchestrator.run(
        "build a high quality deliverable",
        team_skill_ref_path=str(team_skill_dir),
        harness_refs_path="expert_harnesses/harness_refs.yaml",
        progress_callback=progress_events.append,
        seed_optimization_decision_callback=lambda _seed: False,
    )

    expected_workspace = workspace_dir / "seed-confirm-team"
    assert result_dir == str(expected_workspace.resolve())
    assert len(fake_evaluator.calls) == 1
    assert fake_evaluator.calls[0]["cases"][0]["case_id"] == "seed_task"
    assert fake_dataset_generator.calls == []
    context = orchestrator.context_store.load()
    assert context.phase.value == "completed"
    assert context.current.dataset is None
    assert context.metadata["seed_evaluation"]["status"] == "failed"
    assert context.metadata["seed_optimization_confirmation"] == {
        "continue": False,
        "reason": "user_declined",
    }
    confirmation_events = [event for event in progress_events if event.stage == "optimization_confirmation"]
    assert [event.message for event in confirmation_events] == [
        "seed evaluation waiting for optimization confirmation: score=0.3500 status=failed",
        "seed evaluation completed; optimization skipped by user",
    ]


@pytest.mark.asyncio
async def test_run_resume_reuses_completed_artifacts_and_continues_current_batch(
    tmp_path: Path,
) -> None:
    """Resume mode should not re-run completed seed/dataset/evaluation stages."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    team_skill_dir = tmp_path / "team_skill"
    harness_refs_path = tmp_path / "harness_refs.yaml"
    dataset_dir = workspace_dir / "resume-team--sch_original" / "datasets" / "dataset_001"
    dataset_file = dataset_dir / "synthetic_cases.json"
    _write_team_skill_spec(team_skill_dir, team_name="resume-team")
    harness_refs_path.write_text(
        yaml.safe_dump({"harness_refs": {"builder": "builder_harness"}}, allow_unicode=True),
        encoding="utf-8",
    )
    dataset_dir.mkdir(parents=True)
    dataset_file.write_text(json.dumps({"cases": [{"case_id": "c1"}, {"case_id": "c2"}]}), encoding="utf-8")
    _write_config(
        config_path,
        workspace_dir,
        batch_size=1,
        max_epochs=1,
        team_spec_config_ref=str(team_skill_dir / DEFAULT_TEAM_SPEC_FILENAME),
        full_evaluation_enabled=False,
        seed_evaluation_enabled=True,
    )

    team_workspace = workspace_dir / "resume-team--sch_original"
    seed_eval_dir = team_workspace / "evaluations" / "seed"
    seed_eval_dir.mkdir(parents=True)
    seed_summary_path = seed_eval_dir / "summary.json"
    seed_summary_path.write_text(json.dumps({"average_score": 0.5}), encoding="utf-8")
    seed_eval_ref_path = seed_eval_dir / "eval_ref.yaml"
    seed_eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "eval_id": "seed",
                "eval_dir": str(seed_eval_dir.resolve()),
                "summary_path": str(seed_summary_path.resolve()),
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    seed_file = team_workspace / "datasets" / "seed_evaluation" / "targeted_dataset_seed.json"
    seed_file.parent.mkdir(parents=True)
    seed_file.write_text(json.dumps({"gaps": []}), encoding="utf-8")
    eval_dir = team_workspace / "evaluations" / "e001" / "b002" / "ts"
    eval_dir.mkdir(parents=True)
    summary_path = eval_dir / "summary.json"
    summary_path.write_text(json.dumps({"average_score": 0.5}), encoding="utf-8")
    eval_ref_path = eval_dir / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "eval_id": "ts",
                "eval_dir": str(eval_dir.resolve()),
                "summary_path": str(summary_path.resolve()),
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    context_store = OrchestratorContextStore(str(team_workspace / "orchestrator_context.yaml"))
    context = context_store.create("task")
    context_store.save(
        replace(
            context,
            current=replace(
                context.current,
                dataset=DatasetArtifact(
                    dataset_id="dataset_001",
                    dataset_dir=str(dataset_dir.resolve()),
                    dataset_files=[str(dataset_file.resolve())],
                ),
                team_skill_ref_path=str(team_skill_dir.resolve()),
                harness_refs_path=str(harness_refs_path.resolve()),
                eval_ref_path=str(eval_ref_path.resolve()),
            ),
            metadata={
                **context.metadata,
                "seed_evaluation": {
                    "status": "failed",
                    "score": 0.5,
                    "dataset_generation_skipped": False,
                    "eval_ref_path": str(seed_eval_ref_path.resolve()),
                    "targeted_dataset_seed_file": str(seed_file.resolve()),
                },
            },
        )
    )

    orchestrator = OptimizationOrchestrator(str(config_path))
    fake_evaluator = _FakeEvaluator()
    fake_dataset_generator = _FakeDatasetGenerator()
    fake_analyzer = _NoIssueAnalyzer()
    orchestrator.evaluator = fake_evaluator
    orchestrator.dataset_generator = fake_dataset_generator
    orchestrator.evaluation_result_analyzer = fake_analyzer
    orchestrator.team_skill_optimizer = _FakeTeamSkillOptimizer()
    orchestrator.member_optimizer = _FakeMemberOptimizer()
    orchestrator.data_loader = _FakeDataLoader(
        [[{"case_id": "c1"}], [{"case_id": "c2"}]],
        str(dataset_dir / "batch_plan.yaml"),
    )

    loaded_dir = await orchestrator.run("task", resume=True)

    assert loaded_dir == str(dataset_dir.resolve())
    assert orchestrator.workspace_paths.root == team_workspace.resolve()
    assert fake_evaluator.calls == []
    assert fake_dataset_generator.calls == []
    analysis_ref = yaml.safe_load((eval_dir / "eval_ref.yaml").read_text(encoding="utf-8")).get("analysis_ref_path")
    assert analysis_ref
    assert Path(analysis_ref).is_file()


def test_resume_completed_batch_uses_terminal_checkpoint(tmp_path: Path) -> None:
    """A completed batch is an idempotency boundary for Resume."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir, full_evaluation_enabled=False)
    orchestrator = OptimizationOrchestrator(str(config_path))
    context_path = tmp_path / "run" / "orchestrator_context.yaml"
    orchestrator.context_store = OrchestratorContextStore(str(context_path))
    orchestrator.context_store.save(orchestrator.context_store.create("task"))
    orchestrator.resume_enabled = True

    eval_dir = tmp_path / "evaluation"
    eval_dir.mkdir()
    summary_path = eval_dir / "summary.json"
    summary_path.write_text(json.dumps({"average_score": 0.8}), encoding="utf-8")
    eval_ref_path = eval_dir / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "eval_id": "batch-1",
                "eval_dir": str(eval_dir),
                "summary_path": str(summary_path),
            }
        ),
        encoding="utf-8",
    )
    expected = BatchOptimizationResult(
        team_skill_ref_path="team-skill",
        harness_refs_path="candidate-harness-refs",
        eval_ref_paths=[str(eval_ref_path)],
    )

    orchestrator._save_completed_batch_context(epoch=1, batch_index=1, result=expected)
    resumed = orchestrator._resume_completed_batch(epoch=1, batch_index=1)

    assert resumed == expected


def test_seed_feedback_records_team_lifecycle_timeout_as_runtime_blocker(
    tmp_path: Path,
) -> None:
    """Team lifecycle timeouts are code-flow blockers, not dataset targets."""
    case_dir = tmp_path / "cases" / "c001"
    case_dir.mkdir(parents=True)
    result_path = case_dir / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "case_id": "seed_task",
                "status": "error",
                "execution_status": "failed",
                "score": 0.0,
                "evaluation": {
                    "method": "llm_as_judge",
                    "passed": False,
                    "reason": "team lifecycle did not finish within the evaluation case timeout",
                    "metadata": {},
                },
                "error": "team lifecycle did not finish within the evaluation case timeout",
                "artifacts": {
                    "harvested": [],
                    "missing": [],
                },
                "metadata": {
                    "execution": {
                        "failure_type": "team_lifecycle_timeout",
                        "expected_team_lifecycle": [
                            "build_team",
                            "create_task",
                            "send_message",
                            "shutdown_member",
                            "clean_team",
                        ],
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    eval_ref_path = tmp_path / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "eval_id": "seed",
                "cases": [
                    {
                        "case_id": "seed_task",
                        "result_path": str(result_path),
                        "status": "error",
                        "score": 0.0,
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    feedback = _seed_feedback_from_eval(str(eval_ref_path), max_cases=10)

    assert all(gap["id"] != "seed_team_lifecycle_gap" for gap in feedback["quality_gaps"])
    assert feedback["runtime_blockers"][0]["id"] == "seed_team_lifecycle_timeout"
    assert feedback["runtime_blockers"][0]["resolution_owner"] == "code_flow"
    assert feedback["dataset_budget"]["total_cases"] == 3
    assert feedback["dataset_budget"]["case_groups"][0]["source_gap"] != ("seed_team_lifecycle_gap")


def test_seed_feedback_keeps_artifact_quality_gap_when_lifecycle_times_out(
    tmp_path: Path,
) -> None:
    """Harvested artifacts still become quality training signal after lifecycle timeout."""
    case_dir = tmp_path / "cases" / "c001"
    case_dir.mkdir(parents=True)
    result_path = case_dir / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "case_id": "seed_task",
                "status": "error",
                "execution_status": "failed",
                "score": 0.0,
                "evaluation": {
                    "method": "llm_as_judge",
                    "passed": False,
                    "reason": "team lifecycle timed out after artifacts were produced",
                    "metadata": {},
                },
                "error": "team lifecycle did not finish within the evaluation case timeout",
                "artifacts": {
                    "harvested": ["index.html", "styles.css", "game.js"],
                    "missing": [],
                },
                "metadata": {
                    "execution": {
                        "failure_type": "team_lifecycle_timeout",
                        "expected_team_lifecycle": [
                            "build_team",
                            "create_task",
                            "send_message",
                            "shutdown_member",
                            "clean_team",
                        ],
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    eval_ref_path = tmp_path / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "eval_id": "seed",
                "cases": [
                    {
                        "case_id": "seed_task",
                        "result_path": str(result_path),
                        "status": "error",
                        "score": 0.0,
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    feedback = _seed_feedback_from_eval(str(eval_ref_path), max_cases=10)

    gap_ids = {gap["id"] for gap in feedback["quality_gaps"]}
    assert "seed_artifact_quality_gap" in gap_ids
    assert feedback["runtime_blockers"][0]["id"] == "seed_team_lifecycle_timeout"
    artifact_gap = next(gap for gap in feedback["quality_gaps"] if gap["id"] == "seed_artifact_quality_gap")
    assert artifact_gap["dimension"] == "end_to_end_artifact_quality"
    assert {axis["name"] for axis in artifact_gap["quality_axes"]} == {
        "functional_effectiveness",
        "interaction_or_effect_quality",
        "user_visible_output_quality",
        "acceptance_contract",
    }
    assert artifact_gap["evidence"]["harvested_artifacts"] == [
        "index.html",
        "styles.css",
        "game.js",
    ]
    assert feedback["dataset_budget"]["total_cases"] == 3
    assert any(
        group["source_gap"] == "seed_artifact_quality_gap" for group in feedback["dataset_budget"]["case_groups"]
    )


def test_seed_feedback_treats_judge_parse_failure_as_runtime_blocker(
    tmp_path: Path,
) -> None:
    """Judge output parse failures should not masquerade as artifact quality gaps."""
    case_dir = tmp_path / "cases" / "c001"
    case_dir.mkdir(parents=True)
    result_path = case_dir / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "case_id": "seed_task",
                "status": "failed",
                "execution_status": "passed",
                "score": 0.0,
                "evaluation": {
                    "method": "llm_as_judge",
                    "passed": False,
                    "reason": "failed to parse llm_as_judge output",
                    "metadata": {
                        "judge_error": True,
                        "judge_error_type": "parse_failed",
                        "raw_output_truncated": True,
                    },
                },
                "artifacts": {
                    "harvested": ["index.html", "styles.css", "game.js"],
                    "missing": [],
                },
                "metadata": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    eval_ref_path = tmp_path / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "eval_id": "seed",
                "cases": [
                    {
                        "case_id": "seed_task",
                        "result_path": str(result_path),
                        "status": "failed",
                        "score": 0.0,
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    feedback = _seed_feedback_from_eval(str(eval_ref_path), max_cases=10, include_default_gap=False)

    assert feedback["quality_gaps"] == []
    assert feedback["dataset_budget"] == {}
    assert feedback["runtime_blockers"][0]["id"] == "seed_judge_parse_failed"
    assert feedback["runtime_blockers"][0]["resolution_owner"] == "judge_pipeline"


def test_seed_feedback_routes_verification_gap_to_runtime_blocker(
    tmp_path: Path,
) -> None:
    """Evidence gaps should not become member training-data targets."""
    case_dir = tmp_path / "cases" / "c001"
    case_dir.mkdir(parents=True)
    result_path = case_dir / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "case_id": "seed_task",
                "status": "passed",
                "score": 0.2,
                "evaluation": {
                    "method": "llm_as_judge",
                    "passed": False,
                    "metadata": {
                        "parsed": {
                            "quality_gaps": [
                                {
                                    "id": "runtime_validation_gap",
                                    "gap_type": "verification_gap",
                                    "dimension": "deterministic runtime validation",
                                    "severity": "high",
                                    "affected_roles": ["executor"],
                                    "likely_surfaces": ["skill"],
                                    "evidence": "runtime error was not detected before delivery",
                                    "data_needed_to_fix": (
                                        "Add a repeatable runtime check that executes "
                                        "the artifact and catches console errors."
                                    ),
                                }
                            ],
                            "dataset_budget": {
                                "total_cases": 1,
                                "case_groups": [
                                    {
                                        "source_gap": "runtime_validation_gap",
                                        "case_count": 1,
                                        "target_roles": ["executor"],
                                        "target_surfaces": ["skill"],
                                    }
                                ],
                            },
                        }
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    eval_ref_path = tmp_path / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "eval_id": "seed",
                "cases": [
                    {
                        "case_id": "seed_task",
                        "result_path": str(result_path),
                        "status": "passed",
                        "score": 0.2,
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    feedback = _seed_feedback_from_eval(str(eval_ref_path), max_cases=10)

    assert feedback["quality_gaps"] == []
    assert feedback["dataset_budget"] == {}
    assert feedback["runtime_blockers"][0]["id"] == "seed_verification_gap_runtime_validation_gap"
    assert feedback["runtime_blockers"][0]["resolution_owner"] == "evaluation_pipeline"


def test_seed_feedback_filters_verification_gap_from_dataset_budget(
    tmp_path: Path,
) -> None:
    """Only artifact quality gaps should drive seed dataset generation."""
    case_dir = tmp_path / "cases" / "c001"
    case_dir.mkdir(parents=True)
    result_path = case_dir / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "case_id": "seed_task",
                "status": "passed",
                "score": 0.4,
                "evaluation": {
                    "method": "llm_as_judge",
                    "passed": False,
                    "metadata": {
                        "parsed": {
                            "quality_gaps": [
                                {
                                    "id": "runtime_validation_gap",
                                    "gap_type": "verification_gap",
                                    "dimension": "runtime evidence",
                                    "severity": "high",
                                    "affected_roles": ["qa-tester"],
                                    "likely_surfaces": ["tool"],
                                },
                                {
                                    "id": "interaction_quality_gap",
                                    "gap_type": "artifact_quality_gap",
                                    "dimension": "interaction quality",
                                    "severity": "high",
                                    "affected_roles": ["frontend-engineer"],
                                    "likely_surfaces": ["skill"],
                                },
                            ],
                            "dataset_budget": {
                                "total_cases": 4,
                                "case_groups": [
                                    {
                                        "source_gap": "runtime_validation_gap",
                                        "case_count": 2,
                                        "target_roles": ["qa-tester"],
                                        "target_surfaces": ["tool"],
                                    },
                                    {
                                        "source_gap": "interaction_quality_gap",
                                        "case_count": 2,
                                        "target_roles": ["frontend-engineer"],
                                        "target_surfaces": ["skill"],
                                    },
                                ],
                            },
                        }
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    eval_ref_path = tmp_path / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "eval_id": "seed",
                "cases": [
                    {
                        "case_id": "seed_task",
                        "result_path": str(result_path),
                        "status": "passed",
                        "score": 0.4,
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    feedback = _seed_feedback_from_eval(str(eval_ref_path), max_cases=10)

    assert [gap["id"] for gap in feedback["quality_gaps"]] == ["interaction_quality_gap"]
    assert [group["source_gap"] for group in feedback["dataset_budget"]["case_groups"]] == ["interaction_quality_gap"]
    assert feedback["dataset_budget"]["total_cases"] == 2
    assert feedback["runtime_blockers"][0]["id"] == "seed_verification_gap_runtime_validation_gap"


@pytest.mark.asyncio
async def test_run_generates_dataset_when_dataset_dir_is_missing(tmp_path: Path) -> None:
    """run calls DatasetGenerator then DataLoader when dataset_dir is omitted."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    team_skill_dir = tmp_path / "team_skill"
    _write_team_skill_spec(team_skill_dir, team_name="math-optimizer")
    _write_config(
        config_path,
        workspace_dir,
        batch_size=2,
        team_spec_config_ref=str(team_skill_dir / DEFAULT_TEAM_SPEC_FILENAME),
    )

    orchestrator = OptimizationOrchestrator(str(config_path))
    fake_evaluator = _FakeEvaluator()
    fake_member_optimizer = _FakeMemberOptimizer()
    orchestrator.evaluator = fake_evaluator
    orchestrator.evaluation_result_analyzer = _TeamAndMemberAnalyzer()
    orchestrator.team_skill_optimizer = _FakeTeamSkillOptimizer()
    orchestrator.member_optimizer = fake_member_optimizer
    orchestrator.dataset_generator = _FakeDatasetGenerator()
    loaded_dir = await orchestrator.run(
        "evaluate math agent",
        team_skill_ref_path=str(team_skill_dir),
        harness_refs_path="expert_harnesses/harness_refs.yaml",
    )

    expected_dataset_dir = workspace_dir / "math-optimizer" / "datasets" / "dataset_001"
    assert loaded_dir == str(expected_dataset_dir.resolve())
    assert orchestrator.workspace_paths.team_name == "math-optimizer"
    assert orchestrator.workspace_paths.root == (workspace_dir / "math-optimizer").resolve()
    assert (workspace_dir / "math-optimizer" / "datasets").is_dir()
    assert (workspace_dir / "math-optimizer" / "evaluations").is_dir()
    assert (workspace_dir / "math-optimizer" / "team_skills").is_dir()
    assert (workspace_dir / "math-optimizer" / "member_optimizations").is_dir()
    assert not (workspace_dir / "math-optimizer" / "analysis").exists()
    assert (workspace_dir / "math-optimizer" / "checkpoints").is_dir()
    assert (workspace_dir / "optimization_experiences").is_dir()
    assert not (workspace_dir / "math-optimizer" / "optimization_experiences").exists()
    assert orchestrator.current_dataset_artifact is not None
    assert orchestrator.current_dataset_artifact.dataset_dir == str(expected_dataset_dir.resolve())
    assert (expected_dataset_dir / "synthetic_cases.json").is_file()
    assert not hasattr(orchestrator, "current_dataset_batches")
    context = orchestrator.context_store.load()
    assert context.current.dataset is not None
    assert context.current.dataset.dataset_dir == str(expected_dataset_dir.resolve())
    assert context.current.dataset.dataset_files == [str((expected_dataset_dir / "synthetic_cases.json").resolve())]
    assert context.current.team_skill_ref_path == orchestrator.optimized_team_skill_ref_path
    assert Path(orchestrator.optimized_team_skill_ref_path).parent == workspace_dir / "math-optimizer" / "team_skills"
    assert context.current.harness_refs_path == orchestrator.optimized_harness_refs_path
    assert Path(orchestrator.optimized_harness_refs_path).parent.name == "member_optimization_001"
    assert context.current.harness_refs["team_leader"] == orchestrator.optimized_harness_refs_path
    assert context.current.eval_ref_path == orchestrator.optimized_eval_ref_paths[-1]
    assert len(orchestrator.experience_ref_paths) == 2
    assert len(fake_evaluator.calls) == 3
    assert len(fake_member_optimizer.calls) == 1
    assert context.metadata["latest_checkpoint_epoch"] == 1
    assert Path(context.metadata["best_checkpoint_path"], "orchestrator_context.yaml").is_file()
    raw_context = yaml.safe_load(Path(context.context_path).read_text(encoding="utf-8"))
    assert "current_dataset_batches" not in raw_context
    assert "case_results" not in raw_context
    assert raw_context["current"]["dataset"] == {
        "dataset_id": "dataset_001",
        "dataset_dir": str(expected_dataset_dir.resolve()),
        "dataset_files": [str((expected_dataset_dir / "synthetic_cases.json").resolve())],
    }
    assert orchestrator.list_checkpoints() == ["epoch_001"]
    loaded_context_path = await orchestrator.load_checkpoint("epoch_001")
    assert loaded_context_path == orchestrator.context_store.context_path


@pytest.mark.asyncio
async def test_run_bootstraps_member_harness_refs_from_generated_team_skill(
    tmp_path: Path,
) -> None:
    """A generated Team Skill without input harness refs materializes role harnesses."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    dataset_dir = tmp_path / "existing_dataset"
    _write_config(config_path, workspace_dir, batch_size=2)
    _write_dataset(dataset_dir, ["case_001", "case_002"])

    orchestrator = OptimizationOrchestrator(str(config_path))
    fake_evaluator = _FakeEvaluator()
    orchestrator.team_skill_generator = _FakeTeamSkillGenerator()
    orchestrator.evaluator = fake_evaluator
    orchestrator.evaluation_result_analyzer = _NoIssueAnalyzer()
    orchestrator.member_optimizer = _FakeMemberOptimizer()

    await orchestrator.run(
        "create a responsive product landing page",
        dataset_dir=str(dataset_dir),
    )

    context = orchestrator.context_store.load()
    refs_path = Path(context.current.harness_refs_path)
    assert refs_path.is_file()
    refs = yaml.safe_load(refs_path.read_text(encoding="utf-8"))
    assert refs["harness_refs"].keys() == {"planner", "builder"}
    assert [role["member_name"] for role in refs["roles"]] == [
        "planner",
        "builder",
    ]
    for role_name, harness_path in refs["harness_refs"].items():
        harness_dir = Path(harness_path)
        assert harness_dir.is_dir()
        assert (harness_dir / "harness_config.yaml").is_file()
        assert (harness_dir / "identity.md").is_file()
        assert (harness_dir / "soul.md").is_file()
        assert (harness_dir / "prompt_sections" / "sections.yaml").is_file()
        assert role_name in (harness_dir / "identity.md").read_text(encoding="utf-8")
    assert fake_evaluator.calls
    assert all(call["harness_refs_path"] == str(refs_path) for call in fake_evaluator.calls)
    assert context.metadata["initial_harness_refs_path"] == str(refs_path)
    assert context.current.harness_refs == refs["harness_refs"]


def test_initial_member_harness_bootstrap_uses_short_workspace_paths(tmp_path: Path) -> None:
    """Initial harness bootstrap must avoid deep Windows MAX_PATH-sensitive paths."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / ("webpage_single_harness_" + "x" * 32) / "workspace"
    _write_config(config_path, workspace_dir)
    team_skill_dir = tmp_path / "team_skill"
    _write_team_skill_spec(
        team_skill_dir,
        team_name="energy-storage-investor-landing-page-swarm",
        roles=[
            {
                "id": "investor-readiness-reviewer",
                "kind": "ai_agent",
                "purpose": "Review investor readiness and risk disclosure.",
            }
        ],
    )

    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator._configure_team_workspace(str(team_skill_dir))
    refs_path = orchestrator._ensure_initial_member_harness_refs(
        team_skill_ref_path=str(team_skill_dir),
        harness_refs_path="",
    )

    refs = yaml.safe_load(Path(refs_path).read_text(encoding="utf-8"))
    harness_path = Path(refs["harness_refs"]["investor-readiness-reviewer"])
    playbook_path = harness_path / "prompt_sections" / "files" / "role.md"
    assert playbook_path.is_file()
    assert harness_path.parent == Path(refs_path).parent
    assert "member_optimizations" not in playbook_path.parts
    assert len(str(playbook_path)) < 260


@pytest.mark.asyncio
async def test_run_allocates_next_dataset_dir_when_generated_dir_exists(tmp_path: Path) -> None:
    """Generated datasets do not overwrite an existing dataset directory."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    existing_dataset_dir = workspace_dir / "default_team" / "datasets" / "dataset_001"
    existing_dataset_dir.mkdir(parents=True)
    _write_config(config_path, workspace_dir)

    orchestrator = OptimizationOrchestrator(str(config_path))
    orchestrator.evaluator = _FakeEvaluator()
    orchestrator.member_optimizer = _FakeMemberOptimizer()
    orchestrator.dataset_generator = _FakeDatasetGenerator()
    loaded_dir = await orchestrator.run("evaluate math agent")

    assert loaded_dir == str((workspace_dir / "default_team" / "datasets" / "dataset_002").resolve())


@pytest.mark.asyncio
async def test_run_repeats_optimization_until_max_epochs(tmp_path: Path) -> None:
    """run optimizes all batches once per epoch until max_epochs is reached."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    dataset_dir = tmp_path / "existing_dataset"
    _write_config(config_path, workspace_dir, batch_size=10, max_epochs=2)
    _write_dataset(dataset_dir, ["case_001", "case_002"])

    orchestrator = OptimizationOrchestrator(str(config_path))
    fake_evaluator = _FakeEvaluator()
    fake_member_optimizer = _FakeMemberOptimizer()
    orchestrator.evaluator = fake_evaluator
    orchestrator.evaluation_result_analyzer = _TeamAndMemberAnalyzer()
    orchestrator.team_skill_optimizer = _FakeTeamSkillOptimizer()
    orchestrator.member_optimizer = fake_member_optimizer

    await orchestrator.run(
        "evaluate math agent",
        dataset_dir=str(dataset_dir),
        team_skill_ref_path="team_skills/v1/team_skill_ref.yaml",
        harness_refs_path="expert_harnesses/harness_refs.yaml",
    )

    context = orchestrator.context_store.load()
    assert context.epoch == 2
    assert len(fake_evaluator.calls) == 6
    assert len(context.history.team_skill_optimizations) == 2
    assert len(context.history.member_optimizations) == 2
    assert len(orchestrator.experience_ref_paths) == 4
    assert len(fake_member_optimizer.calls) == 2
    assert context.metadata["latest_checkpoint_epoch"] == 2
    assert context.metadata["best_checkpoint_epoch"] == 1
    team_workspace = workspace_dir / "default_team"
    assert (team_workspace / "checkpoints" / "epoch_001" / "orchestrator_context.yaml").is_file()
    assert (team_workspace / "checkpoints" / "epoch_002" / "orchestrator_context.yaml").is_file()


def test_epoch_checkpoint_snapshots_best_harness_refs(tmp_path: Path) -> None:
    """Best refs must point at immutable checkpoint copies, not mutable current refs."""
    from openjiuwen.rsi.schema import (
        CurrentArtifactRefs,
        OrchestratorRunContext,
    )

    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    _write_config(config_path, workspace_dir)
    orchestrator = OptimizationOrchestrator(str(config_path))

    current_harness = workspace_dir / "default_team" / "member_optimizations" / "current_harnesses" / "solver"
    current_harness.mkdir(parents=True)
    (current_harness / "identity.md").write_text("snapshot me\n", encoding="utf-8")
    current_refs_path = workspace_dir / "default_team" / "member_optimizations" / "current_harness_refs.yaml"
    current_refs_path.write_text(
        yaml.safe_dump(
            {"harness_refs": {"solver": str(current_harness)}},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    eval_ref_path = workspace_dir / "default_team" / "evaluations" / "epoch_001" / "eval_ref.yaml"
    eval_ref_path.parent.mkdir(parents=True)
    eval_ref_path.write_text("summary_path: summary.json\n", encoding="utf-8")

    context = OrchestratorRunContext(
        task_id="task_001",
        task="test",
        context_path=str(orchestrator.workspace_paths.context_path),
        checkpoint_dir=str(orchestrator.workspace_paths.checkpoint_dir),
        current=CurrentArtifactRefs(
            team_skill_ref_path="",
            harness_refs_path=str(current_refs_path),
            harness_refs={"solver": str(current_harness)},
            eval_ref_path=str(eval_ref_path),
        ),
    )
    orchestrator.context_store.save(context)

    checkpoint_path = Path(
        orchestrator._save_epoch_checkpoint(
            epoch=1,
            eval_ref_path=str(eval_ref_path),
            score=0.75,
        )
    )
    saved = orchestrator.context_store.load()

    assert saved.best.harness_refs_path == str(checkpoint_path / "harness_refs.yaml")
    assert saved.best.harness_refs["solver"] == str(checkpoint_path / "harnesses" / "solver")
    assert (checkpoint_path / "harness_refs.yaml").is_file()
    assert (checkpoint_path / "harnesses" / "solver" / "identity.md").read_text(
        encoding="utf-8",
    ) == "snapshot me\n"

    (current_harness / "identity.md").write_text("mutated current\n", encoding="utf-8")
    assert (checkpoint_path / "harnesses" / "solver" / "identity.md").read_text(
        encoding="utf-8",
    ) == "snapshot me\n"


@pytest.mark.asyncio
async def test_run_stops_after_epoch_when_score_reaches_target(tmp_path: Path) -> None:
    """run stops after the epoch once optimized batch scores are good enough."""
    config_path = tmp_path / "orchestrator.yaml"
    workspace_dir = tmp_path / "workspace"
    dataset_dir = tmp_path / "existing_dataset"
    _write_config(
        config_path,
        workspace_dir,
        batch_size=1,
        max_epochs=3,
        success_score=0.9,
    )
    _write_dataset(dataset_dir, ["case_001", "case_002"])

    orchestrator = OptimizationOrchestrator(str(config_path))
    fake_evaluator = _ScoredFakeEvaluator(score=0.95)
    fake_member_optimizer = _FakeMemberOptimizer()
    orchestrator.evaluator = fake_evaluator
    orchestrator.evaluation_result_analyzer = _TeamAndMemberAnalyzer()
    orchestrator.team_skill_optimizer = _FakeTeamSkillOptimizer()
    orchestrator.member_optimizer = fake_member_optimizer

    await orchestrator.run(
        "evaluate math agent",
        dataset_dir=str(dataset_dir),
        team_skill_ref_path="team_skills/v1/team_skill_ref.yaml",
        harness_refs_path="expert_harnesses/harness_refs.yaml",
    )

    context = orchestrator.context_store.load()
    assert context.epoch == 1
    assert len(fake_evaluator.calls) == 5
    assert len(context.history.team_skill_optimizations) == 2
    assert len(context.history.member_optimizations) == 2
    assert len(orchestrator.experience_ref_paths) == 4
    assert len(fake_member_optimizer.calls) == 2
    assert context.best.score == 0.95
    assert context.metadata["best_checkpoint_epoch"] == 1
    experience_index = yaml.safe_load(
        (workspace_dir / "optimization_experiences" / "index.yaml").read_text(encoding="utf-8")
    )
    assert {item["learning_status"] for item in experience_index["experiences"]} == {"accepted"}
