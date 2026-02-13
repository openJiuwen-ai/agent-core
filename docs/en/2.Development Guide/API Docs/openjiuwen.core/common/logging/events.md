# openjiuwen.core.common.logging

## class openjiuwen.core.common.logging.events.LogEventType

Log event type enumeration.

* **AGENT_START**: Agent start
* **AGENT_END**: Agent end
* **AGENT_INVOKE**: Agent invocation
* **AGENT_RESPONSE**: Agent response
* **AGENT_ERROR**: Agent error
* **WORKFLOW_START**: Workflow start
* **WORKFLOW_END**: Workflow end
* **WORKFLOW_COMPONENT_START**: Workflow component start
* **WORKFLOW_COMPONENT_END**: Workflow component end
* **WORKFLOW_COMPONENT_ERROR**: Workflow component error
* **WORKFLOW_BRANCH**: Workflow branch selection
* **LLM_CALL_START**: LLM call start
* **LLM_CALL_END**: LLM call end
* **LLM_CALL_ERROR**: LLM call error
* **LLM_STREAM_CHUNK**: LLM streaming chunk
* **TOOL_CALL_START**: Tool call start
* **TOOL_CALL_END**: Tool call end
* **TOOL_CALL_ERROR**: Tool call error
* **STORE_ADD**: Data store add
* **STORE_DELETE**: Data store delete
* **STORE_UPDATE**: Data store update
* **STORE_RETRIEVE**: Data store retrieve
* **STORE_LOAD**: Data store load collection
* **MEMORY_STORE**: Memory store
* **MEMORY_RETRIEVE**: Memory retrieve
* **MEMORY_DELETE**: Memory delete
* **MEMORY_UPDATE**: Memory update
* **MEMORY_PROCESS**: Memory process
* **SESSION_CREATE**: Session create
* **SESSION_UPDATE**: Session update
* **SESSION_DELETE**: Session delete
* **CONTEXT_ADD_MESSAGE**: Context add message
* **CONTEXT_CLEAR**: Context clear
* **CONTEXT_RETRIEVE**: Context retrieve
* **CONTEXT_SAVE**: Context save
* **RETRIEVAL_START**: Retrieval start
* **RETRIEVAL_END**: Retrieval end
* **RETRIEVAL_ERROR**: Retrieval error
* **PERFORMANCE_METRIC**: Performance metric
* **USER_INPUT**: User input
* **USER_FEEDBACK**: User feedback
* **SYSTEM_START**: System start
* **SYSTEM_SHUTDOWN**: System shutdown
* **SYSTEM_ERROR**: System error

## class openjiuwen.core.common.logging.events.LogLevel

Log level enumeration.

* **DEBUG**: Debug level
* **INFO**: Info level
* **WARNING**: Warning level
* **ERROR**: Error level
* **CRITICAL**: Critical error level

## class openjiuwen.core.common.logging.events.ModuleType

Module type enumeration.

* **AGENT**: Agent module
* **WORKFLOW**: Workflow module
* **WORKFLOW_COMPONENT**: Workflow component module
* **LLM**: Large language model module
* **TOOL**: Tool module
* **STORE**: Store module
* **MEMORY**: Memory module
* **SESSION**: Session module
* **CONTEXT**: Context module
* **RETRIEVAL**: Retrieval module
* **SYSTEM**: System module
* **USER**: User module

## class openjiuwen.core.common.logging.events.EventStatus

Event status enumeration.

* **SUCCESS**: Success
* **FAILURE**: Failure
* **PENDING**: Pending
* **TIMEOUT**: Timeout
* **CANCELLED**: Cancelled

## class openjiuwen.core.common.logging.events.BaseLogEvent

Structured log event base class, all other event types inherit from this class.

* **event_id** (str): Unique event identifier
* **event_type** (LogEventType): Event type. Default value: `LogEventType.SYSTEM_START`
* **log_level** (LogLevel): Log level. Default value: `LogLevel.INFO`
* **timestamp** (datetime): Event timestamp (UTC)
* **module_type** (ModuleType): Module type. Default value: `ModuleType.SYSTEM`
* **module_id** (Optional[str]): Module ID (e.g., AgentID, WorkflowID, ToolName, etc.). Default value: `None`
* **module_name** (Optional[str]): Module name. Default value: `None`
* **session_id** (Optional[str]): Session ID. Default value: `None`
* **conversation_id** (Optional[str]): Conversation ID. Default value: `None`
* **trace_id** (Optional[str]): Trace ID. Default value: `None`
* **correlation_id** (Optional[str]): Correlation ID for associating related events. Default value: `None`
* **parent_event_id** (Optional[str]): Parent event ID for building event tree. Default value: `None`
* **status** (EventStatus): Event status. Default value: `EventStatus.SUCCESS`
* **error_code** (Optional[str]): Error code. Default value: `None`
* **error_message** (Optional[str]): Error message. Default value: `None`
* **message** (Optional[str]): Log message content. Default value: `None`
* **stacktrace** (Optional[str]): Stack trace information (for exceptions). Default value: `None`
* **exception** (Optional[str]): Exception details string. Default value: `None`
* **metadata** (Dict[str, Any]): Extended information dictionary


