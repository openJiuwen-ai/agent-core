# openjiuwen.core.single_agent

This module provides core components related to single Agent.

---

## class openjiuwen.core.single_agent.AgentCard

Agent card data class, used to describe the Agent's identity, description, and input/output parameters.

* **id** (str, optional): Unique identifier.
* **name** (str): Agent name, used for display and capability lookup.
* **description** (str): Agent description, used by LLM or upper layers when selecting capabilities.
* **input_params** (dict[str, Any] | Type[BaseModel], optional): Input parameter schema. Default value: `None`.
* **output_params** (dict[str, Any] | Type[BaseModel], optional): Output parameter schema. Default value: `None`.

### tool_info() -> ToolInfo

Convert the current AgentCard to `ToolInfo` for easy invocation as a sub-Agent or capability by other components.

**Returns:**

**ToolInfo**, tool information object containing name, description, and parameters.

---

## class openjiuwen.core.single_agent.BaseAgent

```
class openjiuwen.core.single_agent.BaseAgent(card: AgentCard)
```

Abstract base class for single Agent. When developers implement custom Agents, they need to inherit from this BaseAgent class.

**Parameters:**

* **card** (AgentCard): Agent card, defining Agent identity and input/output parameters.

### abstractmethod configure(config) -> 'BaseAgent'

Set runtime configuration. Subclasses must implement this, typically accepting configuration objects such as `ReActAgentConfig`.

**Parameters:**

* **config**: Configuration object.

**Returns:**

**BaseAgent**, self, supports chained calls.

### abstractmethod async invoke(inputs: Any, session: Optional[Session] = None) -> Any

Batch execution entry point. Subclasses must implement this.

**Parameters:**

* **inputs** (Any): Agent input, supports dict or str format.
* **session** (Session, optional): Session object. Default value: `None`.

**Returns:**

**Any**, Agent output result.

### abstractmethod async stream(inputs: Any, session: Optional[Session] = None, stream_modes: Optional[List[StreamMode]] = None) -> AsyncIterator[Any]

Streaming execution entry point. Subclasses must implement this.

**Parameters:**

* **inputs** (Any): Agent input.
* **session** (Session, optional): Session object. Default value: `None`.
* **stream_modes** (List[StreamMode], optional): List of streaming output modes. Default value: `None`.

**Returns:**

**AsyncIterator[Any]**, async iterator.


---

## class openjiuwen.core.single_agent.AbilityManager

```
class openjiuwen.core.single_agent.AbilityManager()
```

Agent capability manager, storing capability metadata (Card), providing interfaces for adding, removing, querying, and executing capability calls.

### add(ability: Union[Ability, List[Ability]]) -> None

Add one or more capabilities.

**Parameters:**

* **ability** (Ability | List[Ability]): Capability Card or Card list. Supported types: `ToolCard`, `WorkflowCard`, `AgentCard`, `McpServerConfig`.

### remove(name: Union[str, List[str]]) -> Union[None, Ability, List[Ability]]

Remove capabilities by name.

**Parameters:**

* **name** (str | List[str]): Capability name or list of names.

**Returns:**

**Ability | List[Ability] | None**, removed Card.

### get(name: str) -> Optional[Ability]

Get capability Card by name.

**Parameters:**

* **name** (str): Capability name.

**Returns:**

**Ability | None**, corresponding Card.

### list() -> List[Ability]

List all current capability Cards.

**Returns:**

**List[Ability]**, list of all Cards.

### async list_tool_info(names: Optional[List[str]] = None, mcp_server_name: Optional[str] = None) -> List[ToolInfo]

Convert capabilities to a list of `ToolInfo` for LLM use.

**Parameters:**

* **names** (List[str], optional): Filter capabilities by specified names. Default value: `None`, meaning all.
* **mcp_server_name** (str, optional): Filter tools from specified MCP server. Default value: `None`.

**Returns:**

**List[ToolInfo]**, list of tool information.

### async execute(tool_call: Union[ToolCall, List[ToolCall]], session: Session) -> List[Tuple[Any, ToolMessage]]

