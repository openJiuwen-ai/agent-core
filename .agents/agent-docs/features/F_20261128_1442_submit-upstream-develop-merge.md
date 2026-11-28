---
title: submit 同步 upstream develop 冲突处理
author: "MengHan18"
author_email: "hanmeng22@huawei.com"
designs: []
design_note: 已授权的上游同步，不引入独立新设计
modules: [openjiuwen/harness/tools]
files: [openjiuwen/harness/tools/todo.py, tests/unit_tests/harness/tools/test_todo.py, .agents/agent-docs/specs/harness/S_05_tools-contract.md]
id: F_20261128_1442_submit-upstream-develop-merge
type: F
---

## 范围与决策

将 upstream/develop 合并到 prompt/brief-v4-02-submit，保留现有提交历史；不移动 v5 标签。不是新增提示词精简方案。

- submit 原基线：`dfae07f98231b0645b61542d272a56f517361a2b`。
- 本次 upstream/develop：`4f6a92b48f07da9b483f6eb3d6429c50aa1d3ca1`。

保留 submit 的 TodoWriteTool、revision 冲突检查、内部待办原子持久化及对应测试。上游冲突来自已被替换的旧四工具/SysOperation实现，未将其重新引回新工具。其余上游代码正常合并。文档索引按实际合并后的文档重建，工具契约保留新待办接口。

## 验证

Python 3.11、固定合并源码：todo_write、todo、TaskPlanningRail、prompt contracts 共72项通过。未运行全仓、真实模型或 benchmark。
