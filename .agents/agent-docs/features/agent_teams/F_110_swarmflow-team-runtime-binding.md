# Swarmflow stop_all 与 progress script_path（team runtime binding 的 agent-core 侧）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-07 |
| 范围 | `runtime/background_task_controller.py`、`workflow/tool_swarmflow.py`、`workflow/engine/{progress,runtime,runner}.py` |
| 测试基线 | 确定性单测 `test_background_task_controller.py::test_stop_none_*` + `test_engine.py::test_workflow_started_carries_script_path`；workflow 全套 29 passed |
| 关联 | `F_43` / `S_18`；嵌入层 jiuwenswarm 的 team runtime 生命周期联动 |

## 背景

嵌入层（jiuwenswarm）把 swarmflow run 的生命周期完全绑定到 team runtime：点方块 /
切换会话 / 断连兜底都要驱动 controller 的**全量** `pause_all` / `stop_all`。现状三处缺口：

1. `BackgroundTaskController.pause(resume)` 已支持 `run_id=None` 全量，但 `stop(run_id: str)` 仍是
   单值，嵌入层无法表达"停掉本会话全部 run"。
2. 冷启动续跑需要"情境注入"把非终态 run 的 `script_path` 告诉 leader（`resume_id + script_path`
   发射面恢复）。`script_path` 在 tool 层已解析并进 enriched inputs + `swarmflow.launched` 回执，
   但**没进 progress 事件**，嵌入层的 `workflow_runs` 快照拿不到它。
3. 冷启动续跑还缺 `args`：swarmflow 工具暴露 `args`（string），经 `invoke → run_background →
   run_swarmflow → run_workflow → run(args)` 传到脚本。冷启动 advisory 模板只有
   `resume_id + script_path`，无 args → resume 时 `run(args=None)` 与首跑 `run(args=X)` 走不同路径
   （缓存 miss 退化全量重跑或 `args['k']` TypeError）。args 未落盘。

## 决策

1. **`stop(run_id: str | None = None)` 全量语义，对齐 pause/resume**。`run_id=None` 遍历两个注册表：
   - `_active`：逐个 `_abort_one(h, "stop")`（engine 按 `reason=="stop"` 写 seal，缓存断根）后 pop。
   - `_paused`：逐个 pop，**不 `_abort_one`、不补 seal**——这些 run 的 pause 记录早已在 journal，
     丢票只让复活票失效，冷启动仍可凭 `resume_id` 命中缓存前缀续跑（"丢票不 seal"，对应非 swarmflow
     `stop_paused` 的避让清扫思想）。
   单值 `stop(run_id)` 语义不变。
4. **`_abort_one` 等待 task 真正 unwind（pause/stop 返回即记录已落、事件已发）**。
   `async_tool_runtime.cancel()` 只请求取消，engine 在 task 解栈的 finally 里才写 pause/seal 记录并
   发 `WORKFLOW_PAUSED/STOPPED`。嵌入层若在 pause() 返回后立即拆 leader harness
   （Runner.pause 停 EventBus/TeamMonitor），事件会发到已关闭的总线上、快照停在 running（实测：
   controller.pause 后 390ms 才发出 workflow_paused，此时 monitor 已停）。故第三步 cancel 后
   `asyncio.wait({task}, timeout=_UNWIND_TIMEOUT_S=30)` 等 task done。超时只告警不阻塞。
   附带 `[bg-ctl] register/deregister/pause/stop` INFO 诊断（含 controller 实例 id 与注册表大小）。
5. **终态 progress 投递下沉为 awaited（`SwarmflowTool._publish_terminal`）**。`_publish` 是同步
   sink（engine `progress_sink` 契约），用 `create_task` fire-and-forget——中途进度正确（不能阻塞
   engine）。但 `run_background` except 分支里的 5 处终态 publish（PAUSED/STOPPED）本就在 async
   上下文，改为 `await messager.publish`，使「task done ⟹ 事件已投递」成为契约，controller 无需
   知道 `_publish` 内部有几层 create_task；顺带消除 CancelledError 分支里无引用 publish task
   可能被 GC 的隐患。`_build_progress_message` 抽出共享构造。best-effort：总线已关只 debug 不抛。