Execute capability calls.

**Parameters:**

* **tool_call** (ToolCall | List[ToolCall]): Tool call, supports single or list.
* **session** (Session): Session instance.

**Returns:**

**List[Tuple[Any, ToolMessage]]**, list of tuples containing execution results and ToolMessage.

---

## class openjiuwen.core.single_agent.ReActAgent

```
class openjiuwen.core.single_agent.ReActAgent(card: AgentCard)
```

ReAct (Reasoning + Acting) paradigm Agent implementation.

**Parameters:**

* **card** (AgentCard): Agent card.

### configure(config: ReActAgentConfig) -> 'BaseAgent'

Set configuration.

**Parameters:**

* **config** (ReActAgentConfig): Configuration object.

**Returns:**

**BaseAgent**, self, supports chained calls.

### async register_skill(skill_path: Union[str, List[str]])

Register skills.

**Parameters:**

* **skill_path** (str | List[str]): Skill path or list of paths.

### async invoke(inputs: Any, session: Optional[Session] = None) -> Dict[str, Any]

Execute ReAct flow.

**Parameters:**

* **inputs** (Any): User input, supports `{"query": "..."}` or str.
* **session** (Session, optional): Session object. Default value: `None`.

**Returns:**

**Dict[str, Any]**, dictionary containing `output` and `result_type`.

### async stream(inputs: Any, session: Optional[Session] = None, stream_modes: Optional[List[StreamMode]] = None) -> AsyncIterator[Any]

Streaming execution of ReAct flow.

**Parameters:**

* **inputs** (Any): User input.
* **session** (Session, optional): Session object. Default value: `None`.
* **stream_modes** (List[StreamMode], optional): Streaming output modes. Default value: `None`.

**Returns:**

**AsyncIterator[Any]**, streaming output iterator.


---

## class openjiuwen.core.single_agent.ReActAgentConfig

ReActAgent configuration class, providing chained configuration methods.

* **mem_scope_id** (str): Memory scope ID. Default value: `""`.
* **model_name** (str): Model name. Default value: `""`.
* **model_provider** (str): Model provider. Default value: `"openai"`.
* **api_key** (str): API key. Default value: `""`.
* **api_base** (str): API base URL. Default value: `""`.
* **prompt_template_name** (str): Prompt template name. Default value: `""`.
* **prompt_template** (List[Dict]): Prompt template list, format like `[{"role": "system", "content": "..."}]`. Default value: `[]`.
* **max_iterations** (int): Maximum number of ReAct loop iterations. Default value: `5`.
* **model_client_config** (ModelClientConfig, optional): Model client configuration. Default value: `None`.
* **model_config_obj** (ModelRequestConfig, optional): Model request configuration. Default value: `None`.
* **context_engine_config** (ContextEngineConfig): Context engine configuration. Default value: `max_context_message_num=200, default_window_round_num=10`.

### configure_model(model_name: str) -> 'ReActAgentConfig'

Configure model name.

**Parameters:**

* **model_name** (str): Model name.

**Returns:**

**ReActAgentConfig**, self, supports chained calls.

### configure_model_provider(provider: str, api_key: str, api_base: str) -> 'ReActAgentConfig'

Configure model provider.

**Parameters:**

* **provider** (str): Model provider name (e.g., `"openai"`).
* **api_key** (str): API key.
* **api_base** (str): API base URL.

**Returns:**

**ReActAgentConfig**, self, supports chained calls.

### configure_prompt(prompt_name: str) -> 'ReActAgentConfig'

Configure Prompt template name.

**Parameters:**

* **prompt_name** (str): Prompt template name.

**Returns:**

**ReActAgentConfig**, self, supports chained calls.

### configure_prompt_template(prompt_template: List[Dict]) -> 'ReActAgentConfig'

Configure Prompt template.

**Parameters:**

* **prompt_template** (List[Dict]): Prompt template list, format like `[{"role": "system", "content": "..."}]`.

**Returns:**

