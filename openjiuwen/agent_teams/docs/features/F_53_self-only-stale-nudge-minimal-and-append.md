# Self-Only Stale-Task Nudge：收敛渲染、精简载荷、接续投递

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-07-07 |
| 范围 | `openjiuwen/agent_teams/agent/coordination/handlers/stale_task.py`、`.../handlers/member.py`、`.../dispatcher.py`、`openjiuwen/agent_teams/i18n.py` |
| 测试基线 | `uv run python -m pytest tests/unit_tests/agent_teams/test_team_agent_coordination.py`（75 passed） |
| Refs | #751 |

## 背景

「催促任务」（stale-task nudge）此前有四条投递路径，彼此渲染 / 投递方式不统一：

| 路径 | 触发 | 渲染 | 投递 |
|---|---|---|---|
| A 成员自催 | `StaleTaskHandler._self_nudge_stale_claim` | `render_event(kind="stale-claim")` | `deliver_input`（**steer**）|
| B leader 跨进程催成员 | `StaleTaskHandler._leader_nudge_stale_claim` | 扁平模板经 `send_message` → 对方渲成 `<team-inbound>` | 对方 mailbox（steer）|
| C leader 在成员转 READY/ERROR 时批量催 | `MemberHandler._nudge_idle_member_with_stale_claims` | **手搓 `header + 「- [id] title: content (time)」bullet」`**，完全不走 `render_event` | 对方 mailbox（steer）|
| D leader 自催 pending | `StaleTaskHandler._check_stale_pending_tasks` | `render_event(kind="stale-pending")` | `deliver_input`（steer）|

三个痛点：

1. **渲染不统一**：C 路径手搓 bullet，B 路径发扁平模板；只有 A / D 走 `render_event`。
2. **一次性注入太多**：B / C / D 把整段 `task.content` 内联进催促，批量任务时更甚——成员被塞了一大坨本可以按需 `view_task` 自查的详情。
3. **不该 steer**：催促的语义是「你正在做的这件事继续推进」，用 steer 打断正在做这件事的那一轮本身自相矛盾。

## 决策

**把 stale-claim 催促收敛为 self-only，砍掉 leader 跨进程催（B、C）。**

- `StaleTaskHandler._check_stale_claimed_tasks` 只扫**本成员自己认领**的任务（`tk.assignee == own_name`），去掉 `is_leader` 的「额外扫所有成员」分支。sweep 出的 stale task 一律走 `_self_nudge_stale_claim`。
- 删除 `StaleTaskHandler._leader_nudge_stale_claim`（路径 B）、`StaleTaskHandler._nudge_stale_claim` 的 self/leader 分流（现在恒 self）。
- 删除 `MemberHandler._nudge_idle_member_with_stale_claims`（路径 C）及其在 `_handle_leader_member_event` 的调用；MemberHandler 不再碰 stale-claim。

一步同时消掉三个痛点：催促永远是 self → 永远走 `<team-event>`（渲染统一）、永远能非 steer、也不必再跨 handler 共享 throttle。

**精简载荷：只传 task_id + title。** i18n `dispatcher.stale_claim_self` 去掉 `{content}`，改为指向 `view_task`（对齐已有的 `task_assigned_to_self` 风格）+ 完成提醒。stale-pending 每行从 `- [id] title: content (time)` 降为 `- [id] title (time)`，头部补一句「如需回顾详情用 view_task」。

**接续投递：非 steer。** A、D 两条 self-delivery 改为 `deliver_input(..., use_steer=False)`——running 轮次把催促当 follow-up 追加，不打断。

**throttle 私有化。** 原本 `EventDispatcher.__init__` 建一个 `stale_claim_throttle` dict 传给 MemberHandler + StaleTaskHandler 共享（跨「status-change 路径」「poll 路径」去重）。B、C 移除后只剩 poll 一条路径，`_last_stale_nudge` 收回 `StaleTaskHandler` 私有，`MemberHandler` 构造不再接该参数。

## 拒绝的方案

- **保留 B、C，但也统一 body + minimal**：能解决痛点 2，但 B、C 走 `send_message` 跨进程，接收者恒渲成 `<team-inbound>`、恒 steer（受统一 mailbox 投递约束），痛点 1（渲染统一成 `<team-event>`）与痛点 3（非 steer）在这两条上结构性做不到。除非动整个 inbound message 投递——越界且波及所有普通消息。
- **让 leader 跨进程投递预渲染的 `<team-event>`**：接收者 `_format_message` 会再包一层 `<team-inbound>`，标签嵌套。放弃。
- **保留 leader 兜底催（成员进程卡死时）**：成员进程真卡死时，催了也进不了它的 loop，兜底无实际价值；为一个无效兜底维持两条冗余路径 + 跨 handler 共享状态不划算。

## 验证

- `test_team_agent_coordination.py` 全绿（75 passed）。
- 新增 / 改写用例：`test_stale_claim_leader_ignores_other_members_claim`（leader 不再催他人）、`test_stale_claim_leader_self_nudges_own_claim`（leader 自己的 claim 仍自催、body 无 content、`use_steer=False`）、`test_stale_claim_self_nudge_appends_when_running`（非 steer）、`test_stale_pending_leader_appends_when_running`（非 steer）；`test_member_status_change_does_not_nudge_about_stale_claims` 取代原四条 path-C 用例。

## 已知遗留

- stale-pending 仍是 leader 单点自催；若未来要让 leader 之外的角色参与 pending 分派，需要另设机制。
- `_STALE_CLAIM_SECONDS` / `_STALE_PENDING_SECONDS` 仍硬编码 10min，未参数化到 spec。
