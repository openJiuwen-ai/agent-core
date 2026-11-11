---
id: F_20261110_1851_leader-external-harness
title: Leader 三方 Harness 插拔实施与决策归档
type: F
author: alan_cheng
author_email: chengshuo5@huawei.com
modules: [agent_teams]
files:
  - openjiuwen/agent_teams/spawn/external_leader.py
  - openjiuwen/agent_teams/spawn/external_cli_spawn.py
  - openjiuwen/agent_teams/external/member_runtime.py
  - openjiuwen/agent_teams/external/cli_agent/spawn.py
  - openjiuwen/agent_teams/external/tool_gateway.py
  - openjiuwen/agent_teams/external/cli_agent/claude/sdk_mcp.py
  - openjiuwen/agent_teams/agent/agent_configurator.py
  - openjiuwen/agent_teams/agent/team_agent.py
  - openjiuwen/agent_teams/prompts/sections.py
  - openjiuwen/agent_teams/rails/team_policy_rail.py
  - openjiuwen/agent_teams/schema/blueprint.py
  - openjiuwen/agent_teams/schema/team.py
designs: [D-20261110-1839-leader-external-harness]
design_note: null
---

# Leader 三方 Harness 插拔实施与决策归档

## 关联依据

- 设计：`D-20261110-1839-leader-external-harness`。
- spec：`specs/agent_teams/S_27_external-harness-member-runtime.md` 不变量 16–19 与接口契约。
- 日期：2026-11-10。
- 实际实施范围：Leader 可经 `LeaderSpec.cli_agent` 跑在 claude / codex SDK harness 上，拥有 leader 团队
  工具集与 leader 提示词，支持同 session 续接，`enable_swarmflow` 的团队里持有 `swarmflow` 与 `async_task*`
  （见文末 2026-11-10 修订）。未落地：外部 Leader 的 fork、团队记忆、失败可靠性通道；三方 provider 与
  子进程适配器充当 Leader。

## 决策归属与关系

新决定，由上述设计承接。外部成员 runtime 的既有结论（S_27 不变量 1–15、F_96、F_117、F_125）继续有效；
本次只改变其中一条可观察行为：`pause` 对不能暂停但能中止的 provider 由抛异常改为 graceful abort。

## 实际实现与开发决策

1. **Spec**：`LeaderSpec.cli_agent` 按名引用 `external_cli_agents`。`_validate_leader_cli_agent` 拒绝未声明的
   名字、非 SDK backend、与 `enable_swarmflow` / `enable_fork` 同开。`build()` 把名字写进 leader 的
   `TeamRuntimeContext.cli_agent`，此后装配只看 ctx，并跳过 `_validate_leader_model_resolved`（三方 harness
   自带模型来源）。
2. **构建期不碰 session**：从 `build_cli_runtime` 抽出同步的 `build_sdk_member_runtime`，claude / codex 构造
   函数不再收 `TeamJoinDescriptor`，改收 `context`（现成的或工厂）与 `join_env`。teammate 传 descriptor
   派生的值，行为不变；Leader 传 context 工厂与空 `join_env`。
3. **start 期绑定**：`ExternalHarnessMemberRuntime` 新增 `add_startup_hook` / `reset_tool_bindings` /
   `has_stored_checkpoint` 与构造参数 `member_agent_id`。已知 agent id 时，`start` 先开 member session、再跑
   hook、再解析 context，hook 与 context 工厂因此都能读到 session 里有没有 checkpoint。
4. **Leader 模块** `spawn/external_leader.py`：`build_external_leader_runtime`（configurator 调用）与
   `bind_external_leader`（TeamAgent 在 configure 后调用）。hook 每次 start 做三件事：核对历史恢复标记、
   重建工具集、重绑轨迹 recorder。
5. **工具集**：两个网关构造器补了 `team_mode` / `bootstrap_mode` / `exclude_tools` / 专家目录参数；
   `_bind_protocol_member_team_tools` 增加 `role_tool_options`，Leader 传入模型分配器等，teammate 不传。
6. **提示词**：`TeamPolicyRail._build_leader_sections` 的正文下沉为 `prompts.build_leader_static_sections`，
   原生 Leader 的 rail 与外部 Leader 的 `build_leader_system_prompt` 共用一份装配。framework bootstrap 下
   准则经 `TeamContextTracker(policy_text=...)` 投递，走 MCP 的 Leader 带工具命名空间声明。
7. **pause 降级**：见 S_27 不变量 19。

