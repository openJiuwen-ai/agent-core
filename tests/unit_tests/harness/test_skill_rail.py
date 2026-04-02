# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from openjiuwen.core.foundation.llm.model import init_model
from openjiuwen.core.foundation.llm.schema.message import SystemMessage
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import (
    LocalWorkConfig,
    OperationMode,
    SysOperationCard,
)
from openjiuwen.harness import Workspace
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.prompts.builder import PromptSection, SystemPromptBuilder
from openjiuwen.harness.rails.skill_use_rail import SkillUseRail
from openjiuwen.harness.tools.list_skill import ListSkillTool


class _DummyResponse:
    def __init__(self, content: str):
        self.content = content


class _DummyModel:
    def __init__(self, content: str):
        self._content = content
        self.calls: List[Dict[str, Any]] = []

    async def invoke(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return _DummyResponse(self._content)


class _TrackingSkillUseRail(SkillUseRail):
    """Test-only SkillUseRail that records _load_skill calls via subclass override."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.load_calls: List[str] = []

    async def _load_skill(self, skill_dir, update_at):
        self.load_calls.append(skill_dir.name)
        return await super()._load_skill(skill_dir, update_at)


def _write_skill(
    root: Path,
    name: str,
    description: str,
) -> Path:
    """Create a minimal skill directory with SKILL.md."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        f"description: {description}\n"
        "---\n\n"
        f"# {name}\n",
        encoding="utf-8",
    )
    return skill_dir


def _make_sys_operation(tmp_path: Path):
    """Create a local SysOperation for tests."""
    card = SysOperationCard(
        id=f"test_skill_rail_sysop_{tmp_path.name}",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=str(tmp_path)),
    )
    Runner.resource_mgr.add_sys_operation(card)
    return Runner.resource_mgr.get_sys_operation(card.id)


def _make_agent(sys_operation, workspace):
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
        card=AgentCard(name="test_skill_agent", description="test skill agent"),
        system_prompt="You are a test assistant.",
        max_iterations=3,
        enable_task_loop=False,
        workspace=workspace,
        sys_operation=sys_operation
    )


def _sorted_skill_names(skills) -> List[str]:
    return sorted(skill.name for skill in skills)


@pytest.mark.asyncio
async def test_skill_rail_all_mode_loads_skills_on_before_invoke(tmp_path: Path):
    """SkillUseRail should auto-load skills in before_invoke without explicit prepare()."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=True,
    )

    ctx = AgentCallbackContext(
        agent=None,
        inputs=None,
        session=None,
    )

    await skill_rail.before_invoke(ctx)

    assert _sorted_skill_names(skill_rail.skills) == [
        "invoice-parser",
        "xlsx-writer",
    ]
    assert _sorted_skill_names(skill_rail.skills_meta) == [
        "invoice-parser",
        "xlsx-writer",
    ]


@pytest.mark.asyncio
async def test_skill_rail_all_mode_injects_skill_prompt(tmp_path: Path):
    """All mode should add skills section to builder before model call."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=True,
    )
    skill_rail.set_workspace(Workspace(root_path=str(tmp_path)))
    skill_rail.set_sys_operation(sys_operation)

    # Simulate what _create_react_agent does: provide a builder on the rail.
    builder = SystemPromptBuilder()
    builder.add_section(PromptSection(
        name="identity",
        content={"cn": "Base system prompt.", "en": "Base system prompt."},
    ))
    skill_rail.system_prompt_builder = builder

    ctx = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(tools=[]),
        session=None,
    )

    await skill_rail.before_invoke(ctx)
    await skill_rail.before_model_call(ctx)

    # Skills are added to the builder; build() produces the final system prompt.
    content = builder.build()

    assert "Base system prompt." in content
    assert "invoice-parser" in content
    assert "xlsx-writer" in content
    assert "Parse invoice pdf files" in content
    assert "Write xlsx reports" in content


@pytest.mark.asyncio
async def test_skill_rail_filters_enabled_and_disabled_skills(tmp_path: Path):
    """SkillUseRail should respect enabled_skills and disabled_skills."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")
    _write_skill(skills_root, "legacy-skill", "Old skill")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        enabled_skills="invoice-parser,xlsx-writer,legacy-skill",
        disabled_skills="legacy-skill",
        include_tools=True,
    )

    ctx = AgentCallbackContext(
        agent=None,
        inputs=None,
        session=None,
    )

    await skill_rail.before_invoke(ctx)

    assert _sorted_skill_names(skill_rail.skills) == [
        "invoice-parser",
        "xlsx-writer",
    ]


@pytest.mark.asyncio
async def test_skill_rail_register_rail_auto_list_registers_list_skill_tool(tmp_path: Path):
    """auto_list mode should register list_skill tool through agent.register_rail()."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    sys_operation = _make_sys_operation(tmp_path)
    routing_model = _DummyModel(
        content='{"skills": ["invoice-parser"]}'
    )
    agent = _make_agent(sys_operation, skills_root)

    skill_rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="auto_list",
        list_skill_model=routing_model,
        include_tools=True,
    )

    await agent.register_rail(skill_rail)

    ability_names = {
        getattr(item, "name", None)
        for item in agent.ability_manager.list()
        if getattr(item, "name", None)
    }

    assert "read_file" in ability_names
    assert "code" in ability_names
    assert "bash" in ability_names
    assert "list_skill" in ability_names


