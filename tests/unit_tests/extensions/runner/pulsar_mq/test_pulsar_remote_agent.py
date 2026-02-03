#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import time

import pytest

from openjiuwen.core.common.exception.errors import BaseError, ExecutionError, RunnerTermination, Termination
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.drunner.remote_client.remote_agent import RemoteAgent
from openjiuwen.core.runner.drunner.server_adapter.agent_adapter import AgentAdapter
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.runner_config import RunnerConfig, DistributedConfig, MessageQueueConfig, PulsarConfig, \
    DEFAULT_RUNNER_CONFIG
from openjiuwen.core.single_agent import AgentCard


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires real Pulsar uv sync --extra pulsar")
class TestRunnerIntegration:
    def setup_method(self):
        # Save original methods
        self.original_handle_invoke = AgentAdapter._handle_invoke
        self.original_handle_stream = AgentAdapter._handle_stream

        # Replace with custom methods
        async def mock_handle_invoke(self, inputs):
            return {"MOCK_INVOKE": "CUSTOM_RESPONSE"}

        async def mock_handle_stream(self, inputs):
            for i in range(3):
                yield {"MOCK_STREAM": f"chunk_{i}"}

        AgentAdapter._handle_invoke = mock_handle_invoke
        AgentAdapter._handle_stream = mock_handle_stream

        pulsar_mq = RunnerConfig(
            distributed_mode=True,
            distributed_config=DistributedConfig(
                request_timeout=10.0,
                message_queue_config=MessageQueueConfig(
                    type="pulsar",
                    pulsar_config=PulsarConfig(
                        max_workers=8,
                        url="pulsar://localhost:6650",
                    ),
                )
            )
        )
        Runner.set_config(pulsar_mq)

    def teardown_method(self):
        # Restore original methods
        AgentAdapter._handle_invoke = self.original_handle_invoke
        AgentAdapter._handle_stream = self.original_handle_stream
        Runner.set_config(DEFAULT_RUNNER_CONFIG)

    async def test_agent_normal_lifecycle(self):
        """Test normal single_agent lifecycle: creation, invocation, deletion"""
        logger.info("=== Test 0: Agent lifecycle ===")
        await Runner.start()
        weather_adapter = AgentAdapter(agent_id="weather-single_agent")
        weather_adapter.start()

        try:
            # Simulate client sending request
            client = RemoteAgent(agent_id="weather-single_agent")
            Runner.resource_mgr.add_agent(AgentCard(id="remote-weather-single_agent"), agent=client)

            # 1. Test batch request
            logger.info("=== Testing batch invoke ===")
            response = await Runner.run_agent("remote-weather-single_agent", {"city": "London"})
            logger.info(f"Batch response: {response}")
            assert response is not None

            # 2. Test streaming response
            logger.info("=== Testing stream response ===")
            chunks = []
            async for chunk in Runner.run_agent_streaming("remote-weather-single_agent", {"city": "Paris"}):
                logger.info(f"Stream chunk received: {chunk}")
                chunks.append(chunk)

            assert len(chunks) > 0
            logger.info(f"Received {len(chunks)} chunks")

            # 3. Test single_agent removal
            logger.info("=== Testing single_agent removal ===")
            Runner.resource_mgr.remove_agent(id="weather-single_agent")

            # 4. Verify exception is thrown after deletion
            with pytest.raises(BaseError) as e:
                await Runner.run_agent("weather-single_agent", {"city": "London"})
            assert e.value.error_code == StatusCode.AGENT_NOT_FOUND.code

        except Exception as e:
            logger.exception(f"Test failed with error: {e}")
            raise
        finally:
            # Ensure resource cleanup
            await weather_adapter.stop()
            await Runner.stop()

    async def test_agent_request_cancellation(self):
        """Test request cancellation (triggering CancelledError) by sending message to a non-existent single_agent"""
        logger.info("=== Test 1: Manual task cancellation ===")
        await Runner.start()

        try:
            client = RemoteAgent(agent_id="weather-agent2")
            Runner.resource_mgr.add_agent(AgentCard(id="weather-agent2"), agent=client)

            async def long_running_request():
                """A long-running request"""
                return await Runner.run_agent("weather-agent2", {"city": "London"})

            # Create task
            task = asyncio.create_task(long_running_request())

            # Wait a short time then cancel
            await asyncio.sleep(0.1)

            task.cancel()

            # Verify task is cancelled
            with pytest.raises(BaseError) as e:
                await task
            assert e.value.error_code == StatusCode.REMOTE_AGENT_REQUEST_CANCELLED.code
        finally:
            await Runner.stop()

    async def test_agent_request_timeout(self):
        """Test request timeout (triggering TimeoutError) by sending message to a non-existent single_agent"""
        logger.info("=== Test 2: Request timeout ===")
        await Runner.start()
        try:
            client = RemoteAgent(agent_id="slow-single_agent")

            with pytest.raises(BaseError) as e:
                await client.invoke({"test": "data"}, 0.1)
            assert e.value.error_code == StatusCode.REMOTE_AGENT_REQUEST_TIMEOUT.code

            logger.info("Request timed out as expected")
        finally:
            await Runner.stop()

    async def test_agent_runner_shutdown_cancels_clients(self):
        """Verify that unfinished client calls receive CancelledError when Runner is closed early"""
        logger.info("=== Test 3: Runner shutdown cancels clients ===")
        await Runner.start()

        try:
            client = RemoteAgent(agent_id="slow-single_agent")
            Runner.resource_mgr.add_agent(AgentCard(id="slow-single_agent"), agent=client)

            async def long_running_request():
                return await Runner.run_agent("slow-single_agent", {"city": "Berlin"})

            task = asyncio.create_task(long_running_request())

            # Runner closed early
            await asyncio.sleep(0.1)
            await Runner.stop()

            # Verify: client side receives CancelledError
            with pytest.raises(RunnerTermination) as e:
                await task
            # 如果关闭太快，请求发的时候reply已经是close则会收到cancel异常，如果collector已经创建被取消则报错runner stop

            logger.info("Client received CancelledError as expected when Runner stopped")
        finally:
            pass

    async def test_agent_adapter_exception_propagation(self):
        """ Test that error information is correctly passed to the client when
        single_agent adapter returns an exception
        """
        logger.info("=== Test 4: Adapter error propagation ===")
        await Runner.start()
        original_handler = AgentAdapter._handle_invoke

        # Simulate adapter throwing exception
        async def error_handler(self, inputs):
            raise ExecutionError(
                error_code=111,
                message="ADAPTER_ERROR")

        AgentAdapter._handle_invoke = error_handler
        weather_adapter = AgentAdapter(agent_id="weather-single_agent")
        weather_adapter.start()

        try:
            client = RemoteAgent(agent_id="weather-single_agent")
            Runner.resource_mgr.add_agent(AgentCard(id="weather-single_agent"), agent=client)

            # Verify client receives exception containing error code and message
            with pytest.raises(BaseError) as e:
                await Runner.run_agent("weather-single_agent", {"city": "London"})

            assert e.value.error_code == StatusCode.REMOTE_AGENT_PROCESS_ERROR.code
            assert "code: 111, message: ADAPTER_ERROR" in e.value.message
        finally:
            # Restore original handler
            AgentAdapter.handle_invoke = original_handler
            await weather_adapter.stop()
            await Runner.stop()

    async def test_agent_call_without_runner_start_should_raise_exception(self):
        """Verify that Runner should report an error if not started"""
        logger.info("=== Test 5: Runner not started ===")
        try:
            client = RemoteAgent(agent_id="slow-single_agent")
            Runner.resource_mgr.add_agent(AgentCard(id="slow-single_agent"), agent=client)

            async def long_running_request():
                return await Runner.run_agent("slow-single_agent", {"city": "Berlin"})

            task = asyncio.create_task(long_running_request())
            with pytest.raises(BaseError) as e:
                await task
            assert e.value.error_code == StatusCode.RUNNER_DISTRIBUTED_MODE_REQUIRED.code
        finally:
            await Runner.stop()

    @pytest.mark.skip(reason="Skip performance tests")
    async def test_concurrent_vs_sequential_performance_comparison(self):
        """Compare performance differences between concurrent and sequential calls"""
        logger.info("=== Test 6: Performance Comparison ===")
        await Runner.start()
        # Create adapter and client
        weather_adapter = AgentAdapter(agent_id="perf-single_agent")
        weather_adapter.start()

        try:
            client = RemoteAgent(agent_id="perf-single_agent")
            Runner.resource_mgr.add_agent(AgentCard(id="perf-single_agent"), agent=client)

            # 测试数据
            test_data = [{"city": f"City_{i}"} for i in range(10)]

            # 1. Sequential call test
            logger.info("Testing sequential calls...")
            start_time = time.time()
            sequential_results = []
            for data in test_data:
                result = await Runner.run_agent("perf-single_agent", data)
                sequential_results.append(result)
            sequential_time = time.time() - start_time

            # 2. Concurrent call test - Using smaller concurrent batches
            logger.info("Testing concurrent calls...")
            start_time = time.time()
            concurrent_results = await asyncio.gather(
                *[Runner.run_agent("perf-single_agent", data) for data in test_data]
            )
            concurrent_time = time.time() - start_time

            # Performance comparison analysis
            logger.info(f"Sequential calls: {sequential_time:.3f}s for {len(test_data)} requests")
            logger.info(f"Concurrent calls: {concurrent_time:.3f}s for {len(test_data)} requests")
            # Verify result correctness
            assert len(sequential_results) == len(test_data)
            assert len(concurrent_results) == len(test_data)
            assert sequential_results == concurrent_results

            # If concurrent is indeed faster than sequential, record performance improvement
            if concurrent_time < sequential_time:
                logger.info(f"✓ Concurrent is {sequential_time / concurrent_time:.2f}x faster than sequential")
            else:
                logger.info(
                    f"⚠ Concurrent is {concurrent_time / sequential_time:.2f}x slower than "
                    f"sequential (within acceptable range)")

        finally:
            await weather_adapter.stop()
            await Runner.stop()

    @pytest.mark.skip(reason="Skip performance tests")
    async def test_concurrent_streaming(self):
        """ Test streaming calls 10 times, each call returns 5 chunks, performance comparison
        between concurrent and sequential calls
        """
        logger.info("=== Test 9: Concurrent Streaming vs Regular Calls ===")
        await Runner.start()

        # Save original method
        original_handle_stream = AgentAdapter._handle_stream

        # Simulate streaming response
        async def mock_handle_stream(self, inputs):
            for i in range(5):
                yield {"stream_chunk": i, "data": f"chunk_{i}_for_{inputs.get('city', 'unknown')}"}

        AgentAdapter._handle_stream = mock_handle_stream

        streaming_adapter = AgentAdapter(agent_id="streaming-single_agent")
        streaming_adapter.start()

        try:
            client = RemoteAgent(agent_id="streaming-single_agent")
            Runner.resource_mgr.add_agent(AgentCard(id="streaming-single_agent"), agent=client)

            # 测试数据
            test_data = [{"city": f"StreamCity_{i}"} for i in range(10)]

            # 1. Sequential streaming call test
            logger.info("Testing sequential streaming calls...")
            start_time = time.time()
            sequential_chunks = []
            for data in test_data:
                chunk_count = 0
                async for chunk in Runner.run_agent_streaming("streaming-single_agent", data):
                    sequential_chunks.append(chunk)
                    chunk_count += 1
            sequential_time = time.time() - start_time

            # 2. Concurrent streaming call test
            logger.info("Testing concurrent streaming calls...")

            start_time = time.time()

            async def collect_streaming_chunks(data):
                chunks = []
                async for chunk in Runner.run_agent_streaming("streaming-single_agent", data):
                    chunks.append(chunk)
                return chunks

            concurrent_tasks = []
            for data in test_data:
                task = asyncio.create_task(collect_streaming_chunks(data))
                concurrent_tasks.append(task)

            concurrent_results = await asyncio.gather(*concurrent_tasks)
            concurrent_time = time.time() - start_time

            # Calculate results
            total_sequential_chunks = len(sequential_chunks)
            total_concurrent_chunks = sum(len(result) for result in concurrent_results)

            # Performance comparison
            logger.info(
                f"Sequential streaming: {sequential_time:.3f}s for {len(test_data)} requests, "
                f"{total_sequential_chunks} chunks")
            logger.info(
                f"Concurrent streaming: {concurrent_time:.3f}s for {len(test_data)} requests, "
                f"{total_concurrent_chunks} chunks")
            logger.info(f"Performance improvement: {sequential_time / concurrent_time:.2f}x faster")

            # Verify result correctness
            assert total_sequential_chunks == len(test_data) * 5  # Each request has 5 chunks
            assert total_concurrent_chunks == len(test_data) * 5

            # Concurrent should be significantly faster than sequential calls
            assert sequential_time > concurrent_time, f"Sequential should be slower than concurrent"

            # Verify stream content correctness
            for result in concurrent_results:
                assert len(result) == 5, "Each streaming call should return 5 chunks"
                for chunk in result:
                    assert "stream_chunk" in chunk and "data" in chunk
        finally:
            # Restore original method
            AgentAdapter.handle_stream = original_handle_stream
            await streaming_adapter.stop()
            await Runner.stop()
