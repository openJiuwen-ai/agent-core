# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Generated rails must use the host's actual control contract."""

import asyncio
from types import SimpleNamespace

import pytest

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.rsi.harness_rsi.member_optimizer import verification as module
from openjiuwen.rsi.harness_rsi.member_optimizer.verification import (
    _check_rail_runtime_contract,
    _failed_check_relative_path,
    _is_worktree_repairable_check,
)


@pytest.mark.parametrize("body", [
    'return ctx.extra.get("remaining_iterations")',
    'return ctx.extra["answer_text"]',
    'return context.extra.get("task")',
    'ctx.extra["_next_model_tool_choice"] = "required"',
])
def test_phantom_host_fields_are_repairable(tmp_path, body):
    target = "rails/generated.py"
    path = tmp_path / target
    path.parent.mkdir()
    path.write_text(f"def hook(ctx):\n    {body}\n", encoding="utf-8")
    check = _check_rail_runtime_contract("solver", tmp_path, target)
    assert check.status == "failed"
    assert _is_worktree_repairable_check(check.name)
    assert _failed_check_relative_path(check.name) == target


def test_rail_owned_metadata_is_not_mistaken_for_host_field(tmp_path):
    path = tmp_path / "owned.py"
    path.write_text('def hook(ctx):\n    ctx.extra["task"] = {}\n    return ctx.extra.get("task")\n')
    assert _check_rail_runtime_contract("solver", tmp_path, path.name).status == "passed"


def test_real_context_has_no_implicit_budget_or_tool_choice_consumer():
    ctx = AgentCallbackContext(agent=SimpleNamespace())
    assert ctx.extra.get("remaining_iterations") is None
    ctx.extra["_next_model_tool_choice"] = "required"
    assert not ctx.has_force_finish_request


@pytest.mark.parametrize("fixes_contract", [False, True])
def test_existing_verification_and_repair_recheck_rail_contract(tmp_path, monkeypatch, fixes_contract):
    worktrees = tmp_path / "worktrees"
    package = worktrees / "solver/integration"
    rail = package / "rails/generated.py"
    rail.parent.mkdir(parents=True)
    rail.write_text('def hook(ctx):\n    return ctx.extra.get("remaining_iterations")\n')
    (tmp_path / "execution_results.json").write_text("{}")
    monkeypatch.setattr(module, "_validate_role_integration_worktree", lambda *_: [])
    monkeypatch.setattr(module, "_validate_execution_results", lambda *_: [])
    monkeypatch.setattr(module, "_validate_action_results_by_role", lambda *_: {})
    monkeypatch.setattr(module, "resolve_integration_worktree_path", lambda *_: package)
    plan = SimpleNamespace(
        targets=[SimpleNamespace(role="solver")],
        actions=[SimpleNamespace(
            role="solver", action_group="rail", operation="modify", target_path="rails/generated.py",
        )],
    )
    calls = []

    async def repair_role(**kwargs):
        calls.append(kwargs)
        if fixes_contract:
            rail.write_text('def hook(ctx):\n    return ctx.inputs.response\n')
        return {"repairs": []}

    verifier = module.HarnessChangeVerifier(repair_agent=SimpleNamespace(repair_role=repair_role))
    result = asyncio.run(verifier.verify(plan, worktrees, str(tmp_path / "verification.json")))
    assert result.status == "failed"
    assert result.repairable
    fixed = asyncio.run(verifier.repair(result, worktrees, str(tmp_path / "fix.json"), "unused", stage_retry_limit=1))
    assert len(calls) == 1
    assert calls[0]["failed_checks"][0]["name"].startswith("rail_runtime_contract:")
    assert (fixed.final_verification_status == "passed") is fixes_contract


def test_supported_rail_control_uses_real_context_and_is_bounded(tmp_path):
    source = '''from openjiuwen.core.single_agent.rail.base import AgentRail
class FinishRail(AgentRail):
    async def before_invoke(self, ctx):
        self.calls = 0
        self.finished = False
    async def after_model_call(self, ctx):
        self.calls += 1
        response = ctx.inputs.response
        if self.calls >= 2 and not self.finished and response.content and not response.tool_calls:
            self.finished = True
            ctx.request_force_finish({"output": response.content, "result_type": "answer"})
'''
    path = tmp_path / "finish.py"
    path.write_text(source, encoding="utf-8")
    assert _check_rail_runtime_contract("solver", tmp_path, path.name).status == "passed"
    namespace = {}
    exec(compile(source, str(path), "exec"), namespace)  # noqa: S102 - fixed test fixture, not model output

    async def simulate():
        rail = namespace["FinishRail"]()
        ctx = AgentCallbackContext(agent=SimpleNamespace())
        await rail.before_invoke(ctx)
        for content, tools in (("", ["work"]), ("Existing answer", [])):
            ctx.inputs = ModelCallInputs(response=SimpleNamespace(content=content, tool_calls=tools))
            await rail.after_model_call(ctx)
            if tools:
                assert not ctx.has_force_finish_request
        assert ctx.consume_force_finish().result["output"] == "Existing answer"
        await rail.after_model_call(ctx)
        assert not ctx.has_force_finish_request
        await rail.before_invoke(ctx)
        await rail.after_model_call(ctx)
        assert not ctx.has_force_finish_request

    asyncio.run(simulate())
