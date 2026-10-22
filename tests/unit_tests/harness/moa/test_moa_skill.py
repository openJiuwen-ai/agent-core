# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""MoA skill metadata, tool admission, native loading, and lifecycle."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import yaml

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.tool import Tool, ToolCard, ToolExposure
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import SysOperation, SysOperationCard
from openjiuwen.harness import DeepAgent, DeepAgentConfig, Workspace
from openjiuwen.harness.moa import MoAConfig, VerifierConfig
from openjiuwen.harness.moa.builtin_skills import get_builtin_skill_dir
from openjiuwen.harness.moa.skill_mount import CONSULT_SKILL_FILE, CONSULT_SKILL_NAME, CONSULT_SKILL_ROOT
from openjiuwen.harness.moa.state import MoAState, active_moa
from openjiuwen.harness.prompts.tools import get_tool_description, get_tool_input_params, validate_all_tool_providers
from openjiuwen.harness.prompts.tools.moa import ConsultReferenceModelsMetadataProvider
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.rails.moa_rail import MoARail
from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail
from openjiuwen.harness.tools.moa import CONSULT_TOOL, ConsultReferenceModels
from tests.unit_tests.harness.moa.helpers import (
    _REFERENCE_PROPOSAL,
    _call,
    _messages_text,
    _session,
    _unique_rails,
    _write_skill,
)
from tests.unit_tests.harness.moa.helpers import primary as _model


def _skill_body() -> str:
    return CONSULT_SKILL_FILE.read_text(encoding="utf-8").split("---", 2)[2].strip()


class _NamedTool(Tool):
    def __init__(self, name: str) -> None:
        super().__init__(ToolCard(id=f"test-{name}", name=name, description="Test tool", input_params={}))

    async def invoke(self, inputs, **kwargs):
        del inputs, kwargs
        return {"content": "ok"}

    async def stream(self, inputs, **kwargs):
        yield await self.invoke(inputs, **kwargs)


@pytest.mark.level1
@pytest.mark.parametrize("name", ["", ".", "..", "../escape", "/absolute", "a\\b"])
def test_builtin_skill_locator_rejects_names_outside_resource_directory(name):
    with pytest.raises(ValueError, match="single directory name"):
        get_builtin_skill_dir(name)


@pytest.mark.level0
def test_builtin_skill_has_real_metadata_and_structured_english_body() -> None:
    text = CONSULT_SKILL_FILE.read_text(encoding="utf-8")
    _, frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == CONSULT_SKILL_NAME == CONSULT_SKILL_FILE.parent.name
    assert metadata["description"]
    assert body.count("\n## ") >= 4
    assert "consult_reference_models" in body
    assert not any("\u4e00" <= char <= "\u9fff" for char in text)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_first_request_contains_metadata_and_native_tools_without_skill_body(deep_factory) -> None:
    primary = _model(AssistantMessage(content="done"))
    deep, reference = deep_factory(primary)
    result = await deep.invoke("inspect this task", session=_session(deep))
    assert result["output"] == "done"
    request = primary.invoke.await_args.kwargs
    assert CONSULT_SKILL_NAME in str(request["messages"])
    assert _skill_body() not in _messages_text(request["messages"])
    names = {tool.name for tool in request["tools"]}
    assert {CONSULT_TOOL, "skill_tool"} <= names
    assert not {"read_file", "bash"} & names
    reference.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_existing_skill_tool_reads_the_packaged_body_through_sys_operation(deep_factory) -> None:
    deep, _ = deep_factory(_model(AssistantMessage(content="done")))
    session = _session(deep)
    await deep.invoke("task", session=session)
    card = deep.ability_manager.get("skill_tool")
    tool = Runner.resource_mgr.get_tool(card.id)
    output = await tool.invoke({"skill_name": CONSULT_SKILL_NAME}, session=session)
    assert output.success is True
    assert _skill_body() in output.data["content"]
    assert output.data["skill_directory"] == str(CONSULT_SKILL_FILE.parent)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["all", "auto_list"])
