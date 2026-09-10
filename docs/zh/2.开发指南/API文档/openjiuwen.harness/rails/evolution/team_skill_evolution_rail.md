# 团队技能演进 Rail

团队技能创建与在线演进文档。

先安装运行时依赖：`uv sync --extra observability`。

---

## class TeamSkillCreateRail

独立 Rail，用于自动检测多 Agent 协作模式并建议创建团队技能。

### 触发机制

- 等待团队任务完成后，基于当前团队执行中记录到的调用检测 `spawn_member` 次数
- 当调用次数达到阈值（默认 2 次）且未使用已有 Team/Swarm Skill 时，通过 `TaskLoopController` 注入简短 follow_up 唤起下一轮
- 完整自检规则通过系统提示词注入；如果 Agent 判断存在可复用团队协作价值，必须通过普通回复文本确认。用户确认后，调用 `swarmskill-creator` 或兼容的团队技能创建 Skill。如果 creator 不可用，Agent 应通过普通回复文本提醒用户。

### 外部重复证据入口

可信宿主已经识别到重复、可复用且没有现存 Skill 可归因的模式时，可以提交创建审批：

```python
staged = await create_rail.propose_from_external_evidence(
    proposal_key="release-recovery-checklist",
    reusable_guidance="Create a reusable release recovery checklist.",
    evidence=["task-a: ...", "task-b: ..."],
    reason="The same missing workflow caused two review failures.",
)
```

该方法要求 `auto_trigger=True`、非空 key/指导和至少两条去重证据；同一 key 在当前 Rail 生命周期内
只生成一次审批 host event。它不会直接创建或修改 Skill。宿主用 `owns_external_proposal(request_id)`
识别请求，用户作答后调用 `resolve_external_proposal(request_id, accepted=...)`；接受时返回受约束的创建
prompt，拒绝或未知请求返回 `None`。

Rail 只校验重复证据数量，不判断证据是否属于同一语义模式。调用方必须在提交前完成同类分组，不能
用无关任务凑足两条证据。

```text
class TeamSkillCreateRail(
    skills_dir: str,
    *,
    trajectory_span_processor: TrajectorySpanProcessor,
    language: str = "cn",
    auto_trigger: bool = True,
    min_team_members_for_create: int = 2,
)
```

**参数**：

* **skills_dir** (str): 技能目录路径。
* **trajectory_span_processor** (TrajectorySpanProcessor): `get_trajectory_span_processor()` 返回、由 observability demand coordinator 注册的进程级共享 processor。
* **language** (str): 语言设置，支持 `"cn"` 或 `"en"`。
* **auto_trigger** (bool): 是否自动触发，默认 `True`。
* **min_team_members_for_create** (int): 触发阈值，`spawn_member` 调用次数达到此值时触发，默认 2。

### 优先级

`priority = 85`

---

## class TeamSkillEvolutionRail

团队技能演进 public Rail，类似 `SkillEvolutionRail` 但专门处理团队技能。
`TeamSkillRail` 仍作为兼容 public alias 保留；新代码应使用 `TeamSkillEvolutionRail`。
新建 Swarm Skill 仍由 `TeamSkillCreateRail` 负责。Team 自动演进同时识别 legacy
`kind: team-skill` 与当前 `kind: swarm-skill`；Agent-facing subject 会把 legacy kind 归一化为
`swarm-skill`。显式 `/evolve` review 使用从磁盘解析出的 canonical subject kind，而不是强制采用 Rail mode。

### 导入

```python
from openjiuwen.harness.rails import (
    EvolutionInterruptRail,
    TeamSkillEvolutionRail,
    configure_skill_evolution,
)
from openjiuwen.harness.rails.evolution import EvolutionReviewRuntime, build_evolve_review_command_prompt
```

