#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Controller of ReActAgent"""
from typing import List, Dict, Optional, Union, Any
import json

from jiuwen.core.agent.controller.base import Controller
from jiuwen.core.agent.handler.base import AgentHandler, AgentHandlerInputs
from jiuwen.agent.config.base import AgentConfig
from jiuwen.core.agent.task.sub_task import SubTask
from jiuwen.core.runtime.interaction.base import AgentInterrupt
from jiuwen.core.runtime.runtime import Runtime
from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.context_engine.engine import ContextEngine
from jiuwen.core.stream.base import OutputSchema
from jiuwen.core.utils.common.json_utils import JsonUtils
from jiuwen.core.utils.llm.hash_util import generate_key
from jiuwen.core.utils.llm.model_utils.model_factory import ModelFactory
from jiuwen.core.runtime.interaction.interactive_input import InteractiveInput
from jiuwen.core.common.logging import logger
from jiuwen.core.agent.controller.utils import ReActControllerUtils, ReActControllerOutput, ReActControllerInput
from jiuwen.agent.common.enum import ReActControllerStatus
from jiuwen.core.utils.config.user_config import UserConfig


class ReActState:

    def __init__(self, runtime: Runtime):
        self._runtime = runtime

    def is_interrupted(self) -> bool:
        track_state = self._runtime.get_state("react_state")
        return (track_state and
                track_state.get("status") == ReActControllerStatus.INTERRUPTED.value and
                track_state.get("sub_tasks") is not None)

    def get_interrupted_sub_tasks(self) -> List[SubTask]:
        state_data = self._runtime.get_state("react_state") or {}
        return state_data.get("sub_tasks", [])

    def save_interrupt_state(self, sub_tasks: List[SubTask]):
        self._runtime.update_state({
            "react_state": {
                "status": ReActControllerStatus.INTERRUPTED.value,
                "sub_tasks": sub_tasks
            }
        })

    def get_current_status(self) -> ReActControllerStatus:
        track_state = self._runtime.get_state("react_state")
        if not track_state:
            return ReActControllerStatus.NORMAL

        status_value = track_state.get("status", ReActControllerStatus.NORMAL.value)
        try:
            return ReActControllerStatus(status_value)
        except ValueError:
            return ReActControllerStatus.NORMAL

    def set_status(self, status: ReActControllerStatus, sub_tasks: List[SubTask] = None):
        self._runtime.update_state({
            "react_state": {
                "status": status.value,
                "sub_tasks": sub_tasks or []
            }
        })


