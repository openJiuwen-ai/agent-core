# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any


@dataclass
class ExecResult:
    stdout: str = ""
    stderr: str = ""
    returncode: int = -1
    timed_out: bool = False

    @property
    def success(self) -> bool:
        return self.returncode == 0


@dataclass
class Task:
    task_id: str
    instruction: str
    metadata: dict[str, Any] = field(default_factory=dict)
    environment_spec: dict[str, Any] = field(default_factory=dict)
    has_skills: bool = False
    skills: list[str] = field(default_factory=list)


class ModelProtocol(str, Enum):
    """Wire protocol used by an Agent to reach the model gateway."""

    OPENAI_CHAT = "openai_chat"
    ANTHROPIC_MESSAGES = "anthropic_messages"


@dataclass(frozen=True, slots=True)
class AgentRuntimeBinding:
    """Immutable model endpoint and rollout identity for one Agent attempt."""

    attempt_id: str
    agent_session_id: str
    protocol: ModelProtocol
    model_base_url: str
    api_key: str = field(repr=False)
    custom_headers: Mapping[str, str] = field(default_factory=dict)
    requested_model: str = ""
    policy_version: str = ""

    def __post_init__(self) -> None:
        for field_name in (
            "attempt_id",
            "agent_session_id",
            "model_base_url",
            "api_key",
            "requested_model",
            "policy_version",
        ):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} is required")
        if not isinstance(self.protocol, ModelProtocol):
            object.__setattr__(self, "protocol", ModelProtocol(self.protocol))
        headers = {str(name).strip(): str(value) for name, value in self.custom_headers.items() if str(name).strip()}
        object.__setattr__(self, "custom_headers", MappingProxyType(headers))


@dataclass
class AgentContext:
    iteration: int = 1
    has_skill: bool = False
    previous_result: IterationResult | None = None
    evolution_suggestions: str | None = None
    evolution_files: dict[str, str] | None = None
    n_input_tokens: int = 0
    n_output_tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    runtime: AgentRuntimeBinding | None = None


@dataclass
class AgentRunResult:
    final_response: str = ""
    trajectory: list[dict] = field(default_factory=list)
    execution_time: float = 0.0
    tokens_used: int = 0
    raw_output: str = ""
    stderr: str = ""
    evolution_events: list[dict] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    llm_logs: dict[str, str] | None = None


@dataclass
class EvalResult:
    passed: bool = False
    pass_rate: float = 0.0
    test_output: str = ""
    returncode: int = -1
    failed_tests: list[str] = field(default_factory=list)
    test_details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillDelta:
    skills: dict[str, str] = field(default_factory=dict)
    evolutions: dict[str, str] = field(default_factory=dict)
    evolution_files: dict[str, dict[str, str]] = field(default_factory=dict)
    changed: bool = False


@dataclass
class IterationResult:
    iteration: int
    agent_result: AgentRunResult
    eval_result: EvalResult
    skill_delta: SkillDelta
    skill_changed: bool = False
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime = field(default_factory=datetime.now)


@dataclass
class PipelineResult:
    task_id: str
    agent_name: str
    benchmark_name: str
    total_iterations: int
    convergence_achieved: bool
    convergence_type: str = ""
    results: list[IterationResult] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    output_dir: Path = Path("./evolution_results")
    report_path: Path | None = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_name": self.agent_name,
            "benchmark_name": self.benchmark_name,
            "total_iterations": self.total_iterations,
            "convergence_achieved": self.convergence_achieved,
            "convergence_type": self.convergence_type,
            "metrics": self.metrics,
            "output_dir": str(self.output_dir),
            "timestamp": self.timestamp,
        }
