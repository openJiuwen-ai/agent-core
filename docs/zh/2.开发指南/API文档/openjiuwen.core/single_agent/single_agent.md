# openjiuwen.core.single_agent

本模块提供单 Agent 相关核心组件。

---

## class openjiuwen.core.single_agent.AgentCard

Agent 卡片数据类，用于描述 Agent 的身份、说明与输入输出参数。

* **id**(str，可选)：唯一标识。
* **name**(str)：Agent 名称，用于展示与能力查找。
* **description**(str)：Agent 说明，供 LLM 或上层选择能力时使用。
* **input_params**(dict[str, Any] | Type[BaseModel]，可选)：输入参数 schema。默认值：`None`。
* **output_params**(dict[str, Any] | Type[BaseModel]，可选)：输出参数 schema。默认值：`None`。

### tool_info() -> ToolInfo

将当前 AgentCard 转为 `ToolInfo`，便于作为子 Agent 或能力被其他组件调用。

**返回**：

**ToolInfo**，包含 name、description、parameters 的工具信息对象。

---

## class openjiuwen.core.single_agent.BaseAgent

```
class openjiuwen.core.single_agent.BaseAgent(card: AgentCard)
```

单 Agent 抽象基类。开发者实现自定义 Agent 时，需要继承 BaseAgent 这个基类。

**参数**：

* **card**(AgentCard)：Agent 卡片，定义 Agent 身份与输入输出参数。

### abstractmethod configure(config) -> 'BaseAgent'

设置运行时配置。子类需实现，通常接受如 `ReActAgentConfig` 等配置对象。

**参数**：

* **config**：配置对象。

**返回**：

**BaseAgent**，self，支持链式调用。

### abstractmethod async invoke(inputs: Any, session: Optional[Session] = None) -> Any

批量执行入口。子类需实现。

**参数**：

* **inputs**(Any)：Agent 输入，支持 dict 或 str 格式。
* **session**(Session，可选)：会话对象。默认值：`None`。

**返回**：

**Any**，Agent 输出结果。

### abstractmethod async stream(inputs: Any, session: Optional[Session] = None, stream_modes: Optional[List[StreamMode]] = None) -> AsyncIterator[Any]

流式执行入口。子类需实现。

**参数**：

* **inputs**(Any)：Agent 输入。
* **session**(Session，可选)：会话对象。默认值：`None`。
* **stream_modes**(List[StreamMode]，可选)：流式输出模式列表。默认值：`None`。

**返回**：

**AsyncIterator[Any]**，异步迭代器。


---

## class openjiuwen.core.single_agent.AbilityManager

```
class openjiuwen.core.single_agent.AbilityManager()
```

Agent 能力管理器，存储能力元数据（Card），提供增删查与执行能力调用的接口。

### add(ability: Union[Ability, List[Ability]]) -> None

添加一项或多项能力。

**参数**：

* **ability**(Ability | List[Ability])：能力 Card 或 Card 列表。支持类型：`ToolCard`、`WorkflowCard`、`AgentCard`、`McpServerConfig`。

### remove(name: Union[str, List[str]]) -> Union[None, Ability, List[Ability]]

按名称移除能力。

**参数**：

* **name**(str | List[str])：能力名称或名称列表。

**返回**：

**Ability | List[Ability] | None**，被移除的 Card。

### get(name: str) -> Optional[Ability]

按名称获取能力 Card。

**参数**：

* **name**(str)：能力名称。

**返回**：

**Ability | None**，对应的 Card。

### list() -> List[Ability]

列出当前所有能力 Card。

**返回**：

**List[Ability]**，所有 Card 的列表。

### async list_tool_info(names: Optional[List[str]] = None, mcp_server_name: Optional[str] = None) -> List[ToolInfo]

将能力转换为供 LLM 使用的 `ToolInfo` 列表。

**参数**：

* **names**(List[str]，可选)：筛选指定名称的能力。默认值：`None`，表示全部。
* **mcp_server_name**(str，可选)：筛选指定 MCP 服务器的工具。默认值：`None`。

**返回**：

**List[ToolInfo]**，工具信息列表。

### async execute(tool_call: Union[ToolCall, List[ToolCall]], session: Session) -> List[Tuple[Any, ToolMessage]]

执行能力调用。

**参数**：

* **tool_call**(ToolCall | List[ToolCall])：工具调用，支持单个或列表。
* **session**(Session)：会话实例。

**返回**：

**List[Tuple[Any, ToolMessage]]**，执行结果与 ToolMessage 的元组列表。

---

## class openjiuwen.core.single_agent.ReActAgent

```
class openjiuwen.core.single_agent.ReActAgent(card: AgentCard)
```

ReAct（Reasoning + Acting）范式 Agent 实现。

