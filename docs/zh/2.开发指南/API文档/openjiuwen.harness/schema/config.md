# schema.config

配置数据类，用于 DeepAgent 及其子组件的运行时参数。

---

## class VisionModelConfig

```python
@dataclass
class VisionModelConfig:
    api_key: str = ""
    base_url: str = DEFAULT_OPENAI_BASE_URL
    model: str = DEFAULT_OPENAI_VISION_MODEL
    max_retries: int = 3
```

所有 DeepAgent 视觉工具的共享运行时配置。

**属性**:

| 属性 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `api_key` | `str` | `""` | API 密钥 |
| `base_url` | `str` | `"https://api.openai.com/v1"` | API 基础 URL |
| `model` | `str` | `"gpt-4.1-mini"` | 视觉模型名称 |
| `max_retries` | `int` | `3` | 最大重试次数 |

### from_env

```python
@classmethod
def from_env(cls) -> VisionModelConfig
```

从环境变量构建视觉配置。按优先级依次读取 `VISION_API_KEY` → `OPENROUTER_API_KEY` → `OPENAI_API_KEY`。

**返回值**: `VisionModelConfig` — 从环境变量构建的配置实例。

---

## class AudioModelConfig

```python
@dataclass
class AudioModelConfig:
    api_key: str = ""
    base_url: str = DEFAULT_OPENAI_BASE_URL
    transcription_model: str = DEFAULT_OPENAI_AUDIO_TRANSCRIPTION_MODEL
    question_answering_model: str = DEFAULT_OPENAI_AUDIO_QA_MODEL
    max_retries: int = 3
    http_timeout: int = 20
    max_audio_bytes: int = 25 * 1024 * 1024
    acr_access_key: str = ""
    acr_access_secret: str = ""
    acr_base_url: str = DEFAULT_ACR_BASE_URL
```

所有 DeepAgent 音频工具的共享运行时配置。

**属性**:

| 属性 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `api_key` | `str` | `""` | API 密钥 |
| `base_url` | `str` | `"https://api.openai.com/v1"` | API 基础 URL |
| `transcription_model` | `str` | `"gpt-4o-transcribe"` | 转录模型名称 |
| `question_answering_model` | `str` | `"gpt-4o-audio-preview"` | 音频问答模型名称 |
| `max_retries` | `int` | `3` | 最大重试次数 |
| `http_timeout` | `int` | `20` | HTTP 超时秒数 |
| `max_audio_bytes` | `int` | `26214400` | 最大音频文件字节数（25 MB） |
| `acr_access_key` | `str` | `""` | ACRCloud 访问密钥 |
| `acr_access_secret` | `str` | `""` | ACRCloud 访问密钥 |
| `acr_base_url` | `str` | `"https://identify-ap-southeast-1.acrcloud.com/v1/identify"` | ACRCloud 基础 URL |

### from_env

```python
@classmethod
def from_env(cls) -> AudioModelConfig
```

从环境变量构建音频配置。

**返回值**: `AudioModelConfig` — 从环境变量构建的配置实例。

---

## class DeepAgentConfig

```python
@dataclass
class DeepAgentConfig:
    model: Optional[Model] = None
    card: Optional[AgentCard] = None
    system_prompt: Optional[str] = None
    context_engine_config: Optional[Any] = None
    enable_task_loop: bool = False
    enable_async_subagent: bool = False
    add_general_purpose_agent: bool = False
    max_iterations: int = 15
    subagents: Optional[List[SubAgentConfig | DeepAgent]] = None
    tools: Optional[List[ToolCard]] = None
    mcps: Optional[List[McpServerConfig]] = None
    workspace: Optional[Workspace] = None
    skills: Optional[Union[str, List[str]]] = None
    backend: Optional[Any] = None
    sys_operation: Optional[SysOperation] = None
    auto_create_workspace: bool = True
    completion_timeout: float = 600.0
    language: Optional[str] = None
    prompt_mode: Optional[str] = None
    vision_model_config: Optional[VisionModelConfig] = None
    audio_model_config: Optional[AudioModelConfig] = None
    rails: Optional[List[AgentRail]] = None
    progressive_tool_enabled: bool = False
    progressive_tool_always_visible_tools: List[str] = field(default_factory=list)
    progressive_tool_default_visible_tools: List[str] = field(default_factory=list)
    progressive_tool_max_loaded_tools: int = 12
```

DeepAgent 运行时配置。

**属性**:

