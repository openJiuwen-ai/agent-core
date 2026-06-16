# Swarmflow 端到端验证与四项健壮性修复

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-16 |
| 范围 | `workflow/engine/{facade,provider,seam,primitives,runtime}.py`（`human()` 补 label/phase + `workflow()` 嵌套深度改 contextvar）、`workflow/backends/{structured_output_tool,team_worker_backend,avatar_session_backend}.py`（force-finish rail）、`harness/team_harness.py`（`add_rail` 委派）、`harness/native_harness.py`（`_ack` 守卫）；新增 `tests/system_tests/agent_swarm/agent_team_swarmflow_e2e.py` / `agent_team_swarmflow_concurrent_e2e.py` / `config_swarmflow.yaml` / `resources/{party_planner,invitation_card,concurrent_invites}.py` + 5 个单测文件改动 |
| 测试基线 | `tests/unit_tests/agent_teams/workflow/` + `harness/` + `test_team_harness_integration.py` 123 passed；真实 qwen flash 两个 E2E：party_planner `E2E PASSED`(phases=6 / human 7×往返 / structured_output 222→17)、concurrent `CONCURRENCY E2E PASSED`(3/3 并发子工作流 / nested-depth 跳过 0 / supervisor crash 0) |
| Refs | #751 |

## 背景

为从 **team 入口**(`Runner.run_agent_team_streaming` → leader 调 `swarmflow` 工具)端到端验证
swarmflow 全部能力,新建了一个真实 LLM 的系统测试,跑覆盖全部原语的 `party_planner.py`
(无状态 `agent` / 有状态 `agent_session` / 无状态 `human` / 有状态 `human_session` /
`pipeline` / `parallel` / 嵌套 `workflow`)。这次端到端拉通暴露了四个此前单测/MockBackend
覆盖不到的潜在问题——都是"工具/会话被真实小模型驱动 + 真实关停时序"才会触发的:

1. 官方 demo `party_planner.py` 用 `human(..., label="主办人签核")`,但一次性 `human()`
   不接受 `label`,「审批」阶段直接 `human() got an unexpected keyword argument 'label'`
   导致整个工作流 `workflow_failed`。
2. `structured_output` 被反复调用——全程 222 次 / 约 14 个 schema turn(≈每轮 5–7 次),
   小模型调用后收不到"停止"信号一直重发。
3. `workflow()` 在 `parallel`/`pipeline` 里并发调用时,并发的兄弟调用被静默跳过(返回
   `None`):嵌套深度守卫用的是**共享** `Runtime.wf_depth` 计数器。
4. 关停时 supervisor 崩 `InvalidStateError`:`stop()` 取消在途的异步工具完成注入任务,其
   `await send-ack` 被取消,supervisor 随后 dispatch 已入队的 `_CmdSend` 时对已取消 future
   `set_result`。

## 决策

1. **`human()` 补 `label` / `phase`,与 `agent()` / `*_session()` 对称**
   (`facade/provider/seam/primitives` 四层一致补)。一次性 `human` 是"开一个临时 human 会话、
   问一次、关",而 session 早就接受 `label`/`phase`——缺失是移植遗漏。新参数转发给临时
   `AgentSession(_human=True, label=, phase=)`,使一次性 human turn 也带确定性
   correlation id `{phase}:{label}:{turn}`、进度事件可读。**恢复 API 一致性,非新增表面。**

2. **`StructuredOutputFinishRail`:捕获即终止本轮**(`structured_output_tool.py`)。
   `after_tool_call` hook 检测到 `tool_name == "structured_output"` 即
   `ctx.request_force_finish(...)`,经 ability_manager 冒泡到 ReAct 主 ctx → 本轮 `break`、
   不再发起下一次 LLM 调用。worker(`has_schema` 时)与 avatar session(`_start_avatar`
   建后 `start` 前)各挂一份;新增 `TeamHarness.add_rail` 委派给 `self._native.add_rail`
   (对称于既有 `add_tool`)。force-finish 的 payload(`{"accepted": True}`)无关紧要——
   backend 仍从 `StructuredOutputTool.captured` 读结果。**从机制上根治循环,不靠提示词
   说服小模型停止。**(实测 222→17 次。)

3. **`workflow()` 嵌套深度改 per-task contextvar**(`primitives._wf_depth` 取代
   `runtime.wf_depth`)。共享 int 把两个概念混为一谈——"当前执行路径的嵌套层数"(应 per-path)
   与"全局并发 workflow 数"(它实际测的)。contextvar 在 Task 创建时按值拷贝:`parallel`/
   `pipeline` 各分支继承父深度、推进**自己的副本** → 同层并发 `workflow()` 全部放行;真正的
   递归(子工作流 `run()` 再调 `workflow()`)在**同一 Task** 内、看到已自增的深度 → 仍按
   `_MAX_WORKFLOW_DEPTH=1` 封顶。`runtime.py` 删除无用的 `wf_depth` 字段。

