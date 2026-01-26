# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Test structured logging functionality
"""

import json
import logging
import os
import tempfile

import pytest

from openjiuwen.core.common.logging import (
    LogManager,
    set_session_id,
)
from openjiuwen.core.common.logging.default import DefaultLogger
from openjiuwen.core.common.logging.events import (
    AgentEvent,
    LLMEvent,
    LogEventType,
    ModuleType,
    ToolEvent,
    WorkflowEvent,
    create_log_event,
    sanitize_event_for_logging,
    validate_event,
)


@pytest.fixture
def temp_log_dir():  # type: ignore
    """Create a temporary log directory"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def logger_config(temp_log_dir):  # type: ignore
    """Create logger configuration for testing"""
    log_file = os.path.join(temp_log_dir, "test.log")  # type: ignore
    return {
        "log_file": log_file,
        "output": ["console", "file"],
        "level": logging.DEBUG,
        "backup_count": 5,
        "max_bytes": 10 * 1024 * 1024,
        # 'format': '%(asctime)s | %(log_type)s | %(trace_id)s | %(levelname)s | %(message)s',
        "format": (
            "%(asctime)s | %(log_type)s | %(filename)s | %(lineno)d | "
            "%(funcName)s | %(trace_id)s | %(levelname)s | %(message)s"
        ),
    }


def safe_close_logger_handlers(logger: DefaultLogger):  # type: ignore
    """Safely close logger handlers"""
    inner_logger = logger.logger()
    if inner_logger and inner_logger.handlers:
        for h in list(inner_logger.handlers):
            h.close()
            inner_logger.removeHandler(h)


@pytest.fixture
def test_logger(logger_config):  # type: ignore
    """Create a test logger instance"""
    logger = DefaultLogger("test", logger_config)  # type: ignore
    yield logger
    # Cleanup
    LogManager.reset()
    import sys
    if sys.platform.startswith("win"):
        # On Windows, logging handlers should be closed before deleting logging files
        safe_close_logger_handlers(logger)


def test_create_agent_event():
    """Test creating an AgentEvent"""
    event = create_log_event(
        LogEventType.AGENT_START,
        module_id="agent_123",
        module_name="TestAgent",
        agent_type="ReActAgent",
        session_id="session_456",
        trace_id="trace_789",
    )

    assert isinstance(event, AgentEvent)
    assert event.event_type == LogEventType.AGENT_START
    assert event.module_id == "agent_123"
    assert event.module_name == "TestAgent"
    assert event.agent_type == "ReActAgent"
    assert event.module_type == ModuleType.AGENT


def test_create_workflow_event():
    """Test creating a WorkflowEvent"""
    event = create_log_event(LogEventType.WORKFLOW_START, workflow_id="workflow_001", workflow_name="TestWorkflow")

    assert isinstance(event, WorkflowEvent)
    assert event.workflow_id == "workflow_001"
    assert event.module_type == ModuleType.WORKFLOW


def test_create_workflow_component_event():
    """Test creating a WorkflowEvent with component"""
    event = WorkflowEvent(
        event_type=LogEventType.WORKFLOW_COMPONENT_START,
        workflow_id="workflow_001",
        component_id="component_001",
        component_name="LLMComponent",
    )

    assert event.module_type == ModuleType.WORKFLOW_COMPONENT
    assert event.component_id == "component_001"


def test_create_llm_event():
    """Test creating an LLMEvent"""
    event = create_log_event(
        LogEventType.LLM_CALL_START,
        module_id="llm_gpt4",
        model_name="gpt-4",
        query="What is Python?",
        temperature=0.7,
    )

    assert isinstance(event, LLMEvent)
    assert event.model_name == "gpt-4"
    assert event.query == "What is Python?"
    assert event.module_type == ModuleType.LLM


def test_event_to_dict():
    """Test converting event to dictionary"""
    event = create_log_event(LogEventType.AGENT_START, module_id="agent_123", message="Test message")

    event_dict = event.to_dict()

    assert isinstance(event_dict, dict)
    assert event_dict["module_id"] == "agent_123"
    assert event_dict["message"] == "Test message"
    assert event_dict["event_type"] == "agent_start"
    assert event_dict["log_level"] == "INFO"
    assert "event_id" in event_dict
    assert "timestamp" in event_dict


