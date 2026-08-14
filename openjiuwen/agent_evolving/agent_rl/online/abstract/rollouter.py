# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Rollouter interfaces for task execution and SFT sample generation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SFTDockerCommandSpec:
    """One bounded task-rollout command plus metadata needed to run it."""

    name: str
    command: list[str]
    timeout_seconds: int
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class SFTDockerCommandResult:
    """Bounded process output from one task-rollout command."""

    name: str
    command: list[str]
    exit_code: int
    stdout_tail: str
    stderr_tail: str


class SFTTaskRolloutBackend(ABC):
    """Execute one SFT task through a pluggable rollout backend."""

    name = ""
    aliases: tuple[str, ...] = ()

    @abstractmethod
    def build_spec(
        self,
        case: Any,
        config: Any,
        *,
        index: int = 0,
    ) -> SFTDockerCommandSpec:
        """Return one bounded process spec for the selected task case."""

    async def run_case(
        self,
        case: Any,
        config: Any,
        *,
        index: int = 0,
    ) -> SFTDockerCommandResult:
        """Run one case.

        Command-based backends use this default implementation. Backends that
        own a remote runtime, such as Yuanrong, override it so the core flow
        does not mistake a remote sandbox for a local subprocess.
        """

        from ..backends.rollouter.docker_runtime import run_docker_command_spec

        return await run_docker_command_spec(self.build_spec(case, config, index=index))


@dataclass
class SFTRolloutContext:
    """Runtime dependencies and defaults shared by SFT rollouters."""

    supervisor: Any = None
    default_user_id: str = ""
    target_model_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class SFTRollouter(ABC):
    """Base class for raw-trajectory to SFT-sample conversion."""

    scenario: str = ""

    @abstractmethod
    async def rollout(
        self,
        raw_trajectories: list[dict[str, Any]],
        context: SFTRolloutContext,
    ) -> list[dict[str, Any]]:
        """Generate normalized ``sft-sample-v1`` payloads."""


__all__ = [
    "SFTDockerCommandResult",
    "SFTDockerCommandSpec",
    "SFTRolloutContext",
    "SFTRollouter",
    "SFTTaskRolloutBackend",
]
