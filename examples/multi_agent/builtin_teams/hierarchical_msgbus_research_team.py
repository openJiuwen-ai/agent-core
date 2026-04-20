# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
三层层次化研究团队示例 - Hierarchical MessageBus 模式

团队结构（3层）：
    研究主管 (Research Director - Supervisor)
    ├── 文献调研员 (Literature Researcher)
    └── 数据分析员 (Data Analyst - Supervisor)
        └── 统计专家 (Statistics Expert)

通信方式：通过 P2P MessageBus 进行通信，Supervisor 使用 P2PAbilityManager
"""
import sys
import os
# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

import asyncio
from typing import Any, AsyncIterator, Optional

from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.multi_agent.teams.hierarchical_msgbus import (
    HierarchicalTeam,
    HierarchicalTeamConfig,
    SupervisorAgent,
)
from openjiuwen.core.multi_agent.schema.team_card import TeamCard
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.session.session import Session


# ============================================================================
# 配置
# ============================================================================

model_client_config = ModelClientConfig(
    client_id="your client id",
    client_provider="your client provider",
    api_key="your api key",
    api_base="your api base",
    verify_ssl=False
)

model_request_config = ModelRequestConfig(
    model="your model",
    temperature=0.7,
)


# ============================================================================
# 第三层：统计专家（叶子节点）
# ============================================================================

class StatisticsExpert(BaseAgent):
    """统计专家：执行统计分析任务"""

    def configure(self, config):
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        task = inputs.get("task", "") if isinstance(inputs, dict) else str(inputs)
        result = f"统计分析结果：对 '{task}' 进行了描述性统计、假设检验和回归分析"
        return {"analysis": result, "confidence": 0.95}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        result = await self.invoke(inputs, session)
        yield result


# ============================================================================
# 第二层：文献调研员 和 数据分析员（Supervisor）
# ============================================================================

class LiteratureResearcher(BaseAgent):
    """文献调研员：检索和总结相关文献"""

    def configure(self, config):
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        topic = inputs.get("topic", "") if isinstance(inputs, dict) else str(inputs)
        result = f"文献调研：找到 15 篇关于 '{topic}' 的高质量论文，主要发现包括..."
        return {"literature_summary": result, "paper_count": 15}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        result = await self.invoke(inputs, session)
        yield result


# ============================================================================
# AgentCard 定义
# ============================================================================

statistics_expert_card = AgentCard(
    id="statistics_expert",
    name="statistics_expert",
    description="统计专家，执行统计分析、假设检验和回归分析",
    input_params={
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "统计分析任务描述"}
        },
        "required": ["task"]
    }
)

literature_researcher_card = AgentCard(
    id="literature_researcher",
    name="literature_researcher",
    description="文献调研员，检索和总结学术文献",
    input_params={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "研究主题"}
        },
        "required": ["topic"]
    }
)

data_analyst_card = AgentCard(
    id="data_analyst",
    name="data_analyst",
    description="数据分析员，分析数据并生成报告，可调用统计专家",
    input_params={
        "type": "object",
        "properties": {
            "data_description": {"type": "string", "description": "数据描述"},
            "analysis_goal": {"type": "string", "description": "分析目标"}
        },
        "required": ["data_description", "analysis_goal"]
    }
)

research_director_card = AgentCard(
    id="research_director",
    name="research_director",
    description="研究主管，协调研究项目，可调用文献调研员和数据分析员"
)


# ============================================================================
# 主函数
# ============================================================================

async def main():
    print("=" * 80)
    print("三层层次化研究团队示例 - Hierarchical MessageBus 模式")
    print("=" * 80)

    # 1. 创建叶子节点 Agent（第三层）
    statistics_expert = StatisticsExpert(card=statistics_expert_card)

    # 2. 创建第二层 Agent
    literature_researcher = LiteratureResearcher(card=literature_researcher_card)

    # 数据分析员作为 Supervisor（管理统计专家）
    data_analyst_card_supervisor, data_analyst_provider = SupervisorAgent.create(
        agents=[statistics_expert_card],
        model_client_config=model_client_config,
        model_request_config=model_request_config,
        agent_card=data_analyst_card,
        system_prompt="你是数据分析员，负责分析数据。可以调用统计专家进行深度统计分析。",
        max_iterations=5,
        max_parallel_sub_agents=5
    )

    # 3. 创建根节点 Supervisor（第一层）
    research_director_card_supervisor, research_director_provider = SupervisorAgent.create(
        agents=[literature_researcher_card, data_analyst_card],
        model_client_config=model_client_config,
        model_request_config=model_request_config,
        agent_card=research_director_card,
        system_prompt=(
            "你是研究主管，负责协调整个研究项目。"
            "可以调用文献调研员检索文献，调用数据分析员分析数据。"
            "请根据用户需求合理分配任务。"
        ),
        max_iterations=5,
        max_parallel_sub_agents=5
    )

    # 4. 创建 HierarchicalTeam
    team_card = TeamCard(
        id="research_team",
        name="research_team",
        description="三层研究团队"
    )
    team_config = HierarchicalTeamConfig(supervisor_agent=research_director_card)
    team = HierarchicalTeam(card=team_card, config=team_config)

    # 注册所有 Agent 到团队
    team.add_agent(research_director_card, research_director_provider)
    team.add_agent(literature_researcher_card, lambda: literature_researcher)
    team.add_agent(data_analyst_card, data_analyst_provider)
    team.add_agent(statistics_expert_card, lambda: statistics_expert)

    # 5. 运行团队
    print("\n任务：研究人工智能在医疗诊断中的应用\n")
    result = await team.invoke({
        "query": "请研究人工智能在医疗诊断中的应用，包括文献调研和数据分析"
    })

    print("\n" + "=" * 80)
    print("研究结果：")
    print("=" * 80)
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
