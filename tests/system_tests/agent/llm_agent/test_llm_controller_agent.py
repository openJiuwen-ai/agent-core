import asyncio
import os
import unittest
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine

from openjiuwen.core.common import Param
from openjiuwen.core.memory import MemoryChromaVectorStore
from openjiuwen.core.foundation.store import DbBasedKVStore
from openjiuwen.core.retrieval import EmbeddingConfig
from openjiuwen.core.application.llm_agent import create_llm_agent_config, create_llm_agent, LLMAgent
from openjiuwen.core.foundation.llm import ModelConfig, BaseModelInfo, ModelRequestConfig, ModelClientConfig
from openjiuwen.core.workflow import End, WorkflowCard
from openjiuwen.core.workflow import IntentDetectionComponent, IntentDetectionCompConfig
from openjiuwen.core.workflow import LLMComponent, LLMCompConfig
from openjiuwen.core.workflow import Start
from openjiuwen.core.memory.config.config import MemoryEngineConfig, MemoryScopeConfig, AgentMemoryConfig
from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.foundation.store.default_db_store import DefaultDbStore
from openjiuwen.core.runner import Runner
from openjiuwen.core.foundation.tool import LocalFunction
from openjiuwen.core.foundation.tool import RestfulApi, ToolCard, RestfulApiCard
from openjiuwen.core.foundation.tool import tool
from openjiuwen.core.workflow import Workflow