### to_dict() -> Dict[str, Any]

Convert the event object to a serializable dictionary:

* `Enum` values are converted to their `value`
* `datetime` is converted to `isoformat()` string
* `dict` / `list` recursively convert `Enum` / `datetime` within them

## class openjiuwen.core.common.logging.events.AgentEvent

Agent-related event, inherits from `BaseLogEvent`.

* **agent_type** (Optional[str]): Agent type (e.g., ReActAgent, ChatAgent). Default value: `None`
* **agent_config** (Optional[Dict[str, Any]]): Agent configuration. Default value: `None`
* **input_data** (Optional[Dict[str, Any]]): Input data. Default value: `None`
* **output_data** (Optional[Dict[str, Any]]): Output data. Default value: `None`
* **iteration_count** (Optional[int]): Iteration count (for ReAct). Default value: `None`
* **max_iterations** (Optional[int]): Maximum iteration count. Default value: `None`
* **execution_time_ms** (Optional[float]): Execution time (milliseconds). Default value: `None`


## class openjiuwen.core.common.logging.events.WorkflowEvent

Workflow-related event, inherits from `BaseLogEvent`.

* **workflow_id** (Optional[str]): Workflow ID. Default value: `None`
* **workflow_name** (Optional[str]): Workflow name. Default value: `None`
* **component_id** (Optional[str]): Component ID (if component-level event, use base class `module_id` field). Default value: `None`
* **component_name** (Optional[str]): Component name (if component-level event, use base class `module_name` field). Default value: `None`
* **component_type_str** (Optional[str]): Component type string (for recording specific component type, e.g., LLMComponent, ToolComponent). Default value: `None`
* **branch_condition** (Optional[str]): Branch condition (for branch events). Default value: `None`
* **selected_branch** (Optional[str]): Selected branch. Default value: `None`
* **input_data** (Optional[Dict[str, Any]]): Input data. Default value: `None`
* **output_data** (Optional[Dict[str, Any]]): Output data. Default value: `None`
* **execution_time_ms** (Optional[float]): Execution time (milliseconds). Default value: `None`

## class openjiuwen.core.common.logging.events.LLMEvent

LLM call-related event, inherits from `BaseLogEvent`.

* **model_name** (Optional[str]): Model name. Default value: `None`
* **model_provider** (Optional[str]): Model provider. Default value: `None`
* **query** (Optional[str]): Query content (may need sanitization). Default value: `None`
* **messages** (Optional[List[Dict[str, Any]]]): Message list (may need sanitization). Default value: `None`
* **tools** (Optional[List[Dict[str, Any]]]): Tool list. Default value: `None`
* **temperature** (Optional[float]): temperature parameter. Default value: `None`
* **max_tokens** (Optional[int]): Maximum token count. Default value: `None`
* **top_p** (Optional[float]): top_p parameter. Default value: `None`
* **response_content** (Optional[str]): Response content (may need sanitization). Default value: `None`
* **tool_calls** (Optional[List[Dict[str, Any]]]): Tool call list. Default value: `None`
* **usage** (Optional[Dict[str, Any]]): Token usage information. Default value: `None`
* **latency_ms** (Optional[float]): Latency (milliseconds). Default value: `None`
* **is_stream** (bool): Whether it is a streaming call. Default value: `False`
* **chunk_index** (Optional[int]): Chunk index (for streaming calls). Default value: `None`
* **extra_params** (Dict[str, Any]): Additional LLM parameters. Default value: `None`
* **timeout** (Optional[float]): timeout parameter. Default value: `None`
* **stop** (Optional[str]): stop parameter. Default value: `None`
* **max_retries** (Optional[int]): max_retries parameter. Default value: `None`


## class openjiuwen.core.common.logging.events.ToolEvent

Tool call-related event, inherits from `BaseLogEvent`.

* **tool_name** (Optional[str]): Tool name. Default value: `None`
* **tool_type** (Optional[str]): Tool type. Default value: `None`
* **tool_description** (Optional[str]): Tool description. Default value: `None`
* **arguments** (Optional[Dict[str, Any]]): Call arguments. Default value: `None`
* **result** (Optional[Any]): Execution result (may need sanitization). Default value: `None`
* **execution_time_ms** (Optional[float]): Execution time (milliseconds). Default value: `None`
* **tool_call_id** (Optional[str]): Tool call ID. Default value: `None`


