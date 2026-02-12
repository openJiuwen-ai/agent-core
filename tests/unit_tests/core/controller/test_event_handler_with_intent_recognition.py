# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for EventHandlerWithIntentRecognition

Tests all core functionality of the EventHandlerWithIntentRecognition class including:
- Intent recognition and routing
- Task creation, pause, resume, continue, supplement, cancel, modify intents
- Task interaction, completion, and failure event handling
- Unknown intent handling
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, Mock

from openjiuwen.core.controller import JsonDataFrame
from openjiuwen.core.controller.modules.intent_recognizer import EventHandlerWithIntentRecognition
from openjiuwen.core.controller.modules.event_handler import EventHandlerInput
from openjiuwen.core.controller.schema import (
    Intent, IntentType, Task, TaskStatus, 
    InputEvent, TaskInteractionEvent, TaskCompletionEvent, TaskFailedEvent,
    TextDataFrame
)
from openjiuwen.core.controller.config import ControllerConfig
from openjiuwen.core.single_agent import Session


class TestEventHandlerWithIntentRecognition(unittest.IsolatedAsyncioTestCase):
    """Test suite for EventHandlerWithIntentRecognition class"""

    def setUp(self):
        """Set up test fixtures before each test method"""
        # Create mock dependencies
        self.mock_config = MagicMock(spec=ControllerConfig)
        self.mock_config.intent_llm_id = "test_llm_id"
        self.mock_config.intent_confidence_threshold = 0.8
        
        self.mock_task_manager = AsyncMock()
        self.mock_task_scheduler = AsyncMock()
        self.mock_context_engine = AsyncMock()
        self.mock_ability_manager = MagicMock()
        self.mock_session = AsyncMock(spec=Session)
        self.mock_session.get_session_id.return_value = "test_session_id"
        
        # Create handler instance
        self.handler = EventHandlerWithIntentRecognition()
        
        # Inject dependencies
        self.handler.config = self.mock_config
        self.handler.task_manager = self.mock_task_manager
        self.handler.task_scheduler = self.mock_task_scheduler
        self.handler.context_engine = self.mock_context_engine
        self.handler.ability_manager = self.mock_ability_manager
        
        # Mock the recognizer entirely to avoid initialization issues
        self.handler.recognizer = AsyncMock()
        
        # Create sample event
        self.sample_input_event = InputEvent(
            input_data=[TextDataFrame(text="Create a new task")]
        )
        
        # Create sample intents
        self.sample_create_intent = Intent(
            intent_type=IntentType.CREATE_TASK,
            event=self.sample_input_event,
            target_task_id="task1",
            target_task_description="Test task description"
        )
        
        self.sample_pause_intent = Intent(
            intent_type=IntentType.PAUSE_TASK,
            event=self.sample_input_event,
            target_task_id="task1"
        )
        
        self.sample_resume_intent = Intent(
            intent_type=IntentType.RESUME_TASK,
            event=self.sample_input_event,
            target_task_id="task1"
        )
        
        self.sample_continue_intent = Intent(
            intent_type=IntentType.CONTINUE_TASK,
            event=self.sample_input_event,
            target_task_id="task2",
            target_task_description="Continue task description",
            depend_task_id=["task1"]
        )
        
        self.sample_supplement_intent = Intent(
            intent_type=IntentType.SUPPLEMENT_TASK,
            event=self.sample_input_event,
            target_task_id="task1",
            supplementary_info="Mock supplementary_info."
        )
        
        self.sample_cancel_intent = Intent(
            intent_type=IntentType.CANCEL_TASK,
            event=self.sample_input_event,
            target_task_id="task1"
        )
        
        self.sample_modify_intent = Intent(
            intent_type=IntentType.MODIFY_TASK,
            event=self.sample_input_event,
            target_task_id="task1",
            target_task_description="Modified task description",
            modification_details="Mock_modification_details"
        )
        
        self.sample_unknown_intent = Intent(
            target_task_id="",
            intent_type=IntentType.UNKNOWN_TASK,
            event=self.sample_input_event,
            clarification_prompt="Could you please clarify?"
        )

    # ==================== Handle Input Tests ====================
    async def test_handle_input_create_task(self):
        """Test handling input with CREATE_TASK intent"""
        # Mock recognizer to return create intent
        self.handler.recognizer.recognize = AsyncMock(return_value=[self.sample_create_intent])
        
        inputs = EventHandlerInput(event=self.sample_input_event, session=self.mock_session)
        await self.handler.handle_input(inputs)
        
        # Verify task was created
        self.mock_task_manager.add_task.assert_called_once()
        call_args = self.mock_task_manager.add_task.call_args[0][0]
        self.assertEqual(call_args.task_id, "task1")
        self.assertEqual(call_args.description, "Test task description")
        self.assertEqual(call_args.status, TaskStatus.SUBMITTED)

    async def test_handle_input_pause_task(self):
        """Test handling input with PAUSE_TASK intent"""
        self.handler.recognizer.recognize = AsyncMock(return_value=[self.sample_pause_intent])
        
        inputs = EventHandlerInput(event=self.sample_input_event, session=self.mock_session)
        await self.handler.handle_input(inputs)
        
        # Verify pause_task was called
        self.mock_task_scheduler.pause_task.assert_called_once_with("task1")

    async def test_handle_input_resume_task(self):
        """Test handling input with RESUME_TASK intent"""
        # Mock task manager to return a paused task
        paused_task = Task(
            session_id="test_session_id",
            task_id="task1",
            task_type="test_task",
            description="Test task",
            priority=1,
            status=TaskStatus.PAUSED
        )
        self.mock_task_manager.get_task = AsyncMock(return_value=[paused_task])
        
        self.handler.recognizer.recognize = AsyncMock(return_value=[self.sample_resume_intent])
        
        inputs = EventHandlerInput(event=self.sample_input_event, session=self.mock_session)
        await self.handler.handle_input(inputs)
        
        # Verify task status was updated to SUBMITTED
        self.mock_task_manager.update_task.assert_called_once()
        updated_task = self.mock_task_manager.update_task.call_args[0][0]
        self.assertEqual(updated_task.status, TaskStatus.SUBMITTED)

    async def test_handle_input_resume_task_not_paused(self):
        """Test resuming a task that is not paused"""
        # Mock task manager to return a working task
        working_task = Task(
            session_id="test_session_id",
            task_id="task1",
            task_type="test_task",
            description="Test task",
            priority=1,
            status=TaskStatus.WORKING
        )
        self.mock_task_manager.get_task = AsyncMock(return_value=[working_task])
        
        self.handler.recognizer.recognize = AsyncMock(return_value=[self.sample_resume_intent])
        
        inputs = EventHandlerInput(event=self.sample_input_event, session=self.mock_session)
        await self.handler.handle_input(inputs)
        
        # Task should not be updated if not paused
        self.mock_task_manager.update_task.assert_not_called()

    async def test_handle_input_continue_task(self):
        """Test handling input with CONTINUE_TASK intent"""
        # Mock task manager to return dependent tasks
        dependent_task = Task(
            session_id="test_session_id",
            task_id="task1",
            task_type="test_task",
            description="Dependent task",
            priority=1,
            status=TaskStatus.COMPLETED,
            context_id="test_session_id_task1",
            inputs=[self.sample_input_event]
        )
        self.mock_task_manager.get_task = AsyncMock(return_value=[dependent_task])
        
        # Mock context engine
        mock_context = MagicMock()
        mock_context.get_messages.return_value = []
        self.mock_context_engine.get_context.return_value = mock_context
        
        self.handler.recognizer.recognize = AsyncMock(return_value=[self.sample_continue_intent])
        
        inputs = EventHandlerInput(event=self.sample_input_event, session=self.mock_session)
        await self.handler.handle_input(inputs)
        
        # Verify new task was created with previous events
        self.mock_task_manager.add_task.assert_called_once()
        call_args = self.mock_task_manager.add_task.call_args[0][0]
        self.assertEqual(call_args.task_id, "task2")
        self.assertIn(self.sample_input_event, call_args.inputs)

    async def test_handle_input_supplement_task(self):
        """Test handling input with SUPPLEMENT_TASK intent"""
        # Mock task manager to return a task
        existing_task = Task(
            session_id="test_session_id",
            task_id="task1",
            task_type="test_task",
            description="Original task",
            priority=1,
            status=TaskStatus.WORKING
        )
        self.mock_task_manager.get_task = AsyncMock(return_value=[existing_task])
        
        self.handler.recognizer.recognize = AsyncMock(return_value=[self.sample_supplement_intent])
        
        inputs = EventHandlerInput(event=self.sample_input_event, session=self.mock_session)
        await self.handler.handle_input(inputs)
        
        # Verify task was paused and updated
        self.mock_task_scheduler.pause_task.assert_called_once_with("task1")
        self.mock_task_manager.update_task.assert_called_once()
        updated_task = self.mock_task_manager.update_task.call_args[0][0]
        self.assertIn("任务补充信息", updated_task.description)
        self.assertEqual(updated_task.status, TaskStatus.SUBMITTED)

    async def test_handle_input_cancel_task(self):
        """Test handling input with CANCEL_TASK intent"""
        self.handler.recognizer.recognize = AsyncMock(return_value=[self.sample_cancel_intent])
        
        inputs = EventHandlerInput(event=self.sample_input_event, session=self.mock_session)
        await self.handler.handle_input(inputs)
        
        # Verify cancel_task was called
        self.mock_task_scheduler.cancel_task.assert_called_once_with("task1")

    async def test_handle_input_modify_task(self):
        """Test handling input with MODIFY_TASK intent"""
        # Mock task manager to return a task
        existing_task = Task(
            session_id="test_session_id",
            task_id="task1",
            task_type="test_task",
            description="Original task",
            priority=1,
            status=TaskStatus.WORKING,
            inputs=None
        )
        self.mock_task_manager.get_task = AsyncMock(return_value=[existing_task])
        
        self.handler.recognizer.recognize = AsyncMock(return_value=[self.sample_modify_intent])
        
        inputs = EventHandlerInput(event=self.sample_input_event, session=self.mock_session)
        await self.handler.handle_input(inputs)
        
        # Verify task was cancelled and updated
        self.mock_task_scheduler.cancel_task.assert_called_once_with("task1")
        self.mock_task_manager.update_task.assert_called_once()
        updated_task = self.mock_task_manager.update_task.call_args[0][0]
        self.assertEqual(updated_task.description, "Modified task description")
        self.assertEqual(updated_task.status, TaskStatus.SUBMITTED)

    async def test_handle_input_unknown_task(self):
        """Test handling input with UNKNOWN_TASK intent"""
        self.handler.recognizer.recognize = AsyncMock(return_value=[self.sample_unknown_intent])
        
        inputs = EventHandlerInput(event=self.sample_input_event, session=self.mock_session)
        await self.handler.handle_input(inputs)
        
        # Verify clarification prompt was written to stream
        self.mock_session.write_stream.assert_called_once()
        call_args = self.mock_session.write_stream.call_args[0][0]
        self.assertEqual(call_args["clarification_prompt"], "Could you please clarify?")

    async def test_handle_input_multiple_intents(self):
        """Test handling input with multiple intents"""
        intents = [self.sample_create_intent, self.sample_pause_intent]
        self.handler.recognizer.recognize = AsyncMock(return_value=intents)
        
        inputs = EventHandlerInput(event=self.sample_input_event, session=self.mock_session)
        await self.handler.handle_input(inputs)
        
        # Verify both intents were processed
        self.mock_task_manager.add_task.assert_called_once()
        self.mock_task_scheduler.pause_task.assert_called_once()

    # ==================== Handle Task Interaction Tests ====================
    async def test_handle_task_interaction(self):
        """Test handling task interaction event"""
        interaction_event = TaskInteractionEvent(
            interaction=[
                JsonDataFrame(data={"type": "input_required", "message": "Please provide input"})
            ]
        )
        
        inputs = EventHandlerInput(event=interaction_event, session=self.mock_session)
        await self.handler.handle_task_interaction(inputs)
        
        # Verify interaction was written to stream
        self.mock_session.write_stream.assert_called_once()
        call_args = self.mock_session.write_stream.call_args[0][0]
        self.assertEqual(call_args["interaction"], interaction_event.interaction)

    async def test_handle_task_interaction_wrong_event_type(self):
        """Test handling task interaction with wrong event type"""
        inputs = EventHandlerInput(event=self.sample_input_event, session=self.mock_session)
        
        with self.assertRaises(Exception):
            await self.handler.handle_task_interaction(inputs)

    # ==================== Handle Task Completion Tests ====================
    async def test_handle_task_completion(self):
        """Test handling task completion event"""
        completion_event = TaskCompletionEvent(
            task_result=[
                JsonDataFrame(data={"status": "completed", "output": "Task completed successfully"})
            ]
        )
        
        inputs = EventHandlerInput(event=completion_event, session=self.mock_session)
        await self.handler.handle_task_completion(inputs)
        
        # Verify result was written to stream
        self.mock_session.write_stream.assert_called_once()
        call_args = self.mock_session.write_stream.call_args[0][0]
        self.assertEqual(call_args["result"], completion_event.task_result)

    async def test_handle_task_completion_wrong_event_type(self):
        """Test handling task completion with wrong event type"""
        inputs = EventHandlerInput(event=self.sample_input_event, session=self.mock_session)
        
        with self.assertRaises(Exception):
            await self.handler.handle_task_completion(inputs)

    # ==================== Handle Task Failed Tests ====================
    async def test_handle_task_failed(self):
        """Test handling task failed event"""
        failed_event = TaskFailedEvent(
            error_message="Task execution failed"
        )
        
        inputs = EventHandlerInput(event=failed_event, session=self.mock_session)
        await self.handler.handle_task_failed(inputs)
        
        # Verify error message was written to stream
        self.mock_session.write_stream.assert_called_once()
        call_args = self.mock_session.write_stream.call_args[0][0]
        self.assertEqual(call_args["error_message"], "Task execution failed")

    async def test_handle_task_failed_wrong_event_type(self):
        """Test handling task failed with wrong event type"""
        inputs = EventHandlerInput(event=self.sample_input_event, session=self.mock_session)
        
        with self.assertRaises(Exception):
            await self.handler.handle_task_failed(inputs)
