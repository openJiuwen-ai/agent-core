# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DeepAgent ExpertHarness hot load / unload / skip / rollback contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    ToolCall,
)
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.resources_manager.base import Error
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail, ToolCallInputs
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.rails import SkillUseRail, SubagentRail, TaskCompletionRail
from openjiuwen.harness.resources.expert_harness_parts import ResolvedSkill, ResourceKind
from openjiuwen.harness.schema.build_context import BuildContext
from openjiuwen.harness.schema.deep_agent_spec import (
    BuiltinToolSpec,
    DeepAgentSpec,
    ModelSpec,
    RailSpec,
    SubAgentSpec,
)
from openjiuwen.harness.schema.expert_harness_spec import ExpertHarnessConfigSpec, ExpertHarnessSpec
from openjiuwen.harness.tools.ask_user import AskUserTool

pytestmark = pytest.mark.level0

def _fake_model() -> Model:
    return Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="test-key",
            api_base="http://test-base",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="test-model"),
    )


def _fake_model_spec() -> ModelSpec:
    return ModelSpec(
        model_client_config=ModelClientConfig(
            client_provider="openai",
            api_key="fake-key-for-hot-load",
            api_base="http://localhost:0",
            verify_ssl=False,
        ),
        model_request_config=ModelRequestConfig(model="fake-hot-load"),
    )


@pytest.mark.asyncio
async def test_load_from_spec_binds_and_records_refs(tmp_path: Path) -> None:
    """load_expert_harness_from_spec binds rails; ask_user tool comes from AskUserRail."""
    agent = create_deep_agent(
        model=_fake_model(),
        card=AgentCard(name="hot_bind", description="hot"),
        workspace=str(tmp_path / "ws"),
        language="en",
    )
    await agent.ensure_initialized()

    caller_ctx = BuildContext(language="en", extras={"marker": "keep"})
    record = await agent.load_expert_harness_from_spec(
        ExpertHarnessSpec(
            id="pack_bind",
            tools=[BuiltinToolSpec(type="core.ask_user")],
            rails=[RailSpec(type="core.task_completion")],
        ),
        context=caller_ctx,
    )
    assert caller_ctx.extras == {"marker": "keep"}
    assert any(ref.kind == ResourceKind.RAIL and ref.identity == "AskUserRail" for ref in record.refs)
    assert any(ref.kind == ResourceKind.RAIL and ref.identity == "TaskCompletionRail" for ref in record.refs)
    assert record.load_id in agent._load_records
    assert agent.find_rail_by_name("TaskCompletionRail") is not None
    assert agent.find_rail_by_name("AskUserRail") is not None
    assert agent.ability_manager.get("ask_user") is not None


@pytest.mark.asyncio
async def test_same_name_resources_are_skipped(tmp_path: Path) -> None:
    """Second load of same-class rails skips and records no refs."""
    agent = create_deep_agent(
        model=_fake_model(),
        card=AgentCard(name="hot_skip", description="hot"),
        workspace=str(tmp_path / "ws"),
        language="en",
    )
    await agent.ensure_initialized()

    first = await agent.load_expert_harness_from_spec(
        ExpertHarnessSpec(
            id="pack_a",
            tools=[BuiltinToolSpec(type="core.ask_user")],
            rails=[RailSpec(type="core.task_completion")],
        )
    )
    assert first.refs
    second = await agent.load_expert_harness_from_spec(
        ExpertHarnessSpec(
            id="pack_b",
            tools=[BuiltinToolSpec(type="core.ask_user")],
            rails=[RailSpec(type="core.task_completion")],
        )
    )
    assert second.refs == []
    assert second.load_id in agent._load_records


