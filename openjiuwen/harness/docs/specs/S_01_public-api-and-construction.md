# S_01 公开 API 表面与构造流

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/__init__.py`、`openjiuwen/harness/factory.py`、`openjiuwen/harness/schema/config.py`、`openjiuwen/harness/extension_binder.py` |
| 最近一次修订日期 | 2026-08-23 |
| 关联 feature | N/A |

## 范围 / 边界

本规约定义 `openjiuwen.harness` 子系统的两件事，仅此两件事：

1. **公开 API 表面**——`__init__.py` 导出哪些符号是公共契约、哪些是内部实现。
2. **构造流**——`DeepAgentConfig` / `create_deep_agent(...)` / `DeepAgent.configure()` 之间的
   形态约束：配置怎么进入运行时、装配是否回写配置。

不在本规约范围内（落到其它 spec）：
- DeepAgent 生命周期、Runner 入口、状态机 —— 见 `S_02_deep-agent-architecture`。
- TaskLoop 事件体系与任务循环控制器 —— 见 `S_03_task-loop`。
- Rail 体系（基类、priority、事件路由、挂载生命周期）—— 见 `S_04_rails-contract`。
- 工具契约（注册/描述/lifecycle）—— 见 `S_05_tools-contract`。
- Prompt / i18n —— 见 `S_06_prompts-and-l10n`。
- 数据模型（`DeepAgentState` / `DeepLoopEvent` / task 模型）—— 见 `S_02` / `S_03` / `S_05` / `S_12` / `S_13`。
- 扩展包加载（plugin / agent-template）—— 见 `S_13_resources-packages`。

> 模型形态与字段生命周期：配置模型（config / mode / build_context / extension_spec /
> deep_agent_spec）由本 spec 锚定形态；状态模型（state / loop_event / task /
> stop_condition / interaction）由消费方 spec 锚定（`S_02` / `S_03` / `S_05` /
> `S_12` / `S_13`），本 spec 不重复。

## 不变量

公开表面：

1. `openjiuwen/harness/__init__.py` 的 `__all__` 是公开 API 的**完整集合**（8 个符号）：
   `DeepAgent`、`TaskLoopEventHandler`、`TaskLoopEventExecutor`、`DeepAgentConfig`、
   `AudioModelConfig`、`VisionModelConfig`、`create_deep_agent`、`Workspace`。
2. 模块顶层用 `__getattr__` **懒加载**公开符号；从 `from openjiuwen.harness import X` 拿到的
   是重模块导入，但首见开销被推迟到第一次访问。任何不在 `__all__` 里的导入路径都是内部实现，
   不承担兼容性保证。
3. 构造 DeepAgent 的**唯一公共路径**是 `factory.create_deep_agent(model, ...)` 返回
   `DeepAgent`；不存在并行包装。`create_deep_agent` 是同步函数——rails 排队，
   首次 `invoke()` 时异步初始化。
4. 新增装配配置必须落到 `DeepAgentConfig` 字段；`create_deep_agent` 的
   `**config_kwargs` 只透传给 `DeepAgentConfig`，不得在其上引入平铺的新装配语义。

配置流：

5. `DeepAgentConfig`（`schema/config.py`）是 `@dataclass`，持有**plain-data / 已构造依赖**两
   类字段：`model` / `card` / `tools` / `mcps` / `rails` / `workspace` / `sys_operation` 是
   运行时对象；`model_selection` / `permissions` 等是数据描述。
6. `DeepAgent.configure(config)` 是**单向**的：消费 config、装配 agent；不修改传入的
   `DeepAgentConfig` 实例字段值。`configure` 返回 `"DeepAgent"`（链式）。
7. `configure()` 区分首配 / 热重配：首配走 `_initial_configure`，后续走 `_hot_reconfigure`
   （`_hot_reload_rails` / `_hot_reload_model` / `_hot_reload_tools` / `_hot_reload_system_prompt`）。
   `_initial_configure` 只执行一次，由 `_ensure_initialized` 守卫。
8. `VisionModelConfig` / `AudioModelConfig` 是 `@dataclass`，用 `from_env()` 类方法从环境变量
   构造；非完整配置（缺 api_key/base_url/model）由 `is_vision_model_config_complete()` 判定
   `False`，配装侧（factory）据此跳过 vision 工具。
9. `Registry` 约束：`TransportSpec` / `StorageSpec` 的注册表派发在 agent_teams 侧
   （`S_01_public-api-and-spec-flow`）；harness 侧同类机制是 `manifest` 的 catalog，
   见 `S_12_manifest-declarative-assembly`。

## 接口契约

```python
# openjiuwen/harness/__init__.py
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.task_loop.task_loop_event_executor import TaskLoopEventExecutor
from openjiuwen.harness.task_loop.task_loop_event_handler import TaskLoopEventHandler
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.schema.config import AudioModelConfig, DeepAgentConfig, VisionModelConfig
from openjiuwen.harness.workspace.workspace import Workspace

__all__ = ["DeepAgent", "TaskLoopEventHandler", "TaskLoopEventExecutor",
           "DeepAgentConfig", "AudioModelConfig", "VisionModelConfig",
           "create_deep_agent", "Workspace"]
