# Agent Teams

多智能体团队协作子系统。一个 Leader + 若干 Teammate，通过消息总线与任务数据库协同完成复杂任务。

本文件是本模块的入口索引。深入细节时跳转到：
- `tools/AGENTS.md` — 团队工具设计与描述契约
- `prompts/AGENTS.md` — Prompt 模板与语言切换
- `runtime/AGENTS.md` — 对象池 / 派发 / 并发门禁，spec 公共入口 + leader-only 不变量
- `interaction/AGENTS.md` — 三视角交互 inbox + HITT runtime 表面
- `agent/AGENTS.md` — TeamAgent 四象限分解 + spawn / coordination / stream 等 manager
- `cli/AGENTS.md` — 交互式 TUI / 斜杠命令子模块（prompt_toolkit + rich）

## 公开入口（public API）

公开符号仅限 `__init__.py` 导出。**没有 factory wrapper**——`create_agent_team` / `resume_persistent_team` / `recover_agent_team` 这套已删除，所有 lifecycle 都走 `TeamAgentSpec.build()` + `Runner` facade。

| 入口 | 用途 |
|---|---|
| `TeamAgentSpec(...).build()` | 从零组装 TeamAgent 的唯一公共路径。`agents["leader"]` 必填，`agents["teammate"]` 可选 |
| `Runner.run_agent_team[_streaming](agent_team=spec, session=..., ...)` | 运行入口；冷启动 / 热恢复 / 切 session 由 `runtime` 子系统的 dispatch 表自动选 |
| `cli.run_team_cli(*, specs=None, yaml_paths=None)` | 交互式 TUI 公共入口。`/team` `/session` `/spec` 子命令覆盖 lifecycle 全 facade，普通文本透传 `Runner.interact_agent_team`。详见 `cli/AGENTS.md` |

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
├── timefmt.py           # 毫秒 epoch → "绝对本地时间 + 相对差" 渲染（喂 LLM/观测，文案走 i18n）
├── inbound_render.py    # 入站消息/框架事件 → <team-inbound>/<team-event>/<team-note> XML 渲染（纯函数，喂 LLM；文案由 handler 从 i18n 取）。见 F_46
├── message_template.py  # 框架模板消息的两阶段渲染：发送存意图（消息行 content 空 + meta={template,refs,params}），投递时按收件人语言加载 prompts/<lang>/<key>.md、用 {{task.*}}/{{member.*}}/{{param.*}} 填当前行（单遍替换不二次扫描、字段白名单、失败降级为 meta 合成的 fallback 行）。见 F_63
├── tiny_agent.py        # Tiny Agent：随时唤起的极简 NativeHarness（system_prompt + model + 仅结构化输出工具）；run 单轮 / chat 多轮；ephemeral（含 title/summary 预定义）+ team-scoped（TeamAgentSpec.tiny_agents 多实例，TeamInfra 持有）。见 F_45
├── paths.py             # 文件系统布局单一真相源
├── schema/              # 全部数据模型（Spec / Context / Event / Status / Task）
├── models/              # 多模型部署原语（ModelPoolEntry / 池继承 / Allocator）
├── agent/               # 核心运行时（TeamAgent 本体 + 装配链）
├── prompts/             # 系统提示词模板、加载器、PromptSection 构造与缓存
├── rails/               # 团队 Rail + manifest 元素声明（team rail / 内置 rail·tool / context handle / team confirm payload）
├── security/            # Team 权限安全辅助（permission narrowing 等）
├── runtime/             # Runner 进程内 TeamAgent 对象池 + 派发决策 + Run/Interact 并发门禁
├── interaction/         # 外部交互入口（UserInbox / HumanAgentInbox / @ 路由）
├── tools/               # 团队工具（Leader / Teammate / Human Agent 可调用的原子操作）
├── messager/            # 消息传输层（inprocess / pyzmq）
├── spawn/               # 成员启动（process / inprocess）
├── monitor/             # 团队运行态监控（TeamMonitor 只读视图 + TeamStreamLogger 流式诊断日志）
├── reliability/         # 主动可靠性框架（健康信号采集 rail + 检测器 + 分级处置；opt-in）
├── team_workspace/      # 团队共享工作空间（跨成员的文件/锁/版本）
├── cli/                 # 交互式 TUI / 斜杠命令子模块（prompt_toolkit + rich）
├── external/            # 外部 agent 接入核心（ExternalTeamClient：scope 分化 member 真实工具 / operator 控制面）
├── skill/               # 外部 agent 的非交互 CLI + SKILL_member.md / SKILL_operator.md（按 scope 分化）
├── mcp/                 # 外部 agent 的 stdio MCP server（低层 mcp.server.lowlevel.Server，按 scope 分化）
├── workflow/            # Swarmflow 多 agent 工作流编排（dw 引擎移植 + worker backend + 4 层表示）
└── worktree_remote.py   # 跨机器 worktree 后端（团队专属，generic 实现见 harness/tools/worktree）
```

### agent/ — 运行时主骨架

`TeamAgent` 单一实现同时承担 Leader / Teammate 角色（按 `TeamRole` 切），内部组合一个 `DeepAgent`。字段按"四象限"拆（`blueprint` 静态 / `state` 跨 operator 可变 / `resources` 每实例 / `infra` 每进程），spawn / recovery / session / stream / coordination 各有独立 manager。

详见 [`agent/AGENTS.md`](agent/AGENTS.md)。

### prompts/ — 系统提示词

| 文件 | 职责 |
|---|---|
| `loader.py` | `load_template`，按语言加载 `.md` |
| `sections.py` | `TeamSectionName` + `build_team_*_section` 构造 `PromptSection`（唯一装配路径）；`build_team_role_section` 直接读 `leader_policy` / `teammate_policy` |
| `section_cache.py` | `MtimeSectionCache`：dynamic section 的 mtime 缓存 |
| `cn/` · `en/` | 角色 / 工作流 / 生命周期模板 |

**唯一装配路径是 `sections.build_team_*_section`**（由 `TeamPolicyRail` / `build_team_member_system_prompt` 消费，各 builder 直接 `load_template` 读对应 `.md`）。改正文即时生效。详见 `prompts/AGENTS.md`。

### rails/ — 团队 Rail 注入 + manifest 声明

team rail 全部经 **manifest 声明式装配**（`@harness_element` provider），不再手搓 `new` —— 见
`docs/features/F_32_declarative-harness-assembly.md`。`AgentConfigurator` 把 team rail 作为
`RailSpec` 注入 `build_spec.rails`，live handle 经 `BuildContext.extras` 传给工厂，rail 随
`spec.build` + `ensure_initialized` 自动挂载 / init（不再有 `_MountedRails` / 手动 set/init /
customizer 后处理）。

| 文件 | 职责 |
|---|---|
| `team_policy_rail.py` | `TeamPolicyRail`：所有团队 section 均静态（role / HITT 协作契约 [gate `hitt_enabled`] / bridge avatar 自契约 [仅 BRIDGE_AGENT] / workflow / dispatch [gate `dispatch_mode`，LEADER + TEAMMATE] / lifecycle / private / extra + 两个说明 section），init 建一次注入 `SystemPromptBuilder`（前缀 KV cache 稳定）；仅 churn 的 `team_members`（统一名册，人类标 `[human]`）/ `team_info` 经探针缓存刷新后挂 `prompt_attachment_manager`（`_sync_dynamic_sections` 不碰 builder）。见 F_46 / F_50 / F_52 |
| `confirm_payload.py` | `TeamConfirmPayload` + `TeamPermissionConfirmResponse`：team-specific confirmation payload/response models（extend harness base classes with `decided_by` tracking） |
| `team_permission_rail.py` | `TeamPermissionRail` + `TeamApprovalOrchestrator`：team-mode permission guardrail；继承 `PermissionInterruptRail`，leader-mediated ASK resolution + session-scoped auto-confirm（`_persist_allow_always=False`）。`enable_permissions=True` 时替代 `TeamToolApprovalRail` |
| `tool_approval_rail.py` | `TeamToolApprovalRail`：teammate 调工具时通过消息向 leader 申请审批的中断 rail（`enable_permissions=False` 时使用） |
| `team_tool_rail.py` / `team_plan_mode_rail.py` | `TeamToolRail`（协同工具注册）/ `TeamPlanModeRail`（plan mode 提示叠加） |
| `elements.py` | 7 个 team rail 的 `@harness_element` 工厂 + `ConstructionInput`（`team.tool`/`team.policy`/`team.workspace`/`team.tool_approval`/`team.permission`/`team.plan_mode`/`team.reliability`） |
| `team_context.py` | `TeamHandleKey` + accessor + `inject_team_handles`：team live handle 经 `BuildContext.extras` 的 key 常量 + 类型化读取。rail 不缓存——需跨重建存活的状态（如 `reliability_components`）作为复用对象注入，由每轮新建的 rail 包装 |
| `builtin_elements.py` | openjiuwen 内置 rail/tool（`task_planning`/`skill_use`/`web_search` 等）的 `@harness_element` 声明——取代已删的 class registry |
| `registration.py` | `ensure_harness_elements_registered()`：import elements → `register_from_catalog()`，spec build 路径的统一注册入口 |

### schema/ — 数据模型分层

```
blueprint.py       # TeamAgentSpec / LeaderSpec / TransportSpec / StorageSpec —— 顶层装配蓝图
deep_agent_spec.py # DeepAgentSpec / SubAgentSpec / RailSpec 等 —— DeepAgent 侧的 Spec。RailSpec/BuiltinToolSpec 只走 provider（class registry _RAIL_TYPE_REGISTRY/_TOOL_TYPE_REGISTRY 已删，见 F_32）；DeepAgentSpec.resolve_parts/build 分离
team.py            # TeamSpec / TeamRole / TeamLifecycle / TeamRuntimeContext / TeamMemberSpec
events.py          # EventMessage / TeamTopic —— 跨进程事件
status.py          # MemberStatus / ExecutionStatus —— 状态机枚举
stream.py          # TeamOutputSchema —— OutputSchema 子类，带 source_member / role 成员归属字段
task.py            # TaskSummary / TaskDetail —— 任务返回模型
```

- `Spec` 统一含义：**可 JSON 序列化的装配蓝图**。用 `model_dump()` 可跨进程；不放运行时资源引用。
- `TeamRuntimeContext`：运行时上下文，携带 role / messager_config / db_config 等资源配置，是 `Spec → Runtime` 的边界。
- 新增 spec 字段要想清楚：**属于装配数据**（放 Spec）还是**运行时资源**（放 Config/Manager/Runtime）。不要让 Spec 持有 `Runner`、`Session`、文件句柄。
- **Session checkpoint 状态结构按 team 分桶**：`session.update_state` 的全局状态根上有一个 `teams` namespace —— `state["teams"][team_name] = {spec, context, model_allocator_state, lifecycle, db_state, pending_resume}`。同一 session 可以承载多个 team 的状态；读写一律走 `runtime/metadata.py` 的 `read_team_namespace / merge_team_namespace / read_team_db_state / merge_team_db_state / read_pending_resume / merge_pending_resume / clear_pending_resume`，不要直接在 root 上 `update_state({"spec": ...})`。`db_state` 用 `pending_create / created / cleaned` 标记 team DB row 生命周期；`pending_resume`（`{"query": ...}`，leader-only）由 `kernel.pause` 写、`kernel.start` 尾部消费，使 `pause → stop → start` 等价于 `pause → resume`（见 [[F_61]]）。
- `MemberStatus` 状态流转：`UNSTARTED`（DB 记录已创建，agent 进程未启动）→ `STARTING`（CAS guard 占位，正在 spawn）→ `READY`（agent 进程已就绪）→ `BUSY`/`PAUSED`/`STOPPED`/`SHUTDOWN`/`ERROR`。`STARTING` 是过渡态——只有第一个 startup 路径能 CAS 成功 `UNSTARTED→STARTING`，第二个并发路径查到 STARTING/READY 直接跳过。spawn 失败时 rollback `STARTING→UNSTARTED` 保证可重试。`PAUSED` 是自然 round-end idle（persistent team）；`STOPPED` 是外部 `stop_team` 拆掉 runtime、但 team 仍 live；`SHUTDOWN` 是永久退场。`BUSY`/`PAUSED`/`STOPPED`/`SHUTDOWN` 可经 `RESTARTING` 复活。`schema.team.TeamLifecycle`（temporary / persistent）描述静态团队类型，`runtime.pool.RuntimeState`（running / paused）描述对象池中 team 的运行时状态——和 MemberStatus 是不同层次的枚举，不要混用。
- `TeamOutputSchema` 是 `core.session.stream.OutputSchema` 的子类（不污染 core 层），扩出 `source_member: str | None` 与 `role: TeamRole | None`。`Runner.run_agent_team_streaming` 的所有输出 chunk 在 team 路径下都会被 `StreamController` 自动升级为 `TeamOutputSchema` 并打上 `(member_name, role)` 标签。**inprocess 模式**下，`SpawnManager` 在 spawn teammate 时通过 `StreamController.add_chunk_observer` 把 teammate chunk fan-out 到 leader 的 `stream_queue`，让 leader 的 streaming 流出全成员 chunk；subprocess 模式不做转发（chunk 留在 teammate 进程内），扩展点已留好（messager-driven observer）。详见 `agent/AGENTS.md` 的 StreamController 段。

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

**注册与拉起是两件事**（细则见 `docs/specs/S_05` 不变量 1）：

- **注册**：四个 `spawn_*` 工具与 `build_team` 的 predefined 成员都只落到 `TeamBackend.spawn_member`——**只写 DB 行（`UNSTARTED`），不启动任何东西**（该方法 docstring 即契约："does NOT start the member"）。
- **拉起**：只有一条链 —— `TeamAgent.auto_start_member` / `auto_start_all` → `TeamBackend.startup_member` / `startup` → `MemberStatus.UNSTARTED→STARTING` 的 CAS guard（`try_transition_member_status`）→ `_spawn_and_publish` → `_on_teammate_created` → `SpawnManager.spawn_teammate`。

拉起漏斗有三个触发点，全是"消息 / 任务要投给一个还没起来的成员"：leader `send_message` 的 `_auto_start_members`、interact dispatch（`@member` / `@all` / operator 消息）、调度器投递（F_62）。CAS 是这条链上唯一的并发闸——**不要在 spawn 工具里顺手把 agent 拉起来**，那会绕过它。模式由 `TeamAgentSpec.spawn_mode` 决定。

启动模式：
- `spawn_mode="process"` → `Runner.spawn_agent` 走子进程（跨平台，默认）。
- `spawn_mode="inprocess"` → `inprocess_spawn` 在同 event loop 启动协程（适合测试或轻量场景）。
- 顶层 `context.py` 的 `session_id` contextvars 是两种模式共享的上下文载体。

### tools/ — 团队工具集合

参见 `tools/AGENTS.md`。要点：

- `create_team_tools(role=..., teammate_mode=..., exclude_tools=..., lang=...)` 是唯一入口。
- 工具描述文本是**行为契约**，不是 feature 摘要。长文案放 `tools/locales/descs/<lang>/<tool>.md`。
- ToolCard ID 统一 `team.{name}` 前缀。

### runtime/ — TeamAgent 对象池 + 派发 + 并发门禁

`TeamRuntimePool` + `TeamRuntimeManager` + 7 路 dispatch truth table + `InteractGate` + `finalize` / `finalize_member` lifecycle hooks，是 `Runner.run_agent_team*` / `interact_agent_team` / `register_human_agent_inbound` / `pause_agent_team` / `stop_team` / `release_session` / `delete_team` 这一组 SDK facade 的实现层。`Runner.run_agent_team*` 公共入口接 `str | TeamAgentSpec`（默认）：spec 走 `manager.activate`（dispatch 决策 + pool 写入），str 是 `team_name`、复用已激活的 pool entry。跨 session 切换由 `activate` 在 dispatch 前 `stop_team` + pool.remove stale entry，再走 cold rebuild；不再保留"warm 跨 session 复用同一 TeamAgent 实例"的路径。run cycle 退出时由 Runner finally 调 `manager.finalize` / `finalize_member` 决定 pause vs stop（coordination kernel 的 `finalize_round` 不再决策）。spawn 出来的 teammate / human-agent 实例走 `Runner.run_agent_team*(member=True)` 入口、跳过 activate/dispatch、不入 pool；退出 finally 调 `finalize_member`。

详见 [`runtime/AGENTS.md`](runtime/AGENTS.md)。

### team_workspace/ — 共享工作空间

跨成员的文件共享区，支持锁 / 版本 / 冲突策略。独立于 worktree：**worktree 管代码隔离，workspace 管产物协同**。

### interaction/ — 外部交互入口 + HITT + Bridge Agent

三种交互视角（`GodViewMessage` / `OperatorMessage` / `HumanAgentMessage`）+ `UserInbox` / `HumanAgentInbox` 的实现层，所有 HITT runtime 表面（`enable_hitt` 分层开关、人类成员来源、一致性约束、运行约束）也落在这里。

Bridge Agent 把外部独立 agent（claudecode / codex / openclaw / hermes 等）以"团队成员"形式接入：`bridge_protocol.py` 定义纯文本 `BridgeProtocolAdapter`（connect / relay / close），`BridgeMemberSpec(TeamMemberSpec)` 子类 + `enable_bridge` 是 capability ceiling，dynamic spawn 走 `spawn_bridge_agent` 工具；bridge avatar 本地是完整 teammate，远程做实际工作，框架在 mailbox 路径自动转发原消息给远程并把回复组合进 avatar context（参见 `agent/coordination/handlers/message.py:_bridge_deliverable_for`）。详见 `docs/features/F_07_bridge-agent.md`。

详见 [`interaction/AGENTS.md`](interaction/AGENTS.md)。

### cli/ — 交互式 TUI / 斜杠命令

prompt_toolkit + rich 驱动的交互式 CLI。`run_team_cli(*, specs, yaml_paths)` 是公共入口，把 `Runner` 暴露的 team lifecycle facade（`run_agent_team_streaming` / `interact_agent_team` / `pause_agent_team` / `stop_agent_team` / `delete_agent_team` / `release` / `list_active_teams` / `register_human_agent_inbound` / `get_agent_team_monitor`）映射成 `/team` `/session` `/spec` 子命令。普通文本透传 `Runner.interact_agent_team`，由 runtime 的 `parse_interact_str` 一处解析 `# / $ / @member` 前缀；CLI 不做二次解析。详见 [`cli/AGENTS.md`](cli/AGENTS.md)。