@pytest.mark.asyncio
async def test_unload_empty_record_does_not_hurt_prior(tmp_path: Path) -> None:
    """Unloading a skip-only record leaves prior binds intact."""
    agent = create_deep_agent(
        model=_fake_model(),
        card=AgentCard(name="hot_unload", description="hot"),
        workspace=str(tmp_path / "ws"),
        language="en",
    )
    await agent.ensure_initialized()

    first = await agent.load_expert_harness_from_spec(
        ExpertHarnessSpec(
            id="pack_a",
            tools=[BuiltinToolSpec(type="core.ask_user")],
            rails=[RailSpec(type="core.task_completion")],
        )
    )
    second = await agent.load_expert_harness_from_spec(
        ExpertHarnessSpec(
            id="pack_b",
            tools=[BuiltinToolSpec(type="core.ask_user")],
            rails=[RailSpec(type="core.task_completion")],
        )
    )
    unloaded = await agent.unload_expert_harness(second)
    assert unloaded == []
    assert second.load_id not in agent._load_records
    assert first.load_id in agent._load_records
    assert agent.find_rail_by_name("TaskCompletionRail") is not None
    assert agent.ability_manager.get("ask_user") is not None


@pytest.mark.asyncio
async def test_apply_hot_failure_rolls_back_bound_refs(tmp_path: Path) -> None:
    """When a later bind fails, apply_hot unapplies earlier refs from the same batch."""
    import openjiuwen.harness.expert_harness_runtime as runtime

    agent = create_deep_agent(
        model=_fake_model(),
        card=AgentCard(name="hot_rollback", description="hot"),
        workspace=str(tmp_path / "ws"),
        language="en",
    )
    await agent.ensure_initialized()
    assert agent.ability_manager.get("ask_user") is None
    assert agent.find_rail_by_name("AskUserRail") is None
    assert agent.find_rail_by_name("TaskCompletionRail") is None

    _orig_hot_bind_rail = runtime._hot_bind_rail
    calls = {"n": 0}

    async def _fail_after_first(agent_arg, rail):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("forced rail bind failure")
        return await _orig_hot_bind_rail(agent_arg, rail)

    with patch.object(runtime, "_hot_bind_rail", new=_fail_after_first):
        with pytest.raises(Exception, match="forced rail bind failure"):
            await agent.load_expert_harness_from_spec(
                ExpertHarnessSpec(
                    id="pack_rollback",
                    tools=[BuiltinToolSpec(type="core.ask_user")],
                    rails=[RailSpec(type="core.task_completion")],
                )
            )

    assert agent.ability_manager.get("ask_user") is None
    assert agent.find_rail_by_name("AskUserRail") is None
    assert agent.find_rail_by_name("TaskCompletionRail") is None


@pytest.mark.asyncio
async def test_hot_bind_mcp_failure_leaves_no_residue(tmp_path: Path) -> None:
    """MCP register failure must not leave deep_config / ability / server residue."""
    import openjiuwen.harness.expert_harness_runtime as runtime

    agent = create_deep_agent(
        model=_fake_model(),
        card=AgentCard(name="hot_mcp_fail", description="hot"),
        workspace=str(tmp_path / "ws"),
        language="en",
    )
    await agent.ensure_initialized()
    mcp = McpServerConfig(
        server_name="hot_fail_mcp",
        server_id="hot_fail_mcp_001",
        server_path="http://127.0.0.1:9/mcp",
        client_type="streamable-http",
    )
    before = list(agent.deep_config.mcps or [])

    with patch.object(
        Runner.resource_mgr,
        "add_mcp_server",
        new=AsyncMock(return_value=Error(RuntimeError("mcp register failed"))),
    ):
        with pytest.raises(RuntimeError, match="mcp register failed"):
            await runtime._hot_bind_mcp(agent, mcp)

    assert list(agent.deep_config.mcps or []) == before
    assert agent.ability_manager.get(mcp.server_name) is None
    assert Runner.resource_mgr.get_mcp_server_config(mcp.server_id) is None


