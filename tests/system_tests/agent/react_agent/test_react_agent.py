#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
ReAct Agent 测试 - 极简版（无中断、无Controller）

"""
import os
import unittest
from datetime import datetime

from openjiuwen.agent.react_agent.react_agent import ReActAgent
from openjiuwen.agent.config.react_config import ReActAgentConfig
from openjiuwen.core.component.common.configs.model_config import ModelConfig
from openjiuwen.core.utils.llm.base import BaseModelInfo
from openjiuwen.core.utils.tool.function.function import LocalFunction
from openjiuwen.core.utils.tool.param import Param
from openjiuwen.core.utils.tool.service_api.restful_api import RestfulApi
from openjiuwen.core.utils.tool.tool import tool
from openjiuwen.core.runner.runner import Runner, resource_mgr


API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
os.environ.setdefault("LLM_SSL_VERIFY", "false")

def build_current_date():
    current_datetime = datetime.now()
    return current_datetime.strftime("%Y-%m-%d")


class ReActAgentTest(unittest.IsolatedAsyncioTestCase):
    """ReAct Agent 测试套件"""

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    @staticmethod
    def _create_model():
        return ModelConfig(
            model_provider=MODEL_PROVIDER,
            model_info=BaseModelInfo(
                model=MODEL_NAME,
                api_base=API_BASE,
                api_key=API_KEY,
                temperature=0.7,
                top_p=0.9,
                timeout=30
            )
        )

    @staticmethod
    def _create_tool():
        """创建 RestfulApi 工具"""
        weather_plugin = RestfulApi(
            name="WeatherReporter",
            description="天气查询插件",
            params=[
                Param(name="location", description="天气查询的地点，必须为英文", type="string", required=True),
                Param(name="date", description="天气查询的时间，格式为YYYY-MM-DD", type="string", required=True),
            ],
            path="http://127.0.0.1:8000/weather",
            headers={},
            method="GET",
            response=[],
        )
        return weather_plugin

    @staticmethod
    def _create_function_tool():
        """创建 LocalFunction 工具"""
        add_plugin = LocalFunction(
            name="add",
            description="加法",
            params=[
                Param(name="a", description="加数", type="number", required=True),
                Param(name="b", description="被加数", type="number", required=True),
            ],
            func=lambda a, b: a + b
        )
        return add_plugin

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
        """返回被tool注解装饰的函数"""
        return ReActAgentTest.add_function

    @unittest.skip("require network")
    async def test_react_agent_invoke_with_restful_plugin(self):
        """测试 ReAct Agent 使用真实 RestfulApi 插件（使用新的动态配置方法）"""
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")

        # 1. 创建最小化配置的 agent（Linus 风格：只传必要参数）
        react_agent_config = ReActAgentConfig(
            id="react_agent_123",
            version="0.0.1",
            description="AI助手",
            model=self._create_model()
            # plugins, workflows, prompt_template, tools 都不传，使用默认空列表
        )

        react_agent: ReActAgent = ReActAgent(react_agent_config)

        # 2. 动态添加 prompt（内化配置）
        system_prompt = "你是一个AI助手，在适当的时候调用合适的工具，帮助我完成任务！今天的日期为：{}\n注意：1. 如果用户请求中未指定具体时间，则默认为今天。"
        react_agent.add_prompt([
            dict(role="system", content=system_prompt.format(build_current_date()))
        ])

        # 3. 动态添加工具（自动生成 schema，无需手动创建）
        weather_tool = self._create_tool()
        react_agent.add_tools([weather_tool])

        # 4. 添加工具到 resource_mgr（Runner 需要）
        resource_mgr.tool().add_tool("WeatherReporter", weather_tool)

        result = await Runner.run_agent(react_agent, {"query": "查询杭州的天气"})
        print(f"ReActAgent 最终输出结果：{result}")

    @unittest.skip("require network")
    async def test_react_agent_stream_with_restful_plugin(self):
        """测试 ReAct Agent 流式调用（使用新的动态配置方法）"""
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")

        # 1. 创建最小化配置的 agent
        react_agent_config = ReActAgentConfig(
            id="react_agent_stream",
            version="0.0.1",
            description="AI助手",
            model=self._create_model()
        )

        react_agent: ReActAgent = ReActAgent(react_agent_config)

        # 2. 动态添加 prompt
        system_prompt = "你是一个AI助手，在适当的时候调用合适的工具，帮助我完成任务！今天的日期为：{}\n注意：1. 如果用户请求中未指定具体时间，则默认为今天。"
        react_agent.add_prompt([
            dict(role="system", content=system_prompt.format(build_current_date()))
        ])

        # 3. 动态添加工具
        weather_tool = self._create_tool()
        react_agent.add_tools([weather_tool])

        # 4. 添加工具到 resource_mgr
        resource_mgr.tool().add_tool("WeatherReporter", weather_tool)

        res = Runner.run_agent_streaming(react_agent, {"query": "查询杭州的天气"})
        async for i in res:
            print("ReActAgent 输出结果：", i)

    @unittest.skip("require network")
    async def test_react_agent_invoke_without_runtime(self):
        """测试不传runtime的调用（使用新的动态配置方法）"""
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")

        # 1. 创建最小化配置的 agent
        react_agent_config = ReActAgentConfig(
            id="react_agent_no_runtime",
            version="0.0.1",
            description="AI助手",
            model=self._create_model()
        )

        react_agent: ReActAgent = ReActAgent(react_agent_config)

        # 2. 动态添加 prompt
        system_prompt = "你是一个AI助手，在适当的时候调用合适的工具，帮助我完成任务！今天的日期为：{}\n注意：1. 如果用户请求中未指定具体时间，则默认为今天。"
        react_agent.add_prompt([
            dict(role="system", content=system_prompt.format(build_current_date()))
        ])

        # 3. 动态添加工具
        weather_tool = self._create_tool()
        react_agent.add_tools([weather_tool])

        result = await react_agent.invoke({"query": "查询杭州的天气"})
        print(f"ReActAgent 最终输出结果：{result}")

    @unittest.skip("require network")
    async def test_react_agent_invoke_with_function_plugin(self):
        """测试 ReAct Agent 使用 LocalFunction 插件（使用新的动态配置方法）"""
        os.environ.setdefault("LLM_SSL_VERIFY", "false")

        react_agent_config = ReActAgentConfig(
            id="react_agent_1234",
            version="0.0.2",
            description="AI计算助手",
            model=self._create_model()
        )

        react_agent: ReActAgent = ReActAgent(react_agent_config)

        # 2. 动态添加 prompt
        react_agent.add_prompt([
            dict(role="system", content="你是一个数学计算专家。")
        ])

        # 3. 动态添加工具（自动生成 schema）
        add_tool = self._create_function_tool()
        react_agent.add_tools([add_tool])

        # 4. 添加工具到 resource_mgr
        resource_mgr.tool().add_tool("add", add_tool)

        result = await Runner.run_agent(react_agent, {"query": "计算1+2"})
        print(f"ReActAgent 最终输出结果：{result}")

        # 验证结果
        self.assertIn("output", result)

    @unittest.skip("skip system test")
    async def test_react_agent_invoke_with_annotated_function(self):
        """测试使用tool注解装饰的函数作为工具（使用新的动态配置方法）"""
        os.environ.setdefault("LLM_SSL_VERIFY", "false")

        # 1. 创建最小化配置的 agent（Linus 风格：只传必要参数）
        react_agent_config = ReActAgentConfig(
            id="react_agent_1235",
            version="0.0.3",
            description="AI计算助手（使用注解）",
            model=self._create_model()
        )

        react_agent: ReActAgent = ReActAgent(react_agent_config)

        # 2. 动态添加 prompt
        react_agent.add_prompt([
            dict(role="system", content="你是一个数学计算专家。")
        ])

        # 3. 动态添加工具（使用注解装饰的函数，自动生成 schema）
        annotated_tool = self._create_function_tool_with_annotation()
        react_agent.add_tools([annotated_tool])

        # 4. 添加工具到 resource_mgr
        resource_mgr.tool().add_tool("add", annotated_tool)

        result = await Runner.run_agent(react_agent, {"query": "计算1+2"})
        print(f"ReActAgent 使用注解工具最终输出结果：{result}")

    @unittest.skip("require network")
    async def test_react_agent_stream_with_annotated_function(self):
        """测试使用tool注解装饰的函数作为工具（使用新的动态配置方法）"""
        os.environ.setdefault("LLM_SSL_VERIFY", "false")

        # 1. 创建最小化配置的 agent（Linus 风格：只传必要参数）
        react_agent_config = ReActAgentConfig(
            id="react_agent_1235",
            version="0.0.3",
            description="AI计算助手（使用注解）",
            model=self._create_model()
        )

        react_agent: ReActAgent = ReActAgent(react_agent_config)

        # 2. 动态添加 prompt
        react_agent.add_prompt([
            dict(role="system", content="你是一个数学计算专家。")
        ])

        # 3. 动态添加工具（使用注解装饰的函数，自动生成 schema）
        annotated_tool = self._create_function_tool_with_annotation()
        react_agent.add_tools([annotated_tool])

        # 4. 添加工具到 resource_mgr
        resource_mgr.tool().add_tool("add", annotated_tool)

        result = Runner.run_agent_streaming(react_agent, {"query": "计算1+2"})
        async for i in result:
            print("ReActAgent 输出结果：", i)


if __name__ == "__main__":
    unittest.main()
