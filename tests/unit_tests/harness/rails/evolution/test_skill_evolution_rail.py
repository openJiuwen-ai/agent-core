# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionPatch,
    EvolutionRecord,
    EvolutionTarget,
)
from openjiuwen.agent_evolving.experience import (
    ExperienceTracker,
    OnlineEvolutionOrchestrator,
    OnlineEvolutionResult,
)
from openjiuwen.agent_evolving.experience.skill_experience_manager import ExperienceManager
from openjiuwen.agent_evolving.optimizer.llm_resilience import LLMInvokePolicy
from openjiuwen.agent_evolving.signal import (
    EvolutionSignal,
    SignalDetector,
    make_evolution_signal,
)
from openjiuwen.agent_evolving.trajectory.types import LLMCallDetail, ToolCallDetail, Trajectory, TrajectoryStep
from openjiuwen.agent_evolving.types import ApplyResult
from openjiuwen.core.foundation.llm import SystemMessage
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
    ModelCallInputs,
    TaskIterationInputs,
    ToolCallInputs,
)
from openjiuwen.harness.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.evolution.approval_runtime import EvolutionApprovalRuntime
from openjiuwen.harness.rails.evolution.contracts import (
    EvolutionRequestResult,
    SimplifyRequestResult,
)
from openjiuwen.harness.rails.evolution.review.materials import build_review_scoped_materials
from openjiuwen.harness.rails.evolution.review.runtime import EvolutionReviewRuntime
from openjiuwen.harness.rails.evolution.skill_evolution_rail import (
    _MAX_PROCESSED_SIGNAL_KEYS,
    SkillEvolutionRail,
)
from openjiuwen.harness.rails.subagent import SubagentRail


def _default_review_runtime() -> EvolutionReviewRuntime:
    return EvolutionReviewRuntime()


def _make_rail(
    tmp_path,
    *,
    auto_scan: bool = True,
    auto_save: bool = True,
    disabled_skills=None,
    language: str = "cn",
    review_agent_max_iterations: int = 10,
    fuzzy_review: bool = True,
    fuzzy_review_interval: int = 5,
) -> SkillEvolutionRail:
    rail = SkillEvolutionRail(
        skills_dir=str(tmp_path / "skills"),
        llm=Mock(),
        model="dummy-model",
        auto_scan=auto_scan,
        auto_save=auto_save,
        review_runtime=_default_review_runtime(),
        language=language,
        disabled_skills=disabled_skills,
        review_agent_max_iterations=review_agent_max_iterations,
        fuzzy_review=fuzzy_review,
        fuzzy_review_interval=fuzzy_review_interval,
    )
    rail._evolution_store = Mock()
    rail._evolution_store.read_skill_content = AsyncMock(return_value="# skill")
    rail._evolution_store.get_pending_records = AsyncMock(return_value=[])
    rail._evolution_store.append_record = AsyncMock()
    rail._evolver = Mock()
    rail._online_updater = Mock()
    rail._manager = ExperienceManager(
        store=rail._evolution_store,
        scorer=rail._scorer,
        subject_kind="skill",
        language=rail._language,
        skill_ops=rail._skill_ops,
        pending_approval_snapshots=rail._pending_approval_snapshots,
        pending_governance=rail._pending_governance,
    )
    rail._approval_runtime = EvolutionApprovalRuntime(
        manager=rail._manager,
        pending_approval_snapshots=rail._pending_approval_snapshots,
    )
    rail._online_orchestrator = OnlineEvolutionOrchestrator(
        store=rail._evolution_store,
        updater=rail._online_updater,
        manager=rail._manager,
        skill_ops=rail._skill_ops,
        stage_source="experience_updater",
    )
    rail._experience_tracker = ExperienceTracker(
        store=rail._evolution_store,
        scorer=rail._scorer,
        eval_interval=rail._eval_interval,
    )
    return rail


def _make_record(skill_name: str, *, content: str = "experience content") -> EvolutionRecord:
    return EvolutionRecord.make(
        source=f"signal:{skill_name}",
        context="ctx",
        change=EvolutionPatch(
            section="Troubleshooting",
            action="append",
            content=content,
            target=EvolutionTarget.BODY,
        ),
    )


def _stage_approval_request(rail: SkillEvolutionRail, skill_name: str, records: list[EvolutionRecord]):
    return rail._manager.stage_records(skill_name, records, requires_approval=True)


def _make_signal(skill_name: str | None, *, excerpt: str = "signal excerpt") -> EvolutionSignal:
    return make_evolution_signal(
        signal_type="execution_failure",
        section="Troubleshooting",
        excerpt=excerpt,
        tool_name="bash",
        skill_name=skill_name,
        source="passive_conversation",
    )


def _no_records_result(skill_name: str = "skill-a") -> OnlineEvolutionResult:
    return OnlineEvolutionResult(
        skill_name=skill_name,
        status="no_evolution_no_records",
        message=f"no applied updates for skill={skill_name}",
    )


def _failed_result(status: str, skill_name: str = "skill-a") -> OnlineEvolutionResult:
    return OnlineEvolutionResult(
        skill_name=skill_name,
        status=status,
        message=f"{status} for skill={skill_name}",
    )


def _handle_result(request: Any, *, online_result: OnlineEvolutionResult | None = None) -> OnlineEvolutionResult:
    if online_result is None:
        online_result = OnlineEvolutionResult(
            skill_name="skill-a",
            status="staged" if request is not None else "no_evolution_no_records",
            request=request,
        )
    return online_result


def _staged_result(request: Any, skill_name: str = "skill-a") -> OnlineEvolutionResult:
    return OnlineEvolutionResult(
        skill_name=skill_name,
        status="staged",
        request=request,
    )


def _trajectory_with_messages(messages: list[dict]) -> Trajectory:
    return Trajectory(
        execution_id="exec-1",
        steps=[TrajectoryStep(kind="llm", detail=LLMCallDetail(model="test-model", messages=messages))],
        source="online",
    )


def _bind_active_request_evidence(
    rail: SkillEvolutionRail,
    messages: list[dict],
    *,
    skill_names: list[str] | None = None,
) -> Trajectory:
    trajectory = _trajectory_with_messages(messages)
    rail._builder = Mock()
    rail._builder.build.return_value = trajectory
    rail._evolution_store.list_skill_names = Mock(return_value=skill_names or ["skill-a"])
    rail._evolution_store.skill_exists = Mock(return_value=True)
    rail._evolution_store.resolve_skill_dir = Mock(return_value=None)
    return trajectory


def _active_request_detector(
    *,
    trajectory_signals: list[EvolutionSignal] | None = None,
    user_signals: list[EvolutionSignal] | None = None,
    trajectory_error: Exception | None = None,
) -> Mock:
    detector = Mock()
    detector.bind_llm.return_value = detector
    if trajectory_error is None:
        detector.detect_trajectory_signals.return_value = trajectory_signals or []
    else:
        detector.detect_trajectory_signals.side_effect = trajectory_error
    detector.detect_user_intent = AsyncMock(return_value=user_signals or [])
    return detector


def _approval_events(events):
    return [event for event in events if event.type == "chat.ask_user_question"]


def _progress_events(events):
    return [event for event in events if event.payload.get("evolution_meta", {}).get("event_kind") == "progress"]


def _find_rails_by_type(*rails):
    def _find(types):
        return [rail for rail in rails if isinstance(rail, types)]

    return Mock(side_effect=_find)


class _StateSession:
    def __init__(self, state=None):
        self.state = dict(state or {})

    def get_state(self, key):
        return self.state.get(key)

    def update_state(self, state):
        self.state.update(state)


class _MsgContext:
    def __init__(self, messages=None, *, raise_error: bool = False):
        self._messages = list(messages) if messages else []
        self._raise_error = raise_error

    def get_messages(self):
        if self._raise_error:
            raise RuntimeError("get_messages failed")
        return self._messages


class _DummyToolMsg:
    def __init__(self, content: Any):
        self.content = content


# =============================================================================
# Basic Utility Tests
# =============================================================================


def test_extract_file_path():
    rail = SkillEvolutionRail(
        skills_dir="skills",
        llm=Mock(),
        model="dummy",
        review_runtime=_default_review_runtime(),
    )
    cases = [
        ({"file_path": "/a/b/SKILL.md"}, "/a/b/SKILL.md"),
        ({}, ""),
        ('{"file_path":"C:/skills/x/SKILL.md"}', "C:/skills/x/SKILL.md"),
        ("not-json", ""),
        (None, ""),
        (123, ""),
    ]
    for args, expected in cases:
        assert rail._extract_file_path(args) == expected


def test_parse_messages():
    rail = SkillEvolutionRail(
        skills_dir="skills",
        llm=Mock(),
        model="dummy",
        review_runtime=_default_review_runtime(),
    )
    tool_call = SimpleNamespace(id="tc1", name="read_file", arguments='{"file_path":"x"}')
    msg_obj = SimpleNamespace(
        role="assistant",
        content="done",
        tool_calls=[tool_call],
        name="assistant_name",
    )
    raw = [{"role": "user", "content": "hi"}, msg_obj]
    parsed = rail._parse_messages(raw)

    assert parsed[0] == {"role": "user", "content": "hi"}
    assert parsed[1]["role"] == "assistant"
    assert parsed[1]["content"] == "done"
    assert parsed[1]["name"] == "assistant_name"
    assert parsed[1]["tool_calls"][0]["id"] == "tc1"
    assert parsed[1]["tool_calls"][0]["name"] == "read_file"


def test_trajectory_sink_defaults_member_role_to_teammate(tmp_path):
    rail = _make_rail(tmp_path)

    rail.set_trajectory_sink(Mock(), team_id="team-a")

    assert rail._member_role == "teammate"


