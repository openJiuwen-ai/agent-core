# Skill Evolution Rail

普通 Skill 在线演进 public Rail。本文只覆盖已有普通 skill 的经验演进，不覆盖新建 skill，也不覆盖 team skill 演进。

---

## class SkillEvolutionRail

public Rail，用于采集 agent 轨迹、检测普通 skill 的可复用改进、暂存生成的经验记录，并通过 `EvolutionStore` 写入已审批记录。

### 导入

```python
from openjiuwen.harness.rails import (
    EvolutionReviewRuntime,
    SkillEvolutionRail,
    SubagentRail,
    configure_skill_evolution,
)
```

`SkillEvolutionRail` 的主动演进 review 流程会委托 `evolution_reviewer` 子智能体，因此必须与 `SubagentRail` 一起注册。同步子智能体模式会注册 `task_tool`，供 follow-up prompt 调用 review 子智能体。

`SkillEvolutionRail.init()` 不再配置 `EvolutionInterruptRail`，它只负责注册 review 工具与稳定 review subagent。

稳定 review subagent 名称为 `evolution_reviewer`，重复注册时若 `runtime` / `query_service` / `store` 不一致会直接报错（fail fast）。

### 推荐优先 / 推荐构建方式

建议优先使用配置 API：

```python
configure_skill_evolution(
    agent,
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    auto_save=False,
    language="cn",
)
```

配置 API 会在缺少 `SubagentRail` 时自动补齐，并将 `EvolutionInterruptRail` 与普通 `SkillEvolutionRail` 正确绑定。

手工组装时必须显式共享：

```python
runtime = EvolutionReviewRuntime()
skill_rail = SkillEvolutionRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    review_runtime=runtime,
    auto_save=False,
)
interrupt_rail = EvolutionInterruptRail(
    review_runtime=runtime,
    submission_service=skill_rail.experience_manager.experience_submission_service,
)
agent = create_deep_agent(
    model=model_client,
    tools=tools,
    rails=[SubagentRail(), interrupt_rail, skill_rail],
)
```

手工组合时必须只保留一个共享的 `EvolutionInterruptRail`，并用同一 `review_runtime` + `submission_service` 绑定；`EvolutionInterruptRail` 不按 subject kind 路由。

### 触发机制

- 被动演进在 `DeepAgent.invoke()` 完成后运行。
- `signal_trigger` 控制被动信号扫描；`auto_scan` 是兼容别名。二者默认关闭。
- `review_trigger` 控制周期性自检 follow_up 注入；`fuzzy_review` 是兼容别名。二者默认关闭。
- 迁移期如果同时传入新旧参数名，以新参数名的值为准。
- `auto_scan=False` 会关闭被动信号扫描，也会跳过被动演进的 async snapshot。
- 主动演进通过 `request_user_evolution()` 触发；返回的 prompt 会要求主 agent 先调用 `prepare_skill_evolution(user_confirmed=true)`，再用返回的 `evolution_review_ref` 委托 `evolution_reviewer`。prepare tool 会把当前 rail 已采集到的执行/对话轨迹作为默认 review materials，`user_intent` 只补充优化方向。
- 普通 skill 演进会忽略 `kind: team-skill`；team skill 使用 `TeamSkillEvolutionRail` / `TeamSkillRail`。

```text
class SkillEvolutionRail(
    skills_dir: Union[str, list[str]],
    *,
    llm: Model,
    model: str,
    review_runtime: EvolutionReviewRuntime,
    auto_scan: Optional[bool] = None,
    signal_trigger: Optional[bool] = None,
    auto_save: bool = False,
    subject_kind: str = "skill",
    language: str = "cn",
    trajectory_store: Optional[TrajectoryStore] = None,
    eval_interval: int = 5,
    evolution_total_timeout_secs: float = 600.0,
    generate_records_llm_policy: LLMInvokePolicy = ...,
    evaluate_llm_policy: LLMInvokePolicy = ...,
    simplify_llm_policy: LLMInvokePolicy = ...,
    fuzzy_review: Optional[bool] = None,
    review_trigger: Optional[bool] = None,
    fuzzy_review_interval: int = 5,
    disabled_skills: Optional[Union[str, list[str]]] = None,
)
```

**参数**：

* **skills_dir** (Union[str, list[str]]): skill 目录路径或路径列表。
* **llm** (Model): 信号、记录生成、评分和治理阶段使用的 LLM 客户端。
* **model** (str): 模型名称。
* **review_runtime** (EvolutionReviewRuntime): review 子智能体状态与中断审核绑定的共享运行时，active-review 依赖必须显式传入。
* **auto_scan** (bool, 可选): `signal_trigger` 的兼容别名；已设置 `signal_trigger` 时忽略该值。
* **signal_trigger** (bool, 可选): invoke 后是否执行被动信号扫描，默认 `False`。
* **auto_save** (bool): 是否自动审批并持久化生成的被动记录，默认 `False`。
* **subject_kind** (str): 本 rail 的演进对象类型（`"skill"` 或 `"swarm-skill"`，会做统一归一化）。
* **language** (str): prompt 语言，常见值为 `"cn"` 或 `"en"`。
* **trajectory_store** (TrajectoryStore, 可选): 执行轨迹存储。
* **eval_interval** (int): 经验展示评分检查间隔，必须大于等于 1。
* **evolution_total_timeout_secs** (float): 后台演进总超时预算。
* **generate_records_llm_policy** (LLMInvokePolicy): 经验记录生成阶段的 LLM 重试/超时策略。
* **evaluate_llm_policy** (LLMInvokePolicy): 经验评分阶段的 LLM 重试/超时策略。
* **simplify_llm_policy** (LLMInvokePolicy): simplify 治理阶段的 LLM 重试/超时策略。
* **fuzzy_review** (bool, 可选): `review_trigger` 的兼容别名；已设置 `review_trigger` 时忽略该值。
* **review_trigger** (bool, 可选): 是否周期性注入简短演进自检 follow_up，默认 `False`。
* **fuzzy_review_interval** (int): 两次自检检查之间的非 follow_up task iteration 数，必须大于等于 1。
* **disabled_skills** (Optional[Union[str, list[str]]], 可选): 排除自优化范围的技能拒绝列表。支持单个技能名（字符串）或多个技能名（字符串列表）。

