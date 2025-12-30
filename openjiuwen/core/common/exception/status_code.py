# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from enum import Enum


class StatusCode(Enum):
    """Status code enum"""

    SUCCESS = (0, "success")
    ERROR = (-1, "error")

    # Workflow Component  100000 - 109999

    # Workflow: Interactive And Recovery 100000 - 100029
    INTERACTIVE_INVALID_INPUT_ERROR = (100000, "value of interactive_input is invalid")
    INTERACTIVE_UPDATE_FAILED = (100001, "raw_inputs existed, update is invalid")
    COMPONENT_NOT_EXECUTABLE_ERROR = (100002, "workflow component should implement Executable")
    CONTROLLER_INTERRUPTED_ERROR = (100003, "controller interrupted error")
    INTERACTIVE_NOT_SUPPORT_STREAM_ERROR = (100004, "streaming process interface(transform or collect)"
                                            " does not support interact operations")
    COMPONENT_EXECUTE_ERROR = (100005, "component [{node_id}] encountered an exception while executing ability [{"
                                           "ability}], error detail: {error}")
    WORKFLOW_STATE_EXISTS_ERROR = (100006, "workflow state exists but non-interactive input and cleanup is disabled.")

    # Workflow: Execution 100100 - 100199
    WORKFLOW_EXECUTE_INNER_ERROR = (100100, "workflow execute inner error: {error}")
    WORKFLOW_INVOKE_TIMEOUT = (100101, "workflow invoke exceeded {timeout}s")
    WORKFLOW_STREAM_TIMEOUT = (100102, "workflow stream exceeded {timeout}s")

    # Workflow Component - Builtin-workflow Component 101000 - 109999

    ## LLMComponent  101000 - 101049
    LLM_COMPONENT_TEMPLATE_CONFIG_ERROR = (101000, "LLM component template config error, as {error_msg}.")
    LLM_COMPONENT_RESPONSE_FORMAT_CONFIG_ERROR = (101001, "LLM component response format config error, as {error_msg}.")
    LLM_COMPONENT_OUTPUT_CONFIG_ERROR = (101002, "LLM component output config error, as {error_msg}.")
    LLM_COMPONENT_INVOKE_LLM_ERROR = (101003, "LLM component invoke llm error, as {error_msg}.")
    LLM_COMPONENT_JSON_SCHEMA_OUTPUT_ERROR = (101004, "Failed to output json schema, as {error_msg}.")
    LLM_COMPONENT_INIT_LLM_ERROR = (101005, "Failed to init llm, as {error_msg}.")
    LLM_COMPONENT_ASSEMBLE_TEMPLATE_ERROR = (101006, "LLM component assemble template error, as {error_msg}.")
    PROMPT_JSON_SCHEMA_ERROR = (101007, "Invalid json schema, root cause = {error_msg}.")

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
    QUESTIONER_COMPONENT_PARSE_LLM_RESPONSE_ERROR = (101076, "Questioner component parse llm response error,"
                                                     " as {error_msg}.")

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
    LOOP_COMPONENT_NESTED_LOOP_ERROR = (101150, "Nested loops are not supported."
                                        " Cannot add LoopComponent to a LoopGroup")
    LOOP_COMPONENT_EXECUTION_ERROR = (101151, "Loop execution error: {error_msg}")
    LOOP_COMPONENT_EMPTY_GROUP_ERROR = (101152, "Loop group is empty, no components to execute")
    LOOP_COMPONENT_INPUT_TYPE_ERROR = (101153, "Inputs must be a dictionary, got {type}")
    LOOP_COMPONENT_MISSING_INPUT_KEY_ERROR = (101154, "Invalid inputs: missing required key {key}")
    LOOP_COMPONENT_INVALID_LOOP_TYPE_ERROR = (101155, "Invalid loop type '{loop_type}' for LoopComponent")
    LOOP_COMPONENT_MISSING_START_NODES_ERROR = (101156, "LoopGroup must have start_nodes configured")
    LOOP_COMPONENT_MISSING_END_NODES_ERROR = (101157, "LoopGroup must have end_nodes configured")

    ## BreakComponent  101180 - 101189
    BREAK_COMPONENT_INIT_ERROR = (101180, "Failed to initialize loop controller")

    ## ToolComponent  102000 - 102019
    TOOL_COMPONENT_BIND_TOOL_FAILED = (102000, "Tool component failed to bind a valid tool.")
    TOOL_COMPONENT_INPUTS_ERROR = (102001, "Tool component inputs error, as {error_msg}.")
    TOOL_COMPONENT_CHECK_PARAM_ERROR = (102002, "Tool component check parameter error, as {error_msg}.")

    ## StartComponent  102100 - 102119
    WORKFLOW_START_MISSING_GLOBAL_VARIABLE_VALUE = (102100, "start component: global variable(s)"
                                                    " defined with no value assigned:  {variable_name}")
    WORKFLOW_START_CREATE_VALUE = (102101, "start component create error:  {reason}")

    ## EndComponent  102120 - 102149
    WORKFLOW_END_CREATE_VALUE = (102120, "end component create error: {reason}")

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
    # Multi-Agent Orchestration - Single Runtime 131000 - 131999
    # Multi-Agent Orchestration - AgentGroup 132000 - 132999
    AGENT_GROUP_ADD_FAILED = (132000, "failed to add agent, reason: {reason}")
    AGENT_GROUP_CREATE_FAILED = (132001, "failed to create agent group, reason: {reason}")
    AGENT_GROUP_EXECUTION_ERROR = (132002, "failed to execute agent group, reason: {reason}")

    # Multi-Agent Orchestration - Multi-Agent Debug 133000 - 133999
    # Multi-Agent Orchestration - Distribution Runtime 134000 - 134999
    # Multi-Agent Orchestration - Multi-Agent Runner 131000-131030

    # Runner 134000 - 134999
    AGENT_NOT_FOUND = (134002, "Agent {} is not found")
    WORKFLOW_NOT_BOUND_TO_AGENT = (134003, "workflow not bound to agent")
    TOOL_NOT_BOUND_TO_AGENT = (134004, "tool not bound to agent")
    TOOL_NOT_FOUND = (134005, "Tool not found")

    # GraphEngine 140000 - 149999
    # GraphEngine - Graph Orchestration and Execution 140000 - 140999
    # GraphEngine - Conditional Evaluation 140000 - 140019
    EXPRESSION_CONDITION_SYNTAX_ERROR = (140000, "Expression condition has syntax error,"
                                         " expression as {expression}, error as {error_msg}.")
    EXPRESSION_CONDITION_EVAL_ERROR = (140001, "Expression condition eval error, as {error_msg}.")
    ARRAY_CONDITION_ERROR = (140002, "Array condition error")
    NUMBER_CONDITION_ERROR = (140003, "Number condition error")


    # ContextEngine 150000 - 159999
    # ContextEngine - Context Structured Storage and Retrieval 150000 - 150999
    # ContextEngine - Context Dynamic Assembly  151000 - 151999
    # ContextEngine - Context Asynchronous Processing 152000 - 152999
    # ContextEngine - Context Common 153000 - 153999
    CONTEXT_ENGINE_MESSAGE_PROCESS_ERROR = (153000, "Message process error: {error_msg}")

    # Development Toolchain 160000 - 169999
    # Development Toolchain - Prompt Generation 160000 - 160999
    # Development Toolchain - Agent DL convertor 161000 - 161999
    # Development Toolchain - NL2Agent 162000 - 162999

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
    PLUGIN_PROXY_CONNECT_ERROR = (182002, "Plugin restful api proxy connection error")
    PLUGIN_RESPONSE_TOO_BIG_ERROR = (182003, "Plugin restful api  response too big")
    PLUGIN_RESPONSE_HTTP_CODE_ERROR = (182004, "Plugin restful api http code error")
    PLUGIN_PARAMS_CHECK_FAILED = (182005, "Plugin params check failed")

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

    # Runtime 190000 - 199999
    # Runtime - Resource Management 190000 - 190999
    RUNTIME_WORKFLOW_GET_FAILED = (190001, "failed to get workflow, reason: {reason}")
    RUNTIME_WORKFLOW_ADD_FAILED = (190002, "failed to add workflow, reason: {reason}")
    RUNTIME_WORKFLOW_CONFIG_ADD_FAILED = (190011, "failed to add workflow config, reason: {reason}")
    RUNTIME_WORKFLOW_CONFIG_GET_FAILED = (190012, "failed to get workflow config, reason: {reason}")
    RUNTIME_WORKFLOW_TOOL_INFO_GET_FAILED = (190013, "failed to get toolInfo of workflow, reason: {reason}")

    # Runtime - Resource Management - Agent Group 190040 - 190049
    RUNTIME_AGENT_GROUP_ADD_FAILED = (190040, "failed to add agent group, reason: {reason}")
    RUNTIME_AGENT_GROUP_GET_FAILED = (190041, "failed to get agent group, reason: {reason}")
    RUNTIME_AGENT_GROUP_REMOVE_FAILED = (190042, "failed to remove agent group, reason: {reason}")
    
    # Runtime - Resource Management - Workflow Additional
    RUNTIME_WORKFLOW_REMOVE_FAILED = (190003, "failed to remove workflow, reason: {reason}")
    
    # Runtime - Resource Management - Agent 190050 - 190059
    RUNTIME_AGENT_ADD_FAILED = (190050, "failed to add agent, reason: {reason}")
    RUNTIME_AGENT_GET_FAILED = (190051, "failed to get agent, reason: {reason}")
    RUNTIME_AGENT_REMOVE_FAILED = (190052, "failed to remove agent, reason: {reason}")

    RUNTIME_TOOL_GET_FAILED = (190101, "failed to get tool, reason: {reason}")
    RUNTIME_TOOL_ADD_FAILED = (190102, "failed to add tool, reason: {reason}")
    RUNTIME_TOOL_TOOL_INFO_GET_FAILED = (190103, "failed to get toolInfo of tool, reason: {reason}")

    RUNTIME_PROMPT_GET_FAILED = (190201, "failed to get prompt template, reason: {reason}")
    RUNTIME_PROMPT_ADD_FAILED = (190202, "failed to add prompt template, reason: {reason}")

    RUNTIME_MODEL_GET_FAILED = (190301, "failed to get model, reason: {reason}")
    RUNTIME_MODEL_ADD_FAILED = (190302, "failed to add model, reason: {reason}")

    # Runtime - Tracer 191000 - 191999
    RUNTIME_TRACE_ERROR_FAILED = (191001, "failed to record error trace info, reason: {reason}")
    RUNTIME_TRACE_AGENT_UNDEFINED_FAILED = (191002, "Failed to handle undefined exception")

    # Runtime - State 192000 - 192999
    RUNTIME_STATE_RUNTIME_NONE = (192000, "Runtime is None, expected BaseRuntime instance")
    RUNTIME_STATE_INVALID_RUNTIME_TYPE = (192001, "Invalid runtime type: {runtime_type}, expected BaseRuntime")
    RUNTIME_STATE_INVALID_STATE_TYPE = (192002, "Invalid state type: {state_type}, expected CommitState")
    # Runtime - StreamWriter 193000 - 193999
    STREAM_WRITER_WRITE_SCHEMA_FAILED = (193001,
                                         "failed to write stream, stream schema validate failed, details: {detail}")
    STREAM_WRITER_WRITE_FAILED = (193002, "failed to write stream, reason: {reason}")
    STREAM_FRAME_TIMEOUT_FAILED = (193003, "stream frame is timeout ({timeout}s), no stream output")
    STREAM_FIRST_FRAME_TIMEOUT_FAILED = (193004, "stream first frame is timeout ({timeout}s), no stream output")
    STREAM_NO_INPUT_FAILED = (193005, "component has {abilities} ability, no stream input")

    # Runtime - Config 194000 - 194999
    # Runtime - callback 195000 - 195999
    # Runtime - Stream Actor 196000 - 196099
    WORKFLOW_MESSAGE_QUEUE_MANAGER_ERROR = (196000, "Message queue manager error: {error_msg}")

    # Runtime - Component Executable 196100 - 196199
    RUNTIME_COMPONENT_INVALID_RUNTIME_TYPE = (196100, "runtime should be NodeRuntime instance")
    RUNTIME_COMPONENT_ABILITY_NOT_IMPLEMENTED = (196101, "Component ability '{ability}' is registered but '{method}' "
                                                 "method is not implemented. Please implement the '{method}' method "
                                                 "in your component class '{class_name}'.")
    RUNTIME_COMPONENT_ABILITY_NOT_SUPPORTED = (196102, "{ability} is not supported")

    # Runtime - Checkpointer 197000 - 197099
    RUNTIME_CHECKPOINTER_NONE_WORKFLOW_STORE_ERROR = (197000, "workflow store is None")
    RUNTIME_CHECKPOINTER_NONE_AGENT_STORE_ERROR = (197001, "agent store is None")


    @property
    def code(self):
        return self.value[0]

    @property
    def errmsg(self):
        return self.value[1]
