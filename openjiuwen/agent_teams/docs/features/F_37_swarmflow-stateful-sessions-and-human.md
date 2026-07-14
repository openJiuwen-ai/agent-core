# Swarmflow 有状态会话（agent_session / human_session）与 human 参与点

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-08 |
| 范围 | `workflow/engine/`（`primitives` / `backends/base` / `backends/mock` / `journal` / `runtime` / `runner` / `progress` / `facade` / `provider` / `seam` / `__init__`）、`workflow/`（`runner.py` / `tool_swarmflow.py`，新增 `backends/avatar_session_backend.py` / `backends/_member_spec.py`，改 `backends/team_worker_backend.py`）、`harness/team_harness.py`；human 外部 wiring：`schema/events.py`、`agent/agent_configurator.py`、`rails/{team_context,elements,team_tool_rail}.py`、`tools/tool_factory.py`、`runtime/manager.py`、`agent/coordination/handlers/workflow.py`、`i18n.py` |
| 测试基线 | 新增 `test_session_primitives.py`(10) + `test_avatar_session_backend.py`(9) + `test_swarmflow_human_routing.py`(4) + `test_swarmflow_leader.py` human_prompt 用例；`tests/unit_tests/agent_teams/` 全套 1418 passed / 16 skipped / 0 failed（排除预存缺 `prompt_toolkit` 的 cli/observability 模块）|
| Refs | #751 |

## 背景

swarmflow 此前只有一种执行单位：单轮 worker（`agent()` → `TeamWorkerBackend.run` →
`TeamHarness.run_once` → `DeepAgent.invoke`，用完即弃、无记忆）。两个真实缺口：

1. **有状态 agent 交互**：需要能多轮对话、跨轮保留上下文（DeepAgentState / task-loop /
   工具状态 / persona）的 agent。诉求明确：**后端对接 teammate 能力，而不是每轮重建一个单轮
   worker**——拒绝"单轮重放冒充有状态"。
2. **human 参与点**：流程中向真人提问、等真人回复，且真人的原始输入要**经 LLM 格式化为
   结构化输出**——所以 human 也需要一个 avatar 的 DeepAgent，处理方式与 teammate 相似
   （`schema/team.py` 已把 `HUMAN_AGENT` 定义为 avatar-style role）。

参考蓝本是 dw 项目新增的 session DSL（`agent_session`/`human_session`/`send`/`notify`/
`options`），但 dw 的实现是"编排层维护 history + 无状态 backend 每次重放"——本特性借鉴其
**DSL 形态**，backend 换成**真正有状态的 avatar harness**。

## 数据结构 / 状态机

- **`AgentSession`（引擎层，`primitives.py`）**：轻量句柄，持 `_label/_phase/_instructions/
  _options/_human/_history/_sid/_in_flight`。`_history` 是 `(user, assistant)` 镜像，仅用于
  resume 签名与未来 fork；**不进 journal**（靠脚本重放重建）。`_sid` 懒开：首个 cache-miss
  的 `send` 才调 `backend.open_session`。
- **`_SessionState`（业务层，`avatar_session_backend.py`）**：每个有状态会话一行，持
  `kind/spec_base/instructions/member_name/harness/turns_executed` + 每轮收口三件套
  （`turn_future/last_finished/failed`）+ `lock`（一会话一时刻一轮）。
- **send-等-收状态机**：`harness.send(prompt, immediate=False)` 起一轮 → 监听
  `on_round(finished)` 缓存最近 result、`on_state(RUNNING→IDLE)` resolve 本轮 future。一次
  `send` 可能驱动多轮才回 IDLE（task-loop continuation），取**最后一轮 finished 的
  `output`**。

## 决策

1. **有状态 agent = 长生命周期 NativeHarness（teammate spec 派生），不进协调循环**。
   `AvatarSessionManager.open_session` 用 `_member_spec.derive_member_spec`（与 worker 同源）
   从 `worker_base_spec` 派生唯一 card + 多轮 persona + 模型 → `TeamHarness.build(role=WORKER)`
   → `start()` 一次 → 多轮 `send`。`role=WORKER` = "有记忆的 worker"，与现有 worker 同隔离级别
   （不订阅 mailbox、不认领任务），符合 orchestration/coordination 分层铁律。
