#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest
import sys
import types
from unittest.mock import Mock, AsyncMock, patch

from jiuwen.core.component.branch_router import BranchRouter
from jiuwen.core.component.common.configs.model_config import ModelConfig
from jiuwen.core.component.intent_detection_comp import IntentDetectionExecutable, IntentDetectionCompConfig
from jiuwen.core.runtime.workflow import NodeRuntime, WorkflowRuntime
from jiuwen.core.runtime.wrapper import WrappedNodeRuntime

fake_base = types.ModuleType("base")
fake_base.logger = Mock()

sys.modules["jiuwen.core.common.logging.base"] = fake_base

@pytest.fixture
def fake_ctx():
    return WrappedNodeRuntime(NodeRuntime(WorkflowRuntime(), "test-id"))


@pytest.fixture
def fake_model_config() -> ModelConfig:
    return ModelConfig(
        model_provider="openai",
        model_info=Mock(
            api_key="sk-fake",
            api_base="mock_path",
            model_name="mock_name",
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
        "jiuwen.core.utils.llm.model_utils.model_factory.ModelFactory.get_model",
        autospec=True,
    )
    @pytest.mark.asyncio
    async def test_invoke_success(
            self, mock_get_model, fake_ctx, fake_config
    ):
        """LLM 正常返回合法 JSON 时的路径"""
        llm_mock = AsyncMock()
        llm_mock.invoke = Mock(return_value=Mock(content='{"class": "分类2", "reason": "ok"}'))
        mock_get_model.return_value = llm_mock

        exe = IntentDetectionExecutable(fake_config)
        exe.set_router(BranchRouter())
        output = await exe.invoke({"query": "你好"}, fake_ctx, context=Mock())
        assert output["category_name"] == "name2"
        llm_mock.invoke.assert_called_once()
