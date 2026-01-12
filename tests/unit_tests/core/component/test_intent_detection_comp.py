import os
import unittest

import pytest
import sys
import types
from unittest.mock import Mock, AsyncMock, patch

from openjiuwen.core.workflow import BranchRouter, WorkflowCard
from openjiuwen.core.foundation.llm import ModelConfig, ModelRequestConfig, ModelClientConfig
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import IntentDetectionCompConfig, \
    IntentDetectionComponent
from openjiuwen.core.workflow import Start
from openjiuwen.core.context_engine import ContextEngineConfig, ContextEngine
from openjiuwen.core.session import NodeSession, WorkflowSession
from openjiuwen.core.session.node import Session
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.foundation.llm import BaseModelInfo
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow.components.llm.intent_detection_comp import IntentDetectionExecutable

fake_base = types.ModuleType("base")
fake_base.logger = Mock()

sys.modules["openjiuwen.core.common.logging.base"] = fake_base

API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
os.environ["LLM_SSL_VERIFY"] = "false"

# ------------------------------------------------


def _create_model_request_config() -> ModelRequestConfig:
    """创建模型配置"""
    return ModelRequestConfig(
        model="gpt-3.5-turbo",
        temperature=0.7,
        top_p=0.9
    )


def _create_model_client_config() -> ModelClientConfig:
    """创建模型配置"""
    return ModelClientConfig(
        client_provider="OpenAI",
        api_key="sk-fake",
        api_base="https://api.openai.com/v1",
        timeout=30,
        max_retries=3,
        verify_ssl=False
    )


@pytest.fixture
def fake_ctx():
    return Session(NodeSession(WorkflowSession(), "test-id"))


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
            max_tokens=None,
            stop=None
        ),
    )


@pytest.fixture
def fake_config(fake_model_config) -> IntentDetectionCompConfig:
    return IntentDetectionCompConfig(
        user_prompt="请判断用户意图",
        category_name_list=["name1", "name2", "name3"],
        model_config=_create_model_request_config(),
        model_client_config=_create_model_client_config()
    )


class TestIntentDetectionExecutableInvoke:
    @pytest.mark.asyncio
    async def test_invoke_success(self, fake_ctx, fake_config):
        """LLM 正常返回合法 JSON 时的路径"""
        llm_mock = AsyncMock()
        llm_mock.invoke.return_value = Mock(content='{"class": "分类2", "reason": "ok"}')

        with patch.object(IntentDetectionExecutable, "_create_llm_instance", return_value=llm_mock):
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
        flow = Workflow(card=WorkflowCard(name=name, id=id, version=version))

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
            model_config=_create_model_request_config(),
            model_client_config=_create_model_client_config()
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
        ce_engine = ContextEngine(config)
        workflow_context = await ce_engine.create_context(context_id="intent_detection_workflow")
        workflow_session = create_agent_session(trace_id=session_id).create_workflow_session()
        async for chunk in flow.stream({"query": "我的意图是查询景点"}, workflow_session, workflow_context):
            print(chunk)