2. **human = agent + 真人输入源，复用同一 `AvatarSessionManager`**。差别仅 spec 来源
   （`human_base_spec`）与输入源：`_human_turn` 推问题 → 等真人 raw 回复 → 把"问题+回复"喂
   avatar harness，由 avatar 用 LLM 格式化/结构化。四原语对称：`agent()`/`agent_session`/
   `human()`/`human_session`。
3. **resume 靠 avatar `Session` checkpoint，不手搓 history 重放**。cache hit 全程不建
   harness、不调 LLM（引擎层短路）；首个 cache miss 才 `TeamHarness.build` + `start(session=
   avatar_session)`，上一次最后状态（含 DeepAgentState/task-plan）由 session 自带恢复。
4. **backend 接口扩展而非新类**：`AgentBackend` 加 `KNOWN_OPTIONS` + `open_session`/
   `send_turn`/`close_session`/`aclose`（默认抛 `NotImplementedError` / `aclose` no-op），
   `run()` 逐字不动 → Runtime 仍只持一个 backend，worker 路径零影响。`run_workflow` finally
   调 `backend.aclose()` 收口所有会话。
5. **`call_signature` 仅 history 非空时折入**：`agent()`（history 恒空）签名逐字节不变，
   worker resume 零回归；会话 turn 折入 history → 上游变更级联重跑下游。
6. **options bag 白名单仅作用于新原语**：`_build_opts` 校验 `_ENGINE_OPTIONS |
   backend.KNOWN_OPTIONS`，未知 key fail-fast；`agent()` 继续用显式 kwargs。
7. **human 会合 = `AvatarSessionManager` 实例字段 `_pending_human`（corr→future）+ messager
   + 复用 `interact_agent_team`**，无进程级全局 registry；`submit_human_reply(corr, answer)`
   是入向口，`aclose` 取消所有未决 future。
8. **interrupt → 抛 `BackendError` + `team_logger.error` + "后续特性"注释**，不返回半截结果
   （avatar 内部 HITL 留作后续特性）。
9. **schema 多轮注入 per-turn**：会话 IDLE 间隙 `harness.add_tool(StructuredOutputTool)` +
   user prompt 追加 nudge，轮末 `remove_tool`；ability_manager 按 owner re-qualify，并发会话
   不撞。

## 拒绝的方案

- **dw 模型（引擎层 history + 无状态 backend 每轮重放）**：被明确拒绝——"单轮重放冒充有状态"
  不满足"对接 teammate"，且每轮重建上下文丢失 DeepAgentState/task-plan/工具状态。
- **进程级全局 `SwarmflowHumanRegistry` 单例**：生命周期不清、测试隔离差、与既有 messager
  语义重叠。改为 manager 实例字段 + 既有 messager 总线会合。
- **手搓 history seed 进 context 做 resume 重建**：与既有 `Session` checkpoint 机制重复。
  改为 harness `start(session=)` 由 session 恢复。
- **human 做成完整 human_agent 团队成员（spawn/roster/HITT 全套）**：对一个瞬时问答点过度
  设计。只复用 human_agent 的"avatar 格式化 + 通知外部"语义。
- **随机 uuid 作 correlation_id**：被拒——uuid 在"等真人期间中断 → resume"时会变，UI 上已显示
  的旧 corr 失效、真人回复被拒，破坏人机交互可重放。改为引擎确定性生成 `{phase}:{label}:{turn}`
  （与 agent 的结构化 journal key 同源思想），脚本流程确定故重放一致；外部回复带非法 corr（不匹配
  任何 pending）则 `submit_human_reply` 拒绝 + 告警。HUMAN_PROMPT 仍从 backend 等待路径发（只在
  实际等人时），human turn 本身以 `AGENT_STARTED/COMPLETED` 落进 4 层。
