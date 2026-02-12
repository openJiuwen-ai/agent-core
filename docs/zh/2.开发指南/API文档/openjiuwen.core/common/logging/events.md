# openjiuwen.core.common.logging


## class openjiuwen.core.common.logging.events.LogEventType

日志事件类型枚举。

* **AGENT_START**：Agent启动
* **AGENT_END**：Agent结束
* **AGENT_INVOKE**：Agent调用
* **AGENT_RESPONSE**：Agent响应
* **AGENT_ERROR**：Agent错误
* **WORKFLOW_START**：工作流启动
* **WORKFLOW_END**：工作流结束
* **WORKFLOW_COMPONENT_START**：工作流组件启动
* **WORKFLOW_COMPONENT_END**：工作流组件结束
* **WORKFLOW_COMPONENT_ERROR**：工作流组件错误
* **WORKFLOW_BRANCH**：工作流分支选择
* **LLM_CALL_START**：LLM调用启动
* **LLM_CALL_END**：LLM调用结束
* **LLM_CALL_ERROR**：LLM调用错误
* **LLM_STREAM_CHUNK**：LLM流式分块
* **TOOL_CALL_START**：工具调用启动
* **TOOL_CALL_END**：工具调用结束
* **TOOL_CALL_ERROR**：工具调用错误
* **STORE_ADD**：数据存储添加
* **STORE_DELETE**：数据存储删除
* **STORE_UPDATE**：数据存储更新
* **STORE_RETRIEVE**：数据存储检索
* **MEMORY_STORE**：内存存储
* **MEMORY_RETRIEVE**：内存检索
* **MEMORY_DELETE**：内存删除
* **MEMORY_UPDATE**：内存更新
* **MEMORY_PROCESS**：内存处理
* **SESSION_CREATE**：会话创建
* **SESSION_UPDATE**：会话更新
* **SESSION_DELETE**：会话删除
* **CONTEXT_ADD_MESSAGE**：上下文添加消息
* **CONTEXT_CLEAR**：上下文清空
* **CONTEXT_RETRIEVE**：上下文检索
* **CONTEXT_SAVE**：上下文保存
* **RETRIEVAL_START**：检索启动
* **RETRIEVAL_END**：检索结束
* **RETRIEVAL_ERROR**：检索错误
* **PERFORMANCE_METRIC**：性能指标
* **USER_INPUT**：用户输入
* **USER_FEEDBACK**：用户反馈
* **SYSTEM_START**：系统启动
* **SYSTEM_SHUTDOWN**：系统关闭
* **SYSTEM_ERROR**：系统错误

## class openjiuwen.core.common.logging.events.LogLevel

日志级别枚举。

* **DEBUG**：调试级别
* **INFO**：信息级别
* **WARNING**：警告级别
* **ERROR**：错误级别
* **CRITICAL**：严重错误级别

## class openjiuwen.core.common.logging.events.ModuleType

模块类型枚举。

* **AGENT**：Agent模块
* **WORKFLOW**：工作流模块
* **WORKFLOW_COMPONENT**：工作流组件模块
* **LLM**：大语言模型模块
* **TOOL**：工具模块
* **STORE**：存储模块
* **MEMORY**：内存模块
* **SESSION**：会话模块
* **CONTEXT**：上下文模块
* **RETRIEVAL**：检索模块
* **SYSTEM**：系统模块
* **USER**：用户模块

## class openjiuwen.core.common.logging.events.EventStatus

事件状态枚举。

* **SUCCESS**：成功
* **FAILURE**：失败
* **PENDING**：待处理
* **TIMEOUT**：超时
* **CANCELLED**：已取消

## class openjiuwen.core.common.logging.events.BaseLogEvent

结构化日志事件基类，其他事件类型均继承自该类。

* **event_id**(str)：事件唯一标识
* **event_type**(LogEventType)：事件类型。默认值：`LogEventType.SYSTEM_START`
* **log_level**(LogLevel)：日志级别。默认值：`LogLevel.INFO`
* **timestamp**(datetime)：事件时间戳（UTC）
* **module_type**(ModuleType)：模块类型。默认值：`ModuleType.SYSTEM`
* **module_id**(Optional[str])：模块ID（例如AgentID、WorkflowID、ToolName等）。默认值：`None`
* **module_name**(Optional[str])：模块名称。默认值：`None`
* **session_id**(Optional[str])：会话ID。默认值：`None`
* **conversation_id**(Optional[str])：对话ID。默认值：`None`
* **trace_id**(Optional[str])：TraceID。默认值：`None`
* **correlation_id**(Optional[str])：关联ID，用于关联相关事件。默认值：`None`
* **parent_event_id**(Optional[str])：父事件ID，用于构建事件树。默认值：`None`
* **status**(EventStatus)：事件状态。默认值：`EventStatus.SUCCESS`
* **error_code**(Optional[str])：错误码。默认值：`None`
* **error_message**(Optional[str])：错误信息。默认值：`None`
* **message**(Optional[str])：日志消息内容。默认值：`None`
* **stacktrace**(Optional[str])：堆栈信息（用于异常）。默认值：`None`
* **exception**(Optional[str])：异常详情字符串。默认值：`None`
* **metadata**(Dict[str, Any])：扩展信息字典


