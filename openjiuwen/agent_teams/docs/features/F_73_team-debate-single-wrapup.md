# Autonomous Team 思辨单次收束

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-15 |
| 范围 | `debate.py`、`rails/debate_round_cap_rail.py`、`rails/elements.py`、`agent/agent_configurator.py`、`agent/coordination/handlers/{agent_lifecycle,message,member}.py`、`runtime/manager.py`、autonomous teammate 的 `send_message` 参数、消息内部元数据链路、中英文提示词及定向单测 |
| 调度模式 | 仅 `autonomous`；`scheduled` 行为保持不变 |
| Refs | #751 |

## 背景

autonomous Team 的思辨场景此前同时存在两个互不协调的机制：

- `max_debate_rounds` 在成员侧限制互发消息，但成员达到上限时可能过早推动 Leader 收束；
- 每个成员向 Leader 汇报时都会按普通邮箱消息唤醒 Leader，多个成员先后汇报会触发多次模型调用，
  最终表现为 Leader 对同一轮讨论重复总结。

问题不在于思辨轮数不够，而在于缺少一个明确的团队级收束契约。Leader 应等待本轮实际邀请成功的
参与者都完成汇报或明确失败，再被框架唤醒一次；成员间的讨论上限仍应各自独立，不能由任一成员
达到上限就终止全组。

## 决策

### 1. 收束状态只保存在当前 Team 运行实例中

新增 `DebateRunState`，通过当前 Leader 的 `TeamBackend` / `TeamInfra` 在 rail 与 coordination
handler 之间共享。Leader 侧状态包括：

- 当前 `round_id`；
- 尚未完成的邀请 tool call；
- 实际邀请成功的参与者；
- 已明确失败的参与者；
- 按发送者去重的最终汇报；
- `finalizing` / `finalized` 状态及一把 `asyncio.Lock`。

teammate 只保存当前收到的 `round_id`，互发计数仍由各成员自己的 rail 实例维护。

这些状态不落数据库。rail 随 harness run cycle 重建时，teammate-local 状态和未 finalized 的
Leader 轮次会清空，避免复用中断的旧轮；同进程内已 finalized 的 Leader 状态则保留到下一条
外部用户输入，以阻止内部总结轮重新发起讨论。进程退出后全部自然丢失，本特性不尝试恢复尚未
结束的思辨。

### 2. Leader 只跟踪实际发出的邀请

Leader 在首个模型响应完成后检查 `send_message` 调用并登记本轮邀请：

- 只在 autonomous、无开放任务的思辨分支启用；
- LLM stability 跳过的调用不登记；
- 参与者集合来自已经完成的邀请调用结果，而不是模型原始参数；
- multicast 部分成功时，只加入 `data.delivered` 中实际收到邀请的成员；
- 同一成员被多个调用邀请时，任一调用成功即可成为参与者；
- Human、Bridge 和 external CLI 成员不进入自动收束等待集合，避免等待无法提供该内部协议的成员。

邀请工具全部 settled 后，预期参与者集合才固定。这样失败或未实际投递的邀请不会让 Leader
永久等待。

### 3. 使用隐藏消息元数据关联邀请与最终汇报

只有 autonomous teammate 的 `send_message` schema 增加可选布尔参数 `final_report`（默认
`false`）；Leader、scheduled teammate 与 human-agent 的 schema 不变。成员只有在当前存在已激活
的思辨轮、`to` 是名册中 Leader 的真实 `member_name` 字符串（`leader` 不作为角色别名，仅在它确实是该 `member_name` 时有效）且显式传 `final_report=true` 时，
rail 才通过私有 Python 参数创建内部元数据，并在消息入库时规范化为：

```python
{
    "kind": "team_debate",
    "round_id": "...",
    "message_role": "invite" | "final_report",
}
```

- Leader 发出的本轮邀请标记为 `invite`；
- teammate 收到邀请后激活本地 `round_id`；
- teammate 在该轮中显式以 `final_report=true` 发给 Leader 的最终汇报标记为 `final_report`；
- peer 消息和普通未标记消息沿用原邮箱行为。