## class openjiuwen.core.common.logging.events.StoreEvent

Data store-related event, inherits from `BaseLogEvent`.

* **table_name** (Optional[str]): Table name. Default value: `None`
* **data_num** (Optional[int]): Data count. Default value: `None`


## class openjiuwen.core.common.logging.events.MemoryEvent

Memory operation-related event, inherits from `BaseLogEvent`.

* **memory_type** (Optional[str]): Memory type (e.g., short_term, long_term). Default value: `None`
* **operation** (Optional[str]): Operation type (e.g., store, retrieve, delete, update). Default value: `None`
* **memory_id** (Optional[List[str]]): Memory ID. Default value: `None`
* **query** (Optional[str]): Query content (for retrieval). Default value: `None`
* **memory_count** (Optional[int]): Memory count. Default value: `None`
* **retrieved_memories** (Optional[List[Dict[str, Any]]]): Retrieved memories. Default value: `None`
* **storage_size_bytes** (Optional[int]): Storage size (bytes). Default value: `None`
* **user_id** (Optional[str]): User ID. Default value: `None`
* **scope_id** (Optional[str]): Scope ID. Default value: `None`


## class openjiuwen.core.common.logging.events.SessionEvent

Session management-related event, inherits from `BaseLogEvent`.

* **session_type** (Optional[str]): Session type. Default value: `None`
* **user_id** (Optional[str]): User ID. Default value: `None`
* **agent_id** (Optional[str]): Agent ID. Default value: `None`
* **workflow_id** (Optional[str]): Workflow ID. Default value: `None`
* **session_config** (Optional[Dict[str, Any]]): Session configuration. Default value: `None`
* **message_count** (Optional[int]): Message count. Default value: `None`


## class openjiuwen.core.common.logging.events.ContextEvent

Context operation-related event, inherits from `BaseLogEvent`.

* **message_type** (Optional[str]): Message type (e.g., user, assistant, tool, system). Default value: `None`
* **message_content** (Optional[str]): Message content (may need sanitization). Default value: `None`
* **message_role** (Optional[str]): Message role. Default value: `None`
* **context_size** (Optional[int]): Context size (token count or message count). Default value: `None`
* **max_context_size** (Optional[int]): Maximum context size. Default value: `None`


## class openjiuwen.core.common.logging.events.RetrievalEvent

Retrieval-related event, inherits from `BaseLogEvent`.

* **retrieval_type** (Optional[str]): Retrieval type (e.g., vector, keyword, hybrid). Default value: `None`
* **query** (Optional[str]): Query content. Default value: `None`
* **top_k** (Optional[int]): TopK result count. Default value: `None`
* **retrieved_docs** (Optional[List[Dict[str, Any]]]): Retrieved documents. Default value: `None`
* **retrieval_score** (Optional[float]): Retrieval score. Default value: `None`
* **latency_ms** (Optional[float]): Retrieval latency (milliseconds). Default value: `None`
* **knowledge_base_id** (Optional[str]): Knowledge base ID. Default value: `None`


## class openjiuwen.core.common.logging.events.PerformanceEvent

Performance metric-related event, inherits from `BaseLogEvent`.

* **metric_name** (Optional[str]): Metric name. Default value: `None`
* **metric_value** (Optional[float]): Metric value. Default value: `None`
* **metric_unit** (Optional[str]): Metric unit (e.g., ms, bytes, count). Default value: `None`
* **resource_type** (Optional[str]): Resource type (e.g., cpu, memory, network). Default value: `None`
* **operation** (Optional[str]): Operation name. Default value: `None`


## class openjiuwen.core.common.logging.events.UserInteractionEvent

User interaction-related event, inherits from `BaseLogEvent`.

* **user_id** (Optional[str]): User ID. Default value: `None`
* **input_content** (Optional[str]): Input content (may need sanitization). Default value: `None`
* **feedback_type** (Optional[str]): Feedback type (e.g., positive, negative, neutral). Default value: `None`
* **feedback_content** (Optional[str]): Feedback content. Default value: `None`


## class openjiuwen.core.common.logging.events.SystemEvent

System-level event, inherits from `BaseLogEvent`.

* **system_version** (Optional[str]): System version. Default value: `None`
* **system_config** (Optional[Dict[str, Any]]): System configuration. Default value: `None`
* **resource_usage** (Optional[Dict[str, Any]]): Resource usage information. Default value: `None`
