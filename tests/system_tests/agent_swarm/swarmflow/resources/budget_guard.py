# coding: utf-8
"""Swarmflow script whose termination condition is the token budget.

The loop bound (``_MAX_ROUNDS``) is deliberately far higher than the budget can
pay for, so finishing at the bound would mean the budget did nothing. What ends
this run is ``budget.remaining()`` — fed by the tokens each worker's model
client actually reported.

The wind-down is priced from real usage: each round measures what it cost and
the loop stops while there is still room for a round as expensive as the
priciest one so far. That only works because the numbers are real — against a
length-based estimate the reserve would be off by orders of magnitude and the
script would sail past its ceiling into the engine's hard gate.

Each round asks for genuinely multi-step work so the worker spends several model
calls on it: that puts the ceiling *inside* an agent's loop, where only the
backend's rail can enforce it, rather than tidily between ``agent()`` calls.

Every observation goes through ``log()`` so the driving E2E can verify what the
script itself saw (progress events carry the text).
"""

from swarmflow import agent, budget, log, phase

META = {
    "name": "budget-guard",
    "description": "Keep working until the token budget runs out",
    "phases": [{"title": "预算内工作"}],
}

# Far more rounds than any sane budget pays for: reaching this means the budget
# failed to bind, and the E2E fails on it.
_MAX_ROUNDS = 30


async def run(args):
    phase("预算内工作")
    topic = args or "一个通用话题"
    log(f"budget total={budget.total} spent={budget.spent()} remaining={budget.remaining()}")

    if budget.total is None:
        # Unbounded: the loop bound would be the only thing left to stop this —
        # exactly the state this script exists to prove we are no longer in.
        log("no token budget configured — nothing would stop this run")
        return {"rounds": 0, "spent": budget.spent(), "total": None, "stopped_by": "no-budget"}

    rounds = 0
    worst_round = 0  # priciest round so far, measured — not guessed
    stopped_by = "budget"
    while rounds < _MAX_ROUNDS:
        remaining = budget.remaining()
        if rounds > 0 and remaining < worst_round:
            log(
                f"budget spent after {rounds} rounds "
                f"(remaining={remaining} < worst_round={worst_round}) — stopping"
            )
            break
        before = budget.spent()
        await agent(
            f"围绕「{topic}」写出第 {rounds + 1} 个要点:先想清楚要点是什么,"
            f"再用两三句话把它讲透。",
            label="point",
        )
        cost = budget.spent() - before
        worst_round = max(worst_round, cost)
        rounds += 1
        log(
            f"round={rounds} cost={cost} spent={budget.spent()} "
            f"remaining={budget.remaining()}"
        )
    else:
        stopped_by = "max-rounds"
        log(f"hit the {_MAX_ROUNDS}-round bound — the budget never bound this run")

    return {
        "rounds": rounds,
        "spent": budget.spent(),
        "total": budget.total,
        "stopped_by": stopped_by,
    }
