---
title: Provider 驱动的经验记忆 Rail
id: F_20261118_2106_provider-backed-experience-rail
type: F
author: wanghanru
author_email: wanghanru1@huawei.com
designs: []
design_note: 复用普通记忆 Rail 与 Provider 边界的方案在本次开发前由用户确认。
modules:
  - openjiuwen/harness/rails/memory
files:
  - openjiuwen/harness/rails/memory/jiuwen_memory_rail.py
  - openjiuwen/harness/rails/memory/jiuwen_memory_coding_rail.py
  - openjiuwen/harness/rails/memory/experience_capture.py
  - tests/unit_tests/harness/rails/test_jiuwen_experience_rail.py
---

# Provider 驱动的经验记忆 Rail

日期 2026-11-18。接口见 [S_04 Rails 契约](../specs/harness/S_04_rails-contract.md)。

## 背景与决策

经验同步直接集成到 `JiuwenMemoryRail`，由可选 `sync_experience` 回调启用。
不传回调时保留原来的对话检索、提示词、工具与 sync_turn 流程，不注册 set_task_status、
不采集或提交轨迹。传入回调后，同一 Rail 从工具执行回调采集证据，维护跨结构化中断的
活跃和等待状态，完成后后台提交。Core 不导入具体 agent-memory Provider。

2026-11-20 用户确认将此前独立经验子类合并进现有 Rail，取消新建的经验 Rail 类型与导出。
Swarm 通过现有项目适配类直接继承 JiuwenMemoryRail，仅在启用 SDK 经验时传入 Provider
回调，不再创建第二个经验子类或多重继承。

普通对话同步和经验提交分别执行；经验采集或提交失败均不得阻断普通保存。
状态工具只负责标记，真实工具证据由程序采集。Provider 继续负责轨迹编码、空间与持久化。

拒绝继续保留独立经验 Rail：重复的构造选择和 Swarm 多层继承增加调用链复杂度，
可选回调已能表达关闭与开启两种行为，无需另一个 Rail 类型。

2026-11-22 接入 Coding Rail。两条 Rail 共用经验采集器，Coding Rail 为每个挂载的 Agent
保留独立执行证据，关闭共享 Provider 前排空在途提交。Swarm 的 Coding SDK 装配复用已有
`experience_space` 配置与经验检索工具。

2026-11-24 检视修订。Work 和 Coding 在开始采集和工具回调中隔离经验采集异常，仅记录错误类型；
Work 卸载时先等待在途经验提交，再关闭 Provider。事件循环内调度异步清理，避免阻塞当前线程。
Core 的经验、Work、Coding 与中断回归共 111 项通过，覆盖工具证据序列化异常、关闭顺序、
用同一轨迹 ID 重试，以及挂起轨迹过期和容量淘汰。未运行真实模型提炼。

## 验证与边界

2026-11-22 定向回归：Core 41 项与 Swarm 121 项通过，包含两个 Coding Agent 在同一会话分别提交自己的工具证据。真实模型提炼及新会话检索待联调。

Core 定向验证覆盖等待时零提交、相同会话的独立对话不混入、结构化恢复后单次提交以及普通工具回调保留。当前等待状态保存在 Rail 实例中。普通文本任务关联、重启后恢复和共享后端端到端验证依赖宿主与实际部署。

验证环境：本地 Core 与 Swarm/agent-memory 工作树源码，原 Swarm 虚拟环境依赖，
临时目录中的 pytest-asyncio 0.25.3。验证包括 DeepAgent 公开装配与脚本模型驱动的
工具执行、普通记忆预取/回写、可选经验提交，以及经验失败隔离。
固定依赖安装与真实模型/外部后端联调未执行；Swarm 固定 Core 提交仍需随发布对齐。

2026-11-20 回归：Core 原有普通记忆、交互恢复、Coding 记忆及事件路由 105 项通过，
其中包含当时的 8 项经验行为测试；补充公开 Agent 入口后，经验文件 10 项全部通过，
合计覆盖 107 个不同用例。Swarm 装配及现有记忆回归 64 项通过。另 1 项宿主媒体设置
重建测试在导入 DiscoverySettings 时失败，当前 Core 的 symphony.discovery 未提供
该符号；此模块不在本次改动范围，未以排除后通过替代该失败结论。
