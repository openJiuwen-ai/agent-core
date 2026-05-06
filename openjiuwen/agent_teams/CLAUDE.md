# Agent Teams

多智能体团队协作子系统。一个 Leader + 若干 Teammate，通过消息总线与任务数据库协同完成复杂任务。

本文件是本模块的入口索引。深入细节时跳转到：
- `tools/CLAUDE.md` — 团队工具设计与描述契约
- `prompts/CLAUDE.md` — Prompt 模板与语言切换

## 公开入口（public API）

只有两类东西是公开的：`__init__.py` 导出的符号 + `factory.py` 的两个便利函数。

| 入口 | 用途 |
|---|---|
| `create_agent_team(agents={...}, ...)` | 从零组装 TeamAgent。`agents["leader"]` 必填，`agents["teammate"]` 可选 |
| `resume_persistent_team(agent, new_session_id)` | 在新 session 中恢复已完成一轮的持久化团队 |
| `TeamAgentSpec.build()` | 任何希望走 pydantic 模型路径的调用者的统一入口；`create_agent_team` 是它的便利封装 |

**新增配置项走 `TeamAgentSpec`，不要在 `create_agent_team` 上堆 `**kwargs` 或平铺参数。** 扩参数列表是 hack，扩 Spec 才是设计。

## 模块地图

```
agent_teams/
├── __init__.py          # 公开 API 聚合导出
├── constants.py         # 保留名（user/team_leader/human_agent）集中定义
├── context.py           # session_id 跨成员/跨模式共享 contextvars
├── factory.py           # create_agent_team / resume_persistent_team 便利函数
├── i18n.py              # 运行时中/英文字符串（仅装运行时 hard-coded 串）
├── paths.py             # 文件系统布局单一真相源
├── schema/              # 全部数据模型（Spec / Context / Event / Status / Task）
├── models/              # 多模型部署原语（ModelPoolEntry / 池继承 / Allocator）
├── agent/               # 核心运行时（TeamAgent 本体 + 装配链）
├── prompts/             # 系统提示词模板、加载器、PromptSection 构造与缓存
├── rails/               # 团队相关 Rail（系统提示词注入 / 首迭代门 / 工具审批）
├── runtime/             # Runner 进程内 TeamAgent 对象池 + 派发决策 + Run/Interact 并发门禁
├── interaction/         # 外部交互入口（UserInbox / HumanAgentInbox / @ 路由）
├── tools/               # 团队工具（Leader / Teammate / Human Agent 可调用的原子操作）
├── messager/            # 消息传输层（inprocess / pyzmq）
├── spawn/               # 成员启动（process / inprocess）
├── monitor/             # 团队运行态监控
├── team_workspace/      # 团队共享工作空间（跨成员的文件/锁/版本）
└── worktree_remote.py   # 跨机器 worktree 后端（团队专属，generic 实现见 harness/tools/worktree）
```

### agent/ — 运行时主骨架

| 文件 | 职责 |
|---|---|
| `team_agent.py` | `TeamAgent`（单一实现同时承担 Leader / Teammate 角色，内部组合一个 `DeepAgent`） |
| `coordination/` | `CoordinatorLoop` 事件驱动唤醒循环 + dispatcher，**不做决策**，只负责 wake-up 和轮询 |
| `member.py` | `TeamMember` 成员状态管理 |
| `agent_configurator.py` | DeepAgent 装配，挂载 prompts/ 与 rails/ 子模块（spawn 时复用 `models/` 暴露的 allocator 回调） |

### prompts/ — 系统提示词

| 文件 | 职责 |
|---|---|
| `loader.py` | `load_template` / `load_shared_template`，按语言加载 `.md` |
| `policy.py` | `role_policy` / `build_system_prompt` 老装配路径，消费 `system_prompt.md` |
| `sections.py` | `TeamSectionName` + `build_team_*_section` 构造 `PromptSection`（主力路径） |
| `section_cache.py` | `MtimeSectionCache`：dynamic section 的 mtime 缓存 |
| `system_prompt.md` | 语言无关的占位符模板 |
| `cn/` · `en/` | 角色 / 工作流 / 生命周期模板 |

**两条装配路径（`policy.build_system_prompt` vs `sections.build_team_*_section`）读的是同一批 `.md`**。改正文自动同时生效；结构性变更（占位符、section 拆分）要明确落到哪条路径。详见 `prompts/CLAUDE.md`。

