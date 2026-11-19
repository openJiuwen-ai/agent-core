# State 事件与持久化契约

最近修订：2026-11-03。实现与验证记录见 [F_15 State](../features/F_15_ops-state.md)；本文维护当前接口和不变量。

## 范围与调用

`ops.state.get(ref)` 返回 `StateSnapshot(exists, value, version)`；
`ops.state.set(ref, value, expected_version=None)` 返回
`StateSetResult(is_set, changed, version, reason)`。
类型从 `openjiuwen.core.extension.data` 导入。两个方法分别经过同名事件，能力集本身不可调用。
不提供内置业务 Handler，也不改变无包身份的 package 方法。

`StateRef(package, key, scope="auto")` 支持 auto、session、application。
auto 在有真实 SessionContext 时选择 session，否则选择 application；显式 session 必须有会话。
请求包含原调用 package_id 和已解析的 StateAddress；跨包读取允许，写入只允许调用包自己的地址。
请求定位和 expected_version 不得被拦截器改变；value 可以被修改。
可信扩展在同一进程运行，事件字段不是恶意代码的安全隔离。

## 文件、版本与错误

宿主显式传入 `JsonState(directory)`，SDK 不选择应用目录。每个包一个目录：
应用状态为 `<package.id>/application.json`，会话状态为 `<package.id>/sessions/<session_id>.json`。
文件内容就是该作用域的 key 对象，各 key 并列，每个 key 同时保存 value 和 version。
从未设置的地址返回 exists=False、value=None、version=0。
清空会话只改该会话文件，把条目记为不存在并递增版本，不保留旧值；应用状态不受影响。
`JsonState(directory, *, max_bytes=4194304)` 可由宿主调整单个状态文件的容量；
`clear_session(session_id, *, package_id=None)` 清理指定包或目录中的所有包，宿主负责协调真实业务会话删除。
`changed_since(reads)` 接收地址到版本的映射并返回 bool，供交付后复核及跨进程版本轮询使用。

1. 同一状态文件的读改写、版本比较和原子提交共用该文件的稳定跨进程文件锁；阻塞 I/O 在线程完成。
   应用文件与各个会话文件分别加锁。
2. 已存在且 JSON 内容相同的写入返回成功、changed=False，版本不变。
3. 版本不等返回 is_set=False、changed=False、reason=version_conflict，无文件提交或刷新。
4. 确定没有提交的写失败返回 storage_error；可靠当前版本未知时 version=None。
   参数、归属、路径、文件损坏和提交结果未知抛出异常；取消传播并等待文件线程退出。
5. 返回值与调用输入独立复制；布尔值和数字、不同 JSON 类型不得误判为相同。
6. 重启、重连、重载和停机保留值与版本；卸载删除该包目录、其中的状态文件和包锁。

## 事件与登记

事件输入是 `StateGetInput(package_id, address)` 与
`StateSetInput(package_id, address, value, expected_version=None)`。
Matcher 精确支持 package_id、ref.package、ref.key、scope、session_id；空模式匹配全部合法请求。
未知字段或非字符串匹配值报 Events 匹配错误。输入输出由同步 Validator 检查。
事件及 CORE 在 `events/builtins/event_types/state.py` 定义；Build Handler 在
`events/builtins/handler_ops/state.py`，经新建 Bus 的自动发现登记，不在 Build 时读写文件。
拒绝或短路返回上述输出类型，不因业务 Handler 抛异常就假定默认动作被阻止。
明确业务拒绝可返回 `StateSetResult(False, False, known_version, "rejected")`；它不触发默认提交或通知。

## 自动收集与依赖

宿主通过 `with dependencies.collect(target_key) as render_id` 开启一次临时依赖收集。
StateDependencies 使用实例所属的 ContextVar 隔离并发异步构建；state.get CORE 自动识别收集范围。
Bus、Builder 和 Ops 不增加上下文参数，嵌套事件无需适配；SDK 不解释目标字符串或连接路由。

构建内首次读取固定地址快照，重复读取返回副本。作用域退出时释放临时构建；
失败、取消及目标撤销使旧构建失效，继承失效范围的迟到读取被拒绝，不能重新建立依赖。
构建期间禁止 Ops 的 set，含直接触发 state.set CORE。
只有实际交付且最终目标一致的构建才能提交依赖；失败保留旧依赖。
依赖提交后宿主核对已读版本，期间变化时补一轮。提交后的变更通知只入队，不在文件锁内执行业务。
通知失败保留待通知记录，已提交数据仍返回成功；其它进程可通过公开版本核对方法发现变更。
撤销目标同时撤销未完成构建，旧构建不得重新提交。连接、会话、包的目标归属和调度由宿主管理。

普通业务 get 不建立依赖，set 不创建面板或依赖；没有依赖时只持久化。
不要求装配 UI、存在浏览器连接或先发送 ready，state 可独立作为后端持久状态使用，版本与 CAS 语义保持一致。
已有精确地址依赖时，后端业务 set 也会通知该地址的读取面板。

## 验收

验证正式 Build 与调用链、Matcher/Validator、无会话使用、跨包只读、固定构建快照、自动嵌套收集、
CAS 并发、跨进程锁、损坏文件与提交失败、取消、会话清空、重启恢复及卸载失败重试。
组件测试与宿主实际传输验证分别记录，单进程回调不能作为跨进程刷新证据。
覆盖普通后端 get/set 无面板绑定、不同面板/key/会话的并发隔离，以及失败或迟到构建不能重新绑定。