**参数**：

* **card**(AgentCard)：Agent 卡片。

### configure(config: ReActAgentConfig) -> 'BaseAgent'

设置配置。

**参数**：

* **config**(ReActAgentConfig)：配置对象。

**返回**：

**BaseAgent**，self，支持链式调用。

### async register_skill(skill_path: Union[str, List[str]])

注册技能。

**参数**：

* **skill_path**(str | List[str])：技能路径或路径列表。

### async invoke(inputs: Any, session: Optional[Session] = None) -> Dict[str, Any]

执行 ReAct 流程。

**参数**：

* **inputs**(Any)：用户输入，支持 `{"query": "..."}` 或 str。
* **session**(Session，可选)：会话对象。默认值：`None`。

**返回**：

**Dict[str, Any]**，包含 `output` 和 `result_type` 的字典。

### async stream(inputs: Any, session: Optional[Session] = None, stream_modes: Optional[List[StreamMode]] = None) -> AsyncIterator[Any]

流式执行 ReAct 流程。

**参数**：

* **inputs**(Any)：用户输入。
* **session**(Session，可选)：会话对象。默认值：`None`。
* **stream_modes**(List[StreamMode]，可选)：流式输出模式。默认值：`None`。

**返回**：

**AsyncIterator[Any]**，流式输出迭代器。


---

## class openjiuwen.core.single_agent.ReActAgentConfig

ReActAgent 配置类，提供链式配置方法。

* **mem_scope_id**(str)：记忆作用域 ID。默认值：`""`。
* **model_name**(str)：模型名称。默认值：`""`。
* **model_provider**(str)：模型提供商。默认值：`"openai"`。
* **api_key**(str)：API 密钥。默认值：`""`。
* **api_base**(str)：API 基础 URL。默认值：`""`。
* **prompt_template_name**(str)：Prompt 模板名称。默认值：`""`。
* **prompt_template**(List[Dict])：Prompt 模板列表，格式如 `[{"role": "system", "content": "..."}]`。默认值：`[]`。
* **max_iterations**(int)：ReAct 循环最大迭代次数。默认值：`5`。
* **model_client_config**(ModelClientConfig，可选)：模型客户端配置。默认值：`None`。
* **model_config_obj**(ModelRequestConfig，可选)：模型请求配置。默认值：`None`。
* **context_engine_config**(ContextEngineConfig)：上下文引擎配置。默认值：`max_context_message_num=200, default_window_round_num=10`。

### configure_model(model_name: str) -> 'ReActAgentConfig'

配置模型名称。

**参数**：

* **model_name**(str)：模型名称。

**返回**：

**ReActAgentConfig**，self，支持链式调用。

### configure_model_provider(provider: str, api_key: str, api_base: str) -> 'ReActAgentConfig'

配置模型提供商。

**参数**：

* **provider**(str)：模型提供商名称（如 `"openai"`）。
* **api_key**(str)：API 密钥。
* **api_base**(str)：API 基础 URL。

**返回**：

**ReActAgentConfig**，self，支持链式调用。

### configure_prompt(prompt_name: str) -> 'ReActAgentConfig'

配置 Prompt 模板名称。

**参数**：

* **prompt_name**(str)：Prompt 模板名称。

**返回**：

**ReActAgentConfig**，self，支持链式调用。

### configure_prompt_template(prompt_template: List[Dict]) -> 'ReActAgentConfig'

配置 Prompt 模板。

**参数**：

* **prompt_template**(List[Dict])：Prompt 模板列表，格式如 `[{"role": "system", "content": "..."}]`。

**返回**：

**ReActAgentConfig**，self，支持链式调用。

### configure_context_engine(max_context_message_num: Optional[int] = 200, default_window_round_num: Optional[int] = 10, enable_reload: bool = False) -> 'ReActAgentConfig'

配置上下文引擎参数。

**参数**：

* **max_context_message_num**(int，可选)：上下文窗口保留的最大消息数。默认值：`200`。
* **default_window_round_num**(int，可选)：保留的最近对话轮数。默认值：`10`。
* **enable_reload**(bool，可选)：是否允许自动重新加载之前卸载的消息。默认值：`False`。

**返回**：

**ReActAgentConfig**，self，支持链式调用。

### configure_mem_scope(mem_scope_id: str) -> 'ReActAgentConfig'

配置记忆作用域 ID。

**参数**：

* **mem_scope_id**(str)：记忆作用域 ID。

**返回**：

**ReActAgentConfig**，self，支持链式调用。

### configure_max_iterations(max_iterations: int) -> 'ReActAgentConfig'

配置最大迭代次数。

**参数**：

* **max_iterations**(int)：ReAct 循环最大迭代次数。

