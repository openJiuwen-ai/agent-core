import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.model_clients import openai_model_client as native
from openjiuwen.symphony import SymphonyConfig, SymphonyRuntime
from openjiuwen.symphony.agent.retrieval_toolkit import AgenticSkillRetrievalToolkit, SkillRecord
from openjiuwen.symphony.model_bridge import _model_scope


@pytest.mark.asyncio
async def test_native_model_rejects_unbound_sync_build_and_keeps_owner_loop_connections(tmp_path, monkeypatch):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(self.client_address)
            payload = json.dumps({
                "id": "chatcmpl-local", "object": "chat.completion", "created": 0, "model": "local-model",
                "choices": [{"index": 0, "message": {"role": "assistant",
                              "content": '{"skills/documents": ["Alpha"]}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    class LocalHTTPClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(**{**kwargs, "trust_env": False})

    monkeypatch.setattr(native.httpx, "AsyncClient", LocalHTTPClient)
    monkeypatch.setattr(native.UrlUtils, "get_global_proxy_url", lambda _: None)
    monkeypatch.setattr(native.OpenAIModelClient, "_client_cache", {})
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    model = Model(
        ModelClientConfig(client_provider="OpenAI", api_key="local-test", max_retries=0,
                          api_base=f"http://127.0.0.1:{server.server_port}/v1"),
        ModelRequestConfig(model="local-model"),
    )
    root = tmp_path / "skills"
    skill = root / "alpha" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: Alpha\ndescription: Write documents\n---\nDocument instructions\n")
    config = SymphonyConfig.model_validate({
        "capability": {"root": str(root), "type": "skill"},
        "storage": {"artifact_root": str(tmp_path / "runtime")},
        "retrieval": {"enabled": True, "index_build": {
            "request": {"max_retries": 0, "timeout_seconds": 2.0},
            "task": {"allow_fallback_tree": False},
            "output": {"show_tree": False, "cache_metrics_enabled": False},
        }},
    })
    runtime = SymphonyRuntime(model, config)
    messages = [{"role": "user", "content": "Keep the model's connection alive"}]
    try:
        await model.invoke(messages=messages)
        client = model._client._create_async_openai_client()
        pool = client._client._transport._pool
        assert len(pool.connections) == 1 and pool.connections[0].is_idle()

        def reject_unbound_scope():
            with _model_scope(model):
                pass

        with pytest.raises(RuntimeError, match="owner event loop"):
            await asyncio.to_thread(reject_unbound_scope)
        toolkit = AgenticSkillRetrievalToolkit(tmp_path / "unbound", config=config.retrieval.index_build,
                                              model=model, skills=[SkillRecord(name="Alpha")])
        for action in (toolkit.build_index, toolkit.build_index_async):
            with pytest.raises(RuntimeError, match="owner event loop"):
                await asyncio.to_thread(action)
            assert not toolkit.index_root.exists()
        assert len(requests) == 1

        for _ in range(2):
            assert (await runtime.retrieval.build(force=True))["success"]
            assert len(pool.connections) == 1 and pool.connections[0].is_idle()
        reader = SymphonyRuntime(None, config)
        try:
            status = reader.retrieval.status()
            assert status["fresh"]
            assert status["model_identity_checked"] is False
            assert reader.retrieval.read()["success"]
            skill.write_text(skill.read_text() + "Updated instructions\n")
            assert not reader.retrieval.status()["fresh"]
        finally:
            await reader.aclose()
        await runtime.aclose()
        await model.invoke(messages=messages)
        assert len(requests) == 4
        assert len(set(requests)) == 1
        assert not client.is_closed()
        assert not asyncio.get_running_loop().is_closed()
    finally:
        await runtime.aclose()
        await native.OpenAIModelClient.aclose_connections([model.model_client_config])
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=2)
