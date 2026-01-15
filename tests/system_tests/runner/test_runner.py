import asyncio
import os
import unittest
from datetime import datetime
from typing import List
from unittest.mock import patch, AsyncMock

from mcp import StdioServerParameters

from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.single_agent.legacy import WorkflowAgentConfig, WorkflowSchema
from openjiuwen.core.foundation.llm import ModelConfig, BaseModelInfo
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import IntentDetectionComponent, IntentDetectionCompConfig
from openjiuwen.core.workflow import QuestionerComponent, FieldInfo, QuestionerConfig
from openjiuwen.core.workflow import Start
from openjiuwen.core.runner import Runner
from openjiuwen.core.workflow import generate_workflow_key
from openjiuwen.core.session import BaseSession
from openjiuwen.core.session import TaskSession
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.foundation.tool import McpToolCard
from openjiuwen.core.foundation.tool import SseClient, StdioClient, PlaywrightClient
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow import WorkflowCard

API_BASE = "https://mock.com/v1"
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
os.environ.setdefault("LLM_SSL_VERIFY", "false")

SYSTEM_PROMPT_TEMPLATE = "你是一个query改写的AI助手。今天的日期是{}。"

def build_current_date():
    current_datetime = datetime.now()
    return current_datetime.strftime("%Y-%m-%d")

