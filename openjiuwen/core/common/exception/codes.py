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

    # Interactive & Recovery 100000–100029
    INTERACTIVE_INPUT_INVALID = (100000, "interactive input is invalid")
    INTERACTIVE_UPDATE_INVALID = (100001, "interactive input update is invalid")
    COMPONENT_NOT_EXECUTABLE = (100002, "workflow component is not executable")
    CONTROLLER_INTERRUPTED = (100003, "controller interrupted")
    INTERACTIVE_STREAM_NOT_SUPPORTED = (100004, "interactive operation is not supported for stream processing")
    COMPONENT_EXECUTION_ERROR = (
        100005,
        "component execution error, node_id: {node_id}, ability: {ability}, reason: {error_msg}",
    )
    WORKFLOW_STATE_EXISTS = (100006, "workflow state already exists and cleanup is disabled")

    # Execution 100100–100199
    WORKFLOW_EXECUTION_ERROR = (100100, "workflow execution error, reason: {error_msg}")
    WORKFLOW_INVOKE_TIMEOUT = (100101, "workflow invocation timeout ({timeout}s)")
    WORKFLOW_STREAM_TIMEOUT = (100102, "workflow stream timeout ({timeout}s)")

    # =========================
    # Built-in Workflow Components 101000–109999
    # =========================

    # LLM Component 101000–101049
    LLM_COMPONENT_TEMPLATE_CONFIG_ERROR = (101000, "llm component template config error, reason: {error_msg}")
    LLM_COMPONENT_RESPONSE_FORMAT_CONFIG_ERROR = (
        101001,
        "llm component response format config error, reason: {error_msg}",
    )
    LLM_COMPONENT_OUTPUT_CONFIG_ERROR = (101002, "llm component output config error, reason: {error_msg}")
    LLM_COMPONENT_CALL_FAILED = (101003, "llm component call failed, reason: {error_msg}")
    LLM_COMPONENT_JSON_SCHEMA_OUTPUT_ERROR = (
        101004,
        "llm component json schema output error, reason: {error_msg}",
    )
    LLM_COMPONENT_INIT_FAILED = (101005, "llm component initialization failed, reason: {error_msg}")
    LLM_COMPONENT_TEMPLATE_ASSEMBLE_ERROR = (
        101006,
        "llm component template assemble error, reason: {error_msg}",
    )
    PROMPT_JSON_SCHEMA_INVALID = (101007, "prompt json schema is invalid")

    # Intent Detection Component 101050–101069
    INTENT_DETECTION_INPUT_INVALID = (101050, "intent detection input is invalid")
    INTENT_DETECTION_INIT_FAILED = (101051, "intent detection initialization failed, reason: {error_msg}")
    INTENT_DETECTION_CALL_FAILED = (101052, "intent detection llm call failed, reason: {error_msg}")

    # Question Component 101070–101099
    QUESTION_INPUT_INVALID = (101070, "question input is invalid")
    QUESTION_COMPONENT_CONFIG_ERROR = (101071, "question component config error, reason: {error_msg}")
    QUESTION_EMPTY_IN_DIRECT_REPLY = (101072, "question is empty in direct reply mode")
    QUESTION_STATE_INIT_FAILED = (101073, "question component state initialization failed")
    QUESTION_RESPONSE_EXCEEDED = (101074, "question component response exceeded limit")
    QUESTION_LLM_CALL_FAILED = (101075, "question component llm call failed, reason: {error_msg}")
    QUESTION_RESPONSE_PARSE_ERROR = (101076, "question response parse error, reason: {error_msg}")

    # Branch Component 101100–101119
    BRANCH_ADD_FAILED = (101100, "branch add failed, reason: {error_msg}")
    BRANCH_CONDITION_TYPE_INVALID = (101101, "branch condition type is invalid")
    BRANCH_NOT_FOUND = (101102, "branch not found")

    # Set Variable Component 101120–101139
    SET_VARIABLE_MAPPING_ERROR = (101120, "set variable mapping error, reason: {error_msg}")

    # Sub Workflow Component 101140–101149
    SUB_WORKFLOW_INIT_FAILED = (101140, "sub workflow initialization failed, reason: {error_msg}")
    SUB_WORKFLOW_EXECUTION_ERROR = (101141, "sub workflow execution error, reason: {error_msg}")

    # Loop Component 101150–101159
    LOOP_NESTED_NOT_SUPPORTED = (101150, "nested loop is not supported")
    LOOP_EXECUTION_ERROR = (101151, "loop execution error, reason: {error_msg}")
    LOOP_GROUP_EMPTY = (101152, "loop group is empty")
    LOOP_INPUT_TYPE_INVALID = (101153, "loop input type is invalid")
    LOOP_INPUT_KEY_MISSING = (101154, "loop input key is missing")
    LOOP_TYPE_INVALID = (101155, "loop type is invalid")
    LOOP_START_NODE_MISSING = (101156, "loop start node is missing")
    LOOP_END_NODE_MISSING = (101157, "loop end node is missing")

    # Break Component 101180–101189
    BREAK_COMPONENT_INIT_FAILED = (101180, "break component initialization failed")

    # Tool Component 102000–102019
    TOOL_BIND_FAILED = (102000, "tool bind failed")
    TOOL_INPUT_INVALID = (102001, "tool input is invalid")
    TOOL_PARAM_INVALID = (102002, "tool parameter is invalid")

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

    TOOL_NOT_FOUND = (120000, "tool not found")
    TOOL_EXECUTION_ERROR = (120001, "tool execution error, reason: {error_msg}")
    TASK_TYPE_NOT_SUPPORTED = (120002, "task type is not supported")
    AGENT_WORKFLOW_EXECUTION_ERROR = (120003, "agent workflow execution error, reason: {error_msg}")
    PROMPT_PARAM_INVALID = (120004, "prompt parameter is invalid")

    # Agent Controller 123000–123999
    CONTROLLER_LLM_CALL_FAILED = (123000, "controller llm call failed, reason: {error_msg}")
    AGENT_SUB_TASK_TYPE_NOT_SUPPORTED = (123001, "agent sub task type is not supported")
    CONTROLLER_INPUT_HANDLE_ERROR = (123002, "controller input handle error, reason: {error_msg}")
    CONTROLLER_RUNTIME_ERROR = (123003, "controller runtime error, reason: {error_msg}")
    CONTROLLER_STREAM_SEND_FAILED = (123004, "controller stream send failed, reason: {error_msg}")
    CONTROLLER_TOOL_CALL_PARSE_ERROR = (123005, "controller tool call parse error, reason: {error_msg}")

    # =========================
    # Context Engine 130000–133999
    # =========================
    CONTEXT_ADD_MESSAGE_ERROR = (130000, "Message add message error, reason: {error_msg}")
    CONTEXT_GET_MESSAGE_ERROR = (130001, "Message get message error, reason: {error_msg}")
    CONTEXT_POP_MESSAGE_ERROR = (130002, "Message pop message error, reason: {error_msg}")
    CONTEXT_GET_CONTEXT_WINDOW_ERROR = (130003, "Message get context window error, reason: {error_msg}")
    CONTEXT_MESSAGE_VALIDATION_ERROR = (130004, "Context engine message validation error, reason: {error_msg}")
    CONTEXT_CREATE_PROCESSOR_ERROR = (130005, "create context processor failed, reason: {error_msg}")

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
    NUMBER_CONDITION_ERROR = (140003, "number condition error")

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
    # Common Capabilities 180000–189999
    # =========================

    MODEL_PROVIDER_INVALID = (181000, "model provider is invalid")
    MODEL_CALL_FAILED = (181001, "model call failed, reason: {error_msg}")

    LOG_PATH_SENSITIVE = (183000, "log path is sensitive")
    LOG_PATH_CREATE_FAILED = (183001, "log path create failed, reason: {error_msg}")
    LOG_CONFIG_LOAD_FAILED = (183002, "log config load failed, reason: {error_msg}")
    LOG_CONFIG_INVALID = (183003, "log config is invalid")
    LOG_FILE_OPERATION_FAILED = (183004, "log file operation failed, reason: {error_msg}")

    SSL_CONTEXT_CREATE_FAILED = (188000, "ssl context create failed, reason: {error_msg}")
    USER_CONFIG_LOAD_FAILED = (188001, "user config load failed, reason: {error_msg}")
    JSON_LOAD_FAILED = (188002, "json load failed, reason: {error_msg}")
    JSON_DUMP_FAILED = (188003, "json dump failed, reason: {error_msg}")
    URL_INVALID = (188004, "url is invalid")
    SSL_CERT_INVALID = (188005, "ssl certificate is invalid")

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