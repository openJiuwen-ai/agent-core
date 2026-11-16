---
id: D-20261110-1903-team-goal-mode
title: Team Leader 与成员 Goal 模式设计方案
slug: team-goal-mode
status: in-development
stage: active
author: alan_cheng
author_email: chengshuo5@huawei.com
reviewers: []
created: 2026-11-10
updated: 2026-11-10
domain: harness
modules: [agent_teams, harness]
tags: [goal, leader, teammate, task, native-harness, completion]
scope: NativeHarness 的 goal round 驱动、成员任务与 goal 的绑定、提交闸、Leader 完成判定闸、TeamAgentSpec 配置面
related: [D-20261110-1839-leader-external-harness]
approved-date: 2026-11-10
type: D
files: []
---

# Team Leader 与成员 Goal 模式设计方案

## 1. 设计定位与阅读说明

单 agent Harness 已有 Goal 模式（持久目标 + 轮末评估 + 未完成自动续跑），Team 场景没有。本文定义
Leader 与成员获得 Goal 能力的方案：harness 层如何驱动 goal round、成员 goal 如何跟随任务、Leader goal
如何卡住团队完成判定。先读 §4 方案概览与关键决策，再按需读 §5。管理字段见 frontmatter；实施结果归档到
features。

## 2. 背景、目标与范围

### 2.1 背景与问题

已核实的事实（基于 `codex/upstream-develop` 当前代码）：

- `GoalManager` 只在 `DeepAgent.start()` 里构造。`NativeHarness.start()` 重写了启动流程并用自己的
  supervisor 驱动 round，不调用父类 `start()`，所以 team 成员的 `goal_manager` 恒为 `None`，
  `openjiuwen/agent_teams/` 下没有任何 goal 引用。Goal 在 team 成员身上是死的。
- `NativeHarness` 继承了 `DeepAgent.__init__` 建好的 `_event_manager`（`EventManager`，含 goal 队列）
  与 `_interaction_control_lock`；`submit_round` 已支持 `run_kind` / `run_context` 并一路透传到
  `TaskCompletionRail`。goal round 的评估回路（`<goal_task>` 替换、报告消费、`apply_assessment`）全部
  在 `TaskCompletionRail`，不依赖 `DeepAgent` 的交互循环。
- `NativeHarness._settle_round_done` 的收尾优先级是：follow-up > task-plan 续跑 > IDLE。没有"目标未
  完成则续跑"这一档。
- 任务进入 `IN_PROGRESS` 有三个入口：成员 `claim_task`（autonomous，自领或认领已指派任务，都走
  `TeamTaskManager.claim`）、调度器 `start_task`（scheduled，发 `TaskStartedEvent`）、leader
  `approve_plan`（plan_mode，`PLANNING → IN_PROGRESS`）。后两个发生在 leader 进程，成员经事件得知。
- 团队完成判定 `TeamBackend.is_team_completed` 只看三件事：任务全终态、成员全 settled、无未读消息。
  它不知道用户最初要的是什么——任务板清空不等于用户目标达成。

问题：成员可以在任务没做完时结束本轮并进入 IDLE，只能等超时 nudge；成员可以对没做完的任务调用完成；
Leader 可以在任务板清空后直接让团队收尾，即使拆出来的任务没有覆盖用户目标。

### 2.2 目标与关键场景

| 场景 / 触发条件 | 当前行为 | 目标行为 / 可观察变化 |
| --- | --- | --- |
| 成员 `claim_task(claimed)` 成功 | 任务进 `IN_PROGRESS`，无后续约束 | 同时为该成员立 goal，objective 为任务定义 |
| scheduled 成员被调度器 `start_task` | 同上 | 成员收到 `TASK_STARTED` 时立 goal |
| plan_mode 成员的计划被批准 | 同上 | 成员收到批准决定时立 goal |
| 成员一轮结束，任务仍 `IN_PROGRESS` | 进入 IDLE，等 nudge | 不进 IDLE，起 goal round 续跑 |
| 成员提交完成（`claim_task(completed)` / `member_complete_task`） | 直接落 `COMPLETED` / `IN_REVIEW` | 先过评估；不通过则工具调用被拒并返回剩余工作 |
| 成员确实卡住（等人回复、缺权限） | 进入 IDLE | goal 转 `BLOCKED`，不再自动续跑，上报 leader；下一条入站输入后恢复 |
| 任务被改派 / 取消 | 成员收到事件 | 同时清除 goal |
| 评审打回 | 任务回 `IN_PROGRESS` | goal 重新激活 |
| 全员静止且任务全终态，Leader goal 仍 ACTIVE | 发 `team.completed` | 不发；起 Leader goal round 对照原始目标核对 |
| Leader goal 评估为完成 | — | 放行 `team.completed` |
| 续跑触顶（`max_attempts` / `token_budget`） | — | goal 转 `BLOCKED`，流上出现 `team.goal.blocked` 标记 |
| 未开启任何 goal 配置 | 现状 | 行为零变化 |

