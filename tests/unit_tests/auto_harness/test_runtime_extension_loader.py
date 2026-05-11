# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime extension loader tests."""

from __future__ import annotations

from pathlib import Path
import uuid

import pytest

from openjiuwen.core.single_agent.schema.agent_card import (
    AgentCard,
)
from openjiuwen.auto_harness.infra.runtime_extension_loader import (
    load_runtime_rails,
    load_runtime_tools,
)
from openjiuwen.auto_harness.schema import (
    RuntimeExtensionArtifact,
)
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.rails import SkillUseRail
from openjiuwen.harness.schema.config import DeepAgentConfig


def _write_runtime_extension(
    base_dir: Path,
    *,
    extension_name: str = "demo_ext",
    tool_id: str,
    include_skill: bool = False,
    skill_body: str = "runtime skill",
) -> RuntimeExtensionArtifact:
    root = base_dir / extension_name
    (root / "tools").mkdir(parents=True)
    (root / "rails").mkdir(parents=True)
    if include_skill:
        (root / "skills" / "shared_skill").mkdir(
            parents=True
        )
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "tools" / "__init__.py").write_text("", encoding="utf-8")
    (root / "rails" / "__init__.py").write_text("", encoding="utf-8")
    if include_skill:
        (
            root
            / "skills"
            / "shared_skill"
            / "SKILL.md"
        ).write_text(
            "---\n"
            "name: shared_skill\n"
            "description: shared runtime skill\n"
            "---\n\n"
            f"{skill_body}\n",
            encoding="utf-8",
        )
    (root / "tools" / "helper.py").write_text(
        'VALUE = "runtime-ok"\n',
        encoding="utf-8",
    )
    (root / "tools" / "demo_tool.py").write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "from typing import Any, AsyncIterator, Dict",
                "from .helper import VALUE",
                "from openjiuwen.core.foundation.tool import Tool, ToolCard",
                "",
                "class DemoTool(Tool):",
                "    def __init__(self) -> None:",
                f"        super().__init__(ToolCard(id='{tool_id}', name='demo_tool', description=VALUE))",
                "",
                "    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:",
                "        _ = kwargs",
                "        return {'value': VALUE, 'inputs': inputs}",
                "",
                "    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Dict[str, Any]]:",
                "        yield await self.invoke(inputs, **kwargs)",
            ]
        ),
        encoding="utf-8",
    )
    (root / "rails" / "demo_rail.py").write_text(
        "\n".join(
            [
                "from openjiuwen.harness.rails.base import DeepAgentRail",
                "",
                "class DemoRail(DeepAgentRail):",
                "    pass",
            ]
        ),
        encoding="utf-8",
    )
    (root / "harness_config.yaml").write_text(
        "\n".join(
            [
                "schema_version: harness_config.v0.1",
                f"name: {extension_name}",
                "resources:",
                "  rails:",
                "    - type: package",
                f"      module: openjiuwen.extensions.harness.{extension_name}.rails.demo_rail",
                "      class: DemoRail",
                "  tools:",
                "    - type: package",
                f"      module: openjiuwen.extensions.harness.{extension_name}.tools.demo_tool",
                "      class: DemoTool",
                *(
                    [
                        "  skills:",
                        "    dirs:",
                        "      - skills/",
                    ]
                    if include_skill
                    else []
                ),
            ]
        ),
        encoding="utf-8",
    )
    return RuntimeExtensionArtifact(
        extension_name=extension_name,
        runtime_path=str(root),
        config_path=str(root / "harness_config.yaml"),
    )


def test_load_runtime_resources_from_manifest(tmp_path: Path):
    artifact = _write_runtime_extension(
        tmp_path,
        tool_id=f"demo_tool_{uuid.uuid4().hex[:8]}",
    )

    rails = load_runtime_rails(
        artifact,
        session_id="session123",
    )
    tools = load_runtime_tools(
        artifact,
        session_id="session123",
    )

    assert [rail.__name__ for rail in rails] == ["DemoRail"]
    assert [tool.__name__ for tool in tools] == ["DemoTool"]
    assert tools[0]().card.description == "runtime-ok"


@pytest.mark.asyncio
async def test_deep_agent_loads_runtime_extension_config(
    tmp_path: Path,
):
    tool_id = f"demo_tool_{uuid.uuid4().hex[:8]}"
    artifact = _write_runtime_extension(
        tmp_path,
        tool_id=tool_id,
    )
    agent = DeepAgent(
        AgentCard(name="deep", description="test")
    ).configure(
        DeepAgentConfig(enable_task_loop=False)
    )

    loaded = await agent.load_harness_config(
        artifact.config_path
    )

    assert "rail:DemoRail" in loaded
    assert "tool:DemoTool" in loaded
    assert any(
        type(rail).__name__ == "DemoRail"
        for rail in agent._registered_rails
    )
    assert any(
        card.name == "demo_tool"
        for card in agent.ability_manager.list()
    )