@pytest.mark.level1
async def test_reuses_user_skill_rail_and_preserves_its_configuration(deep_factory, tmp_path, mode) -> None:
    user_root = tmp_path / "user-skills"
    _write_skill(user_root, "user-example")
    rail = SkillUseRail(str(user_root), skill_mode=mode, include_tools=False, enable_cache=True)
    deep, _ = deep_factory(_model(AssistantMessage(content="done")), rails=[rail])
    session = _session(deep)
    await deep.invoke("task", session=session)
    assert _unique_rails(deep, SkillUseRail) == [rail]
    assert rail.skill_mode == mode and rail.enable_cache is True and rail.include_tools is False
    skills = {skill.name: skill for skill in rail.get_skills_for_session(session)}
    assert {CONSULT_SKILL_NAME, "user-example"} <= skills.keys()
    assert skills[CONSULT_SKILL_NAME].directory == CONSULT_SKILL_FILE.parent
    card = deep.ability_manager.get("skill_tool")
    output = await Runner.resource_mgr.get_tool(card.id).invoke({"skill_name": "user-example"}, session=session)
    assert output.success is True and "User body." in output.data["content"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_direct_config_loads_skill_without_a_new_factory(tmp_path) -> None:
    primary = _model(AssistantMessage(content="done"))
    card = AgentCard(name="direct-moa-skill")
    config = DeepAgentConfig(
        model=primary,
        card=card,
        moa=MoAConfig(reference_models=(_model(),)),
        sys_operation=SysOperation(SysOperationCard(id=f"direct-skill-{uuid4().hex}")),
        workspace=Workspace(root_path=tmp_path),
        language="en",
        auto_create_workspace=False,
        enable_read_image_multimodal=False,
        enable_tool_resilience_rail=False,
    )
    deep = DeepAgent(card).configure(config)
    try:
        await deep.invoke("task", session=_session(deep))
        assert CONSULT_SKILL_NAME in str(primary.invoke.await_args.kwargs["messages"])
        assert deep.ability_manager.get("skill_tool") is not None
    finally:
        for rail in reversed(_unique_rails(deep, object)):
            await deep.unregister_rail(rail)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_task_loop_model_request_receives_skill_catalog(deep_factory) -> None:
    primary = _model()
    requests = []

    async def stream(messages, **kwargs):
        del kwargs
        requests.append(messages)
        yield AssistantMessageChunk(content="done")

    primary.stream = stream
    deep, _ = deep_factory(primary, task_loop=True)
    result = await deep.invoke("task", session=_session(deep))
    assert result["output"] == "done"
    assert CONSULT_SKILL_NAME in str(requests[0])
    assert _skill_body() not in _messages_text(requests[0])
    primary.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_first_model_hook_populates_catalog_without_outer_before_invoke(deep_factory) -> None:
    primary = _model(AssistantMessage(content="done"))
    deep, reference = deep_factory(primary)
    await deep._ensure_initialized()
    await deep.react_agent.invoke({"query": "task"}, session=_session(deep))
    messages = primary.invoke.await_args.kwargs["messages"]
    assert CONSULT_SKILL_NAME in _messages_text(messages)
    assert _skill_body() not in _messages_text(messages)
    reference.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("filters", [{"enabled_skills": ["other"]}, {"disabled_skills": [CONSULT_SKILL_NAME]}])
@pytest.mark.level1
async def test_explicit_filters_that_hide_builtin_skill_fail_before_model(deep_factory, tmp_path, filters) -> None:
    rail = SkillUseRail(str(tmp_path), include_tools=False, **filters)
    primary = _model(AssistantMessage(content="must not run"))
    deep, reference = deep_factory(primary, rails=[rail])
    with pytest.raises(BaseError, match="MoA consultation skill.*filtered"):
        await deep.invoke("task", session=_session(deep))
    primary.invoke.assert_not_awaited()
    reference.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_user_skill_with_reserved_name_fails_before_model(deep_factory, tmp_path) -> None:
    user_root = tmp_path / "user-skills"
    _write_skill(user_root, CONSULT_SKILL_NAME)
    rail = SkillUseRail(str(user_root), include_tools=False)
    primary = _model(AssistantMessage(content="must not run"))
    deep, _ = deep_factory(primary, rails=[rail])
    with pytest.raises(BaseError, match="MoA consultation skill") as caught:
        await deep.invoke("task", session=_session(deep))
    assert caught.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR
    assert isinstance(caught.value.cause, ValueError)
    primary.invoke.assert_not_awaited()
    assert SkillUseRail._normalize_skill_dirs(rail.skills_dir) == [user_root.resolve()]


@pytest.mark.asyncio
@pytest.mark.level1
@pytest.mark.parametrize("source", ["other_skill_root", "retired_builtin_root"])
async def test_session_baseline_shadow_fails_without_rewriting_persisted_skills(deep_factory, tmp_path, source) -> None:
    retired_skill = (
        CONSULT_SKILL_ROOT.parents[2] / "resources" / "skills" / CONSULT_SKILL_NAME
        if source == "retired_builtin_root"
        else _write_skill(tmp_path / "previous-skills", CONSULT_SKILL_NAME)
    )
    primary = _model(AssistantMessage(content="must not run"))
    deep, reference = deep_factory(primary)
    session = _session(deep)
    session.update_state(
        {
            "skill_use": {
                "schema_version": 1,
                "baseline_skills": [
                    {
                        "name": CONSULT_SKILL_NAME,
                        "description": "Skill from an earlier native session baseline.",
                        "directory": str(retired_skill),
                    }
                ],
            },
        }
    )
    before = deepcopy(session.get_state("skill_use"))
    with pytest.raises(BaseError, match="MoA consultation skill.*session baseline"):
        await deep.invoke("task", session=session)
    assert session.get_state("skill_use") == before
    primary.invoke.assert_not_awaited()
    reference.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_custom_skill_tool_conflict_preserves_the_existing_tool(deep_factory) -> None:
    custom = _NamedTool("skill_tool")
    primary = _model(AssistantMessage(content="must not run"))
    deep, _ = deep_factory(primary, tools=[custom])
    with pytest.raises(BaseError, match="MoA consultation skill.*skill_tool"):
        await deep.invoke("task", session=_session(deep))
    primary.invoke.assert_not_awaited()
    assert deep.ability_manager.get("skill_tool") is custom.card
    assert deep.ability_manager.get(CONSULT_TOOL) is None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_consult_tool_conflict_rolls_back_only_new_root(deep_factory, tmp_path) -> None:
    user_root = tmp_path / "user-skills"
    _write_skill(user_root, "user-example")
    rail = SkillUseRail(str(user_root), include_tools=False)
    custom = _NamedTool(CONSULT_TOOL)
    deep, _ = deep_factory(_model(), rails=[rail], tools=[custom])
    with pytest.raises(BaseError, match=CONSULT_TOOL):
        await deep._ensure_initialized()
    assert SkillUseRail._normalize_skill_dirs(rail.skills_dir) == [user_root.resolve()]
    assert deep.ability_manager.get(CONSULT_TOOL) is custom.card


@pytest.mark.asyncio
@pytest.mark.level1
async def test_failed_consult_registration_rolls_back_the_native_mount(deep_factory, tmp_path, monkeypatch) -> None:
    user_root = tmp_path / "user-skills"
    _write_skill(user_root, "user-example")
    rail = SkillUseRail(str(user_root), include_tools=False)
    deep, _ = deep_factory(_model(), rails=[rail])
    original = deep.ability_manager.add_ability

    def add_ability(card, ability, *args, **kwargs):
        if card.name == CONSULT_TOOL:
            return SimpleNamespace(added=False)
        return original(card, ability, *args, **kwargs)

    monkeypatch.setattr(deep.ability_manager, "add_ability", add_ability)
    with pytest.raises(BaseError, match=CONSULT_TOOL) as caught:
        await deep._ensure_initialized()
    assert caught.value.status is StatusCode.DEEPAGENT_RUNTIME_ERROR
    assert SkillUseRail._normalize_skill_dirs(rail.skills_dir) == [user_root.resolve()]
    assert deep.ability_manager.get(CONSULT_TOOL) is None
    assert deep.ability_manager.get("skill_tool") is not None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_failed_initialization_retry_keeps_user_callbacks_registered_once(deep_factory, monkeypatch) -> None:
    class CountInvocations(DeepAgentRail):
        priority = 50

        def __init__(self):
            super().__init__()
            self.initializations = 0
            self.invocations = 0

        def init(self, agent):
            del agent
            self.initializations += 1

        async def before_invoke(self, ctx):
            del ctx
            self.invocations += 1

    counter = CountInvocations()
    deep, _ = deep_factory(_model(AssistantMessage(content="done")), rails=[counter])
    original = deep.ability_manager.add_ability

    def add_ability(card, ability, *args, **kwargs):
        if card.name == CONSULT_TOOL:
            return SimpleNamespace(added=False)
        return original(card, ability, *args, **kwargs)

    monkeypatch.setattr(deep.ability_manager, "add_ability", add_ability)
    with pytest.raises(BaseError, match=CONSULT_TOOL):
        await deep._ensure_initialized()
    monkeypatch.setattr(deep.ability_manager, "add_ability", original)
    result = await deep.invoke("task", session=_session(deep))
    assert result["output"] == "done"
    assert counter.initializations == 1
    assert counter.invocations == 1
    assert len(_unique_rails(deep, SkillUseRail)) == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_hot_configure_transfers_only_the_owned_root_to_the_new_user_skill_rail(deep_factory, tmp_path) -> None:
    first_root = tmp_path / "first-skills"
    second_root = tmp_path / "second-skills"
    _write_skill(first_root, "first-example")
    _write_skill(second_root, "second-example")
    first = SkillUseRail(str(first_root), skill_mode="all", include_tools=False)
    second = SkillUseRail(str(second_root), skill_mode="all", include_tools=False)
    deep, _ = deep_factory(_model(AssistantMessage(content="done")), rails=[first])
    await deep._ensure_initialized()
    moa = _unique_rails(deep, MoARail)[0]
    deep.configure(replace(deep.deep_config, rails=[second]))
    session = _session(deep)
    await deep.invoke("task", session=session)
    assert _unique_rails(deep, SkillUseRail) == [second]
    assert SkillUseRail._normalize_skill_dirs(first.skills_dir) == [first_root.resolve()]
    assert SkillUseRail._normalize_skill_dirs(second.skills_dir) == [
        second_root.resolve(),
        CONSULT_SKILL_ROOT.resolve(),
    ]
    assert {skill.name for skill in second.get_skills_for_session(session)} == {
        "second-example",
        CONSULT_SKILL_NAME,
    }
    await deep.unregister_rail(moa)
    assert SkillUseRail._normalize_skill_dirs(second.skills_dir) == [second_root.resolve()]
    assert deep.ability_manager.get("skill_tool") is not None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_new_same_name_skill_is_rejected_before_the_next_model_call(deep_factory, tmp_path) -> None:
    user_root = tmp_path / "user-skills"
    _write_skill(user_root, "user-example")
    rail = SkillUseRail(str(user_root), include_tools=False)

    class AddConflictingSkill(DeepAgentRail):
        priority = 5

        async def after_react_iteration(self, ctx):
            del ctx
            _write_skill(user_root, CONSULT_SKILL_NAME)

    primary = _model(_call("echo"), AssistantMessage(content="must not run"))
    deep, _ = deep_factory(primary, rails=[rail, AddConflictingSkill()], tools=[_NamedTool("echo")])
    with pytest.raises(BaseError, match="MoA consultation skill.*conflict"):
        await deep.invoke("task", session=_session(deep))
    assert primary.invoke.await_count == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_repeated_initialization_is_idempotent(deep_factory) -> None:
    deep, _ = deep_factory(_model(AssistantMessage(content="done")))
    await deep._ensure_initialized()
    moa = _unique_rails(deep, MoARail)[0]
    rail = _unique_rails(deep, SkillUseRail)[0]
    skill_card = deep.ability_manager.get("skill_tool")
    consult_card = deep.ability_manager.get(CONSULT_TOOL)
    before = list(rail.skills_dir)
    moa.init(deep)
    moa.init(deep)
    await deep._ensure_initialized()
    assert rail.skills_dir == before
    assert before.count(str(CONSULT_SKILL_ROOT)) == 1
    assert deep.ability_manager.get("skill_tool") is skill_card
    assert deep.ability_manager.get(CONSULT_TOOL) is consult_card
    assert len(_unique_rails(deep, SkillUseRail)) == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_unregister_removes_auto_created_companion_and_allows_plain_execution(deep_factory) -> None:
    deep, _ = deep_factory(_model(AssistantMessage(content="done")))
    await deep._ensure_initialized()
    await deep.unregister_rail(_unique_rails(deep, MoARail)[0])
    assert _unique_rails(deep, SkillUseRail) == []
    assert deep.ability_manager.get("skill_tool") is None
    assert deep.ability_manager.get(CONSULT_TOOL) is None
    result = await deep.invoke("task", session=_session(deep))
    assert result["output"] == "done"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_unregister_preserves_shared_user_skill_rail_and_its_tools(deep_factory, tmp_path) -> None:
    user_root = tmp_path / "user-skills"
    _write_skill(user_root, "user-example")
    rail = SkillUseRail(str(user_root), include_tools=False)
    deep, _ = deep_factory(_model(AssistantMessage(content="done")), rails=[rail])
    await deep._ensure_initialized()
    card = deep.ability_manager.get("skill_tool")
    await deep.unregister_rail(_unique_rails(deep, MoARail)[0])
    assert _unique_rails(deep, SkillUseRail) == [rail]
    assert CONSULT_SKILL_ROOT not in SkillUseRail._normalize_skill_dirs(rail.skills_dir)
    assert deep.ability_manager.get("skill_tool") is card
    assert deep.ability_manager.get(CONSULT_TOOL) is None
    result = await deep.invoke("task", session=_session(deep))
    assert result["output"] == "done"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_unregister_keeps_a_builtin_root_that_the_user_already_mounted(deep_factory, tmp_path) -> None:
    user_root = tmp_path / "user-skills"
    _write_skill(user_root, "user-example")
    roots = [str(user_root), str(CONSULT_SKILL_ROOT)]
    rail = SkillUseRail(roots, include_tools=False)
    deep, _ = deep_factory(_model(), rails=[rail])
    await deep._ensure_initialized()
    await deep.unregister_rail(_unique_rails(deep, MoARail)[0])
    assert rail.skills_dir == roots
    assert deep.ability_manager.get("skill_tool") is not None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_missing_sys_operation_is_a_configuration_error(deep_factory) -> None:
    primary = _model(AssistantMessage(content="must not run"))
    deep, reference = deep_factory(primary, operation=False)
    with pytest.raises(BaseError, match="MoA consultation skill.*SysOperation"):
        await deep.invoke("task", session=_session(deep))
    assert deep.deep_config.sys_operation is None
    assert deep.ability_manager.get("skill_tool") is None
    primary.invoke.assert_not_awaited()
    reference.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_unreadable_package_resource_fails_instead_of_placeholder_metadata(deep_factory) -> None:
    operation = SysOperation(SysOperationCard(id=f"unreadable-skill-{uuid4().hex}"))
    operation.fs().read_file = AsyncMock(return_value=SimpleNamespace(code=1, message="access denied", data=None))
    primary = _model(AssistantMessage(content="must not run"))
    deep, reference = deep_factory(primary, operation=operation)
    with pytest.raises(BaseError, match="MoA consultation skill"):
        await deep.invoke("task", session=_session(deep))
    primary.invoke.assert_not_awaited()
    reference.invoke.assert_not_awaited()
    assert any(Path(call.args[0]) == CONSULT_SKILL_FILE for call in operation.fs().read_file.await_args_list)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_verifier_mode_requires_no_sys_operation_or_skill_tool(deep_factory) -> None:
    primary = _model(AssistantMessage(content="done"))
    deep, reference = deep_factory(primary, operation=False, mode="verifier_routed")
    result = await deep.invoke("task", session=_session(deep))
    assert result["output"] == "done"
    assert deep.deep_config.sys_operation is None
    assert _unique_rails(deep, SkillUseRail) == []
    assert deep.ability_manager.get("skill_tool") is None
    reference.invoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_native_skill_then_consultation_then_transient_proposal(deep_factory) -> None:
    primary = _model(
        _call("skill_tool", {"skill_name": CONSULT_SKILL_NAME}, call_id="read-skill"),
        _call(CONSULT_TOOL, call_id="consult"),
        AssistantMessage(content="done"),
    )
    deep, reference = deep_factory(primary)
    session = _session(deep)
    result = await deep.invoke("investigate alternatives", session=session)
    assert result["output"] == "done"
    reference.invoke.assert_awaited_once()
    requests = primary.invoke.await_args_list
    assert _skill_body() not in _messages_text(requests[0].kwargs["messages"])
    assert _skill_body() in _messages_text(requests[1].kwargs["messages"])
    final = requests[2].kwargs["messages"]
    assert final[-1].role == "user" and final[-1].metadata["transient"] is True
    assert _REFERENCE_PROPOSAL in final[-1].content
    assert reference.invoke.await_args.kwargs["tools"] is None
    stored = deep.react_agent.context_engine.get_context(session_id=session.get_session_id()).get_messages()
    assert _REFERENCE_PROPOSAL not in str(stored)
    assert _skill_body() in _messages_text(stored)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_startup_cancellation_releases_moa_and_preserves_user_callbacks_for_retry(deep_factory, monkeypatch):
    class HostRail(DeepAgentRail):
        priority = 50

        def __init__(self):
            super().__init__()
            self.initializations = 0
            self.invocations = 0

        def init(self, agent):
            del agent
            self.initializations += 1

        async def before_invoke(self, ctx):
            del ctx
            self.invocations += 1

    host = HostRail()
    deep, _ = deep_factory(_model(AssistantMessage(content="done")), rails=[host])
    moa = _unique_rails(deep, MoARail)[0]
    validate = moa.validate_skill_loading
    monkeypatch.setattr(moa, "validate_skill_loading", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await deep._ensure_initialized()
    assert deep.is_registered_rail(host)
    assert deep.ability_manager.get(CONSULT_TOOL) is None
    assert deep.ability_manager.get("skill_tool") is None

    monkeypatch.setattr(moa, "validate_skill_loading", validate)
    result = await deep.invoke("task", session=_session(deep))
    assert result["output"] == "done"
    assert host.initializations == host.invocations == 1
    assert len(_unique_rails(deep, SkillUseRail)) == 1
    assert len(_unique_rails(deep, MoARail)) == 1


@pytest.mark.level1
@pytest.mark.asyncio
@pytest.mark.parametrize("failure_event", ["before_invoke", "before_model_call"])
async def test_runtime_skill_validation_aborts_with_its_framework_cause(deep_factory, monkeypatch, failure_event):
    primary = _model(AssistantMessage(content="must not run"))
    deep, reference = deep_factory(primary)
    await deep._ensure_initialized()
    moa = _unique_rails(deep, MoARail)[0]
    error = build_error(StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, error_msg="MoA skill no longer available")
    results = [error] if failure_event == "before_invoke" else [None, error]
    monkeypatch.setattr(moa, "validate_skill_loading", AsyncMock(side_effect=results))
    with pytest.raises(BaseError, match="MoA skill no longer available") as caught:
        await deep.invoke("task", session=_session(deep))
    assert caught.value is error
    primary.invoke.assert_not_awaited()
    reference.invoke.assert_not_awaited()
    assert active_moa.get() is None


@pytest.mark.parametrize("path_kind", ["wrong_name", "missing_file", "empty"])
@pytest.mark.level1
def test_candidate_configuration_rejects_invalid_directory(tmp_path, path_kind):
    directory = tmp_path / ("other" if path_kind == "wrong_name" else CONSULT_SKILL_NAME)
    directory.mkdir()
    if path_kind == "wrong_name":
        (directory / "SKILL.md").write_text("body", encoding="utf-8")
    with pytest.raises(BaseError, match="consultation_skill_dir"):
        MoAConfig(reference_models=(_model(),), consultation_skill_dir="" if path_kind == "empty" else str(directory))


@pytest.mark.level1
def test_candidate_is_normalized_and_rejected_for_verifier(tmp_path):
    directory = _write_skill(tmp_path, CONSULT_SKILL_NAME)
    config = MoAConfig(reference_models=(_model(),), consultation_skill_dir=str(directory / "."))
    assert config.consultation_skill_dir == str(directory.resolve())
    with pytest.raises(BaseError, match="only supported"):
        replace(config, mode="verifier_routed", scorer=lambda *_: 0.5)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_candidate_native_loading_and_consultation_exclude_sibling_skills(deep_factory, tmp_path):
    directory = _write_skill(tmp_path / "candidates", CONSULT_SKILL_NAME)
    _write_skill(directory.parent, "must-not-be-discovered")
    primary = _model(
        _call("skill_tool", {"skill_name": CONSULT_SKILL_NAME}),
        _call(CONSULT_TOOL),
        AssistantMessage(content="done"),
    )
    deep, reference = deep_factory(primary, skill_dir=str(directory))
    session = _session(deep)
    result = await deep.invoke("task", session=session)
    assert result["output"] == "done"
    rail = _unique_rails(deep, SkillUseRail)[0]
    assert SkillUseRail._normalize_skill_dirs(rail.skills_dir) == [directory.resolve()]
    assert [skill.name for skill in rail.skills_meta] == [CONSULT_SKILL_NAME]
    assert "User body." in _messages_text(primary.invoke.await_args_list[1].kwargs["messages"])
    reference.invoke.assert_awaited_once()
    assert primary.invoke.await_args_list[-1].kwargs["messages"][-1].metadata["transient"] is True


@pytest.mark.asyncio
@pytest.mark.level1
async def test_candidate_reuses_user_rail_and_unmounts_only_owned_directory(deep_factory, tmp_path):
    directory = _write_skill(tmp_path / "candidates", CONSULT_SKILL_NAME)
    user_root = tmp_path / "user"
    _write_skill(user_root, "ordinary-skill")
    rail = SkillUseRail(str(user_root), skill_mode="auto_list", include_tools=False)
    deep, _ = deep_factory(_model(), rails=[rail], skill_dir=str(directory))
    await deep._ensure_initialized()
    tool = Runner.resource_mgr.get_tool(deep.ability_manager.get("skill_tool").id)
    output = await tool.invoke({"skill_name": CONSULT_SKILL_NAME}, session=_session(deep))
    assert output.success and output.data["skill_directory"] == str(directory)
    assert "User body." in output.data["content"]
    await deep.unregister_rail(_unique_rails(deep, MoARail)[0])
    assert SkillUseRail._normalize_skill_dirs(rail.skills_dir) == [user_root.resolve()]
    assert rail.skill_mode == "auto_list"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_candidate_cannot_overlay_packaged_skill_or_change_after_construction(deep_factory, tmp_path):
    directory = _write_skill(tmp_path / "candidate-a", CONSULT_SKILL_NAME)
    second = _write_skill(tmp_path / "candidate-b", CONSULT_SKILL_NAME)
    rail = SkillUseRail(str(CONSULT_SKILL_ROOT), include_tools=False)
    primary = _model(AssistantMessage(content="must not run"))
    deep, _ = deep_factory(primary, rails=[rail], skill_dir=str(directory))
    with pytest.raises(BaseError, match="name conflict"):
        await deep.invoke("task", session=_session(deep))
    primary.invoke.assert_not_awaited()
    with pytest.raises(BaseError, match="cannot change") as caught:
        deep.configure(replace(deep.deep_config, moa=replace(deep.deep_config.moa, consultation_skill_dir=str(second))))
    assert caught.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR


@pytest.mark.asyncio
@pytest.mark.level1
async def test_candidate_cannot_reuse_another_native_session_baseline(deep_factory, tmp_path):
    first = _write_skill(tmp_path / "candidate-a", CONSULT_SKILL_NAME)
    second = _write_skill(tmp_path / "candidate-b", CONSULT_SKILL_NAME)
    deep, _ = deep_factory(_model(AssistantMessage(content="done")), skill_dir=str(second))
    session = _session(deep)
    baseline = {
        "schema_version": 1,
        "baseline_skills": [{"name": CONSULT_SKILL_NAME, "description": "old candidate", "directory": str(first)}],
    }
    session.update_state({"skill_use": baseline})
    with pytest.raises(Exception, match="session baseline"):
        await deep.invoke("task", session=session)
    assert session.get_state("skill_use") == baseline


def _reference(content: str = "private reference suggestion") -> SimpleNamespace:
    return SimpleNamespace(invoke=AsyncMock(return_value=AssistantMessage(content=content)))


def _state() -> MoAState:
    return MoAState(
        baseline=[UserMessage(content="trusted task"), AssistantMessage(content="completed action")],
        active_key=(object(), 2),
        completed_turns=1,
    )


@pytest.mark.level0
@pytest.mark.parametrize("language", ["cn", "en"])
def test_reference_card_uses_shared_metadata_and_keeps_execution_flags(language: str) -> None:
    tool = ConsultReferenceModels(MoAConfig(reference_models=(_reference(),)), language=language)
    assert tool.card.name == CONSULT_TOOL
    assert tool.card.description == get_tool_description(CONSULT_TOOL, language)
    assert tool.card.input_params == get_tool_input_params(CONSULT_TOOL, language)
    assert tool.card.parallel_safe is False
    assert tool.card.idempotent is False
    assert tool.card.stateless is False
    assert tool.card.exposure is ToolExposure.DIRECT


@pytest.mark.level1
@pytest.mark.asyncio
@pytest.mark.parametrize("inputs", [{"context": "untrusted override"}, [], None])
async def test_reference_tool_rejects_arguments_without_spending_budget(inputs) -> None:
    reference = _reference()
    tool = ConsultReferenceModels(MoAConfig(reference_models=(reference,)))
    state = _state()
    token = active_moa.set(state)
    try:
        output = await tool.invoke(inputs)
    finally:
        active_moa.reset(token)
    assert output.success is False
    assert output.error == "Consultation is unavailable for this call."
    assert state.reference_calls == 0
    reference.invoke.assert_not_awaited()


@pytest.mark.level1
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("closed", True, "Consultation is unavailable for this call."),
        ("baseline", [], "Consultation is unavailable for this call."),
        ("completed_turns", 0, "Complete one primary-model turn before consulting."),
        (
            "consultation_rejection",
            "consult_reference_models must be called alone.",
            "consult_reference_models must be called alone.",
        ),
    ],
)
async def test_reference_tool_admission_failures_do_not_consult(field: str, value, error: str) -> None:
    reference = _reference()
    tool = ConsultReferenceModels(MoAConfig(reference_models=(reference,)))
    state = _state()
    setattr(state, field, value)
    token = active_moa.set(state)
    try:
        output = await tool.invoke({})
    finally:
        active_moa.reset(token)
    assert output.success is False
    assert output.error == error
    assert state.reference_calls == 0
    reference.invoke.assert_not_awaited()


