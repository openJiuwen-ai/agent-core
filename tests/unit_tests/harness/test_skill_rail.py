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
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentManager
from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail
from openjiuwen.harness.tools import ListSkillTool
from openjiuwen.harness.tools.skills.skill_tool import SKILL_TOOL_MARKDOWN_IMAGES_HINT


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


class _DummyEvolutionStore:
    def __init__(self, texts: Dict[str, str]):
        self.texts = texts

    async def format_desc_experience_text(self, skill_name: str) -> str:
        return self.texts.get(skill_name, "")


class _SessionState:
    def __init__(self, session_id: str, state: Dict[str, Any] | None = None):
        self.session_id = session_id
        self.state = state or {}

    def get_session_id(self) -> str:
        return self.session_id

    def get_state(self, key: str | None = None):
        return self.state if key is None else self.state.get(key)

    def update_state(self, data: Dict[str, Any]) -> None:
        self.state.update(data)


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
    body: str = "",
) -> Path:
    """Create a minimal skill directory with SKILL.md."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        f"description: {description}\n"
        "---\n\n"
        f"# {name}\n{body}",
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
    assert "list_skill" not in content


@pytest.mark.asyncio
async def test_skill_rail_before_model_call_refreshes_when_before_invoke_is_skipped(tmp_path: Path):
    """before_model_call should load skills for task-loop paths that skip before_invoke."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = _TrackingSkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=False,
    )
    skill_rail.set_sys_operation(sys_operation)

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

    await skill_rail.before_model_call(ctx)

    assert skill_rail.load_calls == ["invoice-parser"]
    assert _sorted_skill_names(skill_rail.skills) == ["invoice-parser"]
    assert "invoice-parser" in builder.build()


@pytest.mark.asyncio
async def test_skill_rail_before_model_call_refreshes_only_when_skill_snapshot_changes(tmp_path: Path):
    """before_model_call should refresh only when directories or SKILL.md mtimes change."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = _TrackingSkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=False,
    )
    skill_rail.set_sys_operation(sys_operation)

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

    await skill_rail.before_model_call(ctx)
    assert skill_rail.load_calls == ["invoice-parser"]

    skill_rail.load_calls.clear()
    await skill_rail.before_model_call(ctx)
    assert skill_rail.load_calls == []

    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")
    await skill_rail.before_model_call(ctx)
    assert skill_rail.load_calls == ["xlsx-writer"]

    skill_rail.load_calls.clear()
    time.sleep(1.1)
    skill_md = skills_root / "invoice-parser" / "SKILL.md"
    original = skill_md.read_text(encoding="utf-8")
    skill_md.write_text(original + "\n<!-- updated -->\n", encoding="utf-8")

    await skill_rail.before_model_call(ctx)
    assert skill_rail.load_calls == ["invoice-parser"]


@pytest.mark.asyncio
async def test_skill_rail_filters_enabled_and_disabled_skills(tmp_path: Path):
    """SkillUseRail should respect enabled_skills and disabled_skills."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")
    _write_skill(skills_root, "legacy-skill", "Old skill")

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
async def test_skill_rail_skill_tool_reads_multimodal_skill_in_hint_mode(tmp_path: Path):
    """Registered skill_tool should read SKILL.md from disk and inject media hints."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill(
        skills_root,
        "media-skill",
        "Skill with reference screenshots",
        "See ![landing](images/landing.png) for the layout.\n",
    )

    sys_operation = _make_sys_operation(tmp_path)
    agent = create_deep_agent(
        model=init_model(
            provider="OpenAI",
            model_name="dummy-model",
            api_key="dummy-key",
            api_base="https://example.com/v1",
            verify_ssl=False,
        ),
        card=AgentCard(name="test_skill_agent", description="test skill agent"),
        system_prompt="You are a test assistant.",
        max_iterations=3,
        enable_task_loop=False,
        workspace=skills_root,
        sys_operation=sys_operation,
        enable_read_image_multimodal=True,
    )
    skill_rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        multimodal_skill_mode="hint",
        include_tools=False,
    )
    await agent.register_rail(skill_rail)

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await skill_rail.before_invoke(ctx)

    skill_tool_card = next(
        item for item in agent.ability_manager.list()
        if getattr(item, "name", None) == "skill_tool"
    )
    skill_tool = Runner.resource_mgr.get_tool(skill_tool_card.id)

    result = await skill_tool.invoke({"skill_name": "media-skill"})

    assert result.success is True
    assert SKILL_TOOL_MARKDOWN_IMAGES_HINT in result.data["content"]
    assert "images/landing.png" in result.data["content"]


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
    assert "invoice-parser" not in content
    assert "read_file" in content
    assert "code" in content
    assert "bash" in content


@pytest.mark.asyncio
async def test_list_skill_tool_reads_latest_skills_from_skill_rail(tmp_path: Path):
    """ListSkillTool should read latest skills via get_skills instead of fixed snapshot."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

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


