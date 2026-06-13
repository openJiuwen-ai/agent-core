# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.harness.rails import SubagentRail
from openjiuwen.harness.rails.evolution import (
    EvolutionInterruptRail,
    SkillEvolutionRail,
    TeamSkillEvolutionRail,
    configure_skill_evolution,
    configure_skill_evolution_runtime,
    unconfigure_skill_evolution,
)
from openjiuwen.harness.rails.evolution.review.runtime import EvolutionReviewRuntime
from openjiuwen.harness.rails import (
    configure_skill_evolution as exported_configure,
    configure_skill_evolution_runtime as exported_configure_runtime,
    unconfigure_skill_evolution as exported_unconfigure,
)


def _skills_dir(tmp_path) -> str:
    return str(tmp_path / "skills")


def _mock_llm() -> Model:
    return Mock()


def _agent(find_subagent_rails=None, strip_result=0):
    """Build a mock agent with the required rail methods."""
    strip_mock = Mock(return_value=strip_result)
    agent = Mock(
        find_rails_by_type=Mock(return_value=find_subagent_rails or []),
        add_rail=Mock(),
        strip_rails_by_type=strip_mock,
    )
    return agent


def test_configure_skill_evolution_regular_adds_rails_in_order(tmp_path):
    agent = _agent()

    result = configure_skill_evolution(
        agent,
        skills_dir=_skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        team=False,
        auto_save=True,
        language="en",
    )

    assert result is agent
    # Should add EvolutionInterruptRail
    interrupt_rails = [
        call.args[0] for call in agent.add_rail.call_args_list if isinstance(call.args[0], EvolutionInterruptRail)
    ]
    assert len(interrupt_rails) == 1

    # Should add SkillEvolutionRail (team=False), use type() to avoid
    # matching TeamSkillEvolutionRail subclass
    skill_rails = [call.args[0] for call in agent.add_rail.call_args_list if type(call.args[0]) is SkillEvolutionRail]
    assert len(skill_rails) == 1
    assert skill_rails[0].auto_save is True
    assert skill_rails[0]._language == "en"

    assert agent.add_rail.call_count == 2
    assert [type(call.args[0]) for call in agent.add_rail.call_args_list] == [
        EvolutionInterruptRail,
        SkillEvolutionRail,
    ]
    assert interrupt_rails[0]._submission_service is skill_rails[0].approval_submission_service


def test_configure_skill_evolution_team_adds_team_evolution_rail(tmp_path):
    agent = _agent()

    configure_skill_evolution(
        agent,
        skills_dir=_skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        team=True,
        auto_save=True,
        language="en",
    )

    # TeamSkillEvolutionRail extends SkillEvolutionRail, use type identity
    team_rails = [
        call.args[0] for call in agent.add_rail.call_args_list if type(call.args[0]) is TeamSkillEvolutionRail
    ]
    assert len(team_rails) == 1

    regular_skill_rails = [
        call.args[0] for call in agent.add_rail.call_args_list if type(call.args[0]) is SkillEvolutionRail
    ]
    assert len(regular_skill_rails) == 0

    assert agent.add_rail.call_count == 2
    assert [type(call.args[0]) for call in agent.add_rail.call_args_list] == [
        EvolutionInterruptRail,
        TeamSkillEvolutionRail,
    ]


def test_configure_does_not_add_subagent_rail_when_already_present(tmp_path):
    agent = _agent(find_subagent_rails=[SubagentRail()])

    configure_skill_evolution(
        agent,
        skills_dir=_skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
    )

    subagent_calls = [call.args[0] for call in agent.add_rail.call_args_list if isinstance(call.args[0], SubagentRail)]
    # Should NOT call add_rail for SubagentRail since it already exists
    assert len(subagent_calls) == 0

    # Still adds interrupt + evolution rails
    assert agent.add_rail.call_count == 2


def test_unconfigure_removes_regular_rails(tmp_path):
    agent = _agent()

    count = unconfigure_skill_evolution(agent, team=False)

    agent.strip_rails_by_type.assert_called_once_with((SkillEvolutionRail, EvolutionInterruptRail))
    assert count == agent.strip_rails_by_type.return_value


def test_unconfigure_removes_team_rails(tmp_path):
    agent = _agent()

    count = unconfigure_skill_evolution(agent, team=True)

    agent.strip_rails_by_type.assert_called_once_with((TeamSkillEvolutionRail, EvolutionInterruptRail))
    assert count == agent.strip_rails_by_type.return_value


def test_unconfigure_removes_all_evolution_rails(tmp_path):
    agent = _agent()

    count = unconfigure_skill_evolution(agent, team=None)

    agent.strip_rails_by_type.assert_called_once_with(
        (SkillEvolutionRail, TeamSkillEvolutionRail, EvolutionInterruptRail)
    )
    assert count == agent.strip_rails_by_type.return_value