### to_dict() -> Dict[str, Any]

将事件对象转换为可序列化的字典：

* `Enum` 值会转换为其 `value`
* `datetime` 会转换为 `isoformat()` 字符串
* `dict` / `list` 会递归转换其中的 `Enum` / `datetime`

## class openjiuwen.core.common.logging.events.AgentEvent

Agent相关事件，继承自`BaseLogEvent`。

* **agent_type**(Optional[str])：Agent类型（例如ReActAgent、ChatAgent）。默认值：`None`
* **agent_config**(Optional[Dict[str, Any]])：Agent配置。默认值：`None`
* **input_data**(Optional[Dict[str, Any]])：输入数据。默认值：`None`
* **output_data**(Optional[Dict[str, Any]])：输出数据。默认值：`None`
* **iteration_count**(Optional[int])：迭代次数（for ReAct）。默认值：`None`
* **max_iterations**(Optional[int])：最大迭代次数。默认值：`None`
* **execution_time_ms**(Optional[float])：执行耗时（毫秒）。默认值：`None`


## class openjiuwen.core.common.logging.events.WorkflowEvent

工作流相关事件，继承自`BaseLogEvent`。

* **workflow_id**(Optional[str])：工作流ID。默认值：`None`
* **workflow_name**(Optional[str])：工作流名称。默认值：`None`
* **component_id**(Optional[str])：组件ID（若为组件级事件，使用基类`module_id`字段）。默认值：`None`
* **component_name**(Optional[str])：组件名称（若为组件级事件，使用基类`module_name`字段）。默认值：`None`
* **component_type_str**(Optional[str])：组件类型字符串（用于记录具体组件类型，例如LLMComponent、ToolComponent）。默认值：`None`
* **branch_condition**(Optional[str])：分支条件（用于分支事件）。默认值：`None`
* **selected_branch**(Optional[str])：选中的分支。默认值：`None`
* **input_data**(Optional[Dict[str, Any]])：输入数据。默认值：`None`
* **output_data**(Optional[Dict[str, Any]])：输出数据。默认值：`None`
* **execution_time_ms**(Optional[float])：执行耗时（毫秒）。默认值：`None`

## class openjiuwen.core.common.logging.events.LLMEvent

LLM调用相关事件，继承自`BaseLogEvent`。

* **model_name**(Optional[str])：模型名称。默认值：`None`
* **model_provider**(Optional[str])：模型提供方。默认值：`None`
* **query**(Optional[str])：查询内容（may need sanitization）。默认值：`None`
* **messages**(Optional[List[Dict[str, Any]]])：消息列表（may need sanitization）。默认值：`None`
* **tools**(Optional[List[Dict[str, Any]]])：工具列表。默认值：`None`
* **temperature**(Optional[float])：temperature 参数。默认值：`None`
* **max_tokens**(Optional[int])：最大token数。默认值：`None`
* **top_p**(Optional[float])：top_p 参数。默认值：`None`
* **response_content**(Optional[str])：响应内容（may need sanitization）。默认值：`None`
* **tool_calls**(Optional[List[Dict[str, Any]]])：工具调用列表。默认值：`None`
* **usage**(Optional[Dict[str, Any]])：token使用信息。默认值：`None`
* **latency_ms**(Optional[float])：延迟（毫秒）。默认值：`None`
* **is_stream**(bool)：是否为流式调用。默认值：`False`
* **chunk_index**(Optional[int])：分片索引（用于流式调用）。默认值：`None`
* **extra_params**(Dict[str, Any])：额外的LLM参数。默认值：`None`
* **timeout**(Optional[float])：timeout 参数。默认值：`None`
* **stop**(Optional[str])：stop 参数。默认值：`None`
* **max_retries**(Optional[int])：max_retries 参数。默认值：`None`


## class openjiuwen.core.common.logging.events.ToolEvent

工具调用相关事件，继承自`BaseLogEvent`。

