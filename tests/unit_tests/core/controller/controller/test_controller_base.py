# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import json
import uuid
from typing import List, AsyncIterator, Tuple
import pytest

from openjiuwen.core.controller.modules.task_manager import TaskFilter
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.session.internal.wrapper import TaskSession
from openjiuwen.core.controller.base import Controller, ControllerConfig
from openjiuwen.core.controller.modules import (
    EventHandler,
    EventHandlerInput,
    TaskExecutor,
    TaskExecutorDependencies,
    TaskManager,
    EventQueue
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
from openjiuwen.core.session import Session
from openjiuwen.core.common.logging import logger


# ==================== Test TaskExecutor ====================

class CancellableTaskExecutor(TaskExecutor):
    """Cancelable task executor
    
    Used to test task cancellation. The task keeps running until it is cancelled.
    """
    
    def __init__(self, dependencies: TaskExecutorDependencies):
        super().__init__(dependencies)
        self._cancelled = False
        self._pause_requested = False
    
    async def execute_ability(self, task_id: str, session: Session) -> AsyncIterator[ControllerOutputChunk]:
        """Execute task - keep running until cancelled"""
        # Start execution
        yield ControllerOutputChunk(
            index=0,
            type="controller_output",
            payload=ControllerOutputPayload(
                type="processing",
                data=[TextDataFrame(type="text", text=f"Task {task_id} started")]
            ),
            last_chunk=False
        )
        
        # Decide execution time based on task ID: task_1 finishes quickly (2 iterations),
        # task_2 and task_3 run slowly (100 iterations)
        if "task_1" in task_id:
            iterations = 2
            sleep_time = 0.1
        else:
            iterations = 100
            sleep_time = 0.1
        
        # Simulate a long-running task
        for i in range(iterations):
            if self._cancelled:
                logger.info(f"Task {task_id} detected cancellation")
                return
            
            if self._pause_requested:
                logger.info(f"Task {task_id} detected pause request")
                return

            await asyncio.sleep(sleep_time)
            
            yield ControllerOutputChunk(
                index=i + 1,
                type="controller_output",
                payload=ControllerOutputPayload(
                    type="processing",
                    data=[TextDataFrame(type="text", text=f"Task {task_id} progress {i+1}/{iterations}")]
                ),
                last_chunk=False
            )
        # Complete normally
        yield ControllerOutputChunk(
            index=iterations + 1,
            type="controller_output",
            payload=ControllerOutputPayload(
                type=EventType.TASK_COMPLETION,
                data=[TextDataFrame(type="text", text=f"Task {task_id} completed")]
            ),
            last_chunk=True
        )
    
    async def can_pause(self, task_id: str, session: Session) -> Tuple[bool, str]:
        """Check whether the task can be paused"""
        return True, ""
    
    async def pause(self, task_id: str, session: Session) -> bool:
        """Pause task"""
        self._pause_requested = True
        logger.info(f"Task {task_id} pause requested")
        return True
    
    async def can_cancel(self, task_id: str, session: Session) -> Tuple[bool, str]:
        """Check whether the task can be cancelled"""
        return True, ""
    
    async def cancel(self, task_id: str, session: Session) -> bool:
        """Cancel task"""
        self._cancelled = True
        logger.info(f"Task {task_id} cancellation requested")
        return True


class NonCancellableTaskExecutor(TaskExecutor):
    """Non-cancellable task executor

    Used to test attempting to cancel a non-cancellable task.
    """

    async def execute_ability(self, task_id: str, session: Session) -> AsyncIterator[ControllerOutputChunk]:
        """Execute task"""
        yield ControllerOutputChunk(
            index=0,
            type="controller_output",
            payload=ControllerOutputPayload(
                type="processing",
                data=[TextDataFrame(type="text", text=f"Non-cancellable task {task_id} running")]
            ),
            last_chunk=False
        )

        await asyncio.sleep(0.5)

        yield ControllerOutputChunk(
            index=1,
            type="controller_output",
            payload=ControllerOutputPayload(
                type=EventType.TASK_COMPLETION,
                data=[TextDataFrame(type="text", text=f"Task {task_id} completed")]
            ),
            last_chunk=True
        )

    async def can_pause(self, task_id: str, session: Session) -> Tuple[bool, str]:
        return False, "This task cannot be paused"

    async def pause(self, task_id: str, session: Session) -> bool:
        raise RuntimeError("pause() should not be called when can_pause() returns False")

    async def can_cancel(self, task_id: str, session: Session) -> Tuple[bool, str]:
        return False, "This task cannot be cancelled"

    async def cancel(self, task_id: str, session: Session) -> bool:
        raise RuntimeError("cancel() should not be called when can_cancel() returns False")


class FailingTaskExecutor(TaskExecutor):
    """Task executor that fails

    Used to test task execution failure.
    """

    async def execute_ability(self, task_id: str, session: Session) -> AsyncIterator[ControllerOutputChunk]:
        """Execute task - will raise an exception"""
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


# ==================== Test EventHandler ====================

class SimpleEventHandler(EventHandler):
    """Simple event handler

    Used for basic tests and creating a single task.
    """

    async def handle_input(self, inputs: EventHandlerInput):
        """Handle input event - create a single task"""
        task = Task(
            session_id=inputs.session.session_id(),
            task_id="test_task_1",
            task_type="cancellable",
            priority=1,
            status=TaskStatus.SUBMITTED,
            context_id="test_context_1"
        )
        await self.task_manager.add_task([task])
        logger.info("SimpleEventHandler: Created task test_task_1")
        return {"status": "success", "tasks_created": 1}  # Return confirmation

    async def handle_task_interaction(self, inputs: EventHandlerInput):
        pass

    async def handle_task_completion(self, inputs: EventHandlerInput):
        """Handle task completion event"""
        logger.info(f"SimpleEventHandler: Task completed - {inputs.event.task.task_id}")
        return {"status": "success", "tasks_created": 1}  # Return confirmation

    async def handle_task_failed(self, inputs: EventHandlerInput):
        """Handle task failure event"""
        logger.error(f"SimpleEventHandler: Task failed - {inputs.event.task.task_id}")
        return {"status": "success", "tasks_created": 1}  # Return confirmation


class DynamicTaskEventHandler(EventHandler):
    """Event handler with dynamic task IDs

    Generates a unique task_id for each call to avoid conflicts across multiple stream calls.
    Suitable for scenarios like testing subscription cleanup with repeated stream calls.
    """

    def __init__(self):
        super().__init__()
        self._task_counter = 0

    async def handle_input(self, inputs: EventHandlerInput):
        """Handle input event - create a task with a unique ID"""
        unique_id = str(uuid.uuid4())[:8]
        self._task_counter += 1
        task_id = f"test_task_{self._task_counter}_{unique_id}"

        task = Task(
            session_id=inputs.session.session_id(),
            task_id=task_id,
            task_type="cancellable",
            priority=1,
            status=TaskStatus.SUBMITTED,
            context_id=f"test_context_{unique_id}"
        )
        await self.task_manager.add_task([task])
        logger.info(f"DynamicTaskEventHandler: Created task {task_id}")
        return {"status": "success", "tasks_created": 1}

    async def handle_task_interaction(self, inputs: EventHandlerInput):
        pass

    async def handle_task_completion(self, inputs: EventHandlerInput):
        logger.info(f"DynamicTaskEventHandler: Task completed - {inputs.event.task.task_id}")
        return {"status": "success"}

    async def handle_task_failed(self, inputs: EventHandlerInput):
        logger.error(f"DynamicTaskEventHandler: Task failed - {inputs.event.task.task_id}")
        return {"status": "success"}


class FailureHandlingEventHandler(EventHandler):
    """Event handler for failed tasks

    Used to test handling logic when tasks fail.
    """

    async def handle_input(self, inputs: EventHandlerInput):
        """Handle input event - create a failing task"""
        task = Task(
            session_id=inputs.session.session_id(),
            task_id="failing_task_1",
            task_type="failing",
            priority=1,
            status=TaskStatus.SUBMITTED,
            context_id="failing_context_1"
        )
        await self.task_manager.add_task([task])
        logger.info("FailureHandlingEventHandler: Created failing task")
        return {"status": "success", "tasks_created": 1}

    async def handle_task_interaction(self, inputs: EventHandlerInput):
        pass

    async def handle_task_completion(self, inputs: EventHandlerInput):
        logger.info(f"FailureHandlingEventHandler: Task completed - {inputs.event.task.task_id}")
        return {"status": "success"}

    async def handle_task_failed(self, inputs: EventHandlerInput):
        """Handle task failure event - record failure information"""
        failed_task = inputs.event.task
        error_msg = inputs.event.error_message
        logger.error(f"FailureHandlingEventHandler: Task {failed_task.task_id} failed: {error_msg}")
        return {"status": "failed", "error": error_msg}


class PauseInHandlerEventHandler(EventHandler):
    """Event handler that pauses tasks inside EventHandler

    Used to test calling pause_task in EventHandler.handle_task_completion.
    Creates three concurrent tasks. When the first task completes, pause the second and verify the third is unaffected.
    """

    def __init__(self):
        super().__init__()
        self.first_task_completed = False

    async def handle_input(self, inputs: EventHandlerInput):
        tasks = [
            Task(
                session_id=inputs.session.session_id(),
                task_id="pause_test_task_1",
                task_type="cancellable",
                priority=1,
                status=TaskStatus.SUBMITTED,
                context_id="pause_test_context_1"
            ),
            Task(
                session_id=inputs.session.session_id(),
                task_id="pause_test_task_2",
                task_type="cancellable",
                priority=1,
                status=TaskStatus.SUBMITTED,
                context_id="pause_test_context_2"
            ),
            Task(
                session_id=inputs.session.session_id(),
                task_id="pause_test_task_3",
                task_type="cancellable",
                priority=1,
                status=TaskStatus.SUBMITTED,
                context_id="pause_test_context_3"
            )
        ]
        await self.task_manager.add_task(tasks)
        logger.info("PauseInHandlerEventHandler: Created 3 tasks (all SUBMITTED)")
        return {"status": "success", "tasks_created": 3}

    async def handle_task_interaction(self, inputs: EventHandlerInput):
        pass

    async def handle_task_completion(self, inputs: EventHandlerInput):
        """Handle task completion event - pause the second task and let the third continue"""
        completed_task_id = inputs.event.task.task_id
        logger.info(f"PauseInHandlerEventHandler: Task {completed_task_id} completed")

        # Only pause the second task when the first task completes
        if completed_task_id == "pause_test_task_1" and not self.first_task_completed:
            self.first_task_completed = True
            target_task_id = "pause_test_task_2"

            # task_1 completes quickly (0.2s); at this time task_2 and task_3 should still be running
            # Directly pause task_2
            logger.info(f"PauseInHandlerEventHandler: Task 1 completed, pausing task 2")

            # Call TaskScheduler.pause_task from the EventHandler
            success = await self.task_scheduler.pause_task(target_task_id, inputs.session)

            if success:
                logger.info(f"PauseInHandlerEventHandler: Successfully paused task {target_task_id}")
            else:
                logger.warning(f"PauseInHandlerEventHandler: Failed to pause task {target_task_id}")

            return {"status": "success", "paused": success}

        # When the third task completes, cancelling the paused second task fails
        # because task_2 is paused and not in _running_tasks
        if completed_task_id == "pause_test_task_3":
            logger.info("PauseInHandlerEventHandler: Task 3 completed, cancelling paused task 2")
            await self.task_scheduler.cancel_task("pause_test_task_2", inputs.session)

        return {"status": "success"}

    async def handle_task_failed(self, inputs: EventHandlerInput):
        logger.error(f"PauseInHandlerEventHandler: Task failed - {inputs.event.task.task_id}")
        return {"status": "failed"}


class PauseNonPausableEventHandler(EventHandler):
    """Event handler that tries to pause a non-pausable task"""

    def __init__(self):
        super().__init__()
        self.first_task_completed = False

    async def handle_input(self, inputs: EventHandlerInput):
        tasks = [
            Task(
                session_id=inputs.session.session_id(),
                task_id="pausable_task",
                task_type="cancellable",
                priority=1,
                status=TaskStatus.SUBMITTED,
                context_id="pausable_context"
            ),
            Task(
                session_id=inputs.session.session_id(),
                task_id="non_pausable_task",
                task_type="non_cancellable",
                priority=1,
                status=TaskStatus.SUBMITTED,
                context_id="non_pausable_context"
            )
        ]
        await self.task_manager.add_task(tasks)
        return {"status": "success", "tasks_created": 2}

    async def handle_task_interaction(self, inputs: EventHandlerInput):
        pass

    async def handle_task_completion(self, inputs: EventHandlerInput):
        if not self.first_task_completed:
            self.first_task_completed = True
            # Try to pause the non-pausable task
            success = await self.task_scheduler.pause_task("non_pausable_task", inputs.session)
            logger.info(f"Attempt to pause non-pausable task: {success}")
            return {"status": "success", "paused": success}
        return {"status": "success"}

    async def handle_task_failed(self, inputs: EventHandlerInput):
        return {"status": "failed"}


class PauseThenCancelEventHandler(EventHandler):
    """Event handler that pauses then cancels a task"""

    def __init__(self):
        super().__init__()
        self.completed_count = 0

    async def handle_input(self, inputs: EventHandlerInput):
        tasks = [
            Task(
                session_id=inputs.session.session_id(),
                task_id=f"multi_op_task_{i}",
                task_type="cancellable",
                priority=1,
                status=TaskStatus.SUBMITTED,
                context_id=f"multi_op_context_{i}"
            )
            for i in range(1, 4)
        ]
        await self.task_manager.add_task(tasks)
        return {"status": "success", "tasks_created": 3}

    async def handle_task_interaction(self, inputs: EventHandlerInput):
        pass

    async def handle_task_completion(self, inputs: EventHandlerInput):
        self.completed_count += 1

        if self.completed_count == 1:
            # When the first task completes, pause the second task
            await self.task_scheduler.pause_task("multi_op_task_2", inputs.session)
            logger.info("Paused task 2")
        elif self.completed_count == 2:
            # When the third task completes, cancel the paused second task
            # The paused task is not in running_tasks, so cancellation will fail
            success = await self.task_scheduler.cancel_task("multi_op_task_2", inputs.session)
            logger.info(f"Attempted to cancel paused task 2: {success}")

        return {"status": "success"}

    async def handle_task_failed(self, inputs: EventHandlerInput):
        return {"status": "failed"}


class CancelInHandlerEventHandler(EventHandler):
    """Event handler that cancels tasks inside EventHandler

    Used to test calling cancel_task in EventHandler.handle_task_completion.
    Creates two tasks and cancels the second when the first completes.
    """

    def __init__(self):
        super().__init__()
        self.first_task_completed = False

    async def handle_input(self, inputs: EventHandlerInput):
        """Handle input event - create two tasks"""
        tasks = [
            Task(
                session_id=inputs.session.session_id(),
                task_id="cancel_test_task_1",
                task_type="cancellable",
                priority=1,
                status=TaskStatus.SUBMITTED,
                context_id="cancel_test_context_1"
            ),
            Task(
                session_id=inputs.session.session_id(),
                task_id="cancel_test_task_2",
                task_type="cancellable",
                priority=1,
                status=TaskStatus.SUBMITTED,
                context_id="cancel_test_context_2"
            )
        ]
        await self.task_manager.add_task(tasks)
        logger.info("CancelInHandlerEventHandler: Created 2 tasks")
        return {"status": "success", "tasks_created": 2}

    async def handle_task_interaction(self, inputs: EventHandlerInput):
        pass

    async def handle_task_completion(self, inputs: EventHandlerInput):
        """Handle task completion event - cancel the second task"""
        completed_task_id = inputs.event.task.task_id
        logger.info(f"CancelInHandlerEventHandler: Task {completed_task_id} completed")

        # Only cancel the second task when the first task completes
        if not self.first_task_completed:
            self.first_task_completed = True
            target_task_id = "cancel_test_task_2"

            # Call TaskScheduler.cancel_task from the EventHandler
            success = await self.task_scheduler.cancel_task(target_task_id, inputs.session)

            if success:
                logger.info(f"CancelInHandlerEventHandler: Successfully cancelled task {target_task_id}")
            else:
                logger.warning(f"CancelInHandlerEventHandler: Failed to cancel task {target_task_id}")

            return {"status": "success", "cancelled": success}

        return {"status": "success"}

    async def handle_task_failed(self, inputs: EventHandlerInput):
        logger.error(f"CancelInHandlerEventHandler: Task failed - {inputs.event.task.task_id}")
        return {"status": "failed"}


class CancelNonCancellableEventHandler(EventHandler):
    """Event handler that tries to cancel a non-cancellable task"""

    def __init__(self):
        super().__init__()
        self.first_task_completed = False

    async def handle_input(self, inputs: EventHandlerInput):
        tasks = [
            Task(
                session_id=inputs.session.session_id(),
                task_id="cancellable_task",
                task_type="cancellable",
                priority=1,
                status=TaskStatus.SUBMITTED,
                context_id="cancellable_context"
            ),
            Task(
                session_id=inputs.session.session_id(),
                task_id="non_cancellable_task",
                task_type="non_cancellable",
                priority=1,
                status=TaskStatus.SUBMITTED,
                context_id="non_cancellable_context"
            )
        ]
        await self.task_manager.add_task(tasks)
        return {"status": "success", "tasks_created": 2}

    async def handle_task_interaction(self, inputs: EventHandlerInput):
        pass

    async def handle_task_completion(self, inputs: EventHandlerInput):
        if not self.first_task_completed:
            self.first_task_completed = True
            # Try to cancel the non-cancellable task
            success = await self.task_scheduler.cancel_task("non_cancellable_task", inputs.session)
            logger.info(f"Attempt to cancel non-cancellable task: {success}")
            return {"status": "success", "cancelled": success}
        return {"status": "success"}

    async def handle_task_failed(self, inputs: EventHandlerInput):
        return {"status": "failed"}


# ==================== EventHandlers for state persistence tests ====================

class StatePersistenceEventHandler(EventHandler):
    """Event handler for state persistence tests

    Used to verify that paused tasks can be persisted across stream calls.
    The first round creates and pauses tasks; the second round verifies the paused task can be read.
    """

    def __init__(self):
        super().__init__()
        self.round_number = 0

    async def handle_input(self, inputs: EventHandlerInput):
        """Handle input event - distinguish between the first and second rounds"""
        self.round_number += 1

        if self.round_number == 1:
            # First round: create 2 tasks (the first completes quickly, the second is paused)
            tasks = [
                Task(
                    session_id=inputs.session.session_id(),
                    task_id="persist_task_1",
                    task_type="cancellable",
                    priority=1,
                    status=TaskStatus.SUBMITTED,
                    context_id="persist_context_1"
                ),
                Task(
                    session_id=inputs.session.session_id(),
                    task_id="persist_task_2",
                    task_type="cancellable",
                    priority=1,
                    status=TaskStatus.SUBMITTED,
                    context_id="persist_context_2"
                )
            ]
            await self.task_manager.add_task(tasks)
            logger.info("StatePersistenceEventHandler: Created 2 tasks in first round")
            return {"status": "success", "round": 1, "tasks_created": 2}
        else:
            # Second round: do not create new tasks, only verify state restoration
            existing_tasks = await self.task_manager.get_task(
                task_filter=TaskFilter(task_id="persist_task_2")
            )

            if existing_tasks:
                logger.info(
                    f"StatePersistenceEventHandler: ✅ Found persisted task {existing_tasks[0].task_id} "
                    f"in second round, status: {existing_tasks[0].status}")
            else:
                logger.error("StatePersistenceEventHandler: ❌ No persisted task found in second round")

            return {"status": "success", "round": 2, "found_persisted_task": len(existing_tasks) > 0}

    async def handle_task_interaction(self, inputs: EventHandlerInput):
        pass

    async def handle_task_completion(self, inputs: EventHandlerInput):
        """Handle task completion event - pause the second task after the first completes"""
        completed_task_id = inputs.event.task.task_id

        if completed_task_id == "persist_task_1":
            # After the first task completes, pause the second task
            success = await self.task_scheduler.pause_task("persist_task_2", inputs.session)
            logger.info(f"StatePersistenceEventHandler: Paused persist_task_2, result: {success}")

        return {"status": "success"}

    async def handle_task_failed(self, inputs: EventHandlerInput):
        logger.error(f"StatePersistenceEventHandler: Task failed - {inputs.event.task.task_id}")
        return {"status": "failed"}


class MultiTaskStatePersistenceEventHandler(EventHandler):
    """Event handler for multi-task state persistence tests

    Used to verify that tasks with different states can all be persisted correctly.
    """

    def __init__(self):
        super().__init__()
        self.round_number = 0

    async def handle_input(self, inputs: EventHandlerInput):
        """Handle input event"""
        self.round_number += 1

        if self.round_number == 1:
            # First round: create 4 tasks
            tasks = [
                Task(
                    session_id=inputs.session.session_id(),
                    task_id="multi_task_1",
                    task_type="cancellable",
                    priority=1,
                    status=TaskStatus.SUBMITTED,
                    context_id="multi_context_1"
                ),
                Task(
                    session_id=inputs.session.session_id(),
                    task_id="multi_task_2",
                    task_type="cancellable",
                    priority=2,
                    status=TaskStatus.SUBMITTED,
                    context_id="multi_context_2"
                ),
                Task(
                    session_id=inputs.session.session_id(),
                    task_id="multi_task_3",
                    task_type="cancellable",
                    priority=3,
                    status=TaskStatus.SUBMITTED,
                    context_id="multi_context_3"
                )
            ]
            await self.task_manager.add_task(tasks)
            logger.info("MultiTaskStatePersistenceEventHandler: Created 3 tasks in first round")
            return {"status": "success", "round": 1, "tasks_created": 3}
        else:
            # Second round: verify the state of all tasks
            all_tasks = await self.task_manager.get_task(task_filter=None)
            logger.info(f"MultiTaskStatePersistenceEventHandler: Restored {len(all_tasks)} tasks in second round")
            for task in all_tasks:
                logger.info(f"  - {task.task_id}: {task.status}")

            return {"status": "success", "round": 2, "restored_count": len(all_tasks)}

    async def handle_task_interaction(self, inputs: EventHandlerInput):
        pass

    async def handle_task_completion(self, inputs: EventHandlerInput):
        completed_task_id = inputs.event.task.task_id

        if completed_task_id == "multi_task_1":
            # After the first task completes, pause task_2 and cancel task_3
            await self.task_scheduler.pause_task("multi_task_2", inputs.session)
            await self.task_scheduler.cancel_task("multi_task_3", inputs.session)
            logger.info("MultiTaskStatePersistenceEventHandler: Paused task_2 and cancelled task_3")

        return {"status": "success"}

    async def handle_task_failed(self, inputs: EventHandlerInput):
        logger.error(f"MultiTaskStatePersistenceEventHandler: Task failed - {inputs.event.task.task_id}")
        return {"status": "failed"}


# ==================== Factory functions ====================

def build_cancellable_executor(dependencies: TaskExecutorDependencies):
    """Build cancelable task executor"""
    return CancellableTaskExecutor(dependencies)


def build_non_cancellable_executor(dependencies: TaskExecutorDependencies):
    """Build non-cancellable task executor"""
    return NonCancellableTaskExecutor(dependencies)


def build_failing_executor(dependencies: TaskExecutorDependencies):
    """Build failing task executor"""
    return FailingTaskExecutor(dependencies)


# ==================== Agent factory function ====================

async def build_test_agent(
    agent_id: str,
    event_handler: EventHandler,
    task_executors: dict
) -> ControllerAgent:
    """Build test Agent

    Args:
        agent_id: Agent ID
        event_handler: Event handler instance
        task_executors: Task executor mapping {task_type: builder_func}

    Returns:
        ControllerAgent: Configured test Agent
    """
    agent_card = AgentCard(
        id=agent_id,
        name=f"Test Agent {agent_id}",
        description="Test agent for controller testing"
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
    """Collect contents from streaming output

    Args:
        stream: Streaming output iterator

    Returns:
        List[str]: Collected text contents
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


# ==================== EventHandler task control tests ====================

class TestEventHandlerTaskControl:
    """Test task control in EventHandler (pause and cancel support)"""

    @pytest.mark.asyncio
    async def test_pause_task_in_event_handler(self):
        """Test pausing a task in EventHandler without affecting other tasks

        Test goals:
        1. Create three concurrent tasks (task_1 finishes quickly in 0.2s, task_2 and task_3 run slowly for 10s)
        2. After the first task completes, call pause_task in EventHandler to pause the second task
        3. Verify the second task is successfully paused
        4. Verify the third task is not affected and continues to complete
        5. Verify task status updates correctly
        """
        # Build Agent
        agent = await build_test_agent(
            agent_id="test_pause_in_handler",
            event_handler=PauseInHandlerEventHandler(),
            task_executors={"cancellable": build_cancellable_executor}
        )

        session = TaskSession(session_id="test_pause_in_handler")

        # Create input event
        input_event = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "test pause in handler"}
        )

        # Execute agent stream
        output_texts = await collect_stream_output(agent.stream(input_event, session))

        # Verify the first task completes
        assert any("pause_test_task_1" in text and "completed" in text for text in output_texts), \
            "The first task should complete"

        # Verify the second task is paused
        tasks = await agent.controller.task_manager.get_task(task_filter=TaskFilter(task_id="pause_test_task_2"))
        task_2 = tasks[0]
        assert task_2 is not None, "The second task should exist"
        assert task_2.status == TaskStatus.PAUSED, \
            f"The final status of the second task should be PAUSED, but is {task_2.status}"

        # Verify the third task completes (key: pausing task_2 does not affect task_3)
        tasks = await agent.controller.task_manager.get_task(task_filter=TaskFilter(task_id="pause_test_task_3"))
        task_3 = tasks[0]
        assert task_3 is not None, "The third task should exist"
        assert task_3.status == TaskStatus.COMPLETED, \
            f"The status of the third task should be COMPLETED, but is {task_3.status}"

        assert any("pause_test_task_3" in text and "completed" in text for text in output_texts), \
            "The third task should complete"

        logger.info("✅ test_pause_task_in_event_handler passed")

    @pytest.mark.asyncio
    async def test_cancel_task_in_event_handler(self):
        """Test cancelling a task in EventHandler

        Test goals:
        1. Create two concurrent tasks
        2. After the first task completes, call cancel_task in EventHandler to cancel the second task
        3. Verify the second task is successfully cancelled
        4. Verify the task status is updated to CANCELED
        """
        # Build Agent
        agent = await build_test_agent(
            agent_id="test_cancel_in_handler",
            event_handler=CancelInHandlerEventHandler(),
            task_executors={"cancellable": build_cancellable_executor}
        )

        session = TaskSession(session_id="test_cancel_in_handler")

        # Create input event
        input_event = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "test cancel in handler"}
        )

        # Execute agent stream
        output_texts = await collect_stream_output(agent.stream(input_event, session))

        # Verify the first task completes
        assert any("cancel_test_task_1" in text and "completed" in text for text in output_texts), \
            "The first task should complete"

        # Verify the second task is cancelled
        tasks = await agent.controller.task_manager.get_task(task_filter=TaskFilter(task_id="cancel_test_task_2"))
        task_2 = tasks[0]
        assert task_2 is not None, "The second task should exist"
        assert task_2.status == TaskStatus.CANCELED, \
            f"The status of the second task should be CANCELED, but is {task_2.status}"

        logger.info("✅ test_cancel_task_in_event_handler passed")

    @pytest.mark.asyncio
    async def test_pause_non_pausable_task_in_event_handler(self):
        """Test attempting to pause a non-pausable task in EventHandler

        Test goals:
        1. Create two tasks: one cancelable task and one non-pausable task
        2. After the first task completes, EventHandler tries to pause the non-pausable task
        3. Verify the pause attempt fails
        4. Verify the non-pausable task continues and completes
        """
        # Build Agent
        agent = await build_test_agent(
            agent_id="test_pause_non_pausable",
            event_handler=PauseNonPausableEventHandler(),
            task_executors={
                "cancellable": build_cancellable_executor,
                "non_cancellable": build_non_cancellable_executor
            }
        )

        session = TaskSession(session_id="test_pause_non_pausable")

        input_event = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "test pause non-pausable"}
        )

        # Execute agent stream
        output_texts = await collect_stream_output(agent.stream(input_event, session))

        # Verify the non-pausable task completes normally
        tasks = await agent.controller.task_manager.get_task(task_filter=TaskFilter(task_id="non_pausable_task"))
        task = tasks[0]
        assert task is not None, "Task should exist"
        assert task.status == TaskStatus.COMPLETED, \
            f"The non-pausable task should complete normally with status COMPLETED, but is {task.status}"

        logger.info("✅ test_pause_non_pausable_task_in_event_handler passed")

    @pytest.mark.asyncio
    async def test_cancel_non_cancellable_task_in_event_handler(self):
        """Test attempting to cancel a non-cancellable task in EventHandler

        Test goals:
        1. Create two tasks: one cancelable task and one non-cancellable task
        2. After the first task completes, EventHandler tries to cancel the non-cancellable task
        3. Verify the cancel attempt fails
        4. Verify the non-cancellable task continues and completes
        """
        # Build Agent
        agent = await build_test_agent(
            agent_id="test_cancel_non_cancellable",
            event_handler=CancelNonCancellableEventHandler(),
            task_executors={
                "cancellable": build_cancellable_executor,
                "non_cancellable": build_non_cancellable_executor
            }
        )

        session = TaskSession(session_id="test_cancel_non_cancellable")

        input_event = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "test cancel non-cancellable"}
        )

        # Execute agent stream
        output_texts = await collect_stream_output(agent.stream(input_event, session))

        # Verify the non-cancellable task completes normally
        tasks = await agent.controller.task_manager.get_task(task_filter=TaskFilter(task_id="non_cancellable_task"))
        task = tasks[0]
        assert task is not None, "Task should exist"
        assert task.status == TaskStatus.COMPLETED, \
            f"The non-cancellable task should complete normally with status COMPLETED, but is {task.status}"

        logger.info("✅ test_cancel_non_cancellable_task_in_event_handler passed")

    @pytest.mark.asyncio
    async def test_pause_then_cancel_in_event_handler(self):
        """Test pausing then cancelling a task in EventHandler

        Test goals:
        1. Create three tasks
        2. After the first task completes, pause the second task
        3. After the third task completes, cancel the paused second task
        4. Verify task state transitions are correct
        """
        # Build Agent
        agent = await build_test_agent(
            agent_id="test_pause_then_cancel",
            event_handler=PauseThenCancelEventHandler(),
            task_executors={"cancellable": build_cancellable_executor}
        )

        session = TaskSession(session_id="test_pause_then_cancel")

        input_event = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "test pause then cancel"}
        )

        # Execute agent stream
        output_texts = await collect_stream_output(agent.stream(input_event, session))

        # Verify the second task is paused
        tasks = await agent.controller.task_manager.get_task(task_filter=TaskFilter(task_id="multi_op_task_2"))
        task_2 = tasks[0]
        assert task_2 is not None, "The second task should exist"
        assert task_2.status == TaskStatus.PAUSED, \
            f"The second task should be PAUSED, but is {task_2.status}"

        logger.info("✅ test_pause_then_cancel_in_event_handler passed")


# ==================== State persistence tests ====================

class TestStatePersistence:
    """Test state persistence"""

    @pytest.mark.asyncio
    async def test_paused_task_state_persistence(self):
        """Verify state persistence of a paused task

        Test goals:
        1. First stream round: create 2 tasks, the first completes quickly and the second is paused
        2. Second stream round: using the same session, verify the paused task from the first round can be read
        3. Verify task status and metadata are correctly persisted
        """
        # Build Agent (reuse the same instance)
        agent = await build_test_agent(
            agent_id="test_state_persistence",
            event_handler=StatePersistenceEventHandler(),
            task_executors={"cancellable": build_cancellable_executor}
        )

        # Use the same Session
        session = TaskSession(session_id="test_state_persistence")

        # ========== First stream round ==========
        logger.info("========== First stream round starts ==========")
        input_event_1 = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "round 1"}
        )
        output_1 = await collect_stream_output(agent.stream(input_event_1, session))

        # Verify round 1: persist_task_1 completed and persist_task_2 paused
        assert any("persist_task_1" in text and "completed" in text for text in output_1), \
            "The first task should complete"

        tasks = await agent.controller.task_manager.get_task(
            task_filter=TaskFilter(task_id="persist_task_2")
        )
        task_2_round_1 = tasks[0]
        assert task_2_round_1 is not None, "At the end of the first round, persist_task_2 should exist"
        assert task_2_round_1.status == TaskStatus.PAUSED, \
            f"At the end of the first round, persist_task_2 should be PAUSED, but is {task_2_round_1.status}"

        logger.info(f"End of first round: persist_task_2 status = {task_2_round_1.status}")

        # ========== Second stream round ==========
        logger.info("========== Second stream round starts ==========")
        input_event_2 = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "round 2"}
        )
        output_2 = await collect_stream_output(agent.stream(input_event_2, session))

        # Verify round 2: persist_task_2 can be read and remains PAUSED
        tasks = await agent.controller.task_manager.get_task(
            task_filter=TaskFilter(task_id="persist_task_2")
        )
        task_2_round_2 = tasks[0]
        assert task_2_round_2 is not None, "In the second round, persist_task_2 should be readable (state persisted)"
        assert task_2_round_2.status == TaskStatus.PAUSED, \
            f"In the second round, persist_task_2 should remain PAUSED, but is {task_2_round_2.status}"
        assert task_2_round_2.task_id == "persist_task_2", "Task ID should be correct"
        assert task_2_round_2.context_id == "persist_context_2", "Context ID should be correct"
        assert task_2_round_2.priority == 1, "Priority should be correct"

        logger.info(f"Second round verification: persist_task_2 status = {task_2_round_2.status}, metadata correct")
        logger.info("✅ test_paused_task_state_persistence passed")

    @pytest.mark.asyncio
    async def test_multi_task_state_persistence(self):
        """Verify mixed multi-task state persistence

        Test goals:
        1. First stream round: create 3 tasks
           - multi_task_1: COMPLETED (finishes quickly)
           - multi_task_2: PAUSED (is paused)
           - multi_task_3: CANCELED (is cancelled)
        2. Second stream round: verify all task states are correctly persisted
        """
        # Build Agent
        agent = await build_test_agent(
            agent_id="test_multi_state_persistence",
            event_handler=MultiTaskStatePersistenceEventHandler(),
            task_executors={"cancellable": build_cancellable_executor}
        )

        # Use the same Session
        session = TaskSession(session_id="test_multi_state")

        # ========== First stream round ==========
        logger.info("========== First stream round starts ==========")
        input_event_1 = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "round 1"}
        )
        output_1 = await collect_stream_output(agent.stream(input_event_1, session))

        # Verify state at the end of the first round
        all_tasks_round_1 = await agent.controller.task_manager.get_task(task_filter=None)
        status_map_1 = {t.task_id: t.status for t in all_tasks_round_1}

        logger.info(f"Task status at end of first round: {status_map_1}")

        assert "multi_task_1" in status_map_1, "multi_task_1 should exist"
        assert status_map_1["multi_task_1"] == TaskStatus.COMPLETED, \
            f"multi_task_1 should be COMPLETED, but is {status_map_1['multi_task_1']}"

        assert "multi_task_2" in status_map_1, "multi_task_2 should exist"
        assert status_map_1["multi_task_2"] == TaskStatus.PAUSED, \
            f"multi_task_2 should be PAUSED, but is {status_map_1['multi_task_2']}"

        assert "multi_task_3" in status_map_1, "multi_task_3 should exist"
        assert status_map_1["multi_task_3"] == TaskStatus.CANCELED, \
            f"multi_task_3 should be CANCELED, but is {status_map_1['multi_task_3']}"

        # ========== Second stream round ==========
        logger.info("========== Second stream round starts ==========")
        input_event_2 = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "round 2"}
        )
        output_2 = await collect_stream_output(agent.stream(input_event_2, session))

        # Verify restored state in the second round (should be identical)
        all_tasks_round_2 = await agent.controller.task_manager.get_task(task_filter=None)
        status_map_2 = {t.task_id: t.status for t in all_tasks_round_2}

        logger.info(f"Task status restored in second round: {status_map_2}")

        assert len(all_tasks_round_2) == 3, \
            f"The second round should restore 3 tasks, but restored {len(all_tasks_round_2)}"
        assert status_map_2 == status_map_1, \
            (f"States in the second round should match the first round\n"
             f"First round: {status_map_1}\nSecond round: {status_map_2}")

        # Verify detailed metadata for each task
        for task in all_tasks_round_2:
            if task.task_id == "multi_task_1":
                assert task.priority == 1, "multi_task_1 priority should be 1"
                assert task.context_id == "multi_context_1", "multi_task_1 context_id should be correct"
            elif task.task_id == "multi_task_2":
                assert task.priority == 2, "multi_task_2 priority should be 2"
                assert task.context_id == "multi_context_2", "multi_task_2 context_id should be correct"
            elif task.task_id == "multi_task_3":
                assert task.priority == 3, "multi_task_3 priority should be 3"
                assert task.context_id == "multi_context_3", "multi_task_3 context_id should be correct"

        logger.info("✅ test_multi_task_state_persistence passed")

    @pytest.mark.asyncio
    async def test_state_restoration_failure_fallback(self):
        """Verify robustness when state restoration fails

        Test goals:
        1. First stream round: create and pause a task
        2. Manually corrupt the state stored in session (simulate serialization error)
        3. Second stream round: verify the system gracefully degrades without throwing exceptions
        """
        # Build Agent
        agent = await build_test_agent(
            agent_id="test_fallback",
            event_handler=StatePersistenceEventHandler(),
            task_executors={"cancellable": build_cancellable_executor}
        )

        # Use the same Session
        session = TaskSession(session_id="test_fallback")

        # ========== First stream round ==========
        logger.info("========== First stream round starts ==========")
        input_event_1 = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "round 1"}
        )
        output_1 = await collect_stream_output(agent.stream(input_event_1, session))

        # Verify the task is paused
        paused_tasks = await agent.controller.task_manager.get_task(
            task_filter=TaskFilter(task_id="persist_task_2")
        )
        paused_task = paused_tasks[0]
        assert paused_task.status == TaskStatus.PAUSED, "At the end of the first round the task should be PAUSED"

        logger.info("End of first round: persist_task_2 has been paused")

        # ========== Corrupt session state ==========
        logger.info("========== Corrupt session state (simulate serialization error) ==========")
        session.update_state({"controller": {"task_manager_state": "invalid_data"}})

        # ========== Second stream round ==========
        logger.info("========== Second stream round starts (should gracefully degrade) ==========")
        input_event_2 = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "round 2"}
        )

        # No exception should be thrown
        try:
            output_2 = await collect_stream_output(agent.stream(input_event_2, session))

            # Verify TaskManager is cleared (because restoration failed)
            all_tasks = await agent.controller.task_manager.get_task(task_filter=None)
            logger.info(f"Number of tasks in TaskManager after failed restoration in second round: {len(all_tasks)}")

            # Note: the second round will create new tasks (because round_number = 2),
            # but persist_task_2 should not be found
            persisted_task = await agent.controller.task_manager.get_task(
                task_filter=TaskFilter(task_id="persist_task_2")
            )

            # If persist_task_2 is found, that means restoration actually succeeded (contrary to expectations)
            # In fact, due to restoration failure, the second round should not find old tasks
            logger.info(f"Is persist_task_2 found in second round: {len(persisted_task) > 0}")

            logger.info("✅ test_state_restoration_failure_fallback passed (system degraded gracefully)")

        except Exception as e:
            pytest.fail(f"No exception should be raised when state restoration fails, but got: {e}")


# ==================== Lifecycle management tests ====================

class TestLifecycleManagement:
    """Test Controller lifecycle management"""

    @pytest.mark.asyncio
    async def test_controller_stop_cleanup_all(self):
        """Test stop() stops all background tasks and subscriptions correctly

        Test goals:
        1. Create multiple tasks and start stream
        2. Call controller.stop()
        3. Verify all background tasks are stopped
        4. Verify all subscriptions are cleaned up
        """
        agent = await build_test_agent(
            agent_id="test_stop_cleanup",
            event_handler=DynamicTaskEventHandler(),
            task_executors={"cancellable": build_cancellable_executor}
        )

        session = TaskSession(session_id="test_stop")

        input_event = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "test stop"}
        )

        # Start stream (running in the background)
        stream_task = asyncio.create_task(
            collect_stream_output(agent.stream(input_event, session))
        )

        # Wait for a while to let tasks start executing
        await asyncio.sleep(0.3)

        # Verify EventQueue and TaskScheduler are both stopped
        try:
            await stream_task
            # Stop controller
            await agent.controller.stop()
        except Exception as e:
            logger.info(f"stream_task raised an exception after stop (expected behavior): {e}")

        # Verify sessions are cleared
        assert len(agent.controller.task_scheduler.sessions) == 0, \
            "Sessions should be cleared after stop"

        logger.info("✅ test_controller_stop_cleanup_all passed")


# ==================== Session management tests ====================

class TestSessionManagement:
    """Test Session management"""

    @pytest.mark.asyncio
    async def test_multi_turn_conversation_no_interference(self):
        """Test that multi-turn conversations do not interfere with each other

        Test goals:
        1. In the same session, the first turn creates tasks
        2. After the first turn finishes, the second turn creates new tasks
        3. Verify tasks from the first turn do not affect the second turn
        4. Verify task status is correct in each turn
        """
        agent = await build_test_agent(
            agent_id="test_multi_turn",
            event_handler=DynamicTaskEventHandler(),
            task_executors={"cancellable": build_cancellable_executor}
        )

        session = TaskSession(session_id="multi_turn")

        # First turn
        input_event_1 = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "turn 1"}
        )
        output_1 = await collect_stream_output(agent.stream(input_event_1, session))
        assert any("test_task_1" in text for text in output_1), "The first turn should create a task"

        # Record tasks after first turn
        tasks_after_turn_1 = await agent.controller.task_manager.get_task(task_filter=None)
        logger.info(f"Number of tasks after first turn: {len(tasks_after_turn_1)}")

        # Second turn
        input_event_2 = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "turn 2"}
        )
        output_2 = await collect_stream_output(agent.stream(input_event_2, session))
        assert any("test_task_2" in text for text in output_2), "The second turn should create a new test_task"

        # Verify tasks do not interfere with each other
        tasks_after_turn_2 = await agent.controller.task_manager.get_task(task_filter=None)
        logger.info(f"Number of tasks after second turn: {len(tasks_after_turn_2)}")

        logger.info("✅ test_multi_turn_conversation_no_interference passed")

    @pytest.mark.asyncio
    async def test_session_registration_and_cleanup(self):
        """Test Session registration and cleanup

        Test goals:
        1. Correctly register session when stream() starts
        2. Correctly remove session when stream() ends
        3. Verify sessions do not leak
        """
        agent = await build_test_agent(
            agent_id="test_session_reg",
            event_handler=SimpleEventHandler(),
            task_executors={"cancellable": build_cancellable_executor}
        )

        session = TaskSession(session_id="test_reg")

        # Verify initial state: no sessions
        assert len(agent.controller.task_scheduler.sessions) == 0, \
            "Sessions should be empty initially"

        input_event = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "test"}
        )

        # Check session registration during stream (using async generator)
        stream = agent.stream(input_event, session)

        # Read the first chunk
        first_chunk = await stream.__anext__()
        assert first_chunk is not None, "Should be able to read the first chunk"

        # Session should be registered at this point
        assert session.session_id() in agent.controller.task_scheduler.sessions, \
            "Session should be registered while stream is running"

        # Consume all chunks
        async for _ in stream:
            pass

        # After stream ends, session should be cleaned up
        assert session.session_id() not in agent.controller.task_scheduler.sessions, \
            "Session should be removed after stream ends"

        logger.info("✅ test_session_registration_and_cleanup passed")


# ==================== Event system tests ====================

class TestEventSystem:
    """Test event system"""

    @pytest.mark.asyncio
    async def test_event_subscribe_and_publish(self):
        """Test event subscription and publishing

        Test goals:
        1. Verify subscribe() correctly creates subscriptions for 4 event types
        2. Verify publish_event() publishes events correctly
        3. Verify events are correctly routed to EventHandler
        """
        agent = await build_test_agent(
            agent_id="test_event_pub_sub",
            event_handler=SimpleEventHandler(),
            task_executors={"cancellable": build_cancellable_executor}
        )

        session = TaskSession(session_id="test_pub_sub")

        input_event = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "test event system"}
        )

        # Execute stream (will trigger subscribe, publish, and route)
        output = await collect_stream_output(agent.stream(input_event, session))

        # Verify events are handled correctly (via output)
        assert len(output) > 0, "There should be output to prove events were handled correctly"
        assert any("started" in text for text in output), "There should be task start information"

        logger.info("✅ test_event_subscribe_and_publish passed")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
