# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Default promotion follows the continuous controller and preserves its resume guard."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from openjiuwen.rsi.harness_rsi.config import AutoCoordinatingHarnessConfig, PromotionConfig
from openjiuwen.rsi.harness_rsi.single_harness import (
    IterativeSingleHarnessRequest,
    SingleHarnessIterativeOptimizationOrchestrator,
)


@pytest.mark.asyncio
async def test_default_run_freezes_baseline_and_resumes_only_with_same_policy(tmp_path: Path) -> None:
    class Evaluator:
        calls = 0

        async def evaluate_batch(self, **kwargs):
            self.calls += 1
            directory = Path(kwargs["output_dir"])
            directory.mkdir(parents=True, exist_ok=True)
            result = directory / "result.json"
            result.write_text(json.dumps({
                "case_id": "one", "status": "passed", "execution_status": "passed",
                "score": 1.0, "evaluation": {"method": "script-based"},
            }), encoding="utf-8")
            trace = directory / "trace.json"
            trace.write_text("{}", encoding="utf-8")
            ref = directory / "eval_ref.yaml"
            ref.write_text(yaml.safe_dump({
                "harness_refs_path": kwargs["harness_refs_path"],
                "cases": [{"case_id": "one", "status": "passed", "score": 1.0,
                           "result_path": str(result), "trace_path": str(trace)}],
            }), encoding="utf-8")
            return str(ref)

    package = tmp_path / "harness"
    package.mkdir()
    (package / "harness.yaml").write_text("name: solver\n", encoding="utf-8")
    refs = tmp_path / "refs.yaml"
    refs.write_text(yaml.safe_dump({"harness_refs": {"solver": str(package)}}), encoding="utf-8")
    dataset = tmp_path / "cases.json"
    dataset.write_text(json.dumps({"cases": [{"case_id": "one", "input": "solve"}]}), encoding="utf-8")
    evaluator = Evaluator()
    config = AutoCoordinatingHarnessConfig(max_epochs=1)
    controller = SingleHarnessIterativeOptimizationOrchestrator(
        config, evaluator=evaluator, analyzer=object(), member_optimizer=object(),
    )
    request = IterativeSingleHarnessRequest(
        dataset_files=[str(dataset)], harness_refs_path=str(refs), output_dir=str(tmp_path / "run"),
    )
    result = await controller.run(request)
    state = yaml.safe_load(Path(result.state_path).read_text(encoding="utf-8"))
    assert state["promotion_policy"]["policy_version"] == "continuous_two_stage_v2"
    assert state["stop_reason"] == "target_threshold_saturated"
    assert result.best_score == 1.0
    assert evaluator.calls == 1
    await controller.run(replace(request, resume=True))
    assert evaluator.calls == 1

    legacy = SingleHarnessIterativeOptimizationOrchestrator(
        replace(config, promotion=PromotionConfig(policy_version="legacy_epoch_v1")),
        evaluator=evaluator, analyzer=object(), member_optimizer=object(),
    )
    with pytest.raises(ValueError, match="promotion policy changed"):
        await legacy.run(replace(request, resume=True))
