# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
客服系统示例 - Handoff 模式

Agent 流程：
    Triage Agent (分流) → Technical Support (技术支持) / Billing Support (账单支持)

每个 Agent 可以：
1. 完成任务并返回结果
2. 转交给其他 Agent（通过 transfer_to_xxx 工具）
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

import asyncio

from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.multi_agent.teams.handoff import (
    HandoffTeam,
    HandoffTeamConfig,
    HandoffConfig,
    HandoffRoute,
)
from openjiuwen.core.multi_agent.schema.team_card import TeamCard
from openjiuwen.core.single_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

model_client_config = ModelClientConfig(
    client_id="openai",
    client_provider="openai",
    api_key="your api key",
    api_base="your api base",
    verify_ssl=False
)

model_request_config = ModelRequestConfig(
    model="your model",
    temperature=0.7,
)


# ============================================================================
# Agent 定义
# ============================================================================

class TriageAgent(ReActAgent):
    """分流 Agent：分析用户问题并转交给合适的专员"""
    pass


class TechnicalSupportAgent(ReActAgent):
    """技术支持 Agent：处理技术问题"""
    pass


class BillingSupportAgent(ReActAgent):
    """账单支持 Agent：处理账单和付款问题"""
    pass


# ============================================================================
# AgentCard 定义
# ============================================================================

triage_card = AgentCard(
    id="triage_agent",
    name="triage_agent",
    description="分流客服，分析用户问题类型并转交给技术支持或账单支持"
)

technical_support_card = AgentCard(
    id="technical_support",
    name="technical_support",
    description="技术支持专员，处理产品使用、故障排查等技术问题"
)

billing_support_card = AgentCard(
    id="billing_support",
    name="billing_support",
    description="账单支持专员，处理账单查询、付款、退款等问题"
)


async def main():
    print("=" * 80)
    print("客服系统示例 - Handoff 模式")
    print("=" * 80)

    # 1. 创建 Agent 实例并配置
    triage_config = ReActAgentConfig(
        model_config_obj=model_request_config,
        model_client_config=model_client_config,
        prompt_template=[{
            "role": "system",
            "content": (
                "你是客服分流专员。分析用户问题：\n"
                "- 技术问题（产品使用、故障、功能）→ 转交 technical_support\n"
                "- 账单问题（付款、退款、发票）→ 转交 billing_support\n"
                "- 简单问候或感谢 → 直接回复\n"
                "使用 transfer_to_xxx 工具转交任务。"
            )
        }]
    )
    triage_agent = TriageAgent(card=triage_card).configure(triage_config)

    technical_config = ReActAgentConfig(
        model_config_obj=model_request_config,
        model_client_config=model_client_config,
        prompt_template=[{
            "role": "system",
            "content": (
                "你是技术支持专员，负责解决产品技术问题。"
                "提供详细的故障排查步骤和解决方案。"
                "如果问题超出技术范围（如账单问题），转交 billing_support。"
            )
        }]
    )
    technical_support = TechnicalSupportAgent(card=technical_support_card).configure(technical_config)

    billing_config = ReActAgentConfig(
        model_config_obj=model_request_config,
        model_client_config=model_client_config,
        prompt_template=[{
            "role": "system",
            "content": (
                "你是账单支持专员，负责处理账单、付款、退款问题。"
                "提供清晰的账单说明和付款指引。"
                "如果问题是技术问题，转交 technical_support。"
            )
        }]
    )
    billing_support = BillingSupportAgent(card=billing_support_card).configure(billing_config)

    # 2. 创建 HandoffTeam 并配置路由
    team_card = TeamCard(
        id="customer_service_team",
        name="customer_service_team",
        description="客服团队"
    )

    # 配置 handoff 路由规则
    handoff_config = HandoffConfig(
        start_agent=triage_card,  # 从分流 Agent 开始
        max_handoffs=5,  # 最多转交 5 次
        routes=[
            # 分流 Agent 可以转交给技术支持或账单支持
            HandoffRoute(source="triage_agent", target="technical_support"),
            HandoffRoute(source="triage_agent", target="billing_support"),
            # 技术支持和账单支持可以互相转交
            HandoffRoute(source="technical_support", target="billing_support"),
            HandoffRoute(source="billing_support", target="technical_support"),
        ]
    )

    team_config = HandoffTeamConfig(handoff=handoff_config)
    team = HandoffTeam(card=team_card, config=team_config)

    # 3. 注册 Agent 到团队
    team.add_agent(triage_card, lambda: triage_agent)
    team.add_agent(technical_support_card, lambda: technical_support)
    team.add_agent(billing_support_card, lambda: billing_support)

    # 4. 测试不同类型的用户问题
    test_cases = [
        "我的账单怎么这么贵？能帮我查一下吗？",
        "产品登录不上去，一直显示网络错误",
        "你好，感谢你们的服务！",
    ]

    for i, query in enumerate(test_cases, 1):
        print(f"\n{'=' * 80}")
        print(f"测试案例 {i}: {query}")
        print("=" * 80)

        result = await team.invoke({"query": query})

        print(f"\n最终结果：")
        print(result)
        print()


if __name__ == "__main__":
    asyncio.run(main())