### rails/ — 团队 Rail 注入

| 文件 | 职责 |
|---|---|
| `team_policy_rail.py` | `TeamPolicyRail`：把 prompts/sections 的 `PromptSection` 注入 `SystemPromptBuilder`；含 `MtimeSectionCache` 驱动的 dynamic section 刷新 |
| `first_iteration_gate.py` | `FirstIterationGate`：异步信号，等到 agent 真正进入 task loop 才放行 steer / follow_up |
| `tool_approval_rail.py` | `TeamToolApprovalRail`：teammate 调工具时通过消息向 leader 申请审批的中断 rail |

### schema/ — 数据模型分层

```
blueprint.py       # TeamAgentSpec / LeaderSpec / TransportSpec / StorageSpec —— 顶层装配蓝图
deep_agent_spec.py # DeepAgentSpec / SubAgentSpec / RailSpec 等 —— DeepAgent 侧的 Spec
team.py            # TeamSpec / TeamRole / TeamLifecycle / TeamRuntimeContext / TeamMemberSpec
events.py          # EventMessage / TeamTopic —— 跨进程事件
status.py          # MemberStatus / ExecutionStatus —— 状态机枚举
task.py            # TaskSummary / TaskDetail —— 任务返回模型
```

- `Spec` 统一含义：**可 JSON 序列化的装配蓝图**。用 `model_dump()` 可跨进程；不放运行时资源引用。
- `TeamRuntimeContext`：运行时上下文，携带 role / messager_config / db_config 等资源配置，是 `Spec → Runtime` 的边界。
- 新增 spec 字段要想清楚：**属于装配数据**（放 Spec）还是**运行时资源**（放 Config/Manager/Runtime）。不要让 Spec 持有 `Runner`、`Session`、文件句柄。
- **Session checkpoint 状态结构按 team 分桶**：`session.update_state` 的全局状态根上有一个 `teams` namespace —— `state["teams"][team_name] = {spec, context, model_allocator_state, lifecycle}`。同一 session 可以承载多个 team 的状态；读写一律走 `runtime/metadata.py` 的 `read_team_namespace / merge_team_namespace`，不要直接在 root 上 `update_state({"spec": ...})`。
- `MemberStatus.PAUSED`：teammate 协程退出但状态保留，可经 `RESTARTING` 重新拉起；与 `SHUTDOWN`（永久退出）区分。`schema.team.TeamLifecycle`（temporary / persistent）描述静态团队类型，`runtime.pool.RuntimeState`（running / paused）描述对象池中 team 的运行时状态——两个枚举各管各的。

### models/ — 多模型部署原语

```
pool.py        # ModelPoolEntry / inherit_pool_ids —— 池条目数据结构 + 池刷新 model_id 继承
allocator.py   # Allocation / ModelAllocator(Protocol) / RoundRobinModelAllocator / ByModelNameAllocator
               # build_model_allocator / resolve_member_model
```

- `ModelPoolEntry` 是 `TeamSpec.model_pool` 的元素，描述一个 LLM 端点 + 凭证 + provider；通过 `to_team_model_config()` 物化为 `TeamModelConfig`。
- 持久化身份用 `(model_name, group_index)`，运行时 client 身份用自动 uuid `model_id`；`inherit_pool_ids` 在池刷新时只对 bit-exact 旧条目继承 `model_id`，避免基础设施层缓存到旧凭证的 client。
- 两条分配策略：`RoundRobinModelAllocator`（线性轮转，无视 `model_name`）/ `ByModelNameAllocator`（按 `model_name` 分组、组内轮转）。新策略实现 `ModelAllocator` 协议即可；`build_model_allocator` 读 `team_spec.model_pool_strategy` 派发。
- 空池 → `build_model_allocator` 返回 `None` → 走 `TeamAgentSpec.agents` per-agent 模型配置兜底。
- 新增模型相关原语优先放本目录，避免渗回 `schema/team.py`（schema 层只声明字段引用，实现在 models/）。

### 基础设施插件化：TransportSpec / StorageSpec

两者通过 **注册表 + type 字符串** 解析具体实现：

```python
register_transport("custom", MyTransportConfig)
register_storage("mysql", MyStorageConfig)
```

