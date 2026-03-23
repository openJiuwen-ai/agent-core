# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""TeamRuntime Pub-Sub Communication Example

发布-订阅（Pub-Sub）通信模式：Coordinator 广播任务，多个 Worker 并发处理。
参考 autogen team chat 设计模式。
"""
import asyncio
from typing import Any, AsyncIterator, Optional

from openjiuwen.core.common.logging import multi_agent_logger
from openjiuwen.core.multi_agent.team_runtime import TeamRuntime, CommunicableAgent
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.session.session import Session


class CoordinatorAgent(CommunicableAgent, BaseAgent):
    """协调 Agent：发布任务到 task_events 主题"""

    def configure(self, config) -> 'CoordinatorAgent':
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        task = inputs.get("task", "") if isinstance(inputs, dict) else str(inputs)
        multi_agent_logger.info(f"[Coordinator] 发布任务: {task}")
        await self.publish(
            message={"event": "new_task", "task": task, "priority": "high"},
            topic_id="task_events",
        )
        return {"status": "task_published"}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        result = await self.invoke(inputs, session)
        yield result


class WorkerAgent(CommunicableAgent, BaseAgent):
    """工作 Agent：订阅 task_events，处理后发布完成事件"""

    def __init__(self, card: AgentCard, worker_id: str):
        super().__init__(card=card)
        self.worker_id = worker_id

    def configure(self, config) -> 'WorkerAgent':
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        if not isinstance(inputs, dict) or inputs.get("event") != "new_task":
            return {"status": "ignored"}
        task = inputs.get("task", "")
        multi_agent_logger.info(f"[Worker-{self.worker_id}] 收到任务: {task}")
        result = f"Worker-{self.worker_id} 完成: {task}"
        multi_agent_logger.info(f"[Worker-{self.worker_id}] {result}")
        await self.publish(
            message={"event": "task_completed", "worker": self.worker_id, "result": result},
            topic_id="completion_events",
        )
        return {"status": "processed"}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        result = await self.invoke(inputs, session)
        yield result


class MonitorAgent(CommunicableAgent, BaseAgent):
    """监控 Agent：订阅 completion_events，记录完成情况"""

    def configure(self, config) -> 'MonitorAgent':
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        if isinstance(inputs, dict) and inputs.get("event") == "task_completed":
            worker = inputs.get("worker")
            result = inputs.get("result")
            multi_agent_logger.info(f"[Monitor]     记录完成: {worker} -> {result}")
        return {"status": "logged"}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        result = await self.invoke(inputs, session)
        yield result


async def main():
    multi_agent_logger.info("=" * 55)
    multi_agent_logger.info("TeamRuntime Pub-Sub 通信示例")
    multi_agent_logger.info("流程: coordinator --publish--> [worker1, worker2, worker3]")
    multi_agent_logger.info("      workers --publish--> monitor")
    multi_agent_logger.info("=" * 55)

    coordinator_card = AgentCard(id="coordinator", name="coordinator", description="任务协调者")
    worker1_card = AgentCard(id="worker1", name="worker1", description="工作者1")
    worker2_card = AgentCard(id="worker2", name="worker2", description="工作者2")
    worker3_card = AgentCard(id="worker3", name="worker3", description="工作者3")
    monitor_card = AgentCard(id="monitor", name="monitor", description="任务监控者")

    runtime = TeamRuntime()
    runtime.register_agent(coordinator_card, lambda: CoordinatorAgent(card=coordinator_card))
    runtime.register_agent(worker1_card, lambda: WorkerAgent(card=worker1_card, worker_id="1"))
    runtime.register_agent(worker2_card, lambda: WorkerAgent(card=worker2_card, worker_id="2"))
    runtime.register_agent(worker3_card, lambda: WorkerAgent(card=worker3_card, worker_id="3"))
    runtime.register_agent(monitor_card, lambda: MonitorAgent(card=monitor_card))

    await runtime.subscribe("worker1", "task_events")
    await runtime.subscribe("worker2", "task_events")
    await runtime.subscribe("worker3", "task_events")
    await runtime.subscribe("monitor", "completion_events")

    await runtime.start()

    try:
        multi_agent_logger.info("\n--- Pub-Sub 广播流程 ---\n")
        await runtime.publish(
            message={"event": "new_task", "task": "处理数据批次", "priority": "high"},
            topic_id="task_events",
            sender="coordinator",
        )
        await asyncio.sleep(1)
        multi_agent_logger.info("\n--- Pub-Sub 流程完成 ---")
    finally:
        await runtime.stop()


if __name__ == "__main__":
    asyncio.run(main())
