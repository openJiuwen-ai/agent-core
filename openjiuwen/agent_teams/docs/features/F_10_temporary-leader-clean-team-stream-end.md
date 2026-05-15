# 临时团队 leader clean_team 后确定性结束自身 stream

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-05-14 |
| 范围 | openjiuwen/agent_teams/agent/state.py、tools/team.py、agent/agent_configurator.py、agent/team_agent.py、agent/stream_controller.py、agent/coordination/handlers/agent_lifecycle.py |
| 测试基线 | `pytest tests/unit_tests/agent_teams/` → 976 passed, 16 skipped；新增 `test_team.py` 4 例 + `test_stream_controller.py` 4 例 |
| Refs | #751 |

## 背景

临时（temporary）团队场景下，leader 的 DeepAgent 在一轮中调用 `clean_team` 工具：
`TeamBackend.clean_team()` 删除 team DB 记录、发布 `TeamCleanedEvent`。teammate 收到
该事件后经 `on_cleaned → shutdown_self → close_stream` 向自己的 stream 队列放入
`None` 哨兵，外层循环正常退出。

但 leader 的 `on_cleaned` 处理器对 LEADER 角色直接 early-return（设计如此：持久团队
leader 必须存活 `clean_team` 以接受下一次交互）。结果：临时团队 leader 调用完
`clean_team` 后，**没有任何路径向 leader 的 `stream_controller.stream_queue` 放入
`None`**。即使本轮 DeepAgent 已经跑完，`TeamAgent.invoke/stream` 的外层
`while True: chunk = await stream_queue.get()` 循环仍永远阻塞在 `None` 哨兵上——
`Runner.run_agent_team[_streaming]` 永不返回。

`on_cleaned` 旧 docstring 声称临时 leader 的 teardown「由自然的 `_finalize_round`
路径处理」，但这是误导：`_finalize_round`（kernel hook）只做 memory 提取和置空
`stream_queue`，从不关流，本轮根本没有结束信号。

## 数据结构 / 状态机

新增一个一次性 latch：`TeamAgentState.team_cleaned: bool`（默认 `False`）。

- **写**：`clean_team` 成功路径 → `TeamBackend._on_team_cleaned` 回调 →
  `TeamAgent._mark_team_cleaned` → `state.team_cleaned = True`。
- **读**：`StreamController._run_one_round` 的 finally 块，作为最高优先级终止条件。
- **生命周期**：一旦置位永不复位——临时团队被清理后即终结，pool entry 由
  `TeamRuntimeManager.finalize` 移除，同一 `TeamAgent` 实例不再有后续 round。
  （对比 `_cancel_requested` 是 round-scoped、每轮重置；`team_cleaned` 是
  team-scoped、终结态。）

跨 operator（写在 tool/clean 路径，读在 stream round-end 路径），符合四象限规则
归属 `TeamAgentState`。

## 决策

### 1. 在 `clean_team` 完成处同步置位，而非依赖 `TeamCleanedEvent` 事件

`clean_team` 工具同步运行在 leader 本轮 DeepAgent 内，回调在 `clean_team()` 成功
路径上 `await` 触发，因此 `state.team_cleaned = True` **必然先于本轮
`_run_one_round` 的 finally 块执行**。无竞态。

回调链路：`TeamBackend.__init__` 新增 keyword-only `on_team_cleaned` 参数 →
`AgentConfigurator.setup_infra / setup_team_backend` 透传 → `TeamAgent._setup_infra`
传入 `self._mark_team_cleaned`。回调仅在 `clean_team()` 成功路径（成功日志 +
事件发布之后、`return True` 之前）触发，early `return False`（成员未全部 SHUTDOWN）
不触发。回调失败 best-effort 吞掉并 `team_logger.error`——一个接线 bug 不能把一次
成功清理变成工具错误。

### 2. round-end 判断作为最高优先级终止条件

`StreamController._run_one_round` finally 块插入 `if self._state.team_cleaned:` 作为
**第一分支**，原 `if not cancelled and not self._cancel_requested:` 降为 `elif`。
团队已清理时：直接 `close_stream()` 入队 `None`，不 restart interrupt resume /
pending inputs（团队已没了，重启无意义），也不跑 mailbox-wake / shutdown-requested
逻辑。即使本轮被 cancel，`team_cleaned` 仍优先关流。

下游退出链路无需改动：`None` 入队 → 外层循环 break → `finally:
coordination.finalize_round()` → Runner finally → `TeamRuntimeManager.finalize` 见
`lifecycle != "persistent"` → `stop_coordination()` + `pool.remove()`。本次修复唯一
职责就是「在本轮结束时入队一次 `None`」。

### 3. `on_cleaned` 的 leader 分支保持 no-op，仅纠正 docstring

leader 不再「依赖」自己的 `TeamCleanedEvent` 做 teardown；该 handler 对 leader 继续
no-op，与 sender_id self-filter 一起作为纵深防御。docstring 改为如实描述新机制。

## 拒绝的方案

- **让 leader 的 `on_cleaned` 直接 `close_stream()`**：`TeamCleanedEvent` 经 messager
  异步投递，event bus 任务与本轮 DeepAgent 并发，不保证在 `_run_one_round` finally
  之前被处理——竞态。且持久团队 leader 必须存活 `clean_team`，在事件 handler 里区分
  临时/持久再决定关不关流，把竞态和角色判断混到一起，比同步 latch 脆弱得多。
- **把 latch 放在 `TeamBackend` 上、由 `StreamController` 读 `team_backend`**：
  `StreamController` 构造时只拿 `state` / `resources`，不持有 `infra` / `team_backend`；
  为读一个 bool 给它加 backend 引用会拓宽其依赖面。latch 本就是跨 operator 的运行时
  可变状态，归属 `TeamAgentState`，`StreamController` 已持有 `state`。
- **让 `clean_team` 工具自己翻 latch**：`CleanTeamTool` 只持有 `TeamBackend`，要让它
  够到 `TeamAgentState` 得给工具层注入 agent 运行时引用——工具层不应反向耦合 agent
  运行时。`TeamBackend` 的成功回调是更干净的边界。

## 验证

- `tests/unit_tests/agent_teams/test_team.py` 新增 4 例：成功路径触发回调恰好一次、
  失败路径不触发、回调抛异常不影响 `clean_team` 成功、未接线时成功路径不报错。
- `tests/unit_tests/agent_teams/test_stream_controller.py` 新增 4 例：`team_cleaned`
  置位时 round-end 关流、未置位时不关流、`team_cleaned` 优先于 pending inputs、
  `team_cleaned` 在 `_cancel_requested` 下仍关流。
- 全量 `pytest tests/unit_tests/agent_teams/` → 976 passed, 16 skipped，无回归。
- 文档同步：`docs/specs/S_08`（`TeamBackend` 构造契约 + `clean_team` 成功回调保证）、
  `docs/specs/S_02`（`TeamAgentState.team_cleaned` 字段）、`runtime/CLAUDE.md`、
  `tools/CLAUDE.md`。

## 已知遗留

- `docs/features/` 存在历史编号撞名（两份 `F_08_*`），本次新文档按目录内文档总数
  顺延取 `F_10`；既有 `F_08` 撞名需单独反馈维护者修正，不在本次改动范围内。
- subprocess 模式下 teammate 的退出仍走 `TeamCleanedEvent → on_cleaned →
  shutdown_self` 既有路径（跨进程，本来就只能靠事件），未受本次改动影响。
