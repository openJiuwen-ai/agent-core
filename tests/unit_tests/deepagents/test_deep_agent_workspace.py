# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for DeepAgent workspace integration - directory creation"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from openjiuwen.core.foundation.llm.model import init_model
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import (
    LocalWorkConfig,
    OperationMode,
    SysOperationCard,
)
from openjiuwen.deepagents import Workspace
from openjiuwen.deepagents.deep_agent import DeepAgent
from openjiuwen.deepagents.factory import create_deep_agent
from openjiuwen.deepagents.schema.config import DeepAgentConfig


# =============================================================================
# Helper Classes and Functions
# =============================================================================

class _DummyResponse:
    """Dummy response for LLM calls."""

    def __init__(self, content: str = ""):
        self.role = "assistant"
        self.content = content
        self.tool_calls = None
        self.usage_metadata = None
        self.finish_reason = "stop"
        self.parser_content = None
        self.reasoning_content = None


class _DummyModel:
    """Dummy Model for testing that supports DeepAgent's _create_react_agent."""

    def __init__(self, content: str = ""):
        self._content = content
        self.calls: List[Dict[str, Any]] = []
        self.model_client_config = None
        self.model_config = None

    async def invoke(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return _DummyResponse(self._content)


class _TrackingDirectoryBuilder:
    """Test-only DirectoryBuilder that records _create_directory_recursive calls."""

    def __init__(self, sys_operation, root_path: str = "./"):
        self.sys_operation = sys_operation
        self.root_path = root_path
        self.create_calls: List[str] = []

    async def build(self, directories: List[Dict]) -> None:
        for node in directories:
            await self._create_directory_recursive(node)

    async def _create_directory_recursive(self, node: Dict, parent_path: str = "") -> None:
        relative_path = node.get("path", "")
        full_path = f"{parent_path}/{relative_path}" if parent_path else relative_path
        self.create_calls.append(full_path)

        for child in node.get("children", []):
            await self._create_directory_recursive(child, full_path)


def _make_sys_operation(tmp_path: Path):
    """Create a local SysOperation for tests."""
    card = SysOperationCard(
        id=f"test_workspace_sysop_{tmp_path.name}_{int(time.time() * 1000)}",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=str(tmp_path)),
    )
    Runner.resource_mgr.add_sys_operation(card)
    return Runner.resource_mgr.get_sys_operation(card.id)


def _make_agent(sys_operation, workspace=None):
    """Create a DeepAgent for tests."""
    model = init_model(
        provider="OpenAI",
        model_name="dummy-model",
        api_key="dummy-key",
        api_base="https://example.com/v1",
        verify_ssl=False,
    )
    return create_deep_agent(
        model=model,
        card=AgentCard(name="test_workspace_agent", description="test workspace agent"),
        system_prompt="You are a test assistant.",
        max_iterations=3,
        enable_task_loop=False,
        workspace=workspace,
        sys_operation=sys_operation
    )

# =============================================================================
# Workspace Schema Tests
# =============================================================================


def test_workspace_default_schema_has_required_directories():
    """Test that default workspace has agent, user, skills, sessions."""
    workspace = Workspace(root_path="./default")
    names = {node.get("name") for node in workspace.directories}

    assert "AGENT.md" in names
    assert "SOUL.md" in names
    assert "HEARTBEAT.md" in names
    assert "IDENTITY.md" in names
    assert "USER.md" in names
    assert "memory" in names
    assert "todo" in names
    assert "messages" in names
    assert "skills" in names
    assert "agents" in names


def test_workspace_custom_directories_preserved():
    """Test that custom directories are preserved."""
    custom_dirs = [
        {"name": "custom1", "path": "custom1", "description": "Custom 1", "children": []},
        {"name": "custom2", "path": "custom2", "description": "Custom 2", "children": []},
    ]
    workspace = Workspace(root_path="./custom", directories=custom_dirs)

    names = {node.get("name") for node in workspace.directories}
    assert "custom1" in names
    assert "custom2" in names


