import os
import unittest
from datetime import datetime

import pytest

from jiuwen.agent.common.schema import PluginSchema
from jiuwen.agent.react_agent import create_react_agent_config, create_react_agent, ReActAgent
from jiuwen.core.component.common.configs.model_config import ModelConfig
from jiuwen.core.utils.llm.base import BaseModelInfo
from jiuwen.core.utils.tool.function.function import LocalFunction
from jiuwen.core.utils.tool.param import Param
from jiuwen.core.utils.tool.service_api.restful_api import RestfulApi
from jiuwen.core.utils.tool.tool import tool


API_BASE = os.getenv("API_BASE", "")
API_KEY = os.getenv("API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")

def build_current_date():
    current_datetime = datetime.now()
    return current_datetime.strftime("%Y-%m-%d")


class ReActAgentTest(unittest.IsolatedAsyncioTestCase):  # ① 关键改动
    @staticmethod
    def _create_model():
        return ModelConfig(model_provider=MODEL_PROVIDER,
                           model_info=BaseModelInfo(
                               model=MODEL_NAME,
                               api_base=API_BASE,
                               api_key=API_KEY,
                               temperature=0.7,
                               top_p=0.9,
                               timeout=30  # 添加超时设置
                           ))

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
    def _create_function_tool():
        weather_plugin = LocalFunction(
            name="add",
            description="加法",
            params=[
                Param(name="a", description="加数", type="number", required=True),
                Param(name="b", description="被加数", type="number", required=True),
            ],
            func=lambda a, b: a + b
        )
        return weather_plugin

    @staticmethod
    @tool(
        name="add",
        description="加法",
        params=[
            Param(name="a", description="加数", type="number", required=True),
            Param(name="b", description="被加数", type="number", required=True),
        ],
    )
    def add_function(a, b):
        """加法函数，使用tool注解装饰"""
        return a + b

    @staticmethod
    def _create_function_tool_with_annotation():
        # 直接返回被tool注解装饰后的函数，它已经是一个LocalFunction对象
        return ReActAgentTest.add_function

    @staticmethod
    def _create_function_tool_schema():
        tool_info = PluginSchema(
            name='add',
            description='加法',
            inputs={
                "type": "object",
                "properties": {
                    "a": {
                        "type": "number",
                        "description": "加数",
                        "required": True
                    },
                    "b": {
                        "type": "number",
                        "description": "被加数",
                        "required": True
                    }
                }
            }
        )
        return tool_info

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

    @staticmethod
    def _create_function_prompt_template():
        system_prompt = "你是一个数学计算专家。"
        return [
            dict(role="system", content=system_prompt.format(build_current_date()))
        ]

    @unittest.skip("skip system test")
    async def test_react_agent_invoke_with_real_plugin(self):
        tools_schema = [self._create_tool_schema()]
        model_config = self._create_model()
        prompt_template = self._create_prompt_template()

        react_agent_config = create_react_agent_config(
            agent_id="react_agent_123",
            agent_version="0.0.1",
            description="AI助手",
            plugins=tools_schema,
            workflows=[],
            model=model_config,
            prompt_template=prompt_template
        )

        react_agent: ReActAgent = create_react_agent(
            agent_config=react_agent_config,
            workflows=[],
            tools=[self._create_tool()]
        )

        result = await react_agent.invoke({"query": "查询杭州的天气"})
        print(f"ReActAgent 最终输出结果：{result}")

    @unittest.skip("skip system test")
    async def test_react_agent_stream_with_real_plugin(self):
        tools_schema = [self._create_tool_schema()]
        model_config = self._create_model()
        prompt_template = self._create_prompt_template()

        react_agent_config = create_react_agent_config(
            agent_id="react_agent_123",
            agent_version="0.0.1",
            description="AI助手",
            plugins=tools_schema,
            workflows=[],
            model=model_config,
            prompt_template=prompt_template
        )

        react_agent: ReActAgent = create_react_agent(
            agent_config=react_agent_config,
            workflows=[],
            tools=[self._create_tool()]
        )

        res = react_agent.stream({"query": "查询杭州的天气"})
        async for i in res:
            print(i)

    @unittest.skip("skip system test")
    async def test_react_agent_invoke_with_real_function_plugin(self):
        tools_schema = [self._create_function_tool_schema()]
        model_config = self._create_model()
        prompt_template = self._create_function_prompt_template()

        react_agent_config = create_react_agent_config(
            agent_id="react_agent_1234",
            agent_version="0.0.2",
            description="AI计算助手",
            plugins=tools_schema,
            workflows=[],
            model=model_config,
            prompt_template=prompt_template
        )

        # 使用传统方式创建的LocalFunction对象
        react_agent: ReActAgent = create_react_agent(
            agent_config=react_agent_config,
            workflows=[],
            tools=[self._create_function_tool()]
        )

        result = await react_agent.invoke({"query": "计算1+2"})
        print(f"ReActAgent 最终输出结果：{result}")

    @unittest.skip("skip system test")
    async def test_react_agent_invoke_with_annotated_function_plugin(self):
        """测试使用tool注解装饰的函数作为工具"""
        tools_schema = [self._create_function_tool_schema()]
        model_config = self._create_model()
        prompt_template = self._create_function_prompt_template()

        react_agent_config = create_react_agent_config(
            agent_id="react_agent_1235",
            agent_version="0.0.3",
            description="AI计算助手（使用注解）",
            plugins=tools_schema,
            workflows=[],
            model=model_config,
            prompt_template=prompt_template
        )

        # 使用tool注解创建的LocalFunction对象
        react_agent: ReActAgent = create_react_agent(
            agent_config=react_agent_config,
            workflows=[],
            tools=[self._create_function_tool_with_annotation()]
        )

        result = await react_agent.invoke({"query": "计算1+2"})
        print(f"ReActAgent 使用注解工具最终输出结果：{result}")
