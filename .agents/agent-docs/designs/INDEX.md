<!-- 本文件由 .agents/skills/jw-design/scripts/generate_index.py 自动生成，请勿手改。重新生成：python .agents/skills/jw-design/scripts/generate_index.py -->
<!-- generated-at: 2026-10-25T20:11+08:00 -->

# 设计文档索引 (INDEX)

> 开发前 14 · 开发中 1 · 开发后 15 · 已否决 0

按模块目录分组，组内按生命周期展示；跨模块设计在各相关模块下列出，全局统计按文档去重。

## 模块目录

| 模块 | 设计数 |
|---|---|
| [agent_evolving](#module-1) | 1 |
| [agent_teams](#module-2) | 8 |
| [agent_teams/workflow](#module-3) | 18 |
| [extensions/observability](#module-4) | 2 |
| [harness](#module-5) | 3 |
| [jiuwenswarm](#module-6) | 3 |
| [jiuwenswarm/channels/web/frontend](#module-7) | 1 |
| [jiuwenswarm/gateway/channel_manager/web](#module-8) | 1 |
| [jiuwenswarm/observability](#module-9) | 1 |
| [未分类](#module-10) | 1 |

<a id="module-1"></a>

## agent_evolving

### 开发前 (proposals)

| ID | 标题 | 状态 | 负责人 | 领域 | 更新日期 |
|---|---|---|---|---|---|
| [SDD-0024](proposals/SDD-0024-trajectory-projection-evolution-rl/design.md) | 轨迹投影：把 OTel 标准轨迹投影为自演进与在线 AgentRL 输入 | draft | @alan_cheng | observability | 2026-09-15 |

<a id="module-2"></a>

## agent_teams

### 开发前 (proposals)

| ID | 标题 | 状态 | 负责人 | 领域 | 更新日期 |
|---|---|---|---|---|---|
| [SDD-0015](proposals/SDD-0015-agent-team-scheduled-dispatch/design.md) | AgentTeam 调度模式：任务图驱动的低通信协同与验证闭环 | draft | @alan_cheng | scheduler | 2026-08-20 |
| [SDD-0019](proposals/SDD-0019-team-model-group/design.md) | Team 模式模型组接入与成员模型分配 | draft | @<huanghao> | core | 2026-09-14 |
| [SDD-0022](proposals/SDD-0022-trajectory-observability-framework/design.md) | 轨迹观测框架：基于 OTel GenAI 语义约定的 Harness 与 Team 轨迹采集和处理 | draft | @alan_cheng | observability | 2026-09-15 |
| [SDD-0029](proposals/SDD-0029-team-session-runtime-integration/design.md) | Team 模式接入统一 Session Runtime | draft | @zhanglongmin | core | 2026-10-09 |
| [SDD-0030](proposals/SDD-0030-swarmflow-agent-group-expert-worker/design.md) | Swarmflow 专家 worker：agent_template 算子参数复用专家团成员 | draft | @chenchunzhou | core | 2026-10-15 |

### 开发后 (archive)

| ID | 标题 | 状态 | 负责人 | 领域 | 更新日期 |
|---|---|---|---|---|---|
| [SDD-0011](archive/SDD-0011-evolvable-team-workspace/design.md) | 可演进 Team 工作区 | shipped | @xiajing | core | 2026-08-20 |
| [SDD-0016](archive/SDD-0016-third-party-agent-reliability/design.md) | 三方 Agent 可靠性设计 | shipped | @zhanglongmin | core | 2026-08-31 |
| [SDD-0018](archive/SDD-0018-swarmflow-team-runtime-binding/design.md) | Swarmflow 启停与 Team 生命周期联动（小飞机控灯 / 恢复裁决 / 冷启动续跑） | shipped | @chenchunzhou | core | 2026-09-18 |

<a id="module-3"></a>

## agent_teams/workflow

### 开发前 (proposals)

| ID | 标题 | 状态 | 负责人 | 领域 | 更新日期 |
|---|---|---|---|---|---|
| [SDD-0012](proposals/SDD-0012-swarmflow-dual-layer-budget/design.md) | Swarmflow 双层 Budget（workflow 级 + session 级）撞顶逻辑 | draft | @wangcan103 | scheduler | 2026-08-18 |
| [SDD-0017](proposals/SDD-0017-swarmflow-verify-primitive/design.md) | Swarmflow 验证原语 verify()：把任务验收机制抽象为可脚本调用的原语 | draft | @sodawlh | core | 2026-08-26 |
| [SDD-0020](proposals/SDD-0020-swarmflow-operator-options/design.md) | Swarmflow 算子 options 参数治理（model 前置校验 + timeout 可观测性） | draft | @wangcan103 | core | 2026-09-28 |
| [SDD-0021](proposals/SDD-0021-swarmflow-toolsearch-and-thirdparty-agent/design.md) | Swarmflow 渐进式工具暴露开关与三方 Agent 接入（tool_search + agent_type） | draft | @chenchunzhou | core | 2026-09-29 |
| [SDD-0030](proposals/SDD-0030-swarmflow-agent-group-expert-worker/design.md) | Swarmflow 专家 worker：agent_template 算子参数复用专家团成员 | draft | @chenchunzhou | core | 2026-10-15 |

### 开发中 (active)

| ID | 标题 | 状态 | 负责人 | 领域 | 更新日期 |
|---|---|---|---|---|---|
| [SDD-0014](active/SDD-0014-swarmflow-session-fork/design.md) | SwarmFlow 有状态会话 fork（agent_session 上下文继承） | in-development | @sodawlh | core | 2026-08-21 |

### 开发后 (archive)

| ID | 标题 | 状态 | 负责人 | 领域 | 更新日期 |
|---|---|---|---|---|---|
| [SDD-0002](archive/SDD-0002-swarmflow-stateful-sessions-and-human/design.md) | SwarmFlow 有状态会话与人机交互编排 | shipped | @alan_cheng | core | 2026-06-24 |
| [SDD-0003](archive/SDD-0003-swarmflow-interruption-recovery-and-durable-state/design.md) | SwarmFlow 中断恢复与崩溃可恢复状态持久化 | shipped | @alan_cheng | core | 2026-06-24 |
| [SDD-0004](archive/SDD-0004-swarmflow-nested-workflow-and-hardening/design.md) | SwarmFlow 嵌套工作流调用与执行加固 | shipped | @alan_cheng | core | 2026-06-24 |
| [SDD-0005](archive/SDD-0005-swarmflow-script-enhancement-and-cc-alignment/design.md) | SwarmFlow 脚本定义增强与 Claude Code 工具对齐 | shipped | @alan_cheng | tooling | 2026-06-24 |
| [SDD-0007](archive/SDD-0007-swarmflow-worker-decouple-from-team-db/design.md) | SwarmFlow Worker 去团队成员化与会话存储解耦 | shipped | @alan_cheng | core | 2026-06-24 |
| [SDD-0008](archive/SDD-0008-swarmflow-concurrency-governor/design.md) | Swarmflow 并发治理模块（ConcurrencyGovernor） | shipped | @chenchunzhou | harness | 2026-07-18 |
| [SDD-0009](archive/SDD-0009-jiuwen-tui-swarmflow-human-session-adaptation/design.md) | jiuwen TUI 适配 SwarmFlow 三算子（human / human_session / agent_session） | shipped | @chenchunzhou | core | 2026-07-28 |
| [SDD-0010](archive/SDD-0010-jiuwen-tui-nested-workflow-and-budget-observability/design.md) | jiuwen TUI 嵌套 Workflow 与 Budget 可观测性 | shipped | @chenchunzhou | observability | 2026-08-06 |
| [SDD-0013](archive/SDD-0013-swarmflow-runtime-control-intents/design.md) | Swarmflow 运行态控制意图（Early-Return / Pause-Resume / Stop） | shipped | @chenchunzhou | core | 2026-09-18 |
| [SDD-0018](archive/SDD-0018-swarmflow-team-runtime-binding/design.md) | Swarmflow 启停与 Team 生命周期联动（小飞机控灯 / 恢复裁决 / 冷启动续跑） | shipped | @chenchunzhou | core | 2026-09-18 |
| [SDD-0027](archive/SDD-0027-swarmflow-per-run-journal-wal-split/design.md) | Swarmflow Journal/WAL 按 run 拆分与 WAL 日志化治理 | shipped | @xiajing | core | 2026-09-16 |
| [SDD-0028](archive/SDD-0028-swarmflow-verify-fork-observability/design.md) | swarmflow verify / fork 可观测与前端呈现 | shipped | @xiajing | observability | 2026-09-29 |

<a id="module-4"></a>

## extensions/observability

### 开发前 (proposals)

| ID | 标题 | 状态 | 负责人 | 领域 | 更新日期 |
|---|---|---|---|---|---|
| [SDD-0022](proposals/SDD-0022-trajectory-observability-framework/design.md) | 轨迹观测框架：基于 OTel GenAI 语义约定的 Harness 与 Team 轨迹采集和处理 | draft | @alan_cheng | observability | 2026-09-15 |
| [SDD-0023](proposals/SDD-0023-trajectory-export-langfuse-otel/design.md) | 轨迹导出：Langfuse 适配与 OTel 兼容平台上报（含文件 WAL 与离线导入） | draft | @alan_cheng | observability | 2026-09-15 |

<a id="module-5"></a>

## harness

### 开发前 (proposals)

| ID | 标题 | 状态 | 负责人 | 领域 | 更新日期 |
|---|---|---|---|---|---|
| [SDD-0022](proposals/SDD-0022-trajectory-observability-framework/design.md) | 轨迹观测框架：基于 OTel GenAI 语义约定的 Harness 与 Team 轨迹采集和处理 | draft | @alan_cheng | observability | 2026-09-15 |
| [SDD-0024](proposals/SDD-0024-trajectory-projection-evolution-rl/design.md) | 轨迹投影：把 OTel 标准轨迹投影为自演进与在线 AgentRL 输入 | draft | @alan_cheng | observability | 2026-09-15 |

### 开发后 (archive)

| ID | 标题 | 状态 | 负责人 | 领域 | 更新日期 |
|---|---|---|---|---|---|
| [SDD-0006](archive/SDD-0006-native-harness-async-tool-control-and-spill/design.md) | 原生 Harness 异步工具管控框架与磁盘溢写 | shipped | @alan_cheng | harness | 2026-06-24 |

<a id="module-6"></a>

## jiuwenswarm

### 开发后 (archive)

| ID | 标题 | 状态 | 负责人 | 领域 | 更新日期 |
|---|---|---|---|---|---|
| [SDD-0009](archive/SDD-0009-jiuwen-tui-swarmflow-human-session-adaptation/design.md) | jiuwen TUI 适配 SwarmFlow 三算子（human / human_session / agent_session） | shipped | @chenchunzhou | core | 2026-07-28 |
| [SDD-0010](archive/SDD-0010-jiuwen-tui-nested-workflow-and-budget-observability/design.md) | jiuwen TUI 嵌套 Workflow 与 Budget 可观测性 | shipped | @chenchunzhou | observability | 2026-08-06 |
| [SDD-0028](archive/SDD-0028-swarmflow-verify-fork-observability/design.md) | swarmflow verify / fork 可观测与前端呈现 | shipped | @xiajing | observability | 2026-09-29 |

<a id="module-7"></a>

## jiuwenswarm/channels/web/frontend

### 开发前 (proposals)

| ID | 标题 | 状态 | 负责人 | 领域 | 更新日期 |
|---|---|---|---|---|---|
| [SDD-0026](proposals/SDD-0026-trajectory-observability-web-ui/design.md) | 轨迹观测前端：单 Agent、Subagent 与 Team 多 Agent 轨迹 UI | draft | @alan_cheng | observability | 2026-09-15 |

<a id="module-8"></a>

## jiuwenswarm/gateway/channel_manager/web

### 开发前 (proposals)

| ID | 标题 | 状态 | 负责人 | 领域 | 更新日期 |
|---|---|---|---|---|---|
| [SDD-0025](proposals/SDD-0025-trajectory-session-store-and-serving/design.md) | 轨迹数据面：会话级 SQLite 存储、实时变更提示与读取 API | draft | @alan_cheng | observability | 2026-09-15 |

<a id="module-9"></a>

## jiuwenswarm/observability

### 开发前 (proposals)

| ID | 标题 | 状态 | 负责人 | 领域 | 更新日期 |
|---|---|---|---|---|---|
| [SDD-0025](proposals/SDD-0025-trajectory-session-store-and-serving/design.md) | 轨迹数据面：会话级 SQLite 存储、实时变更提示与读取 API | draft | @alan_cheng | observability | 2026-09-15 |

<a id="module-10"></a>

## 未分类

### 开发前 (proposals)

| ID | 标题 | 状态 | 负责人 | 领域 | 更新日期 |
|---|---|---|---|---|---|
| [SDD-0001](proposals/SDD-0001-sample-design/design.md) | 示例设计文档（脚手架自带，可删除） | draft | @your-handle | core | 2026-06-15 |