@pytest.mark.asyncio
async def test_runtime_extension_skills_are_refreshed_and_preferred(
    tmp_path: Path,
):
    old_root = tmp_path / "old_skills"
    (old_root / "shared_skill").mkdir(parents=True)
    (old_root / "shared_skill" / "SKILL.md").write_text(
        "---\n"
        "name: shared_skill\n"
        "description: old skill\n"
        "---\n\n"
        "old skill body\n",
        encoding="utf-8",
    )
    tool_id = f"demo_tool_{uuid.uuid4().hex[:8]}"
    artifact = _write_runtime_extension(
        tmp_path / "runtime",
        tool_id=tool_id,
        include_skill=True,
        skill_body="new runtime skill body",
    )
    agent = DeepAgent(
        AgentCard(
            id="runtime-skill-agent",
            name="deep",
            description="test",
        )
    ).configure(
        DeepAgentConfig(enable_task_loop=False)
    )
    old_rail = SkillUseRail(
        skills_dir=str(old_root),
        skill_mode="all",
    )
    await agent.register_rail(old_rail)
    await old_rail.reload_skills()

    loaded = await agent.load_harness_config(
        artifact.config_path
    )

    assert any(
        item.startswith("skill_dir:")
        for item in loaded
    )
    skill_rail = next(
        rail
        for rail in agent._registered_rails
        if isinstance(rail, SkillUseRail)
    )
    skill_dirs = list(skill_rail.skills_dir)
    assert skill_dirs[0].endswith("demo_ext/skills")
    assert skill_rail.skills[0].name == "shared_skill"
    assert str(skill_rail.skills[0].directory).endswith(
        "demo_ext/skills/shared_skill"
    )


@pytest.mark.asyncio
async def test_unload_harness_config_removes_loaded_resources(
    tmp_path: Path,
):
    """Test that unload_harness_config removes rails, tools, and skills."""
    tool_id = f"demo_tool_{uuid.uuid4().hex[:8]}"
    artifact = _write_runtime_extension(
        tmp_path,
        tool_id=tool_id,
        include_skill=True,
        skill_body="test skill body",
    )
    agent = DeepAgent(
        AgentCard(
            id="unload-test-agent",
            name="deep",
            description="test",
        )
    ).configure(
        DeepAgentConfig(enable_task_loop=False)
    )

    # Initial state should be empty
    assert len(agent._registered_rails) == 0
    assert len(agent.ability_manager.list()) == 0

    # Load the config
    loaded = await agent.load_harness_config(artifact.config_path)
    assert "rail:DemoRail" in loaded
    assert "tool:DemoTool" in loaded
    assert any(item.startswith("skill_dir:") for item in loaded)

    # Verify resources are loaded
    rail_count_after_load = len(agent._registered_rails)
    assert rail_count_after_load > 0
    assert any(
        type(rail).__name__ == "DemoRail"
        for rail in agent._registered_rails
    )
    assert any(
        card.name == "demo_tool"
        for card in agent.ability_manager.list()
    )

    # Unload the config (re-parses the file to determine what to remove)
    unloaded = await agent.unload_harness_config(artifact.config_path)
    assert "rail:DemoRail" in unloaded
    assert any(item.startswith("tool_id:") for item in unloaded)
    assert any(item.startswith("tool:") for item in unloaded)
    assert any(item.startswith("skill_dir:") for item in unloaded)

    # Verify resources are removed
    assert not any(
        type(rail).__name__ == "DemoRail"
        for rail in agent._registered_rails
    )
    assert not any(
        card.name == "demo_tool"
        for card in agent.ability_manager.list()
    )


@pytest.mark.asyncio
async def test_unload_harness_config_raises_for_missing_file(
    tmp_path: Path,
):
    """Test that unload_harness_config raises FileNotFoundError for missing file."""
    agent = DeepAgent(
        AgentCard(name="deep", description="test")
    ).configure(
        DeepAgentConfig(enable_task_loop=False)
    )

    missing_config = tmp_path / "missing" / "harness_config.yaml"
    with pytest.raises(FileNotFoundError, match="not found"):
        await agent.unload_harness_config(str(missing_config))


@pytest.mark.asyncio
async def test_unload_harness_config_removes_skill_dirs_from_shared_rail(
    tmp_path: Path,
):
    """Test that unload removes skill dirs from shared SkillUseRail."""
    # Create existing skill rail with one skill dir
    old_root = tmp_path / "old_skills"
    (old_root / "shared_skill").mkdir(parents=True)
    (old_root / "shared_skill" / "SKILL.md").write_text(
        "---\n"
        "name: shared_skill\n"
        "description: old skill\n"
        "---\n\n"
        "old skill body\n",
        encoding="utf-8",
    )

    tool_id = f"demo_tool_{uuid.uuid4().hex[:8]}"
    artifact = _write_runtime_extension(
        tmp_path / "runtime",
        tool_id=tool_id,
        include_skill=True,
        skill_body="new runtime skill body",
    )

    agent = DeepAgent(
        AgentCard(
            id="skill-unload-agent",
            name="deep",
            description="test",
        )
    ).configure(
        DeepAgentConfig(enable_task_loop=False)
    )

    # Add existing skill rail
    old_rail = SkillUseRail(
        skills_dir=str(old_root),
        skill_mode="all",
    )
    await agent.register_rail(old_rail)
    await old_rail.reload_skills()

    # Load the config (will merge skill dirs)
    loaded = await agent.load_harness_config(artifact.config_path)
    assert any(item.startswith("skill_dir:") for item in loaded)

    skill_rail = next(
        rail
        for rail in agent._registered_rails
        if isinstance(rail, SkillUseRail)
    )
    skill_dirs = list(skill_rail.skills_dir)
    assert len(skill_dirs) >= 2  # old_root + runtime skill dir

    # Unload the config
    unloaded = await agent.unload_harness_config(artifact.config_path)
    assert any(item.startswith("skill_dir:") for item in unloaded)

    # Verify runtime skill dir is removed but old_root remains
    skill_dirs_after = list(skill_rail.skills_dir)
    assert str(old_root) in skill_dirs_after
    assert not any(
        d.endswith("demo_ext/skills")
        for d in skill_dirs_after
    )
