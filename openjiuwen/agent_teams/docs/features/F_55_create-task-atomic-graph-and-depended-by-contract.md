# create_task 原子图创建 + depended_by 仅指已有任务的契约

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-07-08 |
| 范围 | `schema/task.py` · `tools/task_manager.py` · `tools/tool_task.py` · `tools/database/task_dao.py` · `tools/locales/` |
| 测试基线 | agent_teams 全量：1738 passed, 16 skipped |
| Refs | #751 |

## 背景

一次数据库诊断 e2e 里，leader 一次 `create_task` 传入 6 个任务：5 个分析任务各带
`depended_by: ["final-diagnosis"]`，`final-diagnosis` 排在最后带 `depends_on` 指向前 5 个。
输入没有任何循环依赖，但 6 条全部失败，且前 5 条报的是
"circular dependency, missing dependent task, or task_id collision"——把用户带偏去查环。

根因两层：

1. **批量路径不是原子的**：`TaskCreateTool` 逐 spec 调 `add` / `add_with_priority`，每条一个独立
   事务。边校验要求两个端点都已在 DB，于是批内前向引用（`depended_by` 指向批内靠后的
   `final-diagnosis`）必然失败；连带 `final-diagnosis` 自己的 `depends_on` 也全部落空。讽刺的是
   底层原语 `mutate_dependency_graph` 本来就支持一次事务插入多节点 + 多边（`_stage_new_tasks`
   先入库、边校验可见同批新节点）——工具层在原子原语外面套逐条循环，把原子性拆没了。
2. **错误 reason 被 bool 吞掉**：DAO 的 `add_task_with_bidirectional_dependencies` 把
   `GraphMutationResult.reason` 压成裸 `bool`，上层 `add_with_priority` 只能编一句三选一的猜测
   文案，违反本模块「失败必须透传 `result.reason`」的既有规约（S_08 / tools/AGENTS.md）。

顺带的设计冗余：批内 A `depends_on` B 与 B `depended_by` A 是**同一条边的两种写法**，允许混用
既浪费 token 又给 LLM 制造歧义。

## 决策

`create_task` 收紧为**两个使用场景、每条边只有一种表示**：

1. **批量创建任务图/子图**：批内边只用 `depends_on`（目标可为同批任务——顺序无关、允许前向
   引用——或已有任务）。
2. **楔入已有依赖链**：`depended_by` 仅可指向**已有**任务；指向批内任务在工具边界拒绝，错误
   信息直接教调用方改用对方的 `depends_on`。

实现上一次调用折成**一次** `mutate_dependency_graph` 原子事务：

- `schema/task.py` 新增 `TaskGraphSpec`（入参）/ `TaskGraphResult`（出参，携带真实 reason）。
- `TeamTaskManager.add_graph(specs)` 成为唯一创建原语：组装 `NewTaskSpec` + 边集、一次图变更、
  按 refresh 结果发布 `TaskCreatedEvent`；`add` 退化为单 spec 薄封装（external operator client
  等单任务调用方签名不变）。
- 删除死路径：`add_with_priority` / `add_batch` / `add_as_top_priority`（库内除工具层外零调用）
  与 DAO 的 bool 包装 `add_task_with_bidirectional_dependencies`。
- `_stage_new_tasks` 增加已有 id 预查，冲突时报 `Task id already exists: <ids>`，取代啰嗦的
  IntegrityError 文案。
- 工具边界校验：title/content 必填、批内 `task_id` 不重复、`depended_by` 不指向批内任务；
  存在性 / 环 / 终态拒绝留在 DAO 层（`_load_endpoints_and_validate` 等）。
- 批量输出简化：原子化后不存在部分成功，`skipped` / `failures` 字段删除，成功返回
  `tasks` + `count`，失败整体返回底层 reason。

## 拒绝的方案

- **按拓扑序分两次创建（先无依赖任务、后有依赖任务）**：LLM 自救时的 workaround，不该固化为
  实现——仍是多事务、仍不原子，且 `depended_by` 与 `depends_on` 混用时无法总排出可行顺序。
- **批内 `depended_by` 静默去重容忍**：物理上可行（边集合天然去重），但契约上留下两种等价写法，
  LLM 永远学不会规范形；显式拒绝 + 教学式错误信息才能收敛行为。
- **保留 `add_with_priority` 并修 reason 透传**：修完后它与 `add_graph` 完全同构（单 spec 特例），
  留着只是第二条创建路径的腐化源。

## 验证基线

- 新增单测：`add_graph` 前向引用 / `depended_by` 楔入 / 原子回滚 / 批内重复 id / 已有 id 冲突 /
  自动生成 id / 空批；工具层批量前向引用、原子失败、批内 `depended_by` 拒绝、批内重复 id 拒绝。
- 原有 `add_task_with_bidirectional_dependencies` 的 DAO 级语义用例（终态拒绝、环检测、refresh）
  全部平移到直接调用 `mutate_dependency_graph`；嵌套写锁用例改用
  `verify_and_fix_task_consistency` 委托对。
- 复现原始 e2e 输入（6 任务、`final-diagnosis` 前置）：一次调用原子成功，`final-diagnosis`
  正确 BLOCKED。

## 已知遗留

- `depends_on` 指向批内自动生成 id 的任务无从表达（LLM 引用不到未知 id）——按需引用就显式给
  `task_id`，不是缺陷。
- 外部 operator 面（`ExternalTeamClient.create_task`）仍是单任务 `add`；如外部编排方需要批量原子
  创建，再把 `add_graph` 暴露到 operator scope。
