#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Union, Any, Dict, AsyncIterator
import json

from jiuwen.agent.common.enum import SubTaskType
from jiuwen.agent.config.base import AgentConfig
from jiuwen.core.agent.controller.base import Controller
from jiuwen.core.agent.controller.utils import WorkflowControllerOutput, WorkflowControllerInput
from jiuwen.core.agent.handler.base import AgentHandler, AgentHandlerInputs
from jiuwen.core.agent.task.sub_task import SubTask
from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.logging import logger
from jiuwen.core.runtime.workflow_manager import generate_workflow_key
from jiuwen.core.context_engine.engine import ContextEngine
from jiuwen.core.runtime.interaction.base import AgentInterrupt
from jiuwen.core.runtime.interaction.interactive_input import InteractiveInput
from jiuwen.core.runtime.runtime import Runtime
from jiuwen.core.utils.common.json_utils import JsonUtils
from jiuwen.core.utils.config.user_config import UserConfig
from jiuwen.core.utils.llm.messages import HumanMessage, AIMessage
from jiuwen.core.utils.llm.messages_chunk import BaseMessageChunk
from jiuwen.core.workflow.base import Workflow, WorkflowOutput, WorkflowExecutionState
from jiuwen.core.stream.base import OutputSchema, CustomSchema, TraceSchema
from jiuwen.core.common.constants.constant import INTERACTION


class WorkflowState:

    def __init__(self, runtime: Runtime):
        self._runtime = runtime

    def is_interrupted(self) -> bool:
        track_state = self._runtime.get_state("workflow_state")
        return (track_state and
                track_state.get("status") == "interrupted" and
                track_state.get("sub_tasks") is not None)

    def get_interrupted_sub_tasks(self) -> List[SubTask]:
        state_data = self._runtime.get_state("workflow_state") or {}
        return state_data.get("sub_tasks", [])

    def save_interrupt_state(self, sub_tasks: List[SubTask]):
        self._runtime.update_state({
            "workflow_state": {
                "status": "interrupted",
                "sub_tasks": sub_tasks
            }
        })

    def get_current_status(self) -> str:
        track_state = self._runtime.get_state("workflow_state")
        if not track_state:
            return "normal"
        return track_state.get("status", "normal")

    def set_status(self, status: str, sub_tasks: List[SubTask] = None):
        self._runtime.update_state({
            "workflow_state": {
                "status": status,
                "sub_tasks": sub_tasks or []
            }
        })


