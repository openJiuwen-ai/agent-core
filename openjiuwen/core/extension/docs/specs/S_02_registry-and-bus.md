# S_02 Registry 与 Bus 发布规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 编号 / slug | `S_02 / registry-and-bus` |
| 关联模块 | `openjiuwen/core/extension/`；九类事件登记模块在 `openjiuwen/core/events/builtins/` |
| 最近一次修订日期 | 2026-11-06 |
| 关联 feature | [F_03](../features/F_03_registry.md)、[F_04](../features/F_04_bus-wiring.md)、[F_06](../features/F_06_package-enable.md) |
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
| `set_enabled(package_id, enabled)` | 内部复用安装或卸载编排 | 一个 `ActivationReport` |

`set_enabled` 不新增管理事件。`enabled=True` 先将受管副本写为启用，再按安装的 plan/apply 路径发布；失败时恢复为禁用。`enabled=False` 采用卸载的依赖、在途调用、注销和资源清理约束，但不删除受管安装副本，最后写入禁用状态。成功结果的 `operation` 为 `enable` 或 `disable`。该方法只接受通用包标识和布尔状态，不包含宿主产品概念。

`init(event_bus, *, host_event_types=None, host_handlers=None, store=None, states=None,`
`state_dependencies=None, cleanup_callbacks=())`
接收 EventBus 和可选宿主服务。store 是 `JsonStore`，目录完全由宿主选择；Build 前绑定到当前 Bus。
`cleanup_callbacks` 是按顺序执行的异步 `PackageResourceCleanup(package_id, generation, reason)` 回调，
类型从 `openjiuwen.core.extension` 导入。宿主回调在 Core store 清理前执行，必须幂等；异常和取消不能吞掉。
初始化失败或取消时撤销本次存储绑定和管理上下文。

2026-11-03：states 是宿主显式配置目录的 JsonState，state_dependencies 是可选 StateDependencies。
二者在 Build 前绑定；宿主资源回调先执行，随后清理 Core state/store 数据。
只有 uninstall 删除持久文件和相应数据依赖；reload/shutdown 保留值与版本。
State 契约见 [Events State 规约](../../../events/docs/specs/S_03_state.md)。

重载保持热切换。新代发布后旧代已开始的 Handler 和嵌套 state 操作可继续完成；
Runtime 按 Ops 记录其原代，旧代退出后异步执行 reason=reload 的清理回调。
回调失败保留后台任务并退避重试，不回滚已经发布的新代。宿主停止 Runtime 时调用
`stop_retirements()`，随后按原 shutdown 顺序释放资源。清理回调必须按 generation 隔离旧代资源。

`open_install_dir` 在 `init` 之后异步调用，返回 None：

```python
async def open_install_dir(
    self, install_dir: Path, *, report_path: Path, host_version: str | None = None,
    domain: str | None = None, allowed_domains: Collection[str] | None = None,
    poll_interval: float = 0.5, dependency_timeout: float = 300.0, lock_timeout: float = 5.0,
) -> None: ...

async def load_installed(self) -> tuple[ActivationReport, ...]: ...
async def begin_shutdown(self) -> None: ...
async def close(self) -> None: ...
```

报告路径必传，时间单位均为秒。open 完成运行锁与进程登记，不安装包；load 从根目录完整发现、选版、稳定排序，再筛出当前域逐包激活。
`domain=None` 保留全量加载；不能和按域实例混用同一报告。每个执行范围只允许一个存活执行者。
宿主停机先 begin_shutdown 拒绝新操作并结束在途管理任务，撤销本地 Handler 和资源后 close 释放运行锁。
报告读写、依赖占用及重启规则见 [F_05](../features/F_05_domain-install-report.md)。

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
| `package.query` | 本地 active/installed/inflight/published 与共享 packages | 报告不可读时明确失败 |

管理写操作先由报告登记 operation_id，并取得 `PackageOperationToken`。`PlanRequest.operation_token` 与 `ApplyRequest.operation_token` 必须显式携带该 token；Handler 按 token 校验包、操作、进程和 operation_id。token 随事件请求传递，因此计划和 apply 可以跨异步任务调度，不依赖发起任务的上下文变量，也不能单独绕过报告执行。apply 在校验后自行进入操作范围，文件写入仍由该范围授权。依赖等待不持报告锁；发布前再次校验。

完成本地动作后提交报告失败时，不覆盖已经得到的 `ActivationReport`，也不中断同批其它包；共享行保留 pending/unknown 并记录异常。查询可以用已完成的本地 Runtime 发布补正报告，不重新执行扩展代码。其它编排步骤遵循架构第 4.1、5 节；内部步骤不另增事件。

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
删除全部受管理安装副本及其 `<install_dir>/<package.id>` 目录、撤销 MCP 声明、最后 drop_active。宿主资源回调先执行，Core store JSON 删除随后执行。
MCP 清理失败保留声明和 store，不继续执行宿主回调或数据删除。
任何一步失败返回 FAILED，并保留记录与未完成版本供同一条链重试；未注销 Handler 阻止新使用。
部分副本删除后 manifest 已不存在时，`remove_installed(..., missing_ok=True)` 仍可完成既知目录的重试。

只有安装副本但未激活的包也由 `package.uninstall` 处理，复用相同清理与副本删除步骤；
此时回调 generation 为 0，不伪造生效记录，不建立另一类清理计划。
待清理包不能 reload，也不能作为新加载包的可用依赖。

重载不调用 uninstall，不删除 store。宿主可在旧代调用结束后，调用相同清理接口并传
`reason="reload"`，停机传 `reason="shutdown"`；Core store 对这两种原因保留文件。
WebUI 仍由宿主清理回调负责；Registry.begin_shutdown/close 负责管理任务和报告运行锁，不代替宿主撤销资源。
store 细节及本次验证见 [Events F_14](../../../events/docs/features/F_14_ops-store.md)。

## 5. 验证索引

| 主题 | 测试文件 |
| --- | --- |
| Registry | `tests/unit_tests/core/extension/test_registry.py` |
| 九类事件编排 | `tests/unit_tests/core/extension/test_package_events.py` |
| Bus 发布 | `tests/unit_tests/core/extension/test_bus_wiring.py` |
