# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Swarmflow token budget: ledger, engine gates, and the harness-stopping rail.

The budget is only worth anything if it binds *inside* an agent's loop, so these
cover both halves: the engine refusing to start another ``agent()`` once the
ledger is dry, and :class:`SwarmflowBudgetRail` billing real usage off the model
response and force-finishing the round that crosses the ceiling.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.rails.team_context import get_swarmflow_budget, inject_team_handles
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.build_context import BuildContext
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_factory import create_team_tools
from openjiuwen.agent_teams.workflow.backends.budget_rail import SwarmflowBudgetRail
from openjiuwen.agent_teams.workflow.engine import run_workflow
from openjiuwen.agent_teams.workflow.engine.backends.base import AgentBackend, AgentResult
from openjiuwen.agent_teams.workflow.engine.budget import BudgetLedger
from openjiuwen.agent_teams.workflow.engine.errors import BudgetExhausted
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UsageMetadata
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs

_SCRIPT = '''
from swarmflow import agent

META = {"name": "burn", "description": "burn tokens", "phases": []}

async def run(args):
    out = []
    for i in range(5):
        out.append(await agent(f"task {i}", label=f"a{i}"))
    return out
'''

_POLLING_SCRIPT = '''
from swarmflow import agent, budget

META = {"name": "poll", "description": "wind down on remaining()", "phases": []}

async def run(args):
    done = 0
    while budget.remaining() > 30:
        await agent("task", label="a")
        done += 1
    return {"done": done, "spent": budget.spent(), "remaining": budget.remaining()}
'''


def _write(tmp_path, src: str) -> str:
    path = tmp_path / "flow.py"
    path.write_text(src, encoding="utf-8")
    return str(path)


class _FixedCostBackend(AgentBackend):
    """Bills a fixed number of tokens per call, like a real backend's rail would."""

    def __init__(self, cost: int) -> None:
        super().__init__()
        self._cost = cost
        self.calls = 0

    async def run(self, prompt: str, opts: dict, schema_json: dict | None) -> AgentResult:
        self.calls += 1
        self.budget.add(self._cost)
        return AgentResult(text=f"ran {opts.get('label')}", tokens=self._cost)


# ---------------------------------------------------------------- ledger


def test_ledger_counts_and_reports_exhaustion():
    """spent/remaining/exhausted track the ceiling; remaining clamps at 0."""
    ledger = BudgetLedger(total=100)
    assert (ledger.spent, ledger.remaining(), ledger.exhausted) == (0, 100, False)

    ledger.add(60)
    assert (ledger.spent, ledger.remaining(), ledger.exhausted) == (60, 40, False)

    # Overshoot: the call that crosses the line is only billed once it returns.
    ledger.add(60)
    assert ledger.spent == 120
    assert ledger.remaining() == 0  # clamped, never negative
    assert ledger.exhausted


def test_ledger_without_total_is_unbounded_but_still_counts():
    """No ceiling: never exhausted, remaining is None, spent still accrues."""
    ledger = BudgetLedger()
    ledger.add(10_000)
    assert ledger.spent == 10_000
    assert ledger.remaining() is None
    assert not ledger.exhausted


def test_ledger_ignores_non_positive_reports():
    """A provider reporting 0 (or nothing) must not move the ledger."""
    ledger = BudgetLedger(total=10)
    ledger.add(0)
    ledger.add(-5)
    assert ledger.spent == 0


# ---------------------------------------------------------------- engine gate


def test_agent_gate_stops_the_run_once_the_ledger_is_dry(tmp_path):
    """agent() raises BudgetExhausted rather than starting call 4 of 5.

    30 per call against a ceiling of 100: calls 1-3 fit (90), the 4th finds the
    ledger short of the line but *not* under it... so it runs and lands on 120,
    and the 5th is refused.
    """
    backend = _FixedCostBackend(cost=30)
    with pytest.raises(BudgetExhausted):
        asyncio.run(
            run_workflow(
                _write(tmp_path, _SCRIPT),
                backend=backend,
                budget=BudgetLedger(total=100),
            )
        )
    assert backend.calls == 4  # the 5th never started