4. **关停竞态:框架守卫 + E2E 等播报,双层防御。**
   - 框架层 `NativeHarness._ack(fut, value)`:`if fut is not None and not fut.done()`
     才 `set_result`——teardown 取消了等 ack 的注入任务后,没人在等这条 send,跳过即可。
     与错误路径早有的 `not ack.done()` 守卫一致;9 处 `cmd.ack.set_result(...)` 统一走它。
     兜住**任意**外部早停(用户 `stop team`)。
   - E2E 层:`workflow_completed` 后不立即 stop,而是等 leader stream 静默
     `_NARRATION_QUIESCE_S` 才收尾——让 leader 真正消费异步工具结果注入并播报,正常路径不再
     触发竞态,日志也含最终播报。

   E2E 脚本另含三项健壮性约定(避免"假绿"/"假卡死"):**唯一 session_id 每跑**(swarmflow
   journal 按 `(team, session_id, workflow名)` 寻址,固定 id 重跑会静默 resume 缓存替身、
   掩盖回归)、**chdir 到 gitignore 的 scratch 目录 + 短相对脚本路径**(长绝对路径被小模型
   篡改,实测 `alan_workspace/agent-core`→`alan-core`)、**启动看门狗**(工具失败/没启动时
   快速失败而非干等超时)。

## 拒绝的方案

- **`human()` 把 label 塞进 `options` 而非显式 kwarg**:破坏与 `agent()`/session 的对称;且
  一次性 human 不显式开 session,label 进不了临时会话的 member 命名/corr。显式 kwarg 才一致。
- **只改提示词治 `structured_output` 循环**(系统提示加"调用一次后停止" + ack 文案改"已记录,
  请停止"):对小模型非确定性,仍可能循环。force_finish 从机制断掉,跟模型无关。
- **在 `workflow()` 入口加锁/信号量限并发**:那是"限并发"不是"防递归",且会真把子工作流串
  行化。contextvar 栈深度才同时满足"禁递归 + 放行并发"。
- **关停只靠 E2E 等播报、不加框架守卫**:任意 E2E 之外的早停(用户 stop team 恰在结果注入
  在途时)仍会崩。两道防御都要——框架守卫是根治,E2E 等播报是让正常路径更干净。
- **E2E 传绝对脚本路径 + 提示词强调"不要改"**:小模型照样篡改。chdir + 短相对路径才稳。

## 验证

- **单测**:`workflow/` + `harness/` + `test_team_harness_integration.py` 共 123 passed。新增
  `test_structured_output_tool.py`(finish rail：structured_output 触发 force_finish、其它工具
  不触发、非 ToolCallInputs 不崩)、`test_session_primitives.py`(`human(label=,phase=)` →
  corr `signoff:host:0`)、`test_engine.py`(`parallel([workflow×3])` 全部非 None;子工作流再调
  `workflow()` 返回 None 即递归仍封顶);测试 fake `_FakeHarness` 补 `add_rail`。
- **真实 LLM E2E(qwen flash)**:party_planner `E2E PASSED`——6 阶段齐全、7 次 human 往返
  全自动应答、`structured_output` 222→17、`workflow_completed`;concurrent `CONCURRENCY
  E2E PASSED`——3 个并发子工作流各产独立邀请函、`nested workflow depth > 1` 命中 0 次。两者
  `supervisor crashed`/`InvalidStateError` 均 0,出现 `leader narration settled` 收尾。

## 已知遗留

- **observability 依赖未进锁**:team 无条件注入 observability rail,E2E 需安装
  `opentelemetry`(`pyproject` 的 `observability` extra)。本次 `uv.lock` 未随提交——若要
  让该依赖进锁文件,应单独走一次依赖管理提交。
- **`structured_output` 仍偶有 1 次引擎 retry**:force_finish 只在工具**被调用后**生效,管不了
  avatar/worker 首轮"完全没调工具"的情况(由引擎 `retries` 兜底);17 次即少数轮有 1 次重试。
- **E2E 播报完成判定是启发式**:用 stream 静默(`_NARRATION_QUIESCE_S=6s` + `_NARRATION_MAX_S`
  上限)近似"leader 播报结束",非精确的完成信号;够用但非确定性。未来可由 harness 暴露
  "异步工具注入已消费"的确定信号替代。