### external/ · skill/ · mcp/ — 外部 agent 直连接入

让团队进程**之外**的 agent（第三方 CLI claudecode / codex / openclaw / hermes，
或独立运行的 agent 服务进程）以一等成员身份直接调用协同工具——直连共享 DB + zmq
messager，不经本地 avatar 代理。与 F_07 bridge（本地完整 DeepAgent + relay 纯文本给
无工具远程执行者）是**正交互补**的两条接入路径：bridge 管"被动文本执行者"，本路径管
"自主一等成员"。

- `external/descriptor.py`：`TeamJoinDescriptor`（session/team/member + role + language +
  dispatch_mode + teammate_mode + db_config + transport_config）+ `TEAM_JOIN_ENV` 环境变量（`OPENJIUWEN_TEAM_JOIN`）。
  团队拉起外部 agent 时注入，或运维下发给独立服务。
**两个场景，按 `descriptor.scope` 首次连接时分化（F_26）**——`scope` 与 team `role` 正交：

- **member**（cli-agent 三方团队成员）：`ExternalTeamClient.connect` 建最小 `TeamBackend` +
  `create_team_tools(role="teammate")`，对外暴露**真实** teammate `TeamTool`
  （`view_task` / `claim_task[claimed|completed]` / `send_message`，结果即 `map_result()`
  文本，与进程内成员逐字一致）+ 外部专有 `read_inbox`（原生 push、外部 pull）。`complete_task`
  折进 `claim_task(status=completed)`、list/get/claimable 折进 `view_task`。MCP instructions
  空（系统提示词已在 spawn 时直接注入 CLI，见 [[F_25]]）。
