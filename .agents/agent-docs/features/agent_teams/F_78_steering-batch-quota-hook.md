# F_78 steering 消费配额钩子：每次 drain 取多少由 rail 现场决定

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-11 |
| 范围 | **core**：`single_agent/rail/base.py`（`SteeringDrainInputs` / `AgentCallbackEvent.BEFORE_STEERING_DRAIN` / `AgentRail.before_steering_drain` / `AgentCallbackContext.drain_steering(limit)`）、`single_agent/rail/__init__.py`（导出）、`single_agent/agents/react_agent.py`（`_drain_steering_batch`）；**harness**：`deep_agent.py`（`_BRIDGE_EVENTS` 加新事件）；**agent_teams**：`schema/blueprint.py`（`TeamAgentSpec.steer_batch_size` + 校验）、`rails/team_policy_rail.py`（`before_steering_drain`）、`rails/elements.py`（`TeamPolicyInput.steer_batch_size`）、`agent/agent_configurator.py`（RailSpec params 透传） |
| 测试基线 | `tests/unit_tests/` 14625 passed / 308 skipped / 3 xfailed；2 个失败（`test_execute_javascript_code_success` node 输出带 ANSI 颜色码、`test_shell_timeout`）在干净树上同样失败，与本次无关 |
| Refs | #984 |

## 背景

一个 teammate 忙着的时候，团队照样在往它信箱里投消息。`MessageHandler._process_unread_messages`
对每条未读消息各调一次 `deliver_input(text, use_steer=True)`——**N 条未读 = N 个队列条目**。

inner ReAct loop 每次模型调用之前把 steering 队列**掏空**：

```python
steering = ctx.drain_steering()          # while not queue.empty(): get_nowait()
if steering:
    await self._admit_user_message(ctx, context, steering, source="steering", prefix="[STEERING] ")
```

`_admit_user_message` 把整批 `"\n".join` 成**一条** user message。成员忙一阵子回过头来，
一次就被灌进十几条彼此独立的消息拼成的巨型 turn，模型输出异常。

这跟 [[F_71]] 处理的是同一个问题的两半。F_71 那半有个特殊性质可用：任务看板是**全量幂等
快照**，后一条完整包含前一条，所以整条剔除就完事了。信箱消息没有这个性质——每条各说各的，
一条都不能丢。**丢不掉，就只能少拿。**

## 决策

### D1 消费点开一个 rail 钩子，而不是加一个静态配额

第一版想的是「给 ctx 挂一个 `steering_batch_limit`，rail 在 `before_invoke` 设一次」。
**被否掉了**：那是一次 invoke 定死一个数，rail 没法按当时的队列深度、消息形态临场判断。

改成在消费点开事件。`AgentCallbackEvent.BEFORE_STEERING_DRAIN` 在**每次** drain 之前触发，
带 `SteeringDrainInputs(pending, limit)`：`pending` 是当前队列深度（只读，给 rail 当判据），
`limit` 由 rail 写。多个 rail 按 priority 串行，各自看到前一个留下的值，最后站着的那个生效
——与 `UserMessageInputs.parts` 的编辑契约同构，不引入「取最小值」之类的第二套规则。

**队列为空则整个事件不触发**：没什么可决定的，不必每轮唤醒一遍 rail 链。

### D2 在 drain **之前**决定，而不是全取之后回塞

另一条看起来更省事的路：保持全取，rail 在 `on_user_message` 里把 `parts` 截到 2 条，多余的
`ctx.push_steering()` 塞回队列。**被否掉了，因为它会乱序**：

```
drain_steering()          -> [m1,m2,m3,m4]   队列空
  await ctx.fire(...)                        ← 这里有真实 await
                             协调层投进 m5    队列=[m5]
  rail 回塞 m3,m4                            队列=[m5,m3,m4]
下一次 drain 拿到 m5 排在 m3 前面 ✗
```

`ON_USER_MESSAGE` 链里确实有 await（`TeamPolicyRail` 要读 session 判定待发团队状态），
协调层的邮箱 drain 跑在另一个 task 里，这个窗口是真实存在的。

在 drain 之前定配额，多余的消息**从没离开过队列**，FIFO 顺序天然成立。副产品是 rail 之间
不再有顺序耦合：配额在任何 rail 看到 `parts` 之前就生效，`_drop_superseded` 与
`insert(0, ...)` 拿到的已经是有界批次，不必去调 rail priority。