def test_workspace_missing_defaults_supplemented():
    """Test that missing default directories are auto-supplemented."""
    custom_dirs = [
        {"name": "custom", "path": "custom", "description": "Custom", "children": []},
    ]
    workspace = Workspace(root_path="./partial", directories=custom_dirs)
    names = {node.get("name") for node in workspace.directories}

    assert "custom" in names
    assert "AGENT.md" in names
    assert "USER.md" in names


def test_workspace_get_directory_returns_path():
    """Test get_directory returns correct path."""
    workspace = Workspace(root_path="./")
    assert workspace.get_directory("AGENT.md") == "AGENT.md"
    assert workspace.get_directory("USER.md") == "USER.md"
    assert workspace.get_directory("nonexistent") is None


def test_workspace_accepts_pathlib_path():
    """Test that workspace.root_path accepts Path objects."""
    workspace = Workspace(root_path=Path("/tmp/test"))
    root = str(workspace.root_path)
    assert "tmp" in root and "test" in root


def test_workspace_set_directory_adds_new():
    """Test set_directory adds new directory node."""
    workspace = Workspace(root_path="./test")
    initial_count = len(workspace.directories)

    workspace.set_directory({"name": "new_dir", "path": "new_dir", "description": "New", "children": []})

    assert len(workspace.directories) == initial_count + 1
    assert workspace.get_directory("new_dir") == "new_dir"


def test_workspace_set_directory_updates_existing():
    """Test set_directory updates existing directory node."""
    workspace = Workspace(root_path="./test")

    workspace.set_directory({"name": "agent", "path": "agent", "description": "Updated desc", "children": []})

    agent_node = next((n for n in workspace.directories if n.get("name") == "agent"), None)
    assert agent_node is not None
    assert agent_node.get("description") == "Updated desc"


# =============================================================================
# WorkspaceNode Enum Tests
# =============================================================================


def test_workspace_get_directory_with_enum():
    """Test get_directory accepts WorkspaceNode enum as argument."""
    from openjiuwen.deepagents.workspace.workspace import WorkspaceNode

    workspace = Workspace(root_path="./test")

    assert workspace.get_directory(WorkspaceNode.USER_MD) == "USER.md"
    assert workspace.get_directory(WorkspaceNode.SKILLS) == "skills"
    assert workspace.get_directory(WorkspaceNode.MEMORY) == "memory"
    assert workspace.get_directory(WorkspaceNode.TODO) == "todo"
    assert workspace.get_directory(WorkspaceNode.MESSAGES) == "messages"
    assert workspace.get_directory(WorkspaceNode.AGENTS) == "agents"
    assert workspace.get_directory(WorkspaceNode.AGENT_MD) == "AGENT.md"
    assert workspace.get_directory(WorkspaceNode.SOUL_MD) == "SOUL.md"
    assert workspace.get_directory(WorkspaceNode.HEARTBEAT_MD) == "HEARTBEAT.md"
    assert workspace.get_directory(WorkspaceNode.IDENTITY_MD) == "IDENTITY.md"
    assert workspace.get_directory(WorkspaceNode.MEMORY_MD) == "MEMORY.md"
    assert workspace.get_directory(WorkspaceNode.DAILY_MEMORY) == "daily_memory"


def test_workspace_get_directory_with_string_still_works():
    """Test get_directory still works with string arguments."""
    workspace = Workspace(root_path="./test")

    assert workspace.get_directory("USER.md") == "USER.md"
    assert workspace.get_directory("skills") == "skills"
    assert workspace.get_directory("AGENT.md") == "AGENT.md"


def test_workspace_get_directory_enum_and_string_equivalent():
    """Test that WorkspaceNode enum and string give same result."""
    from openjiuwen.deepagents.workspace.workspace import WorkspaceNode

    workspace = Workspace(root_path="./test")

    assert workspace.get_directory(WorkspaceNode.USER_MD) == workspace.get_directory("USER.md")
    assert workspace.get_directory(WorkspaceNode.SKILLS) == workspace.get_directory("skills")
    assert workspace.get_directory(WorkspaceNode.MEMORY) == workspace.get_directory("memory")
    assert workspace.get_directory(WorkspaceNode.AGENT_MD) == workspace.get_directory("AGENT.md")