### 优先级

`priority = 80`

### Regular + Team 共用提交服务约束

若同一进程同时启用 regular 与 team/swarm 演进，需要两个 rail 共享同一个 `EvolutionReviewRuntime` 和 `ExperienceSubmissionService`。建议手动组装并显式共享：

```python
from openjiuwen.harness.rails import (
    SubagentRail,
    EvolutionInterruptRail,
    EvolutionReviewRuntime,
    SkillEvolutionRail,
    TeamSkillRail,
)

runtime = EvolutionReviewRuntime()
skill_rail = SkillEvolutionRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    review_runtime=runtime,
)
team_rail = TeamSkillRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    review_runtime=runtime,
    team_id="research-team",
)
interrupt_rail = EvolutionInterruptRail(
    review_runtime=runtime,
    submission_service=skill_rail.experience_manager.experience_submission_service,
)
rails = [SubagentRail(), interrupt_rail, skill_rail, team_rail]
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

skill 数据的演进存储，与 `trajectory_store` 不同。

### store -> EvolutionStore

`evolution_store` 的兼容别名。

### scorer -> ExperienceScorer

经验评分器。

### evolver -> SkillExperienceOptimizer

普通 skill 经验优化器。

### evolution_config -> dict

生效的 LLM 策略、超时、`auto_scan`、`auto_save` 和 `eval_interval`。

---

## 方法

### async request_user_evolution(skill_name, user_intent, *, max_index_records=20) -> EvolutionRequestResult

为普通 skill 构造由 host 投递的主动演进 command prompt。该 prompt 不直接创建 review scope，而是要求主 agent 调用 `prepare_skill_evolution(user_confirmed=true)`，再用返回的 `evolution_review_ref` 调 `task_tool(subagent_type="evolution_reviewer")`。

**参数**：

* **skill_name** (str): 目标普通 skill 名称。
* **user_intent** (str): 用户改进意图。
* **max_index_records** (int): prompt 预览中最多内联的经验索引条目数。

**返回**：

* `EvolutionRequestResult`: `mode="agent_prompt"`，`followup_prompt` 由 host 注入 agent loop；不会暂存记录，也不会发出审批事件。

### async approve_record(request_id) -> None

审批暂存记录，并通过 `EvolutionStore` 写入。

如果发生部分失败，未写入的尾部会保留在同一个 `PendingChange` 中；宿主可使用同一个 `request_id` 重试。

### async reject_record(request_id) -> None

拒绝暂存记录，不写入。

### async request_simplify(skill_name, user_intent=None, mode="agent_prompt") -> SimplifyRequestResult

构造由 host 投递的 simplify command prompt。prompt 会包含有界经验摘要索引，并要求 agent 使用 `list_skill_experiences`、`read_skill_experiences` 和 `simplify_skill_experiences`。

**返回**：

* `SimplifyRequestResult`: `mode="agent_prompt"` 和 `followup_prompt`。它不会调用 scorer、暂存治理动作或发出审批事件。

### async request_rebuild(skill_name, user_intent=None, min_score=0.5) -> Optional[str]

归档当前 skill 资产，并基于筛选后的演进记录返回 rebuild follow-up prompt。宿主或命令处理器需要把返回的 prompt 注入 agent loop；rail 不会直接写出重建后的 `SKILL.md`。

### async drain_pending_host_events(wait=False, timeout=None) -> list[OutputSchema]

返回并清空 buffered host events。若 `wait=True`，会在 `timeout` 内等待后台演进任务完成。

### async drain_pending_approval_events(wait=False, timeout=None) -> list[OutputSchema]

`drain_pending_host_events()` 的兼容 wrapper。

---

## 示例

```python
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails import SkillEvolutionRail, SubagentRail

skill_rail = SkillEvolutionRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    auto_save=False,
)

agent = create_deep_agent(
    model=model_client,
    tools=tools,
    rails=[
        skill_rail,
        SubagentRail(),
    ],
)

result = await skill_rail.request_user_evolution(
    "code-review",
    "优先输出行为级问题，再输出风格建议",
)

if result.followup_prompt:
    # Host 投递方式由应用决定：可以作为下一条 query、follow-up，
    # 或其他等价消息注入 agent loop。
    await agent.invoke({"query": result.followup_prompt})
```
