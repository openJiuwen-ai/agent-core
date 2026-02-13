# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, AsyncIterator, Tuple
import json

from openjiuwen.core.controller import TaskFilter, JsonDataFrame
from openjiuwen.core.controller.schema import ControllerOutputChunk, ControllerOutputPayload, EventType
from openjiuwen.core.controller.schema.event import InputEvent
from openjiuwen.core.controller.schema.task import Task, TaskStatus
from openjiuwen.core.controller.schema import TextDataFrame
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.core.controller.modules import TaskExecutor
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.foundation.llm import SystemMessage, UserMessage, AssistantMessage
from openjiuwen.core.workflow import WorkflowCard, WorkflowOutput
from openjiuwen.core.common.utils.message_utils import MessageUtils
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput


DEFAULT_SYSTEM_PROMPT = """你是一个意图分类助手，擅长判断用户的输入属于哪个分类。
当用户输入没有明确意图或者你无法判断用户输入意图时请选择 {{default_class}}。
以下是给定的意图分类列表：
{{category_list}}
{{example_content}}
请根据上述要求判断用户输入意图分类，输出要求如下：
直接以JSON格式输出分类ID，不进行任何解释。JSON格式如下：
 {"result": int}"""

DEFAULT_USER_PROMPT = """用户与助手的对话历史：
{{chat_history}}
当前输入：
{{input}}"""


def get_default_template():
    return PromptTemplate(
                content=[
                    SystemMessage(content=DEFAULT_SYSTEM_PROMPT),
                    UserMessage(content=DEFAULT_USER_PROMPT),
                ]
            )


class GeneralTaskExecutor(TaskExecutor):
    async def execute_ability(self, task_id: str, session: Session) -> AsyncIterator[ControllerOutputChunk]:
        logger.info("Executing general task: {}".format(task_id))
        yield ControllerOutputChunk(
            index=1,
            type="controller_output",
            payload=ControllerOutputPayload(
                type=EventType.TASK_COMPLETION,
                data=[TextDataFrame(type="text", text="General Task completed")]
            ),
            last_chunk=True
        )

    async def can_pause(self, task_id: str, session: Session) -> Tuple[bool, str]:
        return False, "This task cannot be paused"

    async def pause(self, task_id: str, session: Session) -> bool:
        raise RuntimeError("pause() should not be called when can_pause() returns False")

    async def can_cancel(self, task_id: str, session: Session) -> Tuple[bool, str]:
        return False, "This task cannot be cancelled"

    async def cancel(self, task_id: str, session: Session) -> bool:
        raise RuntimeError("cancel() should not be called when can_cancel() returns False")