def test_workspace_get_directory_nonexistent_with_enum():
    """Test get_directory returns None for nonexistent enum values."""
    from openjiuwen.deepagents.workspace.workspace import WorkspaceNode

    workspace = Workspace(root_path="./test")

    assert workspace.get_directory(WorkspaceNode.USER_MD) is not None
    assert workspace.get_directory("nonexistent_dir") is None


# =============================================================================
# Multi-language Workspace Tests
# =============================================================================


def test_workspace_default_language_is_chinese():
    """Test that default workspace language is Chinese."""
    workspace = Workspace(root_path="./test")
    assert workspace.language == "cn"
    agent_node = next((n for n in workspace.directories if n.get("name") == "AGENT.md"), None)
    assert agent_node is not None
    assert "基础配置和能力" in agent_node.get("description", "")


def test_workspace_english_schema():
    """Test that English workspace uses English descriptions."""
    workspace = Workspace(root_path="./test", language="en")
    assert workspace.language == "en"
    agent_node = next((n for n in workspace.directories if n.get("name") == "AGENT.md"), None)
    assert agent_node is not None
    assert "智能体" not in agent_node.get("description", "")
    assert "Basic" in agent_node.get("description", "")


def test_workspace_english_default_content():
    """Test that English workspace has English default content."""
    workspace = Workspace(root_path="./test", language="en")
    agent_node = next((n for n in workspace.directories if n.get("name") == "AGENT.md"), None)
    assert agent_node is not None
    content = agent_node.get("default_content", "")
    assert "This folder is home" in content


def test_workspace_chinese_default_content():
    """Test that Chinese workspace has Chinese default content."""
    workspace = Workspace(root_path="./test", language="cn")
    agent_node = next((n for n in workspace.directories if n.get("name") == "AGENT.md"), None)
    assert agent_node is not None
    content = agent_node.get("default_content", "")
    assert "智能体" in content


def test_get_workspace_schema_returns_correct_language():
    """Test get_workspace_schema returns correct schema based on language."""
    from openjiuwen.deepagents.workspace.workspace import get_workspace_schema

    schema_cn = get_workspace_schema("cn")
    schema_en = get_workspace_schema("en")

    cn_agent = next((n for n in schema_cn if n.get("name") == "AGENT.md"), None)
    en_agent = next((n for n in schema_en if n.get("name") == "AGENT.md"), None)

    assert cn_agent is not None
    assert en_agent is not None
    assert cn_agent.get("description") != en_agent.get("description")
    assert "基础配置和能力" in cn_agent.get("description")
    assert "Basic" in en_agent.get("description")


def test_get_default_directory_with_language():
    """Test Workspace.get_default_directory with language parameter."""
    schema_cn = Workspace.get_default_directory(language="cn")
    schema_en = Workspace.get_default_directory(language="en")

    cn_agent = next((n for n in schema_cn if n.get("name") == "AGENT.md"), None)
    en_agent = next((n for n in schema_en if n.get("name") == "AGENT.md"), None)

    assert cn_agent is not None
    assert en_agent is not None
    assert cn_agent.get("description") != en_agent.get("description")


def test_workspace_instance_independent_schemas():
    """Test that different language workspaces are independent."""
    workspace_cn = Workspace(root_path="./test", language="cn")
    workspace_en = Workspace(root_path="./test", language="en")

    cn_agent = next((n for n in workspace_cn.directories if n.get("name") == "AGENT.md"), None)
    en_agent = next((n for n in workspace_en.directories if n.get("name") == "AGENT.md"), None)

    assert cn_agent is not None
    assert en_agent is not None
    assert cn_agent.get("description") != en_agent.get("description")


# =============================================================================
# DirectoryBuilder Tests - Directory Creation
# =============================================================================

