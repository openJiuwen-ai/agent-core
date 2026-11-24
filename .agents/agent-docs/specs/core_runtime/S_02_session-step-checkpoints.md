---
title: S_02 单 Agent 按 Step 断点续跑
author: n993577028
author_email: nijialong1@huawei.com
id: S_02_session-step-checkpoints
type: S
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
---

# S_02 单 Agent 按 Step 断点续跑

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/core/session/checkpointer/`、`openjiuwen/core/session/agent.py`、`openjiuwen/core/single_agent/agents/react_agent.py`、`openjiuwen/harness/rails/task_planning_rail.py` |
| 最近一次修订日期 | 2026-11-24 |
| 关联 feature | `F_20261124_1707_checkpoint-step-resume-develop.md` |

## 范围 / 边界

覆盖单 Agent Harness 在进程被杀或崩溃后，以同一 session 再拉起时按已落盘增量续跑的能力（core 侧）。
不覆盖 Team / 子 Agent / DeepResearch / SkillTurbo 的恢复策略，不新增对外 HTTP/前端/平台接口。
宿主的流式事件协议（例如 todo 列表的主动重发）不属于本契约。

## 键与记录

- 记录键：`{session_id}:agent-step:{agent_id}:{seq:020d}`，一轮完整 ReAct 迭代一条，只追加不改写。
- 记录内容为 `StepRecord(seq, iteration, kind, query, context, state, state_full)`，`kind ∈ {step, final, interrupt}`。
- 不写 `agent_state_blobs`；step 落盘不调用 `AgentStorage.save`。
- 会话没有 agent id（无 card 且无 agent config）时，step 存储整体为 no-op。

## 增量语义

- `append` / `reset` 复用 vcs `delta.py`；`replace_range` 只用于 step 路径，恢复时执行 `messages[start:end] = 新内容`。
- 选择规则：前缀增长且卸载表未变 → `append`；压缩/卸载导致中间段改写 → `replace_range`；对不齐或首条记录 → `reset`。
- 卸载全文留在工作区，增量只带截短消息与句柄；仅内存回退的 `offload_messages` 表随增量写入。
- 压缩召回标记 `[[COMPRESSION_RECALL: id=...]]` 原样保留，不剥除。
- 差分基线在进程内存中维护（上次成功写盘的编码快照）；写盘成功后才推进基线，失败则下一轮覆盖两轮。

## 生命周期

| 入口 | 行为 |
|---|---|
| `pre_agent_execute` | 先恢复 blob，再回放 step 增量，最后设置 `INTERACTIVE_INPUT` |
| `interrupt_agent_execute` | 追加 `interrupt` 记录，不写全量 blob |
| `post_agent_execute` | 有记录时按状态分流：`active`/`resume_pending` 不写；`interrupted` 追加 `interrupt`；`idle` 写全量 blob，成功后才清理 step 键。无记录时照旧写全量 blob |
| `release(session_id, agent_id)` | 精确前缀删除该 agent 的 step 键并丢弃进程内状态；仅给 session_id 时按精确前缀删除整个会话并丢弃该会话的进程内状态 |
| `Session.save_agent_step / begin_agent_steps / peek_agent_step_resume / end_agent_steps` | 转发到当前 checkpointer；基类默认空实现 |

- 状态由 run 边界驱动：`begin` 标记 `active` 并取走续跑点，`end` 按 `interrupted` 标记 `interrupted`/`idle`；`end` 在 `finally` 中断言状态，不掩盖原始异常。
- 校验失败（未进入循环）的 invoke 不关闭 step 记录，保留续跑点。

## 恢复语义

- 从空状态按 seq 依次 `apply`；遇到第一条反序列化失败的记录即截尾删除该条及其后全部键。
- 最后一条为 `step`：状态 `resume_pending`，续跑点 `StepResume(last.iteration, last.query)`，从 `iteration + 1` 起跑；为 `interrupt`：走原 HITL 恢复路径；为 `final`：空闲，不续跑。
- 再拉起时 query 与续跑点一致则不重复写入用户问题，不一致则作为新消息写入；`max_iterations` 预算不重置。
- `AgentStepStorage` 的进程内基线优先：同一进程的临时 session 恢复时直接回填基线，不读盘。

## 耐久性

- provider 自建 SQLite 引擎在每条连接执行 `PRAGMA synchronous=FULL`。
- MySQL / PostgreSQL 只检测 `innodb_flush_log_at_trx_commit` / `synchronous_commit` 并告警，不修改服务端全局参数。
- 写盘在进入下一轮之前同步完成；写盘失败不中断任务，基线不前进。

## 性能约束

- 每轮 append 读取 session 自身状态（`get_state(copied=False)`），不拷贝上下文；只对很小的 state 轨做 detached 拷贝。
- 读取与编码在第一个 `await` 之前同步完成，期间不得被其他任务修改；编码结果与 state 拷贝构成基线，不保留活对象引用。
- 每个 context 每轮只编码一遍，并与内存基线做 diff；不在热路径上调用会做全量深拷贝的 `build_step_snapshot()`。

## 不变量

| # | 不变量 |
|---|---|
| N1 | 只有完整的 step 才成为可恢复记录；前半轮不落盘，重跑整轮 |
| N2 | 已落盘的 step 键只追加、不改写 |
| N3 | 未落盘一轮整轮重跑；框架不判断工具是否已产生副作用 |
| N4 | 同一 session 才能续上；框架不提供按任务 id 找回 session 的接口 |
| N5 | 任务结束前不得清理 step 增量；结束时先写全量 blob，成功后清理 |
| N6 | 记录写入返回成功时数据已落盘（fsync 语义） |
