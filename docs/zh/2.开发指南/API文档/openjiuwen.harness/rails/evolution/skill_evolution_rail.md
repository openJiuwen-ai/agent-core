# Skill Evolution Rail

普通 Skill 在线演进 public Rail。本文只覆盖已有普通 skill 的经验演进，不覆盖新建 skill，也不覆盖 team skill 演进。

先安装运行时依赖：`uv sync --extra observability`。

---

## class SkillEvolutionRail

public Rail，用于采集 agent 轨迹、检测普通 skill 的可复用改进、暂存生成的经验记录，并通过 `EvolutionStore` 写入已审批记录。

### 导入

```python
from openjiuwen.harness.rails import (
    EvolutionInterruptRail,
    SkillEvolutionRail,
    configure_skill_evolution,
)
from openjiuwen.harness.rails.evolution import EvolutionReviewRuntime, build_evolve_review_command_prompt
```

`SkillEvolutionRail` 的主动演进 review 流程通过 Rail 自有的 `evolve_review_task` 工具委托稳定的
`evolution_reviewer` 子智能体，不依赖全局 `task_tool` 或 `SubagentRail`。

`SkillEvolutionRail.init()` 不再配置 `EvolutionInterruptRail`，它只负责注册 review 工具与稳定 review subagent。

稳定 review subagent 名称为 `evolution_reviewer`。Rail 初始化时会用当前 runtime、query service 和 store
替换旧的 reviewer 绑定。

### 推荐优先 / 推荐构建方式

建议优先使用配置 API：

```python
configure_skill_evolution(
    agent,
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    trajectory_span_processor=runtime_processor,
    auto_save=False,
    language="cn",
)
```

配置 API 会将 `EvolutionInterruptRail` 与普通 `SkillEvolutionRail` 正确绑定。

Agent 已初始化时改用 `configure_skill_evolution_runtime(...)`，它会立即注册本次新增的 interrupt 与 evolution
Rails。两个配置函数对相同配置都是幂等的，并拒绝配置冲突或跨模式切换。使用
`unconfigure_skill_evolution(agent, team=False)` 移除普通 Skill 演进 Rails；省略 `team` 会移除两种模式。

手工组装时必须显式共享：

```python
runtime = EvolutionReviewRuntime()
skill_rail = SkillEvolutionRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    trajectory_span_processor=runtime_processor,
    review_runtime=runtime,
    auto_save=False,
)
interrupt_rail = EvolutionInterruptRail(
    review_runtime=runtime,
    submission_service=skill_rail.approval_submission_service,
)
agent = create_deep_agent(
    model=model_client,
    tools=tools,
    rails=[interrupt_rail, skill_rail],
)
```

手工组合时必须只保留一个共享的 `EvolutionInterruptRail`，并用同一 `review_runtime` + `submission_service` 绑定；`EvolutionInterruptRail` 不按 subject kind 路由。

### 触发机制

- 被动演进在 `DeepAgent.invoke()` 完成后运行。
- `signal_trigger` 控制被动信号扫描，默认关闭。
- `review_trigger` 控制周期性自检 follow_up 注入，默认关闭。
- `review_interval` 控制两次 review 检查之间的非 follow_up task iteration 数，必须大于等于 1。
- `signal_trigger=False` 会关闭被动信号扫描，也会跳过被动演进的 async snapshot。
- 推荐的主动入口由 host command handler 持有：解析 subject 后调用
  `build_evolve_review_command_prompt()`，并把 prompt 作为同一 Agent Session 的下一条 query。该 prompt 要求先
  调用 `prepare_skill_evolution(user_confirmed=true)`，再调用
  `evolve_review_task(evolution_review_ref=...)`。prepare tool 会把当前 Rail 已采集到的执行/对话轨迹作为默认
  review materials，`user_intent` 只提供审核方向。
- `request_user_evolution()` 仅作为尚未迁移到 command dispatch 的 host 兼容 wrapper 保留。
- 普通 skill 的自动检测会排除 `kind: team-skill` 和 `kind: swarm-skill`；Team root 轨迹采集及
  Team/Swarm Skill 被动检测应使用 `TeamSkillEvolutionRail`。显式 `/evolve` command 使用从磁盘
  `SKILL.md` 解析出的 canonical subject kind，而不是 Rail mode，并且不受该自动检测分工限制；legacy
  `team-skill` 会解析为 `swarm-skill`。

### 外部已归因信号入口

宿主已经完成归因时，可以调用：