@pytest.mark.asyncio
async def test_hot_bind_skill_failure_leaves_no_residue(tmp_path: Path) -> None:
    """Skill reload failure must not leave config.skills or a half-mounted rail."""
    import openjiuwen.harness.expert_harness_runtime as runtime

    workspace = tmp_path / "ws"
    existing_skill = workspace / "skills" / "ok-skill"
    existing_skill.mkdir(parents=True)
    (existing_skill / "SKILL.md").write_text(
        "---\ndescription: ok skill\n---\n\n# ok-skill\n",
        encoding="utf-8",
    )
    skill_dir = workspace / "extra_skills" / "fail-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: fail skill\n---\n\n# fail-skill\n",
        encoding="utf-8",
    )
    agent = create_deep_agent(
        model=_fake_model(),
        card=AgentCard(name="hot_skill_fail", description="hot"),
        workspace=str(workspace),
        language="en",
        skills=[str(workspace / "skills")],
    )
    await agent.ensure_initialized()
    before_skills = list(agent.deep_config.skills or [])
    rail = agent.find_rails_by_type((SkillUseRail,))[0]
    await rail.reload_skills()
    before_skill_names = {skill.name for skill in rail.skills}
    before_dirs = {
        str(Path(item).expanduser().resolve())
        for item in (rail.skills_dir if isinstance(rail.skills_dir, list) else [rail.skills_dir])
        if str(item).strip()
    }
    new_root = str(skill_dir.parent.resolve())
    real_reload = SkillUseRail.reload_skills
    reload_calls = {"n": 0}

    async def _fail_first_reload(self):
        reload_calls["n"] += 1
        if reload_calls["n"] == 1:
            raise RuntimeError("skill reload failed")
        await real_reload(self)

    with patch.object(SkillUseRail, "reload_skills", new=_fail_first_reload):
        with pytest.raises(RuntimeError, match="skill reload failed"):
            await runtime._hot_bind_skill(
                agent,
                ResolvedSkill(directory=str(skill_dir), mode="all"),
            )

    assert list(agent.deep_config.skills or []) == before_skills
    after_dirs = {
        str(Path(item).expanduser().resolve())
        for item in (rail.skills_dir if isinstance(rail.skills_dir, list) else [rail.skills_dir])
        if str(item).strip()
    }
    assert after_dirs == before_dirs
    assert new_root not in after_dirs or new_root in before_dirs
    assert {skill.name for skill in rail.skills} == before_skill_names


@pytest.mark.asyncio
async def test_cold_build_then_hot_load_from_memory_spec(tmp_path: Path) -> None:
    """Cold DeepAgentSpec.build then hot-loads an in-memory ExpertHarnessSpec."""
    agent = DeepAgentSpec(
        card=AgentCard(name="cold_then_hot", description="hot"),
        model=_fake_model_spec(),
        language="en",
        auto_create_workspace=False,
        rails=[RailSpec(type="core.task_completion")],
    ).build()
    await agent.ensure_initialized()
    assert agent.find_rail_by_name("TaskCompletionRail") is not None

    record = await agent.load_expert_harness_from_spec(
        ExpertHarnessSpec(
            id="overlay",
            tools=[BuiltinToolSpec(type="core.ask_user")],
        )
    )
    assert any(ref.kind == ResourceKind.RAIL and ref.identity == "AskUserRail" for ref in record.refs)
    assert agent.ability_manager.get("ask_user") is not None
    # cold rail still present; same-class rail from overlay would skip if re-declared
    assert agent.find_rail_by_name("TaskCompletionRail") is not None


