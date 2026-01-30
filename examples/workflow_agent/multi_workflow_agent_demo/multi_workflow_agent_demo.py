# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import ast
import asyncio
import logging
import os
import uuid
import warnings
from typing import Optional

warnings.filterwarnings("ignore")

from openjiuwen.core.common.logging import llm_logger, logger, prompt_logger

llm_logger.set_level(logging.CRITICAL)
logger.set_level(logging.CRITICAL)
prompt_logger.set_level(logging.CRITICAL)

from openjiuwen.core.controller.base import Controller, ControllerConfig
from openjiuwen.core.controller.modules import TaskExecutorDependencies
from openjiuwen.core.foundation.llm import (
    BaseModelInfo,
    Model,
    ModelClientConfig,
    ModelConfig,
    ModelRequestConfig,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.single_agent.base import ControllerAgent
from openjiuwen.core.workflow import (
    End,
    FieldInfo,
    QuestionerComponent,
    QuestionerConfig,
    Start,
    Workflow,
    WorkflowCard,
)

from workflow_event_handler import EventHandlerWithIntentRecognition
from workflow_executor import GeneralTaskExecutor, WorkflowTaskExecutor


API_BASE = "https://api.deepseek.com"
API_KEY = "API_KEY"
MODEL_NAME = "MODEL_NAME"
MODEL_PROVIDER = "OpenAI"
MODEL_ID = "MODEL_ID"
os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")


def extract_response(s: str) -> Optional[str]:
    """从字符串中提取 response 值"""
    try:
        data = ast.literal_eval(s.strip())
        if isinstance(data, dict):
            return data.get("response")
    except (ValueError, SyntaxError):
        return s

    return s


def build_workflow_task_executor(dependencies: TaskExecutorDependencies) -> WorkflowTaskExecutor:
    """Build workflow task executor"""
    return WorkflowTaskExecutor(dependencies)


def build_general_task_executor(dependencies: TaskExecutorDependencies) -> GeneralTaskExecutor:
    """Build general task executor"""
    return GeneralTaskExecutor(dependencies)


class Utils:
    @staticmethod
    def _create_model_config() -> ModelConfig:
        """创建模型配置"""
        return ModelConfig(
            model_provider=MODEL_PROVIDER,
            model_info=BaseModelInfo(
                model=MODEL_NAME,
                api_base=API_BASE,
                api_key=API_KEY,
                temperature=0.7,
                top_p=0.9,
                timeout=200,
            ),
        )

    @classmethod
    def build_financial_workflow(
            cls,
            workflow_id: str,
            workflow_name: str,
            workflow_desc: str,
            field_name: str,
            field_desc: str
    ) -> Workflow:
        """
        构建金融业务工作流（带中断节点）

        Args:
            workflow_id: 工作流ID
            workflow_name: 工作流名称
            workflow_desc: 工作流描述
            field_name: 提问字段名
            field_desc: 提问字段描述

        Returns:
            Workflow: 包含 start -> questioner -> end 的工作流
        """
        card = WorkflowCard(
            name=workflow_name,
            id=workflow_id,
            version="1.0",
            description=workflow_desc,
        )
        flow = Workflow(card=card)

        # 创建组件
        start = Start()

        # 创建提问器（中断节点）
        key_fields = [
            FieldInfo(
                field_name=field_name,
                description=field_desc,
                required=True
            ),
        ]
        model_config = Utils._create_model_config()
        # client_provider 需要使用正确的大小写格式 (OpenAI, SiliconFlow)
        provider = model_config.model_provider
        if provider and provider.lower() == 'openai':
            provider = 'OpenAI'
        elif provider and provider.lower() == 'siliconflow':
            provider = 'SiliconFlow'
        questioner_config = QuestionerConfig(
            model_client_config=ModelClientConfig(
                client_provider=provider,
                api_key=model_config.model_info.api_key,
                api_base=model_config.model_info.api_base,
                timeout=model_config.model_info.timeout,
                verify_ssl=False,
            ),
            model_config=ModelRequestConfig(
                model=model_config.model_info.model_name,
                temperature=model_config.model_info.temperature,
                top_p=model_config.model_info.top_p,
            ),
            question_content="",
            extract_fields_from_response=True,
            field_names=key_fields,
            with_chat_history=False,
        )
        questioner = QuestionerComponent(questioner_config)

        # End 组件
        end = End({"responseTemplate": f"{workflow_name}完成: {{{{{field_name}}}}}"})

        # 注册组件
        flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
        flow.add_workflow_comp(
            "questioner", questioner, inputs_schema={"query": "${start.query}"}
        )
        flow.set_end_comp(
            "end", end, inputs_schema={field_name: f"${{questioner.{field_name}}}"}
        )

        # 连接拓扑: start -> questioner -> end
        flow.add_connection("start", "questioner")
        flow.add_connection("questioner", "end")

        return flow


async def main():
    # 创建添加模型配置
    model = Model(
        model_client_config=ModelClientConfig(
            client_provider=MODEL_PROVIDER,
            api_base=API_BASE,
            api_key=API_KEY,
            verify_ssl=False,
            timeout=120,
        ),
        model_config=ModelRequestConfig(model=MODEL_NAME),
    )
    Runner.resource_mgr.add_model(
        model_id=MODEL_ID,
        model=lambda: model,
    )

    # 创建持有controller的ControllerAgent
    agent_card = AgentCard(
        id="financial_agent",
        name="Financial Agent",
        description="金融智能体",
    )
    financial_controller = Controller()
    config = ControllerConfig(enable_task_persistence=True, intent_llm_id=MODEL_ID, intent_confidence_threshold=0.3,
                              event_timeout=120000.0, task_timeout=120000.0)
    financial_agent = ControllerAgent(
        card=agent_card,
        controller=financial_controller,
        config=config
    )

    # 绑定事件处理器到controller
    financial_controller.set_event_handler(EventHandlerWithIntentRecognition())
    # 添加所有任务执行器
    (financial_controller.add_task_executor("workflow", build_workflow_task_executor).
                            add_task_executor("general", build_general_task_executor))

    # 创建金融业务工作流
    transfer_workflow = Utils.build_financial_workflow(
        workflow_id="transfer_flow_multi",
        workflow_name="转账服务",
        workflow_desc="处理用户转账请求，支持转账到指定账户",
        field_name="amount",
        field_desc="转账金额（数字）"
    )
    # 创建理财产品工作流
    invest_workflow = Utils.build_financial_workflow(
        workflow_id="invest_flow_multi",
        workflow_name="理财服务",
        workflow_desc="提供理财产品推荐和购买服务",
        field_name="product",
        field_desc="理财产品名称"
    )
    # 创建余额查询工作流
    balance_workflow = Utils.build_financial_workflow(
        workflow_id="balance_flow",
        workflow_name="余额查询",
        workflow_desc="查询用户账户余额信息",
        field_name="account",
        field_desc="账户号码"
    )

    Runner.resource_mgr.add_workflow(transfer_workflow.card, lambda: transfer_workflow)
    Runner.resource_mgr.add_workflow(invest_workflow.card, lambda: invest_workflow)
    Runner.resource_mgr.add_workflow(balance_workflow.card, lambda: balance_workflow)

    financial_agent.ability_manager.add(invest_workflow.card)
    financial_agent.ability_manager.add(transfer_workflow.card)
    financial_agent.ability_manager.add(balance_workflow.card)

    print("\n========== 金融智能体交互系统 ==========")
    print("命令说明:")
    print("  - 直接输入问题进行对话")
    print("  - 'quit' 或 'exit': 退出系统\n")

    conversation_id = str(uuid.uuid4())[:8]
    print(f"当前会话 ID: {conversation_id}")

    round_count = 0
    while True:
        try:
            print(f"\n{'=' * 30}第 {round_count + 1} 轮对话{'=' * 30}\n")
            # 等待用户输入
            user_input = input("\n请输入您的问题: ").strip()

            # 退出条件
            if user_input.lower() in ['quit', 'exit', '退出']:
                print("\n感谢使用，再见！")
                break

            # 跳过空输入
            if not user_input:
                print("输入不能为空，请重新输入")
                continue

            round_count += 1

            # 调用 Agent
            res = Runner.run_agent_streaming(
                financial_agent,
                inputs={
                    "query": user_input,
                    "conversation_id": conversation_id
                }
            )

            # 流式输出结果
            print("助手回复: ", end="", flush=True)
            async for chunk in res:
                # 提取文本内容并打印
                if hasattr(chunk, 'payload') and chunk.payload:
                    if hasattr(chunk.payload, 'data') and chunk.payload.data:
                        for data_frame in chunk.payload.data:
                            if hasattr(data_frame, 'text'):
                                response = extract_response(data_frame.text)
                                print(response, end="", flush=True)
            print()  # 换行

        except KeyboardInterrupt:
            print("\n\n检测到 Ctrl+C，正在退出...")
            break
        except Exception as e:
            logger.error(f"处理输入时发生错误: {e}")
            print(f"\n发生错误: {e}")
            print("请重新输入")


if __name__ == "__main__":
    """金融智能体交互系统示例
    
    请输入您的问题: 我要理财
    助手回复: 请您提供理财产品名称相关的信息
    
    请输入您的问题: 购买稳健型理财产品
    助手回复: 理财服务完成: 稳健型理财产品
    
    请输入您的问题: 我要给张三转账
    助手回复: 请您提供转账金额（数字）相关的信息
    
    请输入您的问题: 查询银行卡余额
    助手回复: 请您提供账户号码相关的
    
    请输入您的问题: 账号是10000
    助手回复: 余额查询完成: 10000
    
    请输入您的问题: 100元
    助手回复: 转账服务完成: 100
    """
    asyncio.run(main())
