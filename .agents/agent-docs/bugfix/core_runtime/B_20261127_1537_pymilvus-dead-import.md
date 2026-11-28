---
id: B_20261127_1537_pymilvus-dead-import
title: 删除 inmemory checkpointer 的未使用 pymilvus 导入
type: B
author: Huazijiayou
author_email: yaohua@huawei.com
modules:
  - openjiuwen/core/session/checkpointer
files:
  - openjiuwen/core/session/checkpointer/inmemory.py
designs: []
design_note: 单行未使用导入删除，无独立设计文档。
---

# 删除 inmemory checkpointer 的未使用 pymilvus 导入

## 现象

`import openjiuwen.core.session.checkpointer.inmemory` 会把 `pymilvus` 及其 `pandas`
依赖拉入进程，即使部署不使用向量库：启动导入耗时约 10.5 秒，启动 RSS 约增加 68~90MB。
OpenAI-only、无向量库的部署被迫承担该开销。

复现：

```text
python -c "import sys; import openjiuwen.core.session.checkpointer.inmemory; print('pymilvus' in sys.modules, 'pandas' in sys.modules)"
# 修复前：True True
# 修复后：False False
```

## 根因与修复

`inmemory.py` 第 5 行 `from pymilvus.client.utils import is_successful` 为未使用导入，
`is_successful` 在该文件中没有任何引用。删除该行；如后续确实需要，
应改为函数内导入，避免影响模块导入链。

## 验证

- 上述复现命令在修复前后对比；
- checkpointer 相关单测通过（CI）；
- 启动导入耗时与 RSS 前后各采样 5 次（调用方本地环境：导入约 10.5s → 约 1s 量级，
  RSS 约 -68~90MB，与部署环境相关）。

## 兼容性

纯删除未使用导入，无行为变化；不新增依赖。