def test_run_without_a_budget_is_unbounded(tmp_path):
    """No ledger: every call runs and the script completes."""
    backend = _FixedCostBackend(cost=10_000)
    result = asyncio.run(run_workflow(_write(tmp_path, _SCRIPT), backend=backend))
    assert backend.calls == 5
    assert len(result) == 5


def test_script_can_wind_down_on_remaining_before_the_gate_fires(tmp_path):
    """A script polling remaining() finishes normally — the gate is only a backstop."""
    backend = _FixedCostBackend(cost=20)
    result = asyncio.run(
        run_workflow(
            _write(tmp_path, _POLLING_SCRIPT),
            backend=backend,
            budget=BudgetLedger(total=100),
        )
    )
    # Stops once remaining() <= 30, i.e. after 4 calls (80 spent, 20 left).
    assert result == {"done": 4, "spent": 80, "remaining": 20}


def test_backend_is_bound_to_the_run_ledger(tmp_path):
    """run_workflow hands the backend the ledger it will be judged against."""
    backend = _FixedCostBackend(cost=5)
    ledger = BudgetLedger(total=1_000)
    asyncio.run(run_workflow(_write(tmp_path, _SCRIPT), backend=backend, budget=ledger))
    assert backend.budget is ledger
    assert ledger.spent == 25  # 5 calls x 5, billed by the backend


def test_mock_backend_bills_the_ledger(tmp_path):
    """The default offline backend feeds budget.* too, so tests can drive it."""
    ledger = BudgetLedger(total=1_000_000)
    asyncio.run(run_workflow(_write(tmp_path, _SCRIPT), budget=ledger))
    assert ledger.spent > 0


# ---------------------------------------------------------------- rail


@dataclass
class _FakeCtx:
    """Minimal AgentCallbackContext stand-in recording force-finish requests."""

    inputs: Any = None
    finish: dict | None = None

    def request_force_finish(self, result: dict) -> None:
        self.finish = result


def _response(total: int = 0, *, inputs: int = 0, outputs: int = 0) -> AssistantMessage:
    return AssistantMessage(
        content="hi",
        usage_metadata=UsageMetadata(
            input_tokens=inputs,
            output_tokens=outputs,
            total_tokens=total,
        ),
    )


def _ctx(response: Any) -> Any:
    return _FakeCtx(inputs=ModelCallInputs(response=response))


def test_rail_bills_real_usage_from_the_model_response():
    """Tokens come off usage_metadata — the provider's own count."""
    ledger = BudgetLedger(total=1_000)
    rail = SwarmflowBudgetRail(ledger)

    asyncio.run(rail.after_model_call(_ctx(_response(total=120))))

    assert ledger.spent == 120
    assert rail.call_tokens == 120


def test_rail_falls_back_to_the_input_output_split():
    """total_tokens absent: sum the split rather than report nothing."""
    ledger = BudgetLedger(total=1_000)
    rail = SwarmflowBudgetRail(ledger)

    asyncio.run(rail.after_model_call(_ctx(_response(inputs=70, outputs=30))))

    assert ledger.spent == 100


def test_rail_reports_nothing_when_the_provider_reports_no_usage():
    """No usage_metadata: bill 0 rather than guess from prompt length."""
    ledger = BudgetLedger(total=1_000)
    rail = SwarmflowBudgetRail(ledger)

    asyncio.run(rail.after_model_call(_ctx(AssistantMessage(content="hi"))))
    asyncio.run(rail.after_model_call(_ctx(None)))

    assert ledger.spent == 0
    assert rail.call_tokens == 0


