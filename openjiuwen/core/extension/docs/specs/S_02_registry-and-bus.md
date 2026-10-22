# S_02 Registry 与 Bus 发布规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 编号 / slug | `S_02 / registry-and-bus` |
| 关联模块 | `openjiuwen/core/extension/`；九类事件登记模块在 `openjiuwen/core/events/builtins/` |
| 最近一次修订日期 | 2026-10-21 |
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
2. 执行宿主传入的事件与 Handler 登记回调，只登记 jiuwenswarm 的内置事件。
3. 绑定本 Registry 使用的 `PackageManager` / `Orchestrator` / `Runtime`。
4. 不读取用户 manifest，不安装扩展。宿主回调返回后触发一次 `ops.build`。
5. 同一实例重复 `init` → 失败，不留下半套登记。

### 2.2 便捷方法

| 方法 | 触发事件 | 结果 |
| --- | --- | --- |
| `install(source)` | `package.install` | 每个包一个 `ActivationReport` |
| `uninstall(package_id)` | `package.uninstall` | 一个 `ActivationReport` |
| `reload(package_id)` | `package.reload` | 一个 `ActivationReport` |

`init(event_bus, *, host_event_types=None, host_handlers=None)` 只接收 EventBus，并执行宿主回调，登记 jiuwenswarm 的内置事件。

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
3. `unpublish(package_id, bus)`：注销该包全部 handler_id，再 `drop_active`。
4. 写入前业务 `trigger` 不得看见 candidate 中的 Handler。
5. `PackageMetadata.root` 为解析后的绝对包根。
6. 扩展侧若登记了同步 handler，发布时包装为 awaitable，以符合 EventBus 约定。

## 5. 验证索引

| 主题 | 测试文件 |
| --- | --- |
| Registry | `tests/unit_tests/core/extension/test_registry.py` |
| 九类事件编排 | `tests/unit_tests/core/extension/test_package_events.py` |
| Bus 发布 | `tests/unit_tests/core/extension/test_bus_wiring.py` |
