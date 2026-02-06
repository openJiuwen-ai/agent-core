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
    BaseLogEvent,
    LLMEvent,
    LogEventType,
    ModuleType,
    ToolEvent,
    WorkflowEvent,
    create_log_event,
    get_event_class,
    register_event_class,
    sanitize_event_for_logging,
    unregister_event_class,
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


# ==================== Dynamic Event Registration Tests ====================


def test_register_custom_event_class():
    """Test registering a custom event class with string key"""
    from dataclasses import dataclass

    @dataclass
    class CustomEvent(BaseLogEvent):
        """Custom event type for testing"""

        custom_field: str = "default"

        def __post_init__(self):
            super().__post_init__()
            self.module_type = ModuleType.SYSTEM

    # Use a string key for custom event type (not in the LogEventType enum)
    custom_event_type = "custom_test_event"

    # Register the custom event class
    register_event_class(custom_event_type, CustomEvent)

    # Verify we can create an event with this type using string
    event = create_log_event(custom_event_type, custom_field="test_value", module_id="test_123")

    assert isinstance(event, CustomEvent)
    assert event.custom_field == "test_value"
    assert event.module_id == "test_123"

    # Cleanup
    unregister_event_class(custom_event_type)


def test_register_with_custom_string_identifier():
    """Test that dynamic registration works with custom string identifiers"""
    from dataclasses import dataclass

    @dataclass
    class CustomAgentEvent(AgentEvent):
        """Custom agent event that adds a field"""

        custom_agent_field: str = "default"

        def __post_init__(self):
            super().__post_init__()

    # Register custom class with unique string identifier
    custom_type = "my_custom_agent_event"
    register_event_class(custom_type, CustomAgentEvent)

    # Create event with custom type - should use custom class
    event = create_log_event(custom_type, custom_agent_field="custom_value", module_id="agent_123")

    assert isinstance(event, CustomAgentEvent)
    assert event.custom_agent_field == "custom_value"
    assert event.module_id == "agent_123"

    # Standard enum types should still use static mapping
    event2 = create_log_event(LogEventType.AGENT_START, module_id="agent_456")
    assert isinstance(event2, AgentEvent)
    assert not isinstance(event2, CustomAgentEvent)

    # Cleanup
    unregister_event_class(custom_type)


def test_unregister_event_class():
    """Test unregistering a custom event class"""
    from dataclasses import dataclass

    @dataclass
    class TempEvent(BaseLogEvent):
        pass

    # Use a string key for custom event type
    temp_type = "temp_event_type"

    # Register and verify
    register_event_class(temp_type, TempEvent)
    assert get_event_class(temp_type) == TempEvent

    # Unregister
    result = unregister_event_class(temp_type)
    assert result is True

    # Should return BaseLogEvent now (no static mapping for string keys)
    assert get_event_class(temp_type) == BaseLogEvent

    # Unregister again should return False
    result = unregister_event_class(temp_type)
    assert result is False


def test_get_event_class_priority():
    """Test that get_event_class follows correct priority"""
    from dataclasses import dataclass

    @dataclass
    class CustomLLMEvent(BaseLogEvent):
        pass

    # Test 1: Static mapping for LogEventType enums
    assert get_event_class(LogEventType.LLM_CALL_START) == LLMEvent

    # Test 2: String key with no registration returns BaseLogEvent
    unregistered_str = "unregistered_custom_type"
    assert get_event_class(unregistered_str) == BaseLogEvent

    # Test 3: Register with string and verify it overrides default
    registered_str = "registered_custom_llm_type"
    register_event_class(registered_str, CustomLLMEvent)
    assert get_event_class(registered_str) == CustomLLMEvent

    # Test 4: Unregister and restore default
    unregister_event_class(registered_str)
    assert get_event_class(registered_str) == BaseLogEvent

    # Test 5: Static mapping is always used for LogEventType enums
    assert get_event_class(LogEventType.LLM_CALL_START) == LLMEvent


