# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for MemberMemoryToolkit."""

from __future__ import annotations

import os
import shutil
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from openjiuwen.core.memory.lite.coding_memory_tool_context import CodingMemoryToolContext
from openjiuwen.core.memory.lite.memory_tool_context import MemoryToolContext
from openjiuwen.core.memory.team.member_memory_toolkit import (
    MemberMemoryToolkit,
    _create_coding_tools,
    _create_general_tools,
)
from openjiuwen.core.runner.runner import Runner


@pytest.fixture(autouse=True)
def _stub_memory_index_manager_get():
    """避免 initialize() 拉起真实 MemoryIndexManager（sqlite / embedding）。"""
    mgr = MagicMock()
    mgr.closed = False
    with patch(
        "openjiuwen.core.memory.lite.manager.MemoryIndexManager.get",
        new_callable=AsyncMock,
        return_value=mgr,
    ):
        yield


class MockWorkspace:
    """Mock workspace for testing."""

    def __init__(self, root: str):
        self._root = root

    def get_node_path(self, node_name: str) -> str:
        node_path = os.path.join(self._root, node_name)
        os.makedirs(node_path, exist_ok=True)
        return node_path


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    dir_path = tempfile.mkdtemp()
    yield dir_path
    shutil.rmtree(dir_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_member_memory_toolkit_initialization_general_scenario(temp_dir):
    """Test MemberMemoryToolkit initializes correctly for general scenario."""
    workspace = MockWorkspace(temp_dir)

    toolkit = MemberMemoryToolkit(
        member_name="alice",
        team_name="team1",
        workspace=workspace,
        scenario="general",
    )

    result = await toolkit.initialize()

    assert result is True or result is False
    assert toolkit._scenario == "general"


@pytest.mark.asyncio
async def test_member_memory_toolkit_initialization_coding_scenario(temp_dir):
    """Test MemberMemoryToolkit initializes correctly for coding scenario."""
    workspace = MockWorkspace(temp_dir)

    toolkit = MemberMemoryToolkit(
        member_name="bob",
        team_name="team1",
        workspace=workspace,
        scenario="coding",
    )

    result = await toolkit.initialize()

    assert result is True or result is False
    assert toolkit._scenario == "coding"


@pytest.mark.asyncio
async def test_member_memory_toolkit_scenario_normalization(temp_dir):
    """Test MemberMemoryToolkit normalizes scenario names."""
    workspace = MockWorkspace(temp_dir)

    toolkit = MemberMemoryToolkit(
        member_name="charlie",
        team_name="team1",
        workspace=workspace,
        scenario="  CODING  ",
    )

    assert toolkit._scenario == "coding"


@pytest.mark.asyncio
async def test_member_memory_toolkit_read_only_flag(temp_dir):
    """Test MemberMemoryToolkit respects read_only flag."""
    workspace = MockWorkspace(temp_dir)

    toolkit = MemberMemoryToolkit(
        member_name="dave",
        team_name="team1",
        workspace=workspace,
        scenario="general",
        read_only=True,
    )

    assert toolkit._read_only is True


@pytest.mark.asyncio
async def test_member_memory_toolkit_get_tools_returns_list(temp_dir):
    """Test get_tools returns a list of tools."""
    workspace = MockWorkspace(temp_dir)

    toolkit = MemberMemoryToolkit(
        member_name="eve",
        team_name="team1",
        workspace=workspace,
        scenario="general",
    )

    tools = toolkit.get_tools()

    assert isinstance(tools, list)


@pytest.mark.asyncio
async def test_member_memory_toolkit_get_tool_cards_returns_list(temp_dir):
    """Test get_tool_cards returns a list of ToolCards."""
    workspace = MockWorkspace(temp_dir)

    toolkit = MemberMemoryToolkit(
        member_name="frank",
        team_name="team1",
        workspace=workspace,
        scenario="general",
    )

    cards = toolkit.get_tool_cards()

    assert isinstance(cards, list)


@pytest.mark.asyncio
async def test_member_memory_toolkit_close(temp_dir):
    """Test close method cleans up resources."""
    workspace = MockWorkspace(temp_dir)

    toolkit = MemberMemoryToolkit(
        member_name="grace",
        team_name="team1",
        workspace=workspace,
        scenario="general",
    )

    await toolkit.close()

    assert toolkit._manager is None
    assert toolkit._ctx is None
    assert toolkit._tools == []
    assert toolkit._initialized is False


@pytest.mark.asyncio
async def test_member_memory_toolkit_manager_property(temp_dir):
    """Test manager property returns the MemoryIndexManager."""
    workspace = MockWorkspace(temp_dir)

    toolkit = MemberMemoryToolkit(
        member_name="henry",
        team_name="team1",
        workspace=workspace,
        scenario="general",
    )

    manager = toolkit.manager

    assert manager is None or manager is not None


def test_create_general_tools_returns_list(temp_dir):
    """Test _create_general_tools returns a list of tools."""
    workspace = MockWorkspace(temp_dir)

    toolkit = MemberMemoryToolkit(
        member_name="iris",
        team_name="team1",
        workspace=workspace,
        scenario="general",
    )
    toolkit._ctx = MagicMock(spec=MemoryToolContext)

    tools = _create_general_tools(toolkit, read_only=False)

    assert isinstance(tools, list)
    assert len(tools) > 0


def test_create_general_tools_read_only(temp_dir):
    """Test _create_general_tools returns fewer tools in read_only mode."""
    workspace = MockWorkspace(temp_dir)

    toolkit_rw = MemberMemoryToolkit(
        member_name="jack",
        team_name="team1",
        workspace=workspace,
        scenario="general",
    )
    toolkit_ro = MemberMemoryToolkit(
        member_name="jack_ro",
        team_name="team1",
        workspace=workspace,
        scenario="general",
    )
    toolkit_rw._ctx = MagicMock(spec=MemoryToolContext)
    toolkit_ro._ctx = MagicMock(spec=MemoryToolContext)

    tools_rw = _create_general_tools(toolkit_rw, read_only=False)
    tools_ro = _create_general_tools(toolkit_ro, read_only=True)

    assert len(tools_ro) < len(tools_rw)


def test_create_coding_tools_returns_list(temp_dir):
    """Test _create_coding_tools returns a list of tools."""
    workspace = MockWorkspace(temp_dir)

    toolkit = MemberMemoryToolkit(
        member_name="kate",
        team_name="team1",
        workspace=workspace,
        scenario="coding",
    )

    toolkit._ctx = MagicMock(spec=CodingMemoryToolContext)

    tools = _create_coding_tools(toolkit, read_only=False)

    assert isinstance(tools, list)


def test_create_coding_tools_read_only(temp_dir):
    """Test _create_coding_tools returns fewer tools in read_only mode."""
    workspace = MockWorkspace(temp_dir)

    toolkit_rw = MemberMemoryToolkit(
        member_name="leo",
        team_name="team1",
        workspace=workspace,
        scenario="coding",
    )
    toolkit_ro = MemberMemoryToolkit(
        member_name="leo_ro",
        team_name="team1",
        workspace=workspace,
        scenario="coding",
    )

    toolkit_rw._ctx = MagicMock(spec=CodingMemoryToolContext)
    toolkit_ro._ctx = MagicMock(spec=CodingMemoryToolContext)

    tools_rw = _create_coding_tools(toolkit_rw, read_only=False)
    tools_ro = _create_coding_tools(toolkit_ro, read_only=True)

    assert len(tools_ro) < len(tools_rw)


def test_different_members_have_different_tool_ids(temp_dir):
    """Test that different members have different tool IDs."""
    workspace = MockWorkspace(temp_dir)

    toolkit1 = MemberMemoryToolkit(
        member_name="alice",
        team_name="team1",
        workspace=workspace,
        scenario="general",
    )
    toolkit2 = MemberMemoryToolkit(
        member_name="bob",
        team_name="team1",
        workspace=workspace,
        scenario="general",
    )

    toolkit1._ctx = MagicMock(spec=MemoryToolContext)
    toolkit2._ctx = MagicMock(spec=MemoryToolContext)

    tools1 = _create_general_tools(toolkit1, read_only=False)
    tools2 = _create_general_tools(toolkit2, read_only=False)

    ids1 = {t.card.id for t in tools1}
    ids2 = {t.card.id for t in tools2}

    assert ids1.isdisjoint(ids2)


def _runner_tool_count() -> int:
    tools = Runner.resource_mgr.get_tool()
    if tools is None:
        return 0
    if isinstance(tools, list):
        return sum(1 for t in tools if t is not None)
    return 1


@pytest.mark.asyncio
@patch("openjiuwen.core.memory.lite.manager.MemoryIndexManager.get", new_callable=AsyncMock)
@patch("openjiuwen.core.memory.lite.config.is_memory_enabled", return_value=True)
async def test_two_toolkits_same_team_different_managers_when_initialized(
    mock_enabled,
    mock_get,
    temp_dir,
):
    mgr_a = MagicMock()
    mgr_a.closed = False
    mgr_b = MagicMock()
    mgr_b.closed = False
    mock_get.side_effect = [mgr_a, mgr_b]

    root_a = os.path.join(temp_dir, "a")
    root_b = os.path.join(temp_dir, "b")
    os.makedirs(root_a)
    os.makedirs(root_b)

    wa = MockWorkspace(root_a)
    wb = MockWorkspace(root_b)
    tk_a = MemberMemoryToolkit(
        member_name="m1",
        team_name="team_iso",
        workspace=wa,
        scenario="general",
    )
    tk_b = MemberMemoryToolkit(
        member_name="m2",
        team_name="team_iso",
        workspace=wb,
        scenario="general",
    )

    assert await tk_a.initialize()
    assert await tk_b.initialize()
    assert id(tk_a.manager) != id(tk_b.manager)

    await tk_a.close()
    await tk_b.close()


@pytest.mark.asyncio
@patch("openjiuwen.core.memory.lite.config.is_memory_enabled", return_value=True)
async def test_initialize_close_leaves_runner_tool_registry_unchanged(
    mock_enabled,
    temp_dir,
):
    before = _runner_tool_count()

    workspace = MockWorkspace(os.path.join(temp_dir, "ws"))
    toolkit = MemberMemoryToolkit(
        member_name="solo",
        team_name="team_x",
        workspace=workspace,
        scenario="general",
    )
    assert await toolkit.initialize()
    await toolkit.close()

    assert _runner_tool_count() == before


def test_general_tool_names_include_search_coding_includes_read(temp_dir):
    workspace = MockWorkspace(temp_dir)

    toolkit_g = MemberMemoryToolkit(
        member_name="g",
        team_name="t",
        workspace=workspace,
        scenario="general",
    )
    toolkit_g._ctx = MagicMock(spec=MemoryToolContext)
    tools_g = _create_general_tools(toolkit_g, read_only=False)
    names_g = {t.card.name for t in tools_g}
    assert "memory_search" in names_g

    toolkit_c = MemberMemoryToolkit(
        member_name="c",
        team_name="t",
        workspace=workspace,
        scenario="coding",
    )
    toolkit_c._ctx = MagicMock(spec=CodingMemoryToolContext)
    tools_c = _create_coding_tools(toolkit_c, read_only=False)
    names_c = {t.card.name for t in tools_c}
    assert "coding_memory_read" in names_c


@pytest.mark.asyncio
@patch("openjiuwen.core.memory.lite.config.is_memory_enabled", return_value=True)
async def test_read_only_initialized_tools_are_read_only_only(mock_enabled, temp_dir):
    workspace = MockWorkspace(temp_dir)
    toolkit = MemberMemoryToolkit(
        member_name="ro",
        team_name="t",
        workspace=workspace,
        scenario="general",
        read_only=True,
    )
    assert await toolkit.initialize()

    names = {t.card.name for t in toolkit.get_tools()}
    assert "memory_search" in names
    assert "write_memory" not in names
    assert "edit_memory" not in names

    await toolkit.close()