```python
result = await skill_rail.evolve_from_external_signals(
    signals=[signal],
    messages=messages,
    trajectory=trajectory,
    user_query="Add reusable validation guidance.",
    requires_approval=True,
)
```

该入口绕过被动 `signal_trigger` 检测，因此即使 `signal_trigger=False` 也可使用；调用方必须负责信号
归因和证据策略。Rail 仍会强制所有信号恰好指向一个现存普通 Skill，拒绝禁用、缺失或 team skill
目标，并通过标准 optimizer、并发 semaphore、审批与 `EvolutionStore` 持久化管线处理。

`requires_approval=None` 时沿用 `not auto_save`；显式 `True` 生成审批请求，显式 `False` 允许宿主在
已有授权范围内自动保存。调用方不应绕过该入口直接写 `evolutions.json`。

```text
class SkillEvolutionRail(
    skills_dir: Union[str, list[str]],
    *,
    llm: Model,
    model: str,
    signal_trigger: Optional[bool] = None,
    auto_save: bool = False,
    review_runtime: EvolutionReviewRuntime,
    subject_kind: str = "skill",
    language: str = "cn",
    trajectory_span_processor: TrajectorySpanProcessor,
    eval_interval: int = 5,
    evolution_total_timeout_secs: float = 600.0,
    generate_records_llm_policy: LLMInvokePolicy = ...,
    evaluate_llm_policy: LLMInvokePolicy = ...,
    simplify_llm_policy: LLMInvokePolicy = ...,
    two_stage: bool = False,
    review_agent_max_iterations: int = 25,
    sharing_config: Optional[Dict[str, Any]] = None,
    disabled_skills: Optional[Union[str, list[str]]] = None,
    evolution_trigger: EvolutionTriggerPoint = EvolutionTriggerPoint.AFTER_INVOKE,
    async_evolution: bool = True,
    max_concurrent_evolution: int = 1,
    review_trigger: Optional[bool] = None,
    review_interval: int = 5,
)
```

**参数**：

* **skills_dir** (Union[str, list[str]]): skill 目录路径或路径列表。
* **llm** (Model): 信号、记录生成、评分和治理阶段使用的 LLM 客户端。
* **model** (str): 模型名称。
* **signal_trigger** (bool, 可选): invoke 后是否执行被动信号扫描，默认 `False`。
* **auto_save** (bool): 是否自动审批并持久化生成的被动记录，默认 `False`。
* **review_runtime** (EvolutionReviewRuntime): review 子智能体状态与中断审核绑定的共享运行时，active-review 依赖必须显式传入。
* **subject_kind** (str): 本 rail 的演进对象类型（`"skill"` 或 `"swarm-skill"`，会做统一归一化）。
* **language** (str): prompt 语言，常见值为 `"cn"` 或 `"en"`。
* **trajectory_span_processor** (TrajectorySpanProcessor): `get_trajectory_span_processor()` 返回的进程级共享 processor。单 Agent 宿主还必须 acquire observability、挂载 `AgentObservabilityRail`，并使用 `open_agent_run_span()` / `close_agent_run_span()` 包裹每个 Agent turn；参见 `trajectory_rail_example`。
* **eval_interval** (int): 经验展示评分检查间隔，必须大于等于 1。
* **evolution_total_timeout_secs** (float): 后台演进总超时预算。
* **generate_records_llm_policy** (LLMInvokePolicy): 经验记录生成阶段的 LLM 重试/超时策略。
* **evaluate_llm_policy** (LLMInvokePolicy): 经验评分阶段的 LLM 重试/超时策略。
* **simplify_llm_policy** (LLMInvokePolicy): simplify 治理阶段的 LLM 重试/超时策略。
* **two_stage** (bool): 经验记录生成是否使用 analyzer + formatter 两阶段流程，默认 `False`。
* **review_agent_max_iterations** (int): `evolution_reviewer` 的最大迭代次数，默认 25。
* **sharing_config** (dict, 可选): 跨用户共享设置，例如 `enabled` 和 `hub_path`。
* **disabled_skills** (Optional[Union[str, list[str]]], 可选): 排除自优化范围的技能拒绝列表。支持单个技能名（字符串）或多个技能名（字符串列表）。
* **evolution_trigger** (EvolutionTriggerPoint): 被动演进使用的 Rail callback 时点，默认 `AFTER_INVOKE`。
* **async_evolution** (bool): 是否异步执行被动演进，默认 `True`。
* **max_concurrent_evolution** (int): 被动演进后台任务的最大并发数，默认 1。
* **review_trigger** (bool, 可选): 是否周期性注入简短演进自检 follow_up，默认 `False`。
* **review_interval** (int): 两次 review 检查之间的非 follow_up task iteration 数，必须大于等于 1，默认 5。

