#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
import sys
import shutil
import asyncio
from pathlib import Path

from dotenv import load_dotenv
from openjiuwen.core.common.logging import logger


def _create_session(session_id: str):
    """
    Create a Session
    """
    try:
        from openjiuwen.core.single_agent import create_agent_session
        return create_agent_session(session_id=session_id)
    except Exception:
        from openjiuwen.core.session.session import Session
        return Session(session_id=session_id)


async def _invoke_agent(agent, session, query: str):
    await agent.context_engine.create_context(session=session)
    res = await agent.invoke(inputs={"query": query}, session=session)
    logger.info(res.get("output", res))


async def main():
    project_root = Path(__file__).resolve().parent

    load_dotenv()

    query = (
        "Analyze SuperStoreUS-2015.xlsx file. Write the analysis results in different worksheets "
        "of the same Excel file. Do not create new Excel file,\n"
        "and ensure all numbers are dynamically generated (no hard-coded values): "
        "What is the store's total revenue?\n"
        "Which product category contributes the most to sales?\n"
        "What is the sales trend over the past year?\n"
        "Which region has the highest sales and which region has the lowest?\n"
        "What is the store's average profit margin?"
    )
    skills_dir = Path(os.getenv("SKILLS_DIR")).expanduser().resolve()
    files_base_dir = os.getenv("FILES_BASE_DIR")

    session_id = "skill_session"
    sys_operation_id = "default_sysop"
    max_iterations = int(os.getenv("MAX_ITERATIONS", "40"))

    api_base = os.getenv("API_BASE", "https://api.openai.com/v1")
    api_key = os.getenv("API_KEY", "")
    model_name = os.getenv("MODEL_NAME", "")
    model_provider = (os.getenv("MODEL_PROVIDER", "OpenAI") or "OpenAI").strip()

    system_prompt = (
        "You are an agent equipped with various skills to solve problems.\n"
        "Before attempting any task, read the relevant skill document (SKILL.md) "
        "using view_file and follow its workflow.\n"
        f"All user-provided files are located at '{files_base_dir}'\n"
    )

    from openjiuwen.core.runner.runner import Runner
    runner = Runner

    from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig

    sysop_card = SysOperationCard(
        id=sys_operation_id,
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=None),
    )

    runner.resource_mgr.add_sys_operation(sysop_card)

    from openjiuwen.core.single_agent.schema.agent_card import AgentCard
    from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig

    agent = ReActAgent(card=AgentCard(name="skill_agent", description="Skill Agent"))

    cfg = ReActAgentConfig()
    cfg.sys_operation_id = sys_operation_id
    cfg = (
        cfg.configure_model_client(
            provider=model_provider,
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
            verify_ssl=(os.getenv("LLM_SSL_VERIFY", "true").lower() != "false"),
        )
        .configure_prompt_template([{"role": "system", "content": system_prompt}])
        .configure_max_iterations(max_iterations)
        .configure_context_limit(None)
    )
    agent.configure(cfg)

    from openjiuwen.core.skills.skill_tool_kit import SkillToolKit

    toolkit = SkillToolKit(sys_operation_id)

    if hasattr(toolkit, "set_runner"):
        toolkit.set_runner(runner)

    toolkit.add_skill_tools(agent)

    session = _create_session(session_id)

    await agent.register_skill(str(skills_dir))
    await _invoke_agent(agent, session, query)


if __name__ == "__main__":
    asyncio.run(main())