def test_rail_force_finishes_the_round_that_crosses_the_ceiling():
    """The agent that empties the pot is stopped where it stands."""
    ledger = BudgetLedger(total=100)
    rail = SwarmflowBudgetRail(ledger)

    ctx = _ctx(_response(total=40))
    asyncio.run(rail.after_model_call(ctx))
    assert ctx.finish is None  # 40/100 — keep going

    ctx = _ctx(_response(total=70))
    asyncio.run(rail.after_model_call(ctx))
    assert ctx.finish is not None  # 110/100 — stop
    assert "budget exhausted" in ctx.finish["reason"]


def test_rail_refuses_a_call_the_run_cannot_pay_for():
    """before_model_call stops an agent drained by a *sibling* mid-loop."""
    ledger = BudgetLedger(total=100)
    rail = SwarmflowBudgetRail(ledger)

    ctx = _ctx(None)
    asyncio.run(rail.before_model_call(ctx))
    assert ctx.finish is None

    ledger.add(100)  # another worker burns the shared budget
    ctx = _ctx(None)
    asyncio.run(rail.before_model_call(ctx))
    assert ctx.finish is not None


def test_rail_never_stops_an_unbounded_run():
    """No ceiling configured: bill, but never force-finish."""
    ledger = BudgetLedger()
    rail = SwarmflowBudgetRail(ledger)

    ctx = _ctx(_response(total=10_000_000))
    asyncio.run(rail.after_model_call(ctx))

    assert ledger.spent == 10_000_000
    assert ctx.finish is None


def test_rails_sharing_a_ledger_see_each_others_spend():
    """Concurrent workers draw down one pool, not a ceiling each."""
    ledger = BudgetLedger(total=100)
    a = SwarmflowBudgetRail(ledger)
    b = SwarmflowBudgetRail(ledger)

    asyncio.run(a.after_model_call(_ctx(_response(total=60))))
    ctx = _ctx(_response(total=60))
    asyncio.run(b.after_model_call(ctx))

    assert ledger.spent == 120
    assert a.call_tokens == 60 and b.call_tokens == 60  # attribution stays per-agent
    assert ctx.finish is not None


# ---------------------------------------------------------------- spec wiring


def _spec(budget: int | None) -> TeamAgentSpec:
    return TeamAgentSpec(
        team_name="t",
        enable_swarmflow=True,
        swarmflow_budget=budget,
        agents={"leader": DeepAgentSpec()},
    )


@pytest.mark.parametrize("budget", [0, -1])
def test_spec_rejects_a_ceiling_with_no_headroom(budget):
    """A non-positive budget would fail every run identically — catch it early."""
    with pytest.raises(BaseError, match="swarmflow_budget must be >= 1"):
        _spec(budget)._validate_swarmflow_budget()


@pytest.mark.parametrize("budget", [1, 12_000, None])
def test_spec_accepts_a_positive_or_absent_ceiling(budget):
    """A real ceiling, or None for unbounded."""
    _spec(budget)._validate_swarmflow_budget()  # does not raise


def test_ledger_reaches_the_swarmflow_tool_through_the_handle_chain():
    """swarmflow_budget travels extras -> create_team_tools -> the tool itself.

    The chain is what makes the ceiling configurable at all; a break anywhere in
    it leaves the tool silently unbounded, which no other test would catch.
    """
    ledger = BudgetLedger(total=12_000)

    context = BuildContext()
    inject_team_handles(context.extras, swarmflow_budget=ledger)
    assert get_swarmflow_budget(context) is ledger

    tools = create_team_tools(
        role="leader",
        agent_team=TeamBackend(
            team_name="t",
            member_name="team_leader",
            is_leader=True,
            db=AsyncMock(),
            messager=AsyncMock(spec=Messager),
        ),
        swarmflow_model_resolver=lambda name: None,
        swarmflow_budget=ledger,
    )

    swarmflow = next(t for t in tools if t.card.name == "swarmflow")
    assert swarmflow._budget is ledger
