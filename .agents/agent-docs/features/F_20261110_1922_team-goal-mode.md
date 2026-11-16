---
id: F_20261110_1922_team-goal-mode
title: Team Leader 与成员 Goal 模式实施与决策归档
type: F
author: alan_cheng
author_email: chengshuo5@huawei.com
modules: [agent_teams, harness]
files:
  - openjiuwen/agent_teams/goal.py
  - openjiuwen/agent_teams/agent/goal_coordinator.py
  - openjiuwen/agent_teams/rails/team_goal_rail.py
  - openjiuwen/agent_teams/rails/elements.py
  - openjiuwen/agent_teams/harness/native_harness.py
  - openjiuwen/agent_teams/harness/team_harness.py
  - openjiuwen/agent_teams/harness/control.py
  - openjiuwen/agent_teams/harness/state.py
  - openjiuwen/agent_teams/agent/team_agent.py
  - openjiuwen/agent_teams/agent/agent_configurator.py
  - openjiuwen/agent_teams/agent/stream_controller.py
  - openjiuwen/agent_teams/agent/member_activity.py
  - openjiuwen/agent_teams/agent/spawn_manager.py
  - openjiuwen/agent_teams/agent/coordination/handlers/agent_lifecycle.py
  - openjiuwen/agent_teams/agent/coordination/handlers/team_completion.py
  - openjiuwen/agent_teams/schema/team.py
  - openjiuwen/agent_teams/schema/blueprint.py
  - openjiuwen/agent_teams/tools/member_options.py
  - openjiuwen/agent_teams/tools/team.py
  - openjiuwen/agent_teams/tools/tool_member.py
  - openjiuwen/agent_teams/i18n.py
  - openjiuwen/harness/rails/task_completion_rail.py
  - tests/system_tests/agent_swarm/goal/agent_team_goal_mode_e2e.py
designs: [D-20261110-1903-team-goal-mode]
design_note: null
---

# Team Leader 与成员 Goal 模式实施与决策归档

## 关联依据

- 设计：[D-20261110-1903-team-goal-mode](../designs/active/D-20261110-1903-team-goal-mode/design.md)。
- spec：agent_teams `S_03`（完成判定约束）、`S_08`（任务工具与 `spawn_teammate`）、`S_09`（`TeamGoalRail`）、
  `S_12`（配置面）、`S_18`（Goal round）；harness `S_11`（第二个宿主与两个新入口）。
- 日期：2026-11-10。
- 实际实施范围：NativeHarness 的 goal round 驱动；成员 goal 的认领立项、提交闸、与任务板的对账；Leader
  goal 的立项、静止等待与完成闸；配置面与 `spawn_teammate(goal)`；单测。三方 Harness 成员 / Leader、
  human / bridge 成员未纳入。任务工具描述与角色提示词未改（见已知遗留）。

## 决策归属与关系

新决定，无被替代的旧记录。它依赖并保持两条既有结论：goal 状态独占 `GoalManager`（harness `S_11`
不变量 1）与 NativeHarness 不依赖 `TeamAgent`（agent_teams harness 铁律 1）。与 verify 闸（F_59 / F_62）
正交：reviewer 是同伴评审，goal 是成员对自己任务的坚持与提交前自检。

## 上下文与跨边界影响

已证实（读代码 + 单测）：

- 改动前 `GoalManager` 只在 `DeepAgent.start()` 构造，`NativeHarness.start()` 不调父类 `start()`，team 成员
  的 `goal_manager` 恒为 None。
- `NativeHarness` 继承到的 `_event_manager` 与 `_interaction_control_lock` 可以直接喂给 `GoalManager`；
  `submit_round` 的 `run_kind` / `run_context` 一路透传到 `TaskCompletionRail`，goal round 的评估回路不依赖
  DeepAgent 的交互循环。设计 §3 的两条假设成立。
