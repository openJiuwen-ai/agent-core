# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Structured Log Event Definitions

This module defines all structured log event types and field definitions for the project.
These events are used to record detailed information about various activities in the system, including:
- Agent activities and interactions
- Workflow execution and status
- LLM calls and responses
- Tool calls and execution
- Memory operations
- Session management
- Performance metrics
"""

import uuid
from dataclasses import (
    asdict,
    dataclass,
    field,
    fields,
)
from datetime import (
    datetime,
    timezone,
)
from enum import Enum
from typing import (
    Any,
    Dict,
    List,
    Optional,
)


class LogEventType(Enum):
    """Log event type enumeration"""

    # Agent related events
    AGENT_START = "agent_start"  # Agent started
    AGENT_END = "agent_end"  # Agent ended
    AGENT_INVOKE = "agent_invoke"  # Agent invoked
    AGENT_RESPONSE = "agent_response"  # Agent response
    AGENT_ERROR = "agent_error"  # Agent error

    # Workflow related events
    WORKFLOW_START = "workflow_start"  # Workflow started
    WORKFLOW_END = "workflow_end"  # Workflow ended
    WORKFLOW_COMPONENT_START = "workflow_component_start"  # Workflow component started
    WORKFLOW_COMPONENT_END = "workflow_component_end"  # Workflow component ended
    WORKFLOW_COMPONENT_ERROR = "workflow_component_error"  # Workflow component error
    WORKFLOW_BRANCH = "workflow_branch"  # Workflow branch selected

    # LLM related events
    LLM_CALL_START = "llm_call_start"  # LLM call started
    LLM_CALL_END = "llm_call_end"  # LLM call ended
    LLM_CALL_ERROR = "llm_call_error"  # LLM call error
    LLM_STREAM_CHUNK = "llm_stream_chunk"  # LLM stream chunk

    # Tool related events
    TOOL_CALL_START = "tool_call_start"  # Tool call started
    TOOL_CALL_END = "tool_call_end"  # Tool call ended
    TOOL_CALL_ERROR = "tool_call_error"  # Tool call error

    # Memory related events
    MEMORY_STORE = "memory_store"  # Memory stored
    MEMORY_RETRIEVE = "memory_retrieve"  # Memory retrieved
    MEMORY_DELETE = "memory_delete"  # Memory deleted
    MEMORY_UPDATE = "memory_update"  # Memory updated

    # Session related events
    SESSION_CREATE = "session_create"  # Session created
    SESSION_UPDATE = "session_update"  # Session updated
    SESSION_DELETE = "session_delete"  # Session deleted

    # Context related events
    CONTEXT_ADD_MESSAGE = "context_add_message"  # Context message added
    CONTEXT_CLEAR = "context_clear"  # Context cleared
    CONTEXT_RETRIEVE = "context_retrieve"  # Context retrieved

    # Retrieval related events
    RETRIEVAL_START = "retrieval_start"  # Retrieval started
    RETRIEVAL_END = "retrieval_end"  # Retrieval ended
    RETRIEVAL_ERROR = "retrieval_error"  # Retrieval error

    # Performance related events
    PERFORMANCE_METRIC = "performance_metric"  # Performance metric

    # User interaction events
    USER_INPUT = "user_input"  # User input
    USER_FEEDBACK = "user_feedback"  # User feedback

    # System events
    SYSTEM_START = "system_start"  # System started
    SYSTEM_SHUTDOWN = "system_shutdown"  # System shutdown
    SYSTEM_ERROR = "system_error"  # System error


class LogLevel(Enum):
    """Log level enumeration"""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ModuleType(Enum):
    """Module type enumeration"""

    AGENT = "agent"
    WORKFLOW = "workflow"
    WORKFLOW_COMPONENT = "workflow_component"
    LLM = "llm"
    TOOL = "tool"
    MEMORY = "memory"
    SESSION = "session"
    CONTEXT = "context"
    RETRIEVAL = "retrieval"
    SYSTEM = "system"
    USER = "user"


class EventStatus(Enum):
    """Event status enumeration"""

    SUCCESS = "success"
    FAILURE = "failure"
    PENDING = "pending"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class BaseLogEvent:
    """Base log event class, base class for all event types"""

    # Basic event information
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: LogEventType = LogEventType.SYSTEM_START
    log_level: LogLevel = LogLevel.INFO
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Module information
    module_type: ModuleType = ModuleType.SYSTEM
    module_id: Optional[str] = None  # Module ID, e.g., Agent ID, Workflow ID, Tool Name, etc.
    module_name: Optional[str] = None  # Module name

    # Context information
    session_id: Optional[str] = None  # Session ID
    conversation_id: Optional[str] = None  # Conversation ID
    trace_id: Optional[str] = None  # Trace ID
    correlation_id: Optional[str] = None  # Correlation ID for associating related events
    parent_event_id: Optional[str] = None  # Parent event ID for building event tree

    # Status and result
    status: EventStatus = EventStatus.SUCCESS
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    # Message and stack trace
    message: Optional[str] = None  # Log message content
    stacktrace: Optional[str] = None  # Stack trace information (for exceptions)
    exception: Optional[str] = None  # Exception detail string

    # Extended fields
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Post-processing to ensure metadata is not None"""
        # The metadata field already has a default value in dataclass, but keep the check for safety
        if self.metadata is None:  # type: ignore
            self.metadata = {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format for serialization"""
        result = asdict(self)

        # Handle enum types
        for key, value in result.items():
            if isinstance(value, Enum):
                result[key] = value.value
            elif isinstance(value, datetime):
                result[key] = value.isoformat()
            elif isinstance(value, dict):
                result[key] = self._convert_dict(value)  # type: ignore[arg-type]

        return result

    @staticmethod
    def _convert_dict(d: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively convert enums and datetime in dictionary"""
        result: Dict[str, Any] = {}
        for k, v in d.items():
            if isinstance(v, Enum):
                result[k] = v.value
            elif isinstance(v, datetime):
                result[k] = v.isoformat()
            elif isinstance(v, dict):
                result[k] = BaseLogEvent._convert_dict(v)  # type: ignore[arg-type]
            elif isinstance(v, list):
                converted_list: List[Any] = []
                for item in v:  # type: ignore[misc]
                    if isinstance(item, dict):
                        converted_list.append(BaseLogEvent._convert_dict(item))  # type: ignore[arg-type]
                    elif isinstance(item, Enum):
                        converted_list.append(item.value)
                    elif isinstance(item, datetime):
                        converted_list.append(item.isoformat())
                    else:
                        converted_list.append(item)
                result[k] = converted_list
            else:
                result[k] = v
        return result


@dataclass
class AgentEvent(BaseLogEvent):
    """Agent related event"""

    agent_type: Optional[str] = None  # Agent type, e.g., ReActAgent, ChatAgent
    agent_config: Optional[Dict[str, Any]] = None  # Agent configuration
    input_data: Optional[Dict[str, Any]] = None  # Input data
    output_data: Optional[Dict[str, Any]] = None  # Output data
    iteration_count: Optional[int] = None  # Iteration count (for ReAct)
    max_iterations: Optional[int] = None  # Maximum iterations
    execution_time_ms: Optional[float] = None  # Execution time (milliseconds)

    def __post_init__(self):
        super().__post_init__()
        self.module_type = ModuleType.AGENT


@dataclass
class WorkflowEvent(BaseLogEvent):
    """Workflow related event"""

    workflow_id: Optional[str] = None  # Workflow ID
    workflow_name: Optional[str] = None  # Workflow name
    component_id: Optional[str] = None  # Component ID (if component event, uses base class module_id field)
    component_name: Optional[str] = None  # Component name (if component event, uses base class module_name field)
    component_type_str: Optional[str] = (
        None  # Component type string (for recording specific component types, e.g., LLMComponent, ToolComponent)
    )
    branch_condition: Optional[str] = None  # Branch condition (for branch events)
    selected_branch: Optional[str] = None  # Selected branch
    input_data: Optional[Dict[str, Any]] = None
    output_data: Optional[Dict[str, Any]] = None
    execution_time_ms: Optional[float] = None

    def __post_init__(self):
        super().__post_init__()
        if self.component_id:
            # If component ID is set, this is a component-level event
            self.module_type = ModuleType.WORKFLOW_COMPONENT
        else:
            # If no component ID, this is a workflow-level event
            self.module_type = ModuleType.WORKFLOW


@dataclass
class LLMEvent(BaseLogEvent):
    """LLM call related event"""

    model_name: Optional[str] = None  # Model name
    model_provider: Optional[str] = None  # Model provider
    query: Optional[str] = None  # Query content (may need sanitization)
    messages: Optional[List[Dict[str, Any]]] = None  # Message list (may need sanitization)
    tools: Optional[List[Dict[str, Any]]] = None  # Tool list
    temperature: Optional[float] = None  # Temperature parameter
    max_tokens: Optional[int] = None  # Maximum token count
    top_p: Optional[float] = None  # top_p parameter
    response_content: Optional[str] = None  # Response content (may need sanitization)
    tool_calls: Optional[List[Dict[str, Any]]] = None  # Tool call list
    usage: Optional[Dict[str, Any]] = None  # Token usage
    latency_ms: Optional[float] = None  # Latency (milliseconds)
    is_stream: bool = False  # Whether it's a streaming call
    chunk_index: Optional[int] = None  # Chunk index (for streaming calls)
    extra_params: Dict[str, Any] = None # extra LLM parameters

    def __post_init__(self):
        super().__post_init__()
        self.module_type = ModuleType.LLM


@dataclass
class ToolEvent(BaseLogEvent):
    """Tool call related event"""

    tool_name: Optional[str] = None  # Tool name
    tool_type: Optional[str] = None  # Tool type
    tool_description: Optional[str] = None  # Tool description
    arguments: Optional[Dict[str, Any]] = None  # Call arguments
    result: Optional[Any] = None  # Execution result (may need sanitization)
    execution_time_ms: Optional[float] = None  # Execution time (milliseconds)
    tool_call_id: Optional[str] = None  # Tool call ID

    def __post_init__(self):
        super().__post_init__()
        self.module_type = ModuleType.TOOL


@dataclass
class MemoryEvent(BaseLogEvent):
    """Memory operation related event"""

    memory_type: Optional[str] = None  # Memory type, e.g., short_term, long_term
    operation: Optional[str] = None  # Operation type, e.g., store, retrieve, delete, update
    memory_id: Optional[str] = None  # Memory ID
    query: Optional[str] = None  # Query content (for retrieval)
    memory_count: Optional[int] = None  # Memory count
    retrieved_memories: Optional[List[Dict[str, Any]]] = None  # Retrieved memories
    storage_size_bytes: Optional[int] = None  # Storage size (bytes)

    def __post_init__(self):
        super().__post_init__()
        self.module_type = ModuleType.MEMORY


@dataclass
class SessionEvent(BaseLogEvent):
    """Session management related event"""

    session_type: Optional[str] = None  # Session type
    user_id: Optional[str] = None  # User ID
    agent_id: Optional[str] = None  # Agent ID
    workflow_id: Optional[str] = None  # Workflow ID
    session_config: Optional[Dict[str, Any]] = None  # Session configuration
    message_count: Optional[int] = None  # Message count

    def __post_init__(self):
        super().__post_init__()
        self.module_type = ModuleType.SESSION


@dataclass
class ContextEvent(BaseLogEvent):
    """Context operation related event"""

    message_type: Optional[str] = None  # Message type, e.g., user, assistant, tool, system
    message_content: Optional[str] = None  # Message content (may need sanitization)
    message_role: Optional[str] = None  # Message role
    context_size: Optional[int] = None  # Context size (token count or message count)
    max_context_size: Optional[int] = None  # Maximum context size

    def __post_init__(self):
        super().__post_init__()
        self.module_type = ModuleType.CONTEXT


@dataclass
class RetrievalEvent(BaseLogEvent):
    """Retrieval related event"""

    retrieval_type: Optional[str] = None  # Retrieval type, e.g., vector, keyword, hybrid
    query: Optional[str] = None  # Query content
    top_k: Optional[int] = None  # Return top k results
    retrieved_docs: Optional[List[Dict[str, Any]]] = None  # Retrieved documents
    retrieval_score: Optional[float] = None  # Retrieval score
    latency_ms: Optional[float] = None  # Retrieval latency (milliseconds)
    knowledge_base_id: Optional[str] = None  # Knowledge base ID

    def __post_init__(self):
        super().__post_init__()
        self.module_type = ModuleType.RETRIEVAL


@dataclass
class PerformanceEvent(BaseLogEvent):
    """Performance metric related event"""

    metric_name: Optional[str] = None  # Metric name
    metric_value: Optional[float] = None  # Metric value
    metric_unit: Optional[str] = None  # Metric unit, e.g., ms, bytes, count
    resource_type: Optional[str] = None  # Resource type, e.g., cpu, memory, network
    operation: Optional[str] = None  # Operation name

    def __post_init__(self):
        super().__post_init__()
        self.module_type = ModuleType.SYSTEM


@dataclass
class UserInteractionEvent(BaseLogEvent):
    """User interaction related event"""

    user_id: Optional[str] = None  # User ID
    input_content: Optional[str] = None  # Input content (may need sanitization)
    feedback_type: Optional[str] = None  # Feedback type, e.g., positive, negative, neutral
    feedback_content: Optional[str] = None  # Feedback content

    def __post_init__(self):
        super().__post_init__()
        self.module_type = ModuleType.USER


@dataclass
class SystemEvent(BaseLogEvent):
    """System-level event"""

    system_version: Optional[str] = None  # System version
    system_config: Optional[Dict[str, Any]] = None  # System configuration
    resource_usage: Optional[Dict[str, Any]] = None  # Resource usage

    def __post_init__(self):
        super().__post_init__()
        self.module_type = ModuleType.SYSTEM


# Event type mapping for creating corresponding event classes based on event type
EVENT_CLASS_MAP: Dict[LogEventType, type] = {
    # Agent events
    LogEventType.AGENT_START: AgentEvent,
    LogEventType.AGENT_END: AgentEvent,
    LogEventType.AGENT_INVOKE: AgentEvent,
    LogEventType.AGENT_RESPONSE: AgentEvent,
    LogEventType.AGENT_ERROR: AgentEvent,
    # Workflow events
    LogEventType.WORKFLOW_START: WorkflowEvent,
    LogEventType.WORKFLOW_END: WorkflowEvent,
    LogEventType.WORKFLOW_COMPONENT_START: WorkflowEvent,
    LogEventType.WORKFLOW_COMPONENT_END: WorkflowEvent,
    LogEventType.WORKFLOW_COMPONENT_ERROR: WorkflowEvent,
    LogEventType.WORKFLOW_BRANCH: WorkflowEvent,
    # LLM events
    LogEventType.LLM_CALL_START: LLMEvent,
    LogEventType.LLM_CALL_END: LLMEvent,
    LogEventType.LLM_CALL_ERROR: LLMEvent,
    LogEventType.LLM_STREAM_CHUNK: LLMEvent,
    # Tool events
    LogEventType.TOOL_CALL_START: ToolEvent,
    LogEventType.TOOL_CALL_END: ToolEvent,
    LogEventType.TOOL_CALL_ERROR: ToolEvent,
    # Memory events
    LogEventType.MEMORY_STORE: MemoryEvent,
    LogEventType.MEMORY_RETRIEVE: MemoryEvent,
    LogEventType.MEMORY_DELETE: MemoryEvent,
    LogEventType.MEMORY_UPDATE: MemoryEvent,
    # Session events
    LogEventType.SESSION_CREATE: SessionEvent,
    LogEventType.SESSION_UPDATE: SessionEvent,
    LogEventType.SESSION_DELETE: SessionEvent,
    # Context events
    LogEventType.CONTEXT_ADD_MESSAGE: ContextEvent,
    LogEventType.CONTEXT_CLEAR: ContextEvent,
    LogEventType.CONTEXT_RETRIEVE: ContextEvent,
    # Retrieval events
    LogEventType.RETRIEVAL_START: RetrievalEvent,
    LogEventType.RETRIEVAL_END: RetrievalEvent,
    LogEventType.RETRIEVAL_ERROR: RetrievalEvent,
    # Performance events
    LogEventType.PERFORMANCE_METRIC: PerformanceEvent,
    # User interaction events
    LogEventType.USER_INPUT: UserInteractionEvent,
    LogEventType.USER_FEEDBACK: UserInteractionEvent,
    # System events
    LogEventType.SYSTEM_START: SystemEvent,
    LogEventType.SYSTEM_SHUTDOWN: SystemEvent,
    LogEventType.SYSTEM_ERROR: SystemEvent,
}

# Cache for event class field names to improve performance
_EVENT_FIELD_CACHE: Dict[type, set[str]] = {}


def _get_event_field_names(event_class: type) -> set[str]:
    """
    Get field names for an event class, with caching for performance

    Args:
        event_class: Event class type

    Returns:
        Set of field names
    """
    if event_class not in _EVENT_FIELD_CACHE:
        _EVENT_FIELD_CACHE[event_class] = {f.name for f in fields(event_class)}
    return _EVENT_FIELD_CACHE[event_class]


# Cache for logger to avoid repeated lookups
_common_logger: Optional[Any] = None


def _get_common_logger() -> Any:
    """
    Get common logger with lazy initialization and caching

    Returns:
        Logger instance
    """
    global _common_logger
    if _common_logger is None:
        try:
            from openjiuwen.core.common.logging.manager import LogManager

            _common_logger = LogManager.get_logger("common")
        except Exception:
            # Fallback to standard logging if LogManager is not available
            import logging

            _common_logger = logging.getLogger(__name__)
    return _common_logger


def create_log_event(event_type: LogEventType, **kwargs: Any) -> BaseLogEvent:
    """
    Create corresponding event object based on event type

    Args:
        event_type: Event type
        **kwargs: Event field parameters (undefined fields will be ignored with warning)

    Returns:
        Event object of corresponding type

    Raises:
        ValueError: If event type is not supported
    """
    event_class = EVENT_CLASS_MAP.get(event_type)
    if event_class is None:
        # If no corresponding class found, use base event class
        event_class = BaseLogEvent

    # Early return if no kwargs to filter
    if not kwargs:
        return event_class(event_type=event_type)

    # Get cached field names for the event class
    field_names = _get_event_field_names(event_class)

    # Filter kwargs to only include fields that exist in the dataclass
    # Use dict comprehension for better performance
    filtered_kwargs = {k: v for k, v in kwargs.items() if k in field_names}

    # Check for ignored fields only if kwargs were filtered (performance optimization)
    if len(filtered_kwargs) < len(kwargs):
        ignored_fields = [k for k in kwargs.keys() if k not in field_names]
        if ignored_fields:
            logger = _get_common_logger()
            logger.warning(  # type: ignore[attr-defined]
                f"Ignoring undefined fields for {event_class.__name__}: {', '.join(ignored_fields)}"
            )

    return event_class(event_type=event_type, **filtered_kwargs)


def validate_event(event: BaseLogEvent) -> bool:
    """
    Validate event object validity

    Args:
        event: Event object to validate

    Returns:
        True if event is valid, False otherwise
    """
    # Check if required fields exist
    if not event.event_id:
        return False

    # Check if enum types are valid (by checking if they have value attribute)
    if not hasattr(event.event_type, "value"):
        return False

    if not hasattr(event.log_level, "value"):
        return False

    if not hasattr(event.module_type, "value"):
        return False

    return True


def sanitize_event_for_logging(event: BaseLogEvent, sensitive_fields: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Sanitize event for logging output

    Args:
        event: Event object to process
        sensitive_fields: List of fields to sanitize, if None, use default sensitive field list

    Returns:
        Sanitized event dictionary
    """
    if sensitive_fields is None:
        sensitive_fields = [
            "messages",
            "response_content",
            "input_content",
            "query",
            "arguments",
            "result",
            "message_content",
            "tool_calls",
            "input_data",
            "output_data",
            "retrieved_memories",
        ]

    event_dict = event.to_dict()

    # Sanitize sensitive fields
    for field_name in sensitive_fields:
        if field_name in event_dict and event_dict[field_name] is not None:
            event_dict[field_name] = "<REDACTED>"

    return event_dict
