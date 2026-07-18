# 运行时 idle 计时驱动的停滞唤醒（autonomous）

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-07-16 |
| 范围 | `openjiuwen/agent_teams/agent/state.py`、`.../agent/stream_controller.py`、`.../agent/team_agent.py`、`.../agent/coordination/kernel.py`、`.../agent/coordination/dispatcher.py`、`.../agent/coordination/handlers/stale_task.py`、`openjiuwen/agent_teams/i18n.py`、`openjiuwen/agent_teams/schema/blueprint.py`、`openjiuwen/agent_teams/tools/locales/descs/{cn,en}/view_task.md` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/`（1939 passed / 1 failed，见「验证」——该 failed 为 HEAD 既有，与本特性无关） |
| Refs | #751 |

## 背景

leader 反复调 `list_members` / `view_task` 轮询成员与任务状态，是纯粹的 token 浪费——状态变更本就会推给它。`list_members` 的描述早已写死「禁止反复调用轮询成员状态」，但 `view_task` 没有对称的抑制语，反而在「使用场景」里鼓励「查看整体进度」「核实任务状态」，与 `leader_policy.md:47`「事件驱动，不轮询」的基调冲突。

抑制轮询的前提是**该推的真的会推**。有两种停滞此前没有任何人主动告知 leader：

- **场景 A**：成员领了活跃任务却长时间 idle——领了活没在推进。
- **场景 B**：任务长时间无人认领，同时又有成员空闲——有活没人干。

`StaleTaskHandler` 已有两个雏形（`_check_stale_claimed_tasks` 自催 / `_check_stale_pending_tasks` leader 自催 pending），但**计时全部读 DB `task.updated_at`**，这在 pause/resume 下从根上是错的。

### 核心洞察：`updated_at` 不是「过了多久」，是「上次写库是什么时候」

团队 pause 期间墙钟照走，`task.updated_at` 不动。pause 两小时再 resume，`now - updated_at` 得到「停滞 2 小时」——而成员其实一秒都没耽误，只是被暂停了。用一个**持久化的绝对时间戳**去度量**运行时的停滞时长**，是拿错了数据源：停滞是运行态的属性，不是数据行的属性。

`TeamMember` 也帮不上忙：状态列不落时间戳（`models.py` 明确注释 status 更新不 bump `updated_at`），所以「某成员 READY 了多久」在 DB 层根本无从查起。

结论：停滞时长必须由**每个成员在自己进程的内存里**记录，从它进入 idle 的那一刻起算。

## 数据结构

```python
# agent/state.py — TeamAgentState（跨 operator 可变状态象限）
idle_since: Optional[float] = None   # time.monotonic()，进程本地、不持久化
```

一条数据，三个触点：

| 角色 | 位置 | 行为 |
|---|---|---|
| 写 | `StreamController._map_state` | `IDLE → READY` 时 `= time.monotonic()`；`RUNNING → BUSY` 时 `= None` |
| 读 | `TeamAgent.idle_seconds()`（经 `AgentRoundController` protocol） | `None` 或 `monotonic() - idle_since` |
| 重置 | `TeamAgent.refresh_idle_baseline()`（`kernel.start` 调） | 仍 idle 的成员重新起算，pause 窗口不计入 |

选 `monotonic` 而非墙钟：它只用于算时长，且不受系统时钟调整影响。

**`idle_seconds() is None` 即「忙」**，这让「正在干活的成员被当成停滞」在类型层面不可表达——旧的 `updated_at` 路径做不到这点，它连成员在不在干活都不看。

## 决策

1. **autonomous 的两个 sweep 全部改用 idle 计时**，不与旧路径并存（并存必然双重 nudge）。
2. **场景 A：先自催，连续 3 个窗口无效才升级 leader**。成员自己才能推进自己的任务，leader 替不了它干活，所以第一层补救是喂成员自己的 loop（沿用 F_53 的 self-only 投递，只换计时基准）。连续 `_STALE_CLAIM_ESCALATE_STREAK = 3` 个窗口仍停滞，说明自催无效，此时由**停滞成员自己发消息上报 leader**（`send_message`），leader 据此问询 / 改派 / 换人。方向是成员→leader，与 F_53 砍掉的 leader→成员跨进程催**方向相反**，不违反其设计。
3. **场景 A 排除 `IN_REVIEW`**：author 在等 reviewer 裁决时 idle 是设计使然，不是停滞。只扫 `{PLANNING, IN_PROGRESS}`——成员自己该推的两个条件。
4. **场景 B 以 leader 自己的 idle 计时为「团队静默多久」的基准**，并新增两个前置：任务必须**无 assignee**（用户要的是「没人认领」），且**至少有一个非 leader 成员 READY**。全员都忙时排队是常态，催 leader 是噪音。
5. **阈值做成 spec 可调**：`stale_claim_idle_timeout` / `stale_pending_idle_timeout`（各默认 600 秒），风格对齐 `review_stall_timeout`。
6. **scheduled 原样不动**：`ScheduledStaleTaskHandler` 覆写 `_check_stale_claimed_tasks` + `_self_nudge_stale_claim`，钉住 pre-F_65 的 `updated_at` 实现，autonomous 基类里不留 `updated_at` 痕迹。
7. **`view_task` 补反轮询抑制语**（cn/en 成对），措辞对 leader 与 teammate 都成立。

## 拒绝的方案

- **naive monotonic（只在进入 idle 时打戳，不管 resume）**——不够。成员**在 idle 状态被 pause** 时，`idle_since` 停在 T0，而 monotonic 跑完整个 pause；且它没有被暂停的 round 可续，`resume_paused_round` 对它不做任何事，于是它**永远不会重新进入 IDLE 去重新打戳**。resume 后第一个 `POLL_TASK` 照样读出假停滞。故必须在 resume 路径显式 `refresh_idle_baseline()`。（pause 时正忙的成员反而没问题：`idle_since` 本就是 `None`，round 续跑后自然重新打戳。）
- **给 `TeamMember` 加状态时间戳列**——把运行态属性写进持久层，pause 一样污染它，且多一次写库。错的数据源加索引还是错的。
- **场景 A 一检测到就直接上报 leader**——leader 推不动别人的任务，第一层补救本就该是成员自己；每次都发跨进程消息也是噪音。
- **场景 A 保留 self-nudge 的同时并行加一条 idle 检测**——同一停滞会被两条路径各催一次。
- **场景 B 沿用 `task.updated_at` 判「pending 多久」**——同一个 pause 缺陷。
- **一个共享阈值**——A 和 B 是不同现象，独立可调更合用；与既有「一个旋钮一个关切」风格一致。

## 旁发现（未在本特性中修复）

`resume_paused_round()` 的 docstring 与四处文档（`agent/coordination/AGENTS.md`、`S_18`、`F_60`、`F_61`）都称它由 **`kernel.start` 尾部**调用，但代码里 `kernel.start`（`kernel.py:126-226`）**没有这个调用**；产品代码唯一调用点是 `notify_team_built`（`kernel.py:261`），而该方法开头 `if self._scheduler is None: return`——**autonomous 团队没有 scheduler，永远走不到**。三个相关单测（`test_coordination_lifecycle.py`）均直接调 `kernel.resume_paused_round()`，从不验证 `start` 是否调用它，因此掩盖了这一点。

后果正是文档所述该调用存在的理由：**autonomous 团队 pause → resume 后，被暂停的 round 不会原地续跑，成员空等新消息、静默丢掉暂停中的工作**。

本特性因此把 `refresh_idle_baseline()` 挂在 `kernel.start`（每个 run cycle 的真实入口、且在 event bus 恢复 POLL_TASK 之前），而非文档所说的「`resume_paused_round()` 之后」——后者对 autonomous 根本不执行。该 bug 独立于本特性，未在此修复，也未据此改文档（文档描述的是设计意图，代码才是偏离方；把文档改成迎合 bug 是错的）。

**更新（已修复）**：该 bug 已由紧随本特性的一次独立修复解决——考古确认它自 `f448159d9` 引入当天就贴错了位置（加在 `notify_team_built` 末尾，而该方法恰好紧邻 `pause`，视觉上像 start 相关代码的末尾）。修法是把调用挪回 `kernel.start` 尾部、`notify_team_built` 只留 scheduler 激活，并补上驱动 `start` 本身的回归测试——旧的三个 `resume_paused_round` 测试都直接调该方法，从不验证接线，这正是 bug 藏了这么久的原因。四处文档与 docstring 无需改动：它们描述的设计意图一直是对的，现在代码终于与之相符。`refresh_idle_baseline()` 的落点不受影响——它仍在 `kernel.start` 内且必须早于 poll 恢复，与 resume 调用分处同一方法的一头一尾。

## 验证

- `pytest tests/unit_tests/agent_teams/test_team_agent_coordination.py tests/unit_tests/agent_teams/test_coordination_lifecycle.py tests/unit_tests/agent_teams/agent/test_mode_handlers.py tests/unit_tests/agent_teams/test_stream_controller.py tests/unit_tests/agent_teams/test_dispatch_choice.py` → **153 passed**
- 全量 `pytest tests/unit_tests/agent_teams/` → **1939 passed / 1 failed**。唯一 failed 是 `monitor/test_models.py::test_monitor_event_does_not_expose_plan_response_feedback`，为 HEAD（`72babd8e8`，给 `MonitorEvent` 加了 `feedback` 字段却未更新该测试）的既有失败，与本特性无关。

覆盖要点：idle 计时的写入/清除、`idle_seconds()` 忙时为 `None`、场景 A 触发与 `IN_REVIEW` 排除、busy 成员绝不被催、连续窗口升级 leader 且只升级一次、场景 B 的三个前置（leader idle / 无主 pending / 有空闲成员）、节流与记账 GC、**resume 后不误判停滞**、spec 阈值串联与校验、scheduled 仍走 `updated_at` 且不做 pending 自催。

## 已知遗留

- **scheduled 仍用 `task.updated_at`**：同一个 pause 缺陷在 scheduled 下依然存在（成员 idle 跨长 pause 后，其活跃任务会被误判 stale）。本次按范围约束刻意不动；迁移到 idle 计时是后续项。
- ~~**`resume_paused_round` 未被 `kernel.start` 调用**（见「旁发现」），autonomous 的 pause→resume 续跑失效。~~ **已修复**：该调用已挪回 `kernel.start` 尾部，并补上驱动 `start` 的回归测试（`test_start_resumes_a_round_left_paused`），使四处文档与 docstring 的既有描述终于成真。
- 场景 A 的 streak 计数按「任务被催了几个窗口」累计，成员中途忙一下不清零（只在任务离开 owned-active 集时 GC）。这是刻意的：否则偶尔动一下就永远升级不了。
