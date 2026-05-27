# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for bridge mailbox wrap / compose pure functions.

Covers:
- ``wrap_outbound_to_remote`` — PASSTHROUGH vs REPHRASE format for cn/en;
  task_hint / broadcast handling.
- ``compose_bridge_inbound`` — original body + remote reply assembly
  with explicit scheduling instructions baked in.
- ``build_bridge_persona`` / ``build_team_overview`` — adapter.connect
  briefing strings for cn/en.
"""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.agent.bridge_inbound_compose import compose_bridge_inbound
from openjiuwen.agent_teams.agent.bridge_outbound_wrap import wrap_outbound_to_remote
from openjiuwen.agent_teams.prompts.bridge_remote_brief import (
    MemberSummary,
    build_bridge_persona,
    build_team_overview,
)
from openjiuwen.agent_teams.schema.team import (
    BridgeMailboxInjectMode,
    TeamRole,
)

# ---------------------------------------------------------------------------
# wrap_outbound_to_remote
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_passthrough_minimal_header_cn():
    text = wrap_outbound_to_remote(
        sender="alice",
        sender_display_name="Alice",
        sender_role=TeamRole.TEAMMATE,
        sender_persona="dev",
        body="please review pr 123",
        broadcast=False,
        task_hint=None,
        mode=BridgeMailboxInjectMode.PASSTHROUGH,
        language="cn",
    )
    assert text == "[来自 Alice] please review pr 123"


@pytest.mark.level0
def test_passthrough_minimal_header_en():
    text = wrap_outbound_to_remote(
        sender="alice",
        sender_display_name="Alice",
        sender_role=TeamRole.TEAMMATE,
        sender_persona="dev",
        body="please review pr 123",
        broadcast=False,
        task_hint=None,
        mode=BridgeMailboxInjectMode.PASSTHROUGH,
        language="en",
    )
    assert text == "[from Alice] please review pr 123"


@pytest.mark.level0
def test_passthrough_broadcast_marker():
    text = wrap_outbound_to_remote(
        sender="leader",
        sender_display_name=None,  # falls back to sender
        sender_role=None,
        sender_persona=None,
        body="standup in 5",
        broadcast=True,
        task_hint=None,
        mode=BridgeMailboxInjectMode.PASSTHROUGH,
        language="en",
    )
    assert text.startswith("[from leader (broadcast)]")


@pytest.mark.level0
def test_rephrase_includes_role_and_persona_cn():
    text = wrap_outbound_to_remote(
        sender="alice",
        sender_display_name="Alice",
        sender_role=TeamRole.TEAMMATE,
        sender_persona="senior dev",
        body="please refactor metrics module",
        broadcast=False,
        task_hint="任务 #42 重构监控",
        mode=BridgeMailboxInjectMode.REPHRASE,
        language="cn",
    )
    assert "Alice" in text
    assert "teammate" in text
    assert "senior dev" in text
    assert "please refactor metrics module" in text
    assert "相关任务：任务 #42 重构监控" in text


@pytest.mark.level0
def test_rephrase_without_task_hint():
    text = wrap_outbound_to_remote(
        sender="alice",
        sender_display_name="Alice",
        sender_role=TeamRole.TEAMMATE,
        sender_persona="dev",
        body="hi",
        broadcast=False,
        task_hint=None,
        mode=BridgeMailboxInjectMode.REPHRASE,
        language="en",
    )
    assert "Re:" not in text


@pytest.mark.level0
def test_rephrase_broadcast_kind_label():
    text = wrap_outbound_to_remote(
        sender="leader",
        sender_display_name="Leader",
        sender_role=TeamRole.LEADER,
        sender_persona="planner",
        body="standup",
        broadcast=True,
        task_hint=None,
        mode=BridgeMailboxInjectMode.REPHRASE,
        language="en",
    )
    assert "broadcast" in text


# ---------------------------------------------------------------------------
# compose_bridge_inbound
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_compose_contains_original_and_remote_cn():
    text = compose_bridge_inbound(
        original_sender="alice",
        original_body="please review pr 123",
        remote_reply="diff looks clean. lgtm.",
        language="cn",
    )
    # Both halves of the input must be visible to the bridge LLM.
    assert "please review pr 123" in text
    assert "diff looks clean. lgtm." in text
    # Sender identity must show up so the LLM can reply to the right person.
    assert "alice" in text
    # The verbatim-pass-through contract must be explicit.
    assert "原样" in text


@pytest.mark.level0
def test_compose_forbids_relay_via_tool_cn():
    """Bridge LLM must NOT call send_message to forward the message to
    the remote — the framework already did it. The compose template
    spells this out so the prompt makes the contract obvious."""
    text = compose_bridge_inbound(
        original_sender="alice",
        original_body="x",
        remote_reply="y",
        language="cn",
    )
    assert "已自动转发" in text
    assert "无需再调用 send_message" in text


@pytest.mark.level0
def test_compose_en_template_includes_scheduling_contract():
    text = compose_bridge_inbound(
        original_sender="leader",
        original_body="status update?",
        remote_reply="working on task 42",
        language="en",
    )
    assert "schedule only" in text
    assert "Do NOT rewrite" in text
    assert "already been forwarded" in text


@pytest.mark.level0
def test_compose_propagates_sentinel_when_no_adapter():
    """When relay was short-circuited because no adapter exists, the
    sentinel ends up verbatim in the composed text — the bridge LLM
    sees the degradation explicitly."""
    from openjiuwen.agent_teams.interaction import REMOTE_UNAVAILABLE_SENTINEL

    text = compose_bridge_inbound(
        original_sender="alice",
        original_body="hi",
        remote_reply=REMOTE_UNAVAILABLE_SENTINEL,
        language="en",
    )
    assert REMOTE_UNAVAILABLE_SENTINEL in text


@pytest.mark.level0
def test_compose_includes_time_info_when_provided():
    """A rendered send time shows up in the header so the bridge avatar
    can gauge message delay; omitting it drops the time marker entirely."""
    text = compose_bridge_inbound(
        original_sender="alice",
        original_body="ping",
        remote_reply="pong",
        language="en",
        time_info="2026-05-27 14:30:05 +08:00 (3m ago)",
    )
    assert "2026-05-27 14:30:05 +08:00 (3m ago)" in text

    plain = compose_bridge_inbound(
        original_sender="alice",
        original_body="ping",
        remote_reply="pong",
        language="en",
    )
    assert "·" not in plain


# ---------------------------------------------------------------------------
# build_bridge_persona / build_team_overview
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_build_bridge_persona_cn_contains_identity_and_contract():
    text = build_bridge_persona(member_name="codex", persona="senior python reviewer")
    assert "codex" in text
    assert "senior python reviewer" in text
    assert "实际执行者" in text
    # No tools / no team state must be explicit so the remote doesn't
    # try to "call" anything.
    assert "没有工具" in text


@pytest.mark.level0
def test_build_bridge_persona_en_passthrough_contract():
    text = build_bridge_persona(
        member_name="claudecode",
        persona="pair-programmer",
        language="en",
    )
    assert "claudecode" in text
    assert "VERBATIM" in text
    assert "do NOT have tools" in text


@pytest.mark.level0
def test_build_team_overview_lists_members_cn():
    text = build_team_overview(
        team_name="demo",
        members=[
            MemberSummary(member_name="leader", role=TeamRole.LEADER, persona="planner"),
            MemberSummary(member_name="alice", role=TeamRole.TEAMMATE, persona="dev"),
            MemberSummary(member_name="codex", role=TeamRole.BRIDGE_AGENT, persona="reviewer"),
        ],
    )
    assert "团队 demo" in text
    assert "leader (leader): planner" in text
    assert "alice (teammate): dev" in text
    assert "codex (bridge_agent): reviewer" in text


@pytest.mark.level0
def test_build_team_overview_empty_members():
    text = build_team_overview(team_name="empty", members=[], language="en")
    assert "Team empty roster:" in text
    # No bullet lines should appear between header and footer.
    lines = [line for line in text.splitlines() if line.startswith("- ")]
    assert lines == []


@pytest.mark.level0
def test_build_team_overview_handles_no_persona():
    text = build_team_overview(
        team_name="demo",
        members=[MemberSummary(member_name="x", role=TeamRole.TEAMMATE)],
        language="en",
    )
    assert "x (teammate): (no persona)" in text
