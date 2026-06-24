# Swarmflow 真实 pause/resume 中断恢复

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-23 |
| 范围 | `runtime/background_task_controller.py`(新)、`workflow/engine/{errors,runtime,primitives,runner}.py`、`workflow/runner.py`、`workflow/backends/{team_worker_backend,avatar_session_backend}.py`、`workflow/tool_swarmflow.py`、`harness/{native_harness,team_harness}.py`、`agent/{member_runtime,team_agent}.py`、`external/runtime.py`、`core/runner/team_runner.py` |
| 测试基线 | 确定性单测 `test_pause_resume.py`(engine 3)+ `test_avatar_session_backend.py::test_abort_all_*`(session 终止)+ `test_background_task_controller.py`(controller 三步)→ workflow 全套 90 passed;真实 LLM e2e `agent_team_swarmflow_pause_resume_runner_e2e.py` 每特性点 pause/resume `pauses=6 resumes=6 phases=6 human_replies=7` PASSED |
| Refs | #1047 |

## 背景

swarmflow 已有完备的 journal 重放基础(F_38/S_18):每个 `agent()` 完成即 `_append_wal`+flush
落盘,`run_swarmflow` 内部 `run_workflow(resume=journal_path, journal_path=journal_path)`,同
`(team,session,name)` 第二次跑自动命中缓存前缀。但**缺一个真实的外部中断恢复接口**——leader
调起的 swarmflow 后台任务跑起来后,外部(operator/TUI)无法 pause(中途停下、保留已完成 agent 的
成果)和 resume(从断点续跑)。

参照 Claude Code 的 workflow 中断恢复模型(`docs/designs/TUI.md`:**pause = abort + 进程级停止,
resume 靠 journal 重放,被中断的 agent 不入 journal、resume 重跑**),从
`Runner.run_agent_team_streaming` 这一层补上真实的 pause/resume。

## 数据结构 / 状态机

执行栈:
```
leader.async_tool_runtime._tasks[task_id]  (asyncio.Task)
  └─ SwarmflowTool.run_background → run_swarmflow → run_workflow(engine Runtime)
      └─ 脚本 run(args)
          ├─ agent()         → TeamWorkerBackend.run() → worker run_once   [不可 abort]
          └─ agent_session() / human_session() → AvatarSessionManager → session harness.send [supervisor,可 abort]
```

两种 worker 形态决定停止机制:
- 单轮 `agent()` 走 `run_once`(无 supervisor,`abort()` 的 `_require_alive` 会 raise)→ **只能
  task.cancel()**;run_once 的 finally 做 post_run+teardown,cancel 后清理干净。
- `agent_session`/`human_session` 走 `harness.start()`(supervisor)+ 多轮 `send` → **可 abort**。
  session supervisor 是 `start()` 起的**独立 asyncio.Task**,顶层 swarmflow task 的 cancel 不会
  传播到它,必须单独 abort。

三个互补的停止机制(`BackgroundTaskController.pause()` 三步,**顺序是正确性关键**):

| 机制 | 停什么 |
|---|---|
| engine `abort_event`(asyncio.Event) | 阻止起新 agent/turn + 在途的不落 WAL(两 checkpoint) |
| session `abort_all` | 正在跑的 session harness(agent+human,supervisor)+ cancel 等真人回复的 future |
| 顶层 task cancel | 在途 run_once worker + 解栈整个 engine;WAL 保留(finalize 被 cancel 跳过) |

## 决策

1. **触发通道 = 统一的 BackgroundTaskController 对象,经 streaming 传入**。不在 Runner 上堆
   `pause_swarmflow`/`resume_swarmflow` 方法,而是一个对象 attach 到 leader harness,
   SwarmflowTool 自注册 run handle。pause/resume 是控制面动作,未来可扩展更多控制/回调接口。
   接线:`run_agent_team_streaming(background_task_controller=ctl)` → `activation.agent
   .set_background_task_controller` → TeamHarness(存 `_bg_controller`,`start` 跨 native rebuild
   回灌)→ `native.background_task_controller`;SwarmflowTool 经 `parent_agent` 读取。
2. **pause 三步固定顺序:set abort_event → abort sessions → cancel top task**。先 set event 让在途
   agent 走 pre-journal guard 时不写 WAL;再在 controller 协程(非顶层 task)里**完整** abort
   sessions(否则顶层 cancel 解栈时 `backend.aclose()` 被二次 cancel,session supervisor 泄漏);
   最后 cancel 顶层 task 停 run_once worker + 解栈 engine。
3. **engine 加通用 `abort_event`(asyncio.Event)+ 两个 checkpoint**:入口 gate(cache-hit 后、起
   backend 前)挡新 agent;pre-journal guard(backend 成功后、`journal.use` 前)确保在途 agent 不
   journal。`asyncio.Event` 是 stdlib,不违反 engine 业务无关铁律。
4. **`WorkflowAborted` 是 `BaseException`**(不是 `WorkflowError`/`Exception`),像 `CancelledError`
   一样穿透 `parallel()`/`pipeline()` 分支的 `except Exception`,不被吞成 `None` 再 journal 成 null。
5. **session `abort_all` 覆盖 agent + human 两类**。human session 的 avatar 也是 supervisor
   harness(`_start_avatar` 对两类都 `start()`),用 `abort(immediate=True)` 停;human session 还
   可能阻塞在 `_await_human_reply` 等真人,故先 cancel `_pending_human` future。被中断的 turn 不
   journal,resume 重跑;human turn 的 `correlation_id` 跨 resume 稳定,真人回复仍能匹配。
