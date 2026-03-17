# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""GroupRuntime P2P Communication Example

点对点（P2P）通信模式：Planner -> Coder -> Reviewer 顺序协作。
参考 autogen sequential workflow 设计模式。

"""
import asyncio
from typing import Any, AsyncIterator, Optional

from openjiuwen.core.common.logging import multi_agent_logger
from openjiuwen.core.multi_agent.group_runtime import GroupRuntime, CommunicableAgent
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.session.session import Session


# ---------------------------------------------------------------------------
# Agent 定义 - 每个 Agent 继承 CommunicableAgent（通信能力）和 BaseAgent（生命周期）
# ---------------------------------------------------------------------------

class PlannerAgent(CommunicableAgent, BaseAgent):
    """规划 Agent：接收任务并制定执行计划"""

    def configure(self, config) -> 'PlannerAgent':
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        task = inputs.get("task", "") if isinstance(inputs, dict) else str(inputs)
        multi_agent_logger.info(f"[Planner] 收到任务: {task}")
        plan = {"task": task, "steps": ["1. 分析需求", "2. 设计方案", "3. 编写代码"]}
        multi_agent_logger.info(f"[Planner] 制定计划: {plan['steps']}")
        return plan

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        result = await self.invoke(inputs, session)
        yield result


class CoderAgent(CommunicableAgent, BaseAgent):
    """编码 Agent：根据计划实现代码"""

    def configure(self, config) -> 'CoderAgent':
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        steps = inputs.get("steps", []) if isinstance(inputs, dict) else []
        multi_agent_logger.info(f"[Coder]   收到计划，步骤数: {len(steps)}，开始编码...")
        code = "def solution():\n    # 实现代码\n    pass"
        multi_agent_logger.info(f"[Coder]   生成代码:\n          {code}")
        return {"code": code, "status": "completed"}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        result = await self.invoke(inputs, session)
        yield result


class ReviewerAgent(CommunicableAgent, BaseAgent):
    """审查 Agent：审查代码质量"""

    def configure(self, config) -> 'ReviewerAgent':
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        code = inputs.get("code", "") if isinstance(inputs, dict) else str(inputs)
        multi_agent_logger.info(f"[Reviewer] 审查代码，长度: {len(code)} 字符")
        review = {"approved": True, "comments": "代码结构清晰，符合规范"}
        multi_agent_logger.info(f"[Reviewer] 审查结果: {review}")
        return review

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        result = await self.invoke(inputs, session)
        yield result


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

async def main():
    multi_agent_logger.info("=" * 55)
    multi_agent_logger.info("GroupRuntime P2P 通信示例")
    multi_agent_logger.info("流程: user -> planner -P2P-> coder -P2P-> reviewer")
    multi_agent_logger.info("=" * 55)

    planner_card = AgentCard(id="planner", name="planner", description="任务规划者")
    coder_card = AgentCard(id="coder", name="coder", description="代码实现者")
    reviewer_card = AgentCard(id="reviewer", name="reviewer", description="代码审查者")

    # 创建并配置 GroupRuntime
    runtime = GroupRuntime()
    runtime.register_agent(planner_card, lambda: PlannerAgent(card=planner_card))
    runtime.register_agent(coder_card, lambda: CoderAgent(card=coder_card))
    runtime.register_agent(reviewer_card, lambda: ReviewerAgent(card=reviewer_card))
    await runtime.start()

    try:
        multi_agent_logger.info("\n--- P2P 顺序通信流程 ---\n")

        # Step 1: user -> planner
        plan = await runtime.send(
            message={"task": "实现快速排序算法"},
            recipient="planner",
            sender="user",
        )

        # Step 2: planner -> coder
        code_result = await runtime.send(
            message=plan,
            recipient="coder",
            sender="planner",
        )

        # Step 3: coder -> reviewer
        review_result = await runtime.send(
            message=code_result,
            recipient="reviewer",
            sender="coder",
        )

        multi_agent_logger.info("\n--- 流程完成 ---")
        multi_agent_logger.info(f"最终审查结果: {review_result}")

    finally:
        await runtime.stop()


if __name__ == "__main__":
    asyncio.run(main())
