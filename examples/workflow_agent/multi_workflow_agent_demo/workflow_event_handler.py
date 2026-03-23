# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import uuid
from typing import List, Optional, Dict, Tuple

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.controller import TextDataFrame, FileDataFrame, JsonDataFrame, IntentType, TaskStatus, Task
from openjiuwen.core.controller.base import ControllerConfig
from openjiuwen.core.controller.modules.event_handler import EventHandler, EventHandlerInput
from openjiuwen.core.controller.modules.task_manager import TaskManager, TaskFilter
from openjiuwen.core.controller.schema import Intent
from openjiuwen.core.controller.schema.event import Event, InputEvent
from openjiuwen.core.foundation.llm import SystemMessage, UserMessage, ToolMessage
from openjiuwen.core.single_agent import Session
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.common.logging import logger


class IntentToolkits:
    def __init__(self, event, confidence_threshold: float):
        self.event = event
        self.confidence_threshold = confidence_threshold

        self._tool_schema_choices = {
            "create_task": {
                "type": "function",
                "function": {
                    "name": "create_task",
                    "description": "Create a new task. Use this method when the user "
                                   "wants to start a new task or activity.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "confidence": {
                                "type": "number",
                                "description": "Your confidence score for this operation (0-1.0), "
                                               "typically used when confidence is low"
                            },
                            "task_description": {
                                "type": "string",
                                "description": "Detailed description of the task, specifying what the "
                                               "user wants to accomplish"
                            }
                        },
                        "required": ["confidence", "task_description"],
                        "additionalProperties": False
                    }
                }
            },
            "resume_task": {
                "type": "function",
                "function": {
                    "name": "resume_task",
                    "description": "Resume a specific task. Use when the user wants to continue a "
                                   "previously input-required or interrupted task.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "confidence": {
                                "type": "number",
                                "description": "Your confidence score for this operation (0-1.0), "
                                               "typically used when confidence is low"
                            },
                            "task_id": {
                                "type": "string",
                                "description": "Unique identifier of the task to be resumed"
                            }
                        },
                        "required": ["confidence", "task_id"],
                        "additionalProperties": False
                    }
                }
            },
            "unknown_task": {
                "type": "function",
                "function": {
                    "name": "unknown_task",
                    "description": "Handle unknown or ambiguous user intents. Use this method when the "
                                   "exact user intent cannot be determined to create clarification "
                                   "questions for the user.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "confidence": {
                                "type": "number",
                                "description": "Your confidence score for this operation (0-1.0), "
                                               "typically used when confidence is low"
                            }
                        },
                        "required": ["confidence"],
                        "additionalProperties": False
                    }
                }
            }
        }

    def _low_confidence_intent(self, confidence: float) -> Tuple[Intent, str]:
        return Intent(
            intent_type=IntentType.UNKNOWN_TASK,
            event=self.event,
            target_task_id="",
            target_task_description=None,
            depend_task_id=[],
            supplementary_info=None,
            modification_details=None,
            confidence=confidence,
            clarification_prompt="Sorry, I couldn't understand your meaning. "
                                 "Please clarify whether you want to create a new "
                                 "task or modify an existing one.",
        ), f"Automatically converted to unknown_task due to low confidence"

    async def create_task(self, confidence: float, task_description: str) -> Tuple[Intent, str]:
        if confidence < self.confidence_threshold:
            return self._low_confidence_intent(confidence)
        target_task_id = str(uuid.uuid4())
        return Intent(
            intent_type=IntentType.CREATE_TASK,
            event=self.event,
            target_task_id=target_task_id,
            target_task_description=task_description,
            depend_task_id=[],
            supplementary_info=None,
            modification_details=None,
            confidence=confidence,
            clarification_prompt=None
        ), (f"Task ID: {target_task_id}, Task Description: {task_description}, "
            f"Current Status: Created and submitted for execution")

    async def resume_task(self, confidence: float, task_id: str) -> Tuple[Intent, str]:
        if confidence < self.confidence_threshold:
            return self._low_confidence_intent(confidence)
        return Intent(
            intent_type=IntentType.RESUME_TASK,
            event=self.event,
            target_task_id=task_id,
            target_task_description=None,
            depend_task_id=[],
            supplementary_info=None,
            modification_details=None,
            confidence=confidence,
            clarification_prompt=None
        ), f"Task ID: {task_id}, Current Status: Resumed"

    async def unknown_task(self, confidence: float) -> Tuple[Intent, str]:
        if confidence < self.confidence_threshold:
            return self._low_confidence_intent(confidence)
        target_task_id = str(uuid.uuid4())
        question_for_user = "Sorry, I couldn't understand your meaning. Please clarify your intent."
        return Intent(
            intent_type=IntentType.UNKNOWN_TASK,
            event=self.event,
            target_task_id=target_task_id,
            target_task_description=None,
            depend_task_id=[],
            supplementary_info=None,
            modification_details=None,
            confidence=confidence,
            clarification_prompt=question_for_user,
        ), f"Request sent, waiting for user response."

    def get_openai_tool_schemas(self, choices: List[str] = None) -> List[Dict]:
        """
        Get OpenAI Tool Schemas

        Returns:
            List[Dict]: OpenAI tool schemas
        """
        if not choices:
            return list(self._tool_schema_choices.values())
        return [self._tool_schema_choices[k] for k in self._tool_schema_choices.keys()]