`TeamSkillEvolutionRail` 会注册稳定的 `evolution_reviewer`，并通过 Rail 自有的 `evolve_review_task` 暴露。主动审核链路不需要全局 `task_tool` 或 `SubagentRail`；相关工具共享 `EvolutionReviewRuntime`。

`TeamSkillEvolutionRail.init()` / `SkillEvolutionRail.init()` 都不再配置 `EvolutionInterruptRail`，如不走工厂函数，需要手动注入共享的 interrupt。

稳定 `evolution_reviewer` 按名称去重；若已有绑定过期，会替换为当前 runtime/query/store。

### 推荐优先 / 推荐构建方式

优先使用配置 API：

```python
configure_skill_evolution(
    agent,
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    trajectory_span_processor=runtime_processor,
    team=True,
    auto_save=False,
    language="cn",
)
```

配置 API 会将 `EvolutionInterruptRail` 与 `TeamSkillEvolutionRail` 正确绑定。

Agent 已初始化时使用 `configure_skill_evolution_runtime(..., team=True)`，使新增 Rails 立即注册。使用
`unconfigure_skill_evolution(agent, team=True)` 移除 Team 演进 stack。

手工组装时需要显式共享：

```python
runtime = EvolutionReviewRuntime()
team_rail = TeamSkillEvolutionRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    trajectory_span_processor=runtime_processor,
    review_runtime=runtime,
    team_id="research-team",
    auto_save=False,
)
interrupt_rail = EvolutionInterruptRail(
    review_runtime=runtime,
    submission_service=team_rail.approval_submission_service,
)
agent = create_deep_agent(
    model=model_client,
    tools=team_tools,
    rails=[interrupt_rail, team_rail],
)
```

`EvolutionInterruptRail` 不按 `subject.kind` 路由；只有一个共享 runtime/service 的中断 rail。

### Regular + Team 共用提交服务约束

若同一进程同时启用 regular 与 team/swarm 演进，需要两个 rail 共享同一个 `EvolutionReviewRuntime` 和 `ExperienceSubmissionService`。建议手动组装并显式共享：

```python
from openjiuwen.harness.rails import (
    EvolutionInterruptRail,
    SkillEvolutionRail,
    TeamSkillEvolutionRail,
)
from openjiuwen.harness.rails.evolution import EvolutionReviewRuntime

runtime = EvolutionReviewRuntime()
skill_rail = SkillEvolutionRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    trajectory_span_processor=runtime_processor,
    review_runtime=runtime,
)
team_rail = TeamSkillEvolutionRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    trajectory_span_processor=runtime_processor,
    review_runtime=runtime,
    team_id="research-team",
)
interrupt_rail = EvolutionInterruptRail(
    review_runtime=runtime,
    submission_service=skill_rail.approval_submission_service,
)
rails = [interrupt_rail, skill_rail, team_rail]
```

### 功能

- 轨迹问题检测（角色配合、约束违反、流程低效）
- 用户请求演进
- 聚合式经验记录生成与审批
- 经验简化/重建

### 触发机制

- 监听 `view_task` 工具结果，检测"所有任务已完成"
- 支持被动信号链路和由 Agent 判断的主动审核链路
- `signal_trigger` 控制被动团队完成态扫描，默认关闭。
- `review_trigger` 控制团队完成后的自检 follow_up 注入，默认关闭。
- `review_trigger=True` 时，团队完成后的主动审核优先于被动信号生成。主 Agent 判断是否需要演进，并调用 Rail 自有的 `evolve_review_task` 运行 `evolution_reviewer`。
- `signal_trigger=False` 会关闭被动完成态扫描，也会关闭 `notify_team_completed()` 的被动触发；当 `review_trigger=True` 时，`notify_team_completed()` 仍可安排主动审核。
- 被动链路使用聚合后的协作轨迹证据，并调用 `SkillExperienceOptimizer(profile="team")`。Team completion、team skill attribution 和 runtime role attribution 是启发式 host bridge 信号，不是强 contract。
- 用户 `/evolve` command 是独立于两个开关的显式 host path。Host 解析 Team/Swarm Skill subject，调用
  `build_evolve_review_command_prompt()` 构造 prompt，并在同一 Agent Session 和 Team root trace 中作为下一条
  query 运行。