def test_unconfigure_returns_strip_count(tmp_path):
    agent = _agent(strip_result=2)

    count = unconfigure_skill_evolution(agent, team=False)

    assert count == 2


def test_new_functions_can_be_imported_from_both_packages():
    from openjiuwen.harness.rails import (
        configure_skill_evolution,
        configure_skill_evolution_runtime,
        unconfigure_skill_evolution,
    )

    assert configure_skill_evolution is exported_configure
    assert configure_skill_evolution_runtime is exported_configure_runtime
    assert unconfigure_skill_evolution is exported_unconfigure


def test_old_build_factory_names_no_longer_exported():
    for name in (
        "build_skill_evolution_rails",
        "build_team_skill_evolution_rails",
        "build_skill_and_team_evolution_rails",
    ):
        with pytest.raises((ImportError, AttributeError)):
            from openjiuwen.harness.rails import evolution

            getattr(evolution, name)


# ── Idempotency tests ──


def _typed_agent(find_rails_by_type=None, strip_result=0):
    """Build a mock agent where find_rails_by_type can be customized per type."""
    strip_mock = Mock(return_value=strip_result)

    def _finder(types):
        if find_rails_by_type is not None:
            return find_rails_by_type(types)
        return []

    agent = Mock(
        find_rails_by_type=Mock(side_effect=_finder),
        add_rail=Mock(),
        strip_rails_by_type=strip_mock,
    )
    return agent


class _RuntimeAgent:
    """Stateful rail fake for runtime registration tests."""

    def __init__(self, *, pending_rails=None, registered_rails=None):
        self._pending_rails = list(pending_rails or [])
        self._registered_rails = list(registered_rails or [])
        self.add_rail = Mock(side_effect=self._add_rail)
        self.register_rail = AsyncMock(side_effect=self._register_rail)
        self.strip_rails_by_type = Mock(return_value=0)
        self.find_rails_by_type = Mock(side_effect=self._find_rails_by_type)

    def _add_rail(self, rail):
        self._pending_rails.append(rail)
        return self

    async def _register_rail(self, rail):
        self._registered_rails.append(rail)
        return self

    def _find_rails_by_type(self, types):
        return [rail for rail in (*self._pending_rails, *self._registered_rails) if isinstance(rail, types)]


def test_configure_twice_regular_is_idempotent(tmp_path):
    runtime = EvolutionReviewRuntime()
    evolution_rail = SkillEvolutionRail(
        _skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        review_runtime=runtime,
    )
    interrupt_rail = EvolutionInterruptRail(
        review_runtime=runtime,
        submission_service=evolution_rail.approval_submission_service,
    )

    def _finder(types):
        if SkillEvolutionRail in types:
            return [evolution_rail]
        if EvolutionInterruptRail in types:
            return [interrupt_rail]
        if SubagentRail in types:
            return [SubagentRail()]
        return []

    agent = _typed_agent(find_rails_by_type=_finder)

    result = configure_skill_evolution(
        agent,
        skills_dir=_skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        team=False,
        review_runtime=runtime,
    )

    assert result is agent
    # Second call should not add any rails
    agent.add_rail.assert_not_called()


def test_configure_twice_team_is_idempotent(tmp_path):
    runtime = EvolutionReviewRuntime()
    evolution_rail = TeamSkillEvolutionRail(
        _skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        review_runtime=runtime,
    )
    interrupt_rail = EvolutionInterruptRail(
        review_runtime=runtime,
        submission_service=evolution_rail.approval_submission_service,
    )

    def _finder(types):
        if TeamSkillEvolutionRail in types:
            return [evolution_rail]
        if EvolutionInterruptRail in types:
            return [interrupt_rail]
        if SubagentRail in types:
            return [SubagentRail()]
        return []

    agent = _typed_agent(find_rails_by_type=_finder)

    result = configure_skill_evolution(
        agent,
        skills_dir=_skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        team=True,
        review_runtime=runtime,
    )

    assert result is agent
    agent.add_rail.assert_not_called()


def test_configure_regular_config_mismatch_fails(tmp_path):
    evolution_rail = SkillEvolutionRail(
        _skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        review_runtime=EvolutionReviewRuntime(),
        auto_save=False,
        language="cn",
    )

    def _finder(types):
        if SkillEvolutionRail in types:
            return [evolution_rail]
        return []

    agent = _typed_agent(find_rails_by_type=_finder)

    with pytest.raises(RuntimeError, match="config mismatch"):
        configure_skill_evolution(
            agent,
            skills_dir=_skills_dir(tmp_path),
            llm=_mock_llm(),
            model="dummy-model",
            team=False,
            review_runtime=evolution_rail._review_runtime,
            auto_save=True,  # different from existing (False)
        )