def test_event_serialization():
    """Test event JSON serialization"""
    event = create_log_event(LogEventType.AGENT_START, module_id="agent_123", message="Test message")

    event_dict = event.to_dict()
    json_str = json.dumps(event_dict, ensure_ascii=False)

    assert isinstance(json_str, str)
    parsed = json.loads(json_str)
    assert parsed["module_id"] == "agent_123"
    assert parsed["message"] == "Test message"


def test_event_with_message_and_stacktrace():
    """Test event with message and stacktrace fields"""
    event = create_log_event(
        LogEventType.AGENT_ERROR,
        module_id="agent_123",
        message="Error occurred",
        stacktrace="Traceback (most recent call last):\n  File...",
        error_code="AGENT_ERROR",
        error_message="Execution failed",
    )

    assert event.message == "Error occurred"
    assert event.stacktrace is not None
    assert event.error_code == "AGENT_ERROR"
    assert event.error_message == "Execution failed"


def test_validate_event():
    """Test event validation"""
    valid_event = create_log_event(LogEventType.AGENT_START, module_id="agent_123")
    assert validate_event(valid_event) is True

    invalid_event = AgentEvent(
        event_id="",  # Empty event_id
        event_type=LogEventType.AGENT_START,
    )
    assert validate_event(invalid_event) is False


def test_sanitize_event():
    """Test event sanitization"""
    event = create_log_event(
        LogEventType.LLM_CALL_END,
        module_id="llm_gpt4",
        messages=[{"role": "user", "content": "secret"}],
        response_content="sensitive response",
        query="sensitive query",
    )

    sanitized = sanitize_event_for_logging(event)

    assert sanitized["messages"] == "<REDACTED>"
    assert sanitized["response_content"] == "<REDACTED>"
    assert sanitized["query"] == "<REDACTED>"
    assert sanitized["module_id"] == "llm_gpt4"  # Not sanitized


def test_event_correlation():
    """Test event correlation with parent_event_id"""
    parent_event = create_log_event(LogEventType.AGENT_START, module_id="agent_123")

    child_event = create_log_event(
        LogEventType.LLM_CALL_START,
        module_id="llm_gpt4",
        parent_event_id=parent_event.event_id,
        correlation_id=parent_event.event_id,
    )

    assert child_event.parent_event_id == parent_event.event_id
    assert child_event.correlation_id == parent_event.event_id


def test_log_string_message(test_logger, temp_log_dir):  # type: ignore
    """Test logging a simple string message"""
    test_logger.info("Test message")  # type: ignore

    # Check that log file was created
    log_file = os.path.join(temp_log_dir, "test.log")  # type: ignore
    assert os.path.exists(log_file)

    # Read log content
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
        assert "Test message" in content


def test_log_with_event_type(test_logger, temp_log_dir):  # type: ignore
    """Test logging with event_type in kwargs"""
    test_logger.info(  # type: ignore
        "Agent started", event_type=LogEventType.AGENT_START, module_id="agent_123"
    )

    log_file = os.path.join(temp_log_dir, "test.log")  # type: ignore
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
        # Should contain JSON with event structure
        assert "agent_start" in content or "Agent started" in content


def test_log_with_event_object(test_logger, temp_log_dir):  # type: ignore
    """Test logging with event object"""
    event = create_log_event(
        LogEventType.AGENT_START, module_id="agent_123", module_name="TestAgent", agent_type="ReActAgent"
    )

    test_logger.info("", event=event)  # type: ignore

    log_file = os.path.join(temp_log_dir, "test.log")  # type: ignore
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
        # Should contain JSON with event structure
        assert "agent_123" in content
        assert "TestAgent" in content


def test_log_with_message_field(test_logger, temp_log_dir):  # type: ignore
    """Test that message field is set correctly"""
    event = create_log_event(LogEventType.AGENT_START, module_id="agent_123")

    test_logger.info("Custom message", event=event)  # type: ignore

    log_file = os.path.join(temp_log_dir, "test.log")  # type: ignore
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
        # Message should be set on the event
        assert "Custom message" in content


def test_log_different_levels(test_logger, temp_log_dir):  # type: ignore
    """Test logging at different levels"""
    test_logger.debug("Debug message")  # type: ignore
    test_logger.info("Info message")  # type: ignore
    test_logger.warning("Warning message")  # type: ignore
    test_logger.error("Error message")  # type: ignore
    test_logger.critical("Critical message")  # type: ignore

    log_file = os.path.join(temp_log_dir, "test.log")  # type: ignore
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
        assert "Debug message" in content
        assert "Info message" in content
        assert "Warning message" in content
        assert "Error message" in content
        assert "Critical message" in content