6. **resume 绕过 `invoke`**,经 controller → `SwarmflowTool._relaunch(inputs)` → `generate_id` 新
   task + `launch_async_tool(同一 inputs)`。journal 路径不变 → 命中前缀、断点后 live。resume 是
   控制面动作,不是 LLM 决定的新 tool_use。`resume_id` 工具参数保持 "not supported yet"。
7. **SwarmflowTool 接住 `WorkflowAborted` 转 `CancelledError`**:pause 时 abort_event 可能让 engine
   raise WorkflowAborted(BaseException),run_background `except WorkflowAborted: raise
   asyncio.CancelledError()`,让 async-tool runtime 当作静默取消(不注入完成),与第三步 cancel 一致。
8. **resume relaunch 必须恢复 `session_id` contextvar**。resume 由外部协程(controller)驱动,不在
   leader round 上下文里;`launch_async_tool` 的新 task 在 `create_task` 时继承当前 context,故
   `_relaunch` 在 launch 前 `set_session_id(原 session)`、`finally` 复位。否则 resume 解析到空
   session → 用错 journal 路径(等于不命中缓存、全部重跑)+ 进度事件发到错 topic(monitor 收不到、
   外部 drain 卡死、human 永等到超时)。`run_background` 捕获 session_id 一次,贯穿 `_publish` topic
   / `run_swarmflow` / relaunch 闭包。**此 bug 由真实 LLM 每特性点 e2e 抓出**——确定性单测 stub 了
   relaunch、覆盖不到 contextvar 跨 task 继承。

## 拒绝的方案

- **Runner 上堆 `pause_swarmflow`/`resume_swarmflow` facade 方法**:拒绝。随控制操作增多会接口
  爆炸;用户明确要一个统一 controller 对象。
- **streaming 回调/control handle 参数**:拒绝。改公共 API 签名风险大,且 AsyncIterator 难在产出
  chunk 的同时把 handle 交还外部;回调协议(双向)比现有只入的 stream_logger 复杂。
- **interact 控制消息(新 payload 类型 + 前缀)**:拒绝。把控制命令混进"给 LLM 的数据消息"通道,
  语义不纯,且要改 payload/解析/路由 4 处。
- **纯 cancel 顶层 task(不加 engine abort_event)**:拒绝。session turn 的 future 可能在 cancel
  落地前被解析为空结果,engine 走到 `journal.use` 写入空 turn(竞态);且 pause 落在两个串行 agent
  之间时没有干净 checkpoint。pre-journal guard 消除竞态。
- **把 worker 从 run_once 改成 supervisor 让它可 abort**:拒绝。改动大、违反 workflow 铁律3
  (worker 单轮 run_once 用完即弃),且 worker 生命周期/backend 对接全要改。run_once worker 靠顶层
  cancel 停止即可(finally 清理干净)。
- **`WorkflowAborted` 继承 `Exception`**:拒绝。会被 parallel/pipeline 分支的 `except Exception`
  吞成 `None`,在途 agent 反而被 journal 成 null 结果,resume 不会重跑。必须 BaseException。

## 验证

- engine 单测 `tests/unit_tests/agent_teams/workflow/test_pause_resume.py`(3):`abort_event` 预设
  挡所有 agent;A 完成后 set、B 在 pre-journal guard raise、WAL 仅 A;resume 命中前缀只重跑 B/C。
- 确定性单测(CI):`test_avatar_session_backend.py::test_abort_all_...`(session `abort_all` 终止
  agent+human harness、cancel 等真人回复的 future)+ `test_background_task_controller.py`(`pause()`
  三步顺序 event→abort_sessions→cancel、`resume()` relaunch、空集 no-op);workflow 全套 90 passed。
- 真实 LLM e2e(手动)`tests/system_tests/agent_swarm/agent_team_swarmflow_pause_resume_runner_e2e.py`
  (仿 `agent_team_swarmflow_e2e.py`,真 leader LLM + `run_agent_team_streaming(background_task_controller=)`):
  party_planner 全原语,**每个 phase 边界都 pause+resume 一次**(构思/征询嘉宾/拟菜单/筹备/审批/
  邀请函,human session 阶段 pause 即 `abort_all` 中断在途真人会话),human 立即应答不留等待。结果
  `pauses=6 resumes=6 phases=6 human_replies=7` PASSED——并抓出修复了 resume 的 session-context bug(决策 #8)。
- 未跑 ruff/mypy(项目约定)。

## 已知遗留

- **真实 LLM e2e:已补**(`agent_team_swarmflow_pause_resume_runner_e2e.py`,每特性点 pause/resume,
  PASSED)——并抓出修复了 resume 的 session-context bug(决策 #8)。需 model endpoint,手动跑、不进 CI。
  原确定性 `agent_team_swarmflow_pause_resume_e2e.py`(stub 版)已删除,被它 + 上述确定性单测取代。
- **多并发 swarmflow run 的 pause**:controller 遍历所有 active handle(已支持),但 e2e 只覆盖单 run。
- **pause 期间真人回复**:human session 被 abort 后,pause 窗口内真人用同 correlation_id 提交的回复
  在 resume 后能否稳定匹配,逻辑已设计(correlation_id 稳定),未做专门 e2e。
- **WorkflowAborted vs CancelledError 双路径**:单测覆盖 WorkflowAborted(pre-journal guard),e2e
  覆盖 CancelledError(顶层 cancel);两路殊途同归(都不 journal 在途、WAL 保留前缀),未合并为单一路径。
