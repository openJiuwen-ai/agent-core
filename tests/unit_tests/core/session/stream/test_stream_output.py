import asyncio
from typing import AsyncIterator, Type

import pytest
from pydantic import BaseModel

from openjiuwen.core.session.stream import StreamMode
from openjiuwen.core.session.stream import StreamEmitter
from openjiuwen.core.session.stream import StreamWriterManager
from openjiuwen.core.session.stream import StreamWriter
from openjiuwen.core.session.stream import AsyncStreamQueue

pytestmark = pytest.mark.asyncio


async def test_send_completion_does_not_swallow_external_cancel(monkeypatch):
    queue = AsyncStreamQueue()
    original_put = queue._stream_queue.put
    sender = None

    async def put_then_cancel(data):
        await original_put(data)
        asyncio.get_running_loop().call_soon(sender.cancel)

    monkeypatch.setattr(queue._stream_queue, "put", put_then_cancel)
    sender = asyncio.create_task(queue.send("frame"))
    with pytest.raises(asyncio.CancelledError):
        await sender
    assert queue._stream_queue.get_nowait() == "frame"


@pytest.mark.parametrize("attempt_timeout", [0, -1, 0.001])
async def test_send_timeout_preserves_retry_limit(monkeypatch, attempt_timeout):
    queue = AsyncStreamQueue(maxsize=1)
    queue._stream_queue.put_nowait("full")
    calls = []
    from openjiuwen.core.session.stream import emitter
    monkeypatch.setattr(emitter.session_logger, "error", lambda *args, **kwargs: calls.append(args[0]))
    await queue.send("blocked", attempt_timeout=attempt_timeout, max_retries=2)
    assert calls.count("Stream data send timeout") == 2
    assert queue._sent_count == 0
    assert queue._stream_queue.get_nowait() == "full"


@pytest.mark.parametrize("attempt_timeout", [0, -1])
async def test_nonpositive_send_timeout_does_not_enqueue(attempt_timeout):
    queue = AsyncStreamQueue()
    await queue.send("must-not-send", attempt_timeout=attempt_timeout, max_retries=1)
    assert queue._stream_queue.empty()
    assert queue._sent_count == 0


class TestStreamOutput:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.emitter = StreamEmitter()
        self.manager = StreamWriterManager(self.emitter)

    def teardown(self):
        pass

    async def test_stream_output_with_custom_writer(self):

        async def mock_stream_output() -> AsyncIterator:
            mock_data = [{
                "name": "Alice",
                "age": 30
            }, {
                "name": "Bob",
                "age": 25
            }, {
                "name": "Charlie",
                "age": 35
            }]
            for data in mock_data:
                print(f"Send data: {data}")
                yield data

        async def write_data():
            async for mock_data in mock_stream_output():
                custom_writer = self.manager.get_custom_writer()
                await custom_writer.write(mock_data)
            await self.emitter.close()

        async def read_data():
            async for data in self.manager.stream_output():
                print(f"Received data: {data}")

        await asyncio.gather(write_data(), read_data())

    async def test_stream_output_with_output_writer(self):

        async def mock_stream_output() -> AsyncIterator:
            mock_data = [
                {
                    "type": "nodeA",
                    "index": 1,
                    "payload": "nodeA_stream"
                },
                {
                    "type": "nodeB",
                    "index": 1,
                    "payload": "nodeB_stream"
                },
                {
                    "type": "nodeC",
                    "index": 1,
                    "payload": "nodeC_stream"
                },
            ]
            for data in mock_data:
                print(f"Send data: {data}")
                yield data

        async def write_data():
            async for mock_data in mock_stream_output():
                output_writer = self.manager.get_output_writer()
                await output_writer.write(mock_data)
            await self.emitter.close()

        async def read_data():
            async for data in self.manager.stream_output():
                print(f"Received data: {data}")

        await asyncio.gather(write_data(), read_data())

    async def test_stream_output_with_trace_writer(self):

        async def mock_stream_output() -> AsyncIterator:
            mock_data = [
                {
                    "type": "on_chain_start",
                    "payload": "nodeA_start"
                },
                {
                    "type": "on_chain_end",
                    "payload": "nodeA_end"
                },
                {
                    "type": "on_chain_error",
                    "payload": "nodeA_error"
                },
            ]
            for data in mock_data:
                print(f"Send data: {data}")
                yield data

        async def write_data():
            async for mock_data in mock_stream_output():
                trace_writer = self.manager.get_trace_writer()
                await trace_writer.write(mock_data)
            await self.emitter.close()

        async def read_data():
            async for data in self.manager.stream_output():
                print(f"Received data: {data}")

        await asyncio.gather(write_data(), read_data())

    async def test_stream_output_with_mock_writer(self):

        class MockSchema(BaseModel):
            data: str

        class MockStreamWriter(StreamWriter[dict, MockSchema]):

            def __init__(
                self,
                stream_emitter: StreamEmitter,
                schema_type: Type[MockSchema] = MockSchema,
            ):
                super().__init__(stream_emitter, schema_type)

        class MockStreamMode(StreamMode):
            MOCK = ("mock", "mock stream data")

        self.manager.add_writer(MockStreamMode.MOCK,
                                MockStreamWriter(self.manager.stream_emitter()))

        async def mock_stream_output() -> AsyncIterator:
            mock_data = [
                {
                    "data": "nodeA_stream"
                },
                {
                    "data": "nodeB_stream"
                },
                {
                    "data": "nodeC_stream"
                },
            ]
            for data in mock_data:
                print(f"Send data: {data}")
                yield data

        async def write_data():
            async for mock_data in mock_stream_output():
                mock_writer = self.manager.get_writer(MockStreamMode.MOCK)
                await mock_writer.write(mock_data)
            await self.emitter.close()

        async def read_data():
            async for data in self.manager.stream_output():
                print(f"Received data: {data}")

        await asyncio.gather(write_data(), read_data())
