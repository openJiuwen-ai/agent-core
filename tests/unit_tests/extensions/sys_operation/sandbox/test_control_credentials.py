# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from unittest.mock import AsyncMock

import httpx
import pytest
from openjiuwen.extensions.sys_operation.sandbox.providers import jiuwenbox as box


def test_cached_and_new_clients_follow_owned_service_rotations(monkeypatch):
    url = "http://127.0.0.1:57823"
    other = "https://example.invalid"
    credentials = {url: "test-first"}
    monkeypatch.setattr(box, "_service_token_resolver", credentials.get, raising=False)
    monkeypatch.setenv("JIUWENBOX_API_TOKEN", "test-external")
    original = httpx.Client
    seen = []
    def capture(request):
        seen.append(request.headers.get("Authorization", "") == "Bearer " + credentials.get(url, ""))
        return httpx.Response(200, json=[])
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(capture), **kwargs))
    with box._JiuwenBoxClient(url) as cached:
        cached.live_sandbox_ids()
        credentials[url] = "test-second"
        cached.live_sandbox_ids()
        with box._JiuwenBoxClient(url) as fresh:
            fresh.live_sandbox_ids()
        cached._client.get(other)
    assert seen == [True, True, True, False]


@pytest.mark.asyncio
async def test_async_exec_resolves_credentials_at_request_time(monkeypatch):
    url = "http://127.0.0.1:57823"
    creds = {url: "test-first"}
    monkeypatch.setattr(box, "_service_token_resolver", creds.get, raising=False)
    original = httpx.AsyncClient
    def reply(request):
        matches = request.headers.get("Authorization") == "Bearer test-second"
        return httpx.Response(200, json={"authenticated": matches})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(reply), **kw))
    with box._JiuwenBoxClient(url) as client:
        creds[url] = "test-second"
        result = await client.exec_async("dummy", ["unused"])
    assert result["authenticated"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 503])
async def test_authentication_failure_cannot_run_local_fallback(monkeypatch, status):
    provider = box._JiuwenBoxProviderMixin()
    from types import SimpleNamespace
    provider.endpoint = SimpleNamespace(base_url="http://example.invalid", isolation_key="test")
    provider.config = None
    monkeypatch.setattr(provider, "_acquire_sandbox_id", AsyncMock(return_value="test"))
    request = httpx.Request("GET", "http://example.invalid")
    response = httpx.Response(status, json={"code": "API_TOKEN_REQUIRED"}, request=request)
    local = AsyncMock(return_value={"ran": True})
    def denied(_):
        response.raise_for_status()
    with pytest.raises(httpx.HTTPStatusError):
        await provider._run_exec_pipeline(sandbox_op=denied, local_op=local, fallback_on_failure=True)
    local.assert_not_awaited()


def test_standalone_client_preserves_explicit_request_authorization(monkeypatch):
    monkeypatch.setattr(box, "_service_token_resolver", None, raising=False)
    monkeypatch.delenv("JIUWENBOX_API_TOKEN", raising=False)
    original = httpx.Client

    def reply(request):
        ok = request.headers.get("Authorization") == "Bearer standalone-test"
        return httpx.Response(200 if ok else 401)

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(reply), **kwargs))
    with box.build_jiuwenbox_http_client("https://example.invalid") as client:
        response = client.get("/health", headers={"Authorization": "Bearer standalone-test"})
    assert response.status_code == 200
