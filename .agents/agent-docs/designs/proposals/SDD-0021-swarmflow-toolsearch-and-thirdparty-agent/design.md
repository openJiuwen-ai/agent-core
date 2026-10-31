---
id: SDD-0021
title: Swarmflow 渐进式工具暴露开关与三方 Agent 接入（tool_search + agent_type）
slug: swarmflow-toolsearch-and-thirdparty-agent
status: draft
stage: proposals
reviewers: []
created: 2026-09-18
updated: 2026-09-29
version: 6
domain: core
modules: [agent_teams/workflow]
tags: [swarmflow, tool-search, progressive-tool, third-party-agent, claude, codex, cache, fork, observability]
related: [SDD-0002, SDD-0005, SDD-0007, SDD-0014, SDD-0016, SDD-0020]
author: '@chenchunzhou'
author_note: 由历史 owner 字段原值迁移
type: D
files: []
author_email: null
---

# Swarmflow 渐进式工具暴露开关与三方 Agent 接入（tool_search + agent_type）

## 1. 背景与问题 (Why)

Swarmflow worker（含 `agent()` 一次性调用与 `agent_session()` 会话）的 avatar
今天把 worker 基 spec 的**全部工具一次性摊给模型**。当团队配置的工具很多时带来两个
问题：

1. **上下文税**：每个 worker 的每次模型调用都携带完整工具清单，工具越多系统提示
   越长，质量与成本同时劣化。单 Agent 模式已有解法——渐进式工具暴露
   （progressive tool exposure，`tool_search` 元工具 + DEFERRED 工具），但团队侧
   完全没有接入：`agents/harness/team/` 下对全局开关
   `progressive_tool_enabled` 零引用，leader / teammate / swarmflow worker
   三类成员都没有 tool_search。
2. **能力封闭**：swarmflow 的 `agent()` / `agent_session()` 只能派 native
   worker。团队对话场景里三方 CLI 成员（Claude Code / Codex）已经可用
   （`external_cli_agents` 声明 + `SpawnExternalCliTool` 拉起），但工作流脚本
   无法把"派活给 Codex"写进编排逻辑——同一团队两套用人渠道，编排侧只能用一半。

本设计一个 SDD 覆盖两个子特性，因为它们共享同一批改动面（worker 基 spec 装配
管道、缓存签名、事件链路）且互不依赖、可独立灰度：

- **Piece A（tool_search 开关）**：让 swarmflow worker avatar 复用 harness
  原生的渐进式工具暴露，以独立配置键 opt-in。
- **Piece B（三方 Agent）**：`agent()` / `agent_session()` 新增
  `options={"agent_type": ...}`，把外部 CLI Agent 引入 swarmflow 编排，复用既有
  三方成员底座（SDK 后端 + checkpoint 续会话），不引入新的 provider 体系。

## 2. 目标与非目标

**目标**

- swarmflow worker avatar 可按团队配置开启渐进式工具暴露（A1-A8）。
- 脚本通过 `options={"agent_type": "claude" | "codex"}` 派三方 Agent 干活，
  一次性调用与会话两种形态都支持（B1-B11）。
- 三方调用的缓存、续跑、预算、事件语义与 native 调用**同构**——用户除了卡片
  头像外感知不到差别。
- Web 前端能区分并展示三方节点（B12）。
- worker 的工具活动（tool_call / tool_result）结构化上报，树状图节点与
  成员调用列表两面可见（A9，v6 敲定）。
- Web 的成员列表、会话缩略图、任务面板按工作流 run 分组展示（A10，v6 敲定）。

**非目标**

- 不改单 Agent 模式的 `progressive_tool_enabled` 行为（团队键独立，见 A2）。
- 不为三方 Agent 做团队协作工具注入（B7：工具面 = CLI 原生工具零注入）。
- 不做三方的 `after` / compact fork 模式（B4f：永久不可用，报错引导）。
- 不做节点级实时流式输出（B12：native 也无此能力，轮末更新即可；A9 的
  工具活动是离散事件，非流式正文）。
- 不做 provider 侧预算透传（B6：SDK 预算机制与引擎双层账本不同构）。
- 不处理 WAL 膨胀（C3：已由 per-run WAL + 永不删除策略解决，见 §6 备注）。
- A9 不做历史活动回放：缓存命中 / resume 重放不重发活动（live-only，与
  F_112 同语义）。

## 3. 术语与上下文

- **渐进式工具暴露**：卡片注册期把工具冻结为 `ToolExposure.DIRECT`（对模型
  常驻可见）或 `DEFERRED`（不在提示中展开，模型经 `tool_search` 元工具按需
  检索、命中后授权进会话）。单 Agent 模式由全局键 `progressive_tool_enabled`
  控制，默认开。
- **swarmflow worker / avatar**：`run_swarmflow()` 组装的 `TeamWorkerBackend`
  派出的成员。一次性 `agent()` 走 `run_once` worker（成员名
  `{run前缀}-{slug}-{调用键哈希}`）；`agent_session()` 走 avatar session
  manager（成员名 `wf-sess-{slug}-{n}`，本设计改为掺 run 前缀，见 §4.6）。
- **三方 Agent 三运行时族**：SDK 后端（claude/codex，`ClaudeCodeHarness` /
  `CodexHarness` + `ExternalHarnessMemberRuntime`）、流式适配器
  （`ExternalCliRuntime`）、一次性适配器（`ReinvokeCliRuntime`）。本设计 v1
  只承诺 SDK 后端族，机制不封死适配器族。
- **双层账本**：session 级（超限 → seal 终态）与 workflow 级（超限 → pause
  可续跑）两个 `BudgetLedger`，入口闸在 `agent()` / `send()` 发射前。
- **`REQUIRE_RESUME`**：外部 harness 的恢复策略，两级候选——checkpoint 有
  会话指针则强制续接；冷续跑（会话已有历史轮）指针缺失即抛错，不静默开新
  会话；首轮发射（无历史轮）以确定性派生 id 首启（此时无指针是正常态）。

## 4. 方案设计 (What / How)

### 4.1 总体架构

```
┌────────────────────── 配置层（jiuwenswarm config.yaml）─────────────────────┐
│ modes.team.jiuwen_team.swarmflow_progressive_tool: false   ★A2（独立键）    │
│ modes.team.jiuwen_team.external_cli_agents: [...]          既有（B1 白名单）│
└─────────────────────────────────────────────────────────────────────────────┘
        │ team_helpers 读键 → swarmflow_config          │ TeamAgentSpec 装载
        ▼                                                 ▼
┌────────────────────── spec 装配层（jiuwenswarm）────────────────────────────┐
│ team_manager: spec.progressive_tool = cfg[...]           ★A1 落 spec        │
│ agent_configurator: leader+enable_swarmflow 分支写                                     │
│   swarmflow_worker_base_spec.progressive_tool = ProgressiveToolSpec(enabled)│
│ derive_member_spec 派生自动继承（fork 子会话同）                                        │
└─────────────────────────────────────────────────────────────────────────────┘
        ▼ worker 基 spec（含 progressive_tool / external 装配参数）
┌────────────────────── 引擎层（agent-core engine/primitives.py）─────────────┐
│ agent(prompt, options={"agent_type": "codex", "model": ..., "timeout": ...})│
│   → _build_opts        键白名单（agent_type 已在 _ENGINE_OPTIONS）           │
│   → call_signature     ★B8：agent_type 非空并入 identity；空 = 字节兼容      │
│   → journal.get_cached 命中：回放，不碰 backend（native/三方同构）★B9       │
│   → miss：backend.run │ backend.send_turn │ open_session                     │
└─────────────────────────────────────────────────────────────────────────────┘
        ▼ run_id / call_key / opts
┌────────────────────── backend 装配层（agent-core）───────────────────────────┐
│ opts.agent_type 空 → 既有 TeamWorkerBackend（native 路径，零改动）           │
│ opts.agent_type 非空 → ★B1 双闸校验（注册表 ∩ 团队声明集）                   │
│   → ★B2 build_cli_workflow_runtime()（兄弟函数，零加盟注入）                 │
│   → ★B3 模型 resolver 出口分流 + 协议硬闸                                    │
│   → ExternalHarnessMemberRuntime + ClaudeCodeHarness/CodexHarness            │
│       （★B4 REQUIRE_RESUME + checkpoint 指针落盘；★B5 token 归一）           │
└─────────────────────────────────────────────────────────────────────────────┘
        ▼ 事件（AGENT_STARTED/COMPLETED/FAILED）
┌────────────────────── 观测层（→ jiuwenswarm → Web）─────────────────────────┐
│ AGENT_STARTED.agent_type ★B12 四层透传 → WorkflowAgent                      │
│ Web 卡片全节点头像（TeamMemberAvatar：cli 品牌脸 / human 人类脸 / 哈希插画） │
│ model 字段填装配期解析实值（不再回落 Leader 模型）                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 数据流与时序

**A 系列数据流（tool_search）**——装配期一次决定，运行期零干预：

```
config.yaml 开关 → team_helpers → spec.progressive_tool
  → configurator 写 swarmflow_worker_base_spec.progressive_tool
  → avatar harness 构建时 ProgressiveToolRail 挂载（harness 原生）
  → 卡片注册期 exposure 冻结（DIRECT ∪ DEFERRED）
  → 轮内 before_model_call 过滤：meta ∪ DIRECT ∪ 已授权集
  → tool_search 命中 → 授权名 + 目录指纹写入会话全局状态
  → 轮末随 checkpointer 落盘（agent_session 跨轮保留 / 冷续跑读回）
