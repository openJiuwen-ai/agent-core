import asyncio
import unittest
from typing import AsyncIterator, Type

from pydantic import BaseModel

from jiuwen.core.stream.base import StreamMode
from jiuwen.core.stream.emitter import StreamEmitter
from jiuwen.core.stream.manager import StreamWriterManager
from jiuwen.core.stream.writer import StreamWriter


class TestStreamOutput(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.emitter = StreamEmitter()
        self.manager = StreamWriterManager(self.emitter)

    async def asyncTearDown(self):
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
