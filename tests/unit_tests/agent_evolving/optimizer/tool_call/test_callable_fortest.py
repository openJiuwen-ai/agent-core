# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import importlib
import json
import sys
import types

import pytest


def _install_fake_fastmcp(monkeypatch):

    for name in list(sys.modules.keys()):
        if name == "fastmcp" or name.startswith("fastmcp."):
            sys.modules.pop(name, None)

    class FakeSSETransport:
        def __init__(self, url: str):
            self.url = url

    class FakeClient:
        calls = []

        def __init__(self, transport):
            self.transport = transport

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def call_tool(self, tool_name, arguments):
            FakeClient.calls.append((tool_name, arguments, self.transport.url))

            class _Piece:
                text = "ok-result"

            return type("Resp", (), {"content": [_Piece()]})()


    fake_fastmcp = types.ModuleType("fastmcp")
    fake_fastmcp.__path__ = []  

    fake_fastmcp_client = types.ModuleType("fastmcp.client")
    fake_fastmcp_client.SSETransport = FakeSSETransport
    fake_fastmcp_client.Client = FakeClient



    fake_fastmcp.client = fake_fastmcp_client


    fake_fastmcp.Client = FakeClient
    fake_fastmcp.SSETransport = FakeSSETransport

    monkeypatch.setitem(sys.modules, "fastmcp", fake_fastmcp)
    monkeypatch.setitem(sys.modules, "fastmcp.client", fake_fastmcp_client)

    return FakeClient


def test_make_sync_mcp_caller(monkeypatch):
    fake_client = _install_fake_fastmcp(monkeypatch)

    mod = importlib.import_module(
        "openjiuwen.agent_evolving.optimizer.tool_call.utils.callable_fortest"
    )
    mod = importlib.reload(mod)

    caller = mod.make_sync_mcp_caller("https://example.com/sse", name="S")

    out = caller({"name": "SearchFunds", "arguments": {"keyword": "abc"}})
    assert out == "ok-result"
    assert fake_client.calls[-1][0] == "SearchFunds"
    assert fake_client.calls[-1][1] == {"keyword": "abc"}
    assert fake_client.calls[-1][2] == "https://example.com/sse"

    out2 = caller({"name": "SearchFunds", "arguments": '{"keyword":"abc"}'})
    assert out2 == "ok-result"
    assert fake_client.calls[-1][1] == {"keyword": "abc"}

    with pytest.raises(ValueError):
        caller({"name": "SearchFunds", "arguments": "{bad-json"})

    assert mod.tool["name"] == "SearchFunds"