```

**B 系列时序（三方一次性调用，miss 路径）**：

```
agent(prompt, options={"agent_type": "codex"})
  → 双闸校验（is_known_backend ∧ 声明集含 codex）
  → build_cli_workflow_runtime：HarnessContext 直构（成员名 + 稳定 agent_id
    + host_session_id），cwd 指向自管 worktree（成员名确定性派生）
  → ExternalHarnessMemberRuntime.start → harness._open_session
      REQUIRE_RESUME 两级候选：checkpoint 指针 → resume 续接；
      冷续跑指针缺失 → HarnessProtocolError（不回退首启）
      首轮发射（无历史轮）→ derived id 首启
  → 一轮 = Thread.run()（Codex）/ query→ResultMessage（Claude）
  → 轮末：CheckpointSink 把 thread_id/session_id 落盘成员子会话
  → usage 归一（增量规则 + 口径规则）→ AgentResult.tokens
  → journal 记录（key/sig/run_id/tokens/label/phase/kind/result/raw_text）
```

### 4.3 接口与数据模型

#### 4.3.1 接口契约（Requirement / Scenario）

**Piece A**：

| # | Requirement（系统 SHALL…） | Scenario（WHEN → THEN） |
|---|---|---|
| A-R1 | 以独立配置键控制 swarmflow worker 的渐进暴露，默认关 | WHEN `modes.team.jiuwen_team.swarmflow_progressive_tool` 为 true 且 `enable_swarmflow` 为 true THEN worker avatar 挂 `ProgressiveToolRail`；WHEN 键缺省或 false THEN 行为与现状字节一致；WHEN `enable_swarmflow` 为 false THEN 键无效（worker 不存在） |
| A-R2 | 带结构化输出的调用在渐进暴露下仍可用 | WHEN 开关开且 `agent(schema=...)` 执行 THEN `StructuredOutputTool` 以 DIRECT 暴露（`set_exposure_declared(True)`），模型无需搜索即可调用提交 |
| A-R3 | 授权随会话持久化 | WHEN `agent_session` 轮内 `tool_search` 授权工具 THEN 名单写入会话全局状态、随 checkpoint 落盘；WHEN 同 run 冷续跑后续轮 THEN 名单读回生效；WHEN fork 出子会话 THEN 授权不继承（新档案盒重新检索） |
| A-R4 | 开关不扰动缓存语义 | WHEN 开关值改变 THEN 同 prompt/label/phase/model/schema 的 `call_signature` 不变（`progressive_tool` 不进 identity 字典），缓存命中行为不变 |
| A-R5 | worker 工具活动结构化上报（v6 新增） | WHEN 三类节点（native `agent()` / native `agent_session` / 三方 CLI）发生工具调用 THEN `AGENT_ACTIVITY` 事件携带 `activity_type`（`tool_call`/`tool_result`）、`tool_call_id`（配对键）、`tool_name`、`content`（≤512 字符预览，引擎截断），不节流每次调用两条；WHEN `agent_session` 轮次 THEN 带与 started/completed 同键的 `correlation_id`；WHEN 三方 harness 事件缺 id 或解析失败 THEN 丢弃该条活动（debug 日志），不影响轮次；WHEN 缓存命中或 resume 重放 THEN 不重发活动（live-only，不落 journal，`agent_id` 不进签名）；WHEN `tool_result` 到达 THEN jiuwenswarm 按其 `agent_id`（缺省 label 回退）定位节点、与同 `tool_call_id` 的 `tool_call` 配对入 `activity`（每节点环形上限 200 条，落 checkpoint） |
| A-R6 | 前端按工作流 run 分组（v6 新增） | WHEN `team.member.spawned` / `team.task` 事件携带 `workflow_run_id` THEN 成员列表、会话缩略图、任务面板三面按其分组，组头显示工作流名 + run 短 id + run 状态徽标；WHEN 成员/任务无 `workflow_run_id` THEN 保持现有平铺区不混入分组；后端零改动（事件与前端模型已带该字段） |

**Piece B**：

| # | Requirement（系统 SHALL…） | Scenario（WHEN → THEN） |
|---|---|---|
| B-R1 | `agent_type` 走声明集白名单 | WHEN `agent_type` 非 `None` 且（不在注册表 或 不在团队 `external_cli_agents` 声明集）THEN 发射前抛 `BackendError`，消息含两个名单与改法建议；WHEN 声明集为空 THEN 同样报错并引导去设置页开对应 CLI 开关 |
| B-R2 | 签名含 `agent_type` 且向后兼容 | WHEN `agent_type` 非空 THEN 进入 `call_signature` identity 字典；WHEN 为空 THEN 签名与旧格式字节一致（存量 journal 零迁移） |
| B-R3 | 会话指针强制续接 | WHEN 外部成员**首轮发射**（本会话无历史轮）THEN 以确定性派生 id 首启（`session_id=<derived>`，非 resume）；WHEN **冷续跑重建**（journal 判定本会话已有历史轮）THEN `REQUIRE_RESUME` 从成员 checkpoint 读指针续接，指针不可得即抛 `HarnessProtocolError`（不回退 derived 首启，不静默开新会话）；WHEN 派生 id 与指针均不可得 THEN 同样抛错 |
| B-R4 | token 归一为 native 口径 | WHEN 外部轮完成 THEN 归一后 `total_tokens` 对齐 native `usage_metadata` 实测口径；WHEN Codex THEN 取 `usage.last`（原生本轮量）；WHEN Claude THEN 逐轮差分，遇 `ConversationResetMessage` 快照重算 |
| B-R5 | fork 硬路由 | WHEN fork 请求模式 ∉ provider 能力集（`card.fork_modes`）THEN 立即 `BackendError`（含模式名 + 支持列表 + 改法），沿 `AGENT_FAILED` 上抛 Leader；WHEN 环境失败或 journal 凭据失效 THEN 同样如实报错；WHEN 请求 `after`/compact 模式 THEN 对三方永久报错引导 |
| B-R6 | schema 能力装配期检测 | WHEN 调用带 `schema` 且 provider 无结构化输出能力 THEN 装配期拒绝（`BackendError`），不进入轮次 |
| B-R7 | 事件与前端标识 | WHEN 外部调用发射 THEN `AGENT_STARTED` 携带 `agent_type` 与装配期解析的 model 实值；WHEN native 调用 THEN `agent_type` 为 `None`、事件字段与现状一致 |
| B-R8 | 中止与超时语义 | WHEN 三步中止到达外部成员 THEN 执行 `runtime.abort(immediate=True)`（STEER 能力感知）；WHEN 被中断 THEN 该轮不写 journal（与 native 同）；WHEN `options.timeout` 到期 THEN 包住整个轮次，超时即 abort + `BackendError` |
| B-R9 | 三方节点模型三参数，全场景无兜底（v5 定稿） | WHEN `options.model` 命中团队模型池且协议兼容（Claude → Anthropic 协议条目；Codex → 非 Anthropic 条目）THEN 池条目转 `ExternalCliModelConfig` 作为主模型，CLI 自启动即走池端点；WHEN `options.model` 在池但协议不兼容、或不在池 THEN 发射前 `BackendError`（含兼容池模型清单；该 kind 声明了 `builtin_models` 目录时同时列出并提示改用 `builtin_model`）；WHEN `options.builtin_model` 填写 THEN 必须在该 kind 声明的 `builtin_models` 目录内，CLI 走自带登录跑该模型，且与 `model` 互斥（同时填 → 报错）；WHEN `options.effort` 填写 THEN 必须伴随 `builtin_model` 且在该模型的 `efforts` 内，缺省取 `default_effort`；WHEN 三参数全缺省 THEN CLI 走自带登录的默认模型（黑盒）；WHEN 任何路径认证失败（401/403）THEN **不兜底**——`AGENT_FAILED` 携带双受众报错文案（根因 + 脚本级绕行建议），Leader 修改脚本后重跑（resume 保缓存）；WHEN native 节点携带 `builtin_model` / `effort` THEN 报错；WHEN native 节点 `model` 不在池 THEN 报错 + 兼容清单（不再静默回落 worker 基 spec 模型） |
| B-R10 | 节点显示实际生效模型（v4 新增，v5 修订） | WHEN `AGENT_STARTED` THEN `model` 为装配期最佳已知值：`model` → 池模型名；`builtin_model` → 该名；缺省 → `None`（**不回填 `agent_type` 字符串、不回落 Leader 模型**）；WHEN 外部轮完成 / 失败 THEN `AGENT_COMPLETED` / `AGENT_FAILED` 携带 CLI 实际回报的模型（`system/init` / `session/model_changed`），jiuwenswarm 以之覆盖节点 `model`；WHEN native 节点 THEN 两事件 `model` 语义不变 |
| B-R11 | 会话模型首锁（v5 新增） | WHEN `agent_session` 的首个 cache-miss 轮开启 avatar THEN 当轮模型 hint 的**解析身份**锁定为 `AgentSession._opened_model`；WHEN 后续轮 hint 解析身份与锁定值相同 THEN no-op，不同 THEN `EngineError` 并指引 `fork()`；WHEN `fork()` 派生子会话 THEN 子会话独立走自己的首轮锁定（fork 时传新 hint 合法）；WHEN 纯 cache-hit 会话 THEN 从未开 avatar，无锁定；WHEN 首轮无 hint THEN 锁定为 base spec 模型（native 为 `worker_base_spec.model`；三方无 hint 的锁定值指名问题遗留，见 §10） |

#### 4.3.2 agent-core 侧接口变更

- `TeamAgentSpec` 新增字段 `progressive_tool: bool = False`（构建期声明，
  A1）。configurator 在 `ctx.role == TeamRole.LEADER and spec.enable_swarmflow`
  分支把它写进 `swarmflow_worker_base_spec` 副本的
  `DeepAgentSpec.progressive_tool: ProgressiveToolSpec`（既有字段）。
- `agent()` / `AgentSession.send()` 的 `_ENGINE_OPTIONS` 已含 `agent_type`
  （既有），本设计补齐其值语义：`None`（native）| `"claude"` | `"codex"` |
  （未来适配器名）。
- `call_signature(prompt, opts, json_schema, history=None)` 的 identity 字典
  在 `opts.get("agent_type")` 非空时并入该键（约 5 行，B8）。
- `WorkflowProgressEvent` 新增 `agent_type: str | None = None`（仅
  `AGENT_STARTED` 携带，B12）；`_build_progress_message` 透传。
- `AgentBackend` 不改协议：`agent_type` 经 `opts` 袋流入，`TeamWorkerBackend`
  内部路由到 native 路径或 `build_cli_workflow_runtime()`。
- **v5（B-R9 / B-R10 / B-R11）**：`_ENGINE_OPTIONS` 新增 `builtin_model` / `effort`
  键（`agent_type` 既有）；`call_signature` 的 identity 字典在三方节点并入
  `builtin_model` / `effort`（B8 同款规则）。`build_cli_workflow_runtime()` 的
  `model_override: str`（裸名）改为 `model_config: ExternalCliModelConfig | None`
  ——backend 在调用前完成解析与校验：`model` 池命中 → 池条目转换（复用
  `swarmflow_model_resolver` 查池 + `_team_model_config_to_external` 转换 +
  `_provider_filter_for_cli` 协议过滤，均为团队成员路径既有件）；`builtin_model`
  → 无端点配置（携带 `effort`，目录与 effort 校验复用
  `TeamBackend.resolve_builtin_model` 同规则）；全缺省 → `None`。校验失败在
  backend 发射前抛 `BackendError` 并标记**不可重试**，文案双受众（见 §5.8）。
  `AgentResult` 新增 `model: str | None`，`ExternalMemberTurns` 从运行时
  `RuntimeReliabilityContext.model` 取值；`WorkflowProgressEvent.model` 扩展到
  `AGENT_COMPLETED` / `AGENT_FAILED`；`describe_model(opts)` 顺序改为
  `opts.model` → 声明条目模型 → `None`（删除 `agent_type` 冒充末位）。
  `AgentSession` 新增 `_opened_model` 槽位实现会话首锁（B-R11）。
- **v6（A-R5）**：`WorkflowProgressEvent` 的 `AGENT_ACTIVITY` 扩展结构化字段
  `activity_type` / `tool_call_id` / `tool_name` / `content`（保留 `message`
  兼容旧消费方）；`SwarmflowActivityRail` 撤销 `min_interval_s` 节流、改挂
  `before_tool_call`（发 call）+ `after_tool_call`（发 result）双钩；引擎
  `_attempt_calls` 为会话路径同样注入 `{**opts, "agent_id": ks}` 副本，
  `AvatarSessionManager` 开会话时挂同款 rail（补 F_112 未覆盖的
  `agent_session`）；三方节点在 `ExternalHarnessMemberRuntime._on_event`
  映射 harness 事件（Claude `tool_use` / `tool_result` 块，Codex
  `item.started` / `item.completed`），原生 id 直接作 `tool_call_id`。

#### 4.3.3 jiuwenswarm 侧接口变更

- `workflow_state.py`：`WorkflowProgress` / `WorkflowAgentState` 各加
  `agent_type` 字段；随 `workflow.updated` 推送。
- **v5（B-R10 / 报错文案）**：`_on_agent_completed` / `_on_agent_failed` 在
  `progress.model` 非空时覆盖节点 `model`（`agent_session` 每轮都回报，分组头
  随之刷新）；Web 无改动，`model` 列已渲染该字段。错误预览透传时保证
  **截断安全**——`AGENT_FAILED.error` 文案按"归类 + 根因在前、绕行建议与
  清单在后"的顺序构造（§5.8），前端错误位截断只裁尾部清单。工具描述
  （swarmflow.md cn/en）与两语指南补 `model` / `builtin_model` / `effort`
  三参数语义、`<builtin_model_catalog>` 注入与"无兜底、失败改脚本"的说明。
- **v6（A-R5 / A-R6）**：`WorkflowRunState._KIND_HANDLERS` 增加
  `agent_activity`——按 `agent_id`（缺省 label 回退）定位节点追加
  `WorkflowAgentActivity`（既有预留模型启用 `tool_name` / `tool_call_id` /
  `tool_result_preview` 字段），每节点环形上限 200 条，随 checkpoint 落盘；
  `WorkflowMonitorHandler` 对只含 activity 的 delta 按 run 合批 300ms（含
  状态变化的 delta 立即发并冲刷积压）；`agent_activity` 同时镜像为
  `TeamMemberExecutionEvent`（member_id = `run_id:agent_id`，与名册镜像 id
  同构），成员调用列表的既有配对/渲染链零改动复用。前端三面（成员列表 /
  会话缩略图 / 任务面板）按 `workflow_run_id` 分组（A-R6），TUI 不适配
  （与 B12 同口径）。
- Web 前端 `workflowTypes.ts`：`WorkflowAgent.agent_type?: string`；
  `SwarmflowTreeView.tsx` 节点行 `StatusIcon` 后渲染 `TeamMemberAvatar`
  （三方 → 品牌脸，human → 人类脸，native → label 哈希插画）。

### 4.4 关键流程

#### 4.4.1 Piece A：worker avatar 的一次渐进暴露轮

1. 装配：configurator 按 §4.1 把 `ProgressiveToolSpec(enabled=True)` 写进
   worker 基 spec；`derive_member_spec` 只覆盖 card/model/system_prompt/tools，
   `progressive_tool` 原样继承（一次性 worker 与 avatar session 两条路径同）。
2. 首轮：卡片注册期 exposure 冻结——`_DEFAULT_DIRECT_TOOL_NAMES` + 显式
   声明（含 A3 的 `StructuredOutputTool`）为 DIRECT，其余 DEFERRED；meta 工具
   （`tool_search` / `tool_call`）恒可见。
3. 模型调用：`before_model_call` 过滤工具清单到 meta ∪ DIRECT ∪ 已授权集。
4. 检索：模型调 `tool_search` → BM25 命中 → 授权名 + 目录指纹写入会话全局
   状态 → 后续轮次直接可见（`agent_session`）；目录变更 → 指纹失配 → 授权
   作废重新检索。
5. 落盘：授权随 `DeepAgentState` 由 checkpointer 写入；冷续跑 `pre_run` 读回。

#### 4.4.2 Piece B：native↔三方混编脚本示例

```python
META = {"name": "code_review_flow"}

