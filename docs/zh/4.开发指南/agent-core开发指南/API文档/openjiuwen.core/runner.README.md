# runner

`openjiuwen.core.runner` 提供了 Workflow、Agent 和 Tool 的统一执行接口，并提供了对 Agent 对象的全生命周期管理能力。同时提供异步回调框架（callback），用于事件驱动、链式执行与回滚、过滤与指标等能力。

**Classes / 模块**：

| CLASS / 模块 | DESCRIPTION |
|----------------------------|--------------------------|
| [Runner](runner/runner.md) | 提供 Workflow、Agent、Tool 的统一执行接口与全生命周期管理 |
| [callback](runner/callback/callback.README.md) | 异步回调框架：事件驱动、过滤、链与回滚、指标与钩子 |
| [resource_manager](runner/resource_manager/resource_manager.md) | 资源管理器：Workflow、Agent、Tool 等资源的注册与获取 |