### 2.3 本次范围与非目标

- 本次包含：`NativeHarness` goal round 驱动；成员 goal 的三个立项入口、提交闸、清除与重激活；Leader
  goal 与完成判定闸；`TeamAgentSpec` / `LeaderSpec` / 成员 options 配置面；`spawn_teammate` 的 `goal`
  参数；单测。
- 本次不包含：跑在三方 Harness 上的成员与 Leader（`ExternalHarnessMemberRuntime`，没有
  `NativeHarness`）；human_agent / passive_human / bridge 成员；swarmflow worker；面向宿主的显式
  goal 管理 facade（set / pause / resume / clear）；单 agent `DeepAgent` 既有 goal 行为的任何改动。

### 2.4 术语与上下文

- goal round：`run_kind="goal"` 的一轮，query 被替换成 `<goal_task>`，轮末由 `TaskCompletionRail` 评估。
- 工作项：goal 所跟随的那份工作。成员的工作项是"我名下 `IN_PROGRESS` 的任务"，Leader 的工作项是
  "本次用户请求"。
- 提交闸：成员调用完成工具时、状态落库之前的评估拦截。

## 3. 设计约束、假设与待决问题

- 已确认约束与依赖：
  - goal 状态独占 `GoalManager`（harness `S_11` 不变量 1），team 侧只经其公开方法读写。
  - `NativeHarness` 不依赖 `TeamAgent`（agent_teams harness 铁律 1）：goal round 驱动是 harness 通用
    能力，team 语义经注入的回调进入。
  - coordinator 不做决策（agent_teams 架构铁律 3）：完成闸只增加一个只读条件，不在 loop 里放业务逻辑。
  - 任务状态以任务板为唯一真相；`GoalRecord` 只是驱动器。
  - 新配置一律走 `TeamAgentSpec`。
- 关键假设：
  - `GoalManager` 依赖的 `has_output_stream` / `cancel_active_round` / `emit_event` / `notify_work`
    四个回调可以由 `NativeHarness` 用自己的 supervisor 语义提供。开发第一步核实；不成立则在
    `harness/goal/` 增加一个不依赖交互循环的最小驱动接口，方案其余部分不变。
  - `TaskCompletionRail` 可以在 `NativeHarness` 上按需注册并拿到 `goal_manager`。同上，第一步核实。
- 待决问题：无。

## 4. 总体方案与设计取舍

### 4.1 方案概览

三层，自下而上：

1. **harness 层**：`NativeHarness` 在 goal 开启时构造 `GoalManager`（store 为成员自己 session 上的
   `SessionGoalStore`）并注册 `TaskCompletionRail`。`_settle_round_done` 的优先级变成
   follow-up > **goal 续跑** > task-plan 续跑 > IDLE。是否可以起 goal round 由一个注入的
   `goal_ready()` 谓词决定，默认恒真；另有公开的 `nudge_goal()` 让外部在 IDLE 时请求重查。
2. **成员层**：`TeamGoalRail` 负责工具侧的两件事——`claim_task(claimed)` 成功后立 goal、完成工具调用
   前过提交闸；`TaskBoardHandler` 负责事件侧——`TASK_STARTED`、计划批准、评审打回时立 goal，改派 /
   取消时清除。两侧都落到同一个绑定函数。