async def run(args):
    result = await agent(
        "审查这段 diff,给出风险清单",
        label="codex_review", phase="审查",
        schema={"risk": str, "severity": "low|mid|high"},
        options={"agent_type": "codex", "model": "gpt-5-codex", "timeout": 300},
    )
    plan = await agent("给出修复方案", label="claude_fix", phase="修复",
                       options={"agent_type": "claude"})
    sess = agent_session(label="codex_iter", phase="迭代",
                         options={"agent_type": "codex"})
    draft = await sess.send("实现方案 A")
    final = await sess.send("按评审意见修改")   # 同一 SDK 会话记忆延续
    return {"risk": result["risk"], "final": final}
```

语义要点：native 调用不写 `agent_type` 行为不变；`parallel` / `pipeline`
分支内混编合法（路由逐调用）；缓存命中轮对三方**零进程成本**（不拉 CLI）。

#### 4.4.3 B4f：fork 凭据生命周期

```
fork(keep_rounds=N, mode=before)（外部会话）
  → 能力闸：mode ∈ card.fork_modes？否 → BackendError（含支持列表）
  → 物化：Codex thread_fork(thread_id, last_turn_id) /
          Claude resume_session_at + fork_session（查询级优先）
  → 凭据三元组（父指针 + 切分点 + 模式）随子会话 checkpoint 落盘
  → 命中回放：读回凭据，不重复 fork
  → 物化前对账：凭据切分点 vs 父线程当前实际轮次；不一致 → BackendError
