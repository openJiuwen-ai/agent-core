# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Solver budgets are validated and participate in source-result identity."""

from dataclasses import replace

import pytest
import yaml

from openjiuwen.rsi.harness_rsi.config import AutoCoordinatingHarnessConfig, EvaluatorConfig
from openjiuwen.rsi.harness_rsi.single_harness.source_evidence import evaluation_context


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "many"])
def test_invalid_solver_iteration_budget_is_rejected(value):
    with pytest.raises(ValueError, match="solver_max_iterations"):
        AutoCoordinatingHarnessConfig.from_dict({"evaluator": {"solver_max_iterations": value}})


def test_changed_solver_budget_invalidates_cached_evaluation(tmp_path):
    package = tmp_path / "harness"
    package.mkdir()
    (package / "harness_config.yaml").write_text("name: solver\n", encoding="utf-8")
    refs = tmp_path / "refs.yaml"
    refs.write_text(yaml.safe_dump({"harness_refs": {"solver": str(package)}}), encoding="utf-8")
    configured = AutoCoordinatingHarnessConfig.from_dict({"evaluator": {"solver_max_iterations": 240}})
    baseline = evaluation_context(harness_refs_path=str(refs), evaluator_config=EvaluatorConfig(), cases=[])
    changed = evaluation_context(harness_refs_path=str(refs), evaluator_config=configured.evaluator, cases=[])
    assert configured.evaluator.solver_max_iterations == 240
    assert baseline["signature"] != changed["signature"]
    assert replace(configured.evaluator, solver_max_iterations=100) == EvaluatorConfig()
