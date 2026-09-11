# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Authoring must not mistake a controller-created draft for an existing skill."""

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from openjiuwen.rsi.harness_rsi.member_optimizer import action_executor
from openjiuwen.rsi.harness_rsi.member_optimizer.schema import (
    MemberOptimizationAction,
    MemberOptimizationPlan,
    MemberOptimizationTarget,
)


@pytest.mark.parametrize(
    "group,operation,existing",
    [
        ("skill", "add", False),
        ("prompt", "add", False),
        ("skill", "modify", False),
        ("skill", "modify", True),
        ("prompt", "modify", True),
    ],
)
@pytest.mark.parametrize("outcome", ["authored", "failed", "empty"])
def test_semantic_authoring_requires_authored_content(tmp_path, monkeypatch, group, operation, existing, outcome):
    root = tmp_path / "harness"
    root.mkdir()
    (root / "identity.md").write_text("Original identity.\n", encoding="utf-8")
    (root / "harness.yaml").write_text("role: solver\nversion: '1.0'\n", encoding="utf-8")
    target = "skills/readback/SKILL.md" if group == "skill" else "prompt_sections/files/readback.md"
    registry = "skills/skills.yaml" if group == "skill" else "prompt_sections/sections.yaml"
    old_target = "skills/baseline/SKILL.md" if group == "skill" else "prompt_sections/files/baseline.md"
    old_content = "---\nname: baseline\ndescription: Original workflow\n---\nKeep original workflow.\n"
    (root / old_target).parent.mkdir(parents=True)
    (root / old_target).write_text(old_content, encoding="utf-8")
    old_entry = (
        "skills/baseline"
        if group == "skill"
        else {
            "name": "baseline",
            "file": old_target,
            "priority": 10,
        }
    )
    expected_entry = (
        "skills/readback"
        if group == "skill"
        else {
            "name": "readback",
            "file": target,
            "priority": 30,
        }
    )
    original = "---\nname: readback\ndescription: Original readback\n---\nORIGINAL_AUTHORED_PROCEDURE\n"
    if existing:
        (root / target).parent.mkdir(parents=True, exist_ok=True)
        (root / target).write_text(original, encoding="utf-8")
    list_key = "skills" if group == "skill" else "sections"
    initial_entries = [old_entry, expected_entry] if existing else [old_entry]
    (root / registry).write_text(yaml.safe_dump({list_key: initial_entries}), encoding="utf-8")
    action = MemberOptimizationAction(
        action_id="author_readback",
        role="solver",
        action_group=group,
        operation=operation,
        action_type="skill_creation" if group == "skill" else "prompt_improvement",
        target_path=target,
        declared_write_paths=[target, registry],
        description="Read back the changed output.",
        rationale="PRIVATE_DIAGNOSIS_SENTINEL",
        expected_effect="PRIVATE_PATCH_SENTINEL",
        constraints={
            "section_name": "readback",
            "priority": 30,
            "optimization_contracts": [{"public_task_contexts": [{"task": "Verify the requested output."}]}],
        },
    )
    content = (
        "---\nname: readback\ndescription: Use when checking an observable change.\n---\n"
        "Read the public contract, exercise the changed output, and compare it to that contract.\n"
        if group == "skill"
        else "# Readback\nRead the public contract and verify the changed output against it.\n"
    )
    calls = []

    async def respond(message):
        calls.append(message)
        assert "PRIVATE_DIAGNOSIS_SENTINEL" not in message
        assert "PRIVATE_PATCH_SENTINEL" not in message
        assert "Keep original workflow." not in message  # Only declared files, not the whole package.
        assert ("ORIGINAL_AUTHORED_PROCEDURE" in message) == existing
        if outcome == "failed":
            return json.dumps({"status": "failed", "file_writes": [], "errors": ["author unavailable"]})
        return json.dumps(
            {
                "status": "succeeded",
                "errors": [],
                "file_writes": [{"path": target, "content": content}] if outcome == "authored" else [],
            }
        )

    author = action_executor.MemberActionExecutorAgent("unused-by-fake")
    monkeypatch.setattr(author, "_invoke_direct_skill_action", respond)

    class FakeAgent:
        async def invoke(self, inputs, session=None):
            return {"text": await respond(inputs["query"])}

    monkeypatch.setattr(action_executor, "create_action_execution_agent", lambda **kwargs: FakeAgent())
    plan = MemberOptimizationPlan(
        plan_id="semantic_authoring",
        targets=[MemberOptimizationTarget(role="solver", harness_ref_path=str(root))],
        actions=[action],
        action_waves=[[action.action_id]],
    )
    results = asyncio.run(
        action_executor.MemberActionExecutor(executor_agent=author).execute(
            plan=plan,
            output_dir=str(tmp_path / "run"),
            model_config_ref="unused-by-fake",
        )
    )
    assert calls
    result = results[0]
    assert result.status == ("succeeded" if outcome == "authored" else "failed"), result.error
    workspace = Path(result.worktree_path)
    assert (workspace / old_target).read_text(encoding="utf-8") == old_content
    entries = yaml.safe_load((workspace / registry).read_text(encoding="utf-8"))[list_key]
    assert old_entry in entries
    if outcome == "authored":
        assert "Read the public contract" in (workspace / target).read_text(encoding="utf-8")
        assert expected_entry in entries
    else:
        if existing:
            assert (workspace / target).read_text(encoding="utf-8") == original
        else:
            assert not (workspace / target).exists()
        assert entries == initial_entries
