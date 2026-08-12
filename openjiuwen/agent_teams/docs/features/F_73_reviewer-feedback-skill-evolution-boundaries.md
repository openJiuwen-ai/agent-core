# Reviewer Feedback 驱动的 Skill 演进边界

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-06 |
| 范围 | `agent_teams/agent/scheduling/scheduler.py`（失败轮次与团队终态 callback）；`agent_evolving/signal/review_feedback.py`（归因与安全策略）；`agent_evolving/trajectory/registry.py`（成员独立轨迹）；`harness/rails/evolution/skill_evolution_rail.py`（外部信号入口）；`harness/rails/skills/team_skill_create_rail.py`（重复模式创建审批入口） |
| 测试基线 | 相关单测 222 passed（review feedback、trajectory registry、scheduler、SkillEvolutionRail、TeamSkillCreateRail） |

## 背景

scheduled 团队已经有完整的 Task 验收闭环：成员提交、reviewer 投票、失败返工、再次验收。但失败
feedback 过去只回到任务执行者，无法成为 Skill 演进证据。平台若要补这条链路，容易犯三个错误：

1. 把每次失败都当成 Skill 缺陷，掩盖执行者没有遵循已有指导的问题。
2. 轨迹没有读过任何 `SKILL.md` 时，凭安装列表随便挑一个 Skill 修改。
3. 直接从 scheduler 写 `evolutions.json`，让调度层同时理解 LLM、Skill 存储和审批协议。

本特性不在 Core 内规定某一种成员/全局目录布局，而是补齐五个可组合的边界：scheduler 输出审核
feedback、平台读取成员独立轨迹、独立归因器产生安全 action、普通 Skill Rail 接受已归因信号、
Team Skill 创建 Rail 接受重复模式证据。JiuwenSwarm 等宿主可以在这些边界上实现“成员自动沉淀、
团队终态汇总审批”，Core 仍保持平台无关。

## 数据流与状态

```text
review round failed
  -> TeamScheduler review_feedback_handler(payload)
  -> host loads assignee trajectory
  -> ReviewFeedbackContextBuilder
  -> ReviewFeedbackAttributor
       -> evolve_existing_skill
       -> suggest_new_skill
       -> record_task_failure
       -> skip_unattributed

all tasks terminal
  -> wait for outstanding task callbacks
  -> review_feedback_handler.on_team_completed(payload)
  -> host aggregates observations
  -> existing Skill: SkillEvolutionRail.evolve_from_external_signals(...)
  -> repeated missing capability: TeamSkillCreateRail.propose_from_external_evidence(...)
```

`TeamScheduler` 新增三组进程内状态：

- `_review_feedback_dispatched[(task_id, review_round)]`：失败轮次 callback 去重。
- `_review_feedback_tasks`：不阻塞任务返工的后台 callback 任务集合。
- `_team_review_feedback_dispatched`：团队终态 callback 去重。

这些状态不是任务事实，不持久化；任务状态、票据和 feedback 仍以 DB 为真相。

## 决策

### 1. Scheduler 只发布事实，不理解演进

宿主通过 `BuildContext.extras["review_feedback_handler"]` 注入可调用对象。每个失败轮次 settle 后，
scheduler 以后台任务调用 handler；看板全部终态时先等待当前 callback，再调用可选的
`handler.on_team_completed(...)`。callback 异常只记录日志，不改变任务状态，也不阻塞返工。

payload 只含调度器已经拥有的事实：team/session/task、review round、任务标题和正文、assignee、
聚合 reviewer feedback。它不携带 Skill 名、轨迹或演进结论。

### 2. LLM 负责语义归因，确定性策略负责安全放行

`ReviewFeedbackAttributor` 使用一次结构化 LLM 调用区分四种情况，但模型输出不能直接触发修改。
后处理策略重新校验 action：

- `skill_issue`：必须有轨迹中的 `SKILL.md` 读取证据、目标 Skill 必须属于已读集合，并提供可复用且
  指向明确 target 的指导，才能得到 `evolve_existing_skill`。
- `executor_error`：只得到 `record_task_failure`，不演进 Skill。
- `new_skill_pattern`：必须有重复证据与可复用指导，才得到 `suggest_new_skill`；模块本身不创建 Skill。
- 空输入、无效 JSON、模型异常、不可信 Skill 名或证据不足全部 fail closed 为 `skip_unattributed`。

