# F_03 执行绑定与调用隔离

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-09-29 |
| 负责人 | yan_zj |
| 状态 | PA-03 实例内绑定与单轮原生调用隔离已通过定向验收；PA-04 正式执行入口及其 task-loop / 子代理集成仍待开发 |
| 范围 | 内部 execution/binding.py、构造绑定接缝和隔离测试；不实现 PA-04 业务事件执行闭环 |
| 规约 | S_01；开发计划 PA-03 |

编号说明：本文保留开发当时的历史 PA 编号；与当前计划对照时，旧 PA-22 对应 PA-04，旧 PA-04～PA-21 对应 PA-05～PA-22，其余编号不变。旧版架构内容可从 Git 历史查阅。

## 设计与范围

沿用原生 DeepAgent，不新增 Agent 类或门面，不 monkey-patch 原生方法。Persistent 内部 invoke / stream 接缝在进入原生 API 前校验原构造的 Agent、Session 对象、完整 ExecutionContext、端口及 owner；传给原生 API 的 Session 固定为构造 Session。内部接口不作外层公开导出，PA-04 后续组合这些函数。Persistent.resolve_parts 注入 NativeStreamTaskLifecycle；普通 DeepAgentConfig 默认句柄为 None，个人版不启用此分支。

每个构造绑定持有一个调用私有状态：活动任务 / 事件循环、停止标记、关闭标记、活动调用占用。占用使用原绑定锁做短时原子检查，不持有线程锁 await；同一实例并发调用立即拒绝，不充当宿主 Session 调度器。不同实例可以并行；错误、取消及显式关闭仅影响原绑定。单轮流的生产者通过原生接缝登记，流退出须取消或等待该生产者及原生 Session 收尾，不能以消费者退出作为后台执行结束的证明。

构造完成时保存原配置对象、模型对象、AbilityManager 和关键声明值快照；替换配置、模型、owner、Card 或修改关键声明均在下一次调用前拒绝。不把当前 live config 重新计算成启动 Spec 指纹；宿主配置版本 / 指纹已由完整 ExecutionContext 固定。任意可信 provider 的内部状态及直接调用 DeepAgent API 不在适配的防篡改边界内。

本步停止只设置此调用停止标记，取消其活动 asyncio task 并调用原生 abort；不实现 ControlPort 鉴权、审批、终态提交或工具子进程收敛。活动任务没有真正退出时不能清理工具或宣称关闭。显式资源释放只在无活动调用时进行，仅移除此 owner 的工具，不关闭宿主 Session、清空消息或删除文件。

## 顺序与验收

1. 新增失败测试：错配在原生执行前拒绝；同实例并发拒绝；不同 Session 原生消息 / 工具状态隔离；停止及清理只影响目标；配置漂移拒绝。
2. 实现绑定占用与原生 invoke / stream 接缝，接入构造快照；不调用授权、预算或提交端口。
3. 补流提前关闭、异常释放、跨循环及关闭后重用边界；运行前三步与默认 Harness 定向回归。
4. 独立审查；同步 S_01 和开发计划，准确记录未接入的可靠执行能力。

## 执行记录

Pre-flight: F_01 的绑定提供原 Session / Execution / owner；F_02 的协议不提供实际事件执行。PA-03 只准备原生 API 的隔离接缝，PA-04 消费它，不另建模型循环。

Ruling: 保留原生 API，不给个人版加全局检查 — 用户要求个人版不变；绕过 Persistent 接缝直接调用原生 API 不获得这些保护，正式可靠执行入口须待 PA-04。

Ruling: 停止不冒充已收敛 / 已取消 — 工具进程与外部效果核对留 PA-04 / PA-08；若活动任务不退出，关闭必须拒绝并保留资源。

本步不提交代码。

阶段记录：18 项新增测试先因缺少执行接缝失败，再通过；补充工具及循环重用测试（测试夹具补齐真实 Session Card / ToolCall type）。再以 3 项 RED 复现关闭与 abort 竞争、停止后的正常返回及调用中归属变化，已补控制进行标记与返回时复核。

独立审查发现 P1：原生 ReAct 单轮流会创建不受 DeepAgent.abort 管理的后台模型任务，消费者关闭后任务仍可能写 Session。原提前关闭测试缺少 model.stream，实际消费的是错误结果，不能作为成功流验证。已删除该错误验收，以真实原生 Agent 的流入口哨兵测试先 RED，随后禁止内部流接缝启动原生任务后 GREEN。此次裁定不修改原生 ReAct / DeepAgent，不通过全局任务扫描或私有字段补丁规避问题；流式完成、取消及资源关闭证明列为 PA-04 放行前置，不能将本步描述为原生流隔离完成。

## 验证记录

- 定向回归：192 passed，2 项已有 Pydantic 弃用警告；25 项新绑定测试与前两步 167 项回归一起运行。