@pytest.mark.asyncio
async def test_skill_rail_persists_baseline_and_attaches_only_runtime_additions(tmp_path: Path):
    """A session baseline is stable while later filesystem additions are attached dynamically."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    skill_rail.set_sys_operation(sys_operation)
    skill_rail.system_prompt_builder = SystemPromptBuilder()
    skill_rail.attachment_manager = PromptAttachmentManager()

    session = _SessionState("session-a")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)
    await skill_rail.before_invoke(ctx)

    baseline = session.get_state("skill_use")
    assert [item["name"] for item in baseline["baseline_skills"]] == ["invoice-parser"]

    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")
    await skill_rail.before_model_call(ctx)

    # The persisted system-prompt baseline does not change.
    assert [item["name"] for item in session.get_state("skill_use")["baseline_skills"]] == [
        "invoice-parser"
    ]
    assert "invoice-parser" in skill_rail.system_prompt_builder.build()
    assert "xlsx-writer" not in skill_rail.system_prompt_builder.build()

    attachments = await skill_rail.attachment_manager.collect_for_session("session-a")
    assert len(attachments) == 1
    assert attachments[0].section == "skills.runtime_changes"
    assert "xlsx-writer" in (attachments[0].content or "")
    assert "Skill 环境状态更新。请根据当前任务需要，按需调用相关 Skill" in (
        attachments[0].content or ""
    )
    assert "使用规则" not in (attachments[0].content or "")
    assert "Activation rule" not in (attachments[0].content or "")


@pytest.mark.asyncio
async def test_skill_rail_puts_baseline_evolution_experience_in_attachment(tmp_path: Path):
    """Baseline evolution experience is attached without changing the system prompt."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")

    sys_operation = _make_sys_operation(tmp_path)
    rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=False,
        evolution_store=_DummyEvolutionStore(
            {"invoice-parser": "- Prefer extracting tables before summarizing the invoice."}
        ),
    )
    rail.set_sys_operation(sys_operation)
    rail.system_prompt_builder = SystemPromptBuilder()
    rail.attachment_manager = PromptAttachmentManager()

    session = _SessionState("evolution-baseline")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)
    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)

    system_prompt = rail.system_prompt_builder.build()
    assert "Parse invoice pdf files" in system_prompt
    assert "Prefer extracting tables" not in system_prompt

    attachments = await rail.attachment_manager.collect_for_session("evolution-baseline")
    assert len(attachments) == 1
    content = attachments[0].content or ""
    assert "Skill 演进经验参考：" in content
    assert "[Skill: invoice-parser]" in content
    assert "Prefer extracting tables before summarizing the invoice." in content


