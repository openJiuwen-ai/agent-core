# openjiuwen.core.common.exception.status_code

## class openjiuwen.core.common.exception.status_code.StatusCode

错误码枚举类,错误的详细信息和解决方法如下表所示:

| 枚举常量 | 错误码 | 描述 | 解决方法 |
|---------|--------|------|----------|
|  |**基础状态码**| | |
| SUCCESS | 0 | 操作成功。 | 无需处理。 |
| ERROR | -1 | 通用错误。 | 根据具体错误信息排查问题。 |
|  | **工作流相关错误 (100000 - 100999)**| | |
|  |**工作流验证错误 (100000 - 100099)** | | |
| WORKFLOW_COMPONENT_ID_INVALID | 100010 | 组件ID无效。 | 检查组件ID是否符合命名规范,确保工作流中组件ID唯一且不为空。 |
| WORKFLOW_COMPONENT_ABILITY_INVALID | 100011 | 组件能力配置无效。 | 检查组件能力配置是否在支持列表中(invoke/stream/collect/transform)。 |
| WORKFLOW_EDGE_INVALID | 100012 | 工作流边连接无效。 | 检查边的起始节点和目标节点是否存在,确保不存在循环依赖。 |
| WORKFLOW_CONDITION_EDGE_INVALID | 100013 | 条件边配置无效。 | 检查条件边的条件表达式语法是否正确,确保条件逻辑合理。 |
| WORKFLOW_COMPONENT_SCHEMA_INVALID | 100014 | 组件输入/输出schema无效。 | 检查组件的inputs_schema和output_config配置是否符合JSON Schema规范。 |
| WORKFLOW_STREAM_EDGE_INVALID | 100015 | 流式边连接无效。 | 检查流式边的源组件是否支持流式输出,目标组件是否支持流式输入。 |
| WORKFLOW_EXECUTE_INPUT_INVALID | 100016 | 工作流执行输入无效。 | 检查工作流执行时的inputs参数是否包含所有必需字段。 |
| WORKFLOW_EXECUTE_SESSION_INVALID | 100017 | 工作流执行session无效。 | 检查session对象是否正确初始化,确保session_id唯一。 |
|  |**工作流执行错误 (100100 - 100199)**| | |
| WORKFLOW_COMPILE_ERROR | 100100 | 工作流编译失败。 | 检查工作流结构完整性,确保所有组件和边配置正确。 |
| WORKFLOW_EXECUTION_TIMEOUT | 100101 | 工作流执行超时。 | 增加超时时间配置或优化工作流组件执行效率。 |
| WORKFLOW_EXECUTION_ERROR | 100102 | 工作流执行出现错误。 | 根据错误详情排查具体组件执行失败原因。 |
| |**工作流组件编排错误 (100200 - 100299)**| | |
| WORKFLOW_INNER_ORCHESTRATION_ERROR | 100053 | 工作流内部编排错误。 | 检查工作流内部组件调度逻辑,确保组件间依赖关系正确。 |
| WORKFLOW_COMPONENT_EXECUTION_ERROR | 100054 | 组件执行错误。 | 根据错误信息排查指定组件的执行逻辑和输入参数。 |
| |**内置工作流组件相关错误 (101000 - 101999)**| | |
| | **End组件 (101010 - 101019)** | | |
| COMPONENT_END_PARAM_INVALID | 100010 | End组件参数无效。 | 检查End组件的responseTemplate配置是否为有效字符串。 |
| |**Branch组件 (101020 - 101029)**| | |
| COMPONENT_BRANCH_PARAM_INVALID | 101020 | Branch组件参数无效。 | 检查Branch组件的分支条件配置是否完整。 |
| COMPONENT_BRANCH_EXECUTION_ERROR | 101021 | Branch组件执行错误。 | 检查分支条件表达式是否正确,确保至少有一个分支可以匹配。 |
| EXPRESSION_SYNTAX_ERROR | 101024 | 表达式语法错误。 | 检查表达式语法,确保符合Python表达式规范。 |
| EXPRESSION_EVAL_ERROR | 101025 | 表达式求值错误。 | 检查表达式中引用的变量是否存在,数据类型是否正确。 |
| ARRAY_CONDITION_ERROR | 101026 | 数组条件错误。 | 检查数组条件表达式,确保数组索引和操作符正确。 |
| NUMBER_CONDITION_ERROR | 101027 | 数值条件错误。 | 检查数值条件表达式,确保比较操作符和数值格式正确。 |
| | **Loop组件 (101030 - 101049)** | | |
| COMPONENT_LOOP_GROUP_PARAM_INVALID | 101030 | Loop group参数无效。 | 检查循环组配置,确保loop_group包含有效组件。 |
| COMPONENT_LOOP_SET_VAR_PARAM_INVALID | 101031 | Loop set_var参数无效。 | 检查循环变量设置配置,确保变量映射关系正确。 |
| COMPONENT_LOOP_EXECUTION_ERROR | 101040 | Loop组件执行错误。 | 检查循环条件和循环体逻辑,确保不会产生死循环。 |
| COMPONENT_LOOP_CONDITION_EXECUTION_ERROR | 101041 | Loop条件执行错误。 | 检查循环条件表达式,确保返回布尔值。 |
| COMPONENT_LOOP_BREAK_EXECUTION_ERROR | 101042 | Loop中断执行错误。 | 检查循环中断条件配置。 |
| COMPONENT_LOOP_SET_VAR_EXECUTION_ERROR | 101043 | Loop变量设置执行错误。 | 检查循环变量赋值逻辑。 |
| | **SubWorkflow组件 (101150 - 101159)** | | |
| COMPONENT_SUB_WORKFLOW_PARAM_INVALID | 101150 | SubWorkflow参数无效。 | 检查子工作流实例是否有效,确保子工作流已正确注册。 |
| | **LLM组件 (101000 - 101049)** | | |
| COMPONENT_LLM_TEMPLATE_CONFIG_ERROR | 101000 | LLM组件提示词模板配置错误。 | 检查template_content配置,确保消息列表格式正确(包含role和content)。 |
| COMPONENT_LLM_RESPONSE_CONFIG_INVALID | 101001 | LLM组件输出格式配置无效。 | 检查response_format配置,确保type字段为"text"/"json"/"markdown"之一。 |
| COMPONENT_LLM_CONFIG_ERROR | 101002 | LLM组件配置错误。 | 检查LLMCompConfig的完整性,确保model_client_config和model_config已配置。 |
| COMPONENT_LLM_INVOKE_CALL_FAILED | 101003 | LLM组件调用大模型失败。 | 检查API_KEY/API_BASE配置,确认模型服务可用性和网络连接。 |
| COMPONENT_LLM_EXECUTION_PROCESS_ERROR | 101004 | LLM组件执行处理错误。 | 检查output_config定义的字段是否与大模型输出匹配。 |
| COMPONENT_LLM_INIT_FAILED | 101005 | LLM组件初始化失败。 | 检查ModelClientConfig配置,确保client_provider正确。 |
| COMPONENT_LLM_TEMPLATE_PROCESS_ERROR | 101006 | LLM组件模板处理错误。 | 检查模板变量填充,确保所有占位符变量都有对应值。 |
| COMPONENT_LLM_CONFIG_INVALID | 101007 | LLM组件配置无效。 | 检查LLM组件配置的完整性和合法性。 |
| | **IntentDetection组件 (101050 - 101069)** | | |
| COMPONENT_INTENT_DETECTION_INPUT_PARAM_ERROR | 101050 | 意图识别组件输入参数错误。 | 检查输入是否包含必需的query字段。 |
| COMPONENT_INTENT_DETECTION_LLM_INIT_FAILED | 101051 | 意图识别组件LLM初始化失败。 | 检查大模型配置是否正确。 |
| COMPONENT_INTENT_DETECTION_INVOKE_CALL_FAILED | 101052 | 意图识别组件调用失败。 | 检查大模型服务可用性和输入格式。 |
| | **Questioner组件 (101070 - 101099)** | | |
| COMPONENT_QUESTIONER_INPUT_PARAM_ERROR | 101070 | 提问器组件输入参数错误。 | 检查输入参数是否满足组件要求。 |
| COMPONENT_QUESTIONER_CONFIG_ERROR | 101071 | 提问器组件配置错误。 | 检查question_content和extract_fields_from_response配置。 |
| COMPONENT_QUESTIONER_INPUT_INVALID | 101072 | 提问器组件输入无效。 | 确保配置了预设问题或开启了参数提取开关。 |
| COMPONENT_QUESTIONER_STATE_INIT_FAILED | 101073 | 提问器组件状态初始化失败。 | 检查状态初始化依赖资源。 |
| COMPONENT_QUESTIONER_RUNTIME_ERROR | 101074 | 提问器组件运行时错误。 | 检查是否超过最大追问次数,确认用户输入完整性。 |
| COMPONENT_QUESTIONER_INVOKE_CALL_FAILED | 101075 | 提问器组件调用失败。 | 检查大模型服务配置和可用性。 |
| COMPONENT_QUESTIONER_EXECUTION_PROCESS_ERROR | 101076 | 提问器组件执行处理错误。 | 检查大模型响应格式和解析逻辑。 |
| | **KnowledgeRetrieval组件 (101100 - 101149)** | | |
| COMPONENT_KNOWLEDGE_RETRIEVAL_INVOKE_CALL_FAILED | 101100 | 知识检索组件调用失败。 | 检查知识库连接和大模型服务可用性。 |
| COMPONENT_KNOWLEDGE_RETRIEVAL_EMBED_MODEL_INIT_ERROR | 101101 | 知识检索组件Embedding模型初始化错误。 | 检查Embedding模型配置是否正确。 |
| COMPONENT_KNOWLEDGE_RETRIEVAL_INPUT_PARAM_ERROR | 101102 | 知识检索组件输入参数错误。 | 检查输入参数是否满足组件要求(如包含query)。 |
| COMPONENT_KNOWLEDGE_RETRIEVAL_LLM_MODEL_INIT_ERROR | 101103 | 知识检索组件LLM模型初始化失败。 | 检查LLM模型配置和ApiKey。 |
| | **Tool组件 (102000 - 102019)** | | |
| COMPONENT_TOOL_EXECUTION_ERROR | 102000 | Tool组件执行错误。 | 检查工具执行逻辑和依赖服务可用性。 |
| COMPONENT_TOOL_INPUT_PARAM_ERROR | 102001 | Tool组件输入参数错误。 | 检查输入参数是否符合工具定义的schema。 |
| COMPONENT_TOOL_INIT_FAILED | 102002 | Tool组件初始化失败。 | 确保绑定了有效的工具实例。 |
| | **Agent编排相关错误 (120000 - 129999)** | | |
| | **ReAct Agent (120000 - 120999)** | | |
| AGENT_TOOL_NOT_FOUND | 120000 | Agent工具未找到。 | 检查工具ID是否正确,确认工具已注册到Agent。 |
| AGENT_TOOL_EXECUTION_ERROR | 120001 | Agent工具执行错误。 | 检查工具执行逻辑和输入参数。 |
| AGENT_TASK_NOT_SUPPORT | 120002 | Agent任务类型不支持。 | 确认任务类型在支持列表中,补充对应处理逻辑。 |
| AGENT_WORKFLOW_EXECUTION_ERROR | 120003 | Agent工作流执行错误。 | 检查工作流配置和执行日志。 |
| AGENT_PROMPT_PARAM_ERROR | 120004 | Agent提示词参数错误。 | 检查prompt_template内容有效性。 |
| | **Agent Controller (123000 - 123999)** | | |
| AGENT_CONTROLLER_INVOKE_CALL_FAILED | 123000 | Agent控制器调用失败。 | 检查控制器的LLM配置和服务可用性。 |
| AGENT_SUB_TASK_TYPE_NOT_SUPPORT | 123001 | Agent子任务类型不支持。 | 检查子任务类型和输入格式。 |
| AGENT_CONTROLLER_USER_INPUT_PROCESS_ERROR | 123002 | Agent控制器用户输入处理错误。 | 根据错误信息检查用户输入格式。 |
| AGENT_CONTROLLER_RUNTIME_ERROR | 123003 | Agent控制器运行时错误。 | 根据错误详情排查控制器运行状态。 |
| AGENT_CONTROLLER_EXECUTION_CALL_FAILED | 123004 | Agent控制器执行调用失败。 | 检查流式输出配置和写入逻辑。 |
| AGENT_CONTROLLER_TOOL_EXECUTION_PROCESS_ERROR | 123005 | Agent控制器工具执行处理错误。 | 检查工具调用参数的JSON格式,优化提示词引导大模型输出。 |
| AGENT_CONTROLLER_TASK_PARAM_ERROR | 123006 | 控制器任务参数错误。 | 检查任务参数配置。 |
| AGENT_CONTROLLER_INTENT_PARAM_ERROR | 123007 | 控制器意图参数错误。 | 检查意图参数配置。 |
| AGENT_CONTROLLER_TASK_EXECUTION_ERROR | 123008 | 控制器任务执行错误。 | 根据错误信息排查任务执行逻辑。 |
| AGENT_CONTROLLER_EVENT_HANDLER_ERROR | 123009 | 控制器事件处理器错误。 | 检查事件处理逻辑。 |
| AGENT_CONTROLLER_EVENT_QUEUE_ERROR | 123010 | Agent控制器事件队列执行错误。 | 检查事件队列配置和处理逻辑。 |
| | **Runner/Distributed相关错误 (110000 - 110999)** | | |
| | **Runner执行 (110000 - 110099)** | | |
| RUNNER_TERMINATION_ERROR | 110002 | Runner已终止。 | 检查Runner状态,重新初始化Runner实例。 |
| RUNNER_RUN_AGENT_ERROR | 110022 | Runner运行Agent失败。 | 根据错误信息排查Agent配置和执行环境。 |
| | **分布式执行 (110100 - 110199)** | | |
| REMOTE_AGENT_EXECUTION_TIMEOUT | 110100 | 远程Agent执行超时。 | 增加超时时间或优化Agent执行效率。 |
| REMOTE_AGENT_EXECUTION_ERROR | 110101 | 远程Agent执行错误。 | 检查远程Agent服务状态和网络连接。 |
| REMOTE_AGENT_RESPONSE_PROCESS_ERROR | 110102 | 远程Agent响应处理错误。 | 检查响应格式和错误码信息。 |
| | **消息队列 (110200 - 110299)** | | |
| MESSAGE_QUEUE_INITIATION_ERROR | 110200 | 消息队列初始化错误。 | 检查消息队列类型配置和连接参数。 |
| MESSAGE_QUEUE_TOPIC_SUBSCRIPTION_ERROR | 110210 | 订阅主题错误。 | 检查主题名称和订阅权限。 |
| MESSAGE_QUEUE_TOPIC_MESSAGE_PRODUCTION_ERROR | 110211 | 生产消息错误。 | 检查消息格式和主题配置。 |
| MESSAGE_QUEUE_MESSAGE_CONSUME_ERROR | 110212 | 消费消息错误。 | 检查消费者配置和消息处理逻辑。 |
| MESSAGE_QUEUE_MESSAGE_PROCESS_EXECUTION_ERROR | 110213 | 处理消息错误。 | 根据错误信息排查消息处理逻辑。 |
| | **分布式消息队列 (110300 - 110399)** | | |
| DIST_MESSAGE_QUEUE_CLIENT_START_ERROR | 110300 | 分布式消息队列客户端启动错误。 | 检查客户端配置和服务连接。 |
| | **资源管理器 (110400 - 110599)** | | |
| RESOURCE_ID_VALUE_INVALID | 110400 | 资源ID无效。 | 检查资源ID格式,确保符合命名规范。 |
| RESOURCE_TAG_VALUE_INVALID | 110401 | 标签无效。 | 检查标签格式和取值范围。 |
| RESOURCE_CARD_VALUE_INVALID | 110402 | 资源卡片无效。 | 检查资源卡片的必需字段(id/name/version)。 |
| RESOURCE_PROVIDER_INVALID | 110403 | 资源提供者无效。 | 检查资源提供者函数是否返回有效实例。 |
| RESOURCE_VALUE_INVALID | 110404 | 资源值无效。 | 检查资源实例的类型和配置。 |
| RESOURCE_ADD_ERROR | 110430 | 资源添加失败。 | 根据错误信息排查资源配置和注册逻辑。 |
| | **标签管理器 (110480 - 110499)** | | |
| RESOURCE_TAG_REMOVE_TAG_ERROR | 110480 | 移除标签错误。 | 检查标签是否存在。 |
| RESOURCE_TAG_ADD_RESOURCE_TAG_ERROR | 110481 | 添加资源标签失败。 | 检查资源ID和标签格式。 |
| RESOURCE_TAG_REMOVE_RESOURCE_TAG_ERROR | 110482 | 移除资源标签失败。 | 检查资源ID和标签是否存在。 |
| RESOURCE_TAG_REPLACE_RESOURCE_TAG_ERROR | 110483 | 替换资源标签失败。 | 检查资源ID和新标签配置。 |
| RESOURCE_TAG_FIND_RESOURCE_ERROR | 110484 | 查找资源失败。 | 检查查询条件和标签过滤器。 |
| | **MCP资源 (110510 - 110519)** | | |
| RESOURCE_MCP_SERVER_PARAM_INVALID | 110510 | MCP服务器参数无效。 | 检查server_config配置完整性。 |
| RESOURCE_MCP_SERVER_CONNECTION_ERROR | 110511 | MCP服务器连接失败。 | 检查服务器地址和网络连接。 |
| RESOURCE_MCP_SERVER_ADD_ERROR | 110512 | MCP服务器添加失败。 | 检查服务器配置和权限。 |
| RESOURCE_MCP_SERVER_REFRESH_ERROR | 110513 | MCP服务器刷新失败。 | 检查服务器状态和连接。 |
| RESOURCE_MCP_SERVER_REMOVE_ERROR | 110514 | MCP服务器移除失败。 | 检查服务器ID和依赖关系。 |
| RESOURCE_MCP_TOOL_GET_ERROR | 110515 | MCP服务器工具获取失败。 | 检查服务器ID和工具可用性。 |
| | **Session相关错误 (111000 - 111999)** | | |
| | **组件Session (111000 - 111009)** | | |
| COMP_SESSION_INTERACT_ERROR | 111005 | 组件Session交互不支持。 | 检查组件是否支持人机交互,流式接口不支持interact调用。 |
| | **交互 (111110 - 111119)** | | |
| INTERACTION_INPUT_INVALID | 111110 | 交互输入无效。 | 检查交互输入的id/value/raw_inputs字段。 |
| | **Checkpointer (111120 - 111129)** | | |
| CHECKPOINTER_POST_WORKFLOW_EXECUTION_ERROR | 111120 | Checkpointer工作流后置执行错误。 | 检查checkpointer配置和存储服务。 |
| CHECKPOINTER_PRE_WORKFLOW_EXECUTION_ERROR | 111121 | Checkpointer工作流前置执行错误。 | 检查checkpointer状态恢复逻辑。 |
| CHECKPOINTER_INTERRUPT_AGENT_ERROR | 111122 | Checkpointer中断Agent错误。 | 检查中断点保存逻辑。 |
| CHECKPOINTER_POST_AGENT_EXECUTION_ERROR | 111123 | Checkpointer Agent后置执行错误。 | 检查Agent状态持久化。 |
| CHECKPOINTER_CONFIG_ERROR | 111124 | Checkpointer配置错误。 | 检查checkpointer配置参数。 |
| | **Stream Writer (111130 - 111139)** | | |
| STREAM_WRITER_MANAGER_ADD_WRITER_ERROR | 111130 | 添加流写入器错误。 | 检查StreamMode配置。 |
| STREAM_WRITER_MANAGER_REMOVE_WRITER_ERROR | 111131 | 移除流写入器错误。 | 检查写入器是否存在。 |
| STREAM_WRITER_WRITE_STREAM_VALIDATION_ERROR | 111132 | 流数据校验错误。 | 检查流数据格式是否符合schema定义。 |
| STREAM_WRITER_WRITE_STREAM_ERROR | 111133 | 写入流数据错误。 | 检查流写入器状态和连接。 |
| STREAM_OUTPUT_FIRST_CHUNK_INTERVAL_TIMEOUT | 111134 | 流输出首块超时。 | 增加首块超时时间或检查生产端。 |
| STREAM_OUTPUT_CHUNK_INTERVAL_TIMEOUT | 111135 | 流输出块间隔超时。 | 增加块间隔超时时间或优化生产端。 |
| | **Tracer (111140 - 111149)** | | |
| TRACER_WORKFLOW_TRACE_ERROR | 111140 | 工作流追踪错误。 | 检查tracer配置和存储服务。 |
| TRACER_AGENT_TRACE_ERROR | 111141 | Agent追踪错误。 | 检查tracer配置和日志输出。 |
| | **Graph Engine相关错误 (112000 - 112999)** | | |
| | **Graph状态提交 (112030 - 112039)** | | |
| GRAPH_STATE_COMMIT_ERROR | 112030 | Graph状态提交错误。 | 检查状态管理和持久化逻辑。 |
| | **Drawable Graph (112020 - 112029)** | | |
| DRAWABLE_GRAPH_START_NODE_INVALID | 112020 | 可绘制图起始节点无效。 | 检查起始节点ID和配置。 |
| DRAWABLE_GRAPH_END_NODE_INVALID | 112021 | 可绘制图结束节点无效。 | 检查结束节点ID和配置。 |
| DRAWABLE_GRAPH_BREAK_NODE_INVALID | 112022 | 可绘制图中断节点无效。 | 检查中断节点ID和配置。 |
| DRAWABLE_GRAPH_TO_MERMAID_INVALID | 112043 | 可绘制图转Mermaid错误。 | 检查图结构和Mermaid语法。 |
| | **Stream Graph执行 (112030 - 112049)** | | |
| GRAPH_STREAM_ACTOR_EXECUTION_ERROR | 112030 | Actor管理器执行错误。 | 检查Actor配置和执行逻辑。 |
| | **Graph顶点执行 (112050 - 112069)** | | |
| GRAPH_VERTEX_EXECUTION_ERROR | 112050 | 顶点执行错误。 | 根据node_id排查顶点执行逻辑。 |
| GRAPH_VERTEX_STREAM_CALL_TIMEOUT | 112051 | 顶点流式调用超时。 | 增加超时时间或优化顶点执行。 |
| GRAPH_VERTEX_STREAM_CALL_ERROR | 112052 | 顶点流式调用错误。 | 检查顶点流式处理逻辑。 |
| | **Pregel Graph (112100 - 112199)** | | |
| PREGEL_GRAPH_NODE_ID_INVALID | 112100 | Pregel节点ID无效。 | 检查节点ID唯一性和格式。 |
| PREGEL_GRAPH_NODE_INVALID | 112101 | Pregel节点无效。 | 检查节点配置和类型。 |
| PREGEL_GRAPH_EDGE_INVALID | 112102 | Pregel边无效。 | 检查边的起止节点存在性。 |
| PREGEL_GRAPH_CONDITION_EDGE_INVALID | 112103 | Pregel条件边无效。 | 检查条件边的条件表达式。 |
| | **Multi-Agent相关错误 (130000 - 139999)** | | |
| AGENT_GROUP_ADD_RUNTIME_ERROR | 132000 | Agent组添加运行时错误。 | 根据错误信息检查Agent配置。 |
| AGENT_GROUP_CREATE_RUNTIME_ERROR | 132001 | Agent组创建运行时错误。 | 检查Agent组配置和成员。 |
| AGENT_GROUP_EXECUTION_ERROR | 132002 | Agent组执行错误。 | 根据错误信息排查组执行逻辑。 |
| | **ContextEngine相关错误 (150000 - 154999)** | | |
| CONTEXT_MESSAGE_PROCESS_ERROR | 153000 | 上下文消息处理错误。 | 检查消息格式是否符合规范。 |
| CONTEXT_EXECUTION_ERROR | 153001 | 上下文执行错误。 | 检查上下文引擎配置。 |
| CONTEXT_MESSAGE_INVALID | 153003 | 上下文消息无效。 | 检查消息内容和格式。 |
| | **KnowledgeBase Retrieval相关错误 (155000 - 157999)** | | |
| | **Embedding (155000 - 155099)** | | |
| RETRIEVAL_EMBEDDING_INPUT_INVALID | 155000 | 检索embedding输入无效。 | 检查输入文本是否为空。 |
| RETRIEVAL_EMBEDDING_MODEL_NOT_FOUND | 155001 | 检索embedding模型未找到。 | 检查模型名称和配置。 |
| RETRIEVAL_EMBEDDING_CALL_FAILED | 155002 | 检索embedding调用失败。 | 检查模型服务连接和配置。 |
| RETRIEVAL_EMBEDDING_RESPONSE_INVALID | 155003 | 检索embedding响应无效。 | 检查响应格式和数据结构。 |
| RETRIEVAL_EMBEDDING_REQUEST_CALL_FAILED | 155004 | 检索embedding请求调用失败。 | 增加重试次数或检查网络连接。 |
| RETRIEVAL_EMBEDDING_UNREACHABLE_CALL_FAILED | 155005 | 检索embedding不可达调用失败。 | 检查代码逻辑和输入数据。 |
| RETRIEVAL_EMBEDDING_CALLBACK_INVALID | 155006 | 检索embedding回调无效。 | 检查回调函数配置。 |
| | **Indexing (155100 - 155199)** | | |
| RETRIEVAL_INDEXING_CHUNK_SIZE_INVALID | 155100 | 检索索引chunk_size无效。 | 确保chunk_size大于0且在合理范围(100-2000)。 |
| RETRIEVAL_INDEXING_CHUNK_OVERLAP_INVALID | 155101 | 检索索引chunk_overlap无效。 | 确保chunk_overlap >= 0且小于chunk_size。 |
| RETRIEVAL_INDEXING_TOKENIZER_PROCESS_ERROR | 155102 | 检索索引分词器处理错误。 | 检查分词器初始化和配置。 |
| RETRIEVAL_INDEXING_FILE_NOT_FOUND | 155103 | 检索索引文件未找到。 | 检查文件路径和权限。 |
| RETRIEVAL_INDEXING_FORMAT_NOT_SUPPORT | 155104 | 检索索引格式不支持。 | 检查文件格式是否在支持列表中。 |
| RETRIEVAL_INDEXING_EMBED_MODEL_NOT_FOUND | 155105 | 检索索引embedding模型未找到。 | 提供有效的embedding模型配置。 |
| RETRIEVAL_INDEXING_DIMENSION_NOT_FOUND | 155106 | 检索索引维度未找到。 | 提供向量维度参数。 |
| RETRIEVAL_INDEXING_PATH_NOT_FOUND | 155107 | 检索索引路径未找到。 | 提供有效的索引路径。 |
| RETRIEVAL_INDEXING_ADD_DOC_RUNTIME_ERROR | 155108 | 检索索引添加文档运行时错误。 | 根据错误信息排查文档处理逻辑。 |
| RETRIEVAL_INDEXING_VECTOR_FIELD_INVALID | 155109 | 检索索引向量字段无效。 | 检查向量字段配置。 |
| | **Retriever (155200 - 155299)** | | |
| RETRIEVAL_RETRIEVER_MODE_NOT_SUPPORT | 155200 | 检索器模式不支持。 | 使用支持的检索模式(vector/keyword/hybrid)。 |
| RETRIEVAL_RETRIEVER_SCORE_THRESHOLD_INVALID | 155201 | 检索器分数阈值无效。 | 确保检索模式为vector时使用分数阈值。 |
| RETRIEVAL_RETRIEVER_EMBED_MODEL_NOT_FOUND | 155202 | 检索器embedding模型未找到。 | 提供有效的embedding模型配置。 |
| RETRIEVAL_RETRIEVER_INDEX_TYPE_NOT_SUPPORT | 155203 | 检索器索引类型不支持。 | 检查索引类型是否在支持列表中。 |
| RETRIEVAL_RETRIEVER_MODE_INVALID | 155204 | 检索器模式无效。 | 确保检索模式与索引类型兼容。 |
| RETRIEVAL_RETRIEVER_CAPABILITY_NOT_SUPPORT | 155205 | 检索器能力不支持。 | 使用检索器支持的检索模式。 |
| RETRIEVAL_RETRIEVER_VECTOR_STORE_NOT_FOUND | 155206 | 检索器向量存储未找到。 | 提供有效的向量存储实例。 |
| RETRIEVAL_RETRIEVER_COLLECTION_NOT_FOUND | 155207 | 检索器集合未找到。 | 提供有效的集合名称。 |
| RETRIEVAL_RETRIEVER_NOT_FOUND | 155208 | 检索器未找到。 | 提供有效的检索器实例。 |
| RETRIEVAL_RETRIEVER_LLM_CLIENT_NOT_FOUND | 155209 | 检索器LLM客户端未找到。 | 提供有效的LLM客户端实例。 |
| RETRIEVAL_RETRIEVER_TOP_K_INVALID | 155210 | 检索器top_k无效。 | 提供有效的top_k参数（正整数）。 |
| RETRIEVAL_RETRIEVER_INVALID | 155211 | 检索器无效 | 检查检索器配置和初始化，查看错误信息。 |
| | **Utils (155300 - 155399)** | | |
| RETRIEVAL_UTILS_CONFIG_FILE_NOT_FOUND | 155300 | 检索工具配置文件未找到。 | 检查配置文件路径和权限。 |
| RETRIEVAL_UTILS_PYYAML_NOT_FOUND | 155301 | 检索工具PyYAML未找到。 | 安装PyYAML: pip install PyYAML。 |
| RETRIEVAL_UTILS_CONFIG_FORMAT_NOT_SUPPORT | 155302 | 检索工具配置格式不支持。 | 使用支持的配置格式(YAML/JSON)。 |
| RETRIEVAL_UTILS_CONFIG_NOT_FOUND | 155303 | 检索工具配置未找到。 | 在访问配置前先加载配置文件。 |
| RETRIEVAL_UTILS_CONFIG_PROCESS_ERROR | 155304 | 检索工具配置处理错误。 | 检查配置文件格式和内容。 |
| | **Vector Store (155400 - 155499)** | | |
| RETRIEVAL_VECTOR_STORE_PATH_NOT_FOUND | 155400 | 检索向量存储路径未找到。 | 提供有效的存储路径。 |
| RETRIEVAL_VECTOR_STORE_QUERY_INVALID | 155400 | 检索向量存储查询无效。 | 检查查询参数格式。 |
| | **Knowledge Base (155500 - 155599)** | | |
| RETRIEVAL_KB_PARSER_NOT_FOUND | 155500 | 检索知识库解析器未找到。 | 提供有效的解析器实例。 |
| RETRIEVAL_KB_CHUNKER_NOT_FOUND | 155501 | 检索知识库分块器未找到。 | 提供有效的分块器实例。 |
| RETRIEVAL_KB_INDEX_MANAGER_NOT_FOUND | 155502 | 检索知识库索引管理器未找到。 | 提供有效的索引管理器实例。 |
| RETRIEVAL_KB_VECTOR_STORE_NOT_FOUND | 155503 | 检索知识库向量存储未找到。 | 提供有效的向量存储实例。 |
| RETRIEVAL_KB_INDEX_BUILD_EXECUTION_ERROR | 155504 | 检索知识库索引构建执行错误。 | 检查输入文档和配置。 |
| RETRIEVAL_KB_CHUNK_INDEX_BUILD_EXECUTION_ERROR | 155505 | 检索知识库分块索引构建执行错误。 | 检查分块器配置和结果。 |
| RETRIEVAL_KB_TRIPLE_INDEX_BUILD_EXECUTION_ERROR | 155506 | 检索知识库三元组索引构建执行错误。 | 检查三元组提取逻辑。 |
| RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR | 155507 | 检索知识库三元组提取处理错误。 | 检查LLM配置和提取逻辑。 |
| RETRIEVAL_KB_DATABASE_CONFIG_INVALID | 155508 | 检索知识库数据库配置无效。 | 检查数据库连接配置。 |
| | **Reranker (155600 - 155699)** | | |
| RETRIEVAL_RERANKER_REQUEST_CALL_FAILED | 155600 | 检索重排序请求调用失败。 | 检查重排序服务连接。 |
| RETRIEVAL_RERANKER_UNREACHABLE_CALL_FAILED | 155601 | 检索重排序不可达调用失败。 | 检查服务地址和网络连接。 |
| RETRIEVAL_RERANKER_INPUT_INVALID | 155602 | 检索重排序输入无效。 | 检查输入格式和数据。 |
| | **Memory Engine相关错误 (158000 - 159999)** | | |
| MEMORY_REGISTER_STORE_EXECUTION_ERROR | 158000 | 内存注册存储执行错误。 | 检查存储类型配置。 |
| MEMORY_SET_CONFIG_EXECUTION_ERROR | 158001 | 内存设置配置执行错误。 | 检查配置参数。 |
| MEMORY_ADD_MEMORY_EXECUTION_ERROR | 158002 | 内存添加执行错误。 | 检查内存数据格式。 |
| MEMORY_DELETE_MEMORY_EXECUTION_ERROR | 158003 | 内存删除执行错误。 | 检查内存ID。 |
| MEMORY_UPDATE_MEMORY_EXECUTION_ERROR | 158004 | 内存更新执行错误。 | 检查更新数据。 |
| MEMORY_GET_MEMORY_EXECUTION_ERROR | 158005 | 内存获取执行错误。 | 检查查询条件。 |
| MEMORY_STORE_INIT_FAILED | 158006 | 内存存储初始化失败。 | 检查存储配置和连接。 |
| MEMORY_CONNECT_STORE_EXECUTION_ERROR | 158007 | 内存连接存储执行错误。 | 检查存储服务可用性。 |
| MEMORY_STORE_VALIDATION_INVALID | 158008 | 内存存储验证无效。 | 检查存储配置参数。 |
| MEMORY_REGISTER_OPERATION_VALIDATION_INVALID | 158009 | 内存迁移操作注册失败。             | 检查实体键和版本号配置。 |
| MEMORY_MIGRATE_MEMORY_EXECUTION_ERROR | 158010 | 内存迁移执行错误。                 | 检查迁移配置和数据。 |
| | **Foundation Tool相关错误 (160000 - 169999)** | | |
| (预留) | | | |
| | **Optimization Toolchain相关错误 (170000 - 179999)** | | |
| | **Prompt Self-optimization (170000 - 170999)** | | |
| TOOLCHAIN_AGENT_PARAM_ERROR | 170000 | 工具链Agent参数错误。 | 检查参数类型和取值范围。 |
| TOOLCHAIN_OPTIMIZER_BACKWARD_EXECUTION_ERROR | 170001 | 工具链优化器反向执行错误。 | 检查优化器配置和模型。 |
| TOOLCHAIN_OPTIMIZER_UPDATE_EXECUTION_ERROR | 170002 | 工具链优化器更新执行错误。 | 检查优化器参数更新逻辑。 |
| TOOLCHAIN_OPTIMIZER_PARAM_ERROR | 170003 | 工具链优化器参数错误。 | 检查优化器超参数配置。 |
| TOOLCHAIN_EVALUATOR_EXECUTION_ERROR | 170004 | 工具链评估器执行错误。 | 检查评估器配置和模型。 |
| TOOLCHAIN_TRAINER_EXECUTION_ERROR | 170005 | 工具链训练器执行错误。 | 检查训练器配置和模型。 |
| | **Prompt Builder (173000 - 173999)** | | |
| TOOLCHAIN_META_TEMPLATE_EXECUTION_ERROR | 173000 | 工具链元模板执行错误。 | 检查元模板类型和配置。 |
| TOOLCHAIN_FEEDBACK_TEMPLATE_EXECUTION_ERROR | 173001 | 工具链反馈模板执行错误。 | 检查反馈优化配置和模型。 |
| TOOLCHAIN_BAD_CASE_TEMPLATE_EXECUTION_ERROR | 173002 | 工具链错误案例模板执行错误。 | 检查错误案例配置和模型。 |
| | **Foundation相关错误 (180000 - 189999)** | | |
| | **Prompt Template (180000 - 180999)** | | |
| PROMPT_ASSEMBLER_VARIABLE_INIT_FAILED | 180000 | 提示词组装器变量初始化失败。 | 检查变量名是否存在于模板中。 |
| PROMPT_ASSEMBLER_TEMPLATE_PARAM_ERROR | 180001 | 提示词组装器模板参数错误。 | 检查变量值是否与模板匹配。 |
| PROMPT_TEMPLATE_RUNTIME_ERROR | 180002 | 提示词模板运行时错误。 | 检查模板名称和过滤条件是否重复。 |
| PROMPT_TEMPLATE_NOT_FOUND | 180003 | 提示词模板未找到。 | 检查模板名称和过滤条件是否存在。 |
| PROMPT_TEMPLATE_INVALID | 180004 | 提示词模板无效。 | 检查模板名称和内容类型。 |
| | **Model API (181000 - 181999)** | | |
| MODEL_PROVIDER_INVALID | 181000 | 模型提供商无效。 | 检查MODEL_PROVIDER配置,支持openai/siliconflow等。 |
| MODEL_CALL_FAILED | 181001 | 模型调用失败。 | 检查API_KEY/API_BASE配置和网络连接。 |
| MODEL_SERVICE_CONFIG_ERROR | 181002 | 模型服务配置错误。 | 检查ModelClientConfig配置完整性。 |
| MODEL_CONFIG_ERROR | 181003 | 模型配置错误。 | 检查ModelRequestConfig配置。 |
| MODEL_INVOKE_PARAM_ERROR | 181004 | 模型调用参数错误。 | 检查调用参数格式和类型。 |
| MODEL_CLIENT_CONFIG_INVALID | 181005 | 模型客户端配置无效。 | 检查客户端配置参数。 |
| | **Tool Definition and Execution (182000 - 182999)** | | |
| | **基础Tool (182000 - 182099)** | | |
| TOOP_CARD_INVALID | 182000 | 工具卡片无效。 | 检查ToolCard配置的必需字段。 |
| TOOL_STREAM_NOT_SUPPORTED | 182010 | 工具不支持流式。 | 使用invoke方法代替stream。 |
| TOOL_INVOKE_NOT_SUPPORTED | 182011 | 工具不支持invoke。 | 检查工具实现。 |
| TOOL_EXECUTION_ERROR | 182012 | 工具执行错误。 | 根据错误信息排查工具逻辑。 |
| | **RESTful API (182100 - 182199)** | | |
| TOOL_RESTFUL_API_CARD_CONFIG_INVALID | 182100 | RESTful API配置无效。 | 检查API配置参数。 |
| TOOL_RESTFUL_API_EXECUTION_TIMEOUT | 182101 | RESTful API执行超时。 | 增加timeout参数。 |
| TOOL_RESTFUL_API_RESPONSE_SIZE_EXCEED_LIMIT | 182102 | RESTful API响应大小超限。 | 调整max_length参数或使用分页。 |
| TOOL_RESTFUL_API_RESPONSE_ERROR | 182103 | RESTful API响应错误。 | 检查HTTP状态码和错误信息。 |
| TOOL_RESTFUL_API_EXECUTION_ERROR | 182104 | RESTful API执行错误。 | 检查请求参数和服务可用性。 |
| TOOL_RESTFUL_API_RESPONSE_PROCESS_ERROR | 182105 | RESTful API响应处理错误。 | 检查响应格式和解析逻辑。 |
| | **Local Function (182200 - 182299)** | | |
| TOOL_LOCAL_FUNCTION_FUNC_NOT_SUPPORTED | 182200 | 本地函数不支持。 | 检查函数签名和注册。 |
| TOOL_LOCAL_FUNCTION_EXECUTION_ERROR | 182205 | 本地函数执行错误。 | 检查函数实现和输入参数。 |
| | **MCP Tool (182300 - 182399)** | | |
| TOOL_MCP_CLIENT_NOT_SUPPORTED | 182300 | MCP客户端不支持。 | 检查MCP客户端配置。 |
| TOOL_MCP_EXECUTION_ERROR | 182301 | MCP执行错误。 | 检查MCP服务连接和调用。 |
| | **OpenAPI Tool (182400 - 182499)** | | |
| TOOL_OPENAPI_CLIENT_EXECUTION_ERROR | 182400 | OpenAPI客户端执行错误。 | 检查OpenAPI规范和客户端配置。 |
| | **Logger (183000 - 183999)** | | |
| COMMON_LOG_PATH_INVALID | 183000 | 日志路径无效。 | 检查日志路径中是否包含非法参数。 |
| COMMON_LOG_PATH_INIT_FAILED | 183001 | 日志路径初始化失败。 | 检查日志路径是否合法和权限。 |
| COMMON_LOG_CONFIG_PROCESS_ERROR | 183002 | 日志配置处理错误。 | 检查日志配置文件格式。 |
| COMMON_LOG_CONFIG_INVALID | 183003 | 日志配置无效。 | 检查日志配置参数。 |
| COMMON_LOG_EXECUTION_RUNTIME_ERROR | 183004 | 日志执行运行时错误。 | 检查日志操作是否合法。 |
| | **Store Supporting (186000 - 186100)** | | |
| STORE_VECTOR_FIELD_DIM_INVALID | 186000 | 向量字段维度无效。 | 检查向量维度配置。 |
| STORE_VECTOR_FIELD_DIM_MISSING | 186001 | 向量字段维度缺失。 | 提供向量维度参数。 |
| STORE_VECTOR_PRIMARY_KEY_FIELD_DUPLICATED | 186002 | 向量主键字段重复。 | 确保集合只有一个主键字段。 |
| STORE_VECTOR_FIELD_NAME_DUPLICATED | 186003 | 向量字段名称重复。 | 检查字段名称唯一性。 |
| STORE_VECTOR_COLLECTION_NOT_EXIST | 186004 | 向量集合不存在。 | 创建集合或检查集合名称。 |
| STORE_VECTOR_SCHEMA_MISSING_PRIMARY_KEY | 186005 | 向量schema缺少主键。 | 在schema中添加主键字段(is_primary=True)。 |
| STORE_VECTOR_SCHEMA_MISSING_VECTOR_FIELD | 186006 | 向量schema缺少向量字段。 | 在schema中添加FLOAT_VECTOR字段。 |
| STORE_VECTOR_DOC_MISSING_PRIMARY_KEY | 186007 | 向量文档缺少主键。 | 确保文档包含主键字段。 |
| STORE_VECTOR_DOC_MISSING_VECTOR_FIELD | 186008 | 向量文档缺少向量字段。 | 确保文档包含向量字段。 |
| | **Common Utility (188000 - 188999)** | | |
| COMMON_SSL_CONTEXT_INIT_FAILED | 188000 | SSL上下文初始化失败。 | 检查SSL配置参数。 |
| COMMON_USER_CONFIG_PROCESS_ERROR | 188001 | 用户配置处理错误。 | 检查配置文件格式。 |
| COMMON_JSON_INPUT_PROCESS_ERROR | 188002 | JSON输入处理错误。 | 检查JSON字符串格式。 |
| COMMON_JSON_EXECUTION_PROCESS_ERROR | 188003 | JSON执行处理错误。 | 检查JSON结构体合法性。 |
| COMMON_URL_INPUT_INVALID | 188004 | URL输入无效。 | 检查URL格式和可达性。 |
| COMMON_SSL_CERT_INVALID | 188005 | SSL证书无效。 | 配置本地证书路径或关闭verify开关。 |
| | **Schema (189000 - 189999)** | | |
| SCHEMA_VALIDATE_INVALID | 189001 | Schema验证无效。 | 检查数据是否符合schema定义。 |
| SCHEMA_FORMAT_INVALID | 189002 | Schema格式无效。 | 检查数据格式化逻辑。 |
| | **SysOperation相关错误 (199000 - 199999)** | | |
| SYS_OPERATION_MANAGER_PROCESS_ERROR | 199001 | 系统操作管理器处理错误。 | 根据错误信息排查操作管理逻辑。 |
| SYS_OPERATION_CARD_PARAM_ERROR | 199002 | 系统操作卡片参数错误。 | 检查操作卡片配置。 |
| SYS_OPERATION_FS_EXECUTION_ERROR | 199003 | 文件系统操作执行错误。 | 检查文件路径和权限。 |
| SYS_OPERATION_SHELL_EXECUTION_ERROR | 199004 | Shell操作执行错误。 | 检查Shell命令和执行环境。 |
| SYS_OPERATION_CODE_EXECUTION_ERROR | 199005 | 代码操作执行错误。 | 检查代码执行逻辑和环境。 |
| SYS_OPERATION_REGISTRY_ERROR | 199006 | 系统操作注册错误。 | 检查注册逻辑和配置。 |