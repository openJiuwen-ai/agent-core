# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure parser tests for ``interaction/router.py``.

Covers the grammar :func:`parse_interact_str` recognises:

::

    input := channel? recipients? body
    channel := "# " | "$<name> "        # default "# " when omitted
    recipients := ("@<name> ")*         # zero or more
    body := <remaining text>

The parser is sync and does not validate names; it only maps syntax
to typed payloads. ``deliver_direct`` (which actually writes to the
bus) is exercised through ``test_human_agent_inbox.py``.
"""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.interaction.payload import (
    GodViewMessage,
    HumanAgentMessage,
    OperatorMessage,
)
from openjiuwen.agent_teams.interaction.router import (
    parse_interact_str,
    parse_mention,
)


# ----------------------------------------------------------------------
# god-view channel (#)
# ----------------------------------------------------------------------


@pytest.mark.level0
def test_god_view_no_recipients_emits_god_view():
    payloads = parse_interact_str("# what is the plan?")
    assert payloads == [GodViewMessage(body="what is the plan?")]


@pytest.mark.level0
def test_no_prefix_defaults_to_god_view():
    payloads = parse_interact_str("just a plain question")
    assert payloads == [GodViewMessage(body="just a plain question")]


@pytest.mark.level0
def test_god_view_single_recipient_emits_operator_direct():
    payloads = parse_interact_str("# @dev-1 ship the patch")
    assert payloads == [OperatorMessage(body="ship the patch", target="dev-1")]


@pytest.mark.level0
def test_default_channel_with_mention_still_god_view_sender():
    """Bare ``@m1 body`` (no #) takes the default god-view channel."""
    payloads = parse_interact_str("@dev-1 ship the patch")
    assert payloads == [OperatorMessage(body="ship the patch", target="dev-1")]


@pytest.mark.level0
def test_god_view_multi_recipient_fans_out():
    payloads = parse_interact_str("# @m1 @m2 @m3 stand-up in 5")
    assert payloads == [
        OperatorMessage(body="stand-up in 5", target="m1"),
        OperatorMessage(body="stand-up in 5", target="m2"),
        OperatorMessage(body="stand-up in 5", target="m3"),
    ]


@pytest.mark.level0
def test_god_view_at_all_collapses_to_broadcast():
    payloads = parse_interact_str("# @all heads up")
    assert payloads == [OperatorMessage(body="heads up")]


@pytest.mark.level0
def test_god_view_at_star_also_broadcasts():
    payloads = parse_interact_str("# @* heads up")
    assert payloads == [OperatorMessage(body="heads up")]


@pytest.mark.level0
def test_broadcast_token_supersedes_other_recipients():
    """``@all`` already covers everyone — extra named recipients are redundant."""
    payloads = parse_interact_str("# @m1 @all wide announce")
    assert payloads == [OperatorMessage(body="wide announce")]


# ----------------------------------------------------------------------
# human-agent channel ($<name>)
# ----------------------------------------------------------------------


@pytest.mark.level0
def test_human_agent_no_recipients_drives_avatar():
    payloads = parse_interact_str("$alice please summarise design.md")
    assert payloads == [HumanAgentMessage(body="please summarise design.md", sender="alice")]


@pytest.mark.level0
def test_human_agent_single_recipient_emits_direct():
    payloads = parse_interact_str("$alice @dev-1 ping me when done")
    assert payloads == [
        HumanAgentMessage(body="ping me when done", sender="alice", target="dev-1"),
    ]


@pytest.mark.level0
def test_human_agent_multi_recipient_fans_out():
    payloads = parse_interact_str("$alice @m1 @m2 status sync")
    assert payloads == [
        HumanAgentMessage(body="status sync", sender="alice", target="m1"),
        HumanAgentMessage(body="status sync", sender="alice", target="m2"),
    ]


@pytest.mark.level0
def test_human_agent_at_all_emits_broadcast_marker():
    """Avatar broadcasts use the ``"*"`` sentinel target."""
    payloads = parse_interact_str("$alice @all heads up")
    assert payloads == [HumanAgentMessage(body="heads up", sender="alice", target="*")]


# ----------------------------------------------------------------------
# Robustness / falls back to god-view default
# ----------------------------------------------------------------------


@pytest.mark.level0
def test_empty_input_returns_empty_list():
    assert parse_interact_str("") == []
    assert parse_interact_str("   \t\n  ") == []


@pytest.mark.level0
def test_hash_without_space_is_content():
    """``#hashtag`` has no separating space, so it is body, not a channel marker."""
    payloads = parse_interact_str("#hashtag is just text")
    assert payloads == [GodViewMessage(body="#hashtag is just text")]


@pytest.mark.level0
def test_dollar_without_body_falls_back_to_god_view():
    """``$alice`` with no following body keeps the literal text on god-view."""
    payloads = parse_interact_str("$alice")
    assert payloads == [GodViewMessage(body="$alice")]


@pytest.mark.level0
def test_at_without_body_keeps_token_in_body():
    """``@m1`` alone (no trailing space + body) is not a recipient token."""
    payloads = parse_interact_str("@dev-1")
    assert payloads == [GodViewMessage(body="@dev-1")]


@pytest.mark.level0
def test_inline_at_in_body_is_not_a_recipient():
    """``@`` only routes when it leads (after channel + earlier @ tokens)."""
    payloads = parse_interact_str("# hello @world this is content")
    assert payloads == [GodViewMessage(body="hello @world this is content")]


@pytest.mark.level0
def test_god_view_with_only_recipients_has_empty_body():
    """``# @m1 `` (trailing space, nothing after) → recipient with empty body."""
    payloads = parse_interact_str("# @dev-1 ")
    assert payloads == [OperatorMessage(body="", target="dev-1")]


@pytest.mark.level1
def test_parse_mention_primitive_still_works():
    """Sanity: the underlying ``@`` parser is unchanged."""
    assert parse_mention("@dev-1 hi") == ("dev-1", "hi")
    assert parse_mention("hello world") is None
