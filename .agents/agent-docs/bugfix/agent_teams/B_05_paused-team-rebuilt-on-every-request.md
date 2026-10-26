# 同一模型选择被判成"换模型"，paused 团队每次追问都被拆掉重建

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-15 |
| 严重程度 | 中：每次追问都冷恢复整个团队（额外开销、成员运行时重建）；宿主挂在旧 leader 上的监控随之失联，团队事件丢失 |
| 范围 | `agent_teams/models/pool.py`、`agent_teams/runtime/manager.py` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/`：3688 passed, 36 skipped, 3 xfailed, 1 failed（`test_member_activity.py::test_observe_member_status_emits_after_the_debounce`，upstream/develop 上同样失败，与本修复无关） |
| 关联 spec | `S_11_models-pool-and-allocation.md`（不变量 3、`inherit_pool_ids` 对齐规则） |

## 现象

jiuwenswarm web 会话里，一个 persistent 团队完成一轮后进入 paused。此后用户每发一条追问，
core 日志都出现：

```
activate: model %s changed for paused team pool; rebuilding runtime
stop_team: team ... stopped and removed from pool
activate: team ... dispatched to cold_recover
```

用户并没有换模型。连续两次追问（15:07:35、15:08:08）都重建了运行时。宿主（swarm）的
`TeamMonitor` 还挂在被拆掉的旧 leader 上，于是这两轮的成员状态、任务创建 / 开工 / 完成事件
全部没有到达前端，也没写进会话历史。

日志里的 `%s` 是另一处小问题：该条日志混用了 `%s` 与 `{}` 占位符，变化原因没有被渲染出来。

## 根因

从会话 checkpoint 取出持久化的 `model_pool`，唯一的条目形如：

```json
{"model_name": "deepseek-flash", "model_id": "2be6...", "metadata": {"client": {"client_id": "42a2...", "timeout": 1800.0, ...}}}
```

swarm 在 `model_selection` 路径（`TeamManager._apply_compiled_team_selection`）把编译出的
`ModelClientConfig` 整个 `model_dump()` 进 `metadata.client`。`ModelClientConfig.client_id` 的
默认值是 `uuid4()`，每次编译都是新值。

`_entry_signature` 只排除了条目级的 `model_id`，`metadata.client.client_id` 照样参与比较。
`TeamRuntimeManager.activate` 用这个签名比较 paused 运行时的 live pool 与本次请求的 pool，
uuid 不同即判定 `selection_changed`，`stop_team` 后走冷恢复。冷恢复又把本次请求的 pool 写成
新的 live pool，下一次请求再换一个 uuid，如此每次都重建。

这个 `client_id` 本身是无效数据：`to_team_model_config` 物化时用 `model_id` 覆盖它。

## 决策

- `_entry_signature` 排除 client 身份：在 dump 出的副本里删掉 `metadata.client.client_id`，
  条目本身不改。`inherit_pool_ids` 与 `activate` 共用这一个签名，两处都受益：
  同一选择的两次构建继承同一个 `model_id`，paused 运行时不再被误拆。
- 修正 `activate` 那条日志的占位符（统一用 `{}`），让变化原因（`pool` / `strategy`）正常输出。

## 拒绝的方案

- **只在 swarm 侧 dump 时去掉 `client_id`**：只修一个调用方；任何把 `ModelClientConfig` dump 进
  `metadata.client` 的宿主都会再踩一次。签名应该只比较配置，这条规则放在 core 才完整。
  swarm 侧顺手清理是可选的，不是修复的前提。
- **签名里忽略整个 `metadata.client`**：`timeout`、`verify_ssl`、`custom_headers` 等确实是
  配置，变化必须触发新 client（S_11 不变量 3）。
- **`activate` 不再因模型变化重建运行时**：真正换模型时冷恢复是设计行为（让新模型生效）。

## 验证

- 新增 `models/test_allocator.py::test_inherit_pool_ids_ignores_client_id_under_metadata_client`：
  两次构建只差 `metadata.client.client_id` 时签名相同、`model_id` 被继承，且签名不改条目。
- 新增 `runtime/test_manager.py::TestActivatePausedModelSelection`：只差 `client_id` 时
  `stop_team` 不被调用；`api_key` 真变化时仍然重建。

## 已知遗留

- 宿主监控挂在旧 leader 上的问题仍在：`stop_team` 拆掉运行时时不会通知已挂上的
  `TeamMonitor`，宿主也无从得知。本修复去掉了"无故重建"这个最常见的触发条件，但真正换模型
  时事件仍会丢，需要 swarm 侧（必要时配合 core 暴露 monitor 绑定的 leader）单独修复。