def test_configure_regular_then_team_fails(tmp_path):
    evolution_rail = SkillEvolutionRail(
        _skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        review_runtime=EvolutionReviewRuntime(),
    )
    team_rail = TeamSkillEvolutionRail(
        _skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        review_runtime=EvolutionReviewRuntime(),
    )

    def _finder(types):
        if SkillEvolutionRail in types:
            return [evolution_rail]
        if TeamSkillEvolutionRail in types:
            return [team_rail]
        return []

    agent = _typed_agent(find_rails_by_type=_finder)

    with pytest.raises(RuntimeError, match="TeamSkillEvolutionRail is already configured"):
        configure_skill_evolution(
            agent,
            skills_dir=_skills_dir(tmp_path),
            llm=_mock_llm(),
            model="dummy-model",
            team=False,
            review_runtime=evolution_rail._review_runtime,
        )


def test_configure_team_then_regular_fails(tmp_path):
    evolution_rail = SkillEvolutionRail(
        _skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        review_runtime=EvolutionReviewRuntime(),
    )
    team_rail = TeamSkillEvolutionRail(
        _skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        review_runtime=EvolutionReviewRuntime(),
    )

    def _finder(types):
        if SkillEvolutionRail in types:
            return [evolution_rail]
        if TeamSkillEvolutionRail in types:
            return [team_rail]
        return []

    agent = _typed_agent(find_rails_by_type=_finder)

    with pytest.raises(RuntimeError, match="SkillEvolutionRail is already configured"):
        configure_skill_evolution(
            agent,
            skills_dir=_skills_dir(tmp_path),
            llm=_mock_llm(),
            model="dummy-model",
            team=True,
            review_runtime=team_rail._review_runtime,
        )


def test_configure_binds_unbound_interrupt_rail(tmp_path):
    evolution_rail = SkillEvolutionRail(
        _skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        review_runtime=EvolutionReviewRuntime(),
    )
    # Create an unbound interrupt rail (no review_runtime set)
    interrupt_rail = EvolutionInterruptRail(
        review_runtime=None,
        submission_service=None,
    )

    def _finder(types):
        if SkillEvolutionRail in types:
            return [evolution_rail]
        if EvolutionInterruptRail in types:
            return [interrupt_rail]
        if SubagentRail in types:
            return [SubagentRail()]
        return []

    agent = _typed_agent(find_rails_by_type=_finder)

    result = configure_skill_evolution(
        agent,
        skills_dir=_skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        team=False,
        review_runtime=evolution_rail._review_runtime,
    )

    assert result is agent
    # Evolution rail already exists, not re-added
    sk_rails = [call.args[0] for call in agent.add_rail.call_args_list if type(call.args[0]) is SkillEvolutionRail]
    assert len(sk_rails) == 0
    # Interrupt rail was bound in-place, not re-added
    int_rails = [
        call.args[0] for call in agent.add_rail.call_args_list if isinstance(call.args[0], EvolutionInterruptRail)
    ]
    assert len(int_rails) == 0
    # Interrupt rail should now be bound
    assert interrupt_rail._review_runtime is evolution_rail._review_runtime
    assert interrupt_rail._submission_service is evolution_rail.approval_submission_service


def test_configure_fails_on_bound_interrupt_submission_mismatch(tmp_path):
    runtime = EvolutionReviewRuntime()
    evolution_rail = SkillEvolutionRail(
        _skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        review_runtime=runtime,
    )
    interrupt_rail = EvolutionInterruptRail(
        review_runtime=runtime,
        submission_service=object(),
    )

    def _finder(types):
        if SkillEvolutionRail in types:
            return [evolution_rail]
        if EvolutionInterruptRail in types:
            return [interrupt_rail]
        return []

    agent = _typed_agent(find_rails_by_type=_finder)

    with pytest.raises(RuntimeError, match="binding mismatch"):
        configure_skill_evolution(
            agent,
            skills_dir=_skills_dir(tmp_path),
            llm=_mock_llm(),
            model="dummy-model",
            team=False,
            review_runtime=runtime,
        )


