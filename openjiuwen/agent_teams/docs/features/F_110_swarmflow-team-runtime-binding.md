# Swarmflow stop_all 与 progress script_path（team runtime binding 的 agent-core 侧）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-07 |
| 范围 | `runtime/background_task_controller.py`、`workflow/engine/{progress,runtime,runner}.py` |
| 测试基线 | 确定性单测 `test_background_task_controller.py::test_stop_none_*` + `test_engine.py::test_workflow_started_carries_script_path`；workflow 全套 29 passed |
| 关联 | SDD-0018（swarm-design-docs 仓）、`F_43` / `S_18` |

## 背景

SDD-0018 在嵌入层（jiuwenswarm）把 swarmflow run 的生命周期完全绑定到 team runtime：点方块 /
切换会话 / 断连兜底都要驱动 controller 的**全量** `pause_all` / `stop_all`。现状两处缺口：

1. `BackgroundTaskController.pause(resume)` 已支持 `run_id=None` 全量，但 `stop(run_id: str)` 仍是
   单值，嵌入层无法表达"停掉本会话全部 run"。
2. 冷启动续跑需要"情境注入"把非终态 run 的 `script_path` 告诉 leader（`resume_id + script_path`
   发射面恢复）。`script_path` 在 tool 层已解析并进 enriched inputs + `swarmflow.launched` 回执，
   但**没进 progress 事件**，嵌入层的 `workflow_runs` 快照拿不到它。

## 决策

1. **`stop(run_id: str | None = None)` 全量语义，对齐 pause/resume**。`run_id=None` 遍历两个注册表：
   - `_active`：逐个 `_abort_one(h, "stop")`（engine 按 `reason=="stop"` 写 seal，缓存断根）后 pop。
   - `_paused`：逐个 pop，**不 `_abort_one`、不补 seal**——这些 run 的 pause 记录早已在 journal，
     丢票只让复活票失效，冷启动仍可凭 `resume_id` 命中缓存前缀续跑（"丢票不 seal"，对应非 swarmflow
     `stop_paused` 的避让清扫思想）。
   单值 `stop(run_id)` 语义不变。
2. **`WorkflowProgressEvent.script_path: str | None = None`**。`run_workflow(path)` 把 `path` 写入
   `Runtime.script_path`，`_exec_loaded` 的 `WORKFLOW_STARTED` 事件携带 `script_path=rt.script_path`。
   业务无关铁律不破：`script_path` 是 engine 已有的 `path` 入参，不引入 agent_teams 依赖；其它
   kind 一律 None（与 name/description 等 WORKFLOW_STARTED 专属字段一致）。

## 拒绝的方案

- **stop 全量复用 `pause(None)` 的"移入 `_paused`"语义**：拒绝。stop 是终止意图，active run 必须
  写 seal 断根（不可 resume），不能移入 `_paused` 造成"stop 后还能 resume"的假象。
- **stop(None) 对 `_paused` 补 seal**：拒绝。paused run 的 pause 记录已表达"可续"，补 seal 会把
  冷启动可续账本在展示层"说死"，且与非 swarmflow 断连/切换保账本的语义不对称。
- **script_path 走 progress 之外的旁路（如单独事件/嵌入层自行存）**：拒绝。progress 事件是
  嵌入层快照的单一来源，旁路会制造第二真相源，必然漂移。

## 验证

- `test_stop_none_stops_all_active_and_paused`：active 被 abort+drop、paused 丢票，两注册表清空。
- `test_stop_none_drops_paused_without_reaborting`：paused run 的 abort_event.reason 保持 `pause`，
  证明 stop(None) 未重新 abort（丢票不 seal）。
- `test_workflow_started_carries_script_path`：`WORKFLOW_STARTED` 事件携带绝对 `script_path`。
- 回归：`test_background_task_controller.py` + `test_engine.py` 全绿（29 passed）。

## 已知遗留

- stop(None) 的 `_active` 全量 abort 与单值路径共用 `_abort_one`，无新增并发边界；controller 锁内
  遍历，与 pause/resume 同锁互斥。
- `script_path` 仅 WORKFLOW_STARTED 携带；resume 重放时 engine 重新发射 WORKFLOW_STARTED，`script_path`
  与首跑一致（同一 `path` 入参），嵌入层快照无需额外合并逻辑。
