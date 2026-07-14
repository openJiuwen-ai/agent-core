# 团队技能演进 Rail

团队技能创建与在线演进文档。

---

## class TeamSkillCreateRail

独立 Rail，用于自动检测多 Agent 协作模式并建议创建团队技能。

### 触发机制

- 在 `AFTER_TASK_ITERATION` 生命周期回调中检测 `spawn_member` 调用次数
- 当调用次数达到阈值（默认 2 次）时，通过 `TaskLoopController` 注入 follow_up
- 用户确认后，调用 `team-skill-creator` 技能执行创建

```text
class TeamSkillCreateRail(
    skills_dir: str,
    *,
    language: str = "cn",
    auto_trigger: bool = True,
    min_team_members_for_create: int = 2,
    trajectory_store: Optional[TrajectoryStore] = None,
)
```

**参数**：

* **skills_dir** (str): 技能目录路径。
* **language** (str): 语言设置，支持 `"cn"` 或 `"en"`。
* **auto_trigger** (bool): 是否自动触发，默认 `True`。
* **min_team_members_for_create** (int): 触发阈值，`spawn_member` 调用次数达到此值时触发，默认 2。
* **trajectory_store** (TrajectoryStore, 可选): 轨迹存储实例。

### 优先级

`priority = 85`

---

## class TeamSkillRail

团队技能演进 public Rail，类似 `SkillEvolutionRail` 但专门处理团队技能。
`TeamSkillRail` 是 `TeamSkillEvolutionRail` 的兼容 public alias。
新建 team skill 仍由 `TeamSkillCreateRail` 负责；本 Rail 只演进已有的 `kind: team-skill`。

### 功能

- 轨迹问题检测（角色配合、约束违反、流程低效）
- 用户请求演进
- 聚合式经验记录生成与审批
- 经验简化/重建

### 触发机制

- 监听 `view_task` 工具结果，检测"所有任务已完成"
- 支持被动轨迹分析和主动用户请求两种演进路径
- `auto_scan=False` 会关闭被动完成态扫描，也会关闭 `notify_team_completed()` 的被动触发。
- 被动演进使用聚合后的协作轨迹证据。Team completion、team skill attribution 和 runtime role attribution 是启发式 host bridge 信号，不是强 contract。

```text
class TeamSkillRail(
    skills_dir: Union[str, list[str]],
    *,
    llm: Model,
    model: str,
    language: str = "cn",
    trajectory_store: Optional[TrajectoryStore] = None,
    team_trajectory_store: Optional[TrajectoryStore] = None,
    trajectory_source: Optional[TrajectorySource] = None,
    trajectory_sink: Optional[TrajectorySink] = None,
    member_role: Optional[str] = None,
    auto_scan: bool = True,
    auto_save: bool = False,
    async_evolution: bool = True,
    max_concurrent_evolution: int = 1,
    team_id: Optional[str] = None,
    trajectories_dir: Optional[Path] = None,
    user_request_llm_policy: LLMInvokePolicy = ...,
    trajectory_issue_llm_policy: LLMInvokePolicy = ...,
    record_llm_policy: LLMInvokePolicy = ...,
    evaluate_llm_policy: LLMInvokePolicy = ...,
    simplify_llm_policy: LLMInvokePolicy = ...,
    eval_interval: int = 5,
    evolution_total_timeout_secs: float = 600.0,
    disabled_skills: Optional[Union[str, list[str]]] = None,
)
```

**参数**：

