# Skill Evolution Rail

普通 Skill 在线演进 public Rail。本文只覆盖已有普通 skill 的经验演进，不覆盖新建 skill，也不覆盖 team skill 演进。

---

## class SkillEvolutionRail

public Rail，用于采集 agent 轨迹、检测普通 skill 的可复用改进、暂存生成的经验记录，并通过 `EvolutionStore` 写入已审批记录。

### 导入

```python
from openjiuwen.harness.rails import SkillEvolutionRail
```

### 触发机制

- 被动演进在 `DeepAgent.invoke()` 完成后运行。
- `auto_scan=False` 会关闭被动信号扫描，也会跳过被动演进的 async snapshot。
- 主动演进通过 `request_user_evolution()` 触发；当前 rail 已采集到的执行/对话轨迹会作为默认证据，`user_intent` 只补充优化方向。
- 普通 skill 演进会忽略 `kind: team-skill`；team skill 使用 `TeamSkillEvolutionRail` / `TeamSkillRail`。

```text
class SkillEvolutionRail(
    skills_dir: Union[str, list[str]],
    *,
    llm: Model,
    model: str,
    auto_scan: bool = True,
    auto_save: bool = True,
    language: str = "cn",
    trajectory_store: Optional[TrajectoryStore] = None,
    team_trajectory_store: Optional[TrajectoryStore] = None,
    eval_interval: int = 5,
    evolution_total_timeout_secs: float = 600.0,
    generate_records_llm_policy: LLMInvokePolicy = ...,
    evaluate_llm_policy: LLMInvokePolicy = ...,
    simplify_llm_policy: LLMInvokePolicy = ...,
    disabled_skills: Optional[Union[str, list[str]]] = None,
)
```

**参数**：

* **skills_dir** (Union[str, list[str]]): skill 目录路径或路径列表。
* **llm** (Model): 信号、记录生成、评分和治理阶段使用的 LLM 客户端。
* **model** (str): 模型名称。
* **auto_scan** (bool): invoke 后是否执行被动信号扫描，默认 `True`。
* **auto_save** (bool): 是否自动审批并持久化生成的被动记录，默认 `True`；生产宿主通常应设置为 `False` 并消费审批事件。
* **language** (str): prompt 语言，常见值为 `"cn"` 或 `"en"`。
* **trajectory_store** (TrajectoryStore, 可选): 执行轨迹存储。
* **team_trajectory_store** (TrajectoryStore, 可选): 已废弃的共享轨迹存储参数。传入时会产生 deprecation warning，且不会为普通 skill 演进启用运行时团队聚合。
* **eval_interval** (int): 经验展示评分检查间隔，必须大于等于 1。
* **evolution_total_timeout_secs** (float): 后台演进总超时预算。
* **generate_records_llm_policy** (LLMInvokePolicy): 经验记录生成阶段的 LLM 重试/超时策略。
* **evaluate_llm_policy** (LLMInvokePolicy): 经验评分阶段的 LLM 重试/超时策略。
* **simplify_llm_policy** (LLMInvokePolicy): simplify 治理阶段的 LLM 重试/超时策略。
* **disabled_skills** (Optional[Union[str, list[str]]], 可选): 排除自优化范围的技能拒绝列表。支持单个技能名（字符串）或多个技能名（字符串列表）。

### 优先级

`priority = 80`

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
| `request_id` | 审批或治理请求 ID。 |
| `signal_type` | 参与生成请求的信号类型。 |
| `source` | 信号或事件来源。 |
| `status` | outcome 状态。 |

审批事件使用 `type="chat.ask_user_question"`，并包含 `payload["request_id"]`。进度事件使用 `type="llm_reasoning"`。后台失败会以 outcome 事件暴露，不会让主 invoke 失败。

`outcome` 事件是调用方可依赖的结构化终态事件。演进流程正常完成但没有生成记录时，SDK 会发出 `status="no_evolution_no_records"`。调用方不应解析 progress 文案来判断终态。

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

### async request_user_evolution(skill_name, user_intent="", *, auto_approve=False) -> EvolutionRequestResult

为普通 skill 暂存一次主动演进请求。该方法优先使用当前 rail 的有界轨迹证据窗口检测执行信号和用户反馈；`user_intent` 非空时会作为显式请求信号追加，而不会覆盖轨迹证据。

**参数**：

* **skill_name** (str): 目标普通 skill 名称。
* **user_intent** (str): 用户改进意图，默认 `""`。为空时，若当前轨迹证据中存在可演进信号，仍可生成请求。
* **auto_approve** (bool): 是否自动审批生成的请求，默认 `False`。

**返回**：

* `EvolutionRequestResult`: 生成记录时 `request_id` 有值，否则返回空结果对象。

### async approve_record(request_id) -> None

审批暂存记录，并通过 `EvolutionStore` 写入。

如果发生部分失败，未写入的尾部会保留在同一个 `PendingChange` 中；宿主可使用同一个 `request_id` 重试。

### async reject_record(request_id) -> None

拒绝暂存记录，不写入。

### async request_simplify(skill_name, user_intent=None) -> Optional[str]

暂存 simplify proposal，并发出审批事件。

**返回**：

* `str`: 生成治理动作时返回 governance request id，否则返回 `None`。

使用 `on_approve_simplify(request_id)` 执行，使用 `on_reject_simplify(request_id)` 放弃。

### async request_rebuild(skill_name, user_intent=None, min_score=0.5) -> Optional[str]

归档当前 skill 资产，并基于筛选后的演进记录返回 rebuild follow-up prompt。宿主或命令处理器需要把返回的 prompt 注入 agent loop；rail 不会直接写出重建后的 `SKILL.md`。

### async drain_pending_host_events(wait=False, timeout=None) -> list[OutputSchema]

返回并清空 buffered host events。若 `wait=True`，会在 `timeout` 内等待后台演进任务完成。

### async drain_pending_approval_events(wait=False, timeout=None) -> list[OutputSchema]

`drain_pending_host_events()` 的兼容 wrapper。

### async generate_and_emit_experience(...) -> bool

旧 host-driven/manual evolution 入口的兼容 wrapper。新集成应使用 `request_user_evolution()`。

---

## 示例

```python
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails import SkillEvolutionRail

skill_rail = SkillEvolutionRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    auto_save=False,
)

agent = create_deep_agent(
    model=model_client,
    tools=tools,
    rails=[skill_rail],
)

result = await skill_rail.request_user_evolution(
    "code-review",
    "优先输出行为级问题，再输出风格建议",
)

if result.approval_event is not None:
    await skill_rail.approve_record(result.request_id)
```