- **operator**（团队外非成员控制接口，默认 scope）：`ExternalTeamClient` 的 per-op 方法
  （send/broadcast/list/get/claimable/claim/complete/update/list_members + `create_task`）+
  `fetch_inbox`/`watch`，全团队控制面；MCP instructions = 控制工作流。

公共件：`client.tools`（member 真实工具字典）、`client.read_inbox()`（`<team-inbound>`/`<team-event>` XML）、
`client.bind_session_context()`（每调用重绑 session/language contextvar）。
- `external/format.py`：纯函数把消息 / 任务板渲染成与进程内 dispatcher 一致的
  `<team-inbound>`/`<team-event>` XML（复用 `inbound_render` 结构 + `i18n.t` note 文案）；
  `read_inbox` 用之。见 F_51。
- `skill/cli.py`：非交互脚本式 CLI（`team-member` 入口），两段解析后按 scope 分化子命令
  （member 驱动真实工具 / operator 控制面）。两份 skill 文档：`skill/SKILL_member.md` /
  `skill/SKILL_operator.md`。与 `cli/` 的交互式 TUI 不同——后者给人用，本 CLI 给外部 agent
  脚本化调用。
- `mcp/server.py`：低层 `mcp.server.lowlevel.Server`（`openjiuwen-team-mcp` 入口；不用 FastMCP，
  因 FastMCP 从函数签名推断 schema，无法暴露真实工具的 `card.input_params`）。`list_tools`/
  `call_tool` 按 `client.scope` 在请求期分化；member 工具的 `inputSchema` 即 `card.input_params`、
  结果即 `str(tool.invoke())`。这是仓库首个 MCP server（其余 MCP 代码都是 client）。

