# State 持久化与构建依赖设计方案

## 1. 元信息

| 项 | 值 |
| --- | --- |
| 建档日期 | 2026-11-03 |
| 范围 | state.get/set、文件持久化、CAS、自动构建依赖及资源清理 |
| 设计依据 | 用户指定设计第二部分第 2 节 2.1—2.6、第 4 节的 state 内容及重载澄清 |
| 方案成熟度 | 已确认并实现；第 7 节记录自动收集替代上下文透传的修订 |
| 进度维护 | [Events 开发计划 EV-15](../designs/events-development-plan.md#ev-15-state-持久化版本与构建依赖) |

## 2. 能力与范围

`ops.state.get(ref)` 读取一个 JSON 值及其版本；`ops.state.set(ref, value, expected_version=None)`
可按版本条件更新。每个包可以管理多个 key，状态按 session/application 作用域隔离，重启和重载保留值与版本。
宿主在构建界面时收集读取依赖，状态提交后安排依赖目标重新构建；普通业务读取不建立展示订阅。

Core 提供状态文件、事件、Ops、自动收集范围和不透明目标依赖。宿主选择保存目录，
管理具体目标、连接、任务、版本轮询和实际发送。SDK 不导入宿主的目标类型，也不选择产品目录。
本次沿用现有 `core/extension/data/` 布局，不另建同义数据包。store 的 Windows 锁与路径修复见
[F_14 store](F_14_ops-store.md#2026-11-03-windows-文件回归修复)，其 CRUD 语义保持。

接口、文件结构和错误语义以 [S_03 State 规约](../specs/S_03_state.md) 为准；
资源清理协作见 [Extension S_02](../../../extension/docs/specs/S_02_registry-and-bus.md)。

## 3. 事件、Ops 与使用

类型从 `openjiuwen.core.extension.data` 导入。引用 `StateRef(package, key, scope="auto")` 在 Ops 入口解析为
`StateAddress(package, key, scope, session_id=None)`。auto 有真实会话时选择 session，否则选择 application。
跨包读取允许，写入仅允许调用包自己的地址；显式 session 没有会话时拒绝，不创建虚拟会话。

| 方法 / 事件 | 输入 | 返回 |
| --- | --- | --- |
| `ops.state.get(ref)` / `state.get` | `StateGetInput(package_id, address)` | `StateSnapshot(exists, value, version)` |
| `ops.state.set(ref, value, expected_version=None)` / `state.set` | `StateSetInput(package_id, address, value, expected_version)` | `StateSetResult(is_set, changed, version, reason)` |

Matcher 支持 package_id、ref.package、ref.key、scope、session_id，空模式匹配全部合法请求。
同步 Validator 拒绝非法输入输出。请求定位与版本条件不能被拦截器改变，set 的 value 可以加工。
普通 Handler 抛异常遵循 Events 跳过规则；明确业务拒绝应返回类型化结果，不能依赖抛异常阻止默认写入。

```python
from openjiuwen.core.extension.data import StateRef

COUNT = StateRef("example.counter", "count")

async def increment(event, ops, next_handler):
    await next_handler(event)
    snapshot = await ops.state.get(COUNT)
    return await ops.state.set(
        COUNT, (snapshot.value if snapshot.exists else 0) + 1,
        expected_version=snapshot.version,
    )
```

以上只展示业务 Handler 中的状态调用；事件回执应由实际业务契约决定。
构树 Handler 只 get，通过 next_handler 提交新树；不能在构树中 set 或再次请求自身构树。
USER / BUILTIN 可通过现有 registrar 登记 state 拦截器，例如按
`{"ref.package": "example.counter", "ref.key": "count"}` 匹配后继续原链或返回合法的拒绝结果。

事件及唯一 CORE 在 `builtins/event_types/state.py`；方法 state_get/state_set 与 Build Handler
build_state_ops 在 `builtins/handler_ops/state.py`，经现有自动发现登记。Build 只登记方法，不访问文件。
`Registry.init(states=..., state_dependencies=...)` 在 Build 前绑定宿主显式提供的服务。

## 4. 实现与取舍

### 4.1 文件与版本

每个包一个目录。应用状态写入 `<package.id>/application.json`，每个会话写入
`<package.id>/sessions/<session_id>.json`。文件顶层 format_version=1，entries 保存该文件内各 key 的值与版本。
它与扩展版本、每个 key 的 version 独立；未知格式拒绝读取，当前不提供自动迁移。
每个状态文件单独使用稳定的旁路锁完成读改写、CAS 比较与原子替换。应用文件和各个会话文件互不共用这把锁。
阻塞文件操作在线程执行，取消等待工作线程真正退出。
State 使用已有依赖 portalocker 的公开 Lock；共享 files 模块提供通用路径和锁方法，store 同样复用，
修复其在 Windows 上的路径前缀误判和锁文件删除问题。

每次读取最新文件，不增加跨进程可能过时的值缓存。单 key 修改保留同一文件中的其它 key，也不改写其它会话或应用文件。
同值写入版本不变，旧版本条件写入不提交。clear_session 只把该会话文件中的条目改成不存在并递增版本，不保留旧值，防止旧请求重新写入。
确定未提交的 I/O 失败返回 storage_error；文件损坏、参数错误或提交结果未知抛出类型化异常。
提交通知在锁释放后运行，通知失败保留待通知记录，不把已保存的数据改报为写入失败。

### 4.2 自动收集与依赖

宿主构树入口使用 StateDependencies.collect(target_key) 开启临时收集范围。
每个 StateDependencies 实例用 ContextVar 保存本次构建标识，state.get CORE 自动识别；
逐 Handler 的 Ops、Bus.trigger、Builder 和已有能力方法保持原有公共接口。
agent/tool 注册、store、FS、MCP 和宿主查询均无需透传参数，嵌套 Handler 的实际 state 读取仍进入同次构建。
连接路由继续由宿主现有机制维护，不塞入 SDK 的调用参数或虚拟会话。

普通业务 get 不绑定面板，set 不创建依赖；state 可以独立作为后端持久化状态使用。
只有构树 get 且实际交付成功，才提交目标与完整状态地址的关系。
不同面板、会话、状态服务的异步构建彼此隔离；构建退出后收集范围复原，迟到的过期读取被拒绝。

StateDependencies 只记录构建标识、target_key 和固定地址快照；同次构建的重复读取保持一致。
宿主确认实际发送、目标一致后提交依赖；失败保留上次依赖。提交后复核读取版本，覆盖首次构建窗口的变化。
`JsonState.changed_since(reads)` 仅提供通用版本核对；定时任务由宿主管理。
撤销 target 同时撤销未完成构建，旧构建不能重新提交。

### 4.3 重载与清理

用户确认保留热切换：新版先处理新调用，旧版已开始的业务继续完成，之后由新版构树逻辑刷新一次。
Runtime 记录活跃调用和嵌套 state 工作线程所属代次；旧代退出后异步执行 reason=reload 的资源回调。
宿主据代次拒绝旧渲染重新绑定，并撤销旧刷新任务和依赖。重载保留 state 值与 version；
重新下发的展示 revision 由宿主管理，只有实际改值才增加 state.version。

卸载先等待使用者退出和宿主资源撤销，再删除包状态；失败保留记录供同一路径重试。
停机停止代次回收任务、释放服务绑定，保留文件。断线或释放运行实例不等于删除持久化会话数据。

## 5. 验收与证据

验收覆盖作用域、JSON 副本、CAS、损坏文件、写失败、跨进程锁、取消等待、清空版本、
Matcher/Validator、拦截继续与拒绝、无会话状态使用、构建固定快照、通知重试和卸载生命周期。

使用现有 Python 3.12 环境和隔离临时数据，不调用真实模型。复验入口：

```text
pytest tests/unit_tests/core/events/test_ops_state.py tests/unit_tests/core/extension/data/test_state.py
pytest tests/unit_tests/core/events/test_events_contract.py
pytest tests/unit_tests/core/events tests/unit_tests/core/extension tests/unit_tests/core/runner/callback
```

自动收集修订后，State 与 Events 公共接口专项 47 passed。
扩大回归曾出现两项 store Windows 失败；按用户要求在 F_14 中修复并复验，
当前 Core 扩大回归为 **810 passed、2 skipped、0 failed**。
store、store Ops 与 state 文件专项为 78 passed、1 skipped，含新增路径前缀与锁失败验证。
宿主正式装配、自动依赖与跨进程专项 17 passed，包括后端状态读写、并发面板、共享 key 和取消清理。
宿主的真实模型对话与部署路由不作为 SDK 组件验收。

## 6. 本次范围复核

2026-11-03：将 state 特性集中到本模块，回退 `.agents/agent-docs` 和模块 AGENTS 的修改；
宿主只维护接入约束和 Core 引用。首次复核撤回了 store 锁改造；后续用户明确要求修复失败用例，
因此按既有文件契约补入 F_14 记录的最小兼容性修复。调试交付物和应用本地依赖不属于本次 Core 提交。
保留状态、依赖和代次回收；随后按用户确认的第 7 节修订，将显式透传替换为自动构树收集。
不新增内置业务 Handler、产品入口、自动重放事务或跨主机存储。

## 7. 2026-11-03 修订：自动收集面板与状态依赖

### 7.1 目标与状态

用户要求统一记录面板、扩展包和状态 key 的对应关系，状态提交后驱动重新构树，
不让每个 Ops 或嵌套事件适配 call_context。用户已确认并要求实施，当前采用本节方案；
原先的显式调用上下文方案已被替代，相关公共参数和既有能力中的透传已撤回。
文件持久化、CAS、跨 Worker 版本轮询及用户已确认的热重载语义继续保留。

### 7.2 唯一依赖登记机制

复用 StateDependencies，不新增另一套面板管理器。宿主继续维护完整的面板目标和路由，
Core 只维护不透明 target_key 与解析后 StateAddress 的对应关系。
地址包含 package、key、scope，session 状态还包含真实 session_id；连接只属于面板路由。
同包可有多个面板，同一个 key 可影响多个面板，每个面板也可依赖多个 key。

宿主在一次面板构树开始时，为该 target_key 自动开启临时收集范围。
state.get CORE 在实际读取时，将该地址、快照和版本记入当前收集范围。
普通业务读取和 set 不建立面板依赖。仅凭调用包身份无法知道读取服务于哪个面板，
因此构树入口负责指定面板，扩展 Handler 和其它 Ops 无需声明或转发。

内部使用按异步调用隔离的 ContextVar 保存临时构建标识，仅用于 state 构建收集。
它不是全局“当前插件”变量，不加入 EventBus、Builder 或 EventHandlerOps 的公共参数。
嵌套调用自然进入同一收集范围，同时构建的面板各自隔离。
收集器带有效期标记，构建退出、失败、取消或目标撤销时失效；继承该收集器的迟到任务不能继续绑定旧构建。
构树期间 state.set 仍被拒绝；构建外使用 state 保持普通业务语义。

### 7.3 构树、提交与刷新

1. 宿主收到 ui.render 构树请求，校验面板、连接和代次，为面板开启一次临时依赖收集。
2. USER / BUILTIN Handler 调用 state.get 并构建 JSON 树。CORE 读取时自动收集地址，
   同次构建的重复读取仍使用固定快照。
3. ui.render CORE 校验并实际发送新树。只有发送成功、目标一致且仍有效，才替换该面板上次的依赖。
   构建失败保留旧依赖；构建结束关闭临时收集器。交付后继续复核版本，覆盖构建窗口内的提交。
4. 业务调用 state.set，其 CORE 调用状态服务完成 CAS 和持久化；只有值实际改变时发布通用状态变更通知。
   保留 JsonState 的提交后通知，使直接使用文件状态服务的调用与事件入口保持一致。
5. 宿主刷新器按变更地址查找依赖面板，按目标合并入队，使用保存的可信路由发起新的 ui.render。
   USER / BUILTIN 重建 JSON 树，CORE 再推送前端；不重放 ui.ready。

依赖登记在构建内的 get 阶段；set 使用已经提交的登记查找目标。
这既支持按钮、回答完成等业务事件更新同一个状态，也避免普通业务 get 订阅所有面板。
没有依赖面板时只保存状态。相同值、CAS 冲突或未提交的写入不安排刷新。

用户进一步明确：state 也可用于纯后端持久状态，使用方式类似 store。
只配置状态目录即可使用 get/set，无需面板、浏览器连接或 ui.ready。
有会话、扩展声明了 UI、曾经显示过其它面板，都不构成自动绑定条件；
绑定条件是本次 get 正处于某个有效面板的构树收集范围，且构建最终成功交付。
普通业务 Handler 或独立后台任务的 get 不绑定；set 不创建依赖或面板。
若同一地址已经被另一个面板在构树时读取，后端业务修改该地址仍正常刷新那个面板。
state 的值、版本和 CAS 在有无 UI 时保持相同语义；不新增“是否启用前端”的必填配置。

set 驱动刷新，但不在文件锁内或 set 的同步调用栈内等待 UI Handler。
CORE 提交后通过现有通用通知唤起宿主；宿主负责 ui.render 类型和实际触发。
这样保持 SDK 不依赖具体 UI 或应用，同时界面可以在提交后异步更新，连续写入也可合并。
跨 Worker 仍由宿主核对已提交依赖的版本，将远端提交送入同一刷新队列。

### 7.4 责任与改动收敛

| 所属 | 修订职责 |
| --- | --- |
| Core 状态与依赖服务 | 自动收集范围、固定快照、target_key 与 StateAddress 关系、提交后通知 |
| 宿主 WebUI | 面板与包的对应、会话/连接路由、构树入口、实际交付确认、刷新队列和生命周期 |
| 扩展 Handler | 读取状态、执行业务写入或构建 JSON 树；无需适配调用上下文 |

删除本次新增的 EventCallContext 以及 trigger / Builder / Ops 上的 call_context 入口。
回退 agent.register、tool.register、store、FS、MCP 和宿主 session 查询中的上下文透传，
并恢复相应公共接口快照与测试替身签名。新开发的普通事件、Handler 或 Ops 无需为 state 追加参数。
宿主已有的连接绑定机制继续承担连接路由，不向 Core 引入浏览器连接类型。

本修订保留现有文件服务、依赖登记和刷新队列，替换依赖收集的定位方式。
卸载、断线、会话释放和重载继续撤销对应依赖及临时构建；旧代业务允许完成，旧代面板不能重新绑定。

### 7.5 修订验收

- 一个包的两个面板读取不同 key，set 只刷新实际依赖的面板；共享 key 可刷新两个面板。
- 两个会话、两个连接、两个并发构建隔离，普通业务读取不建立依赖。
- 未装配 UI 的纯后端 get/set 能保存、读取和恢复值与版本，不创建依赖或触发 ui.render。
- 已有 UI 贡献但未构建读取该 key 时，普通 get/set 仍只操作数据；已有精确依赖时后端 set 正常刷新。
- 构树经过现有 FS/MCP 等嵌套事件后读取 state，无需任何上下文透传仍能自动登记。
- 构建失败、取消和迟到后台读取不覆盖旧依赖；构建中的 set 仍被拒绝。
- 持久化、CAS、同值不刷新、首次构建窗口复核、跨 Worker 刷新及已确认的热重载行为保持。
- agent/tool/store/FS/MCP 相关文件恢复原有参数，正式 Build、公共接口和宿主装配回归通过。

本轮实际结果见第 5 节；新增用例覆盖并发收集隔离、独立 Bus、继承过期收集范围的迟到读取、
取消及读取文件期间撤销构建。已有四类嵌套 Ops 使用 HEAD 代码，仍能自动收集读取依赖。
