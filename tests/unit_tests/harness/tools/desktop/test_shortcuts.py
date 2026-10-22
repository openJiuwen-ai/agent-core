"""General shortcut spellings preserve safety and direct-session recovery."""

import pytest
from test_direct import setup

from openjiuwen.core.session.agent import Session
from openjiuwen.harness.tools.desktop.direct_protocol import decode_call, input_schema
from openjiuwen.harness.tools.desktop.schema import Action
from openjiuwen.harness.tools.desktop.shortcuts import validate_keys


@pytest.mark.parametrize(
    "keys,expected",
    [
        (["Meta", "a"], ["cmd", "a"]),
        (["Command", "S"], ["cmd", "s"]),
        (["CMD", "SHIFT", "S"], ["cmd", "shift", "s"]),
        (["⌘", "C"], ["cmd", "c"]),
        (["Enter"], ["return"]),
        (["ESC"], ["escape"]),
        (["ArrowLeft"], ["left"]),
    ],
)
@pytest.mark.parametrize("legacy", [False, True])
def test_explicit_aliases_in_flat_and_legacy_calls(keys, expected, legacy):
    call = (
        {"operation": "act", "action": {"kind": "shortcut", "keys": keys}}
        if legacy
        else {"operation": "shortcut", "keys": keys}
    )
    call["observation_id"] = "current"
    operation, action = decode_call(call)
    assert operation == "act" and action.keys == expected
    validate_keys(action.keys)


@pytest.mark.parametrize(
    "keys",
    [["hyper", "a"], ["ctrl", "unknown"], ["cmd", "f99"], ["cmd", "cmd", "s"], ["a", "cmd"], ["cmd+a"], ["cmd"], [1]],
)
def test_unsupported_or_malformed_chords_are_rejected(keys):
    with pytest.raises(ValueError):
        decode_call({"operation": "shortcut", "keys": keys, "observation_id": "current"})


def test_tuple_and_string_are_not_coerced_to_key_arrays():
    for value in [("cmd", "s"), "cmd+s"]:
        with pytest.raises(ValueError):
            Action(kind="shortcut", keys=value)
    assert '"cmd","s"' in input_schema()["properties"]["keys"]["description"]


@pytest.mark.asyncio
async def test_bad_shortcut_preserves_token_and_corrected_call_executes_once():
    tool, state, driver, lease = setup()
    try:
        owner = Session("owner")
        await tool.invoke({"operation": "observe"}, session=owner)
        token = state.token
        rejected = await tool.invoke(
            {"operation": "shortcut", "observation_id": token, "keys": ["ctrl", "unknown"]}, session=owner
        )
        assert not rejected.success and rejected.data["executed"] is False
        assert not state.closed and state.token == token and not driver.actions
        corrected = await tool.invoke(
            {"operation": "shortcut", "observation_id": token, "keys": ["Meta", "S"]}, session=owner
        )
        assert corrected.success and len(driver.actions) == 1
        assert driver.actions[0].keys == ["cmd", "s"]
        assert state.token != token
    finally:
        await tool.close()
    assert driver.closed and lease.released