- 内置类型：transport = `inprocess` / `pyzmq`；storage = `sqlite` / `memory`。
- `TransportSpec.build()` / `StorageSpec.build()` 是 spec → 实例的唯一桥。不要绕过注册表直接 import 具体 Config 类。
- 新后端实现前先查 `_ensure_builtin_infra_registered`，避免重复登记或依赖环。

### messager/ — 消息传输

| 文件 | 作用 |
|---|---|
| `base.py` | `MessagerTransportConfig`、`MessagerPeerConfig`、`create_messager` 工厂 |
| `messager.py` | `Messager` 抽象接口 + `MessagerHandler` |
| `inprocess.py` | 进程内内存 pub/sub |
| `pyzmq_backend.py` | 基于 ZeroMQ 的跨进程传输 |

Messager 是点对点 + broadcast 的统一抽象，**任何直接新建 socket / 操作 asyncio queue 的代码都是错的**。经由 `create_messager(config)`。

### spawn/ — 成员启动

两种启动模式走不同入口，但对外由 `TeamAgent.spawn_member` 统一：

- `spawn_mode="process"` → `Runner.spawn_agent` 走子进程（跨平台，默认）。
- `spawn_mode="inprocess"` → `inprocess_spawn` 在同 event loop 启动协程（适合测试或轻量场景）。
- 顶层 `context.py` 的 `session_id` contextvars 是两种模式共享的上下文载体。

### tools/ — 团队工具集合

参见 `tools/CLAUDE.md`。要点：

- `create_team_tools(role=..., teammate_mode=..., exclude_tools=..., lang=...)` 是唯一入口。
- 工具描述文本是**行为契约**，不是 feature 摘要。长文案放 `tools/locales/descs/<lang>/<tool>.md`。
- ToolCard ID 统一 `team.{name}` 前缀。

### runtime/ — TeamAgent 对象池 + 派发 + 并发门禁

| 文件 | 职责 |
|---|---|
| `pool.py` | `TeamRuntimePool` / `ActiveTeam` / `RuntimeState`。pool key 是 `team_name`，同一 team 在内存中至多一个 `ActiveTeam` 实例；同 session 多 team 通过 pool 多 entry 自然支持。`RuntimeState.RUNNING / PAUSED` 是运行时状态（与 `schema.team.TeamLifecycle` "temporary/persistent" 不同） |
| `gate.py` | `InteractGate` / `AdmissionTicket`。run / interact 并发门禁：`admit / consume_done / close_and_drain / reset`。每个 `ActiveTeam` 自带一个 gate |
| `dispatch.py` | `decide_run_action(...)` 纯函数 + `RunAction` / `RunActionKind`。9 路 truth table：`CREATE / NEW_TEAM_IN_SESSION / NEW_TEAM_IN_SESSION_WARM / COLD_RECOVER / WARM_RECOVER / RESUME_FROM_PAUSE / REJECT_RUNNING / REJECT_ORPHANED / REJECT_INCONSISTENT` |
| `metadata.py` | session checkpoint 的 per-team namespace 读写：`read_team_namespace / write_team_namespace / merge_team_namespace / read_team_names_in_session`。状态结构 `state["teams"][team_name] = {spec, context, model_allocator_state, lifecycle}`，按 team 分桶 |
| `manager.py` | `TeamRuntimeManager`：持有 `TeamRuntimePool`；`activate` 用 `decide_run_action` 派发后调用 `_apply_action` 执行副作用；`pause / interact / stop_team / release_session / delete_team` 通过 pool 查 entry。`interact` 接 `InteractPayload`，走 gate 的 `admit / consume_done`，分发到 `UserInbox`（GodView / Operator）或 `HumanAgentInbox`（HumanAgent） |

- **派发决策是纯函数**：`decide_run_action` 只看 `(team_in_db, team_in_session, pool_entry, target_session_id, target_team_name)`，无副作用。manager 把 IO 收集进派发输入，然后 `_apply_action` 才动 factory / pool / session。改派发时改 `dispatch.py`，不要把决策逻辑塞回 manager。
- **同一 team 在内存中只有一个实例**：切 session 走 `recover_for_existing_session`（warm），不要让 pool 出现多个相同 `team_name` 的 entry。
- **InteractGate 的生命周期与 run cycle 对齐**：`run_agent_team[_streaming]` 退出 `finally` 调 `_close_team_interact_gate`（Runner 内 helper）；warm 路径 activate 时调 `gate.reset()` 让下一个 cycle 重新放行。
- **静止前置**：`release_session` / `delete_team` 在 pool 仍持有相关 entry 时报 `AGENT_TEAM_BUSY_INVALID`（ValidationError），调用方必须先 `stop_team`。
- `Runner` 在 `_get_team_runtime_manager()` 里 lazy import 它，避免子进程 bootstrap 时拉链。

