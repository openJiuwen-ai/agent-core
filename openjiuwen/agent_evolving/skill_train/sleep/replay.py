# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Replay mined tasks under a candidate skill/memory."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from openjiuwen.agent_evolving.skill_train.sleep.backend import Backend
from openjiuwen.agent_evolving.skill_train.sleep.types import ReplayResult, TaskRecord


@dataclass(frozen=True)
class ReplayBundle:
    skill: str
    memory: str
    sample_id: int = 0


def _required_tools(task: TaskRecord) -> List[str]:
    if task.reference_kind != "rule" or not task.judge:
        return []
    tools: List[str] = []
    checks = task.judge.get("checks") if isinstance(task.judge, dict) else None
    if not isinstance(checks, list):
        return []
    for check in checks:
        if not isinstance(check, dict):
            continue
        if check.get("op") == "tool_called" and check.get("arg"):
            tools.append(str(check.get("arg")))
    return tools


def replay_one(
    backend: Backend,
    task: TaskRecord,
    skill: str,
    memory: str,
    sample_id: int = 0,
) -> ReplayResult:
    bundle = ReplayBundle(skill=skill, memory=memory, sample_id=sample_id)
    tools = _required_tools(task)
    tools_called: List[str] = []
    started = time.time()
    tokens_before = backend.tokens_used()
    if tools:
        response, tools_called = backend.attempt_with_tools(
            task, bundle.skill, bundle.memory, tools
        )
    else:
        response = backend.attempt(
            task, bundle.skill, bundle.memory, sample_id=bundle.sample_id
        )
    latency_ms = (time.time() - started) * 1000.0
    tokens = max(0, backend.tokens_used() - tokens_before)
    if tokens == 0:
        tokens = (len(bundle.skill) + len(bundle.memory) + len(task.intent) + len(response)) // 4
    hard, soft, rationale = backend.judge(task, response)
    tag0 = task.tags[0] if task.tags else "task"
    return ReplayResult(
        id=task.id,
        hard=float(hard),
        soft=float(soft),
        response=response,
        fail_reason="" if hard >= 1.0 else (rationale or "below threshold"),
        task_type=tag0,
        judge_rationale=rationale,
        tools_called=tools_called,
        tokens=int(tokens),
        latency_ms=round(latency_ms, 1),
    )


def replay_batch(
    backend: Backend,
    tasks: Sequence[TaskRecord],
    skill: str,
    memory: str,
) -> List[Tuple[TaskRecord, ReplayResult]]:
    return [(task, replay_one(backend, task, skill, memory)) for task in tasks]


def aggregate_scores(
    pairs: Sequence[Tuple[TaskRecord, ReplayResult]],
) -> Tuple[float, float]:
    if not pairs:
        return 0.0, 0.0
    n = len(pairs)
    hard = sum(result.hard for _, result in pairs) / n
    soft = sum(result.soft for _, result in pairs) / n
    return hard, soft
