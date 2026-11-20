---
title: Swarmflow fork 轮数边界被运行时附件消息整体前移一轮
author: chenchunzhou
author_email: chenchunzhou1@huawei.com
id: B_20261117_1240_swarmflow-fork-round-boundary-attachment
type: B
modules:
- openjiuwen/agent_teams
files:
- openjiuwen/agent_teams/workflow/backends/avatar_session_backend.py
- tests/unit_tests/agent_teams/workflow/test_avatar_session_backend.py
designs:
- SDD-0014
design_note: SDD-0014 §9 风险 3 已预警"框架注入的额外 user 消息会使轮数偏移"；本次为该风险在产品栈落地后的修复，不改设计方向与对外契约。
---

# Swarmflow fork 轮数边界被运行时附件消息整体前移一轮

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-11-17 |
| 严重程度 | 高：`keep_rounds` 语义在产品栈上全面失真，`after` 小 N 静默继承全量（上下文隔离失效且形似成功） |
| 范围 | `openjiuwen/agent_teams/workflow/backends/avatar_session_backend.py` 的轮数换算；`before` / `after` / 两种 compact 四模式、live 与 persisted 两条捕获路径 |
| 关联 issue | #6450 |
| 关联设计 | SDD-0014（swarmflow-session-fork），§4.5 轮数→消息索引换算、§9 风险 3 |
| 关联 spec | `S_18_swarmflow-engine-and-worker.md`（会话 / fork 契约） |
| 问题日志 | jiuwenswarm 侧 2026-11-11 运行日志（session `web_1a1c9b2e8ce_0a51904ae6b4` / `web_1a1c9baa836_5af4b0429958`） |

## 现象

脚本按 SDD-0014 文档语义调用 fork，子会话继承的轮次与契约不符：

| 组 | 实测保留轮次 | 契约应保留 | 判定 |
|---|---|---|---|
| `full` | r1 r2 r3 | 全量 | 正常 |
| `before/1` | 空集（仅附件） | r1 | 少一轮 |
| `before/2` | r1 | r1 r2 | 少一轮 |
| `before/3` | r1 r2 | r1 r2 r3 | 少一轮 |
| `after/1` | 全量（未切） | r1 r2 r3 | 边界早一轮 |
| `after/2` | 全量（未切） | r2 r3 | 边界早一轮 |
| `after/3` | r2 r3 | r3 | 边界早一轮 |
| `before/5`、`after/5`（越界） | 全量 | 越界降级全量 | 正常 |

典型可见后果：两轮父会话 `fork(before, keep_rounds=1)` 的子会话答"这是我们这轮会话的第一条消息，之前没有任何对话内容……我只能看到系统注入的运行时信息"；三轮父会话 `fork(after, keep_rounds=2)` 的子会话答出全部三个变量。`before/N` 与 `after/N` 不互补、在同一轮上重叠。

## 根因

父会话 avatar 的原生上下文头部有一条**框架注入的 `UserMessage`**——运行时 prompt attachment 快照，而轮数换算按消息类型计数，把它当成了第 1 轮。

1. 产品栈 jiuwenswarm `RuntimePromptRail._refresh_dynamic_attachments` 把运行时状态（当前模型 / 可用模型 / 模式 / 语言 / 渠道）写成 `runtime.setting` 附件。
2. `openjiuwen/harness/prompts/prompt_attachment_manager.py:573` `sync_to_context` 以 **`UserMessage`**（metadata 带 `_openjiuwen_prompt_attachment_history`）落盘进会话上下文；调用点 `react_agent._admit_user_message` 先同步附件、再写本轮 user 消息，故快照恒在第一轮真实提问**之前**。
3. `avatar_session_backend._round_boundary_index` 原实现按 `isinstance(m, UserMessage)` 计数轮次，快照被计入 → `before` 系与 `after` 系边界各自前移一轮。
4. `openjiuwen/agent_teams/fork.py` `ForkContext.from_agent` 仅剥离 `SystemMessage`，快照随保留窗口原样进入子会话（故 `before/1` 的子会话只拿到这条快照）。

同类未被标记的框架注入还有两处会造成同样偏移：`react_agent` 的空回复重试通知（`source_kind="empty_response"`）与迭代上限通知（`source_kind="iteration_limit"`）。

SDD-0014 §9 风险 3 原文已预警该风险，并要求"实现时确认 swarmflow 会话上下文只含 send 的 user 消息"；引擎层 `_agent_turn` 确实只注入 send prompt，但 harness 层的附件机制未被纳入确认范围，风险落地。

## 证据

- 附件先于首轮落盘：jiuwenswarm `logs/run/jiuwen.log` 2026-11-11 16:22:38 `[PromptAttachmentManager] persisted prompt attachment snapshot: session_id=…wf-a61b4659112c-sess-c-cut-0`。
- 子会话只多继承一条消息：同日志父会话 round2 请求 `msg_count=5`，fork 子会话首轮请求 `msg_count=4`（全新会话基线为 3）。
- 九分支对照矩阵（探针 X=8213 / Y=彗星 / Z=琥珀，父轮回复均为纯 `OK`）的九个子会话 `msg_count` = {4,6,7,8,9,10×4}，与"1 条附件 + 3 轮×2 条"按各模式切片的预测逐一相符。
- 子会话回答原文见 `logs/electron-web.log` 的 `workflow.updated` 事件 `outcome` 字段。

## 修复

