# Autonomous Team 思辨单次收束

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-15 |
| 范围 | `debate.py`、`rails/debate_round_cap_rail.py`、`rails/elements.py`、`agent/agent_configurator.py`、`agent/coordination/handlers/{message,member}.py`、消息内部元数据链路、autonomous Leader/teammate 中英文提示词及定向单测 |
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

这些状态不落数据库。每次 harness run cycle 重建时清空，避免 cold resume 复用已经失效的进程内
轮次；本特性不尝试恢复进程中断时尚未结束的思辨。

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

模型可见的 `send_message` schema 不变。框架通过私有 Python 参数创建内部元数据，并在消息入库时
规范化为：

```python
{
    "kind": "team_debate",
    "round_id": "...",
    "message_role": "invite" | "final_report",
}
```

- Leader 发出的本轮邀请标记为 `invite`；
- teammate 收到邀请后激活本地 `round_id`；
- teammate 在该轮中发给 Leader 的最终汇报标记为 `final_report`；
- peer 消息和普通未标记消息沿用原邮箱行为。

私有元数据对象不能由模型 tool arguments 伪造；持久化后的字典只用于跨进程 mailbox 传递和校验。

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
同样规则处理。静默但未失败的成员继续保持 pending，最终由上层既有 watchdog 处理，而不是在
agent-core 内再增加一套思辨超时。

### 6. 收束条件只允许触发一次

以下条件同时成立时，框架生成一次综合输入：

1. 所有邀请调用都已 settled；
2. 预期参与者只包含实际邀请成功且可跟踪的成员；
3. 每个预期参与者都已提交最终汇报或进入明确失败状态。

首个满足条件的检查者在锁内把状态翻转为 `finalizing`，随后通过
`Leader.deliver_input(..., use_steer=True)` 唤醒 Leader。投递成功后标记 `finalized`；投递抛错或
取消时释放 claim，由后续 mailbox poll 重试，避免一次瞬时失败永久丢失收束。

生成给 Leader 的输入包含已收集的成员最终汇报和失败成员列表，并明确要求只向用户总结一次、
不要再次邀请成员。

### 7. pending interrupt 仍具有优先级

如果 Leader 当前已有待处理 interrupt，最终汇报仍可被捕获，但不会越过既有 interrupt gating
立即投递新的收束输入。后续正常 mailbox poll 再尝试完成收束，保持 coordination 层原有的输入
优先级和顺序。

### 8. 提示词与框架契约对齐

autonomous 提示词只补充框架已经实现的职责边界：

- Leader 发起讨论后等待框架提供的团队级收束输入，并且只总结一次；
- teammate 在讨论充分后向 Leader 发送一份最终汇报并停止继续扩张；
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
  -> 向 Leader 发送一次带 final_report metadata 的最终汇报

Leader mailbox
  -> 捕获并按 sender 去重，不逐条唤醒 Leader
  -> 成员 ERROR/FAILED 记为终态
  -> 全部预期参与者终态后，在锁内 claim 一次 finalization
  -> deliver_input() 一次
  -> Leader 面向用户综合总结一次
```

## 兼容性边界

- 普通未标记消息仍按原 mailbox 逻辑投递。
- `send_message` 的模型可见参数和返回结构不因收束协议改变。
- scheduled dispatch 不装配本特性。
- Relay 的 300 秒 watchdog 保持唯一外部超时边界。
- 不新增数据库表、DAO、数据迁移或持久化 debate 状态。
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
- **增加思辨专用超时**：与 Relay 已有 watchdog 重复，形成两套竞态边界。
- **全局限制 `send_message` 并发**：影响非思辨、scheduled 和普通消息链路，范围远超本问题。

## 验证

- focused convergence cases：26 passed。
- `tests/unit_tests/agent_teams/test_team_policy_rail.py`：72 passed。
- `tests/unit_tests/agent_teams/test_team_agent_coordination.py`：106 passed，6 个既有 dispatcher-routing
  用例失败；在本特性修改前同样失败，不属于本次范围。
- `ruff`、`py_compile`、`git diff --check` 通过。

覆盖的关键行为包括：部分 multicast、重复/迟到汇报、预先失败和运行中失败、投递异常与
`CancelledError` 重试、pending interrupt gating、run-cycle reset、Human/Bridge/external CLI 排除、
scheduled 隔离，以及 autonomous 中英文提示词的最小契约。

## 已知遗留

- 静默参与者不会被 agent-core 主动剔除，仍依赖 Relay watchdog 终止整次请求。
- 进程在思辨中途退出后不会恢复尚未完成的 `round_id` 和汇报集合；cold resume 会开始新的
  进程内状态。
- peer cap 是成功调用后的本地计数，并发调用可能在竞争窗口中多成功一次；本特性有意不引入
  reservation 层来换取严格上限。