```powershell
.venv/Scripts/python.exe -m pytest -q tests/unit_tests/harness/persistent_agent tests/unit_tests/harness/test_leaf_spec_build.py tests/unit_tests/harness/test_spec_ownership.py tests/unit_tests/harness/test_factory_model_anomaly_detection_rail.py tests/unit_tests/harness/test_deep_agent_cold_build.py tests/unit_tests/harness/test_deep_agent_workspace.py tests/unit_tests/core/single_agent/test_ability_manager_add_ability.py --tb=short --show-capture=no
```

- 初版 5 个 Python 文件 Ruff check / format --check 通过，git diff --check 无空白错误，仅仓库 CRLF 提示；补齐后的检查结果见下文。
- 全仓实际运行 pytest -q --tb=short，仍被 82 个收集错误阻断、15 skipped；例：tests/cli/integration/test_repl_dispatch.py 缺 prompt_toolkit，tests/unit_tests/harness/observability/test_rail.py 及多项 RSI / observability 测试缺 opentelemetry.exporter。未运行全仓测试用例，不声称全仓通过，不安装额外依赖或修改无关模块。
- 初版未修改共享 Harness。后续经用户明确确认新增默认关闭的共享原生接缝，保留个人版默认路径；JiuwenSwarm 工作区未修改，架构设计文件本步未修改，未提交 / 推送。

## 未实现 / 下一步

2026-09-28 补齐设计（用户已确认并实现）：DeepAgentConfig 默认不设置 stream_task_lifecycle，个人版保持旧路径；Persistent.resolve_parts 注入独立生命周期管理器，DeepAgent 的可选分支将管理器传给原生 ReAct，退出时取消并等待后台及其 Session 清理结束，再释放 Persistent 占用。共享接缝设计见 [F_06](../../../docs/features/F_06_opt-in-stream-task-lifecycle.md)；不新增 Agent 类，不扫描其他任务、不重写模型循环。

独立审查补齐版本发现两项 P1，均以真实 Agent / Session 回归先 RED 再修复：正常 stream_iterator 结束后直接 await 生产者会在取消时打断 commit，改为 shield 并在原生收尾开始标记 finalizing，收尾只等待不二次取消；emit_after 的 once 和 transform_io 的 generator 分支未向下传递关闭，补齐仅 owned stream 启用的 aclose 传播。正常 / 模型异常下 commit 期间取消、三种回调模式提前关闭、重复取消、跨实例停止、无关闭后迟到写入均纳入测试。

补齐版本历史验证：34 项 binding 测试、前两步 90 项、原 Harness / Ability 78 项、原生回调及 ReAct 391 项合计 **593 passed**，2 项已有 Pydantic 弃用警告。另一次包含 test_deep_agent_terminal_stream.py 的定向运行因该文件依赖的 opentelemetry.exporter 缺失而收集失败；完整 pytest 实际运行同样 82 项收集错误、15 skipped（还缺 prompt_toolkit 等），未声称全仓通过或修改环境。共享大文件既有导入顺序告警不做无关格式化。

2026-09-29 当前验收：`tests/unit_tests/harness/persistent_agent` **132 passed**；包含该包的共享 Harness 定向回归 **582 passed**；`tests/unit_tests/agent/react_agent` **136 passed**。改动范围 Ruff check、format --check 通过。实例级绑定、Session 错配拒绝、同实例串行占用、取消 / 关闭归属及 Persistent 专用原生流生产者收敛现已覆盖。默认 ReAct 分支单独回归通过；不据此宣称完整全仓测试或正式业务执行入口通过。

审查范围裁定：task-loop / 惰性子代理的正式入口验收、宿主授权 / 预算 / 提交、任意外部进程收敛及全仓依赖修复仍留后续阶段，不因上述单轮流回归通过而宣称完成。默认 DeepAgent 的实际正常流验证及原生回归已经执行。

- PA-04 组合内部接缝，提供 execute_persistent_request 正式入口，映射原生输出为 AgentEvent，接授权、预算、控制及确认；通过该入口的 task-loop / 惰性子代理端到端隔离也在此联调。
- 单轮流式生命周期已接入；曾采用的失败关闭措施已移除。普通 invoke_bound 不模拟流或另建模型循环。依赖用户 / 宿主显式关闭流；不把遗弃但未关闭的生成器当作已收敛。
- task-loop 与惰性子代理通过正式入口的端到端隔离随 PA-04 验证；本步普通单轮调用测试不推广为这些全链路保证。
- 模型 / Rail / 自定义 provider 内部是可信句柄，本步不是任意 Python 扩展的沙箱；原生 API 仍可直接调用，但不获得 Persistent 隔离和可靠性保证。
- 原生 abort 与 asyncio 任务退出不证明任意工具子进程 / 外部效果已收敛。PA-08 / 宿主核对之前，不宣称业务取消或队列释放。
