# coding: utf-8
"""Swarmflow budget E2E — a token ceiling that really stops the agents.

Drives the same public path as the main swarmflow E2E (the leader's
``swarmflow`` tool via ``Runner.run_agent_team_streaming``), but the team spec
sets ``swarmflow_budget`` and the script (``resources/budget_guard.py``) has no
natural stopping point — it would run 30 rounds. The budget is what ends it.

What this proves that a unit test cannot:

* the tokens are **real** — they come off each worker's model client response
  (``AssistantMessage.usage_metadata``), through the whole live stack, not from
  an estimate;
* the ceiling is **hard** — it binds inside a worker's own agent loop, not just
  between ``agent()`` calls;
* the ceiling is **wired** — ``TeamAgentSpec.swarmflow_budget`` reaches the
  backend through the real configurator / rail / tool chain.

Self-verifying: exits non-zero if the run finished for any reason other than the
budget, or if the budget was never actually spent.

Run directly (needs a real model endpoint, see config_llm_local.yaml):
    python tests/system_tests/agent_swarm/swarmflow/agent_team_swarmflow_budget_e2e.py
"""

from __future__ import annotations

import asyncio
import re
import sys
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))  # sibling base module
sys.path.insert(0, str(_HERE.parent))  # _e2e_utils (shared, stays in agent_swarm/)

# Importing the main swarmflow E2E as a harness also applies its module-level
# setup (logging, openjiuwen home, env defaults) — same as the concurrent E2E.
import agent_team_swarmflow_e2e as base  # noqa: E402

from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec  # noqa: E402
from openjiuwen.core.runner.runner import Runner  # noqa: E402

from _e2e_utils import load_team_config  # noqa: E402
from tests.test_logger import logger as test_logger  # noqa: E402

_TEAM_CONFIG_PATH = _HERE / "config_swarmflow_budget.yaml"
_SCRIPT_REL = "../resources/budget_guard.py"

# Mirrors budget_guard.py's loop bound: finishing there means the budget never
# bound the run.
_MAX_ROUNDS = 30

_ROUND_LOG_RE = re.compile(r"round=(\d+) cost=(\d+) spent=(\d+) remaining=(\d+)")
_STOP_LOG_RE = re.compile(r"budget spent after (\d+) rounds")


def _parse_rounds(probe: base._SwarmflowProbe) -> tuple[int, int]:
    """Return ``(rounds, spent)`` as the script itself last reported them.

    Read off the script's own ``log()`` lines rather than the returned dict: the
    result reaches the leader as free-form narration, while the log events are
    structured and arrive on the monitor stream we already drain.
    """
    rounds = 0
    spent = 0
    for line in probe.logs:
        m = _ROUND_LOG_RE.search(line)
        if m:
            rounds = int(m.group(1))
            spent = int(m.group(3))
    return rounds, spent


def _verify(probe: base._SwarmflowProbe, budget_total: int) -> None:
    """Assert the budget — and only the budget — ended the run."""
    assert probe.started, "workflow never emitted workflow_started"
    assert probe.workflow_name == "budget-guard", (
        f"unexpected workflow name: {probe.workflow_name!r}"
    )
    assert not probe.failed, "workflow reported a failure (the script should wind down cleanly)"
    assert probe.completed, "workflow did not complete (failed or timed out)"

    rounds, spent = _parse_rounds(probe)
    workers = probe.agent_completed.get("point", 0)

    # --- the tokens are real ---
    assert spent > 0, "no tokens were ever billed to the budget — is the rail wired?"
    # Measured: a round costs ~400-1000 tokens against a flash model. The
    # length estimate this replaced would have scored the same round at ~50
    # (a ~50-char prompt and ~150-char answer, each over 4). 200 sits clear of
    # both, so this fails loudly if the accounting ever regresses to a guess.
    per_round = spent / max(rounds, 1)
    assert per_round > 200, (
        f"per-round spend {per_round:.0f} is too small to be real model usage "
        f"(estimate-shaped?): spent={spent} rounds={rounds}"
    )

    # --- the budget, not the loop bound, ended it ---
    assert rounds < _MAX_ROUNDS, (
        f"the script ran its full {_MAX_ROUNDS}-round bound — the budget never bound it"
    )
    assert any(_STOP_LOG_RE.search(line) for line in probe.logs), (
        "the script never reported stopping on its budget"
    )
    # It drew the budget down rather than stopping early for some other reason.
    assert spent >= budget_total * 0.5, (
        f"only {spent}/{budget_total} was spent — the run stopped short of its budget"
    )

    # --- the ceiling held ---
    # Not `spent <= budget_total`: the call that crosses the line is billed when
    # it returns, so a bounded overshoot is by design. What matters is that spend
    # stayed near the ceiling instead of running away — unbounded, these workers
    # would have burned roughly _MAX_ROUNDS x per_round.
    assert spent < budget_total * 2, (
        f"spend {spent} ran far past the ceiling {budget_total} — the workers were not stopped"
    )

    # Sanity: the workers the engine reported match the rounds the script counted.
    assert workers == rounds, (
        f"agent_completed(point)={workers} does not match the script's {rounds} rounds"
    )

    test_logger.info(
        "[budget] verified: rounds=%d workers=%d spent=%d/%d (%.0f tokens/round)",
        rounds,
        workers,
        spent,
        budget_total,
        per_round,
    )


async def main() -> int:
    base._wire_model_env()
    # Reuse the main E2E's gitignored scratch dir (short relative script paths,
    # runtime scaffolding kept out of the test tree).
    base._WORKDIR.mkdir(parents=True, exist_ok=True)
    import os

    os.chdir(base._WORKDIR)

    cfg = load_team_config(_TEAM_CONFIG_PATH)
    cfg.pop("runtime", {})
    spec = TeamAgentSpec.model_validate(cfg)
    budget_total = spec.swarmflow_budget
    assert budget_total, "config_swarmflow_budget.yaml must set swarmflow_budget"

    # Fresh session id: swarmflow resumes from a journal keyed by
    # (team, session, workflow name), and a replayed run bills no tokens at all —
    # which would make this test pass while proving nothing.
    session_id = f"swarmflow_budget_e2e_{uuid.uuid4().hex[:8]}"

    query = (
        "请立即调用 swarmflow 工具来运行一个多 agent 工作流。"
        f"参数:script_path 必须填 \"{_SCRIPT_REL}\",args 填 \"分布式系统的设计权衡\"。"
        "只需调用这一个工具,然后等待工作流结果即可,不要自己拆解或执行其它步骤。"
    )

    await Runner.start()
    test_logger.info("=" * 60)
    test_logger.info("Swarmflow budget E2E — ceiling=%d tokens", budget_total)
    test_logger.info("=" * 60)

    probe = await base._run_team(spec, query, session_id)

    if base._TEARDOWN:
        await Runner.stop()
    else:
        test_logger.info("[budget] teardown skipped (SWARMFLOW_E2E_TEARDOWN=0)")

    try:
        _verify(probe, budget_total)
    except AssertionError as e:
        test_logger.error("[budget] VERIFICATION FAILED: %s", e)
        return 1
    test_logger.info("[budget] E2E PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
