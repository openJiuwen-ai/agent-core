"""Discover configured targets and recover from unknown focus names without input."""

import pytest
from test_direct import setup

from openjiuwen.core.session.agent import Session
from openjiuwen.harness.tools.desktop.direct_protocol import input_schema


def test_target_schema_is_configuration_specific_and_does_not_pollute_other_calls():
    first = input_schema(["source", "destination"])
    assert first["properties"]["target"]["enum"] == ["source", "destination"]
    assert "enum" not in input_schema()["properties"]["target"]
    assert input_schema(["other"])["properties"]["target"]["enum"] == ["other"]


@pytest.mark.asyncio
async def test_unknown_target_preserves_session_and_fresh_token():
    tool, state, driver, lease = setup()
    owner = Session("owner")
    try:
        first = await tool.invoke({"operation": "observe"}, session=owner)
        assert [t["name"] for t in first.data["available_targets"]] == ["notes"]
        token = state.token
        rejected = await tool.invoke(
            {"operation": "focus", "observation_id": token, "target": "unconfigured-app"}, session=owner
        )
        assert not rejected.success and rejected.data["executed"] is False
        assert "notes" in rejected.data["reason"]
        assert not state.closed and state.token == token and not driver.actions
        result = await tool.invoke({"operation": "focus", "observation_id": token, "target": "notes"}, session=owner)
        assert result.success and len(driver.actions) == 1
        assert driver.actions[0].target == "notes" and state.token != token
    finally:
        await tool.close()
    assert driver.closed and lease.released