### 优先级

`priority = 80`

### Regular + Team 共用提交服务约束

若同一进程同时启用 regular 与 team/swarm 演进，需要两个 rail 共享同一个 `EvolutionReviewRuntime` 和 `ExperienceSubmissionService`。建议手动组装并显式共享：

```python
from openjiuwen.harness.rails import (
    EvolutionInterruptRail,
    SkillEvolutionRail,
    TeamSkillEvolutionRail,
)
from openjiuwen.harness.rails.evolution import EvolutionReviewRuntime, build_evolve_review_command_prompt

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

---

## 生命周期

可观测生命周期如下：

```text
采集 trajectory
-> 检测 signals
-> local apply preview
-> pending approval 或 auto-approved
-> EvolutionStore persistence
-> evolutions.json 和 evolution/*.md projection
```

稳定职责边界：

* `EvolutionRail` 负责轨迹采集、callback context snapshot、后台任务和 host event buffer。
* `OnlineEvolutionOrchestrator` 协调 context build、update 生成和 local preview。
* `ExperienceManager + PendingChange` 拥有 pending approval 状态。
* `EvolutionStore` 拥有 durable write 和 projection。

所有 durable skill experience 写入都必须经过 `EvolutionStore`；宿主不应直接修改 `evolutions.json`。

---

## Host Events

消费演进事件的 canonical API 是 `drain_pending_host_events()`。`drain_pending_approval_events()` 是兼容 wrapper，读取同一个共享 host event buffer。

演进事件是 `OutputSchema` 对象，演进相关 metadata 位于：

```python
event.payload["evolution_meta"]
```

已知 metadata 字段：

| 字段 | 含义 |
|---|---|
| `event_kind` | `approval`、`progress` 或 `outcome`。 |
| `rail_kind` | 产生事件的 rail kind，如 `regular` 或 `team`。 |
| `stage` | progress 或 outcome 的生命周期阶段。 |
| `skill_name` | 目标 skill 名称。 |
| `request_id` | 审批请求 ID。 |
| `signal_type` | 参与生成请求的信号类型。 |
| `source` | 信号或事件来源。 |
| `status` | outcome 状态。 |

审批事件使用 `type="chat.ask_user_question"`，并包含 `payload["request_id"]`。进度事件使用 `type="llm_reasoning"`。后台失败会以 outcome 事件暴露，不会让主 invoke 失败。

`outcome` 事件是调用方可依赖的结构化终态事件。演进流程正常完成但没有生成记录时，SDK 会发出 `status="no_evolution_no_records"`。调用方不应解析 progress 文案来判断终态。

### Subject Schema（review 与 mutation Tool）

主动演进和审核工具共享 subject 封装：

```python
{
    "kind": "skill" | "swarm-skill",
    "name": "my-skill",
    "scope": { ... }  # 可选
}
```

`kind="team-skill"` 仍可作为历史兼容输入，运行时会归一到 `swarm-skill` 后再执行持久化与审批。

该 schema 适用于 `prepare_skill_evolution`、`list_skill_experiences`、`read_skill_experiences`、`evolve_skill_experiences`、`simplify_skill_experiences` 等。

---

## Async Snapshot Contract

当 `async_evolution=True` 时，rail 会在后台任务启动前 snapshot callback 数据。

| Snapshot 字段 | 含义 |
|---|---|
| `trajectory` | 本次 invoke 的完整轨迹。 |
| `messages` | 对话消息，优先从 trajectory 派生，必要时 fallback 到 callback/session 数据。 |
| `skill_name` | 可选标签，由具体 rail 或 snapshot 使用。 |

`messages` 是检测上下文，`trajectory` 是执行证据。不要把 snapshot dict 当成 public 序列化格式；public 集成点应使用 host event 和 rail 方法。

---

## 属性

### evolution_store -> EvolutionStore

Skill experience 数据的演进存储。执行轨迹仍由注入的 processor 和 Rail clean window 管理。

### store -> EvolutionStore

`evolution_store` 的兼容别名。

### scorer -> ExperienceScorer

经验评分器。

### evolver -> SkillExperienceOptimizer

普通 skill 经验优化器。

### evolution_config -> dict

生效的记录生成、评分和 simplify LLM 策略、总超时以及 `two_stage` 模式。

---

## 方法

### build_evolve_review_command_prompt(*, subject, user_intent=None, review_agent_name="evolution_reviewer", language="cn") -> str

构造主动 `/evolve` follow-up prompt，从 `openjiuwen.harness.rails.evolution` 导入。Host 应通过
`EvolutionStore.resolve_subject_payload(skill_name)` 解析真实 subject kind，并在产生证据轨迹的同一 Agent
Session 中运行返回的 prompt。

显式 command path 与 `signal_trigger` 相互独立：前者是用户授权的审核请求，后者开启 invoke 后的被动检测。

### 兼容方法：async request_user_evolution(skill_name, user_intent="", *, auto_approve=None, max_index_records=None) -> EvolutionRequestResult

为普通 skill 构造主动演进 command prompt 的兼容 wrapper。新的 host command handler 应在解析 subject 后直接
调用 `build_evolve_review_command_prompt()`。

**参数**：

* **skill_name** (str): 目标普通 skill 名称。
* **user_intent** (str): 用户改进意图，默认 `""`。
* **auto_approve** (bool, 可选): 为兼容旧调用保留，主动审核链路会忽略该值。
* **max_index_records** (int, 可选): 为兼容旧调用保留，主动审核链路会忽略该值。

**返回**：

* `EvolutionRequestResult`: `mode="agent_prompt"`，`followup_prompt` 由 host 注入 agent loop；不会暂存记录，也不会发出审批事件。

### async approve_record(request_id, *, approved_record_ids=None) -> None

审批暂存记录，并通过 `EvolutionStore` 写入。

提供 `approved_record_ids` 时只审批选中的暂存记录；省略时审批全部暂存记录。

如果发生部分失败，未写入的尾部会保留在同一个 `PendingChange` 中；宿主可使用同一个 `request_id` 重试。

### async reject_record(request_id) -> None

拒绝暂存记录，不写入。

### async request_simplify(skill_name, user_intent=None, *, mode="agent_prompt", max_index_records=100) -> SimplifyRequestResult

构造由 host 投递的 simplify command prompt。prompt 会包含受 `max_index_records` 限制的经验摘要索引，
并要求 agent 使用 `list_skill_experiences`、`read_skill_experiences` 和 `simplify_skill_experiences`。

**返回**：

* `SimplifyRequestResult`: `mode="agent_prompt"` 和 `followup_prompt`。它不会调用 scorer、暂存治理动作或发出审批事件。

### async request_rebuild(skill_name, user_intent=None, min_score=0.5, max_context_records=40, max_context_chars=20000) -> Optional[str]

归档当前 skill 资产，并基于筛选后的演进记录返回 rebuild follow-up prompt。宿主或命令处理器需要把返回的 prompt 注入 agent loop；rail 不会直接写出重建后的 `SKILL.md`。

`max_context_records` 和 `max_context_chars` 限制 prompt 中内联的确定性 rebuild context。

### async drain_pending_host_events(wait=False, timeout=None) -> list[OutputSchema]

返回并清空 buffered host events。若 `wait=True`，会在 `timeout` 内等待后台演进任务完成。

### async drain_pending_approval_events(wait=False, timeout=None) -> list[OutputSchema]

`drain_pending_host_events()` 的兼容 wrapper。

---

## 示例

可运行模块 `examples.agent_evolving.skill_evolution_example` 同时包含普通 Skill 创建与演进案例，并包含
span 采集所需、由宿主持有的单 Agent root trace。使用 `--scenario create` 或 `--scenario evolve` 选择
单个案例，使用 `--scenario all` 依次运行两者。

```python
from openjiuwen.harness import create_deep_agent
from openjiuwen.core.runner import Runner
from openjiuwen.harness.rails import EvolutionInterruptRail, SkillEvolutionRail
from openjiuwen.harness.rails.evolution import EvolutionReviewRuntime

runtime = EvolutionReviewRuntime()
skill_rail = SkillEvolutionRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    trajectory_span_processor=runtime_processor,
    review_runtime=runtime,
    auto_save=False,
)
interrupt_rail = EvolutionInterruptRail(
    review_runtime=runtime,
    submission_service=skill_rail.approval_submission_service,
)

agent = create_deep_agent(
    model=model_client,
    tools=tools,
    rails=[interrupt_rail, skill_rail],
)

subject = skill_rail.store.resolve_subject_payload("code-review")
followup_prompt = build_evolve_review_command_prompt(
    subject=subject,
    user_intent="优先输出行为级问题，再输出风格建议",
)
# 在产生轨迹的同一 Session 中作为下一条 query 运行。
await Runner.run_agent(agent, {"query": followup_prompt}, session=session)
```
