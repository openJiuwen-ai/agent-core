# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime extension loader tests."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
import uuid

import pytest

from openjiuwen.core.single_agent.schema.agent_card import (
    AgentCard,
)
from openjiuwen.auto_harness.infra.runtime_extension_loader import (
    load_runtime_rails,
    load_runtime_tools,
)
from openjiuwen.auto_harness.infra.runtime_manifest import (
    load_runtime_manifest,
)
from openjiuwen.auto_harness.schema import (
    RuntimeExtensionArtifact,
)
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import AgentError
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

    record = await agent.load_expert_harness(
        artifact.config_path
    )

    assert any(
        ref.kind.value == "skill"
        for ref in record.refs
    )
    skill_rail = next(
        rail
        for rail in agent._registered_rails
        if isinstance(rail, SkillUseRail)
    )
    skill_dirs = list(skill_rail.skills_dir)
    assert Path(skill_dirs[0]).as_posix().endswith(
        "demo_ext/skills"
    )
    assert skill_rail.skills[0].name == "shared_skill"
    assert Path(skill_rail.skills[0].directory).as_posix().endswith(
        "demo_ext/skills/shared_skill"
    )


@pytest.mark.asyncio
async def test_load_expert_harness_raises_for_missing_file(
    tmp_path: Path,
):
    """load_expert_harness must raise AgentError for a missing manifest."""
    agent = DeepAgent(
        AgentCard(name="deep", description="test")
    ).configure(
        DeepAgentConfig(enable_task_loop=False)
    )

    missing_config = tmp_path / "missing" / "harness_config.yaml"
    with pytest.raises(AgentError, match="not found") as exc_info:
        await agent.load_expert_harness(str(missing_config))

    err = exc_info.value
    message = str(err)
    assert err.status == StatusCode.DEEPAGENT_LOAD_EXPERT_HARNESS_ERROR
    assert isinstance(err.__cause__, FileNotFoundError)
    assert "harness_config.yaml" in message
    assert str(missing_config) in message


@pytest.mark.asyncio
async def test_unload_expert_harness_removes_skill_dirs_from_shared_rail(
    tmp_path: Path,
):
    """unload_expert_harness drops the runtime skill dir from a shared rail."""
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

    # Load through the canonical ExpertHarness interface (merges skill dirs).
    record = await agent.load_expert_harness(artifact.config_path)
    assert any(ref.kind.value == "skill" for ref in record.refs)

    skill_rail = next(
        rail
        for rail in agent._registered_rails
        if isinstance(rail, SkillUseRail)
    )
    skill_dirs = list(skill_rail.skills_dir)
    assert len(skill_dirs) >= 2  # old_root + runtime skill dir

    # Unload via the load record (no fragile re-parse of the manifest).
    unloaded = await agent.unload_expert_harness(record)
    assert any(item.startswith("skill:") for item in unloaded)

    # Verify runtime skill dir is removed but old_root remains
    skill_dirs_after = list(skill_rail.skills_dir)
    assert str(old_root) in skill_dirs_after
    assert not any(
        Path(d).as_posix().endswith("demo_ext/skills")
        for d in skill_dirs_after
    )


# ---------------------------------------------------------------------------
# Regression: the new ``load_expert_harness`` interface must keep the runtime
# extension support that the legacy ``load_harness_config`` provided. The
# critical contract is intra-package relative imports (``from .helper import
# VALUE``) inside a runtime extension whose modules are declared under the
# ``openjiuwen.extensions.harness.<ext>`` namespace.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_expert_harness_loads_runtime_extension(
    tmp_path: Path,
):
    tool_id = f"demo_tool_{uuid.uuid4().hex[:8]}"
    artifact = _write_runtime_extension(
        tmp_path,
        tool_id=tool_id,
        include_skill=True,
        skill_body="expert harness skill body",
    )
    agent = DeepAgent(
        AgentCard(
            id="expert-harness-agent",
            name="deep",
            description="test",
        )
    ).configure(
        DeepAgentConfig(enable_task_loop=False)
    )

    record = await agent.load_expert_harness(artifact.config_path)

    ref_identities = {ref.identity for ref in record.refs}
    assert "DemoRail" in ref_identities
    assert any(ref.kind.value == "tool" for ref in record.refs)

    assert any(
        type(rail).__name__ == "DemoRail"
        for rail in agent._registered_rails
    )

    demo_cards = [
        card
        for card in agent.ability_manager.list()
        if getattr(card, "name", None) == "demo_tool"
    ]
    assert demo_cards
    # Description == "runtime-ok" proves ``from .helper import VALUE`` resolved,
    # i.e. the extension was loaded under its canonical package name and the
    # intra-package relative import worked.
    assert demo_cards[0].description == "runtime-ok"