- 内核 `AbilityManager._railed_execute_single_tool_call` 在 `before_tool_call` 之后读取
  `ctx.extra["_skip_tool_calls"][tool_call_id]`，命中即不执行工具、直接返回 rail 填好的
  `tool_result` / `tool_msg`。提交闸靠它生效。

跨边界链路：任务状态变化（DB，产生方是成员工具 / 调度器 / leader 工具）→ 成员进程在自己一轮收尾时读
任务板 → 经 `GoalManager` 写成员 session state 里的 `GoalRecord` → harness supervisor 据此决定是否起
goal round。Leader 侧：成员状态事件 → `MemberActivityRegistry` → `team.idle` 去抖 / 完成轮询 →
`nudge_goal()`。没有新增跨进程消息类型；成员 goal 触顶上报复用点对点消息。

## 实际实现与开发决策

**harness 层**

- `NativeHarness.enable_goal(stop_config, ready=None)`；`start()` 里 `_setup_goal` 构造 `GoalManager`、
  按需注册 `TaskCompletionRail` 并把停止策略交给它（`set_goal_manager(..., stop_config=)`）。
- `_settle_round_done` 在排队输入之后、task-plan 续跑之前调 `_start_goal_round_if_ready`。
- 新控制命令 `_CmdGoalWork` 承载 `nudge_goal()`；`GoalManager.notify_work` 直接接它。
- 事件管理器只当 goal 队列用：出队后立刻 `mark_started` + `mark_finished`。原因：round 由 supervisor
  串行，in-flight 记账无用；而不清掉它，`push_goal` 会因"同一代 goal 已在跑"拒绝排入续跑。
- 目标被替换 / 清除时不取消在跑的 goal round（`cancel_active_round` 是空实现）。那一轮的评估因记录不再
  匹配而被 `TaskCompletionRail` 丢弃；而一次 abort 会连带丢掉排在它后面的输入。与 F_56"任务级信号取代
  成员取消"一致。
- BLOCKED 的 goal 在一轮外部输入收尾时被 `resume()`。放在 harness 而不是 team 侧，因为"刚收尾的是不是
  goal round"只有 supervisor 知道。

**成员**

- `TeamGoalRail`：`after_tool_call` 上认领成功即立项；`before_tool_call` 上的提交闸调
  `TaskCompletionRail.assess_completion_claim`（新增的只读评估入口），不通过就跳过工具并把评估写回
  `GoalRecord`（下一轮 goal round 的指令因此带着剩余工作）。任务落库成功后才 `apply_assessment(COMPLETE)`
  并解除绑定。
- `TeamGoalCoordinator._sync_task_goal` 作为成员 harness 的 `goal_ready`，每轮收尾时把 goal 与任务板对齐。
- goal 被轮末评估判为完成而任务没提交：不重开 goal，只给成员发一次"去提交"的输入。重开会清零
  attempt 计数，评估器若持续判完成而成员不提交，就成了没有上限的循环。

**Leader**

- `AgentLifecycleHandler.on_user_input` 在**投递之后**调 `open_user_goal`。先立项的话，goal 会在空闲的
  Leader 上自己起一轮，随后到达的同一条请求又被当作排队输入处理一遍。
- `goal_ready` = `MemberActivityRegistry.others_idle()`：只看别人，Leader 自己此刻还在收尾。
- `TeamCompletionHandler.on_poll_task` 经 `goal_holds_completion()` 拦截；同一个调用顺手 nudge。
- `StreamController` 在 `team.idle` 去抖到点、查任务板**之前**触发 `team_quiet_callback`：全员静止但任务没
  做完，正是 Leader 该对照目标检查的时候，不应被"任务板已收敛"这个条件挡住。

**why-not**

- 不让 NativeHarness 调 `DeepAgent.start()`：会多出一套交互 supervisor 和输出 forwarder。
- 不把立项写进 `ClaimTaskTool.invoke`：工具被外部 CLI 成员经 MCP 复用，它们没有 harness。
- 不先落 `COMPLETED` 再评估：依赖已解除、下游可能已开工，回滚不可靠。

