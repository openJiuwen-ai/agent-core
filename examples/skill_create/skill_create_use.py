#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SkillCreator 使用示例 - LLM 智能生成和优化

演示如何使用 SkillCreator 结合 LLM 生成和优化 Skill
支持通过 mode 参数控制是创建新 Skill 还是优化现有 Skill
"""

import asyncio

from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.skill_creator import SkillCreator
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)


async def generate_skill(
    creator: SkillCreator,
        *,
    mode: str = "create",
    name: str = None,
    description: str = None,
    output_path: str = "./skills",
    skill_type: str = "workflow",
    skill_path: str = None,
    optimization_direction: str = None,
    auto_apply: bool = True,
):
    """创建或优化 Skill
    
    Args:
        creator: SkillCreator 实例
        mode: 操作模式，"create" 创建新 Skill，"optimize" 优化现有 Skill
        name: Skill 名称（create 模式必填）
        description: Skill 描述（create 模式必填）
        output_path: 输出路径（create 模式使用）
        skill_type: Skill 类型（create 模式使用）
        skill_path: Skill 路径（optimize 模式必填）
        optimization_direction: 优化方向（optimize 模式可选，为空则全面优化）
        auto_apply: 是否自动应用更改（optimize 模式使用）
    """
    if mode == "create":
        result = await creator.generate(
            mode="create",
            name=name,
            description=description,
            output_path=output_path,
            skill_type=skill_type,
        )
        logger.info(f"=== 创建 {name} Skill ===")
        logger.info(f"metadata: {result.metadata}")
        logger.info(f"body: {result.body}")
    elif mode == "optimize":
        if optimization_direction:
            result = await creator.generate(
                mode="optimize",
                skill_path=skill_path,
                optimization_direction=optimization_direction,
                auto_apply=auto_apply,
            )
            logger.info("=== 指定方向优化 ===")
        else:
            result = await creator.generate(
                mode="optimize",
                skill_path=skill_path,
                auto_apply=auto_apply,
            )
            logger.info("=== 默认全面优化 ===")
        logger.info(f"Changes: {result.changes}")
    else:
        raise ValueError(f"不支持的模式: {mode}，请使用 'create' 或 'optimize'")
    
    return result


async def main():
    # 配置模型客户端
    client_config = ModelClientConfig(
        client_provider="SiliconFlow",
        api_key="sk-api",  # 替换为你的 API Key
        api_base="siliconflow-api",
        verify_ssl=False,
    )

    # 配置模型请求参数
    request_config = ModelRequestConfig(
        model="Pro/zai-org/GLM-4.7",
        temperature=0.7,
    )

    # 创建 SkillCreator 实例
    creator = SkillCreator(
        model_client_config=client_config,
        model_request_config=request_config,
    )

    # 创建 Skill 示例 - 财务管理
    # await generate_skill(
    #     creator=creator,
    #     mode="create",
    #     name="financial_management111111",
    #     description="Help users with budgeting, accounting, investment planning, and debt management.",
    #     output_path="./skills",
    #     skill_type="workflow",
    # )

    # 创建 Skill 示例 - 数据分析
    # await generate_skill(
    #     creator=creator,
    #     mode="create",
    #     name="data_analysis",
    #     description="You can perform data analysis and summarization on Word, Excel, and PDF files.",
    #     output_path="./skills",
    #     skill_type="workflow",
    # )

    # 运行默认全面优化示例
    # await generate_skill(
    #     creator=creator,
    #     mode="optimize",
    #     skill_path="./skills1/data_analysis",
    # )

    # 运行指定方向优化示例
    await generate_skill(
        creator=creator,
        mode="optimize",
        skill_path="./skills/financial_management111111",
        optimization_direction="对于ppt的文件也要能进行总结",
    )


if __name__ == "__main__":
    asyncio.run(main())