def test_properties_and_clear_processed_signals(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    rail.processed_signal_keys.add(("a", "b"))
    rail.auto_scan = False
    rail.auto_save = False
    rail.clear_processed_signals()

    assert rail.auto_scan is False
    assert rail.fuzzy_review is True
    assert rail.auto_save is False
    assert rail.processed_signal_keys == set()

    rail.fuzzy_review = False

    assert rail.auto_scan is False
    assert rail.fuzzy_review is False

    rail.fuzzy_review = True

    assert rail.fuzzy_review is True


def test_auto_save_defaults_false_and_setter_still_updates(tmp_path):
    rail = SkillEvolutionRail(
        skills_dir=str(tmp_path),
        llm=Mock(),
        model="dummy",
        review_runtime=_default_review_runtime(),
    )

    assert rail.auto_save is False

    rail.auto_save = True
    assert rail.auto_save is True

    rail.auto_save = False
    assert rail.auto_save is False


def test_auto_save_setter_updates_only_local_state(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    rail.auto_save = True

    assert rail.auto_save is True

    rail.auto_save = False

    assert rail.auto_save is False


def test_skill_rail_init_registers_canonical_evolution_tools(tmp_path):
    rail = _make_rail(tmp_path)
    ability_manager = SimpleNamespace(
        add=Mock(return_value=SimpleNamespace(added=True)),
        remove=Mock(),
    )
    agent = SimpleNamespace(
        card=SimpleNamespace(id="agent-1"),
        ability_manager=ability_manager,
        find_rails_by_type=_find_rails_by_type(),
    )

    with patch("openjiuwen.core.runner.Runner.resource_mgr.add_tool") as add_tool:
        rail.init(agent)

    tool_names = {tool.card.name for tool in rail._evolution_tools}
    assert tool_names == {
        "prepare_skill_evolution",
        "evolve_review_task",
        "list_skill_experiences",
        "read_skill_experiences",
        "evolve_skill_experiences",
        "simplify_skill_experiences",
    }
    assert add_tool.called
    assert {call.args[0].name for call in ability_manager.add.call_args_list} == tool_names


def test_skill_rail_active_review_plumbing_still_uses_skill_subject_kind(tmp_path):
    rail = _make_rail(tmp_path)
    subagent_rail = SubagentRail()
    agent = SimpleNamespace(
        card=SimpleNamespace(id="agent-1"),
        deep_config=SimpleNamespace(subagents=[]),
        ability_manager=SimpleNamespace(add=Mock(return_value=SimpleNamespace(added=True)), remove=Mock()),
        find_rails_by_type=_find_rails_by_type(subagent_rail),
        _registered_rails=[subagent_rail],
    )

    with patch("openjiuwen.core.runner.Runner.resource_mgr.add_tool"):
        rail.init(agent)

    prepare_tool = next(tool for tool in rail._evolution_tools if tool.card.name == "prepare_skill_evolution")
    submit_review_tool = next(
        tool for tool in agent.deep_config.subagents[0].tools if tool.card.name == "submit_evolution_review"
    )
    subject_schema = prepare_tool.card.input_params["properties"]["subject"]
    review_tool_subject_schema = submit_review_tool.card.input_params["properties"]["subject"]

    assert rail.subject_kind == "skill"
    assert rail.subject_label == "skill"
    assert subject_schema["properties"]["kind"]["enum"] == ["skill", "swarm-skill"]
    assert review_tool_subject_schema["properties"]["kind"]["enum"] == ["skill", "swarm-skill"]


def test_skill_rail_init_does_not_require_subagent_rail_when_deep_config_present(tmp_path):
    rail = _make_rail(tmp_path)
    agent = SimpleNamespace(
        card=SimpleNamespace(id="agent-1"),
        deep_config=SimpleNamespace(subagents=None),
        ability_manager=SimpleNamespace(add=Mock(), remove=Mock()),
        find_rails_by_type=_find_rails_by_type(),
    )

    with patch("openjiuwen.core.runner.Runner.resource_mgr.add_tool"):
        rail.init(agent)

    assert {tool.card.name for tool in rail._evolution_tools} >= {"evolve_review_task"}


def test_skill_rail_init_without_evolution_interrupt_rail_still_registers_tools(tmp_path):
    rail = _make_rail(tmp_path)
    ability_manager = SimpleNamespace(add=Mock(return_value=SimpleNamespace(added=True)), remove=Mock())
    agent = SimpleNamespace(
        card=SimpleNamespace(id="agent-1"),
        ability_manager=ability_manager,
        find_rails_by_type=_find_rails_by_type(),
    )

    with patch("openjiuwen.core.runner.Runner.resource_mgr.add_tool") as add_tool:
        rail.init(agent)

    tool_names = {tool.card.name for tool in rail._evolution_tools}
    assert tool_names == {
        "prepare_skill_evolution",
        "evolve_review_task",
        "list_skill_experiences",
        "read_skill_experiences",
        "evolve_skill_experiences",
        "simplify_skill_experiences",
    }
    assert add_tool.called
    assert {call.args[0].name for call in ability_manager.add.call_args_list} == tool_names


def test_skill_rail_init_refreshes_existing_subagent_rail_after_review_agent_registration(tmp_path):
    rail = _make_rail(tmp_path)
    subagent_rail = SubagentRail()
    subagent_rail.refresh_available_agents = Mock()
    agent = SimpleNamespace(
        card=SimpleNamespace(id="agent-1"),
        deep_config=SimpleNamespace(subagents=[]),
        ability_manager=SimpleNamespace(add=Mock(), remove=Mock()),
        find_rails_by_type=_find_rails_by_type(subagent_rail),
        _registered_rails=[subagent_rail],
    )

    with patch("openjiuwen.core.runner.Runner.resource_mgr.add_tool"):
        rail.init(agent)

    assert any(
        getattr(getattr(spec, "agent_card", None), "name", None) == "evolution_reviewer"
        for spec in agent.deep_config.subagents
    )
    subagent_rail.refresh_available_agents.assert_called_once_with(agent)


def test_skill_rail_init_does_not_refresh_pending_subagent_rail(tmp_path):
    rail = _make_rail(tmp_path)
    subagent_rail = SubagentRail()
    subagent_rail.refresh_available_agents = Mock()
    agent = SimpleNamespace(
        card=SimpleNamespace(id="agent-1"),
        deep_config=SimpleNamespace(subagents=[]),
        ability_manager=SimpleNamespace(add=Mock(), remove=Mock()),
        find_rails_by_type=_find_rails_by_type(subagent_rail),
        _registered_rails=[],
    )

    with patch("openjiuwen.core.runner.Runner.resource_mgr.add_tool"):
        rail.init(agent)

    subagent_rail.refresh_available_agents.assert_not_called()


def test_skill_rail_init_registers_evolve_review_task_without_subagent_rail(tmp_path):
    rail = _make_rail(tmp_path)
    registered_cards = []
    ability_manager = SimpleNamespace(
        add=Mock(side_effect=lambda card: registered_cards.append(card) or SimpleNamespace(added=True)),
        remove=Mock(),
    )
    agent = SimpleNamespace(
        card=SimpleNamespace(id="agent-1"),
        deep_config=SimpleNamespace(subagents=[]),
        ability_manager=ability_manager,
        find_rails_by_type=_find_rails_by_type(),
        _registered_rails=[],
    )

    with patch("openjiuwen.core.runner.Runner.resource_mgr.add_tool"):
        rail.init(agent)

    assert [card.name for card in registered_cards] == [
        "prepare_skill_evolution",
        "evolve_review_task",
        "list_skill_experiences",
        "read_skill_experiences",
        "evolve_skill_experiences",
        "simplify_skill_experiences",
    ]
    assert [config.agent_card.name for config in agent.deep_config.subagents] == ["evolution_reviewer"]


def test_skill_rail_init_registers_review_agent_with_default_max_iterations(tmp_path):
    rail = _make_rail(tmp_path)
    agent = SimpleNamespace(
        card=SimpleNamespace(id="agent-1"),
        deep_config=SimpleNamespace(subagents=[]),
        ability_manager=SimpleNamespace(add=Mock(), remove=Mock()),
        find_rails_by_type=_find_rails_by_type(),
        _registered_rails=[],
    )

    with patch("openjiuwen.core.runner.Runner.resource_mgr.add_tool"):
        rail.init(agent)

    configured = next(
        config
        for config in agent.deep_config.subagents
        if getattr(config.agent_card, "name", None) == "evolution_reviewer"
    )
    assert getattr(configured, "max_iterations", None) == 10


def test_skill_rail_init_registers_review_agent_with_custom_max_iterations(tmp_path):
    rail = _make_rail(tmp_path, fuzzy_review_interval=5, review_agent_max_iterations=7)
    agent = SimpleNamespace(
        card=SimpleNamespace(id="agent-1"),
        deep_config=SimpleNamespace(subagents=[]),
        ability_manager=SimpleNamespace(add=Mock(), remove=Mock()),
        find_rails_by_type=_find_rails_by_type(),
        _registered_rails=[],
    )

    with patch("openjiuwen.core.runner.Runner.resource_mgr.add_tool"):
        rail.init(agent)

    configured = next(
        config
        for config in agent.deep_config.subagents
        if getattr(config.agent_card, "name", None) == "evolution_reviewer"
    )
    assert getattr(configured, "max_iterations", None) == 7


def test_skill_rail_invalid_review_agent_max_iterations_rejected(tmp_path):
    with pytest.raises(ValueError, match="review_agent_max_iterations must be >= 1"):
        _make_rail(
            tmp_path,
            fuzzy_review_interval=5,
            review_agent_max_iterations=0,
        )

@pytest.mark.asyncio
async def test_build_user_evolution_request_requires_registered_evolve_review_task_after_init(tmp_path):
    rail = _make_rail(tmp_path)
    agent = SimpleNamespace(
        card=SimpleNamespace(id="agent-1"),
        deep_config=SimpleNamespace(subagents=None),
        ability_manager=SimpleNamespace(
            add=Mock(return_value=SimpleNamespace(added=True)),
            get=Mock(return_value=None),
            remove=Mock(),
        ),
        find_rails_by_type=_find_rails_by_type(SubagentRail()),
    )

    with patch("openjiuwen.core.runner.Runner.resource_mgr.add_tool"):
        rail.init(agent)

    with pytest.raises(RuntimeError, match="requires evolve_review_task"):
        await rail._build_user_evolution_request("skill-a", "capture parser lesson")


@pytest.mark.asyncio
async def test_evolve_review_task_uses_fixed_evolution_reviewer_subagent(monkeypatch):
    from openjiuwen.harness.rails.evolution.review.subagent import EVOLUTION_REVIEW_AGENT_NAME
    from openjiuwen.agent_evolving.tools.skill import EvolveReviewTaskTool

    captured = {}

    async def fake_invoke(self, inputs, **kwargs):
        del self
        captured["inputs"] = inputs
        captured["kwargs"] = kwargs
        return SimpleNamespace(success=True, data={"output": '{"ok": true}', "agent_id": "subagent-1"})

    monkeypatch.setattr("openjiuwen.agent_evolving.tools.skill.TaskTool.invoke", fake_invoke)
    tool = EvolveReviewTaskTool(parent_agent=Mock(), language="cn", agent_id="agent-1")

    result = await tool.invoke(
        {
            "evolution_review_ref": "review-ref-1",
            "user_intent": "capture parser lesson",
            "subject": {"kind": "skill", "name": "skill-a"},
        },
        session=object(),
    )

    assert result.success is True
    assert captured["inputs"]["subagent_type"] == EVOLUTION_REVIEW_AGENT_NAME
    assert "review-ref-1" in captured["inputs"]["task_description"]
    assert "capture parser lesson" in captured["inputs"]["task_description"]
    assert captured["kwargs"]["session"] is not None


@pytest.mark.asyncio
async def test_build_user_evolution_request_returns_followup_prompt_not_records(tmp_path):
    rail = _make_rail(tmp_path)
    rail._handle_evolution_from_signals = AsyncMock()
    rail._manager.experience_query_service.list_experiences = AsyncMock()

    result = await rail._build_user_evolution_request("skill-a", "capture parser lesson")

    assert result.mode == "agent_prompt"
    assert result.followup_prompt is not None
    assert "evolve_review_task(evolution_review_ref=...)" in result.followup_prompt
    assert "evolution_review_ref" in result.followup_prompt
    assert "task_tool" not in result.followup_prompt
    assert result.request_id is None
    assert result.approval_event is None
    assert result.records == []
    rail._handle_evolution_from_signals.assert_not_awaited()
    rail._manager.experience_query_service.list_experiences.assert_not_awaited()


def test_subject_payload_reads_actual_skill_kind(tmp_path):
    skills_dir = tmp_path / "skills"
    regular_dir = skills_dir / "regular-skill"
    regular_dir.mkdir(parents=True)
    (regular_dir / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    team_dir = skills_dir / "team-skill"
    team_dir.mkdir()
    (team_dir / "SKILL.md").write_text("---\nkind: team-skill\n---\n\n# Team Skill\n", encoding="utf-8")
    swarm_dir = skills_dir / "swarm-skill"
    swarm_dir.mkdir()
    (swarm_dir / "SKILL.md").write_text("---\nkind: swarm-skill\n---\n\n# Swarm Skill\n", encoding="utf-8")
    rail = _make_rail(tmp_path)
    rail._evolution_store = EvolutionStore(str(skills_dir))

    assert rail._subject_payload("regular-skill") == {"kind": "skill", "name": "regular-skill"}
    assert rail._subject_payload("team-skill") == {"kind": "swarm-skill", "name": "team-skill"}
    assert rail._subject_payload("swarm-skill") == {"kind": "swarm-skill", "name": "swarm-skill"}


@pytest.mark.asyncio
async def test_build_user_evolution_request_uses_actual_swarm_skill_kind(tmp_path):
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "swarm-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nkind: swarm-skill\n---\n\n# Swarm Skill\n", encoding="utf-8")
    rail = _make_rail(tmp_path)
    rail._evolution_store = EvolutionStore(str(skills_dir))

    result = await rail._build_user_evolution_request("swarm-skill", "capture swarm lesson")

    assert result.mode == "agent_prompt"
    assert result.followup_prompt is not None
    assert 'subject={"kind": "swarm-skill", "name": "swarm-skill"}' in result.followup_prompt


@pytest.mark.asyncio
async def test_build_user_evolution_request_resolver_failure_falls_back_to_skill_kind(tmp_path):
    rail = _make_rail(tmp_path)
    store = MagicMock()
    store.resolve_subject_payload.side_effect = RuntimeError("bad frontmatter")
    rail._evolution_store = store

    result = await rail._build_user_evolution_request("skill-a", "capture lesson")

    assert result.mode == "agent_prompt"
    assert result.followup_prompt is not None
    assert 'subject={"kind": "skill", "name": "skill-a"}' in result.followup_prompt


@pytest.mark.asyncio
async def test_prepare_tool_uses_rail_owned_trajectory_evidence(tmp_path):
    rail = _make_rail(tmp_path)
    ability_manager = SimpleNamespace(
        add=Mock(return_value=SimpleNamespace(added=True)),
        remove=Mock(),
    )
    agent = SimpleNamespace(
        card=SimpleNamespace(id="agent-1"),
        ability_manager=ability_manager,
        find_rails_by_type=_find_rails_by_type(),
    )
    trajectory = Trajectory(
        execution_id="exec-prepare-review",
        steps=[
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(
                    tool_name="bash",
                    call_result="Error: parser failed",
                ),
            )
        ],
    )
    rail._build_trajectory = Mock(return_value=trajectory)

    with patch("openjiuwen.core.runner.Runner.resource_mgr.add_tool"):
        rail.init(agent)

    prepare_tool = next(tool for tool in rail._evolution_tools if tool.card.name == "prepare_skill_evolution")
    result = await prepare_tool.invoke(
        {
            "subject": {"kind": "skill", "name": "skill-a"},
            "user_confirmed": True,
            "user_intent": "capture parser failure",
        },
        conversation_id="session-1",
    )

    assert result.success is True
    scope = rail._review_runtime.resolve_scope(result.data["evolution_review_ref"], session_id="session-1")
    index_item = scope.scoped_materials["trajectory_steps"][0]
    assert index_item["ref"] == "step-1"
    assert index_item["kind"] == "tool"
    assert index_item["tool_name"] == "bash"
    assert index_item["summary"].startswith("tool=bash result_preview=Error: parser failed")
    detail_item = scope.scoped_materials["trajectory_step_details"]["step-1"]
    assert detail_item["detail"]["call_result"] == "Error: parser failed"


def test_build_review_scoped_materials_keeps_full_trajectory_index():
    steps = [
        TrajectoryStep(
            kind="tool",
            detail=ToolCallDetail(tool_name="bash", call_result=f"result-{index}"),
        )
        for index in range(25)
    ]
    trajectory = Trajectory(execution_id="exec-1", session_id="session-1", steps=steps)

    materials = build_review_scoped_materials(trajectory)

    assert materials["trajectory"]["execution_id"] == "exec-1"
    assert materials["trajectory"]["session_id"] == "session-1"
    assert materials["trajectory"]["step_count"] == 25
    assert [item["ref"] for item in materials["trajectory_steps"]] == [f"step-{index}" for index in range(1, 26)]
    assert materials["trajectory_steps"][0]["index"] == 0
    assert materials["trajectory_steps"][-1]["index"] == 24


def test_build_review_scoped_materials_tool_detail_is_bounded():
    long_result = "x" * 5000
    trajectory = Trajectory(
        execution_id="exec-tool",
        session_id="session-1",
        steps=[
            TrajectoryStep(
                kind="tool",
                error={"message": "failed"},
                detail=ToolCallDetail(
                    tool_name="bash",
                    call_args={"cmd": "pytest"},
                    call_result=long_result,
                    tool_call_id="call-1",
                ),
            )
        ],
    )

    materials = build_review_scoped_materials(trajectory)

    index_item = materials["trajectory_steps"][0]
    assert index_item["tool_name"] == "bash"
    assert index_item["has_error"] is True
    assert index_item["summary"].startswith("tool=bash result_preview=")
    detail = materials["trajectory_step_details"]["step-1"]["detail"]
    assert detail["tool_name"] == "bash"
    assert detail["call_args"] == {"cmd": "pytest"}
    assert detail["tool_call_id"] == "call-1"
    assert len(detail["call_result"]) < len(long_result)
    assert detail["call_result_truncated"] is True
    assert detail["call_result_original_chars"] == len(long_result)


@pytest.mark.asyncio
async def test_build_user_evolution_request_defers_scope_creation_to_prepare_tool(tmp_path):
    rail = _make_rail(tmp_path)
    rail._ensure_evolve_review_task_available = Mock()
    trajectory = Trajectory(
        execution_id="exec-request-review",
        steps=[
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(
                    tool_name="python",
                    call_result="ValueError: invalid parser state",
                ),
            )
        ],
    )
    rail._build_trajectory = Mock(return_value=trajectory)

    result = await rail._build_user_evolution_request("skill-a", "capture parser failure")

    assert result.followup_prompt is not None
    assert rail._review_runtime._scopes_by_ref == {}
    rail._build_trajectory.assert_not_called()


