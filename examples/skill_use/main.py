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
from openjiuwen.core.skills.skill_tool_kit import SkillToolKit


async def main():
    load_dotenv()

    query = "处理用户提供的所有的pdf发票，并生成一份详细的xlsx报表"

    skills_dir = Path(os.getenv("SKILLS_DIR", "")).expanduser().resolve()
    files_base_dir = os.getenv("FILES_BASE_DIR", str(Path(__file__).resolve().parent))
    out_put_dir = os.getenv("OUTPUT_DIR", "")
    max_iterations = int(os.getenv("MAX_ITERATIONS", ""))

    api_base = os.getenv("API_BASE", "")
    api_key = os.getenv("API_KEY", "")
    model_name = os.getenv("MODEL_NAME", "")
    model_provider = os.getenv("MODEL_PROVIDER", "")
    verify_ssl = os.getenv("LLM_SSL_VERIFY", "False")

    system_prompt = (
        "You are an intelligent assistant.\n"
        f"All user-provided files are located at '{files_base_dir}'\n"
        f"Put all generated files into {out_put_dir}\n"
    )

    sysop_card = SysOperationCard(
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=None),
    )
    Runner.resource_mgr.add_sys_operation(sysop_card)

    agent = ReActAgent(card=AgentCard(name="skill_agent", description="Skill Agent"))

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

    toolkit = SkillToolKit(sysop_card.id)
    if hasattr(toolkit, "set_runner"):
        toolkit.set_runner(Runner)
    toolkit.add_skill_tools(agent)

    if skills_dir.exists():
        await agent.register_skill(str(skills_dir))

    res = await Runner.run_agent(
        agent=agent,
        inputs={"query": query, "conversation_id": "013"},
    )
    logger.info(res.get("output", res))


if __name__ == "__main__":
    asyncio.run(main())
