# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest

from openjiuwen.agent_evolving.optimizer.tool_call.utils import rits


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeClient:
    def __init__(self, model_config, model_client_config):
        self.model_config = model_config
        self.model_client_config = model_client_config

    async def invoke(self, messages):
        assert messages[0]["role"] == "developer"
        return _FakeResponse("raw-output")


def test_rits_response_with_and_without_verify(monkeypatch):
    monkeypatch.setattr(rits, "ModelClientConfig", lambda **kwargs: kwargs)
    monkeypatch.setattr(rits, "OpenAIModelClient", _FakeClient)

    out = rits.rits_response("gpt-test", "hello", "key", verify_fn=lambda x: x.upper())
    assert out == "RAW-OUTPUT"

    out2 = rits.rits_response("gpt-test", "hello", "key", verify_fn=None)
    assert out2 == "raw-output"


def test_get_rits_response_wraps_exception(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("x")

    monkeypatch.setattr(rits, "rits_response", boom)
    out = rits.get_rits_response("m", "p", "k")
    assert "error" in out
    assert "Cannot complete LLM call" in out["error"]