class WorkflowTaskExecutor(TaskExecutor):
    async def _detect_workflow_via_llm(self, user_input: str, session: Session):
        tool_list = [tool.description for tool in self._ability_manager.list()]
        category_list = "分类0：意图不明\n" + "\n".join(
            f"分类{i+1}：{c}"
            for i, c in enumerate(tool_list)
        )
        chat_history = ""
        current_inputs = {}
        current_inputs.update({
            "user_prompt": DEFAULT_USER_PROMPT,
            "category_list": category_list,
            "default_class": "分类0",
            "enable_hisory": False,
            "enable_input": False,
            "example_content": ["example_content"],
            "chat_history_max_turn": 100,
            "chat_history": chat_history,
            "input": user_input,
        })
        llm_inputs = get_default_template().format(current_inputs).to_messages()
        model = await Runner.resource_mgr.get_model(model_id=self._config.intent_llm_id, session=session)
        llm_output = await model.invoke(messages=llm_inputs)
        llm_output_content = llm_output.content.strip()
        output_data = json.loads(llm_output_content)
        result = output_data.get("result", 0)
        
        # result=0 表示意图不明，result>=1 表示选择了对应的分类
        if result > 0:
            # 转换为索引：result=1 -> index=0, result=2 -> index=1, ...
            ability_index = result - 1
            abilities = self._ability_manager.list()
            
            # 检查索引是否有效
            if 0 <= ability_index < len(abilities):
                selected_ability = abilities[ability_index]
                # 检查是否是 WorkflowCard
                if isinstance(selected_ability, WorkflowCard):
                    detected_id = selected_ability.id or selected_ability.name
                    logger.info("detected workflow_id: {}".format(detected_id))
            else:
                logger.warning(
                    "result {} (index {}) is out of range, "
                    "total abilities: {}".format(result, ability_index, len(abilities))
                )

        return detected_id


    async def execute_ability(self, task_id: str, session: Session) -> AsyncIterator[ControllerOutputChunk]:
        """Execute task
        
        判断逻辑：
        1. 如果 workflow_id 不存在 → 新任务，需要 LLM 检测
        2. 如果 workflow_id 存在：
           - 如果 input_required_fields 存在 → 恢复中断任务
           - 如果 input_required_fields 不存在 → 状态异常，报错
        """
        tasks = await self._task_manager.get_task(TaskFilter(task_id=task_id))
        task = tasks[0]
        logger.info(f"begin to execute task: {task}")
        
        # 检查 workflow_id
        has_workflow_id = task.extensions and task.extensions.get("workflow_id")
        
        if not has_workflow_id:
            # 新任务：需要 LLM 检测工作流
            logger.info(f"New task without workflow_id, will detect via LLM")
            async for chunk in self._execute_new_workflow(task, session):
                yield chunk
        else:
            # workflow_id 已存在，检查是否是恢复任务
            if task.input_required_fields is not None:
                # 恢复中断任务
                logger.info(f"Resuming interrupted workflow: {has_workflow_id}")
                async for chunk in self._resume_workflow(task, session):
                    yield chunk
            else:
                # 状态异常：workflow_id 存在但 input_required_fields 为空
                # 这种情况不应该出现，因为只有两种合法状态：
                # 1. 新任务：workflow_id 为空
                # 2. 恢复任务：workflow_id 存在 + input_required_fields 存在
                error_msg = (
                    f"Invalid task state: workflow_id exists ({has_workflow_id}) "
                    f"but input_required_fields is None. "
                    f"This task should not be in WORKING status."
                )
                logger.error(error_msg)
                raise ValueError(error_msg)
    
    async def _execute_new_workflow(
        self, 
        task: Task, 
        session: Session
    ) -> AsyncIterator[ControllerOutputChunk]:
        """执行新的工作流任务
        
        前置条件：task.extensions["workflow_id"] 不存在
        
        流程：
        1. 提取用户输入
        2. 使用 LLM 检测工作流 ID
        3. 保存 workflow_id 到 task.extensions
        4. 执行工作流
        5. 处理结果（可能中断或完成）
        """
        user_input = json.loads(task.inputs[0].input_data[0].text).get("query")
        
        # 添加用户消息到上下文
        # only support TextDataFrame for now
        await MessageUtils.add_user_message(
            user_input, self._context_engine, session
        )
        
        # 使用 LLM 检测工作流
        detected_workflow_id = await self._detect_workflow_via_llm(
            user_input, session
        )
        
        logger.info(f"Detected workflow_id: {detected_workflow_id}")
        
        # 保存到 task.extensions
        task.extensions = task.extensions or {}
        task.extensions["workflow_id"] = detected_workflow_id
        await self._task_manager.update_task(task)
        
        # 执行工作流（非流式）
        logger.info("begin to execute workflow: {}".format(detected_workflow_id))
        workflow_session = session.create_workflow_session()
        exec_result = await Runner.run_workflow(
            detected_workflow_id, 
            inputs={"query": user_input}, 
            session=workflow_session
        )
        
        logger.info(f"Successfully executed workflow: {detected_workflow_id}")
        
        # 处理结果
        async for chunk in self._process_workflow_result(exec_result, task, session):
            yield chunk
    
    async def _resume_workflow(
        self, 
        task: Task, 
        session: Session
    ) -> AsyncIterator[ControllerOutputChunk]:
        """恢复被中断的工作流
        
        前置条件：
        - task.extensions["workflow_id"] 必须存在
        - task.input_required_fields 必须存在
        
        核心步骤：
        1. 从 task.extensions 获取 workflow_id
        2. 从 task.input_required_fields 获取 component_id（字段名是 "id"）
        3. 从 task.inputs[-1] 获取用户响应
        4. 构造 InteractiveInput 对象
        5. 清除 input_required_fields
        6. 调用 Runner.run_workflow 恢复执行
        7. 处理结果（可能再次中断或完成）
        """
        # 1. 获取 workflow_id
        workflow_id = task.extensions.get("workflow_id")
        logger.info(f"Resuming workflow: {workflow_id}")
        
        # 2. 获取 component_id
        component_id = task.input_required_fields.get("id")
        if not component_id:
            raise ValueError(
                f"No 'id' field in input_required_fields: {task.input_required_fields}"
            )
        
        logger.info(f"Component ID for interaction: {component_id}")
        
        # 3. 提取用户响应（从最后一个 input 中）
        if not task.inputs or len(task.inputs) == 0:
            raise ValueError(f"No inputs found for resuming task: {task.task_id}")
        
        latest_input_event = task.inputs[-1]
        user_response = self._extract_user_response_from_event(latest_input_event)
        logger.info(f"User response extracted: {user_response}")
        
        # 4. 构造 InteractiveInput
        interactive_input = InteractiveInput()
        interactive_input.update(component_id, user_response)
        
        logger.info(f"Created InteractiveInput: user_inputs={interactive_input.user_inputs}")
        
        # 5. 清除 input_required_fields
        task.input_required_fields = None
        await self._task_manager.update_task(task)
        
        # 6. 执行工作流恢复（非流式接口）
        workflow_session = session.create_workflow_session()
        
        try:
            exec_result = await Runner.run_workflow(
                workflow_id,
                inputs=interactive_input,
                session=workflow_session
            )
            
            logger.info(f"Workflow resume executed, result type: {type(exec_result)}")
            
            # 7. 处理结果（使用通用方法）
            async for chunk in self._process_workflow_result(exec_result, task, session):
                yield chunk
                
        except Exception as e:
            logger.error(f"Error resuming workflow {workflow_id}: {e}")
            task.status = TaskStatus.FAILED
            task.error_message = str(e)
            await self._task_manager.update_task(task)
            raise
    
    async def _process_workflow_result(
        self,
        exec_result: WorkflowOutput,
        task: Task,
        session: Session
    ) -> AsyncIterator[ControllerOutputChunk]:
        """处理工作流执行结果（新任务和恢复任务通用）
        
        处理两种情况：
        1. 工作流再次中断（result 是 List）→ 保存状态，返回 TASK_INTERACTION
        2. 工作流完成（result 不是 List）→ 返回 TASK_COMPLETION
        """

        if isinstance(exec_result, WorkflowOutput):
            if isinstance(exec_result.result, List):
                # 工作流中断
                result = exec_result.result[0]
                logger.info(f"Workflow interrupted at component: {result.payload.id}")
                
                # 保存中断状态到 task
                task.input_required_fields = {
                    "id": result.payload.id,
                    "value": result.payload.value
                }
                task.status = TaskStatus.INPUT_REQUIRED
                interaction_output = ControllerOutputChunk(
                    index=1,
                    type="controller_output",
                    payload=ControllerOutputPayload(
                        type=EventType.TASK_INTERACTION,
                        data=[TextDataFrame(text=f"{result.payload.value}")],
                        metadata={
                            "input_required_fields": {
                                "id": result.payload.id,
                                "value": result.payload.value
                            }
                        }
                    ),
                    last_chunk=True
                )
                task.outputs.append(interaction_output)
                await self._task_manager.update_task(task)
                # 添加工作流执行结果
                await MessageUtils.add_ai_message(AssistantMessage(content=result.payload.value),
                                                  self._context_engine, session)
                # 返回中断信息
                yield interaction_output
            else:
                # 工作流完成
                logger.info(f"Workflow completed successfully")

                # 添加工作流执行结果
                await MessageUtils.add_ai_message(AssistantMessage(content=json.dumps(exec_result.result)),
                                                  self._context_engine, session)
                yield ControllerOutputChunk(
                    index=1,
                    type="controller_output",
                    payload=ControllerOutputPayload(
                        type=EventType.TASK_COMPLETION,
                        data=[TextDataFrame(text=f"{exec_result.result}")]
                    ),
                    last_chunk=True
                )
    
    def _extract_user_response_from_event(self, event: InputEvent) -> str:
        """从 InputEvent 中提取用户响应文本
        
        支持：
        - TextDataFrame: 返回 text 字段
        - JsonDataFrame: 转换为 JSON 字符串
        
        Args:
            event: InputEvent 对象
        
        Returns:
            str: 用户响应文本
        
        Raises:
            ValueError: 如果没有有效的输入数据
        """
        if not event.input_data or len(event.input_data) == 0:
            raise ValueError("No input_data found in InputEvent")
        
        first_data = event.input_data[0]
        
        if isinstance(first_data, TextDataFrame):
            return first_data.text
        elif isinstance(first_data, JsonDataFrame):
            return json.dumps(first_data.data, ensure_ascii=False)
        else:
            logger.warning(f"Unsupported DataFrame type: {type(first_data)}, converting to string")
            return str(first_data)

    async def can_pause(self, task_id: str, session: Session) -> Tuple[bool, str]:
        return False, "This task cannot be paused"

    async def pause(self, task_id: str, session: Session) -> bool:
        raise RuntimeError("pause() should not be called when can_pause() returns False")

    async def can_cancel(self, task_id: str, session: Session) -> Tuple[bool, str]:
        return False, "This task cannot be cancelled"

    async def cancel(self, task_id: str, session: Session) -> bool:
        raise RuntimeError("cancel() should not be called when can_cancel() returns False")
