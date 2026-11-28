---
title: framework 建队事件在订阅前发布被总线丢弃，插件漏收 team_created
author: zhanglongmin
author_email: zhanglongmin@huawei.com
id: B_20261128_1130_framework-team-created-event
type: B
modules:
- openjiuwen/agent_teams
files:
- openjiuwen/agent_teams/tools/team.py
- openjiuwen/agent_teams/agent/coordination/kernel.py
- tests/unit_tests/agent_teams/test_host_managed.py
- tests/unit_tests/agent_teams/test_coordination_lifecycle.py
designs: []
design_note: 方案在缺陷分析对话中与用户逐点确认（三类事件一起补、顺序保持 spawned→created、持留仅由 framework 调用点激活），无独立 D。机制契约依据见 S_05（注册与拉起分离）、tools/AGENTS.md（TeamBackend 事件发布路径）与 extensions/AGENTS.md（监控桥 best-effort、不重放）。关联 issue：#2440。
---

# framework 建队事件在订阅前发布被总线丢弃，插件漏收 team_created

## 现象与影响

`bootstrap_mode="framework"` 首次建队时，团队创建成功、核心日志出现「Team created event published」，但插件没有收到 `team.monitor_event` 中的 `team_created`。依赖这条通知初始化团队视图的插件，要等下一条团队事件、或等用户重新打开会话，才会显示团队信息。

窗口内丢失的不止 `team_created` 一条。framework 首建在 `CoordinationKernel.start()` 早期调用 `backend.build_team()`，此时 leader 尚未执行 `subscribe_transport()`，`build_team` 内部发布的三类事件全部落在「无订阅者」窗口里：

1. PASSIVE_HUMAN 预定义成员的 `MemberSpawnedEvent`（HITT 生效时，经 `spawn_passive_human` 发布）；
2. HOST_MANAGED 预定义成员的 `MemberSpawnedEvent`（经 `_publish_host_managed_registration` 发布）；
3. `TeamCreatedEvent`。

普通 AI teammate 与 leader 的注册不发事件（S_05：注册与拉起分离），它们的 `member_spawned` 在拉起时的 `_spawn_and_publish` 发布，拉起永远发生在订阅之后，不在窗口内。

## 关联设计与当前契约

无独立设计文档。涉及的当前契约：

- `TeamBackend.build_team` 在返回前发布 `TeamCreatedEvent`；幂等接管路径 `_reattach_team` 触发 `on_team_built` 但不发 `TeamCreated`（发布 created 本就是条件行为，本次延续该模式新增一个框架分支）。
- `TeamMonitorEventBridge` 经 `TeamAgent.add_event_listener` 注册，event listeners 只在 `subscribe_transport` 的 `_deliver` 中被调用——事件必须经 messager 订阅回来才能到达插件；桥本身 best-effort、不重放。
- F_122（framework bootstrap）声明「不新增事件、handler、工具、状态、启动路径、投递通道」，本次修复不新增任何事件类型与投递通道，只调整既有事件的发布时点，与其边界一致。

## 根因与修复决策

根因链（四环均已核实）：

1. `kernel.start()` 查不到团队行且 `bootstrap_mode == "framework"` 时就地 `build_team`，发布发生在 `kernel.start()` 早期；
2. `subscribe_transport` 在同一方法尾部才执行（订阅全部 `TeamTopic`）；
3. 进程内总线 `_Bus.publish` 无订阅者直接返回，无缓存、无重放（pyzmq 下 PUB 在 SUB 连接前发消息同样丢失，机制对两种 transport 成立）；
4. 三类事件都是一次性事件、无等价补偿：identity-only 成员（HOST_MANAGED / PASSIVE_HUMAN）注册即全生命周期、永不被拉起，`recover_team` 显式跳过它们且不重发注册事件；`team_created` 是唯一携带 `display_name / leader_member_name / created` 的一次性通知。

修复决策：

- **三类事件一起补**：同窗口、同机制（持留到订阅后按序 flush）。分开修会造成两套代码路径，插件侧将来还要再报一次「host_managed 成员不显示」。
- **`build_team` 增加 keyword-only `defer_event_publish` 参数**：开启时三个发布点经 `_publish_or_defer` 收集 `(topic, message)`；try/finally 只包住尾部发布段（三个发布点全在其间），任何失败路径都不会把收集窗口 latched 打开。默认 False 时行为逐字不变，`BuildTeamTool`（leader 模式）与 `spawn_host_managed_member` / `SpawnPassiveHumanTool` 等窗口外调用方零影响。
- **缓冲停泊在 backend（检视采纳，二次修订）**：初始实现把缓冲经 `build_team` 返回值传给 kernel 局部变量，`start` 中段失败（workspace 初始化 / `write_system_and_tool_prompts` / `init_toolkit` / `event_bus.start` / 订阅，`kernel.start` 在 build 与 flush 之间的整个中段）会让缓冲随局部变量丢弃；而 `db.team.create_team` 已成功，下次 `start` 走恢复分支永不重发——后果与原始缺陷同症状。修订为**双字段**：`_deferred_events` 仅作活动收集窗口（`build_team` 的 finally 关闭，建队失败即丢弃——无团队事实可通知），`_pending_build_events` 承载待 flush 缓冲（建队成功后转入，跨 `start` 存活）。`flush_pending_build_events` 无参、逐条 best-effort、整趟尝试完即清空——每条事件至多一次发布尝试，重试不会让已收到者重复。kernel 删除缓冲局部传递，统一在**每次** `subscribe_transport` 成功后 flush 停泊缓冲：framework 当次与 warm 重试（含恢复分支）共用一条路径。
- **flush 顺序保持收集序（spawned → created）**：与 leader 模式 `BuildTeamTool` 的可观测顺序逐字一致，不引入两种 bootstrap 模式的事件顺序分叉。
- 检视另指出「修复前 build 时 best-effort 发布可到达提前订阅的外部观测方，中段失败组合下构成倒退」：经核实对 leader 自身链路（监控桥——本修复的主要受益方）两版在该场景下都收不到、属持平；对外部提前订阅者的倒退是条件性的（取决于宿主是否在 `start` 前订阅 topic）。采纳停泊补发后该缝隙消解，不再依赖对该 claim 的判定。

