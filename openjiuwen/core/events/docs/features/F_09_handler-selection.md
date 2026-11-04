# EventBus handler_ids 限定派发历史方案（已撤回）

建档日期：2026-10-28。主责契约：[S_01](../specs/S_01_events-contract.md)。最近修订：2026-11-03。计划条目 EV-08。**本方案已撤回**：Core 的 `trigger` 恢复为仅接收 event 和可选 session；JiuwenSwarm 在宿主注册 `session.start` Handler 时绑定可信包目标，匹配阶段筛选。以下为撤回前的设计与当时版本的验证记录，不代表当前公开接口。撤回后的验证以本轮工作树和 JiuwenSwarm F_21 的新增记录为准。

撤回验证：Windows / Python 3.11.15，Core `feature/session-start-match-pattern-20261103` 基于已合入 !110 的目标分支 `45e6418c`，与 Swarm 同名分支（目标基线 `4b6021ca8`）合跑。Core 相关 Events、Extension、callback、AgentRuntime 与 DeepAgent 回归 **709 passed、1 skipped**；Swarm 相关回归 **1458 passed、5 skipped、2 deselected**。两项 LoopBeat 计时测试此前已在未修改基线复现，未计入通过。初版候选基于 `4aceb5cd` 曾得 Core 581 passed/1 skipped、Swarm 1414 passed/5 skipped/2 deselected；这些数值不代替目标分支更新后的结果。

扩大到目标分支新增 store 用例时，Core 首轮 **761 passed、2 skipped、2 failed**；同两项 store 用例在未修改的目标基线也失败。Swarm 首轮 **1463 passed、5 skipped、2 deselected、1 failed**，失败的 store 持久化用例在未修改目标基线同样复现。因此当前通过数明确排除 Core `test_ops_store.py` 与 `core/extension/data`、Swarm `test_store_persistence.py`，不宣称全套通过。JUnit、日志、基线对照和真实 Front → 子 Worker 两轮模型输入见 `D:/work_space/.codex-temp/session-start-match-pattern-20261103/` 及 Swarm F_21。旧版下述通过数不能替代本次验证。

## 1. 范围、状态与依据

基于 rsi_native_agent_os 的 ead700d7d。现有 Runtime.published_handlers(package_id) 能提供该包的公开 Handler ID，但 trigger 只能向所有匹配 Handler 派发。初始化某个新激活包时，需要只运行指定注册记录，不能重跑旧包。

本项只增加通用可选派发白名单，不把包管理或 session.start 业务移入 Core，不引入新事件、Ops、权限或生命周期状态。无直接 Claude mods 同名方法对照；它是宿主调用机制的增量。

## 2. 能力说明与对照

`await bus.trigger(event, session, handler_ids=ids)` 将 USER/BUILTIN 候选限制在所给 Handler ID 集合内，再按既有 matcher 和顺序执行；CORE 仍为默认尾部。适合宿主基于已核实的包发布记录发起定向通知。省略参数时完整兼容原分发。

ID 只控制候选，不构成授权。列表可同时包含其他事件的 ID；这些 ID 在当前事件中不参与。未知或已注销 ID 不参与，不按参数顺序重新排序，不绕过 matcher。

## 3. 事件契约

不新增事件，也不改变业务输入/输出、Validator、短路或故障隔离。限制仅在 trigger 的最初匹配边界生效；本次快照内的 EventNext、跳层和恢复不能扩大候选集合。ops.build 拒绝任何非 None 的 handler_ids，避免部分装配能力目录。

## 4. 方法契约

```python
async def trigger(
    self,
    event: Event,
    session: Session | SessionContext | None = None,
    *,
    handler_ids: Sequence[HandlerId] | None = None,
) -> EventOutput | None:
    ...
```

None 维持旧行为，空序列只运行 CORE（正常短路语义仍适用）；序列元素须为非空字符串，字符串整体、bytes、非 Sequence 或非法元素按 EVENTS_PAYLOAD_INVALID 报错。重复项去重。调用在首个 await 前复制为不可变集合，之后调用方修改列表不影响本次执行。

```python
ids = runtime.published_handlers(package_id)
result = await bus.trigger(event, session_context, handler_ids=ids)
```

其余参数/结果及异常、取消仍按 S_01，不解包或重新构造业务结果。该示意要求调用方已经有 Runtime、合法业务 Event 和 SessionContext；不是新的业务事件定义。

## 5. 接入与兼容方案

Protocol 与真实 Bus 同步增加仅关键字可选参数；_match_handlers 在事件类型和白名单过滤后才调用 matcher。其余注册、Ops Build、逐 Handler Ops、Framework 和故障恢复不改造。

旧调用省略参数保持原行为。自定义 Bus / Fake 若声明实现新 Protocol，需接受此可选参数并实现限定语义，不能靠忽略参数声称支持定向派发。

备选“让所有包先收到事件再自行去重”不能保证旧包不被再次调用；“宿主读取 Bus 私有表或直接调用用户函数”绕过 Core 会话/包身份与容错。采用公开 ID 白名单，让业务代次和并发准入继续由宿主负责。

注销不打断已开始调用，新注册不进入已有快照。白名单不缓存、不持久化；调用方应从当前 Runtime 重新获取 ID。Core 不负责包代次与重新激活策略。

## 6. 验收与未决问题

2026-11-02 冲突集成：当前目标分支 `bb8f7ba4a` 已合入 AgentRuntime 及 Team 相关能力，并使用 EV-07 和最高 F_08。本 PR 保留目标内容，将待合入的限定派发条目顺延为 EV-08 / F_09；`bus.py`、`protocol.py` 和 handler_ids 测试相对原 !110 提交 `0b5dd43f1` 保持不变。开发计划合并双方工作项，同时修复该计划指向 AgentRuntime 已改名 feature 的引用。