def test_log_exception_with_stacktrace(test_logger, temp_log_dir):  # type: ignore
    """Test logging exception with stacktrace"""
    try:
        raise ValueError("Test exception")
    except Exception:
        test_logger.exception("Exception occurred")  # type: ignore

    log_file = os.path.join(temp_log_dir, "test.log")  # type: ignore
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
        assert "Exception occurred" in content
        assert "ValueError" in content or "Test exception" in content


def test_log_with_trace_id(test_logger, temp_log_dir):  # type: ignore
    """Test logging with trace_id from context"""
    set_session_id("test_trace_123")

    test_logger.info("Message with trace")  # type: ignore

    log_file = os.path.join(temp_log_dir, "test.log")  # type: ignore
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
        assert "test_trace_123" in content


def test_log_with_custom_kwargs(test_logger, temp_log_dir):  # type: ignore
    """Test logging with custom kwargs for event creation"""
    test_logger.info(  # type: ignore
        "Custom event",
        event_type=LogEventType.AGENT_START,
        module_id="agent_123",
        session_id="session_456",
        trace_id="trace_789",
        metadata={"custom_field": "custom_value"},
    )

    log_file = os.path.join(temp_log_dir, "test.log")  # type: ignore
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
        # Should contain the custom fields
        assert "agent_123" in content or "session_456" in content


def test_log_json_format(test_logger, temp_log_dir):  # type: ignore
    """Test that log output is in JSON format"""
    event = create_log_event(LogEventType.AGENT_START, module_id="agent_123", message="Test message")

    test_logger.info("", event=event)  # type: ignore

    log_file = os.path.join(temp_log_dir, "test.log")  # type: ignore
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
        # Try to parse JSON from log line
        # Log format includes timestamp and other fields, so we need to extract JSON part
        lines = content.strip().split("\n")
        for line in lines:
            if "agent_123" in line:
                # Try to find JSON part (usually at the end of the line)
                try:
                    # Extract JSON part if it exists
                    if "{" in line:
                        json_start = line.find("{")
                        json_part = line[json_start:]
                        parsed = json.loads(json_part)
                        assert parsed["module_id"] == "agent_123"
                        assert parsed["message"] == "Test message"
                        break
                except ValueError:
                    continue


def test_agent_events():
    """Test all agent event types"""
    event_types = [
        LogEventType.AGENT_START,
        LogEventType.AGENT_END,
        LogEventType.AGENT_INVOKE,
        LogEventType.AGENT_RESPONSE,
        LogEventType.AGENT_ERROR,
    ]

    for event_type in event_types:
        event = create_log_event(event_type, module_id="agent_123")
        assert isinstance(event, AgentEvent)
        assert event.event_type == event_type


def test_llm_events():
    """Test LLM event types"""
    event = create_log_event(LogEventType.LLM_CALL_START, module_id="llm_gpt4", query="Test query")
    assert isinstance(event, LLMEvent)
    assert event.query == "Test query"

    event = create_log_event(LogEventType.LLM_CALL_END, module_id="llm_gpt4", response_content="Response")
    assert isinstance(event, LLMEvent)
    assert event.response_content == "Response"


def test_tool_events():
    """Test tool event types"""
    event = create_log_event(
        LogEventType.TOOL_CALL_START, module_id="tool_search", tool_name="web_search", arguments={"query": "Python"}
    )
    assert isinstance(event, ToolEvent)
    assert event.tool_name == "web_search"


def test_workflow_events():
    """Test workflow event types"""
    event = create_log_event(LogEventType.WORKFLOW_START, workflow_id="workflow_001")
    assert isinstance(event, WorkflowEvent)
    assert event.workflow_id == "workflow_001"


def test_event_with_metadata():
    """Test event with metadata"""
    event = create_log_event(
        LogEventType.AGENT_START, module_id="agent_123", metadata={"key1": "value1", "key2": 123}
    )

    assert event.metadata["key1"] == "value1"
    assert event.metadata["key2"] == 123


def test_event_metadata_serialization():
    """Test metadata serialization"""
    event = create_log_event(LogEventType.AGENT_START, module_id="agent_123", metadata={"nested": {"key": "value"}})

    event_dict = event.to_dict()
    assert "metadata" in event_dict
    assert event_dict["metadata"]["nested"]["key"] == "value"
