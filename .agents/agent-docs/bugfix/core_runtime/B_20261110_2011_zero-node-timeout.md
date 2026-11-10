---
id: B_20261110_2011_zero-node-timeout
title: 工作流节点零超时误触发立即超时
type: B
author: 羿寒 王
author_email: 2127132571@qq.com
modules:
- openjiuwen/core/graph
files:
- openjiuwen/core/graph/vertex.py
- tests/unit_tests/core/graph/test_vertex_retry.py
designs: []
design_note: 恢复 NodeSpec 与 add_workflow_comp 已声明的非正数不设超时契约。
---

# 工作流节点零超时误触发立即超时

关联 Issue：[GitHub #2278](https://github.com/openJiuwen-ai/agent-core/issues/2278)。

## 现象与根因

`NodeSpec.timeout` 和 `Workflow.add_workflow_comp()` 约定 `timeout <= 0` 不设节点执行超时。`Vertex._run_executable_with_retry()` 却只排除负数，把零传入 `asyncio.wait_for()`，导致健康节点在开始执行前即触发超时；开启重试后仍重复失败。

## 修复

将节点超时判断从 `timeout < 0` 改为 `timeout <= 0`。零与负数均直接执行组件；正数继续使用原有超时和重试路径。

## 验证

环境为 Python 3.11.9，依赖使用仓库锁定版本。测试运行真实 `Vertex.call()` 和节点配置，以本地异步组件替代外部业务。

- 原有定向基线：25 项通过。
- 新增八个参数化场景：负数、零、正数的正常执行，以及正数超时；分别覆盖关闭和开启重试。
- 修复前新增场景：2 项失败、6 项通过；两个失败均为零超时。
- 修复后图执行与工作流回归：183 项通过。

```bash
python -m pytest -o addopts= -p no:cacheprovider tests/unit_tests/core/graph tests/unit_tests/core/workflow -q --tb=short
```
