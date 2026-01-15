import asyncio

import pytest

from openjiuwen.core.single_agent.legacy import AgentConfig, AgentSession
from openjiuwen.core.session import Config
from openjiuwen.core.session import Session
from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.stream import CustomSchema
from openjiuwen.core.workflow import Workflow
from tests.unit_tests.core.session.tracer.mock_node_with_tracer import StreamNodeWithTracer
from tests.unit_tests.core.session.tracer.test_workflow_tracer import record_tracer_info
from tests.unit_tests.core.workflow.mock_nodes import MockStartNode, MockEndNode


class MockLLM:
    def __init__(self, tracer):
        self.tracer = tracer

    async def stream(self, span):
        try:
            await self.tracer.trigger("tracer_agent", "on_llm_start", span=span, inputs={"llm": "mock llm"},
                                      instance_info={"class_name": "Openai"})
            await asyncio.sleep(2)
        except Exception as e:
            await self.tracer.trigger("tracer_agent", "on_llm_error", span=span, error=e,
                                      )
            raise e
        finally:
            await self.tracer.trigger("tracer_agent", "on_llm_end", span=span, outputs={"outputs": "mock llm"},
                                      )


class MockPlugin:
    def __init__(self, tracer):
        self.tracer = tracer

    async def stream(self, span):
        try:
            await self.tracer.trigger("tracer_agent", "on_plugin_start", span=span, inputs={"llm": "mock tool"},
                                      instance_info={"class_name": "RestFulAPI"})
            await asyncio.sleep(2)
        except Exception as e:
            await self.tracer.trigger("tracer_agent", "on_plugin_error", span=span, error=e,
                                      )
            raise e
        finally:
            await self.tracer.trigger("tracer_agent", "on_plugin_end", span=span, outputs={"outputs": "mock tool"},
                                      )


class TestAgent:

    """
    Agent(llm -> tool -> workflow)
    """

    @pytest.fixture(autouse=True)
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.tracer_chunks = []

    def tearDown(self):
        record_tracer_info(self.tracer_chunks, "test_agent_workflow_seq_exec_stream_workflow_with_tracer.json")

    async def run_workflow_seq_exec_stream_workflow_with_tracer(self, context: Session):
        """
        start -> a -> b -> end
        """

        # workflow与agent共用一个tracer
        workflow_session= context.create_workflow_session()
        assert (workflow_session.tracer() is self.tracer)

        flow = Workflow()
        flow.set_start_comp("start", MockStartNode("start"),
                            inputs_schema={
                                "a": "${a}",
                                "b": "${b}",
                                "c": 1,
                                "d": [1, 2, 3]})

        node_a_expected_datas = [
            {"node_id": "a", "id": 1, "data": "1"},
            {"node_id": "a", "id": 2, "data": "2"},
        ]
        node_a_expected_datas_model = [CustomSchema(**item) for item in node_a_expected_datas]
        flow.add_workflow_comp("a", StreamNodeWithTracer("a", node_a_expected_datas),
                               inputs_schema={
                                   "aa": "${start.a}",
                                   "ac": "${start.c}"})

        node_b_expected_datas = [
            {"node_id": "b", "id": 1, "data": "1"},
            {"node_id": "b", "id": 2, "data": "2"},
        ]
        node_b_expected_datas_model = [CustomSchema(**item) for item in node_b_expected_datas]
        flow.add_workflow_comp("b", StreamNodeWithTracer("b", node_b_expected_datas),
                               inputs_schema={
                                   "ba": "${a.aa}",
                                   "bc": "${a.ac}"})

        flow.set_end_comp("end", MockEndNode("end"),
                          inputs_schema={
                              "result": "${b.ba}"})

        flow.add_connection("start", "a")
        flow.add_connection("a", "b")
        flow.add_connection("b", "end")

        expected_datas_model = {
            "a": node_a_expected_datas_model,
            "b": node_b_expected_datas_model
        }
        index_dict = {key: 0 for key in expected_datas_model.keys()}

        async for chunk in flow.stream({"a": 1, "b": "haha"}, workflow_session):
            if isinstance(chunk, CustomSchema):
                node_id = chunk.node_id
                index = index_dict[node_id]
                assert chunk == expected_datas_model[node_id][index], f"Mismatch at node {node_id} index {index}"
                logger.info(f"stream chunk: {chunk}")
                index_dict[node_id] = index_dict[node_id] + 1

    async def run_agent_workflow_seq_exec_stream_workflow_with_tracer(self):
        # context手动初始化tracer，agent和workflow共用一个tracer
        config = Config()
        config.set_agent_config(AgentConfig(id="test_agent_checkpoint"))
        agent_session = AgentSession(config)

        context = await agent_session.pre_run(session_id="test")
        self.tracer = context.tracer()

        agent_span = self.tracer.tracer_agent_span_manager.create_agent_span()
        try:
            await self.tracer.trigger("tracer_agent", "on_chain_start", span=agent_span,
                                      inputs={"intput": "mock chain"},
                                      instance_info={"class_name": "Agent"})  # class_name为必选参数

            # 模拟需要运行llm、tool
            for runner in [MockLLM(self.tracer), MockPlugin(self.tracer)]:
                runner_span = self.tracer.tracer_agent_span_manager.create_agent_span(agent_span)  # 用于记录父子span关系
                await runner.stream(runner_span)

            # 模拟运行workflow
            await self.run_workflow_seq_exec_stream_workflow_with_tracer(context)

            await self.tracer.trigger("tracer_agent", "on_chain_end", span=agent_span,
                                      outputs={"outputs": "mock chain"},
                                      )

        except Exception as e:
            await self.tracer.trigger("tracer_agent", "on_chain_error", span=agent_span, error=e,
                                      )
            raise e
        finally:
            await context.post_run()

    async def get_stream_output(self):
        async for item in self.tracer._stream_writer_manager.stream_output(need_close=True):
            self.tracer_chunks.append(item)

    def test_agent_workflow_seq_exec_stream_workflow_with_tracer(self):
        async def main():
            task1 = asyncio.create_task(self.run_agent_workflow_seq_exec_stream_workflow_with_tracer())
            task2 = asyncio.create_task(self.get_stream_output())
            await asyncio.gather(task1, task2)

        self.loop.run_until_complete(main())