- **真人回复专用 facade `answer_swarmflow_human`**：评估后选了"复用 `interact_agent_team`
  薄路由"（seam B），不新增公共表面（详见已知遗留）。

## 验证

- 引擎层（`MockBackend` + 录制 backend，零网络/零 LLM）：多轮 history 累积、options 白名单
  fail-fast、notify 单向、resume 全-hit 纯重放、上游变更级联重跑、`agent()` 签名不变。
- `AvatarSessionManager`（fake harness 驱动回调）：单 harness 跨轮复用、schema 挂摘、interrupt
  抛错、human 推-等-格式化、human 超时 skipped、aclose 全 dispose、human 无 base spec 清晰报错。
- 全 `tests/unit_tests/agent_teams/` 1411 passed / 0 failed，worker 重构（共享 `_member_spec`
  helper）零回归。

## human 外部 wiring（seam B，已接线）

后续接续时已完成的外部链路，与上文决策一致：

- **出向**：`engine/progress.py` 加 `HUMAN_PROMPT`/`HUMAN_REPLIED` + `correlation_id`；manager
  的 `on_human_prompt(member, corr, prompt)` 由 `run_swarmflow` 接到 `observer.emit
  (WorkflowProgressEvent(kind=HUMAN_PROMPT, ...))` → `_publish` → `WorkflowProgressTeamEvent`
  （加 `correlation_id`）→ leader `WorkflowHandler` 渲染 `workflow.human_prompt`/`human_replied`
  文案（i18n cn/en）。**corr 由引擎确定性生成 `{phase}:{label}:{turn}`**（`turn = len(history)//2`，
  hit/miss 都推进），跨 resume 稳定；HUMAN_PROMPT 事件只从 backend 等待路径发（实际等人时），
  故不在 cache-hit 重放上出现，progress 也不进 journal。
- **装配**：`agent_configurator` 计算 `swarmflow_human_base_spec`（`base_specs.get
  ("human_agent")` 缺省回退 worker spec），经 `inject_team_handles` →
  `SWARMFLOW_HUMAN_BASE_SPEC` handle → `elements`/`team_tool_rail`/`tool_factory` →
  `SwarmflowTool` → `run_swarmflow` → `TeamWorkerBackend` → `AvatarSessionManager`。messager +
  session_id 同链路下发。
- **入向（seam B）**：`schema/events.py` 加 `TeamEvent.WORKFLOW_HUMAN_REPLY` + 专用
  `swarmflow_human_reply_topic(session_id, team_name)`（独立于 `TeamTopic.TEAM`，不与 leader 订阅
  冲突）。`runtime/manager.py:interact` 在 `resolve_targets` 之前用
  `_as_swarmflow_human_reply` 识别 `HumanAgentMessage(target="swarmflow:<corr>")` →
  `_route_swarmflow_human_reply` publish 到 reply topic（绕过 gate / roster）。
  `AvatarSessionManager` 在首个 human open 时 `messager.subscribe(reply_topic, _on_reply_event)`，
  handler 过滤 `WORKFLOW_HUMAN_REPLY` → `submit_human_reply`；`aclose` 退订。

## 已知遗留

- **human 全链路系统测试**：单测覆盖了 manager turn 逻辑、messager 往返、interact 路由检测、
  leader 播报；但"真实 LLM avatar 把真人输入结构化 + 跨进程 messager"的端到端需一个
  system test 验证（无 LLM 环境跑不了）。
- **CLI `$` 回复语法**：UI 目前需用 `interact_agent_team(HumanAgentMessage(target=
  "swarmflow:<corr>"))` 回复；交互式 CLI 的 `$swarmflow:<corr> <answer>` 解析（`parse_interact_str`
  是否允许 `:`）未验证，作为 UI 侧 follow-up。
- **resume 部分-hit 续跑**：依赖 avatar session checkpoint 持久化分桶（复用 team 的
  `session.state["teams"]` 机制）；首期保证"冷启动多轮 + 全-hit 纯重放"。
- **`fork`**：本期不做。
