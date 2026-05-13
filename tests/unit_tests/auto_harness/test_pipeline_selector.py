# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pipeline selector tests for competitor-driven routing."""

from __future__ import annotations

from openjiuwen.auto_harness.infra.pipeline_selector import (
    choose_session_pipeline,
    detect_pipeline_signal,
)
from openjiuwen.auto_harness.pipelines import (
    EXTENDED_EVOLVE_PIPELINE,
    META_EVOLVE_PIPELINE,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    OptimizationTask,
)


class TestPipelineSelector:
    def test_detects_competitor_signal_from_task_text(self):
        signal = detect_pipeline_signal(
            [
                OptimizationTask(
                    topic="吸收 hermes 的动态能力创建机制"
                )
            ],
            AutoHarnessConfig(),
        )
        assert signal == EXTENDED_EVOLVE_PIPELINE

    def test_detects_competitor_signal_from_config(self):
        signal = detect_pipeline_signal(
            [OptimizationTask(topic="优化 harness")],
            AutoHarnessConfig(
                optimization_goal="cursor"
            ),
        )
        assert signal == EXTENDED_EVOLVE_PIPELINE

    def test_choose_session_pipeline_prefers_signal(self):
        result = choose_session_pipeline(
            tasks=[
                OptimizationTask(
                    topic="吸收 hermes 的主动审查能力"
                )
            ],
            config=AutoHarnessConfig(),
            available_pipelines=[
                META_EVOLVE_PIPELINE,
                EXTENDED_EVOLVE_PIPELINE,
            ],
        )
        assert result.pipeline_name == (
            EXTENDED_EVOLVE_PIPELINE
        )
        assert "signal" in result.reason

    def test_config_meta_preference_wins_over_signal(self):
        result = choose_session_pipeline(
            tasks=[
                OptimizationTask(
                    topic="吸收 hermes",
                )
            ],
            config=AutoHarnessConfig(
                pipeline_preference=META_EVOLVE_PIPELINE,
            ),
            available_pipelines=[
                META_EVOLVE_PIPELINE,
                EXTENDED_EVOLVE_PIPELINE,
            ],
        )
        assert result.pipeline_name == (
            META_EVOLVE_PIPELINE
        )
        assert "config" in result.reason

    def test_config_extended_preference_wins_for_plain_task(self):
        result = choose_session_pipeline(
            tasks=[OptimizationTask(topic="优化 harness")],
            config=AutoHarnessConfig(
                pipeline_preference=EXTENDED_EVOLVE_PIPELINE,
            ),
            available_pipelines=[
                META_EVOLVE_PIPELINE,
                EXTENDED_EVOLVE_PIPELINE,
            ],
        )
        assert result.pipeline_name == (
            EXTENDED_EVOLVE_PIPELINE
        )
        assert "config" in result.reason

    def test_auto_plain_task_defaults_to_meta(self):
        result = choose_session_pipeline(
            tasks=[OptimizationTask(topic="优化 harness")],
            config=AutoHarnessConfig(),
            available_pipelines=[
                META_EVOLVE_PIPELINE,
                EXTENDED_EVOLVE_PIPELINE,
            ],
        )
        assert result.pipeline_name == (
            META_EVOLVE_PIPELINE
        )