模型不能直接伪造私有元数据对象；公开布尔参数只触发 rail 在上述边界内注入。工具调用成功后才
清除成员的 `round_id`，失败时保留，允许成员重试。持久化后的元数据字典只用于跨进程 mailbox
传递和校验。

### 4. `max_debate_rounds` 保持成员粒度

每个 teammate 独立统计成功发给其他参与者的 peer 消息：

- 一个成员达到上限，只阻止该成员继续 peer 互发；
- 达到上限继续返回既有工具反馈，不立即唤醒 Leader；
- 不新增全组计数或全局 `send_message` 并发限制；
- 并发 peer 调用允许在竞争窗口中轻微超过上限，不为此增加 reservation/CAS 层。

因此上限约束的是单个成员的讨论扩张，不是整个 Team 的统一停止条件。

### 5. 最终汇报先捕获，满足条件后统一唤醒 Leader

Leader mailbox 遇到匹配当前 `round_id` 的 `final_report` 时：

- 保存该发送者的第一份汇报；
- 标记消息已读，但不为单份汇报调用 `deliver_input()`；
- 同一发送者的重复汇报和已经收束后的迟到汇报不再次唤醒 Leader。

成员进入 `ERROR` / `FAILED` 时视为终态，从等待集合中移除；在本轮建立前已经失败的受邀成员也按
同样规则处理。第一个新的终态事件启动 300 秒收束宽限期，后续每个新的终态成员刷新宽限期；同一
成员的重复汇报不刷新。一个可取消的进程内 timer 负责到期触发；run cycle pause / stop 时取消
timer，start 后按原 monotonic deadline 恢复或立即结算。宽限期到期后，仍 pending 的成员记为
`unreported`，使用已有报告收束一次。mailbox poll 继续承担投递失败或 interrupt 清除后的重试。

teammate 发送 peer 消息前会读取当前 `round_id` 已持久化的 invite、最终汇报和成员失败状态，以实际
收到 invite 的成员为本轮参与者。明确目标已经终态时直接返回工具反馈；广播仅在本轮已经没有其他
active teammate 时拒绝。拒绝调用不投递，也不计入该成员自己的 peer cap。

### 6. 收束条件只允许触发一次

以下条件同时成立时，框架生成一次综合输入：

1. 所有邀请调用都已 settled；
2. 预期参与者只包含实际邀请成功且可跟踪的成员；
3. 每个预期参与者都已提交最终汇报、进入明确失败状态，或在收束宽限期到期后记为 `unreported`。

首个满足条件的检查者在锁内把状态翻转为 `finalizing`，随后通过
`Leader.deliver_input(..., use_steer=True)` 唤醒 Leader。wakeup 回调返回是否接受本次投递：接受后
标记 `finalized`；返回 `false`、投递抛错或取消时只释放 `finalizing` claim，由后续 mailbox poll
重试，避免一次瞬时失败或 pending interrupt 永久丢失收束。

生成给 Leader 的输入包含已收集的成员最终汇报和失败成员列表，并明确要求只向用户总结一次、
不要再次邀请成员。

### 7. pending interrupt 仍具有优先级

如果 Leader 在读取报告前已有待处理 interrupt，该消息保持未读，待 interrupt 结束后再捕获；若
interrupt 在报告已捕获、收束 wakeup 前出现，消息可以标记已读，但报告仍保存在进程内
`DebateRunState`，wakeup 返回 `false` 且不调用 `deliver_input()`，状态只撤销 `finalizing`、保持
`finalized=false`。两种情况都由后续 mailbox poll 再试。报告集合不是持久化恢复状态，进程退出后
仍遵循本文“已知遗留”的冷恢复边界。

### 8. 内部总结轮不能重新开启思辨

收束投递成功后，`finalized=true` 会阻止 `after_model_call()` 在内部总结轮重新 `begin_round()`。
只有下一条真正从团队外部投给 Leader 的输入才重置上一轮状态，包括 `USER_INPUT` / `GodViewMessage`、
Operator 广播和 Operator 直发 Leader；Operator 只定向其他成员时不重置。mailbox 消息、调度消息和框架
生成的收束 prompt 都不会重置。这样当前轮只总结一次，而下一条真实用户消息
仍可开始一轮新讨论。rail 随 harness/run cycle 重建时只保留已 finalized 的 Leader 状态；未完成
的旧轮会清理，新进程也自然创建空状态。

