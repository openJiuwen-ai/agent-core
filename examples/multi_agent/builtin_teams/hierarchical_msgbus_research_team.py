# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
三层层次化研究团队示例 - Hierarchical MessageBus 模式

团队结构（3层）：
    研究主管 (Research Director - Supervisor)
    ├── 文献调研员 (Literature Researcher)
    └── 数据分析员 (Data Analyst - Supervisor)
        └── 统计专家 (Statistics Expert)
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

import asyncio
from typing import Any, AsyncIterator, Optional

from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.multi_agent.teams.hierarchical_msgbus import (
    HierarchicalTeam,
    HierarchicalTeamConfig,
    SupervisorAgent,
)
from openjiuwen.core.multi_agent.team_runtime.communicable_agent import CommunicableAgent
from openjiuwen.core.multi_agent.schema.team_card import TeamCard
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.session.session import Session

# ============================================================================
# 模型配置
# ============================================================================

model_client_config = ModelClientConfig(
    client_id="openai-client",
    client_provider="openai",
    api_key="your api key",
    api_base="your api base",
    timeout=600.0,
    max_retries=3,
    verify_ssl=False,
)

model_request_config = ModelRequestConfig(
    model="gpt-4o",
    temperature=0.7,
)


# ============================================================================
# Agent 实现
# ============================================================================

class StatisticsExpert(CommunicableAgent, BaseAgent):
    """统计专家：执行统计分析任务"""

    def configure(self, config):
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        task = inputs.get("task", "") if isinstance(inputs, dict) else str(inputs)
        return {"analysis": f"统计分析结果：对 '{task}' 进行了描述性统计、假设检验和回归分析", "confidence": 0.95}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        result = await self.invoke(inputs, session)
        yield result


class LiteratureResearcher(CommunicableAgent, BaseAgent):
    """文献调研员：检索和总结相关文献"""

    def configure(self, config):
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        topic = inputs.get("topic", "") if isinstance(inputs, dict) else str(inputs)
        return {"literature_summary": f"文献调研：找到 15 篇关于 '{topic}' 的高质量论文", "paper_count": 15}

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
        "properties": {"task": {"type": "string", "description": "统计分析任务描述"}},
        "required": ["task"],
    },
)

literature_researcher_card = AgentCard(
    id="literature_researcher",
    name="literature_researcher",
    description="文献调研员，检索和总结学术文献",
    input_params={
        "type": "object",
        "properties": {"topic": {"type": "string", "description": "研究主题"}},
        "required": ["topic"],
    },
)

data_analyst_card = AgentCard(
    id="data_analyst",
    name="data_analyst",
    description="数据分析员，分析数据并生成报告，可调用统计专家",
    input_params={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "分析任务，包含数据描述和分析目标"},
        },
        "required": ["query"],
    },
)

research_director_card = AgentCard(
    id="research_director",
    name="research_director",
    description="研究主管，协调研究项目，可调用文献调研员和数据分析员",
)


# ============================================================================
# 数据分析师 Provider
# ============================================================================

def _create_data_analyst_provider():
    """直接使用 SupervisorAgent.create，无需 patched_invoke。
    data_analyst_card.input_params 已定义为 {"query": ...}，
    LLM 生成的 tool_call 直接带 query 字段，ReActAgent.invoke 可直接提取。
    """
    _card, _provider_fn = SupervisorAgent.create(
        agents=[statistics_expert_card],
        model_client_config=model_client_config,
        model_request_config=model_request_config,
        agent_card=data_analyst_card,
        system_prompt="你是数据分析员，负责分析数据。可以调用统计专家进行深度统计分析。",
        max_iterations=5,
        max_parallel_sub_agents=5,
    )

    def provider():
        return _provider_fn()

    return provider


# ============================================================================
# 主函数
# ============================================================================

async def main():
    print("=" * 80)
    print("三层层次化研究团队示例 - Hierarchical MessageBus 模式")
    print("=" * 80)

    statistics_expert = StatisticsExpert(card=statistics_expert_card)
    literature_researcher = LiteratureResearcher(card=literature_researcher_card)

    research_director_card_supervisor, research_director_provider_fn = SupervisorAgent.create(
        agents=[literature_researcher_card, data_analyst_card],
        model_client_config=model_client_config,
        model_request_config=model_request_config,
        agent_card=research_director_card,
        system_prompt="你是研究主管，负责协调整个研究项目。可以调用文献调研员检索文献，调用数据分析员分析数据。请根据用户需求合理分配任务。\n重要：调用数据分析员时，必须将所有分析需求（数据描述和分析目标）组合为一个 query 字符串，格式示例：'数据描述：xxx。分析目标：yyy。' 直接传入 query 参数，不要分开传。",
        max_iterations=5,
        max_parallel_sub_agents=1,
    )

    def research_director_provider():
        return research_director_provider_fn()

    team_card = TeamCard(
        id="research_team",
        name="research_team",
        description="三层研究团队",
    )
    team_config = HierarchicalTeamConfig(supervisor_agent=research_director_card)
    team = HierarchicalTeam(card=team_card, config=team_config)

    def statistics_expert_provider():
        return statistics_expert

    def literature_researcher_provider():
        return literature_researcher

    team.add_agent(research_director_card, research_director_provider)
    team.add_agent(literature_researcher_card, literature_researcher_provider)
    team.add_agent(data_analyst_card, _create_data_analyst_provider())
    team.add_agent(statistics_expert_card, statistics_expert_provider)

    print("\n任务：研究人工智能在医疗诊断中的应用\n")
    result = await team.invoke(
        {"query": "请研究人工智能在医疗诊断中的应用，包括文献调研和数据分析"}
    )
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
