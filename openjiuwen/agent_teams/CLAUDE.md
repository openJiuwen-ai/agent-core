# Agent Teams

多智能体团队协作子系统。一个 Leader + 若干 Teammate，通过消息总线与任务数据库协同完成复杂任务。

本文件是本模块的入口索引。深入细节时跳转到：
- `tools/CLAUDE.md` — 团队工具设计与描述契约
- `prompts/CLAUDE.md` — Prompt 模板与语言切换
- `runtime/CLAUDE.md` — 对象池 / 派发 / 并发门禁，spec 公共入口 + leader-only 不变量
- `interaction/CLAUDE.md` — 三视角交互 inbox + HITT runtime 表面
- `agent/CLAUDE.md` — TeamAgent 四象限分解 + spawn / coordination / stream 等 manager
- `cli/CLAUDE.md` — 交互式 TUI / 斜杠命令子模块（prompt_toolkit + rich）

## 公开入口（public API）

公开符号仅限 `__init__.py` 导出。**没有 factory wrapper**——`create_agent_team` / `resume_persistent_team` / `recover_agent_team` 这套已删除，所有 lifecycle 都走 `TeamAgentSpec.build()` + `Runner` facade。

| 入口 | 用途 |
|---|---|
| `TeamAgentSpec(...).build()` | 从零组装 TeamAgent 的唯一公共路径。`agents["leader"]` 必填，`agents["teammate"]` 可选 |
| `Runner.run_agent_team[_streaming](agent_team=spec, session=..., ...)` | 运行入口；冷启动 / 热恢复 / 切 session 由 `runtime` 子系统的 dispatch 表自动选 |
| `cli.run_team_cli(*, specs=None, yaml_paths=None)` | 交互式 TUI 公共入口。`/team` `/session` `/spec` 子命令覆盖 lifecycle 全 facade，普通文本透传 `Runner.interact_agent_team`。详见 `cli/CLAUDE.md` |

**新增配置项一律走 `TeamAgentSpec`**——曾经的"在 factory 函数上堆参数"路径已物理消失。扩参数列表永远是 hack，扩 Spec 才是设计。

**Cold-recover 的低层入口**：`TeamAgent.recover_from_session(session, team_name, runtime_spec=spec)` + `await agent.recover_team()`。仅供运维脚本绕过 Runner 直接拿 leader 实例时使用，常规应用走 `Runner.run_agent_team_streaming(agent_team=spec, session=...)` 即可。

**Runner 入口签名收紧**：`Runner.run_agent_team` / `run_agent_team_streaming` 默认接 `str | TeamAgentSpec`——str 是 `team_name`，要求该 team 已被 spec 路径激活过、pool 里有 entry。已 build 的 `TeamAgent` 实例不再是合法入参。要跑 multi_agent 体系的 `BaseTeam`（`str | BaseTeam`），传同一个方法、加 `base=True` 切到 multi_agent 路径——`Runner` 公共表面只有这一对方法，由 `base` 参数分流。

## 模块地图

```
agent_teams/
├── __init__.py          # 公开 API 聚合导出
├── constants.py         # 保留名（user/team_leader/human_agent）集中定义
├── context.py           # session_id 跨成员/跨模式共享 contextvars
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
├── monitor/             # 团队运行态监控（TeamMonitor 只读视图 + TeamStreamLogger 流式诊断日志）
├── team_workspace/      # 团队共享工作空间（跨成员的文件/锁/版本）
├── cli/                 # 交互式 TUI / 斜杠命令子模块（prompt_toolkit + rich）
└── worktree_remote.py   # 跨机器 worktree 后端（团队专属，generic 实现见 harness/tools/worktree）
```

### agent/ — 运行时主骨架

`TeamAgent` 单一实现同时承担 Leader / Teammate 角色（按 `TeamRole` 切），内部组合一个 `DeepAgent`。字段按"四象限"拆（`blueprint` 静态 / `state` 跨 operator 可变 / `resources` 每实例 / `infra` 每进程），spawn / recovery / session / stream / coordination 各有独立 manager。