### team_workspace/ — 共享工作空间

跨成员的文件共享区，支持锁 / 版本 / 冲突策略。独立于 worktree：**worktree 管代码隔离，workspace 管产物协同**。

### interaction/ — 外部交互入口

| 文件 | 作用 |
|---|---|
| `payload.py` | `GodViewMessage` / `OperatorMessage` / `HumanAgentMessage` 三种交互视角的 dataclass + `InteractPayload` Union + `DeliverResult(ok, message_id, reason)` 统一返回类型 |
| `router.py` | `parse_mention(raw) -> (target, body) \| None` 纯函数；`is_reserved_name(name)` 校验保留名 |
| `user_inbox.py` | `UserInbox`：user 侧显式 API。`broadcast` / `direct` / `deliver_to_leader`，全部返回 `DeliverResult` |
| `human_agent_inbox.py` | `HumanAgentInbox`：human_agent 对外发声，仅在 HITT 启用时可用。`send` 成功返 `DeliverResult`；HITT 关闭抛 `HumanAgentNotEnabledError`，未知 sender 抛 `UnknownHumanAgentError`（manager 层捕获转 `DeliverResult.failure(reason)`） |

- `Runner.interact_agent_team(payload, *, team_name, session_id)` 接收 `InteractPayload`，bare `str` 作为 `GodViewMessage` 的便捷形式。
- 三视角到 inbox 的 dispatch 统一在 `runtime/manager.py:_dispatch_payload`：GodView → `deliver_to_leader`，Operator(target=None/x) → `UserInbox.broadcast/direct`，HumanAgent → `HumanAgentInbox.send`。
- 旧的 `@xxx body` 解析从 `agent/dispatcher.py` 移到这里——dispatcher 只保留调用点。`TeamAgent.broadcast()` 和 `TeamAgent.human_agent_say()` 也走这一层。

### HITT（Human in the Team）

`enable_hitt` 是**分层开关**：

- `TeamAgentSpec.enable_hitt`（spec 层）= 能力天花板（capability ceiling）。True 才允许 HITT；False 时所有 human-agent 创建路径全部拒绝。
- `build_team(enable_hitt=...)`（工具参数，`Optional[bool]`）= 本次实例的运行时开关。`None` 继承 spec；`True` 显式启用（要求 spec=True，否则报错）；`False` 显式禁用（即使 spec=True 也覆盖，跳过预配的 HUMAN_AGENT 并 warning）。

人类成员的来源：

- **静态**：在 `TeamAgentSpec.predefined_members` 显式声明 `role_type=HUMAN_AGENT` 成员（自定 `member_name`，可多人）。框架不再隐式注入默认 `human_agent`。
- **动态**：leader 在已建团后通过 `spawn_member(role_type='human_agent', member_name=..., display_name=..., desc=...)` 拉新人类成员加入。`role_type='human_agent'` 时禁止传 `model_name` / `prompt`（由框架内置模板托管）。

一致性约束（`TeamAgentSpec.build()` 时 fail-fast）：

- `enable_hitt=False` 且 `predefined_members` 含 HUMAN_AGENT → `AGENT_TEAM_CONFIG_INVALID`（特性禁了但预配了人）。
- `enable_hitt=True` 且 `predefined_members` 无 HUMAN_AGENT → 允许（动态 spawn 路径）。

`_resolve_team_mode`（`agent_configurator.py:55`）只把**非 HUMAN_AGENT** 的 predefined member 计入 predefined 派生 —— 所以纯 HITT 团队（仅声明人类成员）仍然是 `default` 模式，leader 保留 `spawn_member` 工具。

运行约束（代码层 + Prompt 层双重保证）：