3. **Leader 层**：首条用户输入立 goal；`goal_ready()` 注入为"团队静止"；`TeamCompletionHandler` 在
   三个完成条件之外再要求 Leader goal 不是 ACTIVE，条件成立但 goal 仍 ACTIVE 时调 `nudge_goal()`。

成员与 Leader 在 harness 层走同一条代码路径，差别只在注入的 `goal_ready`。

### 4.2 关键决策

| 决策 | 选择 | 依据 |
| --- | --- | --- |
| goal 状态与评估 | 复用 `GoalManager` / `GoalEvaluator` / `TaskCompletionRail`，不在 team 侧另写 | 状态独占是既有不变量；另写一套就是两个真相源 |
| goal 跟随什么 | 跟随工作项（任务 / 用户请求） | 立项、清除、重激活全部由工作项的状态变化推出，不需要额外的 goal 生命周期 API |
| 成员完成的唯一入口 | 完成工具（`claim_task(completed)` / `member_complete_task`），闸在其前 | 任务板是真相；先落库再评估就得回滚已解除的依赖 |
| 轮末评估判 COMPLETE 但任务未提交 | 按 CONTINUE 处理，下一轮指令为"提交任务" | 不让 goal 状态先于任务状态完成 |
| 卡住的表达 | `BLOCKED` = "下一条外部输入到来前不自动续跑" | 入站消息本来就会起新一轮，轮后重新评估，不需要专门的 resume 入口 |
| Leader 何时续跑 | 团队静止时，而不是 Leader 一轮结束时 | Leader 空闲而成员在干活是常态，此时续跑只会空转 |
| 立项入口 | `claim_task` 走工具侧 rail；`start_task` / 计划批准走成员侧事件 | 前者发生在成员进程内、可同步完成；后两者发生在 leader 进程，成员只能经事件得知 |
| 续跑上限 | team 侧默认有界（`max_attempts=10`） | 无上限续跑是真实的 token 风险 |

### 4.3 替代方案与排除理由

| 方案 | 排除理由 |
| --- | --- |
| 让 `NativeHarness` 调 `DeepAgent.start()` 以获得 goal | 会同时启动 DeepAgent 的交互 supervisor 与输出 forwarder，和 NativeHarness 自己的 supervisor 形成两套 round 驱动 |
| team 侧自建 goal 状态机，不用 `GoalManager` | 重复实现 attempt 记账、预算、评估与持久化，并违反状态独占 |
| 把立项逻辑写进 `ClaimTaskTool.invoke` | 工具被外部 CLI 成员经 MCP 复用，它们没有 harness；且工具要保持"`invoke` 直线、零角色分支" |
| 三个入口统一成成员侧事件驱动 | 成员收不到自己发的 `TASK_CLAIMED`（自发事件被过滤），且用户明确要求由 claim 工具触发 |
| 不设提交闸，只做续跑 | 只防"提前停下"，不防"谎报完成"，达不到"确保真实完成" |
| 先落 `COMPLETED` 再评估、失败回滚 | 依赖已解除、下游可能已开工，回滚不可靠 |
| Leader 每轮结束即评估续跑 | 成员在干活时 Leader 会被反复驱动 |
| 复用 verify 闸（reviewer）代替 goal | reviewer 是同伴评审，只在 scheduled 下可达，不解决成员提前停下的问题；两者正交 |

## 5. 详细设计

### 5.1 模块责任与关键不变量

| 模块 | 责任 |
| --- | --- |
| `harness/goal/` | 不变。`GoalManager` 仍是 `GoalRecord` 的唯一写入者 |
| `agent_teams/harness/native_harness.py` | 构造 `GoalManager`、消费 goal 队列、在收尾优先级里插入 goal 续跑、提供 `goal_ready` 注入点与 `nudge_goal()` |
| `agent_teams/rails/team_goal_rail.py`（新） | 工具侧：claim 成功后立 goal；完成工具前过提交闸 |
| `agent_teams/agent/coordination/handlers/task_board.py` | 事件侧：开工 / 批准 / 打回立 goal，改派 / 取消清除 |
| `agent_teams/agent/coordination/handlers/team_completion.py` | 完成闸：Leader goal ACTIVE 时不发 `TEAM_COMPLETED`，改为 `nudge_goal()` |
| `agent_teams/schema/` + `tools/member_options.py` | 配置面与成员级开关持久化 |

