# Human-Agent Mailbox: Flip Auto-Read Off

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-17 |
| 范围 | `openjiuwen/agent_teams/tools/message_manager.py`、`openjiuwen/agent_teams/tools/team.py`、`openjiuwen/agent_teams/interaction/CLAUDE.md`、`openjiuwen/agent_teams/docs/specs/S_07_interaction-views-and-hitt.md`、`openjiuwen/agent_teams/docs/designs/architecture_cn.md`、`tests/unit_tests/agent_teams/test_hitt.py` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/`：1031 passed / 16 skipped；4 条 hitt 用例反转后仍 level0 |
| Refs | `#751` |

## 背景

排查"发给 human-agent 的消息没进入 avatar DeepAgent"时发现 send / receive 两侧
对 human-agent 邮箱的契约**互相矛盾**。

**发送侧（旧）**：`TeamMessageManager.send_message` / `broadcast_message`
对 `to_member_name in self._human_agent_names` 的目标在写入时自动置 `is_read=True`
（点对点）或写完即调 `mark_message_read`（广播）。注释明说：

> "HITT: human agents have no background consumer polling their mailboxes,
> so mark messages addressed to any of them as read on write."

**接收侧（新）**：自 `F_04_hitt-human-in-the-team` + `F_14_human-agent-team-event-rendering`
之后，human-agent 走与 teammate 一致的 spawn 路径——`spawn_member(role_type='human_agent')`
拉起的是**带 DeepAgent 的真实 runtime**，coordination `MessageHandler` 的
`on_poll_mailbox` → `_process_unread_messages` 同时为 teammate 与 human-agent 工作：
读 unread → role-aware 文本格式化（`hitt.msg_received_for_human` 前缀）→
`AgentRoundController.deliver_input` 把消息喂进 avatar 的 DeepAgent →
`mark_message_read`。整条链 role 中性，只在文案上分流。

两侧对 "human-agent 有没有 polling consumer" 的认知不一致：

- 发送侧停留在 F_04 之前的旧设计（avatar 没有真实 runtime，只是 SDK 回调出口），
  所以写入时直接标已读防止 dispatcher unread sweep 反复触发。
- 接收侧已经按"avatar 有真实 DeepAgent"重写，但发送侧那条短路把同一条消息
  抢在 poll handler 之前标已读，`_read_all_unread` 永远拿不到它，
  `deliver_input` 永远不被调用。

观察到的症状：leader 调 `send_message(to="human_agent", content="...")`
返回 success，但 avatar 这侧的 DeepAgent 完全没收到——既不进 LLM context，
也不在 stream 输出。SDK `on_inbound` 回调（out-of-band 通知通道）能拿到事件，
但那只是 best-effort 通知，不是 avatar 的输入通道。

## 数据结构 / 状态机

`TeamMessage.is_read` 字段在本次变更中行为没动——只是写入侧不再为
human-agent 做特殊置位：

- **before**：`recipient in _human_agent_names → is_read=True at write`
- **after**：所有 recipient（teammate / human-agent 一视同仁）`is_read=False at write`，
  由消费侧的 `MessageHandler._process_unread_messages` 在 `deliver_input` 完成后
  通过 `mark_message_read` 推进。

`TeamMessageManager._human_agent_names` 字段 + 构造参数随之删除——只剩
`auto_read` / broadcast mark-read 两处用，删完即孤立。

## 决策

**消除特殊情况而不是添加分支**（Linus "good taste"）。两侧角色分流逻辑同时
存在就是设计债——保留接收侧的统一路径（已有 role-aware 文案分流），
删掉发送侧的 role-aware 写入分流。

具体落地：

| 文件 | 改动 |
|---|---|
| `tools/message_manager.py` | 删 `human_agent_names` 构造参数 + `_human_agent_names` 字段；`send_message` 总是 `is_read=False`；`broadcast_message` 删 mark-read 循环；docstring 同步清理 |
| `tools/team.py` | `TeamMessageManager(...)` 构造去掉 `human_agent_names=` 实参 |
| `interaction/CLAUDE.md` 运行约束 #4 | 反转表述：消息保持 unread，由 poll 路径消费 |
| `docs/specs/S_07` 运行约束 #4 | 同上；元信息修订日期更新 |
| `docs/designs/architecture_cn.md` 运行约束 #4 | 同上 |
| `tests/unit_tests/agent_teams/test_hitt.py` | 反转 4 条用例：`test_direct_message_to_human_agent_stays_unread` / `test_broadcast_to_human_agent_stays_unread` / `test_direct_message_to_every_human_member_stays_unread` / `test_broadcast_to_every_human_member_stays_unread`，level0 保留 |

`_human_agent_names` 在 `TeamBackend` 上保留——它仍被 `backend.is_human_agent()` /
`backend.human_agent_names()` 等查询消费（rail 装配、`_format_message` role-aware
分流、HITT prompt section 等），与本次"发送侧标已读"的废弃路径无关。

## 拒绝的方案

1. **在 `MessageHandler._process_unread_messages` 跳过 human-agent**。
   保留发送侧自动标读，让接收侧不再尝试 deliver。否决理由：接收侧的
   `_format_message(is_human_agent=True)` + `hitt.msg_received_for_human` 模板、
   `prompts/sections.py::_hitt_section_human_agent_cn/en` 行为约束、SDK 文档
   都是基于"avatar DeepAgent 会收到消息"建模的；跳过接收 = 让上层一整套
   role-aware 装配变成死代码。S_07 运行约束 #6 也明说"团队事件...流向
   human-agent harness 时**直接**走 coordination 的 `deliver_input`（与 teammate
   路径同）"——保留接收侧是契约一致的方向。

2. **保留 `_human_agent_names` 字段但改语义**（例如改成 "需要额外 inbound 通知的
   成员名"）。否决理由：字段名 + 文档全部围绕 "auto-read" 设计，重新解释
   语义只会让下个读者继续误会。删干净比改名更便宜。

3. **新增配置开关**（`auto_read_for_human_agent: bool`）。否决理由：这是 bug fix
   不是 trade-off——auto_read=True 的语义就是错的，无人想要这条路径。开关只会
   把死代码长期保留。

4. **只改点对点，不改广播**。否决理由：两条路径来自同一份错误假设，半改更糟糕——
   "point-to-point 收得到，broadcast 收不到"对 LLM avatar 是更迷惑的状态。
   一起翻转一次性收敛。

## 验证

- **既有测试**：`tests/unit_tests/agent_teams/test_team_agent_coordination.py:272`
  (`test_mailbox_messages_deferred_while_interrupt_pending`)、:653
  (`_format_message(is_human_agent=True)`) 覆盖了 poll → deliver 路径的 role-aware
  分流，与本次变更协同生效。
- **反转的 4 条用例** 直接断言"send_message / broadcast → human-agent 后消息
  仍为 unread"，守住发送侧契约。
- 全量 `pytest tests/unit_tests/agent_teams/`：1031 passed / 16 skipped。

## 已知遗留

- HITT avatar 收到消息后由 prompt section（`_hitt_section_human_agent_cn/en`）约束
  "不应自主调 send_message / member_complete_task / claim_task"。这是 prompt 层
  约束，不在本次代码改动范围。若实测发现 LLM 越权，按 S_07 注 2 的弹药库（
  tool-level 静态护栏）追加，不要倒退到"发送侧自动标读"的旧方案。
- SDK 的 `on_inbound` 回调（`MessageHandler._notify_human_agent_inbound`）继续
  作为 out-of-band 通知通道存在，行为不变——它和本次变更的 avatar inbox
  路径正交。