@pytest.mark.asyncio
async def test_load_ability_binds_tool_rail_skill_and_unloads(tmp_path: Path) -> None:
    """load_expert_harness_ability binds pre-built tools/rails/skills and unloads symmetrically."""
    workspace = tmp_path / "ws"
    skill_dir = workspace / "extra_skills" / "ability-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: ability hot-load skill\n---\n\n# ability-skill\n",
        encoding="utf-8",
    )

    agent = create_deep_agent(
        model=_fake_model(),
        card=AgentCard(name="hot_ability", description="hot"),
        workspace=str(workspace),
        language="en",
        # Keep a default SkillUseRail mount so skill unapply does not hit empty skills_dir.
        skills=[str(workspace / "skills")],
    )
    await agent.ensure_initialized()
    assert agent.ability_manager.get("ask_user") is None
    assert agent.find_rail_by_name("TaskCompletionRail") is None

    tool = AskUserTool(language="en", agent_id=agent.card.id)
    rail = TaskCompletionRail()
    skill = ResolvedSkill(directory=str(skill_dir), mode="all")

    record = await agent.load_expert_harness_ability(
        tools=tool,
        rails=rail,
        skills=skill,
    )
    assert record.source_uri is None
    assert record.load_id in agent._load_records
    assert any(ref.kind == ResourceKind.TOOL for ref in record.refs)
    assert any(ref.kind == ResourceKind.RAIL and ref.identity == "TaskCompletionRail" for ref in record.refs)
    assert any(ref.kind == ResourceKind.SKILL and ref.identity == str(skill_dir) for ref in record.refs)

    # Tool: ability card + resource manager instance are the ones we passed in.
    assert agent.ability_manager.get("ask_user") is tool.card
    assert any(card is tool.card for card in (agent.deep_config.tools or []))
    assert Runner.resource_mgr.get_tool(tool_id=tool.card.id, tag=agent.card.id) is tool

    # Rail: same instance is registered and active (not merely pending).
    assert agent.find_rail_by_name("TaskCompletionRail") is rail
    assert agent.is_registered_rail(rail)

    # Skill: root mounted on SkillUseRail and skill materialized for use.
    skill_rails = agent.find_rails_by_type((SkillUseRail,))
    assert skill_rails
    skill_rail = skill_rails[0]
    skill_dirs = {
        str(Path(item).expanduser().resolve())
        for item in (
            skill_rail.skills_dir if isinstance(skill_rail.skills_dir, list) else [skill_rail.skills_dir]
        )
        if item
    }
    assert str(skill_dir.parent.resolve()) in skill_dirs
    assert any(s.name == "ability-skill" for s in skill_rail.skills)

    unloaded = await agent.unload_expert_harness(record)
    assert unloaded
    assert record.load_id not in agent._load_records
    assert agent.ability_manager.get("ask_user") is None
    assert Runner.resource_mgr.get_tool(tool_id=tool.card.id, tag=agent.card.id) is None
    assert agent.find_rail_by_name("TaskCompletionRail") is None
    assert not agent.is_registered_rail(rail)
    skill_mounted = {
        str(Path(item).expanduser().resolve())
        for item in (agent.deep_config.skills or [])
    }
    assert str(skill_dir.parent.resolve()) not in skill_mounted
    skill_rails_after = agent.find_rails_by_type((SkillUseRail,))
    if skill_rails_after:
        assert not any(s.name == "ability-skill" for s in skill_rails_after[0].skills)


@pytest.mark.asyncio
async def test_hot_load_custom_subagent_ensures_rail_and_task_tool(tmp_path: Path) -> None:
    """Custom SubAgentSpec hot-load binds SUBAGENT and ensures SubagentRail + task_tool."""
    agent = create_deep_agent(
        model=_fake_model(),
        card=AgentCard(name="hot_custom_sub", description="hot"),
        workspace=str(tmp_path / "ws"),
        language="en",
    )
    await agent.ensure_initialized()
    assert agent.ability_manager.get("task_tool") is None
    assert not agent.find_rails_by_type((SubagentRail,))

    record = await agent.load_expert_harness_from_spec(
        ExpertHarnessSpec(
            id="pack_custom_bind",
            config=ExpertHarnessConfigSpec(enable_subagent=True),
            subagents=[
                SubAgentSpec(
                    agent_card=AgentCard(name="custom_echo", description="hot custom"),
                    system_prompt="You are a custom hot-loaded subagent.",
                )
            ],
        )
    )
    assert any(ref.kind == ResourceKind.SUBAGENT and ref.identity == "custom_echo" for ref in record.refs)
    assert any(ref.kind == ResourceKind.RAIL and ref.identity == "SubagentRail" for ref in record.refs)

    rails = agent.find_rails_by_type((SubagentRail,))
    assert len(rails) == 1
    assert rails[0].tools
    task_card = agent.ability_manager.get("task_tool")
    assert task_card is not None
    assert "custom_echo" in (task_card.description or "")

    child = agent.create_subagent("custom_echo", "hot_custom_sub_session")
    assert child.card.name == "custom_echo"
    assert "custom hot-loaded" in (child.deep_config.system_prompt or "")