不变量：

1. 成员 goal 为 ACTIVE ⟹ 该成员名下存在 `IN_PROGRESS` 任务，且 goal 绑定的就是它。
2. 任务到达 `COMPLETED` / `IN_REVIEW`（经 goal 成员之手）⟹ 提交闸已放行。
3. `TEAM_COMPLETED` 发出 ⟹ Leader goal 不是 ACTIVE（未开启时视为满足）。
4. 外部输入优先于 goal 续跑：有排队 follow-up 时不起 goal round。
5. goal 关闭时，所有新增代码路径不可达。

### 5.2 关键类与函数设计

- `NativeHarness`
  - 构造参数 `goal: GoalStopConfig | None`、`goal_ready: Callable[[], Awaitable[bool]] | None`。
  - `start()`：`goal` 非空时构造 `GoalManager` 并注册 / 绑定 `TaskCompletionRail`。
  - `_settle_round_done()`：follow-up 之后、task-plan 之前调用 `_start_goal_round_if_ready()`。
  - `nudge_goal()`：IDLE 时经 supervisor 命令队列触发同一个检查。
  - `_start_round(..., run_kind, run_context)`：把 goal 上下文传给 `submit_round`。
- `TeamGoalBinding`（`agent_teams/agent/` 下的小对象，成员与 Leader 各持一个）
  - `bind_task(task_id)`：读任务，确认 `IN_PROGRESS` 且归我，渲染 objective，`goal_manager.set(...,
    overwrite_confirmed=True)`；同一任务重复绑定为幂等。
  - `release(task_id)`：goal 绑定的是该任务时 `goal_manager.clear()`。
  - `bind_request(text)`：Leader 用，无 ACTIVE goal 时立项。
  - 绑定的任务 id 存在成员 session state，与 `GoalRecord` 同生命周期。
- `TeamGoalRail`
  - `after_tool_call`：`claim_task` 且 `status=claimed` 且成功 → `bind_task`。
  - `before_tool_call`：完成工具且目标任务就是 goal 任务 → 评估；不通过则置 `_skip_tool` 并返回
    含 `remaining_work` 的错误文本，同时 `apply_assessment(CONTINUE)`；通过则放行，工具成功后
    `apply_assessment(COMPLETE)`。

### 5.3 接口定义与消息契约

- 工具 schema：`claim_task` / `member_complete_task` 不变。`spawn_teammate` 在团队开启成员 goal 时多一个
  可选布尔属性 `goal`（属性级门控，与 `fork` 同一模式）。
- 提交闸拒绝文本：一句"任务尚未达成完成条件" + 评估给出的证据与剩余工作 + "完成后重新提交"。
- 流标记：新增 `team.goal.blocked`（`TeamOutputSchema`，不关流），payload 含成员名、原因。
- 成员 goal 触顶上报：一条模板消息发给 leader，文案放 `prompts/<lang>/`。

### 5.4 配置与能力控制

```python
class TeamGoalSpec(BaseModel):
    strategy: Literal["agent_report", "transcript", "hybrid"] = "hybrid"
    max_attempts: int | None = 10
    token_budget: int | None = None

LeaderSpec.goal: TeamGoalSpec | None = None            # Leader 开关
TeamAgentSpec.member_goal: TeamGoalSpec | None = None  # 成员能力上限 + 默认参数
TeamMemberSpec.goal: bool | None = None                # 预定义成员；None = 跟随 member_goal 是否配置
spawn_teammate(goal: bool)                             # 动态成员，落 TeamMemberOptions.goal
```

- `member_goal is None` 时成员级开关不可用：`spawn_teammate` 不暴露 `goal`，偷传被拒。
- `strategy="agent_report"` 时提交闸直接放行（相当于关闭闸）。
- 校验：`leader.goal` 与 `leader.cli_agent` 同时配置时 `build()` 前拒绝。

### 5.5 数据模型与事实归属

| 事实 | 归属 |
| --- | --- |
| 任务是否完成 | 任务板（DB） |
| goal 状态、attempt、token 用量、上次评估 | `GoalRecord`，存成员 session state |
| goal 绑定的任务 id | 成员 session state |
| 成员是否开启 goal | `TeamMember.options.goal`（动态）/ spec（预定义） |

