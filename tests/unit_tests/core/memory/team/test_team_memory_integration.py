# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Integration tests for TeamMemoryManager lifecycle."""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Any, Dict
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
    """避免 MemberMemoryToolkit 单测拉起真实索引与 embedding。"""
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

    def remove(self, names: list) -> None:
        for name in names:
            self._abilities.pop(name, None)


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


def create_test_params(
    member_name: str = "test_member",
    team_name: str = "test_team",
    role: TeamRole = "teammate",
    lifecycle: TeamLifecycle = "temporary",
    scenario: TeamScenario = "general",
    workspace_root: str = None,
    enable_auto_extract: bool = False,
    team_memory_dir: str = None,
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
        team_memory_dir=team_memory_dir,
        language="en",
        prompt_mode="passive",
        enable_auto_extract=enable_auto_extract,
        read_only_source_workspace=None,
    )


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    dir_path = tempfile.mkdtemp()
    yield dir_path
    shutil.rmtree(dir_path, ignore_errors=True)


@pytest.fixture
def temp_team_dir(temp_dir):
    """Create a temporary team memory directory."""
    team_dir = os.path.join(temp_dir, "team_memory")
    os.makedirs(team_dir, exist_ok=True)
    return team_dir


@pytest.mark.asyncio
async def test_full_lifecycle_init_register_inject_close(temp_dir):
    """Test complete lifecycle: init -> register -> inject -> close."""
    params = create_test_params(workspace_root=temp_dir)
    manager = TeamMemoryManager(params)

    init_result = await manager.init_toolkit()
    assert init_result is True

    deep_agent = MockDeepAgent()
    manager.register_tools(deep_agent)
    assert len(manager._owned_tool_names) > 0

    await manager.load_and_inject(deep_agent, query="test")
    section = deep_agent.system_prompt_builder.get_section(TeamMemoryManager.SECTION_NAME)
    assert section is not None

    await manager.close()
    assert len(manager._owned_tool_names) == 0
    assert manager._toolkit is None


@pytest.mark.asyncio
async def test_lifecycle_with_persistent_leader_auto_extract(temp_dir, temp_team_dir):
    """Test persistent leader with auto extract enabled triggers extraction."""
    params = create_test_params(
        workspace_root=temp_dir,
        lifecycle="persistent",
        role="leader",
        enable_auto_extract=True,
        team_memory_dir=temp_team_dir,
    )
    params.db = MagicMock()
    params.task_manager = MagicMock()
    params.extraction_model = MagicMock()
    params.sys_operation = MagicMock()

    manager = TeamMemoryManager(params)
    await manager.init_toolkit()

    with patch(
        "openjiuwen.core.memory.team.extractor.extract_team_memories",
        new_callable=AsyncMock,
    ) as mock_extract:
        await manager.extract_after_round()
        mock_extract.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifecycle_inject_without_register_does_not_fail(temp_dir):
    """Test that load_and_inject works even without prior register_tools."""
    params = create_test_params(workspace_root=temp_dir)
    manager = TeamMemoryManager(params)
    await manager.init_toolkit()

    deep_agent = MockDeepAgent()
    await manager.load_and_inject(deep_agent)

    section = deep_agent.system_prompt_builder.get_section(TeamMemoryManager.SECTION_NAME)
    assert section is not None


@pytest.mark.asyncio
async def test_lifecycle_multiple_rounds_inject(temp_dir):
    """Test multiple rounds of inject use cached section."""
    params = create_test_params(workspace_root=temp_dir)
    manager = TeamMemoryManager(params)
    await manager.init_toolkit()

    deep_agent = MockDeepAgent()

    await manager.load_and_inject(deep_agent, query="first")
    first_section = manager._cached_base_section

    await manager.load_and_inject(deep_agent, query="second")
    second_section = manager._cached_base_section

    assert first_section is second_section


@pytest.mark.asyncio
async def test_lifecycle_extract_does_not_run_twice_in_same_round(temp_dir, temp_team_dir):
    """Each extract_after_round awaits extract_team_memories when leader conditions hold."""
    params = create_test_params(
        workspace_root=temp_dir,
        lifecycle="persistent",
        role="leader",
        enable_auto_extract=True,
        team_memory_dir=temp_team_dir,
    )
    params.db = MagicMock()
    params.task_manager = MagicMock()
    params.extraction_model = MagicMock()
    params.sys_operation = MagicMock()

    manager = TeamMemoryManager(params)
    await manager.init_toolkit()

    with patch(
        "openjiuwen.core.memory.team.extractor.extract_team_memories",
        new_callable=AsyncMock,
    ) as mock_extract:
        await manager.extract_after_round()
        assert mock_extract.await_count == 1

        await manager.extract_after_round()
        assert mock_extract.await_count == 2


@pytest.mark.asyncio
async def test_lifecycle_read_only_mode(temp_dir):
    """Test lifecycle with read_only_source_workspace."""
    from openjiuwen.harness.workspace.workspace import Workspace

    read_only_source = os.path.join(temp_dir, "source")
    os.makedirs(read_only_source, exist_ok=True)

    params = create_test_params(workspace_root=temp_dir)
    params.read_only_source_workspace = read_only_source
    manager = TeamMemoryManager(params)

    init_result = await manager.init_toolkit()

    assert manager._workspace is not None


@pytest.mark.asyncio
async def test_lifecycle_coding_scenario(temp_dir):
    """Test lifecycle with coding scenario."""
    params = create_test_params(
        workspace_root=temp_dir,
        scenario="coding",
    )
    manager = TeamMemoryManager(params)

    await manager.init_toolkit()
    deep_agent = MockDeepAgent()
    await manager.load_and_inject(deep_agent)

    section = deep_agent.system_prompt_builder.get_section(TeamMemoryManager.SECTION_NAME)
    assert section is not None


@pytest.mark.asyncio
async def test_lifecycle_chinese_language(temp_dir):
    """Test lifecycle with Chinese language."""
    params = create_test_params(workspace_root=temp_dir)
    params.language = "cn"
    manager = TeamMemoryManager(params)

    await manager.init_toolkit()
    deep_agent = MockDeepAgent()
    await manager.load_and_inject(deep_agent)

    section = deep_agent.system_prompt_builder.get_section(TeamMemoryManager.SECTION_NAME)
    assert section is not None


@pytest.mark.asyncio
async def test_lifecycle_proactive_mode(temp_dir):
    """Test lifecycle with proactive prompt mode."""
    params = create_test_params(workspace_root=temp_dir)
    params.prompt_mode = "proactive"
    manager = TeamMemoryManager(params)

    await manager.init_toolkit()
    deep_agent = MockDeepAgent()
    await manager.load_and_inject(deep_agent)

    section = deep_agent.system_prompt_builder.get_section(TeamMemoryManager.SECTION_NAME)
    assert section is not None


@pytest.mark.asyncio
async def test_lifecycle_close_after_multiple_operations(temp_dir):
    """Test close after multiple operations doesn't raise."""
    params = create_test_params(workspace_root=temp_dir)
    manager = TeamMemoryManager(params)

    await manager.init_toolkit()

    deep_agent = MockDeepAgent()
    manager.register_tools(deep_agent)

    await manager.load_and_inject(deep_agent, query="test1")
    await manager.load_and_inject(deep_agent, query="test2")

    await manager.close()
    await manager.close()

    assert manager._toolkit is None
    assert len(manager._owned_tool_names) == 0