详见 [`agent/CLAUDE.md`](agent/CLAUDE.md)。

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
stream.py          # TeamOutputSchema —— OutputSchema 子类，带 source_member / role 成员归属字段
task.py            # TaskSummary / TaskDetail —— 任务返回模型
```

- `Spec` 统一含义：**可 JSON 序列化的装配蓝图**。用 `model_dump()` 可跨进程；不放运行时资源引用。
- `TeamRuntimeContext`：运行时上下文，携带 role / messager_config / db_config 等资源配置，是 `Spec → Runtime` 的边界。
- 新增 spec 字段要想清楚：**属于装配数据**（放 Spec）还是**运行时资源**（放 Config/Manager/Runtime）。不要让 Spec 持有 `Runner`、`Session`、文件句柄。
- **Session checkpoint 状态结构按 team 分桶**：`session.update_state` 的全局状态根上有一个 `teams` namespace —— `state["teams"][team_name] = {spec, context, model_allocator_state, lifecycle, db_state}`。同一 session 可以承载多个 team 的状态；读写一律走 `runtime/metadata.py` 的 `read_team_namespace / merge_team_namespace / read_team_db_state / merge_team_db_state`，不要直接在 root 上 `update_state({"spec": ...})`。`db_state` 用 `pending_create / created / cleaned` 标记 team DB row 生命周期。
- `MemberStatus` 三态分得清：`PAUSED` 是自然 round-end idle（persistent team，coordination kernel `pause` 路径写）；`STOPPED` 是外部 `stop_team` 拆掉 runtime、但 team 仍 live（kernel `stop` 路径写）；`SHUTDOWN` 是永久退场。三者都可经 `RESTARTING` 复活，DB 写状态选谁取决于"为什么 runtime 不在了"。`schema.team.TeamLifecycle`（temporary / persistent）描述静态团队类型，`runtime.pool.RuntimeState`（running / paused）描述对象池中 team 的运行时状态——和 MemberStatus 是不同层次的枚举，不要混用。
- `TeamOutputSchema` 是 `core.session.stream.OutputSchema` 的子类（不污染 core 层），扩出 `source_member: str | None` 与 `role: TeamRole | None`。`Runner.run_agent_team_streaming` 的所有输出 chunk 在 team 路径下都会被 `StreamController` 自动升级为 `TeamOutputSchema` 并打上 `(member_name, role)` 标签。**inprocess 模式**下，`SpawnManager` 在 spawn teammate 时通过 `StreamController.add_chunk_observer` 把 teammate chunk fan-out 到 leader 的 `stream_queue`，让 leader 的 streaming 流出全成员 chunk；subprocess 模式不做转发（chunk 留在 teammate 进程内），扩展点已留好（messager-driven observer）。详见 `agent/CLAUDE.md` 的 StreamController 段。

### models/ — 多模型部署原语

```
pool.py        # ModelPoolEntry / ModelRouterConfig / inherit_pool_ids
               # —— 池条目 + 单端点 router 便利配置 + 池刷新 model_id 继承
allocator.py   # Allocation / ModelAllocator(Protocol)
               # / RoundRobinModelAllocator / ByModelNameAllocator / RouterAllocator
               # build_model_allocator / resolve_member_model