`avatar_session_backend.py` 新增 `_is_round_opening_message` / `_has_round_markers` / `_message_metadata`，由 `_round_boundary_index` 的计数循环与 `before` 系前向扫描共同使用。判定采用**白名单为主、黑名单兜底**的双读法：

- 上下文中存在任一**带标记的轮次开口消息**时，只有这类 `UserMessage` 算一轮，其余一律视为框架状态。"轮次开口"需同时满足两个条件：`external_user` origin，**且** `source_kind` 属于 `{None, "query"}`。
- 上下文中完全没有这类消息时（engine 层 `MockBackend` 上下文，或压缩器把摘要重建成裸消息的已压缩上下文），回退到原先的裸消息读法，并显式排除 `_openjiuwen_prompt_attachment_history` 与仅带 `source_kind` 的注入。
- 附件快照无论哪种读法都被排除。

**为何 origin 不足以判定轮次（检视发现，2026-11-17）**：`_admit_user_message`（`react_agent.py:949`）是所有消费输入的唯一入口，它对**每一种**输入都无条件打同一个 `external_user` origin（`react_agent.py:993-996`），包括三类：`source="query"`（真正的 `send()` 轮次）、`source="steering"`（轮内注入的团队信箱消息，带 `[STEERING] ` 前缀）、`source="resume"`（workflow interrupt 恢复，属同一轮续跑）。只按 origin 判定会把 steering 与 resume 也计为一轮，使边界在**轮中**切开——子会话拿到一条没有 assistant 回复的 user 消息，以及可能的孤儿 ToolMessage。实测：上下文形如 `[快照, q1, asst, tool, [STEERING], asst, q2, asst]` 时，`before/1` 的边界从正确的 6 被挤到 4。故在白名单分支追加 `source_kind` 过滤。`_has_round_markers` 同步使用同一判定——否则一个只含 steering 标记的上下文会被误判为"有标记"，导致一轮都数不到、静默降级全量。

就 swarmflow avatar 当前的可达性而言：`steering` 需 `send(immediate=True)` 或 PAUSED 态 warm resume 推入（`native_harness.py:910` / `:944`），而 avatar 每轮固定 `immediate=False`（`avatar_session_backend.py:1356`）；`resume` 需 workflow interrupt，而 avatar 的 HITL 中断被 `_raise_on_interrupt_or_fail` 直接拒绝。两条路径当前不可达，但它们是 `react_agent` 侧既有的生产者契约、与 fork 实现无耦合，任何一端放开即生效，故按契约而非按当前可达性收紧。

**为何不只用白名单**：上下文压缩器（`context_engine/processor/forked/compressor/` 下 8 处）在压缩时新建**无 metadata** 的 `UserMessage` 承载摘要，真实轮次的标记会随压缩丢失。纯白名单在压缩后会数不到任何轮次 → 一律返回 `None` → 静默降级为全量 fork；这比 off-by-one 更危险，因为上下文隔离失效且外观与成功无异。故保留裸消息读法作为兜底。

**为何不改注入侧**：附件以 `UserMessage` 落盘是 context engine 既有机制，承载快照/delta 状态追踪、DashScope 路由的 system 投影与恢复，改其消息类型影响面远大于本缺陷。

该函数是 live 捕获（`_fork_data_from_native`）与 persisted 恢复（`_fork_ctx_from_messages`）的共享换算点，单处修复即覆盖四种截断/压缩模式与两条路径；`full` 模式不经边界计算，行为不变。

## 验证

| 项 | 结果 |
|---|---|
| 新增回归用例（边界 5 个 + 端到端 9 个） | 修复前 12 个失败、修复后全通过；含 steering / resume 轮内注入与"仅含 steering 标记"回退两组 |
| `tests/unit_tests/agent_teams/workflow/test_avatar_session_backend.py` | 58 passed |
| `tests/unit_tests/agent_teams/workflow/` 全量 | 498 passed，无回归 |
| 现场场景重放（query1 两轮 before/1、before/2；query2 三轮 after/2；矩阵越界组） | 全部符合契约 |

**用例区分能力已校验**：把 `_round_boundary_index` 临时退回修复前的裸 `UserMessage` 计数后，12 个用例失败。其中 `test_fork_before_and_after_partition_at_the_named_round` 原先写成"`before/N` 与 `after/N+1` 无重叠且拼接完整"，经检视指出该断言对本缺陷**恒真**（两者按构造解析到同一下标，任何一致偏移的切点都满足互补），实测确认修复前也通过；已改为绑定真实轮次的绝对断言（各侧探针逐项相等 + head 消息条数固定为 `1 + 2N`），改写后该用例在修复前确实失败。

越界语义同步回正：3 轮父会话现在 `keep_rounds=4` 才触发 out-of-range 告警与全量回退（修复前因附件占一个名额需传 5）。

## 遗留

- 父会话运行时状态仍会随保留窗口进入子会话（`ForkContext.from_agent` 只剥 `SystemMessage`）。若要求子会话完全不继承父的运行时附件，需在 `_fork_data_from_native` / `_fork_ctx_from_messages` 两个捕获入口剥离——注意不能只改 `ForkContext.from_agent`，边界索引在其调用之前已算出。该项改动面更大且影响 `full` 模式契约，未纳入本次修复。
- 已压缩上下文上的 fork 仍走裸消息读法，若压缩摘要本身是 `UserMessage`，其轮数计数仍可能偏移。彻底解决需压缩器保留原消息 origin metadata，属独立改动。
- 运行中若追加附件 delta（例如中途切换模型），本次修复已使其不计入轮数；此前该场景会使偏移量随 delta 数量累积。
