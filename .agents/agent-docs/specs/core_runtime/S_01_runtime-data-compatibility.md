---
id: S_01_runtime-data-compatibility
title: 运行时身份、持久化与流式结束兼容规约
type: S
author: alan_cheng
author_email: chengshuo5@huawei.com
modules:
  - openjiuwen/core/session
  - openjiuwen/core/context_engine
  - openjiuwen/core/controller
  - openjiuwen/core/runner/drunner/dmessage_queue
  - openjiuwen/extensions/a2a
files:
  - openjiuwen/core/session/session_controller/scope.py
  - openjiuwen/core/session/session_controller/chain_session.py
  - openjiuwen/core/session/vcs/models.py
  - openjiuwen/core/session/vcs/delta.py
  - openjiuwen/core/session/vcs/adapter.py
  - openjiuwen/core/context_engine/context_engine.py
  - openjiuwen/core/controller/modules/intent_recognizer.py
  - openjiuwen/core/runner/drunner/dmessage_queue/message_serializer.py
  - openjiuwen/core/runner/runner_config.py
  - openjiuwen/core/runner/drunner/dmessage_queue/message_queue_factory.py
  - openjiuwen/core/runner/drunner/dmessage_queue/message_queue_fake.py
  - openjiuwen/extensions/message_queue/message_queue_pulsar.py
  - openjiuwen/extensions/a2a/a2a_agent_executor.py
---

# 运行时身份、持久化与流式结束兼容规约

## 1. 持久化身份与恢复

- `GroupUserSubject` 的历史 `group:{group_id}:user:{user_id}` key 按字面量解析，`%2F/%41/%3A` 不解码。
  无冒号 ID 继续写原格式；含冒号的 ID 写 `group-user-v2:{encoded_group}:user:{encoded_user}`，
  仅该前缀启用百分号解码。`SessionScope.from_string` 识别两种格式。
- 下游关系的权威标识是 `.link` payload 中的 `target_agent/target_session`。
  新文件名为 `v2-<sha256(JSON([agent, session]))>.link`，避免分量下划线碰撞和路径分隔符进入文件名。
  旧文件仍按 payload 优先、文件名回退读取；flush 写新文件后清理旧文件。
- `StateDelta.path_encoding` 缺省为 1，沿用旧的点号分割，不解码 `~0/~1`；
  新 `diff_state` 输出版本 2，对分量内的 `~` 和 `.` 分别编码为 `~0` 和 `~1`。
  版本 1 序列化时不输出该字段，保持旧 WAL CRC 输入不变；未知版本拒绝解析。
- VCS 快照恢复只删除目标快照中不存在的 context；已有实例执行 `load_state`，保留 processor、
  token counter 和调用方引用。`ContextEngine.list_context_ids(session_id)` 只枚举该会话，不保存状态。
  缺失实例通过引擎默认配置创建；快照不包含可恢复的自定义运行配置。

## 2. 任务与 A2A 结束

- CREATE_TASK 省略 `target_task_id` 时，在创建 Task 的边界生成 UUID，并统一用于 `task_id/context_id`；
  显式指定的非空 ID 保留。任务须真实进入 TaskManager，而不是仅能构造 Intent。
- A2A `last_chunk` 属于单个 artifact。终态结果中的每个 artifact 都标记完成。
  流式执行按 artifact ID 跟踪未结束产物；纯 status 终态及自然耗尽时先发空 parts、
  `append=True,last_chunk=True` 的结束事件，再发任务终态；已有内容不被替换。
  跟踪集合属于单次流式调用，不跨请求共享。缺少 artifact ID 时分配 ID。

## 3. MQ 版本与滚动升级

- `serialize_message(..., wire_version=1)` 为默认行为，继续输出旧格式；新版 reader 不根据
  `__dsh_escaped__` 业务键前缀去掉任何字符。可被旧格式表达的载荷支持旧写新读、新写旧读。
- 原始字面量格式的旧 reader 无法表达普通字典里的 `__class__` 或 `__type__="datetime"`。默认 writer
  遇到这些冲突时抛出 `ValueError`，禁止静默改键或伪装成可兼容格式。
- `wire_version=2` 显式写 `DmqWireEnvelope/version=2`；普通字典使用带类型标识的键值对容器，
  不修改业务键。嵌套模型和 datetime 保留类型。reader 自动识别 envelope，未知版本拒绝读取。
- `MessageQueueConfig.wire_version` 贯穿队列工厂、FakeMQ 和 Pulsar 的生产者；
  `legacy_key_encoding` 为 `literal`（默认）或 `escaped`，同时决定 v1 写入及无版本消息的读取规则。
  `escaped` 精确兼容 upstream `69e0ce1b0` 的转义格式：写入时对保留键和已有前缀多加一层
  `__dsh_escaped__`，读取普通字典时仅去掉一层；有版本的 v2 信封始终按 v2 读取，不受该设置影响。
- 与原始 reader 通信用默认 `wire_version=1, legacy_key_encoding="literal"`；
  与 upstream 的转义 reader/writer 通信用 `wire_version=1, legacy_key_encoding="escaped"`。
  同一无版本队列中的历史消息必须采用已知的统一编码；两种格式不能通过键内容可靠自动识别。
- 全部接收端升级后用 `MessageQueueConfig(wire_version=2, legacy_key_encoding=...)` 启用 v2；
  `legacy_key_encoding` 继续按队列中尚存的无版本消息选择。直接构造 `FakeMQ`、`MessageQueuePulsar`
  或调用 `MessageQueuePulsar.start_with_retry` 时提供同名关键字；Pulsar 回退 FakeMQ 保留两项配置。
- 必须先升级接收端，再由调用方选择 v2；内置队列不自动协商、不自动切换 writer。
  v2 对旧 reader 不兼容，应明确失败，不能承诺滚动升级期间任意保留键都能透传给旧 reader。

配置示例（确认所有接收端支持 v2，历史队列消息采用转义格式）：

```python
MessageQueueConfig(
    type="pulsar",
    pulsar_config=PulsarConfig(url="pulsar://localhost:6650"),
    wire_version=2,
    legacy_key_encoding="escaped",
)
```

## 4. 支持边界与验证入口

未版本化但已经做过转义的数据无法仅靠内容与旧字面量无歧义地区分，不能自动猜测迁移；
必须按部署或队列的既有编码显式选择 `legacy_key_encoding`，不能混入另一种无版本格式。
新 WAL 和含冒号的新版 scope key 不保证旧程序可读；已有原始历史数据保持新程序可读。
`.link` 多文件写入沿用既有 flush 行为，不新增跨文件事务或多 writer 保证。

验证入口：`test_scope.py`、`test_link_filename_collision.py`、`test_review_compatibility.py`、
`test_intent_optional_target.py`、`test_artifact_completion.py`、`test_message_serializer_versions.py`。
实施依据与证据见 [修复记录](../../bugfix/core_runtime/B_20261103_1050_grandocean-review-feedback.md)。
