# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
import asyncio

from openjiuwen.dev_tools.tune.optimizer.prompt_search.environment import CallableEnvironment
from openjiuwen.dev_tools.tune.optimizer.prompt_search.models import (
    PromptCandidate,
    PromptTaskCase,
    PromptTaskSpec,
)


def test_callable_environment_runs_all_cases():
    env = CallableEnvironment(lambda sp, ci: f"{sp}:{ci}")
    task = PromptTaskSpec(
        objective="o",
        cases=[PromptTaskCase.from_text("a"), PromptTaskCase.from_text("b", hidden=True)],
    )
    ex = asyncio.run(env.execute(PromptCandidate(prompt="P"), task))

    assert len(ex.case_results) == 2
    assert ex.combined_output() == "P:a"  # visible only
    assert ex.combined_output(hidden=True) == "P:b"
    assert ex.error is None


def test_callable_environment_isolates_case_errors():
    def runner(sp, ci):
        if ci == "boom":
            raise ValueError("bad case")
        return "ok"

    env = CallableEnvironment(runner)
    task = PromptTaskSpec(
        objective="o",
        cases=[PromptTaskCase.from_text("fine"), PromptTaskCase.from_text("boom")],
    )
    ex = asyncio.run(env.execute(PromptCandidate(prompt="P"), task))

    outputs = {r.case_input: (r.output, r.error) for r in ex.case_results}
    assert outputs["fine"] == ("ok", None)
    assert outputs["boom"][0] == ""
    assert "bad case" in outputs["boom"][1]
    # not all cases failed -> execution-level error stays None
    assert ex.error is None


def test_async_runner_supported():
    async def runner(sp, ci):
        return "async-ok"

    env = CallableEnvironment(runner)
    task = PromptTaskSpec(objective="o", cases=[PromptTaskCase.from_text("a")])
    ex = asyncio.run(env.execute(PromptCandidate(prompt="P"), task))
    assert ex.case_results[0].output == "async-ok"