@pytest.mark.asyncio
async def test_directory_builder_creates_directories_with_markers(tmp_path: Path):
    """DirectoryBuilder should create directories with .workspace files."""
    sys_operation = _make_sys_operation(tmp_path)
    from openjiuwen.deepagents.workspace.directory_builder import DirectoryBuilder

    builder = DirectoryBuilder(sys_operation=sys_operation)

    directories = [
        {"name": "agent", "path": "agent", "description": "Agent dir", "children": []},
        {"name": "user", "path": "user", "description": "User dir", "children": []},
    ]
    await builder.build(directories)

    assert (tmp_path / "agent" / ".workspace").exists()
    assert (tmp_path / "user" / ".workspace").exists()


@pytest.mark.asyncio
async def test_directory_builder_creates_nested_directories(tmp_path: Path):
    """DirectoryBuilder should create nested directory structures."""
    sys_operation = _make_sys_operation(tmp_path)
    from openjiuwen.deepagents.workspace.directory_builder import DirectoryBuilder

    builder = DirectoryBuilder(sys_operation=sys_operation)

    directories = [
        {
            "name": "project",
            "path": "project",
            "description": "Project",
            "children": [
                {"name": "src", "path": "src", "description": "Source", "children": []},
                {"name": "tests", "path": "tests", "description": "Tests", "children": []},
            ],
        }
    ]
    await builder.build(directories)

    assert (tmp_path / "project" / ".workspace").exists()
    assert (tmp_path / "project" / "src" / ".workspace").exists()
    assert (tmp_path / "project" / "tests" / ".workspace").exists()


@pytest.mark.asyncio
async def test_directory_builder_reuses_cached_directories_across_builds(tmp_path: Path):
    """DirectoryBuilder should track created directories and not duplicate."""
    sys_operation = _make_sys_operation(tmp_path)
    builder = _TrackingDirectoryBuilder(sys_operation=sys_operation, root_path=str(tmp_path))

    directories = [
        {"name": "agent", "path": "agent", "description": "Agent", "children": []},
        {"name": "user", "path": "user", "description": "User", "children": []},
    ]
    await builder.build(directories)

    assert len(builder.create_calls) == 2
    assert "agent" in builder.create_calls
    assert "user" in builder.create_calls


# =============================================================================
# DeepAgent _init_workspace Tests - Directory Creation Integration
# =============================================================================

@pytest.mark.asyncio
async def test_init_workspace_creates_directories(tmp_path: Path):
    """Test that _init_workspace creates the expected directory structure."""
    sys_op = _make_sys_operation(tmp_path)
    card = AgentCard(name="test", description="test")
    workspace = Workspace(root_path=str(tmp_path))
    config = DeepAgentConfig(
        model=_DummyModel(),
        workspace=workspace,
        sys_operation=sys_op,
        auto_create_workspace=True,
        enable_task_loop=False,
    )
    agent = DeepAgent(card)
    agent.configure(config)
    await agent.init_workspace()
    workspace_root = tmp_path / f"{card.id}_workspace"

    expected_files = ["AGENT.md", "SOUL.md", "HEARTBEAT.md", "IDENTITY.md"]
    for file_name in expected_files:
        file_path = workspace_root / file_name
        assert file_path.exists(), f"Missing file: {file_name}"

    assert (workspace_root / "memory" / "MEMORY.md").exists()
    assert (workspace_root / "memory" / "daily_memory" / ".workspace").exists()


@pytest.mark.asyncio
async def test_init_workspace_with_custom_directories(tmp_path: Path):
    """Test _init_workspace with custom directory structure."""
    sys_op = _make_sys_operation(tmp_path)

    custom_dirs = [
        {"name": "project", "path": "project", "description": "Project", "children": [
            {"name": "src", "path": "src", "description": "Source", "children": []},
        ]},
    ]
    workspace = Workspace(root_path=str(tmp_path), directories=custom_dirs)
    config = DeepAgentConfig(
        model=_DummyModel(),
        workspace=workspace,
        sys_operation=sys_op,
        auto_create_workspace=True,
        enable_task_loop=False,
    )
    card = AgentCard(name="test", description="test")
    agent = DeepAgent(card)
    agent.configure(config)
    workspace_root = tmp_path / f"{card.id}_workspace"

    await agent.init_workspace()

    assert (workspace_root / "project" / ".workspace").exists()
    assert (workspace_root / "project" / "src" / ".workspace").exists()