```

- `ModelPoolEntry` 是 `TeamSpec.model_pool` 的元素，描述一个 LLM 端点 + 凭证 + provider；通过 `to_team_model_config()` 物化为 `TeamModelConfig`。
- 持久化身份用 `(model_name, group_index)`，运行时 client 身份用自动 uuid `model_id`；`inherit_pool_ids` 在池刷新时只对 bit-exact 旧条目继承 `model_id`，避免基础设施层缓存到旧凭证的 client。
- 三条分配策略：`RoundRobinModelAllocator`（线性轮转，无视 `model_name`）/ `ByModelNameAllocator`（按 `model_name` 分组、组内轮转）/ `RouterAllocator`（单端点路由，model_name 唯一映射，无 hint 时返回首项）。新策略实现 `ModelAllocator` 协议即可；`build_model_allocator` 读 `team_spec.model_pool_strategy` 派发。
- `ModelRouterConfig` 是用户面向的便利输入：一份 `(api_key, api_base_url, api_provider)` + `model_names: list[str]`。在 `TeamAgentSpec.build()` 时通过 `to_pool_entries()` 展开成 `model_pool` 并把 `model_pool_strategy` 设为 `"router"`，下游 `resolve_member_model` / `inherit_pool_ids` / `update_model_pool` 全部复用 pool 路径，没有特殊分支。
- `model_router` 与 `model_pool` 在 `TeamAgentSpec` 上**互斥**：同时配置直接 `ValueError`。strategy `"router"` 也可以由用户手动配 pool + 设置 strategy 触发，但必须保证 pool 内 `model_name` 唯一（RouterAllocator 在构造时校验）。
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

`TeamRuntimePool` + `TeamRuntimeManager` + 7 路 dispatch truth table + `InteractGate` + `finalize` / `finalize_member` lifecycle hooks，是 `Runner.run_agent_team*` / `interact_agent_team` / `register_human_agent_inbound` / `pause_agent_team` / `stop_team` / `release_session` / `delete_team` 这一组 SDK facade 的实现层。`Runner.run_agent_team*` 公共入口接 `str | TeamAgentSpec`（默认）：spec 走 `manager.activate`（dispatch 决策 + pool 写入），str 是 `team_name`、复用已激活的 pool entry。跨 session 切换由 `activate` 在 dispatch 前 `stop_team` + pool.remove stale entry，再走 cold rebuild；不再保留"warm 跨 session 复用同一 TeamAgent 实例"的路径。run cycle 退出时由 Runner finally 调 `manager.finalize` / `finalize_member` 决定 pause vs stop（coordination kernel 的 `finalize_round` 不再决策）。spawn 出来的 teammate / human-agent 实例走 `Runner.run_agent_team*(member=True)` 入口、跳过 activate/dispatch、不入 pool；退出 finally 调 `finalize_member`。

详见 [`runtime/CLAUDE.md`](runtime/CLAUDE.md)。

### team_workspace/ — 共享工作空间

跨成员的文件共享区，支持锁 / 版本 / 冲突策略。独立于 worktree：**worktree 管代码隔离，workspace 管产物协同**。

### interaction/ — 外部交互入口 + HITT

三种交互视角（`GodViewMessage` / `OperatorMessage` / `HumanAgentMessage`）+ `UserInbox` / `HumanAgentInbox` 的实现层，所有 HITT runtime 表面（`enable_hitt` 分层开关、人类成员来源、一致性约束、运行约束）也落在这里。

详见 [`interaction/CLAUDE.md`](interaction/CLAUDE.md)。

### cli/ — 交互式 TUI / 斜杠命令

prompt_toolkit + rich 驱动的交互式 CLI。`run_team_cli(*, specs, yaml_paths)` 是公共入口，把 `Runner` 暴露的 team lifecycle facade（`run_agent_team_streaming` / `interact_agent_team` / `pause_agent_team` / `stop_agent_team` / `delete_agent_team` / `release` / `list_active_teams` / `register_human_agent_inbound` / `get_agent_team_monitor`）映射成 `/team` `/session` `/spec` 子命令。普通文本透传 `Runner.interact_agent_team`，由 runtime 的 `parse_interact_str` 一处解析 `# / $ / @member` 前缀；CLI 不做二次解析。详见 [`cli/CLAUDE.md`](cli/CLAUDE.md)。

### worktree — Git worktree 隔离

通用实现已下沉到 `openjiuwen.harness.tools.worktree`，由 deepagent 与 team 共用。team 侧只保留三件事：

