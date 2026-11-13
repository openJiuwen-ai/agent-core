# coding: utf-8
"""Event metadata is normalized to a dict on construction."""

from openjiuwen.core.controller.schema.event import InputEvent, TaskInteractionEvent


def test_input_event_has_empty_metadata_dict():
    event = InputEvent.from_user_input("hello")

    assert event.metadata == {}
    assert event.model_dump(mode="json")["metadata"] == {}


def test_task_interaction_event_has_empty_metadata_dict():
    event = TaskInteractionEvent()

    assert event.metadata == {}
