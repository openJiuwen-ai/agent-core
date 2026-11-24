---
id: F_20261124_1707_checkpoint-step-resume-develop
title: 单 Agent 按 Step 断点续跑移植到 develop
type: F
author: n993577028
author_email: nijialong1@huawei.com
date: 2026-11-24
designs: []
design_note: core 侧正式落地的方案先在 dev-stable 实现并评审（agent-core #3885 / #3889 / #3923，需求 jiuwenswarm#6553），develop 只做移植与适配，无独立 D 文档；设计背景见 jiuwenswarm issue 6553 与 dev-stable PR 描述。
modules:
- openjiuwen/core/session/checkpointer
- openjiuwen/core/session
- openjiuwen/core/single_agent/agents
- openjiuwen/harness/rails
files:
- openjiuwen/core/session/checkpointer/step_delta.py
- openjiuwen/core/session/checkpointer/step_storage.py
- openjiuwen/core/session/checkpointer/base.py
- openjiuwen/core/session/checkpointer/persistence.py
- openjiuwen/core/session/agent.py
- openjiuwen/core/single_agent/agents/react_agent.py
- openjiuwen/harness/rails/task_planning_rail.py
- tests/unit_tests/core/session/checkpointer/test_step_delta.py
- tests/unit_tests/core/session/checkpointer/test_step_storage.py
- tests/unit_tests/core/session/checkpointer/test_persistence_step_checkpoint.py
- tests/unit_tests/core/session/checkpointer/test_step_resume_e2e.py
- tests/unit_tests/core/single_agent/agents/test_react_agent_step_resume.py
- tests/unit_tests/harness/rails/test_task_planning_step_todo.py
---

# 单 Agent 按 Step 断点续跑移植到 develop 实施记录

## 关联依据

- 设计：dev-stable 实现与其评审记录——agent-core `#3885`（增量落盘与恢复）、`#3889`（无 card session 修复）、`#3923`（每轮落盘 CPU 优化），需求单 jiuwenswarm#6553。
- spec：`S_02_session-step-checkpoints`（新建）。
- 日期：2026-11-24。
- 实际实施范围：core 侧完整能力（增量落盘、崩溃续跑、todo.json 快照与滚回、落盘耐久性、性能优化）。宿主侧事件（续跑首帧主动重发 todo.updated）不在本仓范围，develop 的宿主当前也无对应代码。

## 决策归属与关系

dev-stable 与 develop 长期分叉，本记录是同一特性在 develop 的落地归档，不替代 dev-stable 的 PR 记录；两者代码以各自分支为准。本仓此前没有 session/checkpointer 的 spec，本次新建 `S_02` 承载契约。

## 上下文与跨边界影响

- 产生方：`ReActAgent` 在每轮完整迭代后调用 `Session.save_agent_step`；`TaskPlanningRail` 在 `after_react_iteration` 写 todo.json 快照，在 `before_invoke` 检测待续跑点时滚回文件。
- 载体：`PersistenceCheckpointer` 的 KV 键；进程内 `AgentStepStorage._tracks` 保存差分基线。
- 恢复：`Session.pre_run → pre_agent_execute → blob → step 回放 → 交互输入`。
- 失败与终态：写盘失败不中断任务且基线不前进；`finally` 关闭 step 记录；任务结束时写全量 blob 成功后清理增量。

## 实际实现与开发决策

1. **框架部分按 dev-stable 的最终实现移植**：`step_delta.py`、`step_storage.py` 原样引入；`base.py` / `persistence.py` / `agent.py` 以 cherry-pick 自动合入为准。
2. **`ReActAgent` 按 develop 的钩子点手工落地**：develop 的 `_inner_invoke` 与 dev-stable 分叉较大，未整文件套用，而是在 `interruption_state` 之后取续跑点、在 `elif not resume_continuation` 之前插入续跑分支、在 `AFTER_REACT_ITERATION` 之后写增量、两条无 tool_calls 结束路径各写 `final`、`finally` 先关 step 再提交；abort 路径通过 `_handle_context_abort` 新增 `close_steps` 参数，在 commit 前关闭记录。
3. **todo 快照适配 develop 的文件设施**：develop 没有 `todo_store`、`ToolSnapshot`/`missing_ok` 选项，改为"普通读 + 父目录列表探测"区分空文件与缺失文件；滚回用 `write_file(prepend_newline=False)`。
4. **不引入代际重打**：develop 的 `TodoItem` 没有 `generation_token` 字段，也没有宿主侧代际过滤，因此不移植 dev-stable #10746 的续跑保代际逻辑；待 develop 侧未来引入代际体系时再评估。
5. **按 issue 建议补开关 `ContextEngineConfig.enable_iteration_checkpoint`（默认关闭）**：关闭时 ReActAgent 不取续跑点、不写记录，TaskPlanningRail 也不做 todo 快照，基线行为与引入前一致；打开后每轮一次增量写。默认关闭意味着宿主需显式启用（dev-stable 侧无开关、默认生效，两分支存在差异）。
5. **任务结束清理顺序**：先写全量 blob、成功后才删 step 键；序列化失败时保留增量，避免数据丢失。

## 与设计的偏差

- dev-stable 设计中的"续跑轮不递增 todo 代际并补发 todo.updated"属于宿主行为；develop 无对应实现，故本仓不落地，影响是续跑首帧的 todo 展示由宿主的既有刷新路径决定。
- todo 快照的读取由"snapshot 选项"改为"普通读 + 目录探测"，语义等价，后端不可判定时按未知处理并跳过快照。

## 实际执行与证据

| 执行日期 / 验证范围 | 实现版本与环境 | 命令 / 复现步骤 / 记录链接 | 实际结果 | 未覆盖项 / 失败原因 |
| --- | --- | --- | --- | --- |
| 2026-11-24 定向新测试 | develop 分支工作区，Windows 11 / Python 3.11 | `uv run pytest tests/unit_tests/core/session/checkpointer tests/unit_tests/core/single_agent/agents/test_react_agent_step_resume.py tests/unit_tests/harness/rails/test_task_planning_step_todo.py` | 91 passed | 无 |
| 2026-11-24 受影响目录 | 同上 | `uv run pytest tests/unit_tests/core/session tests/unit_tests/core/single_agent tests/unit_tests/harness/rails` | 2014 passed，2 failed | 2 条失败均为环境问题且未被本分支改动：`test_colliding_and_path_like_ids_survive_flush_load`（Windows 非 ASCII 路径读取）、`test_snapshot_cyclic_symlink_is_empty`（Windows 符号链接权限） |

### 可见行为、副作用与证据有效范围

- 模型可见：续跑后上下文、工具结果与迭代序号连续；`max_iterations` 预算从上次完成轮继续计算。
- 文件副作用：续跑前 `todo.json` 被滚回到上一道完成 step 的快照；文件快照记录的迭代仍会随 step 增量恢复。
- 证据对应本分支当前工作区与上述版本；dev-stable 的实现差异（todo_store、generation token）不适用于本分支结论。

## 验证结论

- 已证明范围：单 Agent 崩溃续跑、增量格式、损坏截断、无 card 会话 no-op、todo 快照与滚回、abort 顺序。
- 未覆盖或未通过的承诺：真实进程被杀后的跨进程续跑只在 SQLite 单测中模拟（`test_step_resume_e2e`），未做真实平台重启联调；宿主侧续跑首帧的 todo 刷新不在本仓范围。
- 可支持的交付判断：满足 core 侧验收范围，可进入检视；宿主联动需另行确认。