**返回**：

**ReActAgentConfig**，self，支持链式调用。

### configure_model_client(provider: str, api_key: str, api_base: str, model_name: str, verify_ssl: bool = False) -> 'ReActAgentConfig'

配置模型客户端，用于 LLM 初始化。

**参数**：

* **provider**(str)：模型提供商名称（如 `"OpenAI"`）。
* **api_key**(str)：API 密钥。
* **api_base**(str)：API 基础 URL。
* **model_name**(str)：模型名称。
* **verify_ssl**(bool，可选)：是否验证 SSL。默认值：`False`。

**返回**：

**ReActAgentConfig**，self，支持链式调用。

---

## class openjiuwen.core.single_agent.Session

```
class openjiuwen.core.single_agent.Session(session_id: str = None, envs: dict[str, Any] = None, card: AgentCard = None)
```

Agent 会话类，提供会话管理与流式输出功能。

**参数**：

* **session_id**(str，可选)：会话 ID，若为 `None` 则自动生成。默认值：`None`。
* **envs**(dict[str, Any]，可选)：环境变量字典。默认值：`None`。
* **card**(AgentCard，可选)：Agent 卡片。默认值：`None`。

### get_session_id() -> str

获取会话 ID。

**返回**：

**str**，会话 ID。

### get_env

```python
get_env(self, key) -> Optional[Any]
```

获取本次Agent执行配置的环境变量的值。

**参数**：

- **key** (str)：环境变量的键。

**返回**：

**Optional[Any]**，为本次Agent执行配置的环境变量的值。

### get_envs() -> dict

获取环境变量。

**返回**：

**dict**，环境变量字典。

### get_agent_id() -> str

获取关联的 Agent ID。

**返回**：

**str**，Agent 的 id。

### get_agent_name() -> str

获取关联的 Agent 名称。

**返回**：

**str**，Agent 的 name。

### get_agent_description() -> str

获取关联的 Agent 描述。

**返回**：

**str**，Agent 的 description。

### update_state

```python
update_state(self, data: dict)
```

在Agent执行过程中更新状态，表示更新当前Agent的状态数据，若不存在字段会新增，并且立即生效。

**参数**：

- **data**(dict)：新增或更新的键值对字典。若data为None或{}，表示不更新。


### get_state

```python
get_state(self, key: Union[str, list, dict] = None) -> Any
```

在Agent执行过程中获取状态信息，表示获取本Agent的状态信息。

**参数**：

- **key** (Union[str, list, dict], 可选)：查询状态数据中对应value的key，默认`None`，表示获取全部的状态信息。根据`key`的取值类型，支持多种方式获取状态数据。
  - 当`key`为`str`类型，表示获取`key`路径下的状态数据，key可以为嵌套路径结构，例如`"user_inputs.query"`。
  - 当`key`为`dict`\ `list`类型，表示获取多个状态数据，该`dict`\ `list`中存在多个用`"${}"`包裹的变量，每个变量都是一个状态数据的路径。

**返回**：

**Any**，返回的状态值。根据`key`的类型的不同，返回结果为：

- 当`key`为`str`类型，返回为该路径下的状态信息，若不存在，则返回`None`。
- 当`key`为`dict`\ `list`类型，该接口会将输入的`dict`\ `list`中所有`${}`内的变量替换成相应路径的变量的值，若对应变量路径不存在，则替换为`None`，最终返回为替换完的结果。

### async write_stream(data: Union[dict, OutputSchema])

写入流式输出数据。

**参数**：

* **data**(dict | OutputSchema)：要写入的数据。

### async write_custom_stream(data: dict)

写入自定义流式输出数据。

**参数**：

* **data**(dict)：要写入的自定义数据。

### stream_iterator() -> AsyncIterator[Any]

获取流式输出迭代器。

**返回**：

**AsyncIterator[Any]**，异步迭代器。

### async pre_run()

执行前处理，创建检查点。

### async post_run()

执行后处理，关闭流式输出。

### create_workflow_session() -> WorkflowSession

创建工作流子会话。

**返回**：

**WorkflowSession**，工作流会话实例。

### async interact(value)

向会话发送交互数据。

**参数**：

* **value**：交互数据。

---

## func openjiuwen.core.single_agent.create_agent_session(session_id: str = None, envs: dict[str, Any] = None, card: AgentCard = None) -> Session

创建 Agent 会话的工厂函数。

**参数**：

* **session_id**(str，可选)：会话 ID，若为 `None` 则自动生成。默认值：`None`。
* **envs**(dict[str, Any]，可选)：环境变量字典。默认值：`None`。
* **card**(AgentCard，可选)：Agent 卡片。默认值：`None`。

**返回**：

**Session**，创建的会话实例。