@pytest.mark.asyncio
async def test_ensure_initialized_skips_when_already_initialized(tmp_path: Path):
    """Test that _ensure_initialized skips workspace init if already done."""
    sys_op = _make_sys_operation(tmp_path)

    workspace = Workspace(root_path=str(tmp_path))
    config = DeepAgentConfig(
        model=_DummyModel(),
        workspace=workspace,
        sys_operation=sys_op,
        auto_create_workspace=True,
        enable_task_loop=False,
    )

    agent = DeepAgent(AgentCard(name="test", description="test"))
    agent.configure(config)

    await agent.ensure_initialized()
    first_marker_exists = (tmp_path / "agent" / ".workspace").exists()

    await agent.ensure_initialized()
    second_marker_exists = (tmp_path / "agent" / ".workspace").exists()

    assert first_marker_exists == second_marker_exists


@pytest.mark.asyncio
async def test_ensure_initialized_skips_when_disabled(tmp_path: Path):
    """Test that workspace init is skipped when auto_create_workspace=False."""
    sys_op = _make_sys_operation(tmp_path)

    workspace = Workspace(root_path=str(tmp_path))
    config = DeepAgentConfig(
        model=_DummyModel(),
        workspace=workspace,
        sys_operation=sys_op,
        auto_create_workspace=False,
        enable_task_loop=False,
    )

    agent = DeepAgent(AgentCard(name="test", description="test"))
    agent.configure(config)

    await agent.ensure_initialized()

    assert not (tmp_path / "agent").exists()


@pytest.mark.asyncio
async def test_ensure_initialized_skips_without_sys_operation(tmp_path: Path):
    """Test that workspace init is skipped when sys_operation is None."""
    workspace = Workspace(root_path=str(tmp_path))
    config = DeepAgentConfig(
        model=_DummyModel(),
        workspace=workspace,
        sys_operation=None,
        auto_create_workspace=True,
        enable_task_loop=False,
    )

    agent = DeepAgent(AgentCard(name="test", description="test"))
    agent.configure(config)

    await agent.ensure_initialized()

    assert getattr(agent, '_workspace_initialized') is False


# =============================================================================
# DeepAgentConfig Workspace Fields Tests
# =============================================================================

def test_config_default_auto_create_workspace():
    """Test that auto_create_workspace defaults to True."""
    config = DeepAgentConfig()
    assert config.auto_create_workspace is True


# =============================================================================
# Integration Test - Full Workspace Flow (Directory Creation)
# =============================================================================

@pytest.mark.asyncio
async def test_full_workspace_flow_create_only(tmp_path: Path):
    """Test complete flow: _init_workspace creates dirs."""
    sys_op = _make_sys_operation(tmp_path)

    custom_dirs = [
        {"name": "myapp", "path": "myapp", "description": "My application", "children": [
            {"name": "backend", "path": "backend", "description": "Backend code", "children": []},
            {"name": "frontend", "path": "frontend", "description": "Frontend code", "children": []},
        ]},
    ]
    workspace = Workspace(root_path=str(tmp_path), directories=custom_dirs)
    config = DeepAgentConfig(
        model=_DummyModel(),
        workspace=workspace,
        sys_operation=sys_op,
        auto_create_workspace=True,
        enable_task_loop=False,
    )

    card = AgentCard(name="test", description="test")
    agent = DeepAgent(card)
    agent.configure(config)
    await agent.init_workspace()
    workspace_root = tmp_path / f"{card.id}_workspace"

    assert (workspace_root / "myapp" / ".workspace").exists()
    assert (workspace_root / "myapp" / "backend" / ".workspace").exists()
    assert (workspace_root / "myapp" / "frontend" / ".workspace").exists()