```text
class TeamSkillEvolutionRail(
    skills_dir: Union[str, list[str]],
    *,
    llm: Model,
    model: str,
    language: str = "cn",
    trajectory_span_processor: TrajectorySpanProcessor,
    member_role: Optional[str] = None,
    signal_trigger: Optional[bool] = None,
    auto_save: bool = False,
    review_runtime: EvolutionReviewRuntime,
    async_evolution: bool = True,
    max_concurrent_evolution: int = 1,
    team_id: Optional[str] = None,
    record_llm_policy: LLMInvokePolicy = ...,
    evaluate_llm_policy: LLMInvokePolicy = ...,
    simplify_llm_policy: LLMInvokePolicy = ...,
    eval_interval: int = 5,
    evolution_total_timeout_secs: float = 720.0,
    disabled_skills: Optional[Union[str, list[str]]] = None,
    review_trigger: Optional[bool] = None,
    review_interval: int = 5,
    review_agent_max_iterations: int = 40,
)
```

**参数**：

* **skills_dir** (Union[str, list[str]]): 技能目录路径或路径列表。
* **llm** (Model): LLM 客户端实例。
* **model** (str): 模型名称。
* **language** (str): 语言设置。
* **trajectory_span_processor** (TrajectorySpanProcessor): `get_trajectory_span_processor()` 返回、由 observability demand coordinator 注册的进程级共享 processor。
* **member_role** (str, 可选): 写入轨迹 resource metadata 的成员角色。团队技能演进默认是 `"leader"`。
* **signal_trigger** (bool, 可选): 是否检测被动 team completion 并触发被动演进，默认 `False`。
* **auto_save** (bool): 是否自动保存生成的经验记录，默认 `False`（需用户审批）。
* **review_runtime** (EvolutionReviewRuntime): 主动审核与中断复用的共享运行时（必填）。
* **async_evolution** (bool): 是否异步执行演进，默认 `True`。
* **max_concurrent_evolution** (int): 后台演进最大并发数，默认 1。
* **team_id** (str, 可选): 兼容配置值。运行时采集和主动 review 以当前 Team root span 中的 team name 作为权威 Team identity。
* **record_llm_policy** (LLMInvokePolicy): 经验记录生成 LLM 调用策略。
* **evaluate_llm_policy** (LLMInvokePolicy): 经验评估 LLM 调用策略。
* **simplify_llm_policy** (LLMInvokePolicy): 经验简化 LLM 调用策略。
* **eval_interval** (int): 经验展示评分检查间隔，必须大于等于 1。
* **evolution_total_timeout_secs** (float): 后台演进总超时预算，默认 720s。
* **disabled_skills** (Optional[Union[str, list[str]]], 可选): 排除自优化范围的技能拒绝列表。支持单个技能名（字符串）或多个技能名（字符串列表）。
* **review_trigger** (bool, 可选): 团队完成后是否注入简短演进自检 follow_up，默认 `False`。
* **review_interval** (int): 共享基类接受的 review 间隔，必须大于等于 1，默认 5；Team review follow-up 仍由团队完成态驱动。
* **review_agent_max_iterations** (int): `evolution_reviewer` 的最大迭代次数，默认 40。

### 运行时轨迹采集

`TeamSkillEvolutionRail` 消费 `EvolutionRail` 维护的 canonical clean window。Subscription 使用当前 Team root
trace ID，因此 leader Rail 可以选择同进程协作 span，不再需要运行时 source/sink registry。宿主必须注入
与该 runtime 其他 Rails 共用的、由 `get_trajectory_span_processor()` 返回的进程级
`TrajectorySpanProcessor`。Team host 应 acquire/release Team observability demand，并挂载
`maybe_observability_rails()` 返回的 Agent/Team Rail 组合。
执行 invoke 与后续 `/evolve` review invoke 使用同一 Agent Session 时，Team root span 必须在两次调用期间
保持 recording，并在完成后 finalize。

