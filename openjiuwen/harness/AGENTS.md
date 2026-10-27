---
description: 给修改 openjiuwen/harness（及被 spec 点名的 openjiuwen/core 文件）的 AI 助手与工程师：本目录没有自己的 spec 体系，机制契约登记在 jiuwenswarm 仓的 S_01~S_21；本文件是"改哪些文件前必读哪份 spec"的跨仓回链索引
---

# openjiuwen/harness — 跨仓契约回链索引

## 本文件是什么

- 本仓（agent-core）的仓库级规范由根 [AGENTS.md](../../AGENTS.md) 与 `.claude/rules/` 管（Card/Config 分离、测试、安全、日志等）；**本文件管的是跨仓机制契约**。
- 同一机制常"本体在 openjiuwen、装配与宿主行为在 jiuwenswarm"：契约文档统一登记在 jiuwenswarm 仓
  `../jiuwenswarm/jiuwenswarm/agents/harness/docs/specs/`（S_01~S_21，编号跨域连续）。
- 那些 spec 的"关联模块（agent-core 仓）"行能从 swarm 侧找到这里的文件；**本表是反向入口**——core 侧维护者改下表文件前，先读对应 spec 的"不变量"与"范围 / 边界"两节。
- 本表只做索引，不复制 spec 内容；契约语义一律以 spec 文本为准。

## 修改纪律

1. 改下表文件前先读对应 spec——尤其是不变量（N 条目）与边界声明。
2. 契约级变更（不变量、公开接口、可观察行为、payload / 存储格式）须同步更新对侧 spec 并登记其修订日期；纯实现重构且可观察行为不变时不动 spec，拿不准就读。
3. 下表文件移动 / 重命名时，同步修改各 spec 的"关联模块（agent-core 仓）"行与本表。
4. spec 与本表中的行号是撰写时快照，允许漂移；行号冲突时以 spec 的语义条款为准。
5. jiuwenswarm 侧新增 spec（从 S_22 起）若机制主体落在 `openjiuwen/harness/`，须回填本表对应域。

## 索引表

### 任务规划域（S_01~S_06）

| core 侧文件（仓内路径） | 受 spec 约束的要点 | spec |
|---|---|---|
| `openjiuwen/harness/tools/todo.py` | todo 主链四工具本体；profile 子类化基类；`TodoModifyTool` 是 monkeypatch 换类目标 | S_01 / S_05 / S_06 / S_09 |
| `openjiuwen/harness/schema/task.py` | `TaskPlan` / `TodoItem` / `TodoStatus` schema；`from_dict` 往返兼容被 S_06 依赖 | S_01 / S_05 / S_06 / S_09 |
| `openjiuwen/harness/workspace/workspace.py` | workspace 布局中的 `todo` 目录 | S_01 |
| `openjiuwen/harness/rails/task_planning_rail.py` | `TaskPlanningRail` 父类（guard 重置、todo_create 阻断等基线行为） | S_02 / S_09 |
| `openjiuwen/harness/rails/agent_mode_rail.py` | `AgentModeRail` 父类：`DEFAULT_PLAN_MODE_ALLOWED_TOOLS` 基线、switch_mode 解析 | S_04 |
| `openjiuwen/harness/tools/agent_mode_tools.py` | `_EXIT_PLAN_EMPTY_MSG` / `_EXIT_PLAN_WITH_CONTENT_PREFIX` 模板常量（S_04 N11 推导源）；`_register_task_tool` 基线行为 | S_04 |

### 可靠性域（S_07~S_10）

