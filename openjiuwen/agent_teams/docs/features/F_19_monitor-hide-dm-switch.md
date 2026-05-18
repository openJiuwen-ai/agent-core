# Monitor `hide_dm` Switch

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-17 |
| 范围 | `openjiuwen/agent_teams/monitor/team_monitor.py`、`openjiuwen/agent_teams/runtime/manager.py`、`openjiuwen/core/runner/team_runner.py`、`openjiuwen/agent_teams/docs/specs/S_14_monitor-and-observability.md` |
| 测试基线 | 未跑（纯参数透传 + 过滤分支，无新外部依赖） |
| Refs | #751 |

## 背景

`TeamMonitor` 把团队邮箱里的所有消息暴露给 SDK / CLI / 上层 UI——点对点 DM
与广播都进同一份 `get_messages` 结果 + 同一条 `events()` 事件流。某些消费场景
（例如 IM 频道镜像、告警面板、合规审计）只关心广播这种"对全员可见"的消息，
不想让点对点 DM 进入视图。之前调用方只能在外层自己再过滤一道，且事件流里仍
会被 `MonitorEventType.MESSAGE` 噪音淹没。把过滤下沉到 monitor 实例上，由
单一开关 `hide_dm` 统管 pull / push 两条路径。

## 决策

- **开关粒度落在 `TeamMonitor` 实例**：一次配置贯穿该 monitor 的全生命周期，
  避免在每次 `get_messages` 调用处都让消费方传一遍参数；也避免 monitor 状态
  与外部 boolean 不一致。
- **对称过滤 pull + push**：`get_messages` 与 `events()` 必须同向。单边过滤
  会让"流里没有 DM，但 query 仍能查到"或反过来，破坏调用方"hide_dm = DM
  不可见"的预期。形成不变量 15 写入 `S_14`。
- **DAO 层下推 broadcast 过滤**：全 team 查询走
  `get_team_messages(broadcast=True)`，让数据库直接过滤，不在 Python 层再
  做后置筛选——`message_dao` / `memory_database` 两个后端原生支持
  `broadcast: Optional[bool]` 参数，复用即可。
- **单收件人视图直接短路 `[]`**：`get_messages(to_member_name=X)` 的语义就是
  "X 的 DM 收件箱"，`hide_dm=True` 时按构造必然为空，无需进 DAO，省一次
  session 绑定与 SQL。
- **`hide_dm` 不下沉到 schema**：开关属于"观察者视角策略"，不是消息本身的
  属性，也不写入持久层；保持 `MonitorEvent` / `MessageInfo` 字段不变，新增能力
  完全在 monitor 内部消化。
- **关键字参数贯通整条 facade 链**：`Runner.get_agent_team_monitor` →
  `_RunnerImpl.get_agent_team_monitor` → `TeamRuntimeManager.get_monitor` →
  `create_monitor` → `TeamMonitor.__init__` 全部追加 keyword-only `hide_dm:
  bool = False`。默认 False 保持向后兼容；存量调用方零改动。

## 拒绝的方案

- **在 `_on_event` 里复用 `MonitorEventType` 的"黑名单 set"**：考虑过让
  调用方传任意事件类型集合做过滤（更通用），但需求只问消息维度，强行
  扩成"通用事件过滤器"会把 monitor 退化成"半个 EventBus 订阅器"，与不变量 4
  "白名单基于 MonitorEventType"的语义冲突。一个布尔够用，多余的灵活性会
  腐蚀边界。
- **在 `MessageInfo` 上加 `is_dm` 字段让调用方在外层过滤**：把"看不见 DM"的
  策略推给每个消费方实现一次，事件流里仍有 DM 噪音；与"开关粒度落在
  monitor 实例"决策矛盾。
- **后置 Python 过滤（`if i.broadcast`）**：实现更短，但全 team 视图下会把
  DM 拉到内存再丢弃，DAO 已经支持 `broadcast=True` 下推，没理由不用。

## 验证

- 静态：四个文件均通过 ruff 行长 120 与项目命名/类型注解约束。
- 行为：
  - 默认路径（`hide_dm=False`）：所有签名追加的是 keyword-only 默认参数，
    现有调用点（含 `tests/unit_tests/agent_teams/test_runner_team_runtime.py:1280`
    的 mock）行为不变。
  - `hide_dm=True`：`get_messages()` 不带 `to_member_name` 时调用
    `get_team_messages(broadcast=True)`；带 `to_member_name` 时直接返 `[]`，
    不进 `_bound_session`；`_on_event` 丢弃
    `MonitorEventType.MESSAGE`，`BROADCAST` 与所有 team/member/task 事件正常入队。

## 已知遗留

- 未来如果要扩展"按发件人 / 按时间窗"等更细的过滤维度，应该考虑把
  `hide_dm` 升级为一个 `MonitorFilter` 配置对象，而不是继续往 `__init__`
  上堆 boolean 参数；当前只有一个开关、暂时不抽。
- CLI `/team monitor` 子命令目前没有暴露 `hide_dm`；若 IM 镜像类用例需要，
  另起 feature 在 `cli/commands.py` 加 flag 转发。