class IntentRecognizer:
    """意图识别器

    负责识别用户输入中的意图，将事件转换为Intent对象。
    """
    def __init__(
            self,
            config: ControllerConfig,
            task_manager: TaskManager,
            ability_manager: AbilityManager,
            context_engine: ContextEngine
    ):
        """初始化意图识别器

        Args:
            config: 控制器配置
            task_manager: 任务管理器
            ability_manager: 能力包
            context_engine: 上下文引擎
        """
        self._config = config
        self._task_manager = task_manager
        self._context_engine = context_engine
        self._ability_manager = ability_manager

        self._system_message = SystemMessage(content="""# 角色
你是一个任务管理助手，专门使用工具创建和管理任务。你的核心理念是：**任何用户请求都可以转化为一个任务**，并由任务管理器处理。

# 核心原则
1. **任务化一切**：对于任何用户请求（包括信息查询、事务处理、提醒等），你的第一反应不是直接执行或拒绝，而是思考如何将它创建为一个任务。
2. **透明管理**：如果任务需要外部能力（如天气API），你仍然创建它，并明确告知用户任务的状态。
3. **现有任务优先**：总是先判断用户请求是否和已有任务相关，如果相关恢复任务，如果不相关创建新的任务

# 工作流程
1. **解析请求**：理解用户想做什么。
2. **任务操作**：使用工具创建一个对应的任务或恢复已有任务。
3. **永远不拒绝**：不声称“超出能力范围”，而是告知用户任务会由其他执行器处理。

# 任务目标
- 根据用户输入，**总是优先创建对应的任务**。
- 使用工具进行任务操作（创建、恢复、删除）。
- 只有纯粹闲聊或问候时不调用工具。
""")

        self._user_prompt_template = """你当前拥有的任务有：
{task_descriptions}

当前用户的输入为：
{query}

请根据你当前的任务和用户输入，进行合适的任务操作。
"""

    async def _prepare_user_message(self, query):
        """只添加非完成状态的任务"""
        tasks = await self._task_manager.get_task()
        task_prompt = []
        if tasks:
            for task in tasks:
                if task.status == TaskStatus.COMPLETED:
                    continue
                output_text = ""
                if task.outputs and len(task.outputs) > 0:
                    first_output = task.outputs[0]
                    if first_output.payload and first_output.payload.data:
                        # 获取第一个 data 项
                        if len(first_output.payload.data) > 0:
                            first_data = first_output.payload.data[0]
                            # 检查是否为 TextDataFrame
                            if hasattr(first_data, 'text'):
                                output_text = first_data.text
                            elif hasattr(first_data, 'data'):  # JsonDataFrame
                                output_text = str(first_data.data)
                task_prompt.append(
                    f"## Task id: {task.task_id}\n### Task description: {task.description}\n"
                    f"### Task outputs: {output_text} \nStatus: {task.status}\n")
        else:
            task_prompt.append("无")
        task_prompt = "\n".join(task_prompt)

        prompt = self._user_prompt_template.format(
            task_descriptions=task_prompt,
            query=query
        )
        return UserMessage(content=prompt)

    async def recognize(self, event: Event, session: Session) -> List[Intent]:
        """识别意图"""
        # 1. 输入验证
        text_input = self._validate_and_extract_input(event)
        
        # 2. 准备上下文
        context = self._context_engine.get_context(session_id=session.get_session_id())
        if not context:
            logger.info("create context")
            context = await self._context_engine.create_context(session=session)
        
        # 3. 准备消息并调用 LLM
        user_message = await self._prepare_user_message(query=text_input)
        await context.add_messages(user_message)
        logger.info("IntentRecognizer user message: {}".format(user_message))
        
        toolkits = IntentToolkits(event, self._config.intent_confidence_threshold)
        from openjiuwen.core.runner import Runner
        model = await Runner.resource_mgr.get_model(model_id=self._config.intent_llm_id, session=session)
        
        response = await model.invoke(
            messages=[self._system_message] + context.get_messages(size=50),
            tools=toolkits.get_openai_tool_schemas(self._config.intent_type_list)
        )
        logger.info("IntentRecognizer get response: {}".format(response))
        await context.add_messages(response)
        
        # 4. 处理工具调用 - 简化逻辑
        if not response.tool_calls:
            logger.info("IntentRecognizer no tool calls")
            return []
        
        # 只处理第一个工具调用
        tool_call = response.tool_calls[0]
        instance = getattr(toolkits, tool_call.name)
        intent, result = await instance(**json.loads(tool_call.arguments))
        logger.info("IntentRecognizer get intent {}".format(intent))
        
        # 添加工具响应到上下文（供后续对话使用）
        await context.add_messages(ToolMessage(
            tool_call_id=tool_call.id,
            content=result
        ))
        
        return [intent]

    @staticmethod
    def _validate_and_extract_input(event: Event) -> str:
        """验证输入并提取文本"""
        if not isinstance(event, InputEvent):
            raise ValueError("Event must be InputEvent")
        
        inputs = event.input_data
        texts = [df for df in inputs if isinstance(df, TextDataFrame)]
        
        if any(isinstance(df, (FileDataFrame, JsonDataFrame)) for df in inputs):
            raise build_error(
                status=StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                error_msg="Files and JSON inputs are not supported for intent recognition."
            )
        
        if len(texts) != 1:
            raise build_error(
                status=StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                error_msg=f"Expected exactly 1 text input, got {len(texts)}."
            )
        
        return texts[0].text


