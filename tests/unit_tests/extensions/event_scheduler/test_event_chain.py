# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for EventChainHandler."""

from __future__ import annotations

import uuid
import pytest

from openjiuwen.core.controller.schema.task import Task, TaskStatus
from openjiuwen.extensions.event_scheduler.schema import EventChainRule, EventSchedulerConfig


@pytest.mark.asyncio
class TestEventChainHandler:
    """Tests for EventChainHandler task chaining logic."""

    async def test_handle_completion_creates_chained_task(
            self, event_chain_handler, mock_task_manager
    ):
        """Test that task completion triggers chain rule and creates downstream task."""
        completed_task = Task(
            session_id="session-1",
            task_id=str(uuid.uuid4()),
            task_type="data_extraction",
            status=TaskStatus.COMPLETED,
            metadata={"format": "csv"},
        )

        created_ids = await event_chain_handler.handle_task_completion(completed_task)

        assert len(created_ids) == 1
        mock_task_manager.add_task.assert_called_once()

        # Verify the created task has correct type
        created_task = mock_task_manager.add_task.call_args[0][0]
        assert created_task.task_type == "data_validation"
        assert created_task.status == TaskStatus.SUBMITTED
        assert created_task.parent_task_id == completed_task.task_id

    async def test_handle_completion_no_matching_rules(
            self, event_chain_handler, mock_task_manager
    ):
        """Test that no tasks are created when no rules match."""
        completed_task = Task(
            session_id="session-1",
            task_id=str(uuid.uuid4()),
            task_type="unknown_type",
            status=TaskStatus.COMPLETED,
        )

        created_ids = await event_chain_handler.handle_task_completion(completed_task)

        assert len(created_ids) == 0
        mock_task_manager.add_task.assert_not_called()

    async def test_handle_completion_condition_not_met(
            self, event_chain_handler, mock_task_manager
    ):
        """Test that chain rule with unmet condition does not trigger."""
        completed_task = Task(
            session_id="session-1",
            task_id=str(uuid.uuid4()),
            task_type="data_validation",
            status=TaskStatus.COMPLETED,
            metadata={"format": "json"},  # Rule expects format=csv
        )

        created_ids = await event_chain_handler.handle_task_completion(completed_task)

        assert len(created_ids) == 0
        mock_task_manager.add_task.assert_not_called()

    async def test_handle_completion_condition_met(
            self, event_chain_handler, mock_task_manager
    ):
        """Test that chain rule with met condition triggers correctly."""
        completed_task = Task(
            session_id="session-1",
            task_id=str(uuid.uuid4()),
            task_type="data_validation",
            status=TaskStatus.COMPLETED,
            metadata={"format": "csv"},
        )

        created_ids = await event_chain_handler.handle_task_completion(completed_task)

        assert len(created_ids) == 1
        created_task = mock_task_manager.add_task.call_args[0][0]
        assert created_task.task_type == "data_load"

    async def test_chaining_disabled(
            self, mock_task_manager
    ):
        """Test that chaining does nothing when disabled."""
        from openjiuwen.extensions.event_scheduler.core.event_chain import EventChainHandler

        config = EventSchedulerConfig(
            enable_event_chaining=False,
            chain_rules=[
                EventChainRule(
                    rule_id="rule-1",
                    source_task_type="a",
                    target_task_type="b",
                )
            ],
        )
        handler = EventChainHandler(config=config, task_manager=mock_task_manager)

        completed_task = Task(
            session_id="session-1",
            task_id=str(uuid.uuid4()),
            task_type="a",
            status=TaskStatus.COMPLETED,
        )

        created_ids = await handler.handle_task_completion(completed_task)
        assert len(created_ids) == 0

    async def test_add_rule_dynamically(
            self, event_chain_handler, mock_task_manager
    ):
        """Test adding a chain rule at runtime."""
        new_rule = EventChainRule(
            rule_id="rule-new",
            source_task_type="custom_type",
            target_task_type="custom_downstream",
        )
        event_chain_handler.add_rule(new_rule)

        completed_task = Task(
            session_id="session-1",
            task_id=str(uuid.uuid4()),
            task_type="custom_type",
            status=TaskStatus.COMPLETED,
        )

        created_ids = await event_chain_handler.handle_task_completion(completed_task)
        assert len(created_ids) == 1

    async def test_remove_rule(self, event_chain_handler):
        """Test removing a chain rule by ID."""
        assert event_chain_handler.remove_rule("rule-1") is True
        assert event_chain_handler.remove_rule("nonexistent") is False