### 9. 提示词与框架契约对齐

autonomous 提示词只补充框架已经实现的职责边界：

- Leader 发起讨论后等待框架提供的团队级收束输入，并且只总结一次；
- teammate 在讨论充分后以 `final_report=true` 向 Leader 发送一份最终汇报并停止继续扩张；
- 中文和英文语义一致。

scheduled 提示词不修改。测试只保留中英文 Leader/teammate 的关键契约断言，不逐句锁定文案。

## 运行流程

```text
Leader 首次模型响应
  -> rail 登记候选邀请调用并创建 round_id
  -> send_message 注入隐藏 invite metadata
  -> 各邀请调用完成，按真实 delivery 更新 expected_participants

teammate 收到 invite
  -> 激活本地 round_id
  -> 与其他参与者互发，使用自己的 peer cap
  -> 已终态目标不可再接收本轮 peer 消息
  -> 以 final_report=true 向 Leader 发送一次最终汇报

Leader mailbox
  -> 捕获并按 sender 去重，不逐条唤醒 Leader
  -> 成员 ERROR/FAILED 记为终态
  -> 第一个终态启动 300 秒宽限期，新终态刷新
  -> 全部预期参与者终态或宽限期到期后，在锁内 claim 一次 finalization
  -> deliver_input() 一次
  -> Leader 面向用户综合总结一次
```

## 兼容性边界

- 普通未标记消息仍按原 mailbox 逻辑投递。
- 只有 autonomous teammate 的 `send_message` 增加可选 `final_report` 布尔参数；其余形态与返回结构不变。
- scheduled dispatch 不装配本特性。
- 300 秒只约束第一个终态出现后的收束宽限期，不限制此前的实际讨论时长。
- 不新增 debate 专用表或持久化 debate 状态；消息表只通过可空 `coordination_meta` 携带跨进程关联信息。
- 不增加全局流式输出闸门，也不改变所有 `send_message` variant 的并发属性。

## 拒绝的方案

- **任一成员达到 cap 就立即通知 Leader 收束**：把成员局部限制错误升级为全组终止，讨论速度快的
  成员会提前截断其他参与者。
- **每份最终汇报都走普通 `deliver_input()`**：会让 Leader 每收到一份就推理一次，直接造成重复
  总结，也是本特性要修复的核心问题。
- **等待固定 roster 的所有成员**：没有被邀请、邀请失败或不支持内部协议的成员会无意义地阻塞
  收束。等待集合必须来自实际 delivery。
- **新增 debate 表和数据库 CAS**：本轮状态只在一个 Team run cycle 内有意义，持久化会显著扩大
  生命周期、清理和恢复语义，且不能解决进程中断后的模型执行恢复。
- **从思辨启动时计算固定总时长**：会截断仍持续产生有效讨论的 Team；宽限期只从第一个终态开始。
- **全局限制 `send_message` 并发**：影响非思辨、scheduled 和普通消息链路，范围远超本问题。

## 验证

- debate round-cap focused cases：31 passed；coordination / runtime 的 debate 相关用例和生命周期：10 passed。
- 6 个相关测试文件合跑：353 passed、14 skipped；另有 6 个既有 dispatcher-routing 用例失败，
  名称与本特性修改前一致，不属于本次范围。
- `ruff`、`py_compile`、`git diff --check` 通过。

覆盖的关键行为包括：部分 multicast、重复/迟到汇报、预先失败和运行中失败、投递异常与
`CancelledError` 重试、pending interrupt gating、外部用户输入 reset、内部总结不重开、
Human/Bridge/external CLI 排除、scheduled 隔离、显式 `final_report` schema 隔离，以及 autonomous
中英文提示词的最小契约。

## 已知遗留

- 如果整轮没有任何成员进入终态，收束宽限期不会启动，仍由外层请求生命周期处理。
- 进程在思辨中途退出后不会恢复尚未完成的 `round_id` 和汇报集合；cold resume 会开始新的
  进程内状态。
- peer cap 是成功调用后的本地计数，并发调用可能在竞争窗口中多成功一次；本特性有意不引入
  reservation 层来换取严格上限。
