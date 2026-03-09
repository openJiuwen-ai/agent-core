# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Callback Framework Preset Events

Defines standard event names for agent execution lifecycle and other common callback points.

Events support scope isolation using colon(:) as separator, e.g., "scope:event_name".
System-level events use "_framework" as default scope.
"""


# Default system scope
DEFAULT_SCOPE = "_framework"


def build_event_name(scope: str, event_name: str) -> str:
    """Build a scoped event name.

    Args:
        scope: The scope for the event
        event_name: The event name

    Returns:
        Scoped event name in format "scope:event_name"
    """
    return f"{scope}:{event_name}"


def parse_event_name(scoped_event: str) -> tuple[str, str]:
    """Parse a scoped event name into scope and event name.

    Args:
        scoped_event: Scoped event name in format "scope:event_name"

    Returns:
        Tuple of (scope, event_name)
        If no scope is specified, returns (DEFAULT_SCOPE, event_name)
    """
    if ":" in scoped_event:
        scope, event_name = scoped_event.split(":", 1)
        return scope, event_name
    return DEFAULT_SCOPE, scoped_event


class EventBase:
    """Base class for event definitions with scope support.

    Attributes:
        scope: The scope for all events in this class
    """
    scope: str = DEFAULT_SCOPE

    def __init_subclass__(cls, **kwargs):
        """Initialize subclass and resolve event names with correct scope.

        This ensures that event attributes defined with get_event() use
        the subclass's scope, not EventBase's scope.

        Args:
            **kwargs: Additional keyword arguments passed to parent
        """
        super().__init_subclass__(**kwargs)
        for attr_name, attr_value in list(cls.__dict__.items()):
            if isinstance(attr_value, str) and ':' in attr_value:
                scope, event_name = parse_event_name(attr_value)
                if scope == DEFAULT_SCOPE and cls.scope != DEFAULT_SCOPE:
                    setattr(cls, attr_name, build_event_name(cls.scope, event_name))

    @classmethod
    def get_event(cls, event_name: str) -> str:
        """Get the full scoped event name.

        Args:
            event_name: The raw event name

        Returns:
            Full scoped event name
        """
        return build_event_name(cls.scope, event_name)


class AgentEvents(EventBase):
    """Standard event names for agent lifecycle events.

    Attributes:
        AGENT_STARTED: Agent execution started
        AGENT_FINISHED: Agent execution completed successfully
        AGENT_ERROR: Agent execution failed with an error
        AGENT_CANCELLED: Agent execution was cancelled
        AGENT_STATE_CHANGED: Agent internal state changed
    """
    AGENT_STARTED = EventBase.get_event("agent_started")
    AGENT_FINISHED = EventBase.get_event("agent_finished")
    AGENT_ERROR = EventBase.get_event("agent_error")
    AGENT_CANCELLED = EventBase.get_event("agent_cancelled")
    AGENT_STATE_CHANGED = EventBase.get_event("agent_state_changed")


class WorkflowEvents(EventBase):
    """Standard event names for workflow execution.

    Attributes:
        WORKFLOW_STARTED: Workflow execution started
        WORKFLOW_FINISHED: Workflow execution completed successfully
        WORKFLOW_ERROR: Workflow execution failed with an error
        WORKFLOW_CANCELLED: Workflow execution was cancelled
        NODE_EXECUTED: Workflow node completed execution
        NODE_ERROR: Workflow node execution failed
        EDGE_TRAVERSED: Workflow edge was traversed
        LOOP_STARTED: Workflow loop started
        LOOP_FINISHED: Workflow loop completed
        WORKFLOW_INVOKE_INPUT: Fired before Workflow.invoke with call arguments
        WORKFLOW_INVOKE_OUTPUT: Fired after Workflow.invoke with the result
        WORKFLOW_STREAM_INPUT: Fired before Workflow.stream with call arguments
        WORKFLOW_STREAM_OUTPUT: Fired for each item yielded by Workflow.stream
    """
    WORKFLOW_STARTED = EventBase.get_event("workflow_started")
    WORKFLOW_FINISHED = EventBase.get_event("workflow_finished")
    WORKFLOW_ERROR = EventBase.get_event("workflow_error")
    WORKFLOW_CANCELLED = EventBase.get_event("workflow_cancelled")
    NODE_EXECUTED = EventBase.get_event("workflow_node_executed")
    NODE_ERROR = EventBase.get_event("workflow_node_error")
    EDGE_TRAVERSED = EventBase.get_event("workflow_edge_traversed")
    LOOP_STARTED = EventBase.get_event("workflow_loop_started")
    LOOP_FINISHED = EventBase.get_event("workflow_loop_finished")
    WORKFLOW_INVOKE_INPUT = EventBase.get_event("workflow_invoke_input")
    WORKFLOW_INVOKE_OUTPUT = EventBase.get_event("workflow_invoke_output")
    WORKFLOW_STREAM_INPUT = EventBase.get_event("workflow_stream_input")
    WORKFLOW_STREAM_OUTPUT = EventBase.get_event("workflow_stream_output")


class LLMCallEvents(EventBase):
    """Standard event names for LLM call operations.

    Attributes:
        LLM_CALL_STARTED: LLM call initiated
        LLM_CALL_FINISHED: LLM call completed successfully
        LLM_CALL_ERROR: LLM call failed with an error
        LLM_RESPONSE_RECEIVED: LLM response received (streaming)
        LLM_RESPONSE_COMPLETED: LLM response completed (streaming)
        PROMPT_GENERATED: Prompt was generated for LLM call
        LLM_INVOKE_INPUT: Fired before BaseModelClient.invoke with call arguments
        LLM_INVOKE_OUTPUT: Fired after BaseModelClient.invoke with the result
        LLM_STREAM_INPUT: Fired before BaseModelClient.stream with call arguments
        LLM_STREAM_OUTPUT: Fired for each item yielded by BaseModelClient.stream
    """
    LLM_CALL_STARTED = EventBase.get_event("llm_call_started")
    LLM_CALL_FINISHED = EventBase.get_event("llm_call_finished")
    LLM_CALL_ERROR = EventBase.get_event("llm_call_error")
    LLM_RESPONSE_RECEIVED = EventBase.get_event("llm_response_received")
    LLM_RESPONSE_COMPLETED = EventBase.get_event("llm_response_completed")
    PROMPT_GENERATED = EventBase.get_event("prompt_generated")
    LLM_INVOKE_INPUT = EventBase.get_event("llm_invoke_input")
    LLM_INVOKE_OUTPUT = EventBase.get_event("llm_invoke_output")
    LLM_STREAM_INPUT = EventBase.get_event("llm_stream_input")
    LLM_STREAM_OUTPUT = EventBase.get_event("llm_stream_output")


class ToolCallEvents(EventBase):
    """Standard event names for tool call operations.

    Attributes:
        TOOL_CALL_STARTED: Tool call initiated
        TOOL_CALL_FINISHED: Tool call completed successfully
        TOOL_CALL_ERROR: Tool call failed with an error
        TOOL_RESULT_RECEIVED: Tool result received
        TOOL_PARSE_STARTED: Tool result parsing started
        TOOL_PARSE_FINISHED: Tool result parsing completed
        TOOL_INVOKE_INPUT: Fired before Tool.invoke with call arguments
        TOOL_INVOKE_OUTPUT: Fired after Tool.invoke with the result
        TOOL_STREAM_INPUT: Fired before Tool.stream with call arguments
        TOOL_STREAM_OUTPUT: Fired for each item yielded by Tool.stream
    """
    TOOL_CALL_STARTED = EventBase.get_event("tool_call_started")
    TOOL_CALL_FINISHED = EventBase.get_event("tool_call_finished")
    TOOL_CALL_ERROR = EventBase.get_event("tool_call_error")
    TOOL_RESULT_RECEIVED = EventBase.get_event("tool_result_received")
    TOOL_PARSE_STARTED = EventBase.get_event("tool_parse_started")
    TOOL_PARSE_FINISHED = EventBase.get_event("tool_parse_finished")
    TOOL_INVOKE_INPUT = EventBase.get_event("tool_invoke_input")
    TOOL_INVOKE_OUTPUT = EventBase.get_event("tool_invoke_output")
    TOOL_STREAM_INPUT = EventBase.get_event("tool_stream_input")
    TOOL_STREAM_OUTPUT = EventBase.get_event("tool_stream_output")


class ContextEvents(EventBase):
    """Standard event names for context management.

    Attributes:
        CONTEXT_UPDATED: Context was updated
        CONTEXT_COMPRESSED: Context was compressed
        CONTEXT_OFFLOADED: Context was offloaded to storage
        CONTEXT_RETRIEVED: Context was retrieved from storage
        CONTEXT_CLEARED: Context was cleared
    """
    CONTEXT_UPDATED = EventBase.get_event("context_updated")
    CONTEXT_COMPRESSED = EventBase.get_event("context_compressed")
    CONTEXT_OFFLOADED = EventBase.get_event("context_offloaded")
    CONTEXT_RETRIEVED = EventBase.get_event("context_retrieved")
    CONTEXT_CLEARED = EventBase.get_event("context_cleared")


class SessionEvents(EventBase):
    """Standard event names for session management.

    Attributes:
        SESSION_CREATED: Session was created
        SESSION_UPDATED: Session was updated
        SESSION_ENDED: Session was ended
        SESSION_RESTORED: Session was restored from storage
        SESSION_SAVED: Session was saved to storage
    """
    SESSION_CREATED = EventBase.get_event("session_created")
    SESSION_UPDATED = EventBase.get_event("session_updated")
    SESSION_ENDED = EventBase.get_event("session_ended")
    SESSION_RESTORED = EventBase.get_event("session_restored")
    SESSION_SAVED = EventBase.get_event("session_saved")


class RetrievalEvents(EventBase):
    """Standard event names for knowledge retrieval operations.

    Attributes:
        RETRIEVAL_STARTED: Knowledge retrieval started
        RETRIEVAL_FINISHED: Knowledge retrieval completed successfully
        RETRIEVAL_ERROR: Knowledge retrieval failed with an error
        DOCUMENT_RETRIEVED: Document was retrieved
        DOCUMENT_RERANKED: Retrieved documents were reranked
        EMBEDDING_GENERATED: Embedding was generated for query
    """
    RETRIEVAL_STARTED = EventBase.get_event("retrieval_started")
    RETRIEVAL_FINISHED = EventBase.get_event("retrieval_finished")
    RETRIEVAL_ERROR = EventBase.get_event("retrieval_error")
    DOCUMENT_RETRIEVED = EventBase.get_event("document_retrieved")
    DOCUMENT_RERANKED = EventBase.get_event("document_reranked")
    EMBEDDING_GENERATED = EventBase.get_event("embedding_generated")
