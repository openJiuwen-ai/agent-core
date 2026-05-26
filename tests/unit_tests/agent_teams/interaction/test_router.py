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
    resolve_targets,
)


def _exists_in(*known: str):
    """Build an async roster predicate matching only ``known`` names."""

    async def _predicate(name: str) -> bool:
        return name in known

    return _predicate


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


@pytest.mark.level0
def test_human_agent_no_space_before_at_splits_sender_and_recipient():
    """``$alice@dev-1 ping`` — ``@`` glued to ``$name`` still splits correctly."""
    payloads = parse_interact_str("$alice@dev-1 ping me")
    assert payloads == [
        HumanAgentMessage(body="ping me", sender="alice", target="dev-1"),
    ]


@pytest.mark.level0
def test_human_agent_at_in_sender_name_is_rejected():
    """``@`` must not be part of the sender name; ``$name@other`` splits at ``@``."""
    payloads = parse_interact_str("$player-6@player-3 汇报当前进展")
    assert payloads == [
        HumanAgentMessage(body="汇报当前进展", sender="player-6", target="player-3"),
    ]


@pytest.mark.level0
def test_human_agent_no_space_multi_recipient():
    """``$name@m1@m2 body`` — multiple ``@`` glued recipients."""
    payloads = parse_interact_str("$alice@m1 @m2 sync")
    assert payloads == [
        HumanAgentMessage(body="sync", sender="alice", target="m1"),
        HumanAgentMessage(body="sync", sender="alice", target="m2"),
    ]


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


# ----------------------------------------------------------------------
# resolve_targets — strict roster matching after parsing
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_resolve_targets_keeps_known_recipients():
    """All recipients known → input list returned unchanged."""
    payloads = parse_interact_str("# @m1 @m2 sync")
    resolved = await resolve_targets(payloads, member_exists=_exists_in("m1", "m2"))
    assert resolved == payloads


@pytest.mark.asyncio
@pytest.mark.level0
async def test_resolve_targets_folds_unknown_operator_to_god_view():
    """A single unknown recipient folds into a god-view message, text preserved."""
    payloads = parse_interact_str("# @ghost ship it")
    resolved = await resolve_targets(payloads, member_exists=_exists_in())
    assert resolved == [GodViewMessage(body="@ghost ship it")]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_resolve_targets_folds_unknown_human_agent_to_avatar():
    """Unknown recipient on the ``$`` channel folds to an avatar-drive message."""
    payloads = parse_interact_str("$alice @ghost hi")
    resolved = await resolve_targets(payloads, member_exists=_exists_in())
    assert resolved == [HumanAgentMessage(body="@ghost hi", sender="alice")]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_resolve_targets_partial_match_keeps_known_and_folds_unknown():
    """Known recipients stay point-to-point; unknown ones fold into one message."""
    payloads = parse_interact_str("# @m1 @ghost on it")
    resolved = await resolve_targets(payloads, member_exists=_exists_in("m1"))
    assert resolved == [
        OperatorMessage(body="on it", target="m1"),
        GodViewMessage(body="@ghost on it"),
    ]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_resolve_targets_multiple_unknown_rejoin_in_one_message():
    """Several unknown recipients rejoin as ``@a @b body`` in a single message."""
    payloads = parse_interact_str("# @g1 @g2 stand-up in 5")
    resolved = await resolve_targets(payloads, member_exists=_exists_in())
    assert resolved == [GodViewMessage(body="@g1 @g2 stand-up in 5")]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_resolve_targets_passes_through_broadcast_and_god_view():
    """Broadcast / god-view payloads carry no named target — never touched."""
    predicate = _exists_in()  # roster is empty, but these never hit it
    god = await resolve_targets(parse_interact_str("# plain"), member_exists=predicate)
    assert god == [GodViewMessage(body="plain")]
    bcast = await resolve_targets(parse_interact_str("# @all heads up"), member_exists=predicate)
    assert bcast == [OperatorMessage(body="heads up")]
