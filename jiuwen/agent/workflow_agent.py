#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from typing import Dict, Any, AsyncIterator

from jiuwen.agent.common.enum import ControllerType
from jiuwen.agent.config.workflow_config import WorkflowAgentConfig
from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.common.logging import logger
from jiuwen.core.context_engine.config import ContextEngineConfig
from jiuwen.core.context_engine.engine import ContextEngine
from jiuwen.core.agent.controller.workflow_controller import WorkflowController
from jiuwen.core.agent.agent import Agent
from jiuwen.core.agent.handler.base import AgentHandlerImpl
from jiuwen.core.runtime.config import Config
from jiuwen.core.runtime.runtime import Runtime
from jiuwen.core.utils.config.user_config import UserConfig


class WorkflowAgent(Agent):
    def __init__(self, agent_config: WorkflowAgentConfig):
        self._config = Config()
        self._config.set_agent_config(agent_config=agent_config)
        super().__init__(self._config)
        self.context_engine = self._create_context_engine()

    def _init_controller(self):
        if self._config.get_agent_config().controller_type != ControllerType.WorkflowController:
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

    def _create_controller(self, context_engine: ContextEngine, runtime: Runtime) -> WorkflowController:
        controller = WorkflowController(
            self._config.get_agent_config(),
            context_engine,
            runtime
        )
        controller.set_agent_handler(self._agent_handler)
        return controller

    async def _execute_with_controller(self, inputs: Dict, runtime: Runtime) -> Any:
        controller = self._create_controller(self.context_engine, runtime)
        return await controller.execute(inputs)

    async def invoke(self, inputs: Dict) -> Dict:
        session_id = inputs.pop("conversation_id", "default_session")
        inputs.update({"stream": False})
        runtime = await self._runtime.pre_run(session_id=session_id)
        
        try:
            result = await self._execute_with_controller(inputs, runtime)
            return result
        except JiuWenBaseException as e:
            if UserConfig.is_sensitive():
                logger.info(f"JiuWenBaseException WorkflowAgent invoke error.")
            else:
                logger.error(f"JiuWenBaseException WorkflowAgent invoke error: {e}")
            raise e
        except Exception as e:
            if UserConfig.is_sensitive():
                logger.info(f"WorkflowAgent invoke error.")
            else:
                logger.error(f"WorkflowAgent invoke error: {e}")
        finally:
            await runtime.post_run()

    async def stream(self, inputs: Dict) -> AsyncIterator[Any]:
        session_id = inputs.pop("conversation_id", "default_session")
        inputs.update({"stream": True})
        runtime = await self._runtime.pre_run(session_id=session_id)

        async def stream_process():
            try:
                await self._execute_with_controller(inputs, runtime)
            except JiuWenBaseException as e:
                if UserConfig.is_sensitive():
                    logger.info(f"JiuWenBaseException WorkflowAgent stream error.")
                else:
                    logger.error(f"JiuWenBaseException WorkflowAgent stream error: {e}")
                raise e
            except Exception as e:
                if UserConfig.is_sensitive():
                    logger.info(f"WorkflowAgent stream error.")
                else:
                    logger.error(f"WorkflowAgent stream error: {e}")
                raise JiuWenBaseException(StatusCode.AGENT_SUB_TASK_TYPE_ERROR.code,
                                          "WorkflowAgent stream error.")
            finally:
                await runtime.post_run()

        task = asyncio.create_task(stream_process())
        
        async for result in runtime.stream_iterator():
            yield result

        try:
            await task
        except Exception as e:
            logger.error(f"WorkflowAgent stream error.")
            if UserConfig.is_sensitive():
                raise JiuWenBaseException(StatusCode.AGENT_SUB_TASK_TYPE_ERROR.code,
                                          "WorkflowAgent stream error.")
            else:
                raise JiuWenBaseException(StatusCode.AGENT_SUB_TASK_TYPE_ERROR.code,
                                          "WorkflowAgent stream error.") from e

    async def close_session(self, session_id: str):
        await self._runtime.release(session_id=session_id)