Server !99 目前已经合入 `rsi_native_agent_os`，其 session.start 仍调用 `trigger(..., handler_ids=ids)`。本轮直接使用已合入的 Server 源码验证消费兼容性；不再把“先 Core、后 Server”的历史合并计划当作当前状态，也不将四项生命周期业务移入 Core。

本轮 Windows / Python 3.11.15 验证：Core Events、Extension、callback、AgentRuntime 与 DeepAgent 绑定回归 **595 项通过、1 项跳过**，包含 handler_ids 专项 14 项；跳过原有目录符号链接测试，当前环境不允许创建符号链接。已合入 Server `e847fb045` 与本候选 Core 合跑，扩展、Runtime、原生切换和历史入口 **1327 项通过、2 项跳过**，包含 start/end/attach/detach 与字段契约的 94 项生命周期测试；跳过项分别为当前平台无法提供的符号链接和命名管道。没有排除失败用例，未运行格式化或静态检查。

实际命令如下，PYTHONPATH 分别指向本候选 Core 和已合入 Server 的独立 worktree，运行时打印的模块来源已核对；Core 的 Python 实现与本轮测试期间一致：

```text
python -m pytest -q -o addopts= --asyncio-mode=auto tests/unit_tests/core/events tests/unit_tests/core/extension tests/unit_tests/core/runner/callback tests/unit_tests/core/single_agent/test_agent_runtime.py tests/unit_tests/harness/test_deep_agent_runtime_binding.py
python -m pytest -q -o addopts= --asyncio-mode=auto --no-cov -o log_cli=false tests/unit_tests/extensions_v2 tests/unit_tests/runtime tests/unit_tests/agentserver/test_session_switch_characterization.py tests/unit_tests/agentserver/test_history_append_record.py
```

本地命令、版本/来源收据与 JUnit XML 位于 `D:/work_space/.codex-temp/resolve-core110-20261102`；报告为 core-regression.xml、server-regression.xml。上述为源码组装与单元/组件回归，本轮未重跑真实子 Worker/模型、浏览器、非 Windows 实机或远端 CI。下列旧日期的通过数量仍归属于各自原始版本，不替代本轮验证。


2026-10-31 合并交付调整：Core !110 合并最新目标分支 `bdde48a8`，保留其已合入的 EV-06 / F_06 包信息能力；本项待合入的编号改为 EV-07 / F_07，`handler_ids` 代码和公开语义不变。Server 原 !91 的消费者与文档、结束、关联及解除能力一起整理到 [Server !99](https://gitcode.com/huenrui_/jiuwenswarm/merge_requests/99)，保留四个提交；当时计划先 Core !110、再 Server；当前合入状态见本节 2026-11-02 记录。

本次 Windows / Python 3.11.15 的 Events、Extension、callback 回归 **587 项通过、1 项跳过**，包括目标分支新增的包信息能力与原 handler_ids 用例；符号链接用例因当前环境权限跳过。命令和报告位于 `D:\work_space\.codex-temp\consolidate-server-prs-20261031\core-command.json`、`core-regression.xml`。本环境未提供 make，本轮未执行 make check；本次不以历史静态检查、真实模型或远端 CI 记录代替当前执行结果。

| 输入与操作 | 预期与证据 |
| --- | --- |
| 省略白名单、限制 USER/BUILTIN、名单次序颠倒且含重复/其他事件/未知 ID | 原 matcher 和层级注册顺序不变，未选 Handler 不进入；专项用例验证真实 Ops 包/会话绑定 |
| 空序列或未知 ID、非法序列/元素、ops.build 白名单 | 前两者只保留 CORE，非法值报既有 payload 错误，Build 拒绝后仍能正常完整装配 |
| 首 Handler 等待期间修改名单、注销/重注册 | 本次快照继续旧注册，修改只影响后续调用 |
| 选中 Handler 自身失败、短路和取消 | 恢复不扩大候选，不重复 CORE；合法短路与外部取消保持原语义 |
| Server 用 Runtime.published_handlers 定向调用 | 真实 Registry 两包空 matcher、新包、重载和重装只运行目标包；Server 专项测试验证，非直接调用用户函数 |

2026-10-28，Windows / Python 3.11.15：Events 专项合跑 112 项通过；Events + extension 包管理 + callback 回归 **568 项通过、1 项跳过**。命令如下；跳过项为既有包路径符号链接用例，具体环境条件由 pytest 报告保留。未将远端 CI 或非 Windows 实机纳入通过结论。

```text
python -m pytest -o addopts= -q tests/unit_tests/core/events tests/unit_tests/core/extension tests/unit_tests/core/runner/callback --asyncio-mode=auto -o log_cli=false
```

证据在 `D:\work_space\.codex-temp\server-session-start-event-20261028\core-focused.xml` / `core-regression.xml`。公共机制无未决设计项；Server 负责输入触发边界、激活身份、至多一次策略和真实用户接入验证，不能据此认定其产品功能已验收。

## 7. 参考资料

- [Bus](../../bus.py)、[Protocol](../../protocol.py)、[执行测试](../../../../../tests/unit_tests/core/events/test_event_bus_execution.py)。
- [扩展 Runtime](../../../extension/runtime.py)：published_handlers 与发布/替换快照。
- [F_05 容错](F_05_ev05-handler-fault-tolerance.md)：沿用的异常与取消边界。