@pytest.mark.level1
@pytest.mark.asyncio
async def test_reference_tool_requires_an_active_invocation() -> None:
    reference = _reference()
    token = active_moa.set(None)
    try:
        output = await ConsultReferenceModels(MoAConfig(reference_models=(reference,))).invoke({})
    finally:
        active_moa.reset(token)
    assert output.success is False
    reference.invoke.assert_not_awaited()


@pytest.mark.level0
@pytest.mark.asyncio
async def test_partial_reference_success_is_bound_to_next_turn_and_receipt_is_private() -> None:
    reference = _reference()
    failed = SimpleNamespace(invoke=AsyncMock(side_effect=RuntimeError("reference unavailable")))
    tool = ConsultReferenceModels(MoAConfig(reference_models=(reference, failed)))
    state = _state()
    token = active_moa.set(state)
    try:
        output = await tool.invoke({})
    finally:
        active_moa.reset(token)
    assert output.success is True
    assert output.data == {"reference_count": 2, "success_count": 1}
    assert "private reference suggestion" not in str(output)
    assert state.reference_calls == 1
    assert state.pending.target == (state.active_key[0], 3)
    assert state.pending.sources[0].text == "private reference suggestion"
    assert reference.invoke.await_args.kwargs["tools"] is None


@pytest.mark.level1
@pytest.mark.asyncio
async def test_exhausted_reference_budget_does_not_make_another_request() -> None:
    reference = _reference()
    config = MoAConfig(reference_models=(reference,), verifier=VerifierConfig(max_reference_calls=1))
    state = _state()
    state.reference_calls = 1
    token = active_moa.set(state)
    try:
        output = await ConsultReferenceModels(config).invoke({})
    finally:
        active_moa.reset(token)
    assert output.success is False
    assert output.error == "Reference-call budget exhausted."
    assert state.reference_calls == 1
    reference.invoke.assert_not_awaited()