### D3 不新增续跑机制——已有的 `has_pending_steering()` 就是

`react_agent.py` 里本来就有：

```python
if not ai_message.tool_calls:
    if ctx.has_pending_steering():
        continue          # 队列非空就不结束 round，下一轮 iteration 继续 drain
```

这条原本是为「模型生成期间来了 steer」写的。有界 drain 之后它自动变成分批消费的驱动力：
拿走 2 条，剩下的还在队列里，`has_pending_steering()` 为真，loop 继续，下次模型调用再拿 2 条。
**一行新代码都不用加。** 这是选 D2 而不是 D1 静态配额之外的第二个理由——回塞方案同样能用上
它，但要多付一个乱序的代价。

### D4 不变量：队列非空时至少取 1 条

`limit=0` 会让 loop 空转——一条都不投递，但 `has_pending_steering()` 恒为真，一路 `continue`
到 `max_iterations` 耗尽。`drain_steering` 内部按 `max(1, limit)` 取。

这不是「容错」，是把「消费必须推进」写死在数据结构上：一个能让循环停在原地的配额不该是可表达的。
spec 侧另加 `steer_batch_size > 0` 校验，让 0 连配置层面都到不了——运行时会把 0 当 1，
而**悄悄当成 1** 比直接报错更难查。

### D5 生效范围 = 所有非 leader 成员，复用 `_drop_superseded` 那道门

`TeamPolicyRail.before_steering_drain` 的角色门就是 `role == LEADER → return`，与
`_drop_superseded` 逐字相同，理由也相同：**leader 读的是快照之间的差异**（哪个任务出现、
哪个动了）来决定重规划还是收尾，把序列切碎发给它，等于让它自己重新拼。

teammate / human_agent avatar / bridge avatar / worker 一视同仁。给「仅 TEAMMATE」开一道
第二种角色门是纯粹的特殊情况——同一个 rail 里两个门两套判据，下一个人读代码时必须去查为什么。

### D6 配额进 `TeamAgentSpec`，默认 2

照 `stale_claim_idle_timeout` / `default_max_review_rounds` 的既有惯例加
`steer_batch_size: int = 2`，经 `RailSpec.params` 下发。写死常量的话，换个上下文容忍度不同
的模型就得改代码发版；本模块也明确要求「新增配置项一律走 `TeamAgentSpec`」。

### D7 新事件必须进 `_BRIDGE_EVENTS`

`DeepAgent` 有两个 callback-manager 命名空间（自己的 + 内层 `ReActAgent` 的），
`_register_rail_selective` 按事件把 rail 的每个 callback 路由到其中一个，**路由错了是静默的**
——callback 直接不跑。本事件由内层 ReAct loop 触发，所以和 `ON_USER_MESSAGE` /
`BEFORE_MODEL_CALL` 一样进 `_BRIDGE_EVENTS`。

漏了这一步的后果不是报错，是 `TeamPolicyRail.before_steering_drain` 永远不被调用、限流悄悄
失效。`tests/unit_tests/harness/test_deep_agent_rail_event_routing.py::test_every_event_has_a_routing_decision`
就是为此存在的——本次实现确实先漏了，靠它抓出来的。**加 `AgentCallbackEvent` 成员 = 必须同时
做一次路由决策。**

## 数据流

```
MessageHandler._process_unread_messages
  └─ 每条未读消息 deliver_input(text, use_steer=True)
       └─ harness.send(immediate=True) → LoopQueues.steering.put_nowait   [m1..m5]

ReActAgent inner loop，每次模型调用之前
  └─ _drain_steering_batch(ctx)
       ├─ 队列空 → 直接返回 []，不触发事件
       ├─ ctx.inputs = SteeringDrainInputs(pending=5)
       ├─ fire(BEFORE_STEERING_DRAIN)
       │    └─ TeamPolicyRail：非 leader → inputs.limit = 2
       ├─ 恢复 ctx.inputs（借用，不覆盖外层 invoke 的 inputs）
       └─ ctx.drain_steering(2) → [m1,m2]，队列留 [m3,m4,m5]
  └─ _admit_user_message(..., source="steering", prefix="[STEERING] ")
       └─ fire(ON_USER_MESSAGE) → _drop_superseded / 团队状态 insert(0)
       └─ "\n".join → 一条 UserMessage
  └─ 模型调用
  └─ 无 tool_calls 且 has_pending_steering() → continue，下一轮拿 [m3,m4]
```

