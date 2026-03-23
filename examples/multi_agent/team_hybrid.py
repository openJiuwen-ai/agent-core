# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""BaseTeam Hybrid Communication Example

将混合通信（P2P + Pub-Sub）封装为 TaskExecutionTeam（继承 BaseTeam），
对外暴露统一的 invoke() 接口。

正确模式：
  主流程 -P2P-> orchestrator  (只做 Pub-Sub 广播)
  orchestrator -Pub-Sub-> executors
  executors    -Pub-Sub-> aggregator  (set done_event)
  主流程 -P2P-> reporter
"""
import asyncio
from typing import (
    Any,
    AsyncIterator,
    Optional,
)

from openjiuwen.core.common.logging import multi_agent_logger
from openjiuwen.core.multi_agent.config import TeamConfig
from openjiuwen.core.multi_agent.team import BaseTeam
from openjiuwen.core.multi_agent.team_runtime import CommunicableAgent
from openjiuwen.core.multi_agent.schema.team_card import TeamCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent_team import Session
from openjiuwen.core.session.session import Session as AgentSession
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


# ---------------------------------------------------------------------------
# Agent 定义
# ---------------------------------------------------------------------------

class OrchestratorAgent(CommunicableAgent, BaseAgent):
    """收到任务后 Pub-Sub 广播"""

    def configure(self, config) -> 'OrchestratorAgent':
        return self

    async def invoke(self, inputs: Any, session: Optional[AgentSession] = None) -> Any:
        task = inputs.get("task", "") if isinstance(inputs, dict) else str(inputs)
        multi_agent_logger.info(f"  [Orchestrator] task: {task}")
        if session:
            await session.write_stream({"event": "orchestrator_received", "task": task})
        session_id = session.get_session_id() if session else None
        await self.publish(
            message={"event": "execution_request", "task": task},
            topic_id="execution_events",
            session_id=session_id,
        )
        result = {"status": "broadcast_done", "task": task}
        if session:
            await session.write_stream({"event": "orchestrator_published", **result})
        return result

    async def stream(self, inputs: Any, session: Optional[AgentSession] = None) -> AsyncIterator[Any]:
        await self.invoke(inputs, session)
        if False:
            yield None


class ExecutorAgent(CommunicableAgent, BaseAgent):
    """订阅 execution_events，执行后 Pub-Sub 发布完成"""

    def __init__(self, card, executor_id: int):
        super().__init__(card=card)
        self.executor_id = executor_id

    def configure(self, config) -> 'ExecutorAgent':
        return self

    async def invoke(self, inputs: Any, session: Optional[AgentSession] = None) -> Any:
        if not isinstance(inputs, dict) or inputs.get("event") != "execution_request":
            return {"status": "ignored"}
        task = inputs.get("task", "")
        if session:
            await session.write_stream(
                {"event": "executor_started", "executor": self.executor_id, "task": task}
            )
        result = f"executor-{self.executor_id} done: {task}"
        multi_agent_logger.info(f"  [Executor-{self.executor_id}] {result}")
        session_id = session.get_session_id() if session else None
        await self.publish(
            message={"event": "task_completed", "executor": self.executor_id, "result": result},
            topic_id="completion_events",
            session_id=session_id,
        )
        response = {"status": "executed", "executor": self.executor_id, "result": result}
        if session:
            await session.write_stream({"event": "executor_completed", **response})
        return response

    async def stream(self, inputs: Any, session: Optional[AgentSession] = None) -> AsyncIterator[Any]:
        await self.invoke(inputs, session)
        if False:
            yield None


class AggregatorAgent(CommunicableAgent, BaseAgent):
    """收集结果，完成后 set done_event"""

    def __init__(self, card, done_event: asyncio.Event, expected: int = 3):
        super().__init__(card=card)
        self._results: list = []
        self._lock = asyncio.Lock()
        self._done_event = done_event
        self._expected = expected

    def configure(self, config) -> 'AggregatorAgent':
        return self

    def reset(self, done_event: asyncio.Event, expected: int) -> None:
        self._results.clear()
        self._done_event = done_event
        self._expected = expected

    def get_results(self) -> list:
        return list(self._results)

    async def invoke(self, inputs: Any, session: Optional[AgentSession] = None) -> Any:
        if not isinstance(inputs, dict) or inputs.get("event") != "task_completed":
            return {"status": "ignored"}
        async with self._lock:
            self._results.append(inputs.get("result", ""))
            count = len(self._results)
            multi_agent_logger.info(f"  [Aggregator]   ({count}/{self._expected}): {inputs.get('result')}")
            if session:
                await session.write_stream(
                    {
                        "event": "aggregator_progress",
                        "status": "aggregating",
                        "count": count,
                        "total": self._expected,
                        "result": inputs.get("result", ""),
                    }
                )
            if count == self._expected:
                self._done_event.set()
                if session:
                    await session.write_stream(
                        {
                            "event": "aggregator_done",
                            "status": "aggregated",
                            "count": count,
                            "total": self._expected,
                        }
                    )
        return {"status": "aggregated", "count": count, "total": self._expected}

    async def stream(self, inputs: Any, session: Optional[AgentSession] = None) -> AsyncIterator[Any]:
        await self.invoke(inputs, session)
        if False:
            yield None


class ReporterAgent(CommunicableAgent, BaseAgent):
    """由主流程 P2P 调用，生成最终报告"""

    def configure(self, config) -> 'ReporterAgent':
        return self

    async def invoke(self, inputs: Any, session: Optional[AgentSession] = None) -> Any:
        results = inputs.get("results", []) if isinstance(inputs, dict) else []
        multi_agent_logger.info(f"  [Reporter]     report ({len(results)} items):")
        if session:
            await session.write_stream({"event": "reporter_started", "total": len(results)})
        for i, r in enumerate(results, 1):
            multi_agent_logger.info(f"                 {i}. {r}")
        result = {"status": "report_generated", "total": len(results)}
        if session:
            await session.write_stream({"event": "reporter_completed", **result})
        return result

    async def stream(self, inputs: Any, session: Optional[AgentSession] = None) -> AsyncIterator[Any]:
        await self.invoke(inputs, session)
        if False:
            yield None


# ---------------------------------------------------------------------------
# BaseTeam 封装
# ---------------------------------------------------------------------------

class TaskExecutionTeam(BaseTeam):
    """任务执行团队：封装混合通信。"""

    EXECUTOR_COUNT = 3
    EXTRA_PUBLISH_COUNT = 1

    def __init__(self, card: TeamCard, config: Optional[TeamConfig] = None):
        super().__init__(card=card, config=config)
        self._subscriptions_ready = False

        self.orchestrator_card = AgentCard(id="orchestrator", name="orchestrator", description="orchestrator")
        self.executor1_card = AgentCard(id="executor1", name="executor1", description="executor1")
        self.executor2_card = AgentCard(id="executor2", name="executor2", description="executor2")
        self.executor3_card = AgentCard(id="executor3", name="executor3", description="executor3")
        self.aggregator_card = AgentCard(id="aggregator", name="aggregator", description="aggregator")
        self.reporter_card = AgentCard(id="reporter", name="reporter", description="reporter")

        self.aggregator = AggregatorAgent(
            card=self.aggregator_card,
            done_event=asyncio.Event(),
            expected=self.expected_result_count(),
        )

        (self
         .add_agent(self.orchestrator_card, lambda: OrchestratorAgent(card=self.orchestrator_card))
         .add_agent(self.executor1_card, lambda: ExecutorAgent(card=self.executor1_card, executor_id=1))
         .add_agent(self.executor2_card, lambda: ExecutorAgent(card=self.executor2_card, executor_id=2))
         .add_agent(self.executor3_card, lambda: ExecutorAgent(card=self.executor3_card, executor_id=3))
         .add_agent(self.aggregator_card, lambda: self.aggregator)
         .add_agent(self.reporter_card, lambda: ReporterAgent(card=self.reporter_card)))

    @classmethod
    def expected_result_count(cls) -> int:
        return cls.EXECUTOR_COUNT * (1 + cls.EXTRA_PUBLISH_COUNT)

    async def _setup_subscriptions(self) -> None:
        if self._subscriptions_ready:
            return
        await self.subscribe("executor1", "execution_events")
        await self.subscribe("executor2", "execution_events")
        await self.subscribe("executor3", "execution_events")
        await self.subscribe("aggregator", "completion_events")
        self._subscriptions_ready = True

    @staticmethod
    async def _write_team_stream(session: Optional[Session], payload: dict[str, Any]) -> None:
        if session:
            await session.write_stream(payload)

    async def _run_workflow(self, message: Any, session: Optional[Session] = None) -> Any:
        done_event = asyncio.Event()
        session_id = session.get_session_id() if session else None
        self.aggregator.reset(done_event=done_event, expected=self.expected_result_count())

        await self.runtime.start()
        await self._setup_subscriptions()
        try:
            await self._write_team_stream(
                session,
                {"event": "team_started",
                 "task": message.get("task", "") if isinstance(message, dict) else str(message)},
            )
            # Step 1a: P2P -> orchestrator
            # orchestrator 内部会 publish 到 execution_events
            multi_agent_logger.info("  [Main] 使用 P2P 发送到 orchestrator")
            orch_result = await self.runtime.send(
                message=message,
                recipient="orchestrator",
                sender="main_process",
                session_id=session_id,
            )

            # Step 1b: 主流程直接 publish 示例
            # 直接发布消息到 topic，与 P2P 并行使用
            multi_agent_logger.info("  [Main] 同时使用直接 publish 发送额外通知")
            await self._write_team_stream(
                session,
                {
                    "event": "team_direct_publish",
                    "task": f"{message.get('task', '')} [直接发布]" if isinstance(message, dict) else f"{message} [直接发布]",
                },
            )
            await self.runtime.publish(
                message={
                    "event": "execution_request",
                    "task": f"{message.get('task', '')} [直接发布]",
                    "source": "main_process",
                },
                topic_id="execution_events",
                sender="main_process",
                session_id=session_id,
            )

            # Step 2: 等待 aggregator 收集完成
            # 注意：现在会收到 6 个结果（3个来自 orchestrator，3个来自直接 publish）
            await self._write_team_stream(
                session,
                {"event": "team_waiting_aggregation", "expected": self.expected_result_count()},
            )
            try:
                await asyncio.wait_for(done_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                multi_agent_logger.info("  [warn] aggregator timeout")
                await self._write_team_stream(session, {"event": "team_timeout", "status": "timeout"})

            # Step 3: P2P -> reporter 生成最终报告
            report = await self.runtime.send(
                message={"results": self.aggregator.get_results()},
                recipient="reporter",
                sender="main_process",
                session_id=session_id,
            )

            result = {"orchestration": orch_result, "report": report}
            await self._write_team_stream(
                session,
                {"event": "team_completed", "status": "completed", "result": result},
            )
            return result
        finally:
            await self.runtime.stop()

    async def invoke(self, message: Any, session: Optional[Session] = None) -> Any:
        if session is None:
            raise ValueError("TaskExecutionTeam.invoke requires a team session. Use Runner.run_agent_team(...).")
        return await self._run_workflow(message, session)

    async def stream(self, message: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        if session is None:
            raise ValueError(
                "TaskExecutionTeam.stream requires a team session. Use Runner.run_agent_team_streaming(...)."
            )

        async def run_workflow() -> None:
            try:
                await self._run_workflow(message, session)
            finally:
                await session.close_stream()

        task = asyncio.create_task(run_workflow())
        try:
            async for chunk in session.stream_iterator():
                yield chunk
        finally:
            await task


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

async def main():
    multi_agent_logger.info("=" * 55)
    multi_agent_logger.info("BaseTeam 混合通信封装示例")
    multi_agent_logger.info("=" * 55)

    team_card = TeamCard(id="task_execution_team", name="task_execution_team", description="任务执行团队")
    team_config = TeamConfig(max_agents=10)
    team = TaskExecutionTeam(card=team_card, config=team_config)

    multi_agent_logger.info("\n--- 混合通信示例 (P2P + Pub-Sub) ---\n")
    await Runner.resource_mgr.add_agent_team(team.card, lambda: team)
    try:
        result = await Runner.run_agent_team(
            agent_team=team.card.id,
            inputs={"task": "开发新功能模块"},
        )

        multi_agent_logger.info("\n--- 任务完成 ---")
        multi_agent_logger.info(f"结果: {result}")
    finally:
        await Runner.resource_mgr.remove_agent_team(team_id=team.card.id)
        await team.runtime.stop()


if __name__ == "__main__":
    asyncio.run(main())
