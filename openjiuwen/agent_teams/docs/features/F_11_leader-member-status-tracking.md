# Leader Member Status Tracking

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-14 |
| 范围 | `openjiuwen/agent_teams/agent/team_agent.py`、`agent/member.py`、`agent/member_factory.py` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/`（939 passed, 16 skipped） |
| Refs | #751 |

## 背景

`agent_teams` 里 **leader 的成员状态从不写库**，而普通 teammate 的状态更新正常。
leader 的 `team_member` DB 行被冻结在 `build_team` 时写入的 `BUSY` / `RUNNING`，
之后真实的 READY/BUSY/ERROR 轮转全部丢失——leader 在 roster 视图、监控、事件流里
看起来永远「在忙」。

根因是一条**从未触发**的懒初始化链路：

1. `TeamAgent._update_status` / `_update_execution` 在 `_state.team_member is None`
   时是静默 no-op。
2. `_setup_agent` 只对 `TEAMMATE` / `HUMAN_AGENT` 同步创建 `TeamMember` handle，
   LEADER 被跳过——注释声称 leader handle「懒创建于 `_on_teammate_created(self.member_name)`」。
3. `_on_teammate_created` 唯一触发点是 `TeamBackend.startup(on_created=...)`，
   而 `startup` 只遍历 `status == UNSTARTED` 的成员。
4. `build_team` 把 leader 注册为 `status=BUSY`——leader 永远不在 UNSTARTED 集合里，
   `_on_teammate_created(leader)` 永不触发，`_init_leader_member` 永不执行，
   `_state.team_member` 对 leader 永远是 `None`。
5. cold recovery 同样中招：`recover_from_session` 重建 leader 走 `configure()` →
   `_setup_agent`（仍跳过 LEADER），`build_team` 不在 recovery 跑，
   `RecoveryManager.recover_team` 也不初始化 leader 自己的 handle。

`create_member_handle` 其实是**纯构造函数**——只需已绑定的 `team_backend`，不碰 DB；
`TeamMember` 是无状态薄 handle，`status()` 在行不存在时返回 `None`。所谓「leader 的
team row 晚于 handle，所以必须懒创建」的 *data dependency* 是个伪命题。

## 数据结构 / 状态机

无新增数据结构。涉及的既有状态机：

- `MemberStatus` / `ExecutionStatus`（`schema/status.py`），`MEMBER_TRANSITIONS` 允许
  `BUSY -> READY` 与 `READY -> BUSY`——leader 行存在后轮转合法，无无效转换日志。
- `TeamMember`（`agent/member.py`）现在显式定义「行尚未注册」契约：`status()` 返回
  `None` 时，`update_status` / `update_execution_status` 静默返回 `False`
  （`team_logger.debug`，不打 error），不再下探到 DAO。

## 决策

**消除特殊情况**：handle 是纯构造函数，对所有角色在 `_setup_agent` 内同步创建即可，
和 teammate 完全一致。被跳过的那条懒初始化链路本就是坏的，直接删掉。

代码层面：

1. `team_agent.py::_setup_agent`——构造 handle 的角色判断从
   `ctx.role in (TEAMMATE, HUMAN_AGENT) and ctx.member_name` 收敛为 `ctx.member_name`，
   所有角色一视同仁。这一条同时修复了 fresh build 与 cold recovery（recovery 也走
   `configure()` → `_setup_agent`，且此时 team row 已存在）。
2. `team_agent.py`——删除死代码 `_init_leader_member`，删除 `_on_teammate_created` 里
   `teammate_id == self._member_name()` 的 leader 分支。`on_teammate_created` 经
   `TeamToolRail` → `SendMessageTool` 的接线**保留**（teammate 自动启动仍需要）。
3. `member.py`——`update_status` / `update_execution_status` 在 `old_status is None`
   时静默返回 `False`。让状态写入路径**靠数据健壮**：fresh-build leader 在调
   `build_team` 之前持有一个「行尚未存在」的 handle，这是预期的 no-op 而非错误。
4. `member_factory.py`——重写模块 docstring，删除已不成立的「触发时机不同、无法统一」
   叙述。

## 拒绝的方案

**Option A+（按现有注释意图，多个显式触发点）**：把 `on_teammate_created` 接进
`BuildTeamTool`、`build_team` 成功后调 `on_teammate_created(leader)`，再在 recovery
路径单独补一次 leader handle 初始化。

拒绝原因：保留了坏掉的懒初始化机制，为「handle 应该存在」这**一个事实**引入 2~3 个
触发点（`BuildTeamTool` + recovery + 原 `_on_teammate_created`），并保留
`_on_teammate_created` 的角色重载。这正是 `.claude/rules/rules.md` 反对的特殊情况
思维——「好代码没有特殊情况」「解决问题的根本」。Option C 把三个触发点收敛成
`_setup_agent` 这**一个**构造点，且不依赖「调用时序正确」。

## 验证

- 新增回归测试：
  - `test_team_agent.py::test_setup_agent_builds_leader_member_handle`——核心回归守卫，
    `.build()` 出的 leader 的 `_state.team_member` 必须非空。
  - `test_team_agent.py::test_setup_agent_builds_teammate_member_handle`——teammate 路径无回归。
  - `test_runner_team_runtime.py::test_team_agent_recover_from_session_builds_leader_member_handle`——
    cold-recovered leader 同样持有 handle。
  - `test_member.py::test_update_status_silent_false_when_row_absent`——行未注册时
    `update_status` / `update_execution_status` 静默返回 `False`、不下探 DAO。
  - `test_member.py::test_leader_member_status_persists_after_build_team`——`build_team`
    物化 leader 行后，handle 写入落库（`BUSY -> READY -> BUSY`）。
- 测试质量清理：`test_team_agent_coordination.py` 多处 `agent._team_member = None`
  写的是不存在的属性（真实字段 `agent._state.team_member`），改正为
  `agent._state.team_member = None`，让这些用例真正按其注释意图卸掉 handle。
- 全量 `tests/unit_tests/agent_teams/`：939 passed, 16 skipped。

## 已知遗留

无。