## 与设计的偏差

| 设计 | 实际 | 原因 / 影响 |
| --- | --- | --- |
| §4.1 / §5.1：调度器开工、计划批准、评审打回、改派、取消由 `TaskBoardHandler` 逐事件处理 | 由成员每轮收尾时的一次对账（`_sync_task_goal`）覆盖，`task_board.py` 未改 | 这些事件到达成员后都会驱动一轮，收尾时任务板已是最新；一处对账消掉五个事件分支，冷恢复后也自动对齐。用户要求的"claim 工具触发"仍由 rail 在同一轮内完成。目标行为不变 |
| §5.2：独立的 `TeamGoalBinding` 对象 | `goal.py` 的模块级函数 + `TeamGoalCoordinator` | 绑定逻辑没有自己的状态，函数足够 |
| §5.2：`NativeHarness` 构造参数 `goal` / `goal_ready` | `enable_goal()` 方法 | `TeamHarness` 每个运行周期重建 native，且 `ready` 在 harness 建好之后才由 `TeamAgent` 装上 |
| §5.6：评审打回时带 feedback 重新立项 | 重新立项，但 objective 不含 feedback | 对账只读任务板，拿不到 feedback；feedback 已由既有的 `TASK_REVISION_REQUESTED` 输入送进成员上下文。`bind_task_goal(feedback=)` 参数保留但当前无调用方 |
| §5.3：成员触顶上报用 `prompts/<lang>/` 模板消息 | `i18n.py` 的 `goal.member_blocked` 直接渲染后发送 | 文案只有一句且带运行时原因，未走两阶段模板 |
| §6.1 第 7 步：提示词 / 工具描述片段 | 只加了运行时文案与 `spawn_teammate.goal` 参数描述 | 见已知遗留 |

## 实际执行与证据

| 执行日期 / 验证范围 | 实现版本与环境 | 命令 / 复现步骤 / 记录链接 | 实际结果 | 未覆盖项 / 失败原因 |
| --- | --- | --- | --- | --- |
| 2026-11-10 harness goal round | 工作区未提交改动，基线 `65679903c`；真实 task-loop 内核 + `FakeReactAgent` | `pytest tests/unit_tests/agent_teams/harness/test_goal_rounds.py` | 5 通过 | 无真实模型 |
| 2026-11-10 team goal | 同上；真实 `NativeHarness` + 内存任务板桩；SQLite `:memory:` 的 `TeamBackend` | `pytest tests/unit_tests/agent_teams/test_team_goal.py` | 33 通过 | 见下 |
| 2026-11-10 回归 | 同上 | `pytest tests/unit_tests/agent_teams tests/unit_tests/harness/goal tests/unit_tests/harness/test_task_completion_extensions.py` | 4584 通过、39 跳过、2 xfail、1 失败 | 失败的是 `tools/test_spawn_teammate_model_group.py::test_requested_model_survives_rebuilding_the_member_from_db`：环境未安装 `intelli_router` 包；在未改动的主仓库检出上同样失败，与本次改动无关 |

