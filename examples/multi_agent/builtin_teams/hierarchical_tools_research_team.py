# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
三层层次化研究团队示例 - Hierarchical Tools 模式

团队结构（3层）：
    研究主管 (Research Director)
    ├── 文献调研员 (Literature Researcher)
    └── 数据分析员 (Data Analyst)
        └── 统计专家 (Statistics Expert)

通信方式：通过 ability_manager 将子 Agent 注册为工具
"""
import sys
import os
# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

import asyncio
from typing import Any, AsyncIterator, Optional

from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.multi_agent.teams.hierarchical_tools import (
    HierarchicalTeam,
    HierarchicalTeamConfig,
)
from openjiuwen.core.multi_agent.schema.team_card import TeamCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.session.session import Session


# ============================================================================
# 配置
# ============================================================================

# 从 api_key_new.txt 读取配置
model_client_config = ModelClientConfig(
    client_id="jk0009",
    client_provider="OpenAI",
    api_key="jntqX_0W58WyYNiCBhWuaEswGOMZOzAfa8f-dnQ2svyqb6zQy5NLTgyuhwpZdS-Sn3zFlJvMYjEvKesDcnx1Sw",
    api_base="https://api.modelarts-maas.com/v2",
    verify_ssl=False
)

model_request_config = ModelRequestConfig(
    model="z-ai/glm-4.7",
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
        # 模拟统计分析
        result = f"统计分析结果：对 '{task}' 进行了描述性统计、假设检验和回归分析"
        return {"analysis": result, "confidence": 0.95}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        result = await self.invoke(inputs, session)
        yield result


# ============================================================================
# 第二层：文献调研员 和 数据分析员
# ============================================================================

class LiteratureResearcher(BaseAgent):
    """文献调研员：检索和总结相关文献"""

    def configure(self, config):
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        topic = inputs.get("topic", "") if isinstance(inputs, dict) else str(inputs)
        # 模拟文献检索
        result = f"文献调研：找到 15 篇关于 '{topic}' 的高质量论文，主要发现包括..."
        return {"literature_summary": result, "paper_count": 15}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        result = await self.invoke(inputs, session)
        yield result


class DataAnalyst(ReActAgent):
    """数据分析员：分析数据，可调用统计专家"""

    def __init__(self, card: AgentCard):
        super().__init__(card=card)


# ============================================================================
# 第一层：研究主管（根节点）
# ============================================================================

class ResearchDirector(ReActAgent):
    """研究主管：协调整个研究项目，可调用文献调研员和数据分析员"""

    def __init__(self, card: AgentCard):
        super().__init__(card=card)


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
    print("三层层次化研究团队示例 - Hierarchical Tools 模式")
    print("=" * 80)

    # 1. 创建叶子节点 Agent（第三层）
    statistics_expert = StatisticsExpert(card=statistics_expert_card)
    Runner.resource_mgr.add_agent(statistics_expert_card, lambda: statistics_expert)

    # 2. 创建第二层 Agent
    literature_researcher = LiteratureResearcher(card=literature_researcher_card)
    Runner.resource_mgr.add_agent(literature_researcher_card, lambda: literature_researcher)

    data_analyst_config = ReActAgentConfig(
        model_config_obj=model_request_config,
        model_client_config=model_client_config,
        prompt_template=[{
            "role": "system",
            "content": "你是数据分析员，负责分析数据。可以调用统计专家进行深度统计分析。"
        }]
    )
    data_analyst = DataAnalyst(card=data_analyst_card).configure(data_analyst_config)
    Runner.resource_mgr.add_agent(data_analyst_card, lambda: data_analyst)

    # 3. 创建根节点 Agent（第一层）
    director_config = ReActAgentConfig(
        model_config_obj=model_request_config,
        model_client_config=model_client_config,
        prompt_template=[{
            "role": "system",
            "content": (
                "你是研究主管，负责协调整个研究项目。"
                "可以调用文献调研员检索文献，调用数据分析员分析数据。"
                "请根据用户需求合理分配任务。"
            )
        }]
    )
    research_director = ResearchDirector(card=research_director_card).configure(director_config)
    Runner.resource_mgr.add_agent(research_director_card, lambda: research_director)

    # 4. 创建 HierarchicalTeam 并构建层次结构
    team_card = TeamCard(
        id="research_team",
        name="research_team",
        description="三层研究团队"
    )
    team_config = HierarchicalTeamConfig(root_agent=research_director_card)
    team = HierarchicalTeam(card=team_card, config=team_config)

    # 注册 Agent 到团队，并指定父子关系
    # 第一层：研究主管（根节点）
    team.add_agent(research_director_card, lambda: research_director)

    # 第二层：文献调研员和数据分析员（研究主管的子节点）
    team.add_agent(
        literature_researcher_card,
        lambda: literature_researcher,
        parent_agent_id="research_director"
    )
    team.add_agent(
        data_analyst_card,
        lambda: data_analyst,
        parent_agent_id="research_director"
    )

    # 第三层：统计专家（数据分析员的子节点）
    team.add_agent(
        statistics_expert_card,
        lambda: statistics_expert,
        parent_agent_id="data_analyst"
    )

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
