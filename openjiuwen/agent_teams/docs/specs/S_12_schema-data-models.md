# Schema Data Models

## Checkpoint `db_state` note

The per-team checkpoint bucket includes `db_state` in addition to
`spec`, `context`, `model_allocator_state`, and `lifecycle`.
Valid values are `pending_create`, `created`, and `cleaned`; runtime
dispatch uses this field when a session bucket exists but the team DB row
does not.

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/schema/blueprint.py`、`openjiuwen/agent_teams/schema/deep_agent_spec.py`、`openjiuwen/agent_teams/schema/team.py`、`openjiuwen/agent_teams/schema/events.py`、`openjiuwen/agent_teams/schema/status.py`、`openjiuwen/agent_teams/schema/stream.py`、`openjiuwen/agent_teams/schema/task.py` |
| 最近一次修订日期 | 2026-05-12 |
| 关联 feature | `F_05_lifecycle-finalize-relocation.md`（`MemberStatus.STOPPED` 新增）。其余条目见 `docs/features/` |

## 范围 / 边界

本规约钉死 `agent_teams/schema/` 子模块的全部数据模型与它们与运行时之间的边界。
覆盖：

1. **顶层装配蓝图**（`blueprint.py`）：`TeamAgentSpec` / `LeaderSpec` /
   `TransportSpec` / `StorageSpec`，以及 transport / storage 注册表。
2. **DeepAgent 侧 Spec**（`deep_agent_spec.py`）：`DeepAgentSpec` /
   `SubAgentSpec` / `RailSpec` / `BuiltinToolSpec` / `WorkspaceSpec` /
   `SysOperationSpec` / `VisionModelSpec` / `AudioModelSpec` /
   `ProgressiveToolSpec` / `TeamModelConfig`。
3. **团队声明与运行时上下文**（`team.py`）：`TeamSpec` / `TeamRole` /
   `TeamLifecycle` / `TeamRuntimeContext` / `TeamMemberSpec` /
   `MemberOpResult`。
4. **跨进程事件**（`events.py`）：`TeamTopic` / `TeamEvent` /
   `BaseEventMessage` 子类家族 / `EventMessage` 包装。
5. **状态机枚举**（`status.py`）：`MemberStatus` / `ExecutionStatus` /
   `TaskStatus` / `MemberMode`，以及对应的合法转移表与
   `is_valid_transition`。
6. **Team 流式输出扩展**（`stream.py`）：`TeamOutputSchema` —— `OutputSchema`
   子类，扩 `source_member` / `role`，不污染 core 层。
7. **任务返回模型与写路径结果包装**（`task.py`）：`TaskSummary` /
   `TaskDetail` / `TaskListResult` / `NewTaskSpec` 以及 `TaskOpResult` /
   `TaskCreateResult` / `GraphMutationResult`。

不在本规约内：

- Spec → Runtime 之间的实际**装配链路**（哪个 manager 怎么消费）：见
  `S_01_public-api-and-spec-flow.md` 与 `S_02_team-agent-architecture.md`。
- 协调协议、coordination 状态机、消息总线传输细节：见 `S_03` / `S_06` / `messager/`。
- 模型分配策略实现（`models/allocator.py`）：见 `S_11_models-pool-and-allocation`，schema
  只暴露 `model_pool` / `model_pool_strategy` / `model_router` 字段。
- session 持久化路径与 per-team namespace 读写入口：见 `S_04_session-and-recovery.md`，
  本规约只钉**入桶字段形状**。

## 不变量

下列条目在 schema 层任意时刻必须为真。每条都可单点反例验证 —— 违反就是 bug。

### I-1 Spec 必须 JSON 可序列化

`TeamAgentSpec` / `DeepAgentSpec` / 子 spec 全部基于 pydantic `BaseModel`（少量
result 包装类基于 `@dataclass(frozen=True, slots=True)`）。`model_dump()` /
`model_dump_json()` 必须能 round-trip 出整份 spec。

唯一允许排除在序列化外的字段是：

- `TeamAgentSpec.agent_customizer`（`Field(..., exclude=True)`）—— 平台适配器注入
  rails/tools 的回调，仅 in-process spawn 模式下可用，定义为不可跨进程。

任何新增字段如果持有 `Runner` / `Session` / 文件句柄 / 已实例化的 Manager，
**就不该放 Spec**，应当下沉到 Config / Manager / Runtime。

### I-2 Spec 不持有运行时引用

Spec 是**装配蓝图**，不是运行时容器。下列对象**禁止**作为字段类型出现在任何
Spec 上：

- `Runner` / `AgentTeamSession` / `TeamAgent` / `DeepAgent`
- 已打开的文件句柄、socket、asyncio 队列
- 任何 manager 实例（`SpawnManager` / `CoordinationManager` / ...）

判定标准：dump 出去的 JSON 字符串能否在另一台机器上被 `model_validate_json`
构造回原始语义？不能 → 这字段不该在 Spec 上。

### I-3 Spec → build() → Runtime 单向流

每个 Spec 类提供 `build()`（或 `build_card()`，如 `SysOperationSpec`），输出
对应的运行时对象。运行时对象**不**回写 Spec，也不持有 Spec 引用以触发反向
变更。热更新走 session + resume 路径，不靠污染 Spec。

### I-4 注册表是基础设施 Spec 的唯一桥

`TransportSpec.build()` 和 `StorageSpec.build()` 通过 `_TRANSPORT_REGISTRY` /
`_STORAGE_REGISTRY` 解析 `type` 字符串。**禁止**绕过注册表直接 import 具体 Config
类构造 —— 这破坏插件化语义。新后端走 `register_transport(name, cls)` /
`register_storage(name, cls)`。

`_ensure_builtin_infra_registered()` 是延迟登记，幂等：第一次调用注入内置
`inprocess` / `pyzmq` / `sqlite` / `postgresql` / `mysql` / `memory`，后续
no-op。`RailSpec` 和 `BuiltinToolSpec` 走同样模式（`_RAIL_TYPE_REGISTRY` /
`_TOOL_TYPE_REGISTRY`），由 `_ensure_builtin_*_registered` 延迟填充。

### I-5 `model_pool` 与 `model_router` 互斥

`TeamAgentSpec._validate_pool_router_exclusive` 强制：同时配置两者直接
`ValueError`。`router` 是 `pool` 的便利输入形态，`build()` 时通过
`ModelRouterConfig.to_pool_entries()` 展开成 `model_pool`，并把
`model_pool_strategy` 强制设为 `"router"`。下游所有路径只见 pool。

### I-6 `enable_hitt` 是能力上限

`TeamAgentSpec._validate_hitt_consistency` 在 `build()` 时强制：

- `enable_hitt=False` 且 `predefined_members` 含任一 `HUMAN_AGENT` →
  `StatusCode.AGENT_TEAM_CONFIG_INVALID`。
- `enable_hitt=True` 且无 predefined `HUMAN_AGENT` → 允许（动态 spawn 路径）。

`build_team` 工具自身的 `enable_hitt` 可**下调**这个上限到 `False`，但**不能**
在 Spec 关时单方面打开。

### I-7 保留名约束

`_validate_reserved_names()` 强制：

- Leader 的 `member_name` 不可在 `RESERVED_MEMBER_NAMES - {DEFAULT_LEADER_MEMBER_NAME}`
  中（即 `user` / `human_agent` 禁用，但默认 `team_leader` 允许 leader 自用）。
- `predefined_members` 的非 `HUMAN_AGENT` 成员的 `member_name` 不可落入
  `RESERVED_MEMBER_NAMES` —— `HUMAN_AGENT` 角色的 HITT 自动注入路径例外。

### I-8 Spawn 模式与 transport 默认匹配

`TeamAgentSpec._default_transport_for_spawn_mode` 在 validation 阶段补默认：
`spawn_mode="inprocess"` 且 `transport is None` → 自动设 `TransportSpec(type="inprocess")`。
`spawn_mode="process"` 不补默认 —— 调用方必须显式声明跨进程后端（如 `pyzmq`），
否则 teammate spawn 会失败。

理由：dump 出去的 spec 必须**自描述**实际使用的 transport，不依赖运行时补全。

### I-9 Leader 模型必须可解析

`_validate_leader_model_resolved` 强制：当 `team_spec.model_pool` 非空时，
leader 必须在 build() 完成前拿到一个可用的 `TeamModelConfig`：

- `ModelAllocator.allocate(model_name=leader.model_name)` 返回非空，或
- `agents['leader'].model` 显式配置非空。

否则用 `StatusCode.AGENT_TEAM_CONFIG_INVALID` 抛错并附带可执行的 remediation
列表（"set leader.model_name to ... / configure agents['leader'].model / switch
strategy to round_robin"）。**禁止**让错误延迟到首次 LLM 调用。

### I-10 状态转移必须查表

`MemberStatus` / `ExecutionStatus` / `TaskStatus` 的合法转移由
`MEMBER_TRANSITIONS` / `EXECUTION_TRANSITIONS` / `TASK_TRANSITIONS` 三张静态
字典定义，写路径必须经 `is_valid_transition(current, new, transitions)` 校验。
**禁止**在业务代码里 hard-code "from X to Y is fine"。

### I-11 PAUSED ≠ STOPPED ≠ SHUTDOWN

三个非 BUSY 的 quiescent / terminal 状态语义严格分开：

| 状态 | 来源 | runtime 是否在跑 | 团队是否解散 | 可经哪些转移恢复 |
|---|---|---|---|---|
| `PAUSED` | round 自然结束（persistent + idle） | 否 | 否 | `READY` / `RESTARTING` / `SHUTDOWN_REQUESTED` / `SHUTDOWN` / `ERROR` / `STOPPED` |
| `STOPPED` | 外部 `stop_coordination`（team 仍 live） | 否 | 否 | `READY` / `RESTARTING` / `SHUTDOWN_REQUESTED` / `SHUTDOWN` / `ERROR` |
| `SHUTDOWN` | 永久退出 | 否 | 否（DB row 留着，仅 `RESTARTING` 可复活） | `RESTARTING` |

差异**不在"runtime 跑没跑"**——三者都意味着协程已退出，区分点在**为什么退出**：
PAUSED 是 lifecycle 驱动的 round-end idle；STOPPED 是 manager.finalize / 外部
stop_team 主动 tear-down；SHUTDOWN 是逻辑退场（`shutdown_self` / 失败累积）。
这个区分让 leader 在 pause / stop 路径上能向持久层 (`team_member.status`) 写出
**为什么** teammate runtime 不在了，使 `recover_team` 重启决策有数据基础。

`STOPPED` 的引入背景见 F_05：lifecycle finalize 决策权从 `CoordinationKernel`
搬到 `TeamRuntimeManager` 时，stop 路径需要区分"团队解散"和"team live、runtime
被外部 stop 拆掉"两种语义，原 PAUSED 一锅端的写法盖不住后者。

不要继续给 `MemberStatus` 加新枚举来"区分细微情形"，新增情形先证明现有 PAUSED /
STOPPED / RESTARTING / ERROR 不能覆盖。

### I-12 两枚举各管各的层

| 枚举 | 模块 | 描述 |
|---|---|---|
| `TeamLifecycle`（temporary / persistent） | `schema/team.py` | **静态**团队类型，写在 spec 里 |
| `RuntimeState`（running / paused） | `runtime/pool.py` | **运行时**对象池中 team 的状态 |

`TeamLifecycle` 是装配数据，跨进程序列化；`RuntimeState` 是 pool 的 in-memory
状态，不进 spec。两个枚举不要混用、不要互转。

### I-13 `TeamOutputSchema` 不污染 core

`TeamOutputSchema` 继承 `core.session.stream.OutputSchema`，**只在 team 层**新增
`source_member: str | None` 与 `role: TeamRole | None`。规则：

- core 层不感知这些字段，非 team 路径产出的 chunk 仍是裸 `OutputSchema`。
- 升级语义只发生在 team 路径的 `StreamController` 入口 ——
  `TeamOutputSchema.from_output(base, source_member=..., role=...)` 构造新实例，
  原 `base` 不被 mutate（DeepAgent 内部对象身份保留）。
- `(source_member, role)` 是一对：`source_member` 是 producer 的 `member_name`，
  `role` 是它在 team 中的角色（`LEADER` / `TEAMMATE` / `HUMAN_AGENT`）。`TeamRole`
  是 `str` 枚举，pydantic 序列化得到字符串值，跨进程兼容。
- inprocess 模式下，`SpawnManager` 通过
  `StreamController.add_chunk_observer` 把 teammate chunk fan-out 到 leader 的
  `stream_queue`；subprocess 模式不做转发（chunk 留在 teammate 进程内）。

### I-14 `EventMessage` 是事件总线唯一传输形态

跨进程事件统一用 `EventMessage` 包装：`event_type`（`TeamEvent.*` 字符串） +
`payload`（具体 `BaseEventMessage` 子类的 `model_dump()`） + `sender_id`。
发布端 `EventMessage.from_event(concrete_event)`，订阅端 `event_msg.get_payload()`
反向解包到具体子类。

`_EVENT_TYPE_MAP` / `_EVENT_CLASS_MAP` 双向表是 single source of truth；新增事件
类型必须**同时**在 `TeamEvent` 加常量、写 `BaseEventMessage` 子类、登记到
`_EVENT_TYPE_MAP`。漏一个就是悬空事件。

### I-15 Topic 命名收敛

`TeamTopic.build(session_id, team_name)` 是构造 topic 字符串的唯一入口。格式
固定为 `session:{session_id}:team:{team_name}:{topic.value}`。**禁止**散落
`f"session:{...}:..."` 字符串拼装。

### I-16 Session checkpoint 状态按 team 分桶

session checkpoint 全局状态根上有一个 `teams` namespace：
`state["teams"][team_name] = {spec, context, model_allocator_state, lifecycle}`。
同一 session 可承载多个 team 的状态。

读写一律走 `runtime/metadata.py` 的 `read_team_namespace` /
`merge_team_namespace` / `write_team_namespace` / `remove_team_namespace`，
**禁止**直接在 root 上 `update_state({"spec": ...})` —— 那会把不同 team 的状态
踩烂。

## 接口契约

### blueprint.py

#### `TransportSpec`

| 字段 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `type` | `str` | required | 注册表 key（内置：`inprocess` / `pyzmq`） |
| `params` | `dict[str, Any]` | `{}` | 透传到目标 Config 类的额外参数 |

`build() -> BaseModel`：调 `_ensure_builtin_infra_registered()`，从
`_TRANSPORT_REGISTRY` 解析 `type`；merge `{"backend": self.type, **self.params}`，
再 `model_validate`。未知 `type` → `ValueError`，错误信息列出已注册类型。

#### `StorageSpec`

字段同 `TransportSpec`（`type` / `params`）。`build()` merge
`{"db_type": self.type, **self.params}` 后 `model_validate`。内置：`sqlite` /
`postgresql` / `mysql` / `memory`。

#### `LeaderSpec`

| 字段 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `member_name` | `str` | `"team_leader"` | leader 在 team 中的成员名 |
| `display_name` | `str` | `"Team Leader"` | UI 展示名 |
| `persona` | `str` | `t("blueprint.default_persona")` | 默认人设文本 |
| `model_name` | `Optional[str]` | `None` | 池模式下指定从哪一组 / 哪一条路由取 endpoint；`round_robin` 忽略 |

#### `TeamAgentSpec`

| 字段 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `agents` | `dict[str, DeepAgentSpec]` | required | 必须含 `"leader"` key；可选 `"teammate"` |
| `team_name` | `str` | `"agent_team"` | 团队名（与 session 一同决定 topic 路由前缀） |
| `lifecycle` | `str`（取 `TeamLifecycle` 值） | `TEMPORARY` | 静态团队类型 |
| `teammate_mode` | `str` | `"build_mode"` | teammate 是否需 leader 审批：`build_mode` / `plan_mode` |
| `spawn_mode` | `str` | `"process"` | `process` / `inprocess` |
| `leader` | `LeaderSpec` | `LeaderSpec()` | leader 身份块 |
| `predefined_members` | `list[TeamMemberSpec]` | `[]` | 预声明成员；HITT 视角下含 `HUMAN_AGENT` |
| `model_pool` | `list[ModelPoolEntry]` | `[]` | LLM endpoint 池 |
| `model_router` | `Optional[ModelRouterConfig]` | `None` | 单端点路由便利输入；与 `model_pool` 互斥 |
| `model_pool_strategy` | `Literal["round_robin", "by_model_name", "router"]` | `"round_robin"` | 分配策略；`router` 在 `model_router` 设时强制 |
| `team_mode` | `Literal["default", "predefined", "hybrid"] \| None` | `None` | 默认派生：非 HUMAN_AGENT predefined 非空→`hybrid`，否则 `default`；`predefined` 显式 |
| `transport` | `Optional[TransportSpec]` | `None` | inprocess spawn 时自动补 `TransportSpec(type="inprocess")` |
| `storage` | `Optional[StorageSpec]` | `None` | 缺省走 `DatabaseConfig()` 默认；sqlite 无 connection_string 时回填 `get_agent_teams_home() / "team.db"` |
| `worktree` | `Optional[WorktreeConfig]` | `None` | 跨成员 worktree 隔离配置 |
| `workspace` | `Optional[TeamWorkspaceConfig]` | `None` | 共享工作空间配置 |
| `metadata` | `dict[str, Any]` | `{}` | 用户自定义透传字段 |
| `enable_hitt` | `bool` | `False` | HITT 能力上限开关 |
| `language` | `Optional[str]` | `None` | 通过 `resolve_language` 归一到内置语言之一 |
| `agent_customizer` | `Optional[Callable]` | `None`（exclude） | 仅 inprocess spawn 可用的 DeepAgent 改造回调 |
| `memory` | `Optional[TeamMemoryConfig]` | `None` | 团队级 memory 配置 |

方法：

- `build() -> TeamAgent`：构造 `TeamSpec` / `TeamRuntimeContext` / `TeamAgent`，
  在内部跑 `_validate_reserved_names` / `_validate_hitt_consistency` /
  `_validate_leader_model_resolved`，提前 build allocator 并把 leader 的
  `Allocation` 通过 `agent.attach_model_allocator(...)` 注入。
- `resolve_db_config()`：物化 `DatabaseConfig`，sqlite 无 connection_string 时
  回填默认路径 —— 与 `build()` 走同一逻辑，给 runtime manager 不构造 TeamAgent
  也能查到 storage handle。
- `_validate_pool_router_exclusive`（model_validator）：见 I-5。
- `_default_transport_for_spawn_mode`（model_validator）：见 I-8。

注册表入口：

- `register_transport(name: str, cls: type[BaseModel])`
- `register_storage(name: str, cls: type[BaseModel])`

### deep_agent_spec.py

#### `TeamModelConfig`

| 字段 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `model_client_config` | `ModelClientConfig` | required | LLM 客户端配置 |
| `model_request_config` | `Optional[ModelRequestConfig]` | `None` | 请求配置（temperature 等） |

`build() -> Model`：构造 `Model(model_client_config=..., model_config=...)`。

#### `DeepAgentSpec`

字段（节选关键）：

| 字段 | 类型 | 默认 |
|---|---|---|
| `model` | `Optional[TeamModelConfig]` | `None` |
| `card` | `Optional[AgentCard]` | `None` |
| `system_prompt` | `Optional[str]` | `None` |
| `tools` | `Optional[list[ToolCard \| BuiltinToolSpec]]` | `None` |
| `mcps` | `Optional[list[McpServerConfig]]` | `None` |
| `subagents` | `Optional[list[SubAgentSpec]]` | `None` |
| `rails` | `Optional[list[RailSpec]]` | `None` |
| `enable_task_loop` | `bool` | `False` |
| `enable_async_subagent` | `bool` | `False` |
| `add_general_purpose_agent` | `bool` | `False` |
| `max_iterations` | `int` | `15` |
| `workspace` | `Optional[WorkspaceSpec]` | `None` |
| `skills` | `Optional[list[str]]` | `None` |
| `enable_skill_discovery` | `bool` | `False` |
| `sys_operation` | `Optional[SysOperationSpec]` | `None` |
| `language` | `Optional[str]` | `None` |
| `prompt_mode` | `Optional[str]` | `None` |
| `vision_model` | `Optional[VisionModelSpec]` | `None` |
| `audio_model` | `Optional[AudioModelSpec]` | `None` |
| `enable_task_planning` | `bool` | `False` |
| `restrict_to_sandbox` | `bool` | `False` |
| `auto_create_workspace` | `bool` | `True` |
| `completion_timeout` | `float` | `600.0` |
| `progressive_tool` | `Optional[ProgressiveToolSpec]` | `None` |
| `approval_required_tools` | `Optional[list[str]]` | `None` |

`build() -> DeepAgent`：解析所有子 spec → `create_deep_agent(...)`。子 spec 的
build 顺序：`workspace` → `vision_model` / `audio_model` → `rails`（依赖
workspace）→ `subagents`（依赖 parent_model + language）→ `sys_operation`（注入
`Runner.resource_mgr`）→ `tools`（`BuiltinToolSpec` 转 Tool 实例，`ToolCard`
透传）。

#### `SubAgentSpec`

| 字段 | 类型 | 默认 |
|---|---|---|
| `agent_card` | `AgentCard` | required |
| `system_prompt` | `str` | required |
| `tools` | `list[ToolCard \| BuiltinToolSpec]` | `[]` |
| `mcps` | `list[McpServerConfig]` | `[]` |
| `model` | `Optional[TeamModelConfig]` | `None` |
| `rails` | `Optional[list[RailSpec]]` | `None` |
| `skills` | `Optional[list[str]]` | `None` |
| `workspace` | `Optional[WorkspaceSpec]` | `None` |
| `sys_operation` | `Optional[SysOperationSpec]` | `None` |
| `language` | `Optional[str]` | `None` |
| `prompt_mode` | `Optional[str]` | `None` |
| `enable_task_loop` | `bool` | `False` |
| `max_iterations` | `Optional[int]` | `None` |
| `factory_name` | `Optional[str]` | `None` |
| `factory_kwargs` | `dict[str, Any]` | `{}` |

`build(*, parent_model, language) -> SubAgentConfig`：`model` 缺省走 parent；
tool / rail / sys_operation 与 `DeepAgentSpec.build()` 同模式。

#### `RailSpec`

| 字段 | 类型 |
|---|---|
| `type` | `str`（注册表 key） |
| `params` | `dict[str, Any]` |

`build(*, language, workspace=None) -> AgentRail`：从 `_RAIL_TYPE_REGISTRY`
解析；构造时若 `language` 在 `__init__` 签名里则自动注入；`type=="skill_use"`
且未传 `skills_dir` 时从 workspace 的 `skills` 节点解析 + 默认 CLI 目录
（`~/.openjiuwen/workspace/skills`、`~/.claude/skills`）。

注册：`register_rail_type(name, cls)`。内置：`task_planning` / `skill_use` /
`subagent` / `filesystem` + 可选 `context_engineering` / `token_tracking` /
`tool_tracking` / `ask_user` / `confirm_interrupt`（importable 时登记）。

#### `BuiltinToolSpec`

字段同 `RailSpec`。`build(*, language, tool_id=None) -> Any`：从
`_TOOL_TYPE_REGISTRY` 解析；`language` / `tool_id` 在签名里时自动注入。
内置（可选）：`web_search` / `web_fetch`。

注册：`register_tool_type(name, cls)`。

#### `WorkspaceSpec`

| 字段 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `root_path` | `str` | `"./"` | workspace 根路径 |
| `language` | `str` | `"cn"` | workspace 语言 |
| `stable_base` | `bool` | `False` | 为 True 时由调用方将 `root_path` 重写到 `{repo_root}/.agent_teams/workspaces/`，避免 worktree 清理一同抹掉 |

`build() -> Workspace`。`stable_base` 的路径重写**不在 `build()` 内部完成**，由
`agent_configurator` 在装配过程中完成。

#### 其余叶子 Spec

- `VisionModelSpec` / `AudioModelSpec`：pydantic 镜像对应 dataclass，`build()`
  透传字段构造。
- `SysOperationSpec`：`build_card() -> SysOperationCard`；写入
  `Runner.resource_mgr` 由调用方完成（避免 schema 层依赖 Runner 单例）。
- `ProgressiveToolSpec`：声明式开关，`DeepAgentSpec._progressive_tool_kwargs`
  展开到 `create_deep_agent` 的 progressive 系列入参。

### team.py

#### `TeamSpec`

| 字段 | 类型 | 默认 |
|---|---|---|
| `team_name` | `str` | required |
| `display_name` | `str` | required |
| `leader_member_name` | `Optional[str]` | `None` |
| `language` | `Optional[str]` | `None` |
| `metadata` | `dict` | `{}` |
| `model_pool` | `list[ModelPoolEntry]` | `[]` |
| `model_pool_strategy` | `Literal["round_robin", "by_model_name", "router"]` | `"round_robin"` |

由 `TeamAgentSpec.build()` 物化，**只读**地传给 `TeamRuntimeContext`，不作为
公共构造入口。

#### `TeamMemberSpec`

| 字段 | 类型 | 默认 |
|---|---|---|
| `member_name` | `str` | required |
| `display_name` | `str` | required |
| `role_type` | `TeamRole` | `TEAMMATE` |
| `persona` | `str` | required |
| `prompt_hint` | `Optional[str]` | `None` |
| `model_name` | `Optional[str]` | `None` |

仅用于 `TeamAgentSpec.predefined_members`；不是运行时载体，spawn / restart 路径
不读它，读 DB。

#### `TeamRuntimeContext`

| 字段 | 类型 | 默认 |
|---|---|---|
| `role` | `TeamRole` | `LEADER` |
| `member_name` | `Optional[str]` | `None` |
| `persona` | `str` | `""` |
| `team_spec` | `Optional[TeamSpec]` | `None` |
| `messager_config` | `Optional[MessagerTransportConfig]` | `None` |
| `db_config` | `DatabaseConfig \| MemoryDatabaseConfig` | `DatabaseConfig()` |
| `member_model` | `Optional[TeamModelConfig]` | `None` |

是 **Spec → Runtime 的边界**。`build()` 出来的对象（messager config、db
config、allocator 给当前成员的 model 选择）封进 `TeamRuntimeContext`，传给
`TeamAgent.configure(spec, context)`。`TeamRuntimeContext` 自身不持有 live
session、live messager 实例 —— 只放配置，由 manager 拿配置去 `build()` /
`create_messager(...)`。

#### `MemberOpResult`

`@dataclass(frozen=True, slots=True)`。字段 `ok: bool` / `reason: str = ""`。
`__bool__` 转发到 `ok`，`success()` / `fail(reason)` 工厂。

`TeamBackend` 的 mutation 方法（`spawn_member` / `shutdown_member` / ...）返回
此对象，给 tool wrapper 把失败原因带回 LLM，而不是吞进日志。

#### 枚举

- `TeamLifecycle`（str enum）：`TEMPORARY = "temporary"` / `PERSISTENT = "persistent"`。
- `TeamRole`（str enum）：`LEADER = "leader"` / `TEAMMATE = "teammate"` /
  `HUMAN_AGENT = "human_agent"`。`HUMAN_AGENT` 是一等成员，但运行时不持
  DeepAgent 进程，仅持 `send_message` 工具，状态留在 `READY` 直到团队清理。

### events.py

#### `TeamTopic`

```
TEAM = "team"
TASK = "task"
MESSAGE = "message"
```

`build(session_id, team_name) -> str`：唯一 topic 构造入口（见 I-15）。

#### `TeamEvent`

不是 enum，是含字符串常量的容器类。常量分组：team lifecycle / member lifecycle
/ collaboration / messaging / task / worktree / workspace。新增事件类型必须同时
更新 `_EVENT_TYPE_MAP`。

完整列表（`agent_teams/schema/events.py:42-87`）：

- Team lifecycle：`team_created` / `team_cleaned` / `team_standby`
- Member lifecycle：`member_spawned` / `member_restarted` /
  `member_status_changed` / `member_execution_changed` / `member_shutdown` /
  `member_canceled`
- Collaboration：`plan_approval` / `tool_approval_result`
- Messaging：`message` / `broadcast`
- Task：`task_created` / `task_updated` / `task_claimed` / `task_completed` /
  `task_cancelled` / `task_unblocked`
- Worktree：`worktree_created` / `worktree_removed`
- Workspace：`workspace_artifact_updated` / `workspace_conflict` /
  `workspace_lock_request` / `workspace_lock_response`

#### `BaseEventMessage` 与子类

公共字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `team_name` | `str` | 路由用 |
| `member_name` | `Optional[str]` | 成员级事件必填，团队级事件留空 |

每个 `TeamEvent` 常量对应一个 `BaseEventMessage` 子类，承载该事件的 payload
字段（如 `MemberStatusChangedEvent.old_status` / `new_status`，
`ToolApprovalResultEvent.tool_call_id` / `approved` / `feedback` /
`auto_confirm` 等，详见源码）。

#### `EventMessage`

| 字段 | 类型 |
|---|---|
| `event_type` | `str`（取 `TeamEvent.*`） |
| `payload` | `dict[str, Any]`（具体事件 `model_dump()`） |
| `sender_id` | `str`（默认 `""`，发布方节点 id，过滤自发自收用） |

方法：

- `from_event(event: BaseEventMessage)`：通过 `_EVENT_CLASS_MAP[type(event)]`
  反查 `event_type`；未注册类抛 `ValueError`。
- `get_payload() -> BaseEventMessage`：通过 `_EVENT_TYPE_MAP[event_type]` 反查
  目标类，`model_validate(payload)`。
- `serialize() -> bytes` / `deserialize(data: bytes) -> EventMessage`：UTF-8
  JSON 编解码，跨进程消息总线唯一通道。

### status.py

| 类 | 类型 | 用途 |
|---|---|---|
| `MemberStatus` | str enum | 成员状态机；见数据结构段 |
| `ExecutionStatus` | str enum | 任务执行状态机；见数据结构段 |
| `TaskStatus` | enum | 团队任务状态机 |
| `MemberMode` | str enum | `BUILD_MODE` / `PLAN_MODE`，控制 teammate 是否需 leader 审批 |

每个状态机伴随转移表常量（`MEMBER_TRANSITIONS` / `EXECUTION_TRANSITIONS` /
`TASK_TRANSITIONS`），写路径必经
`is_valid_transition(current, new, transitions) -> bool`。

### stream.py

#### `TeamOutputSchema`

继承 `core.session.stream.OutputSchema`，新增字段：

| 字段 | 类型 | 默认 |
|---|---|---|
| `source_member` | `str \| None` | `None` |
| `role` | `TeamRole \| None` | `None` |

`from_output(base: OutputSchema, *, source_member: str | None, role: TeamRole | None = None) -> TeamOutputSchema`：
不可 mutate `base`，返回新实例。两字段同时为 `None` 表示 producer 不属于任何
team（如直接 streaming 的单 agent）。`role` 用 `TeamRole`（`str` 枚举）保持跨
进程序列化兼容。

升级路径：

1. team 路径下，`Runner.run_agent_team_streaming` 输出的所有 chunk 经
   `StreamController` 入口升级为 `TeamOutputSchema`，自动打 `(member_name, role)`。
2. inprocess spawn 模式下，`SpawnManager` 在 spawn 每个 teammate 时调
   `StreamController.add_chunk_observer`，把 teammate chunk fan-out 到 leader
   的 `stream_queue`，使 leader streaming 流出整团 chunk 序列。
3. subprocess spawn 模式不做 chunk 转发（chunk 留在 teammate 进程）；扩展点
   保留为 messager-driven observer。

### task.py

#### `TaskSummary`

| 字段 | 类型 |
|---|---|
| `task_id` | `str` |
| `title` | `str` |
| `status` | `str`（`TaskStatus.value`） |
| `assignee` | `Optional[str]` |
| `blocked_by` | `list[str]` |

`view_task` 工具的 list / claimable 动作返回。

#### `TaskDetail`

`TaskSummary` 之外加：

| 字段 | 类型 |
|---|---|
| `content` | `str` |
| `blocks` | `list[str]` |
| `updated_at` | `Optional[int]`（毫秒级 wall-clock 最近一次状态变更时刻） |

`updated_at` 的语义随 `status` 漂移 —— claimed 时是 claim 时刻，completed 时是
完成时刻。

#### `TaskListResult`

`tasks: list[TaskSummary]` + `count: int`。

#### `NewTaskSpec`

`@dataclass(frozen=True, slots=True)`。字段 `task_id` / `title` / `content` /
`initial_status`（caller-supplied seed）。`mutate_dependency_graph` 在节点插入
后跑 refresh pass，可能把 `initial_status` 翻成 `PENDING` / `BLOCKED`。

#### 写路径结果包装

| 类 | 含义 |
|---|---|
| `TaskOpResult` | `{ok, reason}`；普通任务变更结果 |
| `TaskCreateResult` | `{task, reason}`；创建结果，`__getattr__` 透传到 wrapped task，让旧调用点 `result.title` 直接生效 |
| `GraphMutationResult` | `{ok, reason, refreshed_tasks}`；`mutate_dependency_graph` 输出 |

三者均提供 `success(...)` / `fail(reason)` 工厂、`__bool__` 接 `ok`。设计意图：
不丢 reason。

## 数据结构

### `MemberStatus` 状态机

合法转移（来自 `MEMBER_TRANSITIONS`）：

```
UNSTARTED  -> READY | SHUTDOWN | ERROR
READY      -> READY | BUSY | PAUSED | STOPPED | SHUTDOWN_REQUESTED | SHUTDOWN | ERROR
BUSY       -> READY | PAUSED | STOPPED | SHUTDOWN_REQUESTED | ERROR
PAUSED     -> READY | RESTARTING | STOPPED | SHUTDOWN_REQUESTED | SHUTDOWN | ERROR
STOPPED    -> READY | RESTARTING | SHUTDOWN_REQUESTED | SHUTDOWN | ERROR
RESTARTING -> READY | STOPPED | ERROR | SHUTDOWN
SHUTDOWN_REQUESTED -> SHUTDOWN | ERROR
SHUTDOWN   -> RESTARTING
ERROR      -> RESTARTING | READY | STOPPED | SHUTDOWN_REQUESTED | SHUTDOWN
```

关键约束：

- `READY` 自转移合法（用于 ready→ready 的轻量心跳 / 状态广播）。
- `SHUTDOWN` 不是终态：可重新 `RESTARTING`（成员复活路径）。
- `BUSY` 不可直接 `SHUTDOWN`：必须经 `SHUTDOWN_REQUESTED` 或 `ERROR` /
  `READY`，避免硬中断进行中的任务。
- `STOPPED` 与 `PAUSED` 一样可直接 `RESTARTING`（`recovery_manager` 把
  `{PAUSED, STOPPED, ERROR, SHUTDOWN}` 视作"directly restartable"，
  `READY` / `BUSY` / `UNSTARTED` / `SHUTDOWN_REQUESTED` 才需要先归一到
  `ERROR` 再跳）。`STOPPED` 也从 active 成员快照中被过滤掉（与
  `UNSTARTED` / `SHUTDOWN` 同列），不会被当成 live teammate 误重启。

### `ExecutionStatus` 状态机

合法转移（来自 `EXECUTION_TRANSITIONS`）：

```
IDLE              -> STARTING
STARTING          -> RUNNING | CANCEL_REQUESTED | CANCELLING | FAILED | TIMED_OUT
RUNNING           -> CANCEL_REQUESTED | CANCELLING | COMPLETING | FAILED | TIMED_OUT
CANCEL_REQUESTED  -> CANCELLING | CANCELLED | FAILED | TIMED_OUT
CANCELLING        -> CANCELLED | FAILED | TIMED_OUT
CANCELLED         -> IDLE
COMPLETING        -> COMPLETED | FAILED | TIMED_OUT
COMPLETED         -> IDLE
FAILED            -> IDLE
TIMED_OUT         -> IDLE
```

设计意图：所有终态（CANCELLED / COMPLETED / FAILED / TIMED_OUT）都汇回
`IDLE` —— 同一成员可以连续承接多次任务，不必为新一轮额外构造状态对象。

### `TaskStatus` 状态机

合法转移（来自 `TASK_TRANSITIONS`）：

```
PENDING        -> CLAIMED | BLOCKED | CANCELLED
CLAIMED        -> PLAN_APPROVED | COMPLETED | CANCELLED | BLOCKED | PENDING
PLAN_APPROVED  -> COMPLETED | PENDING | CANCELLED
BLOCKED        -> PENDING | CANCELLED
COMPLETED      -> (terminal)
CANCELLED      -> (terminal)
```

`PLAN_APPROVED` 仅对 `MemberMode.PLAN_MODE` 成员激活；`BUILD_MODE` 走
`CLAIMED → COMPLETED` 直通路径。

### Session checkpoint per-team namespace

session checkpoint 全局状态根的 `teams` 桶字段形状：

```python
state["teams"][team_name] = {
    "spec": ...,                  # TeamAgentSpec.model_dump()
    "context": ...,               # TeamRuntimeContext.model_dump()
    "model_allocator_state": ...  # allocator 的 round-robin 游标 / 已分配 model_id 等
    "lifecycle": ...,             # TeamLifecycle 字符串
}
```

读写入口（`runtime/metadata.py`）：

| 函数 | 语义 |
|---|---|
| `read_team_namespace(session, team_name)` | 取桶；缺失返回 `None` |
| `write_team_namespace(session, team_name, payload)` | 整体覆盖该 team 的桶 |
| `merge_team_namespace(session, team_name, partial)` | 浅合并；`partial` 中的 key 只覆盖同名 key，不动其它 |
| `remove_team_namespace(session, team_name)` | 删除桶；返回是否真删了 |

**禁止**直接在 root 上 `update_state({"spec": ...})`（见 I-16）。

### `TeamLifecycle` × `RuntimeState` 矩阵

两枚举不互转，但语义上有交集 —— 同一 team 在 spec 与 pool 两层各取一个值：

| `TeamLifecycle` | `RuntimeState` | 含义 |
|---|---|---|
| TEMPORARY | RUNNING | 一次性团队，正常执行中 |
| TEMPORARY | PAUSED | 一次性团队，已 pause（可 resume，pool 仍持有） |
| PERSISTENT | RUNNING | 持久化团队，当前 round 在跑 |
| PERSISTENT | PAUSED | 持久化团队，跨 round 间 standby |

`TeamLifecycle` 进 spec → 跨进程序列化；`RuntimeState` 留在 pool entry，进程内
in-memory，不写 spec。

## 与其它 spec 的关系

- **`S_01_public-api-and-spec-flow.md`**：本规约钉死 schema 形状，S_01 描述
  这些 Spec 如何流经 `TeamAgentSpec.build()` 与 `Runner.run_agent_team*`。
  schema 是数据，S_01 是控制流。
- **`S_02_team-agent-architecture.md`**：`TeamRuntimeContext` 是
  `TeamAgent.configure(spec, context)` 的入参之一，S_02 钉死 TeamAgent 内部
  四象限分解，本规约提供它消费的字段形状。
- **`S_03_coordination-protocol.md`**：消费 `TeamEvent` / `EventMessage` /
  `TeamTopic`；本规约钉死事件 schema，S_03 钉死 trigger 规则与 handler 路由。
- **`S_04_session-and-recovery.md`**：消费 per-team namespace
  字段（`spec` / `context` / `model_allocator_state` / `lifecycle`）；本规约
  钉死 namespace 形状，S_04 钉死何时读 / 写 / 合并、bind / release 状态机。
- **`S_05_member-spawn-and-stream.md`**：消费 `MemberStatus` / `ExecutionStatus`
  状态机与 `TeamOutputSchema` 升级路径；本规约钉死状态枚举与 stream 字段，
  S_05 钉死状态转移触发点与 stream observer 注册时机。
- **`S_06_runtime-pool-dispatch.md`**：消费 `TeamLifecycle`（spec 字段）与
  `RuntimeState`（pool 字段）的边界（见 I-12）；本规约钉死两枚举的归属，
  S_06 钉死 7 路 dispatch truth table 与 finalize 决策。
- **`S_07_interaction-views-and-hitt.md`**：消费 `TeamRole.HUMAN_AGENT` 与
  `enable_hitt`；本规约钉死字段语义与一致性约束（I-6 / I-7），S_07 钉死
  HITT runtime 表面与三视角交互模型。
- **`S_08_team-tools-contract.md`**：消费 `TaskSummary` / `TaskDetail` /
  `TaskListResult` / `MemberOpResult` / `TaskOpResult` / `TaskCreateResult` /
  `GraphMutationResult`；本规约钉死返回模型与写路径结果包装的形状，S_08
  钉死团队工具的描述契约与 ID 命名。