| 2026-11-10 真实模型 E2E-1：autonomous，正常成员 | 同上；deepseek-flash，inprocess 团队（leader + 1 成员），SQLite 文件库，`strategy=hybrid`、`max_attempts=4` | 临时脚本经 `Runner.run_agent_team_streaming` 驱动，请求：交付 `fib.py` + `test_fib.py` 并实际运行通过 | 通过：认领时立成员 goal；提交前评估模型被真实调用（temperature 0 的非流式请求）并放行；全员静止后 Leader 起 1 轮 goal round，评估 complete 后才出现 `team.completed`；交付的测试独立复跑通过。约 51s | 成员未出现需要续跑的情况 |
| 2026-11-10 真实模型 E2E-2：autonomous，偷懒成员 | 同上；成员系统提示词要求"不干活直接提交" | 同上 | 通过：首次提交被闸拒绝（评估依据为"只有认领与完成两次调用、无任何证据"），成员按返回的剩余工作补做后再次提交被接受。约 60s | 无 |
| 2026-11-10 真实模型 E2E-3：scheduled，正常成员 | 同上，`dispatch_mode=scheduled` | 同上 | **暴露缺陷**：成员在调度器开工后的同一轮内做完并提交，goal 只在轮末对账时才立，提交未经评估。Leader goal 与完成闸正常 | 见"实施演进与修订" |
| 2026-11-10 真实模型 E2E-4：scheduled，偷懒成员（修复后） | 同上 | 同上 | 通过：`member_complete_task` 首次提交被拒，补做后接受；Leader goal round 评估 complete 后 `team.completed`；交付测试复跑通过。约 68s | 无 |

| 2026-11-10 E2E-5：3 名成员、3 个依赖任务（autonomous） | 同上，deepseek-flash | 多场景临时脚本 | 通过：三名成员依次认领立 goal、提交过闸；Leader 核对后 `team.completed`；交付的测试独立复跑通过。约 209s | 无 |
| 2026-11-10 E2E-6：scheduled + reviewer，首次验证打回 | 同上，`enable_task_verification=True`，开发 + 验证者 | 同上 | 通过：打回后同一任务得到新的 goal，二次提交被接受；评审进行中 Leader 的 goal round 两次判 continue、第三次判 complete。约 127s | 无 |
| 2026-11-10 E2E-7：plan_mode | 同上，`teammate_mode=plan_mode` | 同上 | 通过（goal 部分）：计划批准后任务得到 goal，提交经评估后接受 | 途中暴露与 goal 无关的既有问题，见已知遗留 |
| 2026-11-10 E2E-8：成员每轮只有 4 次内部迭代 | 同上，成员 `max_iterations=4` | 同上 | 通过：成员两次以 `Max iterations reached` 中断，goal 续跑两轮（attempt 1 评估 continue，attempt 2 内提交被接受）。约 58s | 无 |
| 2026-11-10 E2E-9：Leader 中途改派 | 同上，2 名成员 | 同上 | 通过：任务改派后原持有者的 goal 被清除，新持有者得到 goal 并完成；Leader goal 两次判 continue 并补建任务，第三次判 complete。约 234s | 无 |
| 2026-11-10 E2E-10：严格串行任务，成员每轮 2 次迭代、只允许 1 次续跑 | 同上，成员 `max_iterations=2`、`member_goal.max_attempts=1` | 同上 | 通过：成员 goal 以 `max_attempts_exhausted: 1/1` 转 BLOCKED，Leader 收到上报后接手做完，任务离手后成员 goal 被清除，Leader goal 判 complete。哈希链 8 步独立复算全部一致。约 109s | Leader 自己的 goal 触顶与 `team.goal.blocked` 标记未出现 |

| 2026-11-10 system test 全量 | 同上，deepseek-flash；`tests/system_tests/agent_swarm/goal/agent_team_goal_mode_e2e.py` | `OPENJIUWEN_E2E_LLM_CONFIG=<本地配置> python tests/system_tests/agent_swarm/goal/agent_team_goal_mode_e2e.py --jobs 5` | 9/9 场景通过，每个场景的全部检查通过（含对交付物的独立复核） | 一次运行；依赖模型自主动作的三个场景（见用例 README）这次都走到了目标路径 |

运行环境：worktree 自带的 `.venv` 缺 `opentelemetry.exporter`，依赖它的既有用例在该环境下本来就失败；
以上均用主仓库 `.venv` 的解释器、以 worktree 为工作目录执行。未运行格式化与静态检查（仓库约定）。

### 可见行为、副作用与证据有效范围

