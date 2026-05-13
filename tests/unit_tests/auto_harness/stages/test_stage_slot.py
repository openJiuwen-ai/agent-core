# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Stage slot and PipelineStageMap tests."""

from __future__ import annotations

import pytest

from openjiuwen.auto_harness.pipelines.base import (
    PipelineStageMap,
)
from openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.extended_evolve_pipeline import (
    ExtendedEvolvePipeline,
)
from openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.extension_task_pipeline import (
    ExtensionTaskPipeline,
)
from openjiuwen.auto_harness.pipelines.meta_evolve_pipeline.meta_evolve_pipeline import (
    MetaEvolvePipeline,
)
from openjiuwen.auto_harness.pipelines.meta_evolve_pipeline.meta_evolve_task_pipeline import (
    PRTaskPipeline,
)
from openjiuwen.auto_harness.schema import (
    StageSlot,
)
from openjiuwen.auto_harness.stages.assess import (
    AssessStage,
    ExtendAssessStage,
    MetaAssessStage,
)
from openjiuwen.auto_harness.stages.activate import (
    ExtendActivateStage,
)
from openjiuwen.auto_harness.stages.commit import (
    CommitStage,
)
from openjiuwen.auto_harness.stages.implement import (
    ExtendImplementStage,
    ImplementStage,
    MetaImplementStage,
)
from openjiuwen.auto_harness.stages.learnings import (
    LearningsStage,
)
from openjiuwen.auto_harness.stages.plan import (
    ExtendPlanStage,
    MetaPlanStage,
    PlanStage,
)
from openjiuwen.auto_harness.stages.publish_pr import (
    PublishPRStage,
)
from openjiuwen.auto_harness.stages.verify import (
    ExtendVerifyStage,
    MetaVerifyStage,
    VerifyStage,
)


class TestStageSlotValues:
    """Each stage class declares the correct slot."""

    def test_assess_base_slot(self):
        assert AssessStage.slot == "assess"

    def test_meta_assess_slot(self):
        assert MetaAssessStage.slot == "assess"

    def test_extend_assess_slot(self):
        assert ExtendAssessStage.slot == "assess"

    def test_plan_base_slot(self):
        assert PlanStage.slot == "plan"

    def test_meta_plan_slot(self):
        assert MetaPlanStage.slot == "plan"

    def test_extend_plan_slot(self):
        assert ExtendPlanStage.slot == "plan"

    def test_implement_base_slot(self):
        assert ImplementStage.slot == "implement"

    def test_meta_implement_slot(self):
        assert MetaImplementStage.slot == "implement"

    def test_extend_implement_slot(self):
        assert ExtendImplementStage.slot == "implement"

    def test_verify_base_slot(self):
        assert VerifyStage.slot == "verify"

    def test_meta_verify_slot(self):
        assert MetaVerifyStage.slot == "verify"

    def test_extend_verify_slot(self):
        assert ExtendVerifyStage.slot == "verify"

    def test_commit_slot(self):
        assert CommitStage.slot == "commit"

    def test_publish_pr_slot(self):
        assert PublishPRStage.slot == "publish"

    def test_learnings_slot(self):
        assert LearningsStage.slot == "learnings"


class TestStageNameUniqueness:
    """Meta and Extend subclasses have distinct names."""

    def test_assess_names_differ(self):
        assert MetaAssessStage.name == "assess"
        assert ExtendAssessStage.name == "assess_ext"

    def test_plan_names_differ(self):
        assert MetaPlanStage.name == "plan"
        assert ExtendPlanStage.name == "plan_ext"

    def test_implement_names_differ(self):
        assert MetaImplementStage.name == "implement"
        assert ExtendImplementStage.name == "implement_ext"

    def test_verify_names_differ(self):
        assert MetaVerifyStage.name == "verify"
        assert ExtendVerifyStage.name == "verify_ext"


class TestPipelineStageMap:
    """PipelineStageMap.resolve() instantiates correctly."""

    def test_resolve_returns_instance(self):
        stage_map = PipelineStageMap(
            mapping={
                StageSlot.COMMIT: CommitStage,
            }
        )
        stage = stage_map.resolve(StageSlot.COMMIT)
        assert isinstance(stage, CommitStage)

    def test_resolve_unknown_slot_raises(self):
        stage_map = PipelineStageMap(mapping={})
        with pytest.raises(KeyError, match="No stage bound"):
            stage_map.resolve("nonexistent")


class TestPipelineStageMaps:
    """Each pipeline declares a stage_map covering its slots."""

    def test_meta_evolve_pipeline_stage_map(self):
        sm = MetaEvolvePipeline.stage_map
        assert sm.mapping[StageSlot.ASSESS] is MetaAssessStage
        assert sm.mapping[StageSlot.PLAN] is MetaPlanStage
        assert sm.mapping[StageSlot.LEARNINGS] is LearningsStage

    def test_extended_evolve_pipeline_stage_map(self):
        sm = ExtendedEvolvePipeline.stage_map
        assert sm.mapping[StageSlot.ASSESS] is ExtendAssessStage
        assert sm.mapping[StageSlot.PLAN] is ExtendPlanStage

    def test_pr_task_pipeline_stage_map(self):
        sm = PRTaskPipeline.stage_map
        assert sm.mapping[StageSlot.IMPLEMENT] is MetaImplementStage
        assert sm.mapping[StageSlot.VERIFY] is MetaVerifyStage
        assert sm.mapping[StageSlot.COMMIT] is CommitStage
        assert sm.mapping[StageSlot.PUBLISH] is PublishPRStage

    def test_extension_task_pipeline_stage_map(self):
        sm = ExtensionTaskPipeline.stage_map
        assert sm.mapping[StageSlot.IMPLEMENT] is ExtendImplementStage
        assert sm.mapping[StageSlot.VERIFY] is ExtendVerifyStage
        assert sm.mapping[StageSlot.ACTIVATE] is ExtendActivateStage