1. `human_agent` 是保留成员名（`constants.RESERVED_MEMBER_NAMES`），用作动态 spawn 的默认人类成员名；自定 HUMAN_AGENT 成员名可避开此保留名。普通 teammate 的 predefined 成员仍然不允许撞保留名（`_validate_reserved_names`）。
2. human-agent 走标准 UNSTARTED → spawn 流程（与 teammate 一致），但工具集仅保留 `view_task` + `member_complete_task`（`HUMAN_AGENT_TOOLS`）；rail 装配会剥离 `FirstIterationGate` / `TeamToolApprovalRail`。
3. 一旦 `task.assignee` 指向某个 human-agent 且状态 CLAIMED，`UpdateTaskTool` 拒绝 reassign 和 cancel；批量 cancel 链路也跳过。
4. 发送给 human-agent 的点对点消息 `is_read=True`；广播后 human-agent 的 `read_at` 立即跟进。
5. TeamPolicyRail 注入 `team_hitt` section（priority=12），按 role 给 leader/teammate/human_agent 下达角色特定的行为约束。section 注入条件来自 `backend.hitt_enabled()` —— 反映运行时 effective flag，不依赖 roster 是否已 spawn。

### worktree — Git worktree 隔离

通用实现已下沉到 `openjiuwen.harness.tools.worktree`，由 deepagent 与 team 共用。team 侧只保留两件事：

- 通过 `TeamAgentSpec.worktree`（`WorktreeConfig`）描述配置，`agent_configurator.create_worktree_manager` 在非 LEADER 角色上构造 `WorktreeManager`。
- `worktree_remote.py`：`RemoteWorktreeBackend` / `WorktreeRemoteHandler` 跨机器 worktree 后端，依赖 `paths.get_agent_teams_home`。需要时由调用方直接 `WorktreeManager(backend=RemoteWorktreeBackend(...))` 注入，不走 backend registry（构造参数不止 config）。

`harness/tools/worktree` 暴露的 `WorktreeManager` 接受可选 `event_handler: Callable[[WorktreeEvent], Awaitable[None]]`；team 端如需把生命周期事件桥接到 `TeamEvent.WORKTREE_*` 总线，在 `create_worktree_manager` 里传一个适配器即可（当前未启用）。

## 架构铁律

1. **Card / Config 分层不可破坏**  
   Card（`AgentCard`、`ToolCard`）是可序列化标识；Config / Manager / Runtime 持运行时状态。不要把 `runner` / `session` 塞进 Card；不要在 Config 上定义 static description 字段。详见 `.claude/rules/architecture.md`。

2. **TeamAgent 是单一实现**  
   Leader / Teammate 通过 `TeamRole` 切换行为，不是两个类。新增角色前先想：真的需要新类，还是一个新的 policy 分支？

3. **coordinator 不做决策**  
   `CoordinatorLoop` 只管 wake-up，所有业务行为由内部 DeepAgent + team tools 驱动。不要把业务逻辑塞到 loop 里。

4. **i18n 的两条路径**  
   - 运行时 hard-coded 字符串（dispatcher 通知、default persona 等）走 `agent_teams/i18n.py` 的 `t(key)`；
   - Prompt / 工具描述的长文本走各自模块的 `locales/` 或 `prompts/`（按 `lang` 参数入参传递）。  
   **新增字符串前先判断归属**：运行时提示进 `i18n.py`，模板正文进 `prompts/` 或 `tools/locales/descs/`。

5. **paths.py 是文件系统布局的单一真相源**  
   `get_agent_teams_home()`、`team_home(team_name)`、`independent_member_workspace(member_name)`。创建和清理都走这里，不要散落 `Path("…")` 硬编码。

6. **Spec → build() → Runtime 是单向流**  
   Spec 不保留运行时引用；build() 产出运行时对象；运行时对象不回写 Spec。想支持热更新？通过 session + resume 路径，而不是反向污染 Spec。

## 测试

- 单测路径镜像源码：`openjiuwen/agent_teams/tools/team_tools.py` → `tests/unit_tests/agent_teams/tools/test_team_tools.py`
- 内存后端：`MemoryDatabaseConfig` + `InProcessMessager` 适合单测，不依赖 sqlite / zmq。
- 多成员场景优先用 `spawn_mode="inprocess"`，避免子进程拉起开销。
- `pytest` 纯函数风格；禁止 `print`，改用 `team_logger` 或 test_logger。

## 提交约定

本模块改动的 commit footer 固定：`Refs: #751`。见 `CLAUDE.local.md` 的 Git 提交规范。
