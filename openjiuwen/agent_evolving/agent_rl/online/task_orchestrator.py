# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Deep orchestration Module for one external-Agent online-RL task attempt."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.agent_evolving.agent_rl.online.gateway.collector import (
    CollectionSessionManager,
    CollectionSessionSpec,
    RewardMode,
)
from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.task_reward import TaskReward
from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.base import BaseAgentAdapter, BaseBenchAdapter
from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models import (
    AgentContext,
    AgentRunResult,
    AgentRuntimeBinding,
    EvalResult,
    Task,
)


@dataclass(frozen=True, slots=True)
class RLTaskAttemptSpec:
    """Immutable rollout, collection, and output identity for one task attempt."""

    training_key: str
    collection_session_id: str
    tokenizer_revision: str
    template_revision: str
    runtime: AgentRuntimeBinding
    output_dir: Path

    def __post_init__(self) -> None:
        for field_name in (
            "training_key",
            "collection_session_id",
            "tokenizer_revision",
            "template_revision",
        ):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} is required")
        object.__setattr__(self, "output_dir", Path(self.output_dir))

    def to_collection_spec(self) -> CollectionSessionSpec:
        return CollectionSessionSpec(
            session_id=self.collection_session_id,
            model_id=self.runtime.requested_model,
            tokenizer_revision=self.tokenizer_revision,
            template_revision=self.template_revision,
            reward_mode=RewardMode.TERMINAL_TASK,
        )


@dataclass(frozen=True, slots=True)
class RLTaskAttemptResult:
    """Observable successful outcome of the orchestration Module."""

    spec: RLTaskAttemptSpec
    agent_result: AgentRunResult
    eval_result: EvalResult
    reward: TaskReward
    projected_samples: int


class OnlineRLTaskOrchestrator:
    """Own collection, environment, Agent, verifier, reward, and cleanup ordering."""

    def __init__(
        self,
        *,
        collection_manager: CollectionSessionManager,
    ) -> None:
        self._collection_manager = collection_manager

    async def run_attempt(
        self,
        spec: RLTaskAttemptSpec,
        task: Task,
        *,
        agent: BaseAgentAdapter,
        benchmark: BaseBenchAdapter,
        environment: Any,
    ) -> RLTaskAttemptResult:
        await asyncio.to_thread(spec.output_dir.mkdir, parents=True, exist_ok=True)
        agent.set_logs_dir(spec.output_dir)
        session_created = False
        session_finalized = False
        environment_started = False
        try:
            await self._collection_manager.create_session(spec.to_collection_spec())
            session_created = True
            await environment.start()
            environment_started = True
            await benchmark.prepare_environment(task, environment)
            if not await agent.setup(environment):
                raise RuntimeError(f"Agent setup failed: {agent.name()}")

            agent_result = await agent.run(environment, task, AgentContext(runtime=spec.runtime))
            if bool(agent_result.metadata.get("is_error")):
                terminal_reason = str(agent_result.metadata.get("terminal_reason") or "unknown")
                raise RuntimeError(f"Agent run failed: {agent.name()} ({terminal_reason})")
            await self._collection_manager.finalize_session(spec.collection_session_id)
            session_finalized = True
            eval_result = await benchmark.evaluate(environment, task)
            reward = TaskReward(
                reward_id=f"{spec.runtime.attempt_id}:terminal",
                attempt_id=spec.runtime.attempt_id,
                task_id=task.task_id,
                training_key=spec.training_key,
                score=max(0.0, min(1.0, float(eval_result.pass_rate))),
                passed=eval_result.passed,
                termination_reason=str(agent_result.metadata.get("terminal_reason") or ""),
                details={
                    "returncode": eval_result.returncode,
                    "failed_tests": list(eval_result.failed_tests),
                    "test_details": dict(eval_result.test_details),
                },
            )
            projected_samples = await self._collection_manager.submit_task_reward(spec.collection_session_id, reward)
            return RLTaskAttemptResult(
                spec=spec,
                agent_result=agent_result,
                eval_result=eval_result,
                reward=reward,
                projected_samples=projected_samples,
            )
        except BaseException:
            if session_created and not session_finalized:
                await self._collection_manager.abort_session(spec.collection_session_id)
            raise
        finally:
            if environment_started:
                await environment.stop()
