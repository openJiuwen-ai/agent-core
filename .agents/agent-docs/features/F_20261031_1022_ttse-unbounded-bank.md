---
id: F_20261031_1022_ttse-unbounded-bank
title: 移除 TTSE 经验库条数上限
type: F
author: xinyang_bali
author_email: 835397263@qq.com
modules: [openjiuwen/agent_evolving/ttse]
files:
  - openjiuwen/agent_evolving/ttse/config.py
  - openjiuwen/agent_evolving/ttse/stores.py
  - openjiuwen/agent_evolving/ttse/index.py
  - tests/unit_tests/agent_evolving/ttse/test_ttse_core.py
  - tests/unit_tests/agent_evolving/ttse/test_ttse_dream.py
  - tests/unit_tests/harness/rails/evolution/test_ttse_rail.py
designs: []
design_note: 用户明确要求取消 max_facts 和 max_tips 上限，属于局部配置和写入策略调整，无独立设计。
---

# 移除 TTSE 经验库条数上限

日期：2026-10-31。当前契约：[S_01_ttse-bank](../specs/agent_evolving/S_01_ttse-bank.md)。

## 背景与决策

原 FACT/TIP 各默认最多 400 条，写入后按 count 排序截断，可能立即丢弃低 count 的新经验。
根据用户要求删除这两个配置字段，以及普通写入和 dream 直接写入路径中的数量截断。
同步调整 `TTSEIndex` 对 `_add` 的包装签名。保留去重、排序、retire、显式删除与 Auto-dream 清理。
该决策独立于已有经验检索提示词和插件生命周期变更。

embedding 缓存原来依赖两项配置的总和，现改为当前活跃经验数加 100 个临时查询位置。
没有改成无限缓存，以免无关查询文本无限积累；也没有保留固定 900 条缓存，因为经验库增长后会反复淘汰经验向量。
未采用极大容量值或特殊哨兵值：字段直接移除，旧构造参数须由调用方删除。

## 验证

新增 FACT/TIP × 普通/直接写入的四组回归：从 400 条开始新增低 count 规则，核对内存、JSON 和重载均保留全部 401 条；普通写入还验证重复合并。
调整 embedding LRU 测试，覆盖容量溢出淘汰和经验增长后缓存扩容；shared_store 测试改用 dedup_threshold 验证首份配置生效。

在本仓 `.venv` 激活、`PYTHONPATH` 为仓库根、`PYTHONUTF8=1` 后执行：

```text
python -m pytest -o addopts='' -q --tb=short --show-capture=no tests/unit_tests/agent_evolving/ttse tests/unit_tests/harness/rails/evolution/test_ttse_rail.py tests/unit_tests/harness/rails/evolution/test_ttse_close.py
```

首次运行暴露索引包装残留 cap 参数；同步修复后完整集得到 211 passed、1 failed。
剩余失败来自新增缓存断言：索引写入本身会缓存新规则向量，随后额外查询正常淘汰最旧查询。
将保留断言放在规则写入后，并校验后续查询淘汰后的保留项，定向重跑该用例得到 1 passed。
合计 212 项已通过验证；两次运行均有 2 条已有 Pydantic 弃用警告。未执行静态检查或格式化。

## 已知边界

未做大规模经验库性能测试。取消条数上限后内存、JSON 文件和线性去重开销随经验数增长。
单次检索数量和字符预算不变；本次未修改宿主侧配置，外部调用方若传旧参数需同步移除。