@pytest.mark.asyncio
async def test_skill_rail_combines_additions_removals_and_evolution_in_one_attachment(tmp_path: Path):
    """Skill changes and evolution experience share one Skill attachment."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    removed_dir = _write_skill(skills_root, "legacy-parser", "Parse legacy files")

    sys_operation = _make_sys_operation(tmp_path)
    rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=False,
        evolution_store=_DummyEvolutionStore(
            {
                "invoice-parser": "- Prefer extracting tables first.",
                "legacy-parser": "- Preserve legacy field names.",
                "new-parser": "- Validate the output schema before returning.",
            }
        ),
    )
    rail.set_sys_operation(sys_operation)
    rail.system_prompt_builder = SystemPromptBuilder()
    rail.attachment_manager = PromptAttachmentManager()

    session = _SessionState("combined-changes")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)
    await rail.before_invoke(ctx)

    removed_dir.joinpath("SKILL.md").unlink()
    _write_skill(skills_root, "new-parser", "Parse new files")
    await rail.before_model_call(ctx)

    attachments = await rail.attachment_manager.collect_for_session("combined-changes")
    assert len(attachments) == 1
    content = attachments[0].content or ""
    assert "新增可用 Skill：" in content
    assert "new-parser: Parse new files" in content
    assert "已移除、当前不可用的 Skill：" in content
    assert "legacy-parser" in content
    assert "[Skill: invoice-parser]" in content
    assert "[Skill: legacy-parser]" in content
    assert "[Skill: new-parser]" in content


@pytest.mark.asyncio
async def test_skill_rail_renders_evolution_attachment_in_english(tmp_path: Path):
    """English prompt builders use the English Skill attachment headings."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")

    sys_operation = _make_sys_operation(tmp_path)
    rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=False,
        evolution_store=_DummyEvolutionStore({"invoice-parser": "- Extract tables first."}),
    )
    rail.set_sys_operation(sys_operation)
    rail.system_prompt_builder = SystemPromptBuilder(language="en")
    rail.attachment_manager = PromptAttachmentManager()

    session = _SessionState("evolution-english")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)
    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)

    attachments = await rail.attachment_manager.collect_for_session("evolution-english")
    content = attachments[0].content or ""
    assert "Skill environment status update." in content
    assert "Skill evolution experience reference:" in content
    assert "[Skill: invoice-parser]" in content
    assert "Extract tables first." in content


@pytest.mark.asyncio
async def test_skill_rail_refreshes_evolution_when_skill_files_are_unchanged(tmp_path: Path):
    """Evolution changes are refreshed independently from the Skill.md snapshot."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    evolution_store = _DummyEvolutionStore({"invoice-parser": "- Use the first experience."})

    sys_operation = _make_sys_operation(tmp_path)
    rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=False,
        evolution_store=evolution_store,
    )
    rail.set_sys_operation(sys_operation)
    rail.system_prompt_builder = SystemPromptBuilder()
    rail.attachment_manager = PromptAttachmentManager()

    session = _SessionState("evolution-refresh")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)
    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)

    evolution_store.texts["invoice-parser"] = "- Use the updated experience."
    await rail.before_model_call(ctx)

    attachments = await rail.attachment_manager.collect_for_session("evolution-refresh")
    content = attachments[0].content or ""
    assert "Use the updated experience." in content
    assert "Use the first experience." not in content


@pytest.mark.asyncio
async def test_skill_rail_attaches_deleted_skill_change(tmp_path: Path):
    """Deleting a baseline Skill is reported through the session attachment."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    deleted_skill_dir = _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    skill_rail.set_sys_operation(sys_operation)
    skill_rail.system_prompt_builder = SystemPromptBuilder()
    skill_rail.attachment_manager = PromptAttachmentManager()

    session = _SessionState("session-delete")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)
    await skill_rail.before_invoke(ctx)

    deleted_skill_dir.joinpath("SKILL.md").unlink()
    await skill_rail.before_model_call(ctx)

    attachments = await skill_rail.attachment_manager.collect_for_session("session-delete")
    assert len(attachments) == 1
    assert "已移除、当前不可用的 Skill" in (attachments[0].content or "")
    assert "invoice-parser" in (attachments[0].content or "")


@pytest.mark.asyncio
async def test_skill_rail_new_session_promotes_installed_skill_to_baseline(tmp_path: Path):
    """A newly installed skill becomes baseline only when a new session starts."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    session = _SessionState("session-b")
    await rail.before_invoke(AgentCallbackContext(agent=None, inputs=None, session=session))

    assert [item["name"] for item in session.get_state("skill_use")["baseline_skills"]] == [
        "invoice-parser",
        "xlsx-writer",
    ]


@pytest.mark.asyncio
async def test_skill_rail_rehydrates_baseline_after_rail_restart(tmp_path: Path):
    """Restoring a Session does not require the Rail to retain the baseline in memory."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    restored_state = {
        "skill_use": {
            "schema_version": 1,
            "baseline_skills": [
                {
                    "name": "invoice-parser",
                    "description": "Persisted description",
                    "directory": str(skills_root / "invoice-parser"),
                }
            ],
        }
    }
    session = _SessionState("restored", restored_state)
    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    await rail.before_invoke(AgentCallbackContext(agent=None, inputs=None, session=session))

    baseline = rail._get_session_baseline(AgentCallbackContext(agent=None, inputs=None, session=session))
    assert [skill.name for skill in baseline] == ["invoice-parser"]
    assert baseline[0].description == "Persisted description"
    assert [skill.name for skill in rail.get_skills_for_session(session)] == [
        "invoice-parser",
        "xlsx-writer",
    ]


