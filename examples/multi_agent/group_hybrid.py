# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""BaseGroup Hybrid Communication Example

将混合通信（P2P + Pub-Sub）封装为 TaskExecutionGroup（继承 BaseGroup），
对外暴露统一的 invoke() 接口。

正确模式：
  主流程 -P2P-> orchestrator  (只做 Pub-Sub 广播)
  orchestrator -Pub-Sub-> executors
  executors    -Pub-Sub-> aggregator  (set done_event)
  主流程 -P2P-> reporter
"""
import asyncio
from typing import Any, AsyncIterator, Optional

from openjiuwen.core.common.logging import multi_agent_logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.multi_agent.group import BaseGroup
from openjiuwen.core.multi_agent.group_runtime import CommunicableAgent
from openjiuwen.core.multi_agent.schema.group_card import GroupCard
from openjiuwen.core.multi_agent.config import GroupConfig
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.session.agent_group import Session
from openjiuwen.core.session.session import Session as AgentSession


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
        session_id = session.get_session_id() if session else None
        await self.publish(
            message={"event": "execution_request", "task": task},
            topic_id="execution_events",
            session_id=session_id,
        )
        return {"status": "broadcast_done", "task": task}

    async def stream(self, inputs: Any, session: Optional[AgentSession] = None) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, session)


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
        result = f"executor-{self.executor_id} done: {task}"
        multi_agent_logger.info(f"  [Executor-{self.executor_id}] {result}")
        session_id = session.get_session_id() if session else None
        await self.publish(
            message={"event": "task_completed", "executor": self.executor_id, "result": result},
            topic_id="completion_events",
            session_id=session_id,
        )
        return {"status": "executed"}

    async def stream(self, inputs: Any, session: Optional[AgentSession] = None) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, session)


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
            if count == self._expected:
                self._done_event.set()
        return {"status": "aggregated"}

    async def stream(self, inputs: Any, session: Optional[AgentSession] = None) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, session)


class ReporterAgent(CommunicableAgent, BaseAgent):
    """由主流程 P2P 调用，生成最终报告"""

    def configure(self, config) -> 'ReporterAgent':
        return self

    async def invoke(self, inputs: Any, session: Optional[AgentSession] = None) -> Any:
        results = inputs.get("results", []) if isinstance(inputs, dict) else []
        multi_agent_logger.info(f"  [Reporter]     report ({len(results)} items):")
        for i, r in enumerate(results, 1):
            multi_agent_logger.info(f"                 {i}. {r}")
        return {"status": "report_generated", "total": len(results)}

    async def stream(self, inputs: Any, session: Optional[AgentSession] = None) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, session)


# ---------------------------------------------------------------------------
# BaseGroup 封装
# ---------------------------------------------------------------------------

class TaskExecutionGroup(BaseGroup):
    """任务执行团队：封装混合通信。"""

    EXECUTOR_COUNT = 3
    EXTRA_PUBLISH_COUNT = 1

    def __init__(self, card: GroupCard, config: Optional[GroupConfig] = None):
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
            expected=self._expected_result_count(),
        )

        (self
         .add_agent(self.orchestrator_card, lambda: OrchestratorAgent(card=self.orchestrator_card))
         .add_agent(self.executor1_card, lambda: ExecutorAgent(card=self.executor1_card, executor_id=1))
         .add_agent(self.executor2_card, lambda: ExecutorAgent(card=self.executor2_card, executor_id=2))
         .add_agent(self.executor3_card, lambda: ExecutorAgent(card=self.executor3_card, executor_id=3))
         .add_agent(self.aggregator_card, lambda: self.aggregator)
         .add_agent(self.reporter_card, lambda: ReporterAgent(card=self.reporter_card)))

    @classmethod
    def _expected_result_count(cls) -> int:
        return cls.EXECUTOR_COUNT * (1 + cls.EXTRA_PUBLISH_COUNT)

    async def _setup_subscriptions(self) -> None:
        if self._subscriptions_ready:
            return
        await self.subscribe("executor1", "execution_events")
        await self.subscribe("executor2", "execution_events")
        await self.subscribe("executor3", "execution_events")
        await self.subscribe("aggregator", "completion_events")
        self._subscriptions_ready = True

    async def invoke(self, message: Any, session: Optional[Session] = None) -> Any:
        done_event = asyncio.Event()
        session_id = session.get_session_id() if session else None
        self.aggregator.reset(done_event=done_event, expected=self._expected_result_count())

        await self.runtime.start()
        await self._setup_subscriptions()
        try:
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
            await self.runtime.publish(
                message={
                    "event": "execution_request", 
                    "task": f"{message.get('task', '')} [直接发布]",
                    "source": "main_process"
                },
                topic_id="execution_events",
                sender="main_process",
                session_id=session_id,
            )
            
            # Step 2: 等待 aggregator 收集完成
            # 注意：现在会收到 6 个结果（3个来自 orchestrator，3个来自直接 publish）
            try:
                await asyncio.wait_for(done_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                multi_agent_logger.info("  [warn] aggregator timeout")
            
            # Step 3: P2P -> reporter 生成最终报告
            report = await self.runtime.send(
                message={"results": self.aggregator.get_results()},
                recipient="reporter",
                sender="main_process",
                session_id=session_id,
            )
            
            return {"orchestration": orch_result, "report": report}
        finally:
            await self.runtime.stop()

    async def stream(self, message: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        yield await self.invoke(message, session)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

async def main():
    multi_agent_logger.info("=" * 55)
    multi_agent_logger.info("BaseGroup 混合通信封装示例")
    multi_agent_logger.info("=" * 55)

    group_card = GroupCard(id="task_execution_group", name="task_execution_group", description="任务执行团队")
    group_config = GroupConfig(max_agents=10)
    group = TaskExecutionGroup(card=group_card, config=group_config)

    multi_agent_logger.info("\n--- 混合通信示例 (P2P + Pub-Sub) ---\n")
    await Runner.resource_mgr.add_agent_group(group.card, lambda: group)
    try:
        result = await Runner.run_agent_group(
            agent_group=group.card.id,
            inputs={"task": "开发新功能模块"},
        )

        multi_agent_logger.info("\n--- 任务完成 ---")
        multi_agent_logger.info(f"结果: {result}")
    finally:
        await Runner.resource_mgr.remove_agent_group(group_id=group.card.id)
        await group.runtime.stop()


if __name__ == "__main__":
    asyncio.run(main())