**team 拉起外部 CLI 成员（F_22）**：CLI 启动知识静态预置在
`TeamAgentSpec.external_cli_agents`（`ExternalCliAgentSpec` 列表：`cli_agent` 种类标识 +
`command`/`cwd`/`inject_mcp`/`mcp_server_command`/`env`/`ssh_transport`），非空集即外部 CLI 成员的能力上限。
leader 用 `spawn_external_cli(cli_agent=<name>)` 按名引用，不在 spawn
调用里传启动细节。当前内置 adapter：claude / codex / gemini / openclaw / hermes / generic。
spawn 路径（`external_cli_spawn` → `build_cli_runtime`）按 adapter 注入团队 MCP server——
有 launch flag 的 claude `--mcp-config <inline-json>`、codex `-c mcp_servers...`；无 flag 的
gemini / hermes 由 spawn 路径跑一次 `<cli> mcp add ...` 带外注册（`mcp_register_command`），
openclaw 无已知注册方式则 `mcp_inject=none` + 大声告警。MCP server 是 CLI 子进程，继承
`OPENJIUWEN_TEAM_JOIN` env，自动绑定成员身份。`ssh_transport` 配置后 CLI 进程在远程 SSH 端点
启动，`command` / `cwd` / `mcp_server_command` 均按远程主机解释；DB / messager 可达性由部署保证。
外部 CLI 成员的**系统提示词**复用 team-rail 的
`build_team_static_sections`（role/workflow/lifecycle/private-prompt，排除其它 DeepAgent rail），经
claude `--append-system-prompt` / codex `-c developer_instructions` / 其余 prepend 下发；其
stdout 叙述经 `outputs()` surface 为 `TeamOutputSchema` chunk、与进程内成员同路 fan-out。
详见 [[F_22]] 与 [[F_25_external-cli-hardening-and-gemini]]。