- 模型可见：提交被拒时的工具结果文本（断言含剩余工作）；goal round 的 query 含任务标题与内容；
  "去提交"提醒只发一次。断言的预期来自测试内写死的任务与评估，不是快照。
- 独立读取的状态：`GoalRecord`（经 `GoalManager.get()`）、绑定键、任务板桩的任务状态、落库的
  `TeamMember.options.goal`。
- 提交闸的评估在单测里要么走 `agent_report` 策略、要么被桩替换；HYBRID 下真实的评估模型调用由上表的
  真实运行覆盖（放行与拒绝各有实例，拒绝依据引用了对话里真实的工具调用序列）。
- E2E-1~10 是开发期用临时脚本做的观察；其场景已沉淀为入库的 system test（九个场景，自带判定），上表最后
  一行是它的首次全量运行。模型输出不确定，单次通过不保证每次通过。
- 完成闸用例把 `TeamAgent._goal` 换成了 mock：验证的是处理器对闸的服从，不是端到端的 Leader 循环。
- 上述证据对应当前工作区。`NativeHarness._settle_round_done`、`TaskCompletionRail` 的 goal 回路、
  `AbilityManager` 的跳过机制或 `MemberActivityRegistry` 变化后需重验。

## 验证结论

- 已证明范围：goal 未完成时 harness 持续起轮并在上限处阻塞；排队输入先于 goal；`ready` 与 nudge；清除后
  不再起轮；外部输入重新打开 BLOCKED；认领立项；提交闸的放行、拒绝与无结论放行；任务离手时清除、
  打回后重新立项；Leader 立项不被后续输入覆盖；完成闸；配置解析与校验；`spawn_teammate(goal)` 门控与落库；
  rail 经 harness 挂载后能拿到宿主。
- 未覆盖或未通过的承诺：设计 §8.1 的"恢复"场景没有专门用例，也没有真实运行；Leader 自己的 goal 触顶
  （`team.goal.blocked` 标记）与"评估为完成但未提交"的提醒只有单测证据；真实运行全部是同一个模型、
  分钟级的小任务。
- 可支持的交付判断：机制按设计落地并有单测支撑；在 deepseek-flash 上，认领 / 调度 / 计划批准三个开工
  入口、续跑、提交闸的拒绝与放行、评审打回、改派、成员触顶上报、Leader 核对后结束团队，各有至少一次
  真实运行为证。长任务、更大的团队与其它模型上的评估质量仍无证据。


### 覆盖与资料同步

- 已盘点并理解：harness goal 子系统、NativeHarness supervisor、任务状态机的三个开工入口、团队完成判定、
  `team.idle` 去抖。
- 已同步：上述六份 spec；`agent_teams/AGENTS.md`（模块地图、rails 表）、`agent/AGENTS.md`、
  `harness/AGENTS.md`、`tools/AGENTS.md`。
- 未同步：面向用户的 `docs/` 与 `examples/` 没有 goal 模式的说明。

## 已知遗留

