# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime contexts for auto-harness pipelines and stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openjiuwen.auto_harness.schema import (
    Experience,
    OptimizationTask,
)
from openjiuwen.core.session.stream.base import (
    OutputSchema,
)

if TYPE_CHECKING:
    from openjiuwen.auto_harness.orchestrator import (
        AutoHarnessOrchestrator,
    )


def task_key(task: OptimizationTask) -> str:
    """Return the scoped artifact key for a task."""
    return task.topic or "task"


@dataclass
class TaskRuntime:
    """Prepared task-scoped execution dependencies."""

    related: list[Experience]
    wt_path: str
    edit_safety_rail: Any
    preexisting_dirty_files: list[str]
    task_agent: Any
    commit_agent: Any


@dataclass
class BaseExecutionContext:
    """Shared execution context surface."""

    orchestrator: "AutoHarnessOrchestrator"

    @property
    def task_id(self) -> str:
        return ""

    def get_artifact(
        self,
        name: str,
        *,
        default: Any = None,
    ) -> Any:
        return self.orchestrator.artifacts.get(
            name,
            task_id=self.task_id,
            default=default,
        )

    def require_artifact(self, name: str) -> Any:
        return self.orchestrator.artifacts.require(
            name,
            task_id=self.task_id,
        )

    def put_artifact(self, name: str, value: Any) -> None:
        self.orchestrator.artifacts.put(
            name,
            value,
            task_id=self.task_id,
        )

    def put_artifacts(
        self,
        artifacts: dict[str, Any],
    ) -> None:
        self.orchestrator.artifacts.put_many(
            artifacts,
            task_id=self.task_id,
        )

    @staticmethod
    def message(text: str) -> OutputSchema:
        return OutputSchema(
            type="message",
            index=0,
            payload={"content": text},
        )


@dataclass
class SessionContext(BaseExecutionContext):
    """Runtime context passed into session pipelines and stages."""


@dataclass
class TaskContext(SessionContext):
    """Runtime context passed into task pipelines and stages."""

    task: OptimizationTask
    runtime: TaskRuntime

    @property
    def task_id(self) -> str:
        return task_key(self.task)
