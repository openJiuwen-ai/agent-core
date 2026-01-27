# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Controller concurrency and exception handling tests

This test file contains concurrency and exception handling test cases for Controller.

Test scenarios:
1. Concurrent session isolation tests
2. Multiple sessions running concurrently without interfering with each other
3. Exceptions in event handling do not affect other events
4. Task execution exception handling
5. Stream output exception isolation

Key verification points:
- Multiple sessions running concurrently do not interfere with each other
- Tasks/exceptions in one session do not affect other sessions
- Events are correctly routed to the corresponding session
- Exceptions are correctly captured and isolated
"""

import asyncio
import json
from typing import List, AsyncIterator, Tuple
from collections import Counter
import pytest

from openjiuwen.core.controller.modules.task_manager import TaskFilter
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.session.internal.wrapper import TaskSession
from openjiuwen.core.controller.base import Controller, ControllerConfig
from openjiuwen.core.controller.modules import (
    EventHandler,
    EventHandlerInput,
    TaskExecutor,
    TaskExecutorDependencies
)
from openjiuwen.core.controller.schema import (
    ControllerOutputChunk,
    ControllerOutputPayload,
    EventType,
    JsonDataFrame,
    TextDataFrame,
    Task,
    TaskStatus,
    InputEvent,
)
from openjiuwen.core.single_agent.agent import AbilityManager, ControllerAgent
from openjiuwen.core.common.logging import logger
from openjiuwen.core.session import Session


# ==================== TaskExecutors for testing ====================

class NormalTaskExecutor(TaskExecutor):
    """Task executor that runs normally"""
    
    async def execute_ability(self, task_id: str, session: Session) -> AsyncIterator[ControllerOutputChunk]:
        """Execute task"""
        yield ControllerOutputChunk(
            index=0,
            type="controller_output",
            payload=ControllerOutputPayload(
                type="processing",
                data=[TextDataFrame(type="text", text=f"Task {task_id} started in session {session.session_id()}")]
            ),
            last_chunk=False
        )
        
        # Simulate execution
        for i in range(3):
            await asyncio.sleep(0.1)
            yield ControllerOutputChunk(
                index=i + 1,
                type="controller_output",
                payload=ControllerOutputPayload(
                    type="processing",
                    data=[TextDataFrame(type="text", text=f"Task {task_id} progress {i+1}/3")]
                ),
                last_chunk=False
            )
        
        # Complete
        yield ControllerOutputChunk(
            index=4,
            type="controller_output",
            payload=ControllerOutputPayload(
                type=EventType.TASK_COMPLETION,
                data=[TextDataFrame(type="text", text=f"Task {task_id} completed in session {session.session_id()}")]
            ),
            last_chunk=True
        )
    
    async def can_pause(self, task_id: str, session: Session) -> Tuple[bool, str]:
        return True, ""
    
    async def pause(self, task_id: str, session: Session) -> bool:
        return True
    
    async def can_cancel(self, task_id: str, session: Session) -> Tuple[bool, str]:
        return True, ""
    
    async def cancel(self, task_id: str, session: Session) -> bool:
        return True


class FailingTaskExecutor(TaskExecutor):
    """Task executor that fails intentionally"""
    
    async def execute_ability(self, task_id: str, session: Session) -> AsyncIterator[ControllerOutputChunk]:
        """Execute task - raises exception"""
        yield ControllerOutputChunk(
            index=0,
            type="controller_output",
            payload=ControllerOutputPayload(
                type="processing",
                data=[TextDataFrame(type="text", text=f"Task {task_id} starting...")]
            ),
            last_chunk=False
        )
        
        await asyncio.sleep(0.1)
        
        # Simulate task failure
        raise RuntimeError(f"Task {task_id} failed intentionally")
    
    async def can_pause(self, task_id: str, session: Session) -> Tuple[bool, str]:
        return True, ""
    
    async def pause(self, task_id: str, session: Session) -> bool:
        return True
    
    async def can_cancel(self, task_id: str, session: Session) -> Tuple[bool, str]:
        return True, ""
    
    async def cancel(self, task_id: str, session: Session) -> bool:
        return True


class ExceptionInStreamTaskExecutor(TaskExecutor):
    """Task executor that raises exception during streaming output"""
    
    async def execute_ability(self, task_id: str, session: Session) -> AsyncIterator[ControllerOutputChunk]:
        """Execute task - raises exception in the middle of streaming output"""
        yield ControllerOutputChunk(
            index=0,
            type="controller_output",
            payload=ControllerOutputPayload(
                type="processing",
                data=[TextDataFrame(type="text", text=f"Task {task_id} started")]
            ),
            last_chunk=False
        )
        
        await asyncio.sleep(0.05)
        
        yield ControllerOutputChunk(
            index=1,
            type="controller_output",
            payload=ControllerOutputPayload(
                type="processing",
                data=[TextDataFrame(type="text", text=f"Task {task_id} progress 1/3")]
            ),
            last_chunk=False
        )
        
        # Raise exception in the middle
        raise RuntimeError(f"Task {task_id} stream failed")
    
    async def can_pause(self, task_id: str, session: Session) -> Tuple[bool, str]:
        return True, ""
    
    async def pause(self, task_id: str, session: Session) -> bool:
        return True
    
    async def can_cancel(self, task_id: str, session: Session) -> Tuple[bool, str]:
        return True, ""
    
    async def cancel(self, task_id: str, session: Session) -> bool:
        return True


# ==================== EventHandlers for testing ====================

class ConcurrentSessionEventHandler(EventHandler):
    """Event handler for concurrent session tests"""
    
    async def handle_input(self, inputs: EventHandlerInput):
        """Handle input event - create task (task_id contains session_id)"""
        session_id = inputs.session.session_id()
        task = Task(
            session_id=session_id,
            task_id=f"task_{session_id}",
            task_type="normal",
            priority=1,
            status=TaskStatus.SUBMITTED,
            context_id=f"context_{session_id}"
        )
        await self.task_manager.add_task([task])
        logger.info(f"ConcurrentSessionEventHandler: Created task for session {session_id}")
        return {"status": "success", "session_id": session_id}
    
    async def handle_task_interaction(self, inputs: EventHandlerInput):
        pass
    
    async def handle_task_completion(self, inputs: EventHandlerInput):
        """Handle task completion event"""
        logger.info(f"ConcurrentSessionEventHandler: Task completed - {inputs.event.task.task_id}")
        return {"status": "success"}
    
    async def handle_task_failed(self, inputs: EventHandlerInput):
        """Handle task failure event"""
        logger.error(f"ConcurrentSessionEventHandler: Task failed - {inputs.event.task.task_id}")
        return {"status": "failed"}


class ExceptionInEventHandlerEventHandler(EventHandler):
    """Test handler that raises exceptions inside EventHandler"""
    
    def __init__(self):
        super().__init__()
        self.handle_count = 0
    
    async def handle_input(self, inputs: EventHandlerInput):
        """Handle input event - create multiple tasks"""
        tasks = [
            Task(
                session_id=inputs.session.session_id(),
                task_id=f"task_{i}",
                task_type="normal",
                priority=i,
                status=TaskStatus.SUBMITTED,
                context_id=f"context_{i}"
            )
            for i in range(1, 4)
        ]
        await self._task_manager.add_task(tasks)
        return {"status": "success", "tasks_created": 3}
    
    async def handle_task_interaction(self, inputs: EventHandlerInput):
        pass
    
    async def handle_task_completion(self, inputs: EventHandlerInput):
        """Handle task completion event - raise exception when first task completes"""
        self.handle_count += 1
        
        if self.handle_count == 1:
            # Raise exception when the first task completes
            logger.info("ExceptionInEventHandlerEventHandler: Throwing exception in handle_task_completion")
            raise RuntimeError("Exception in handle_task_completion")
        
        # Other tasks are handled normally
        logger.info(f"ExceptionInEventHandlerEventHandler: Task {inputs.event.task.task_id} completed normally")
        return {"status": "success"}
    
    async def handle_task_failed(self, inputs: EventHandlerInput):
        """Handle task failure event"""
        logger.error(f"ExceptionInEventHandlerEventHandler: Task failed - {inputs.event.task.task_id}")
        return {"status": "failed"}


class FailingTaskEventHandler(EventHandler):
    """Event handler for failed tasks"""
    
    async def handle_input(self, inputs: EventHandlerInput):
        """Handle input event - create failing task and normal task"""
        tasks = [
            Task(
                session_id=inputs.session.session_id(),
                task_id="failing_task",
                task_type="failing",
                priority=1,
                status=TaskStatus.SUBMITTED,
                context_id="failing_context"
            ),
            Task(
                session_id=inputs.session.session_id(),
                task_id="normal_task",
                task_type="normal",
                priority=2,
                status=TaskStatus.SUBMITTED,
                context_id="normal_context"
            )
        ]
        await self._task_manager.add_task(tasks)
        logger.info("FailingTaskEventHandler: Created 1 failing task and 1 normal task")
        return {"status": "success", "tasks_created": 2}
    
    async def handle_task_interaction(self, inputs: EventHandlerInput):
        pass
    
    async def handle_task_completion(self, inputs: EventHandlerInput):
        logger.info(f"FailingTaskEventHandler: Task completed - {inputs.event.task.task_id}")
        return {"status": "success"}
    
    async def handle_task_failed(self, inputs: EventHandlerInput):
        failed_task = inputs.event.task
        error_msg = inputs.event.error_message
        logger.error(f"FailingTaskEventHandler: Task {failed_task.task_id} failed: {error_msg}")
        return {"status": "failed", "error": error_msg}


class StreamExceptionTaskEventHandler(EventHandler):
    """Event handler for stream output exceptions"""
    
    async def handle_input(self, inputs: EventHandlerInput):
        """Handle input event - create task that raises exception during streaming and a normal task"""
        tasks = [
            Task(
                session_id=inputs.session.session_id(),
                task_id="stream_fail_task",
                task_type="stream_exception",
                priority=1,
                status=TaskStatus.SUBMITTED,
                context_id="stream_fail_context"
            ),
            Task(
                session_id=inputs.session.session_id(),
                task_id="normal_task_2",
                task_type="normal",
                priority=2,
                status=TaskStatus.SUBMITTED,
                context_id="normal_context_2"
            )
        ]
        await self._task_manager.add_task(tasks)
        logger.info("StreamExceptionTaskEventHandler: Created 1 stream-exception task and 1 normal task")
        return {"status": "success", "tasks_created": 2}
    
    async def handle_task_interaction(self, inputs: EventHandlerInput):
        pass
    
    async def handle_task_completion(self, inputs: EventHandlerInput):
        """Handle task completion event"""
        logger.info(f"StreamExceptionTaskEventHandler: Task completed - {inputs.event.task.task_id}")
        return {"status": "success"}
    
    async def handle_task_failed(self, inputs: EventHandlerInput):
        """Handle task failure event"""
        failed_task = inputs.event.task
        error_msg = inputs.event.error_message
        logger.error(f"StreamExceptionTaskEventHandler: Task {failed_task.task_id} failed: {error_msg}")
        return {"status": "failed", "error": error_msg}


class ConcurrentTasksEventHandler(EventHandler):
    """Event handler for concurrent tasks"""

    def __init__(self):
        super().__init__()
        self.created = False

    async def handle_input(self, inputs: EventHandlerInput):
        if self.created:
            return {"status": "success", "tasks_created": 0}
        self.created = True
        tasks = [
            Task(
                session_id=inputs.session.session_id(),
                task_id=f"concurrent_task_{i}",
                task_type="normal",
                priority=1,
                status=TaskStatus.SUBMITTED,
                context_id=f"concurrent_context_{i}",
            )
            for i in range(3)
        ]
        await self._task_manager.add_task(tasks)
        return {"status": "success", "tasks_created": len(tasks)}

    async def handle_task_interaction(self, inputs: EventHandlerInput):
        pass

    async def handle_task_completion(self, inputs: EventHandlerInput):
        return {"status": "success"}

    async def handle_task_failed(self, inputs: EventHandlerInput):
        return {"status": "failed"}


class TimeoutTestEventHandler(EventHandler):
    """Event handler for timeout testing"""

    async def handle_input(self, event_input: EventHandlerInput):
        """Create 3 tasks for timeout testing, the 2nd task is normal, other tasks are very time-consuming"""
        tasks = [
            Task(
                session_id=event_input.session.session_id(),
                task_id=f"timeout_task_{i}",
                task_type="slow",
                priority=1,
                status=TaskStatus.SUBMITTED,
                context_id=f"timeout_context_{i}",
            )
            for i in range(3)
        ]
        await self._task_manager.add_task(tasks)
        return {"status": "success"}

    async def handle_task_completion(self, event_input: EventHandlerInput):
        return {"status": "success"}

    async def handle_task_interaction(self, event_input: EventHandlerInput):
        return {"status": "success"}

    async def handle_task_failed(self, event_input: EventHandlerInput):
        return {"status": "success"}


# ==================== Factory functions ====================

def build_normal_executor(dependencies: TaskExecutorDependencies):
    """Build normal task executor"""
    return NormalTaskExecutor(dependencies)


def build_failing_executor(dependencies: TaskExecutorDependencies):
    """Build failing task executor"""
    return FailingTaskExecutor(dependencies)


def build_stream_exception_executor(dependencies: TaskExecutorDependencies):
    """Build stream-exception task executor"""
    return ExceptionInStreamTaskExecutor(dependencies)


def build_slow_executor_factory(sleep_time: float = 10.0):
    """Factory function that returns a builder for SlowTaskExecutor with custom sleep_time
    
    Args:
        sleep_time: Sleep time in seconds for the slow executor
        
    Returns:
        Builder function that accepts TaskExecutorDependencies
    """
    def build_slow_executor(dependencies: TaskExecutorDependencies):
        """Builder function for SlowTaskExecutor"""
        return SlowTaskExecutor(dependencies, sleep_time)
    return build_slow_executor


# ==================== Agent building function ====================

async def build_test_agent(
    agent_id: str,
    event_handler: EventHandler,
    task_executors: dict
) -> ControllerAgent:
    """Build Agent for testing
    
    Args:
        agent_id: Agent ID
        event_handler: EventHandler instance
        task_executors: task executor mapping {task_type: builder_func}
    
    Returns:
        ControllerAgent: configured test Agent
    """
    agent_card = AgentCard(
        id=agent_id,
        name=f"Test Agent {agent_id}",
        description="Test agent for concurrency and exception testing"
    )
    
    controller = Controller()
    agent = ControllerAgent(card=agent_card, controller=controller)
    
    # Set event handler
    controller.set_event_handler(event_handler)

    # Register task executors
    for task_type, builder_func in task_executors.items():
        controller.add_task_executor(task_type, builder_func)

    agent.configure(ControllerConfig(enable_task_persistence=True))

    return agent


# ==================== Helper functions ====================

async def collect_stream_output(stream: AsyncIterator[ControllerOutputChunk]) -> List[str]:
    """Collect content from streaming output

    Args:
        stream: streaming output iterator

    Returns:
        List[str]: list of content strings
    """
    output_texts = []
    async for chunk in stream:
        if chunk.payload and chunk.payload.data:
            for item in chunk.payload.data:
                if isinstance(item, TextDataFrame):
                    output_texts.append(item.text)
                elif isinstance(item, JsonDataFrame):
                    output_texts.append(json.dumps(item.data))
    return output_texts


# ==================== Concurrent session isolation tests ====================

class TestConcurrentSessionIsolation:
    """Test concurrent session isolation"""

    @pytest.mark.asyncio
    async def test_concurrent_sessions_isolation(self):
        """Test multiple sessions running concurrently without interference
        
        Test objectives:
        1. Start streams for 3 sessions at the same time
        2. Each session creates its own task
        3. Verify tasks in each session complete correctly
        4. Verify task output contains the correct session_id
        5. Verify one session does not affect other sessions
        """
        # Build shared Agent
        agent = await build_test_agent(
            agent_id="test_concurrent_sessions",
            event_handler=ConcurrentSessionEventHandler(),
            task_executors={"normal": build_normal_executor}
        )

        # Create 3 different sessions
        session_1 = TaskSession(session_id="session_1")
        session_2 = TaskSession(session_id="session_2")
        session_3 = TaskSession(session_id="session_3")

        # Create 3 input events
        input_event_1 = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "request from session 1"}
        )
        input_event_2 = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "request from session 2"}
        )
        input_event_3 = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "request from session 3"}
        )

        # Execute 3 streams concurrently
        logger.info("========== Start 3 sessions concurrently ==========")
        results = await asyncio.gather(
            collect_stream_output(agent.stream(input_event_1, session_1)),
            collect_stream_output(agent.stream(input_event_2, session_2)),
            collect_stream_output(agent.stream(input_event_3, session_3)),
            return_exceptions=True
        )

        # Verify all sessions have finished successfully
        assert len(results) == 3, "There should be 3 results"

        for i, output in enumerate(results, 1):
            if isinstance(output, Exception):
                pytest.fail(f"Session {i} raised exception: {output}")

            assert len(output) > 0, f"Session {i} should have output"

            # Verify output contains correct session_id
            session_id = f"session_{i}"
            assert any(session_id in text for text in output), \
                f"Output of session {i} should contain {session_id}"

            # Verify task completion
            assert any("completed" in text for text in output), \
                f"Task of session {i} should be completed"

            logger.info(f"Session {i} passed verification: {len(output)} outputs")

        # Verify all tasks were created and completed correctly
        all_tasks = await agent.controller.task_manager.get_task(task_filter=None)
        completed_tasks = [t for t in all_tasks if t.status == TaskStatus.COMPLETED]

        assert len(completed_tasks) >= 3, \
            f"There should be at least 3 completed tasks, actually {len(completed_tasks)}"

        logger.info("✅ test_concurrent_sessions_isolation passed")

    @pytest.mark.asyncio
    async def test_session_task_isolation(self):
        """Test that tasks of one session do not affect other sessions
        
        Test objectives:
        1. Session 1 creates a normal task
        2. Session 2 creates a failing task
        3. Verify failure in Session 2 does not affect Session 1
        4. Verify task states of the two sessions are independent
        """
        # Create two different Agents (with different EventHandlers)
        agent_1 = await build_test_agent(
            agent_id="test_session_1",
            event_handler=ConcurrentSessionEventHandler(),
            task_executors={"normal": build_normal_executor}
        )

        agent_2 = await build_test_agent(
            agent_id="test_session_2",
            event_handler=FailingTaskEventHandler(),
            task_executors={
                "normal": build_normal_executor,
                "failing": build_failing_executor
            }
        )

        session_1 = TaskSession(session_id="normal_session")
        session_2 = TaskSession(session_id="failing_session")

        input_event_1 = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "normal request"}
        )
        input_event_2 = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "failing request"}
        )

        # Execute concurrently
        logger.info("========== Execute normal and failing tasks concurrently ==========")
        results = await asyncio.gather(
            collect_stream_output(agent_1.stream(input_event_1, session_1)),
            collect_stream_output(agent_2.stream(input_event_2, session_2)),
            return_exceptions=True
        )

        # Verify Session 1 completes normally
        output_1 = results[0]
        assert not isinstance(output_1, Exception), "Session 1 should not raise exception"
        assert any("completed" in text for text in output_1), \
            "Task in Session 1 should complete normally"

        # Verify Session 2 also finishes (even though there is a failed task)
        output_2 = results[1]
        assert not isinstance(output_2, Exception), \
            "Session 2 should not raise exception (task failure != session failure)"

        # Verify failed tasks of Session 2
        failed_tasks = [t for t in await agent_2.controller.task_manager.get_task(task_filter=None)
                       if t.status == TaskStatus.FAILED]
        assert len(failed_tasks) > 0, "Session 2 should have failed tasks"

        # Verify Session 1 has no failed tasks
        all_tasks_1 = await agent_1.controller.task_manager.get_task(task_filter=None)
        failed_tasks_1 = [t for t in all_tasks_1 if t.status == TaskStatus.FAILED]
        assert len(failed_tasks_1) == 0, "Session 1 should have no failed tasks (not affected by Session 2)"

        logger.info("✅ test_session_task_isolation passed")

    @pytest.mark.asyncio
    async def test_event_routing_to_correct_session(self):
        """Test events are routed to the correct session

        Test objectives:
        1. Start multiple sessions concurrently
        2. Verify events of each session are only routed to its own EventHandler
        3. Verify events are not routed to other sessions
        """
        agent = await build_test_agent(
            agent_id="test_event_routing",
            event_handler=ConcurrentSessionEventHandler(),
            task_executors={"normal": build_normal_executor}
        )

        sessions = [TaskSession(session_id=f"session_{i}") for i in range(5)]

        input_events = [
            InputEvent(
                event_type=EventType.INPUT,
                content={"query": f"request {i}"}
            )
            for i in range(5)
        ]

        # Execute 5 sessions concurrently
        logger.info("========== Execute 5 sessions concurrently ==========")
        results = await asyncio.gather(
            *[collect_stream_output(agent.stream(event, session))
              for event, session in zip(input_events, sessions)],
            return_exceptions=True
        )

        # Verify all sessions succeed
        for i, output in enumerate(results):
            assert not isinstance(output, Exception), f"Session {i} should not raise exception"
            assert len(output) > 0, f"Session {i} should have output"

            # Verify each session output only contains its own session_id
            session_id = f"session_{i}"
            assert any(session_id in text for text in output), \
                f"Output of session {i} should contain {session_id}"

        logger.info("✅ test_event_routing_to_correct_session passed")


# ==================== Exception handling tests ====================

class TestExceptionHandling:
    """Test exception handling"""

    @pytest.mark.asyncio
    async def test_task_execution_exception_handling(self):
        """Test task execution exception handling

        Test objectives:
        1. Task raises exception during execution
        2. Verify task status is updated to FAILED
        3. Verify handle_task_failed is called
        4. Verify error message is recorded correctly
        """
        agent = await build_test_agent(
            agent_id="test_task_exception",
            event_handler=FailingTaskEventHandler(),
            task_executors={
                "normal": build_normal_executor,
                "failing": build_failing_executor
            }
        )

        session = TaskSession(session_id="test_exception")

        input_event = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "test task exception"}
        )

        # Execute agent stream
        output = await collect_stream_output(agent.stream(input_event, session))

        # Verify status of failed task
        failed_tasks = await agent.controller.task_manager.get_task(
            task_filter=TaskFilter(task_id="failing_task")
        )
        failed_task = failed_tasks[0]

        assert failed_task is not None, "Failed task should exist"
        assert failed_task.status == TaskStatus.FAILED, \
            f"Status of failed task should be FAILED, actually {failed_task.status}"

        # Verify error message is recorded
        assert failed_task.error_message is not None, "Error message should be recorded"
        assert "failed intentionally" in failed_task.error_message, \
            f"Error message should contain failure reason, actually: {failed_task.error_message}"

        # Verify normal task is not affected
        normal_tasks = await agent.controller.task_manager.get_task(
            task_filter=TaskFilter(task_id="normal_task")
        )
        normal_task = normal_tasks[0]

        assert normal_task is not None, "Normal task should exist"
        assert normal_task.status == TaskStatus.COMPLETED, \
            f"Normal task should be completed, actual status is {normal_task.status}"

        logger.info("✅ test_task_execution_exception_handling passed")

    @pytest.mark.asyncio
    async def test_stream_output_exception_isolation(self):
        """Test that stream output exceptions do not affect other tasks

        Test objectives:
        1. Task 1 raises exception in the middle of streaming output
        2. Task 2 executes normally
        3. Verify exception of task 1 does not affect task 2
        4. Verify task 1 status is FAILED and task 2 status is COMPLETED
        """
        agent = await build_test_agent(
            agent_id="test_stream_exception",
            event_handler=StreamExceptionTaskEventHandler(),
            task_executors={
                "normal": build_normal_executor,
                "stream_exception": build_stream_exception_executor
            }
        )

        session = TaskSession(session_id="test_stream_exception")

        input_event = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "test stream exception"}
        )

        # Execute agent stream
        output = await collect_stream_output(agent.stream(input_event, session))

        # Verify status of task with stream output exception
        stream_fail_tasks = await agent.controller.task_manager.get_task(
            task_filter=TaskFilter(task_id="stream_fail_task")
        )
        stream_fail_task = stream_fail_tasks[0]

        assert stream_fail_task is not None, "Task with stream output exception should exist"
        assert stream_fail_task.status == TaskStatus.FAILED, \
            f"Status of task with stream output exception should be FAILED, actually {stream_fail_task.status}"

        # Verify normal task is not affected
        normal_tasks = await agent.controller.task_manager.get_task(
            task_filter=TaskFilter(task_id="normal_task_2")
        )
        normal_task = normal_tasks[0]

        assert normal_task is not None, "Normal task should exist"
        assert normal_task.status == TaskStatus.COMPLETED, \
            (f"Normal task should be completed (not affected by stream output exception),"
             f"actual status is {normal_task.status}")

        logger.info("✅ test_stream_output_exception_isolation passed")

    @pytest.mark.asyncio
    async def test_event_handler_exception_isolation(self):
        """Test that EventHandler exceptions do not affect other events

        Test objectives:
        1. When first task completes, handle_task_completion raises exception
        2. Other tasks continue to run
        3. Verify handle_task_completion of other tasks executes normally
        4. Verify exception is caught and does not affect overall flow
        """
        agent = await build_test_agent(
            agent_id="test_handler_exception",
            event_handler=ExceptionInEventHandlerEventHandler(),
            task_executors={"normal": build_normal_executor}
        )

        session = TaskSession(session_id="test_handler_exception")

        input_event = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "test handler exception"}
        )

        # Execute agent stream
        # Note: exceptions in EventHandler are caught and should not cause stream to fail
        output = await collect_stream_output(agent.stream(input_event, session))

        # Verify at least one task is completed
        assert len(output) > 0, "There should be output"

        # Verify multiple tasks are completed (proving first task exception does not affect others)
        all_tasks = await agent.controller.task_manager.get_task(task_filter=None)
        completed_tasks = [t for t in all_tasks if t.status == TaskStatus.COMPLETED]

        # There should be at least 2 completed tasks (first throws exception, others are normal)
        assert len(completed_tasks) >= 2, \
            f"There should be at least 2 completed tasks, actually {len(completed_tasks)}"

        logger.info("✅ test_event_handler_exception_isolation passed")

    @pytest.mark.asyncio
    async def test_exception_in_concurrent_sessions(self):
        """Test exception isolation in concurrent sessions
        
        Test objectives:
        1. Execute multiple sessions concurrently
        2. One session has a failed task
        3. Verify failure does not affect other sessions
        4. Verify all sessions can finish normally
        """
        # Create one normal agent and one failing agent
        normal_agent = await build_test_agent(
            agent_id="normal_agent",
            event_handler=ConcurrentSessionEventHandler(),
            task_executors={"normal": build_normal_executor}
        )

        failing_agent = await build_test_agent(
            agent_id="failing_agent",
            event_handler=FailingTaskEventHandler(),
            task_executors={
                "normal": build_normal_executor,
                "failing": build_failing_executor
            }
        )

        session_1 = TaskSession(session_id="normal_1")
        session_2 = TaskSession(session_id="failing")
        session_3 = TaskSession(session_id="normal_2")

        input_event = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "test"}
        )

        # Execute concurrently
        logger.info("========== Execute concurrently (including failing task) ==========")
        results = await asyncio.gather(
            collect_stream_output(normal_agent.stream(input_event, session_1)),
            collect_stream_output(failing_agent.stream(input_event, session_2)),
            collect_stream_output(normal_agent.stream(input_event, session_3)),
            return_exceptions=True
        )

        # Verify all sessions can finish (no exceptions raised)
        for i, output in enumerate(results, 1):
            assert not isinstance(output, Exception), \
                f"Session {i} should not raise exception (task failure != session failure)"
            assert len(output) > 0, f"Session {i} should have output"

        logger.info("✅ test_exception_in_concurrent_sessions passed")


# ==================== ControllerConfig tests ====================

class SlowTaskExecutor(TaskExecutor):
    """Task executor that runs slowly (for timeout testing)"""
    
    def __init__(self, dependencies: TaskExecutorDependencies, sleep_time: float = 10.0):
        super().__init__(dependencies)
        self.sleep_time = sleep_time
    
    async def execute_ability(self, task_id: str, session: Session) -> AsyncIterator[ControllerOutputChunk]:
        """Execute slow task"""
        yield ControllerOutputChunk(
            index=0,
            type="controller_output",
            payload=ControllerOutputPayload(
                type="processing",
                data=[TextDataFrame(type="text", text=f"Slow task {task_id} started, "
                                                      f"will sleep for {self.sleep_time}s")]
            ),
            last_chunk=False
        )
        
        try:
            # Sleep for a long time (will be interrupted by timeout or cancellation) except 2nd task
            if task_id == "timeout_task_2":
                await asyncio.sleep(1.0)
            else:
                await asyncio.sleep(self.sleep_time)
            
            # If we reach here, task completed normally
            yield ControllerOutputChunk(
                index=1,
                type="controller_output",
                payload=ControllerOutputPayload(
                    type=EventType.TASK_COMPLETION,
                    data=[TextDataFrame(type="text", text=f"Slow task {task_id} completed")]
                ),
                last_chunk=True
            )
        except asyncio.CancelledError:
            # Task was cancelled (by timeout or manual cancellation)
            logger.info(f"SlowTaskExecutor: task {task_id} was cancelled")
            raise
    
    async def can_pause(self, task_id: str, session: Session) -> Tuple[bool, str]:
        return True, ""
    
    async def pause(self, task_id: str, session: Session) -> bool:
        return True
    
    async def can_cancel(self, task_id: str, session: Session) -> Tuple[bool, str]:
        return True, ""
    
    async def cancel(self, task_id: str, session: Session) -> bool:
        return True


class TestControllerConfig:
    """Test controller config"""

    @pytest.mark.asyncio
    async def test_task_timeout(self):
        """Test task timeout configuration
        
        Test objectives:
        1. Task exceeds configured timeout and is marked as FAILED and don't affect other tasks
        2. TimeoutError is handled correctly and doesn't affect other tasks
        3. Timeout error message is properly set
        """
        # Create agent with 2 second timeout
        agent = await build_test_agent(
            agent_id="test_timeout",
            event_handler=TimeoutTestEventHandler(),
            task_executors={
                "slow": build_slow_executor_factory(sleep_time=10.0)
            }
        )
        
        # Configure timeout
        agent.controller.task_scheduler.config.task_timeout = 2.0  # 2 seconds
        agent.controller.task_scheduler.config.schedule_interval = 0.1
        
        session = TaskSession(session_id="test_timeout_session")
        input_event = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "test timeout"},
        )
        
        # Start streaming
        stream_task = asyncio.create_task(
            collect_stream_output(agent.stream(input_event, session))
        )
        
        try:
            # Wait for task to timeout (should happen after ~2 seconds)
            await asyncio.sleep(3.0)
            
            # Check task status
            task_manager = agent.controller.task_scheduler.task_manager
            tasks = await task_manager.get_task(task_filter=TaskFilter(session_id=session.session_id()))
            
            assert len(tasks) > 0, "Should have at least one task"

            c = Counter(task.status for task in tasks)

            assert c[TaskStatus.COMPLETED] >= 1, "normal task should be completed"
            assert c[TaskStatus.FAILED] == 2, "slow tasks should be failed"
            
            logger.info("✅ test_task_timeout passed")
            
        finally:
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass
            await agent.controller.stop()

    @pytest.mark.asyncio
    async def test_timeout_vs_manual_cancel(self):
        """Test timeout vs manual cancellation
        
        Test objectives:
        1. Manual cancellation before timeout should result in CANCELED status
        2. TimeoutError and CancelledError are handled separately
        """
        # Create agent with long timeout
        agent = await build_test_agent(
            agent_id="test_timeout_vs_cancel",
            event_handler=TimeoutTestEventHandler(),
            task_executors={
                "slow": build_slow_executor_factory(sleep_time=10.0)
            }
        )
        
        # Set a long timeout (10 seconds)
        agent.controller.task_scheduler.config.task_timeout = 10.0
        agent.controller.task_scheduler.config.schedule_interval = 0.1
        
        session = TaskSession(session_id="test_cancel_session")
        input_event = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "test manual cancel"},
        )
        
        # Start streaming
        stream_task = asyncio.create_task(
            collect_stream_output(agent.stream(input_event, session))
        )
        
        try:
            # Wait for task to start
            await asyncio.sleep(0.5)
            
            # Manually cancel the task (before timeout)
            task_manager = agent.controller.task_scheduler.task_manager
            tasks = await task_manager.get_task(task_filter=TaskFilter(session_id=session.session_id()))
            assert len(tasks) > 0, "Should have at least one task"
            
            task_id = tasks[0].task_id
            logger.info(f"Manually cancelling task {task_id}")
            
            success = await agent.controller.task_scheduler.cancel_task(task_id, session)
            assert success, "Manual cancellation should succeed"
            
            await asyncio.sleep(0.5)
            
            # Verify task is CANCELED (not FAILED from timeout)
            tasks = await task_manager.get_task(task_filter=TaskFilter(task_id=task_id))
            assert len(tasks) > 0, "Task should still exist"
            
            cancelled_task = tasks[0]
            logger.info(f"Task status after cancel: {cancelled_task.status}")
            
            assert cancelled_task.status == TaskStatus.CANCELED, f"Task should be CANCELED, got {cancelled_task.status}"
            
            logger.info("✅ test_timeout_vs_manual_cancel passed: Manual cancel works independently from timeout")
            
        finally:
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass
            await agent.controller.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
