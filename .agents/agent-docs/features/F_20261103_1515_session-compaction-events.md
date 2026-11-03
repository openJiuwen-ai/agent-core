---
id: F_20261103_1515_session-compaction-events
title: 原生压缩候选与 Server 扩展联动实施
type: F
author: 刘瑜
author_email: liuyu524@huawei.com
modules: [core/context_engine, core/events]
files: [openjiuwen/core/context_engine/compaction.py, openjiuwen/core/context_engine/context_engine.py, openjiuwen/core/context_engine/context/context.py, openjiuwen/core/events/bus.py, openjiuwen/core/events/ops.py]
designs: [D-20261103-1156-session-compaction-events]
created: 2026-11-03
---

# 原生压缩候选与 Server 扩展联动实施

## 关联依据与范围

用户于 2026-11-03 确认 [联合设计](../designs/active/D-20261103-1156-session-compaction-events/design.md) 后实施。Core 基于 `45e6418c01302124da6736972eec20e68d0dd4f2`，Server 联调基于 `4b6021ca873e4e3b697628e29b72fdd1eef344d6`。持久契约见 [压缩规约](../specs/context_engine/S_01_compaction.md)和 Events S_01。

本特性新增原生 Hook、候选处理、最终安装与缓存，并为 Server 提供当前 Handler 身份和单次排除集。Server session.compact / Ops 与 JSONL 处理属于 Server 仓，未下沉到 Core。

## 实际决策与复用

压缩沿用原生和 forked 算法，在隔离的消息、处理器、Session 快照和文件目录内准备候选；最终 USER 决策后才安装。没有采用“先压缩再恢复”、全局 monkeypatch 或共享 prompt 修改。每次 instructions 由局部模型代理加入真实请求；已经发生的模型请求/观测不回滚。

真实 Worker 验证识别了运行轨道使用 forked 独立基类，因此 canonical 与 forked 基类均接入安全 fork；仅复制选中算法，避免强迫旁路 Symphony/自定义处理器加入本次压缩。第三方真正被选中时仍须显式适配，不静默提前提交。

precompute 是内存候选，实际安装重新进入 Hook；会话消息前缀、模型、处理器白名单/配置、记忆和宿主策略不兼容时重算。自动 ADD 保留 pending 一次；GET 保留系统与工具窗口并重算最终窗口。Core 的旧 compress_context 消费方在未注册 Hook 时沿用旧流程。

EventBus 为每个实际 USER/BUILTIN Handler 绑定 handler_id、任务和活动作用域，过期/跨任务/手工构造视图不能冒充当前调用。exclude_handler_ids 与原 matcher/allowlist 共同作用，CORE 尾不被排除。

## 与方案的关系及限制

延迟状态通过候选副本及原算法实际文件写入隔离，而不是新增另一套摘要算法。选中 Processor 可共享只读模型客户端，不能共享可变计数。文件发布与 Session 保存不是原子事务；未知保存结果冻结目标并要求权威 reload。Server 首期只接原生主会话；子 Agent、外部 Harness 和 Claude 经典 Hook 宿主不在当前交付范围。

## 验证证据

- 35 项定向验证通过：24 项压缩测试、3 项调用身份/排除测试、8 项 Events 公共契约测试。覆盖 post-next skip、自定义结果、原生文件引用、Session memory 延迟、失败冻结/重载、取消保存收尾、预计算不占锁/尾部复用/模型失效、forked 五类构造，以及传统 Context 调用。
- 真实独立 Front/Supervisor/Worker 使用 Server 安装 ZIP、原 command.compact 与 session.switch，连接配置中的 Qwen3.8-Flash。完成两个真实 query、手动 veto/提交、插件 Ops 提交、自动扩展/提交，以及模型读取新摘要标记；Worker PID/创建时间、退出和端口释放已核对。
- SQLite checkpoint 使用 `__BYTES__:` Base64 的 typed pickle；独立按生产序列化器解码，再用新的 Session/ContextEngine 加载其中的 context，恢复两条消息并读到安装后的标记。修正验收探针的 raw-byte 搜索后，基于最新 Server 装配重新跑完整真实入口，主执行与独立冷恢复复核退出码均为 0；较早失败记录仍保留。
- 本地复核：`D:\work_space\.codex-temp\session-compact-20261103\acceptance-verified.json`；真实入口工件：`real-155942`；运行源文件 SHA256 与复核时一致，ui.render 0 次。未执行的 Claude 宿主或远端 CI 不列为通过。

Core ContextEngine / Events 回归共 792 个不同用例通过：整组 791 项通过，另 1 项默认 import 子进程受 OpenBLAS 内存分配影响，限定该进程 OPENBLAS_NUM_THREADS=1 后单独复验通过。5 项 Windows 基线失败单独排除（4 项零时间戳转换、1 项符号链接权限），均在干净目标基线复现。证据：core-final.xml、core-import-recheck.xml；未将该整组描述为一次全绿。Server 的完整字段、用户示例和对应回归范围在 Server F_30 / S_17 维护。

## 交付与重验条件

Core PR 先提供本协议；Server PR 标记依赖具体 Core 提交。任何影响 compaction/context/event dispatch 的运行代码变化都要重验相应边界；纯文档/提交不改变已核实的运行源指纹。批准、实现、测试通过、PR 创建和合入分别记录。
