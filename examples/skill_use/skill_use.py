#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
import asyncio
from pathlib import Path

from dotenv import load_dotenv
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig


async def main():
    # 获取配置项
    load_dotenv()

    skills_dir = Path(os.getenv("SKILLS_DIR", "")).expanduser().resolve()
    files_base_dir = os.getenv("FILES_BASE_DIR", str(Path(__file__).resolve().parent))
    out_put_dir = os.getenv("OUTPUT_DIR", "")
    max_iterations = int(os.getenv("MAX_ITERATIONS", "40"))

    api_base = os.getenv("API_BASE", "")
    api_key = os.getenv("API_KEY", "")
    model_name = os.getenv("MODEL_NAME", "")
    model_provider = os.getenv("MODEL_PROVIDER", "")
    verify_ssl = os.getenv("LLM_SSL_VERIFY", "False")

    # 构建agent对象
    agent = ReActAgent(card=AgentCard(name="skill_agent", description="Skill Agent"))

    # 创建并设置agent配置项
    system_prompt = (
        "You are an intelligent assistant.\n"
        f"All user-provided files are located at '{files_base_dir}'\n"
        f"Put all generated files into {out_put_dir}\n"
        "You may use tools when necessary.\n"
    )

    sysop_card = SysOperationCard(
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=None),
    )
    Runner.resource_mgr.add_sys_operation(sysop_card)

    cfg = (ReActAgentConfig()
           .configure_model_client(
                provider=model_provider,
                api_key=api_key,
                api_base=api_base,
                model_name=model_name,
                verify_ssl=verify_ssl,
            )
           .configure_prompt_template([{"role": "system", "content": system_prompt}])
           .configure_max_iterations(max_iterations)
           .configure_context_engine(
                max_context_message_num=None,
                default_window_round_num=None
           )
        )
    cfg.sys_operation_id = sysop_card.id
    agent.configure(cfg)

    #1）read_file_tool
    read_file_card = Runner.resource_mgr.get_sys_op_tool_cards(sys_operation_id=sysop_card.id,
                                                          operation_name="fs",
                                                          tool_name="read_file")
    agent.ability_manager.add(read_file_card)

    #2)execute_code
    execute_code_card = Runner.resource_mgr.get_sys_op_tool_cards(sys_operation_id=sysop_card.id,
                                                          operation_name="code",
                                                          tool_name="execute_code")
    agent.ability_manager.add(execute_code_card)

    #3)execute_cmd
    execute_cmd_card = Runner.resource_mgr.get_sys_op_tool_cards(sys_operation_id=sysop_card.id,
                                                          operation_name="shell",
                                                          tool_name="execute_cmd")
    agent.ability_manager.add(execute_cmd_card)

    # 为agent添加skills
    if skills_dir.exists():
        await agent.register_skill(str(skills_dir))

    # 运行agent
    query = "处理用户提供的所有的pdf发票，并生成一份详细的xlsx报表"

    res = await Runner.run_agent(
        agent=agent,
        inputs={"query": query, "conversation_id": "013"},
    )
    logger.info(res.get("output", res))


if __name__ == "__main__":
    asyncio.run(main())