为什么不这样做：

- 不让 teammate 也改走 startup hook：会改动已稳定的 spawn 绑定顺序，回归面大于统一带来的收益。
- 不为三方 provider 起进程内 HTTP MCP：没有要当 Leader 的 provider，属于为不存在的需求加传输面。
- 不把完整准则塞进外部 Leader 的系统提示词：会与原生 Leader 的披露时机分叉。

## 与设计的偏差

- 设计 5.2 写的是只新增 hook 与 checkpoint 探测；实施时还加了 `member_agent_id` 构造参数与
  `reset_tool_bindings`。前者是为了让 hook 先于 context 工厂运行且能读到 member session，后者是因为
  `bind_mcp_servers` 是追加语义，Leader 的 runtime 跨 run cycle 复用，不清会重复挂载。
- pause 降级比设计 D-03 多一个条件：provider 连 abort 能力都没有时仍抛原来的错误，而不是换成另一条
  "不支持 abort" 的错误。
- 设计 6.1 列了 `TeamAgent.build_context` 守卫，已实施；设计未提的 `_resolve_fork_native` 没有改，因为
  `enable_fork` 与外部 Leader 同开已被 spec 校验拒绝。

## 实际执行与证据

| 执行日期 / 验证范围 | 实现版本与环境 | 命令 / 复现步骤 | 实际结果 | 未覆盖项 |
| --- | --- | --- | --- | --- |
| 2026-11-10 V-01–V-05 | 工作区未提交；假 Codex harness、内存 DB、inprocess 传输 | `pytest tests/unit_tests/agent_teams/external/test_external_leader.py tests/unit_tests/agent_teams/external/test_member_runtime.py` | 51 通过 | 真实 CLI |
| 2026-11-10 V-06 回归 | 同上 | `pytest tests/unit_tests/agent_teams` | 4457 通过、39 跳过、2 xfail、1 失败 | 失败项见下 |
| 2026-11-10 V-06 回归 | 同上 | `pytest tests/unit_tests/harness_providers` | 249 通过、1 跳过 | 无 |
| 2026-11-10 端到端接线 | 同上，经 `Runner.run_agent_team_streaming` | 临时脚本：激活团队、投首轮输入、经工具网关调 `build_team` 与 `create_task`、`stop_agent_team` | 输入送达 harness；`build_team` 建队并返回协同准则；`create_task` 落库；停止后 harness 被 stop | 脚本未入库 |

唯一失败的 `tools/test_spawn_teammate_model_group.py::test_requested_model_survives_rebuilding_the_member_from_db`
在暂存本次改动后的基线上同样失败，原因是本机未安装 `intelli_router` 包，与本次改动无关。

### 可见行为、副作用与证据有效范围

- 模型可见：外部 Leader 的系统提示词含 leader bootstrap 与调用方的 leader 提示词；工具声明为
  `build_team` / `clean_team` / `create_task` / `update_task` / `view_task` / `send_message` /
  `spawn_teammate` / `shutdown_member`（default 模式、autonomous、未开 HITT / bridge / 专家目录时）。
- 副作用：`build_team` 写团队行并触发 `on_team_built`，`create_task` 写任务行，均经 leader 自己的 backend。
- 证据只覆盖假 harness。真实 claude / codex 的会话续接、工具调用往返、`AskUserQuestion` 中断与失败 turn 的
  流上表现都没有验证；claude 的进程内 MCP 工具集构造路径没有被单测覆盖（单测用 codex 分支）。

## 验证结论

- 已证明范围：spec 校验、构建接线、leader 工具集内容、每周期重绑、framework bootstrap 的准则通道、历史标记
  的两种结局、pause 降级、既有外部成员与团队用例不回归。
- 未覆盖：真实 CLI 端到端；`pause` 后 `RESUME_FROM_PAUSE` 再次 `kernel.start` 时 runtime 是否已被
  `finalize_round` 停掉（依赖现有周期收尾顺序，未单独构造用例）。
- 交付判断：单测与假 harness 端到端通过，可进入检视；真实 CLI 联调前不应视为可用。

### 覆盖与资料同步

已更新 `S_27`、`openjiuwen/agent_teams/AGENTS.md`。`S_18_harness-interaction-contract` 中"CLI runtime 的
pause 降级为 no-op"的表述针对子进程适配器 runtime，未改。

## 已知遗留