@pytest.mark.asyncio
async def test_unload_custom_subagent_removes_config_and_ability(tmp_path: Path) -> None:
    """Unload drops custom subagent from config/ability and remove task_tool with SubagentRail."""
    agent = create_deep_agent(
        model=_fake_model(),
        card=AgentCard(name="hot_custom_unload", description="hot"),
        workspace=str(tmp_path / "ws"),
        language="en",
    )
    await agent.ensure_initialized()

    record = await agent.load_expert_harness_from_spec(
        ExpertHarnessSpec(
            id="pack_custom_unload",
            config=ExpertHarnessConfigSpec(enable_subagent=True),
            subagents=[
                SubAgentSpec(
                    agent_card=AgentCard(name="custom_echo", description="hot custom"),
                    system_prompt="You are a custom hot-loaded subagent.",
                )
            ],
        )
    )
    assert agent.create_subagent("custom_echo", "before_unload") is not None

    unloaded = await agent.unload_expert_harness(record)
    assert any(label == "subagent:custom_echo" for label in unloaded)
    assert not any(
        getattr(item.agent_card, "name", None) == "custom_echo"
        for item in (agent.deep_config.subagents or [])
    )
    assert agent.ability_manager.get("custom_echo") is None
    assert agent.find_rail_by_name("SubagentRail") is None
    assert agent.ability_manager.get("task_tool") is None

    with pytest.raises(Exception, match="custom_echo"):
        agent.create_subagent("custom_echo", "after_unload")


@pytest.mark.asyncio
async def test_hot_load_custom_subagent_task_tool_can_invoke(tmp_path: Path) -> None:
    """Hot-load custom SubAgentSpec; fake model emits task_tool function-call to run subagent."""
    finish_rail = _ForceFinishAfterTaskToolRail()
    fake_model = _TaskToolCallModel(
        tool_args={"subagent_type": "custom_echo", "task_description": "ping"},
    )
    agent = create_deep_agent(
        model=_fake_model(),
        card=AgentCard(name="hot_task_invoke", description="hot"),
        workspace=str(tmp_path / "ws"),
        language="en",
        max_iterations=3,
        rails=[finish_rail],
    )
    await agent.ensure_initialized()
    await agent.load_expert_harness_from_spec(
        ExpertHarnessSpec(
            id="pack_custom_sub",
            config=ExpertHarnessConfigSpec(enable_subagent=True),
            subagents=[
                SubAgentSpec(
                    agent_card=AgentCard(name="custom_echo", description="hot custom"),
                    system_prompt="You are a custom hot-loaded subagent.",
                )
            ],
        )
    )
    assert agent.ability_manager.get("task_tool") is not None

    agent.react_agent.set_llm(fake_model)

    _orig_invoke = DeepAgent.invoke

    async def _selective_invoke(self, inputs=None, session=None):
        if getattr(getattr(self, "card", None), "name", None) == "custom_echo":
            return {"output": "custom-ok"}
        return await _orig_invoke(self, inputs, session=session)

    with patch.object(DeepAgent, "invoke", new=_selective_invoke):
        result = await agent.invoke(
            {"query": "delegate to custom_echo", "conversation_id": "hot_task_fc"},
        )

    assert fake_model.call_count >= 1
    assert "task_tool" in fake_model.last_tool_names()
    assert finish_rail.tool_results, "task_tool was not executed via function-call"
    tool_result = finish_rail.tool_results[0]
    data = getattr(tool_result, "data", tool_result)
    if isinstance(data, dict) and "data" in data and "output" not in data:
        data = data["data"]
    assert isinstance(data, dict)
    assert data.get("output") == "custom-ok"
    assert result.get("result_type") == "answer"