* **tool_name**(Optional[str])：工具名称。默认值：`None`
* **tool_type**(Optional[str])：工具类型。默认值：`None`
* **tool_description**(Optional[str])：工具描述。默认值：`None`
* **arguments**(Optional[Dict[str, Any]])：调用参数。默认值：`None`
* **result**(Optional[Any])：执行结果（may need sanitization）。默认值：`None`
* **execution_time_ms**(Optional[float])：执行耗时（毫秒）。默认值：`None`
* **tool_call_id**(Optional[str])：工具调用ID。默认值：`None`


## class openjiuwen.core.common.logging.events.StoreEvent

数据存储相关事件，继承自`BaseLogEvent`。

* **table_name**(Optional[str])：表名。默认值：`None`
* **data_num**(Optional[int])：数据数量。默认值：`None`


## class openjiuwen.core.common.logging.events.MemoryEvent

内存（Memory）操作相关事件，继承自`BaseLogEvent`。

* **memory_type**(Optional[str])：Memory类型（例如short_term、long_term）。默认值：`None`
* **operation**(Optional[str])：操作类型（例如store、retrieve、delete、update）。默认值：`None`
* **memory_id**(Optional[List[str]])：MemoryID。默认值：`None`
* **query**(Optional[str])：查询内容（用于检索）。默认值：`None`
* **memory_count**(Optional[int])：Memory数量。默认值：`None`
* **retrieved_memories**(Optional[List[Dict[str, Any]]])：检索到的memories。默认值：`None`
* **storage_size_bytes**(Optional[int])：存储大小（字节）。默认值：`None`
* **user_id**(Optional[str])：用户ID。默认值：`None`
* **scope_id**(Optional[str])：作用域ID。默认值：`None`


## class openjiuwen.core.common.logging.events.SessionEvent

会话管理相关事件，继承自`BaseLogEvent`。

* **session_type**(Optional[str])：会话类型。默认值：`None`
* **user_id**(Optional[str])：用户ID。默认值：`None`
* **agent_id**(Optional[str])：AgentID。默认值：`None`
* **workflow_id**(Optional[str])：WorkflowID。默认值：`None`
* **session_config**(Optional[Dict[str, Any]])：会话配置。默认值：`None`
* **message_count**(Optional[int])：消息数量。默认值：`None`


## class openjiuwen.core.common.logging.events.ContextEvent

上下文操作相关事件，继承自`BaseLogEvent`。

* **message_type**(Optional[str])：消息类型（例如user、assistant、tool、system）。默认值：`None`
* **message_content**(Optional[str])：消息内容（may need sanitization）。默认值：`None`
* **message_role**(Optional[str])：消息角色。默认值：`None`
* **context_size**(Optional[int])：上下文大小（token count或message count）。默认值：`None`
* **max_context_size**(Optional[int])：最大上下文大小。默认值：`None`


## class openjiuwen.core.common.logging.events.RetrievalEvent

检索相关事件，继承自`BaseLogEvent`。

* **retrieval_type**(Optional[str])：检索类型（例如vector、keyword、hybrid）。默认值：`None`
* **query**(Optional[str])：查询内容。默认值：`None`
* **top_k**(Optional[int])：返回TopK结果数。默认值：`None`
* **retrieved_docs**(Optional[List[Dict[str, Any]]])：检索到的文档。默认值：`None`
* **retrieval_score**(Optional[float])：检索分数。默认值：`None`
* **latency_ms**(Optional[float])：检索延迟（毫秒）。默认值：`None`
* **knowledge_base_id**(Optional[str])：知识库ID。默认值：`None`


## class openjiuwen.core.common.logging.events.PerformanceEvent

性能指标相关事件，继承自`BaseLogEvent`。

* **metric_name**(Optional[str])：指标名称。默认值：`None`
* **metric_value**(Optional[float])：指标值。默认值：`None`
* **metric_unit**(Optional[str])：指标单位（例如ms、bytes、count）。默认值：`None`
* **resource_type**(Optional[str])：资源类型（例如cpu、memory、network）。默认值：`None`
* **operation**(Optional[str])：操作名称。默认值：`None`


## class openjiuwen.core.common.logging.events.UserInteractionEvent

用户交互相关事件，继承自`BaseLogEvent`。

* **user_id**(Optional[str])：用户ID。默认值：`None`
* **input_content**(Optional[str])：输入内容（may need sanitization）。默认值：`None`
* **feedback_type**(Optional[str])：反馈类型（例如positive、negative、neutral）。默认值：`None`
* **feedback_content**(Optional[str])：反馈内容。默认值：`None`


## class openjiuwen.core.common.logging.events.SystemEvent

系统级事件，继承自`BaseLogEvent`。

* **system_version**(Optional[str])：系统版本。默认值：`None`
* **system_config**(Optional[Dict[str, Any]])：系统配置。默认值：`None`
* **resource_usage**(Optional[Dict[str, Any]])：资源使用情况。默认值：`None`
