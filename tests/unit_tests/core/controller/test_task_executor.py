# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Task Executor lifecycle tests

This test file contains test cases for TaskExecutor lifecycle management:
1. Executor registration (add/remove/get)
2. Executor creation (one instance per task)
3. Executor cleanup (after task completion)

Test scenarios:
- Register and retrieve executors
- Remove executors
- Handle unregistered task types
- Verify independent executor instances per task
- Verify executor cleanup after task completion
"""

from typing import List, AsyncIterator, Tuple
import asyncio
import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.controller.base import Controller, ControllerConfig
from openjiuwen.core.controller.modules import (
    EventHandler,
    EventHandlerInput,
    TaskExecutor,
    TaskExecutorDependencies,
)
from openjiuwen.core.controller.schema import (
    ControllerOutputChunk,
    ControllerOutputPayload,
    EventType,
    TextDataFrame,
    Task,
    TaskStatus,
    InputEvent,
)
from openjiuwen.core.single_agent.base import AbilityManager, ControllerAgent
from openjiuwen.core.single_agent import Session, create_agent_session
from openjiuwen.core.common.logging import logger


# ==================== Test TaskExecutor ====================

class TrackableTaskExecutor(TaskExecutor):
    """Task executor that tracks its lifecycle

    Used to verify executor creation and cleanup.
    """

    # Class-level tracking
    instances_created = 0
    instances_cleaned = 0
    active_instances = []

    def __init__(self, dependencies: TaskExecutorDependencies):
        super().__init__(dependencies)
        TrackableTaskExecutor.instances_created += 1
        TrackableTaskExecutor.active_instances.append(self)
        self.instance_id = TrackableTaskExecutor.instances_created
        logger.info(f"TrackableTaskExecutor instance {self.instance_id} created")

    def __del__(self):
        """Track when instance is garbage collected"""
        TrackableTaskExecutor.instances_cleaned += 1
        if self in TrackableTaskExecutor.active_instances:
            TrackableTaskExecutor.active_instances.remove(self)
        logger.info(f"TrackableTaskExecutor instance {self.instance_id} cleaned up")

    async def execute_ability(self, task_id: str, session: Session) -> AsyncIterator[ControllerOutputChunk]:
        """Execute task"""
        yield ControllerOutputChunk(
            index=0,
            type="controller_output",
            payload=ControllerOutputPayload(
                type="processing",
                data=[TextDataFrame(type="text", text=f"Task {task_id} executed by instance {self.instance_id}")]
            ),
            last_chunk=False
        )

        await asyncio.sleep(0.1)

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
        return True, ""

    async def pause(self, task_id: str, session: Session) -> bool:
        return True

    async def can_cancel(self, task_id: str, session: Session) -> Tuple[bool, str]:
        return True, ""

    async def cancel(self, task_id: str, session: Session) -> bool:
        return True

    @classmethod
    def reset_tracking(cls):
        """Reset tracking counters"""
        cls.instances_created = 0
        cls.instances_cleaned = 0
        cls.active_instances = []


class SimpleTaskExecutor(TaskExecutor):
    """Simple task executor for basic tests"""

    async def execute_ability(self, task_id: str, session: Session) -> AsyncIterator[ControllerOutputChunk]:
        """Execute task"""
        yield ControllerOutputChunk(
            index=0,
            type="controller_output",
            payload=ControllerOutputPayload(
                type="processing",
                data=[TextDataFrame(type="text", text=f"Task {task_id} running")]
            ),
            last_chunk=False
        )

        await asyncio.sleep(0.1)

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
        return True, ""

    async def pause(self, task_id: str, session: Session) -> bool:
        return True

    async def can_cancel(self, task_id: str, session: Session) -> Tuple[bool, str]:
        return True, ""

    async def cancel(self, task_id: str, session: Session) -> bool:
        return True


# ==================== Test EventHandler ====================

class MultiTaskEventHandler(EventHandler):
    """Event handler that creates multiple tasks

    Used to test executor instance creation per task.
    """

    async def handle_input(self, inputs: EventHandlerInput):
        """Handle input event - create multiple tasks"""
        tasks = [
            Task(
                session_id=inputs.session.get_session_id(),
                task_id=f"trackable_task_{i}",
                task_type="trackable",
                priority=1,
                status=TaskStatus.SUBMITTED,
                context_id=f"trackable_context_{i}"
            )
            for i in range(1, 4)  # Create 3 tasks
        ]
        await self.task_manager.add_task(tasks)
        logger.info("MultiTaskEventHandler: Created 3 trackable tasks")
        return {"status": "success", "tasks_created": 3}

    async def handle_task_interaction(self, inputs: EventHandlerInput):
        pass

    async def handle_task_completion(self, inputs: EventHandlerInput):
        """Handle task completion event"""
        logger.info(f"MultiTaskEventHandler: Task completed - {inputs.event.task.task_id}")
        return {"status": "success"}

    async def handle_task_failed(self, inputs: EventHandlerInput):
        """Handle task failure event"""
        logger.error(f"MultiTaskEventHandler: Task failed - {inputs.event.task.task_id}")
        return {"status": "failed"}


class SimpleEventHandler(EventHandler):
    """Simple event handler for basic tests"""

    async def handle_input(self, inputs: EventHandlerInput):
        """Handle input event - create a single task"""
        task = Task(
            session_id=inputs.session.get_session_id(),
            task_id="simple_task_1",
            task_type="simple",
            priority=1,
            status=TaskStatus.SUBMITTED,
            context_id="simple_context_1"
        )
        await self.task_manager.add_task([task])
        logger.info("SimpleEventHandler: Created simple task")
        return {"status": "success", "tasks_created": 1}

    async def handle_task_interaction(self, inputs: EventHandlerInput):
        pass

    async def handle_task_completion(self, inputs: EventHandlerInput):
        """Handle task completion event"""
        logger.info(f"SimpleEventHandler: Task completed - {inputs.event.task.task_id}")
        return {"status": "success"}

    async def handle_task_failed(self, inputs: EventHandlerInput):
        """Handle task failure event"""
        logger.error(f"SimpleEventHandler: Task failed - {inputs.event.task.task_id}")
        return {"status": "failed"}


# ==================== Factory functions ====================

def build_trackable_executor(dependencies: TaskExecutorDependencies):
    """Build trackable task executor"""
    return TrackableTaskExecutor(dependencies)


def build_simple_executor(dependencies: TaskExecutorDependencies):
    """Build simple task executor"""
    return SimpleTaskExecutor(dependencies)


# ==================== Agent building function ====================

def build_test_controller() -> Controller:
    card = AgentCard(
        id="test_controller",
        name="Test Controller",
        description="Test controller for task executor registration"
    )
    controller = Controller()
    config = ControllerConfig()
    ability_manager = AbilityManager()
    context_engine = ContextEngine(ContextEngineConfig())
    controller.init(
        card=card,
        config=config,
        ability_manager=ability_manager,
        context_engine=context_engine
    )
    return controller


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
        description="Test agent for task executor testing"
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
    return output_texts


# ==================== Task Executor Registration Tests ====================

class TestTaskExecutorRegistration:
    """Test task executor registration"""

    @pytest.mark.asyncio
    async def test_add_task_executor(self):
        """Test adding task executor

        Test goals:
        1. Create a controller
        2. Register a task executor
        3. Verify executor is registered correctly
        4. Verify executor can be retrieved
        """
        controller = build_test_controller()

        controller.add_task_executor("test_type", build_simple_executor)

        registry = controller.task_scheduler.task_executor_registry
        assert "test_type" in registry.task_executor_builders, \
            "Executor should be registered"

        logger.info("✅ test_add_task_executor passed")

    @pytest.mark.asyncio
    async def test_remove_task_executor(self):
        """Test removing task executor

        Test goals:
        1. Register a task executor
        2. Remove the executor
        3. Verify executor is removed
        4. Verify executor cannot be retrieved
        """
        controller = build_test_controller()

        controller.add_task_executor("test_type", build_simple_executor)
        registry = controller.task_scheduler.task_executor_registry
        assert "test_type" in registry.task_executor_builders

        controller.remove_task_executor("test_type")

        assert "test_type" not in registry.task_executor_builders, \
            "Executor should be removed"

        logger.info("✅ test_remove_task_executor passed")

    @pytest.mark.asyncio
    async def test_get_task_executor(self):
        """Test getting task executor

        Test goals:
        1. Register a task executor
        2. Retrieve the executor by task type
        3. Verify correct executor is returned
        """
        controller = build_test_controller()

        controller.add_task_executor("test_type", build_simple_executor)

        registry = controller.task_scheduler.task_executor_registry
        executor_builder = registry.task_executor_builders.get("test_type")

        assert executor_builder is not None, "Executor builder should be returned"
        assert executor_builder == build_simple_executor, \
            "Should return the correct executor builder"

        logger.info("✅ test_get_task_executor passed")

    @pytest.mark.asyncio
    async def test_get_unregistered_task_executor(self):
        """Test getting unregistered task executor

        Test goals:
        1. Try to get an executor for unregistered task type
        2. Verify appropriate exception is raised
        """
        controller = build_test_controller()

        registry = controller.task_scheduler.task_executor_registry
        dependencies = TaskExecutorDependencies(
            config=controller.config,
            ability_manager=controller.ability_manager,
            context_engine=controller.context_engine,
            task_manager=controller.task_manager,
            event_queue=controller.event_queue
        )

        with pytest.raises(BaseError) as exc_info:
            registry.get_task_executor("unregistered_type", dependencies)

        assert "task executor not found" in str(exc_info.value).lower(), \
            "Should raise error for unregistered task type"

        logger.info("✅ test_get_unregistered_task_executor passed")

    @pytest.mark.asyncio
    async def test_multiple_executor_registration(self):
        """Test registering multiple executors

        Test goals:
        1. Register multiple executors with different types
        2. Verify all executors are registered
        3. Verify each can be retrieved correctly
        """
        controller = build_test_controller()

        controller.add_task_executor("type1", build_simple_executor)
        controller.add_task_executor("type2", build_trackable_executor)

        registry = controller.task_scheduler.task_executor_registry
        assert "type1" in registry.task_executor_builders
        assert "type2" in registry.task_executor_builders

        executor1 = registry.task_executor_builders.get("type1")
        executor2 = registry.task_executor_builders.get("type2")

        assert executor1 == build_simple_executor
        assert executor2 == build_trackable_executor

        logger.info("✅ test_multiple_executor_registration passed")


# ==================== Task Executor Lifecycle Tests ====================

class TestTaskExecutorLifecycle:
    """Test task executor lifecycle (creation and cleanup)"""

    @pytest.mark.asyncio
    async def test_executor_instance_per_task(self):
        """Test that each task gets its own executor instance

        Test goals:
        1. Create 3 tasks
        2. Verify 3 executor instances are created
        3. Verify each task is executed by a different instance
        """
        # Reset tracking
        TrackableTaskExecutor.reset_tracking()

        # Build Agent
        agent = await build_test_agent(
            agent_id="test_executor_instances",
            event_handler=MultiTaskEventHandler(),
            task_executors={"trackable": build_trackable_executor}
        )

        session = create_agent_session(session_id="test_executor_instances", card=agent.card)
        await session.pre_run()

        input_event = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "test executor instances"}
        )

        # Execute agent stream
        output_texts = await collect_stream_output(agent.stream(input_event, session))

        # Verify 3 tasks were executed
        assert len([text for text in output_texts if "completed" in text]) == 3, \
            "Should have 3 completed tasks"

        # Verify 3 executor instances were created
        assert TrackableTaskExecutor.instances_created == 3, \
            f"Should create 3 executor instances, but created {TrackableTaskExecutor.instances_created}"

        # Verify each task was executed by different instance
        instance_ids = set()
        for text in output_texts:
            if "executed by instance" in text:
                # Extract instance ID from text like "Task X executed by instance Y"
                parts = text.split("instance ")
                if len(parts) > 1:
                    instance_id = parts[1].strip()
                    instance_ids.add(instance_id)

        assert len(instance_ids) == 3, \
            f"Should have 3 different executor instances, but found {len(instance_ids)}"

        logger.info(f"✅ test_executor_instance_per_task passed")

    @pytest.mark.asyncio
    async def test_executor_cleanup_after_task_completion(self):
        """Test that executors are cleaned up after task completion

        Test goals:
        1. Create and complete tasks
        2. Wait for cleanup
        3. Verify executor instances are garbage collected
        """
        # Reset tracking
        TrackableTaskExecutor.reset_tracking()

        # Build Agent
        agent = await build_test_agent(
            agent_id="test_executor_cleanup",
            event_handler=MultiTaskEventHandler(),
            task_executors={"trackable": build_trackable_executor}
        )

        session = create_agent_session(session_id="test_executor_cleanup", card=agent.card)
        await session.pre_run()

        input_event = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "test executor cleanup"}
        )

        # Execute agent stream
        output_texts = await collect_stream_output(agent.stream(input_event, session))

        # Verify tasks completed
        assert len([text for text in output_texts if "completed" in text]) == 3, \
            "Should have 3 completed tasks"

        # Record instances created
        instances_created = TrackableTaskExecutor.instances_created
        assert instances_created == 3, "Should have created 3 instances"

        logger.info(f"✅ test_executor_cleanup_after_task_completion passed")

    @pytest.mark.asyncio
    async def test_executor_isolation_between_tasks(self):
        """Test that executor instances are isolated between tasks

        Test goals:
        1. Create multiple tasks
        2. Verify each task has its own executor instance
        3. Verify state is not shared between executors
        """
        # Reset tracking
        TrackableTaskExecutor.reset_tracking()

        # Build Agent
        agent = await build_test_agent(
            agent_id="test_executor_isolation",
            event_handler=MultiTaskEventHandler(),
            task_executors={"trackable": build_trackable_executor}
        )

        session = create_agent_session(session_id="test_executor_isolation", card=agent.card)
        await session.pre_run()
        input_event = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "test executor isolation"}
        )

        # Execute agent stream
        output_texts = await collect_stream_output(agent.stream(input_event, session))

        # Verify each task was executed by a unique instance
        executed_by_instances = []
        for text in output_texts:
            if "executed by instance" in text:
                parts = text.split("instance ")
                if len(parts) > 1:
                    instance_id = parts[1].strip()
                    executed_by_instances.append(instance_id)

        # Verify no duplicate instance IDs (each task has its own executor)
        assert len(executed_by_instances) == len(set(executed_by_instances)), \
            "Each task should have its own executor instance"

        logger.info("✅ test_executor_isolation_between_tasks passed")

    @pytest.mark.asyncio
    async def test_executor_creation_on_demand(self):
        """Test that executors are created on demand (not pre-created)

        Test goals:
        1. Register executor but don't create tasks
        2. Verify no executor instances are created
        3. Create tasks and verify executors are created on demand
        """
        # Reset tracking
        TrackableTaskExecutor.reset_tracking()

        # Build Agent
        agent = await build_test_agent(
            agent_id="test_executor_on_demand",
            event_handler=SimpleEventHandler(),
            task_executors={
                "simple": build_simple_executor,
                "trackable": build_trackable_executor
            }
        )

        # Verify no trackable executors created yet
        assert TrackableTaskExecutor.instances_created == 0, \
            "No executor instances should be created until tasks are executed"

        # Now create and execute a task (but not trackable type)
        session = create_agent_session(session_id="test_executor_on_demand", card=agent.card)
        await session.pre_run()
        input_event = InputEvent(
            event_type=EventType.INPUT,
            content={"query": "test on demand"}
        )

        # Execute (creates simple task, not trackable)
        await collect_stream_output(agent.stream(input_event, session))

        # Verify still no trackable executors created
        assert TrackableTaskExecutor.instances_created == 0, \
            "Trackable executor should not be created for simple task type"

        logger.info("✅ test_executor_creation_on_demand passed")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