class _ForceFinishAfterTaskToolRail(AgentRail):
    """Stop the ReAct loop after task_tool runs; capture tool_result."""

    priority = 20

    def __init__(self) -> None:
        super().__init__()
        self.tool_results: list[Any] = []

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, ToolCallInputs):
            return
        if ctx.inputs.tool_name != "task_tool":
            return
        self.tool_results.append(ctx.inputs.tool_result)
        ctx.request_force_finish(
            {
                "result_type": "answer",
                "output": "task_tool completed",
                "tool_result": ctx.inputs.tool_result,
            }
        )


class _TaskToolCallModel:
    """Fake model that emits one task_tool function-call."""

    model_config = None

    def __init__(self, *, tool_args: dict[str, Any]) -> None:
        self.tool_args = dict(tool_args)
        self.call_history: list[dict[str, Any]] = []
        self.model_client_config = ModelClientConfig(
            client_provider="openai",
            api_key="fake-key-for-hot-load",
            api_base="http://localhost:0",
            verify_ssl=False,
        )

    async def invoke(
        self,
        messages: Any,
        *,
        tools: Any = None,
        **kwargs: object,
    ) -> AssistantMessage:
        _ = kwargs
        self.call_history.append(
            {
                "messages": list(messages) if isinstance(messages, list) else [messages],
                "tools": list(tools or []),
            }
        )
        return AssistantMessage(
            content="",
            tool_calls=[
                ToolCall(
                    id="hot_load_task_tool_call",
                    type="function",
                    name="task_tool",
                    arguments=json.dumps(self.tool_args),
                )
            ],
            finish_reason="tool_calls",
        )

    async def stream(self, *args: object, **kwargs: object) -> None:
        _ = args, kwargs
        raise AssertionError("fake task_tool model should not use stream()")

    @property
    def call_count(self) -> int:
        return len(self.call_history)

    def last_tool_names(self) -> set[str]:
        if not self.call_history:
            return set()
        names: set[str] = set()
        for tool in self.call_history[-1]["tools"]:
            name = tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", None)
            if isinstance(name, str):
                names.add(name)
        return names

@pytest.mark.asyncio
async def test_deprecated_unload_harness_config_accepts_package_directory(
    tmp_path: Path,
) -> None:
    """Directory load/unload must resolve to the same manifest as source_uri."""
    package = tmp_path / "pack"
    package.mkdir()
    # Use expert_harness.yaml (not harness_config.yaml) so unload must share
    # find_expert_harness_manifest rather than a hardcoded filename join.
    (package / "expert_harness.yaml").write_text(
        "\n".join(
            [
                "schema_version: expert_harness.v1",
                "id: dir_unload_pack",
                "name: Dir Unload Pack",
                "tools:",
                "  - type: core.ask_user",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    agent = create_deep_agent(
        model=_fake_model(),
        card=AgentCard(name="dep_unload_dir", description="hot"),
        workspace=str(tmp_path / "ws"),
        language="en",
    )
    await agent.ensure_initialized()

    with pytest.warns(DeprecationWarning, match="load_harness_config"):
        labels = await agent.load_harness_config(str(package))
    assert labels
    assert len(agent._load_records) == 1
    load_id = next(iter(agent._load_records))
    assert agent._load_records[load_id].source_uri == str(
        (package / "expert_harness.yaml").resolve()
    )

    with pytest.warns(DeprecationWarning, match="unload_harness_config"):
        unloaded = await agent.unload_harness_config(str(package))
    assert unloaded
    assert load_id not in agent._load_records
    assert agent.ability_manager.get("ask_user") is None