Team clean window 是在线演进证据，不是完整 Team runtime archive。当前实现不提供
`TeamTrajectoryRail`、`MemberTrajectorySnapshot` 或 `InMemoryTrajectoryRegistry`。

### 优先级

`priority = 80`

---

## 属性

### store -> EvolutionStore

演进存储实例。

### scorer -> ExperienceScorer

经验评分器。

### generator -> SkillExperienceOptimizer

被动信号链路使用的共享经验优化器，配置为 `profile="team"`。

### evolution_config -> dict

完整演进配置，包含各阶段 LLM 调用策略和超时设置。

---

## 生命周期与 Contract

被动信号链路的可观测生命周期与普通 skill 演进一致：

```text
聚合 team trajectory
-> 检测 team signals
-> local apply preview
-> pending approval 或 auto-approved
-> EvolutionStore persistence
-> evolutions.json 和 evolution/*.md projection
```

主动审核使用独立链路：

```text
团队完成或用户请求
-> 主 Agent 判断并准备有界审核 scope
-> evolve_review_task 运行 evolution_reviewer
-> reviewer 通过审核工具提交 proposal
-> 中断治理的审批与持久化
```

稳定职责边界：

* `TeamSkillEvolutionRail` 拥有 team 专属 host bridge 行为：`view_task` 完成态检测、`notify_team_completed()`、team trajectory aggregation 和已使用 team skill 检测。
* Rail 自有的 `evolve_review_task` 是专用 `evolution_reviewer` 的唯一 task wrapper。
* `OnlineEvolutionOrchestrator` 协调 context build、update 生成和 local preview。
* `ExperienceManager + PendingChange` 拥有 pending approval 状态。
* `EvolutionStore` 拥有 durable write 和 projection。

`EvolutionApprovalRuntime` 是绑定在 rail 上的 adapter，只包装 manager approval 方法和 pending snapshot lookup。它不拥有审批状态，也不应把 approval lifecycle 放回 `EvolutionRail`。

### Host events

消费演进事件的 canonical API 是 `drain_pending_host_events()`。`drain_pending_approval_events()` 是同一 buffer 的兼容 wrapper。

演进 metadata 位于 `OutputSchema.payload["evolution_meta"]`：

| 字段 | 含义 |
|---|---|
| `event_kind` | `approval`、`progress` 或 `outcome`。 |
| `rail_kind` | 产生事件的 rail kind，本 Rail 通常为 `team`。 |
| `stage` | progress 或 outcome 的生命周期阶段。 |
| `skill_name` | 目标 team skill 名称。 |
| `request_id` | 审批请求 ID。 |
| `signal_type` | 参与生成请求的信号类型。 |
| `source` | 信号或事件来源。 |
| `status` | outcome 状态。 |

审批事件使用 `type="chat.ask_user_question"`，并包含 `payload["request_id"]`。进度事件使用 `type="llm_reasoning"`。后台失败会以 outcome 事件暴露，不会让主 invoke 失败。

`outcome` 事件是调用方可依赖的结构化终态事件。演进流程正常完成但没有生成记录时，SDK 会发出 `status="no_evolution_no_records"`。调用方不应解析 progress 文案来判断终态。

### Snapshot 与 signal 边界

Async snapshot 包含 `trajectory`、`messages` 和可选 `skill_name`。`messages` 是检测上下文，`trajectory` 是执行证据。当前实现保留 legacy dict 兼容，因此宿主应把 rail 方法和 host events 当作 public 集成点，不应依赖 dict 形状。

Team signal 语义一部分在 `EvolutionSignal` 字段中结构化，一部分仍保存在 `EvolutionSignal.context`。runtime team member / role attribution 仍是启发式；从 `SKILL.md` 提取的 roles summary 是文档上下文，不是运行时身份凭据。