```

Claude `before` 模式切分规则（SDK 源码定论，转实现规则）：`resume_session_at`
取**保留轮最后一条转写条目**（whatever its type——structured_output 轮的末条目
在 assistant 消息之后，取 assistant UUID 会被 CLI 按设计拒绝，报错文本确定性
可匹配："Resume rejected by --resume-drops-turn:"）。

### 4.5 配置与开关

| 键 | 位置 | 默认 | 说明 |
|---|---|---|---|
| `swarmflow_progressive_tool` | `modes.team.jiuwen_team.*` | `false` | Piece A 总开关；仅 `enable_swarmflow: true` 时生效；改后新建团队生效。**不与**单 Agent 的顶层 `progressive_tool_enabled` 共用配置 |
| `external_cli_agents` | `modes.team.jiuwen_team.*` | `[]` | 既有键，B1 复用为三方白名单第二名单（条目携带 `cli_path`/`cwd`/`skills` 等装配参数，B2/B7 直接取用） |

回退策略：A 关掉即回现状（字节兼容）；B 的 `agent_type` 不写即 native
（B8 保证存量缓存不受扰动）。

### 4.6 会话记忆的 run 隔离（C1+C2 合并方案）

`agent_session()` 成员名从纯计数器 `wf-sess-{slug}-{n}` 改为掺 run 前缀
（对齐一次性 worker 路径 `{run前缀}-{slug}-{...}` 的既有模式）：

| 场景 | 行为 | 机制 |
|---|---|---|
| 续跑（同 `run_id`） | 记忆完整找回 | 同 run_id 派生同成员名 → 同会话 id `{team}/{wf}/{member}` → checkpointer 读回 |
| 全新重跑（新 `run_id`） | 记忆从零 | 新前缀 → 新格子 |
| 并发同名两场 run | 互不干扰 | 前缀天然隔离（一次性 worker 路径已验证此模式） |

三方成员同语义：CLI 会话指针存成员子会话 checkpoint，成员 `agent_id` 含 run
前缀后指针按 run 隔离。一次性 `agent()` 本就无跨轮记忆，不受影响。

## 5. 关键决策与权衡 (ADR)

### 5.1 Piece A：复用 harness 原生机制而非自建清单

- **决策：** swarmflow 不维护任何工具暴露清单；DIRECT/DEFERRED 冻结、检索、
  授权、指纹校验全部复用 harness 原生（`ProgressiveToolRail` /
  `before_model_call` 过滤 / 会话全局状态）。
- **背景：** 单 Agent 模式已有一整套实现并默认开启，团队侧缺的只是把
  `ProgressiveToolSpec` 写进 worker 基 spec 的管道。
- **备选与理由：** 自建 per-workflow 工具清单——被否决：两套清单必然漂移，
  且授权持久化、指纹校验要重写一遍。

### 5.2 Piece A：独立配置键 + 默认关，不共用单 Agent 键

- **决策：** 新键 `swarmflow_progressive_tool` 默认 `false`；不读顶层
  `progressive_tool_enabled`，不做 chat.send 会话级参数，不做前端开关。
- **背景：** 评审中曾两度翻转（先"默认开对齐单 Agent"，后用户反思改回
  "默认关"）。翻转点：默认开意味着新版本上线即改变存量 swarmflow 行为，
  且 A3（StructuredOutputTool 直达）必须同版落地否则带 schema 调用失效；
  默认关则 A3 降为普通必改项，存量零风险。
- **备选与理由：** 共用单 Agent 键——被否决（swarmflow 场景自己管理，
  两类成员的暴露面与风险不同构）；Web 面板会话级开关——被否决（团队级
  能力配置而非会话偏好；面板在有历史的会话上只读，语义本就构建期）。

### 5.3 Piece B：兄弟函数而非复用 `build_cli_runtime`

- **决策：** 新增 `build_cli_workflow_runtime()`：`HarnessContext` 直构、
  `inject_mcp=False`、无 `OPENJIUWEN_TEAM_JOIN` 环境变量、无团队系统提示词。
- **背景：** 既有 `build_cli_runtime()` 是给"要长期在线、能回聊团队"的队友
  用的（加盟三件套）；工作流三方成员只被脚本单向派活，加盟件是纯负担。
- **备选与理由：** 加开关复用同一函数——被否决：函数已有 20+ 参数，再叠
  语义开关造成组合爆炸；模型翻译 / skills / OTel 桥 / checkpoint 池等与
  团队无关的能力在两函数间共享底层件。

### 5.4 Piece B：worktree 经 `cwd` 自管，不用 `extra_args`

- **决策：** 隔离经 `cwd` 指向自管 worktree，路径由成员名确定性派生、跨冷
  续跑稳定；复用 `SwarmflowWorkerWorktrees` 管理件。
- **备选与理由：** Claude SDK `extra_args={"worktree": ...}`——被否决：
  版本耦合（参数名随 CLI 版本走）、生命周期黑箱（CLI 何时建/删 worktree
  不可知）、provider 不对称（Codex 无对应参数）。`cwd` 是全后端公共参数。

### 5.5 Piece B：fork 硬路由 + 镜像降级退役

- **决策：** 请求模式 ∈ `card.fork_modes` → SDK 原生物化；否则立即
  `BackendError` 上抛 Leader 修改参数；native 侧的引擎镜像降级机制对外部
  整体退役；环境失败与凭据失效同样如实报错。
- **背景：** 评审中用户明确要求"只支持 SDK 支持的模式，不支持的报错给
  Leader 改参数"。镜像降级（用引擎侧对话镜像拼一个降级 fork）会让 Leader
  误以为 fork 成功而实际上下文是残缺的。
- **备选与理由：** 镜像降级保留为兜底——被否决（上述静默残缺风险）；
  `after`/compact 两模式补齐——被否决（两家 SDK 均无对应原语，模拟成本
  与保真度都不可接受）。

### 5.6 Piece B：token 事后记账 + 不透传 SDK 预算

- **决策：** 轮末 usage 归一入账双层账本，入口闸拦下一次；不把引擎预算传
  给 Claude `max_budget_usd` / Codex goal。
- **背景：** 三处不同构——单位（token vs USD）、记账位置（CLI 内部 vs 引擎
  账本）、截断语义（错误结果 vs 暂停/封存）。透传即双重记账 + 暂停语义
  伪装成调用失败。
- **备选与理由：** SDK 侧硬上限作保险丝——评审中讨论后未采纳进 v1（粗
  粒度熔断仍需文档声明单位换算不精确，收益有限）；最坏超支一轮为有界
  可接受代价（token 轮末才可见）。

### 5.7 C1+C2：会话记忆按 run 隔离（成员名掺 run 前缀）

- **决策：** `agent_session()` 成员名掺 run 前缀，对齐一次性 worker 路径。
- **背景：** 旧设计 `{team}/{wf}/{member}` 不含 run_id，同名并发 run 的
  avatar 会话互踩对话记忆（静默脏数据）。评审确认一次性 worker 路径已改
  为 run 前缀 + 调用键哈希，会话路径是唯一残留。
- **备选与理由：** SDD 声明"同名串行"——被否决（约束只存在于文档）；
  启动守卫拒绝并发第二 run——被否决（拒绝的是合法用例，且守卫要跨进程
  状态）。掺前缀后代价：同脚本全新 run 的会话记忆从"读回上一场"变"从零
  开始"（更符合"再跑一次"的直觉），代价是每场重新积累上下文。

### 5.8 Piece B（v5）：三方节点模型三参数、全场景无兜底、双受众报错

- **决策：** 工作流三方节点的模型参数为 `model`（池端点）/ `builtin_model`
  （CLI 自带登录目录内模型）+ `effort`（伴随 `builtin_model`），`model` 与
  `builtin_model` 互斥，三参数全缺省则走 CLI 自带登录默认模型；**任何路径都
  不设兜底**——认证失败如实 `AGENT_FAILED`，报错文案双受众（Leader 可修的
  告知改法；修不了的根因在前端准确透出），Leader 改脚本后重跑（resume 保
  住已缓存轮次）。
- **背景与迭代：** v4 曾定"池兜底 + 进程内晋升"对齐团队成员路径（背景：
  实测 Codex 无 `auth.json` 必 401，而团队模式成员有兜底能跑）。评审中用户
  三轮收紧：先加 `cli_fallback_model` 必填参数（后否决——脚本逐节点抄兜底
  名，负担与出错面大）；再改引擎规则自动兜底（后否决——隐藏决策，违背
  "实际模型 == 声明参数"的一致性原则）；最终定 A 案（全面无兜底）。决定性
  理由：① resume/journal 架构使恢复便宜（改脚本重跑，其余节点缓存命中），
  401 无特殊性；② 引擎改动收敛（无需把团队模式兜底链接进 workflow 运行
  时）；③ CLI 登录失效是环境缺陷，自动兜底会静默滑到池预算、掩盖根因，
  A 在第一个节点就大声失败。"与团队模式对齐"论据不成立——swarmflow 已在
  兜底参数、`_preferred_current_model` 规则、builtin+401 三处刻意偏离。
- **报错文案契约（双受众）：** Leader 可修（脚本参数错误：不在池/不兼容/
  不在目录/互斥/effort 非法）→ 指名参数 + 可用清单，沿用团队工具三档指路
  （名字在内置目录误填 `model` → 提示改用 `builtin_model`）；Leader 修不了
  根因（CLI 登录 401/403、CLI 未安装、池密钥失效）→ 两段式：先根因（含
  CLI 原始状态码/URL，标明"环境问题，非脚本错误"），再脚本级绕行（"可给
  该节点加 `model=<池模型>`"+ 兼容清单）。**归类与根因前置**（前端错误预览
  截断保头部），校验类错误标记不可重试（避免引擎 3 次白跑）。
- **配套：** `builtin_model` / `effort` 进 `call_signature`（不进则同 prompt
  不同模型撞缓存）；fork 子会话继承三参数；会话首锁见 B-R11；`describe_model`
  删除 `agent_type` 冒充末位（B-R10）；工具描述注入 `<builtin_model_catalog>`。
- **实际模型回报（B-R10）：** 装配期只知"最佳已知"，CLI 自带登录时真实模型
  只有 CLI 自己知道（`system/init` / `session/model_changed`），故完成 /
  失败事件补带 `model`，前端以之覆盖；`AGENT_STARTED` 不再用 `agent_type`
  字符串冒充模型名。

### 5.9 被否决的备选汇总

| 方案 | 否决理由 |
|---|---|
| B12 文字徽标 `[codex]` / 品牌图标 / 分组染色三案 | 用户选定全节点头像方案（复用 `TeamMemberAvatar` 认脸体系），其余渲染案弃 |
| B12 TUI 同步适配 | 用户明确 TUI 不适配，仅 Web |
| A9 复用 `JiuSwarmStreamEventRail` 收集 tool_call 名单 | 用户明确不预设复用；实查该 rail 在 worker 上跑的帧写入 backend 自建的 `TeamSession` 流，无人消费、成员名与镜像 id 对不上——v6 采 B 方案经引擎 `AGENT_ACTIVITY` 通道（§5.10） |
| B4 会话级 fork 凭据（不落盘） | 命中回放会重复 fork；凭据必须随子会话记录落盘 |
| 三方 provider 内置 fallback 模型链 | v1 不做；**v4 翻转**为池兜底 + 进程内晋升（§5.8 旧文）；**v5 再翻转回全面否决**：用户定 A 案——一致性原则（实际模型 == 声明参数）、resume 恢复便宜、避免掩盖登录失效根因（§5.8） |
| `cli_fallback_model` 必填参数（v5 评审中段草案） | 脚本逐节点抄兜底名，负担与出错面大；兜底仅在认证失败时用到，为它付必填参数不划算——用户否决，改引擎规则后又全部放弃 |
| 无参数时引擎自动兜底（B 案）/ 无参数直接用 team 模型端点（C 案） | B：无参数路径无声明可背离、对齐团队模式，但仍需接兜底链且掩盖根因；C：默认供给从用户订阅变成团队预算、协议不兼容时仍需定义回退——用户选 A（全面报错 + 双受众文案） |

### 5.10 Piece A（v6）：工具活动走单一引擎通道，前端按 run 分组

- **决策：** worker 的 tool_call / tool_result 活动经 F_112 的
  `AGENT_ACTIVITY` 事件通道升级承载（新增结构化字段、撤销节流），一条通道
  喂树状图与成员调用列表两个面；前端三面按 `workflow_run_id` 分组。
- **背景：** A9 原"延后，方案另行设计"。现状三条链路均断：F_112 只有节流
  的工具名字符串、只覆盖 native 一次性 `agent()`；jiuwenswarm
  `WorkflowRunState` 不处理 `agent_activity`（数据模型早已预留
  `WorkflowAgentActivity`，`activity` 恒空）；worker 挂的
  `JiuSwarmStreamEventRail` 跑在 backend 自建的无人消费 `TeamSession` 流上，
  其 `chat.tool_call` 帧根本到不了 Web。
- **方案对比与理由（用户选定 B）：** A（树用工具名、列表另开全量通道）——
  两条通道语义不同、同一次调用两次上报，被否决；C（两面都只有工具名）——
  列表面降级、与真人队友列表不一致，被否决；B（一条全量通道喂两面）——
  两面是同一份数据的两种视图，事件自带 `agent_id` 天然对齐镜像成员 id
  （`run_id:agent_id`），且是唯一能统一覆盖 native / `agent_session` / 三方
  节点的路径。代价（事件量）由三档口径控制：参数/结果预览 512 字符（引擎
  截断，所有消费方同一份）、只含 activity 的 delta 按 run 合批 300ms、每
  节点 activity 环形上限 200 条（防长跑 run 快照膨胀）。
- **live-only 的取舍：** 活动不落 journal、resume 不回放（与 F_112 同）——
  落盘会让 journal 膨胀且回放期观战方看到的"历史活动"与实际时间线混淆；
  刷新恢复走 checkpoint 里的 `activity` 快照（合批后仍落），只缺 resume
  重放期的实时性。
- **A10 的定位：** 后端零改动（镜像事件已带 `workflow_run_id`，前端模型
  已有该字段），纯前端渲染分组；无 `workflow_run_id` 的真实队友/普通任务
  保持平铺，不与工作流节点混编。

## 6. 影响面与兼容性

**agent-core（openjiuwen/agent_teams）**：

- `schema/blueprint.py`：`TeamAgentSpec.progressive_tool`（+1 字段）。
- `agent/agent_configurator.py`：swarmflow worker 副本写
  `progressive_tool`（约 18 行）。
- `workflow/engine/primitives.py`：`call_signature` identity（约 5 行）。
- `workflow/engine/progress.py` + `workflow/tool_swarmflow.py`：
  `agent_type` 事件字段（各 1-3 行）。
- `workflow/backends/team_worker_backend.py`：`agent_type` 路由 + B1 双闸
  + 调 `build_cli_workflow_runtime`。
- `external/cli_agent/spawn.py`（或新文件）：`build_cli_workflow_runtime()`
  兄弟函数。
- `tools/structured_output_tool.py`：DIRECT 声明（A3）。

**jiuwenswarm**：

- `common/config.py` + `resources/config.yaml`：`swarmflow_progressive_tool`
  键（含注释）。
- `agents/harness/team/team_manager.py` + `server/runtime/agent_adapter/
  team_helpers.py`：键读取与落 spec。
- `agents/harness/team/handlers/workflow_state.py`：`agent_type` 字段。
- Web 前端：`workflowTypes.ts`（+1 字段）、`SwarmflowTreeView.tsx`（头像
  渲染 + 身份推导函数）。

**兼容性**：

- 键缺省 = 现状（A 字节兼容）；`agent_type` 空 = 旧签名（B 存量 journal
  零迁移）；`AGENT_STARTED.agent_type` 为 `None` 时事件消费者不感知。
- **BREAKING（声明式）**：`agent_session()` 会话记忆从跨 run 变按 run 隔离
  （§4.6）。旧行为"全新 run 读回上一场记忆"本就是 C1 声明的副作用而非
  契约，此变更使其符合直觉；同 run_id 续跑行为不变。
- 备注：journal 快照与 WAL 已按 run_id 分文件
  （`journal-{run_id}.jsonl` / `wal/{run_id}.wal`），WAL 永不主动删除——
  膨胀问题已在此前修复中处理，非本设计范围。

**性能预期**：三方命中轮零进程成本；miss 轮多一次 CLI 子进程拉起（秒级），
被 `options.timeout` 包住。

## 7. 测试与验证

**单元（agent-core，走该仓自有规范）**：

- `call_signature`：`agent_type` 空/非空的字节稳定性（B-R2）。
- 双闸校验：三个失败分支的报错文案（B-R1）。
- token 归一：Codex `usage.last` 直取；Claude 差分 + reset 快照重算（构造
  `ConversationResetMessage` 序列）。
- fork 路由：模式越界报错文案；凭据对账不一致报错。
- Claude 切分规则：structured_output 轮取"最后一条转写条目（非 assistant
  UUID）"（B4f 实现规则，SDK 源码已定论）。

**集成（jiuwenswarm OpenSpec BDD 为唯一验收来源）**：

- Piece A 开关矩阵：{开关} × {schema 有无} × {session 有无} 的行为与缓存
  签名稳定性。
- Piece B 混编脚本：native/claude/codex 同脚本 + `parallel` 混编；缓存命中
  轮不拉进程；暂停→续跑→三方 `REQUIRE_RESUME` 续接。
- B12 前端：`agent_type` 事件到达 `WorkflowAgent`；三类头像渲染。

**实测项（C5，交付前现场验证）**：

| # | 项 | 判定 |
|---|---|---|
| 1 | Codex 跨进程寻址：swarmflow SDK 客户端 fork app-server 内父线程是否可达 | 失败 → 该 provider fork 能力集置空（报错引导），不阻塞交付（B11） |
| 2 | token 口径：外部 `total_tokens` vs native `usage_metadata` 实测对齐与折算系数 | 差异超阈值 → 调整归一加权 |
| 3 | Claude fork 后转写落盘时机（fork 完成 → 文件可见的窗口） | 窗口过长 → 物化后加等待/重试 |

（原 C5 的"fork 后能否 REQUIRE_RESUME 续接"与"轮末条目陷阱"两项已由 SDK
源码定论转为实现规则 + 单元测试，不再列为实测。）

## 8. 可观测性

- `AGENT_STARTED.agent_type` + model 实值：节点来源可判定（B12）。
- token：外部轮的 `tokens` 字段经归一后与 native 同口径，双层账本、
  `budget_exhausted_scope` 语义复用。
- 日志：B1 拒绝（两名单）、B4f 三路报错、B-R3 指针不可得，均带结构化
  上下文（成员名 / run_id / provider）。
- OTel：外部成员 span 桥（`ChunkRecordingSpanBridge`）已有，本设计零改动。

## 9. 风险与回滚

| 风险 | 缓解 |
|---|---|
| A3 遗漏导致带 schema 调用在渐进模式下失效 | A-R2 单元测试强制；A2 默认关压缩暴露面 |
| 三方 CLI 版本漂移（SDK 参数 / 转写格式变化） | 装配期能力卡片集中声明；B4f 报错带 provider 上下文便于定位 |
| 会话记忆 run 隔离后用户预期落差（以为会记住） | §4.6 表格进用户文档；事件层不加提示（同 run 续跑行为不变） |
| Codex fork 实测失败 | B11 预案：能力集置空 + 报错引导，不阻塞其余交付 |
| 双层账本对外部轮最坏超支一轮 | 已声明边界（B6）；入口闸保证下一调用被拦 |

回滚：A 关配置键即回现状；B 不写 `agent_type` 即 native；两者独立灰度。
C1+C2 的成员名变更随版本回滚即恢复旧命名（checkpoint 内容不变，仅寻址
前缀变）。

## 10. 开放问题 (Open Questions)

- [x] A9（原"延后"）：tool_call / tool_result 名单进树状图与成员调用
      列表——**v6 敲定**（A-R5，§5.10 B 方案）；A10 前端按 run 分组同步敲定
      （A-R6）。
- [ ] B12 细节：头像尺寸/对齐、`AgentDetailModal` 标题拼接格式——开发调试
      阶段按最终效果调整（用户已确认此安排）。
- [ ] Claude schema 替代路径（提示词约束 + `ResultMessage.structured_output`
      解析校验）的实现细节——装配期能力检测已定，路径细节实现时定。
- [x] B-R11 三方会话无 hint 时的锁定值指名（CLI 默认模型黑盒，报错如何指名 / 是否强制首轮填模型）——**已解决（2026-10-10）**：上游合入的修复仅到通用短语层面（native 不指名具体模型、三方未触及），经核验不满足后自行开发——native 经 backend 钩子 `describe_base_model()` 指名基 spec 具体模型名，三方由引擎按身份元组指名 `the {cli} CLI's own default model`；agent-core `73227be1f`（fix）+ `d89fcd63d`（docs）
      指名 / 是否强制首轮填模型）——**遗留**：用户确认将在他人 PR 中修复，
      本设计不处理，SDD 仅记录。