真实考虑过的备选与拒绝原因：

- **给总线加缓存 / 重放**：把 best-effort 语义改成可靠队列，波及全部 topic 全部事件的消费方，改动面远超本缺陷。拒绝。
- **只修 `team_created`**：两类 identity-only 成员的注册事件同病同源（一次性、无补偿），分开修需要两份文档解释为什么只补一半。拒绝。
- **顺手把顺序统一为 created → spawned**：需要连 leader 模式一起改（否则两模式分叉），那是 leader 模式的行为变更，超出本修复边界。作为后续建议记入遗留。

## 回归证据与适用范围

候选：本次修复的工作区改动，与本文档同批提交（B_20261128_1130）。环境：Windows 11，Python 3.11.9，pytest 9.2.0，`uv sync --extra observability` 后的环境。Refs: #2440。

- 新增 `tests/unit_tests/agent_teams/test_coordination_lifecycle.py` 5 例（framework bootstrap 段）：defer 传递、flush 严格在订阅之后、**中段失败后恢复路径补发停泊缓冲**（检视核心场景：第一次 start 在 `update_status` 失败 → flush 未发生；第二次 start 走恢复分支 → flush 补发）、订阅失败当次不发布；既有 framework 测试桩 `_framework_backend` 补 `flush_pending_build_events`。
- 新增 `tests/unit_tests/agent_teams/test_host_managed.py` 2 例：真实 `TeamBackend` + `InProcessMessager` 验证 defer 期间零发布、停泊缓冲顺序为 [passive spawned, host spawned, created]、flush 后订阅者按序收到且缓冲清空、二次 flush 不重复投递；flush 逐条 best-effort（首条失败次条仍发布、整趟清空）。
- 回归：上述两文件 36 passed；全部真实 `build_team` 调用方（test_hitt / test_lookup_role_db / test_dispatch_choice / team_workspace 3 文件 / test_passive_tool_executor / bridge 2 文件）132 passed；agent_teams level0 冒烟 1686 passed。
- 环境性失败与本次无关：`team_workspace/test_resolve_identity_read_path.py` 3 例为 Windows 符号链接特权（WinError 1314）；`cli/` 4 文件收集失败为缺 `prompt_toolkit`。
- 未验证范围：pyzmq 真实传输的跨进程 E2E 未跑（发布点为同一 `messager.publish`，机制与 transport 无关）；插件（jiuwenswarm）侧实际收到行为依赖宿主联调。

## 防错模式与资料同步

- 「**发布时点必须晚于订阅建立**」是本修复沉淀的约束：framework bootstrap 的建队事件一律经缓冲、由 kernel 在订阅后 flush；新增 build-time 事件时同样走 `_publish_or_defer`，不得直接 `messager.publish`。当前仅此一处置有该模式，暂不上推为模块级 spec 不变量；若后续出现第二个调用方再提炼。
- 本次检查过 F_122 与 S_12：F_122 只声明「不新增事件」，未描述发布时点，无需追补；S_12 仅枚举 `team_created` 事件类型，不动。
- 索引已由 `sync_documents.py --document` 刷新（见本次执行记录）。

## 遗留与后续

- **start 中段失败的补发覆盖范围（检视修订后）**：缓冲停泊在 backend 实例字段上，覆盖 warm 路径（同实例的下次 `start`：失败重试、pause→start、恢复分支）——恢复分支补发的正是团队已建库但上次 start 没走到 flush 的一次性事件。**冷恢复不覆盖**：进程重启 / 换 session 时 `recover_from_session` 重建的是新 backend 实例，停泊缓冲随之丢弃，由宿主侧重开会话兜底（与插件现状一致）。把缓冲落 DB 以覆盖冷恢复被评估后拒绝：那会把 best-effort 事件升级成持久化投递队列，与「桥不是可靠存储、不重放」的设计语义冲突，且引出投递去重等新契约。
- **flush 的 at-most-once 语义**：单趟 flush 逐条 best-effort，趟末整体清空——部分失败的事件不重试。重试会造成已收到者重复通知，而 `team.monitor_event` 的消费方契约不保证幂等。
- **created → spawned 顺序统一化**：若团队认可「先团队上下文、后成员叠加」作为统一契约，应作为独立改动（`build_team` 尾部两处发布对调 + 插件侧验证），不与本次修复耦合。
- 插件侧消费验证依赖 jiuwenswarm 宿主联调，agent-core 侧以 event listener 收到行为为等价证据。
