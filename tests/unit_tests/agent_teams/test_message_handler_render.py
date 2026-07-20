# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for ``MessageHandler._format_message`` inbound rendering.

``_format_message`` turns one mailbox row into the ``<team-inbound>`` XML an
agent reads. The method itself depends only on the message fields, the i18n
table, and the pure renderer (no ``self._infra`` / ``self._blueprint``), so
the handler is instantiated via ``object.__new__`` to avoid the full
coordination wiring. The HITT path is covered as a regression guard: a
human-agent avatar must be told to stay silent, and that constraint must keep
its load-bearing wording.
"""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.agent.coordination.handlers.message import MessageHandler
from openjiuwen.agent_teams.i18n import get_language, set_language
from openjiuwen.agent_teams.message_template import ExpandedMessage
from tests.test_logger import logger


class _FakeMessage:
    """Minimal mailbox row exposing only the fields ``_format_message`` reads."""

    def __init__(
        self,
        *,
        content: str,
        from_member_name: str,
        message_id: str,
        broadcast: bool = False,
        timestamp: int = 1_700_000_000_000,
    ) -> None:
        self.content = content
        self.from_member_name = from_member_name
        self.message_id = message_id
        self.broadcast = broadcast
        self.timestamp = timestamp


def _plain(msg: _FakeMessage) -> ExpandedMessage:
    """Delivery text of an ordinary (non-templated) message: its own content."""
    return ExpandedMessage(body=msg.content, is_template=False)


def _make_handler() -> MessageHandler:
    """Build a MessageHandler without the coordination dependency wiring.

    ``_format_message`` does not touch any instance attribute, so bypassing
    ``__init__`` keeps the test focused on the rendering contract.
    """
    return object.__new__(MessageHandler)


_NOW_MS = 1_700_000_100_000


@pytest.mark.level0
def test_format_message_direct_for_teammate():
    handler = _make_handler()
    msg = _FakeMessage(
        content="please review the PR",
        from_member_name="leader1",
        message_id="m-1",
    )
    out = handler._format_message(msg, expanded=_plain(msg), is_human_agent=False, now_ms=_NOW_MS)

    assert "<team-inbound" in out
    assert 'type="direct"' in out
    assert 'message_id="m-1"' in out
    assert "please review the PR" in out
    assert '<team-note kind="reply-hint"' in out
    # A normal teammate is never framed as a controller notification.
    assert 'for="controller"' not in out
    logger.info("direct teammate inbound: %s", out)


@pytest.mark.level1
def test_format_message_broadcast_type():
    handler = _make_handler()
    msg = _FakeMessage(
        content="standup in 5",
        from_member_name="leader1",
        message_id="m-2",
        broadcast=True,
    )
    out = handler._format_message(msg, expanded=_plain(msg), is_human_agent=False, now_ms=_NOW_MS)
    assert 'type="broadcast"' in out
    assert "standup in 5" in out


@pytest.mark.level0
def test_format_message_template_message_drops_reply_hint():
    """A framework instruction is answered with a tool call, not a reply (F_63).

    The row carries no body of its own — the document was rendered at delivery
    from the template — and the envelope must still be the ordinary
    ``<team-inbound>`` one, so a member reads framework and human messages the
    same way.
    """
    handler = _make_handler()
    msg = _FakeMessage(content="", from_member_name="team_leader", message_id="m-4")
    expanded = ExpandedMessage(body="[Task Started] Task [a] ...", is_template=True)

    out = handler._format_message(msg, expanded=expanded, is_human_agent=False, now_ms=_NOW_MS)

    assert "<team-inbound" in out
    assert 'from="team_leader"' in out
    assert "[Task Started] Task [a] ..." in out
    assert "reply-hint" not in out
    logger.info("template inbound: %s", out)


@pytest.mark.level0
def test_format_message_hitt_silence_regression():
    """HITT regression: a human-agent avatar must be told to stay silent.

    Guards the load-bearing constraint that keeps a human-agent avatar from
    autonomously acting on inbound messages — the message is framed for its
    controller and carries the silence note. Weakening this wording would let
    the avatar speak/act on its own, so the cn keywords are asserted directly.
    """
    handler = _make_handler()
    msg = _FakeMessage(
        content="ping the avatar",
        from_member_name="dev1",
        message_id="m-3",
    )
    # The i18n language is process-global; pin cn so the assertion is
    # deterministic regardless of test ordering, then restore it.
    previous = get_language()
    set_language("cn")
    try:
        out = handler._format_message(msg, expanded=_plain(msg), is_human_agent=True, now_ms=_NOW_MS)
    finally:
        set_language(previous)

    assert 'for="controller"' in out
    assert '<team-note kind="hitt-silence"' in out
    # Load-bearing wording: the avatar must be strictly forbidden to act and
    # must stay silent.
    assert "严格禁止" in out
    assert "保持静默" in out
    # The original message body is still surfaced inside the inbound element.
    assert "ping the avatar" in out
    logger.info("hitt silence inbound: %s", out)
