# coding: utf-8
"""Lightweight test for TeamSkillRail evolution flow.

Usage:
    cd agent-core
    python tests/test_team_skill_evolution.py

No real LLM calls — uses a mock that returns canned JSON.
No real agent/service needed — constructs synthetic Trajectory directly.
Typical run time: < 3 seconds.
"""

from __future__ import annotations

import asyncio
import pytest
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openjiuwen.agent_evolving.trajectory.types import (
    Trajectory,
    TrajectoryStep,
    ToolCallDetail,
    LLMCallDetail,
)
from openjiuwen.harness.rails.team_skill_rail import TeamSkillRail


# ============================================================
# Mock LLM: returns canned JSON, no network calls
# ============================================================

_CREATE_RESPONSE = """\
```json
{
  "should_create": true,
  "name": "deep-research-to-ppt",
  "description": "多角色深度调研并生成PPT的协作模式",
  "body": "# Deep Research to PPT\\n\\n由多个 researcher 并行调研，最后由 merger 汇总成 PPT。",
  "reason": "该协作模式涉及10个角色并行调研+1个合并角色，具有通用性",
  "roles": [
    {"id": "researcher", "skills": [], "tools": ["web_search"]},
    {"id": "merger", "skills": ["pptx"], "tools": []}
  ],
  "extra_files": {
    "roles/researcher.md": "# Researcher\\n负责深度调研指定主题并生成单页PPT。",
    "roles/merger.md": "# Merger\\n负责将所有单页PPT合并为完整文档。",
    "workflow.md": "# Workflow\\n1. Leader分配10个调研任务 (并行)\\n2. 各Researcher完成调研+单页PPT\\n3. Merger合并所有PPT",
    "bind.md": "# Constraints\\n- 每个Researcher最多3轮对话\\n- 总超时60分钟"
  }
}
```"""

_PATCH_RESPONSE = """\
```json
{
  "need_patch": true,
  "section": "Workflow",
  "content": "### 经验: PPT风格统一\\n在分配任务时需明确要求所有Researcher使用统一的颜色主题和字体，避免合并后风格不一致。",
  "reason": "本次执行中各成员PPT风格差异大，合并后不协调"
}
```"""


class MockLLM:
    """Fake LLM that returns pre-defined responses based on prompt keywords."""

    def __init__(self, response: str = _CREATE_RESPONSE):
        self.response = response
        self.call_count = 0

    async def invoke(self, messages: list, model: str = "", **kwargs) -> Any:
        return await self.chat(messages, model, **kwargs)

    async def chat(self, messages: list, model: str = "", **kwargs) -> Any:
        self.call_count += 1
        prompt = messages[0]["content"] if messages else ""
        # Route to different responses based on prompt content
        if "当前 Team Skill 内容" in prompt or "Current Team Skill" in prompt:
            return _MockResponse(_PATCH_RESPONSE)
        return _MockResponse(self.response)


@dataclass
class _MockResponse:
    content: str


# ============================================================
# Mock AgentCallbackContext (minimal stub)
# ============================================================

@dataclass
class _MockAgent:
    @dataclass
    class _Card:
        id: str = "test-leader"
    card: _Card = field(default_factory=_Card)


@dataclass
class _MockCtx:
    agent: Any = field(default_factory=_MockAgent)
    event: Any = None
    inputs: Any = None
    config: Any = None
    session: Any = None
    context: Any = None
    extra: dict = field(default_factory=dict)


# ============================================================
# Trajectory builders
# ============================================================

def build_create_trajectory(member_count: int = 5) -> Trajectory:
    """Build a synthetic trajectory with spawn_member calls (triggers CREATE)."""
    steps = []
    # Leader's initial reasoning
    steps.append(TrajectoryStep(
        kind="llm",
        detail=LLMCallDetail(
            model="glm-5",
            messages=[{"role": "user", "content": "深度调研openclaw技术原理"}],
            response={"content": "我需要分配10个调研任务给不同成员"},
        ),
    ))
    # spawn_member calls
    for i in range(member_count):
        role = f"researcher-{i}"
        steps.append(TrajectoryStep(
            kind="tool",
            detail=ToolCallDetail(
                tool_name="spawn_member",
                call_args={"name": role, "desc": f"负责调研第{i+1}个方面"},
                call_result={"status": "spawned", "member_id": role},
            ),
        ))
    # Some view_task polling
    steps.append(TrajectoryStep(
        kind="tool",
        detail=ToolCallDetail(
            tool_name="view_task",
            call_args={"action": "list"},
            call_result={"tasks": [{"status": "completed"}] * member_count},
        ),
    ))
    return Trajectory(
        execution_id="test-create-001",
        steps=steps,
        source="online",
        session_id="test-session",
    )