API_BASE = os.getenv("API_BASE", "")
API_KEY = os.getenv("API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
os.environ.setdefault("LLM_SSL_VERIFY", "false")


def build_current_date():
    current_datetime = datetime.now()
    return current_datetime.strftime("%Y-%m-%d")


class LLMAgentTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    @staticmethod
    def _create_model_config():
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
            card=RestfulApiCard(
                name="WeatherReporter",
                description="天气查询插件",
                input_params={
                    "type": "object",
                    "properties": {
                        "location": {"description": "天气查询的地点，必须为英文", "type": "string"},
                        "date": {"description": "天气查询的时间，格式为YYYY-MM-DD", "type": "string"},
                    },
                    "required": ["location", "date"],
                },
                url="http://127.0.0.1:8000/weather",
                headers={},
                method="GET",
            ),
        )
        return weather_plugin

    @staticmethod
    def _create_function_tool():
        weather_plugin = LocalFunction(
            card=ToolCard(
                name="add",
                description="加法",
                input_params={
                    "type": "object",
                    "properties": {
                        "a": {"description": "加数", "type": "number"},
                        "b": {"description": "被加数", "type": "number"},
                    },
                    "required": ["a", "b"],
                },
            ),
            func=lambda a, b: a + b,
        )
        return weather_plugin

    @staticmethod
    @tool(
        card=ToolCard(
            name="add",
            description="加法",
            parameters={
                "type": "object",
                "properties": {
                    "a": {"description": "加数", "type": "number"},
                    "b": {"description": "被加数", "type": "number"},
                },
                "required": ["a", "b"],
            },
        )
    )
    def add_function(a, b):
        """加法函数，使用tool注解装饰"""
        return a + b

    @staticmethod
    def _create_function_tool_with_annotation():
        # 直接返回被tool注解装饰后的函数，它已经是一个LocalFunction对象
        return LLMAgentTest.add_function

    @staticmethod
    def _create_prompt_template():
        system_prompt = "你是一个AI助手，在适当的时候调用合适的工具，帮助我完成任务！今天的日期为：{}\n注意：1. 如果用户请求中未指定具体时间，则默认为今天。"
        return [
            dict(role="system", content=system_prompt.format(build_current_date()))
        ]

    @staticmethod
    def _create_prompt_template_with_memory():
        system_prompt = "你是一个AI助手，在适当的时候调用合适的工具，帮助我完成任务！今天的日期为：{}\n注意：1. 如果用户请求中未指定具体时间，则默认为今天。"
        system_prompt = system_prompt.format(build_current_date())
        system_prompt += "\n以下列出的是用户历史保存的记忆变量\n{{sys_memory_variables}}\n以下列出的是用户的长期记忆信息\n{{sys_long_term_memory}}"
        return [
            dict(role="system", content=system_prompt)
        ]

    @staticmethod
    def _create_function_prompt_template():
        system_prompt = "你是一个数学计算专家。"
        return [
            dict(role="system", content=system_prompt.format(build_current_date()))
        ]

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
            model_client_config=ModelClientConfig(
                client_provider=MODEL_PROVIDER,
                api_key=API_KEY,
                api_base=API_BASE,
                verify_ssl=False
            ),
            model_config=ModelRequestConfig(
                model=MODEL_NAME
            ),
        )

        component = IntentDetectionComponent(config)
        # 定义不同意图下路由到相应的分支链路，如果天气是晴天，则路由到`llm_1`组件所在的分支进行处理，否则路由到`llm_2`组件所在的分支执行非晴天场景的流程
        component.add_branch("${intent.classification_id} == 0", ["end"], "默认分支")
        component.add_branch("${intent.classification_id} == 1", ["llm_1"], "晴天分支")
        component.add_branch("${intent.classification_id} == 2", ["llm_2"], "非晴天分支")

        return component

    def _create_llm_component(self, user_prompt) -> LLMComponent:
        config = LLMCompConfig(
            model_client_config=ModelClientConfig(
                client_provider=MODEL_PROVIDER,
                api_key=API_KEY,
                api_base=API_BASE,
                verify_ssl=False
            ),
            model_config=ModelRequestConfig(
                model=MODEL_NAME
            ),
            template_content=[{"role": "system", "content": "你是一个AI助手。"},
                              {"role": "user", "content": user_prompt}],
            response_format={"type": "text"},
            output_config={
                "output": {"type": "string", "description": "大模型生成的文本", "required": True}
            },
        )
        return LLMComponent(config)

    @staticmethod
    def _create_start_component():
        return Start()

    @staticmethod
    def _create_end_component():
        return End({"responseTemplate": "最终结果为：{{good_weather_output}} {{bad_weather_output}}"})

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
        card = WorkflowCard(
            name=name,
            id=id,
            version=version,
            description="根据天气生成对应文本",
            input_params=dict(
                type="object",
                properties={
                    "query": {
                        "type": "string",
                        "description": "用户输入",
                    }
                },
                required=['query']
            )
        )
        flow = Workflow(card=card)
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

    async def _create_memory_engine(self):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        resource_dir = os.path.join(project_root, "resources")
        os.makedirs(resource_dir, exist_ok=True)
        kv_path = os.path.join(resource_dir, "kv_store.db")
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{kv_path}",
            pool_pre_ping=True,
            echo=False,
        )
        kv_store = DbBasedKVStore(engine)
        db_store = DefaultDbStore(engine)
        vector_store = MemoryChromaVectorStore(persist_directory="./resource_dir")
        memory_engine = LongTermMemory()
        await memory_engine.register_store(kv_store=kv_store, db_store=db_store, vector_store=vector_store)
        memory_engine.set_config(MemoryEngineConfig(
            default_model_cfg=ModelRequestConfig(),
            default_model_client_cfg=ModelClientConfig(
                client_provider="SiliconFlow",
                api_key="xxx",
                api_base="xxx",
                verify_ssl=False
            ),
        ))
        return memory_engine

    def _create_memory_scope_config(self):
        return MemoryScopeConfig(
            model_cfg=ModelRequestConfig(
                model=MODEL_NAME
            ),
            model_client_cfg=ModelClientConfig(
                client_provider=MODEL_PROVIDER,
                api_key=API_KEY,
                api_base=API_BASE,
                verify_ssl=False
            ),
            embedding_cfg=EmbeddingConfig(
                model_name=os.getenv("EMBED_MODEL_NAME"),
                api_key=os.getenv("EMBED_API_KEY"),
                base_url=os.getenv("EMBED_API_BASE")
            )
        )

    @unittest.skip("skip system test")
    async def test_llm_agent_invoke_with_real_plugin(self):
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")
        model_config = self._create_model_config()
        prompt_template = self._create_prompt_template()

        llm_agent_config = create_llm_agent_config(
            agent_id="react_agent_123",
            agent_version="0.0.1",
            description="AI助手",
            plugins=[],
            workflows=[],
            model=model_config,
            prompt_template=prompt_template,
            tools=[]
        )

        llm_agent: LLMAgent = create_llm_agent(
            agent_config=llm_agent_config,
            workflows=[],
            tools=[]
        )

        # 动态添加工具
        llm_agent.add_tools([self._create_tool()])

        # 调用
        result = await llm_agent.invoke({"query": "查询杭州的天气"})
        print(f"LLMAgent 输出结果：{result}")

    @unittest.skip("skip system test")
    async def test_llm_agent_stream_with_real_plugin(self):
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")
        model_config = self._create_model_config()
        prompt_template = self._create_prompt_template()

        llm_agent_config = create_llm_agent_config(
            agent_id="react_agent_123",
            agent_version="0.0.1",
            description="AI助手",
            plugins=[],
            workflows=[],
            model=model_config,
            prompt_template=prompt_template,
            tools=[]
        )

        llm_agent: LLMAgent = create_llm_agent(
            agent_config=llm_agent_config,
            workflows=[],
            tools=[]
        )

        llm_agent.add_tools([self._create_tool()])
        res = llm_agent.stream({"query": "查询杭州的天气"})
        async for i in res:
            print("LLMAgent 输出结果：", i)

    @unittest.skip("skip system test")
    async def test_llm_agent_invoke_with_real_function_plugin(self):
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")
        model_config = self._create_model_config()
        prompt_template = self._create_function_prompt_template()

        llm_agent_config = create_llm_agent_config(
            agent_id="react_agent_1234",
            agent_version="0.0.2",
            description="AI计算助手",
            plugins=[],
            workflows=[],
            model=model_config,
            prompt_template=prompt_template,
            tools=[]
        )

        llm_agent: LLMAgent = create_llm_agent(
            agent_config=llm_agent_config,
            workflows=[],
            tools=[]
        )

        llm_agent.add_tools([self._create_function_tool()])
        result = await llm_agent.invoke({"query": "计算1+2"})
        print(f"LLMAgent 最终输出结果：{result}")

    @unittest.skip("skip system test")
    async def test_llm_agent_invoke_with_annotated_function_plugin(self):
        """测试使用tool注解装饰的函数作为工具"""
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")
        model_config = self._create_model_config()
        prompt_template = self._create_function_prompt_template()

        llm_agent_config = create_llm_agent_config(
            agent_id="react_agent_1235",
            agent_version="0.0.3",
            description="AI计算助手（使用注解）",
            plugins=[],
            workflows=[],
            model=model_config,
            prompt_template=prompt_template,
            tools=[]
        )

        # 使用tool注解创建的LocalFunction对象
        llm_agent: LLMAgent = create_llm_agent(
            agent_config=llm_agent_config,
            workflows=[],
            tools=[]
        )

        llm_agent.add_tools([self._create_function_tool_with_annotation()])
        result = await llm_agent.invoke({"query": "计算1+2"})
        print(f"LLMAgent 最终输出结果：{result}")

    @unittest.skip("skip system test")
    async def test_llm_agent_invoke_with_workflow(self):
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")
        model_config = self._create_model_config()
        prompt_template = self._create_prompt_template()
        workflow = self._create_workflow()

        llm_agent_config = create_llm_agent_config(
            agent_id="react_agent_123",
            agent_version="0.0.1",
            description="AI助手",
            plugins=[],
            workflows=[],
            model=model_config,
            prompt_template=prompt_template
        )
        llm_agent: LLMAgent = create_llm_agent(
            agent_config=llm_agent_config,
            workflows=[],
        )

        llm_agent.add_workflows([workflow])
        result = await llm_agent.invoke({"query": "今天上海天气很差，请生成一段文本"})
        print(f"LLMAgent 最终输出结果：{result}")

    @unittest.skip("skip system test")
    async def test_llm_agent_stream_with_workflow(self):
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")
        model_config = self._create_model_config()
        prompt_template = self._create_prompt_template()
        workflow = self._create_workflow()

        llm_agent_config = create_llm_agent_config(
            agent_id="react_agent_123",
            agent_version="0.0.1",
            description="AI助手",
            plugins=[],
            workflows=[],
            model=model_config,
            prompt_template=prompt_template
        )
        llm_agent: LLMAgent = create_llm_agent(
            agent_config=llm_agent_config,
            workflows=[]
        )

        llm_agent.add_workflows([workflow])
        result = llm_agent.stream({"query": "今天上海天气晴朗，温度适宜，请生成一段文本"})

        async for i in result:
            print("LLMAgent 输出结果：", i)

    # This ut should be at the bottom, singleton memory engine is created from this ut
    @unittest.skip("skip system test")
    async def test_llm_agent_with_memory(self):
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")
        user_id = "default_user_id"
        scope_id = "react_agent_123"
        model_config = self._create_model_config()
        prompt_template = self._create_prompt_template_with_memory()
        memory_engine = await self._create_memory_engine()
        llm_agent_config = create_llm_agent_config(
            agent_id="react_agent_123",
            agent_version="0.0.1",
            description="AI助手",
            plugins=[],
            workflows=[],
            model=model_config,
            prompt_template=prompt_template,
            tools=[]
        )
        llm_agent_config.agent_memory_config = AgentMemoryConfig(
            mem_variables=[
                Param.string(name="name", description="用户的姓名", required=False),
                Param.string(name="career", description="用户的职业", required=False),
            ],
            enable_long_term_mem=True
        )
        await memory_engine.set_scope_config(scope_id, self._create_memory_scope_config())

        # no scope id in llm_agent_config, should not get memory and add memory
        llm_agent_no_scope_id: LLMAgent = create_llm_agent(
            agent_config=llm_agent_config,
            workflows=[],
            tools=[]
        )
        result = await llm_agent_no_scope_id.invoke({"query": "我叫张明，目前刚到杭州来杭州做软件开发工作",
                                         "user_id": user_id})
        print(f"LLMAgent 输出结果：{result}")
        await asyncio.sleep(10)
        result = await memory_engine.get_variables(user_id=user_id, scope_id=scope_id)
        print(f"memory variables: {result}")
        self.assertEqual(len(result), 0)
        result = await memory_engine.search_user_mem(user_id=user_id, scope_id=scope_id, query="我叫什么名字", num=1)
        self.assertEqual(len(result), 0)

        # set wrong scope id in llm_agent_config, should call llm failed
        llm_agent_config.memory_scope_id = "wrong_scope_id"
        llm_agent_wrong_scope_id: LLMAgent = create_llm_agent(
            agent_config=llm_agent_config,
            workflows=[],
            tools=[]
        )
        result = await llm_agent_wrong_scope_id.invoke({"query": "我叫张明，目前刚到杭州来杭州做软件开发工作",
                                         "user_id": user_id})
        print(f"LLMAgent 输出结果：{result}")
        await asyncio.sleep(10)
        result = await memory_engine.get_variables(user_id=user_id, scope_id=scope_id)
        print(f"memory variables: {result}")
        self.assertEqual(len(result), 0)
        result = await memory_engine.search_user_mem(user_id=user_id, scope_id=scope_id, query="我叫什么名字", num=1)
        self.assertEqual(len(result), 0)

        # set scope id in llm_agent_config, should get memory and add memory
        llm_agent_config.memory_scope_id = scope_id
        llm_agent: LLMAgent = create_llm_agent(
            agent_config=llm_agent_config,
            workflows=[],
            tools=[]
        )

        # 调用
        result = await llm_agent.invoke({"query": "我叫张明，目前刚到杭州来杭州做软件开发工作",
                                         "user_id": user_id})
        print(f"LLMAgent 输出结果：{result}")
        await asyncio.sleep(30)
        result = await llm_agent.invoke({"query": "我叫什么名字",
                                         "user_id": user_id})
        print(f"LLMAgent 输出结果：{result}")
        result = await memory_engine.get_variables(user_id=user_id, scope_id=scope_id)
        print(f"memory variables: {result}")
        self.assertEqual(len(result), 2)
        result = await memory_engine.search_user_mem(user_id=user_id, scope_id=scope_id, query="我叫什么名字", num=1)
        self.assertEqual(len(result), 1)  # may be [] is llm_agent.invoke return too fast
        print("memory result:", result[0])

    @unittest.skip("skip system test")
    async def test_llm_agent_with_multi_memory(self):
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")
        user_id = "default_user_id"
        scope_id = "react_agent_123"
        model_config = self._create_model_config()
        prompt_template = self._create_prompt_template()
        memory_engine = await self._create_memory_engine()
        llm_agent_config = create_llm_agent_config(
            agent_id="react_agent_123",
            agent_version="0.0.1",
            description="AI助手",
            plugins=[],
            workflows=[],
            model=model_config,
            prompt_template=prompt_template,
            tools=[]
        )
        llm_agent_config.memory_scope_id = scope_id
        await memory_engine.set_scope_config(scope_id, self._create_memory_scope_config())
        llm_agent_config.agent_memory_config = AgentMemoryConfig(
            mem_variables=[],
            enable_long_term_mem=True
        )
        llm_agent: LLMAgent = create_llm_agent(
            agent_config=llm_agent_config,
            workflows=[],
            tools=[]
        )

        # 调用
        queries = ["我叫张明", "我喜欢运动", "我今年20岁", "我的工作是软件工程师", "我来自杭州"]
        for query in queries:
            result = await llm_agent.invoke({"query": query,
                                             "user_id": user_id})
            print(f"LLMAgent 输出结果：{result}")
        await asyncio.sleep(30)
        result = await memory_engine.get_user_mem_by_page(user_id=user_id, scope_id=scope_id, page_size=4, page_idx=1)
        print(f"page1: memory result:{result}")
        result = await memory_engine.get_user_mem_by_page(user_id=user_id, scope_id=scope_id, page_size=4, page_idx=2)
        print(f"page2: memory result:{result}")
        result = await memory_engine.get_user_mem_by_page(user_id=user_id, scope_id=scope_id, page_size=99, page_idx=1)
        print(f"total memory result:{result}")

    @unittest.skip("skip system test")
    async def test_llm_agent_only_with_memory_variable(self):
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")
        user_id = "default_user_id_var"
        scope_id = "react_agent_123_var"
        model_config = self._create_model_config()
        prompt_template = self._create_prompt_template_with_memory()
        memory_engine = await self._create_memory_engine()
        llm_agent_config = create_llm_agent_config(
            agent_id="react_agent_123_var",
            agent_version="0.0.1",
            description="AI助手",
            plugins=[],
            workflows=[],
            model=model_config,
            prompt_template=prompt_template,
            tools=[]
        )
        llm_agent_config.memory_scope_id = scope_id
        await memory_engine.set_scope_config(scope_id, self._create_memory_scope_config())
        llm_agent_config.agent_memory_config = AgentMemoryConfig(
            mem_variables=[
                Param.string(name="name", description="用户的姓名", required=False),
                Param.string(name="career", description="用户的职业", required=False),
            ],
            enable_long_term_mem=False
        )
        llm_agent: LLMAgent = create_llm_agent(
            agent_config=llm_agent_config,
            workflows=[],
            tools=[]
        )

        # 调用
        result = await llm_agent.invoke({"query": "我叫张明，目前刚到杭州来杭州做软件开发工作",
                                         "user_id": user_id})
        print(f"LLMAgent 输出结果：{result}")
        await asyncio.sleep(30)
        result = await llm_agent.invoke({"query": "我叫什么名字",
                                         "user_id": user_id})
        print(f"LLMAgent 输出结果：{result}")
        result = await memory_engine.get_variables(user_id=user_id, scope_id=scope_id)
        print(f"mem_variables:{result}")
        self.assertEqual(len(result), 2)
