# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compare actual loopback HTTP uploads with observation enabled and disabled."""

import asyncio
from contextlib import ExitStack

import aiohttp
import pytest
import pytest_asyncio
import requests
from aiohttp import web

from openjiuwen.core.sys_operation.local.operation_observer import bind_operation_observer
from openjiuwen.harness.security.outbound import async_client, sync_client
from openjiuwen.harness.security.outbound.body_preview import MAX_PREVIEW_BYTES, UNAVAILABLE
from openjiuwen.harness.security.outbound.policy import reset_host_exit_policy


@pytest_asyncio.fixture
async def upload_url():
    reset_host_exit_policy()

    async def receive(request):
        if request.content_type == "multipart/form-data":
            reader = await request.multipart()
            parts = []
            while (part := await reader.next()) is not None:
                parts.append({"name": part.name, "filename": part.filename, "body": bytes(await part.read()).hex()})
            return web.json_response({"parts": parts})
        return web.json_response({"body": (await request.read()).hex(), "content_type": request.content_type})

    app = web.Application()
    app.router.add_post("/upload", receive)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    try:
        yield f"http://127.0.0.1:{port}/upload"
    finally:
        await runner.cleanup()
        reset_host_exit_policy()


@pytest.mark.asyncio
@pytest.mark.parametrize("client", ["requests", "aiohttp"])
@pytest.mark.parametrize("kind", ["form", "json", "text", "bytes", "binary", "file", "multipart"])
async def test_observation_preserves_actual_http_upload(upload_url, tmp_path, client, kind):
    text = "TEST_TOKEN:" + "中" * 4000
    raw = text.encode("utf-8")
    path = tmp_path / "note.txt"
    path.write_bytes(b"skip" + raw)
    events = []

    async def observer(name, arguments):
        if name == "network_send":
            events.append(arguments)
        return True

    async def send(observe):
        with ExitStack() as stack:
            if kind == "form":
                kwargs = {"data": [("secret", "TEST_TOKEN"), ("secret", "second value")]}
            elif kind == "json":
                kwargs = {"json": {"secret": text}}
            elif kind == "text":
                kwargs = {"data": text}
            elif kind == "bytes":
                kwargs = {"data": raw}
            elif kind == "binary":
                kwargs = {"data": b"\x00\xff" + raw}
            else:
                stream = stack.enter_context(path.open("rb"))
                stream.seek(4)
                if kind == "file":
                    kwargs = {"data": stream}
                elif client == "requests":
                    kwargs = {"data": {"description": "original"}, "files": {"upload": ("note.txt", stream)}}
                else:
                    form = aiohttp.FormData()
                    form.add_field("description", "original")
                    form.add_field("upload", stream, filename="note.txt", content_type="text/plain")
                    kwargs = {"data": form}
            with bind_operation_observer(observer if observe else None):
                if client == "requests":
                    session = stack.enter_context(requests.Session())
                    session.trust_env = False
                    response = await asyncio.to_thread(
                        sync_client.post, upload_url, session=session, timeout=5, **kwargs
                    )
                    return response.json()
                async with aiohttp.ClientSession(trust_env=False) as session:
                    async with await async_client.request(
                        session, "POST", upload_url, timeout=aiohttp.ClientTimeout(total=5), **kwargs
                    ) as response:
                        return await response.json()

    baseline = await send(False)
    observed = await send(True)
    assert observed == baseline
    assert len(events) == 1
    preview = events[0]["data"]
    assert len(preview.encode("utf-8")) <= MAX_PREVIEW_BYTES
    if kind == "binary":
        assert preview == UNAVAILABLE
    else:
        assert "TEST_TOKEN" in preview
    if kind == "multipart":
        assert "note.txt" in preview and '"size":' in preview
        assert bytes.fromhex(observed["parts"][-1]["body"]) == raw
    elif kind == "file":
        # aiohttp wraps an IOBase in multipart; requests sends the raw stream.
        body = observed["parts"][-1]["body"] if "parts" in observed else observed["body"]
        assert bytes.fromhex(body) == raw