```

错误 / 返回语义：

- `create_deep_agent(model, ...)` → `DeepAgent`；`model` 为必填 `Model` 实例。
- `create_deep_agent` 的 `**config_kwargs` 键必须是 `DeepAgentConfig` 已知字段；
  未知键由 `dataclasses.replace` 语义在运行时抛 `TypeError`（工厂未捕获）。
- `DeepAgent.configure(config)` → `DeepAgent`（链式）；非法配置（工具 id 冲突等）在
  `_filter_disabled_tools` / `_ensure_builtin_tool_resource` 阶段抛对应 `StatusCode`。
- 顶层无效属性访问 → `AttributeError`（懒加载 `__getattr__` 兜底）。

```python
# openjiuwen/harness/factory.py
def create_deep_agent(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    tool_owner_id: Optional[str] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    subagents: Optional[List[SubAgentConfig | DeepAgent]] = None,
    rails: Optional[List[AgentRail]] = None,
    enable_task_loop: bool = False,
    enable_async_subagent: bool = False,
    enable_subagent_runtime: bool = False,
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
    enable_read_image_multimodal: Optional[bool] = None,
    enable_task_planning: bool = False,
    restrict_to_work_dir: bool = True,
    default_mode: AgentMode = AgentMode.NORMAL,
    model_selection: Optional[Dict[Model, str]] = None,
    parallel_tool_calls: bool = True,
    enable_security_rail: bool = True,
    enable_model_anomaly_detection_rail: bool = True,
    **config_kwargs: Any,
) -> DeepAgent
```

- 工厂把 `workspace: str | Workspace`、`skills: List[str]` 规范化后
  `DeepAgent(card).configure(config)`。
- `enable_task_loop` / `enable_async_subagent` / `enable_subagent_runtime` /
  `enable_task_planning` / `enable_security_rail` / `enable_model_anomaly_detection_rail`
  都是开关 boolean；默认值如上。
- 环境注入：`_append_env_online_training_rail` 按环境变量追加在线训练 rail，
  不暴露给宿主——宿主只需设环境变量。

## 数据结构

### DeepAgentConfig 字段分类生命周期

| 字段 | 设置时机 | 清空时机 | 备注 |
|---|---|---|---|
| `model` / `card` | 构造时（用户） | 不清 | 运行时对象，`configure` 只读消费 |
| `tools` / `mcps` | 构造时 | 热重配时替换 | `_hot_reload_tools` 按 card 身份做增删校验 |
| `rails` | 构造时 | 热重配时替换 | `_hot_reload_rails` 按类型替换；`register_rail` 追加 |
| `system_prompt` | 构造时 | 热重配时替换 | `_hot_reload_system_prompt` 重建 builder |
| `enable_task_loop` | 构造时 | 不清 | `_setup_task_loop` 读它建 loop |
| `max_iterations` | 构造时 | 不清 | 传给 `LoopCoordinator` 做轮次上限 |
| `vision_model_config` / `audio_model_config` | 构造时（用户或 `from_env`） | 不清 | `is_vision_model_config_complete` 判定是否装配 vision 工具 |
| `permissions` / `permission_host` | 构造时 | 不清 | 走 `security/`（见 S_08） |



### 配置模型形态

| 模型 | 类型 | 所属 |
|---|---|---|
| `DeepAgentConfig` | `@dataclass` | `schema/config.py` |
| `SubAgentConfig` | `@dataclass` | `schema/config.py` |
| `VisionModelConfig` | `@dataclass` | `schema/config.py` |
| `AudioModelConfig` | `@dataclass` | `schema/config.py` |
| `AgentMode` | `enum` | `schema/agent_mode.py`（`S_02` 消费） |
| `BuildContext` | plain-data / 重建 factory | `schema/build_context.py`（`S_12` 消费） |
| `_ExtensionSpecModel` 族 | pydantic | `schema/extension_spec.py`（`S_13` 消费） |
| `DeepAgentSpec` / `SubAgentSpec` | pydantic | `schema/deep_agent_spec.py`（`S_13` 消费） |

序列化边界：pydantic 模型走 `model_dump_json()` / `model_validate_json()`；dataclass
由工厂显式搬运，不承诺自动 JSON。

### create_deep_agent 的装配编排

`create_deep_agent` 是 `DeepAgentConfig` 的**字面量构造点**以外的唯一配置入口。装配顺序：

1. 规范化 `workspace`（`str` → `Workspace`，默认 `auto_create_workspace=True`）。
2. 构造 `DeepAgent`（`card` 必填或默认）。
3. `configure(config)`：首配走 `_initial_configure`，`_queue_pending_rails` 排队 rails。
4. 首次 `invoke()` 的 `_ensure_initialized` 完成：`_register_pending_mcps`、
   `_register_pending_rails`（`register_rail` → `init_rail`）、`init_workspace`。

## 与其它 spec 的关系

- 本 spec 描述**入口形态**与 **Config → configure → DeepAgent 单向流**；进入装配后
  `DeepAgent` 的生命周期、task-loop 装配、rail 挂载留给 `S_02` / `S_03` / `S_04`。
- `DeepAgentConfig` 具体字段的运行时语义（mode、permissions、model_selection）分散在
  `S_02` / `S_08` / `S_11`，本 spec 只锚定"新增配置必须落字段"这条形态。
- `create_deep_agent` 的 `subagents` 参数语义与预设子代理归属见 `S_18_subagents-and-lifecycle`；
  异步子代理运行时见 `S_10_subagent-runtime`。
- 顶层懒加载 `__getattr__` 模式与 `task_loop/__init__.py` / `schema/__init__.py` 的子包懒加载
  同构，但各自独立维护，不共享实现。
