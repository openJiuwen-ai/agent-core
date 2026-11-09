---
id: F_20261109_1618_symphony-orchestration-experience
title: Symphony 编排经验配置与写入门控
type: F
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-11-09
updated: 2026-11-09
modules: [symphony]
files: [openjiuwen/symphony/config.py, openjiuwen/symphony/runtime.py, openjiuwen/symphony/runtime_flow.py, openjiuwen/symphony/graph_engine.py, openjiuwen/symphony/flow/engine.py, tests/unit_tests/symphony]
designs: [D-20261109-1603-symphony-orchestration-experience]
---

# Symphony 编排经验配置与写入门控

## 实施

基于 `326b129b0`。用 `OrchestrationExperienceConfig` 承接图观测与能力包蒸馏。有效开关集中在 `OrchestrationConfig.experience_enabled`，`SymphonyRuntime`、图观测写入入口和惰性 Flow 共用。规划按独立开关使用已有观测。默认值和调用契约维护在 `S_01_configuration-contract`。

## 验证

- Python 3.11.15 和 OpenTelemetry SDK 1.45.1。Symphony 全量及对应 Harness Rail 回归共 1279 项通过，其中本机 HTTP 回环用例单独运行。使用确定性模型替身和临时存储验证实际写入、三次合格成功生成候选以及关闭经验后的动态观测读取。
- 配置往返、默认值与导出一致性随 Symphony 测试通过。依赖安装及宿主联测记录由 Swarm feature 维护。
