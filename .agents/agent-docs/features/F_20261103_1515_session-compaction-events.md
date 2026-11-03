---
id: F_20261103_1515_session-compaction-events
title: 原生压缩候选与 Server 扩展联动实施
type: F
author: 刘瑜
author_email: liuyu524@huawei.com
modules: [core/context_engine, core/extension]
files: [openjiuwen/core/context_engine/compaction.py, openjiuwen/core/context_engine/context_engine.py, openjiuwen/core/context_engine/context/context.py, openjiuwen/core/extension/runtime.py]
designs: [D-20261103-1156-session-compaction-events]
created: 2026-11-03
---

# 原生压缩候选与 Server 扩展联动实施

## 关联依据与范围

用户于 2026-11-03 确认 [联合设计](../designs/active/D-20261103-1156-session-compaction-events/design.md) 后实施。Core 基于 `45e6418c01302124da6736972eec20e68d0dd4f2`，Server 联调基于 `4b6021ca873e4e3b697628e29b72fdd1eef344d6`。持久契约见 [压缩规约](../specs/context_engine/S_01_compaction.md) 与 [Extension Runtime 调用校验](../../../openjiuwen/core/extension/docs/specs/S_02_registry-and-bus.md#42-当前包-handler-调用有效性)。EventBus 沿用上游 Events S_01。

本特性新增原生 Hook、候选处理、最终安装与缓存，并由 Extension Runtime 为 Server 提供活动包 Handler 的调用校验。Server session.compact / Ops 与 JSONL 处理属于 Server 仓，未下沉到 Core。

## 实际决策与复用

压缩沿用原生和 forked 算法，在隔离的消息、处理器、Session 快照和文件目录内准备候选；最终 USER 决策后才安装。没有采用“先压缩再恢复”、全局 monkeypatch 或共享 prompt 修改。每次 instructions 由局部模型代理加入真实请求；已经发生的模型请求/观测不回滚。

真实 Worker 验证识别了运行轨道使用 forked 独立基类，因此 canonical 与 forked 基类均接入安全 fork；仅复制选中算法，避免强迫旁路 Symphony/自定义处理器加入本次压缩。第三方真正被选中时仍须显式适配，不静默提前提交。

precompute 是内存候选，实际安装重新进入 Hook；会话消息前缀、模型、处理器白名单/配置、记忆和宿主策略不兼容时重算。自动 ADD 保留 pending 一次；GET 保留系统与工具窗口并重算最终窗口。Core 的旧 compress_context 消费方在未注册 Hook 时沿用旧流程。

2026-11-03 经用户确认后，撤回原 EventBus/Ops 身份和排除接口增量；当前运行实现保持最新目标的 EventBus、Ops 与 Builder。调用有效期放在既有 Extension Runtime Handler 包装：检查当前 Ops 对象、嵌套层、Task 和活动标记；Server 仅注入只读校验回调。压缩模块在第二次事件前拒绝同会话重入，匹配链不作额外排除。

## 与方案的关系及限制

延迟状态通过候选副本及原算法实际文件写入隔离，而不是新增另一套摘要算法。选中 Processor 可共享只读模型客户端，不能共享可变计数。文件发布与 Session 保存不是原子事务；未知保存结果冻结目标并要求权威 reload。Server 首期只接原生主会话；子 Agent、外部 Harness 和 Claude 经典 Hook 宿主不在当前交付范围。

## 原提交验证证据（d816a0db / Server 5b495aa）

- 35 项定向验证通过：24 项压缩测试、3 项调用身份/排除测试、8 项 Events 公共契约测试。覆盖 post-next skip、自定义结果、原生文件引用、Session memory 延迟、失败冻结/重载、取消保存收尾、预计算不占锁/尾部复用/模型失效、forked 五类构造，以及传统 Context 调用。
- 真实独立 Front/Supervisor/Worker 使用 Server 安装 ZIP、原 command.compact 与 session.switch，连接配置中的 Qwen3.8-Flash。完成两个真实 query、手动 veto/提交、插件 Ops 提交、自动扩展/提交，以及模型读取新摘要标记；Worker PID/创建时间、退出和端口释放已核对。
- SQLite checkpoint 使用 `__BYTES__:` Base64 的 typed pickle；独立按生产序列化器解码，再用新的 Session/ContextEngine 加载其中的 context，恢复两条消息并读到安装后的标记。修正验收探针的 raw-byte 搜索后，基于最新 Server 装配重新跑完整真实入口，主执行与独立冷恢复复核退出码均为 0；较早失败记录仍保留。
- 本地复核：`D:\work_space\.codex-temp\session-compact-20261103\acceptance-verified.json`；真实入口工件：`real-155942`；运行源文件 SHA256 与复核时一致，ui.render 0 次。未执行的 Claude 宿主或远端 CI 不列为通过。

Core ContextEngine / Events 回归共 792 个不同用例通过：整组 791 项通过，另 1 项默认 import 子进程受 OpenBLAS 内存分配影响，限定该进程 OPENBLAS_NUM_THREADS=1 后单独复验通过。5 项 Windows 基线失败单独排除（4 项早期时间戳转换、1 项符号链接权限），均在干净目标基线复现。证据：core-final.xml、core-import-recheck.xml；未将该整组描述为一次全绿。Server 的完整字段、用户示例和对应回归范围在 Server F_30 / S_17 维护。

## 保持 EventBus 接口的修订

用户在同一任务明确确认替代设计并要求实施。同步当前目标 Core 647c6d9aee4a4d6ae256263220705f17a3837569、Server 23ba7697d8c9f3947b3bc3264209080246a3fd58；保留上游撤回 handler_ids 和 session.start 包 matcher 的结果，不重引入旧接口。

新增 Runtime.is_active_invocation，并复用 _tracked_handler 的包调用计数和活动 Ops；begin_calls/store 原有行为不变。宿主回调只为 Runtime 发布的包 Handler 开放 compact Ops，普通手动/自动入口不依赖这个包校验。当前 38 项 Core 定向、29 项 Server 定向通过。完整 Core ContextEngine / Events / Extension 回归 941 项通过、2 项跳过、7 项基线失败排除：前次已复现的 4 项 Windows 早期时间戳转换、1 项符号链接权限，以及当前干净 647c6d9a 基线复现的 2 项 store 用例（并发锁路径偶发失败、清理后锁文件断言）。没有修改 store 行为；失败记录分别保留在 core-regression.xml、core-store-baseline.xml 和 store-race-baseline.xml，最终排除范围在 core-regression-final.xml。

Server 全组与命令回归 845 项通过、2 项跳过，1 项既有 Token Weather 短路用例排除。真实 Worker / Qwen3.8-Flash 验收重新执行；manual veto/默认摘要、plugin Ops、auto、后续模型读取新摘要、PID 退出和端口释放全部通过，另验证子任务 Ops 拒绝和压缩内同会话重入拒绝。SQLite checkpoint 按生产 typed serializer 解码并由全新 Session/ContextEngine 冷恢复通过。实际源指纹一致，主执行与独立复核均退出 0，ui.render 0 次。

当前修订证据目录：D:\work_space\.codex-temp\session-compact-eventbus-stable-20261103；真实入口 real-165733，复核 acceptance-verified.json，源码指纹 real-final-exit.json。最初启动探针因工作目录错误未启动 Worker，已修正启动位置并完整重跑；原失败工件仍保留。原提交的 792 项和旧真实验收只说明旧版本，不作为当前通过数。

## 交付与重验条件

Core PR 先提供本协议；Server PR 标记依赖具体 Core 提交。任何影响 compaction/context/event dispatch 的运行代码变化都要重验相应边界；纯文档/提交不改变已核实的运行源指纹。批准、实现、测试通过、PR 创建和合入分别记录。