@pytest.mark.asyncio
async def test_list_experiences_is_host_query(tmp_path):
    rail = _make_rail(tmp_path)
    rail._manager.experience_query_service.list_experiences = AsyncMock(return_value={"total_count": 0, "items": []})

    result = await rail.list_experiences("skill-a")

    assert result == {"total_count": 0, "items": []}
    rail._manager.experience_query_service.list_experiences.assert_awaited_once_with(
        {"kind": "skill", "name": "skill-a"},
        min_score=None,
        limit=100,
        cursor=None,
        target=None,
        section=None,
        query=None,
        sort="score_desc",
    )


@pytest.mark.asyncio
async def test_list_experiences_uses_query_service_not_agent_tool(tmp_path):
    rail = _make_rail(tmp_path)
    rail._manager.experience_query_service.list_experiences = AsyncMock(
        return_value={"success": True, "operation": "list", "items": []}
    )

    result = await rail.list_experiences("browser", limit=5, query="parser|json")

    assert result["operation"] == "list"
    rail._manager.experience_query_service.list_experiences.assert_awaited_once_with(
        {"kind": "skill", "name": "browser"},
        min_score=None,
        limit=5,
        cursor=None,
        target=None,
        section=None,
        query="parser|json",
        sort="score_desc",
    )


@pytest.mark.asyncio
async def test_evolution_protocol_section_is_injected_without_command_parsing(tmp_path):
    rail = _make_rail(tmp_path)
    builder = SystemPromptBuilder(language="cn")
    agent = SimpleNamespace(system_prompt_builder=builder)
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(messages=[{"role": "user", "content": "/evolve skill-a"}]),
    )
    original_messages = list(ctx.inputs.messages)

    await rail.before_model_call(ctx)

    section = builder.get_section(SectionName.EVOLUTION_PROTOCOL)
    assert section is not None
    assert section.name == SectionName.EVOLUTION_PROTOCOL
    assert ctx.inputs.messages == original_messages


@pytest.mark.asyncio
async def test_evolution_protocol_section_supports_english(tmp_path):
    rail = _make_rail(tmp_path)
    builder = SystemPromptBuilder(language="en")
    agent = SimpleNamespace(system_prompt_builder=builder)
    ctx = AgentCallbackContext(agent=agent, inputs=ModelCallInputs(messages=[]))

    await rail.before_model_call(ctx)

    section = builder.get_section(SectionName.EVOLUTION_PROTOCOL)
    assert section is not None
    assert section.name == SectionName.EVOLUTION_PROTOCOL


def _make_task_iteration_ctx(*, agent=None, is_follow_up: bool = False) -> AgentCallbackContext:
    return AgentCallbackContext(
        agent=agent,
        inputs=TaskIterationInputs(
            iteration=1,
            loop_event=SimpleNamespace(),
            conversation_id="conv-1",
            query="run",
            is_follow_up=is_follow_up,
        ),
        session=None,
    )


@pytest.mark.asyncio
async def test_fuzzy_review_enqueues_every_interval_for_non_followup_iterations(tmp_path):
    rail = _make_rail(tmp_path, fuzzy_review_interval=5)
    controller = Mock()
    controller.enqueue_follow_up = Mock()
    agent = SimpleNamespace(_loop_controller=controller)
    ctx = _make_task_iteration_ctx(agent=agent)

    for _ in range(4):
        await rail._on_after_task_iteration(ctx)
    controller.enqueue_follow_up.assert_not_called()
    assert rail._fuzzy_review_non_followup_count == 4

    await rail._on_after_task_iteration(ctx)
    controller.enqueue_follow_up.assert_called_once()
    assert rail._fuzzy_review_non_followup_count == 0

    for _ in range(4):
        await rail._on_after_task_iteration(ctx)
    assert controller.enqueue_follow_up.call_count == 1
    assert rail._fuzzy_review_non_followup_count == 4

    await rail._on_after_task_iteration(ctx)
    assert controller.enqueue_follow_up.call_count == 2
    assert rail._fuzzy_review_non_followup_count == 0


@pytest.mark.asyncio
async def test_fuzzy_review_followup_iteration_does_not_count_or_recurse(tmp_path):
    rail = _make_rail(tmp_path, fuzzy_review_interval=1)
    controller = Mock()
    controller.enqueue_follow_up = Mock()
    agent = SimpleNamespace(_loop_controller=controller)

    await rail._on_after_task_iteration(_make_task_iteration_ctx(agent=agent, is_follow_up=True))
    await rail._on_after_task_iteration(_make_task_iteration_ctx(agent=agent))

    controller.enqueue_follow_up.assert_called_once()


@pytest.mark.asyncio
async def test_fuzzy_review_can_be_disabled(tmp_path):
    rail = _make_rail(tmp_path, fuzzy_review=False, fuzzy_review_interval=1)
    controller = Mock()
    controller.enqueue_follow_up = Mock()
    agent = SimpleNamespace(_loop_controller=controller)

    await rail._on_after_task_iteration(_make_task_iteration_ctx(agent=agent))

    controller.enqueue_follow_up.assert_not_called()


@pytest.mark.asyncio
async def test_fuzzy_review_without_task_loop_controller_drops_followup(tmp_path):
    rail = _make_rail(tmp_path, fuzzy_review_interval=1)
    ctx = _make_task_iteration_ctx(agent=SimpleNamespace())

    await rail._on_after_task_iteration(ctx)

    assert rail._fuzzy_review_non_followup_count == 0


def test_fuzzy_review_interval_must_be_positive(tmp_path):
    with pytest.raises(ValueError, match="fuzzy_review_interval"):
        _make_rail(tmp_path, fuzzy_review_interval=0)


@pytest.mark.asyncio
async def test_evolution_protocol_section_omits_fuzzy_review_when_disabled(tmp_path):
    rail = _make_rail(tmp_path, fuzzy_review=False)
    builder = SystemPromptBuilder(language="en")
    agent = SimpleNamespace(system_prompt_builder=builder)
    ctx = AgentCallbackContext(agent=agent, inputs=ModelCallInputs(messages=[]))

    await rail.before_model_call(ctx)

    section = builder.get_section(SectionName.EVOLUTION_PROTOCOL)
    assert section is not None


