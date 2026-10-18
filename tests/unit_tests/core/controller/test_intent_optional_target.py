# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""CREATE_TASK Intent does not require a target task id."""

from openjiuwen.core.controller.schema.event import InputEvent, TextDataFrame
from openjiuwen.core.controller.schema.intent import Intent, IntentType


def test_create_task_intent_without_target_task_id() -> None:
    intent = Intent(
        intent_type=IntentType.CREATE_TASK,
        event=InputEvent(input_data=[TextDataFrame(text="make a plan")]),
        target_task_description="make a plan",
    )

    assert intent.target_task_id is None