class EventHandlerWithIntentRecognition(EventHandler):
    """基于意图识别的事件处理器"""
    def __init__(self):
        super().__init__()
        self._recognizer = None

    @property
    def recognizer(self):
        """延迟初始化 recognizer，确保依赖已注入"""
        if self._recognizer is None:
            if self.task_manager is None or self.ability_manager is None:
                raise RuntimeError(
                    "EventHandler dependencies not initialized. "
                    "Ensure set_event_handler() is called before using the handler."
                )
            self._recognizer = IntentRecognizer(
                self._config,
                self.task_manager,
                self.ability_manager,
                self.context_engine
            )
        return self._recognizer

    async def handle_input(self, inputs: EventHandlerInput) -> Optional[Dict]:
        logger.info(f"handle input get event: {inputs.event}, session: {inputs.session}")
        # 将JsonDataFrame转化成TextDataFrame
        converted_data = []
        for df in inputs.event.input_data:
            if isinstance(df, JsonDataFrame):
                # 将 JSON 数据转换为字符串
                text_content = json.dumps(df.data, ensure_ascii=False)
                converted_data.append(TextDataFrame(text=text_content))
            else:
                converted_data.append(df)
        inputs.event.input_data = converted_data

        intents = await self.recognizer.recognize(inputs.event, inputs.session)
        logger.info("handle_input get intent: {}".format(intents))
        logger.info("for now, just use the first intent")
        intent = intents[0]
        if intent.intent_type == IntentType.RESUME_TASK:
            await self._process_resume_task_intent(intent, inputs.session)
        else:
            await self._process_create_task_intent(intent, inputs.session)

        return {"status": "success"}

    async def handle_task_completion(self, inputs: EventHandlerInput) -> Optional[Dict]:
        logger.info("handle_task_completion called here")
        return {"status": "success"}

    async def handle_task_interaction(self, inputs: EventHandlerInput) -> Optional[Dict]:
        logger.info("handle_task_interaction called here, get event: {}".format(inputs.event))

        return {"status": "success"}

    async def handle_task_failed(self, inputs: EventHandlerInput) -> Optional[Dict]:
        return {"status": "success"}

    async def _process_create_task_intent(self, intent: Intent, session: Session):
        """处理创建任务意图

        用户自定义执行新任务逻辑。

        Args:
            intent: 意图
            session: Session
        """
        task = Task(
            session_id=session.get_session_id(),
            task_id=intent.target_task_id,
            task_type="workflow",
            description=intent.target_task_description,
            priority=1,
            context_id=f"{session.get_session_id()}_{intent.target_task_id}",
            inputs=[intent.event] if isinstance(intent.event, InputEvent) else None,
            status=TaskStatus.SUBMITTED,
            error_message=None,
            metadata=intent.metadata,
            extensions={"workflow_id": ""}
        )
        await self.task_manager.add_task(task)
        logger.info(f"successfully add task to task manager, task: {task}")

    async def _process_resume_task_intent(self, intent: Intent, session: Session):
        """处理恢复任务意图

        将要恢复的任务的状态置为 submitted。

        Args:
            intent: 意图
            session: Session
        """
        task = await self.task_manager.get_task(TaskFilter(task_id=intent.target_task_id))
        task = task[0]
        if task.status == TaskStatus.INPUT_REQUIRED:
            if isinstance(intent.event, InputEvent):
                task.inputs.append(intent.event)
                task.status = TaskStatus.SUBMITTED
                # 更新任务对象（包含新的 inputs）
                await self.task_manager.update_task(task)
                logger.info(f"Added input event to task.inputs: {len(task.inputs)}")

            logger.info("successfully update task status to SUBMITTED")
        else:
            await self.task_manager.update_task_status(task.task_id, TaskStatus.INPUT_REQUIRED)