@pytest.mark.asyncio
async def test_on_approve_simplify_delegates_to_manager(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    with patch.object(
        rail._manager,
        "approve_simplify",
        AsyncMock(return_value={"kept": 1, "deleted": 0, "merged": 0, "refined": 0, "errors": 0}),
    ) as approve_simplify:
        result = await rail.on_approve_simplify("req-1")

    approve_simplify.assert_awaited_once_with("req-1")
    assert result["kept"] == 1


@pytest.mark.asyncio
async def test_on_reject_simplify_delegates_to_manager(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    rail._pending_governance["req-2"] = {"skill_name": "skill-a"}

    with patch.object(rail._manager, "reject_simplify", AsyncMock()) as reject_simplify:
        await rail.on_reject_simplify("req-2")

    reject_simplify.assert_awaited_once_with("req-2")


@pytest.mark.asyncio
async def test_request_user_evolution_wrapper_delegates_to_internal_builder(tmp_path):
    rail = _make_rail(tmp_path)
    expected = EvolutionRequestResult(
        skill_name="skill-a",
        mode="agent_prompt",
        followup_prompt="review this skill",
        request_id="request-id",
    )

    with patch.object(
        rail,
        "_build_user_evolution_request",
        AsyncMock(return_value=expected),
    ) as build_request:
        result = await rail.request_user_evolution(
            "skill-a",
            "improve parser fallback",
            auto_approve=True,
            max_index_records=20,
        )

    build_request.assert_awaited_once_with("skill-a", "improve parser fallback")
    assert result == expected


@pytest.mark.asyncio
async def test_request_simplify_returns_followup_prompt_not_staged_actions(tmp_path):
    rail = _make_rail(tmp_path)
    rail._manager.request_simplify = AsyncMock()
    rail._manager.experience_query_service.list_experiences = AsyncMock(
        return_value={"items": [{"id": "exp-1"}], "has_more": False},
    )

    result = await rail.request_simplify(
        "skill-a",
        "reduce noise",
        mode="agent_prompt",
        max_index_records=7,
    )

    rail._manager.request_simplify.assert_not_awaited()
    rail._manager.experience_query_service.list_experiences.assert_awaited_once()
    assert rail._manager.experience_query_service.list_experiences.await_args.args[0] == {
        "kind": "skill",
        "name": "skill-a",
    }
    assert rail._manager.experience_query_service.list_experiences.await_args.kwargs["limit"] == 7
    assert isinstance(result, SimplifyRequestResult)
    assert result.mode == "agent_prompt"
    assert result.followup_prompt is not None
    assert "simplify_skill_experiences" in result.followup_prompt
    assert result.request_id is None
    assert result.approval_event is None
    assert result.actions == []


@pytest.mark.asyncio
async def test_request_simplify_rejects_non_agent_prompt_mode(tmp_path):
    rail = _make_rail(tmp_path)

    with pytest.raises(ValueError, match="mode='agent_prompt'"):
        await rail.request_simplify("skill-a", mode="scorer_actions")


@pytest.mark.asyncio
async def test_request_rebuild_delegates_to_manager_with_params(tmp_path):
    rail = _make_rail(tmp_path)

    with patch.object(
        rail._manager,
        "request_rebuild",
        AsyncMock(return_value="simplify command"),
    ) as request_rebuild:
        result = await rail.request_rebuild(
            "skill-a",
            "refine records",
            0.55,
            max_context_records=19,
            max_context_chars=12345,
        )

    request_rebuild.assert_awaited_once_with(
        "skill-a",
        user_intent="refine records",
        min_score=0.55,
        max_context_records=19,
        max_context_chars=12345,
    )
    assert result == "simplify command"


# =============================================================================
# after_tool_call Tests
# =============================================================================


@pytest.mark.asyncio
async def test_after_tool_call_does_not_inject_body_experience(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.format_body_experience_text = AsyncMock(return_value="\nnew experience")

    inputs = ToolCallInputs(
        tool_name="read_file",
        tool_args={"file_path": r"C:\skills\invoice-parser\SKILL.md"},
        tool_msg=_DummyToolMsg("original"),
    )
    ctx = AgentCallbackContext(agent=None, inputs=inputs, session=None)

    await rail._on_after_tool_call(ctx)

    assert inputs.tool_msg.content == "original"
    rail._evolution_store.format_body_experience_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_after_tool_call_does_not_read_experience_text(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.format_body_experience_text = AsyncMock(return_value="\nnew experience")
    cases = [
        ToolCallInputs(tool_name="list_skill", tool_args={"file_path": "/a/s/SKILL.md"}, tool_msg=_DummyToolMsg("x")),
        ToolCallInputs(tool_name="read_file", tool_args={"file_path": "/a/s/README.md"}, tool_msg=_DummyToolMsg("x")),
        ToolCallInputs(tool_name="read_file", tool_args={"file_path": "/a/s/SKILL.md"}, tool_msg=None),
    ]
    for item in cases:
        ctx = AgentCallbackContext(agent=None, inputs=item, session=None)
        await rail._on_after_tool_call(ctx)

    rail._evolution_store.format_body_experience_text.assert_not_awaited()
    assert cases[0].tool_msg.content == "x"
    assert cases[1].tool_msg.content == "x"

    rail._evolution_store.format_body_experience_text = AsyncMock(return_value="")
    empty_case = ToolCallInputs(
        tool_name="read_file",
        tool_args={"file_path": "/a/demo/SKILL.md"},
        tool_msg=_DummyToolMsg("z"),
    )
    await rail._on_after_tool_call(AgentCallbackContext(agent=None, inputs=empty_case, session=None))
    assert empty_case.tool_msg.content == "z"
    rail._evolution_store.format_body_experience_text.assert_not_awaited()


# =============================================================================
# after_invoke Tests
# =============================================================================


@pytest.mark.asyncio
async def test_run_evolution_returns_immediately_when_auto_scan_disabled(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=False)
    rail._collect_messages = AsyncMock(return_value=[{"role": "user", "content": "x"}])
    await rail.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))
    rail._collect_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_after_invoke_does_not_trigger_evolution_when_auto_scan_disabled(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=False)
    rail.run_evolution = AsyncMock()

    ctx = AgentCallbackContext(
        agent=None,
        inputs=InvokeInputs(query="round 1", conversation_id="conv-1"),
        session=None,
    )

    await rail.before_invoke(ctx)
    await rail.after_invoke(ctx)

    rail.run_evolution.assert_not_awaited()


@pytest.mark.asyncio
async def test_after_invoke_suppresses_auto_scan_during_agent_driven_evolution(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True)
    rail._async_evolution = False
    rail.run_evolution = AsyncMock()
    invoke_ctx = AgentCallbackContext(
        agent=None,
        inputs=InvokeInputs(query="round 1", conversation_id="conv-1"),
        session=None,
    )

    await rail.before_invoke(invoke_ctx)
    rail._prepare_evolution_review_scope(
        source="explicit_command",
        subject={"kind": "skill", "name": "skill-a"},
        session_id="conv-1",
        user_intent="capture lesson",
    )
    await rail.after_invoke(invoke_ctx)

    rail.run_evolution.assert_not_awaited()

    await rail.before_invoke(invoke_ctx)
    await rail.after_invoke(invoke_ctx)

    rail.run_evolution.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_evolution_auto_save_commits_via_manager_lifecycle(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    approval_request = SimpleNamespace(request_id="skill_evolve_req")
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/skill-a/SKILL.md"}]},
        {"role": "tool", "content": "Error: command failed", "name": "bash"},
    ]

    rail._collect_messages = AsyncMock(return_value=messages)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._stage_evolution_from_signals = AsyncMock(return_value=_staged_result(approval_request))
    rail._emit_generated_records = AsyncMock()
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    trajectory = _trajectory_with_messages(messages)

    await rail.run_evolution(trajectory, ctx)

    signals = rail._stage_evolution_from_signals.await_args.kwargs["signals"]
    rail._stage_evolution_from_signals.assert_awaited_once_with(
        skill_name="skill-a",
        signals=signals,
        messages=messages,
        trajectory=trajectory,
        user_query="",
        requires_approval=False,
    )
    rail._emit_generated_records.assert_not_awaited()
    events = _progress_events(await rail.drain_pending_host_events())
    stages = [event.payload["evolution_meta"]["stage"] for event in events]
    assert "signals_attributed" in stages
    assert "optimizing" in stages
    assert "auto_approved" in stages
    assert "completed" not in stages


@pytest.mark.asyncio
async def test_run_evolution_uses_online_updater_path_after_init(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._evolution_store.skill_exists = Mock(return_value=True)
    rail._evolution_store.resolve_skill_dir = Mock(return_value=None)
    detector = Mock()
    detector.bind_llm.return_value = detector
    detector.detect_trajectory_signals.return_value = [_make_signal("skill-a")]
    detector.detect_user_intent = AsyncMock(return_value=[])
    rail._handle_evolution_from_signals = AsyncMock(return_value=_no_records_result())
    ability_manager = SimpleNamespace(add=Mock(return_value=SimpleNamespace(added=True)))
    agent = SimpleNamespace(
        card=SimpleNamespace(id="agent-1"),
        ability_manager=ability_manager,
        find_rails_by_type=_find_rails_by_type(),
    )

    with (
        patch("openjiuwen.core.runner.Runner.resource_mgr.add_tool"),
        patch("openjiuwen.harness.rails.evolution.skill_evolution_rail.SignalDetector", return_value=detector),
    ):
        rail.init(agent)
        await rail.run_evolution(
            _trajectory_with_messages([{"role": "user", "content": "prefer parser fields"}]),
            AgentCallbackContext(agent=agent, inputs=None, session=None),
        )

    rail._handle_evolution_from_signals.assert_awaited_once()
    assert "passive" not in rail._handle_evolution_from_signals.await_args.kwargs


@pytest.mark.asyncio
async def test_run_evolution_auto_save_false_emits_events(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=False)
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/skill-a/SKILL.md"}]},
        {"role": "tool", "content": "Error: command failed", "name": "bash"},
    ]
    trajectory = _trajectory_with_messages(messages)
    approval_request = object()

    rail._collect_messages = AsyncMock(return_value=messages)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._stage_evolution_from_signals = AsyncMock(return_value=_staged_result(approval_request))
    rail._emit_generated_records = AsyncMock()
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    await rail.run_evolution(trajectory, ctx)

    signals = rail._stage_evolution_from_signals.await_args.kwargs["signals"]
    rail._stage_evolution_from_signals.assert_awaited_once_with(
        skill_name="skill-a",
        signals=signals,
        messages=messages,
        trajectory=trajectory,
        user_query="",
        requires_approval=True,
    )
    rail._emit_generated_records.assert_awaited_once_with(ctx, "skill-a", approval_request)
    events = _progress_events(await rail.drain_pending_host_events())
    stages = [event.payload["evolution_meta"]["stage"] for event in events]
    assert "signals_attributed" in stages
    assert "optimizing" in stages


@pytest.mark.asyncio
async def test_run_evolution_auto_save_false_emits_real_approval_event(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=False)
    record = _make_record("skill-a", content="fresh approval record")
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/skill-a/SKILL.md"}]},
        {"role": "tool", "content": "Error: command failed", "name": "bash"},
    ]
    trajectory = _trajectory_with_messages(messages)

    rail._collect_messages = AsyncMock(return_value=messages)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    approval_request = SimpleNamespace(
        skill_name="skill-a",
        proposal=SimpleNamespace(record_count=1, signal_type=None, signal_source=None),
        pending_change=SimpleNamespace(payload=[record]),
        request_id="skill_evolve_req",
    )
    rail._stage_evolution_from_signals = AsyncMock(return_value=_staged_result(approval_request))
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    await rail.run_evolution(trajectory, ctx)

    signals = rail._stage_evolution_from_signals.await_args.kwargs["signals"]
    rail._stage_evolution_from_signals.assert_awaited_once_with(
        skill_name="skill-a",
        signals=signals,
        messages=messages,
        trajectory=trajectory,
        user_query="",
        requires_approval=True,
    )
    events = _approval_events(await rail.drain_pending_approval_events())
    assert len(events) == 1
    assert events[0].payload["evolution_meta"]["skill_name"] == "skill-a"


@pytest.mark.asyncio
async def test_run_evolution_emits_cancelled_when_attributed_signals_generate_no_records(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=False)
    messages = [{"role": "user", "content": "please review whether this skill learned anything"}]
    trajectory = _trajectory_with_messages(messages)
    signal = _make_signal("skill-a", excerpt="review this conversation")
    detector = Mock()
    detector.bind_llm.return_value = detector
    detector.detect_trajectory_signals.return_value = [signal]
    detector.detect_user_intent = AsyncMock(return_value=[])

    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._online_updater.bind = Mock()
    rail._online_updater.process = AsyncMock(return_value={})

    with patch(
        "openjiuwen.harness.rails.evolution.skill_evolution_rail.SignalDetector",
        return_value=detector,
    ):
        await rail.run_evolution(trajectory, AgentCallbackContext(agent=None, inputs=None, session=None))

    rail._online_updater.process.assert_awaited_once()
    drained_events = await rail.drain_pending_host_events()
    events = _progress_events(drained_events)
    stages = [event.payload["evolution_meta"]["stage"] for event in events]
    assert "signals_attributed" in stages
    assert "optimizing" in stages
    assert stages[-1] == "cancelled"
    assert events[-1].payload["evolution_meta"]["skill_name"] == "skill-a"
    assert "produced no reusable evolution records" in events[-1].payload["content"]
    outcomes = [
        event for event in drained_events if event.payload.get("evolution_meta", {}).get("event_kind") == "outcome"
    ]
    assert outcomes
    assert outcomes[-1].payload["evolution_meta"]["status"] == "no_evolution_no_records"
    assert outcomes[-1].payload["evolution_meta"]["skill_name"] == "skill-a"


@pytest.mark.asyncio
async def test_handle_evolution_emits_outcome_for_generation_failed(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=False)
    rail._stage_evolution_from_signals = AsyncMock(return_value=_failed_result("generation_failed"))

    result = await rail._handle_evolution_from_signals(
        skill_name="skill-a",
        signals=[_make_signal("skill-a")],
        messages=[],
        ctx=None,
        requires_approval=True,
    )

    assert result.status == "generation_failed"
    assert result.request is None
    drained_events = await rail.drain_pending_host_events()
    outcomes = [
        event for event in drained_events if event.payload.get("evolution_meta", {}).get("event_kind") == "outcome"
    ]
    assert outcomes
    assert outcomes[-1].payload["evolution_meta"]["status"] == "generation_failed"
    assert outcomes[-1].payload["evolution_meta"]["skill_name"] == "skill-a"


@pytest.mark.asyncio
async def test_handle_evolution_emits_persistence_failed_without_auto_approved_finalize(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    failed_request = SimpleNamespace(request_id="req-failed")
    rail._stage_evolution_from_signals = AsyncMock(
        return_value=OnlineEvolutionResult(
            skill_name="skill-a",
            status="persistence_failed",
            request=failed_request,
            message="disk full",
        )
    )
    rail.approval_runtime.finalize_staged_evolution_request = AsyncMock(return_value=failed_request)

    result = await rail._handle_evolution_from_signals(
        skill_name="skill-a",
        signals=[_make_signal("skill-a")],
        messages=[],
        ctx=None,
        requires_approval=False,
    )

    assert result.status == "persistence_failed"
    assert result.request is failed_request
    rail.approval_runtime.finalize_staged_evolution_request.assert_not_awaited()
    drained_events = await rail.drain_pending_host_events()
    outcomes = [
        event for event in drained_events if event.payload.get("evolution_meta", {}).get("event_kind") == "outcome"
    ]
    assert outcomes
    assert outcomes[-1].payload["evolution_meta"]["status"] == "persistence_failed"
    assert outcomes[-1].payload["evolution_meta"]["stage"] == "failed"
    assert outcomes[-1].payload["evolution_meta"]["request_id"] == "req-failed"
    assert "disk full" in outcomes[-1].payload["content"]


@pytest.mark.asyncio
async def test_run_evolution_filters_empty_skill_name_and_swallow_exceptions(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/skill-a/SKILL.md"}]},
        {"role": "tool", "content": "Error: command failed", "name": "bash"},
    ]
    rail._collect_messages = AsyncMock(return_value=messages)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())
    rail._evolution_store.append_record = AsyncMock()

    await rail.run_evolution(
        _trajectory_with_messages(messages), AgentCallbackContext(agent=None, inputs=None, session=None)
    )
    rail._stage_evolution_from_signals.assert_awaited_once()
    assert rail._stage_evolution_from_signals.await_args.kwargs["skill_name"] == "skill-a"

    rail2 = _make_rail(tmp_path, auto_scan=True)
    rail2._collect_messages = AsyncMock(side_effect=RuntimeError("boom"))
    await rail2.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))


@pytest.mark.asyncio
async def test_run_evolution_clears_processed_signal_keys_when_exceed_limit(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/skill-a/SKILL.md"}]},
        {"role": "tool", "content": "Error: command failed", "name": "bash"},
    ]
    rail._processed_signal_keys = {
        ("old", "", "skill-a", str(index)) for index in range(_MAX_PROCESSED_SIGNAL_KEYS + 1)
    }
    rail._collect_messages = AsyncMock(return_value=messages)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())

    await rail.run_evolution(
        _trajectory_with_messages(messages),
        AgentCallbackContext(agent=None, inputs=None, session=None),
    )

    assert rail._processed_signal_keys == set()


# =============================================================================
# _detect_signals Tests
# =============================================================================


def test_detect_signals_deduplicates_with_processed_keys(tmp_path, monkeypatch):
    rail = _make_rail(tmp_path)
    rail._evolution_store.skill_exists = Mock(return_value=True)
    signal = _make_signal("skill-a", excerpt="same-excerpt")
    monkeypatch.setattr(SignalDetector, "detect", lambda self, messages: [signal])

    first = rail._detect_signals([{"role": "user", "content": "x"}], ["skill-a"])
    second = rail._detect_signals([{"role": "user", "content": "x"}], ["skill-a"])

    assert len(first) == 1
    assert len(second) == 0
    assert ("execution_failure", "bash", "skill-a", "same-excerpt") in rail.processed_signal_keys


def test_detect_signals_clears_processed_keys_when_exceed_limit(tmp_path, monkeypatch):
    rail = _make_rail(tmp_path)
    rail._evolution_store.skill_exists = Mock(return_value=True)
    rail._processed_signal_keys = {(f"type-{i}", f"excerpt-{i}") for i in range(_MAX_PROCESSED_SIGNAL_KEYS)}
    monkeypatch.setattr(SignalDetector, "detect", lambda self, messages: [_make_signal("skill-a", excerpt="new-one")])

    detected = rail._detect_signals([{"role": "user", "content": "x"}], ["skill-a"])

    assert len(detected) == 1
    assert rail.processed_signal_keys == set()


# =============================================================================
# _stage_evolution_from_signals / event buffer Tests
# =============================================================================


@pytest.mark.asyncio
async def test_stage_evolution_from_signals_builds_context(tmp_path):
    rail = _make_rail(tmp_path)
    signal = _make_signal("skill-a")
    old_desc = _make_record("skill-a", content="old desc")
    old_body = _make_record("skill-a", content="old body")
    new_record = _make_record("skill-a", content="new")

    rail._evolution_store.read_skill_content = AsyncMock(return_value="# skill")
    rail._evolution_store.get_pending_records = AsyncMock(side_effect=[[old_desc], [old_body], []])
    rail._evolver.generate_records = AsyncMock(return_value=[new_record])

    rail._online_updater.bind = Mock()
    rail._online_updater.process = AsyncMock(return_value={("skill_experience_skill-a", "experiences"): [new_record]})
    rail._manager.stage_apply_results = Mock(return_value="approval-request")

    result = await rail._stage_evolution_from_signals(
        "skill-a",
        [signal],
        [{"role": "user", "content": "x"}],
        requires_approval=True,
    )

    assert result.status == "staged"
    assert result.request == "approval-request"
    bind_kwargs = rail._online_updater.bind.call_args.kwargs
    assert list(bind_kwargs["operators"]) == ["skill_experience_skill-a"]
    assert bind_kwargs["targets"] == ["experiences"]
    online_ctx = bind_kwargs["online_contexts"]["skill-a"]
    assert online_ctx.skill_content == "# skill"
    assert online_ctx.messages == [{"role": "user", "content": "x"}]
    assert online_ctx.existing_desc_records == [old_desc]
    assert online_ctx.existing_body_records == [old_body]
    assert online_ctx.existing_script_records == []
    rail._manager.stage_apply_results.assert_called_once()
    stage_args = rail._manager.stage_apply_results.call_args
    assert stage_args.args[0] == "skill-a"
    assert stage_args.kwargs["requires_approval"] is True