- 通过 `TeamAgentSpec.worktree`（`WorktreeConfig`）描述配置，`agent_configurator.create_worktree_manager` 在非 LEADER 角色上构造 `WorktreeManager`。
- **workspace 视图软链由 team 侧自管**：`create_worktree_manager` 给 `WorktreeManager` 注入一个翻译适配器，把 `WorktreeCreatedEvent` / `WorktreeRemovedEvent` 路由到 `TeamWorkspaceManager.mount_worktree` / `unmount_worktree`，在共享 team workspace 下维护 `.worktree/{slug}` 软链。这一层是"本 team 当前活跃 worktree 一览"的导航视图，**单 agent 不订阅事件，软链物理上不存在**——`WorktreeManager` 本身不知道软链。
- `worktree_remote.py`：`RemoteWorktreeBackend` / `WorktreeRemoteHandler` 跨机器 worktree 后端，依赖 `paths.get_agent_teams_home`。需要时由调用方直接 `WorktreeManager(backend=RemoteWorktreeBackend(...))` 注入，不走 backend registry（构造参数不止 config）。

`harness/tools/worktree` 暴露的 `WorktreeManager` 接受可选 `event_handler: Callable[[WorktreeEvent], Awaitable[None]]`；team 端如需进一步把生命周期事件桥接到 `TeamEvent.WORKTREE_*` 总线，让上面的 mount/unmount 适配器和总线发布共享同一个 handler 即可（当前总线投递未启用）。

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

## 设计文档归档与双向同步（仅本模块强制）

本模块的设计文档**全部**落在 `docs/` 下，命名与结构规约见 [`docs/CLAUDE.md`](docs/CLAUDE.md)。

把 **`openjiuwen/agent_teams/` 下的代码 + 本目录 `docs/specs/` + `docs/features/`
+ 各级 `<subdir>/CLAUDE.md`** 当作**一个一致性单元**来维护——`agent_teams` 子系统
对契约一致性的要求高于仓库其它模块（多角色 + 多进程 + 持久化状态 + 公共 SDK 表面），
所以本节的强制约束**只限于** `openjiuwen/agent_teams/` 子树，不上推到仓库其它模块。

三条强制约束，提交时必查：

1. **每次特性更新提交代码前必须归档 feature 文档**：在 `docs/features/` 下新增一份
   `F_NN_<slug>.md`，记录决策、拒绝的方案、验证基线、已知遗留。commit message 只写 what，
   feature 文档负责写 why / why-not。归档与代码改动应在同一次 commit（或紧邻的两次 commit）落地，
   避免设计上下文随时间漂移。
2. **所有模块设计规约变动必须更新 specs 文档**：模块契约、跨子模块的公共协议、不变量、
   公共 API 形态发生变化时，同步修订对应 `docs/specs/S_NN_<slug>.md`；新规约 = 新 spec 文件。
   规约变了但 specs 没改，下次读 spec 的人就被误导——这是设计债，不是文档懒。
3. **双向同步：读到与代码不一致的描述必须当场修文档**。在本模块任何一份
   `CLAUDE.md` / `docs/specs/S_*` / `docs/features/F_*` 里读到的接口名、枚举值、
   truth table 行数、文件路径、不变量等只要与当前代码不符，**不要**把过时表述当作新约束去执行、
   也不要原样转述给用户；先 `grep` 代码、以代码为准刷新文档，在同一次改动里落地。
   `CLAUDE.md` 里每条点名了"X 个分支 / Y 路 dispatch / Z 方法"的句子都是契约的一部分。

收尾规范：

- spec 头部"最近一次修订日期"字段在每次修订该 spec 时填当天日期（`YYYY-MM-DD`）；feature 文档用
  头部"日期"字段记录归档当天，不设独立修订字段。**不要在元信息里写 commit hash**——避免"提交后
  回填"的来回反复。
- 子模块自身的本地约定继续放各 `<subdir>/CLAUDE.md`；跨子模块的设计规约一律落到 `docs/specs/`，
  不要塞进单一子目录的 CLAUDE.md。
- 拿不准某次改动算 feature-grade（要 `F_NN_*.md`）还是普通修复（不必归档）时，先问用户。
  歧义情况默认**归档**——多一份 markdown 的成本远低于丢失设计上下文。

## 提交约定

本模块改动的 commit footer 固定：`Refs: #751`。见 `CLAUDE.local.md` 的 Git 提交规范。
