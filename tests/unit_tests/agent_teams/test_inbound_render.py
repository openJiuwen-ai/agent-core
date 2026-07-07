# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the pure inbound/event XML renderers in ``inbound_render``."""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.inbound_render import (
    INBOUND_TYPE_BROADCAST,
    INBOUND_TYPE_DIRECT,
    render_event,
    render_inbound,
)
from tests.test_logger import logger


@pytest.mark.level0
def test_inbound_type_tokens_are_stable_contract():
    assert INBOUND_TYPE_DIRECT == "direct"
    assert INBOUND_TYPE_BROADCAST == "broadcast"


@pytest.mark.level0
def test_render_inbound_carries_core_attributes_and_body():
    out = render_inbound(
        content="hello there",
        sender="dev1",
        message_id="m-42",
        msg_type=INBOUND_TYPE_DIRECT,
        time_info="2026-06-25 (just now)",
    )
    assert out.startswith("<team-inbound ")
    assert 'from="dev1"' in out
    assert 'message_id="m-42"' in out
    assert 'type="direct"' in out
    assert 'time="2026-06-25 (just now)"' in out
    # Body sits verbatim inside the element.
    assert "hello there" in out
    assert out.rstrip().endswith("</team-inbound>")
    logger.info("rendered inbound: %s", out)


@pytest.mark.level0
def test_render_inbound_escapes_body_and_attrs():
    out = render_inbound(
        content="a < b & c > d",
        sender='ev"il',
        message_id="m1",
        msg_type=INBOUND_TYPE_DIRECT,
        time_info="t",
    )
    # Body escaping (quotes left intact in body).
    assert "&lt;" in out
    assert "&gt;" in out
    assert "&amp;" in out
    assert "a < b & c > d" not in out
    # Attribute escaping (quotes escaped).
    assert "&quot;" in out
    assert 'from="ev"il"' not in out


@pytest.mark.level1
def test_render_inbound_for_controller_marks_hitt():
    out = render_inbound(
        content="x",
        sender="s",
        message_id="m",
        msg_type=INBOUND_TYPE_DIRECT,
        time_info="t",
        for_controller=True,
    )
    assert 'for="controller"' in out


@pytest.mark.level1
def test_render_inbound_note_rendered_only_when_both_present():
    base_kwargs = {
        "content": "x",
        "sender": "s",
        "message_id": "m",
        "msg_type": INBOUND_TYPE_DIRECT,
        "time_info": "t",
    }

    with_note = render_inbound(**base_kwargs, note_kind="reply-hint", note_text="please reply")
    assert '<team-note kind="reply-hint">' in with_note
    assert "please reply" in with_note

    # Missing either half suppresses the note entirely.
    assert "<team-note" not in render_inbound(**base_kwargs, note_kind="reply-hint")
    assert "<team-note" not in render_inbound(**base_kwargs, note_text="please reply")
    assert "<team-note" not in render_inbound(**base_kwargs)


@pytest.mark.level0
def test_render_event_carries_kind_and_body():
    out = render_event(kind="task-assigned", body="do the thing")
    assert out.startswith("<team-event ")
    assert 'kind="task-assigned"' in out
    assert "do the thing" in out
    assert out.rstrip().endswith("</team-event>")
    # No optional attributes by default.
    assert "task_id=" not in out
    assert "for=" not in out


@pytest.mark.level1
def test_render_event_optional_task_id_and_controller_and_note():
    out = render_event(
        kind="task-assigned",
        body="b",
        task_id="t-9",
        for_controller=True,
        note_kind="hitt-silence",
        note_text="stay silent",
    )
    assert 'task_id="t-9"' in out
    assert 'for="controller"' in out
    assert '<team-note kind="hitt-silence">' in out
    assert "stay silent" in out


@pytest.mark.level1
def test_render_event_escapes_body():
    out = render_event(kind="k", body="<x> & <y>")
    assert "&lt;" in out
    assert "&amp;" in out
    assert "<x> & <y>" not in out