@pytest.mark.asyncio
async def test_stage_evolution_from_signals_passes_trajectory_to_orchestrator(tmp_path):
    rail = _make_rail(tmp_path)
    rail._online_orchestrator.evolve = AsyncMock(
        return_value=OnlineEvolutionResult(
            skill_name="skill-a",
            status="no_evolution_no_records",
            message="none",
        )
    )
    trajectory = Trajectory(execution_id="exec-1", session_id="session-1", steps=[])

    await rail._stage_evolution_from_signals(
        "skill-a",
        [],
        [{"role": "user", "content": "hello"}],
        trajectory=trajectory,
        requires_approval=True,
    )

    assert rail._online_orchestrator.evolve.await_args.kwargs["trajectory"] is trajectory


@pytest.mark.asyncio
async def test_stage_evolution_from_signals_returns_no_records_result_when_apply_results_empty(tmp_path):
    rail = _make_rail(tmp_path)
    rail._online_updater.bind = Mock()
    rail._online_updater.process = AsyncMock(return_value={})

    result = await rail._stage_evolution_from_signals("skill-a", [_make_signal("skill-a")], [], requires_approval=True)
    assert result.status == "no_evolution_no_records"
    assert result.request is None


@pytest.mark.asyncio
async def test_emit_generated_records_and_drain_pending_events(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a", content="x" * 1200)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    request = _stage_approval_request(rail, "skill-a", [record])

    await rail._emit_generated_records(ctx, "skill-a", request)
    events = _approval_events(await rail.drain_pending_approval_events())

    assert len(events) == 1
    event = events[0]
    assert event.type == "chat.ask_user_question"
    request_id = event.payload["request_id"]
    # request_id uses the "skill_evolve_" prefix (PendingChange.change_id)
    assert request_id.startswith("skill_evolve_")
    assert event.payload["questions"][0]["header"] == "技能演进审批"
    assert event.payload["evolution_meta"]["rail_kind"] == "regular"
    assert event.payload["evolution_meta"]["skill_name"] == "skill-a"
    assert event.payload["evolution_meta"]["request_id"] == request_id
    assert request_id in rail._pending_approval_snapshots
    assert await rail.drain_pending_approval_events() == []


@pytest.mark.asyncio
async def test_emit_generated_records_ignores_missing_approval_request(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    await rail._emit_generated_records(ctx, "skill-a")

    assert await rail.drain_pending_approval_events() == []


@pytest.mark.asyncio
async def test_on_approve_flushes_snapshot_records(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    request_id = "skill_evolve_req"
    rail._manager.approve_request = AsyncMock(return_value=Mock(applied_count=1, pending_count=0))
    rail._pending_approval_snapshots[request_id] = Mock(
        skill_name="skill-a",
        payload=[],
        messages=None,
        is_shared_records=False,
    )

    await rail.on_approve(request_id)

    rail._manager.approve_request.assert_awaited_once_with(request_id)


@pytest.mark.asyncio
async def test_on_reject_discards_snapshot_records(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    request_id = "skill_evolve_req"
    rail._manager.reject_request = AsyncMock(return_value=Mock(rejected_count=1))
    rail._pending_approval_snapshots[request_id] = Mock(
        skill_name="skill-a",
        payload=[],
        messages=None,
        is_shared_records=False,
    )

    await rail.on_reject(request_id)

    rail._manager.reject_request.assert_awaited_once_with(request_id)


@pytest.mark.asyncio
async def test_on_approve_partial_failure_retains_pending_change(tmp_path):
    """If one record fails, the unwritten tail stays pending for retry."""
    rail = _make_rail(tmp_path, auto_save=False)
    record_1 = _make_record("skill-a", content="first")
    record_2 = _make_record("skill-a", content="second")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    rail._evolution_store.append_record = AsyncMock(side_effect=[None, OSError("disk full")])
    request = _stage_approval_request(rail, "skill-a", [record_1, record_2])

    await rail._emit_generated_records(ctx, "skill-a", request)
    events = _approval_events(await rail.drain_pending_approval_events())
    request_id = events[0].payload["request_id"]

    await rail.on_approve(request_id)

    assert rail._evolution_store.append_record.await_count == 2
    assert request_id in rail._pending_approval_snapshots
    pending = rail._pending_approval_snapshots[request_id]
    assert pending.payload == [record_2]

    # Host retries: now the remaining record succeeds
    rail._evolution_store.append_record = AsyncMock()
    await rail.on_approve(request_id)
    rail._evolution_store.append_record.assert_awaited_once_with("skill-a", record_2, subject_kind="skill")
    assert request_id not in rail._pending_approval_snapshots


@pytest.mark.asyncio
async def test_on_approve_full_failure_then_retry_succeeds(tmp_path):
    """If the first record fails, PendingChange is kept; retry succeeds."""
    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a", content="important")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    rail._evolution_store.append_record = AsyncMock(side_effect=OSError("disk full"))
    request = _stage_approval_request(rail, "skill-a", [record])

    await rail._emit_generated_records(ctx, "skill-a", request)
    events = _approval_events(await rail.drain_pending_approval_events())
    request_id = events[0].payload["request_id"]

    await rail.on_approve(request_id)

    # All records still pending after full failure
    assert request_id in rail._pending_approval_snapshots
    pending = rail._pending_approval_snapshots[request_id]
    assert len(pending.payload) == 1

    # Host retries: now append succeeds
    rail._evolution_store.append_record = AsyncMock()
    await rail.on_approve(request_id)
    rail._evolution_store.append_record.assert_awaited_once_with("skill-a", record, subject_kind="skill")
    assert request_id not in rail._pending_approval_snapshots


@pytest.mark.asyncio
async def test_concurrent_approval_batches_are_independent(tmp_path):
    """Two approval prompts for the same skill must operate on disjoint record batches."""
    rail = _make_rail(tmp_path, auto_save=False)
    record_a = _make_record("skill-a", content="batch-1")
    record_b = _make_record("skill-a", content="batch-2")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    rail._evolution_store.append_record = AsyncMock()

    request_a = _stage_approval_request(rail, "skill-a", [record_a])
    await rail._emit_generated_records(ctx, "skill-a", request_a)

    request_b = _stage_approval_request(rail, "skill-a", [record_b])
    await rail._emit_generated_records(ctx, "skill-a", request_b)

    events = _approval_events(await rail.drain_pending_approval_events())
    assert len(events) == 2
    req1 = events[0].payload["request_id"]
    req2 = events[1].payload["request_id"]

    # Approving the first prompt should write only record_a
    await rail.on_approve(req1)
    rail._evolution_store.append_record.assert_awaited_once_with("skill-a", record_a, subject_kind="skill")
    rail._evolution_store.append_record.reset_mock()

    # Approving the second prompt should write only record_b
    await rail.on_approve(req2)
    rail._evolution_store.append_record.assert_awaited_once_with("skill-a", record_b, subject_kind="skill")


@pytest.mark.asyncio
async def test_on_approve_only_flushes_snapshot_records(tmp_path):
    """Approving one batch must not flush unrelated pending records for the same skill."""
    rail = _make_rail(tmp_path, auto_save=False)
    approved = _make_record("skill-a", content="approved")
    pending_later = _make_record("skill-a", content="pending-later")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    rail._evolution_store.append_record = AsyncMock()
    request = _stage_approval_request(rail, "skill-a", [approved])

    await rail._emit_generated_records(ctx, "skill-a", request)
    request_id = _approval_events(await rail.drain_pending_approval_events())[0].payload["request_id"]

    later_request = _stage_approval_request(rail, "skill-a", [pending_later])
    await rail.on_approve(request_id)

    rail._evolution_store.append_record.assert_awaited_once_with("skill-a", approved, subject_kind="skill")
    assert later_request.request_id in rail._pending_approval_snapshots
    assert request_id not in rail._pending_approval_snapshots


# =============================================================================
# _infer_primary_skill Tests
# =============================================================================


def test_infer_primary_skill_picks_most_frequent(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"arguments": "/skills/skill-a/SKILL.md"},
                {"arguments": "/skills/skill-b/SKILL.md"},
            ],
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"arguments": "/skills/skill-a/SKILL.md"},
            ],
        },
    ]
    result = rail._infer_primary_skill(messages, ["skill-a", "skill-b"])
    assert result == "skill-a"


def test_infer_primary_skill_returns_none_when_no_match(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {"role": "user", "content": "just chatting"},
    ]
    result = rail._infer_primary_skill(messages, ["skill-a"])
    assert result is None


def test_infer_primary_skill_from_tool_result_content(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {"role": "tool", "content": "Content of /skills/skill-x/SKILL.md: ..."},
    ]
    result = rail._infer_primary_skill(messages, ["skill-x"])
    assert result == "skill-x"


def test_infer_primary_skill_ignores_unknown_skills(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {"role": "tool", "content": "/skills/unknown-skill/SKILL.md"},
    ]
    result = rail._infer_primary_skill(messages, ["skill-a"])
    assert result is None


def test_infer_primary_skill_prefers_skill_tool_over_paths(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"name": "read_file", "arguments": "/workspace/skills/skill-b/reference.md"},
                {"name": "skill_tool", "arguments": '{"skill_name":"skill-a","relative_file_path":"reference.md"}'},
            ],
        },
        {"role": "tool", "content": "/workspace/skill-b/SKILL.md"},
    ]

    result = rail._infer_primary_skill(messages, ["skill-a", "skill-b"])

    assert result == "skill-a"


def test_infer_primary_skill_prefers_skills_path_over_legacy_skill_md(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {"role": "tool", "content": "/workspace/legacy-skill/SKILL.md"},
        {"role": "tool", "content": "/workspace/skills/new-skill/reference/notes.md"},
    ]

    result = rail._infer_primary_skill(messages, ["legacy-skill", "new-skill"])

    assert result == "new-skill"


def test_is_regular_skill_filters_team_and_swarm_skill(tmp_path):
    rail = SkillEvolutionRail(
        skills_dir=str(tmp_path / "skills"),
        llm=Mock(),
        model="dummy-model",
        review_runtime=_default_review_runtime(),
    )
    rail._evolver = Mock()

    regular_dir = tmp_path / "skills" / "regular-skill"
    regular_dir.mkdir(parents=True)
    (regular_dir / "SKILL.md").write_text(
        "---\nname: regular-skill\nkind: skill\n---\n# Regular",
        encoding="utf-8",
    )

    team_dir = tmp_path / "skills" / "team-skill"
    team_dir.mkdir(parents=True)
    (team_dir / "SKILL.md").write_text(
        "---\nname: team-skill\nkind: team-skill\n---\n# Team",
        encoding="utf-8",
    )
    swarm_dir = tmp_path / "skills" / "swarm-skill"
    swarm_dir.mkdir(parents=True)
    (swarm_dir / "SKILL.md").write_text(
        "---\nname: swarm-skill\nkind: swarm-skill\nroles:\n  - name: planner\n    kind: ai_agent\n---\n# Swarm",
        encoding="utf-8",
    )

    assert rail._is_regular_skill("regular-skill") is True
    assert rail._is_regular_skill("team-skill") is False
    assert rail._is_regular_skill("swarm-skill") is False


# =============================================================================
# Zero-signal fallback & unattributed signal tests
# =============================================================================


@pytest.mark.asyncio
async def test_run_evolution_zero_signals_skips_conversation_review(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"arguments": "/skills/skill-a/SKILL.md"},
            ],
        },
        {"role": "user", "content": "hello"},
    ]
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._infer_primary_skill = Mock(return_value="skill-a")
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())
    rail._evolution_store.append_record = AsyncMock()

    await rail.run_evolution(
        _trajectory_with_messages(messages), AgentCallbackContext(agent=None, inputs=None, session=None)
    )

    rail._stage_evolution_from_signals.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evolution_uses_normalized_messages_for_signal_detection(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    rail._evolution_store.list_skill_names = Mock(return_value=[])
    detector = Mock()
    detector.bind_llm.return_value = detector
    detector.detect_trajectory_signals.return_value = []
    detector.detect_user_intent = AsyncMock(return_value=[])
    trajectory = Trajectory(
        execution_id="exec-message-object",
        steps=[
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(
                    model="test-model",
                    messages=[SystemMessage(content="system prompt")],
                ),
            )
        ],
        source="online",
    )
    snapshot = {
        "trajectory": trajectory,
        "messages": [{"role": "system", "content": "system prompt"}],
        "presented_entries": [],
    }

    with patch(
        "openjiuwen.harness.rails.evolution.skill_evolution_rail.SignalDetector",
        return_value=detector,
    ):
        await rail.run_evolution(trajectory, ctx=None, snapshot=snapshot)

    detector.detect_trajectory_signals.assert_called_once_with(
        trajectory,
        signal_types={"execution_failure", "script_artifact"},
    )
    detector.detect_user_intent.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evolution_does_not_use_llm_for_passive_user_messages(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"arguments": "/skills/skill-a/SKILL.md"}],
        },
        {"role": "user", "content": "不对，你应该先检查文件是否存在"},
    ]
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._infer_primary_skill = Mock(return_value="skill-a")
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())

    rail._evolver._llm.invoke = AsyncMock(
        return_value={"content": '{"is_feedback": true, "excerpt": "不对，你应该先检查文件是否存在"}'}
    )
    await rail.run_evolution(
        _trajectory_with_messages(messages), AgentCallbackContext(agent=None, inputs=None, session=None)
    )

    rail._stage_evolution_from_signals.assert_not_awaited()
    rail._evolver._llm.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evolution_auto_scan_consumes_script_artifact_rule_signal(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc_skill",
                    "name": "read_file",
                    "type": "function",
                    "arguments": '{"path": "/skills/skill-a/SKILL.md"}',
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "tc_skill",
            "name": "read_file",
            "content": "# skill-a",
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc_1",
                    "name": "python_exec",
                    "type": "function",
                    "arguments": '{"code": "print(\'hello world\')\\nfor i in range(10): print(i)"}',
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "tc_1",
            "name": "python_exec",
            "content": "hello world\n0\n1\n2",
        },
    ]
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._infer_primary_skill = Mock(return_value="skill-a")
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())

    await rail.run_evolution(
        _trajectory_with_messages(messages), AgentCallbackContext(agent=None, inputs=None, session=None)
    )

    rail._stage_evolution_from_signals.assert_awaited_once()
    signals_passed = rail._stage_evolution_from_signals.await_args.kwargs["signals"]
    assert [signal.signal_type for signal in signals_passed] == ["script_artifact"]


