---
id: B_20261118_0610_db-kv-exclusive-set-atomic-insert
title: DbBasedKVStore.exclusive_set 改为原子的“不存在才插入”
type: B
author: Yanqing-Shi
author_email: toddsyq@gmail.com
modules:
- openjiuwen/core/foundation/store
- openjiuwen/core/session/checkpointer
files:
- openjiuwen/core/foundation/store/kv/db_based_kv_store.py
- tests/unit_tests/core/foundation/store/test_db_based_kv_store.py
- tests/unit_tests/core/session/checkpointer/test_conversation_store.py
designs: []
design_note: 共享会话检视（2026-11-17）指出的 KV 原子性缺陷；修复让实现符合 BaseKVStore.exclusive_set 既有文档契约，接口不变。
---

# DbBasedKVStore.exclusive_set 改为原子的“不存在才插入”

## 现象与影响

`KVSessionConversationStore.create()` 依赖 `exclusive_set` 只创建一次共享记录。SQL 实现先
`SELECT`，再执行冲突时覆盖 value 的 upsert。两个创建者都读到“不存在”时，后到者的 upsert 会在
先到者写入首轮消息之后把记录重置为空窗口，且两者都返回 True。

复现（SQLite，两个引擎同一文件）：第二个创建者停在写语句之前，第一个创建并提交 1 条消息，
放行后记录变为 0 条消息。另：已有行是非对象 JSON（如 `"10"`）时 `data.get` 抛 `AttributeError`。

## 关联设计与当前契约

`BaseKVStore.exclusive_set` 文档写明“Atomically set ... only if the key does not already exist”。
共享记录契约见 core_runtime `S_02` 的 `create(key)` 行，本次补写其对原子性的依赖。

## 根因与修复决策

检查与写入是两条语句，且写入是覆盖式 upsert。修复：

- 先执行一条“不存在才插入”：PostgreSQL / SQLite `ON CONFLICT (key) DO NOTHING`，MySQL
  `INSERT IGNORE`（value 列为 LONGTEXT，IGNORE 只会跳过重复键）。`rowcount == 1` 即成功。
- 未插入时读现有行：非 JSON、非对象、无过期或未过期都返回 False。
- 已过期的条目用 `UPDATE ... WHERE key = :key AND value = :old` 接管，`rowcount == 1` 才成功，
  避免两个调用方同时接管。

`ShelveStore.exclusive_set` 同样没有加锁，本次未改（检视范围外）。

## 回归证据与适用范围

候选：agent-core 工作区（基于 `ed2754c39`）。

- 新增：三方言语句断言（不含 UPDATE）、并发只有一个写入者、过期条目只被接管一次、拒绝覆盖普通值、
  共享记录后到创建不清空首轮消息。修复前 7 项全部失败，修复后通过。
- store / session / context_engine / memory 等 Core 选集 3866 passed / 26 skipped。

未验证：MySQL / PostgreSQL 只做语句编译断言，没有真实数据库执行；条件 UPDATE 的竞争窗口在 SQLite
上不可达（SQLite 自第一条写语句起持有库级写锁）。

## 防错模式与资料同步

“只创建一次”必须由数据库单条语句裁决，不能用读后写。已写入 core_runtime `S_02` 的 `create(key)` 行。

## 遗留与后续

- `ShelveStore.exclusive_set` 的并发安全。
- 设置 `KV_STORE_TEST_URLS` 后可在真实 PostgreSQL / MySQL 上运行 `run_default_kv_store`。
