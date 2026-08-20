# Reviewer Feedback 驱动的 Skill 演进边界

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-06 |
| 范围 | `agent_teams/agent/scheduling/scheduler.py`（失败轮次与团队终态 callback）；`agent_teams/agent/scheduling/review_feedback_evolution.py`（逐 Task 归因与团队汇总）；`agent_evolving/signal/review_feedback.py`（归因与安全策略）；`agent_evolving/trajectory/team.py`（成员轨迹投影）；`harness/rails/evolution/`（团队演进与标准审批事件）；`harness/rails/skills/team_skill_create_rail.py`（重复模式创建审批入口） |

## 背景

scheduled 团队已经有完整的 Task 验收闭环：成员提交、reviewer 投票、失败返工、再次验收。但失败
feedback 过去只回到任务执行者，无法成为 Skill 演进证据。平台若要补这条链路，容易犯三个错误：

1. 把每次失败都当成 Skill 缺陷，掩盖执行者没有遵循已有指导的问题。
2. 轨迹没有读过任何 `SKILL.md` 时，凭安装列表随便挑一个 Skill 修改。
3. 直接从 scheduler 写 `evolutions.json`，让调度层同时理解 LLM、Skill 存储和审批协议。

本特性把与产品无关的演进协调完整放进 Core：scheduler 只把审核事实交给已挂载的团队演进 Rail；
团队 Rail 持有协调器，读取成员独立轨迹、逐 Task 执行安全归因并保留结构化 observation，在团队终态
按 Team Skill 汇总后统一演进。Team Skill 更新与新建 Skill 都继续生成标准 Rail host event。
JiuwenSwarm 等宿主只负责目录和模型等构造参数，以及复用既有事件传输和审批入口。

## 数据流与状态

```text
review round failed
  -> TeamScheduler finds mounted TeamSkillEvolutionRail
  -> rail.handle_review_feedback(payload)
  -> coordinator loads assignee trajectory
  -> ReviewFeedbackContextBuilder
  -> ReviewFeedbackAttributor
       -> evolve_existing_skill
            -> retain structured task observation
       -> suggest_new_skill
       -> record_task_failure
       -> skip_unattributed

all tasks terminal
  -> wait for outstanding task callbacks
  -> rail.finalize_review_feedback(payload)
  -> coordinator aggregates observations
  -> existing Skill: mounted TeamSkillEvolutionRail
  -> repeated missing capability: mounted TeamSkillCreateRail
  -> existing team approval transport and endpoint
```

`TeamScheduler` 新增三组进程内状态：

- `_review_feedback_dispatched[(task_id, review_round)]`：失败轮次 callback 去重。
- `_review_feedback_tasks`：不阻塞任务返工的后台 callback 任务集合。
- `_team_review_feedback_dispatched`：团队终态 callback 去重。

这些状态不是任务事实，不持久化；任务状态、票据和 feedback 仍以 DB 为真相。

## 决策

### 1. Scheduler 只发布事实，不理解演进

每个失败轮次 settle 后，scheduler 从 host harness 中查找已声明式挂载且开启该能力的
`TeamSkillEvolutionRail`，以后台任务调用 `handle_review_feedback(...)`；看板全部终态时先等待当前
callback，再调用 `finalize_review_feedback(...)`。callback 异常只记录日志，不改变任务状态，也不
阻塞返工。scheduler 不通过 `BuildContext.extras` 注入业务 handler，也不自行构造 Rail。

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

`SkillEvolutionRail.evolve_from_external_signals(...)` 只接受恰好归因到一个现存 Skill 的信号。
它绕过被动 `signal_trigger` 检测，但不绕过其余治理：仍走标准 optimizer、同一个 semaphore、
`ExperienceManager`、审批/自动保存选择和 `EvolutionStore` 持久化。调用方可以通过
`requires_approval` 明确审批策略；不应直接写 `evolutions.json`。Reviewer Feedback 链路不会在
单个 Task 失败时调用它，而是在团队终态把同一 Team Skill 的 observations 合并后调用一次挂载中的
`TeamSkillEvolutionRail`。

### 4. 成员轨迹与团队聚合轨迹分开读取

协调器从已挂载的 `TeamSkillEvolutionRail.get_trajectory(...)` 取得统一 Team trajectory，再通过
`select_member_spans(...)` 按 assignee 投影出成员轨迹。这样既复用 Rail 已有的 OTLP 采集窗口，
又避免 Task 级 Skill 归因被 leader 或其他成员的 Skill 调用污染。归因结果只保存在协调器的
observation 列表中，不创建成员私有 Skill 副本，也不写成员 `evolutions.json`；完整团队轨迹只在
终态汇总演进时使用。

### 5. 新建 Skill 只进入审批，不直接创建

`TeamSkillCreateRail.propose_from_external_evidence(...)` 要求非空稳定 key、可复用指导和至少两条去重
证据；同一 key 在一个 Rail 生命周期内只提议一次。它生成与团队演进相同 schema、相同 request-id
命名空间的结构化审批 host event。用户接受后，`resolve_external_proposal(...)` 返回受约束的创建
prompt；挂载的 `TeamSkillEvolutionRail` 保存 continuation，宿主的既有团队审批入口将它交给已有
creator 能力执行。

Core 不定义“两个模式是否相同”的算法；宿主必须先完成同类分组，不能用两个无关失败凑重复次数。

### 6. 审批仍走已挂载团队 Rail 的旧通道

现有 Skill 的汇总更新直接调用已挂载的 `TeamSkillEvolutionRail`，因此 optimizer、pending request、
审批记录和接受/拒绝逻辑与普通 Team Skill 演进完全相同。新建候选仍由已挂载的创建 Rail 产生事件，
再回流到同一团队事件队列。宿主仍只需原来的团队事件 watcher、请求 owner 查找和团队审批端点，
不需要 Reviewer Feedback 专用 sidecar、WebSocket 协议或前端卡片类型。

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
- **在 JiuwenSwarm 注入 Reviewer Feedback handler 和全局 sidecar Rail**：拒绝。它复制了 Core 已有
  Rail 生命周期、pending request 和审批路由，导致业务策略散落到产品层；改为由挂载的团队 Rail
  持有 Core 协调器并转发标准 host event。

## 验证

- 归因器：四种 classification、无 Skill 读取证据、伪造 Skill 名、无效模型输出、重复模式门禁。
- 轨迹投影：从 Team Rail snapshot 按成员筛选 span，未知成员返回 `None`，终态汇总仍使用完整轨迹。
- scheduler：失败 settle 后 callback、同轮去重、异常隔离、团队终态等待 task callback 后汇总。
- `SkillEvolutionRail`：外部信号绕过关闭的被动触发、单 Skill 约束、禁用/不存在目标、审批参数透传。
- `TeamSkillCreateRail`：重复证据校验、proposal 去重、审批事件、接受/拒绝解析。
- `TeamSkillEvolutionRail`：协调器生命周期、终态汇总复用父 Rail、新建 Skill continuation 与审批事件。

## 已知遗留

- `ReviewFeedbackAttributor` 当前一次调用失败即 fail closed，不自动重试；安全上不会误改 Skill，
  但该条 feedback 不会演进，需要宿主通过日志和指标观测。
- 重复模式目前按归因结果的稳定 target/key 分组；跨表达的语义聚类仍可继续增强。
- 外部创建 proposal 是 Rail 进程内 pending 状态；跨进程重启后的审批恢复尚未定义。
