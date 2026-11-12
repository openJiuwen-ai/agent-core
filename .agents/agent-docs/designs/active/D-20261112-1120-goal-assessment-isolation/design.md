---
id: D-20261112-1120-goal-assessment-isolation
title: Goal 独立评估的轨迹与执行上下文隔离
slug: goal-assessment-isolation
status: in-development
stage: active
author: alan_cheng
author_email: chengshuo5@huawei.com
reviewers: []
approved-date: 2026-11-12
created: 2026-11-12
updated: 2026-11-12
domain: observability
modules:
  - core/foundation/llm
  - extensions/observability
  - harness
  - agent_evolving/trajectory
tags: [goal, trajectory, observability]
scope: 区分执行 Agent 的 Goal 工作轮与独立评估器模型调用，隔离主上下文链和展示。
type: D
files: []
---

# Goal 独立评估的轨迹与执行上下文隔离

## 背景与确认

用户在会话 web_1a1cda42aee_ae27a226f652 中观察到领导的执行轨迹与 Goal 评估器提示词、JSON 混排。
分析确认：评估器直接调用 model.invoke，沿用领导 subject / turn / step，采集默认 purpose=assistant，
评估输入还推进领导 context.window.commit，形成 47 remove、1 replace、1 insert 的录制窗口变更。
2026-11-12 用户在阅读对话方案后明确要求「按方案实现」。本设计据该真实确认进入开发，未虚构独立 Maintainer 评审。
这是 Goal 执行能力之外的独立观测问题；既有 Team Goal 的调度、次数及状态权威决定继续有效。

## 目标与边界

- Goal 唤起领导工作的真实执行轮仍属于领导轨迹，标明目标与尝试次数。
- 独立评估器属于该领导，但其请求用途、输入和输出独立展示，不是新团队成员。
- 评估调用不能推进执行 Agent 的主上下文窗口，也不能成为消息重建或保留 checkpoint 的执行基线。
- 所有物理模型调用的 token、耗时和失败仍完整保留；执行与评估可分别统计。
- 不改写旧轨迹，不靠提示词文本启发式补造用途；不改变 Goal 管理、评估策略或重试语义。

## 契约与实现分工

core foundation 提供 task-local、不可变、callback-only 的模型观测 scope；purpose 和关联属性不进入供应商请求参数。
评估器绑定 goal_assessment，用 goal id、attempt number、assessment id、strategy 关联一次逻辑评估。
TypeError 兼容重试沿用同一 assessment id，物理调用仍各有 request id；异常或取消退出 scope 必须恢复外层值。
共享采集层识别 assistant / compaction / goal_assessment；保留 owner subject 与 session，不伪造团队成员。
推理子 span 继承同一用途和关联属性。评估输入输出留在原始 inference，独立评估不发主上下文 commit。
执行轮从可信 run_kind / run_context 和 GoalManager 取得目标、尝试次数，传播到其模型调用。
core 的 trajectory 消息重建跳过评估请求，但原始记录不删。

Swarm 生成语义键、归一化字段并在投影前分出评估请求；每个请求从自己的输入起始，不参与执行窗口 diff。
在执行 Agent 下展示独立折叠区，复用既有详情查看 system/input/output/source/OTel，展示模型结论、次数、策略、耗时与 token。
执行模型请求与评估物理请求分别计数，session 总量仍包括全部请求。保留策略维持物理请求编号偏移，但不以评估请求替代执行 lineage。
core 契约在本仓维护，Swarm 只记录其适配责任及依赖条件。

## 取舍

采用用途与窗口隔离，保留真实 Agent 归属。否决前端文本过滤（采集链和回放仍错误），
否决把无工具的临时模型调用伪装为 teammate（误导成员数量与身份），不抑制评估采集（会丢失成本和失败原因）。
历史缺少用途标记的记录按原始事实展示，不自动迁移。

## 验证计划

覆盖真实 callback 采集、评估前后主窗口一致、报告 continue 后下一轮、错误与取消恢复、并发 scope 隔离、
同目标多次评估、消息重建、保留 checkpoint 与实时/归档投影一致；验证浏览器折叠详情及中英文界面。
实际实施与证据维护于 features/F_20261112_1120_goal-assessment-isolation.md，不回填设计。
