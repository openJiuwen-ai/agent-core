# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
SkillEvaluator - Use LLM to intelligently evaluate Skills
"""
import os
import copy
from pathlib import Path
from typing import Union
from datetime import datetime

from dotenv import load_dotenv

from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.foundation.tool import tool
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.single_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig


class SkillEvaluator:
    agent: ReActAgent
    _cfg: ReActAgentConfig
    _tools: list[ToolCard]

    def __init__(self):
        self._tools = []

    async def create_agent(self):
        load_dotenv()

        skills_dir = Path(os.getenv("SKILLS_DIR", "openjiuwen/dev_tools/skill_evaluator/skills")).expanduser().resolve()
        files_base_dir = os.getenv("FILES_BASE_DIR", str(Path(__file__).resolve().parent))
        max_iterations = int(os.getenv("MAX_ITERATIONS", 25))
        _output_dir = os.getenv("OUTPUT_DIR", "")

        api_base = os.getenv("API_BASE", "")
        api_key = os.getenv("API_KEY", "")
        model_name = os.getenv("MODEL_NAME", "")
        model_provider = os.getenv("MODEL_PROVIDER", "")
        verify_ssl = os.getenv("LLM_SSL_VERIFY", "False")

        # Construct agent instance
        self.agent = ReActAgent(card=AgentCard(name="skill_evaluator_agent", description="Skill Evaluator Agent"))

        # Create system prompt
        system_prompt = (
            "You are an intelligent assistant.\n"
            f"All user-provided files are located at '{files_base_dir}'\n"
            f"Put all generated files into {_output_dir}\n"
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
        self.agent.configure(cfg)

        # Register tools
        read_file_card = Runner.resource_mgr.get_sys_op_tool_cards(
            sys_operation_id=sysop_card.id, operation_name="fs", tool_name="read_file"
        )
        execute_code_card = Runner.resource_mgr.get_sys_op_tool_cards(
            sys_operation_id=sysop_card.id, operation_name="code", tool_name="execute_code"
        )
        execute_cmd_card = Runner.resource_mgr.get_sys_op_tool_cards(
            sys_operation_id=sysop_card.id, operation_name="shell", tool_name="execute_cmd"
        )
        write_file_card = Runner.resource_mgr.get_sys_op_tool_cards(
            sys_operation_id=sysop_card.id, operation_name="fs", tool_name="write_file"
        )

        self._tools = [read_file_card, execute_code_card, execute_cmd_card, write_file_card]
        self.agent.ability_manager.add(self._tools)

        # Subagent tool — delegates subtasks to a fresh agent instance
        subagent_card = self._create_subagent_tool(
            config=cfg,
            tools=self._tools,
            default_skills_dir=str(skills_dir),
        )
        Runner.resource_mgr.add_tool(subagent_card)
        self.agent.ability_manager.add(subagent_card.card)

        # Register skills
        if skills_dir.exists():
            await self.agent.register_skill(str(skills_dir))
        else:
            raise FileNotFoundError(f"Skills directory '{skills_dir}' does not exist.")

    async def evaluate(
        self,
        skill_path: Union[str, Path],
        requirement: str = "",
        output_path: Union[str, Path, None] = None,
    ) -> dict:
        """
        Evaluate the skill located at *skill_path*.

        Args:
            skill_path:  Path to the skill folder (or SKILL.md) to be evaluated.
            output_path: Optional directory where the evaluation report is written.
                         Falls back to OUTPUT_DIR from the environment when omitted.

        Returns:
            The raw result dict produced by the Runner.
        """
        skill_path = Path(skill_path)
        report_dir = Path(output_path) if output_path else Path(self._output_dir)

        query = (
            f"Help me evaluate the skill in the '{skill_path}'.\n"
            f"Save evaluation report to '{report_dir}' foler." + requirement
        )

        res = await Runner.run_agent(
            agent=self.agent,
            inputs={"query": query, "conversation_id": "skill_eval_001"},
        )
        return res

    @staticmethod
    def _create_subagent_tool(config: ReActAgentConfig, tools: list[ToolCard], default_skills_dir: str):
        """Factory that returns a *create_subagent* tool bound to the given config."""

        @tool(
            name="create_subagent",
            description=(
                "Create and invoke a subagent to complete a specified task. "
                "The subagent loads skills from the provided skills directory and executes it. "
            ),
        )
        async def create_subagent(user_prompt: str, skills_dir: str = "default") -> str:
            """
            Args:
                user_prompt: Instruction or task description for the subagent.
                skills_dir:  Directory from which the subagent loads its skills.
                             Pass "default" to reuse the directory set at initialisation.
            Returns:
                Result string produced by the subagent.
            """
            resolved_dir = Path(default_skills_dir if skills_dir == "default" else skills_dir)
            sub_agent = ReActAgent(card=AgentCard(name="skill_evaluator_subagent", description="Subagent"))

            sysop_card = SysOperationCard(
                mode=OperationMode.LOCAL,
                work_config=LocalWorkConfig(work_dir=None),
            )
            Runner.resource_mgr.add_sys_operation(sysop_card)

            sub_cfg = copy.deepcopy(config) # inherit config settings
            sub_cfg.sys_operation_id = sysop_card.id
            sub_agent.configure(sub_cfg)
            sub_agent.ability_manager.add(tools)

            if resolved_dir.exists():
                await sub_agent.register_skill(str(resolved_dir))
            return await sub_agent.invoke(inputs=user_prompt)

        return create_subagent