设计文档见 `docs/features/F_21_external-agent-access.md`（接入面 + spawn 接线）与
`docs/features/F_22_external-cli-spawn-member-and-mcp-injection.md`（external_cli spawn 工具 +
静态 spec 配置 + MCP 自动注入）。

### worktree — Git worktree 隔离

通用实现已下沉到 `openjiuwen.harness.tools.worktree`，由 deepagent 与 team 共用。team 侧只保留三件事：

- 通过 `TeamAgentSpec.worktree`（`WorktreeConfig`）描述配置；team 下 worktree 隔离只由 leader / 宿主在 `SpawnManager.build_context_from_db` 里按 `TeamMember.options.worktree.isolation == "worktree"` 调用 `create_owner_worktree(slug)` 创建，不向 leader 或 teammate 暴露 `enter_worktree` / `exit_worktree` 作为手动兜底。
- **workspace 视图软链由 team 侧自管**：`create_worktree_manager` 给 `WorktreeManager` 注入一个翻译适配器，把 `WorktreeCreatedEvent` / `WorktreeRemovedEvent` 路由到 `TeamWorkspaceManager.mount_worktree` / `unmount_worktree`，在共享 team workspace 下维护 `.worktree/{slug}` 软链。这一层是"本 team 当前活跃 worktree 一览"的导航视图，**单 agent 不订阅事件，软链物理上不存在**——`WorktreeManager` 本身不知道软链。team 侧同时把这些事件桥到 `TeamEvent.WORKTREE_*` 总线。
- Team teammate 隔离 worktree 命名固定为 `agent-{team_name}-{member_name}-{hash8}`。`TeamMember.options.worktree` 持久化 `isolation/path`；旧库迁移会把 `model_ref_json` 回填到 `options.model_ref` 后删除旧列，不匹配 `isolation/worktree_path` 物理列。`worktree_name/worktree_branch/head_commit` 留在 leader 宿主内存。`cleanup_teammate` 停掉成员后检查变更：干净则 `git worktree remove` 并清空路径字段；有变更、宿主 metadata 丢失或无法确认状态则保留 `worktree_path` 给 leader 合并与解冲突。
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
   - 运行时 hard-coded 字符串（dispatcher 通知、default desc 等）走 `agent_teams/i18n.py` 的 `t(key)`；
   - Prompt / 工具描述的长文本走各自模块的 `locales/` 或 `prompts/`（按 `lang` 参数入参传递）。  
   **新增字符串前先判断归属**：运行时提示进 `i18n.py`，模板正文进 `prompts/` 或 `tools/locales/descs/`。  
   调度器交接消息（F_63）属后者且**只有模板一份**：文案在 `prompts/<lang>/scheduler_*.md`，
   投递时渲染；`i18n.py` 里**不再**留成员侧短句变体——同一条消息两处文案必然漂移。
   `scheduler.*` 键只剩 leader 直投的摘要/升级（不经邮箱，无模板通道）。

