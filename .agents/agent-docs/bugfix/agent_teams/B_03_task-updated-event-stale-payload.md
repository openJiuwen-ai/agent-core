# TASK_UPDATED 事件载荷取自写前快照且不携带任务状态

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-15 |
| 严重程度 | 中：并发改派窗口内事件会通知错人；消费方无法从事件本身恢复任务状态视图 |
| 范围 | `agent_teams/schema/events.py`、`agent_teams/tools/database/task_dao.py`、`agent_teams/tools/task_manager.py` |
| 测试基线 | `tests/unit_tests/agent_teams/test_task_manager.py`：85 passed；`tests/unit_tests/agent_teams/test_database.py`：129 passed |
| 关联 spec | `S_03_coordination-protocol.md`、`S_08_team-tools-contract.md`（TASK_UPDATED 载荷契约变更） |

## 现象

`update_task`（编辑标题 / 内容）发布的 `TASK_UPDATED` 只带 `member_name`，且该字段取自
manager 在 DAO 写入**之前**预读的任务行：

1. 预读与写入之间若发生并发改派（`reassign` 的 CAS 交换已提交），事件携带的是**旧**
   assignee——通知发给了已不持有该任务的成员（重看一个不属于自己的任务），而新
   assignee 收不到内容变更提示。
2. 事件不携带任务状态。以事件流维护任务视图的消费方无法从 `TASK_UPDATED` 本身判断
   任务的当前状态（编辑不改状态，但"现在是什么状态"只有回查数据库才知道），错过一条
   状态迁移事件后也无法借这条编辑事件恢复。

## 根因

`TeamTaskManager.update_task` 的流程是"先 `get_task` 预读（供校验与失败消息使用），再调
DAO 写入"，事件从预读行取 `assignee`——预读发生在任何事务之外，进程级写锁管不到它。
DAO `update_task` 返回 `bool`，只表达"改没改成"，无法把写后行带回来；manager 因此没有
一条不重查就能拿到写后状态（assignee / status）的通道。

## 决策

1. DAO `update_task` 返回值 `bool → Optional[TeamTaskBase]`：行在**写事务内**选出并随提交
   返回，`None` 统一表达"任务不存在或 IN_REVIEW 锁定"。返回行**未水合**（`content` 可能
   仍是溢写占位符），读正文走 `get_task`。
2. `TaskUpdatedEvent` 新增 `status: Optional[str]`，语义是"编辑时刻的已提交状态"（编辑本身
   不改状态）；manager 从写后行取 `assignee` 与 `status` 构造事件，消除预读竞态。
3. 事件载荷属跨子模块契约，同步修订 `S_03`（`on_task_updated` 消费侧描述）与 `S_08`
   （不变量 17 的载荷描述）。

## 拒绝的方案

- **manager 写后 `get_task` 重查**：多一次 DB 往返，且重查在写事务外，只是把竞态窗口挪
  了个位置。
- **事件维持最小载荷、消费方自行回查**：把一致性问题复制给每个消费方；错过迁移的视图
  仍要一次额外查询才能恢复。
- **DAO 返回完整水合行**：编辑路径不需要正文，水合 `content`（可能触发溢写读取）是纯
  浪费；需要正文的调用方本来就该走 `get_task`。

## 验证

- `test_task_manager.py::test_update_task_event_carries_post_write_status`：in_progress 与
  pending 两条路径下，事件的 `status` / `member_name` 均来自写后行。
- `test_database.py` 六个 `update_task` DAO 用例改为断言新契约（返回行 / `None`），其中
  in_progress 用例锁住"编辑不改状态"的写后语义。
- 全量基线：test_task_manager 85 passed、test_database 129 passed。

## 已知遗留

- 消费侧（coordination `on_task_updated`、monitor）尚未利用 `status` 字段——本次只保证
  载荷正确，消费方按需接入。
- 返回行未水合是 DAO 契约的一部分（docstring 已声明），本次不改动水合行为。