class WorkflowController(Controller):
    def __init__(self, config: AgentConfig, context_engine: ContextEngine, runtime: Runtime):
        super().__init__(config)
        self._context_engine = context_engine
        self._runtime = runtime
        self._state = WorkflowState(runtime)
        self._agent_handler = None

    def set_agent_handler(self, agent_handler: AgentHandler):
        self._agent_handler = agent_handler

    @staticmethod
    def _filter_inputs(schema: dict, user_data: dict) -> dict:
        """
        Filter and validate user input according to the schema
        :param schema: The schema of workflow.inputs, in the form of {"query": {"type": "string", "required": True}}
        :param user_data: The actual data passed in by the user, in the form of {"query": "Hello", "foo": "bar"}
        :return: Only retain the fields declared in the schema
        :raises KeyError: Raised when a required field is missing
        """
        if not schema:
            return {}

        required_fields = {
            k for k, v in schema.items()
            if isinstance(v, dict) and v.get("required") is True
        }

        filtered = {}
        for k in schema:
            if k not in user_data:
                if k in required_fields:
                    raise KeyError(f"missing required parameter: {k}")
                continue
            filtered[k] = user_data[k]

        return filtered

    def _find_workflow(self, inputs: AgentHandlerInputs) -> Workflow:
        context = inputs.context
        workflow_name = inputs.name
        workflow_metadata = self._agent_handler.search_workflow_metadata_by_workflow_name(workflow_name)
        workflow_id = generate_workflow_key(workflow_metadata.id, workflow_metadata.version)
        workflow = context.get_workflow(workflow_id)
        return workflow

    def _add_msg_to_chat_histroy(self, message: Union[HumanMessage, AIMessage]):
        workflow_context = self._context_engine.get_workflow_context(workflow_id=self._config.workflows[0].id,
                                                                     session_id=self._runtime.session_id())
        workflow_context.add_message(message)

    def invoke(
            self, inputs: Dict, context
    ) -> WorkflowControllerOutput:
        if len(self._config.workflows) != 1:
            raise NotImplementedError("Multi-workflow not implemented yet")

        workflow = self._config.workflows[0]

        filtered_inputs = self._filter_inputs(
            schema=workflow.inputs or {},
            user_data=inputs
        )

        sub_tasks = [
            SubTask(
                sub_task_type=SubTaskType.WORKFLOW,
                func_name=workflow.name,
                func_id=f"{workflow.id}_{workflow.version}",
                func_args=filtered_inputs,
            )
        ]

        user_message = HumanMessage(content=inputs.get("query"))
        self._add_msg_to_chat_histroy(user_message)
        if UserConfig.is_sensitive():
            logger.info(f"Added user message to chat history")
        else:
            logger.info(f"Added user message to chat history: {inputs.get('query')}")

        return WorkflowControllerOutput(is_task=True, sub_tasks=sub_tasks)

    async def stream(self,
                     inputs: WorkflowControllerInput,
                     context: Runtime
                     ) -> AsyncIterator[Union[BaseMessageChunk, WorkflowControllerOutput]]:
        pass

    def should_continue(self, output: WorkflowControllerOutput) -> bool:
        return not output.is_task

    def handle_workflow_results(self, results):
        if self._config.is_single_workflow:
            return results[self._config.workflows[0].name]
        raise Exception("Multi-workflow not implemented yet")

    @staticmethod
    def _validate_inputs(inputs: Dict):
        if isinstance(inputs.get("query"), InteractiveInput):
            raise JiuWenBaseException(5000, "Non-interrupt status data format error.")

    async def execute(self, inputs: Dict) -> Dict | list:
        if UserConfig.is_sensitive():
            logger.info("Starting Workflow execution with inputs.")
        else:
            logger.info(f"Starting Workflow execution with inputs: {inputs}")

        if not self._state.is_interrupted():
            self._validate_inputs(inputs)

        if self._state.is_interrupted():
            return await self._resume_task(inputs)

        return await self._run_workflow(inputs)

    async def _resume_task(self, inputs: Dict) -> Dict | list:
        if not isinstance(inputs.get("query"), InteractiveInput):
            raise JiuWenBaseException(5000, "Interrupt status data format error.")
        if UserConfig.is_sensitive():
            logger.info(f"Processing interrupt recovery")
        else:
            logger.info(f"Processing interrupt recovery: {inputs}")

        sub_tasks = self._state.get_interrupted_sub_tasks()
        if not sub_tasks:
            self._state.set_status("normal")
            return await self._run_workflow(inputs)

        sub_tasks[0].func_args = inputs.get("query", "")

        stream_flag = bool(inputs.get("stream"))
        result = await self._execute_workflow_task(sub_tasks[0], stream=stream_flag)

        if result and hasattr(result, 'state') and result.state.value == "INPUT_REQUIRED":
            interrupt_data_list = []
            for output_scheme in result.result:
                interrupt_data_list.append(output_scheme)
            return interrupt_data_list
        else:
            self._state.set_status("normal")
            final_result = self.handle_workflow_results({sub_tasks[0].func_name: result})
            return {"output": final_result, "result_type": "answer"}

    async def _run_workflow(self, inputs: Dict) -> Dict | list:
        controller_output: WorkflowControllerOutput = self.invoke(inputs, None)

        if not controller_output.sub_tasks:
            return {"output": "No tasks to execute", "result_type": "answer"}

        sub_task = controller_output.sub_tasks[0]
        stream_flag = bool(inputs.get("stream"))
        result = await self._execute_workflow_task(sub_task, stream=stream_flag)

        if result and hasattr(result, 'state') and result.state.value == "INPUT_REQUIRED":
            self._state.save_interrupt_state([sub_task])
            interrupt_data_list = []
            for output_scheme in result.result:
                interrupt_data_list.append(output_scheme)
            return interrupt_data_list
        else:
            final_result = self.handle_workflow_results({sub_task.func_name: result})
            return {"output": final_result, "result_type": "answer"}

    async def _execute_workflow_task(self, sub_task: SubTask, stream: bool = False) -> Any:
        try:
            inputs = AgentHandlerInputs(
                context=self._runtime,
                name=sub_task.func_name,
                arguments=sub_task.func_args
            )
            workflow = self._find_workflow(inputs)

            workflow_runtime = inputs.context.create_workflow_runtime()
            if not stream:
                result = await workflow.invoke(inputs.arguments, workflow_runtime)
            else:
                last_final_result = None
                interaction_chunks = []
                async for chunk in workflow.stream(inputs.arguments, workflow_runtime):
                    if isinstance(chunk, OutputSchema):
                        await self._runtime.write_stream(chunk)
                    elif isinstance(chunk, TraceSchema):
                        await self._runtime.write_custom_stream(chunk)
                    elif isinstance(chunk, CustomSchema):
                        await self._runtime.write_custom_stream(chunk)
                    if isinstance(chunk, OutputSchema) and chunk.type == "workflow_final":
                        last_final_result = chunk.payload
                    if isinstance(chunk, OutputSchema) and chunk.type == INTERACTION:
                        interaction_chunks.append(chunk)

                if last_final_result is not None:
                    result = WorkflowOutput(result=last_final_result, state=WorkflowExecutionState.COMPLETED)
                elif interaction_chunks:
                    result = WorkflowOutput(result=interaction_chunks, state=WorkflowExecutionState.INPUT_REQUIRED)
                else:
                    result = WorkflowOutput(result=None, state=WorkflowExecutionState.COMPLETED)

            if hasattr(result, 'result') and hasattr(result, 'state'):
                # For WorkflowOutput, store the complete object to facilitate subsequent status processing.
                sub_task.result = result
                return result
            else:
                # For other objects, attempt serialization; if it fails, store them directly.
                sub_task.result = JsonUtils.safe_json_dumps(result, result, ensure_ascii=False)
                return result

        except AgentInterrupt as e:
            if UserConfig.is_sensitive():
                error_msg = f"Tool execution failed"
                logger.info(f"Sub task {sub_task.func_name} failed.")
            else:
                error_msg = f"Tool execution failed: {str(e)}"
                logger.error(f"Sub task {sub_task.func_name} failed: {error_msg}")

            error_result = {
                "error": True,
                "id": "0",
                "value": e.message,
                "message": error_msg,
                "tool_name": sub_task.func_name
            }
            sub_task.result = JsonUtils.safe_json_dumps(error_result, ensure_ascii=False)
            return error_result
