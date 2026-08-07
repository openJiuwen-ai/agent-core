创建团队任务并指派承担者（仅 Leader 可用）。
**任务应聚焦于可交付成果和验收标准，不要规划具体执行步骤。**

本团队运行在**调度指派模式**：任务不进入公共认领池，成员不会自主认领。**每个任务都必须通过 `assignee` 指定承担者，未指派的任务永远不会有人执行。**

{{create_task_edge_semantics}}

## 任务字段

- **title**: 简明描述任务目标（祈使语气，如 "实现用户认证"）
- **content**: 目标、验收标准和约束 — 不写具体操作步骤
- **assignee**（必填）: 承担该任务的成员名称。**该成员必须已经存在且不能是leader**——先 `spawn_teammate` 建人，再 `create_task` 派活
- **reviewer**（每个任务需要至少 1 个）: 结构化对象的列表，每项必填 `type`（verifier / inspector / challenger）和 `reviewer_id`（名称，不能等于 assignee），可选 `instruction`（verifier 的验证侧重点描述）。例如 `{"type": "verifier", "reviewer_id": "功能正确性验证", "instruction": "运行测试用例..."}`。配了验证者的任务完成后进入 `in_review` 验收，任一 reviewer 投 fail 即打回（一票否决制）。reviewer 不需要提前 spawn——调度框架会根据 type 自动创建对应类型的临时验证智能体。
- **max_review_rounds**（可选，需配 reviewer）: 验证返工轮数上限，超限后不再自动打回而是升级给你处置；不传用团队默认
- **task_id** (必填) : 自定义 ID，用于依赖引用 
- **depends_on**（可选）: **"我依赖谁"** — 前置任务 ID 列表，须先完成才能开始本任务；可引用同批或已有任务, 填写依赖的时候要确保task_id是正确的.
- **depended_by**（可选）: **"谁依赖我"**（反向依赖）— 需要等待本任务完成的**已有**任务 ID 列表；不得引用同批任务

任务初始状态由依赖决定：**无依赖**的任务落地即 `pending` 并归属 assignee（已指派、未开始），调度框架随即为它开始并通知开工；**有未解决依赖**的任务落地为 `blocked`，assignee 已经记录在案，依赖全部完成后自动回到 `pending`，等调度框架开始。你不需要事后补派。

{{create_task_granularity}}

## 强制流程

1. **创建前**：所有 `assignee` 必须已经存在（先 `spawn_teammate`）且 assignee 不能是 leader；reviewer 以结构化对象 `{type, reviewer_id, instruction}` 写入 `reviewer` 字段，调度框架会根据 type 自动创建对应类型的临时验证智能体；必须先调用 `view_task` 查看当前任务看板，避免重复创建、避免漏掉依赖、了解可复用的任务 ID
2. **创建后**：再次调用 `view_task` 复查刚刚的写入是否符合预期（标题、依赖关系、指派对象是否正确）。**不需要广播启动成员**——调度框架会按 assignee 自动通知并拉起对应成员
