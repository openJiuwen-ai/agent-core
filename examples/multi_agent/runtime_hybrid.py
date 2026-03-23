# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""TeamRuntime Hybrid Communication Example

混合通信模式（P2P + Pub-Sub）。

本示例展示混合通信（P2P & PubSub）模式：
  主流程 -P2P-> orchestrator
  orchestrator -Pub-Sub-> executors
  executors -Pub-Sub-> aggregator
  主流程 -P2P-> reporter  (主流程收集结果后调用)

参考 autogen mixture-of-agents 设计模式。
"""
import asyncio
from typing import Any, AsyncIterator, Optional

from openjiuwen.core.common.logging import multi_agent_logger
from openjiuwen.core.multi_agent.team_runtime import TeamRuntime, CommunicableAgent
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.session.session import Session


# ---------------------------------------------------------------------------
# Agent 定义
# ---------------------------------------------------------------------------

class OrchestratorAgent(CommunicableAgent, BaseAgent):
    """编排 Agent：收到任务后用 Pub-Sub 广播给所有 Executor。"""

    def configure(self, config) -> 'OrchestratorAgent':
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        task = inputs.get("task", "") if isinstance(inputs, dict) else str(inputs)
        multi_agent_logger.info(f"[Orchestrator] task: {task}")

        await self.publish(
            message={"event": "execution_request", "task": task},
            topic_id="execution_events",
        )
        return {"status": "broadcast_done", "task": task}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, session)


class ExecutorAgent(CommunicableAgent, BaseAgent):
    """执行 Agent：订阅 execution_events，完成后 Pub-Sub 发布结果。"""

    def __init__(self, card, executor_id: int):
        super().__init__(card=card)
        self.executor_id = executor_id

    def configure(self, config) -> 'ExecutorAgent':
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        if not isinstance(inputs, dict) or inputs.get("event") != "execution_request":
            return {"status": "ignored"}
        task = inputs.get("task", "")
        result = f"executor-{self.executor_id} done: {task}"
        multi_agent_logger.info(f"[Executor-{self.executor_id}] {result}")
        await self.publish(
            message={"event": "task_completed", "executor": self.executor_id, "result": result},
            topic_id="completion_events",
        )
        return {"status": "executed"}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, session)


class AggregatorAgent(CommunicableAgent, BaseAgent):
    """聚合 Agent：收集所有 Executor 结果，完成后 set done_event。"""

    def __init__(self, card, done_event: asyncio.Event, expected: int = 3):
        super().__init__(card=card)
        self._results: list = []
        self._lock = asyncio.Lock()
        self._done_event = done_event
        self._expected = expected

    def configure(self, config) -> 'AggregatorAgent':
        return self

    def get_results(self) -> list:
        return list(self._results)

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        if not isinstance(inputs, dict) or inputs.get("event") != "task_completed":
            return {"status": "ignored"}
        async with self._lock:
            self._results.append(inputs.get("result", ""))
            count = len(self._results)
            multi_agent_logger.info(f"[Aggregator]   ({count}/{self._expected}): {inputs.get('result')}")
            if count == self._expected:
                self._done_event.set()
        return {"status": "aggregated"}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, session)


class ReporterAgent(CommunicableAgent, BaseAgent):
    """报告 Agent：由主流程 P2P 调用，生成最终报告。"""

    def configure(self, config) -> 'ReporterAgent':
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        results = inputs.get("results", []) if isinstance(inputs, dict) else []
        multi_agent_logger.info(f"[Reporter]     final report ({len(results)} items):")
        for i, r in enumerate(results, 1):
            multi_agent_logger.info(f"               {i}. {r}")
        return {"status": "report_generated", "total": len(results)}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, session)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

async def main():
    multi_agent_logger.info("=" * 55)
    multi_agent_logger.info("TeamRuntime Hybrid (P2P + Pub-Sub)")
    multi_agent_logger.info("Flow: main -P2P-> orchestrator -Pub-Sub-> executors")
    multi_agent_logger.info("      executors -Pub-Sub-> aggregator")
    multi_agent_logger.info("      main -P2P-> reporter")
    multi_agent_logger.info("=" * 55)

    done_event = asyncio.Event()

    orchestrator_card = AgentCard(id="orchestrator", name="orchestrator", description="orchestrator")
    executor1_card = AgentCard(id="executor1", name="executor1", description="executor1")
    executor2_card = AgentCard(id="executor2", name="executor2", description="executor2")
    executor3_card = AgentCard(id="executor3", name="executor3", description="executor3")
    aggregator_card = AgentCard(id="aggregator", name="aggregator", description="aggregator")
    reporter_card = AgentCard(id="reporter", name="reporter", description="reporter")

    agg = AggregatorAgent(card=aggregator_card, done_event=done_event, expected=3)

    runtime = TeamRuntime()
    runtime.register_agent(orchestrator_card, lambda: OrchestratorAgent(card=orchestrator_card))
    runtime.register_agent(executor1_card, lambda: ExecutorAgent(card=executor1_card, executor_id=1))
    runtime.register_agent(executor2_card, lambda: ExecutorAgent(card=executor2_card, executor_id=2))
    runtime.register_agent(executor3_card, lambda: ExecutorAgent(card=executor3_card, executor_id=3))
    runtime.register_agent(aggregator_card, lambda: agg)
    runtime.register_agent(reporter_card, lambda: ReporterAgent(card=reporter_card))

    # Pub-Sub 订阅
    await runtime.subscribe("executor1", "execution_events")
    await runtime.subscribe("executor2", "execution_events")
    await runtime.subscribe("executor3", "execution_events")
    await runtime.subscribe("aggregator", "completion_events")

    await runtime.start()
    multi_agent_logger.info("\n--- flow start ---\n")

    try:
        # Step 1: P2P -> orchestrator (广播给 executors)
        orch_result = await runtime.send(
            message={"task": "build new feature"},
            recipient="orchestrator",
            sender="main",
        )
        multi_agent_logger.info(f"[main] orchestrator result: {orch_result}")

        # Step 2: 等待 aggregator 收集完 3 条结果（最多 5 秒）
        try:
            await asyncio.wait_for(done_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            multi_agent_logger.info("[warn] aggregator timeout")

        # Step 3: P2P -> reporter
        report = await runtime.send(
            message={"results": agg.get_results()},
            recipient="reporter",
            sender="main",
        )

        multi_agent_logger.info("\n--- flow complete ---")
        multi_agent_logger.info(f"report: {report}")

    finally:
        await runtime.stop()


if __name__ == "__main__":
    asyncio.run(main())