| 属性 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `model` | `Optional[Model]` | `None` | 预构建的 LLM Model 实例 |
| `card` | `Optional[AgentCard]` | `None` | 智能体身份卡 |
| `system_prompt` | `Optional[str]` | `None` | 注入内部 ReActAgent 提示模板的系统提示词 |
| `context_engine_config` | `Optional[Any]` | `None` | 上下文工程配置，设置后应用为内部 ReActAgent 的 `ContextEngineConfig` |
| `enable_task_loop` | `bool` | `False` | 是否启用外层任务循环 |
| `enable_async_subagent` | `bool` | `False` | 启用通过 SessionRail 的异步子智能体 |
| `add_general_purpose_agent` | `bool` | `False` | 为 True 时自动添加通用子智能体 |
| `max_iterations` | `int` | `15` | 每次 invoke 的最大 ReAct 迭代次数 |
| `subagents` | `Optional[List[SubAgentConfig \| DeepAgent]]` | `None` | 子智能体规格或实例列表 |
| `tools` | `Optional[List[ToolCard]]` | `None` | 挂载的工具卡列表 |
| `mcps` | `Optional[List[McpServerConfig]]` | `None` | 挂载的 MCP 服务器配置 |
| `workspace` | `Optional[Workspace]` | `None` | 文件操作的工作区 |
| `skills` | `Optional[Union[str, List[str]]]` | `None` | 技能定义 |
| `backend` | `Optional[Any]` | `None` | 后端协议实例 |
| `sys_operation` | `Optional[SysOperation]` | `None` | 系统操作实例 |
| `auto_create_workspace` | `bool` | `True` | 是否自动创建工作区目录 |
| `completion_timeout` | `float` | `600.0` | 单次任务循环迭代的最大等待秒数 |
| `language` | `Optional[str]` | `None` | 提示词语言 |
| `prompt_mode` | `Optional[str]` | `None` | 提示词模式 |
| `vision_model_config` | `Optional[VisionModelConfig]` | `None` | 视觉模型配置 |
| `audio_model_config` | `Optional[AudioModelConfig]` | `None` | 音频模型配置 |
| `rails` | `Optional[List[AgentRail]]` | `None` | 要注册的 Rails 列表 |
| `progressive_tool_enabled` | `bool` | `False` | 是否启用渐进式工具暴露 |
| `progressive_tool_always_visible_tools` | `List[str]` | `[]` | 始终可见的工具名称列表 |
| `progressive_tool_default_visible_tools` | `List[str]` | `[]` | 默认可见的工具名称列表 |
| `progressive_tool_max_loaded_tools` | `int` | `12` | 最大同时加载工具数 |

---

## class SubAgentConfig

```python
@dataclass
class SubAgentConfig:
    agent_card: AgentCard
    system_prompt: str
    tools: List[Tool | ToolCard] = field(default_factory=list)
    mcps: List[McpServerConfig] = field(default_factory=list)
    model: Optional[Model] = None
    rails: Optional[List[AgentRail]] = None
    skills: Optional[List[str]] = None
    backend: Optional[Any] = None
    workspace: Optional[Workspace] = None
    sys_operation: Optional[SysOperation] = None
    language: Optional[str] = None
    prompt_mode: Optional[str] = None
    enable_task_loop: bool = False
    max_iterations: Optional[int] = None
    factory_name: Optional[str] = None
    factory_kwargs: dict[str, Any] = field(default_factory=dict)
```

DeepAgent 子智能体配置。

**属性**:

| 属性 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `agent_card` | `AgentCard` | *(必填)* | 子智能体身份卡 |
| `system_prompt` | `str` | *(必填)* | 子智能体系统提示词 |
| `tools` | `List[Tool \| ToolCard]` | `[]` | 工具列表 |
| `mcps` | `List[McpServerConfig]` | `[]` | MCP 服务器配置列表 |
| `model` | `Optional[Model]` | `None` | 子智能体 LLM 模型。为 None 时继承父智能体的模型 |
| `rails` | `Optional[List[AgentRail]]` | `None` | 子智能体 Rails |
| `skills` | `Optional[List[str]]` | `None` | 技能列表 |
| `backend` | `Optional[Any]` | `None` | 后端实例 |
| `workspace` | `Optional[Workspace]` | `None` | 工作区配置。为 None 时基于父工作区创建子目录 |
| `sys_operation` | `Optional[SysOperation]` | `None` | 系统操作 |
| `language` | `Optional[str]` | `None` | 语言。为 None 时继承父智能体的语言 |
| `prompt_mode` | `Optional[str]` | `None` | 提示词模式。为 None 时继承父智能体的模式 |
| `enable_task_loop` | `bool` | `False` | 是否启用任务循环 |
| `max_iterations` | `Optional[int]` | `None` | 最大迭代次数。为 None 时继承父智能体的值 |
| `factory_name` | `Optional[str]` | `None` | 工厂函数名称（如 `"browser_agent"`） |
| `factory_kwargs` | `dict[str, Any]` | `{}` | 传递给工厂函数的额外关键字参数 |
