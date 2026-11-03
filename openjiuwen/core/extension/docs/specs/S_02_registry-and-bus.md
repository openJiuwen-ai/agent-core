# S_02 Registry 与 Bus 发布规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 编号 / slug | `S_02 / registry-and-bus` |
| 关联模块 | `openjiuwen/core/extension/`；九类事件登记模块在 `openjiuwen/core/events/builtins/` |
| 最近一次修订日期 | 2026-11-02 |
| 关联 feature | [F_03](../features/F_03_registry.md)、[F_04](../features/F_04_bus-wiring.md) |
| 设计依据 | [开发计划](../designs/extension-development-plan.md)、[架构](../designs/extension-architecture.md) |

本规约维护 Registry 入口、candidate 发布到 EventBus、以及九类包管理事件的输入输出与完成依据。控制面不变量仍以 [S_01](S_01_package-loading.md) 为准。

## 1. 范围与责任

- **本规约定义：** `Registry` 行为；九类事件名与 Core Handler 完成条件；Runtime 发布/替换/移除 Handler 的语义；`PackageMetadata` 在登记时的填入规则。
- **本规约不定义：** EventBus 匹配与洋葱链（Events S_01）；manifest 与排序（本模块 S_01）。
- **依赖方向：** `extension → events`。builtins 模块不得在导入时加载 extension；可在 Handler 运行时延迟导入。

## 2. Registry

### 2.1 init

1. 接收已经初始化的 EventBus。九类管理事件及其 Core Handler 在 EventBus 初始化时登记，`init` 不再登记它们。
2. 执行宿主传入的事件与 Handler 登记回调，登记宿主自身的事件与能力。
3. 绑定本 Registry 使用的 `PackageManager` / `Orchestrator` / `Runtime`。
4. 不读取用户 manifest，不安装扩展。宿主回调返回后触发一次 `ops.build`。
5. 同一实例重复 `init` → 失败，不留下半套登记。

### 2.2 便捷方法

| 方法 | 触发事件 | 结果 |
| --- | --- | --- |
| `install(source)` | `package.install` | 每个包一个 `ActivationReport` |
| `uninstall(package_id)` | `package.uninstall` | 一个 `ActivationReport` |
| `reload(package_id)` | `package.reload` | 一个 `ActivationReport` |

`init(event_bus, *, host_event_types=None, host_handlers=None, store=None, cleanup_callbacks=())`
接收 EventBus 和可选宿主服务。store 是 `JsonStore`，目录完全由宿主选择；Build 前绑定到当前 Bus。
`cleanup_callbacks` 是按顺序执行的异步 `PackageResourceCleanup(package_id, generation, reason)` 回调，
类型从 `openjiuwen.core.extension` 导入。宿主回调在 Core store 清理前执行，必须幂等；异常和取消不能吞掉。
初始化失败或取消时撤销本次存储绑定和管理上下文。

`open_install_dir(install_dir, *, host_version=None)` 在 `init` 之后调用。它记住安装目录，用 `host_version` 和 `requires.host` 比较，并返回目录里已有的 `<package.id>/<version>/`。这一步不安装。调用方把返回的路径交给 `install`。

`open_install_dir` 不触发 `ops.build`。`init` 在宿主回调返回后触发一次。调用方不再单独触发。

## 3. 九类包管理事件

事件名：`package.install`、`package.uninstall`、`package.reload`、`package.parse`、`package.resolve`、`package.query`、`extension.plan`、`extension.apply`、`extension.rollback`。

| 事件 | 成功时 | 主要失败 |
| --- | --- | --- |
| `package.parse` | 返回候选包声明与根路径 | 来源/manifest 非法 |
| `package.resolve` | 可装集合与包序，或卸载是否允许 | 依赖/冲突/忙/被依赖 |
| `extension.plan` | 可信 `ExtensionPlan` | 包内环、未生效等 |
| `extension.apply` | `ActivationReport`；安装/重载已发布或卸载已移除 | 加载失败、忙等 |
| `extension.rollback` | candidate 已丢掉 | 清理残留须写入结果 |
| `package.install` | 汇总各包 Report | 子事件失败按依赖跳过规则 |
| `package.uninstall` | 注册移除且安装副本删除 | `PACKAGE_BUSY` / `PACKAGE_IN_USE` |
| `package.reload` | 该包 Handler 已热切换 | 依赖方不兼容、准备失败 |
| `package.query` | 当前生效/进展事实 | 无目标时明确空结果 |

