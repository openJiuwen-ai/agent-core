# subagents

预配置子智能体工厂函数。每个工厂函数内部调用 `create_deep_agent()`，预注入特定领域的工具和 Rails。

---

## function create_browser_agent

```python
def create_browser_agent(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    subagents: Optional[List[SubAgentConfig | DeepAgent]] = None,
    rails: Optional[List[AgentRail]] = None,
    enable_task_loop: bool = False,
    max_iterations: int = 15,
    workspace: Optional[str | Workspace] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    settings: Optional[RuntimeSettings] = None,
    **config_kwargs: Any,
) -> DeepAgent
```

创建浏览器子智能体，配备 Playwright 运行时工具。自动注入 `BrowserRuntimeRail` 和浏览器运行时工具。

**参数**:

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `model` | `Model` | *(必填)* | 预构建的 Model 实例 |
| `card` | `Optional[AgentCard]` | `None` | 智能体身份卡。为 None 时创建 `name="browser_agent"` 的默认卡 |
| `system_prompt` | `Optional[str]` | `None` | 系统提示词。为 None 时使用浏览器专用默认提示词 |
| `tools` | `Optional[List[Tool \| ToolCard]]` | `None` | 额外工具（追加到浏览器运行时工具之后） |
| `mcps` | `Optional[List[McpServerConfig]]` | `None` | MCP 服务器配置 |
| `subagents` | `Optional[List[SubAgentConfig \| DeepAgent]]` | `None` | 子智能体配置 |
| `rails` | `Optional[List[AgentRail]]` | `None` | 额外 Rails（追加到 BrowserRuntimeRail 之后） |
| `settings` | `Optional[RuntimeSettings]` | `None` | Playwright 运行时设置。为 None 时从 Model 配置推导 |
| `**config_kwargs` | `Any` | — | 转发到 `create_deep_agent()` |

**返回值**: `DeepAgent` — 已配置的浏览器智能体实例。

---

## function create_code_agent

```python
def create_code_agent(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    subagents: Optional[List[SubAgentConfig | DeepAgent]] = None,
    rails: Optional[List[AgentRail]] = None,
    enable_task_loop: bool = False,
    max_iterations: int = 15,
    workspace: Optional[str | Workspace] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    **config_kwargs: Any,
) -> DeepAgent
```

创建编码子智能体，配备 `CodeTool` 和 `FileSystemRail`。擅长将任务转化为可运行的代码和可验证的结果。

**完全覆盖规则**: 显式传入 `tools` 或 `rails` 时不注入默认值。

**参数**:

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `model` | `Model` | *(必填)* | 预构建的 Model 实例 |
| `card` | `Optional[AgentCard]` | `None` | 智能体身份卡。为 None 时创建 `name="code_agent"` 的默认卡 |
| `system_prompt` | `Optional[str]` | `None` | 系统提示词。为 None 时使用编码专用默认提示词 |
| `tools` | `Optional[List[Tool \| ToolCard]]` | `None` | 工具列表。为 None 时注入 `CodeTool` |
| `rails` | `Optional[List[AgentRail]]` | `None` | Rails 列表。为 None 时注入 `FileSystemRail` |
| `**config_kwargs` | `Any` | — | 转发到 `create_deep_agent()` |

**返回值**: `DeepAgent` — 已配置的编码智能体实例。

---

## function create_research_agent

```python
def create_research_agent(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    subagents: Optional[List[SubAgentConfig | DeepAgent]] = None,
    rails: Optional[List[AgentRail]] = None,
    enable_task_loop: bool = False,
    max_iterations: int = 15,
    workspace: Optional[str | Workspace] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    **config_kwargs: Any,
) -> DeepAgent
```

创建研究子智能体，配备 `FileSystemRail` 和网页搜索工具。专注于研究调查任务，每次只处理一个主题。

**完全覆盖规则**: 显式传入 `rails` 时不注入默认值。

**参数**:

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `model` | `Model` | *(必填)* | 预构建的 Model 实例 |
| `card` | `Optional[AgentCard]` | `None` | 智能体身份卡。为 None 时创建 `name="research_agent"` 的默认卡 |
| `system_prompt` | `Optional[str]` | `None` | 系统提示词。为 None 时使用研究专用默认提示词 |
| `rails` | `Optional[List[AgentRail]]` | `None` | Rails 列表。为 None 时注入 `FileSystemRail` |
| `**config_kwargs` | `Any` | — | 转发到 `create_deep_agent()` |

**返回值**: `DeepAgent` — 已配置的研究智能体实例。