| 遗留项 | 未处理原因 / 性质 | 影响与现有处置 | 后续入口 / 条件及所需验证 |
| --- | --- | --- | --- |
| 真实模型验证的覆盖面 | 部分完成 | Leader goal 触顶（`team.goal.blocked`）、"评估为完成但未提交"的提醒、冷恢复、长任务、多模型未验证 | 在 `tests/system_tests/agent_swarm/goal/` 增补场景 |
| plan_mode 下已指派的任务无法 `submit_plan` | 既有问题，与本特性无关，未修复 | `TeamTaskManager` 只允许把无主的 PENDING 任务预留进 PLANNING，带 assignee 的任务报 `Failed to reserve task for planning`；E2E-7 中 Leader 靠取消并重建无主任务绕过 | 单独立项修复并补回归 |
| Leader goal 的轮次成本 | 已观察 | 需要 Leader 介入的场景里 goal round 出现 2~3 轮，单轮 10~75s（E2E-6 / 9） | 按真实负载决定是否调低 leader 默认 `max_attempts` 或改用 `agent_report` |
| 角色提示词与任务工具描述未提及 goal 模式 | 未实现 | 成员不知道提交会被评估，首次被拒时只能从拒绝文案理解；`submit_goal_report` 工具对成员可见但没有 team 语境的说明 | 给 `claim_task` / `member_complete_task` 描述加 capability 槽，或在成员 workflow 里加一段 |
| 冷恢复专门用例 | 未验证 | `GoalRecord` 与绑定键随 session 持久化、收尾对账会自动对齐，但没有用例守护 | 重建 harness 后断言 ACTIVE goal 继续驱动、任务已易主时被清除 |
| 开了 goal 的成员多出 `submit_goal_report` / `get_current_goal` 两个工具 | 有意取舍（复用 harness 回路的代价） | 工具列表变化影响该成员的提示词前缀缓存；仅在开启 goal 时发生 | 无需动作，除非要对成员隐藏 `submit_goal_report(complete)` |
| goal 续跑与 stale / stall nudge 可能同时催促 | 未验证 | goal 成员只有 BLOCKED 后才会 idle，届时 nudge 作为外部输入会重新打开 goal 并换得一次尝试 | 真实运行中观察是否形成"nudge → 一次尝试 → 再阻塞"的慢循环，必要时让 nudge 跳过 goal 已阻塞的任务 |
| Leader 在全员静止时连续起 goal round | 有意取舍，未在真实模型下观察 | 若 Leader 每轮都报告"继续"却不产生新工作，会一直跑到 `max_attempts`（默认 10）才阻塞；上限保证有界 | 真实运行中看 Leader 是否能在一两轮内给出完成报告或补出任务，必要时调低 leader 的默认上限 |
| 评审打回时 objective 不含 feedback | 有意取舍 | feedback 已在成员上下文里；goal round 的 `<goal_task>` 不重复它 | 若评估器因看不到 feedback 误判，再把 feedback 接进对账 |
| 三方 Harness 成员 / Leader、human、bridge 成员无 goal | 范围外 | `resolve_goal_spec` 对它们返回 None；`leader.goal` + `leader.cli_agent` 在 spec 校验期被拒 | 需要 `harness_protocol` 层的 goal 能力，另行设计 |
| 面向宿主的 goal 管理 facade（pause / resume / clear / 查询） | 范围外 | 宿主目前只能经 `team.goal.blocked` 标记得知阻塞，并用新输入恢复 | 有明确宿主需求时在 `Runner` facade 上设计 |

## 实施演进与修订

### 2026-11-10 未经认领开工的任务在同一轮内提交时绕过提交闸

- 原结论与适用范围：调度器开工 / 计划批准的任务由成员轮末对账立项，提交闸只看"任务是不是当前 ACTIVE goal"。
- 新证据与原因：真实模型 E2E-3（scheduled）中，成员收到交接后在一轮内做完并调用
  `member_complete_task`。轮末对账还没发生，任务没有 goal，闸直接放行。autonomous 不受影响，因为认领
  本身就在同一轮内立项。
- 最终变化：`TeamGoalRail.before_tool_call` 在判定前先调 `bind_task_goal`——提交的若是本人名下的进行中
  任务，就地立项再评估。`bind_task_goal` 相应改为"仍绑定着该任务即不重开"，避免对已评估完成、只差提交
  的任务再评一次。已实现。
- 旧结论的效力：轮末对账仍然有效，负责续跑、清除与冷恢复后的对齐；它不再是提交闸的前提。
- 影响与同步：`S_08` 的 Goal 模式段已更新；新增单测
  `test_hand_in_of_a_task_started_without_a_claim_is_still_assessed`。
- 验证：单测通过；真实模型 E2E-4（scheduled + 偷懒成员）首次提交被拒、补做后接受。