`ReviewFeedbackContextBuilder` 只从工具调用参数证明 Skill 被读取；安装目录、模型叙述和工具结果都
不能单独充当读取证据。可加载的 Skill 正文会作为有界上下文提供给归因模型。

### 3. 外部信号复用标准演进管线

`SkillEvolutionRail.evolve_from_external_signals(...)` 只接受恰好归因到一个现存普通 Skill 的信号。
它绕过被动 `signal_trigger` 检测，但不绕过其余治理：仍走标准 optimizer、同一个 semaphore、
`ExperienceManager`、审批/自动保存选择和 `EvolutionStore` 持久化。调用方可以通过
`requires_approval` 明确成员级自动保存或全局审批；不应直接写 `evolutions.json`。

### 4. 成员轨迹与团队聚合轨迹分开读取

`TrajectorySource` 增加 `get_member_trajectory(team_id, session_id, member_id)`，用于 task feedback
只读取 assignee 的最新 snapshot。`get_trajectory(...)` 继续服务团队级聚合，二者不互相替代。
这样成员 Skill 的归因不会被 leader 或其他成员的 Skill 调用污染。

### 5. 新建 Skill 只进入审批，不直接创建

`TeamSkillCreateRail.propose_from_external_evidence(...)` 要求非空稳定 key、可复用指导和至少两条去重
证据；同一 key 在一个 Rail 生命周期内只提议一次。它只生成结构化审批 host event。用户接受后，
`resolve_external_proposal(...)` 返回受约束的创建 prompt，由宿主继续交给已有 creator 能力执行。

Core 不定义“两个模式是否相同”的算法；宿主必须先完成同类分组，不能用两个无关失败凑重复次数。

## 拒绝的方案

- **把演进代码写进 `TeamScheduler`**：拒绝。scheduler 的职责是看板判定与交接；引入 LLM、轨迹、
  文件目录和审批会破坏调度边界，也让 Core 无法被不同宿主复用。
- **所有 reviewer fail 都自动修改 Skill**：拒绝。失败可能是执行者失误、验收标准临时变化或无法
  归因的问题；只有 `skill_issue` 且证据完整时才允许进入演进管线。
- **从已安装 Skills 中让模型自由选择目标**：拒绝。安装不等于本次执行使用过；目标必须有具体
  `SKILL.md` 读取证据。
- **第一条重复工作观测就创建 Skill**：拒绝。单例很可能是一次性需求；第一条只保留候选摘要，
  重复同类证据出现后才允许提交创建审批。
- **宿主直接合并 feedback 到 `evolutions.json`**：拒绝。它绕过生成、去重、评分、审批、投影和
  并发门禁；所有持久化继续归 `EvolutionStore`。
- **同步等待每次归因后再通知成员返工**：拒绝。演进是旁路能力，不能增加任务状态机关键路径延迟；
  团队终态只在汇总前等待已启动 callback 收敛。

## 验证

- 归因器：四种 classification、无 Skill 读取证据、伪造 Skill 名、无效模型输出、重复模式门禁。
- 轨迹 registry：按 `(team, session, member)` 返回独立最新 snapshot，未知成员返回 `None`。
- scheduler：失败 settle 后 callback、同轮去重、异常隔离、团队终态等待 task callback 后汇总。
- `SkillEvolutionRail`：外部信号绕过关闭的被动触发、单 Skill 约束、禁用/不存在目标、审批参数透传。
- `TeamSkillCreateRail`：重复证据校验、proposal 去重、审批事件、接受/拒绝解析。
- 相关测试集合：222 passed。

## 已知遗留

- Core 只提供边界，不持有成员 copy-on-write 目录、全局 observation 聚合或前端卡片投递；这些属于
  JiuwenSwarm 等宿主实现。
- `ReviewFeedbackAttributor` 当前一次调用失败即 fail closed，不自动重试；安全上不会误改 Skill，
  但该条 feedback 不会演进，需要宿主通过日志和指标观测。
- 重复模式的同类匹配属于宿主策略。Core 只校验重复数量，无法判断两条文本是否语义等价。
- 外部创建 proposal 是 Rail 进程内 pending 状态；跨进程重启后的审批恢复尚未定义。