| core 侧文件 | 受 spec 约束的要点 | spec |
|---|---|---|
| `openjiuwen/harness/rails/llm_retry_rail.py` | `LLMRetryRail` 父类：repeat 检测参数、`backoff_delay`、`_is_repeat_exception` / `_is_stream_timeout_exception` / `_is_transient_invoke_exception` | S_07 |
| `openjiuwen/harness/rails/base.py` | `DeepAgentRail` 基类（多份 spec 的父类契约） | S_02 / S_07 / S_08 |
| `openjiuwen/core/runner/`（`Runner.resource_mgr`） | 进程级工具注册表（S_09 并发安全前提）；`callback/framework.py` / `callback/chain.py` 钩子链按 priority 降序执行 | S_09 / S_10 |
| `openjiuwen/core/single_agent/rail/base` | `AgentRail` / `AgentCallbackContext` / `ToolCallInputs`；`ctx.extra["_skip_tool"]` 跳过执行约定 | S_08 / S_10 |
| `openjiuwen/core/foundation/llm` | `ToolMessage` 消息写入载体 | S_10 |
| `openjiuwen/core/sys_operation/cwd` | `get_cwd` 相对路径基准 | S_10 |
| `openjiuwen/harness/rails/`（`SysOperationRail`） | S_09 并发注册守卫的基类面 | S_09 |

### subagent 域（S_11~S_14）

| core 侧文件 | 受 spec 约束的要点 | spec |
|---|---|---|
| `openjiuwen/harness/deep_agent.py` | `create_subagent` 唯一汇聚点、`_find_subagent_spec`、`_bind_inherited_artifact_root`、释放三入口 | S_11 / S_12 |
| `openjiuwen/harness/schema/config.py` | `SubAgentConfig` 声明契约 | S_11 |
| `openjiuwen/harness/subagents/` | 七个预置工厂；`verification_agent` 为唯一 sticky（VERDICT 输出契约） | S_11 |
| `openjiuwen/harness/tools/subagent/type_aliases.py` | `canonicalize_subagent_type` 类型归一化 | S_11 |
| `openjiuwen/harness/tools/subagent/task_tool.py` | `_build_sub_session_id`（sticky 确定性 ID）、`_summarize_task_description`、卡片豁免、`_invoke_subagent`（被宿主 patch 的窄缝） | S_11 / S_14 |
| `openjiuwen/harness/subagent_runtime/` | `SubagentControl` per-parent-session、状态机、容量与驱逐、超时体系（S_12 机制主体） | S_12 |
| `openjiuwen/harness/tools/subagent/_control_registry.py` | 属性挂载协议（compat 类替换的挂载目标） | S_12 |
| `openjiuwen/harness/tools/subagent/subagent_tools.py` | runtime 六工具 | S_12 / S_14 |
| `openjiuwen/harness/rails/subagent/subagent_rail.py` | `SubagentRail` 三模式互斥（priority=95）、`_REFRESHABLE_TOOL_NAMES`、`uninit` | S_14 |
| `openjiuwen/harness/tools/subagent/session_tools.py` | `SessionsSpawnTool` / `SessionsCancelTool` / `SessionToolkit` | S_14 |
| `openjiuwen/harness/task_loop/session_spawn_executor.py` | `SessionSpawnExecutor`（async 分发通道） | S_14 |
| `openjiuwen/harness/tools/subagent/thinking_hook.py` | 进程级 hook 注册表 | S_14 |
| `openjiuwen/harness/security/skill_authorization/subagent_approval_registry.py` | `SubagentApprovalRegistry` 单例 / Future 审批 / 容量五重约束（S_13 N6） | S_13 |
| `openjiuwen/harness/security/skill_authorization/` | Grant store / generation / `is_skill_authorization_enabled` | S_13 |
| `openjiuwen/harness/rails/skills/skill_authorization_rail.py` | 模板钩子 / `MAIN_AGENT_SCOPE_ID` / `_resolve_session_id` | S_13 |
| `openjiuwen/harness/rails/skills/skill_lifecycle_events.py` | 指纹清理挂点 | S_13 |
| `openjiuwen/harness/security/core.py` | `PermissionEngine` 判定序（S_16）；S_13 N10 手工合成引擎的模板 | S_13 / S_16 |

### 中断交互域（S_15~S_19）

