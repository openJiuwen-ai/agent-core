# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for session-scoped agent identity in the execution context."""

import asyncio

import pytest

from openjiuwen.core.session.agent import Session, create_agent_session
from openjiuwen.core.session.identity import (
    current_agent_identity,
    reset_current_agent_identity,
    set_current_agent_identity,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


@pytest.fixture(autouse=True)
def _restore_identity():
    """Restore the ambient identity after each test.

    The probe set() snapshots the incoming value; its token reset in
    teardown rewinds to exactly that, whatever the test bound in between.
    """
    token = set_current_agent_identity("fixture-probe", "FixtureProbe")
    yield
    reset_current_agent_identity(token)


class TestIdentityContext:
    def test_set_and_get_roundtrip(self):
        set_current_agent_identity("agent-1", "MyAgent", "Does things")

        assert current_agent_identity() == {
            "agent_id": "agent-1",
            "agent_name": "MyAgent",
            "agent_description": "Does things",
        }

    def test_reset_restores_previous_binding(self):
        token_a = set_current_agent_identity("agent-a", "A")
        token_b = set_current_agent_identity("agent-b", "B")
        assert current_agent_identity()["agent_id"] == "agent-b"

        reset_current_agent_identity(token_b)
        assert current_agent_identity()["agent_id"] == "agent-a"
        reset_current_agent_identity(token_a)

    def test_empty_agent_id_binds_nothing(self):
        assert set_current_agent_identity(None, "NameOnly") is None
        assert set_current_agent_identity("", "NameOnly") is None


class TestSessionBindsIdentity:
    def test_create_agent_session_binds_card_identity(self):
        card = AgentCard(id="group-77", name="SupportGroup", description="Support group")

        create_agent_session(session_id="sess-identity-1", card=card)

        identity = current_agent_identity()
        assert identity["agent_id"] == "group-77"
        assert identity["agent_name"] == "SupportGroup"
        assert identity["agent_description"] == "Support group"

    def test_session_constructor_binds_card_identity(self):
        Session(session_id="sess-identity-2", card=AgentCard(id="agent-9", name="Worker"))

        assert current_agent_identity()["agent_id"] == "agent-9"
        assert current_agent_identity()["agent_name"] == "Worker"

    def test_cardless_session_leaves_ambient_identity_untouched(self):
        outer = set_current_agent_identity("outer-1", "OuterAgent")

        Session(session_id="sess-identity-3", card=None)

        assert current_agent_identity()["agent_id"] == "outer-1"
        reset_current_agent_identity(outer)

    @pytest.mark.asyncio
    async def test_child_task_inherits_identity(self):
        """Member execution tasks (asyncio.create_task) see the identity —
        the mechanism orchestrated modes rely on."""
        create_agent_session(
            session_id="sess-identity-4",
            card=AgentCard(id="group-77", name="SupportGroup"),
        )

        async def member_work():
            return current_agent_identity()

        inherited = await asyncio.create_task(member_work())

        assert inherited["agent_id"] == "group-77"
        assert inherited["agent_name"] == "SupportGroup"
