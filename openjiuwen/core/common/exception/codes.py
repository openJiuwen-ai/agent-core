# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025.

from enum import Enum


class StatusCode(Enum):
    """Unified StatusCode enum (linted & normalized)"""

    SUCCESS = (0, "success")
    ERROR = (-1, "error")

    # =========================
    # Workflow Component 100000–109999
    # =========================

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
    COMPONENT_LLM_RESPONSE_CONFIG_INVALID = (101001, "component llm_response_config is invalid, reason: {error_msg}")
    COMPONENT_LLM_CONFIG_ERROR = (101002, "component llm config error, reason: {error_msg}")
    COMPONENT_LLM_INVOKE_CALL_FAILED = (101003, "component llm_invoke call failed, reason: {error_msg}")
    COMPONENT_LLM_EXECUTION_PROCESS_ERROR = (101004, "component llm_execution process error, reason: {error_msg}")
    COMPONENT_LLM_INIT_FAILED = (101005, "component llm initialization failed, reason: {error_msg}")
    COMPONENT_LLM_TEMPLATE_PROCESS_ERROR = (101006, "component llm_template process error, reason: {error_msg}")
    COMPONENT_LLM_CONFIG_INVALID = (101007, "component llm_config is invalid, reason: {error_msg}")

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
    COMPONENT_TOOL_INIT_FAILED = (102002, "component tool initialization failed, reason: {error_msg}")

    ## StartComponent  102100 - 102119
    COMPONENT_START_INPUT_INVALID = (102100, "component start_input is invalid, reason: {error_msg}")
    COMPONENT_START_CONFIG_ERROR = (102101, "component start config error, reason: {error_msg}")

    ## EndComponent  102120 - 102149
    COMPONENT_END_INIT_FAILED = (102120, "component end initialization failed, reason: {error_msg}")

    # =========================
    # Workflow Graph & Orchestration 110000–119999
    # =========================

    GRAPH_START_NODE_SET_FAILED = (110001, "graph start node set failed, reason: {error_msg}")
    GRAPH_END_NODE_SET_FAILED = (110002, "graph end node set failed, reason: {error_msg}")
    GRAPH_NODE_ADD_FAILED = (110003, "graph node add failed, reason: {error_msg}")
    GRAPH_EDGE_ADD_FAILED = (110004, "graph edge add failed, reason: {error_msg}")
    GRAPH_CONDITION_EDGE_ADD_FAILED = (110005, "graph condition edge add failed, reason: {error_msg}")
    WORKFLOW_COMPONENT_CONFIG_ERROR = (110006, "workflow component config error, reason: {error_msg}")

    DRAWABLE_GRAPH_TITLE_INVALID = (110024, "drawable graph title is invalid")
    DRAWABLE_GRAPH_EXPAND_CONFIG_INVALID = (110025, "drawable graph expand_subgraph config is invalid")
    DRAWABLE_GRAPH_ANIMATION_CONFIG_INVALID = (110026, "drawable graph animation config is invalid")

    # =========================
    # Agent Orchestration 120000–129999
    # =========================

    # Agent Orchestration - ReAct Agent Orchestration And Execution 120000 - 120999
    AGENT_TOOL_NOT_FOUND = (120000, "agent tool not found, reason: {error_msg}")
    AGENT_TOOL_EXECUTION_ERROR = (120001, "agent tool execution error, reason: {error_msg}")
    AGENT_TASK_NOT_SUPPORT = (120002, "agent task is not supported, reason: {error_msg}")
    AGENT_WORKFLOW_EXECUTION_ERROR = (120003, "agent workflow execution error, reason: {error_msg}")
    AGENT_PROMPT_PARAM_ERROR = (120004, "agent prompt parameter error, reason: {error_msg}")

    # Agent Orchestration - Workflow Agent Orchestration And Execution 121000 - 121999
    # Agent Orchestration - Custom Agent Interface 122000 - 122999

    # Agent Controller 123000 - 123999
    AGENT_CONTROLLER_INVOKE_CALL_FAILED = (123000, "agent controller_invoke call failed, reason: {error_msg}")
    AGENT_SUB_TASK_TYPE_NOT_SUPPORT = (123001, "agent sub_task_type is not supported, reason: {error_msg}")
    AGENT_CONTROLLER_USER_INPUT_PROCESS_ERROR = (
        123002,
        "agent controller_user_input process error, reason: {error_msg}")
    AGENT_CONTROLLER_RUNTIME_ERROR = (123003, "agent controller runtime error, reason: {error_msg}")
    AGENT_CONTROLLER_EXECUTION_CALL_FAILED = (
        123004,
        "agent controller_execution call failed, reason: {error_msg}")
    AGENT_CONTROLLER_TOOL_EXECUTION_PROCESS_ERROR = (
        123005,
        "agent controller_tool_execution process error, reason: {error_msg}")

    # =========================
    # Runner / Distributed 134000–134999
    # =========================

    REMOTE_AGENT_REQUEST_TIMEOUT = (134001, "remote agent request timeout ({timeout}s)")
    AGENT_NOT_FOUND = (134002, "agent not found")
    WORKFLOW_NOT_BOUND_TO_AGENT = (134003, "workflow not bound to agent")
    TOOL_NOT_BOUND_TO_AGENT = (134004, "tool not bound to agent")
    RUNNER_DISTRIBUTED_MODE_REQUIRED = (134006, "runner distributed mode is required")
    RUNNER_STOPPED = (134007, "runner is stopped")
    REMOTE_AGENT_REQUEST_CANCELLED = (134008, "remote agent request cancelled")
    REMOTE_AGENT_PROCESS_ERROR = (134009, "remote agent process error, reason: {error_msg}")

    # =========================
    # Graph Engine 140000–149999
    # =========================

    EXPRESSION_SYNTAX_ERROR = (140000, "expression syntax error")
    EXPRESSION_EVAL_ERROR = (140001, "expression evaluation error, reason: {error_msg}")
    ARRAY_CONDITION_ERROR = (140002, "array condition error")
    NUMBER_CONDITION_ERROR = (140003, "number condition error, reason: {error_msg}")

    # =========================
    # ContextEngine 150000 - 154999
    # =========================

    CONTEXT_MESSAGE_PROCESS_ERROR = (153000, "context message process error, reason: {error_msg}")
    CONTEXT_EXECUTION_ERROR = (153001, "context execution execution error, reason: {error_msg}")
    CONTEXT_MESSAGE_INVALID = (153003, "context message is invalid, reason: {error_msg}")

    # =========================
    # KnowledgeBase Retrieval 155000 - 157999
    # =========================

    # KnowledgeBase Retrieval - Embedding 155000 - 155099
    RETRIEVAL_EMBEDDING_INPUT_INVALID = (155000, "retrieval embedding_input is invalid, reason: {error_msg}")
    RETRIEVAL_EMBEDDING_MODEL_NOT_FOUND = (155001, "retrieval embedding_model not found, reason: {error_msg}")
    RETRIEVAL_EMBEDDING_CALL_FAILED = (155002, "retrieval embedding call failed, reason: {error_msg}")
    RETRIEVAL_EMBEDDING_RESPONSE_INVALID = (155003, "retrieval embedding_response is invalid, reason: {error_msg}")
    RETRIEVAL_EMBEDDING_REQUEST_CALL_FAILED = (
        155004,
        "retrieval embedding_request call failed, reason: {error_msg}",
    )
    RETRIEVAL_EMBEDDING_UNREACHABLE_CALL_FAILED = (155005, "retrieval embedding call failed, reason: {error_msg}")
    RETRIEVAL_EMBEDDING_CALLBACK_INVALID = (155006, "retrieval embedding_callback is invalid, reason: {error_msg}")

    # KnowledgeBase Retrieval - Indexing 155100 - 155199
    RETRIEVAL_INDEXING_CHUNK_SIZE_INVALID = (155100, "retrieval indexing_chunk_size is invalid, reason: {error_msg}")
    RETRIEVAL_INDEXING_CHUNK_OVERLAP_INVALID = (
        155101,
        "retrieval indexing_chunk_overlap is invalid, reason: {error_msg}",
    )
    RETRIEVAL_INDEXING_TOKENIZER_PROCESS_ERROR = (
        155102,
        "retrieval indexing_tokenizer process error, reason: {error_msg}",
    )
    RETRIEVAL_INDEXING_FILE_NOT_FOUND = (155103, "retrieval indexing_file not found, reason: {error_msg}")
    RETRIEVAL_INDEXING_FORMAT_NOT_SUPPORT = (155104, "retrieval indexing_format is not supported, reason: {error_msg}")
    RETRIEVAL_INDEXING_EMBED_MODEL_NOT_FOUND = (155105, "retrieval indexing_embed_model not found, reason: {error_msg}")
    RETRIEVAL_INDEXING_DIMENSION_NOT_FOUND = (155106, "retrieval indexing_dimension not found, reason: {error_msg}")
    RETRIEVAL_INDEXING_PATH_NOT_FOUND = (155107, "retrieval indexing_path not found, reason: {error_msg}")
    RETRIEVAL_INDEXING_DISTANCE_METRIC_INVALID = (
        155108, "retrieval invalid distance metric selected, reason: {error_msg}"
    )
    RETRIEVAL_INDEXING_ADD_DOC_RUNTIME_ERROR = (155109, "retrieval indexing_add_doc runtime error, reason: {error_msg}")

    # KnowledgeBase Retrieval - Retriever 155200 - 155299
    RETRIEVAL_RETRIEVER_MODE_NOT_SUPPORT = (155200, "retrieval retriever_mode is not supported, reason: {error_msg}")
    RETRIEVAL_RETRIEVER_SCORE_THRESHOLD_INVALID = (
        155201,
        "retrieval retriever_score_threshold is invalid, reason: {error_msg}",
    )
    RETRIEVAL_RETRIEVER_EMBED_MODEL_NOT_FOUND = (
        155202,
        "retrieval retriever_embed_model not found, reason: {error_msg}",
    )
    RETRIEVAL_RETRIEVER_INDEX_TYPE_NOT_SUPPORT = (
        155203,
        "retrieval retriever_index_type is not supported, reason: {error_msg}",
    )
    RETRIEVAL_RETRIEVER_MODE_INVALID = (155204, "retrieval retriever_mode is invalid, reason: {error_msg}")
    RETRIEVAL_RETRIEVER_CAPABILITY_NOT_SUPPORT = (
        155205,
        "retrieval retriever_capability is not supported, reason: {error_msg}",
    )
    RETRIEVAL_RETRIEVER_VECTOR_STORE_NOT_FOUND = (
        155206,
        "retrieval retriever_vector_store not found, reason: {error_msg}",
    )
    RETRIEVAL_RETRIEVER_COLLECTION_NOT_FOUND = (155207, "retrieval retriever_collection not found, reason: {error_msg}")
    RETRIEVAL_RETRIEVER_GRAPH_RETRIEVER_NOT_FOUND = (
        155208,
        "retrieval retriever_graph_retriever not found, reason: {error_msg}",
    )
    RETRIEVAL_RETRIEVER_LLM_CLIENT_NOT_FOUND = (155209, "retrieval retriever_llm_client not found, reason: {error_msg}")
    RETRIEVAL_RETRIEVER_TOP_K_NOT_FOUND = (155210, "retrieval retriever_top_k not found, reason: {error_msg}")

    # KnowledgeBase Retrieval - Utils 155300 - 155399
    RETRIEVAL_UTILS_CONFIG_FILE_NOT_FOUND = (155300, "retrieval utils_config_file not found, reason: {error_msg}")
    RETRIEVAL_UTILS_PYYAML_NOT_FOUND = (155301, "retrieval utils_pyyaml not found, reason: {error_msg}")
    RETRIEVAL_UTILS_CONFIG_FORMAT_NOT_SUPPORT = (
        155302,
        "retrieval utils_config_format is not supported, reason: {error_msg}",
    )
    RETRIEVAL_UTILS_CONFIG_NOT_FOUND = (155303, "retrieval utils_config not found, reason: {error_msg}")
    RETRIEVAL_UTILS_CONFIG_PROCESS_ERROR = (155304, "retrieval utils_config process error, reason: {error_msg}")

    # KnowledgeBase Retrieval - Vector Store 155400 - 155499
    RETRIEVAL_VECTOR_STORE_PATH_NOT_FOUND = (155400, "retrieval vector_store_path not found, reason: {error_msg}")

    # KnowledgeBase Retrieval - Knowledge Base 155500 - 155599
    RETRIEVAL_KB_PARSER_NOT_FOUND = (155500, "retrieval kb_parser not found, reason: {error_msg}")
    RETRIEVAL_KB_CHUNKER_NOT_FOUND = (155501, "retrieval kb_chunker not found, reason: {error_msg}")
    RETRIEVAL_KB_INDEX_MANAGER_NOT_FOUND = (155502, "retrieval kb_index_manager not found, reason: {error_msg}")
    RETRIEVAL_KB_VECTOR_STORE_NOT_FOUND = (155503, "retrieval kb_vector_store not found, reason: {error_msg}")
    RETRIEVAL_KB_INDEX_BUILD_EXECUTION_ERROR = (155504, "retrieval kb_index_build execution error, reason: {error_msg}")
    RETRIEVAL_KB_CHUNK_INDEX_BUILD_EXECUTION_ERROR = (
        155505,
        "retrieval kb_chunk_index_build execution error, reason: {error_msg}",
    )
    RETRIEVAL_KB_TRIPLE_INDEX_BUILD_EXECUTION_ERROR = (
        155506,
        "retrieval kb_triple_index_build execution error, reason: {error_msg}",
    )
    RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR = (
        155507,
        "retrieval kb_triple_extraction process error, reason: {error_msg}",
    )
    RETRIEVAL_KB_DATABASE_CONFIG_INVALID = (
        155508,
        "retrieval kb_database_config is invalid, reason: Vector store and index manager have "
        "incompatible {config_name} configs: {error_msg}",
    )

    # =========================
    # Memory Engine 158000 – 159999
    # =========================
    MEMORY_REGISTER_STORE_EXECUTION_ERROR = (158000, "failed to register {store_type} to memory engine, "
                                                     "reason: {error_msg}")
    MEMORY_SET_CONFIG_EXECUTION_ERROR = (158001, "failed to set {config_type} config, reason: {error_msg}")
    MEMORY_ADD_MEMORY_EXECUTION_ERROR = (158002, "failed to add {memory_type} memory, reason: {error_msg}")
    MEMORY_DELETE_MEMORY_EXECUTION_ERROR = (158003, "failed to delete {memory_type} memory, reason: {error_msg}")
    MEMORY_UPDATE_MEMORY_EXECUTION_ERROR = (158004, "failed to update {memory_type} memory, reason: {error_msg}")
    MEMORY_GET_MEMORY_EXECUTION_ERROR = (158005, "failed to get {memory_type} memory, reason: {error_msg}")
    MEMORY_STORE_INIT_FAILED = (158006, "failed to init {store_type}, reason: {error_msg}")
    MEMORY_CONNECT_STORE_EXECUTION_ERROR = (158007, "failed to connect {store_type}, reason: {error_msg}")
    MEMORY_STORE_VALIDATION_INVALID = (158008, "{store_type} validation failed, reason: {error_msg}")

    # =========================
    # Foundation Tool 160000–169999
    # =========================
    TOOL_STREAM_NOT_SUPPORTED = (160001, "stream is not support, card={card}")
    TOOL_INVOKE_NOT_SUPPORTED = (160002, "invoke is not support, card={card}")
    TOOL_CARD_NOT_SUPPORTED = (160003, "card is not support")
    TOOL_CARD_ID_NOT_SUPPORTED = (160004, "card's id is not support, card={card}")

    # RestfulApi 160100-160199
    # RestfulApiCard validate 160100-160120
    TOOL_RESTFUL_API_CARD_CONFIG_INVALID = (160100, "config failed, {reason}")
    # RestfulApiCard Execution 160121 - 160199
    TOOL_RESTFUL_API_TIMEOUT = (160121,
        "execute {interface} failed, request is timeout, timeout={timeout}s, card=[{card}]")
    TOOL_RESTFUL_API_RESPONSE_SIZE_EXCEED_LIMIT = (160122,
        "execute {interface} failed, response is too big,"
        " max_size={max_length}b, actual={actual_length}b, card=[{card}]")
    TOOL_RESTFUL_API_RESPONSE_ERROR = (160123,
        "execute {interface} failed, response error, code={code}, reason={reason}")
    TOOL_RESTFUL_API_EXECUTION_ERROR = (160124, "RestfulApi execute {interface} failed,"
                                                " reason={reason}, card=[{card}]")

    # LocalFunction 160200-160299
    # LocalFunction validate 160200-160220
    TOOL_LOCAL_FUNCTION_FUNC_NOT_SUPPORTED = (160201, "func is not supported, card={card}")

    # LocalFunction execution 160221-160299
    TOOL_LOCAL_FUNCTION_EXECUTION_ERROR = (160221, "execute {interface} failed, reason={reason}, card={card}")

    # MCPTool 160300-160399
    # MCPTool validate 160300-160320
    TOOL_MCP_CLIENT_NOT_SUPPORTED = (160301, "mcp client is not supported, card={card}")

    # MCPTool execution 160321-160399
    TOOL_MCP_EXECUTION_ERROR = (160321, "execute {interface} failed, reason={reason}, card={card}")


    # =========================
    # Optimization Toolchain 170000 - 179999
    # =========================

    # Optimization Toolchain - Prompt Self-optimization 170000 - 170999
    TOOLCHAIN_AGENT_PARAM_ERROR = (
        170000, "toolchain agent parameter error, reason: {error_msg}"
    )
    TOOLCHAIN_OPTIMIZER_BACKWARD_EXECUTION_ERROR = (
        170001, "toolchain optimizer_backword execution error, reason: {error_msg}"
    )
    TOOLCHAIN_OPTIMIZER_UPDATE_EXECUTION_ERROR = (
        170002, "toolchain optimizer_update execution error, reason: {error_msg}"
    )
    TOOLCHAIN_OPTIMIZER_PARAM_ERROR = (170003, "toolchain optimizer parameter error, reason: {error_msg}")
    TOOLCHAIN_EVALUATOR_EXECUTION_ERROR = (170004, "toolchain evaluator execution error, reason: {error_msg}")
    TOOLCHAIN_TRAINER_EXECUTION_ERROR = (170005, "toolchain trainer execution error, reason: {error_msg}")

    # Optimization Toolchain - End-to-end Performance Optimization 171000 - 171999
    # Optimization Toolchain - AgentRL 172000 - 172999

    # Optimization Toolchain - Prompt Builder 173000 - 173999
    TOOLCHAIN_META_TEMPLATE_EXECUTION_ERROR = (
        173000, "toolchain meta_template execution error, reason: {error_msg}"
    )
    TOOLCHAIN_FEEDBACK_TEMPLATE_EXECUTION_ERROR = (
        173001, "toolchain feedback_template execution error, reason: {error_msg}"
    )
    TOOLCHAIN_BAD_CASE_TEMPLATE_EXECUTION_ERROR = (
        173002, "toolchain bad_case_template execution error, reason: {error_msg}"
    )

    # =========================
    # Foundation 180000 – 189999
    # =========================

    # Foundation - Prompt Template 180000 - 180999
    PROMPT_ASSEMBLER_VARIABLE_INIT_FAILED = (180000,
                                             "prompt assembler_variable initialization failed, reason: {error_msg}")
    PROMPT_ASSEMBLER_TEMPLATE_PARAM_ERROR = (
        180001, "prompt assembler_template parameter error, reason: {error_msg}")
    PROMPT_TEMPLATE_RUNTIME_ERROR = (180002, "prompt template runtime error, reason: {error_msg}")
    PROMPT_TEMPLATE_NOT_FOUND = (180003, "prompt template not found, reason: {error_msg}")
    PROMPT_TEMPLATE_INVALID = (180004, "prompt template is invalid, reason: {error_msg}")

    # Foundation - Model API 181000 - 181999
    MODEL_PROVIDER_INVALID = (181000, "model provider is invalid, reason: {error_msg}")
    MODEL_CALL_FAILED = (181001, "model call failed, reason: {error_msg}")
    MODEL_SERVICE_CONFIG_ERROR = (181002, "model service config error, reason: {error_msg}")
    MODEL_CONFIG_ERROR = (181003, "model config error, reason: {error_msg}")
    MODEL_INVOKE_PARAM_ERROR = (181004, "model invoke parameter error, reason: {error_msg}")
    MODEL_CLIENT_CONFIG_INVALID = (181005, "model client_config is invalid, reason: {error_msg}")

    # Foundation - Tool Definition and Execution 182000 - 182999
    PLUGIN_EXECUTION_RUNTIME_ERROR = (182000, "plugin execution runtime error, reason: {error_msg}")
    PLUGIN_REQUEST_TIMEOUT = (182001, "plugin request timeout ({timeout}s), reason: {error_msg}")
    PLUGIN_RESPONSE_PROCESS_ERROR = (182002, "plugin response process error, reason: {error_msg}")
    PLUGIN_RESPONSE_INVALID = (182003,
                               "plugin response is invalid, reason: {error_msg}")
    PLUGIN_RESPONSE_CALL_FAILED = (182004, "plugin response call failed, reason: {error_msg}")
    PLUGIN_INPUT_PARAM_ERROR = (182005, "plugin input parameter error, reason: {error_msg}")
    PLUGIN_RESTFUL_API_NOT_SUPPORT = (182006,
                                      "plugin restful_api is not supported, reason: {error_msg}")

    # Foundation - Logger 183000 - 183999
    COMMON_LOG_PATH_INVALID = (183000, "common log_path is invalid, reason: {error_msg}")
    COMMON_LOG_PATH_INIT_FAILED = (183001, "common log_path initialization failed, reason: {error_msg}")
    COMMON_LOG_CONFIG_PROCESS_ERROR = (183002, "common log_config process error, reason: {error_msg}")
    COMMON_LOG_CONFIG_INVALID = (183003, "common log_config is invalid, reason: {error_msg}")
    COMMON_LOG_EXECUTION_RUNTIME_ERROR = (183004, "common log_execution runtime error, reason: {error_msg}")

    # Foundation - Exception Handling 184000 - 184999
    # Foundation - Support Mcp Tool 185000 - 185999

    # Foundation - Common Utility 188000 - 188999
    COMMON_SSL_CONTEXT_INIT_FAILED = (188000, "common ssl_context initialization failed, reason: {error_msg}")
    COMMON_USER_CONFIG_PROCESS_ERROR = (188001, "common user_config process error, reason: {error_msg}")
    COMMON_JSON_INPUT_PROCESS_ERROR = (188002, "common json_input process error, reason: {error_msg}")
    COMMON_JSON_EXECUTION_PROCESS_ERROR = (188003, "common json_execution process error, reason: {error_msg}")
    COMMON_URL_INPUT_INVALID = (188004, "common url_input is invalid, reason: {error_msg}")
    COMMON_SSL_CERT_INVALID = (188005, "common ssl_cert is invalid, reason: {error_msg}")

    # Foundation - Schema 189000 - 189999
    SCHEMA_VALIDATE_INVALID = (189001, "validate data with schema failed, reason={reason}, data={data}")
    SCHEMA_FORMAT_INVALID = (189002, "format data with schema failed, reason={reason}, data={data}")

    def __init__(self, code: int, msg: str):
        """Validate and initialize enum member values.

        Args:
            code: integer status code
            msg: error message template (supports str.format placeholders)
        """
        if not isinstance(code, int):
            raise TypeError(f"StatusCode code must be int, got {type(code)!r} for {self.name}")
        if not isinstance(msg, str):
            raise TypeError(f"StatusCode errmsg must be str, got {type(msg)!r} for {self.name}")
        self._code = code
        self._msg = msg

    @property
    def code(self) -> int:
        """Return the integer error code."""
        return self._code

    @property
    def errmsg(self) -> str:
        """Return the error message template (unformatted)."""
        return self._msg