| 遗留项 | 性质 | 影响与现有处置 | 后续入口 |
| --- | --- | --- | --- |
| 外部 Leader 无 swarmflow / `async_task*` | 已解决（2026-11-10） | 见文末修订 | — |
| 外部 Leader 的 swarmflow 未跑过真实 worker | 已验证（2026-11-10） | 见文末真实 CLI 验证 | — |
| 外部 Leader 无 fork / checkpoint 工具 | 有意取舍 | spec 校验拒绝 `enable_fork` | 复用外部会话 fork（F_115） |
| CLI 压缩上下文可能丢失 `build_team` 披露的准则 | 未验证 | 仅 `bootstrap_mode="leader"` 受影响 | 实测后决定是否改走 tracker 投递 |
| Leader 失败无可靠性通道 | 部分解决（2026-11-10） | 失败 turn 以 `task_failed` 帧上流；没有重试与状态置 ERROR | 需要时为 Leader 设计独立的上报面 |
| 暂停的轮次不自动续跑 | 有意取舍 | 需要新输入唤起 | — |
| Leader 的 CLI skills 不做 project_dir 额外安装 | 未实现 | 只按 harness 配置装到 cwd | 与 teammate 的安装步骤对齐 |
| 真实 claude / codex 端到端 | 已验证主路径（2026-11-10） | 见文末真实 CLI 验证 | 冷恢复续接、pause、`AskUserQuestion` 仍未用真实 CLI 验证 |

## 实施演进与修订

### 2026-11-10 外部 Leader 接入 swarmflow（设计 D-02 修订）

首版按设计拒绝 `enable_swarmflow` 与 `leader.cli_agent` 同开。同日用户要求接上，设计 D-02 随之修订。

实际做法：

- `_validate_leader_cli_agent` 只保留对 `enable_fork` 的拒绝。
- `setup_agent` 的外部分支在 LEADER 且 `enable_swarmflow` 时调用 `build_swarmflow_infra`，与原生 Leader 是同一个
  单点构造；外部 teammate 的宿主本来就从 leader 的 `infra.swarmflow` 读取，因此不需要再改。
- `bind_external_leader` 建一个 `ExternalSwarmflowHost` 并交给 `_bind_protocol_member_team_tools`
  （新增 `swarmflow_host` 参数，不传则沿用原来的每次新建）。Leader 的 runtime 跨 run cycle 复用，宿主如果每个
  周期新建，上个周期启动的 run 就不在 `async_tasks_list` 里了。
- `ExternalHarnessMemberRuntime` 保存后台任务控制器（原为空操作），`dispose` 先 `stop_by_owner`。Runner 在每次
  run 的 stream 开始前挂接控制器，早于 harness start，所以 startup hook 读到的总是当次的控制器。
- `build_leader_system_prompt` 增加 `swarmflow_enabled`；`async_task*` 的排除改为"没有宿主时才排除"。

验证（2026-11-10，工作区未提交，假 Codex harness）：

- 新增用例 `test_swarmflow_team_leader_holds_the_swarmflow_and_async_task_tools`、
  `test_dispose_stops_the_leaders_background_runs_first`；`test_external_leader.py` 与 `test_member_runtime.py`
  共 52 通过。
- `pytest tests/unit_tests/agent_teams tests/unit_tests/harness_providers`：4707 通过、40 跳过、2 xfail、
  1 失败（同前，本机缺 `intelli_router`）。
- 经 `Runner.run_agent_team_streaming(background_task_controller=...)` 的临时脚本：Leader 调 `swarmflow` 启动一个
  不含 `agent()` 的内联脚本，收到启动回执；`workflow started` 播报与 `[Swarmflow completed]` 结果先后作为新输入
  送达 harness；`async_tasks_list` 列出该任务为 completed。脚本未入库。

未验证：带 `agent()` 的脚本（需要 worker 模型）、pause / resume 一个外部 Leader 名下的 run、真实 CLI。

### 2026-11-10 真实 CLI 验证与两处修复

环境：本机 Claude Code 2.1.305、codex-cli 0.162.1，各自的账号登录；内存 DB、inprocess 传输；经
`Runner.run_agent_team_streaming` 驱动的临时脚本（未入库）。