5. **paths.py 是文件系统布局的单一真相源**  
   `get_agent_teams_home()`、`team_home(team_name)`、`independent_member_workspace(member_name)`。创建和清理都走这里，不要散落 `Path("…")` 硬编码。

6. **Spec → build() → Runtime 是单向流**  
   Spec 不保留运行时引用；build() 产出运行时对象；运行时对象不回写 Spec。想支持热更新？通过 session + resume 路径，而不是反向污染 Spec。

## 测试

- 单测路径镜像源码：`openjiuwen/agent_teams/tools/team_tools.py` → `tests/unit_tests/agent_teams/tools/test_team_tools.py`
- 内存后端：`MemoryDatabaseConfig` + `InProcessMessager` 适合单测，不依赖 sqlite / zmq。
- 多成员场景优先用 `spawn_mode="inprocess"`，避免子进程拉起开销。
- `pytest` 纯函数风格；禁止 `print`，改用 `team_logger` 或 test_logger。

## 代码风格

- **类型注解**：使用 PEP 585 内置泛型（`list[X]`、`dict[K, V]`、`set[X]`）和 PEP 604 联合类型（`X | Y`、`X | None`）；禁止从 `typing` 导入 `List` / `Dict` / `Set` / `Tuple` / `Optional` / `Union` 等已被内置语法替代的别名。`Callable`、`Awaitable`、`AsyncIterator`、`TYPE_CHECKING`、`Any` 等无内置等价物的仍从 `typing` 导入。

