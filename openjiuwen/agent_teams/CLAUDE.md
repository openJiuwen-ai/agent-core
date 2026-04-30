# Agent Teams

多智能体团队协作子系统。一个 Leader + 若干 Teammate，通过消息总线与任务数据库协同完成复杂任务。

本文件是本模块的入口索引。深入细节时跳转到：
- `tools/CLAUDE.md` — 团队工具设计与描述契约
- `agent/prompts/CLAUDE.md` — Prompt 模板与语言切换

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
├── factory.py           # create_agent_team / resume_persistent_team 便利函数
├── i18n.py              # 运行时中/英文字符串（仅装运行时 hard-coded 串）
├── paths.py             # 文件系统布局单一真相源
├── schema/              # 全部数据模型（Spec / Context / Event / Status / Task）
├── agent/               # 核心运行时（TeamAgent 本体 + 装配链）
├── interaction/         # 外部交互入口（UserInbox / HumanAgentInbox / @ 路由）
├── tools/               # 团队工具（Leader / Teammate / Human Agent 可调用的原子操作）
├── messager/            # 消息传输层（inprocess / pyzmq）
├── spawn/               # 成员启动（process / inprocess）
├── monitor/             # 团队运行态监控
├── team_workspace/      # 团队共享工作空间（跨成员的文件/锁/版本）
└── worktree/            # Git worktree 隔离（成员级代码空间）
```

### agent/ — 运行时主骨架

| 文件 | 职责 |
|---|---|
| `team_agent.py` | `TeamAgent`（单一实现同时承担 Leader / Teammate 角色，内部组合一个 `DeepAgent`） |
| `coordinator.py` | `CoordinatorLoop` 事件驱动唤醒循环，**不做决策**，只负责 wake-up 和轮询 |
| `dispatcher.py` | 成员事件 → 运行时通知文本的映射 |
| `policy.py` | `build_system_prompt` 老装配路径，消费 `prompts/system_prompt.md` |
| `team_rail.py` | Rail 装配路径（主力），把 prompt 模板拆成多个 `PromptSection` 合并 |
| `rails.py` | 其它团队相关 Rail |
| `member.py` | `TeamMember` 成员状态管理 |
| `model_allocator.py` | spawn 时为成员分配 model 配置的回调机制 |
| `prompts/` | 语言相关模板（cn/en）+ 语言无关装配壳 `system_prompt.md` |

**两条装配路径（`policy` vs `team_rail`）读的是同一批 `.md`**。改正文自动同时生效；结构性变更（占位符、section 拆分）要明确落到哪条路径。详见 `agent/prompts/CLAUDE.md`。

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
- `context.py` 的 `session_id` contextvars 是两种模式共享的上下文载体。

### tools/ — 团队工具集合

参见 `tools/CLAUDE.md`。要点：

- `create_team_tools(role=..., teammate_mode=..., exclude_tools=..., lang=...)` 是唯一入口。
- 工具描述文本是**行为契约**，不是 feature 摘要。长文案放 `tools/locales/descs/<lang>/<tool>.md`。
- ToolCard ID 统一 `team.{name}` 前缀。

### team_workspace/ — 共享工作空间

跨成员的文件共享区，支持锁 / 版本 / 冲突策略。独立于 worktree：**worktree 管代码隔离，workspace 管产物协同**。

### interaction/ — 外部交互入口

| 文件 | 作用 |
|---|---|
| `router.py` | `parse_mention(raw) -> (target, body) \| None` 纯函数；`is_reserved_name(name)` 校验保留名 |
| `user_inbox.py` | `UserInbox`：user 侧显式 API。`broadcast` / `direct` / `deliver_to_leader` |
| `human_agent_inbox.py` | `HumanAgentInbox`：human_agent 对外发声，仅在 HITT 启用时可用；非 HITT 调用抛 `HumanAgentNotEnabledError` |

旧的 `@xxx body` 解析从 `agent/dispatcher.py` 移到这里——dispatcher 只保留调用点。新增的 `TeamAgent.broadcast()` 和 `TeamAgent.human_agent_say()` 都走这一层。

### HITT（Human in the Team）

开启方式：

- **静态**：`TeamAgentSpec.enable_hitt=True`，build() 时自动注册 human_agent 成员。
- **动态**：leader 通过 `build_team(enable_hitt=true)` 工具在建团时启用。

运行约束（代码层 + Prompt 层双重保证）：

1. `human_agent` 是保留成员名，常量在 `constants.RESERVED_MEMBER_NAMES`。预定义成员不允许撞保留名。
2. human_agent 直接 READY，不进入 UNSTARTED/BUSY/SHUTDOWN 流程；唯一工具是 `send_message`。
3. 一旦 `task.assignee == "human_agent"` 且状态 CLAIMED，`UpdateTaskTool` 拒绝 reassign 和 cancel；批量 cancel 链路也跳过。
4. 发送给 human_agent 的点对点消息 `is_read=True`；广播后 human_agent 的 `read_at` 立即跟进。
5. TeamRail 注入 `team_hitt` section（priority=12），按 role 分别给 leader/teammate/human_agent 下达角色特定的行为约束。

### worktree/ — Git worktree 隔离

可选启用（`TeamAgentSpec.worktree`），为成员挂一个独立 git worktree。
- `manager.py`：生命周期管理（创建/清理）
- `backend.py`：`WorktreeBackend` 抽象 + `GitBackend` 默认实现；可用 `register_worktree_backend` 注册自定义后端（如远程）
- `rails.py`：自动进入 worktree / diff summary 的 Rail

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
