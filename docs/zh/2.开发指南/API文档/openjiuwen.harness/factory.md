# factory

## function openjiuwen.harness.create_deep_agent

```python
def create_deep_agent(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    subagents: Optional[List[SubAgentConfig | DeepAgent]] = None,
    rails: Optional[List[AgentRail]] = None,
    enable_task_loop: bool = False,
    enable_async_subagent: bool = False,
    add_general_purpose_agent: bool = False,
    max_iterations: int = 15,
    workspace: Optional[str | Workspace] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    vision_model_config: Optional[VisionModelConfig] = None,
    audio_model_config: Optional[AudioModelConfig] = None,
    enable_task_planning: bool = False,
    restrict_to_work_dir: bool = True,
    **config_kwargs: Any,
) -> DeepAgent
```

创建并配置 `DeepAgent` 实例的主入口函数。该函数为同步调用；Rails 在首次 `invoke()` 时异步注册。

**参数**:

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `model` | `Model` | *(必填)* | 预构建的 Model 实例，用于 LLM 调用 |
| `card` | `Optional[AgentCard]` | `None` | 智能体身份卡。为 None 时创建默认卡 |
| `system_prompt` | `Optional[str]` | `None` | 内部 ReActAgent 的系统提示词 |
| `tools` | `Optional[List[Tool \| ToolCard]]` | `None` | 注册到智能体的工具实例或工具卡 |
| `mcps` | `Optional[List[McpServerConfig]]` | `None` | 注册到智能体的 MCP 服务器配置 |
| `subagents` | `Optional[List[SubAgentConfig \| DeepAgent]]` | `None` | 子智能体规格或子智能体实例，支持不同的模型、工具和提示词 |
| `rails` | `Optional[List[AgentRail]]` | `None` | 要注册的 AgentRail 实例 |
| `enable_task_loop` | `bool` | `False` | 是否启用外层任务循环 |
| `enable_async_subagent` | `bool` | `False` | 启用通过 SessionRail 的异步子智能体。为 True 且配置了子智能体时，使用 SessionRail 代替 SubagentRail |
| `add_general_purpose_agent` | `bool` | `False` | 为 True 时自动添加通用子智能体 |
| `max_iterations` | `int` | `15` | 每次 invoke 的最大 ReAct 迭代次数 |
| `workspace` | `Optional[str \| Workspace]` | `None` | 文件操作的工作区路径或 Workspace 对象 |
| `skills` | `Optional[List[str]]` | `None` | 技能定义列表 |
| `backend` | `Optional[Any]` | `None` | 后端协议实例 |
| `sys_operation` | `Optional[SysOperation]` | `None` | 系统操作实例 |
| `language` | `Optional[str]` | `None` | 提示词语言（`"cn"` 或 `"en"`） |
| `prompt_mode` | `Optional[str]` | `None` | 提示词模式（`"full"`、`"minimal"`、`"none"`） |
| `vision_model_config` | `Optional[VisionModelConfig]` | `None` | 共享视觉模型配置，注入到所有视觉工具 |
| `audio_model_config` | `Optional[AudioModelConfig]` | `None` | 共享音频模型配置，注入到所有音频工具 |
| `enable_task_planning` | `bool` | `False` | 是否启用 TaskPlanningRail |
| `restrict_to_work_dir` | `bool` | `True` | 为 True 时限制文件访问在工作区目录内；为 False 时允许访问任意路径 |
| `**config_kwargs` | `Any` | — | 转发到 `DeepAgentConfig` 的额外字段 |

**返回值**: `DeepAgent` — 已配置的 DeepAgent 实例，可直接调用 `invoke()` / `stream()`。

**自动注入的默认 Rails**:

当调用方未显式提供时，工厂函数自动注入以下 Rails：

| Rail | 注入条件 |
|---|---|
| `SecurityRail` | 始终注入 |
| `TaskPlanningRail` | `enable_task_planning=True` |
| `SkillUseRail` | `skills` 非空 |
| `SessionRail` | `subagents` 非空且 `enable_async_subagent=True` |
| `SubagentRail` | `subagents` 非空且 `enable_async_subagent=False` |