* **skills_dir** (Union[str, list[str]]): 技能目录路径或路径列表。
* **llm** (Model): LLM 客户端实例。
* **model** (str): 模型名称。
* **language** (str): 语言设置。
* **trajectory_store** (TrajectoryStore, 可选): 轨迹存储实例。
* **team_trajectory_store** (TrajectoryStore, 可选): 已废弃的团队轨迹存储实例。运行时聚合请使用 `trajectory_source` / `trajectory_sink`。
* **trajectory_source** (TrajectorySource, 可选): 运行时聚合成员轨迹证据的 source。
* **trajectory_sink** (TrajectorySink, 可选): 发布当前成员最新轨迹 snapshot 的 sink。
* **member_role** (str, 可选): 写入 snapshot 的成员角色。团队技能演进默认是 `"leader"`。
* **auto_scan** (bool): 是否检测被动 team completion 并触发被动演进，默认 `True`。
* **auto_save** (bool): 是否自动保存生成的经验记录，默认 `False`（需用户审批）。
* **async_evolution** (bool): 是否异步执行演进，默认 `True`。
* **max_concurrent_evolution** (int): 后台演进最大并发数，默认 1。
* **team_id** (str, 可选): 团队 ID。
* **trajectories_dir** (Path, 可选): 轨迹目录路径。
* **user_request_llm_policy** (LLMInvokePolicy): 用户意图检测 LLM 调用策略。
* **trajectory_issue_llm_policy** (LLMInvokePolicy): 轨迹问题检测 LLM 调用策略。
* **record_llm_policy** (LLMInvokePolicy): 经验记录生成 LLM 调用策略。
* **evaluate_llm_policy** (LLMInvokePolicy): 经验评估 LLM 调用策略。
* **simplify_llm_policy** (LLMInvokePolicy): 经验简化 LLM 调用策略。
* **eval_interval** (int): 经验展示评分检查间隔，必须大于等于 1。
* **evolution_total_timeout_secs** (float): 后台演进总超时预算，默认 600s。
* **disabled_skills** (Optional[Union[str, list[str]]], 可选): 排除自优化范围的技能拒绝列表。支持单个技能名（字符串）或多个技能名（字符串列表）。

### 运行时轨迹 Source/Sink

`TeamSkillRail` 使用 `trajectory_source` 和 `trajectory_sink` 完成在线团队轨迹聚合。常见配置是把同一个 `InMemoryTrajectoryRegistry` 同时作为 source 和 sink：

```python
from openjiuwen.agent_evolving.trajectory import InMemoryTrajectoryRegistry
from openjiuwen.harness.rails import TeamSkillRail

trajectory_registry = InMemoryTrajectoryRegistry()

team_rail = TeamSkillRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    team_id="research-team",
    trajectory_source=trajectory_registry,
    trajectory_sink=trajectory_registry,
)
```

Rail 在 invoke 结束后发布 `MemberTrajectorySnapshot`。snapshot 包含 `team_id`、`session_id`、`member_id`、`member_role`、`trajectory` 和 `recorded_at_ms`，不包含 public revision。`InMemoryTrajectoryRegistry` 负责判定最新 snapshot：`recorded_at_ms` 更新者优先；时间相同则按 registry 接收顺序，后接收者优先。

若要聚合多个成员，需要让所有贡献轨迹证据的 rail 或 agent 都发布到同一个 `trajectory_sink`；本 rail 再通过 `trajectory_source` 读取这个共享 registry。

### 优先级

`priority = 80`

---

## 属性

### store -> EvolutionStore

演进存储实例。

### scorer -> ExperienceScorer

经验评分器。

### generator -> TeamSkillExperienceOptimizer

团队技能经验优化器。

### evolution_config -> dict

完整演进配置，包含各阶段 LLM 调用策略和超时设置。

---

## 运行时轨迹方法

### set_trajectory_source(source) -> None

绑定或替换用于聚合团队轨迹证据的运行时 `TrajectorySource`。

### set_trajectory_sink(sink, *, team_id, member_role=None) -> None

绑定或替换用于发布本 rail 成员 snapshot 的运行时 `TrajectorySink`。当 `sink` 非 `None` 时必须提供 `team_id`。团队技能演进中的 `member_role` 默认是 `"leader"`。

---

## 生命周期与 Contract

可观测生命周期与普通 skill 演进一致：

```text
聚合 team trajectory
-> 检测 team signals
-> local apply preview
-> pending approval 或 auto-approved
-> EvolutionStore persistence
-> evolutions.json 和 evolution/*.md projection
```

稳定职责边界：

* `TeamSkillEvolutionRail` 拥有 team 专属 host bridge 行为：`view_task` 完成态检测、`notify_team_completed()`、team trajectory aggregation 和已使用 team skill 检测。
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
| `request_id` | 审批或治理请求 ID。 |
| `signal_type` | 参与生成请求的信号类型。 |
| `source` | 信号或事件来源。 |
| `status` | outcome 状态。 |

审批事件使用 `type="chat.ask_user_question"`，并包含 `payload["request_id"]`。进度事件使用 `type="llm_reasoning"`。后台失败会以 outcome 事件暴露，不会让主 invoke 失败。

`outcome` 事件是调用方可依赖的结构化终态事件。演进流程正常完成但没有生成记录时，SDK 会发出 `status="no_evolution_no_records"`。调用方不应解析 progress 文案来判断终态。

