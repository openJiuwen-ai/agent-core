# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""CREATE_TASK Intent does not require a target task id."""

import pytest

from openjiuwen.core.controller.config import ControllerConfig
from openjiuwen.core.controller.modules.intent_recognizer import EventHandlerWithIntentRecognition
from openjiuwen.core.controller.modules.task_manager import TaskManager
from openjiuwen.core.controller.schema.event import InputEvent, TextDataFrame
from openjiuwen.core.controller.schema.intent import Intent, IntentType
from openjiuwen.core.session.agent import create_agent_session


def test_create_task_intent_without_target_task_id() -> None:
    intent = Intent(
        intent_type=IntentType.CREATE_TASK,
        event=InputEvent(input_data=[TextDataFrame(text="make a plan")]),
        target_task_description="make a plan",
    )

    assert intent.target_task_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("task_id", [None, "caller-supplied-id"])
async def test_create_task_without_id_reaches_task_manager(task_id: str | None) -> None:
    handler = EventHandlerWithIntentRecognition()
    handler.task_manager = TaskManager(ControllerConfig())
    session = create_agent_session(session_id="create-id")
    intent = Intent(
        intent_type=IntentType.CREATE_TASK,
        event=InputEvent(input_data=[TextDataFrame(text="make a plan")]),
        target_task_id=task_id,
        target_task_description="make a plan",
    )
    await handler._process_create_task_intent(intent, session)
    await handler._process_create_task_intent(intent.model_copy(update={"target_task_id": None}), session)
    tasks = await handler.task_manager.get_task()
    assert len(tasks) == 2
    for task in tasks:
        assert task.task_id
        assert task.context_id == f"create-id_{task.task_id}"
        assert task.session_id == "create-id"
    if task_id:
        assert task_id in {task.task_id for task in tasks}