@pytest.mark.asyncio
async def test_deep_agent_invoke_triggers_workspace_init(tmp_path: Path):
    """Test that invoking DeepAgent triggers _ensure_initialized."""
    sys_op = _make_sys_operation(tmp_path)

    workspace = Workspace(root_path=str(tmp_path))
    config = DeepAgentConfig(
        model=_DummyModel(content="test response"),
        workspace=workspace,
        sys_operation=sys_op,
        auto_create_workspace=True,
        enable_task_loop=False,
    )

    card = AgentCard(name="test", description="test")
    agent = DeepAgent(card)
    agent.configure(config)

    await agent.invoke(inputs="test query")
    workspace_root = tmp_path / f"{card.id}_workspace"

    expected_dirs = ["memory", "todo", "messages", "skills", "agents"]
    for dir_name in expected_dirs:
        assert (workspace_root / dir_name / ".workspace").exists()
    expected_files = ["AGENT.md", "SOUL.md", "HEARTBEAT.md", "IDENTITY.md", "USER.md"]
    for file_name in expected_files:
        assert (workspace_root / file_name).exists()


@pytest.mark.asyncio
async def test_workspace_agent_id_naming(tmp_path: Path):
    """Test that workspace root is named as {agent_id}_workspace/."""
    sys_op = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    config = DeepAgentConfig(
        model=_DummyModel(),
        workspace=workspace,
        sys_operation=sys_op,
        auto_create_workspace=True,
        enable_task_loop=False,
    )
    card = AgentCard(name="test", description="test")
    agent = DeepAgent(card)
    agent.configure(config)
    await agent.ensure_initialized()

    workspace_root = tmp_path / f"{card.id}_workspace"
    assert workspace_root.exists(), f"Missing workspace root: {workspace_root}"

    expected_dirs = ["memory", "todo", "messages", "skills", "agents"]
    for dir_name in expected_dirs:
        marker_path = workspace_root / dir_name / ".workspace"
        assert marker_path.exists(), f"Missing marker in workspace: {dir_name}"


@pytest.mark.asyncio
async def test_workspace_creates_files_not_directories(tmp_path: Path):
    """Test that files (e.g. AGENT.md) are created as files, not directories."""
    sys_op = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    config = DeepAgentConfig(
        model=_DummyModel(),
        workspace=workspace,
        sys_operation=sys_op,
        auto_create_workspace=True,
        enable_task_loop=False,
    )
    card = AgentCard(name="test", description="test")
    agent = DeepAgent(card)
    agent.configure(config)
    await agent.init_workspace()

    workspace_root = tmp_path / f"{card.id}_workspace"
    md_files = ["AGENT.md", "SOUL.md", "HEARTBEAT.md", "IDENTITY.md", "memory/MEMORY.md"]
    for file_path in md_files:
        full_path = workspace_root / file_path
        assert full_path.exists(), f"Missing file: {file_path}"
        assert full_path.is_file(), f"Should be a file, not directory: {file_path}"

    for file_path in md_files:
        marker_path = workspace_root / file_path / ".workspace"
        assert not marker_path.exists(), f"Should not have marker (file not dir): {file_path}"


@pytest.mark.asyncio
async def test_workspace_memory_subdirectory_structure(tmp_path: Path):
    """Test that memory/ directory has correct subdirectory structure."""
    sys_op = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    config = DeepAgentConfig(
        model=_DummyModel(),
        workspace=workspace,
        sys_operation=sys_op,
        auto_create_workspace=True,
        enable_task_loop=False,
    )
    card = AgentCard(name="test", description="test")
    agent = DeepAgent(card)
    agent.configure(config)
    await agent.init_workspace()
    workspace_root = tmp_path / f"{card.id}_workspace"

    assert (workspace_root / "memory").exists()
    assert (workspace_root / "memory" / ".workspace").exists()

    assert (workspace_root / "memory" / "MEMORY.md").exists()
    assert (workspace_root / "memory" / "MEMORY.md").is_file()

    assert (workspace_root / "memory" / "daily_memory").exists()
    assert (workspace_root / "memory" / "daily_memory" / ".workspace").exists()


