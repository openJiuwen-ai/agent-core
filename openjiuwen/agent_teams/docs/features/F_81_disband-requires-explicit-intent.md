# 解散团队只认显式意图

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-15 |
| 范围 | `openjiuwen/agent_teams/agent/coordination/handlers/member.py`、`openjiuwen/agent_teams/agent/coordination/kernel.py`、`openjiuwen/agent_teams/tools/team.py`（docstring）、`docs/specs/S_03`、`docs/specs/S_08` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/` → 2625 passed / 39 skipped / 3 xfailed |
| Refs | #984 |

## 背景

用户报告：一个 persistent 团队在 leader 之外的成员全部 `shutdown_member` 之后，团队自己没了。

复现路径是两处**自动** `clean_team`：

1. `MemberHandler._maybe_clean_team_after_shutdown` —— leader 每收到一条
   `MEMBER_STATUS_CHANGED(new_status=SHUTDOWN)` 就列一次名册，只要非 leader 成员集合
   全是 `SHUTDOWN`，立刻 `team_backend.clean_team()`。**不分 lifecycle**。
2. `CoordinationKernel.start` —— leader 启动时读到团队行仍在、且全体 teammate 是
   `SHUTDOWN`，判定为"上次清理没做完"，同样直接 `clean_team()`。

两处的后果都不轻：`clean_team` cascade 删掉团队行、名册、任务板、消息历史，再经
`_remove_cleanup_paths()` 对登记的 workspace / output 路径 `shutil.rmtree`——**产物直接删盘**。
而 runtime pool 里的 entry 还在，`list_active_teams` 仍报这个团队活着。

真正致命的是它与既有禁令直接冲突。`tools/tool_factory.py` 对 persistent 团队摘掉
`clean_team` 工具，理由写在原地：

> Persistent teams are torn down by the operator through SDK facades
> (delete_agent_team etc.); letting the leader LLM call clean_team mid-round would
> race the runtime pool invariants and silently de-register a team the operator
> still considers live.

而 handler 干的正是这段话点名禁止的事——round 中途、绕过 operator、悄悄注销一个
operator 认为存活的团队。原注释把这条禁令当成缺陷去补（"persistent teams do not expose
`clean_team` as a leader tool，所以需要一个 leader-side guard"），方向搞反了：前门锁上
是因为不该进。

## 根因

**用一个瞬时状态推断一个意图。**

"此刻没有存活成员"与"要解散团队"不同构。leader 关掉最后一名成员、准备 spawn 下一批，
这两个动作之间的那一瞬同样满足前者。把它读成后者，就是拿一个高频出现的中间态去触发
一个不可逆的破坏性操作。

这不是边界条件没考虑周全，是这套推断本身站不住——所以修法不是加 gate，是删推断。

## 决策

1. **删除 `MemberHandler._maybe_clean_team_after_shutdown` 及其调用点与
   `_team_clean_requested` 字段。** leader 在 `MEMBER_STATUS_CHANGED` 上只剩观测 + 喂活动
   登记（F_74），handler 回到无状态。
2. **删除 `CoordinationKernel.start` 的 all-SHUTDOWN 分支。** 团队行还在就是有团队可以
   重新加入，`recover_team()` 无条件走，名册长什么样都不改变这个判断。
3. **`S_03` 新增不变量 21**：解散团队是显式行为，coordination 永远不从名册状态推断它。
   合法调用者只有两个——temporary leader 的 `clean_team` 工具，operator 的
   `delete_agent_team`（经 `force_clean_team`）。这是铁律 1（coordination 不做决策）在
   生命周期上的特例化。
4. **同步刷掉两处对已删分支的交叉引用**：`TeamBackend.rejects_rebuild` 的 docstring 与
   `S_08` 不变量 21 都拿"kernel.start 的清理分支"当"恢复出来的 leader 可能没有团队行"的
   例子，改为显式解散的两条真实路径。`S_03` 机制 17（teammate stop 不写持久状态）的理由
   也重写——结论不变，但它不再依赖那条自保分支，而是直接说 `STOPPED` 与 `SHUTDOWN` 的
   语义差别。

## 拒绝的方案

### A. 只给 persistent 加 lifecycle gate，temporary 保留自动清理

与 `tool_factory` 的工具暴露范围逐字对齐，改动最小。

否决理由：它只修了症状最重的那一半。temporary 团队同样会在"换一批成员"的窗口里被误删，
而那个窗口的存在与 lifecycle 无关——错的是推断本身，不是推断的适用范围。留着它等于留着
同一个 bug，只是把触发面积改小到不容易被报上来。

### B. 加一个"解散意图"标记，leader 表达解散后才允许自动清理

让 leader 先声明意图（工具参数 / 状态位），`shutdown_member` 走完再由 handler 收尾。

否决理由：那个标记就是 `clean_team` 本身。temporary leader 已经有这个工具了，加一个
语义完全重合的标记只是把一次显式调用拆成两步，多一个可能不一致的状态位。persistent
团队则根本不该有这条路——它的解散入口在 operator 那侧。

### C. 保留自动清理，但只删 DB 行不删文件

减轻后果（不 rmtree 产物），保留"自然语言解散"的兜底。

否决理由：仍然会误删任务板与消息历史，仍然会让 operator 手里的 pool entry 悬空。把一个
错误操作做得轻一点，不如不做。

## 已知遗留

- **自然语言的"解散团队"若只做到 `shutdown_member` 就停手，团队行会留下。** 这是本次有意
  接受的代价：一个残留的空团队可以被 operator 清理、也可以被 leader 补一次 `clean_team`
  （temporary），而误删的产物找不回来。若后续要治，正确方向是让 leader 的
  `shutdown_member` 结果文本提示"要解散请调 `clean_team`"，而不是替它推断。
- `kernel.start` 删掉分支后，一个所有 teammate 都是 `SHUTDOWN` 的团队会走
  `recover_team()`。该路径对空名册的行为未在本次改动中专门加固——现有单测未暴露问题，
  但值得在下一次碰 recovery 时确认。
