# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import MagicMock

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor import (
    DialogueCompressor,
    DialogueCompressorConfig,
)
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig


@ContextEngine.register_processor()
class RebindableDialogueCompressor(DialogueCompressor):
    """Use the forked compressor without changing the global processor registry."""


def _model_request_config(model_name: str) -> ModelRequestConfig:
    return ModelRequestConfig(model=model_name, context_window=8192)


def _model_client_config(model_name: str) -> ModelClientConfig:
    return ModelClientConfig(
        client_provider="OpenAI",
        api_key=f"{model_name}-key",
        api_base="http://test.local",
        verify_ssl=False,
    )


def _compressor_config(model_name: str) -> DialogueCompressorConfig:
    return DialogueCompressorConfig(
        model=_model_request_config(model_name),
        model_client=_model_client_config(model_name),
    )


def _mock_model(model_name: str):
    model = MagicMock()
    model.model_config = _model_request_config(model_name)
    model.model_client_config = _model_client_config(model_name)
    return model


@pytest.mark.asyncio
async def test_update_model_context_rebinds_cached_compression_executor():
    engine = ContextEngine(ContextEngineConfig(model_name="old-model"))
    context = await engine.create_context(
        "ctx",
        None,
        processors=[("RebindableDialogueCompressor", _compressor_config("old-model"))],
    )
    processor = context._processors[0]
    old_executor = processor._compression_executor
    new_model = _mock_model("new-model")

    engine.update_model_context(
        model_name="new-model",
        context_window_tokens=16384,
        model=new_model,
        model_config=new_model.model_config,
        model_client_config=new_model.model_client_config,
    )

    assert processor._model is new_model
    assert processor._compression_executor is not old_executor
    assert processor._compression_executor._model is new_model
    assert processor.config.model is new_model.model_config
    assert processor.config.model_client is new_model.model_client_config


@pytest.mark.asyncio
async def test_react_agent_set_llm_rebinds_cached_compressor():
    old_model_config = _model_request_config("old-model")
    old_client_config = _model_client_config("old-model")
    agent = ReActAgent(AgentCard(name="rebind-agent")).configure(
        ReActAgentConfig(
            model_name="old-model",
            model_client_config=old_client_config,
            model_config_obj=old_model_config,
            context_engine_config=ContextEngineConfig(model_name="old-model"),
            context_processors=[
                ("RebindableDialogueCompressor", _compressor_config("old-model"))
            ],
        )
    )
    context = await agent._init_context(None)
    processor = context._processors[0]
    new_model = _mock_model("new-model")

    agent.set_llm(new_model)

    assert processor._model is new_model
    assert processor._compression_executor._model is new_model
    assert agent._config.model_name == "new-model"
