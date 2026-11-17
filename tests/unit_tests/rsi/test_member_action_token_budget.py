# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Interactive author requests preserve the configured output budget."""

import json

import certifi
import httpx
import pytest
from openai import AsyncOpenAI

from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.runner import Runner
from openjiuwen.rsi.harness_rsi.member_optimizer import action_executor
from openjiuwen.rsi.harness_rsi.member_optimizer.action_executor import MemberActionExecutorAgent
from openjiuwen.rsi.harness_rsi.member_optimizer.schema import MemberOptimizationAction


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", [None, 4096, 16384])
@pytest.mark.parametrize("group", ["skill", "prompt"])
async def test_interactive_author_reads_edits_and_preserves_request_budget(tmp_path, monkeypatch, cap, group):
    package = tmp_path / "package"
    package.mkdir()
    target = package / "identity.md"
    target.write_text("Original identity.", encoding="utf-8")
    config = tmp_path / "model.json"
    config.write_text(json.dumps({
        "model_client_config": {"client_provider": "OpenAI", "api_key": "test-only",
                                "api_base": "https://example.test/v1", "ssl_cert": certifi.where()},
        "model_request_config": {"model": "custom-model", "max_tokens": cap},
    }), encoding="utf-8")
    requests = []
    agents = []
    build = action_executor.create_action_execution_agent

    def capture_agent(**kwargs):
        agent = build(**kwargs)
        agents.append(agent)
        return agent

    monkeypatch.setattr(action_executor, "create_action_execution_agent", capture_agent)

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "read-original", "type": "function", "function": {
                    "name": "read_file", "arguments": json.dumps({"file_path": str(target)}),
                },
            }]}
        elif len(requests) == 2:
            assert "Original identity" in str(body["messages"])
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "apply-change", "type": "function", "function": {
                    "name": "edit_file", "arguments": json.dumps({"file_path": str(target),
                        "old_string": "Original", "new_string": "Updated"}),
                },
            }]}
        else:
            message = {"role": "assistant", "content": json.dumps({"status": "succeeded",
                "changed_files": ["identity.md"], "errors": []})}
        return httpx.Response(200, json={"id": f"turn-{len(requests)}", "object": "chat.completion",
            "created": 0, "model": "custom-model", "choices": [{"index": 0,
            "finish_reason": "tool_calls" if message.get("tool_calls") else "stop", "message": message}]})

    monkeypatch.setattr("openjiuwen.harness.deep_agent.schedule_image_support_probe", lambda *args, **kwargs: None)
    async with AsyncOpenAI(api_key="test-only", base_url="https://example.test/v1",
                           http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))) as sdk:
        monkeypatch.setattr(OpenAIModelClient, "_create_async_openai_client", lambda self, **kwargs: sdk)
        action = MemberOptimizationAction(action_id="interactive-budget", role="solver", action_group=group,
            operation="modify", action_type="prompt_improvement", target_path="identity.md",
            declared_write_paths=["identity.md"], description="Update identity", rationale="Observed requirement")
        result = await MemberActionExecutorAgent(str(config)).execute_action(package, action, "summary", [], [])
    assert result["status"] == "succeeded", result
    assert target.read_text(encoding="utf-8") == "Updated identity."
    assert len(requests) == 3
    operation_id = agents[0].deep_config.sys_operation.id
    assert Runner.resource_mgr.get_sys_operation(operation_id) is None
    for body in requests:
        assert {item["function"]["name"] for item in body["tools"]} >= {"read_file", "edit_file", "write_file"}
        if cap is None:
            assert "max_tokens" not in body
        else:
            assert body["max_tokens"] == cap
