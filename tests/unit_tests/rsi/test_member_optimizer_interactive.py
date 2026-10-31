"""Regression coverage for bounded, interactive artifact authors and repairs."""

import asyncio
import json
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace

import pytest

from openjiuwen.rsi.harness_rsi.member_optimizer import action_executor, verification
from openjiuwen.rsi.harness_rsi.member_optimizer.agents import factory
from openjiuwen.rsi.harness_rsi.member_optimizer.schema import MemberOptimizationAction


def _action(operation: str = "add") -> MemberOptimizationAction:
    return MemberOptimizationAction(
        action_id="interactive", role="solver", action_group="prompt",
        operation=operation, action_type="prompt_section", target_path="identity.md",
        description="Improve identity", rationale="Use observed constraints",
        declared_write_paths=["identity.md"],
    )


@pytest.mark.parametrize("repair", [False, True])
def test_native_tools_enforce_write_scope_and_allow_iterative_read_edit(tmp_path: Path, repair: bool) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "identity.md").write_text("Original identity\n", encoding="utf-8")
    (package / "plan.yaml").write_text("original: true\n", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    (package / "escape.md").symlink_to(outside)
    tools, _ = factory._build_file_tools_for_workspace(
        workspace=package, agent_name="scope_regression",
        declared_write_paths=None if repair else ["identity.md"], repair=repair,
    )
    by_name = {tool.card.name: tool for tool in tools}

    async def exercise():
        read = await by_name["read_file"].invoke({"file_path": str(package / "identity.md")})
        assert read.success
        edit = await by_name["edit_file"].invoke({
            "file_path": str(package / "identity.md"), "old_string": "Original", "new_string": "Updated",
        })
        assert edit.success, edit.error
        for forbidden in ["plan.yaml", "escape.md", "../outside.md", ".git/config"]:
            result = await by_name["write_file"].invoke({"file_path": str(package / forbidden), "content": "bad"})
            assert not result.success, forbidden
        if not repair:
            result = await by_name["write_file"].invoke({"file_path": str(package / "soul.md"), "content": "bad"})
            assert not result.success
        assert {"grep", "glob", "list_files"} <= by_name.keys()

    asyncio.run(exercise())
    assert (package / "identity.md").read_text() == "Updated identity\n"
    assert (package / "plan.yaml").read_text() == "original: true\n"
    assert outside.read_text() == "outside\n"


def test_action_accepts_real_tool_edits_without_replacement_payload(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "identity.md").write_text("Original identity\n")

    def build_agent(**kwargs):
        tools, _ = factory._build_file_tools_for_workspace(
            workspace=kwargs["workspace"], agent_name="interactive_author",
            declared_write_paths=kwargs["declared_write_paths"],
        )
        by_name = {tool.card.name: tool for tool in tools}

        async def invoke(inputs, session):
            await by_name["read_file"].invoke({"file_path": str(tmp_path / "identity.md")})
            result = await by_name["edit_file"].invoke({
                "file_path": str(tmp_path / "identity.md"), "old_string": "Original", "new_string": "Updated",
            })
            assert result.success, result.error
            return {"output": json.dumps({"status": "succeeded", "changed_files": ["identity.md"], "errors": []})}
        return SimpleNamespace(invoke=invoke)

    monkeypatch.setattr(action_executor, "create_action_execution_agent", build_agent)
    result = asyncio.run(action_executor.MemberActionExecutorAgent("unused").execute_action(
        tmp_path, _action("modify"), "summary", [], [],
    ))
    assert result["status"] == "succeeded", result
    assert result["changed_files"] == ["identity.md"]
    assert (tmp_path / "identity.md").read_text() == "Updated identity\n"


def test_modify_missing_baseline_fails_without_invoking_author(monkeypatch, tmp_path: Path) -> None:
    def unexpected(**kwargs):
        pytest.fail("missing modify baseline must fail before creating the author")
    monkeypatch.setattr(action_executor, "create_action_execution_agent", unexpected)
    result = asyncio.run(action_executor.MemberActionExecutorAgent("unused").execute_action(
        tmp_path, _action("modify"), "summary", [], [],
    ))
    assert result["status"] == "failed"
    assert "baseline" in result["error"]
    assert not (tmp_path / "identity.md").exists()


def test_rail_contract_rejects_mapping_access_on_typed_hook_inputs(tmp_path: Path) -> None:
    (tmp_path / "guard.py").write_text(
        "class Guard:\n    def after_model_call(self, ctx):\n        return ctx.inputs.get('response')\n",
    )
    check = verification._check_rail_runtime_contract("solver", tmp_path, "guard.py")
    assert check.status == "failed"
    assert "typed" in check.error


def test_repair_no_progress_stops_after_first_attempt(tmp_path: Path) -> None:
    from openjiuwen.rsi.harness_rsi.member_optimizer.schema import (
        MemberVerificationResult, RoleVerificationResult, VerificationCheck,
    )
    root = tmp_path / "wt" / "solver" / "integration"
    root.mkdir(parents=True)
    (root / "harness.yaml").write_text("invalid: [")
    check = VerificationCheck(name="yaml_parse:solver/harness.yaml", status="failed", error="bad YAML")
    initial = MemberVerificationResult(
        status="failed", checked_roles=["solver"], checks=[check], repairable=True,
        role_results={"solver": RoleVerificationResult(
            role="solver", status="failed", checks=[check], repairable=True,
        )},
    )

    class NoProgressRepair:
        calls = 0

        async def repair_role(self, **kwargs):
            self.calls += 1
            return {"status": "attempted", "repairs": [{
                "role": "solver", "action": "deepagent_repair", "description": "claimed success",
                "status": "attempted", "error": "",
            }]}

    repairer = NoProgressRepair()
    result = asyncio.run(verification.HarnessChangeVerifier(repair_agent=repairer).repair(
        initial, tmp_path / "wt", str(tmp_path / "fix.json"), "unused", stage_retry_limit=20,
    ))
    assert result.final_verification_status == "failed"
    assert repairer.calls == 1
    assert result.repairs[0].status == "failed"
    assert "no file changes" in result.repairs[0].error


def test_repair_rolls_back_protected_file_changes(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "plan.yaml").write_text("original: true\n")

    async def invoke(inputs, session):
        (tmp_path / "plan.yaml").write_text("tampered: true\n")
        return {"output": '{"status": "repaired"}'}

    monkeypatch.setattr(verification, "create_verification_repair_agent", lambda **kwargs: SimpleNamespace(invoke=invoke))
    result = asyncio.run(verification.HarnessRepairAgent("unused").repair_role(
        tmp_path, "solver", [{"name": "yaml_parse:solver/harness.yaml", "status": "failed", "error": "bad"}],
    ))
    assert result["status"] == "failed"
    assert "outside" in result["repairs"][0]["error"]
    assert (tmp_path / "plan.yaml").read_text() == "original: true\n"


@pytest.mark.parametrize("hook", ["before_task_iteration", "after_task_iteration"])
def test_rail_contract_rejects_task_loop_only_hooks_in_single_round_solver(tmp_path: Path, hook: str) -> None:
    (tmp_path / "guard.py").write_text(f"class Guard:\n    def {hook}(self, ctx):\n        ctx.push_steering('continue')\n")
    check = verification._check_rail_runtime_contract("solver", tmp_path, "guard.py")
    assert check.status == "failed"
    assert "enable_task_loop=False" in check.error


def test_native_search_can_read_host_contracts_without_writing_them(tmp_path: Path) -> None:
    tools, _ = factory._build_file_tools_for_workspace(
        workspace=tmp_path, agent_name="host_contracts", declared_write_paths=["identity.md"],
    )
    by_name = {tool.card.name: tool for tool in tools}
    host_file = factory.supported_host_read_roots()[0] / "base.py"

    async def exercise():
        from openjiuwen.core.sys_operation.cwd import init_cwd
        init_cwd(str(tmp_path))
        response = await by_name["read_file"].invoke({"file_path": str(host_file), "offset": 295, "limit": 25})
        assert response.success, response.error
        response = await by_name["grep"].invoke({"path": str(host_file), "pattern": "class ModelCallInputs"})
        assert response.success, response.error
        response = await by_name["write_file"].invoke({"file_path": str(host_file), "content": "bad"})
        assert not response.success

    asyncio.run(exercise())


def test_legacy_file_write_rejects_truncated_baseline(tmp_path: Path) -> None:
    original = "x" * 12001 + "\nprotected tail\n"
    (tmp_path / "identity.md").write_text(original)
    with pytest.raises(ValueError, match="truncated baseline"):
        action_executor.MemberActionExecutorAgent._apply_structured_file_writes(
            action_worktree=tmp_path, action=_action("modify"), declared_paths=["identity.md"],
            file_writes=[{"path": "identity.md", "content": "only saw the first excerpt"}],
        )
    assert (tmp_path / "identity.md").read_text() == original


def test_action_policy_accepts_native_local_search_tools() -> None:
    from openjiuwen.rsi.harness_rsi.member_optimizer.action_groups import validate_action_policy

    action = _action("modify")
    action = replace(action, allowed_tools=["read_file", "grep", "glob", "list_files", "write_file", "edit_file"])
    result = validate_action_policy(action)
    assert result.valid, result.errors


@pytest.mark.parametrize("group,path,content", [
    ("prompt", "prompt_sections/sections.yaml", "sections: ["),
    ("skill", "skills/skills.yaml", "skills: ["),
    ("subagent", "subagents/worker/tool.py", "def broken(:"),
])
def test_action_validation_checks_syntax_on_all_written_surfaces(tmp_path: Path, group: str, path: str, content: str) -> None:
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text(content)
    errors = action_executor._validate_generated_action_resources(
        tmp_path, replace(_action(), action_group=group), [path],
    )
    assert errors
    assert path in errors[0]
