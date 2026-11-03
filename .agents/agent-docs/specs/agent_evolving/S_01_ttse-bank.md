---
id: S_01_ttse-bank
title: TTSE 经验库容量与写入契约
type: S
author: xinyang_bali
author_email: 835397263@qq.com
modules: [openjiuwen/agent_evolving/ttse]
files:
  - openjiuwen/agent_evolving/ttse/config.py
  - openjiuwen/agent_evolving/ttse/stores.py
  - openjiuwen/agent_evolving/ttse/index.py
---

# TTSE 经验库容量与写入契约

最近修订：2026-10-31。

## 范围与边界

描述 TTSE FACT/TIP 活跃经验库的容量、写入与 embedding 缓存约束，不定义归纳质量或宿主插件生命周期。

## 不变量

1. FACT 和 TIP 无固定条数上限；新增经验不能因条数超限删除已有经验或丢弃新增经验。
2. `add_fact` / `add_tip` 保留去重：重复规则合并并增加 `count`；`add_record_direct` 不执行在线去重。写入后的 count 排序不用于容量淘汰。
3. 保存、重载和检索索引同步须保留全部活跃经验。显式删除、retire、Auto-dream 合并与清理仍可改变数量。
4. embedding LRU 容量为当前 FACT 条数 + 当前 TIP 条数 + 100；额外空间服务临时查询，缓存淘汰不删除经验记录。

## 接口与数据结构

`TTSEConfig` 不提供 `max_facts` / `max_tips` 参数，调用方须移除旧参数；不使用 0、None 或极大数模拟无上限。
`TTSERecordStore` 继续使用 facts / tips / retired 列表及 JSON 持久化格式。
`TTSEIndex` 的写入包装与 store 的无容量参数写入接口一致。
`consult_top_k`、`consult_max_rules`、`consult_max_chars` 等约束单次返回规模，独立于经验库存储容量。

## 相关规约

本规约属于 agent_evolving 的经验存储层；Harness Rail 生命周期见 `../harness/S_04_rails-contract.md`，本次不改变该生命周期。