@pytest.mark.asyncio
async def test_skill_rail_keeps_empty_baseline_distinct_from_missing_baseline(tmp_path: Path):
    """An empty first-session baseline still makes later installations dynamic."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    session = _SessionState("empty-first")
    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)

    await rail.before_invoke(AgentCallbackContext(agent=None, inputs=None, session=session))
    assert session.get_state("skill_use")["baseline_skills"] == []

    _write_skill(skills_root, "new-skill", "Installed after the session started")
    await rail.before_invoke(AgentCallbackContext(agent=None, inputs=None, session=session))
    assert [skill.name for skill in rail.get_skills_for_session(session)] == ["new-skill"]
    assert session.get_state("skill_use")["baseline_skills"] == []


@pytest.mark.asyncio
async def test_skill_rail_skips_nonexistent_directories(tmp_path: Path):
    """SkillUseRail should silently skip directories that do not exist."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "my-skill", "Real skill")

    nonexistent = tmp_path / "does_not_exist"
    another_nonexistent = tmp_path / "also_missing"

    skill_rail = SkillUseRail(
        skills_dir=[str(nonexistent), str(skills_root), str(another_nonexistent)],
        skill_mode="all",
        include_tools=False,
    )

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await skill_rail.before_invoke(ctx)

    assert _sorted_skill_names(skill_rail.skills) == ["my-skill"]


@pytest.mark.asyncio
async def test_skill_rail_all_dirs_nonexistent_produces_empty_skills(tmp_path: Path):
    """SkillUseRail should produce empty skills when all directories are missing."""
    nonexistent_a = tmp_path / "missing_a"
    nonexistent_b = tmp_path / "missing_b"

    skill_rail = SkillUseRail(
        skills_dir=[str(nonexistent_a), str(nonexistent_b)],
        skill_mode="all",
        include_tools=False,
    )

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await skill_rail.before_invoke(ctx)

    assert skill_rail.skills == []


@pytest.mark.asyncio
async def test_skill_rail_priority_dedup_first_dir_wins(tmp_path: Path):
    """When multiple dirs contain a skill with the same name, the first dir wins."""
    high_prio = tmp_path / "high"
    low_prio = tmp_path / "low"

    _write_skill(high_prio, "shared-skill", "High priority version")
    _write_skill(low_prio, "shared-skill", "Low priority version")
    _write_skill(low_prio, "unique-skill", "Only in low")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = SkillUseRail(
        skills_dir=[str(high_prio), str(low_prio)],
        skill_mode="all",
        include_tools=False,
    )
    skill_rail.set_sys_operation(sys_operation)

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await skill_rail.before_invoke(ctx)

    names = _sorted_skill_names(skill_rail.skills)
    assert names == ["shared-skill", "unique-skill"]

    # Verify the "shared-skill" comes from high_prio directory
    shared = [s for s in skill_rail.skills if s.name == "shared-skill"][0]
    assert str(high_prio.resolve()) in str(shared.directory.resolve())
    assert shared.description == "High priority version"


@pytest.mark.asyncio
async def test_skill_rail_multi_dir_with_missing_dirs(tmp_path: Path):
    """SkillUseRail loads skills from existing dirs and skips missing ones."""
    existing_a = tmp_path / "dir_a"
    missing_b = tmp_path / "dir_b"
    existing_c = tmp_path / "dir_c"

    _write_skill(existing_a, "skill-a", "From dir A")
    # dir_b does not exist
    _write_skill(existing_c, "skill-c", "From dir C")

    skill_rail = SkillUseRail(
        skills_dir=[str(existing_a), str(missing_b), str(existing_c)],
        skill_mode="all",
        include_tools=False,
    )

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await skill_rail.before_invoke(ctx)

    assert _sorted_skill_names(skill_rail.skills) == ["skill-a", "skill-c"]
