import os
import unittest

import pytest
import sys
import types
from unittest.mock import Mock, AsyncMock, patch

from openjiuwen.core.component.branch_router import BranchRouter
from openjiuwen.core.component.common.configs.model_config import ModelConfig
from openjiuwen.core.component.end_comp import End
from openjiuwen.core.component.intent_detection_comp import IntentDetectionExecutable, IntentDetectionCompConfig, \
    IntentDetectionComponent
from openjiuwen.core.component.start_comp import Start
from openjiuwen.core.context_engine.config import ContextEngineConfig
from openjiuwen.core.context_engine.engine import ContextEngine
from openjiuwen.core.runtime.workflow import NodeRuntime, WorkflowRuntime
from openjiuwen.core.runtime.wrapper import WrappedNodeRuntime, TaskRuntime
from openjiuwen.core.utils.llm.base import BaseModelInfo
from openjiuwen.core.workflow.base import Workflow
from openjiuwen.core.workflow.workflow_config import WorkflowConfig, WorkflowMetadata

fake_base = types.ModuleType("base")
fake_base.logger = Mock()

sys.modules["openjiuwen.core.common.logging.base"] = fake_base

API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")

# ------------------------------------------------


@pytest.fixture
def fake_ctx():
    return WrappedNodeRuntime(NodeRuntime(WorkflowRuntime(), "test-id"))


@pytest.fixture
def fake_model_config() -> ModelConfig:
    return ModelConfig(
        model_provider="openai",
        model_info=Mock(
            api_key="sk-fake",
            api_base="https://api.openai.com/v1",
            model_name="gpt-3.5-turbo",
            temperature=0,
            top_p=1,
            streaming=False,
            timeout=30,
        ),
    )


@pytest.fixture
def fake_config(fake_model_config) -> IntentDetectionCompConfig:
    return IntentDetectionCompConfig(
        user_prompt="请判断用户意图",
        category_name_list=["name1", "name2", "name3"],
        model=fake_model_config
    )


class TestIntentDetectionExecutableInvoke:
    @patch(
        "openjiuwen.core.utils.llm.model_utils.model_factory.ModelFactory.get_model",
        autospec=True,
    )
    @pytest.mark.asyncio
    async def test_invoke_success(
            self, mock_get_model, fake_ctx, fake_config
    ):
        """LLM 正常返回合法 JSON 时的路径"""
        # 1. 伪造 LLM
        llm_mock = AsyncMock()
        llm_mock.invoke = Mock(return_value=Mock(content='{"class": "分类2", "reason": "ok"}'))
        mock_get_model.return_value = llm_mock

        # 2. 构造 Executable 并调用
        exe = IntentDetectionExecutable(fake_config)
        exe.set_router(BranchRouter())
        output = await exe.invoke({"query": "你好"}, fake_ctx, context=Mock())
        print(output)
        # 3. 断言
        assert output["category_name"] == "name2"
        llm_mock.invoke.assert_called_once()


class TestIntentDetectionComponent:
    @unittest.skip("skip system test")
    @pytest.mark.asyncio
    async def test_start_intent_end_stream(self):
        id = "intent_stream"
        version = "1.0"
        name = "intent"
        flow = Workflow(workflow_config=WorkflowConfig(metadata=WorkflowMetadata(name=name, id=id, version=version)))

        start_component = Start(
            {
                "inputs": [
                    {"id": "query", "type": "String", "required": "true", "sourceType": "ref"}
                ]
            }
        )
        end_component = End({"responseTemplate": "{{output}}"})

        model_config = ModelConfig(model_provider=MODEL_PROVIDER,
                                   model_info=BaseModelInfo(
                                       model=MODEL_NAME,
                                       api_base=API_BASE,
                                       api_key=API_KEY,
                                       temperature=0.7,
                                       top_p=0.9,
                                       timeout=30  # 添加超时设置
                                   ))


        config = IntentDetectionCompConfig(
            user_prompt="请判断用户意图",
            category_name_list=["查询某地的景点", "查询某地天气"],
            model=model_config,
        )
        intent_component = IntentDetectionComponent(config)
        intent_component.add_branch("${intent.classification_id} == 0", ["end"], "默认分支")
        intent_component.add_branch("${intent.classification_id} == 1", ["end"], "查询景点分支")
        intent_component.add_branch("${intent.classification_id} == 2", ["end"], "查询天气分支")

        flow.set_start_comp(
            "start",
            start_component,
            inputs_schema={"query": "${query}"},
        )
        flow.set_end_comp("end", end_component,
                          inputs_schema={"output": "${intent.category_name}"})
        flow.add_workflow_comp(
            "intent",
            intent_component,
            inputs_schema={"query": "${start.query}"},
        )

        flow.add_connection("start", "intent")

        session_id = "test_intent_detection"
        config = ContextEngineConfig()
        ce_engine = ContextEngine("123", config)
        workflow_context = ce_engine.get_workflow_context(workflow_id="intent_detection_workflow", session_id=session_id)
        workflow_runtime = TaskRuntime(trace_id=session_id).create_workflow_runtime()
        async for chunk in flow.stream({"query": "我的意图是查询景点"}, workflow_runtime, workflow_context):
            print(chunk)
