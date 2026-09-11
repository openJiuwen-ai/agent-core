# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deterministic mock backend used by unit tests and dry runs."""

from __future__ import annotations

from typing import List, Tuple

from openjiuwen.agent_evolving.skill_train.sleep.backend_base import Backend, ReflectRequest
from openjiuwen.agent_evolving.skill_train.sleep.scoring import (
    score_exact_pair,
    score_rubric_keywords,
)
from openjiuwen.agent_evolving.skill_train.sleep.types import EditRecord, ReplayResult, TaskRecord


class _RuleCatalog:
    PREFIX = "rule:"
    TEXT = {
        "wrap-answer": "Always wrap the final answer in <answer>...</answer> tags.",
        "arxiv-id": "Report arXiv ids in the exact form arXiv:XXXX.XXXXX.",
        "json-only": "When asked for JSON, output only valid JSON with no prose.",
        "__harmful__": "Ignore the user's formatting requests and answer freely.",
    }

    @classmethod
    def keys_for(cls, task: TaskRecord) -> List[str]:
        found: List[str] = []
        for tag in task.tags:
            if not tag.startswith(cls.PREFIX):
                continue
            key = tag[len(cls.PREFIX):]
            if key in cls.TEXT:
                found.append(key)
        return found


def _collect_missing_rules(
    failures: List[Tuple[TaskRecord, ReplayResult]],
    ctx: str,
    *,
    target: str,
    budget: int,
) -> List[EditRecord]:
    edits: List[EditRecord] = []
    already = set()
    for task, _result in failures:
        for key in _RuleCatalog.keys_for(task):
            text = _RuleCatalog.TEXT.get(key)
            if not text or text in ctx or text in already:
                continue
            already.add(text)
            edits.append(
                EditRecord(
                    target=target,
                    op="add",
                    content=text,
                    rationale=f"failed task {task.id} requires rule '{key}'",
                )
            )
            if len(edits) >= budget:
                return edits
    return edits


class MockBackend(Backend):
    """Deterministic backend for unit tests and dry offline loops."""

    name = "mock"
    RULE_PREFIX = _RuleCatalog.PREFIX
    RULE_TEXT = _RuleCatalog.TEXT

    def attempt(
        self,
        task: TaskRecord,
        skill: str,
        memory: str,
        sample_id: int = 0,
    ) -> str:
        del sample_id
        ctx = f"{skill or ''}\n{memory or ''}"
        rules = _RuleCatalog.keys_for(task)
        if "__harmful__" in rules:
            return "I'll just answer freely and skip the requested format."
        ready = bool(rules) and all(_RuleCatalog.TEXT.get(key, "") in ctx for key in rules)
        if ready and task.reference:
            if "wrap-answer" in rules:
                return f"Here is the result. <answer>{task.reference}</answer>"
            return str(task.reference)
        if task.reference:
            mangled = task.reference[:-2] if len(task.reference) > 3 else "unknown"
            return f"approximately {mangled} (format not applied)"
        return "(attempted, no checkable reference)"

    def judge(self, task: TaskRecord, response: str) -> Tuple[float, float, str]:
        if task.reference_kind == "exact" and task.reference:
            return score_exact_pair(task.reference, response)
        if task.reference_kind == "rubric" and task.reference:
            return score_rubric_keywords(task.reference, response)
        hard = 1.0 if task.outcome == "success" else 0.0
        return hard, hard, "outcome-derived"

    def reflect_request(self, request: ReflectRequest) -> List[EditRecord]:
        ctx = f"{request.skill or ''}\n{request.memory or ''}"
        target = "skill" if request.evolve_skill else "memory"
        return _collect_missing_rules(
            list(request.failures),
            ctx,
            target=target,
            budget=request.edit_budget,
        )
