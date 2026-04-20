# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pipeline registry tests for auto-harness."""

from __future__ import annotations

from openjiuwen.auto_harness.pipelines.base import (
    BasePipeline,
)
from openjiuwen.auto_harness.pipelines import (
    EXTENDED_EVOLVE_PIPELINE,
    META_EVOLVE_PIPELINE,
)
from openjiuwen.auto_harness.registry import (
    build_pipeline_registry,
    build_stage_registry,
)
from openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.extended_evolve_pipeline import (
    ExtendedEvolvePipeline,
)
from openjiuwen.auto_harness.pipelines.meta_evolve_pipeline.meta_evolve_pipeline import (
    MetaEvolvePipeline,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    PipelineSpec,
    StageResult,
    StageSpec,
)
from openjiuwen.auto_harness.stages.assess import (
    AssessStage,
)
from openjiuwen.auto_harness.stages.base import (
    SessionStage,
)
from openjiuwen.auto_harness.stages.commit import (
    CommitStage,
)
from openjiuwen.auto_harness.stages.implement import (
    ImplementStage,
)
from openjiuwen.auto_harness.stages.learnings import (
    LearningsStage,
)
from openjiuwen.auto_harness.stages.plan import (
    PlanStage,
)
from openjiuwen.auto_harness.stages.publish_pr import (
    PublishPRStage,
)
from openjiuwen.auto_harness.stages.verify import (
    VerifyStage,
)


class _DummyStage(SessionStage):
    name = "custom_stage"
    produces = ["custom_artifact"]

    async def stream(self, ctx):
        del ctx
        yield StageResult()


class _DummyPipeline(BasePipeline):
    name = "custom_pipeline"
    expected_outputs = ["custom_artifact"]

    async def stream(self, ctx):
        yield ctx.message("ok")


def register_test_stage(registry):
    registry.register(
        StageSpec(
            name="custom_stage",
            stage_cls=_DummyStage,
            produces=["custom_artifact"],
        )
    )


def register_test_pipeline(registry, stage_registry):
    assert stage_registry.require("custom_stage")
    registry.register(
        PipelineSpec(
            name="custom_pipeline",
            pipeline_cls=_DummyPipeline,
            expected_outputs=["custom_artifact"],
        )
    )


class TestPipelineBuilders:
    def test_builtin_meta_pipeline_uses_pipeline_class(self):
        cfg = AutoHarnessConfig()
        stage_registry = build_stage_registry(cfg)
        pipeline_registry = build_pipeline_registry(
            cfg,
            stage_registry=stage_registry,
        )
        spec = pipeline_registry.require(META_EVOLVE_PIPELINE)
        assert spec.pipeline_cls is MetaEvolvePipeline
        assert spec.expected_outputs == ["session_results"]

    def test_extended_pipeline_uses_pipeline_class(self):
        cfg = AutoHarnessConfig()
        stage_registry = build_stage_registry(cfg)
        pipeline_registry = build_pipeline_registry(
            cfg,
            stage_registry=stage_registry,
        )
        spec = pipeline_registry.require(
            EXTENDED_EVOLVE_PIPELINE
        )
        assert spec.pipeline_cls is ExtendedEvolvePipeline

    def test_builtin_stage_registry_uses_stage_classes(self):
        cfg = AutoHarnessConfig()
        registry = build_stage_registry(cfg)
        assert registry.require("assess").stage_cls is AssessStage
        assert registry.require("plan").stage_cls is PlanStage
        assert registry.require("implement").stage_cls is ImplementStage
        assert registry.require("verify").stage_cls is VerifyStage
        assert registry.require("commit").stage_cls is CommitStage
        assert registry.require("publish_pr").stage_cls is PublishPRStage
        assert registry.require("learnings").stage_cls is LearningsStage

    def test_build_stage_registry_loads_registrars(self):
        cfg = AutoHarnessConfig(
            stage_registrars=[
                (
                    "tests.unit_tests.auto_harness.test_pipeline:"
                    "register_test_stage"
                )
            ]
        )
        registry = build_stage_registry(cfg)
        assert registry.require("custom_stage").stage_cls is _DummyStage

    def test_build_pipeline_registry_loads_registrars(
        self,
    ):
        cfg = AutoHarnessConfig(
            stage_registrars=[
                (
                    "tests.unit_tests.auto_harness.test_pipeline:"
                    "register_test_stage"
                )
            ],
            pipeline_registrars=[
                (
                    "tests.unit_tests.auto_harness.test_pipeline:"
                    "register_test_pipeline"
                )
            ],
        )
        stage_registry = build_stage_registry(cfg)
        pipeline_registry = build_pipeline_registry(
            cfg,
            stage_registry=stage_registry,
        )
        assert (
            pipeline_registry.require("custom_pipeline").pipeline_cls
            is _DummyPipeline
        )