编排步骤遵循架构第 4.1、5 节；内部步骤不另增事件。

## 4. Bus 发布

1. `publish(candidate, plan, bus, *, manifest=...)`：对每条声明调用 `bus.register_event_handler(..., handler_level=..., package_metadata=plan.package)`；任一条失败则注销本批已登记 id，并 `abort(candidate)`。`manifest` 用于写入生效记录中的 `requires_packages`（`PackageMetadata` 本身不含该字段）。
2. `replace_published`（重载）：登记新批成功后，再注销该 `package_id` 旧 id；新批失败则只清新批。
3. `unpublish(package_id, bus, *, keep_record=False)`：内部复核依赖与在途调用，停止新调用，
   注销该包全部 handler_id。注销失败保留失败 ID 和包记录；默认成功后 `drop_active`，
   卸载编排传 `keep_record=True`，清理完成后才移除记录。
4. 写入前业务 `trigger` 不得看见 candidate 中的 Handler。
5. `PackageMetadata.root` 为解析后的绝对包根。
6. 扩展侧若登记了同步 handler，发布时包装为 awaitable，以符合 EventBus 约定。

### 4.1 store 卸载清理

管理 apply 持包级锁，其他同包管理操作返回 `EXTENSION_PACKAGE_BUSY`。
Runtime 发布的 Handler 自动计入 begin/end；Ops 发起的 store 调用另外计数，后台已开始的 store
即使原 Handler 返回也仍阻止卸载。取消等待文件线程退出后才释放计数。

卸载依次执行：复核卸载条件、记住已验证的安装版本、unpublish 保留记录、
清理已声明的 MCP 连接、
`await Runtime.cleanup_package_resources(package_id, generation, reason="uninstall")`、
删除受管理安装副本、撤销 MCP 声明、最后 drop_active。宿主资源回调先执行，Core store JSON 删除随后执行。
MCP 清理失败保留声明和 store，不继续执行宿主回调或数据删除。
任何一步失败返回 FAILED，并保留记录与未完成版本供同一条链重试；未注销 Handler 阻止新使用。
部分副本删除后 manifest 已不存在时，`remove_installed(..., missing_ok=True)` 仍可完成既知目录的重试。

只有安装副本但未激活的包也由 `package.uninstall` 处理，复用相同清理与副本删除步骤；
此时回调 generation 为 0，不伪造生效记录，不建立另一类清理计划。
待清理包不能 reload，也不能作为新加载包的可用依赖。

重载不调用 uninstall，不删除 store。宿主可在旧代调用结束后，调用相同清理接口并传
`reason="reload"`，停机传 `reason="shutdown"`；Core store 对这两种原因保留文件。
当前未增加 WebUI、后台刷新任务、state 数据或通用停机入口。
store 细节及本次验证见 [Events F_14](../../../events/docs/features/F_14_ops-store.md)。

### 4.2 当前包 Handler 调用有效性

Runtime.is_active_invocation(ops: EventHandlerOps) -> bool 只读判断此 Runtime 发布的当前 Handler 调用是否有效；不注册、触发或筛选事件。Handler 包装建立调用帧并沿用原活动 Ops / 包在途计数。只有本层 Ops 对象、活动标记和 asyncio Task 同时匹配时返回 True。

嵌套 Handler 临时替换当前帧，退出后恢复父帧；子任务即使继承 ContextVar 也不匹配原 Task。返回、异常或取消都会关闭原帧，因此复制上下文不能延长调用寿命。其它 Runtime、手工构造、非受管直接注册或过期的 Ops 返回 False。调用只发生在包装的真实执行期间；它不是恶意 Python 代码隔离。

EventBus、EventHandlerOps 和 Builder 的公共接口不受影响；begin_calls/end_calls 的既有计数与 store 校验语义不变。宿主可通过回调使用此只读校验，业务准入和重入控制仍由所属业务模块负责。验证：tests/unit_tests/core/extension/test_invocation_scope.py。

## 5. 验证索引

| 主题 | 测试文件 |
| --- | --- |
| Registry | `tests/unit_tests/core/extension/test_registry.py` |
| 九类事件编排 | `tests/unit_tests/core/extension/test_package_events.py` |
| Bus 发布 | `tests/unit_tests/core/extension/test_bus_wiring.py` |
