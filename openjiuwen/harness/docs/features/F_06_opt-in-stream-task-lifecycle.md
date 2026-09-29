# F_06 可选流式后台任务生命周期

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-09-28 |
| 状态 | 已实现；验证结果见下文及 Persistent F_03 |
| 范围 | DeepAgentConfig、DeepAgent 单轮流 / abort、ReAct 可选任务接缝、Persistent 管理器 |

## 背景与决策

原生单轮 ReAct 流创建后台模型任务，消费者退出不保证该任务退出。用户要求在 DeepAgent 中增加仅 Persistent 启用的执行分支，个人版默认行为不变。

DeepAgentConfig 增加可选运行时 stream_task_lifecycle 句柄，默认 None；不增加 Agent 类，不改变普通 Spec 的装配，不重写模型循环。Persistent.resolve_parts 在配置应用前注入独立管理器。DeepAgent 仅在句柄存在时将其 scope 传给 ReAct.stream；原生 ReAct 的新参数是可选关键字，不传时保留原路径。

接缝存在时，ReAct 明确关闭内部生成器，并将创建的生产者任务纳入管理器 scope。原生回调的 before / after（per_item、once）/ transform（frame、generator）及 Session 包装器只对 owned stream 向下传递 aclose，默认关闭语义不变。scope 的退出取消并等待生产者完全退出，包括原生取消归档、Session.commit 和 close_stream；重复取消不能中断这段等待。DeepAgent.abort 的可选分支也请求该管理器收敛任务。Persistent 占用直到整个原生流退出才释放。取消不合作时保持占用，不把超时或消费者结束冒充后台完成。

生产者进入正常或异常收尾时调用 begin_finalization，此后只等待、不能再取消其 commit。消费者等待生产者使用 shield，禁止把消费者取消直接传播到正在提交的后台任务。独立审查发现的 commit 时机和 once / generator 关闭漏传均已增加 RED → GREEN 回归。

## 拒绝的方案

- 全局改写原生默认取消语义：违反个人版行为不变要求。
- 扫描 asyncio.all_tasks、全局 task factory 或 monkey-patch 私有字段：容易误杀其他实例任务。
- 只取消消费者或只设置停止标记：不能证明后台模型与 Session 写入已结束。
- 复制 ReAct 模型 / 工具循环：违反原生 DeepAgent 复用约束。

## 验证计划

先用真实 DeepAgent / Session 和本地流模型复现失败，再验证正常流输出、提前关闭、取消期间占用、重复取消、无关闭后迟到写入、不同实例不互相停止。默认构造的句柄须为 None，原生 / 个人版回归一起运行。详细证据同步 Persistent F_03；全仓环境限制单独记录。

实际验证：34 项 binding 测试与构造、协议、原 Harness / Ability、回调和 ReAct 回归合计 593 passed。独立审查的两项 P1 均经失败回归复现并修复。全仓仍有 82 项依赖相关收集错误；test_deep_agent_terminal_stream.py 也因缺 opentelemetry.exporter 无法收集，不声称全仓验收通过。未修改 JiuwenSwarm，未提交 / 推送。

## 边界

本管理器证明其登记的原生模型生产者退出，不证明任意外部工具子进程或第三方副作用收敛；后者仍属 PA-08。不新增宿主授权、提交或可靠事件能力。本次不提交代码。
