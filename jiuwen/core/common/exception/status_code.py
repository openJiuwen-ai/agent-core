#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from enum import Enum


class StatusCode(Enum):
    """Status code enum"""

    SUCCESS = (0, "success")
    ERROR = (-1, "error")

    # Workflow Component  100000 - 109999

    # Workflow Component - Component Development:  100000 - 100999
    # Workflow Component - Component Development: Interactive And Recovery 100000 - 100029
    INTERACTIVE_INVALID_INPUT_ERROR = (100000, "value of interactive_input is invalid")
    INTERACTIVE_UPDATE_FAILED = (100001, "raw_inputs existed, update is invalid")
    COMPONENT_NOT_EXECUTABLE_ERROR = (100002, "workflow component should implement Executable")

    # Workflow Component - Builtin-workflow Component 101000 - 109999

    ## LLMComponent  101000 - 101049
    LLM_COMPONENT_TEMPLATE_CONFIG_ERROR = (101000, "LLM component template config error, as {error_msg}.")
    LLM_COMPONENT_RESPONSE_FORMAT_CONFIG_ERROR = (101001, "LLM component response format config error, as {error_msg}.")
    LLM_COMPONENT_OUTPUT_CONFIG_ERROR = (101002, "LLM component output config error, as {error_msg}.")
    LLM_COMPONENT_INVOKE_LLM_ERROR = (101003, "LLM component invoke llm error, as {error_msg}.")
    LLM_COMPONENT_JSON_SCHEMA_OUTPUT_ERROR = (101004, "Failed to output json schema, as {error_msg}.")
    LLM_COMPONENT_INIT_LLM_ERROR = (101005, "Failed to init llm, as {error_msg}.")
    LLM_COMPONENT_ASSEMBLE_TEMPLATE_ERROR = (101006, "LLM component assemble template error, as {error_msg}.")

    ## IntentDetectionComponent 101050 - 101069
    INTENT_DETECTION_COMPONENT_USER_INPUT_ERROR = (101050,
                                                   "Intent detection component user input error, as {error_msg}.")
    INTENT_DETECTION_COMPONENT_INIT_LLM_ERROR = (101051, "Intent detection component init llm error, as {error_msg}.")
    INTENT_DETECTION_COMPONENT_INVOKE_LLM_ERROR = (101052,
                                                   "Intent detection component invoke llm error, as {error_msg}.")

    ## QuestionComponent 101070 - 101099
    QUESTIONER_COMPONENT_USER_INPUT_ERROR = (101070, "Questioner component user input error, as {error_msg}.")
    QUESTIONER_COMPONENT_CONFIG_ERROR = (101071, "Questioner component config error, as {error_msg}.")
    QUESTIONER_COMPONENT_EMPTY_QUESTION_IN_DIRECT_REPLY = \
        (101072, "Questioner component empty question in direct reply mode.")
    QUESTIONER_COMPONENT_INIT_STATE_ERROR = (101073, "Questioner component init state error.")
    QUESTIONER_COMPONENT_EXCEED_MAX_RESPONSE = (101074, "Questioner component exceed max response.")
    QUESTIONER_COMPONENT_INVOKE_LLM_ERROR = (101075, "Questioner component invoke llm error, as {error_msg}.")

    ## BranchComponent  101100 - 101119
    BRANCH_COMPONENT_ADD_BRANCH_ERROR = (101100, "Branch adding error, as {error_msg}.")
    BRANCH_COMPONENT_BRANCH_CONDITION_TYPE_ERROR = (101101, "Branch condition type does not meet the requirements.")
    BRANCH_COMPONENT_BRANCH_NOT_FOUND_ERROR = (101102, "Branch meeting the condition was not found.")

    ## SetVariableComponent  101120 - 101139
    SET_VAR_COMPONENT_VAR_MAPPING_ERROR = (101120, "Set variable component mapping error, as {error_msg}.")

    ## SubWorkflowComponent  101140 - 101149
    SUB_WORKFLOW_COMPONENT_INIT_ERROR = (101140, "Sub workflow component init error, as {error_msg}.")
    SUB_WORKFLOW_COMPONENT_RUNNING_ERROR = (101141, "Sub workflow component running error, detail: {detail}")

    ## LoopComponent  101150 - 101159
    LOOP_COMPONENT_NESTED_LOOP_ERROR = (101150, "Nested loops are not supported. Cannot add LoopComponent to a LoopGroup")
    LOOP_COMPONENT_EXECUTION_ERROR = (101151, "Loop execution error: {error_msg}")
    LOOP_COMPONENT_EMPTY_GROUP_ERROR = (101152, "Loop group is empty, no components to execute")
    LOOP_COMPONENT_INPUT_TYPE_ERROR = (101153, "Inputs must be a dictionary, got {type}")
    LOOP_COMPONENT_MISSING_INPUT_KEY_ERROR = (101154, "Invalid inputs: missing required key {key}")
    LOOP_COMPONENT_INVALID_LOOP_TYPE_ERROR = (101155, "Invalid loop type '{loop_type}' for LoopComponent")

    ## ToolComponent  102000 - 102019
    TOOL_COMPONENT_BIND_TOOL_FAILED = (102000, "Tool component failed to bind a valid tool.")
    TOOL_COMPONENT_INPUTS_ERROR = (102001, "Tool component inputs error, as {error_msg}.")
    TOOL_COMPONENT_CHECK_PARAM_ERROR = (102002, "Tool component check parameter error, as {error_msg}.")

    # Workflow 110000 - 119999
    # Workflow - Orchestration And Execution 110000 - 110999
    GRAPH_SET_START_NODE_FAILED = (110001, "Graph create error, caused by start node set failed, detail: {detail}")
    GRAPH_SET_END_NODE_FAILED = (110002, "Graph create error, caused by end node set failed, detail: {detail}")
    GRAPH_ADD_NODE_FAILED = (110003, "Graph create error, caused by add node failed, detail: {detail}")
    GRAPH_ADD_EDGE_FAILED = (110004, "Graph create error, caused by add edge failed, detail: {detail}")
    GRAPH_ADD_CONDITION_EDGE_FAILED = (110005,
                                       "Graph create error, caused by add conditional edge failed, detail: {detail}")

    # Workflow - Exception Handling 111000 - 111999
    # Agent Orchestration 120000 - 129999
    # Agent Orchestration - ReAct Agent Orchestration And Execution 120000 - 120999
    # Agent Orchestration - Workflow Agent Orchestration And Execution 121000 - 121999
    # Agent Orchestration - Custom Agent Interface 122000 - 122999

    # Multi-Agent Orchestration 130000 - 139999
    # Multi-Agent Orchestration - Multi-Agent Communication  130000 - 130999
    # Multi-Agent Orchestration - Single Runtime 131000 - 131999
    # Multi-Agent Orchestration - AgentGroup 132000 - 132999
    # Multi-Agent Orchestration - Multi-Agent Debug 133000 - 133999
    # Multi-Agent Orchestration - Distribution Runtime 134000 - 134999

    # GraphEngine 140000 - 149999
    # GraphEngine - Graph Orchestration and Execution 140000 - 140999
    # GraphEngine - Conditional Evaluation 140000 - 140019
    EXPRESSION_CONDITION_SYNTAX_ERROR = (140000, "Expression condition has syntax error, expression as {expression}, error as {error_msg}.")
    EXPRESSION_CONDITION_EVAL_ERROR = (140001, "Expression condition eval error, as {error_msg}.")
    ARRAY_CONDITION_ERROR = (140002, "Array condition error")
    NUMBER_CONDITION_ERROR = (140003, "Number condition error")


    # ContextEngine 150000 - 159999
    # ContextEngine - Context Structured Storage and Retrieval 150000 - 150999
    # ContextEngine - Context Dynamic Assembly  151000 - 151999
    # ContextEngine - Context Asynchronous Processing 152000 - 152999

    # Development Toolchain 160000 - 169999
    # Development Toolchain - Prompt Generation 160000 - 160999
    # Development Toolchain - Agent DL convertor 161000 - 161999
    # Development Toolchain - NL2Agent 162000 - 162999

    # Optimization Toolchain 170000 - 179999
    # Optimization Toolchain - Prompt Self-optimization 170000 - 170999
    # Optimization Toolchain - End-to-end Performance Optimization 171000 - 171999
    # Optimization Toolchain - AgentRL 172000 - 172999

    # Common Capabilities 180000 - 189999
    # Common Capabilities - Prompt Population 180000 - 180999
    # Common Capabilities - Model API 181000 - 181999
    # Common Capabilities - Tool Definition and Execution 182000 - 182999
    PLUGIN_UNEXPECTED_ERROR = (182000, "Plugin unexpected error")
    PLUGIN_REQUEST_TIMEOUT_ERROR = (182001, "Plugin restful api request timed out")
    PLUGIN_PROXY_CONNECT_ERROR = (182002, "Plugin restful api proxy connection error")
    PLUGIN_RESPONSE_TOO_BIG_ERROR = (182003, "Plugin restful api  response too big")
    PLUGIN_RESPONSE_HTTP_CODE_ERROR = (182004, "Plugin restful api http code error")
    PLUGIN_PARAMS_CHECK_FAILED = (182005, "Plugin params check failed")

    # Common Capabilities - Logger 183000 - 183999
    # Common Capabilities - Exception Handling 184000 - 184999
    # Common Capabilities - Support Mcp Tool 185000 - 185999

    # Common Capabilities - Common Utility 188000 - 180099
    SSL_UTILS_CREATE_SSL_CONTEXT_ERROR = (188000, "ssl utils error, as {error_msg}")
    USER_CONFIG_LOAD_ERROR = (188001, "User config load error, as {error_msg}")
    JSON_LOADS_ERROR = (188002, "Json loads error, as {error_msg}")
    JSON_DUMPS_ERROR = (188003, "Json dumps error, as {error_msg}")
    URL_INVALID_ERROR = (188004, "Url invalid error, as {error_msg}")

    # Runtime 190000 - 199999
    # Runtime - Resource Management 190000 - 190999
    RUNTIME_WORKFLOW_GET_FAILED = (190001, "failed to get workflow, reason: {reason}")
    RUNTIME_WORKFLOW_ADD_FAILED = (190002, "failed to add workflow, reason: {reason}")
    RUNTIME_WORKFLOW_CONFIG_ADD_FAILED = (190011, "failed to add workflow config, reason: {reason}")
    RUNTIME_WORKFLOW_CONFIG_GET_FAILED = (190012, "failed to get workflow config, reason: {reason}")
    RUNTIME_WORKFLOW_TOOL_INFO_GET_FAILED = (19021, "failed to get toolInfo of workflow, reason: {reason}")

    RUNTIME_TOOL_GET_FAILED = (190101, "failed to get tool, reason: {reason}")
    RUNTIME_TOOL_ADD_FAILED = (190102, "failed to add tool, reason: {reason}")
    RUNTIME_TOOL_TOOL_INFO_GET_FAILED = (19121, "failed to get toolInfo of tool, reason: {reason}")

    RUNTIME_PROMPT_GET_FAILED = (190201, "failed to get prompt template, reason: {reason}")
    RUNTIME_PROMPT_ADD_FAILED = (190202, "failed to add prompt template, reason: {reason}")

    RUNTIME_MODEL_GET_FAILED = (190301, "failed to get model, reason: {reason}")
    RUNTIME_MODEL_ADD_FAILED = (190302, "failed to add model, reason: {reason}")

    # Runtime - Tracer 191000 - 191999
    RUNTIME_TRACE_ERROR_FAILED = (191001, "failed to record error trace info, reason: {reason}")
    # Runtime - State 192000 - 192999
    # Runtime - StreamWriter 193000 - 193999
    STREAM_WRITER_WRITE_SCHEMA_FAILED = (193001,
                                         "failed to write stream, stream schema validate failed, details: {detail}")
    STREAM_WRITER_WRITE_FAILED = (193002, "failed to write stream, reason: {reason}")
    STREAM_FRAME_TIMEOUT_FAILED = (193003, "stream frame is timeout ({timeout}s), no stream output")
    # Runtime - Config 194000 - 194999
    # Runtime - callback 195000 - 195999

    WORKFLOW_START_MISSING_GLOBAL_VARIABLE_VALUE = (101501,
                                                    "start component: global variable(s) defined with no value assigned:  {variable_name}")
    WORKFLOW_START_CREATE_VALUE = (101502, "start component create error:  {reason}")
    WORKFLOW_END_CREATE_VALUE = (101511, "end component create error: {reason}")

    WORKFLOW_MESSAGE_QUEUE_MANAGER_ERROR = (101771, "Message queue manager error: {error_msg}")

    PROMPT_ASSEMBLER_VARIABLE_INIT_ERROR = (102050, "Wrong arguments for initializing the variable")
    PROMPT_ASSEMBLER_INIT_ERROR = (102051, "Wrong arguments for initializing the assembler")
    PROMPT_ASSEMBLER_INPUT_KEY_ERROR = (
        102052,
        "Missing or unexpected key-value pairs passed in as arguments for the assembler or variable when updating"
    )
    PROMPT_ASSEMBLER_TEMPLATE_FORMAT_ERROR = (
        102053,
        "Errors occur when formatting the template content due to wrong format")
    PROMPT_JSON_SCHEMA_ERROR = (102056, "Invalid json schema, root cause = {error_msg}.")

    PROMPT_TEMPLATE_DUPLICATED_ERROR = (102101, "Template duplicated")
    PROMPT_TEMPLATE_NOT_FOUND_ERROR = (102102, "Template not found")
    PROMPT_TEMPLATE_INCORRECT_ERROR = (102103, "Template data incorrect")

    INVOKE_LLM_FAILED = (103004, "Failed to call model")
    CONTROLLER_INTERRUPTED_ERROR = (10312, "controller interrupted error")

    AGENT_SUB_TASK_TYPE_ERROR = (103032, "SubTask type {msg} is not supported")

    CONTEXT_ENGINE_MESSAGE_PROCESS_ERROR = (106000, "Message process error: {error_msg}")

    @property
    def errmsg(self):
        return self.value[1]

    @property
    def code(self):
        return self.value[0]
