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
updated: 2026-11-04
---

# 原生压缩候选与 Server 扩展联动实施

**2026-11-03 范围收缩：** 用户明确要求本轮只交付 Ops / Event 接口与 Handler 链，主流程接入注释保留，真实压缩暂不接入。生产环境不绑定原生引擎，不拦截原有手动、自动和溢出压缩，不自动调度预计算；默认 Ops 报 COMPACT_NOT_INTEGRATED。以下完整压缩实现和原验收记录仅说明预留机制及历史版本，不能作为当前主流程已启用的结论。


## 关联依据与范围

用户于 2026-11-03 确认 [联合设计](../designs/active/D-20261103-1156-session-compaction-events/design.md) 后实施。Core 基于 `45e6418c01302124da6736972eec20e68d0dd4f2`，Server 联调基于 `4b6021ca873e4e3b697628e29b72fdd1eef344d6`。持久契约见 [压缩规约](../specs/context_engine/S_01_compaction.md) 与 [Extension Runtime 调用校验](../../../openjiuwen/core/extension/docs/specs/S_02_registry-and-bus.md#42-当前包-handler-调用有效性)。EventBus 沿用上游 Events S_01。

本特性新增原生 Hook、候选处理、最终安装与缓存，并由 Extension Runtime 为 Server 提供活动包 Handler 的调用校验。Server session.compact / Ops 与 JSONL 处理属于 Server 仓，未下沉到 Core。

## 实际决策与复用

压缩沿用原生和 forked 算法，在隔离的消息、处理器、Session 快照和文件目录内准备候选；最终 USER 决策后才安装。没有采用“先压缩再恢复”、全局 monkeypatch 或共享 prompt 修改。每次 instructions 由局部模型代理加入真实请求；已经发生的模型请求/观测不回滚。

真实 Worker 验证识别了运行轨道使用 forked 独立基类，因此 canonical 与 forked 基类均接入安全 fork；仅复制选中算法，避免强迫旁路 Symphony/自定义处理器加入本次压缩。第三方真正被选中时仍须显式适配，不静默提前提交。

precompute 的候选机制保留。完整接入版本曾验证自动 ADD/GET；当前这些调用点及传统 compress_context 的重定向均已注释，即使注册 Hook，原入口仍走旧流程。显式 compact_context 作为独立宿主 API/测试入口保留，生产 Server 不绑定引擎、不调用该 API。

2026-11-03 经用户确认后，撤回原 EventBus/Ops 身份和排除接口增量；当前运行实现保持最新目标的 EventBus、Ops 与 Builder。调用有效期放在既有 Extension Runtime Handler 包装：检查当前 Ops 对象、嵌套层、Task 和活动标记；Server 仅注入只读校验回调。压缩模块在第二次事件前拒绝同会话重入，匹配链不作额外排除。

## 与方案的关系及限制

延迟状态通过候选副本及原算法实际文件写入隔离，而不是新增另一套摘要算法。选中 Processor 可共享只读模型客户端，不能共享可变计数。文件发布与 Session 保存不是原子事务；未知保存结果冻结目标并要求权威 reload。Server 首期只接原生主会话；子 Agent、外部 Harness 和 Claude 经典 Hook 宿主不在当前交付范围。

## 原提交验证证据（d816a0db / Server 5b495aa）

- 35 项定向验证通过：24 项压缩测试、3 项调用身份/排除测试、8 项 Events 公共契约测试。覆盖 post-next skip、自定义结果、原生文件引用、Session memory 延迟、失败冻结/重载、取消保存收尾、预计算不占锁/尾部复用/模型失效、forked 五类构造，以及传统 Context 调用。
- 真实独立 Front/Supervisor/Worker 使用 Server 安装 ZIP、原 command.compact 与 session.switch，连接配置中的 Qwen3.8-Flash。完成两个真实 query、手动 veto/提交、插件 Ops 提交、自动扩展/提交，以及模型读取新摘要标记；Worker PID/创建时间、退出和端口释放已核对。
- SQLite checkpoint 使用 `__BYTES__:` Base64 的 typed pickle；独立按生产序列化器解码，再用新的 Session/ContextEngine 加载其中的 context，恢复两条消息并读到安装后的标记。修正验收探针的 raw-byte 搜索后，基于最新 Server 装配重新跑完整真实入口，主执行与独立冷恢复复核退出码均为 0；较早失败记录仍保留。
- 本地复核：`D:\work_space\.codex-temp\session-compact-20261103\acceptance-verified.json`；真实入口工件：`real-155942`；运行源文件 SHA256 与复核时一致，ui.render 0 次。未执行的 Claude 宿主或远端 CI 不列为通过。

Core ContextEngine / Events 回归共 792 个不同用例通过：整组 791 项通过，另 1 项默认 import 子进程受 OpenBLAS 内存分配影响，限定该进程 OPENBLAS_NUM_THREADS=1 后单独复验通过。5 项 Windows 基线失败单独排除（4 项早期时间戳转换、1 项符号链接权限），均在干净目标基线复现。证据：core-final.xml、core-import-recheck.xml；未将该整组描述为一次全绿。Server 的完整字段、用户示例和对应回归范围在 Server F_28 / S_15 维护。

## 保持 EventBus 接口的修订

用户在同一任务明确确认替代设计并要求实施。同步当前目标 Core 647c6d9aee4a4d6ae256263220705f17a3837569、Server 23ba7697d8c9f3947b3bc3264209080246a3fd58；保留上游撤回 handler_ids 和 session.start 包 matcher 的结果，不重引入旧接口。

新增 Runtime.is_active_invocation，并复用 _tracked_handler 的包调用计数和活动 Ops；begin_calls/store 原有行为不变。宿主回调只为 Runtime 发布的包 Handler 开放 compact Ops，普通手动/自动入口不依赖这个包校验。当前 38 项 Core 定向、29 项 Server 定向通过。完整 Core ContextEngine / Events / Extension 回归 941 项通过、2 项跳过、7 项基线失败排除：前次已复现的 4 项 Windows 早期时间戳转换、1 项符号链接权限，以及当前干净 647c6d9a 基线复现的 2 项 store 用例（并发锁路径偶发失败、清理后锁文件断言）。没有修改 store 行为；失败记录分别保留在 core-regression.xml、core-store-baseline.xml 和 store-race-baseline.xml，最终排除范围在 core-regression-final.xml。

Server 全组与命令回归 845 项通过、2 项跳过，1 项既有 Token Weather 短路用例排除。真实 Worker / Qwen3.8-Flash 验收重新执行；manual veto/默认摘要、plugin Ops、auto、后续模型读取新摘要、PID 退出和端口释放全部通过，另验证子任务 Ops 拒绝和压缩内同会话重入拒绝。SQLite checkpoint 按生产 typed serializer 解码并由全新 Session/ContextEngine 冷恢复通过。实际源指纹一致，主执行与独立复核均退出 0，ui.render 0 次。

当前修订证据目录：D:\work_space\.codex-temp\session-compact-eventbus-stable-20261103；真实入口 real-165733，复核 acceptance-verified.json，源码指纹 real-final-exit.json。最初启动探针因工作目录错误未启动 Worker，已修正启动位置并完整重跑；原失败工件仍保留。原提交的 792 项和旧真实验收只说明旧版本，不作为当前通过数。

## 交付与重验条件

Core PR 先提供本协议；Server PR 标记依赖具体 Core 提交。任何影响 compaction/context/event dispatch 的运行代码变化都要重验相应边界；纯文档/提交不改变已核实的运行源指纹。批准、实现、测试通过、PR 创建和合入分别记录。

## 文档编号修订

2026-11-03 按用户要求接续 Server 当前目录，compact 特性文档改为 F_28_session-compact.md、规约改为 S_15_session-compact.md，原 F30/S17 为历史别名。Core 本文的日期编号、ContextEngine S_01 和设计 ID 保持稳定；此次仅同步跨仓引用，不改变压缩实现及原版本验收事实。

## 当前接口阶段实施与验证

按用户“先把主流程接入部分注释掉”及“真实压缩暂不接入”的明确要求，局部替代原完整接入范围：Core 的 Engine.compress_context、Context.compress_context 和自动 ADD/GET 重定向已注释；原算法保持原调用路径。保留显式 API、Hook、候选准备/校验/提交机制与 Extension Runtime 只读调用校验，供 Server 独立 Ops/Event 测试及后续主流程接入使用，不改 EventBus 接口。

Core 31 项定向与 139 项原生 ContextEngine/ContextModel/FullCompact 回归通过，共 170 个不同用例。验证注册 Hook 后原生 ADD/GET/两个 legacy 压缩入口仍不触发 Hook，显式 API 的候选、post-next skip、安全边界仍可独立使用。Server 85 项回归加实际安装包调用 1 项通过，共 86 项；默认活动 Ops 报 COMPACT_NOT_INTEGRATED，测试显式绑定后完整 USER→BUILTIN→CORE 链返回结果。证据在 `D:\work_space\.codex-temp\session-compact-interface-only-20261103` 的 core-interface-final.xml、core-native-regression.xml、server-interface-final.xml、server-installed-package.xml。

本轮未启用生产真实压缩，未重跑真实模型/SQLite 压缩恢复；上文原完整接入版本的证据仅保留历史含义。早期测试启动遗漏 asyncio_mode 及两个新增断言不准确的失败工件保留，修正后受影响范围全部通过。恢复生产入口后需重新做主流程和持久化验收，远端 CI 不作为本地通过证据。


## 2026-11-04 目标分支冲突适配

用户要求先修复 Core !125 与目标分支的冲突，再联合验证和重提 Server PR。目标为 6b36731b3a83ca5a3520a4d31698b1c16c6df3fb，新增 State 依赖追踪与包代次退休。唯一代码冲突在 Extension Runtime：保留目标分支的 _ops_generations / wait_generation_idle / retire_generation 和三参数 _tracked_handler，同时保留本 PR 的 Task/当前层调用帧；两类状态在同一个 finally 中成对释放，不回退 State 的热切换和清理逻辑。

更新原调用帧测试的包代次参数，补充嵌套 7/8 两代、当前层权限恢复、各代空闲等待互不提前结束的回归。当前 Core 210 项定向（压缩接口、原生 ContextEngine/ContextModel/FullCompact、调用帧及 State）通过；Registry、包管理事件与 Bus 发布另 31 项通过，共 241 个不同用例。证据：D:\work_space\.codex-temp\session-compact-reissue-20261104 的 core-focused-first.xml 与 core-management.xml。未将历史完整主流程/真实模型证据当成本轮重新验证。

主流程停用范围不变：传统手动压缩、ADD/GET 自动压缩和溢出恢复不重定向到本新增 Hook；显式 compact_context 及类型导出是此 PR 提供的接口。Server 在 Core !125 未合入时不能无条件导入这些类型。Server 重提将同时验证“当前目标 Core 未导出这些类型”与“本 PR 已导出”的两种组合，接口层提供兼容边界，不靠注释业务调用规避模块导入问题。


## 2026-11-04 联合验证与 Server 重提

Core 冲突修复已在本 PR 推送，运行实现提交为 b21e5d7b9394abc5369faefc368ae1ec23798a5f；后续本节只补文档，不改变运行代码。重新提交 [Server !116](https://gitcode.com/huenrui_/jiuwenswarm/pull/116)，替代已经回退的 Server !109。Server 通用层不无条件导入 Core 新压缩类型，仅显式 bind_native 才核对能力和注册 Hook；旧目标 Core 仍可启动并得到明确未接入结果。

两仓当前组合的 Server 定向 90 项通过，包含实际 Ops→Core→Server Event→默认 continuation→候选后置 skip / 放行提交，以及原 legacy 入口不触发新 Hook；这些使用受控模型，不代表生产接入已开启。Server 配当前未合入接口的 Core 目标，87 项通过、3 项原生桥接测试跳过。Server 全组 982 通过、2 平台跳过，3 项用量/渲染既有失败在干净相同基线复现，未称整组全绿。

两种 Core 都通过真实非 stub Front/Supervisor/Worker 的 AGENT_READY、ZIP 安装、session.create/switch→attach USER→Ops 默认 NOT_INTEGRATED，记录实际加载路径及 PID/创建时间，并确认真实退出和端口释放。使用占位模型配置，未发送模型 query，ui.render 为 0。证据与完整字段/调用链由 Server F_28/S_15 第 6 节及 D:\work_space\.codex-temp\session-compact-reissue-20261104 维护。Core 传统入口的注释/停用范围继续保持，未把旧真实模型/SQLite 证据计入本轮通过。