class ReActController(Controller):
    """Reason→Act→Observe→Decide"""

    def __init__(self, config: AgentConfig, context_engine: ContextEngine, runtime: Runtime):
        super().__init__(config)
        self._context_engine = context_engine
        self._runtime = runtime
        self._model = self._init_model()

        self._state = ReActState(runtime)
        self._agent_handler = None

    def _init_model(self):
        model_id = generate_key(
            self._config.model.model_info.api_key,
            self._config.model.model_info.api_base,
            self._config.model.model_provider
        )

        model = self._runtime.get_model(model_id=model_id)

        if model is None:
            model = ModelFactory().get_model(
                model_provider=self._config.model.model_provider,
                api_base=self._config.model.model_info.api_base,
                api_key=self._config.model.model_info.api_key
            )
            self._runtime.add_model(model_id=model_id, model=model)

        return self._runtime.get_model(model_id=model_id)

    @staticmethod
    def _validate_inputs(inputs: Dict):
        if isinstance(inputs.get("query"), InteractiveInput):
            raise JiuWenBaseException(5000, "Non-interrupt status data format error.")

    def set_agent_handler(self, agent_handler: AgentHandler):
        self._agent_handler = agent_handler

    async def execute(self, inputs: Dict) -> Dict:
        if UserConfig.is_sensitive():
            logger.info(f"Starting ReAct execution.")
        else:
            logger.info(f"Starting ReAct execution with inputs: {inputs}")

        if not self._state.is_interrupted():
            self._validate_inputs(inputs)

        return await self._run_react_loop(inputs)

    async def _run_react_loop(self, inputs: Dict) -> Dict | list:
        """Reason→Act→Observe→Decide"""

        for iteration in range(self._config.constrain.max_iteration):
            logger.info(f"ReAct iteration {iteration + 1}")

            # Check whether interrupt recovery needs to be handled.
            if self._state.is_interrupted():
                interrupt_data = await self._resume_task(inputs)
                if interrupt_data is not None:
                    return interrupt_data
                # After the interrupt recovery is completed, proceed to the next iteration to summarize the reasons.
                continue

            # 1. Reason: LLM inference generation plan
            plan_result = await self.reason(inputs)

            # 2. Decide: Determine whether to continue
            if not plan_result.should_continue:
                self._state.set_status(ReActControllerStatus.COMPLETED)
                final_result = {"output": plan_result.llm_output.content, "result_type": "answer"}
                await self._runtime.write_stream(OutputSchema(type="answer", index=0, payload=final_result))
                return final_result

            # 3. Act: Execute tool call
            completed_tasks, exec_result = await self.act(plan_result.sub_tasks)

            # 4. Observe: Observe the results and update the history
            interrupt_data = await self.observe(completed_tasks, exec_result)
            if interrupt_data is not None:
                return interrupt_data

        # Set the timeout status and return the timeout result.
        self._state.set_status(ReActControllerStatus.TIMEOUT)
        timeout_result = {"output": "执行超过最大迭代次数", "result_type": "answer"}
        await self._runtime.write_stream(OutputSchema(type="answer", index=0, payload=timeout_result))
        return timeout_result

    async def _resume_task(self, inputs: Dict) -> Optional[Dict]:
        """Resume the Interrupted Task

        Args:
            inputs: Input data, including InteractiveInput

        Returns:
            If the task is completed and requires returning a result, return a result dictionary; otherwise, return None to continue the loop.
        """
        if not isinstance(inputs.get("query"), InteractiveInput):
            raise JiuWenBaseException(5000, "Interrupt status data format error.")
        if UserConfig.is_sensitive():
            logger.info(f"Processing interrupt recovery within ReAct loop")
        else:
            logger.info(f"Processing interrupt recovery within ReAct loop: {inputs}")

        for _, query in inputs.get("query").user_inputs.items():
            ReActControllerUtils.add_user_message(query, self._context_engine, self._runtime)

        sub_tasks = self._state.get_interrupted_sub_tasks()
        if not sub_tasks:
            self._state.set_status(ReActControllerStatus.NORMAL)
            return None

        sub_tasks[0].func_args = inputs.get("query", "")

        completed_tasks, exec_result = await self.act(sub_tasks)

        interrupt_data = await self.observe(completed_tasks, exec_result)

        if interrupt_data is not None:
            return interrupt_data

        self._state.set_status(ReActControllerStatus.NORMAL)
        return None

    async def act(self, sub_tasks: List[SubTask]) -> tuple[List[SubTask], Any]:
        if not sub_tasks:
            return [], None

        completed_tasks = []
        exec_result = None

        for sub_task in sub_tasks:
            try:
                inputs = AgentHandlerInputs(
                    context=self._runtime,
                    name=sub_task.func_name,
                    arguments=sub_task.func_args
                )
                exec_result = await self._agent_handler.invoke(sub_task.sub_task_type, inputs)
                sub_task.result = JsonUtils.safe_json_dumps(exec_result, ensure_ascii=False)
                completed_tasks.append(sub_task)

            except AgentInterrupt as e:
                interrupt_result = ReActControllerUtils.create_interrupt_result(e, sub_task.func_name)
                sub_task.result = interrupt_result
                exec_result = interrupt_result

                self._state.save_interrupt_state(sub_tasks)
                break

        return completed_tasks, exec_result

    async def observe(self, completed_tasks: List[SubTask], exec_result: Any = None) -> Any | None:
        if exec_result and ReActControllerUtils.is_interaction_result(exec_result):
            interrupt_data_list = []
            for output_scheme in exec_result.get("value", []):
                await self._runtime.write_stream(output_scheme)
                interrupt_data_list.append(output_scheme)
            return interrupt_data_list

        ReActControllerUtils.add_tool_results(completed_tasks, self._context_engine, self._runtime)
        return None

    async def reason(self, inputs: Union[Dict, ReActControllerInput],
                     context: Optional[Runtime] = None) -> ReActControllerOutput:
        if isinstance(inputs, dict):
            controller_input = ReActControllerInput(**inputs)
        else:
            controller_input = inputs

        if not isinstance(controller_input.query, InteractiveInput):
            ReActControllerUtils.add_user_message(controller_input.query, self._context_engine, self._runtime)

        tools = self._runtime.get_tool_info()
        chat_history = ReActControllerUtils.get_chat_history(self._context_engine, self._runtime, self._config)
        llm_inputs = ReActControllerUtils.format_llm_inputs(controller_input, chat_history, self._config)
        if UserConfig.is_sensitive():
            logger.info(f"React llm inputs")
        else:
            logger.info(f"React llm inputs: {llm_inputs}")

        try:
            response = await self._model.ainvoke(
                self._config.model.model_info.model_name,
                llm_inputs,
                tools
            )
        except Exception as e:
            raise JiuWenBaseException(
                error_code=StatusCode.INVOKE_LLM_FAILED.code,
                message=StatusCode.INVOKE_LLM_FAILED.errmsg
            ) from e

        result = ReActControllerUtils.parse_llm_output(response, self._config)
        ReActControllerUtils.add_ai_message(result.llm_output, self._context_engine, self._runtime)
        if UserConfig.is_sensitive():
            logger.info(f"React llm output")
        else:
            logger.info(f"React llm output: {result.llm_output}")
        return result
