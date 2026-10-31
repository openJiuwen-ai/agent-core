# EventBus handler_ids 限定派发特性设计

建档日期：2026-10-28。主责契约：[S_01](../specs/S_01_events-contract.md)。计划条目 EV-07，配套 JiuwenSwarm Server PR06 的 session.start；本文件在实现前建档，当前机制已实现并完成下述验证。

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

2026-10-31 合并交付调整：Core !110 合并最新目标分支 `bdde48a8`，保留其已合入的 EV-06 / F_06 包信息能力；本项待合入的编号改为 EV-07 / F_07，`handler_ids` 代码和公开语义不变。Server 原 !91 的消费者与文档、结束、关联及解除能力一起整理到 [Server !99](https://gitcode.com/huenrui_/jiuwenswarm/merge_requests/99)，保留四个提交；合并顺序仍为先 Core !110，再 Server，不能只合入 Server。

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