| core 侧文件 | 受 spec 约束的要点 | spec |
|---|---|---|
| `openjiuwen/core/single_agent/interrupt/` | `state.py` 五状态键、`response.py` 两 Request、`exception.py` `ToolInterruptException`、`handler.py` `ToolInterruptHandler`、`resume_guard.py` 恢复分类四问——S_15 N1~N14 契约主体 | S_15 / S_18 / S_19 / S_21 |
| `openjiuwen/harness/rails/interrupt/interrupt_base.py` | `BaseInterruptRail` 三态决策封闭 | S_15 |
| `openjiuwen/harness/rails/interrupt/confirm_rail.py` | `ConfirmInterruptRail` 基类（权限 rail 与子代理审批 rail 的父类） | S_13 / S_15 / S_16 |
| `openjiuwen/harness/rails/interrupt/ask_user_rail.py` | `AskUserRail`（空 answers 必收口不 re-pend，S_15 N11） | S_15 |
| `openjiuwen/harness/rails/security/tool_security_rail.py` | `PermissionInterruptRail`（拦截一切工具的判定序 / auto_confirm / batch allow 双 key / 授权三级记忆 / persist 回滚） | S_16 |
| `openjiuwen/harness/security/host.py` | `ToolPermissionHost` 回调契约（宿主实现归 S_17） | S_16 / S_17 |
| `openjiuwen/harness/security/models.py` | `PermissionConfirmResponse` / `PermissionLevel` / `PermissionResult` | S_16 / S_17 |
| `openjiuwen/harness/security/patterns.py` | 授权规则 merge / 写盘（persist 三真与写盘回滚） | S_16 |
| `openjiuwen/harness/security/shell_ast.py` | `parse_shell_for_permission` | S_16 |
| `openjiuwen/harness/security/skill_authorization/` | `SKILL_APPROVAL_CARD_EXTENSION_KEY`（审批卡透传） | S_17 |

### 事件流域（S_20~S_21）

| core 侧文件 | 受 spec 约束的要点 | spec |
|---|---|---|
| `openjiuwen/core/session/stream/` | `base.py` 三 schema 与模式枚举、`emitter.py` `StreamEmitter` + `AsyncStreamQueue`、`writer.py` StreamWriter 三型、`manager.py` `StreamWriterManager`、`chunk_logging.py` | S_20 |
| `openjiuwen/core/sys_operation/shell_process_registry.py` | `set_shell_session_id` / `kill_shell_processes_for_session_tree` / `reset_shell_session_id`（pause 击杀进程树） | S_21 |
| `openjiuwen/core/session/stream` 的 `OutputSchema` | 发射单一入口（S_07 亦引用） | S_07 / S_20 |

注：表中 `openjiuwen/core/...` 条目在本目录之外，因契约登记在同一批 spec 内，一并列出。

## 无 spec 覆盖登记（本目录）

以下文件出现在 jiuwenswarm 侧域职责表中、但当前**没有任何 spec 登记其契约**（S_01~S_21 均未点名）：

- `openjiuwen/harness/rails/task_completion_rail.py`、`openjiuwen/harness/tools/goal.py`（任务完成判定 / goal 评估本体）
- `openjiuwen/harness/tools/ask_user.py`（ask_user 工具本体——S_15 只覆盖 `ask_user_rail.py`）
- `openjiuwen/harness/rails/llm_stability_rail.py`、`openjiuwen/harness/rails/agent_ras_rail.py` 与 `openjiuwen/harness/agent_ras/` 框架（S_08 仅登记与 circuit breaker 的互斥关系，无本体契约）
- `openjiuwen/harness/rails/subagent/` 下 `verification_rail.py` / `verification_contract_rail.py` / `session_rail.py`（S_14 只覆盖 `subagent_rail.py`；S_11 点名 verification_agent 的 sticky 语义）

改动这些文件不受 spec 门控；若其行为被 jiuwenswarm 侧依赖（装配点见 swarm 侧各 spec），新增 spec 后回填本表。

## 相关入口

- swarm 侧主索引与七子树地图：`../jiuwenswarm/jiuwenswarm/agents/harness/AGENTS.md`
- specs 全目录：`../jiuwenswarm/jiuwenswarm/agents/harness/docs/specs/`（S_01~S_21）
- 本仓仓库级规范：[../../AGENTS.md](../../AGENTS.md) 与 `.claude/rules/`