## 设计文档归档与双向同步（仅本模块强制）

本模块的设计文档**全部**落在 `docs/` 下，命名与结构规约见 [`docs/AGENTS.md`](docs/AGENTS.md)。

把 **`openjiuwen/agent_teams/` 下的代码 + 本目录 `docs/specs/` + `docs/features/`
+ 各级 `<subdir>/AGENTS.md`** 当作**一个一致性单元**来维护——`agent_teams` 子系统
对契约一致性的要求高于仓库其它模块（多角色 + 多进程 + 持久化状态 + 公共 SDK 表面），
所以本节的强制约束**只限于** `openjiuwen/agent_teams/` 子树，不上推到仓库其它模块。

**前置铁律——文档先是设计的输入，才是提交的产出。** 动手改任何代码设计**之前**，先
`grep` + 读相关的 `docs/specs/S_NN_*`（现有契约 / 不变量 / 边界）与 `docs/features/F_NN_*`
（为什么长这样、当初拒绝了哪些方案），拿它们校准方案——spec 说"系统是什么样"、feature 说
"为什么这么改"，先读才不会撞坏既有契约、也不会重蹈已被拒绝的方案。方案定了**当场**同步改掉
对应 S / F，让文档和方案一起成形。**先码后档、拖到提交前才补文档是把流程做反了**：那样文档
不再参与设计，退化成走过场的归档义务。下面三条是"提交时必查"的产出侧同步；本条是它更靠前的
输入侧——两半合起来才叫双向同步，缺了前半，第 3 条的"读到不一致当场改"永远慢半拍。