@pytest.mark.asyncio
async def test_auto_list_prompt_is_injected_without_preselecting_skills(tmp_path: Path):
    """auto_list mode should add guide prompt to builder without pre-expanding skills."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="auto_list",
        include_tools=True,
    )

    # Simulate what _create_react_agent does: provide a builder on the rail.
    builder = SystemPromptBuilder()
    builder.add_section(PromptSection(
        name="identity",
        content={"cn": "Base system prompt.", "en": "Base system prompt."},
    ))
    skill_rail.system_prompt_builder = builder

    ctx = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(tools=[]),
        session=None,
    )

    await skill_rail.before_invoke(ctx)
    await skill_rail.before_model_call(ctx)

    # Skills section is added to the builder; build() produces the final prompt.
    content = builder.build()
    assert "Base system prompt." in content
    assert "list_skill" in content


@pytest.mark.asyncio
async def test_list_skill_tool_reads_latest_skills_from_skill_rail(tmp_path: Path):
    """ListSkillTool should read latest skills via get_skills instead of fixed snapshot."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    sys_operation = _make_sys_operation(tmp_path)
    routing_model = _DummyModel(
        content='{"skills": ["xlsx-writer"]}'
    )
    skill_rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="auto_list",
        list_skill_model=routing_model,
        include_tools=True,
    )

    tool = ListSkillTool(
        get_skills=lambda: skill_rail.skills,
        list_skill_model=routing_model,
    )

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await skill_rail.before_invoke(ctx)

    result = await tool.invoke({"query": "generate xlsx report"})

    assert result.success is True
    assert result.data["mode"] == "filtered"
    assert result.data["selected_skill_names"] == ["xlsx-writer"]
    assert len(result.data["skills"]) == 1
    assert result.data["skills"][0]["name"] == "xlsx-writer"


@pytest.mark.asyncio
async def test_list_skill_tool_returns_all_skills_when_query_empty(tmp_path: Path):
    """ListSkillTool should return all skills when query is empty."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="auto_list",
        include_tools=True,
    )

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await skill_rail.before_invoke(ctx)

    tool = ListSkillTool(
        get_skills=lambda: skill_rail.skills,
        list_skill_model=None,
    )

    result = await tool.invoke({})

    assert result.success is True
    assert result.data["mode"] == "all"
    assert sorted(item["name"] for item in result.data["skills"]) == [
        "invoice-parser",
        "xlsx-writer",
    ]


@pytest.mark.asyncio
async def test_skill_rail_reuses_cached_skills_across_invokes(tmp_path: Path):
    """SkillUseRail should reuse cached skills across invokes when no skill is changed."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = _TrackingSkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=False,
    )
    skill_rail.set_workspace(Workspace(root_path=str(tmp_path)))
    skill_rail.set_sys_operation(sys_operation)

    ctx1 = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=[SystemMessage(content="x")], tools=[]),
        session=None,
    )
    await skill_rail.before_invoke(ctx1)
    assert sorted(skill_rail.load_calls) == ["invoice-parser", "xlsx-writer"]

    skill_rail.load_calls.clear()

    ctx2 = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=[SystemMessage(content="x")], tools=[]),
        session=None,
    )
    await skill_rail.before_invoke(ctx2)
    assert skill_rail.load_calls == []


@pytest.mark.asyncio
async def test_skill_rail_only_loads_new_skill_on_incremental_refresh(tmp_path: Path):
    """SkillUseRail should load only newly added skills on later invokes."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = _TrackingSkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=False,
    )
    skill_rail.set_workspace(Workspace(root_path=str(tmp_path)))
    skill_rail.set_sys_operation(sys_operation)

    ctx1 = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=[SystemMessage(content="x")], tools=[]),
        session=None,
    )
    await skill_rail.before_invoke(ctx1)
    assert skill_rail.load_calls == ["invoice-parser"]

    skill_rail.load_calls.clear()
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    ctx2 = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=[SystemMessage(content="x")], tools=[]),
        session=None,
    )
    await skill_rail.before_invoke(ctx2)
    assert skill_rail.load_calls == ["xlsx-writer"]


@pytest.mark.asyncio
async def test_skill_rail_reload_updated_skill_by_update_at(tmp_path: Path):
    """SkillUseRail should reload only updated skills when SKILL.md update_at changes."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = _TrackingSkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=False,
    )
    skill_rail.set_workspace(Workspace(root_path=str(tmp_path)))
    skill_rail.set_sys_operation(sys_operation)
    ctx1 = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=[SystemMessage(content="x")], tools=[]),
        session=None,
    )
    await skill_rail.before_invoke(ctx1)
    assert sorted(skill_rail.load_calls) == ["invoice-parser", "xlsx-writer"]

    skill_rail.load_calls.clear()

    time.sleep(1.1)
    skill_md = skills_root / "invoice-parser" / "SKILL.md"
    original = skill_md.read_text(encoding="utf-8")
    skill_md.write_text(original + "\n<!-- updated -->\n", encoding="utf-8")

    ctx2 = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=[SystemMessage(content="x")], tools=[]),
        session=None,
    )
    await skill_rail.before_invoke(ctx2)
    assert skill_rail.load_calls == ["invoice-parser"]
