# 团队技能 Rails

团队技能相关的 Rails，用于自动化团队技能创建和在线演进。

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

团队技能演进 Rail，类似 `SkillEvolutionRail` 但专门处理团队技能。

### 功能

- 轨迹问题检测（角色配合、约束违反、流程低效）
- 用户请求演进
- PATCH 生成和审批
- 经验简化/重建

### 触发机制

- 监听 `view_task` 工具结果，检测"所有任务已完成"
- 支持被动轨迹分析和主动用户请求两种演进路径

```text
class TeamSkillRail(
    skills_dir: Union[str, list[str]],
    *,
    llm: Model,
    model: str,
    language: str = "cn",
    trajectory_store: Optional[TrajectoryStore] = None,
    team_trajectory_store: Optional[TrajectoryStore] = None,
    auto_save: bool = False,
    async_evolution: bool = True,
    team_id: Optional[str] = None,
    trajectories_dir: Optional[Path] = None,
    user_request_llm_policy: LLMInvokePolicy = ...,
    trajectory_issue_llm_policy: LLMInvokePolicy = ...,
    patch_llm_policy: LLMInvokePolicy = ...,
    evaluate_llm_policy: LLMInvokePolicy = ...,
    simplify_llm_policy: LLMInvokePolicy = ...,
    evolution_total_timeout_secs: float = 600.0,
)
```

**参数**：

* **skills_dir** (Union[str, list[str]]): 技能目录路径或路径列表。
* **llm** (Model): LLM 客户端实例。
* **model** (str): 模型名称。
* **language** (str): 语言设置。
* **trajectory_store** (TrajectoryStore, 可选): 轨迹存储实例。
* **team_trajectory_store** (TrajectoryStore, 可选): 团队轨迹存储实例。
* **auto_save** (bool): 是否自动保存 PATCH，默认 `False`（需用户审批）。
* **async_evolution** (bool): 是否异步执行演进，默认 `True`。
* **team_id** (str, 可选): 团队 ID。
* **trajectories_dir** (Path, 可选): 轨迹目录路径。
* **user_request_llm_policy** (LLMInvokePolicy): 用户意图检测 LLM 调用策略。
* **trajectory_issue_llm_policy** (LLMInvokePolicy): 轨迹问题检测 LLM 调用策略。
* **patch_llm_policy** (LLMInvokePolicy): PATCH 生成 LLM 调用策略。
* **evaluate_llm_policy** (LLMInvokePolicy): 经验评估 LLM 调用策略。
* **simplify_llm_policy** (LLMInvokePolicy): 经验简化 LLM 调用策略。
* **evolution_total_timeout_secs** (float): 后台演进总超时预算，默认 600s。

### 优先级

`priority = 80`

---

## 属性

### store -> EvolutionStore

演进存储实例。

### scorer -> ExperienceScorer

经验评分器。

### optimizer -> TeamSkillOptimizer

团队技能优化器。

### evolution_config -> dict

完整演进配置，包含各阶段 LLM 调用策略和超时设置。

---

## 方法

### async notify_team_completed(ctx) -> bool

触发技能演进（当所有任务完成时）。

**参数**：

* **ctx** (AgentCallbackContext, 可选): 回调上下文。

**返回**：

* `bool`: 是否成功触发演进。

---

### async request_user_evolution(skill_name, user_intent, auto_approve) -> Optional[str]

用户主动请求演进。

**参数**：

* **skill_name** (str): 目标技能名称。
* **user_intent** (str): 用户改进意图描述。
* **auto_approve** (bool): 是否自动审批，默认 `False`。

**返回**：

* `str`: request_id 或 `None`（技能不存在或无 PATCH 时）。

---

### async request_simplify(skill_name, user_intent) -> Optional[Dict[str, int]]

请求经验简化，直接执行无需审批。

**参数**：

* **skill_name** (str): 目标技能名称。
* **user_intent** (str, 可选): 用户简化意图。

**返回**：

* `Dict[str, int]`: 简化动作计数 `{"deleted": N, "merged": N, "refined": N, "kept": N, "errors": N}` 或 `None`。

---

### async request_rebuild(skill_name, user_intent, min_score) -> Optional[str]

请求技能重建（归档旧版本并生成新版本）。

**参数**：

* **skill_name** (str): 目标技能名称。
* **user_intent** (str, 可选): 用户重建意图。
* **min_score** (float): 演进经验筛选阈值，默认 0.5。

**返回**：

* `str`: 重建提示文本或 `None`（技能不存在时）。

---

### async on_approve_patch(request_id) -> None

审批 PATCH，将暂存的 PATCH 写入 `evolutions.json`。

**参数**：

* **request_id** (str): 请求 ID。

---

### async on_reject_patch(request_id) -> None

拒绝 PATCH，清除暂存记录。

**参数**：

* **request_id** (str): 请求 ID。

---

### async drain_pending_approval_events(wait, timeout) -> List[OutputSchema]

获取待审批事件列表。

**参数**：

* **wait** (bool): 是否等待事件到达。
* **timeout** (float, 可选): 等待超时时间，默认使用 `evolution_total_timeout_secs`。

**返回**：

* `List[OutputSchema]`: 待审批事件列表。

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
from openjiuwen.harness.rails import TeamSkillCreateRail, TeamSkillRail
from openjiuwen.harness import create_deep_agent

# 创建团队技能创建 Rail
create_rail = TeamSkillCreateRail(
    skills_dir="/path/to/skills",
    min_team_members_for_create=2,
)

# 创建团队技能演进 Rail
team_rail = TeamSkillRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
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
request_id = await team_rail.request_user_evolution(
    skill_name="research-team",
    user_intent="增加 reviewer 角色，限制 research 时间不超过 10 分钟",
)

# 获取审批事件
events = await team_rail.drain_pending_approval_events(wait=True)
for event in events:
    if event.type == "chat.ask_user_question":
        request_id = event.payload["request_id"]
        # 用户审批
        await team_rail.on_approve_patch(request_id)

# 请求简化
result = await team_rail.request_simplify("research-team")

# 请求重建
prompt = await team_rail.request_rebuild("research-team", min_score=0.5)
```