### 5.6 状态机与生命周期

成员 goal：

```text
(无) ──claim / start / 计划批准──▶ ACTIVE ──提交闸放行──▶ COMPLETED
                                   │  ▲
                 评估 blocked / 触顶 │  │ 入站输入后的一轮结束
                                   ▼  │
                                 BLOCKED
ACTIVE / BLOCKED ──改派 / 取消──▶ (清除)
COMPLETED ──评审打回──▶ ACTIVE（重新立项，带 feedback）
```

Leader goal：首条用户输入 → ACTIVE；评估 COMPLETE → COMPLETED；触顶 / blocked → BLOCKED；下一条用户
输入恢复。

### 5.7 正常执行流程

成员（autonomous）：读看板 → `claim_task(claimed)` → rail 立 goal → 同一轮继续干 →
（a）本轮内提交：闸评估 → 放行 → 任务完成、goal 完成；
（b）本轮结束未提交：无 follow-up → goal round → 轮末评估 → 继续 / 卡住。

Leader：用户输入 → 立 goal → 建队、拆任务 → 一轮结束进 IDLE → 成员工作 → 全员静止 →
完成条件成立且 goal ACTIVE → `nudge_goal()` → goal round（对照目标核对任务板，补任务或提交完成报告）→
评估 COMPLETE → 下一次完成检查放行 `team.completed`。

### 5.8 异常、中断与重试处理

- goal round 抛错：沿用 `TaskCompletionRail`，goal 转 `BLOCKED`。
- 提交闸的评估模型调用失败：按"无可靠评估"处理为放行，并记日志。闸的目的是拦谎报，不能因为评估器
  故障把任务永久卡在 `IN_PROGRESS`。
- pause / abort：goal round 与普通 round 同等对待；abort 丢弃当前轮但不改 `GoalRecord`，下一次收尾
  重新判定。
- 成员 `SHUTDOWN_REQUESTED`：不再起 goal round。
- 绑定时任务已不在 `IN_PROGRESS` 或不归我：不立项。

### 5.9 并发与一致性

`GoalManager` 的全部写入持 `_interaction_control_lock`。goal round 的启动只发生在 supervisor 协程内
（收尾路径与 `nudge_goal()` 命令都在其中处理），与 `send` / `abort` / `pause` 串行。事件侧的
`bind_task` / `release` 从 coordination 协程调用，只经 `GoalManager` 公开方法写入，不直接碰 supervisor
状态。

### 5.10 持久化与恢复

`GoalRecord` 与绑定任务 id 都在成员自己的 session state，随 checkpoint 持久化。冷恢复后 harness 启动时
读回；绑定的任务若已不在 `IN_PROGRESS` 或不归我则清除，否则 goal 保持并在首次收尾时照常判定。

## 6. 影响面与实施设计

### 6.1 修改点与实施顺序

1. 核实 §3 两条假设。
2. `agent_teams/harness/native_harness.py`：goal round 驱动。
3. `TeamGoalBinding` + `TeamGoalRail` + rail 的 manifest 声明与装配。
4. `task_board.py` 事件侧绑定 / 清除。
5. Leader：立项、`goal_ready`、`team_completion.py` 完成闸、`team.goal.blocked` 标记。
6. 配置面：`TeamGoalSpec`、`LeaderSpec.goal`、`member_goal`、成员 options、`spawn_teammate` 属性门控。
7. 提示词 / 工具描述片段与 i18n。
8. 单测。

### 6.2 文档同步

- features：新增一份 `F_YYYYMMDD_HHMM_team-goal-mode.md`（实施时按当时的新加坡时间命名）。
- specs：`agent_teams/S_08`（工具契约）、`S_12`（schema）、`S_18`（harness 交互契约）、`S_03`（协调
  协议 / 完成判定）、`S_09`（rails）；harness `S_11` 补一句 NativeHarness 也是 `GoalManager` 的宿主。
- AGENTS.md：`agent_teams/AGENTS.md`、`agent_teams/harness/AGENTS.md`、`tools/AGENTS.md`、`rails` 段落。

