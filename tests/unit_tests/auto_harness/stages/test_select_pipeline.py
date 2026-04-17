# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the select_pipeline stage."""

from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

from openjiuwen.auto_harness.pipelines import (
    EXTENDED_EVOLVE_PIPELINE,
    META_EVOLVE_PIPELINE,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    OptimizationTask,
)
from openjiuwen.auto_harness.stages.select_pipeline import (
    run_select_pipeline,
)


class TestSelectPipelineStage(
    IsolatedAsyncioTestCase,
):
    async def test_explicit_task_pipeline_wins(self):
        result = await run_select_pipeline(
            AutoHarnessConfig(model=MagicMock()),
            OptimizationTask(
                topic="t1",
                pipeline_name=EXTENDED_EVOLVE_PIPELINE,
            ),
        )
        assert result.pipeline_name == EXTENDED_EVOLVE_PIPELINE

    async def test_no_model_falls_back_to_pr(self):
        result = await run_select_pipeline(
            AutoHarnessConfig(model=None),
            OptimizationTask(topic="t1"),
        )
        assert result.pipeline_name == META_EVOLVE_PIPELINE