## 11. 评审记录 (Iteration Log)

| 轮次 | 日期 | 版本 | 评审人 | 结论 | 主要修改/讨论 |
|---|---|---|---|---|---|
| 1 | 2026-09-18 | 1 | @chenchunzhou | draft | 27 点逐条审视定案（8+14+5）；A2 默认值两度翻转终定"默认关"；B4f 从镜像降级改硬路由；C1+C2 合并为 run 隔离方案；C5 经 SDK 源码复核收窄至 2 项半 |
| 2 | 2026-09-18 | 2 | @chenchunzhou | 修订 | B-R3 收紧：`REQUIRE_RESUME` 语义区分"首轮发射（derived id 首启合法，非 resume）"与"冷续跑（指针必须可得，缺失即 `HarnessProtocolError`，不回退 derived 首启）"——消除与 §4.2 时序图"无指针 → 新会话"的措辞冲突（现 harness 实现的 derived 回退仅对首轮合法，冷续跑指针丢失须显式报错而非静默首启）；同步修订术语表与 B 系列时序图 |
| 3 | 2026-09-21 | 3 | @chenchunzhou | 实现记录 | 两仓实现落地（agent-core F_113/F_114/F_115，jiuwenswarm OpenSpec 变更 verify 无 Critical）；§12 记录 11 项落地偏差与待办（B-R3 守卫位置、token 来源、schema 解析路径、fork 凭据/物化两段式、Claude 转写 uuid 切点、cwd opt-in、describe_model 钩子、pin 未升级） |
| 4 | 2026-09-21 | 4 | @chenchunzhou | 修订 | 实测 Codex 节点 401：新增 B-R9（模型来源对齐团队成员：池主模型 / 原生登录 + 池兜底 / 进程内晋升）与 B-R10（完成事件回报实际模型），§5.8 记录对 v1 否决项的翻转；§12 补两条落地缺陷（白名单误读运行时 `TeamSpec`；`describe_model` 以 `agent_type` 冒充模型名） |
| 5 | 2026-09-29 | 5 | @chenchunzhou | 定稿 | B-R9 三轮收紧后定 A 案：`model` / `builtin_model` + `effort` 三参数、全场景无兜底、双受众报错文案（§5.8 记录否决史：`cli_fallback_model` 参数 → 引擎规则兜底 → 全否）；新增 B-R11 会话模型首锁；`model` 不在池从"透传"改为报错 + 清单；三方会话无 hint 锁定值指名遗留（他人 PR） |
| 6 | 2026-09-29 | 6 | @chenchunzhou | 定稿 | A9/A10 从延后转正并入本文档（取消新开 SDD-0027 设想）：A-R5 工具活动结构化上报（B 方案单通道、512/300ms/200 三档口径、`agent_session` 与三方节点补覆盖）、A-R6 前端三面按 `workflow_run_id` 分组；§5.10 ADR 记录三案对比与 `JiuSwarmStreamEventRail` 不可复用的实查结论 |

