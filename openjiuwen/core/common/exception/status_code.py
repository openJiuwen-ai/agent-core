# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from enum import Enum

from openjiuwen.core.common.exception.code_template import generate_error_message_template


class StatusCode(Enum):
    """Status code enum"""

    SUCCESS = (0, "success")
    ERROR = (-1, "error")

    # Util Error
    COMMON_SCHEMA_INVALID = (90000, "common schema is invalid, reason: {error_msg}")
    COMMON_SCHEMA_CONFIG_ERROR = (90001, "common schema config error, reason: {error_msg}")

    # Workflow Component  100000 - 109999

    # Workflow: Interactive And Recovery 100000 - 100029
    WORKFLOW_INPUT_INVALID = (100000, "workflow input is invalid, reason: {error_msg}")
    WORKFLOW_STATE_RUNTIME_ERROR = (100001, "workflow state runtime error, reason: {error_msg}")
    WORKFLOW_EXECUTION_NOT_SUPPORT = (100002, "workflow execution is not supported, reason: {error_msg}")
    WORKFLOW_INTERRUPT_EXECUTION_ERROR = (100003, "workflow interrupt execution error, reason: {error_msg}")
    WORKFLOW_STREAM_NOT_SUPPORT = (100004, "workflow stream is not supported, reason: {error_msg}")
    WORKFLOW_COMPONENT_RUNTIME_ERROR = (100005, "workflow component runtime error, reason: {error_msg}")
    WORKFLOW_STATE_INVALID = (100006, "workflow state is invalid, reason: {error_msg}")

    # Workflow: Execution 100100 - 100199
    WORKFLOW_EXECUTION_RUNTIME_ERROR = (100100, "workflow execution runtime error, reason: {error_msg}")
    WORKFLOW_INVOKE_TIMEOUT = (100101, "workflow invoke timeout ({timeout}s), reason: {error_msg}")
    WORKFLOW_STREAM_EXECUTION_TIMEOUT = (100102, "workflow stream_execution timeout ({timeout}s), reason: {error_msg}")

    # Workflow Component - Builtin-workflow Component 101000 - 109999

    ## LLMComponent  101000 - 101049
    COMPONENT_LLM_TEMPLATE_CONFIG_ERROR = (101000, "component llm_template config error, reason: {error_msg}")
    COMPONENT_LLM_CONFIG_INVALID = (101001, "component llm_config is invalid, reason: {error_msg}")
    COMPONENT_LLM_CONFIG_ERROR = (101002, "component llm config error, reason: {error_msg}")
    COMPONENT_LLM_INVOKE_CALL_FAILED = (101003, "component llm_invoke call failed, reason: {error_msg}")
    COMPONENT_LLM_EXECUTION_PROCESS_ERROR = (101004, "component llm_execution process error, reason: {error_msg}")
    COMPONENT_LLM_INIT_FAILED = (101005, "component llm initialization failed, reason: {error_msg}")
    COMPONENT_LLM_TEMPLATE_PROCESS_ERROR = (101006, "component llm_template process error, reason: {error_msg}")
    COMPONENT_LLM_TEMPLATE_INVALID = (101007, "component llm_template is invalid, reason: {error_msg}")


    ## LLM Service 102001 - 102999
    MODEL_SERVICE_CONFIG_ERROR = (102001, "model service config error, reason: {error_msg}")
    MODEL_CONFIG_ERROR = (102002, "model config error, reason: {error_msg}")
    MODEL_INVOKE_PARAM_ERROR = (102003, "model invoke parameter error, reason: {error_msg}")
    MODEL_CLIENT_CONFIG_INVALID = (102004, "model client_config is invalid, reason: {error_msg}")

    ## IntentDetectionComponent 101050 - 101069
    COMPONENT_INTENT_DETECTION_INPUT_PARAM_ERROR = (101050,
        "component intent_detection_input parameter error, reason: {error_msg}")
    COMPONENT_INTENT_DETECTION_LLM_INIT_FAILED = (101051,
        "component intent_detection_llm initialization failed, reason: {error_msg}")
    COMPONENT_INTENT_DETECTION_INVOKE_CALL_FAILED = (101052,
                                                   "component intent_detection_invoke call failed, reason: {error_msg}")

    ## QuestionComponent 101070 - 101099
    COMPONENT_QUESTIONER_INPUT_PARAM_ERROR = (101070,
        "component questioner_input parameter error, reason: {error_msg}")
    COMPONENT_QUESTIONER_CONFIG_ERROR = (101071, "component questioner config error, reason: {error_msg}")
    COMPONENT_QUESTIONER_INPUT_INVALID = (101072, "component questioner_input is invalid, reason: {error_msg}")
    COMPONENT_QUESTIONER_STATE_INIT_FAILED = (101073,
        "component questioner_state initialization failed, reason: {error_msg}")
    COMPONENT_QUESTIONER_RUNTIME_ERROR = (101074, "component questioner runtime error, reason: {error_msg}")
    COMPONENT_QUESTIONER_INVOKE_CALL_FAILED = (101075, "component questioner_invoke call failed, reason: {error_msg}")
    COMPONENT_QUESTIONER_EXECUTION_PROCESS_ERROR = (101076,
        "component questioner_execution process error, reason: {error_msg}")

    ## BranchComponent  101100 - 101119
    COMPONENT_BRANCH_PARAM_ERROR = (101100, "component branch parameter error, reason: {error_msg}")
    COMPONENT_BRANCH_EXECUTION_ERROR = (101101, "component branch execution error, reason: {error_msg}")

    ## SetVariableComponent  101120 - 101139
    COMPONENT_SET_VAR_INPUT_PARAM_ERROR = (101120, "component set_var_input parameter error, reason: {error_msg}")
    COMPONENT_SET_VAR_INIT_FAILED = (101121, "component set_var initialization failed, reason: {error_msg}")

    ## SubWorkflowComponent  101140 - 101149
    COMPONENT_SUB_WORKFLOW_INIT_FAILED = (101140, "component sub_workflow initialization failed, reason: {error_msg}")
    COMPONENT_SUB_WORKFLOW_RUNTIME_ERROR = (101141, "component sub_workflow runtime error, reason: {error_msg}")

    ## LoopComponent  101150 - 101159
    COMPONENT_LOOP_NOT_SUPPORT = (101150, "component loop is not supported, reason: {error_msg}")
    COMPONENT_LOOP_EXECUTION_ERROR = (101151, "component loop execution error, reason: {error_msg}")
    COMPONENT_LOOP_INPUT_INVALID = (101152, "component loop_input is invalid, reason: {error_msg}")
    COMPONENT_LOOP_CONFIG_ERROR = (101153, "component loop config error, reason: {error_msg}")

    ## BreakComponent  101180 - 101189
    COMPONENT_BREAK_EXECUTION_ERROR = (101180, "component break execution error, reason: {error_msg}")

    ## ToolComponent  102000 - 102019
    COMPONENT_TOOL_EXECUTION_ERROR = (102000, "component tool execution error, reason: {error_msg}")
    COMPONENT_TOOL_INPUT_PARAM_ERROR = (102001, "component tool_input parameter error, reason: {error_msg}")

    ## StartComponent  102100 - 102119
    COMPONENT_START_INPUT_INVALID = (102100, "component start_input is invalid, reason: {error_msg}")
    COMPONENT_START_CONFIG_ERROR = (102101, "component start config error, reason: {error_msg}")

    ## EndComponent  102120 - 102149
    COMPONENT_END_INIT_FAILED = (102120, "component end initialization failed, reason: {error_msg}")

    # Workflow 110000 - 119999
    # Workflow - Orchestration And Execution 110000 - 110999
    GRAPH_SET_START_NODE_FAILED = (110001, "Graph create error, caused by start node set failed, detail: {detail}")
    GRAPH_SET_END_NODE_FAILED = (110002, "Graph create error, caused by end node set failed, detail: {detail}")
    GRAPH_ADD_NODE_FAILED = (110003, "Graph create error, caused by add node failed, detail: {detail}")
    GRAPH_ADD_EDGE_FAILED = (110004, "Graph create error, caused by add edge failed, detail: {detail}")
    GRAPH_ADD_CONDITION_EDGE_FAILED = (110005,
                                       "Graph create error, caused by add conditional edge failed, detail: {detail}")
    WORKFLOW_COMPONENT_CONFIG_ERROR = (110006, "Workflow component config error: {error_msg}")
    DRAWABLE_GRAPH_SET_START_NODE_FAILED = (110021, "Drawable Graph create error, caused by start node set failed, "\
                                                    "node id: {node_id}")
    DRAWABLE_GRAPH_SET_END_NODE_FAILED = (110022, "Drawable Graph create error, caused by end node set failed, "\
                                                    "node id: {node_id}")
    DRAWABLE_GRAPH_SET_BREAK_NODE_FAILED = (110023, "Drawable Graph create error, caused by break node set failed, "\
                                                    "node id: {node_id}")
    DRAWABLE_GRAPH_INVALID_TITLE = (110024, "Invalid value of argument 'title', expected a str")
    DRAWABLE_GRAPH_INVALID_EXPAND_SUBGRAPH = (110025, "Invalid value of argument 'expand_subgraph', "\
                                              "expected a boolean or a non-negative integer")
    DRAWABLE_GRAPH_INVALID_ENABLE_ANIMATION = (110026, "Invalid value of argument 'enable_animation', "\
                                              "expected a boolean")

    # Workflow - Exception Handling 111000 - 111999
    # Agent Orchestration 120000 - 129999
    # Agent Orchestration - ReAct Agent Orchestration And Execution 120000 - 120999
    TOOL_NOT_FOUND_ERROR = (120000, "Tool not found error")
    TOOL_EXECUTION_ERROR = (120001, "Tool execution error: {msg}")
    TASK_NOT_SUPPORT_ERROR = (120002, "Get unknown task type: {msg}")
    WORKFLOW_EXECUTION_ERROR = (120003, "Workflow execution error: {msg}")
    PROMPT_PARAMS_CHECK_ERROR = (120004, "Prompt params check error: {msg}")
    # Agent Orchestration - Workflow Agent Orchestration And Execution 121000 - 121999
    # Agent Orchestration - Custom Agent Interface 122000 - 122999
    # Agent Controller 123000 - 123999
    CONTROLLER_INVOKE_LLM_FAILED = (123000, "Controller failed to call model, the reason is {error_msg}")
    AGENT_SUB_TASK_TYPE_ERROR = (123001, "SubTask type {msg} is not supported")
    CONTROLLER_HANDLE_USER_INPUT_ERROR = (123002, "ReAct Agent failed to handle user input, the reason is {error_msg}")
    CONTROLLER_RUNTIME_ERROR = (123003, "Controller runtime error, the reason is {error_msg}")
    CONTROLLER_SEND_STREAM_FAILED = (123004, "Controller failed to send stream, the reason is {error_msg}")
    CONTROLLER_PARSE_TOOL_CALL_ERROR = (123005, "Controller failed to parse tool call, the reason is {error_msg}")

    # Multi-Agent Orchestration 130000 - 139999
    # Multi-Agent Orchestration - Multi-Agent Communication  130000 - 130999
    # Multi-Agent Orchestration - Single Session 131000 - 131999
    # Multi-Agent Orchestration - AgentGroup 132000 - 132999
    AGENT_GROUP_ADD_FAILED = (132000, "failed to add single_agent, reason: {reason}")
    AGENT_GROUP_CREATE_FAILED = (132001, "failed to create single_agent group, reason: {reason}")
    AGENT_GROUP_EXECUTION_ERROR = (132002, "failed to execute single_agent group, reason: {reason}")

    # Multi-Agent Orchestration - Multi-Agent Debug 133000 - 133999
    # Multi-Agent Orchestration - Distribution Session 134000 - 134999
    # Multi-Agent Orchestration - Multi-Agent Runner 131000-131030

    # Runner 134000 - 134999
    REMOTE_AGENT_REQUEST_TIMEOUT = (134001, "RemoteAgent {} request timeout")
    AGENT_NOT_FOUND = (134002, "Agent {} is not found")
    WORKFLOW_NOT_BOUND_TO_AGENT = (134003, "workflow not bound to single_agent")
    TOOL_NOT_BOUND_TO_AGENT = (134004, "tool not bound to single_agent")
    TOOL_NOT_FOUND = (134005, "Tool not found")
    RUNNER_DISTRIBUTED_MODE_REQUIRED = (134006, "Runner must be initialized with distributed_mode enabled. message: {}")
    RUNNER_STOPPED = (134007, "Runner not running: {}")
    REMOTE_AGENT_REQUEST_CANCELLED = (134008, "Remote single_agent request cancelled: {}")
    REMOTE_AGENT_PROCESS_ERROR = (134009, "Remote single_agent process error. code: {error_code}, message: {error_msg}")
    # Runner Dmq 134100 - 134199
    MESSAGE_QUEUE_NOT_RUNNING = (134101, "Message queue is not running: {}")
    MESSAGE_QUEUE_INIT_ERROR = (134102, "Message queue init error: {}")

    # GraphEngine 140000 - 149999
    # GraphEngine - Graph Orchestration and Execution 140000 - 140999
    # GraphEngine - Conditional Evaluation 140000 - 140019
    EXPRESSION_CONDITION_SYNTAX_ERROR = (140000, "Expression condition has syntax error,"
                                         " expression as {expression}, error as {error_msg}.")
    EXPRESSION_CONDITION_EVAL_ERROR = (140001, "Expression condition eval error, as {error_msg}.")
    ARRAY_CONDITION_ERROR = (140002, "Array condition error")
    NUMBER_CONDITION_ERROR = (140003, "Number condition error")


    # ContextEngine 150000 - 154999
    # ContextEngine - Context Structured Storage and Retrieval 150000 - 150999
    # ContextEngine - Context Dynamic Assembly  151000 - 151999
    # ContextEngine - Context Asynchronous Processing 152000 - 152999
    # ContextEngine - Context Common 153000 - 153999
    CONTEXT_ENGINE_MESSAGE_PROCESS_ERROR = (153000, "Message process error: {error_msg}")
    CONTEXT_ENGINE_ADD_MESSAGE_ERROR = (153001, "Message add message error: {error_msg}")
    CONTEXT_ENGINE_GET_MESSAGE_ERROR = (153002, "Message get message error: {error_msg}")
    CONTEXT_ENGINE_POP_MESSAGE_ERROR = (153003, "Message pop message error: {error_msg}")
    CONTEXT_ENGINE_GET_CONTEXT_WINDOW_ERROR = (153004, "Message get context window error: {error_msg}")
    CONTEXT_ENGINE_MESSAGE_VALIDATION_ERROR = (153005, "Context engine message validation error: {error_msg}")

    # KnowledgeBase Retrieval 155000 - 157999
    # KnowledgeBase Retrieval - Embedding 155000 - 155099
    EMBEDDING_EMPTY_INPUT_ERROR = (155000, "Empty text or texts list provided for embedding")
    EMBEDDING_MODEL_NOT_FOUND_ERROR = (155001, "Embedding model not found: {error_msg}")
    EMBEDDING_CONNECTION_ERROR = (155002, "Failed to connect to embedding service: {error_msg}")
    EMBEDDING_RESPONSE_FORMAT_ERROR = (155003, "Invalid embedding response format: {error_msg}")
    EMBEDDING_REQUEST_FAILED_ERROR = (155004, "Failed to get embedding after {max_retries} attempts: {error_msg}")
    EMBEDDING_UNREACHABLE_ERROR = (155005, "Unreachable code in embedding: {error_msg}")
    # KnowledgeBase Retrieval - Indexing 155100 - 155199
    INDEXING_CHUNK_SIZE_ERROR = (155100, "Invalid chunk size: {error_msg}")
    INDEXING_CHUNK_OVERLAP_ERROR = (155101, "Invalid chunk overlap: {error_msg}")
    INDEXING_TOKENIZER_ERROR = (155102, "Tokenizer error: {error_msg}")
    INDEXING_FILE_NOT_FOUND_ERROR = (155103, "File not found: {error_msg}")
    INDEXING_UNSUPPORTED_FORMAT_ERROR = (155104, "Unsupported file format: {error_msg}")
    INDEXING_EMBED_MODEL_REQUIRED_ERROR = (155105, "Embed model is required: {error_msg}")
    INDEXING_DIMENSION_REQUIRED_ERROR = (155106, "Dimension is required: {error_msg}")
    INDEXING_PATH_REQUIRED_ERROR = (155107, "Path is required and cannot be empty: {error_msg}")
    # KnowledgeBase Retrieval - Retriever 155200 - 155299
    RETRIEVER_UNSUPPORTED_MODE_ERROR = (155200, "Unsupported retrieval mode: {error_msg}")
    RETRIEVER_SCORE_THRESHOLD_ERROR = (155201, "Score threshold is only supported when mode='vector': {error_msg}")
    RETRIEVER_EMBED_MODEL_REQUIRED_ERROR = (155202, "Embed model is required: {error_msg}")
    RETRIEVER_UNSUPPORTED_INDEX_TYPE_ERROR = (155203, "Unsupported index type: {error_msg}")
    RETRIEVER_MODE_INCOMPATIBLE_ERROR = (155204, "Mode is incompatible with index type: {error_msg}")
    RETRIEVER_NOT_SUPPORT_MODE_ERROR = (155205, "Retriever does not support mode: {error_msg}")
    RETRIEVER_VECTOR_STORE_REQUIRED_ERROR = (155206, "Vector store is required: {error_msg}")
    RETRIEVER_COLLECTION_REQUIRED_ERROR = (155207, "Collection is required: {error_msg}")
    RETRIEVER_GRAPH_RETRIEVER_REQUIRED_ERROR = (155208, "Graph retriever is required: {error_msg}")
    RETRIEVER_LLM_CLIENT_REQUIRED_ERROR = (155209, "LLM client is required: {error_msg}")
    RETRIEVER_TOP_K_REQUIRED_ERROR = (155210, "top_k is required: {error_msg}")
    # KnowledgeBase Retrieval - Utils 155300 - 155399
    UTILS_CONFIG_FILE_NOT_FOUND_ERROR = (155300, "Configuration file not found: {error_msg}")
    UTILS_PYYAML_REQUIRED_ERROR = (155301, "PyYAML is required: {error_msg}")
    UTILS_UNSUPPORTED_CONFIG_FORMAT_ERROR = (155302, "Unsupported configuration file format: {error_msg}")
    UTILS_NO_CONFIG_TO_SAVE_ERROR = (155303, "No configuration to save: {error_msg}")
    UTILS_CONFIG_NOT_LOADED_ERROR = (155304, "Configuration not loaded: {error_msg}")
    # KnowledgeBase Retrieval - Vector Store 155400 - 155499
    VECTOR_STORE_PATH_REQUIRED_ERROR = (155400, "Path is required and cannot be empty: {error_msg}")
    # KnowledgeBase Retrieval - Knowledge Base 155500 - 155599
    KB_PARSER_REQUIRED_ERROR = (155500, "Parser is required: {error_msg}")
    KB_CHUNKER_REQUIRED_ERROR = (155501, "Chunker is required: {error_msg}")
    KB_INDEX_MANAGER_REQUIRED_ERROR = (155502, "Index manager is required: {error_msg}")
    KB_VECTOR_STORE_REQUIRED_ERROR = (155503, "Vector store is required: {error_msg}")
    KB_BUILD_INDEX_FAILED_ERROR = (155504, "Failed to build index: {error_msg}")
    KB_BUILD_CHUNK_INDEX_FAILED_ERROR = (155505, "Failed to build chunk index: {error_msg}")
    KB_VECTORSTORE_AND_INDEXER_DATABASE_MISMATCH_ERROR = (
        155506,
        "Vector store and index manager uses different database names: {error_msg}",
    )

    # Memory Engine 158000 - 159999
    MEMORY_STORE_REGISTER_FAILED = (158000, "failed to register {store_type} to memory engine, reason: {error_msg}")
    MEMORY_SET_CONFIG_OPERATION_FAILED = (158001, "failed to set {config_type} config, reason: {error_msg}")
    MEMORY_ADD_MEMORY_OPERATION_FAILED = (158002, "failed to add {memory_type} memory, reason: {error_msg}")
    MEMORY_DELETE_MEMORY_OPERATION_FAILED = (158003, "failed to delete {memory_type} memory, reason: {error_msg}")
    MEMORY_UPDATE_MEMORY_OPERATION_FAILED = (158004, "failed to update {memory_type} memory, reason: {error_msg}")
    MEMORY_GET_MEMORY_OPERATION_FAILED = (158005, "failed to get {memory_type} memory, reason: {error_msg}")
    MEMORY_STORE_INIT_FAILED = (158006, "failed to init {store_type}, reason: {error_msg}")
    MEMORY_STORE_CONNECT_FAILED = (158007, "failed to connect {store_type}, reason: {error_msg}")
    MEMORY_STORE_VALIDATION_FAILED = (158008, "{store_type} validation failed, reason: {error_msg}")

    # Development Toolchain 160000 - 169999
    # Development Toolchain - Prompt Generation 160000 - 160999
    # Development Toolchain - Agent DL convertor 161000 - 161999
    # Development Toolchain - NL2Agent 162000 - 162999
    NL2AGENT_WORKFLOW_INTENTION_DETECT_ERROR = (162000, "NL2Agent workflow intention detect error: {error_msg}")
    NL2AGENT_WORKFLOW_STATE_ERROR = (162001, "NL2Agent workflow state error: {error_msg}")
    NL2AGENT_WORKFLOW_DL_GENERATION_ERROR = (162002, "NL2Agent workflow dl generation error: {error_msg}")
    NL2AGENT_LLM_AGENT_STATE_ERROR = (162010, "NL2Agent llm single_agent state error: {error_msg}")

    # Optimization Toolchain 170000 - 179999
    # Optimization Toolchain - Prompt Self-optimization 170000 - 170999
    AGENT_BUILDER_AGENT_PARAMS_ERROR = (170000, "Parameters error: {error_msg}")
    AGENT_BUILDER_AGENT_OPTIMIZER_BACKWORD_ERROR = (170010, "Do optimizer's backward failed: {error_msg}")
    AGENT_BUILDER_AGENT_OPTIMIZER_UPDATE_ERROR = (170011, "Do optimizer's update failed: {error_msg}")
    AGENT_BUILDER_AGENT_OPTIMIZER_PARAMS_ERROR = (170012, "Agent optimizer parameters error: {error_msg}")
    AGENT_BUILDER_AGENT_EVALUATOR_EVALUATE_ERROR = (170030, "Do evaluator's evaluate failed: {error_msg}")
    AGENT_BUILDER_AGENT_TRAINER_TRAIN_ERROR = (170040, "Do trainer's train error: {error_msg}")
    # Optimization Toolchain - End-to-end Performance Optimization 171000 - 171999
    # Optimization Toolchain - AgentRL 172000 - 172999
    # Optimization Toolchain - Prompt Builder 173000 - 173999
    AGENT_BUILDER_META_TEMPLATE_REGISTER_ERROR = (173000, "Meta-template register failed: {error_msg}")
    AGENT_BUILDER_META_TEMPLATE_ERROR = (173001, "Meta-template builder  failed: {error_msg}")
    AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR = (173002, "Feedback-template builder failed: {error_msg}")
    AGENT_BUILDER_BAD_CASE_TEMPLATE_ERROR = (173003, "Bad case-template builder failed: {error_msg}")
    # Common Capabilities 180000 - 189999
    # Common Capabilities - Prompt Template 180000 - 180999
    PROMPT_ASSEMBLER_VARIABLE_INIT_ERROR = (180000, "Wrong arguments for initializing the variable")
    PROMPT_ASSEMBLER_TEMPLATE_FORMAT_ERROR = (
        180001,
        "Errors occur when formatting the template content due to wrong format")
    PROMPT_TEMPLATE_DUPLICATED_ERROR = (180002, "Template duplicated")
    PROMPT_TEMPLATE_NOT_FOUND_ERROR = (180003, "Template not found")
    PROMPT_TEMPLATE_INCORRECT_ERROR = (180004, "Template data incorrect")

    # Common Capabilities - Model API 181000 - 181999
    MODEL_PROVIDER_INVALID_ERROR = (181000, "Invalid model provider, the reason is {error_msg}")
    MODEL_CALL_FAILED = (181001, "Failed to call model, the reason is: {error_msg}")
    # Common Capabilities - Tool Definition and Execution 182000 - 182999
    PLUGIN_UNEXPECTED_ERROR = (182000, "Plugin unexpected error")
    PLUGIN_REQUEST_TIMEOUT_ERROR = (182001, "Plugin restful api request timed out")
    PLUGIN_RESPONSE_FORMAT_ERROR = (182002, "Plugin restful api response format error")
    PLUGIN_RESPONSE_TOO_BIG_ERROR = (182003,
        "Plugin restful api  response too big, maxLength={max_length}, actualLength={actual_length}")
    PLUGIN_RESPONSE_HTTP_CODE_ERROR = (182004, "Plugin restful api http code error")
    PLUGIN_PARAMS_CHECK_FAILED = (182005, "Plugin params check failed")
    PLUGIN_RESTFUL_API_METHOD_ERROR = (182006,
        "Plugin restful api unsupported HTTP method: '{method}', only accepts: {support}")

    # Common Capabilities - Logger 183000 - 183999
    LOG_PATH_SENSITIVE_ERROR = (183000, "Log path is sensitive or unsafe: {path}")
    LOG_PATH_CREATE_FAILED = (183001, "Failed to create log directory: {path}, error: {error_msg}")
    LOG_CONFIG_LOAD_ERROR = (183002, "Failed to load log configuration file: {error_msg}")
    LOG_CONFIG_INVALID_ERROR = (183003, "Log configuration is invalid: {error_msg}")
    LOG_FILE_OPERATION_ERROR = (183004, "Log file operation failed: {error_msg}")
    # Common Capabilities - Exception Handling 184000 - 184999
    # Common Capabilities - Support Mcp Tool 185000 - 185999

    # Common Capabilities - Common Utility 188000 - 180099
    SSL_UTILS_CREATE_SSL_CONTEXT_ERROR = (188000, "ssl utils error, as {error_msg}")
    USER_CONFIG_LOAD_ERROR = (188001, "User config load error, as {error_msg}")
    JSON_LOADS_ERROR = (188002, "Json loads error, as {error_msg}")
    JSON_DUMPS_ERROR = (188003, "Json dumps error, as {error_msg}")
    URL_INVALID_ERROR = (188004, "Url invalid error, as {error_msg}")
    INVALID_SSL_CERT_ERROR = (188005, "Invalid ssl cert error, as {error_msg}")

    # Session 190000 - 199999
    # Session - Resource Management 190000 - 190999
    SESSION_WORKFLOW_GET_FAILED = (190001, "failed to get workflow, reason: {reason}")
    SESSION_WORKFLOW_ADD_FAILED = (190002, "failed to add workflow, reason: {reason}")
    SESSION_WORKFLOW_CONFIG_ADD_FAILED = (190011, "failed to add workflow config, reason: {reason}")
    SESSION_WORKFLOW_CONFIG_GET_FAILED = (190012, "failed to get workflow config, reason: {reason}")
    SESSION_WORKFLOW_TOOL_INFO_GET_FAILED = (190013, "failed to get toolInfo of workflow, reason: {reason}")

    # Session - Resource Management - Agent Group 190040 - 190049
    SESSION_AGENT_GROUP_ADD_FAILED = (190040, "failed to add single_agent group, reason: {reason}")
    SESSION_AGENT_GROUP_GET_FAILED = (190041, "failed to get single_agent group, reason: {reason}")
    SESSION_AGENT_GROUP_REMOVE_FAILED = (190042, "failed to remove single_agent group, reason: {reason}")
    
    # Session - Resource Management - Workflow Additional
    SESSION_WORKFLOW_REMOVE_FAILED = (190003, "failed to remove workflow, reason: {reason}")
    
    # Session - Resource Management - Agent 190050 - 190059
    SESSION_AGENT_ADD_FAILED = (190050, "failed to add single_agent, reason: {reason}")
    SESSION_AGENT_GET_FAILED = (190051, "failed to get single_agent, reason: {reason}")
    SESSION_AGENT_REMOVE_FAILED = (190052, "failed to remove single_agent, reason: {reason}")

    SESSION_TOOL_GET_FAILED = (190101, "failed to get tool, reason: {reason}")
    SESSION_TOOL_ADD_FAILED = (190102, "failed to add tool, reason: {reason}")
    SESSION_TOOL_TOOL_INFO_GET_FAILED = (190103, "failed to get toolInfo of tool, reason: {reason}")

    SESSION_PROMPT_GET_FAILED = (190201, "failed to get prompt template, reason: {reason}")
    SESSION_PROMPT_ADD_FAILED = (190202, "failed to add prompt template, reason: {reason}")

    SESSION_MODEL_GET_FAILED = (190301, "failed to get model, reason: {reason}")
    SESSION_MODEL_ADD_FAILED = (190302, "failed to add model, reason: {reason}")

    SESSION_TAG_MANAGE_FAILED = (190401, "failed to manage tag, reason: {reason}")

    # Session - Tracer 191000 - 191999
    SESSION_TRACE_ERROR_FAILED = (191001, "failed to record error trace info, reason: {reason}")
    SESSION_TRACE_AGENT_UNDEFINED_FAILED = (191002, "Failed to handle undefined exception")

    # Session - State 192000 - 192999
    SESSION_STATE_SESSION_NONE = (192000, "Session is None, expected BaseSession instance")
    SESSION_STATE_INVALID_SESSION_TYPE = (192001, "Invalid session type: {session_type}, expected BaseSession")
    SESSION_STATE_INVALID_STATE_TYPE = (192002, "Invalid state type: {state_type}, expected CommitState")
    # Session - StreamWriter 193000 - 193999
    STREAM_WRITER_WRITE_SCHEMA_FAILED = (193001,
                                         "failed to write stream, stream schema validate failed, details: {detail}")
    STREAM_WRITER_WRITE_FAILED = (193002, "failed to write stream, reason: {reason}")
    STREAM_FRAME_TIMEOUT_FAILED = (193003, "stream frame is timeout ({timeout}s), no stream output")
    STREAM_FIRST_FRAME_TIMEOUT_FAILED = (193004, "stream first frame is timeout ({timeout}s), no stream output")
    STREAM_NO_INPUT_FAILED = (193005, "component has {abilities} ability, no stream input")

    # Session - Config 194000 - 194999
    # Session - callback 195000 - 195999
    # Session - Stream Actor 196000 - 196099
    WORKFLOW_MESSAGE_QUEUE_MANAGER_ERROR = (196000, "Message queue manager error: {error_msg}")

    # Session - Component Executable 196100 - 196199
    SESSION_COMPONENT_INVALID_SESSION_TYPE = (196100, "session should be NodeSession instance")
    SESSION_COMPONENT_ABILITY_NOT_IMPLEMENTED = (196101, "Component ability '{ability}' is registered but '{method}' "
                                                 "method is not implemented. Please implement the '{method}' method "
                                                 "in your component class '{class_name}'.")
    SESSION_COMPONENT_ABILITY_NOT_SUPPORTED = (196102, "{ability} is not supported")

    # Session - Checkpointer 197000 - 197099
    SESSION_CHECKPOINTER_NONE_WORKFLOW_STORE_ERROR = (197000, "workflow store is None")
    SESSION_CHECKPOINTER_NONE_AGENT_STORE_ERROR = (197001, "agent store is None")


    @property
    def code(self):
        return self.value[0]

    @property
    def errmsg(self):
        return self.value[1]
