# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Rollouter interfaces for task execution backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TaskRolloutCommandSpec:
    """One bounded task-rollout command plus metadata needed to run it."""

    name: str
    command: list[str]
    timeout_seconds: int
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class TaskRolloutCommandResult:
    """Bounded process output from one task-rollout command."""

    name: str
    command: list[str]
    exit_code: int
    stdout_tail: str
    stderr_tail: str


class TaskRolloutBackend(ABC):
    """Execute one task through a pluggable rollout backend."""

    name = ""
    aliases: tuple[str, ...] = ()

    @abstractmethod
    def build_spec(
        self,
        case: Any,
        config: Any,
        *,
        index: int = 0,
    ) -> TaskRolloutCommandSpec:
        """Return one bounded process spec for the selected task case."""

    async def run_case(
        self,
        case: Any,
        config: Any,
        *,
        index: int = 0,
    ) -> TaskRolloutCommandResult:
        """Run one case.

        Command-based backends use this default implementation. Backends that
        own a non-process runtime can override it so the core flow does not
        mistake that runtime for a local subprocess.
        """

        from ..backends.rollouter.docker_runtime import run_docker_command_spec

        return await run_docker_command_spec(self.build_spec(case, config, index=index))


__all__ = [
    "TaskRolloutBackend",
    "TaskRolloutCommandResult",
    "TaskRolloutCommandSpec",
]
