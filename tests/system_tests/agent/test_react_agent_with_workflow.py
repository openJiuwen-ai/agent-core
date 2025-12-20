import os
import unittest
from datetime import datetime

from jiuwen.agent.common.schema import PluginSchema
from jiuwen.agent.react_agent import create_react_agent_config, create_react_agent, ReActAgent
from jiuwen.core.component.common.configs.model_config import ModelConfig
from jiuwen.core.component.start_comp import Start
from jiuwen.core.component.end_comp import End
from jiuwen.core.workflow.base import Workflow
from jiuwen.core.workflow.workflow_config import WorkflowConfig, WorkflowMetadata, WorkflowInputsSchema
from jiuwen.agent.common.schema import WorkflowSchema
from jiuwen.core.component.intent_detection_comp import IntentDetectionComponent, IntentDetectionCompConfig
from jiuwen.core.component.llm_comp import LLMComponent, LLMCompConfig
from jiuwen.core.utils.llm.base import BaseModelInfo
from jiuwen.core.utils.tool.param import Param
from jiuwen.core.utils.tool.service_api.restful_api import RestfulApi

API_BASE = os.getenv("API_BASE", "")
API_KEY = os.getenv("API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")


def build_current_date():
    current_datetime = datetime.now()
    return current_datetime.strftime("%Y-%m-%d")


class ReActAgentWorkflowTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _create_start_component():
        return Start({"inputs": [{"id": "query", "type": "String", "required": "true", "sourceType": "ref"}]})

    @staticmethod
    def _create_end_component():
        return End({"responseTemplate": "最终结果为：{{good_weather_output}} {{bad_weather_output}}"})

    @staticmethod
    def _create_model_config():
        return ModelConfig(model_provider=MODEL_PROVIDER,
                           model_info=BaseModelInfo(
                               model=MODEL_NAME,
                               api_base=API_BASE,
                               api_key=API_KEY,
                               temperature=0.7,
                               top_p=0.9,
                               timeout=30
                           ))

    @staticmethod
    def _create_intent_detection_component() -> IntentDetectionComponent:
        """创建意图识别组件。"""

        user_prompt = """

         {{user_prompt}}

         当前可供选择的功能分类如下：
         {{category_info}}

         用户与助手的对话历史：
         {{chat_history}}

         当前输入：
         {{input}}

         请根据当前输入和对话历史分析并输出最适合的功能分类。输出格式为 JSON：
         {"class": "分类xx"}
         如果没有合适的分类，请输出 {{default_class}}。
         """

        config = IntentDetectionCompConfig(
            user_prompt="请判断用户意图",
            category_name_list=["天气是晴天", "天气不是晴天"],
            model=ReActAgentWorkflowTest._create_model_config(),
        )

        component = IntentDetectionComponent(config)
        # 定义不同意图下路由到相应的分支链路，如果天气是晴天，则路由到`llm_1`组件所在的分支进行处理，否则路由到`llm_2`组件所在的分支执行非晴天场景的流程
        component.add_branch("${intent.classification_id} == 0", ["end"], "默认分支")
        component.add_branch("${intent.classification_id} == 1", ["llm_1"], "晴天分支")
        component.add_branch("${intent.classification_id} == 2", ["llm_2"], "非晴天分支")

        return component

    def _create_llm_component(self, user_prompt) -> LLMComponent:
        config = LLMCompConfig(
            model=self._create_model_config(),
            template_content=[{"role": "system", "content": "你是一个AI助手。"},
                              {"role": "user", "content": user_prompt}],
            response_format={"type": "text"},
            output_config={
                "output": {"type": "string", "description": "大模型生成的文本", "required": True}
            },
        )
        return LLMComponent(config)

    def _create_workflow(self):
        # 实例化各组件
        start = self._create_start_component()
        intent = self._create_intent_detection_component()
        llm_1 = self._create_llm_component("天气很好，请帮我生成一段建议出行的文本")
        llm_2 = self._create_llm_component("天气不好，请帮我生成一段疗愈心灵的文本")
        end = self._create_end_component()

        # 注册组件到工作流
        id = "weather_generation_text_workflow"
        version = "1.0"
        name = "weather_generation_text"
        workflow_config = WorkflowConfig(
            metadata=WorkflowMetadata(
                name=name,
                id=id,
                version=version,
                description="根据天气生成对应文本"
            ),
            workflow_inputs_schema=WorkflowInputsSchema(
                type="object",
                properties={
                    "query": {
                        "type": "string",
                        "description": "用户输入",
                        "required": True
                    }
                },
                required=['query']
            )
        )
        flow = Workflow(workflow_config=workflow_config)
        flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
        flow.add_workflow_comp("intent", intent, inputs_schema={"query": "${start.query}"})
        flow.add_workflow_comp("llm_1", llm_1, inputs_schema={"query": "${start.query}"})
        flow.add_workflow_comp("llm_2", llm_2, inputs_schema={"query": "${start.query}"})
        flow.set_end_comp("end", end,
                          inputs_schema={
                              "good_weather_output": "${llm_1.output}",
                              "bad_weather_output": "${llm_2.output}"
                          })

        # 连接组件
        flow.add_connection("start", "intent")
        flow.add_connection("llm_1", "end")
        flow.add_connection("llm_2", "end")

        return flow

    @staticmethod
    def _create_workflow_schema():
        workflow_info = WorkflowSchema(
            id="weather_generation_text_workflow",
            name='weather_generation_text',
            version='1.0',
            description='根据天气生成不同风格文本的工作流',
            inputs={
                "type": "object",
                "properties": {
                    "weather_condition": {
                        "type": "string",
                        "description": "工作流输入：天气查询结果",
                        "required": True
                    }
                }
            }
        )

        return workflow_info

    @staticmethod
    def _create_tool():
        weather_plugin = RestfulApi(
            name="WeatherReporter",
            description="天气查询插件",
            params=[
                Param(name="location", description="天气查询的地点，必须为英文", type="string", required=True),
                Param(name="date", description="天气查询的时间，格式为YYYY-MM-DD", type="string", required=True),
            ],
            path="http://127.0.0.1:9000/weather",
            headers={},
            method="GET",
            response=[],
        )
        return weather_plugin

    @staticmethod
    def _create_tool_schema():
        tool_info = PluginSchema(
            name='WeatherReporter',
            description='天气查询插件',
            inputs={
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "天气查询的地点。\n注意：地点名称必须为英文",
                        "required": True
                    },
                    "date": {
                        "type": "string",
                        "description": "天气查询的时间，格式为YYYY-MM-DD",
                        "required": True
                    }
                }
            }
        )
        return tool_info

    @staticmethod
    def _create_prompt_template():
        system_prompt = "你是一个AI助手，在适当的时候调用合适的工具，帮助我完成任务！今天的日期为：{}\n注意：1. 如果用户请求中未指定具体时间，则默认为今天。"
        return [
            dict(role="system", content=system_prompt.format(build_current_date()))
        ]

    @unittest.skip("skip system test require llm")
    async def test_react_agent_with_workflow(self):
        tools_schema = [self._create_tool_schema()]
        model_config = self._create_model_config()
        prompt_template = self._create_prompt_template()

        react_agent_config = create_react_agent_config(
            agent_id="react_agent_123",
            agent_version="0.0.1",
            description="AI助手",
            plugins=tools_schema,
            workflows=[self._create_workflow_schema()],
            model=model_config,
            prompt_template=prompt_template
        )
        react_agent: ReActAgent = create_react_agent(
            agent_config=react_agent_config,
            workflows=[self._create_workflow()],
            tools=[self._create_tool()]
        )
        result = await react_agent.invoke({"query": "今天上海天气晴朗，温度适宜，请生成一段文本"})
        print(f"ReActAgent 最终输出结果：{result.get('output')}")
