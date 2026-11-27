# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""HTTP observation preserves bodies, streams and authorization ordering."""

import asyncio
import io
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest

from openjiuwen.core.sys_operation.local.operation_observer import bind_operation_observer
from openjiuwen.harness.security.outbound import async_client, sync_client
from openjiuwen.harness.security.outbound.body_preview import (
    MAX_PREVIEW_BYTES,
    UNAVAILABLE,
    async_request_body_preview,
    request_body_preview,
)
from openjiuwen.harness.security.outbound.policy import reset_host_exit_policy


@pytest.fixture(autouse=True)
def isolate_policy():
    reset_host_exit_policy()
    yield
    reset_host_exit_policy()


async def _call(client, kwargs, observer):
    response = SimpleNamespace(is_redirect=False, status=200, headers={})
    session = SimpleNamespace(request=Mock(return_value=response), close=Mock())
    with bind_operation_observer(observer):
        if client == "requests":
            await asyncio.to_thread(sync_client.post, "https://upload.example/test", session=session, **kwargs)
        else:
            session.request = AsyncMock(return_value=response)
            await async_client.request(session, "POST", "https://upload.example/test", **kwargs)
    return session.request.call_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("client", ["requests", "aiohttp"])
@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"data": {"secret": "TEST_TOKEN"}}, "secret=TEST_TOKEN"),
        ({"data": [("secret", "first"), ("secret", "second value")]}, "secret=first&secret=second+value"),
        ({"data": (("secret", ["one", "two"]),)}, "secret=one&secret=two"),
        ({"data": "TEST_TOKEN"}, "TEST_TOKEN"),
        ({"data": b"TEST_TOKEN"}, "TEST_TOKEN"),
        ({"data": bytearray(b"TEST_TOKEN")}, "TEST_TOKEN"),
        ({"data": memoryview(b"TEST_TOKEN")}, "TEST_TOKEN"),
        ({"json": {"secret": "TEST_TOKEN"}}, '{"secret": "TEST_TOKEN"}'),
        ({"data": b"\x00\xffbinary"}, UNAVAILABLE),
        ({}, ""),
        ({"data": "", "json": None}, ""),
    ],
)
async def test_both_clients_observe_body_and_forward_original_arguments(client, kwargs, expected):
    seen = []

    async def observer(name, arguments):
        seen.append((name, arguments))
        return True

    forwarded = await _call(client, kwargs, observer)
    assert seen[0] == ("network_send", {"url": "https://upload.example/test", "data": expected})
    assert seen[1][0] == "network_fetch"
    for name, value in kwargs.items():
        assert forwarded[name] is value


@pytest.mark.parametrize(
    "body", ["中" * 10000, ("中" * 10000).encode(), {"x": "中" * 10000}], ids=["str", "bytes", "json"]
)
def test_all_preview_forms_have_utf8_byte_limit(body):
    kwargs = {"json" if isinstance(body, dict) else "data": body}
    preview = request_body_preview(kwargs, client="requests")
    assert 8100 < len(preview.encode("utf-8")) <= MAX_PREVIEW_BYTES
    assert "�" not in preview
    assert next(iter(kwargs.values())) is body


def test_requests_actual_body_precedence_and_none_form_values():
    assert request_body_preview({"data": {"x": "form"}, "json": {"x": "ignored"}}, client="requests") == "x=form"
    assert request_body_preview({"data": {}, "json": {"x": "json"}}, client="requests") == '{"x": "json"}'
    assert request_body_preview({"data": {"x": None, "y": "ok"}}, client="requests") == "y=ok"
    assert request_body_preview({"data": {"x": None}}, client="aiohttp") == "x=None"


@pytest.mark.parametrize("stream_factory", [io.BytesIO, io.StringIO])
def test_seekable_stream_uses_current_cursor_and_restores_it(stream_factory):
    contents = b"skipTEST_TOKEN" if stream_factory is io.BytesIO else "skipTEST_TOKEN"
    stream = stream_factory(contents)
    stream.seek(4)
    assert request_body_preview({"data": stream}, client="requests") == "TEST_TOKEN"
    assert stream.tell() == 4
    assert not stream.closed
    assert stream.read() == contents[4:]


@pytest.mark.parametrize("files_factory", [dict, list])
@pytest.mark.parametrize("tuple_size", [2, 3, 4])
def test_multipart_preview_includes_fields_filename_size_and_content(files_factory, tuple_size):
    stream = io.BytesIO(b"skipTEST_TOKEN")
    stream.seek(4)
    item = ("note.txt", stream, "text/plain", {"X-Part": "test"})[:tuple_size]
    files = files_factory([("upload", item)])
    preview = request_body_preview({"data": {"description": "hello"}, "files": files}, client="requests")
    assert "description=hello" in preview
    assert '"filename": "note.txt"' in preview and '"size": 14' in preview
    assert "TEST_TOKEN" in preview and "skipTEST_TOKEN" not in preview
    assert stream.tell() == 4
    assert stream.read() == b"TEST_TOKEN"


def test_multipart_string_bytes_and_bare_file_forms():
    stream = io.BytesIO(b"TEST_STREAM")
    stream.name = "note.txt"
    files = {"text": (None, "TEST_TEXT"), "blob": ("data.bin", b"\xff"), "stream": stream}
    preview = request_body_preview({"files": files}, client="requests")
    assert "TEST_TEXT" in preview and UNAVAILABLE in preview and "TEST_STREAM" in preview
    assert "note.txt" in preview and stream.tell() == 0


