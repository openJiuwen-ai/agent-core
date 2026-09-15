# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Protocol-like base class for sleep backends."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from openjiuwen.agent_evolving.skill_train.sleep.types import EditRecord, ReplayResult, TaskRecord

_TOOL_CALL_RE = re.compile(r"(?i)\btool_call\s*:\s*(?P<name>\S+)")


@dataclass(frozen=True)
class ReflectRequest:
    """Bundled inputs for a reflect call."""

    failures: Sequence[Tuple[TaskRecord, ReplayResult]]
    successes: Sequence[Tuple[TaskRecord, ReplayResult]]
    skill: str
    memory: str
    edit_budget: int
    evolve_skill: bool
    evolve_memory: bool


class Backend:
    """Protocol-like base for attempt / judge / reflect."""

    name = "base"
    preferences: str = ""

    def attempt(
        self,
        task: TaskRecord,
        skill: str,
        memory: str,
        sample_id: int = 0,
    ) -> str:
        raise NotImplementedError("Backend.attempt must be overridden")

    def attempt_with_tools(
        self,
        task: TaskRecord,
        skill: str,
        memory: str,
        tools: List[str],
    ) -> Tuple[str, List[str]]:
        reply = self.attempt(task, skill, memory)
        wanted = {tool.lower(): tool for tool in tools}
        hit: List[str] = []
        for match in _TOOL_CALL_RE.finditer(reply):
            key = match.group("name").lower()
            original = wanted.get(key)
            if original is not None and original not in hit:
                hit.append(original)
        return reply, hit

    def judge(self, task: TaskRecord, response: str) -> Tuple[float, float, str]:
        raise NotImplementedError("Backend.judge must be overridden")

    @staticmethod
    def synthesize_rubric(task: TaskRecord) -> str:
        return task.reference or ""

    def reflect(
        self,
        failures: Sequence[Tuple[TaskRecord, ReplayResult]],
        successes: Sequence[Tuple[TaskRecord, ReplayResult]],
        skill: str,
        memory: str,
        **opts: object,
    ) -> List[EditRecord]:
        """Compatibility entry; real work lives in ``reflect_request``."""
        budget = opts.get("edit_budget", 4)
        evolve_skill = opts.get("evolve_skill", True)
        evolve_memory = opts.get("evolve_memory", False)
        packed = ReflectRequest(
            failures=tuple(failures),
            successes=tuple(successes),
            skill=skill or "",
            memory=memory or "",
            edit_budget=max(0, int(budget)),  # type: ignore[arg-type]
            evolve_skill=bool(evolve_skill),
            evolve_memory=bool(evolve_memory),
        )
        return self.reflect_request(packed)

    @staticmethod
    def reflect_request(request: ReflectRequest) -> List[EditRecord]:
        del request
        raise NotImplementedError("Backend.reflect_request must be overridden")

    @staticmethod
    def tokens_used() -> int:
        return 0
