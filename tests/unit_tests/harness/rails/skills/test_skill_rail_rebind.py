# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.harness.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail
from tests.unit_tests.harness.test_skill_rail import _CannedBackend, _SessionState, _write_skill


@pytest.mark.level1
@pytest.mark.asyncio
@pytest.mark.parametrize("reuse_backend", [False, True], ids=["new-backend", "same-backend"])
async def test_rebind_after_unbind_replaces_committed_session_baseline(tmp_path: Path, reuse_backend: bool):
    """Reattaching a backend refreshes old descriptions even at the same path and mtime."""
    skill_dir = _write_skill(tmp_path, "alpha", "Local description")
    initial_text = "---\ndescription: Original backend description\n---\n"
    rebound_text = "---\ndescription: Rebound backend description\n---\n"
    first_backend = _CannedBackend(initial_text)
    rail = SkillUseRail(skills_dir=str(tmp_path), skill_mode="all", include_tools=False)
    rail.system_prompt_builder = SystemPromptBuilder()
    session = _SessionState("rebind")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)

    rail.set_sys_operation(first_backend)
    await rail.before_invoke(ctx)
    old_state = session.get_state("skill_use")
    assert "Original backend description" in rail.system_prompt_builder.build()

    rail.set_sys_operation(first_backend)
    assert [skill.description for skill in rail.get_skills_for_session(session)] == ["Original backend description"]
    assert session.get_state("skill_use") is old_state

    rail.set_sys_operation(None)
    await rail.before_model_call(ctx)
    assert [skill.description for skill in rail.get_skills_for_session(session)] == ["Local description"]
    assert session.get_state("skill_use") is old_state

    if reuse_backend:
        first_backend._text = rebound_text
        rebound_backend = first_backend
    else:
        rebound_backend = _CannedBackend(rebound_text)
    rail.set_sys_operation(rebound_backend)
    assert rail.get_skills_for_session(session) == []
    assert "Original backend description" not in rail.system_prompt_builder.build()
    assert "Local description" not in rail.system_prompt_builder.build()
    assert session.get_state("skill_use") is old_state

    read_count = len(rebound_backend.reads)
    await rail.reload_skills()
    assert [path for path, _ in rebound_backend.reads[read_count:]] == [str(skill_dir / "SKILL.md")]
    assert [skill.description for skill in rail.get_skills_for_session(session)] == ["Rebound backend description"]
    rebound_state = session.get_state("skill_use")
    assert rebound_state is not old_state
    assert [item["description"] for item in rebound_state["baseline_skills"]] == ["Rebound backend description"]

    await rail.before_model_call(ctx)
    prompt = rail.system_prompt_builder.build()
    assert "Rebound backend description" in prompt
    assert "Original backend description" not in prompt
    assert session.get_state("skill_use") is rebound_state


@pytest.mark.level1
@pytest.mark.asyncio
async def test_rebind_waits_for_commit_before_exposing_candidate_baseline(tmp_path: Path):
    """Reading the new file does not commit its description while the refresh is unfinished."""
    _write_skill(tmp_path, "alpha", "Local description")
    rail = SkillUseRail(skills_dir=str(tmp_path), skill_mode="all", include_tools=False)
    rail.system_prompt_builder = SystemPromptBuilder()
    session = _SessionState("waiting-for-rebind")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)
    rail.set_sys_operation(_CannedBackend("---\ndescription: Original backend description\n---\n"))
    await rail.before_invoke(ctx)
    old_state = session.get_state("skill_use")

    evolution_started = asyncio.Event()
    release_evolution = asyncio.Event()

    class _PausedEvolutionStore:
        async def format_desc_experience_text(self, skill_name: str) -> str:
            evolution_started.set()
            await release_evolution.wait()
            return ""

    rail.set_sys_operation(None)
    rebound_backend = _CannedBackend("---\ndescription: Rebound backend description\n---\n")
    rail.set_sys_operation(rebound_backend)
    rail.evolution_store = _PausedEvolutionStore()
    refresh = asyncio.create_task(rail.reload_skills())
    try:
        await asyncio.wait_for(evolution_started.wait(), 5)
        assert rebound_backend.reads
        assert rail.get_skills_for_session(session) == []
        assert rail.get_skills_for_session(_SessionState("new-session")) == []
        assert session.get_state("skill_use") is old_state
    finally:
        release_evolution.set()
        await asyncio.wait_for(refresh, 5)

    assert [skill.description for skill in rail.get_skills_for_session(session)] == ["Rebound backend description"]
    assert session.get_state("skill_use") is not old_state


@pytest.mark.level1
@pytest.mark.asyncio
async def test_rebind_persists_a_successful_empty_baseline(tmp_path: Path):
    """A successful scan discovers that old backend skills disappeared."""
    skill_dir = _write_skill(tmp_path, "alpha", "Local description")
    rail = SkillUseRail(skills_dir=str(tmp_path), skill_mode="all", include_tools=False)
    rail.system_prompt_builder = SystemPromptBuilder()
    session = _SessionState("empty-rebind")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)
    rail.set_sys_operation(_CannedBackend("---\ndescription: Original backend description\n---\n"))
    await rail.before_invoke(ctx)
    old_state = session.get_state("skill_use")

    rail.set_sys_operation(None)
    (skill_dir / "SKILL.md").unlink()
    rail.set_sys_operation(_CannedBackend(""))
    assert rail.get_skills_for_session(session) == []
    assert session.get_state("skill_use") is old_state

    await rail.before_model_call(ctx)
    assert rail.get_skills_for_session(session) == []
    assert "Original backend description" not in rail.system_prompt_builder.build()
    assert session.get_state("skill_use")["baseline_skills"] == []
