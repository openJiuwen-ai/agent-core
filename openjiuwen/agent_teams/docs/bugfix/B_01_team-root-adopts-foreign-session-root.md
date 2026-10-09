# Team 根 span 误认并发会话的 root，导致整条团队轨迹丢失

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-30 |
| 严重程度 | 高（整个 Team 会话的轨迹静默丢失，无报错） |
| 范围 | `openjiuwen/core/runner/team_runner.py`、`openjiuwen/agent_teams/observability/span_context.py`、`openjiuwen/extensions/observability/span_context.py`、`openjiuwen/harness/observability/span_context.py`、`openjiuwen/harness/deep_agent.py`、`openjiuwen/agent_teams/agent/coordination/kernel.py` |
| 测试基线 | `tests/unit_tests/{agent_teams,extensions/observability,harness,core/runner}`：9390 passed, 93 skipped, 3 xfailed（基于 upstream/develop） |
| 关联 spec | S_14_monitor-and-observability.md（不变量 17） |

## 现象

JiuwenSwarm Web 端的一个 Team 会话（"创建一个 3 人团队，其中 1 个成员是 human agent，依次报数"）
任务正常完成、对话区内容完整，但轨迹 UI 显示"暂无轨迹 / session not found"，trace 数和 span 数都是 0。

- 前端轮询 `/api/trajectory/sessions/<sid>/subjects` 与 `/usage` 返回 200（空），`/stream-frames`
  持续返回 404 `session not found`——会话元数据正常，是该会话的轨迹 sqlite 从未创建
  （`AsyncTrajectoryReader._connect` 找不到文件时返回 None）。
- 同一进程导出的 OTel 文件 `traces-<date>.jsonl` 里，该会话 id 与其成员名命中 0 次；同期其他会话
  （包括 8 分钟后同样 prompt 的 HITT 团队）都有完整 span。问题不是 sink 丢数据，而是 span 根本没产出。

## 根因

关键日志（`logs/run/jiuwen.log`）：

```
_maybe_attach_observability: found existing team span
  name=agent.agent.work.normal.web_<另一个单 Agent 会话> is_recording=True
```

正常团队这里打印的是 `get_or_create_team_span CREATE new team span`。本次 Team 启动时，另一个
**单 Agent 会话**正在运行（时间窗完全重叠），链路如下：

1. `_TeamRunnerMixin._maybe_attach_observability(agent, session_id)` 手里有本 Team 的 `session_id`，
   但查"已有 team root"时调用的是不带 session 的 `get_team_span()` → `get_root_span()`。
2. 这条启动路径上 session ContextVar 未绑定（日志 trace id 为 `default_trace_id`），`requested_sid`
   为空；`get_root_span` 在无 session 时有进程级兜底：**session 注册表里只有一个存活 root 就返回它**
   （harness 的 `resolve_run_root_span` 也有同样的"唯一在飞 run"兜底）。
3. 此刻进程里唯一存活的 root 是单 Agent 会话的 run root，于是被当作"本团队已有的 team root"，
   runner 跳过 `get_or_create_team_span(..., session_id=session_id)`。
4. 本 Team 的 session 在 `_root_registry` 里从未注册 root；成员 rail / callback 按本 session 查 root
   得到 None，整轮不记录任何 span → 轨迹库从未创建 → UI 404。
5. 那个单 Agent 的 trace 里也没有任何团队 span——团队 span 不是挂错了父，而是整体丢失。

复现条件：**单 Agent 会话运行期间启动 Team 会话**。单 Agent 结束后再起的 Team 不受影响，
所以问题表现为偶发。

## 决策

核心原则：**单 Agent 与 Team 会话天然隔离；已知自身 session 时只认本 session 的 root，
任何路径都不得以"进程里唯一存活的 root"猜测归属。**

1. **新增严格访问器 `get_session_root_span(session_id)`**（`extensions/observability/span_context.py`）。
   只返回本 session 的 root：上下文绑定的 root 仅当绑定 session 为空或等于本 session 时接受；
   其余一律查 `_root_registry[session_id]`；不走 ambient、不走其他 session；空 session 抛 `ValueError`。
2. **`get_root_span()` 删除"注册表唯一存活 root"兜底**。注册表只按 session 精确查；无 session 时只看
   上下文绑定的 root 与宿主显式设置的 ambient root。该兜底的风险是双向的：Team 可能认领单 Agent
   的 root，单 Agent 迟到的回调也可能认领 Team 的 root。
3. **`get_team_span()` 按 session 解析**：新增 `session_id` 参数，解析优先级为显式参数 >
   `agent_teams.context` ContextVar > observability 当前 session。能确定 session 就调用
   `get_session_root_span`；确定不了只返回上下文绑定的 root。`get_or_create_team_span()`
   复用已有 root 时同样按 session 精确查找。
4. **runner 侧 `session_id` 改为必填**：`_maybe_attach_observability(agent, session_id: str)`
   对空值打 warning 并跳过（遵循"可观测性不阻断运行"）；查找已有 root 改为
   `get_team_span(session_id=session_id)`。严格查找只返回仍在记录的 span，原来的
   "team span ENDED → clear 后重建"分支已不可达，一并删除。
5. **单 Agent 的 supervisor 携带 session，harness 删除"唯一在飞 run"兜底**。`DeepAgent.start()`
   在创建 scheduler / forwarder / supervisor 之前，与 `init_session_state()` 同处调用
   `set_current_session_id(sid)`：交互循环一次只绑定一个 session，`start()` 本来就在调用方 context 上
   绑定会话级状态，由它派生的所有 task 都继承这个 session。于是 `resolve_run_root_span` 只做精确查找
   （`_ROOT_SPANS[session]`）；没有 session 的调用方只能命中未带 session 注册的 run。某个会话结束后
   迟到的回调，不会再落进另一个仍在运行的会话的 trace。