| 场景 | 结果 |
| --- | --- |
| Claude Leader：`build_team` + `create_task`，回复 DONE | 通过。工具经 `mcp__openjiuwen-team__*` 调用，任务落库 |
| Codex Leader（`external_model_config.model="gpt-5.6-luna"`）：同上 | 通过。团队行存在，任务为 pending |
| Codex Leader，本机默认模型 `gpt-6.1-sol` | provider 返回 400 `model_not_found`；修复前流上无任何输出，修复后出现 `task_failed` 帧 |
| Claude Leader + Claude teammate：`spawn_teammate(cli_agent="claude")`、指派任务 | teammate 认领、给 Leader 发消息、完成任务；Leader 收到消息并继续等待。未截到最终 DONE 与终态 |
| Claude Leader 的 swarmflow，脚本含 `agent(..., options={"agent_type": "claude"})` | 修复后通过：worker 返回 `pong`，完成结果回灌，Leader 回复 `FINAL=pong` |

验证中发现并修复的两处缺陷：

1. **Leader 的 turn 失败是静默的**。设计里"失败经输出流到达用户"的假设不成立：`HarnessIOAdapter` 默认不投影
   turn 终态，teammate 靠可靠性上下文把失败投到 leader 邮箱，Leader 两条都没有。修复：
   `HarnessIOAdapter(emit_turn_failure=...)` 把 FAILED turn 投影成 DeepAgent 的 `task_failed` 帧，
   `ExternalHarnessMemberRuntime(surface_turn_failures=...)` 透传，Leader 构建时打开。选 `task_failed` 是因为
   宿主已经会渲染原生 Leader 的这种帧，不需要认识新形状。teammate 默认关闭，行为不变。
2. **没有平台 build context 的团队，swarmflow 宿主丢了三方 worker 白名单**。`_external_swarmflow_host` 只在
   `spec.build_context` 非空时才派生成员上下文并写入白名单，否则宿主的 `build_context` 是 `None`，脚本里的
   `agent_type` 一律被判为"未声明"。这是外部 teammate 托管 swarmflow 时就存在的问题，外部 Leader 继承了它。
   修复：没有平台上下文时建一个普通 `BuildContext`，与进程内成员的 configurator 做法一致。

修复后的回归：`pytest tests/unit_tests/agent_teams tests/unit_tests/harness_providers tests/unit_tests/harness_protocol`
4744 通过、40 跳过、2 xfail、1 失败（同前，本机缺 `intelli_router`）。新增用例 3 个。

上面 `gpt-6.1-sol` 的 400 当时被归因为"账号不支持该模型"，这个判断是错的，见下一节。

### 2026-11-10 升级 codex CLI 后的复验

`brew upgrade --cask codex` 把 PATH 上的 codex 从 0.162.1 升到 0.167.0。直接重跑仍然是同一个 400，原因是
**Codex provider 默认不用 PATH 上的 CLI**：`CodexHarnessConfig.codex_bin` 为空时，Python SDK 用它自带的
`openai-codex-cli-bin`（本仓虚拟环境里是 0.144.4）。本机默认模型 `gpt-6.1-sol` 需要更新的 CLI，旧的捆绑
二进制请求它就被拒绝。所以前一节的失败不是账号问题，而是捆绑二进制过旧。

在 `external_cli_agents` 的 codex 条目上设 `cli_path="/opt/homebrew/bin/codex"` 后，用默认模型复验：

| 场景 | 结果 |
| --- | --- |
| Codex Leader：`build_team` + `create_task`，回复 DONE | 通过，团队行存在，任务为 pending |
| Codex Leader 的 swarmflow，脚本含 `agent(..., options={"agent_type": "codex"})` | 通过：worker 返回 `pong`，Leader 回复 `FINAL=pong` |
| Codex Leader + Codex teammate：`spawn_teammate(cli_agent="codex")`、指派任务 | 通过：teammate 认领、发消息、完成任务；Leader 收到后回复 ALLDONE，随后 `shutdown_member` 与 `clean_team`；任务终态 completed |

本轮没有改代码。观察到但未处理的两点：

- 协作场景里有 6 条 `Invalid state transition for member dev: idle -> running / completing / completed` 的
  ERROR 日志，对象是外部 Codex **teammate** 的执行状态，不影响任务完成。它在 teammate 既有路径上，是否为
  已知问题没有核实。
- Leader 调了两次 `clean_team`，结束后团队行仍在。没有查看这两次调用的返回值，不确定是工具拒绝还是其它原因。

捆绑二进制过旧这一点对所有走 Codex 的成员都成立，不只是 Leader：虚拟环境里的 `openai-codex` 不升级时，
需要靠 `cli_path` / `codex_bin` 指向较新的 CLI。
