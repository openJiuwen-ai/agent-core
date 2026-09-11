# Swarmflow relaunch_kind 信号与 seal/pause 终态语义

用 `relaunch_kind` 区分"脚本编辑重跑"与"暂停续跑"两种 relaunch 路径，并用 seal/pause 两类
run 级记录把 run 的可恢复性从代码行为下沉到 journal 可查的契约。

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-28 |
| 范围 | `schema/events.py`（`WorkflowProgressTeamEvent.relaunch_kind`、`swarmflow_human_reply_topic`）、`workflow/tool_swarmflow.py`（`_RELAUNCH_KIND_KEY`、`_seal_guard`、`_format_early_return`）、`workflow/engine/runner.py`（`_write_seal_record`/`_write_pause_record`、CancelledError seal 兜底）、`workflow/engine/errors.py`（`WorkflowAborted.reason`） |
| 测试基线 | `tests/unit_tests/agent_teams/workflow/test_swarmflow_abort_branch.py`、`test_budget.py` 全量通过 |
| Refs | #— |

## 背景

F_43 的 pause/resume 把 run 的可恢复性建立在"task 是否还在 controller 的 `_paused` 池"——但
这是**进程内**状态，重启后丢失，且无法从 journal 反查。前端要决定"阶段树是整体替换还是
增量合并"，只能靠猜 run 当前态。

`task_id` 曾被用来当 relaunch 信号，但 `task_id` 是 AsyncTool 的统一 ID，resume 会换新的，
拿它判 relaunch 本就脆弱。需要一个**语义明确**的信号字段，直接说"这次 launch 相对上次是
relaunch（整体重建）还是 resume（增量合并）"。

终态语义也有漂移：early_return（用户要改脚本重跑）本应可恢复，但早期实现把它发成
`WORKFLOW_STOPPED`（终端）；session 级 budget 撞顶本应终端，却发成 `WORKFLOW_FAILED`（可重试）。
事件语义与可恢复性不对齐，前端和 leader 都会误判。

## 数据结构 / 状态机

### `relaunch_kind`

`WorkflowProgressTeamEvent.relaunch_kind: "relaunch" | "resume" | None`：

- `"relaunch"`：脚本编辑重跑，同 run_id 下**整体替换** phase/agent 树。`SwarmflowTool.invoke`
  在存在 `resume_id` 时设置（`enriched[_RELAUNCH_KIND_KEY] = "relaunch"`）。
- `"resume"`：pause→resume 续跑，同 run_id 下**增量合并**。`_relaunch` 设置
  （`inputs[_RELAUNCH_KIND_KEY] = "resume"`）。
- `None`：全新 launch（无 resume_id）。

`_publish` 把 `relaunch_kind` 从 inputs 透传进 `WorkflowProgressTeamEvent`。

### seal / pause 记录

`_write_seal_record(rt, terminal_status)`：写 `__run__:seal:{run_id}`，标记 run 终态
（`terminal_status` = `completed` / `stopped`）。sealed run 不可 resume，下次 invoke 经
seal guard 强制新 run_id。

`_write_pause_record(rt, pause_reason)`：写 `__run__:pause:{run_id}`，标记 run 可恢复中断
（`pause_reason` = `paused` / `early_return` / `workflow_budget_exhausted`）。pause 记录的 run
可同 run_id resume。

### 事件语义对齐

| 场景 | 事件 | status | seal/pause | 可恢复 |
|---|---|---|---|---|
| 正常完成 | `WORKFLOW_COMPLETED` | completed | seal(completed) | 否（终态） |
| workflow 级 budget 撞顶 | `WORKFLOW_FAILED` | failed | pause(workflow_budget_exhausted) | 是（改脚本重试） |
| session 级 budget 撞顶 | `WORKFLOW_STOPPED` | stopped | seal(stopped) | 否（终端） |
| early_return（改脚本重跑） | `WORKFLOW_PAUSED` | paused | pause(early_return) | 是 |
| pause/resume | `WORKFLOW_PAUSED` | paused | pause(paused) | 是 |
| stop | `WORKFLOW_STOPPED` | stopped | seal(stopped) | 否 |