def test_configure_ignores_existing_subagent_rail(tmp_path):
    from openjiuwen.harness.rails.evolution.review.runtime import EvolutionReviewRuntime

    evolution_rail = SkillEvolutionRail(
        _skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        review_runtime=EvolutionReviewRuntime(),
    )
    interrupt_rail = EvolutionInterruptRail(
        review_runtime=evolution_rail._review_runtime,
        submission_service=evolution_rail.approval_submission_service,
    )

    def _finder(types):
        if SkillEvolutionRail in types:
            return [evolution_rail]
        if EvolutionInterruptRail in types:
            return [interrupt_rail]
        if SubagentRail in types:
            return [SubagentRail()]
        return []

    agent = _typed_agent(find_rails_by_type=_finder)

    configure_skill_evolution(
        agent,
        skills_dir=_skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        team=False,
        review_runtime=evolution_rail._review_runtime,
    )

    subagent_calls = [call.args[0] for call in agent.add_rail.call_args_list if isinstance(call.args[0], SubagentRail)]
    assert len(subagent_calls) == 0


def test_first_configure_does_not_add_subagent_rail(tmp_path):
    def _finder(types):
        return []

    agent = _typed_agent(find_rails_by_type=_finder)

    configure_skill_evolution(
        agent,
        skills_dir=_skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        team=False,
    )

    subagent_calls = [call.args[0] for call in agent.add_rail.call_args_list if isinstance(call.args[0], SubagentRail)]
    assert len(subagent_calls) == 0
    assert agent.add_rail.call_count == 2


@pytest.mark.asyncio
async def test_configure_skill_evolution_runtime_first_config_registers_new_rails_in_runtime_order(tmp_path):
    agent = _RuntimeAgent()
    runtime = EvolutionReviewRuntime()

    result = await configure_skill_evolution_runtime(
        agent,
        skills_dir=_skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        review_runtime=runtime,
    )

    assert result is agent
    registered = [call.args[0] for call in agent.register_rail.await_args_list]
    assert [type(rail) for rail in registered] == [
        EvolutionInterruptRail,
        SkillEvolutionRail,
    ]
    assert agent._pending_rails == []


@pytest.mark.asyncio
async def test_configure_skill_evolution_runtime_team_config_registers_new_rails_in_runtime_order(tmp_path):
    agent = _RuntimeAgent()
    runtime = EvolutionReviewRuntime()

    result = await configure_skill_evolution_runtime(
        agent,
        skills_dir=_skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        team=True,
        review_runtime=runtime,
    )

    assert result is agent
    registered = [call.args[0] for call in agent.register_rail.await_args_list]
    assert [type(rail) for rail in registered] == [
        EvolutionInterruptRail,
        TeamSkillEvolutionRail,
    ]
    team_rail = registered[1]
    interrupt_rail = registered[0]
    assert team_rail._review_runtime is runtime
    assert interrupt_rail._review_runtime is runtime
    assert interrupt_rail._submission_service is team_rail.approval_submission_service
    assert agent._pending_rails == []


@pytest.mark.asyncio
async def test_configure_skill_evolution_runtime_second_consistent_config_does_not_register_again(tmp_path):
    agent = _RuntimeAgent()
    runtime = EvolutionReviewRuntime()

    await configure_skill_evolution_runtime(
        agent,
        skills_dir=_skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        review_runtime=runtime,
    )
    agent.register_rail.reset_mock()

    result = await configure_skill_evolution_runtime(
        agent,
        skills_dir=_skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        review_runtime=runtime,
    )

    assert result is agent
    agent.register_rail.assert_not_awaited()
    assert agent._pending_rails == []


@pytest.mark.asyncio
async def test_configure_skill_evolution_runtime_reuses_existing_runtime_when_runtime_omitted(tmp_path):
    agent = _RuntimeAgent()

    await configure_skill_evolution_runtime(
        agent,
        skills_dir=_skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
    )
    agent.register_rail.reset_mock()

    result = await configure_skill_evolution_runtime(
        agent,
        skills_dir=_skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
    )

    assert result is agent
    agent.register_rail.assert_not_awaited()
    assert agent._pending_rails == []


@pytest.mark.asyncio
async def test_configure_skill_evolution_runtime_binds_existing_unbound_interrupt_without_registering(tmp_path):
    runtime = EvolutionReviewRuntime()
    evolution_rail = SkillEvolutionRail(
        _skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        review_runtime=runtime,
    )
    interrupt_rail = EvolutionInterruptRail(
        review_runtime=None,
        submission_service=None,
    )
    agent = _RuntimeAgent(registered_rails=[evolution_rail, interrupt_rail, SubagentRail()])

    result = await configure_skill_evolution_runtime(
        agent,
        skills_dir=_skills_dir(tmp_path),
        llm=_mock_llm(),
        model="dummy-model",
        review_runtime=runtime,
    )

    assert result is agent
    agent.register_rail.assert_not_awaited()
    assert interrupt_rail._review_runtime is runtime
    assert interrupt_rail._submission_service is evolution_rail.approval_submission_service