## 12. 实现记录与偏差

实现落地于两仓（2026-09-20 ~ 2026-09-21）：agent-core `bugfix/develop_swarmflow`（Piece A 5 commits：`c799fdd7`..`25fc8838`，F_113；Piece B 核心 6 commits：`0711f323`..`b46fd91b`，F_114；fork 6 commits：`5a97e83c`..`d41bc677`，F_115；S_12 / S_18 / S_27 同步修订）；jiuwenswarm `bugfix/develop_swarmflow`（Piece A `071ec1b28`..`801ed6d27`；组 5 `3210357f1`..`3d035438a`）。OpenSpec 变更 `swarmflow-toolsearch-and-thirdparty-agent`（5 份 BDD 规格，`/opsx:verify` 无 Critical）。

### 12.1 与设计的偏差（均为落地时按代码事实修正，非需求变更）

| # | 设计原文 | 实测 / 落地 | 影响 |
|---|---|---|---|
| 1 | A1 jiuwenswarm 键经 `team_helpers` + `team_manager` 落 spec（§6） | `config_loader.load_team_spec_dict` 已把整个团队块 deepcopy 进 `TeamAgentSpec.model_validate`，一行 `spec_dict["progressive_tool"] = bool(spec_dict.pop("swarmflow_progressive_tool", False))` 即可；不进会话级三级优先级 | 无 |
| 2 | A-R3 授权"随 checkpoint 落盘、冷续跑读回" | 引擎只保证**同进程**恢复（avatar 会话 InMemory store，agent-core S_18 第 12 条）；跨进程持久化来自 jiuwenswarm 装配的 `PersistenceCheckpointerProvider`。BDD 场景与 F_113 已按此收窄 | 三方 B-R3 指针存同一成员子会话 checkpoint，同样依赖宿主 checkpointer |
| 3 | B-R1 "不发 `AGENT_STARTED`" | 闸门在 backend，`AGENT_STARTED` 由引擎在调 backend 前发出；失败沿 `AGENT_FAILED` 带文案上抛。BDD 场景已改 | 前端会先见 started 再见 failed |
| 4 | B-R3 守卫在 harness（§4.2 "冷续跑指针缺失 → HarnessProtocolError"） | 判定在 `AvatarSessionManager._external_turn`（外部会话运行时**延迟到首轮 `send_turn`** 启动：引擎 history 镜像空=首启、非空=冷续跑），拒绝经 `ExternalHarnessMemberRuntime(require_checkpoint=True)` opt-in 标志；provider harness 未动。引擎 `_attempt_calls` 会把该错误按 `retries` 重试后转 `AGENT_FAILED` | 团队对话语义不变 |
| 5 | B-R4 拦 `UsageUpdatedEvent`、`ConversationResetMessage` 映射 | 来源为终态 `TurnResult.usage`（Codex 累加器按轮新建=本轮量；Claude 按会话累计差分，总量回落视为重置）。**Claude 冷续跑首轮可能重复计费**（重建驱动从 0 起算），修法是随指针保存基线，待 C5-2 实测定案 | 预算最坏多记一轮 |
| 6 | B-R6 schema 能力装配期检测 | 两家 SDK 均走"提示词追加 JSON 指令 + 轮末解析（整段/围栏/最后平衡对象）"，不做能力探测；解析失败 = native 未提交语义 | 无 |
| 7 | B-R5 凭据三元组随子会话 checkpoint 落盘；Claude `resume_session_at + fork_session=True`；"最后一条转写条目 UUID" | `capture_fork` 只记凭据 `{provider, parent_pointer, mode, cut_marker, keep_rounds}`（**不落 journal**，续跑时脚本重跑 `fork()` 重算）；子会话首轮 `reconcile` → 物化（Claude **离线** `fork_session(up_to_message_id=<AssistantMessage.uuid 转写 uuid，非 API message_id>)`；Codex 底层 `thread/fork(lastTurnId)` 短命客户端，带 `entry.env`）→ **物化出的子指针**经 `seed_checkpoint` 写入子成员自己的 checkpoint（provider checkpoint 作用域按 `agent_id`，父指针不能给子用）；子会话已持有指针则直接续接不再 fork；`keep_rounds` 越界**报错不回退 full**；轮标记经运行时成员子会话持久化（`swarmflow_external_turns`） | `HarnessCard.fork_modes` 落地，无 `harness.fork_session` 方法 |
| 8 | §5.4 `cwd` 指向自管 worktree | 仅 `options.isolation="worktree"` 时取 `SwarmflowWorkerWorktrees` 路径（与 native opt-in 语义对齐），否则声明 `cwd`、再否则 `build_context.project_dir` | 无 |
| 9 | B12 model 实值"装配期解析" | 引擎在调 backend 前发事件，新增 `AgentBackend.describe_model(opts)` 钩子：三方返回 `opts.model` → 声明条目模型 → `agent_type` 名；native 返回 None 保持原 hint | 无 |
| 10 | §4.6 成员名 `{run_prefix}-{slug}-{...}` | `{run_prefix}-sess-{slug}-{n}` / `-human-`，计数器保留（同 run 内同 label 多会话区分；程序序一致故续跑 `n` 一致），`agent_session` 记忆按 run 隔离生效 | 声明式 BREAKING 如 §6 |
| 11 | Web 前端 B12 | `workflowAgentIdentity()` native 返回 `{}`（切断名册回落，避免 label 与成员 id 撞名时 AI 节点顶真人脸）；三方开关入口是**设置 → 实验功能 → 三方 Agent（仅集群模式）** | 无 |
| 12 | B-R1 声明集来源 `TeamAgentSpec.external_cli_agents` | 落地时误从运行时 `ctx.team_spec`（`TeamSpec`，无此字段）读取，所有开 swarmflow 的 Leader 启动即 `AttributeError`；单测用 `SimpleNamespace` 伪造 ctx 未暴露。已改为读蓝图 `spec`（agent-core `204e0498c`），新增用例断言 `TeamSpec.model_fields` 不含该键 | 缺陷，已修 |
| 13 | B3 "模型 resolver 出口分流 + 协议硬闸"（§4.1）、B12 model 实值 | 落地只读 `entry.external_model_config`（jiuwenswarm 条目永远无模型），未接模型池也无兜底，CLI 全靠原生登录；`describe_model` 末位以 `agent_type` 字符串冒充模型名。实测 Codex 节点 401。v5 以 B-R9 / B-R10 / B-R11 补齐（§5.8 定稿）——**2026-10-08 已实现**（agent-core F_116 三提交 + jiuwenswarm 三提交，OpenSpec 变更已归档） | 已实现 |