### CancelledError seal 兜底

stop 落在 LLM 调用中途时，`WorkflowAborted` 的 checkpoint（入口 gate / pre-journal guard）
来不及触发，run 就被 cancel 了。此时 `_exec_loaded` 的 `except asyncio.CancelledError` 检查
`rt.abort_event.reason == "stop"`，若然则 `task.uncancel()` 后补写 seal 记录，再 raise——
保证 stop 永远落 seal，resume 不回放已停的 run。

### seal guard

`SwarmflowTool._seal_guard(script_path, resume_id)`：invoke 时若 `resume_id` 指向一个已 seal
的 run（`journal.find_run_record(resume_id, "seal")` 命中），清空 `resume_id`（返回 `""`），
使后续 `_enriched_inputs` 走 `new_swarmflow_run_id()` 生成全新 run_id。best-effort：journal 读
取失败只 debug log，不阻塞。

### `swarmflow_human_reply_topic`

human session 的真人回复走专用 topic（`session:{sid}:team:{tn}:run:{rid}:swarmflow_human_reply`），
不与 leader 的 team-event 订阅抢同一 messager——避免回复路由竞态。run_id 非空时 topic 是
run-scoped，并发 run 不串回复。

## 决策

1. **`relaunch_kind` 替代 `task_id` 当 relaunch 信号**。`task_id` 是 AsyncTool ID，resume 会换
   新的，语义脆弱；`relaunch_kind` 是语义字段，直接声明这次 launch 的性质，前端无需猜。
2. **early_return 发 PAUSED，不发 STOPPED**。early_return 是用户要改脚本重跑，本质是可恢复
   中断；`_format_early_return` 生成注入 leader 下一轮的指令文本（含 resume_id 引导），leader
   编辑脚本后以 `resume_id={run_id}` 重跑，命中 `relaunch_kind="relaunch"`。
3. **session 撞顶发 STOPPED，不发 FAILED**。session 账本是 leader 级上限，耗尽即终端，不可靠
   改脚本重试；FAILED 的语义是"可重试"，会误导。workflow 撞顶才是 FAILED（可重试）。
4. **seal/pause 记录下沉到 journal**。可恢复性不再只靠进程内 controller 池，journal 可查——
   重启后 seal guard 仍能拦截 sealed run_id 的误 resume。
5. **CancelledError 补 seal 兜底**。stop 在 LLM 中途时 checkpoint 没机会跑，不补 seal 的话
   该 run_id 在 journal 里没有终态记录，resume 会错误回放。`task.uncancel()` 保证补 seal 的
   await 跑完再 raise。

## 拒绝的方案

- **用 `task_id` 判 relaunch**：拒绝。resume 换 task_id，语义脆弱，前端要猜"换没换"。
- **early_return 发 STOPPED**：拒绝。用户改脚本重跑是可恢复路径，发终端态会让 leader 不再
   尝试。
- **seal 记录只存内存**：拒绝。重启丢失，seal guard 失效，sealed run_id 会被误 resume。
- **CancelledError 不补 seal**：拒绝。中途 stop 的 run 无终态记录，resume 回放已停结果。
- **seal guard 强一致（journal 读失败就阻塞）**：拒绝。best-effort，journal 异常不应阻塞
   launch；读失败时返回原 resume_id，最坏情况是误命中一次旧缓存，不致命。

## 验证

- `test_swarmflow_abort_branch.py`：`WorkflowAborted` 各 reason 的事件语义（pause→PAUSED、
  stop→STOPPED、early_return→PAUSED）与 seal/pause 记录写入。
- `test_budget.py`：workflow 撞顶（FAILED+pause）与 session 撞顶（STOPPED+seal）的事件与
  scope 区分。

## 已知遗留

- 真实 LLM e2e：CancelledError seal 兜底（stop 在 LLM 中途）需端到端验证（手动，不进 CI）。
- `swarmflow_human_reply_topic` 的回复路由在 jiuwenswarm 平台侧偶现竞态（AvatarSessionManager
  订阅匹配），agent-core 侧 topic 已就位，平台侧仍在排查。