@pytest.mark.asyncio
async def test_load_expert_harness_resolves_relative_import(
    tmp_path: Path,
):
    """Directly assert the relative-import path that the legacy loader handled."""
    tool_id = f"demo_tool_{uuid.uuid4().hex[:8]}"
    artifact = _write_runtime_extension(
        tmp_path,
        tool_id=tool_id,
    )
    agent = DeepAgent(
        AgentCard(
            id="relative-import-agent",
            name="deep",
            description="test",
        )
    ).configure(
        DeepAgentConfig(enable_task_loop=False)
    )

    # Must not raise ImportError("attempted relative import with no known parent package").
    record = await agent.load_expert_harness(artifact.config_path)

    assert record.refs
    tool = next(
        tool
        for tool in agent.deep_config.tools or []
        if getattr(tool, "name", None) == "demo_tool"
    )
    assert tool.description == "runtime-ok"


@pytest.mark.asyncio
async def test_load_expert_harness_ignores_stale_official_extension_modules(
    tmp_path: Path,
):
    """Runtime loads must prefer the current package root over stale official aliases."""
    helper_module_name = "openjiuwen.extensions.harness.demo_ext.tools.helper"
    stale_helper = ModuleType(helper_module_name)
    stale_helper.__file__ = str(tmp_path / "stale" / "helper.py")
    stale_helper.EXTENSION_NAME = "stale-demo-ext"
    previous_helper = sys.modules.get(helper_module_name)
    sys.modules[helper_module_name] = stale_helper

    try:
        artifact = _write_runtime_extension(
            tmp_path,
            tool_id=f"demo_tool_{uuid.uuid4().hex[:8]}",
        )
        agent = DeepAgent(
            AgentCard(
                id="stale-official-module-agent",
                name="deep",
                description="test",
            )
        ).configure(
            DeepAgentConfig(enable_task_loop=False)
        )

        record = await agent.load_expert_harness(artifact.config_path)

        assert record.refs
        tool = next(
            tool
            for tool in agent.deep_config.tools or []
            if getattr(tool, "name", None) == "demo_tool"
        )
        assert tool.description == "runtime-ok"
        assert sys.modules.get(helper_module_name) is stale_helper
    finally:
        if previous_helper is None:
            sys.modules.pop(helper_module_name, None)
        else:
            sys.modules[helper_module_name] = previous_helper


@pytest.mark.asyncio
async def test_unload_expert_harness_reverts_runtime_extension(
    tmp_path: Path,
):
    tool_id = f"demo_tool_{uuid.uuid4().hex[:8]}"
    artifact = _write_runtime_extension(
        tmp_path,
        tool_id=tool_id,
    )
    agent = DeepAgent(
        AgentCard(
            id="expert-harness-unload-agent",
            name="deep",
            description="test",
        )
    ).configure(
        DeepAgentConfig(enable_task_loop=False)
    )

    record = await agent.load_expert_harness(artifact.config_path)
    assert any(
        type(rail).__name__ == "DemoRail"
        for rail in agent._registered_rails
    )

    unloaded = await agent.unload_expert_harness(record)

    assert unloaded
    assert not any(
        type(rail).__name__ == "DemoRail"
        for rail in agent._registered_rails
    )
    assert not any(
        getattr(card, "name", None) == "demo_tool"
        for card in agent.ability_manager.list()
    )