### 12.2 待办 / 未验证

- jiuwenswarm `pyproject.toml` 钉 `openjiuwen@d0bd8335`（早于本特性）：升 pin 前 `agent_type` 被旧引擎**静默忽略**（按 native 跑，不报错），Web 头像全为 native 插画；指南已加引擎版本门槛说明。
- C5 实测（人工，交付前）：Codex 跨进程 thread 可寻址性（失败 → `CodexHarness.card.fork_modes` 置空）；Claude fork 后转写落盘窗口；token 口径系数 + Claude 冷续跑计费基线。
- 无专属测试的场景（行为由既有机制保证）：`tool_search` 授权同 run 续跑读回、fork 子会话不继承授权、`agent_session` 同 run 续跑端到端记忆读回；Web 视觉验证（tasks 5.6）待 pin 升级。
- A9（tool_call 名单前端展示）按 §10 延后。
- **v5 已实现**（2026-10-08，见上落地记录）；遗留：9.1 人工实测（codex 401 文案 Web 可读性、`builtin_model` 实跑、会话换模型拒绝对真实 CLI）。
**v5/v6 模型选择落地记录（2026-10-08）**：agent-core `feat/test/docs(swarm)` 三提交（F_116_swarmflow-thirdparty-model-source、S_18/S_27 修订）+ jiuwenswarm 三提交（workflow_state 覆盖 + 双语指南）；OpenSpec `swarmflow-thirdparty-model-source` 已验证归档（主规范新建 + web-node-avatar 修订同步）。落地偏差：①`tool_member` / `team_worker_backend` 对 `model_compat` / `plan_external_model` 采用惰性导入（`external/__init__` 与 `schema.events` 循环依赖，顶层导入实测死锁）；②不可重试 `BackendError` 走 fail-fast 失败结果而非 re-raise（`parallel()` 分支会吞异常、直呼路径会炸整 run，终审修复）；③会话首锁身份按**裸 hint** 比较（引擎层无解析上下文，plan 路径休眠）——同 hint 必同解析故无假 no-op，代价是隐式/显式默认 `effort` 视为不同身份；④认证文案去掉 `external member 'X' failed:` 前缀（事件发射层重加 label，agent_id 归位）；⑤无 resolver 的 native 构造（仅测试/非 swarmflow）保持静默回退；⑥全部 deferred Minor 记录于 F_116 遗留（首锁 turn-1 失败砖死会话、getattr 守卫、`_backfill_terminal` failed 分支无 model 回填等）。

