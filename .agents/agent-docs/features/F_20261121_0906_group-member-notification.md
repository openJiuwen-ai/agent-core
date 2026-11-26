---
id: F_20261121_0906_group-member-notification
title: 群聊成员通知入口实施记录
type: F
author: weichenhao
author_email: weichenhao3@huawei.com
modules: [openjiuwen/agent_teams]
files: [openjiuwen/agent_teams/tools/team.py, openjiuwen/agent_teams/tools/database/message_dao.py, openjiuwen/agent_teams/tools/models.py, openjiuwen/agent_teams/tools/database/engine.py, openjiuwen/agent_teams/schema/conversation.py]
designs: [D-20261121-0832-group-member-notification]
---

# 群聊成员通知入口实施记录

## 关联依据

- 设计：`D-20261121-0832-group-member-notification`（草稿，未经评审；需求方在 2026-11-21 要求开始实现）。
- spec：`S_28_group-conversation-and-delegation` 新增“成员通知”一节。
- 日期：2026-11-21。
- 实际实施范围：通知动态表、DAO 的写入、三处寻址查询与已读标记、`TeamBackend.notify_group_member`、`GroupNotificationResult`。未改 `context_for`、消息处理器和外部客户端。

## 决策归属与关系

新决定。`S_28` 中“只有 mentions 中的专家才接收模型输入”“群聊不额外创建定向通知记录”两句的适用范围缩小为没有通知的消息；其余契约继续有效。宿主侧的判断与界面见 jiuwenclaw 的 `D-20261120-1701-swarmchat-auto-reply`。

## 实际实现与开发决策

- 新增每会话动态表 `group_notification_<session>`，主键为宿主提供的 `notification_id`，另有 `(team_name, member_name, message_id)` 复合索引。加入 `_ensure_session_tables`、`drop_cur_session_tables`、`TEAM_DYNAMIC_TABLE_PREFIXES` 和动态模型缓存清理。
- `get_broadcast_messages` 把“存在通知”与“在 mentions 中”并列为寻址条件；未读过滤时前者看通知是否待消费，后者仍看水位。`get_unread_group_members` 与 `_broadcast_unread_recipients` 并入有待消费通知的成员，沿用原有的角色与状态排除。
- `_mark_read_in_session` 在同一事务把（成员, 消息）的待消费通知标为已消费。先查询是否存在再更新：绝大多数广播没有通知，直接更新会让每次读取多一次写和一次取时钟。
- `notify_group_member` 校验后写入，再对该消息调用既有的 `publish_broadcast`。重复标识也会再次发布，已消费的通知不会因此重新投递。
- why-not：没有把被通知成员写入消息 meta，也没有用隐藏广播行或私信，理由见设计 4.3。

## 与设计的偏差

无。设计中的 5.2、5.3、5.5 按原样落地。

## 实际执行与证据

| 执行日期 / 验证范围 | 实现版本与环境 | 命令 / 复现步骤 | 实际结果 | 未覆盖项 / 失败原因 |
| --- | --- | --- | --- | --- |
| 2026-11-21 V-01 至 V-08 | 工作区未提交改动，基线 55756c559；内存 SQLite；jiuwenclaw 的 `.venv`（Python 3.11.14） | `python -m pytest tests/unit_tests/agent_teams/test_group_notification.py --no-cov` | 10 项通过 | 无 |
| 2026-11-21 V-09 回归，第一次 | 同上 | `python -m pytest tests/unit_tests/agent_teams --no-cov -n 6` | 4641 通过、1 失败、37 跳过、2 预期失败 | `external/test_client.py::test_group_inbox_only_consumes_mentions` 失败：已读标记无条件读取时钟，用例的时钟只提供两个值 |
| 2026-11-21 V-09 回归，修正后 | 同上 | 同上 | 4642 通过、37 跳过、2 预期失败 | 无 |

新增用例覆盖：被通知成员读到未 @ 的消息且消息行与 `history.jsonl` 字节不变；早于水位的通知仍被读到、摘录只含触发消息、水位不后退；未启动成员进入启动名单；重复标识与消费后重复；真人、宿主管理、未知、已离开成员及自己的消息、不存在的消息、普通广播、普通团队模式被拒；待消费通知计入未读直到消费或撤回；表随会话表创建并被两种清理入口删除。

### 可见行为、副作用与证据有效范围

证据来自真实 SQLite 上的 DAO、`TeamBackend` 与 `GroupMessageHandler`，Harness 投递为替身。未运行真实成员运行时、真实模型或宿主联调：Leader 启动未运行成员的实际过程、成员收到输入后的公开回复没有端到端证据。`cleanup_all_runtime_state` 的清理未单独断言，它与 `drop_session_tables_by_id` 共用同一前缀清单。

## 验证结论

- 已证明范围：通知的持久化、幂等、寻址、消费和校验，以及既有 agent_teams 单测不回归。
- 未覆盖的承诺：真实运行时下的启动与回复；升级前创建的真实数据库文件上的补建（只验证了删表后重建）。
- 可支持的交付判断：可供宿主联调。代码未提交、未发布；宿主固定的 core 版本尚未更新。

## 删除撤回入口（2026-11-23）

按需求方决定删除 `MessageDao.cancel_group_notifications` 与 `TeamBackend.cancel_group_notifications`，`consumed_at` 只在消费时写入。原因：宿主唯一的调用方是整群取消，而群聊页面已没有整群停止入口。测试 `test_pending_notification_counts_as_unread_until_consumed` 改为两条通知都经消费结束。agent_teams 全量 4642 项通过、37 跳过、2 预期失败。

## 通知写入错误处理（2026-11-24）

通知写入重试耗尽后抛出原数据库异常。仅在通知标识已存在，或并发插入发生唯一性冲突且回查到该标识时，返回重复。外键等完整性错误继续向上抛出，Core 不发布广播。

定向回归覆盖 SQLite 主键冲突、真实外键错误，以及注入的数据库锁和连接池超时。修复前，外键与两类重试耗尽用例均失败。修复后，Core 群通知与群聊定向测试 53 项通过，使用本地 Core 的 JiuwenSwarm 自动回复和群轮次测试 72 项通过。PostgreSQL 和 MySQL 未运行实例联调。

## CI 中的 SQLite 驱动兼容（2026-11-24）

CI 用 `pysqlite3` 替换 `sqlite3`，该驱动未导出 `SQLITE_CONSTRAINT_PRIMARYKEY`，导致 DAO 及依赖它的测试模块导入失败。SQLite 分支改为精确匹配当前通知表 `notification_id` 的唯一约束错误，并保留回查确认。

新增隔离子进程用例，模拟缺少该常量的 SQLite 驱动。在干净源码快照复测群通知、群聊、Harness 协议、RSI、奖励和团队记忆，380 项通过、13 项跳过。远端 Linux 全量 UT 尚未复跑。