@pytest.mark.asyncio
async def test_run_evolution_auto_scan_ignores_team_collaboration_activity(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    trajectory = Trajectory(
        execution_id="team-collab",
        session_id="session-team",
        source="online",
        steps=[
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(
                    model="test-model",
                    messages=[
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "tc_skill",
                                    "name": "read_file",
                                    "type": "function",
                                    "arguments": '{"path": "/skills/skill-a/SKILL.md"}',
                                }
                            ],
                        },
                    ],
                ),
            ),
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(
                    tool_name="send_message",
                    call_args={"to_member_name": "coder", "message": "please continue"},
                    call_result="sent",
                ),
            ),
        ],
        meta={"member_id": "researcher", "team_id": "team-1"},
    )
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._evolution_store.skill_exists = Mock(return_value=True)
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())

    await rail.run_evolution(trajectory, AgentCallbackContext(agent=None, inputs=None, session=None))

    rail._stage_evolution_from_signals.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evolution_zero_signals_no_primary_skill_returns(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    messages = [{"role": "user", "content": "hi"}]
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._infer_primary_skill = Mock(return_value=None)
    rail._stage_evolution_from_signals = AsyncMock()

    await rail.run_evolution(
        _trajectory_with_messages(messages), AgentCallbackContext(agent=None, inputs=None, session=None)
    )

    rail._stage_evolution_from_signals.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evolution_emits_started_and_cancelled_when_no_skill_used(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    messages = [{"role": "user", "content": "hi"}]
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._infer_primary_skill = Mock(return_value=None)
    rail._stage_evolution_from_signals = AsyncMock()

    await rail.run_evolution(
        _trajectory_with_messages(messages),
        AgentCallbackContext(agent=None, inputs=None, session=None),
    )

    events = _progress_events(await rail.drain_pending_host_events())
    stages = [event.payload["evolution_meta"]["stage"] for event in events]
    contents = [event.payload["content"] for event in events]

    assert stages[0] == "started"
    assert "detecting_signals" in stages
    assert stages[-1] == "cancelled"
    assert "regular skill" in contents[-1]
    assert "no skill usage" in contents[-1]
    assert "cancelling" in contents[-1]
    rail._stage_evolution_from_signals.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evolution_cancels_when_all_signals_are_unattributed(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    messages = [{"role": "tool", "content": "Error: command failed", "name": "bash"}]
    detector = Mock()
    detector.bind_llm.return_value = detector
    detector.detect_trajectory_signals.return_value = [_make_signal(None)]
    detector.detect_user_intent = AsyncMock(return_value=[])

    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._stage_evolution_from_signals = AsyncMock()

    with patch(
        "openjiuwen.harness.rails.evolution.skill_evolution_rail.SignalDetector",
        return_value=detector,
    ):
        await rail.run_evolution(
            _trajectory_with_messages(messages),
            AgentCallbackContext(agent=None, inputs=None, session=None),
        )

    events = _progress_events(await rail.drain_pending_host_events())
    stages = [event.payload["evolution_meta"]["stage"] for event in events]
    assert stages[-1] == "cancelled"
    assert "signals_attributed" not in stages
    assert "no regular skill could be attributed" in events[-1].payload["content"]
    rail._stage_evolution_from_signals.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evolution_filters_team_and_swarm_skills_from_detection(tmp_path):
    rail = SkillEvolutionRail(
        skills_dir=str(tmp_path / "skills"),
        llm=Mock(),
        model="dummy-model",
        auto_scan=True,
        auto_save=True,
        review_runtime=_default_review_runtime(),
    )
    rail._evolver = Mock()

    regular_dir = tmp_path / "skills" / "skill-a"
    regular_dir.mkdir(parents=True)
    (regular_dir / "SKILL.md").write_text(
        "---\nname: skill-a\nkind: skill\n---\n# Skill A",
        encoding="utf-8",
    )

    team_dir = tmp_path / "skills" / "team-skill-a"
    team_dir.mkdir(parents=True)
    (team_dir / "SKILL.md").write_text(
        "---\nname: team-skill-a\nkind: team-skill\n---\n# Team Skill A",
        encoding="utf-8",
    )
    swarm_dir = tmp_path / "skills" / "swarm-skill-a"
    swarm_dir.mkdir(parents=True)
    (swarm_dir / "SKILL.md").write_text(
        "---\nname: swarm-skill-a\nkind: swarm-skill\nroles:\n  - name: planner\n    kind: ai_agent\n---\n# Swarm Skill A",
        encoding="utf-8",
    )

    messages = [{"role": "user", "content": "hi"}]
    rail._evolution_store = EvolutionStore(str(tmp_path / "skills"))
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a", "team-skill-a", "swarm-skill-a"])
    rail._infer_primary_skill = Mock(return_value=None)
    rail._stage_evolution_from_signals = AsyncMock()

    await rail.run_evolution(
        _trajectory_with_messages(messages), AgentCallbackContext(agent=None, inputs=None, session=None)
    )

    rail._infer_primary_skill.assert_not_called()


@pytest.mark.asyncio
async def test_run_evolution_unattributed_signals_get_fallback_skill(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/skill-a/SKILL.md"}]},
        {"role": "tool", "content": "Error: command failed", "name": "bash"},
        {"role": "tool", "content": "Traceback: boom"},
    ]

    rail._collect_messages = AsyncMock(return_value=messages)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())
    rail._evolution_store.append_record = AsyncMock()

    await rail.run_evolution(
        _trajectory_with_messages(messages), AgentCallbackContext(agent=None, inputs=None, session=None)
    )

    rail._stage_evolution_from_signals.assert_awaited_once()
    call_args = rail._stage_evolution_from_signals.await_args
    assert all(signal.skill_name == "skill-a" for signal in call_args.kwargs["signals"])


@pytest.mark.asyncio
async def test_run_evolution_multiple_attributed_skills_no_fallback(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/skill-a/SKILL.md"}]},
        {"role": "tool", "content": "Error: a", "name": "bash"},
        {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/skill-b/SKILL.md"}]},
        {"role": "tool", "content": "Error: b", "name": "bash"},
    ]

    rail._collect_messages = AsyncMock(return_value=messages)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a", "skill-b"])
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())
    rail._evolution_store.append_record = AsyncMock()

    await rail.run_evolution(
        _trajectory_with_messages(messages), AgentCallbackContext(agent=None, inputs=None, session=None)
    )

    assert rail._stage_evolution_from_signals.await_count == 2


@pytest.mark.asyncio
async def test_run_evolution_continues_when_only_some_signals_are_attributed(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    messages = [{"role": "tool", "content": "Error: command failed", "name": "bash"}]
    detector = Mock()
    detector.bind_llm.return_value = detector
    detector.detect_trajectory_signals.return_value = [
        _make_signal("skill-a", excerpt="Error: a"),
        _make_signal(None, excerpt="Error: unattributed"),
        _make_signal("skill-b", excerpt="Error: b"),
    ]
    detector.detect_user_intent = AsyncMock(return_value=[])

    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a", "skill-b"])
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())
    rail._evolution_store.append_record = AsyncMock()

    with patch(
        "openjiuwen.harness.rails.evolution.skill_evolution_rail.SignalDetector",
        return_value=detector,
    ):
        await rail.run_evolution(
            _trajectory_with_messages(messages),
            AgentCallbackContext(agent=None, inputs=None, session=None),
        )

    assert rail._stage_evolution_from_signals.await_count == 2
    handled_skills = [call.kwargs["skill_name"] for call in rail._stage_evolution_from_signals.await_args_list]
    assert handled_skills == ["skill-a", "skill-b"]
    assert all(call.kwargs["signals"][0].skill_name for call in rail._stage_evolution_from_signals.await_args_list)

    events = _progress_events(await rail.drain_pending_host_events())
    attributed_events = [event for event in events if event.payload["evolution_meta"]["stage"] == "signals_attributed"]
    assert len(attributed_events) == 1
    assert "skill_name" not in attributed_events[0].payload["evolution_meta"]
    assert "2 signal(s)" in attributed_events[0].payload["content"]
    assert "2 regular skill(s)" in attributed_events[0].payload["content"]


# =============================================================================
# Constructor Validation Tests (Issue #4)
# =============================================================================


def test_init_invalid_eval_interval_raises():
    with pytest.raises(ValueError, match="eval_interval"):
        SkillEvolutionRail(
            skills_dir="skills",
            llm=Mock(),
            model="m",
            review_runtime=_default_review_runtime(),
            eval_interval=0,
        )


def test_team_trajectory_store_is_not_accepted_by_skill_evolution_rail():
    with pytest.raises(TypeError, match="team_trajectory_store"):
        SkillEvolutionRail(
            skills_dir="skills",
            llm=Mock(),
            model="m",
            review_runtime=_default_review_runtime(),
            team_trajectory_store=Mock(),
        )


def test_init_valid_params_no_error():
    rail = SkillEvolutionRail(
        skills_dir="skills",
        llm=Mock(),
        model="m",
        review_runtime=_default_review_runtime(),
        eval_interval=3,
    )
    assert rail._eval_interval == 3


def test_init_accepts_custom_policies_and_timeout():
    generate_policy = LLMInvokePolicy(
        attempt_timeout_secs=9,
        total_budget_secs=27,
        max_attempts=2,
    )
    evaluate_policy = LLMInvokePolicy(
        attempt_timeout_secs=11,
        total_budget_secs=33,
        max_attempts=2,
    )
    simplify_policy = LLMInvokePolicy(
        attempt_timeout_secs=13,
        total_budget_secs=39,
        max_attempts=2,
    )
    rail = SkillEvolutionRail(
        skills_dir="skills",
        llm=Mock(),
        model="m",
        review_runtime=_default_review_runtime(),
        evolution_total_timeout_secs=555.0,
        generate_records_llm_policy=generate_policy,
        evaluate_llm_policy=evaluate_policy,
        simplify_llm_policy=simplify_policy,
    )

    assert rail.evolution_total_timeout_secs == 555.0
    assert rail.generate_records_llm_policy is generate_policy
    assert rail.evaluate_llm_policy is evaluate_policy
    assert rail.simplify_llm_policy is simplify_policy
    assert rail.evolution_config["generate_records_llm_policy"] is generate_policy
    assert rail.evolution_config["evolution_total_timeout_secs"] == 555.0


def test_update_llm_refreshes_fresh_optimizer_references(tmp_path):
    rail = _make_rail(tmp_path)
    rail._scorer = Mock()
    new_llm = Mock()

    rail.update_llm(new_llm, "new-model")

    rail._evolver.update_llm.assert_called_once_with(new_llm, "new-model")
    rail._scorer.update_llm.assert_called_once_with(new_llm, "new-model")


@pytest.mark.asyncio
async def test_drain_pending_approval_events_defaults_to_total_timeout(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_total_timeout_secs = 321.0

    assert await rail.drain_pending_approval_events(wait=True) == []


# =============================================================================
# Session-level State Isolation Tests
# =============================================================================


def test_tracker_session_presented_records_isolated_per_session(tmp_path):
    from types import SimpleNamespace

    rail = _make_rail(tmp_path)
    telemetry = rail._experience_tracker
    session_a = SimpleNamespace()
    session_b = SimpleNamespace()

    # Set different values for different sessions
    telemetry.set_session_presented_records(session_a, [("skill-a", _make_record("skill-a"), "snippet-a")])
    telemetry.set_session_presented_records(session_b, [("skill-b", _make_record("skill-b"), "snippet-b")])

    records_a = telemetry.get_session_presented_records(session_a)
    records_b = telemetry.get_session_presented_records(session_b)

    assert len(records_a) == 1
    assert records_a[0][0] == "skill-a"
    assert records_a[0][2] == "snippet-a"
    assert len(records_b) == 1
    assert records_b[0][0] == "skill-b"
    assert records_b[0][2] == "snippet-b"


def test_tracker_session_eval_counter_isolated_per_session(tmp_path):
    from types import SimpleNamespace

    rail = _make_rail(tmp_path)
    telemetry = rail._experience_tracker
    session_a = SimpleNamespace()
    session_b = SimpleNamespace()

    telemetry.set_session_eval_counter(session_a, 3)
    telemetry.set_session_eval_counter(session_b, 7)

    assert telemetry.get_session_eval_counter(session_a) == 3
    assert telemetry.get_session_eval_counter(session_b) == 7


def test_tracker_session_helpers_with_none_session(tmp_path):
    rail = _make_rail(tmp_path)
    telemetry = rail._experience_tracker
    assert telemetry.get_session_presented_records(None) == []
    assert telemetry.get_session_eval_counter(None) == 0
    # Setting with None session must not raise
    telemetry.set_session_presented_records(None, [])
    telemetry.set_session_eval_counter(None, 5)


# =============================================================================
# Fix #2: Presented records carry per-presentation snippet
# =============================================================================


def test_tracker_session_presented_records_store_snippet(tmp_path):
    """Each entry stored in session must include the presentation-time snippet."""
    rail = _make_rail(tmp_path)
    telemetry = rail._experience_tracker
    session = SimpleNamespace()

    record = _make_record("sk")
    telemetry.set_session_presented_records(session, [("sk", record, "my_snippet")])

    entries = telemetry.get_session_presented_records(session)
    assert len(entries) == 1
    skill_name, stored_record, snippet = entries[0]
    assert skill_name == "sk"
    assert stored_record is record
    assert snippet == "my_snippet"


@pytest.mark.asyncio
async def test_tracker_evaluation_uses_per_record_snippet(tmp_path):
    """Scorer must be called with the snippet bound to each record, not current messages."""
    rail = _make_rail(tmp_path)
    telemetry = rail._experience_tracker

    record_a = _make_record("skill_a")
    record_b = _make_record("skill_b")

    # Two records from different conversations with different snippets
    presented_entries = [
        ("skill_a", record_a, "snippet_from_turn_1"),
        ("skill_b", record_b, "snippet_from_turn_2"),
    ]

    captured_snippets: list[str] = []

    async def fake_evaluate(snippet, records):
        captured_snippets.append(snippet)
        return []

    rail._scorer = Mock()
    rail._scorer.evaluate = fake_evaluate
    telemetry = ExperienceTracker(
        store=rail._evolution_store,
        scorer=rail._scorer,
        eval_interval=rail._eval_interval,
    )

    await telemetry.evaluate_presented(presented_entries)

    # Must have evaluated with the stored snippets, not the current messages
    assert "snippet_from_turn_1" in captured_snippets
    assert "snippet_from_turn_2" in captured_snippets


@pytest.mark.asyncio
async def test_snapshot_consumes_experience_tracker_state(tmp_path):
    rail = _make_rail(tmp_path)
    record = _make_record("skill-a")
    presented_entries = [("skill-a", record, "snippet")]
    rail._experience_tracker.consume_eval_state = Mock(return_value=presented_entries)
    session = SimpleNamespace()
    ctx = AgentCallbackContext(
        agent=None,
        inputs=SimpleNamespace(conversation_id="session-1"),
        session=session,
        context=_MsgContext(messages=[{"role": "user", "content": "hello"}]),
    )

    snapshot = await rail._snapshot_for_evolution(
        _trajectory_with_messages([{"role": "user", "content": "hello"}]),
        ctx,
    )

    assert snapshot is not None
    assert snapshot["presented_entries"] == presented_entries
    rail._experience_tracker.consume_eval_state.assert_called_once_with(session)


@pytest.mark.asyncio
async def test_run_evolution_evaluates_presented_entries_from_snapshot(tmp_path):
    rail = _make_rail(tmp_path)
    record = _make_record("skill-a")
    presented_entries = [("skill-a", record, "snippet")]
    rail._experience_tracker.evaluate_presented = AsyncMock()
    rail._evolution_store.list_skill_names = Mock(return_value=[])
    rail._infer_primary_skill = Mock(return_value=None)

    detector = Mock()
    detector.bind_llm.return_value = detector
    detector.detect_trajectory_signals = Mock(return_value=[])
    detector.detect_user_intent = AsyncMock(return_value=[])
    with patch("openjiuwen.harness.rails.evolution.skill_evolution_rail.SignalDetector", return_value=detector):
        await rail.run_evolution(
            None,
            snapshot={
                "trajectory": None,
                "messages": [{"role": "user", "content": "hello"}],
                "presented_entries": presented_entries,
            },
        )

    rail._experience_tracker.evaluate_presented.assert_awaited_once_with(presented_entries)


# =============================================================================
# Fix #3: Only BODY records are tracked as presented
# =============================================================================


@pytest.mark.asyncio
async def test_tracker_record_presented_only_body_records(tmp_path):
    """record_presented must only update BODY records, not DESCRIPTION records."""
    rail = _make_rail(tmp_path)
    telemetry = rail._experience_tracker

    body_record = _make_record("sk")
    body_record.target = EvolutionTarget.BODY
    body_record.score = 0.8

    desc_record = _make_record("sk", content="desc exp")
    desc_record.target = EvolutionTarget.DESCRIPTION
    desc_record.score = 0.9  # Higher score but wrong type

    rail._evolution_store.get_records_by_score = AsyncMock(return_value=[desc_record, body_record])
    rail._evolution_store.update_record_scores = AsyncMock()

    session = SimpleNamespace()
    await telemetry.record_presented(session=session, skill_name="sk", presentation_snippet="some_snippet")

    # update_record_scores must only contain the body record id
    call_args = rail._evolution_store.update_record_scores.call_args
    updates: dict = call_args[0][1]
    assert body_record.id in updates
    assert desc_record.id not in updates

    # Session must only contain the body record
    entries = telemetry.get_session_presented_records(session)
    assert len(entries) == 1
    assert entries[0][1].id == body_record.id


@pytest.mark.asyncio
async def test_tracker_record_presented_skips_when_no_body_records(tmp_path):
    """record_presented must be a no-op when there are no BODY records."""
    rail = _make_rail(tmp_path)
    telemetry = rail._experience_tracker

    desc_record = _make_record("sk")
    desc_record.target = EvolutionTarget.DESCRIPTION
    desc_record.score = 0.9

    rail._evolution_store.get_records_by_score = AsyncMock(return_value=[desc_record])
    rail._evolution_store.update_record_scores = AsyncMock()

    session = SimpleNamespace()
    await telemetry.record_presented(session=session, skill_name="sk", presentation_snippet="snip")

    rail._evolution_store.update_record_scores.assert_not_called()
    assert telemetry.get_session_presented_records(session) == []


@pytest.mark.asyncio
async def test_tracker_record_presented_records_only_matching_body_ids(tmp_path):
    rail = _make_rail(tmp_path)
    telemetry = rail._experience_tracker

    body_record = _make_record("sk")
    body_record.id = "ev_body"
    body_record.score = 0.8
    desc_record = _make_record("sk", content="desc exp")
    desc_record.id = "ev_desc"
    desc_record.change.target = EvolutionTarget.DESCRIPTION
    desc_record.score = 0.9

    rail._evolution_store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[desc_record, body_record]))
    rail._evolution_store.update_record_scores = AsyncMock()

    session = SimpleNamespace()
    await telemetry.record_presented_records(
        session=session,
        skill_name="sk",
        presentation_snippet="### [ev_desc]\n### [ev_body]",
        record_ids=["ev_desc", "ev_body"],
    )

    updates = rail._evolution_store.update_record_scores.call_args.args[1]
    assert list(updates) == ["ev_body"]
    entries = telemetry.get_session_presented_records(session)
    assert len(entries) == 1
    assert entries[0][1].id == "ev_body"


@pytest.mark.asyncio
async def test_after_tool_call_does_not_record_experience_tracker(tmp_path):
    """after_tool_call must not record presentation telemetry for SKILL.md reads."""
    rail = _make_rail(tmp_path, auto_scan=True)

    tool_inputs = ToolCallInputs(
        tool_name="read_file",
        tool_args={"file_path": "/workspace/skills/my_skill/SKILL.md"},
    )
    ctx = Mock()
    ctx.inputs = tool_inputs
    ctx.session = None

    rail._evolution_store.format_body_experience_text = AsyncMock(return_value="")
    rail._experience_tracker.record_presented = AsyncMock()

    await rail._on_after_tool_call(ctx)

    rail._experience_tracker.record_presented.assert_not_awaited()


@pytest.mark.asyncio
async def test_after_tool_call_does_not_record_skill_md_index_ids(tmp_path):
    """SKILL.md index visibility must not count as BODY experience presentation."""
    rail = _make_rail(tmp_path, auto_scan=True)
    rail._experience_tracker.record_presented_records = AsyncMock()

    tool_inputs = ToolCallInputs(
        tool_name="skill_tool",
        tool_args={"skill_name": "my_skill"},
        tool_msg=_DummyToolMsg("## Evolution Experiences\n- [ev_body] (score=0.90) useful"),
    )
    ctx = AgentCallbackContext(agent=None, inputs=tool_inputs, session=SimpleNamespace())

    await rail._on_after_tool_call(ctx)

    rail._experience_tracker.record_presented_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_after_tool_call_records_skill_tool_evolution_detail_read(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True)
    session = SimpleNamespace()
    rail._experience_tracker.record_presented_records = AsyncMock()

    tool_inputs = ToolCallInputs(
        tool_name="skill_tool",
        tool_args={
            "skill_name": "my_skill",
            "relative_file_path": "evolution/troubleshooting.md",
        },
        tool_result=SimpleNamespace(
            data={
                "skill_content": "# Troubleshooting\n\n### [ev_body] Fix the parser\nDetails",
            }
        ),
        tool_msg=_DummyToolMsg("ToolOutput wrapper"),
    )
    ctx = AgentCallbackContext(agent=None, inputs=tool_inputs, session=session)

    await rail._on_after_tool_call(ctx)

    rail._experience_tracker.record_presented_records.assert_awaited_once_with(
        session=session,
        skill_name="my_skill",
        presentation_snippet="# Troubleshooting\n\n### [ev_body] Fix the parser\nDetails",
        record_ids=["ev_body"],
    )


@pytest.mark.asyncio
async def test_after_tool_call_records_read_file_evolution_detail_read(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True)
    session = SimpleNamespace()
    skill_dir = tmp_path / "skills" / "my_skill"
    experience_file = skill_dir / "evolution" / "troubleshooting.md"
    rail._evolution_store.list_skill_names = Mock(return_value=["my_skill"])
    rail._evolution_store.resolve_skill_dir = Mock(return_value=skill_dir)
    rail._experience_tracker.record_presented_records = AsyncMock()

    tool_inputs = ToolCallInputs(
        tool_name="read_file",
        tool_args={"file_path": str(experience_file)},
        tool_msg=_DummyToolMsg("     7\t### [ev_body] Fix the parser\n     8\tDetails"),
    )
    ctx = AgentCallbackContext(agent=None, inputs=tool_inputs, session=session)

    await rail._on_after_tool_call(ctx)

    rail._experience_tracker.record_presented_records.assert_awaited_once_with(
        session=session,
        skill_name="my_skill",
        presentation_snippet="     7\t### [ev_body] Fix the parser\n     8\tDetails",
        record_ids=["ev_body"],
    )


@pytest.mark.asyncio
async def test_after_tool_call_skips_evolution_detail_when_no_record_ids(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True)
    rail._experience_tracker.record_presented_records = AsyncMock()

    tool_inputs = ToolCallInputs(
        tool_name="skill_tool",
        tool_args={
            "skill_name": "my_skill",
            "relative_file_path": "evolution/troubleshooting.md",
        },
        tool_msg=_DummyToolMsg("Details from a partial read without a heading id"),
    )
    ctx = AgentCallbackContext(agent=None, inputs=tool_inputs, session=SimpleNamespace())

    await rail._on_after_tool_call(ctx)

    rail._experience_tracker.record_presented_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_presented_experiences_delegates_to_tracker(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True)
    session = SimpleNamespace()
    rail._experience_tracker.record_presented = AsyncMock()

    await rail.record_presented_experiences(
        "skill-a",
        "presentation snippet",
        session=session,
    )

    rail._experience_tracker.record_presented.assert_awaited_once_with(
        session=session,
        skill_name="skill-a",
        presentation_snippet="presentation snippet",
    )


@pytest.mark.asyncio
async def test_record_presented_experiences_with_record_ids_delegates_to_tracker(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True)
    session = SimpleNamespace()
    rail._experience_tracker.record_presented_records = AsyncMock()

    await rail.record_presented_experiences(
        "skill-a",
        "presentation snippet",
        session=session,
        record_ids=["ev_1", "ev_2"],
    )

    rail._experience_tracker.record_presented_records.assert_awaited_once_with(
        session=session,
        skill_name="skill-a",
        presentation_snippet="presentation snippet",
        record_ids=["ev_1", "ev_2"],
    )


# =============================================================================
# Fix #4: create_skill refuses to overwrite existing skill
# =============================================================================


@pytest.mark.asyncio
async def test_create_skill_refuses_to_overwrite_existing(tmp_path):
    """create_skill must return None and not write files when skill dir already exists."""
    from openjiuwen.agent_evolving.checkpointing.evolution_store import EvolutionStore

    store = EvolutionStore(str(tmp_path))

    # Pre-create the skill directory to simulate an existing skill
    existing_dir = tmp_path / "my_skill"
    existing_dir.mkdir()
    existing_skill_md = existing_dir / "SKILL.md"
    existing_skill_md.write_text("original content")

    result = await store.create_skill(
        name="my_skill",
        description="duplicate",
        body="should not overwrite",
    )

    assert result is None, "create_skill must return None for existing skill"
    # Original file must be untouched
    assert existing_skill_md.read_text() == "original content"


@pytest.mark.asyncio
async def test_create_skill_succeeds_for_new_skill(tmp_path):
    """create_skill must succeed and create files when skill does not exist."""
    from openjiuwen.agent_evolving.checkpointing.evolution_store import EvolutionStore

    store = EvolutionStore(str(tmp_path))

    result = await store.create_skill(
        name="brand_new_skill",
        description="A fresh skill",
        body="## Instructions\nDo things.",
    )

    assert result is not None
    assert (result / "SKILL.md").exists()
    content = (result / "SKILL.md").read_text()
    assert "brand_new_skill" in content
    assert "A fresh skill" in content


@pytest.mark.asyncio
async def test_approve_record_and_reject_record_aliases(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    rail.approval_runtime.approve_pending_request = AsyncMock(
        return_value=(SimpleNamespace(skill_name="skill-a"), SimpleNamespace(pending_count=0, applied_count=1))
    )
    rail.approval_runtime.reject_pending_request = AsyncMock(
        return_value=(SimpleNamespace(skill_name="skill-a"), SimpleNamespace(rejected_count=1))
    )

    await rail.on_approve("req-approve")
    await rail.on_reject("req-reject")

    rail.approval_runtime.approve_pending_request.assert_awaited_once_with(
        "req-approve",
        rail_name="SkillEvolutionRail",
        action_name="approve_record",
    )
    rail.approval_runtime.reject_pending_request.assert_awaited_once_with(
        "req-reject",
        rail_name="SkillEvolutionRail",
        action_name="reject_record",
    )


@pytest.mark.asyncio
async def test_on_approve_uses_rebound_pending_snapshot_store(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a", content="rebound")
    rail._evolution_store.append_record = AsyncMock()

    request = rail._manager.stage_records(
        "skill-a",
        [record],
        requires_approval=True,
    )
    pending = rail._pending_approval_snapshots.pop(request.request_id)

    rebound_snapshots = {request.request_id: pending}
    rail._pending_approval_snapshots = rebound_snapshots
    rail._manager.bind_pending_approval_snapshots(rebound_snapshots)

    await rail.on_approve(request.request_id)

    rail._evolution_store.append_record.assert_awaited_once_with("skill-a", record, subject_kind="skill")
    assert request.request_id not in rebound_snapshots


@pytest.mark.asyncio
async def test_stage_evolution_from_signals_stages_explicit_request_metadata_from_preferred_user_intent(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    operator = Mock()
    operator.operator_id = "skill_experience_skill-a"
    operator.get_state = Mock(return_value={})
    operator.load_state = Mock()
    operator.refresh_state = AsyncMock()
    operator.apply_update = Mock(
        return_value=ApplyResult(
            operator_id="skill_experience_skill-a",
            target="experiences",
            applied=True,
            mode="append",
            effect="pending_change",
            value=["new-record"],
            records=["new-record"],
            change_type="skill_experience_entry",
            lifecycle_stage="local_apply_completed",
        )
    )
    rail._skill_ops["skill-a"] = operator
    rail._online_updater.bind = Mock()
    rail._online_updater.process = AsyncMock(return_value={("skill_experience_skill-a", "experiences"): ["new-record"]})
    rail._manager.stage_apply_results = Mock(return_value=SimpleNamespace(request_id="req-1"))

    passive_signal = make_evolution_signal(
        signal_type="execution_failure",
        section="Troubleshooting",
        excerpt="passive",
        skill_name="skill-a",
        source="conversation",
    )
    explicit_signal = make_evolution_signal(
        signal_type="user_intent",
        section="Instructions",
        excerpt="explicit",
        skill_name="skill-a",
        source="explicit_request",
    )

    await rail._stage_evolution_from_signals(
        "skill-a",
        [passive_signal, explicit_signal],
        [{"role": "user", "content": "explicit"}],
        user_query="explicit",
        requires_approval=True,
    )

    assert rail._manager.stage_apply_results.call_args.kwargs["user_query"] == "explicit"
    assert rail._manager.stage_apply_results.call_args.kwargs["signal_type"] == "user_intent"
    assert rail._manager.stage_apply_results.call_args.kwargs["signal_source"] == "explicit_request"


@pytest.mark.asyncio
async def test_emit_generated_records_preserves_signal_metadata_in_event(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    pending_change = SimpleNamespace(payload=[], change_id="req-1")
    approval_request = SimpleNamespace(
        pending_change=pending_change,
        request_id="req-1",
        proposal=SimpleNamespace(
            record_count=0,
            signal_type="user_intent",
            signal_source="explicit_request",
        ),
    )

    await rail._emit_generated_records(None, "skill-a", approval_request)

    event = _approval_events(rail._pending_host_events)[-1]
    assert event.payload["evolution_meta"] == {
        "event_kind": "approval",
        "rail_kind": "regular",
        "skill_name": "skill-a",
        "request_id": "req-1",
        "signal_type": "user_intent",
        "source": "explicit_request",
    }


# =============================================================================
# Breaking Change Tests
# =============================================================================


def test_rewrite_skill_api_removed(tmp_path):
    rail = _make_rail(tmp_path)
    assert not hasattr(rail, "rewrite_skill")


def test_skill_rewriter_exports_removed():
    from openjiuwen.agent_evolving.optimizer import skill_call

    assert not hasattr(skill_call, "SkillRewriter")
    assert not hasattr(skill_call, "SkillRewriteResult")



# =============================================================================
# Experience sharing integration
# =============================================================================


@pytest.mark.asyncio
async def test_on_approve_runs_qc_after_approval(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    rail._evolution_store.append_record = AsyncMock()

    sharer = Mock()
    sharer.has_pending = Mock(return_value=True)
    sharer.flush_pending_uploads = AsyncMock()
    rail._experience_sharer = sharer

    stager = Mock()
    stager.screen_and_stage = AsyncMock()
    rail._share_stager = stager
    rail._keyword_extractor = Mock()

    request = _stage_approval_request(rail, "skill-a", [record])
    await rail._emit_generated_records(ctx, "skill-a", request)
    events = _approval_events(await rail.drain_pending_approval_events())
    request_id = events[0].payload["request_id"]

    await rail.on_approve(request_id)

    stager.screen_and_stage.assert_awaited_once_with(
        skill_name="skill-a",
        records=[record],
        messages=None,
    )
    sharer.flush_pending_uploads.assert_awaited_once_with("skill-a")


@pytest.mark.asyncio
async def test_approve_record_stages_only_approved_records_for_share(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    record_1 = _make_record("skill-a", content="approved")
    record_2 = _make_record("skill-a", content="rejected")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    sharer = Mock()
    sharer.has_pending = Mock(return_value=True)
    sharer.flush_pending_uploads = AsyncMock()
    rail._experience_sharer = sharer

    stager = Mock()
    stager.screen_and_stage = AsyncMock()
    rail._share_stager = stager
    rail._keyword_extractor = Mock()

    request = _stage_approval_request(rail, "skill-a", [record_1, record_2])
    await rail._emit_generated_records(ctx, "skill-a", request)

    await rail.approve_record(request.request_id, approved_record_ids=[record_1.id])

    stager.screen_and_stage.assert_awaited_once_with(
        skill_name="skill-a",
        records=[record_1],
        messages=None,
    )
    sharer.flush_pending_uploads.assert_awaited_once_with("skill-a")


@pytest.mark.asyncio
async def test_on_reject_does_not_upload_shared_queue(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    sharer = Mock()
    sharer.has_pending = Mock(return_value=True)
    sharer.flush_pending_uploads = AsyncMock()
    sharer.discard_pending_uploads = AsyncMock()
    rail._experience_sharer = sharer
    rail._share_stager = Mock()
    rail._keyword_extractor = Mock()

    request = _stage_approval_request(rail, "skill-a", [record])
    await rail._emit_generated_records(ctx, "skill-a", request)
    request_id = _approval_events(await rail.drain_pending_approval_events())[0].payload["request_id"]

    await rail.on_reject(request_id)

    sharer.flush_pending_uploads.assert_not_awaited()
    sharer.discard_pending_uploads.assert_not_called()


@pytest.mark.asyncio
async def test_on_approve_skips_upload_for_shared_hub_records(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    rail._evolution_store.append_record = AsyncMock()

    stager = Mock()
    stager.screen_and_stage = AsyncMock()
    rail._experience_sharer = Mock()
    rail._share_stager = stager
    rail._keyword_extractor = Mock()

    request = rail._manager.stage_records(
        "skill-a",
        [record],
        source="experience_sharing",
        is_shared_records=True,
    )
    await rail._emit_generated_records(ctx, "skill-a", request)
    approval_event = _approval_events(await rail.drain_pending_approval_events())[0]
    request_id = approval_event.payload["request_id"]
    assert approval_event.payload["questions"][0]["header"] == "在线共享经验审批"

    await rail.on_approve(request_id)

    stager.screen_and_stage.assert_not_awaited()


@pytest.mark.asyncio
async def test_is_sharing_enabled_requires_both_sharer_and_stager(tmp_path):
    rail = _make_rail(tmp_path)
    assert rail.is_sharing_enabled is False

    rail._experience_sharer = Mock()
    rail._share_stager = Mock()
    rail._keyword_extractor = Mock()
    assert rail.is_sharing_enabled is True


# =============================================================================
# disabled_skills Tests
# =============================================================================


def test_disabled_skills_constructor_parameter(tmp_path):
    rail = SkillEvolutionRail(
        skills_dir=str(tmp_path / "skills"),
        llm=Mock(),
        model="dummy-model",
        disabled_skills=["skill-a", "skill-b"],
        review_runtime=_default_review_runtime(),
    )
    assert rail.disabled_skills == {"skill-a", "skill-b"}


def test_disabled_skills_from_single_string(tmp_path):
    rail = SkillEvolutionRail(
        skills_dir=str(tmp_path / "skills"),
        llm=Mock(),
        model="dummy-model",
        disabled_skills="skill-a",
        review_runtime=_default_review_runtime(),
    )
    assert rail.disabled_skills == {"skill-a"}


def test_disabled_skills_defaults_to_empty(tmp_path):
    rail = SkillEvolutionRail(
        skills_dir=str(tmp_path / "skills"),
        llm=Mock(),
        model="dummy-model",
        review_runtime=_default_review_runtime(),
    )
    assert rail.disabled_skills == set()


@pytest.mark.asyncio
async def test_run_evolution_filters_out_disabled_skills(tmp_path):
    """run_evolution should exclude disabled skills from the evolution scope."""
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True, disabled_skills=["disabled-skill"])

    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/active-skill/SKILL.md"}]},
        {"role": "tool", "content": "Error: command failed", "name": "bash"},
    ]
    trajectory = _trajectory_with_messages(messages)

    rail._evolution_store.list_skill_names = Mock(return_value=["active-skill", "disabled-skill"])
    rail._is_regular_skill = Mock(return_value=True)
    rail._collect_messages = AsyncMock(return_value=messages)
    rail._handle_evolution_from_signals = AsyncMock(return_value=_no_records_result("active-skill"))

    await rail.run_evolution(trajectory, ctx=None, snapshot={"trajectory": trajectory, "messages": messages})

    rail._handle_evolution_from_signals.assert_awaited_once()
    call_kwargs = rail._handle_evolution_from_signals.call_args
    assert call_kwargs.kwargs["skill_name"] == "active-skill"


@pytest.mark.asyncio
async def test_run_evolution_all_skills_disabled(tmp_path):
    """run_evolution should skip when all skills are disabled."""
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True, disabled_skills=["skill-a", "skill-b"])

    messages = [{"role": "user", "content": "hello"}]
    trajectory = _trajectory_with_messages(messages)

    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a", "skill-b"])
    rail._is_regular_skill = Mock(return_value=True)
    rail._collect_messages = AsyncMock(return_value=messages)
    rail._handle_evolution_from_signals = AsyncMock(return_value=_no_records_result("skill-a"))

    await rail.run_evolution(trajectory, ctx=None, snapshot={"trajectory": trajectory, "messages": messages})

    rail._handle_evolution_from_signals.assert_not_awaited()


def test_skill_evolution_rail_registers_stable_review_subagent_without_rail_state(monkeypatch, tmp_path):
    from openjiuwen.harness.rails.evolution.review.subagent import EVOLUTION_REVIEW_AGENT_NAME

    rail = _make_rail(tmp_path)
    agent = SimpleNamespace(
        card=SimpleNamespace(id="agent-1"),
        ability_manager=SimpleNamespace(add=Mock(return_value=SimpleNamespace(added=True))),
        deep_config=SimpleNamespace(subagents=[]),
        find_rails_by_type=_find_rails_by_type(SubagentRail()),
    )

    monkeypatch.setattr(
        "openjiuwen.core.runner.Runner.resource_mgr.add_tool",
        Mock(return_value=SimpleNamespace(is_err=lambda: False)),
    )

    rail.init(agent)

    assert [config.agent_card.name for config in agent.deep_config.subagents] == [EVOLUTION_REVIEW_AGENT_NAME]
    assert agent.deep_config.subagents[0].model is None
    review_query_services = {
        getattr(tool, "_query_service")
        for tool in agent.deep_config.subagents[0].tools
        if hasattr(tool, "_query_service") and getattr(tool, "_query_service") is not None
    }
    assert review_query_services == {rail.experience_manager.experience_query_service}
    assert getattr(rail, "_review_runtime") is not None


@pytest.mark.asyncio
async def test_run_evolution_regular_signal_uses_online_updater_without_passive_state(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._evolution_store.skill_exists = Mock(return_value=True)
    rail._evolution_store.resolve_skill_dir = Mock(return_value=None)
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())
    detector = Mock()
    detector.bind_llm.return_value = detector
    detector.detect_trajectory_signals.return_value = [_make_signal("skill-a")]
    detector.detect_user_intent = AsyncMock(return_value=[])

    with patch("openjiuwen.harness.rails.evolution.skill_evolution_rail.SignalDetector", return_value=detector):
        await rail.run_evolution(
            _trajectory_with_messages([{"role": "user", "content": "trigger"}]),
            AgentCallbackContext(agent=None, inputs=None, session=None),
        )

    rail._stage_evolution_from_signals.assert_awaited_once()