@pytest.mark.asyncio
async def test_workspace_todo_session_isolated_structure(tmp_path: Path):
    """Test that todo/ and messages/ have session-isolated structure."""
    sys_op = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    config = DeepAgentConfig(
        model=_DummyModel(),
        workspace=workspace,
        sys_operation=sys_op,
        auto_create_workspace=True,
        enable_task_loop=False,
    )
    card = AgentCard(name="test", description="test")
    agent = DeepAgent(card)
    agent.configure(config)
    await agent.init_workspace()

    workspace_root = tmp_path / f"{card.id}_workspace"

    assert (workspace_root / "todo").exists()
    assert (workspace_root / "todo" / ".workspace").exists()

    assert (workspace_root / "messages").exists()
    assert (workspace_root / "messages" / ".workspace").exists()

    assert (workspace_root / "skills").exists()
    assert (workspace_root / "skills" / ".workspace").exists()

    assert (workspace_root / "agents").exists()
    assert (workspace_root / "agents" / ".workspace").exists()


# =============================================================================
# Default Content Tests
# =============================================================================


@pytest.mark.asyncio
async def test_init_workspace_writes_default_content_to_md_files(tmp_path: Path):
    """Test that init_workspace writes default content to md files, not empty files."""
    sys_op = _make_sys_operation(tmp_path)
    card = AgentCard(name="test_agent", description="test")
    workspace = Workspace(root_path=str(tmp_path))
    config = DeepAgentConfig(
        model=_DummyModel(),
        workspace=workspace,
        sys_operation=sys_op,
        auto_create_workspace=True,
        enable_task_loop=False,
    )
    agent = DeepAgent(card)
    agent.configure(config)
    await agent.init_workspace()
    workspace_root = tmp_path / f"{card.id}_workspace"

    md_files = ["AGENT.md", "SOUL.md", "HEARTBEAT.md", "IDENTITY.md", "memory/MEMORY.md"]
    for file_path in md_files:
        full_path = workspace_root / file_path
        assert full_path.exists(), f"Missing file: {file_path}"
        content = full_path.read_text(encoding="utf-8")
        assert len(content) > 0, f"File {file_path} should not be empty"
        agent_md = workspace_root / "AGENT.md"
        content = agent_md.read_text(encoding="utf-8")
        assert "智能体" in content
        assert "首次运行" in content or "会话启动" in content
        assert "Agent" not in content.split("智能体")[0] or "Agent" not in content


@pytest.mark.asyncio
async def test_directory_builder_with_default_content(tmp_path: Path):
    """Test DirectoryBuilder writes content when default_content is provided."""
    sys_operation = _make_sys_operation(tmp_path)
    from openjiuwen.deepagents.workspace.directory_builder import DirectoryBuilder

    builder = DirectoryBuilder(
        sys_operation=sys_operation,
        root_path=str(tmp_path),
    )

    directories = [
        {
            "name": "test",
            "path": "test.md",
            "is_file": True,
            "default_content": "# Test\nHello World",
        }
    ]
    await builder.build(directories)

    test_file = tmp_path / "test.md"
    assert test_file.exists()
    content = test_file.read_text(encoding="utf-8")
    assert "Hello World" in content


@pytest.mark.asyncio
async def test_directory_builder_without_default_content_creates_empty_file(tmp_path: Path):
    """Test DirectoryBuilder creates empty file when default_content is not provided."""
    sys_operation = _make_sys_operation(tmp_path)
    from openjiuwen.deepagents.workspace.directory_builder import DirectoryBuilder

    builder = DirectoryBuilder(sys_operation=sys_operation, root_path=str(tmp_path))

    directories = [
        {
            "name": "empty",
            "path": "empty.md",
            "is_file": True,
        }
    ]
    await builder.build(directories)

    empty_file = tmp_path / "empty.md"
    assert empty_file.exists()
    content = empty_file.read_text(encoding="utf-8")
    assert content == "\n"