## 7. 兼容与迁移

全部新增字段默认关闭，关闭时新增路径不可达。`TeamMemberOptions` 是 `extra="allow"` 的 JSON blob，
新增 `goal` 键无需 DB 迁移。无公开 API 的破坏性变化。

## 8. 验证设计与通过条件

### 8.1 验证设计与通过条件

| 场景 | 验证方式 | 通过条件 |
| --- | --- | --- |
| goal 关闭 | 既有 agent_teams / harness 相关单测 | 无回归 |
| harness goal 续跑 | NativeHarness 单测，假模型 | ACTIVE goal 下收尾起 `run_kind="goal"` 轮；有 follow-up 时先跑 follow-up；BLOCKED / COMPLETED 后进 IDLE |
| 触顶 | 同上 | 达到 `max_attempts` 后 goal 为 BLOCKED 且不再起轮 |
| claim 立项 | rail 单测 + 内存后端 | `claim_task(claimed)` 成功后 `GoalRecord` 存在且 objective 含任务标题与内容 |
| scheduled / plan 立项 | handler 单测 | `TASK_STARTED` / 计划批准事件后同上 |
| 提交闸 | rail 单测 | 评估不通过时任务仍 `IN_PROGRESS`、工具结果含剩余工作；通过时任务落终态、goal COMPLETED |
| 改派 / 取消 / 打回 | handler 单测 | goal 被清除 / 重新 ACTIVE |
| Leader 完成闸 | completion handler 单测 | 三条件成立且 goal ACTIVE 时不发 `TEAM_COMPLETED` 并触发 nudge；goal COMPLETED 后发出 |
| 配置校验 | spec 单测 | `leader.goal` + `leader.cli_agent` 被拒；`member_goal` 未配时 `spawn_teammate` 无 `goal` 属性 |
| 恢复 | 单测 | 重建 harness 后 ACTIVE goal 仍驱动续跑；任务已不归我时被清除 |

## 9. 设计风险与已知限制

### 9.1 设计风险

| 风险 / 触发条件 | 对本次方案的影响 | 已有应对措施 | 核实或验证方式 |
| --- | --- | --- | --- |
| `GoalManager` 的回调与 NativeHarness supervisor 语义对不上 | harness 层复用方式 | 退路见 §3 假设 | 实施第一步核实 |
| 评估器误判导致成员反复被拒 | 成员卡在提交 | `max_attempts` 触顶转 BLOCKED 并上报 leader | 触顶单测 |
| goal 续跑与既有 stale / stall nudge 重复催促 | 成员收到两类催促 | goal 成员只有 BLOCKED 时才会 idle，nudge 仍是该状态下的兜底 | 单测覆盖 BLOCKED 后 idle |

### 9.2 已知设计限制

- 三方 Harness 成员与 Leader 不支持。
- 提交闸每次提交多一次评估模型调用（`agent_report` 策略除外）。
- Leader objective 取首条用户输入，goal 进行中的后续输入不改写 objective。

## 10. 按日期记录的评审与设计修订

| 日期 | 评审人 / 确认方 | 评审或修订主题 | 结论 | 修改与依据 |
| --- | --- | --- | --- | --- |
| 2026-11-10 | 用户（会话内确认） | 初版方案：三层结构、提交闸、Leader objective 取首条输入、三方 Harness 不支持 | 按推荐方案通过 | 用户回复"其他按推荐方案" |
| 2026-11-10 | 用户（会话内确认） | 立项入口范围 | 调度模式与 Plan 模式也要支持 | 初版建议只做 autonomous + build_mode；按用户要求扩为三个入口，§4.2 / §5.2 / §5.6 相应设计 |

## 11. 参考与交接

- 关联设计、架构和 spec：harness `S_11_goal-and-evaluation`、`S_04_rails-contract`；agent_teams
  `S_08` / `S_12` / `S_18` / `S_03`；`D-20261110-1839-leader-external-harness`（Leader 跑三方 Harness 时
  本特性不可用）。
- 开发交接：范围见 §2.3；关键决策见 §4.2；无待决事项；验收场景见 §8.1。
- 关联实施归档：尚无。
