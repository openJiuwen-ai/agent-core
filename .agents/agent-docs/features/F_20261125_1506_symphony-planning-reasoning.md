---
id: F_20261125_1506_symphony-planning-reasoning
title: Symphony 在线规划继承模型推理设置
type: F
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
modules: [openjiuwen/symphony/orchestration]
files:
  - openjiuwen/symphony/orchestration/routing.py
  - openjiuwen/symphony/orchestration/planning/fast.py
  - openjiuwen/symphony/orchestration/planning/beam.py
designs: []
design_note: 用户明确了在线规划与构图的模型设置，直接调整已有入口。
---

# 实施与验证

自动路由、Fast 规划和 Beam 最终重排原先用每次调用的参数覆盖宿主设置。移除三处覆盖后，调用方可以在注入模型时选择推理档位。Fast 提示词继续要求严格 JSON。构图本体匹配继续传入关闭参数。[模型契约](../specs/openjiuwen/symphony/orchestration/S_01_planning-reasoning.md)

2026-11-25 在 Swarm 锁定的基线 `31171fb02` 上，使用 Python 3.12 运行 `pytest tests/unit_tests/symphony --asyncio-mode=auto`，1082 项通过。测试捕获自动路由、专家规划、Fast 和 Beam 的模型调用参数，并在同一模型上验证构图和规划的差异。模型端使用测试替身。