class TestRunner(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        try:
            _, workflow = self._build_interrupt_workflow()
            self.workflow = workflow
            Runner.resource_mgr.add_workflow(
                WorkflowCard(id=generate_workflow_key(workflow.card.id, workflow.card.version)), workflow)
        except Exception:
            pass
        await Runner.start()

    async def asyncTearDown(self):
        try:
            Runner.resource_mgr.remove_workflow(
                generate_workflow_key(self.workflow.card.id, self.workflow.card.version))
            await Runner.stop()
        except Exception:
            pass

    @staticmethod
    def _create_model_config() -> ModelConfig:
        """根据环境变量构造模型配置。"""
        return ModelConfig(
            model_provider="siliconflow",
            model_info=BaseModelInfo(
                model=MODEL_NAME,
                api_base=API_BASE,
                api_key=API_KEY,
                temperature=0.7,
                top_p=0.9,
                timeout=120,  # 增加超时时间到120秒，避免网络问题
            ),
        )

    @staticmethod
    def _create_intent_detection_component() -> IntentDetectionComponent:
        """创建意图识别组件。"""
        model_config = TestRunner._create_model_config()
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
            category_name_list=["查询某地天气"],
            model=model_config,
        )
        component = IntentDetectionComponent(config)
        component.add_branch("${intent.classification_id} == 0", ["end"], "默认分支")
        component.add_branch("${intent.classification_id} == 1", ["questioner"], "查询天气分支")
        return component

    @staticmethod
    def _create_questioner_component() -> QuestionerComponent:
        """创建信息收集组件。"""
        key_fields = [
            FieldInfo(field_name="location", description="地点", required=True),
            FieldInfo(
                field_name="date",
                description="时间",
                required=True,
                default_value="today",
            ),
        ]
        model_config = TestRunner._create_model_config()
        config = QuestionerConfig(
            model=model_config,
            question_content="",
            extract_fields_from_response=True,
            field_names=key_fields,
            with_chat_history=False,
        )
        return QuestionerComponent(config)

    @staticmethod
    def _create_start_component():
        return Start({"inputs": [{"id": "query", "type": "String", "required": "true", "sourceType": "ref"}]})

    @staticmethod
    def _create_end_component():
        return End({"responseTemplate": "{{output}}"})

    def _build_interrupt_workflow(self) -> tuple[BaseSession, Workflow]:
        """
        构建包含交互式组件的工作流，用于测试中断恢复功能。

        返回 (context, workflow) 二元组，可直接用于 invoke。
        """
        # 1. 初始化工作流与上下文
        id = "test_interrupt_workflow"
        version = "1.0"
        name = "interrupt_test"
        card = WorkflowCard(
                name=name,
                id=id,
                version=version,
        )
        flow = Workflow(
            card=card
        )
        context = TaskSession(trace_id="test")

        # 2. 实例化各组件
        start = self._create_start_component()
        intent = self._create_intent_detection_component()
        questioner = self._create_questioner_component()
        end = self._create_end_component()

        # 3. 注册组件到工作流
        flow.set_start_comp(
            "start",
            start,
            inputs_schema={"query": "${query}"},
        )
        flow.add_workflow_comp(
            "intent",
            intent,
            inputs_schema={"query": "${start.query}"},
        )
        flow.add_workflow_comp(
            "questioner",
            questioner,
            inputs_schema={"query": "${start.query}"}
        )
        flow.set_end_comp("end", end, inputs_schema={"output": "${questioner.location}"})

        # 4. 连接拓扑
        flow.add_connection("start", "intent")
        # intent 组件通过分支路由自动连接到 questioner 或 end
        flow.add_connection("questioner", "end")

        return context.create_workflow_session(), flow

    @staticmethod
    def _create_workflow_schema(id, name: str, version: str) -> WorkflowSchema:
        return WorkflowSchema(id=id,
                              name=name,
                              description="天气查询工作流",
                              version=version,
                              inputs={"query": {
                                  "type": "string",
                              }})

    def _create_agent(self, workflow):
        """根据 workflow 实例化 WorkflowAgent。"""
        from openjiuwen.core.application.workflow_agent import WorkflowAgent
        workflow_id = workflow.card.id
        workflow_name = workflow.card.name
        workflow_version = workflow.card.version
        schema = self._create_workflow_schema(workflow_id, workflow_name, workflow_version)
        config = WorkflowAgentConfig(
            id="test_weather_agent",
            version="0.1.0",
            description="测试用天气 single_agent",
            workflows=[schema],
        )
        agent = WorkflowAgent(config)
        return agent

    def _test_interaction_detection(self, result, method_name):
        """检测交互请求的通用方法"""
        if isinstance(result, List) and isinstance(result[0], OutputSchema) and result[0].type == '__interaction__':
            print(f"✅ {method_name} 检测到交互请求!")
            return result
        return []

    @unittest.skip("skip system test - requires network")
    async def test_workflow_agent_invoke_with_interrupt_recovery(self):
        """端到端测试：WorkflowAgent.invoke 带中断恢复逻辑。"""
        print("=== 测试 WorkflowAgent.invoke 方法 ===")
        agent = self._create_agent(self.workflow)

        # 第一次调用 - 应该触发中断（设置30秒超时）
        try:
            result = await asyncio.wait_for(
                Runner.run_agent(agent, {"query": "查询天气", "conversation_id": "c123"}),
                timeout=50.0
            )
        except asyncio.TimeoutError:
            print("❌ 第一次调用超时！")
            raise
        print(f"Workflow Agent第一次输出结果 >>> {result}")

        # 校验第一次调用结果：应该返回交互请求
        self.assertIsInstance(result, list, "第一次调用应该返回交互请求列表")
        self.assertEqual(result[0].type, '__interaction__', "应该返回交互类型")
        print(f"✅ 第一次调用校验通过：返回交互请求")

        interaction_outputs = self._test_interaction_detection(result, "invoke")
        if interaction_outputs:
            print("检测到交互请求，准备进行中断恢复...")
            # 注意：外部调用者只传入字符串，不需要手动创建 InteractiveInput
            # WorkflowMessageHandler 会根据中断状态自动封装

            # 第二次调用 - 传入字符串格式的回答，agent内部会自动处理中断恢复
            try:
                result2 = await asyncio.wait_for(
                    Runner.run_agent(agent, {"query": "上海", "conversation_id": "c123"}),
                    timeout=30.0
                )
            except asyncio.TimeoutError:
                print("❌ 第二次调用（恢复）超时！")
                raise
            print(f"Workflow Agent中断恢复后输出结果 >>> {result2}")

            # 校验第二次调用结果：应该返回完成状态
            self.assertIsInstance(result2, dict, "第二次调用应该返回字典")
            self.assertEqual(result2['result_type'], 'answer', "应该返回answer类型")
            self.assertEqual(result2['output'].state.value, 'COMPLETED', "工作流应该完成")
            self.assertEqual(result2['output'].result['responseContent'], '上海', "应该返回上海")
            print(f"✅ 第二次调用校验通过：工作流完成，返回结果正确")

            return result, result2  # 返回结果用于比对
        else:
            print("未检测到交互请求，测试可能未按预期执行")
            self.fail("应该检测到交互请求")

    @unittest.skip("skip system test - requires network")
    async def test_runner_agent_resource_management(self):
        """端到端测试：验证Runner的资源管理功能 - 通过Runner.add_agent添加智能体并执行，包含交互流程。"""
        print("=== 测试 Runner 资源管理功能 ===")

        # 创建智能体
        agent_id = "test_resource_agent"
        agent = self._create_agent(self.workflow)
        conversation_id = "c124"

        try:
            # 1. 测试添加智能体
            print(f"Step 1: 通过Runner.add_agent添加智能体，ID: {agent_id}")
            Runner.resource_mgr.add_agent(AgentCard(id=agent_id), agent=agent)
            print("✅ 智能体添加成功")

            # 2. 测试通过ID运行智能体 - 第一次调用，获取交互请求
            print("Step 2: 通过智能体ID运行智能体（第一次调用，获取交互请求）")
            try:
                # 第一次调用 - 应该触发中断
                result = await asyncio.wait_for(
                    Runner.run_agent(agent_id, {"query": "查询天气", "conversation_id": conversation_id}),
                    timeout=50.0
                )
                print(f"Runner运行智能体结果（第一次调用）>>> {result}")

                # 校验第一次调用结果：应该返回交互请求
                self.assertIsInstance(result, list, "第一次调用应该返回交互请求列表")
                self.assertEqual(result[0].type, '__interaction__', "应该返回交互类型")
                print("✅ 第一次调用校验通过：返回交互请求")

                # 检查交互请求是否正确
                interaction_outputs = self._test_interaction_detection(result, "run_agent")
                if interaction_outputs:
                    print("检测到交互请求，准备进行第二次调用...")

                    # 3. 第二次调用 - 传入字符串格式的回答，完成工作流
                    print("Step 3: 第二次调用，传入回答")
                    try:
                        result2 = await asyncio.wait_for(
                            Runner.run_agent(agent_id, {"query": "上海", "conversation_id": conversation_id}),
                            timeout=30.0
                        )
                        print(f"Runner运行智能体结果（第二次调用）>>> {result2}")

                        # 校验第二次调用结果：应该返回完成状态
                        self.assertIsInstance(result2, dict, "第二次调用应该返回字典")
                        self.assertEqual(result2['result_type'], 'answer', "应该返回answer类型")
                        self.assertEqual(result2['output'].state.value, 'COMPLETED', "工作流应该完成")
                        self.assertEqual(result2['output'].result['responseContent'], '上海', "应该返回上海")
                        print("✅ 第二次调用校验通过：工作流完成，返回结果正确")

                    except asyncio.TimeoutError:
                        print("❌ 第二次调用超时！")
                        raise
                    except Exception as e:
                        print(f"❌ 第二次调用时发生错误: {e}")
                        raise
                else:
                    print("未检测到交互请求，测试可能未按预期执行")
                    self.fail("应该检测到交互请求")

                # 4. 测试移除智能体
                print("Step 4: 移除智能体")
                Runner.resource_mgr.remove_agent(id=agent_id)
                print("✅ 智能体移除成功")

                # 5. 测试移除后再次运行应失败
                print("Step 5: 验证移除后再次运行智能体应失败")
                with self.assertRaises(Exception):
                    await Runner.run_agent(agent_id, {"query": "查询天气", "conversation_id": conversation_id})
                print("✅ 验证通过：移除后的智能体无法运行")

            except asyncio.TimeoutError:
                print("❌ 运行智能体超时！")
                raise
            except Exception as e:
                print(f"❌ 运行智能体时发生错误: {e}")
                raise

        finally:
            # 清理资源，确保即使测试失败也移除智能体
            try:
                Runner.resource_mgr.remove_agent(id=agent_id)
            except:
                pass
            print("✅ 测试完成，资源清理")

    @unittest.skip("skip system test")
    async def test_mcp_tools_sse(self):
        """
        端到端测试 MCP-SSE 工具生命周期：
        连接 → 拉取工具 → 调用工具 → 移除服务器
        全程仅 mock SseClient 四个公共方法，断言调用参数。
        """
        # -------------------- 预置数据 --------------------
        mock_tools = [
            McpToolCard(
                name="browser_navigate",
                description="Navigate to a URL",
                input_params={
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "The URL to navigate to"}},
                    "required": ["url"],
                },
            ),
            McpToolCard(
                name="browser_extract_text",
                description="Extract text from the current page",
                input_params={
                    "type": "object",
                    "properties": {"selector": {"type": "string", "description": "CSS selector for the element"}},
                    "required": ["selector"],
                },
            ),
        ]
        mock_tool_result = "Successfully navigated to example.com and extracted title: Example Domain"
        test_inputs = {"url": "https://example.com"}

        # -------------------- mock 配置 --------------------
        with patch("openjiuwen.core.protocols.mcp.SseClient.connect", AsyncMock(return_value=True)), \
                patch("openjiuwen.core.protocols.mcp.SseClient.disconnect", AsyncMock(return_value=True)), \
                patch("openjiuwen.core.protocols.mcp.SseClient.list_tools", AsyncMock(return_value=mock_tools)), \
                patch.object(SseClient, "call_tool", AsyncMock(return_value=mock_tool_result)) as mock_call_tool:
            # -------------------- 服务器配置 --------------------
            mcp_server_config = McpServerConfig(
                server_name="browser-use-server",
                server_path="http://127.0.0.1:8930/sse",
                client_type="sse",
            )

            # -------------------- 添加到管理器 --------------------
            ok_list = await Runner.resource_mgr.add_tool_servers([mcp_server_config])
            assert ok_list == [True]

            # -------------------- 工具列表校验 --------------------
            server_tools = Runner.resource_mgr.get_tool_infos(tool_server_name="browser-use-server")
            assert len(server_tools) == 2
            assert server_tools[0].name == "browser-use-server.browser_navigate"

            # -------------------- Runner 拉取工具 --------------------
            tools = await Runner.resource_mgr.get_mcp_tool_infos(server_name="browser-use-server")
            assert len(tools) == 2
            first_tool = tools[0]
            tool_id = first_tool.name

            # -------------------- 调用工具 --------------------
            tool = Runner.resource_mgr.get_tool(tool_id)
            result = await tool.invoke(test_inputs)

            # -------------------- 实例级调用断言 --------------------
            mock_call_tool.assert_awaited_once_with(
                tool_name="browser_navigate",
                arguments=test_inputs,
            )

            # -------------------- 结果校验 --------------------
            assert result and "error" not in str(result).lower()
            if isinstance(result, dict) and "result" in result:
                assert result["result"] == mock_tool_result

            # -------------------- 移除服务器 --------------------
            await Runner.resource_mgr.remove_mcp_server("browser-use-server")
            empty_tools = await Runner.resource_mgr.get_mcp_tool_infos(tool_server_name="browser-use-server")
            assert empty_tools == None

            return True

    @unittest.skip("skip system test")
    async def test_mcp_tools_stdio(self):
        """
        端到端测试 MCP-stdio 工具生命周期：
        连接 → 拉取工具 → 调用工具 → 移除服务器
        全程仅 mock StdioClient 四个公共方法，断言调用参数。
        """
        # -------------------- 预置数据 --------------------
        mock_tools = [
            McpToolCard(
                name="doubter",
                description="Doubter tool via stdio",
                input_params={
                    "type": "object",
                    "properties": {"history": {"type": "string", "description": "Agent action history"}},
                    "required": ["history"],
                },
            ),
            McpToolCard(
                name="checker",
                description="Checker tool via stdio",
                input_params={
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "URL to check"}},
                    "required": ["url"],
                },
            ),
        ]
        mock_tool_result = "score: 0.85, decision: ACCEPT, review: actions verified"
        test_inputs = {"history": "single_agent navigated to example.com and extracted title"}

        # -------------------- mock 配置 --------------------
        with patch("openjiuwen.core.protocols.mcp.StdioClient.connect", AsyncMock(return_value=True)), \
                patch("openjiuwen.core.protocols.mcp.StdioClient.disconnect", AsyncMock(return_value=True)), \
                patch("openjiuwen.core.protocols.mcp.StdioClient.list_tools", AsyncMock(return_value=mock_tools)), \
                patch.object(StdioClient, "call_tool", AsyncMock(return_value=mock_tool_result)) as mock_call_tool:
            # -------------------- 服务器配置 --------------------
            # 参数内容可以是任意占位符，真实值不会被用到
            mcp_server_config = McpServerConfig(
                server_name="doubter-mcp-server",
                server_path="",
                params=dict(StdioServerParameters(command="python", args=["dummy.py"])),
                client_type="stdio",
            )

            # -------------------- 添加到管理器 --------------------
            ok_list = await Runner.resource_mgr.add_tool_servers([mcp_server_config])
            assert len(ok_list) == 1
            assert ok_list[0].is_ok() == True


            # -------------------- 工具列表校验 --------------------
            server_tools = await Runner.resource_mgr.get_mcp_tool_infos(tool_server_name="doubter-mcp-server")
            assert len(server_tools) == 2
            assert server_tools[0].name == "doubter-mcp-server.doubter"

            # -------------------- Runner 拉取工具 --------------------
            tools = await Runner.resource_mgr.get_mcp_tool_infos(server_name="doubter-mcp-server")
            assert len(tools) == 2
            first_tool = tools[0]
            tool_id = first_tool.name

            # -------------------- 调用工具 --------------------
            tool = Runner.resource_mgr.get_tool(tool_id)
            result = await tool.invoke(test_inputs)

            # -------------------- 实例级调用断言 --------------------
            mock_call_tool.assert_awaited_once_with(
                tool_name="doubter",
                arguments=test_inputs,
            )

            # -------------------- 结果校验 --------------------
            assert result and "error" not in str(result).lower()
            if isinstance(result, dict) and "result" in result:
                assert result["result"] == mock_tool_result

            # -------------------- 移除服务器 --------------------
            await Runner.resource_mgr.remove_mcp_server("doubter-mcp-server")
            empty_tools = await Runner.resource_mgr.get_mcp_tool_infos(tool_server_name="doubter-mcp-server")
            assert empty_tools == None

            return True

    @unittest.skip("skip system test")
    async def test_mcp_tools_playwright(self):
        """
        端到端测试 MCP-Playwright 工具生命周期：
        连接 → 拉取工具 → 调用工具 → 移除服务器
        全程仅 mock PlaywrightClient 四个公共方法，断言调用参数。
        """
        # -------------------- 预置数据 --------------------
        mock_tools = [
            McpToolCard(
                name="browser_navigate",
                description="Navigate to a URL via Playwright",
                input_params={
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "The URL to navigate to"}},
                    "required": ["url"],
                },
            ),
            McpToolCard(
                name="browser_click",
                description="Click an element via Playwright",
                input_params={
                    "type": "object",
                    "properties": {"selector": {"type": "string", "description": "CSS selector"}},
                    "required": ["selector"],
                },
            ),
        ]
        mock_tool_result = "Navigated to https://example.com and clicked button"
        test_inputs = {"url": "https://example.com"}

        # -------------------- mock 配置 --------------------
        with patch("openjiuwen.core.protocols.mcp.PlaywrightClient.connect", AsyncMock(return_value=True)), \
                patch("openjiuwen.core.protocols.mcp.PlaywrightClient.disconnect", AsyncMock(return_value=True)), \
                patch("openjiuwen.core.protocols.mcp.PlaywrightClient.list_tools",
                      AsyncMock(return_value=mock_tools)), \
                patch.object(PlaywrightClient, "call_tool", AsyncMock(return_value=mock_tool_result)) as mock_call_tool:
            # -------------------- 服务器配置 --------------------
            # 可以是 URL 或 StdioServerParameters，PlaywrightClient 内部自动识别
            mcp_server_config = McpServerConfig(
                server_name="playwright-mcp-server",
                server_path="http://127.0.0.1:8931/sse",  # 实际不会发起网络，仅占位
                client_type="playwright",
            )

            # -------------------- 添加到管理器 --------------------
            ok_list = await Runner.resource_mgr.add_tool_servers([mcp_server_config])
            assert ok_list[0].is_ok() == True

            # -------------------- 工具列表校验 --------------------
            server_tools = await Runner.resource_mgr.get_mcp_tool_infos(tool_server_name="playwright-mcp-server")
            assert len(server_tools) == 2
            assert server_tools[0].name == "playwright-mcp-server.browser_navigate"

            # -------------------- Runner 拉取工具 --------------------
            tools = await Runner.resource_mgr.get_mcp_tool_infos("playwright-mcp-server")
            assert len(tools) == 2
            first_tool = tools[0]
            tool_id = first_tool.name

            # -------------------- 调用工具 --------------------
            tool = Runner.resource_mgr.get_tool(tool_id)
            result = await tool.invoke(test_inputs)

            # -------------------- 实例级调用断言 --------------------
            mock_call_tool.assert_awaited_once_with(
                tool_name="browser_navigate",
                arguments=test_inputs,
            )

            # -------------------- 结果校验 --------------------
            assert result and "error" not in str(result).lower()
            if isinstance(result, dict) and "result" in result:
                assert result["result"] == mock_tool_result

            # -------------------- 移除服务器 --------------------
            await Runner.resource_mgr.remove_mcp_server("playwright-mcp-server")
            empty_tools = Runner.resource_mgr.get_mcp_tool_infos(tool_server_name="playwright-mcp-server")
            assert empty_tools == None

            return True
        
    @unittest.skip("skip system test - requires network")
    async def test_connect_and_list_tools_with_query_ak(self):
        """End-to-end test: SSE client connection and tool listing with ak query parameter (set via env vars)."""
        
        server_path = os.getenv("MCP_SERVER_PATH", "https://mcp.example.com/sse")
        query_key = os.getenv("MCP_AUTH_QUERY_PARAM_KEY", "ak")
        query_value = os.getenv("MCP_AUTH_QUERY_PARAM_VALUE", "your-ak")

        config = McpServerConfig(
            server_name="example-mcp-server",
            server_path=server_path,
            client_type="sse",
            auth_query_params={query_key: query_value}
        )

        client = SseClient(config.server_path, config.server_name,
                           auth_query_params=config.auth_query_params)

        try:
            connected = await asyncio.wait_for(client.connect(timeout=60), timeout=60)
            self.assertTrue(connected, "Should connect to the MCP SSE server with ak query parameter")

            tools = await asyncio.wait_for(client.list_tools(timeout=60), timeout=60)
            self.assertIsInstance(tools, list)
            self.assertGreater(len(tools), 0, "Expected the server to return at least one tool")
        finally:
            await asyncio.wait_for(client.disconnect(timeout=15), timeout=15)
            