@pytest.mark.level1
@pytest.mark.asyncio
async def test_failed_references_report_failure_and_consume_the_attempt() -> None:
    reference = SimpleNamespace(invoke=AsyncMock(side_effect=RuntimeError("reference unavailable")))
    state = _state()
    token = active_moa.set(state)
    try:
        output = await ConsultReferenceModels(MoAConfig(reference_models=(reference,))).invoke({})
    finally:
        active_moa.reset(token)
    assert output.success is False
    assert output.data == {"reference_count": 1, "success_count": 0}
    assert output.error == "All reference models failed."
    assert state.reference_calls == 1
    assert state.pending is None


@pytest.mark.level1
@pytest.mark.asyncio
async def test_consultation_cancellation_propagates() -> None:
    reference = SimpleNamespace(invoke=AsyncMock(side_effect=asyncio.CancelledError()))
    state = _state()
    token = active_moa.set(state)
    try:
        with pytest.raises(asyncio.CancelledError):
            await ConsultReferenceModels(MoAConfig(reference_models=(reference,))).invoke({})
    finally:
        active_moa.reset(token)
    assert state.reference_calls == 1
    assert state.pending is None


@pytest.mark.level0
@pytest.mark.asyncio
async def test_stream_without_a_target_keeps_receipt_and_does_not_bind_advice() -> None:
    state = _state()
    state.active_key = None
    token = active_moa.set(state)
    try:
        tool = ConsultReferenceModels(MoAConfig(reference_models=(_reference(),)))
        outputs = [output async for output in tool.stream({})]
    finally:
        active_moa.reset(token)
    assert len(outputs) == 1
    assert outputs[0].success is True
    assert outputs[0].data == {"reference_count": 1, "success_count": 1}
    assert state.pending is None


@pytest.mark.level0
def test_reference_metadata_is_registered_and_bilingual() -> None:
    provider = ConsultReferenceModelsMetadataProvider()
    provider.validate()
    validate_all_tool_providers()
    cn = get_tool_description(CONSULT_TOOL, "cn")
    en = get_tool_description(CONSULT_TOOL, "en")
    assert cn != en
    for description in (cn, en):
        assert 'skill_tool(skill_name="consult-reference-models")' in description
    assert get_tool_description(CONSULT_TOOL, "unknown") == cn


@pytest.mark.level0
@pytest.mark.parametrize("language", ["cn", "en"])
def test_reference_schema_accepts_only_an_empty_object(language: str) -> None:
    schema = get_tool_input_params(CONSULT_TOOL, language)
    assert schema == {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    schema["properties"]["untrusted_context"] = {"type": "string"}
    assert get_tool_input_params(CONSULT_TOOL, language)["properties"] == {}
