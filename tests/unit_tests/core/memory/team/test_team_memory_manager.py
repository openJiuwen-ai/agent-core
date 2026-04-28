# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for TeamMemoryManager."""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.memory.team.manager import TeamMemoryManager
from openjiuwen.core.memory.team.manager_params import (
    TeamLifecycle,
    TeamMemoryManagerParams,
    TeamRole,
    TeamScenario,
)


@pytest.fixture(autouse=True)
def _stub_memory_index_manager_get():
    """避免 MemberMemoryToolkit / TeamMemoryManager 单测拉起真实索引与 embedding。"""
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
        self._nodes = {}

    def get_node_path(self, node_name: str) -> Any:
        if node_name not in self._nodes:
            node_path = os.path.join(self._root, node_name)
            os.makedirs(node_path, exist_ok=True)
            self._nodes[node_name] = node_path
        return self._nodes[node_name]


class MockDeepAgent:
    """Mock DeepAgent for testing."""

    def __init__(self):
        self.system_prompt_builder = MockPromptBuilder()
        self.ability_manager = MockAbilityManager()
        self._pending_rails = []
        self._registered_rails = []
        self._stale_rails = []

    def strip_rails_by_type(self, rail_types):
        removed = 0
        before = len(self._pending_rails)
        self._pending_rails = [r for r in self._pending_rails if not isinstance(r, rail_types)]
        removed += before - len(self._pending_rails)
        for rail in list(self._registered_rails):
            if isinstance(rail, rail_types):
                self._stale_rails.append(rail)
                removed += 1
        return removed


class MockPromptBuilder:
    """Mock prompt builder for testing."""

    def __init__(self):
        self._sections: Dict[str, Any] = {}

    def add_section(self, section: Any) -> None:
        self._sections[section.name] = section

    def remove_section(self, name: str) -> None:
        self._sections.pop(name, None)

    def get_section(self, name: str) -> Any:
        return self._sections.get(name)


class MockAbilityManager:
    """Mock ability manager for testing."""

    def __init__(self):
        self._abilities: Dict[str, Any] = {}

    def add(self, tool_card: Any) -> Any:
        result = MagicMock()
        result.added = tool_card.name not in self._abilities
        self._abilities[tool_card.name] = tool_card
        return result

    def remove(self, names: List[str]) -> None:
        for name in names:
            self._abilities.pop(name, None)