def build_patch_trajectory(skill_name: str = "deep-research-to-ppt") -> Trajectory:
    """Build a trajectory that references an existing team skill (triggers PATCH)."""
    steps = []
    # Leader reads the existing SKILL.md
    steps.append(TrajectoryStep(
        kind="tool",
        detail=ToolCallDetail(
            tool_name="read_file",
            call_args=f"team_skills/{skill_name}/SKILL.md",
            call_result="---\nname: deep-research-to-ppt\n---\n# ...",
        ),
    ))
    # Subsequent execution
    for i in range(3):
        steps.append(TrajectoryStep(
            kind="tool",
            detail=ToolCallDetail(
                tool_name="spawn_member",
                call_args={"name": f"researcher-{i}", "desc": f"调研{i}"},
                call_result={"status": "spawned"},
            ),
        ))
    return Trajectory(
        execution_id="test-patch-001",
        steps=steps,
        source="online",
        session_id="test-session-2",
    )


# ============================================================
# Test cases
# ============================================================

@pytest.mark.asyncio
async def test_create_path():
    """Test: no existing skills → CREATE proposal → approval events emitted."""
    print("\n" + "=" * 60)
    print("TEST 1: CREATE path (no existing team skills)")
    print("=" * 60)

    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        mock_llm = MockLLM()
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=mock_llm,
            model="mock-model",
        )

        trajectory = build_create_trajectory(member_count=5)
        ctx = _MockCtx()

        print(f"  Trajectory steps: {len(trajectory.steps)}")
        print(f"  spawn_member calls: {sum(1 for s in trajectory.steps if s.detail and getattr(s.detail, 'tool_name', '') == 'spawn_member')}")

        await rail.run_evolution(trajectory, ctx)

        events = rail.drain_pending_approval_events()
        approval_events = [e for e in events if e.type == "chat.ask_user_question"]
        proposals = rail._pending_skill_proposals

        print(f"  LLM call count: {mock_llm.call_count}")
        print(f"  Pending approval events: {len(approval_events)}")
        print(f"  Pending proposals: {len(proposals)}")

        if approval_events:
            ev = approval_events[0]
            payload = ev.payload
            print(f"  Event type: {ev.type}")
            print(f"  Request ID: {payload.get('request_id', '?')}")
            skill_data = payload.get("_team_skill_data", {})
            print(f"  Proposed skill name: {skill_data.get('name', '?')}")
            print(f"  Proposed description: {skill_data.get('description', '?')}")

        # Simulate approval
        if proposals:
            req_id = list(proposals.keys())[0]
            print(f"\n  Simulating approval for: {req_id}")
            result = await rail.on_approve_team_skill(req_id)
            print(f"  Approved skill: {result}")

            # Check files on disk
            if result:
                skill_dir = tmp / result
                print(f"  Skill dir exists: {skill_dir.exists()}")
                for f in sorted(skill_dir.rglob("*")):
                    if f.is_file():
                        print(f"    {f.relative_to(skill_dir)} ({f.stat().st_size} bytes)")

        assert mock_llm.call_count == 1, "Expected exactly 1 LLM call"
        assert len(approval_events) == 1, "Expected 1 approval event"
        print("\n  PASS: CREATE path passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_patch_path():
    """Test: existing skill detected → PATCH proposal → approval events emitted."""
    print("\n" + "=" * 60)
    print("TEST 2: PATCH path (existing team skill found)")
    print("=" * 60)

    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        # Pre-create a skill so _detect_used_team_skill can find it
        skill_dir = tmp / "deep-research-to-ppt"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: deep-research-to-ppt\nkind: team-skill\n---\n# Deep Research\nWorkflow here.",
            encoding="utf-8",
        )

        mock_llm = MockLLM()
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=mock_llm,
            model="mock-model",
            auto_save=False,
        )

        trajectory = build_patch_trajectory("deep-research-to-ppt")
        ctx = _MockCtx()

        print(f"  Existing skills: {rail.store.list_skill_names()}")
        print(f"  Trajectory steps: {len(trajectory.steps)}")

        await rail.run_evolution(trajectory, ctx)

        events = rail.drain_pending_approval_events()
        approval_events = [e for e in events if e.type == "chat.ask_user_question"]
        patches = rail._pending_patch_snapshots

        print(f"  LLM call count: {mock_llm.call_count}")
        print(f"  Pending patch approval events: {len(approval_events)}")
        print(f"  Pending patch snapshots: {len(patches)}")

        if approval_events:
            ev = approval_events[0]
            print(f"  Event type: {ev.type}")
            print(f"  Request ID: {ev.payload.get('request_id', '?')}")

        # Simulate approval
        if patches:
            req_id = list(patches.keys())[0]
            print(f"\n  Simulating patch approval for: {req_id}")
            await rail.on_approve_patch(req_id)
            print("  Patch approved and persisted")

        assert mock_llm.call_count == 1, "Expected exactly 1 LLM call"
        assert len(approval_events) == 1, "Expected 1 patch approval event"
        print("\n  PASS: PATCH path passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_patch_auto_save():
    """Test: auto_save=True → patch persisted immediately without approval."""
    print("\n" + "=" * 60)
    print("TEST 3: PATCH path with auto_save=True")
    print("=" * 60)

    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        skill_dir = tmp / "deep-research-to-ppt"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: deep-research-to-ppt\nkind: team-skill\n---\n# Deep Research",
            encoding="utf-8",
        )

        mock_llm = MockLLM()
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=mock_llm,
            model="mock-model",
            auto_save=True,
        )

        trajectory = build_patch_trajectory("deep-research-to-ppt")
        ctx = _MockCtx()

        await rail.run_evolution(trajectory, ctx)

        events = rail.drain_pending_approval_events()
        approval_events = [e for e in events if e.type == "chat.ask_user_question"]
        patches = rail._pending_patch_snapshots

        print(f"  LLM call count: {mock_llm.call_count}")
        print(f"  Pending approval events: {len(approval_events)} (should be 0 for auto_save)")
        print(f"  Pending patch snapshots: {len(patches)} (should be 0 for auto_save)")

        assert len(approval_events) == 0, "auto_save should NOT produce approval events"
        assert len(patches) == 0, "auto_save should NOT buffer patches"
        print("\n  PASS: PATCH auto_save passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_below_threshold():
    """Test: too few spawn_member calls → no CREATE triggered."""
    print("\n" + "=" * 60)
    print("TEST 4: Below threshold (only 1 member)")
    print("=" * 60)

    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        mock_llm = MockLLM()
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=mock_llm,
            model="mock-model",
        )

        trajectory = build_create_trajectory(member_count=1)
        ctx = _MockCtx()

        await rail.run_evolution(trajectory, ctx)

        events = rail.drain_pending_approval_events()
        approval_events = [e for e in events if e.type == "chat.ask_user_question"]
        print(f"  LLM call count: {mock_llm.call_count} (should be 0)")
        print(f"  Approval events: {len(approval_events)} (should be 0)")

        assert mock_llm.call_count == 0, "Should NOT call LLM when below threshold"
        assert len(approval_events) == 0, "Should NOT emit events when below threshold"
        print("\n  PASS: Below threshold passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_reject_create():
    """Test: user rejects CREATE proposal → no files created."""
    print("\n" + "=" * 60)
    print("TEST 5: Reject CREATE proposal")
    print("=" * 60)

    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        mock_llm = MockLLM()
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=mock_llm,
            model="mock-model",
        )

        trajectory = build_create_trajectory(member_count=5)
        ctx = _MockCtx()

        await rail.run_evolution(trajectory, ctx)

        proposals = dict(rail._pending_skill_proposals)
        assert len(proposals) == 1, "Should have 1 pending proposal"

        req_id = list(proposals.keys())[0]
        print(f"  Rejecting proposal: {req_id}")
        await rail.on_reject_team_skill(req_id)

        assert len(rail._pending_skill_proposals) == 0, "Proposal should be cleared"
        skill_dirs = list(tmp.iterdir())
        print(f"  Files in tmp dir: {len(skill_dirs)} (should be 0)")
        assert len(skill_dirs) == 0, "No skill dir should be created after rejection"
        print("\n  PASS: Reject CREATE passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_notify_team_completed_without_view_task():
    """Test: notify_team_completed triggers evolution without view_task interception."""
    print("\n" + "=" * 60)
    print("TEST 6: notify_team_completed (external trigger, no view_task)")
    print("=" * 60)

    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        mock_llm = MockLLM()
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=mock_llm,
            model="mock-model",
        )

        # Simulate trajectory accumulation by manually setting builder
        from openjiuwen.agent_evolving.trajectory import TrajectoryBuilder
        rail._builder = TrajectoryBuilder(session_id="test-session", source="online")
        rail._evolution_triggered = False

        # Record steps directly (simulating what before_invoke + after_tool_call do)
        trajectory_steps = build_create_trajectory(member_count=5)
        for step in trajectory_steps.steps:
            rail._builder.record_step(step)

        # Call notify_team_completed WITHOUT ctx (external path)
        result = await rail.notify_team_completed(ctx=None)

        events = rail.drain_pending_approval_events()
        approval_events = [e for e in events if e.type == "chat.ask_user_question"]
        proposals = rail._pending_skill_proposals

        print(f"  notify_team_completed returned: {result}")
        print(f"  LLM call count: {mock_llm.call_count}")
        print(f"  Pending approval events: {len(approval_events)}")
        print(f"  Pending proposals: {len(proposals)}")
        print(f"  evolution_triggered: {rail._evolution_triggered}")

        assert result is True, "notify_team_completed should return True"
        assert mock_llm.call_count == 1, "Expected exactly 1 LLM call"
        assert len(approval_events) == 1, "Expected 1 approval event"
        assert rail._evolution_triggered is True, "Flag should be set"
        print("\n  PASS: notify_team_completed (external trigger) passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_notify_team_completed_idempotent():
    """Test: notify_team_completed is idempotent — second call is a no-op."""
    print("\n" + "=" * 60)
    print("TEST 7: notify_team_completed idempotency")
    print("=" * 60)

    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        mock_llm = MockLLM()
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=mock_llm,
            model="mock-model",
        )

        from openjiuwen.agent_evolving.trajectory import TrajectoryBuilder
        rail._builder = TrajectoryBuilder(session_id="test-session", source="online")
        rail._evolution_triggered = False

        trajectory_steps = build_create_trajectory(member_count=5)
        for step in trajectory_steps.steps:
            rail._builder.record_step(step)

        # First call: should trigger
        result1 = await rail.notify_team_completed(ctx=None)
        # Second call: should be no-op
        result2 = await rail.notify_team_completed(ctx=None)

        print(f"  First call result: {result1}")
        print(f"  Second call result: {result2}")
        print(f"  LLM call count: {mock_llm.call_count} (should be 1)")

        assert result1 is True, "First call should trigger"
        assert result2 is False, "Second call should be no-op"
        assert mock_llm.call_count == 1, "LLM should only be called once"
        print("\n  PASS: idempotency passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_notify_team_completed_no_trajectory():
    """Test: notify_team_completed returns False when no trajectory is available."""
    print("\n" + "=" * 60)
    print("TEST 8: notify_team_completed with no trajectory")
    print("=" * 60)

    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        mock_llm = MockLLM()
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=mock_llm,
            model="mock-model",
        )
        # Do NOT set _builder — simulates notify called before any invoke
        rail._evolution_triggered = False

        result = await rail.notify_team_completed(ctx=None)

        print(f"  Result: {result} (should be False)")
        print(f"  LLM call count: {mock_llm.call_count} (should be 0)")
        print(f"  evolution_triggered: {rail._evolution_triggered} (should be True, flag set even on failure)")

        assert result is False, "Should return False when no trajectory"
        assert mock_llm.call_count == 0, "Should NOT call LLM"
        print("\n  PASS: no trajectory handled correctly")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def main():
    print("=" * 60)
    print("TeamSkillRail Evolution Test Suite (mock LLM, no service)")
    print("=" * 60)

    await test_create_path()
    await test_patch_path()
    await test_patch_auto_save()
    await test_below_threshold()
    await test_reject_create()
    await test_notify_team_completed_without_view_task()
    await test_notify_team_completed_idempotent()
    await test_notify_team_completed_no_trajectory()

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