6. **复活票 = 纯数据；执行宿主 = 当前 cycle 的 launcher（`set_launcher`）**。team 层 pause 经
   `kernel.finalize_round` 销毁 leader NativeHarness（**cycle 级**：每轮 stream 退出即销毁、
   `TeamHarness.start` 重建），`TeamToolRail` 随之重建 SwarmflowTool。controller 注册表是 **session
   级**——票据若捕获 launch 时的工具/harness，跨一次 pause 必然悬空：resumed 协程挂在死 harness，
   能跑、能发进度（messager 是 team 级），但完成回灌 `_inject_async_completion → send()` 命中
   TERMINATED 被吞，leader 永远不知完成（不汇报、无 team.idle、方块不熄；实测 `completion injection
   skipped` 紧随 `workflow_completed`）。修法：`SwarmflowRunHandle` 去掉 `relaunch` 闭包，只存
   `inputs + session_id`；`BackgroundTaskController.set_launcher(tool)` 由每个 cycle 的新
   SwarmflowTool 在 `__init__` 登记（`_native.background_task_controller` 在 rail build 前已 attach，
   构造时可达）；`resume()` 用当前 launcher 的 `relaunch(h.inputs, h.session_id)`（跨类调用的公开契约，非受保护成员），无 launcher 时
   保留票据返回 False。按钮/语义两路统一，无 per-call 参数。曾尝试 `resume(run_id, tool=self)` +
   `(tool or self)._relaunch` 闭包（已回退）——只修了语义通道，按钮路径无工具在手仍落死 harness。
7. **`action="stop"` 对已解栈的 run 由工具「宣告 stopped」**。paused run 在 pause 时已 unwind，
   引擎不会再为它发 `WORKFLOW_STOPPED`——无论 controller 仍持票（丢票返回 True）还是冷启动无票
   （返回 False）。若只靠引擎事件，leader 的「停止」在这两种场景下前端都不落终态（实测：持票
   stop 返回 success 但卡片停在 paused）。`_control_run` 在 stop 前记下 `controller.is_paused`，
   满足「曾 paused 或未命中」即调 `_announce_stopped(run_id)`：向 team topic 发一条
   `WORKFLOW_STOPPED` progress 事件让 Monitor 卡片落终态。active run 不宣告（引擎 unwind 时自己
   发，重复会双重终态）。**不写 journal seal**——pause 记录仍在，手动 `resume_id + script_path`
   仍可续（与决策 1 的「丢票不 seal」一致）。resume 无此退化：没有票据就没有可重放的 inputs，
   只能报 not_found 让 leader 走发射面。

8. **`script_path` 必须跨过 tool 层的桥**。决策 2 让引擎在 `WORKFLOW_STARTED` 上带 `script_path`，但
   `SwarmflowTool._build_progress_message` 逐字段构造 `WorkflowProgressTeamEvent`，schema 没有该字段、
   构造也没拷贝——引擎侧测试和嵌入层侧测试各自通过，中间这一跳漏了。实测：冷启动后快照里所有 run
   的 `script_path` 都是 None，advisory 给不出发射面调用，leader 只能新起 run。修法：
   `WorkflowProgressTeamEvent.script_path` + 桥上拷贝 `progress.script_path`；补一条走真实
   `run_background → observer → publish` 链路的测试。

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
- `test_cold_start_resume_recovers_args`：首跑 `args="hello"` 落 `__run__:args` 记录，第二次 resume
  不传 args 仍恢复 `"hello"`（非 None）。
- `test_stop_on_unregistered_run_announces_stopped_without_seal` / `test_stop_on_paused_run_announces_stopped`：
  controller.stop 未命中或命中 paused 票据时 team topic 收到 `workflow_stopped`（含 run_id）；
  `test_stop_on_active_run_does_not_double_announce`：active run 不宣告；resume 未命中仍 not_found。
- `test_workflow_started_team_event_carries_script_path`：经 `run_background` 真实发布链路，team event 携带 `script_path`。
- 回归：`test_background_task_controller.py` + `test_engine.py` 全绿（45 passed）；workflow 全套 288 passed。

## 已知遗留

- stop(None) 的 `_active` 全量 abort 与单值路径共用 `_abort_one`，无新增并发边界；controller 锁内
  遍历，与 pause/resume 同锁互斥。
- `script_path` 仅 WORKFLOW_STARTED 携带；resume 重放时 engine 重新发射 WORKFLOW_STARTED，`script_path`
  与首跑一致（同一 `path` 入参），嵌入层快照无需额外合并逻辑。