### Subject Schema（演进工具）

团队演进工具的 subject 采用统一 schema：

```python
{
    "kind": "swarm-skill",
    "name": "team-skill-name",
    "scope": { ... }  # 可选
}
```

`subject.kind="team-skill"` 作为历史兼容仍可被接受，并在运行时归一为 `"swarm-skill"` 后再进入审核与持久化流程。

---

## 方法

### async notify_team_completed(ctx=None) -> bool

标记团队完成，交给已启用的被动信号和/或主动审核 trigger 处理。

**参数**：

* **ctx** (AgentCallbackContext, 可选): 回调上下文。

**返回**：

* `bool`: 是否已接受本次团队完成标记并进入已配置的演进处理。

---

### build_evolve_review_command_prompt(*, subject, user_intent=None, review_agent_name="evolution_reviewer", language="cn") -> str

构造推荐的主动 `/evolve` follow-up prompt，从 `openjiuwen.harness.rails.evolution` 导入。调用前使用
`EvolutionStore.resolve_subject_payload(skill_name)` 解析真实 subject kind。

### 兼容方法：async request_user_evolution(skill_name, user_intent="", *, auto_approve=None, max_index_records=None) -> EvolutionRequestResult

为具名且已存在的 Skill subject 构造主动审核 prompt 的兼容 wrapper。新的 host command handler 应直接使用
`build_evolve_review_command_prompt()`。

**参数**：

* **skill_name** (str): 目标技能名称。
* **user_intent** (str): 用户改进意图描述，默认 `""`。
* **auto_approve** (bool, 可选): 为兼容旧调用保留，主动审核链路会忽略该值。
* **max_index_records** (int, 可选): 为兼容旧调用保留，主动审核链路会忽略该值。

**返回**：

* `EvolutionRequestResult`: `mode="agent_prompt"`，包含由 host 投递给主 Agent 的 `followup_prompt`；技能不存在时返回空结果，subject 由磁盘中的实际 kind 决定。

---

### async request_simplify(skill_name, user_intent=None, *, mode="agent_prompt") -> SimplifyRequestResult

暂存 scorer 驱动的 Team Skill simplify governance，并返回审批事件。

**参数**：

* **skill_name** (str): 目标技能名称。
* **user_intent** (str, 可选): 用户简化意图。
* **mode** (str): Team Rail 接受但当前忽略的兼容参数。

**返回**：

* `SimplifyRequestResult`: governance `request_id`、建议的 `actions` 和可选 `approval_event`。

使用 `on_approve_simplify(request_id)` 执行，使用 `on_reject_simplify(request_id)` 放弃。

---

### async request_rebuild(skill_name, user_intent=None, min_score=0.5, *, max_context_records=40, max_context_chars=20000) -> Optional[str]

归档当前 Team Skill 资产并返回有界、确定性的 rebuild prompt。Host 必须把 prompt 交给 Agent；该方法不会
生成或写入重建后的 Skill body。

**参数**：

* **skill_name** (str): 目标技能名称。
* **user_intent** (str, 可选): 用户重建意图。
* **min_score** (float): 演进经验筛选阈值，默认 0.5。
* **max_context_records** (int): rebuild context 最多内联的记录数，默认 40。
* **max_context_chars** (int): 内联 rebuild context 的最大字符数，默认 20000。

**返回**：

* `str`: rebuild follow-up prompt 文本或 `None`（技能不存在时）。调用方需要把返回的 prompt 注入 agent loop；rail 不会直接写出重建后的 `SKILL.md`。

---

### async approve_record(request_id, *, approved_record_ids=None) -> None

审批暂存的经验记录，并写入 `evolutions.json`。

**参数**：

* **request_id** (str): 请求 ID。
* **approved_record_ids** (Sequence[str], 可选): 要审批的暂存记录 ID；省略时审批全部暂存记录。

