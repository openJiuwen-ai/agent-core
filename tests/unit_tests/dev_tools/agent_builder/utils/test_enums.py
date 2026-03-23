# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import pytest

from openjiuwen.dev_tools.agent_builder.utils.enums import (
    AgentType,
    AgentTypeLiteral,
    BuildState,
    ProgressStage,
    ProgressStatus,
)


class TestAgentType:
    @staticmethod
    def test_agent_type_values():
        assert AgentType.LLM_AGENT.value == "llm_agent"
        assert AgentType.WORKFLOW.value == "workflow"

    @staticmethod
    def test_agent_type_is_string_enum():
        assert isinstance(AgentType.LLM_AGENT, str)
        assert isinstance(AgentType.WORKFLOW, str)

    @staticmethod
    def test_agent_type_from_string():
        assert AgentType("llm_agent") == AgentType.LLM_AGENT
        assert AgentType("workflow") == AgentType.WORKFLOW

    @staticmethod
    def test_agent_type_invalid_value():
        with pytest.raises(ValueError):
            AgentType("invalid_type")


class TestBuildState:
    @staticmethod
    def test_build_state_values():
        assert BuildState.INITIAL.value == "initial"
        assert BuildState.PROCESSING.value == "processing"
        assert BuildState.COMPLETED.value == "completed"

    @staticmethod
    def test_build_state_is_string_enum():
        assert isinstance(BuildState.INITIAL, str)
        assert isinstance(BuildState.PROCESSING, str)
        assert isinstance(BuildState.COMPLETED, str)

    @staticmethod
    def test_build_state_from_string():
        assert BuildState("initial") == BuildState.INITIAL
        assert BuildState("processing") == BuildState.PROCESSING
        assert BuildState("completed") == BuildState.COMPLETED


class TestProgressStage:
    @staticmethod
    def test_progress_stage_values():
        assert ProgressStage.INITIALIZING.value == "initializing"
        assert ProgressStage.CLARIFYING.value == "clarifying"
        assert ProgressStage.RESOURCE_RETRIEVING.value == "resource_retrieving"
        assert ProgressStage.COMPLETED.value == "completed"
        assert ProgressStage.ERROR.value == "error"
        assert ProgressStage.OPTIMIZING.value == "optimizing"
        assert ProgressStage.GENERATING_CONFIG.value == "generating_config"
        assert ProgressStage.TRANSFORMING_DSL.value == "transforming_dsl"
        assert ProgressStage.DETECTING_INTENTION.value == "detecting_intention"
        assert ProgressStage.GENERATING_WORKFLOW_DESIGN.value == "generating_workflow_design"
        assert ProgressStage.GENERATING_DL.value == "generating_dl"
        assert ProgressStage.VALIDATING_DL.value == "validating_dl"
        assert ProgressStage.REFINING_DL.value == "refining_dl"
        assert ProgressStage.TRANSFORMING_MERMAID.value == "transforming_mermaid"
        assert ProgressStage.TRANSFORMING_WORKFLOW_DSL.value == "transforming_workflow_dsl"

    @staticmethod
    def test_progress_stage_is_string_enum():
        assert isinstance(ProgressStage.INITIALIZING, str)
        assert isinstance(ProgressStage.COMPLETED, str)


class TestProgressStatus:
    @staticmethod
    def test_progress_status_values():
        assert ProgressStatus.PENDING.value == "pending"
        assert ProgressStatus.RUNNING.value == "running"
        assert ProgressStatus.SUCCESS.value == "success"
        assert ProgressStatus.FAILED.value == "failed"
        assert ProgressStatus.WARNING.value == "warning"

    @staticmethod
    def test_progress_status_is_string_enum():
        assert isinstance(ProgressStatus.PENDING, str)
        assert isinstance(ProgressStatus.SUCCESS, str)


class TestAgentTypeLiteral:
    @staticmethod
    def test_agent_type_literal_values():
        valid_values: list[AgentTypeLiteral] = ["llm_agent", "workflow"]
        assert "llm_agent" in valid_values
        assert "workflow" in valid_values
