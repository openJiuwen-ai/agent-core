# openjiuwen.core.application.llm_agent.llm_agent

## class openjiuwen.core.application.llm_agent.llm_agent.LLMAgent

```
class openjiuwen.core.application.llm_agent.llm_agent.LLMAgent(agent_config: ReActAgentConfig)
```

ReAct-style agent based on the new architecture, internally using a controller to complete the "think-plan-execute-report" process, with optional long-term memory integration.

### async invoke(inputs: Dict, session: Session = None) -> Dict

Execute the agent and return the complete result. Supports asynchronous long-term memory writing.

**Parameters**:
* **inputs** (Dict): Must contain `query`, recommended to include `conversation_id` (default "default_session") and `user_id` (required for memory writing).
* **session** (Session, optional): Existing session; if not provided, one will be created automatically.

**Returns**:
* **Dict**: Controller execution result (content depends on task planning and execution).

### async stream(inputs: Dict, session: Session = None) -> AsyncIterator[Any]

Stream execution of the agent, producing `OutputSchema` chunks in real time. Automatically manages session lifecycle and supports asynchronous long-term memory writing.

**Parameters**:

* **inputs** (Dict): Same as `invoke`, requires `query`, optional `conversation_id`/`user_id`.
* **session** (Session, optional): External session; if not provided, one will be created internally and lifecycle will be managed.

**Returns**:

* **AsyncIterator[Any]**: Streaming output chunks.

**Exceptions**:

* **RuntimeError**: Controller not initialized.

### set_prompt_template(prompt_template: List[Dict]) -> None

Update the prompt template for Agent and LLMController, and refresh the internal configuration wrapper.

**Parameters**:

* **prompt_template** (List[Dict]): New prompt template list.

### async _write_messages_to_memory(inputs, result=None) -> None

Write user queries and model responses to long-term memory. Supports multiple output formats (`OutputSchema`/dict/str).

**Parameters**:

* **inputs**: Original input dictionary, must contain `user_id`.
* **result** (optional): Controller return result or streamed concatenated text.

## func openjiuwen.core.application.llm_agent.llm_agent.create_llm_agent_config(agent_id: str, agent_version: str, description: str, workflows: List[WorkflowSchema], plugins: List[PluginSchema], model: ModelConfig, prompt_template: List[Dict], tools: Optional[List[str]] = None)

Create a configuration object for LLM ReAct Agent (compatible with legacy interface).

**Parameters**:
- **agent_id** (str): Unique identifier for the Agent.
- **agent_version** (str): Version number of the Agent.
- **description** (str): Description of the Agent.
- **workflows** (List[WorkflowSchema]): List of workflow descriptions supported by the Agent.
- **plugins** (List[PluginSchema]): List of plugin descriptions supported by the Agent.
- **model** (ModelConfig): Model configuration used by the Agent.
- **prompt_template** (List[Dict]): Prompt template list, e.g., `{ "role": "system", "content": "..." }`; `role` can be `system` or `user`, default value: `[]`.
- **tools** (List[str], optional): List of available tool IDs, default value: `None` (will be converted to empty list internally).

**Returns**:
- **ReActAgentConfig**: New ReAct Agent configuration object.

## func openjiuwen.core.application.llm_agent.llm_agent.create_llm_agent(agent_config: ReActAgentConfig, workflows: List[Workflow] = None, tools: List[Tool] = None)

Convenience factory function to create LLM ReAct Agent based on configuration and batch register workflows and tools.

**Parameters**:
- **agent_config** (ReActAgentConfig): Constructed ReAct Agent configuration object.
- **workflows** (List[Workflow], optional): List of workflow instances to register, default `None` (converted to empty list internally).
- **tools** (List[Tool], optional): List of tool instances to register, default `None` (converted to empty list internally).

**Returns**:
- **LLMAgent**: Initialized LLM Agent instance with workflows/tools registered.


# openjiuwen.core.single_agent.legacy.config

## class openjiuwen.core.single_agent.legacy.config.ConstrainConfig

Data model representing agent behavior constraint configuration.

- **reserved_max_chat_rounds** (int): Maximum allowed conversation rounds, must be greater than 0. Default value: 10.
- **max_iteration** (int): Maximum iteration count for a single task, must be greater than 0. Default value: 5.

## class openjiuwen.core.single_agent.legacy.config.IntentDetectionConfig

Data model for intent detection configuration.

- **intent_detection_template** (List[Dict]): Intent classification prompt template list, default empty list.
- **default_class** (str): Default classification label, default value: "分类1".
- **enable_input** (bool): Whether to enable current input content for identification, default value: True.
- **enable_history** (bool): Whether to use chat history to assist identification, default value: False.
- **chat_history_max_turn** (int): Maximum number of history turns to use, default value: 5.
- **category_list** (List[str]): Optional classification label list, default empty list.
- **user_prompt** (str): User prompt template, default empty string.
- **example_content** (List[str]): Example content list for few-shot guidance, default empty list.

## class openjiuwen.core.single_agent.legacy.config.LegacyReActAgentConfig

ReAct Agent configuration compatible with legacy interface.

- **controller_type** (ControllerType): Controller type, default `ReActController`.
- **prompt_template_name** (str): Prompt template name, default value: `"react_system_prompt"`.
- **prompt_template** (List[Dict]): Prompt template list, default empty list.
- **constrain** (ConstrainConfig): Behavior constraint configuration, default `ConstrainConfig()`.
- **plugins** (List[Any]): Plugin schema list, default empty list.
- **memory_scope_id** (str): Long-term memory scope ID, default empty string.
- **agent_memory_config** (AgentMemoryConfig): Long-term memory configuration, default constructed.