**ReActAgentConfig**, self, supports chained calls.

### configure_context_engine(max_context_message_num: Optional[int] = 200, default_window_round_num: Optional[int] = 10, enable_reload: bool = False) -> 'ReActAgentConfig'

Configure context engine parameters.

**Parameters:**

* **max_context_message_num** (int, optional): Maximum number of messages to retain in context window. Default value: `200`.
* **default_window_round_num** (int, optional): Number of recent dialogue rounds to retain. Default value: `10`.
* **enable_reload** (bool, optional): Whether to allow automatic reloading of previously offloaded messages. Default value: `False`.

**Returns:**

**ReActAgentConfig**, self, supports chained calls.

### configure_mem_scope(mem_scope_id: str) -> 'ReActAgentConfig'

Configure memory scope ID.

**Parameters:**

* **mem_scope_id** (str): Memory scope ID.

**Returns:**

**ReActAgentConfig**, self, supports chained calls.

### configure_max_iterations(max_iterations: int) -> 'ReActAgentConfig'

Configure maximum number of iterations.

**Parameters:**

* **max_iterations** (int): Maximum number of ReAct loop iterations.

**Returns:**

**ReActAgentConfig**, self, supports chained calls.

### configure_model_client(provider: str, api_key: str, api_base: str, model_name: str, verify_ssl: bool = False) -> 'ReActAgentConfig'

Configure model client for LLM initialization.

**Parameters:**

* **provider** (str): Model provider name (e.g., `"OpenAI"`).
* **api_key** (str): API key.
* **api_base** (str): API base URL.
* **model_name** (str): Model name.
* **verify_ssl** (bool, optional): Whether to verify SSL. Default value: `False`.

**Returns:**

**ReActAgentConfig**, self, supports chained calls.

---

## class openjiuwen.core.single_agent.Session

```
class openjiuwen.core.single_agent.Session(session_id: str = None, envs: dict[str, Any] = None, card: AgentCard = None)
```

Agent session class, providing session management and streaming output functionality.

**Parameters:**

* **session_id** (str, optional): Session ID, automatically generated if `None`. Default value: `None`.
* **envs** (dict[str, Any], optional): Environment variable dictionary. Default value: `None`.
* **card** (AgentCard, optional): Agent card. Default value: `None`.

### get_session_id() -> str

Get session ID.

**Returns:**

**str**, session ID.

### get_envs() -> dict

Get environment variables.

**Returns:**

**dict**, environment variable dictionary.

### get_agent_id() -> str

Get associated Agent ID.

**Returns:**

**str**, Agent's id.

### get_agent_name() -> str

Get associated Agent name.

**Returns:**

**str**, Agent's name.

### get_agent_description() -> str

Get associated Agent description.

**Returns:**

**str**, Agent's description.

### async write_stream(data: Union[dict, OutputSchema])

Write streaming output data.

**Parameters:**

* **data** (dict | OutputSchema): Data to write.

### async write_custom_stream(data: dict)

Write custom streaming output data.

**Parameters:**

* **data** (dict): Custom data to write.

### stream_iterator() -> AsyncIterator[Any]

Get streaming output iterator.

**Returns:**

**AsyncIterator[Any]**, async iterator.

### async post_run()

Post-execution processing, close streaming output.

### create_workflow_session() -> WorkflowSession

Create workflow sub-session.

**Returns:**

**WorkflowSession**, workflow session instance.

### async interact(value)

Send interaction data to session.

**Parameters:**

* **value**: Interaction data.

---

## func openjiuwen.core.single_agent.create_agent_session(session_id: str = None, envs: dict[str, Any] = None, card: AgentCard = None) -> Session

Factory function to create Agent session.

**Parameters:**

* **session_id** (str, optional): Session ID, automatically generated if `None`. Default value: `None`.
* **envs** (dict[str, Any], optional): Environment variable dictionary. Default value: `None`.
* **card** (AgentCard, optional): Agent card. Default value: `None`.

**Returns:**

**Session**, created session instance.
