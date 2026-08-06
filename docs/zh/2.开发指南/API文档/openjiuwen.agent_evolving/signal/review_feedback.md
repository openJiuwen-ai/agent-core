# Reviewer Feedback 归因

`openjiuwen.agent_evolving.signal.review_feedback` 将任务 reviewer 的聚合 feedback 归因为结构化、
可安全执行的下游动作。模块本身不订阅团队事件、不修改 Skill，也不创建 Skill；运行时接线由宿主负责。

## 导入

```python
from openjiuwen.agent_evolving.signal import (
    ReviewFeedbackAction,
    ReviewFeedbackAttribution,
    ReviewFeedbackAttributor,
    ReviewFeedbackClassification,
    ReviewFeedbackContext,
    ReviewFeedbackContextBuilder,
    attribution_to_evolution_signal,
)
```

## 归因结果

| classification | action | 含义 |
|---|---|---|
| `skill_issue` | `evolve_existing_skill` | 已读 Skill 缺少或写错了可复用指导。 |
| `new_skill_pattern` | `suggest_new_skill` | 没有可归因的已读 Skill，但同类可复用模式已重复出现。 |
| `executor_error` | `record_task_failure` | Skill 指导充分，执行者没有遵循；不修改 Skill。 |
| `unattributed` | `skip_unattributed` | 证据不足或无法安全归因。 |

`ReviewFeedbackAttribution` 还包含 `is_skill_actionable`、`skill_name`、`target`、`reason`、
`reusable_guidance`、`confidence` 和有界 `feedback_excerpt`。

## class ReviewFeedbackAttributor

```python
attributor = ReviewFeedbackAttributor(
    llm=model_client,
    model="model-name",
    language="cn",
    timeout=30.0,
)

result = await attributor.attribute(feedback, context=context)
```

归因器用一次结构化 LLM 调用判断语义，再用确定性策略校验模型输出。模型异常、无效 JSON、空输入、
不可信 Skill 名或证据不足都会 fail closed 为 `skip_unattributed`。

## class ReviewFeedbackContextBuilder

```python
context = await ReviewFeedbackContextBuilder(store=evolution_store).build(
    task_id="task-1",
    review_round=1,
    task_objective="Build and validate a workbook",
    trajectory=member_trajectory,
    repetition_count=1,
    repeated_pattern_evidence=(),
)
```

builder 从轨迹工具调用参数中提取 `SKILL.md` 读取证据，并加载对应 Skill 正文作为有界归因上下文。
仅安装 Skill、模型在文本中提到 Skill、或工具结果中出现 Skill 名，都不足以证明本次任务使用过它。

## 安全边界

- 只有 `skill_issue` 可以演进已有 Skill。
- 目标 Skill 必须属于轨迹证明已读且当前仍可加载的集合。
- 现有 Skill 演进必须包含可复用指导和明确的 `description`、`body` 或 `script` target。
- `executor_error` 永远不会转换为 Skill mutation。
- 新建 Skill 建议需要重复证据；本模块只返回建议，不执行创建。

## 转换为标准 EvolutionSignal

```python
signal = attribution_to_evolution_signal(
    result,
    task_id="task-1",
    review_round=1,
)
```

只有可执行的 `evolve_existing_skill` 结果会转换成功；其余 action 返回 `None`。产生的 signal 使用
`signal_type="review_feedback"` 和 `source="scheduler_review_feedback"`，可交给
`SkillEvolutionRail.evolve_from_external_signals(...)` 继续走标准经验生成与审批流程。
