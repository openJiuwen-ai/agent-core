# openjiuwen.agent_evolving.optimizer.team_skill_optimizer

`openjiuwen.agent_evolving.optimizer.team_skill_optimizer` 提供 LLM 驱动的团队技能 PATCH 生成和演进支持，从执行轨迹中提炼可复用的团队协作经验。

---

## class TeamSkillOptimizer

LLM 驱动的团队技能 PATCH 生成器，支持从轨迹分析、用户意图和轨迹问题三个维度生成演进 PATCH。

```text
class TeamSkillOptimizer(
    llm: Model,
    model: str,
    language: str = "cn",
    debug_dir: Optional[str] = None,
    patch_llm_policy: LLMInvokePolicy = ...,
)
```

**参数**：

* **llm** (Model): LLM 客户端实例。
* **model** (str): 模型名称。
* **language** (str): 语言设置，支持 `"cn"` 或 `"en"`，默认 `"cn"`。
* **debug_dir** (str, 可选): 调试输出目录，用于保存 LLM 原始响应。
* **patch_llm_policy** (LLMInvokePolicy): PATCH 生成的 LLM 调用策略，默认超时 120s、总预算 420s、最大重试 3 次。

---

## 属性

### language -> str

语言设置（`"cn"` 或 `"en"`）。

### llm -> Model

LLM 客户端实例。

### model -> str

模型名称。

### patch_llm_policy -> LLMInvokePolicy

PATCH 生成的 LLM 调用策略配置。

---

## 方法

### async generate_patch(trajectory, skill_name, current_skill_content) -> Optional[EvolutionRecord]

分析轨迹与现有技能对比，生成 PATCH（如需要）。

**参数**：

* **trajectory** (Trajectory): 执行轨迹。
* **skill_name** (str): 技能名称。
* **current_skill_content** (str): 当前技能 SKILL.md 内容。

**返回**：

* `EvolutionRecord` 或 `None`（无需 PATCH 时）。

---

### async generate_user_patch(trajectory, skill_name, user_intent) -> Optional[EvolutionRecord]

根据用户明确改进意图生成 PATCH。

**参数**：

* **trajectory** (Trajectory): 执行轨迹（可为空或最小占位）。
* **skill_name** (str): 技能名称。
* **user_intent** (str): 用户改进意图描述。

**返回**：

* `EvolutionRecord` 或 `None`。

---

### async generate_trajectory_patch(trajectory, skill_name, current_skill_content, trajectory_issues) -> Optional[EvolutionRecord]

根据轨迹问题分析结果生成 PATCH。

**参数**：

* **trajectory** (Trajectory): 执行轨迹。
* **skill_name** (str): 技能名称。
* **current_skill_content** (str): 当前技能内容。
* **trajectory_issues** (list[dict]): 轨迹问题列表，每个元素包含 `issue_type`、`description`、`affected_role`、`severity`。

**返回**：

* `EvolutionRecord` 或 `None`。

---

### async regenerate_body(skill_name, current_body, evolution_records, user_intent) -> Optional[str]

重写技能 body，融合演进经验。

**参数**：

* **skill_name** (str): 技能名称。
* **current_body** (str): 当前 SKILL.md body 内容。
* **evolution_records** (List): 演进记录列表。
* **user_intent** (str, 可选): 用户指定的优化方向。

**返回**：

* 新的 body 文本或 `None`（LLM 失败或拒绝时）。

---

### update_llm(llm, model) -> None

更新 LLM 客户端和模型名称。

---

### staticmethod build_trajectory_summary(trajectory) -> str

构建轨迹摘要文本，供 LLM 分析使用。优先提取协作相关工具调用（`spawn_member`、`create_task`、`build_team` 等），其次为 LLM 响应。

---

### staticmethod parse_json(raw) -> Optional[Dict]

解析 LLM 输出的 JSON，支持代码块提取、语法修复、平衡括号扫描等多种策略。

---

## 示例

```python
from openjiuwen.agent_evolving.optimizer.team_skill_optimizer import TeamSkillOptimizer
from openjiuwen.core.foundation.llm.model import Model

# 创建优化器
optimizer = TeamSkillOptimizer(
    llm=model_client,
    model="gpt-4",
    language="cn",
)

# 生成轨迹 PATCH
record = await optimizer.generate_trajectory_patch(
    trajectory=team_trajectory,
    skill_name="research-team",
    current_skill_content=skill_md_content,
    trajectory_issues=[
        {"issue_type": "coordination", "description": "handoff 太松散", "severity": "medium"}
    ],
)

# 生成用户 PATCH
record = await optimizer.generate_user_patch(
    trajectory=trajectory,
    skill_name="research-team",
    user_intent="增加 reviewer 角色，限制 research 时间不超过 10 分钟",
)

# 构建轨迹摘要
summary = TeamSkillOptimizer.build_trajectory_summary(trajectory)
```