def create_test_params(
    member_name: str = "test_member",
    team_name: str = "test_team",
    role: TeamRole = "teammate",
    lifecycle: TeamLifecycle = "temporary",
    scenario: TeamScenario = "general",
    workspace_root: str = None,
) -> TeamMemoryManagerParams:
    """Create test params with defaults."""
    workspace = None
    if workspace_root:
        workspace = MockWorkspace(workspace_root)

    return TeamMemoryManagerParams(
        member_name=member_name,
        team_name=team_name,
        role=role,
        lifecycle=lifecycle,
        scenario=scenario,
        embedding_config=None,
        workspace=workspace,
        sys_operation=None,
        team_memory_dir=None,
        language="en",
        prompt_mode="passive",
        enable_auto_extract=False,
        read_only_source_workspace=None,
    )


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    dir_path = tempfile.mkdtemp()
    yield dir_path
    shutil.rmtree(dir_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_init_toolkit_idempotent(temp_dir):
    """Test that init_toolkit is idempotent - calling it twice only initializes once."""
    params = create_test_params(workspace_root=temp_dir)
    manager = TeamMemoryManager(params)

    result1 = await manager.init_toolkit()
    result2 = await manager.init_toolkit()

    assert result1 is True
    assert result2 is True
    assert manager._toolkit is not None


@pytest.mark.asyncio
async def test_init_toolkit_no_workspace():
    """Test init_toolkit returns False when workspace is None."""
    params = create_test_params(workspace_root=None)
    manager = TeamMemoryManager(params)

    result = await manager.init_toolkit()

    assert result is False
    assert manager._toolkit is None


@pytest.mark.asyncio
async def test_register_tools_idempotent(temp_dir):
    """Test that register_tools is idempotent - calling it twice skips re-registration."""
    params = create_test_params(workspace_root=temp_dir)
    manager = TeamMemoryManager(params)
    await manager.init_toolkit()

    deep_agent = MockDeepAgent()

    manager.register_tools(deep_agent)
    owned_names_after_first = set(manager._owned_tool_names)

    manager.register_tools(deep_agent)
    owned_names_after_second = set(manager._owned_tool_names)

    assert owned_names_after_first == owned_names_after_second


@pytest.mark.asyncio
async def test_register_tools_strips_memory_rails(temp_dir):
    """Test that register_tools removes MemoryRail/CodingMemoryRail from DeepAgent."""
    from openjiuwen.harness.rails.memory.memory_rail import MemoryRail
    from openjiuwen.harness.rails.memory.coding_memory_rail import CodingMemoryRail

    params = create_test_params(workspace_root=temp_dir)
    manager = TeamMemoryManager(params)
    await manager.init_toolkit()

    deep_agent = MockDeepAgent()
    mock_rail1 = MagicMock(spec=MemoryRail)
    mock_rail2 = MagicMock(spec=CodingMemoryRail)
    deep_agent._pending_rails = [mock_rail1, mock_rail2]
    deep_agent._registered_rails = [mock_rail1, mock_rail2]

    manager.register_tools(deep_agent)

    assert len(deep_agent._pending_rails) == 0
    assert len(deep_agent._stale_rails) == 2


@pytest.mark.asyncio
async def test_load_and_inject_without_builder(temp_dir):
    """Test load_and_inject handles missing builder gracefully."""
    params = create_test_params(workspace_root=temp_dir)
    manager = TeamMemoryManager(params)
    await manager.init_toolkit()

    deep_agent = MockDeepAgent()
    deep_agent.system_prompt_builder = None

    await manager.load_and_inject(deep_agent, query="test")

    assert manager._cached_base_section is None


@pytest.mark.asyncio
async def test_load_and_inject_with_cached_section(temp_dir):
    """Test load_and_inject reuses cached section."""
    params = create_test_params(workspace_root=temp_dir)
    manager = TeamMemoryManager(params)
    await manager.init_toolkit()

    deep_agent = MockDeepAgent()

    await manager.load_and_inject(deep_agent, query="")
    first_section = manager._cached_base_section

    await manager.load_and_inject(deep_agent, query="")
    second_section = manager._cached_base_section

    assert first_section is second_section


@pytest.mark.asyncio
async def test_extract_after_round_non_persistent(temp_dir):
    """Test extract_after_round does nothing for non-persistent lifecycle."""
    params = create_test_params(
        workspace_root=temp_dir,
        lifecycle="temporary",
        role="leader",
    )
    manager = TeamMemoryManager(params)
    await manager.init_toolkit()

    with patch(
        "openjiuwen.core.memory.team.extractor.extract_team_memories",
        new_callable=AsyncMock,
    ) as mock_extract:
        await manager.extract_after_round()
        mock_extract.assert_not_called()


@pytest.mark.asyncio
async def test_extract_after_round_teammate(temp_dir):
    """Test extract_after_round does nothing for teammate role."""
    params = create_test_params(
        workspace_root=temp_dir,
        lifecycle="persistent",
        role="teammate",
    )
    manager = TeamMemoryManager(params)
    await manager.init_toolkit()

    with patch(
        "openjiuwen.core.memory.team.extractor.extract_team_memories",
        new_callable=AsyncMock,
    ) as mock_extract:
        await manager.extract_after_round()
        mock_extract.assert_not_called()


@pytest.mark.asyncio
async def test_extract_after_round_no_auto_extract(temp_dir):
    """Test extract_after_round does nothing when auto_extract is disabled."""
    params = create_test_params(
        workspace_root=temp_dir,
        lifecycle="persistent",
        role="leader",
    )
    params.enable_auto_extract = False
    manager = TeamMemoryManager(params)
    await manager.init_toolkit()

    with patch(
        "openjiuwen.core.memory.team.extractor.extract_team_memories",
        new_callable=AsyncMock,
    ) as mock_extract:
        await manager.extract_after_round()
        mock_extract.assert_not_called()


@pytest.mark.asyncio
async def test_extract_after_round_leader_persistent_calls_extract_team_memories(temp_dir):
    """Leader + persistent + auto_extract + db + team_memory_dir invokes extraction."""
    tm_dir = os.path.join(temp_dir, "team-memory")
    os.makedirs(tm_dir, exist_ok=True)

    mock_db = MagicMock()
    mock_task_manager = MagicMock()
    mock_model = MagicMock()
    mock_sys_op = MagicMock()

    params = create_test_params(
        workspace_root=temp_dir,
        lifecycle="persistent",
        role="leader",
    )
    params.enable_auto_extract = True
    params.team_memory_dir = tm_dir
    params.db = mock_db
    params.task_manager = mock_task_manager
    params.extraction_model = mock_model
    params.sys_operation = mock_sys_op
    params.timezone_offset_hours = 9.0

    manager = TeamMemoryManager(params)
    await manager.init_toolkit()

    with patch(
        "openjiuwen.core.memory.team.extractor.extract_team_memories",
        new_callable=AsyncMock,
    ) as mock_extract:
        await manager.extract_after_round()
        mock_extract.assert_awaited_once_with(
            team_name=params.team_name,
            db=mock_db,
            task_manager=mock_task_manager,
            team_memory_dir=tm_dir,
            sys_operation=mock_sys_op,
            model=mock_model,
            tz_offset_hours=9.0,
        )


@pytest.mark.asyncio
async def test_close_cleans_up_resources(temp_dir):
    """Test close properly cleans up all resources."""
    params = create_test_params(workspace_root=temp_dir)
    manager = TeamMemoryManager(params)
    await manager.init_toolkit()

    deep_agent = MockDeepAgent()
    manager.register_tools(deep_agent)

    assert len(manager._owned_tool_names) > 0

    await manager.close()

    assert len(manager._owned_tool_names) == 0
    assert len(manager._owned_tool_ids) == 0
    assert manager._cached_base_section is None
    assert manager._toolkit is None


@pytest.mark.asyncio
async def test_close_without_register_tools(temp_dir):
    """Test close works when register_tools was never called."""
    params = create_test_params(workspace_root=temp_dir)
    manager = TeamMemoryManager(params)
    await manager.init_toolkit()

    await manager.close()

    assert manager._toolkit is None


@pytest.mark.asyncio
async def test_close_multiple_times(temp_dir):
    """Test close is idempotent - can be called multiple times."""
    params = create_test_params(workspace_root=temp_dir)
    manager = TeamMemoryManager(params)
    await manager.init_toolkit()

    await manager.close()
    await manager.close()
    await manager.close()

    assert manager._toolkit is None


@pytest.mark.asyncio
async def test_load_and_inject_adds_section_to_builder(temp_dir):
    """Test that load_and_inject adds section to builder."""
    params = create_test_params(workspace_root=temp_dir)
    manager = TeamMemoryManager(params)
    await manager.init_toolkit()

    deep_agent = MockDeepAgent()
    await manager.load_and_inject(deep_agent)

    section = deep_agent.system_prompt_builder.get_section(TeamMemoryManager.SECTION_NAME)
    assert section is not None


@pytest.mark.asyncio
async def test_load_and_inject_removes_old_section(temp_dir):
    """Test that load_and_inject removes old section before adding new one."""
    params = create_test_params(workspace_root=temp_dir)
    manager = TeamMemoryManager(params)
    await manager.init_toolkit()

    deep_agent = MockDeepAgent()
    await manager.load_and_inject(deep_agent)

    old_section = deep_agent.system_prompt_builder.get_section(TeamMemoryManager.SECTION_NAME)

    await manager.load_and_inject(deep_agent)

    new_section = deep_agent.system_prompt_builder.get_section(TeamMemoryManager.SECTION_NAME)
    assert new_section is not None and old_section is not None
    assert new_section.name == old_section.name


@pytest.mark.asyncio
async def test_register_tools_with_missing_ability_manager(temp_dir):
    """Test register_tools handles missing ability_manager gracefully."""
    params = create_test_params(workspace_root=temp_dir)
    manager = TeamMemoryManager(params)
    await manager.init_toolkit()

    deep_agent = MockDeepAgent()
    del deep_agent.ability_manager

    manager.register_tools(deep_agent)

    assert manager._deep_agent_for_cleanup is None