def test_register_invalid_class_raises_error():
    """Test that registering a non-BaseLogEvent class raises TypeError"""

    class NotAnEvent:
        """Not a BaseLogEvent subclass"""

        pass

    # Use string key
    custom_type = "invalid_event_type"

    with pytest.raises(TypeError) as exc_info:
        register_event_class(custom_type, NotAnEvent)  # type: ignore

    assert "must be a subclass of BaseLogEvent" in str(exc_info.value)


def test_cannot_register_enum_conflicting_string():
    """Test that string values conflicting with enum values are rejected"""
    from dataclasses import dataclass

    @dataclass
    class CustomEvent(BaseLogEvent):
        pass

    # Try to register using a string that matches an existing enum value
    with pytest.raises(ValueError) as exc_info:
        register_event_class("agent_start", CustomEvent)  # This matches LogEventType.AGENT_START.value

    assert "conflicts with predefined enum value" in str(exc_info.value)


def test_static_event_class_map_unchanged():
    """Test that static EVENT_CLASS_MAP is not modified by dynamic registration"""
    from dataclasses import dataclass

    from openjiuwen.core.common.logging.events import EVENT_CLASS_MAP

    # Get original values
    original_agent_class = EVENT_CLASS_MAP.get(LogEventType.AGENT_START)
    original_size = len(EVENT_CLASS_MAP)

    @dataclass
    class CustomEvent(BaseLogEvent):
        pass

    # Register using a string key
    new_type = "new_dynamic_event_type"
    register_event_class(new_type, CustomEvent)

    # EVENT_CLASS_MAP should be unchanged
    assert EVENT_CLASS_MAP.get(LogEventType.AGENT_START) == original_agent_class
    assert len(EVENT_CLASS_MAP) == original_size
    # The new type should NOT be in EVENT_CLASS_MAP
    assert new_type not in EVENT_CLASS_MAP

    # Cleanup
    unregister_event_class(new_type)


def test_custom_event_with_create_log_event():
    """Test creating custom events through create_log_event with various kwargs"""
    from dataclasses import dataclass

    @dataclass
    class DetailedEvent(BaseLogEvent):
        detail_field: str = ""
        detail_count: int = 0

        def __post_init__(self):
            super().__post_init__()
            self.module_type = ModuleType.SYSTEM

    # Use string key
    detailed_type = "detailed_event_type"
    register_event_class(detailed_type, DetailedEvent)

    # Create with multiple fields
    event = create_log_event(
        detailed_type,
        module_id="detail_123",
        detail_field="important_detail",
        detail_count=42,
        message="Detailed event message",
    )

    assert isinstance(event, DetailedEvent)
    assert event.detail_field == "important_detail"
    assert event.detail_count == 42
    assert event.module_id == "detail_123"
    assert event.message == "Detailed event message"

    # Cleanup
    unregister_event_class(detailed_type)


def test_custom_event_serialization():
    """Test that custom events can be properly serialized"""
    from dataclasses import dataclass

    @dataclass
    class SerializableEvent(BaseLogEvent):
        extra_data: str = ""

        def __post_init__(self):
            super().__post_init__()
            self.module_type = ModuleType.SYSTEM

    custom_type = "serializable_event"
    register_event_class(custom_type, SerializableEvent)

    event = create_log_event(custom_type, extra_data="test_data", module_id="ser_123")

    # Test serialization
    event_dict = event.to_dict()
    assert event_dict["extra_data"] == "test_data"
    assert event_dict["module_id"] == "ser_123"

    # Test JSON serialization
    json_str = json.dumps(event_dict, ensure_ascii=False)
    parsed = json.loads(json_str)
    assert parsed["extra_data"] == "test_data"

    # Cleanup
    unregister_event_class(custom_type)


def test_custom_event_validation():
    """Test validation of custom events"""
    from dataclasses import dataclass

    @dataclass
    class ValidatedEvent(BaseLogEvent):
        pass

    custom_type = "validated_event"
    register_event_class(custom_type, ValidatedEvent)

    event = create_log_event(custom_type, module_id="valid_123")

    # Should be valid
    assert validate_event(event) is True

    # Cleanup
    unregister_event_class(custom_type)