6. **子 Agent 路径无需改动**：`SubagentInstance` / `task_tool` 把父 run 的 root 以子 session id
   注册为别名，子 Agent 自己的 `start()` 绑定的正是这个子 session，精确查找直接命中。
7. **kernel 在 listener 分发处绑定订阅的 session**。`CoordinationKernel.subscribe_transport` 的
   topic 按会话订阅，投递来的事件一定属于该会话；但 messager 调用 handler 的上下文不是这个会话
   （pyzmq 用自己的接收循环 task，inprocess 用发布方的 task）。`_filter_self` 在整个投递处理期间，
   用 `set_session_id` / `reset_session_id` 绑定订阅时的 session，实际处理放在内层 `_deliver`。这样
   `OtelTeamMonitorHandler` 和 `TeamObservabilityRail` 里的 `get_team_span()` 都能按正确的 session
   精确解析，listener 的日志 trace id 也会显示真实的 session。绑定范围覆盖整个投递而不只是 listener
   分发：同一处理流程里还有自发 BROADCAST 的群聊判定，它会查 per-session 的 message 表，表名同样由
   session contextvar 哈希得出，从未绑定的接收循环里执行会查错表。

## 拒绝的方案

1. **只修 `team_runner` 一处调用点，保留全局兜底**。可以修掉这次的现象，但
   `get_root_span()` 的"唯一存活 root"兜底仍会在其他无 session 调用点跨会话、跨模式认领 root，
   同类问题会换个入口再出现。
2. **给 root 打模式标签（single / team），兜底时按模式过滤**。会话之间本来就用 session id 隔离，
   再加一层模式过滤只会掩盖"查找没带 session"这个真正的问题，而且同模式并发时照样串会话。
3. **`get_session_root_span` 拒绝一切 session 不等的上下文 root（包括空 session 绑定）**。
   第一版就是这样实现的，结果 5 个既有用例回归：先以空 session 创建 team root，随后回调设置了
   session，root 就找不到了。ContextVar 不会带出其他 task 的绑定，因此空 session 的上下文 root
   必然属于当前执行；本次故障的外来 root 位于另一个 task，只能经注册表查到，而注册表已按 session
   精确查找，所以放宽这一条不影响修复。
4. **整体删除 harness 的 `resolve_run_root_span` 注册表**。单 Agent 的 supervisor task 读不到请求
   上下文的 root ContextVar，要靠按 session 的注册表把 LLM/tool span 挂到正确的 trace 上；子 Agent
   也靠它把父 run 的 root 以子 session 为 key 挂上别名。需要删掉的只是其中"唯一在飞 run"这一条猜测，
   注册表本身保留（见决策 5）。
5. **在 monitor handler / rail 各调用点从事件 payload 显式传入 session**。`EventMessage` 不带
   session，payload 里也不保证有；而且 handler 是进程级单例，被所有 leader 共用，自己没法知道事件
   属于哪个会话。真正知道归属的是按会话订阅 topic 的 kernel，所以在分发处统一绑定（见决策 7）。

## 验证

- 新增回归用例（`tests/unit_tests/agent_teams/observability/test_team_root_session_registry.py`）：
  - `test_the_runner_never_adopts_a_concurrent_single_agent_root`：单 Agent run 在飞时启动 Team，
    Team 必须创建自己的 `team.<name>` root，且单 Agent 的 root 不受影响（复现本次故障）；
  - `test_an_unscoped_lookup_never_adopts_a_session_registered_root`；
  - `test_a_session_lookup_ignores_a_root_bound_for_another_session`；
  - `test_a_session_lookup_requires_a_session_id`。
- 同一场景在修复前的代码上复现出与线上一致的日志
  `found existing team span name=agent.single`，Team root 为 None。
- 遗留修复新增 / 调整的用例：
  - `tests/unit_tests/agent_teams/test_event_listener_session_binding.py`：从未绑定 session 的上下文投递
    事件，listener 仍能看到订阅时的 session；自发 BROADCAST 的 message 表查询也在订阅的 session 下
    执行（两条用例都在修复前的 kernel 上失败）；
  - `tests/unit_tests/harness/observability/test_span_context.py`：不带 session 的查找不会认领唯一在飞的
    run；未带 session 注册的 run 可以不带 session 查到；绑定了 session 的 supervisor 在多个 run 并发时
    能精确找到自己的 root；LLM span 查找改为通过 supervisor 绑定的 session 解析；
  - `tests/unit_tests/harness/observability/test_run_span.py`：run root 注册断言改为按 session 显式查找。
    原断言依赖环境里残留的 session，在批量运行中恰好被"唯一在飞 run"兜底掩盖。
- 相关单测目录全量（`agent_teams`、`extensions/observability`、`harness`、`core/runner`）：
  9390 passed, 93 skipped, 3 xfailed（rebase 到 upstream/develop 之后）。

## 已知遗留

首轮修复时记录的两条遗留，已按决策 5–7 修复：

- ~~harness `resolve_run_root_span` 的"唯一在飞单 Agent run"兜底~~ → 决策 5。
- ~~monitor handler / rail 的 `get_team_span()` 依赖的 session ContextVar 在事件回调中没有保证~~ → 决策 7。

目前没有新的遗留。