@pytest.mark.asyncio
async def test_init_workspace_english_soul_md_has_english_content(tmp_path: Path):
    """Test that English workspace SOUL.md contains English default content."""
    sys_op = _make_sys_operation(tmp_path)
    card = AgentCard(name="test_en_soul", description="test")
    workspace = Workspace(root_path=str(tmp_path), language="en")
    config = DeepAgentConfig(
        model=_DummyModel(),
        workspace=workspace,
        sys_operation=sys_op,
        auto_create_workspace=True,
        enable_task_loop=False,
    )
    agent = DeepAgent(card)
    agent.configure(config)
    await agent.init_workspace()
    workspace_root = tmp_path / f"{card.id}_workspace"

    soul_md = workspace_root / "SOUL.md"
    assert soul_md.exists()
    content = soul_md.read_text(encoding="utf-8")
    assert "SOUL" in content
    assert "genuinely helpful" in content or "Have opinions" in content
    assert "灵魂" not in content


# =============================================================================
# get_node_path Tests
# =============================================================================


def test_get_node_path_with_string_name():
    """Test get_node_path returns correct absolute path using string name."""
    workspace = Workspace(root_path="/workspace")

    # After configure(), root_path is pre-calculated to include agent_id
    workspace.root_path = "/workspace/test_agent_workspace"

    # Test top-level directories
    memory_path = workspace.get_node_path("memory")
    assert memory_path == Path("/workspace/test_agent_workspace/memory")

    todo_path = workspace.get_node_path("todo")
    assert todo_path == Path("/workspace/test_agent_workspace/todo")

    skills_path = workspace.get_node_path("skills")
    assert skills_path == Path("/workspace/test_agent_workspace/skills")

    # Test top-level files
    agent_md_path = workspace.get_node_path("AGENT.md")
    assert agent_md_path == Path("/workspace/test_agent_workspace/AGENT.md")

    soul_md_path = workspace.get_node_path("SOUL.md")
    assert soul_md_path == Path("/workspace/test_agent_workspace/SOUL.md")


def test_get_node_path_with_workspace_node_enum():
    """Test get_node_path works with WorkspaceNode enum values."""
    from openjiuwen.deepagents.workspace.workspace import WorkspaceNode

    workspace = Workspace(root_path="/workspace")
    workspace.root_path = "/workspace/test_agent_workspace"

    assert workspace.get_node_path(WorkspaceNode.MEMORY) == Path(
        "/workspace/test_agent_workspace/memory"
    )
    assert workspace.get_node_path(WorkspaceNode.TODO) == Path(
        "/workspace/test_agent_workspace/todo"
    )
    assert workspace.get_node_path(WorkspaceNode.SKILLS) == Path(
        "/workspace/test_agent_workspace/skills"
    )
    assert workspace.get_node_path(WorkspaceNode.AGENT_MD) == Path(
        "/workspace/test_agent_workspace/AGENT.md"
    )


def test_get_node_path_returns_none_for_nested_nodes():
    """Test get_node_path returns None for nested nodes (not supported)."""
    workspace = Workspace(root_path="/workspace")
    workspace.root_path = "/workspace/test_agent_workspace"

    # Nested nodes (children of top-level nodes) are not supported
    memory_md_path = workspace.get_node_path("MEMORY.md")
    assert memory_md_path is None

    daily_memory_path = workspace.get_node_path("daily_memory")
    assert daily_memory_path is None


def test_get_node_path_returns_none_for_unknown_node():
    """Test get_node_path returns None for unknown node names."""
    workspace = Workspace(root_path="/workspace")
    workspace.root_path = "/workspace/test_agent_workspace"

    unknown_path = workspace.get_node_path("unknown_directory")
    assert unknown_path is None


def test_get_node_path_after_deep_agent_configure(tmp_path: Path):
    """Test get_node_path returns correct path after DeepAgent.configure()."""
    sys_op = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    config = DeepAgentConfig(
        model=_DummyModel(),
        workspace=workspace,
        sys_operation=sys_op,
        auto_create_workspace=True,
        enable_task_loop=False,
    )
    card = AgentCard(name="my_test_agent", description="test")
    agent = DeepAgent(card)
    agent.configure(config)

    # After configure(), root_path should be pre-calculated
    expected_root = tmp_path / f"{card.id}_workspace"

    memory_path = agent.deep_config.workspace.get_node_path("memory")
    assert memory_path == expected_root / "memory"

    agent_md_path = agent.deep_config.workspace.get_node_path("AGENT.md")
    assert agent_md_path == expected_root / "AGENT.md"