---

### async reject_record(request_id) -> None

拒绝暂存的经验记录，并清理待审批请求。

**参数**：

* **request_id** (str): 请求 ID。

---

### async drain_pending_approval_events(wait=False, timeout=None) -> List[OutputSchema]

读取 buffered host events 的兼容 wrapper。

**参数**：

* **wait** (bool): 是否等待事件到达。
* **timeout** (float, 可选): 等待超时时间，默认使用 `evolution_total_timeout_secs`。

**返回**：

* `List[OutputSchema]`: 待审批事件列表。

### async drain_pending_host_events(wait=False, timeout=None) -> List[OutputSchema]

获取并清空 buffered host events。若 `wait=True`，会在 `timeout` 内等待后台演进任务完成。

**参数**：

* **wait** (bool): 是否等待事件到达。
* **timeout** (float, 可选): 等待超时时间，默认使用 `evolution_total_timeout_secs`。

**返回**：

* `List[OutputSchema]`: 待处理的演进 host events。

---

## 辅助类型

### class TeamSignalType

演进信号类型枚举：

* `USER_REQUEST`: 用户主动请求演进
* `TRAJECTORY_ISSUE`: 轨迹问题检测触发演进

### class UserIntent

用户意图数据类：

* `is_improvement` (bool): 是否为改进意图
* `intent` (str): 意图描述

### class TrajectoryIssue

轨迹问题数据类：

* `issue_type` (str): 问题类型
* `description` (str): 问题描述
* `affected_role` (str): 受影响角色
* `severity` (str): 严重程度（`"low"` | `"medium"` | `"high"`）

---

## 示例

可运行模块 `examples.agent_evolving.swarmskill_evolution_example` 合并了 Swarm Skill 创建与演进案例；实现使用
当前公开 API `TeamSkillCreateRail` 和 `TeamSkillEvolutionRail`。

```python
from openjiuwen.harness.rails import TeamSkillCreateRail, TeamSkillEvolutionRail, configure_skill_evolution
from openjiuwen.harness.rails.evolution import build_evolve_review_command_prompt
from openjiuwen.harness import create_deep_agent
from openjiuwen.core.runner import Runner
from openjiuwen.extensions.observability.demand import get_trajectory_span_processor
from openjiuwen.agent_teams.observability import maybe_observability_rails

# 由 observability demand coordinator 注册的进程级共享 processor。
processor = get_trajectory_span_processor()

# 创建团队技能创建 Rail
create_rail = TeamSkillCreateRail(
    skills_dir="/path/to/skills",
    trajectory_span_processor=processor,
    min_team_members_for_create=2,
)

# 将创建 Rail 配置到 DeepAgent。
agent = create_deep_agent(
    model=model_client,
    tools=team_tools,
    rails=[create_rail, *maybe_observability_rails()],
    enable_task_loop=True,
)
configure_skill_evolution(
    agent,
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    trajectory_span_processor=processor,
    team=True,
    auto_save=False,
    async_evolution=True,
)
team_rail = next(
    rail
    for rail in agent.find_rails_by_type((TeamSkillEvolutionRail,))
    if rail.__class__ is TeamSkillEvolutionRail
)

# Host 处理 /evolve，并在同一 Agent Session 中运行该 prompt。
subject = team_rail.store.resolve_subject_payload("research-team")
followup_prompt = build_evolve_review_command_prompt(
    subject=subject,
    user_intent="增加 reviewer 角色，限制 research 时间不超过 10 分钟",
)
result = await Runner.run_agent(agent, {"query": followup_prompt}, session=session)

# 请求简化
simplify_result = await team_rail.request_simplify("research-team")
if simplify_result.approval_event:
    await team_rail.on_approve_simplify(simplify_result.request_id)

# 请求重建
prompt = await team_rail.request_rebuild("research-team", min_score=0.5)
```
