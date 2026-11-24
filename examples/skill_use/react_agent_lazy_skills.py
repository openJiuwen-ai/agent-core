# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Discover and lazily load local skills with a directly configured ReActAgent."""

import argparse
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperationCard
from openjiuwen.harness.rails import SkillUseRail


def build_agent(skills_dir: Path) -> ReActAgent:
    """Configure a ReActAgent with skill discovery but no fallback shell tools."""
    sysop_card = SysOperationCard(
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=str(skills_dir)),
    )
    Runner.resource_mgr.add_sys_operation(sysop_card)
    agent = ReActAgent(card=AgentCard(name="lazy_skill_agent", description="Lazy skill discovery example"))
    config = (
        ReActAgentConfig()
        .configure_model_client(
            provider=os.getenv("MODEL_PROVIDER", ""),
            api_key=os.getenv("API_KEY", ""),
            api_base=os.getenv("API_BASE", ""),
            model_name=os.getenv("MODEL_NAME", ""),
            verify_ssl=os.getenv("LLM_SSL_VERIFY", "True"),
        )
        .configure_prompt_template([
            {"role": "system", "content": "Answer domain questions using the available skills."},
        ])
        .configure_max_iterations(10)
    )
    config.sys_operation_id = sysop_card.id
    agent.configure(config)
    return agent


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate registration without calling an LLM")
    args = parser.parse_args()
    load_dotenv()
    skills_dir = Path(os.getenv("SKILLS_DIR", Path(__file__).resolve().parent / "skills")).resolve()
    agent = build_agent(skills_dir)
    skill_rail = SkillUseRail(
        skills_dir=str(skills_dir),
        skill_mode="auto_list",
        include_tools=False,
    )
    await agent.register_rail(skill_rail)

    if args.check:
        from openjiuwen.core.single_agent.rail import AgentCallbackContext

        await skill_rail.before_invoke(AgentCallbackContext(agent=agent, inputs=None, session=None))
        tool_names = {tool.name for tool in agent.ability_manager.list() if tool is not None}
        expected_tools = {"skill_tool", "list_skill"}
        if not expected_tools.issubset(tool_names):
            raise RuntimeError(f"Skill tools were not registered: {sorted(tool_names)}")
        if not any(skill.name == "domain-guide" for skill in skill_rail.skills):
            raise RuntimeError("The example domain-guide skill was not discovered")
        logger.info("Skill discovery and lazy-loading tools registered successfully")
        return

    # Directly configured ReActAgent uses invoke(); Runner.run_agent() is not supported here.
    result = await agent.invoke(
        {"query": "Use the domain-guide skill to summarize its release checklist."},
    )
    logger.info(result.get("output", result))


if __name__ == "__main__":
    asyncio.run(main())
