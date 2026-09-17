from __future__ import annotations

import httpx
import pytest

from openjiuwen.agent_evolving.agent_rl.online.lora_client import AIGWLoRAClient
from openjiuwen.agent_evolving.agent_rl.online.training_runner import PolicySnapshot
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError


@pytest.mark.asyncio
async def test_lora_client_reads_active_policy_and_activates_with_parent_cas() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "status": "active",
                    "active_lora": {"lora_name": "model-1:v2", "lora_path": "/loras/model-1/v2"},
                },
            )
        return httpx.Response(200, json={"status": "active"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AIGWLoRAClient(
            endpoint="http://aigw.local",
            model_id="model-1",
            timeout=150.0,
            http_client=http_client,
        )

        policy = await client.active_policy()
        await client.activate(
            training_run_id="run-1",
            model_id="model-1",
            base_model="/models/base",
            lora_name="model-1:v3",
            lora_path="/loras/model-1/v3",
            expected_lora_name="model-1:v2",
        )

    assert policy == PolicySnapshot("model-1:v2", "/loras/model-1/v2")
    assert requests[0].url.path == "/internal/v1/rl/loras/model-1"
    assert requests[1].url.path == "/internal/v1/rl/loras/activate"
    assert requests[0].extensions["timeout"] == {"connect": 150.0, "read": 150.0, "write": 150.0, "pool": 150.0}
    assert requests[1].extensions["timeout"] == {"connect": 150.0, "read": 150.0, "write": 150.0, "pool": 150.0}
    assert requests[1].read().decode() == (
        '{"base_model":"/models/base","lora_name":"model-1:v3","lora_path":"/loras/model-1/v3",'
        '"expected_lora_name":"model-1:v2","training_run_id":"run-1"}'
    )


@pytest.mark.asyncio
async def test_lora_client_confirms_activation_after_response_timeout() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            raise httpx.ReadTimeout("activation response timed out", request=request)
        return httpx.Response(
            200,
            json={
                "status": "active",
                "active_lora": {"lora_name": "model-1:v3", "lora_path": "/loras/model-1/v3"},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AIGWLoRAClient(
            endpoint="http://aigw.local",
            model_id="model-1",
            timeout=150.0,
            http_client=http_client,
        )
        await client.activate(
            training_run_id="run-1",
            model_id="model-1",
            base_model="/models/base",
            lora_name="model-1:v3",
            lora_path="/loras/model-1/v3",
            expected_lora_name="model-1:v2",
        )


@pytest.mark.asyncio
async def test_lora_client_rejects_timeout_when_target_is_not_active() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            raise httpx.ReadTimeout("activation response timed out", request=request)
        return httpx.Response(200, json={"status": "base", "active_lora": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AIGWLoRAClient(
            endpoint="http://aigw.local",
            model_id="model-1",
            timeout=150.0,
            http_client=http_client,
        )
        with pytest.raises(BaseError, match="timed out") as exc_info:
            await client.activate(
                training_run_id="run-1",
                model_id="model-1",
                base_model="/models/base",
                lora_name="model-1:v3",
                lora_path="/loras/model-1/v3",
                expected_lora_name="model-1:v2",
            )

    assert exc_info.value.status is StatusCode.AGENT_RL_LORA_CALL_FAILED


@pytest.mark.asyncio
async def test_lora_client_maps_base_policy_and_rejects_control_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"status": "base", "active_lora": None})
        return httpx.Response(409, json={"error": {"code": "lora_cas_conflict"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AIGWLoRAClient(endpoint="http://aigw.local", model_id="model-1", http_client=http_client)
        assert await client.active_policy() == PolicySnapshot()
        with pytest.raises(BaseError, match="409") as exc_info:
            await client.activate(
                training_run_id="run-1",
                model_id="model-1",
                base_model="/models/base",
                lora_name="model-1:v1",
                lora_path="/loras/model-1/v1",
                expected_lora_name="base",
            )
        assert exc_info.value.status is StatusCode.AGENT_RL_LORA_CALL_FAILED


@pytest.mark.asyncio
async def test_lora_client_rejects_non_object_policy_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AIGWLoRAClient(endpoint="http://aigw.local", model_id="model-1", http_client=http_client)
        with pytest.raises(BaseError) as exc_info:
            await client.active_policy()

    assert exc_info.value.status is StatusCode.AGENT_RL_LORA_CALL_FAILED
