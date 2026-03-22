# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from datetime import datetime, timezone

import pytest

from openjiuwen.dev_tools.agent_builder.utils.enums import ProgressStage, ProgressStatus
from openjiuwen.dev_tools.agent_builder.utils.progress import (
    BuildProgress,
    ProgressReporter,
    ProgressStep,
)


class TestProgressStep:
    @staticmethod
    def test_progress_step_creation():
        step = ProgressStep(
            stage=ProgressStage.INITIALIZING,
            status=ProgressStatus.RUNNING,
            message="Starting build..."
        )
        assert step.stage == ProgressStage.INITIALIZING
        assert step.status == ProgressStatus.RUNNING
        assert step.message == "Starting build..."
        assert step.details == {}
        assert step.duration is None
        assert step.error is None

    @staticmethod
    def test_progress_step_with_details():
        details = {"key": "value"}
        step = ProgressStep(
            stage=ProgressStage.CLARIFYING,
            status=ProgressStatus.SUCCESS,
            message="Completed",
            details=details
        )
        assert step.details == details

    @staticmethod
    def test_progress_step_to_dict():
        step = ProgressStep(
            stage=ProgressStage.INITIALIZING,
            status=ProgressStatus.RUNNING,
            message="Starting",
            details={"count": 1},
            duration=1.5
        )
        result = step.to_dict()
        assert result["stage"] == "initializing"
        assert result["status"] == "running"
        assert result["message"] == "Starting"
        assert result["details"] == {"count": 1}
        assert result["duration"] == 1.5
        assert "timestamp" in result

    @staticmethod
    def test_progress_step_with_error():
        step = ProgressStep(
            stage=ProgressStage.ERROR,
            status=ProgressStatus.FAILED,
            message="Failed",
            error="Test error"
        )
        assert step.error == "Test error"
        result = step.to_dict()
        assert result["error"] == "Test error"


class TestBuildProgress:
    @staticmethod
    def test_build_progress_creation():
        progress = BuildProgress(
            session_id="test_session",
            agent_type="llm_agent",
            current_stage=ProgressStage.INITIALIZING,
            current_status=ProgressStatus.PENDING,
            current_message="Initializing..."
        )
        assert progress.session_id == "test_session"
        assert progress.agent_type == "llm_agent"
        assert progress.current_stage == ProgressStage.INITIALIZING
        assert progress.current_status == ProgressStatus.PENDING
        assert progress.steps == []
        assert progress.overall_progress == 0.0

    @staticmethod
    def test_build_progress_to_dict():
        progress = BuildProgress(
            session_id="test_session",
            agent_type="workflow",
            current_stage=ProgressStage.COMPLETED,
            current_status=ProgressStatus.SUCCESS,
            current_message="Done"
        )
        result = progress.to_dict()
        assert result["session_id"] == "test_session"
        assert result["agent_type"] == "workflow"
        assert result["current_stage"] == "completed"
        assert result["current_status"] == "success"
        assert result["current_message"] == "Done"
        assert "start_time" in result
        assert "last_update_time" in result

    @staticmethod
    def test_build_progress_with_steps():
        step1 = ProgressStep(
            stage=ProgressStage.INITIALIZING,
            status=ProgressStatus.SUCCESS,
            message="Init done"
        )
        step2 = ProgressStep(
            stage=ProgressStage.CLARIFYING,
            status=ProgressStatus.RUNNING,
            message="Clarifying..."
        )
        progress = BuildProgress(
            session_id="test",
            agent_type="llm_agent",
            current_stage=ProgressStage.CLARIFYING,
            current_status=ProgressStatus.RUNNING,
            current_message="Processing",
            steps=[step1, step2]
        )
        result = progress.to_dict()
        assert len(result["steps"]) == 2


class TestProgressReporter:
    @staticmethod
    def test_progress_reporter_creation():
        reporter = ProgressReporter("session_123", "llm_agent")
        assert reporter.session_id == "session_123"
        assert reporter.agent_type == "llm_agent"
        assert reporter.progress.current_stage == ProgressStage.INITIALIZING
        assert reporter.progress.current_status == ProgressStatus.PENDING

    @staticmethod
    def test_add_callback():
        reporter = ProgressReporter("session_123", "llm_agent")
        callback_called = []

        def callback(progress: BuildProgress):
            callback_called.append(progress.session_id)

        reporter.add_callback(callback)
        reporter.start_stage(ProgressStage.CLARIFYING, "Test message")
        
        assert len(callback_called) == 1
        assert callback_called[0] == "session_123"

    @staticmethod
    def test_remove_callback():
        reporter = ProgressReporter("session_123", "llm_agent")
        callback_called = []

        def callback(progress: BuildProgress):
            callback_called.append(1)

        reporter.add_callback(callback)
        reporter.remove_callback(callback)
        reporter.start_stage(ProgressStage.CLARIFYING, "Test message")
        
        assert len(callback_called) == 0

    @staticmethod
    def test_start_stage():
        reporter = ProgressReporter("session_123", "llm_agent")
        reporter.start_stage(
            ProgressStage.CLARIFYING,
            "Clarifying requirements...",
            details={"query_length": 100},
            progress=20.0
        )
        
        assert reporter.progress.current_stage == ProgressStage.CLARIFYING
        assert reporter.progress.current_status == ProgressStatus.RUNNING
        assert reporter.progress.current_message == "Clarifying requirements..."
        assert reporter.progress.overall_progress == 20.0

    @staticmethod
    def test_complete_stage():
        reporter = ProgressReporter("session_123", "llm_agent")
        reporter.start_stage(ProgressStage.CLARIFYING, "Clarifying...")
        reporter.complete_stage("Clarification complete", details={"found": 5})
        
        assert reporter.progress.current_status == ProgressStatus.SUCCESS

    @staticmethod
    def test_fail_stage():
        reporter = ProgressReporter("session_123", "llm_agent")
        reporter.start_stage(ProgressStage.CLARIFYING, "Clarifying...")
        reporter.fail_stage(error="Test error", message="Error occurred")
        
        assert reporter.progress.current_status == ProgressStatus.FAILED
        assert reporter.progress.error == "Test error"

    @staticmethod
    def test_complete():
        reporter = ProgressReporter("session_123", "llm_agent")
        reporter.start_stage(ProgressStage.COMPLETED, "Finishing...")
        reporter.complete("Build completed successfully")
        
        assert reporter.progress.current_stage == ProgressStage.COMPLETED
        assert reporter.progress.current_status == ProgressStatus.SUCCESS
        assert reporter.progress.overall_progress == 100.0

    @staticmethod
    def test_multiple_stages():
        reporter = ProgressReporter("session_123", "llm_agent")
        
        reporter.start_stage(ProgressStage.INITIALIZING, "Init...")
        assert reporter.progress.current_stage == ProgressStage.INITIALIZING
        
        reporter.complete_stage("Init done")
        reporter.start_stage(ProgressStage.CLARIFYING, "Clarifying...")
        assert reporter.progress.current_stage == ProgressStage.CLARIFYING
        
        reporter.complete_stage("Clarify done")
        reporter.start_stage(ProgressStage.GENERATING_CONFIG, "Generating...")
        assert reporter.progress.current_stage == ProgressStage.GENERATING_CONFIG
