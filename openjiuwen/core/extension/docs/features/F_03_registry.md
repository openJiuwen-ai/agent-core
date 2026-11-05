# EX-03 Registry 管理入口

## 1. 元信息

| 项 | 值 |
| --- | --- |
| 建档日期 | 2026-10-21 |
| 最近修订 | 2026-10-21 |
| 范围 | `Registry.init` / `install` / `uninstall` / `reload`；九类包管理事件的登记与 Core Handler 编排 |
| 方案成熟度 | 已确认。契约见 [S_02](../specs/S_02_registry-and-bus.md) |
| 计划条目 | [EX-03](../designs/extension-development-plan.md#ex-03-registry-管理入口) |
| 设计依据 | [概览](../designs/extension-overview.md)、[架构](../designs/extension-architecture.md) 第 4.1 节 |
| 关联 feature | 控制面 [F_01](F_01_package-source.md)、[F_02](F_02_plan-runtime.md)；Bus 发布 [F_04](F_04_bus-wiring.md) |
| 验证摘要 | 2026-10-21：`pytest tests/unit_tests/core/extension/ -q` → 82 passed, 1 skipped。详见第 7 节 |

## 2. 背景、目标与范围

业务代码需要薄入口：接收已初始化的 EventBus，登记九类包管理事件（若尚未登记），再通过便捷方法触发安装、卸载、重载。

| 场景 | 目标行为 |
| --- | --- |
| `Registry.init(bus, …)` | 接收已初始化的 EventBus，登记 jiuwenswarm 内置事件；不安装包 |
| `install` / `uninstall` / `reload` | `trigger` 对应根事件并交回可核对结果 |
| 重复 `init` | 按约定拒绝或保持已就绪实例，不重复登记 |

**包含：** `Registry`；九类事件类型与 Core Handler 的登记（薄封装在 `events/builtins`，编排逻辑在 `extension`）。

**不包含：** candidate 写入 Bus 的细节（F_04）；宿主 `config.yaml` 解析。

## 3. 设计约束

- 依赖方向保持 `extension → events`。builtins 内只放登记与转发，业务编排延迟导入 extension，避免 `events` 包加载时反向依赖。
- `init` 不读用户 manifest，不安装扩展。宿主回调返回后，`init` 触发一次 `ops.build`，调用方不再单独触发。
- `open_install_dir` 在 `init` 之后异步绑定安装根目录、报告路径、可选域和宿主版本，登记报告执行者；不安装，也不返回路径列表。
- 九类事件的特性说明与验收放在本模块文档；Events 开发计划不单列这些工作项。

## 4. 方案概览

```mermaid
flowchart LR
    Host["调用方"] --> Reg["Registry"]
    Bus["EventBus"] --> Reg
    Reg -->|register 九类事件| Bus
    Reg -->|trigger| Bus
    Bus --> CH["Core Handler"]
    CH --> Ctrl["PM / Orch / Runtime"]
```

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| D-01 | 九类事件在 EventBus 初始化时登记 | `init` 只登记 jiuwenswarm 的内置事件 |
| D-02 | builtins 薄封装 + extension 编排 | 代码落点符合 builtins 约定，且不破坏依赖方向 |
| D-03 | feature/spec 放在 extension/docs | Events 计划未覆盖包加载事件；便于与 EX-03/EX-04 一并维护 |

## 5. 模块与主路径

| 模块 | 责任 |
| --- | --- |
| `extension/registry.py` | init / install / uninstall / reload |
| `extension/management/` | 九类事件 Core Handler 编排 |
| `events/builtins/event_types/package_events.py`、`extension_events.py` | `register(bus)` 登记九类事件并转发到 extension |

主路径：`init` → `await open_install_dir(..., report_path=...)` → `await load_installed()`。在线来源继续用 install。域和报告见 [F_05](F_05_domain-install-report.md)。

## 6. 影响面

| 位置 | 内容 |
| --- | --- |
| `extension/registry.py`、`management/` | Registry 与 Handler 编排 |
| `events/builtins/event_types/` | 九类事件登记模块 |
| 单元测试 | `test_registry.py`、`test_package_events.py` |

## 7. 验证

| 验收 ID | 覆盖要点 | 测试文件 |
| --- | --- | --- |
| EX-03-V-01 | init 不安装包；宿主回调后触发一次 `ops.build`，随后第一次 install 可以执行 | `test_registry.py` |
| EX-03-V-02 | 便捷方法触发根事件并返回结果 | `test_registry.py`、`test_package_events.py` |
| EX-03-V-03 | 重复 init 行为符合约定 | `test_registry.py` |

| 日期 | 命令 | 结果 |
| --- | --- | --- |
| 2026-10-21 | `pytest tests/unit_tests/core/extension/ -q` | 82 passed, 1 skipped |