### Snapshot 与 signal 边界

Async snapshot 包含 `trajectory`、`messages` 和可选 `skill_name`。`messages` 是检测上下文，`trajectory` 是执行证据。当前实现保留 legacy dict 兼容，因此宿主应把 rail 方法和 host events 当作 public 集成点，不应依赖 dict 形状。

Team signal 语义一部分在 `EvolutionSignal` 字段中结构化，一部分仍保存在 `EvolutionSignal.context`。runtime team member / role attribution 仍是启发式；从 `SKILL.md` 提取的 roles summary 是文档上下文，不是运行时身份凭据。

---

## 方法

### async notify_team_completed(ctx) -> bool

触发技能演进（当所有任务完成时）。

**参数**：

* **ctx** (AgentCallbackContext, 可选): 回调上下文。

**返回**：

* `bool`: 是否成功触发演进。

---

### async request_user_evolution(skill_name, user_intent="", *, auto_approve=False) -> EvolutionRequestResult

用户主动请求演进。该方法信任传入的 `skill_name` 作为演进主体，并使用当前 rail 轨迹或 `trajectory_source` 聚合后的团队轨迹作为证据窗口；`user_intent` 只是补充方向。

**参数**：

* **skill_name** (str): 目标技能名称。
* **user_intent** (str): 用户改进意图描述，默认 `""`。为空时，若团队轨迹证据中存在可演进信号，仍可生成请求。
* **auto_approve** (bool): 是否自动审批，默认 `False`。

**返回**：

* `EvolutionRequestResult`: 生成记录时 `request_id` 有值；技能不存在、无证据且无意图、或未生成记录时返回空结果对象。

---

### async request_simplify(skill_name, user_intent=None) -> Optional[str]

暂存经验简化 proposal，并发出审批事件。

**参数**：

* **skill_name** (str): 目标技能名称。
* **user_intent** (str, 可选): 用户简化意图。

**返回**：

* `str`: 生成治理动作时返回 governance request id，否则返回 `None`。

使用 `on_approve_simplify(request_id)` 执行，使用 `on_reject_simplify(request_id)` 放弃。

---

### async request_rebuild(skill_name, user_intent=None, min_score=0.5) -> Optional[str]

请求技能重建（归档旧版本并生成新版本）。

**参数**：

* **skill_name** (str): 目标技能名称。
* **user_intent** (str, 可选): 用户重建意图。
* **min_score** (float): 演进经验筛选阈值，默认 0.5。

**返回**：

* `str`: rebuild follow-up prompt 文本或 `None`（技能不存在时）。调用方需要把返回的 prompt 注入 agent loop；rail 不会直接写出重建后的 `SKILL.md`。

---

### async approve_record(request_id) -> None

审批暂存的经验记录，并写入 `evolutions.json`。

**参数**：

* **request_id** (str): 请求 ID。

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

```python
from openjiuwen.agent_evolving.trajectory import InMemoryTrajectoryRegistry
from openjiuwen.harness.rails import TeamSkillCreateRail, TeamSkillRail
from openjiuwen.harness import create_deep_agent

# 创建团队技能创建 Rail
create_rail = TeamSkillCreateRail(
    skills_dir="/path/to/skills",
    min_team_members_for_create=2,
)

trajectory_registry = InMemoryTrajectoryRegistry()

# 创建团队技能演进 Rail
team_rail = TeamSkillRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    team_id="research-team",
    trajectory_source=trajectory_registry,
    trajectory_sink=trajectory_registry,
    auto_save=False,
    async_evolution=True,
)

# 配置到 DeepAgent
agent = create_deep_agent(
    model=model_client,
    tools=team_tools,
    rails=[create_rail, team_rail],
    enable_task_loop=True,
)

# 用户请求演进
result = await team_rail.request_user_evolution(
    skill_name="research-team",
    user_intent="增加 reviewer 角色，限制 research 时间不超过 10 分钟",
)

# 用户审批
if result.approval_event is not None:
    await team_rail.approve_record(result.request_id)

# 请求简化
simplify_request_id = await team_rail.request_simplify("research-team")
if simplify_request_id:
    await team_rail.on_approve_simplify(simplify_request_id)

# 请求重建
prompt = await team_rail.request_rebuild("research-team", min_score=0.5)
```