## 拒绝的方案

| 方案 | 否掉的理由 |
|---|---|
| 全取后把多余的 `push_steering` 回塞队尾 | D2：`ON_USER_MESSAGE` 链里有真实 await，并发投进来的新消息会排到回塞消息前面，乱序 |
| ctx 上挂静态 `steering_batch_limit`，`before_invoke` 设一次 | D1：一次 invoke 定死一个数，rail 无法按当时队列深度临场判断 |
| 包一个假 `empty()` 的 Queue 子类骗过 drain 循环 | `has_pending_steering()` 读的是同一个 `empty()`，会让 round 带着未消费的消息结束 |
| `limit` 做成 core 的 `ReActAgentConfig` 字段 | 这是 team 的策略，不是 core 的默认行为；core 只该提供中立能力 |
| 顺手给 follow-up 批次也限流 | 范围外，且性质不同：那条队列里主要是全量快照看板，F_71 的整条剔除已经处理了它 |

## 验证

- `tests/unit_tests/core/single_agent/rail/test_before_steering_drain.py`（新增）：事件映射、
  hook opt-in 注册、rail 截断后剩余留队列且 `has_pending_steering()` 为真、连续 drain 按序
  走完 backlog、`pending` 反映队列深度、空队列不触发事件、无 rail 意见时全取、`ctx.inputs` 借用后归还。
- `tests/unit_tests/core/single_agent/rail/test_ctx_steering.py`（扩充）：`limit` 的 FIFO
  截断 / 超出 backlog / `None` 全取 / `limit<=0` 仍取 1 条 / 空队列。
- `tests/unit_tests/agent_teams/test_team_policy_rail.py`（扩充 `TestTeamPolicyRailSteeringQuota`）：
  四个非 leader 角色都拿到配额、leader 保持 `None`、配额跟随 `steer_batch_size`、
  配额与队列深度无关、无 inputs 时不炸。
- `tests/unit_tests/agent_teams/test_dispatch_choice.py`（扩充）：`steer_batch_size` 默认值 +
  `<= 0` 拒绝。
- `tests/unit_tests/harness/test_deep_agent_rail_event_routing.py`（扩充）：新事件进
  `_BRIDGE_EVENTS`（D7）。

## 同步的文档

- `docs/specs/S_09`：新增不变量 29（输入批次配额，两条队列一剔一限）/ 30（新事件必须做路由决策）。
  顺带修掉一处与代码不符的旧描述——`FirstIterationGate` 已随单 supervisor 模型删除，spec 里
  仍把它列为团队 rail 之一并带两条不变量；**编号 19 / 20 留空不复用**，避免打乱其它文档对
  21-28 的引用。
- `docs/specs/S_12`：新增 `steer_batch_size` 的 spec 字段与校验说明（无新表 / 无新列）。
- `agent_teams/AGENTS.md`：`TeamPolicyRail` 行补第二件"喂多少"的职责。
- `agent_teams/harness/AGENTS.md`：「输入队列」段原文写「drain 整批」，已不准确，改为按 rail
  配额取、默认全取。
- 零回归判据：不设 `limit` 的路径（单 agent / TinyAgent / leader）行为逐字不变，既有用例全绿
  且无需修改。

## 已知遗留

- **follow-up 批次不限流**。`NativeHarness._drain_pending_follow_ups` 仍整批驱动一轮。当前
  那条队列里主要是任务看板（`use_steer=False`，见 `handlers/AGENTS.md` 维度 3），而看板是全量
  快照、已由 F_71 的整条剔除折叠成一条，所以过载风险低。若将来有大量非快照输入走 follow-up，
  同一个钩子形态可以照搬。
- **配额消耗 iteration 预算**。10 条排队 + 配额 2 时，成员至少要 5 次模型调用才能读完；
  `max_iterations` 耗尽时剩余消息**不丢**（留在队列里，下一个 round 继续消费），但那一轮的
  推理深度被挤占。默认 2 是在「turn 不过载」与「别把预算全花在读信」之间取的折中。
- **`LoopQueues.drain_steering` 未同步加 `limit`**。它生产代码零调用（只有测试引用），本次
  不动——真正生效的消费点是 `AgentCallbackContext.drain_steering`。
