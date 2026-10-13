"""Tests for the exact-name Persistent tool effect policy."""

from importlib import import_module

import pytest

_MODULE = "openjiuwen.harness.persistent_agent.tools.effect_policy"


def is_read_only_tool_name(tool_name):
    """Load lazily so an unimplemented policy fails as an assertion, not collection error."""
    try:
        policy = import_module(_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name == _MODULE:
            pytest.fail("the Persistent tool effect policy is not implemented")
        raise
    return policy.is_read_only_tool_name(tool_name)


@pytest.mark.parametrize("tool_name", ["read_file", "list_files"])
def test_only_exact_workspace_read_tool_names_are_read_only(tool_name):
    assert is_read_only_tool_name(tool_name) is True


@pytest.mark.parametrize(
    "tool_name",
    [
        "",
        None,
        "READ_FILE",
        "Read_file",
        "read_file ",
        " read_file",
        "read_file_v2",
        "my_read_file",
        "search_files",
        "custom_tool",
        "mcp__files__read_file",
    ],
)
def test_unregistered_and_near_match_names_are_side_effecting(tool_name):
    assert is_read_only_tool_name(tool_name) is False