def test_many_multipart_files_share_one_budget():
    first, second = io.BytesIO(b"a" * 10000), io.BytesIO(b"b" * 10000)
    preview = request_body_preview({"files": {"one": ("a.txt", first), "two": ("b.txt", second)}}, client="requests")
    assert len(preview.encode()) == MAX_PREVIEW_BYTES
    assert first.tell() == second.tell() == 0


def test_file_memoryview_size_counts_bytes_not_elements():
    contents = memoryview(b"TEST").cast("I")
    preview = request_body_preview({"files": {"upload": ("note.txt", contents)}}, client="requests")
    assert '"size": 4' in preview and "TEST" in preview


def test_aiohttp_form_data_is_inspected_without_consuming_fields_or_file():
    stream = io.BytesIO(b"skipTEST_TOKEN")
    stream.seek(4)
    form = aiohttp.FormData()
    form.add_field("description", "hello")
    form.add_field("upload", stream, filename="note.txt", content_type="text/plain")
    fields_before = list(form._fields)
    preview = request_body_preview({"data": form}, client="aiohttp")
    assert "description=hello" in preview and "note.txt" in preview and "TEST_TOKEN" in preview
    assert form._fields == fields_before
    assert stream.tell() == 4 and not stream.closed


def test_aiohttp_urlencoded_form_keeps_duplicate_fields():
    form = aiohttp.FormData([("x", "one"), ("x", "two")])
    assert request_body_preview({"data": form}, client="aiohttp") == "x=one&x=two"
    assert len(form._fields) == 2


class _NonSeekable:
    def __init__(self):
        self.reads = 0

    def seekable(self):
        return False

    def read(self, _size=-1):
        self.reads += 1
        return b"TEST_TOKEN"


def test_unseekable_and_iterable_bodies_are_marked_without_reading():
    stream = _NonSeekable()
    assert request_body_preview({"data": stream}, client="requests") == UNAVAILABLE
    assert stream.reads == 0
    chunks = iter([b"TEST_TOKEN"])
    assert request_body_preview({"data": chunks}, client="requests") == UNAVAILABLE
    assert next(chunks) == b"TEST_TOKEN"
    values = iter(["TEST_TOKEN"])
    assert UNAVAILABLE in request_body_preview({"data": {"x": values}}, client="requests")
    assert next(values) == "TEST_TOKEN"


@pytest.mark.asyncio
@pytest.mark.parametrize("client", ["requests", "aiohttp"])
async def test_no_observer_does_not_read_body(client):
    stream = io.BytesIO(b"TEST_TOKEN")
    stream.read = Mock(side_effect=AssertionError("unobserved request must not preview"))
    forwarded = await _call(client, {"data": stream}, None)
    assert forwarded["data"] is stream
    stream.read.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("client", ["requests", "aiohttp"])
async def test_observer_can_deny_form_before_http_io(client):
    async def deny(name, arguments):
        assert name == "network_send" and arguments["data"] == "secret=TEST_TOKEN"
        return False

    with pytest.raises(PermissionError, match="network_send"):
        await _call(client, {"data": {"secret": "TEST_TOKEN"}}, deny)


@pytest.mark.asyncio
async def test_async_cancellation_waits_for_cursor_restoration():
    entered, release = threading.Event(), threading.Event()

    class SlowStream(io.BytesIO):
        def read(self, size=-1):
            contents = super().read(size)
            entered.set()
            assert release.wait(5)
            return contents

    stream = SlowStream(b"TEST_TOKEN")
    task = asyncio.create_task(async_request_body_preview({"data": stream}))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.tell() == 0


def test_incomplete_utf8_body_is_not_reported_as_empty():
    assert request_body_preview({"data": b"\xe4"}, client="requests") == UNAVAILABLE


def test_async_stream_objects_are_not_called_synchronously():
    stream = SimpleNamespace(read=AsyncMock(), seek=AsyncMock(), tell=AsyncMock(), seekable=AsyncMock())
    assert request_body_preview({"data": stream}, client="requests") == UNAVAILABLE
    stream.read.assert_not_called()
    stream.seekable.assert_not_called()


@pytest.mark.parametrize("data", [object(), [("bad",)], 42])
def test_unsupported_body_types_are_explicit(data):
    assert request_body_preview({"data": data}, client="requests") == UNAVAILABLE


def test_empty_and_prebuilt_aiohttp_multipart_are_distinguished():
    assert request_body_preview({"data": aiohttp.FormData()}, client="aiohttp") == ""
    form = aiohttp.FormData()
    form.add_field("file", b"TEST_TOKEN", filename="note.txt")
    payload = form()
    assert request_body_preview({"data": payload}, client="aiohttp") == UNAVAILABLE
    assert request_body_preview({"data": form}, client="aiohttp") == UNAVAILABLE


@pytest.mark.asyncio
async def test_async_iterable_is_not_consumed():
    async def chunks():
        yield b"TEST_TOKEN"

    body = chunks()
    assert request_body_preview({"data": body}, client="aiohttp") == UNAVAILABLE
    assert await anext(body) == b"TEST_TOKEN"
    await body.aclose()


def test_failed_preview_read_restores_cursor():
    class BrokenRead(io.BytesIO):
        def read(self, size=-1):
            super().read(size)
            raise OSError("test read failure")

    stream = BrokenRead(b"TEST_TOKEN")
    assert request_body_preview({"data": stream}, client="requests") == UNAVAILABLE
    assert stream.tell() == 0


def test_failed_rewind_does_not_allow_sending_consumed_body():
    class BrokenRewind(io.BytesIO):
        def seek(self, offset, whence=0):
            if self.tell():
                raise OSError("test restore failure")
            return super().seek(offset, whence)

    with pytest.raises(OSError, match="restore failure"):
        request_body_preview({"data": BrokenRewind(b"TEST_TOKEN")}, client="requests")
