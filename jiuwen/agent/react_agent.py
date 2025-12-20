#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""ReActAgent"""
import asyncio
from typing import Dict, Any, List, AsyncIterator

from jiuwen.agent.common.enum import ControllerType
from jiuwen.agent.common.schema import WorkflowSchema, PluginSchema
from jiuwen.agent.config.react_config import ReActAgentConfig
from jiuwen.core.agent.agent import Agent
from jiuwen.core.agent.controller.react_controller import ReActController
from jiuwen.core.agent.handler.base import AgentHandlerImpl
from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.common.logging import logger
from jiuwen.core.component.common.configs.model_config import ModelConfig
from jiuwen.core.runtime.config import Config
from jiuwen.core.runtime.runtime import Runtime
from jiuwen.core.context_engine.engine import ContextEngine
from jiuwen.core.utils.config.user_config import UserConfig
from jiuwen.core.utils.tool.base import Tool
from jiuwen.core.context_engine.config import ContextEngineConfig
from jiuwen.core.workflow.base import Workflow



def create_react_agent_config(agent_id: str,
                              agent_version: str,
                              description: str,
                              workflows: List[WorkflowSchema],
                              plugins: List[PluginSchema],
                              model: ModelConfig,
                              prompt_template: List[Dict]):
    config = ReActAgentConfig(id=agent_id,
                              version=agent_version,
                              description=description,
                              workflows=workflows,
                              plugins=plugins,
                              model=model,
                              prompt_template=prompt_template)
    return config


def create_react_agent(agent_config: ReActAgentConfig,
                       workflows: List[Workflow] = None,
                       tools: List[Tool] = None):
    agent = ReActAgent(agent_config)
    agent.bind_workflows(workflows)
    agent.bind_tools(tools)
    return agent


class ReActAgent(Agent):
    def __init__(self, agent_config: ReActAgentConfig):
        self._config = Config()
        self._config.set_agent_config(agent_config=agent_config)
        super().__init__(self._config)
        self.context_engine = self._create_context_engine()


    def _init_controller(self):
        if self._config.get_agent_config().controller_type != ControllerType.ReActController:
            raise NotImplementedError("")
        return None

    def _init_agent_handler(self):
        return AgentHandlerImpl(self._config.get_agent_config())

    def _create_context_engine(self) -> ContextEngine:
        context_config = ContextEngineConfig(
            conversation_history_length=self._config.get_agent_config().constrain.reserved_max_chat_rounds * 2
        )
        return ContextEngine(
            agent_id=self._config.get_agent_config().id,
            config=context_config,
            model=None
        )

    def _create_controller(self, context_engine: ContextEngine, runtime: Runtime) -> ReActController:
        controller = ReActController(
            self._config.get_agent_config(),
            context_engine,
            runtime
        )
        controller.set_agent_handler(self._agent_handler)
        return controller

    async def invoke(self, inputs: Dict) -> Dict:
        # 1. Initialize ContextEngine and Runtime
        session_id = inputs.pop("conversation_id", "default_session")
        runtime = await self._runtime.pre_run(session_id=session_id)

        # 2. Create the Controller
        controller = self._create_controller(self.context_engine, runtime)

        # 3. Execute the ReAct process
        result = await controller.execute(inputs)
        await runtime.post_run()
        return result

    async def stream(self, inputs: Dict) -> AsyncIterator[Any]:
        # 1. Initialize ContextEngine and Runtime
        session_id = inputs.pop("conversation_id", "default_session")
        runtime = await self._runtime.pre_run(session_id=session_id)

        # 2. Create the Controller
        controller = self._create_controller(self.context_engine, runtime)

        async def stream_process():
            try:
                await controller.execute(inputs)
            except Exception as e:
                if UserConfig.is_sensitive():
                    logger.info(f"ReActAgent stream error.")
                else:
                    logger.error(f"ReActAgent stream error: {e}")
            finally:
                await runtime.post_run()

        task = asyncio.create_task(stream_process())
        # 3. Execute the streaming ReAct process
        async for result in runtime.stream_iterator():
            yield result

        try:
            await task
        except Exception as e:
            logger.error(f"ReActAgent stream error.")
            if UserConfig.is_sensitive():
                raise JiuWenBaseException(StatusCode.AGENT_SUB_TASK_TYPE_ERROR.code,
                                          "ReActAgent stream error.")
            else:
                raise JiuWenBaseException(StatusCode.AGENT_SUB_TASK_TYPE_ERROR.code,
                                          "ReActAgent stream error.") from e

    async def close_session(self, session_id: str):
        await self._runtime.release(session_id=session_id)