**v6 A9/A10 落地记录（2026-10-08）**：agent-core 三提交（`feat c35a09cab` 结构化 `AGENT_ACTIVITY` + 不节流双钩 rail + 会话覆盖 + 三方 `bind_activity_sink` 映射 + 共享 emitter 工厂；F_112 原地修订；857 用例）+ jiuwenswarm 三提交（`feat 75894e383` 状态机 `agent_activity` 消费 + 300ms 合批 + 镜像到既有 `chat.tool_call`/`chat.tool_result` 链 + 前端三面 run 分组 + 节点详情活动列表与行内工具名提示；341 + 6/6 用例）；OpenSpec `swarmflow-worker-activity` 已验证归档（两份主规范新建）。落地偏差：①镜像通道从新事件类型改为既有 `chat.tool_call` 帧链（评审裁定：新类型前端无消费者，违背"前端零改动"）；②树面渲染（节点详情活动列表 + 行内提示）在终审发现缺失后补齐折入；③`tool_result` 无名丢弃守卫按规格补齐；④12+marker 截断口径与 jiuwenswarm 任务卡片惯例一致。遗留收口（2026-10-08）：快照重放已解决刷新丢失（jiuwenswarm `40490f80e`/`fbb24a433`——快照广播从 checkpointed run states 重放镜像成员事件，跨 DB/phase 去重，含已结束 run，指南同步）；首锁 turn-1 失败砖死会话已修（agent-core `46af22eb1`——无完成轮时允许重锁）；仍开放：三方会话无 hint 锁定值指名（他人 PR）、其余 deferred Minor（F_116/F_112 遗留）、8.1 人工实测。

- **v6 待实现**：A-R5 工具活动结构化上报（引擎 `AGENT_ACTIVITY` 扩字段 + rail 双钩去节流 + `_attempt_calls` 注入 `agent_id` 补 `agent_session` + 三方 harness 事件映射；jiuwenswarm `agent_activity` 状态机 + 300ms 合批 + `TeamMemberExecutionEvent` 镜像）与 A-R6 前端三面按 `workflow_run_id` 分组。

## 13. 参考与关联设计

- SDD-0002（有状态会话与人机交互）：`agent_session` 基础语义。
- SDD-0005（脚本定义增强与 CC 工具对齐）：options 袋与工具面治理。
- SDD-0007（worker 去团队成员化）：worker 基 spec 装配管道。
- SDD-0014（有状态会话 fork）：native fork 五模式与双源捕获。
- SDD-0016（三方 Agent 可靠性）：外部成员底座与重试。
- SDD-0020（options 参数治理）：`agent_type` 与 model/timeout 同袋治理。
