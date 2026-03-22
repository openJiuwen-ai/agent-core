# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for agent_builder utils module.

Tests integration between utility components and their real behavior.
"""
import pytest

from openjiuwen.dev_tools.agent_builder.utils.constants import (
    DEFAULT_MAX_HISTORY_SIZE,
    MAX_QUERY_LENGTH,
    MIN_QUERY_LENGTH,
)
from openjiuwen.dev_tools.agent_builder.utils.enums import AgentType, BuildState, ProgressStage, ProgressStatus
from openjiuwen.dev_tools.agent_builder.utils.progress import BuildProgress, ProgressManager, ProgressReporter
from openjiuwen.dev_tools.agent_builder.utils.utils import (
    extract_json_from_text,
    format_dialog_history,
    safe_json_loads,
    validate_session_id,
)


class TestUtilsIntegration:
    @staticmethod
    def test_extract_and_parse_json_workflow():
        llm_response = '''
        Here is the generated DSL:
        ```json
        {
            "name": "Test Agent",
            "type": "llm_agent",
            "config": {
                "temperature": 0.7,
                "max_tokens": 1000
            }
        }
        ```
        Please review and confirm.
        '''
        
        json_str = extract_json_from_text(llm_response)
        assert json_str is not None
        
        parsed = safe_json_loads(json_str)
        assert parsed is not None
        assert parsed["name"] == "Test Agent"
        assert parsed["config"]["temperature"] == 0.7

    @staticmethod
    def test_dialog_history_formatting_integration():
        history = [
            {"role": "user", "content": "创建一个助手"},
            {"role": "assistant", "content": "好的，请告诉我助手名称"},
            {"role": "user", "content": "叫小助手"},
        ]
        
        formatted = format_dialog_history(history)
        
        assert "user: 创建一个助手" in formatted
        assert "assistant: 好的，请告诉我助手名称" in formatted
        assert "user: 叫小助手" in formatted

    @staticmethod
    def test_session_id_validation_with_real_patterns():
        valid_ids = [
            "session-12345",
            "user_001_session",
            "abc123XYZ",
            "test-session-2024",
        ]
        
        for session_id in valid_ids:
            assert validate_session_id(session_id) is True

    @staticmethod
    def test_constants_enums_consistency():
        assert DEFAULT_MAX_HISTORY_SIZE > 0
        assert MAX_QUERY_LENGTH > MIN_QUERY_LENGTH
        assert len(AgentType) >= 2
        assert len(BuildState) >= 3


class TestProgressIntegration:
    @staticmethod
    def test_progress_reporter_with_callbacks():
        reporter = ProgressReporter(session_id="test_session", agent_type="llm_agent")
        
        events = []
        
        def callback(progress):
            events.append(progress)
        
        reporter.add_callback(callback)
        
        reporter.start_stage(ProgressStage.INITIALIZING, "Starting...")
        reporter.complete_stage("Done")
        
        assert len(events) >= 1

    @staticmethod
    def test_build_progress_serialization():
        progress = BuildProgress(
            session_id="test_session",
            agent_type="llm_agent",
            current_stage=ProgressStage.INITIALIZING,
            current_status=ProgressStatus.PENDING,
            current_message="Initializing"
        )
        
        progress_dict = progress.to_dict()
        
        assert "session_id" in progress_dict
        assert "steps" in progress_dict
        assert progress_dict["session_id"] == "test_session"

    @staticmethod
    def test_progress_manager_integration():
        manager = ProgressManager()
        
        reporter = manager.create_reporter("session_001", "llm_agent")
        
        assert reporter is not None
        assert reporter.session_id == "session_001"
        
        retrieved = manager.get_reporter("session_001")
        assert retrieved is reporter

    @staticmethod
    def test_progress_reporter_complete_workflow():
        reporter = ProgressReporter(session_id="test_session", agent_type="llm_agent")
        
        reporter.start_stage(ProgressStage.INITIALIZING, "Starting...")
        reporter.complete_stage("Initialization done")
        
        reporter.start_stage(ProgressStage.CLARIFYING, "Clarifying requirements...")
        reporter.complete_stage("Clarification done")
        
        reporter.complete("Build completed")
        
        progress = reporter.get_progress()
        assert progress.current_stage == ProgressStage.COMPLETED
        assert progress.overall_progress == 100.0


class TestEnumIntegration:
    @staticmethod
    def test_agent_type_string_conversion():
        llm_type = AgentType.LLM_AGENT
        workflow_type = AgentType.WORKFLOW
        
        assert llm_type.value == "llm_agent"
        assert workflow_type.value == "workflow"

    @staticmethod
    def test_build_state_transitions():
        states = [BuildState.INITIAL, BuildState.PROCESSING, BuildState.COMPLETED]
        
        for state in states:
            assert isinstance(state.value, str)
            assert len(state.value) > 0

    @staticmethod
    def test_progress_stage_values():
        stages = [
            ProgressStage.INITIALIZING,
            ProgressStage.CLARIFYING,
            ProgressStage.COMPLETED
        ]
        
        for stage in stages:
            assert isinstance(stage.value, str)
            assert len(stage.value) > 0

    @staticmethod
    def test_progress_status_values():
        statuses = [
            ProgressStatus.PENDING,
            ProgressStatus.RUNNING,
            ProgressStatus.SUCCESS,
            ProgressStatus.FAILED
        ]
        
        for status in statuses:
            assert isinstance(status.value, str)