三条强制约束，提交时必查：

1. **每次特性更新必须归档 feature 文档，且特性代码、测试代码、文档拆成三个连续提交**：在 `docs/features/`
   下新增一份 `F_NN_<slug>.md`，记录决策、拒绝的方案、验证基线、已知遗留。commit message 只写
   what，feature 文档负责写 why / why-not。落地顺序**固定为三个紧邻的提交**——提交 1 落特性代码
   （`feat(swarm): ...` 或对应 type），提交 2 落本次新增/改动的单测（`test(swarm): ...`），提交 3
   落本次涉及的全部文档：`docs/features/F_NN_*.md` 新增 + 下面 #2 要求的 `docs/specs/S_NN_*.md`
   修订（`docs(swarm): ...`）。特性代码、测试、文档不再混进同一次 commit——既不让大段文档 diff 淹没
   代码评审，也让测试改动独立可审，还避免文档归档拖延导致设计上下文随时间漂移。
2. **所有模块设计规约变动必须更新 specs 文档**：模块契约、跨子模块的公共协议、不变量、
   公共 API 形态发生变化时，同步修订对应 `docs/specs/S_NN_<slug>.md`；新规约 = 新 spec 文件。
   规约变了但 specs 没改，下次读 spec 的人就被误导——这是设计债，不是文档懒。
3. **双向同步：读到与代码不一致的描述必须当场修文档**。在本模块任何一份
   `AGENTS.md` / `docs/specs/S_*` / `docs/features/F_*` 里读到的接口名、枚举值、
   truth table 行数、文件路径、不变量等只要与当前代码不符，**不要**把过时表述当作新约束去执行、
   也不要原样转述给用户；先 `grep` 代码、以代码为准刷新文档，在同一次改动里落地。
   `AGENTS.md` 里每条点名了"X 个分支 / Y 路 dispatch / Z 方法"的句子都是契约的一部分。
   **更新目标是 `AGENTS.md`，不是 `CLAUDE.md`**——`CLAUDE.md` 现在只是 `@AGENTS.md` 的单行壳，
   编辑它没有任何意义；所有内容变更一律落到对应目录的 `AGENTS.md`。

收尾规范：

- spec 头部"最近一次修订日期"字段在每次修订该 spec 时填当天日期（`YYYY-MM-DD`）；feature 文档用
  头部"日期"字段记录归档当天，不设独立修订字段。**不要在元信息里写 commit hash**——避免"提交后
  回填"的来回反复。
- 子模块自身的本地约定继续放各 `<subdir>/AGENTS.md`；跨子模块的设计规约一律落到 `docs/specs/`，
  不要塞进单一子目录的 AGENTS.md。
- **`CLAUDE.md` 是只读壳，不要编辑它**：本模块每个子目录的 `CLAUDE.md` 仅含 `@AGENTS.md` 一行，
  编辑 `CLAUDE.md` 的修改不会被保留在任何有效文档里。需要更新文档时，直接编辑 `AGENTS.md`。
- 拿不准某次改动算 feature-grade（要 `F_NN_*.md`）还是普通修复（不必归档）时，先问用户。
  歧义情况默认**归档**——多一份 markdown 的成本远低于丢失设计上下文。

## 提交约定

本模块改动的 commit message scope 固定用 `swarm`（如 `feat(swarm): ...`）。

footer 用 `Refs: #<issue>` 格式关联 issue。issue 号若无法从当前上下文明确，必须先询问用户，不要臆造或留空。

涉及 `docs/features/F_*` / `docs/specs/S_*` 文档更新的特性改动，**特性代码、测试代码、文档拆成三个连续提交**（提交 1 特性代码 `feat(swarm)`，提交 2 单测 `test(swarm)`，提交 3 文档 `docs(swarm)`），细则见上文「设计文档归档与双向同步」约束 #1。
