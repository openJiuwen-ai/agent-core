# openjiuwen.extensions.sys_operation

`openjiuwen.extensions.sys_operation` 提供可选的 **沙箱 provider** 扩展，用于把 `SysOperation` 的文件系统、命令行与代码执行能力对接到外部沙箱服务。

当前只包含一个子包：

- `sandbox.providers`：具体 provider 实现。导入 `openjiuwen.extensions.sys_operation.sandbox` 或 `openjiuwen.extensions.sys_operation.sandbox.providers` 即会把所有扩展 provider 注册到 `SandboxRegistry`。

## 已注册 provider

| Provider 类 | 注册名 | 能力 | 来源模块 |
|---|---|---|---|
| `JiuwenBoxFSProvider` | `"jiuwenbox"` / `fs` | 文件系统 | `sandbox.providers.jiuwenbox` |
| `JiuwenBoxShellProvider` | `"jiuwenbox"` / `shell` | 命令行执行 | `sandbox.providers.jiuwenbox` |
| `JiuwenBoxCodeProvider` | `"jiuwenbox"` / `code` | 代码执行 | `sandbox.providers.jiuwenbox` |
| `AIOFSProvider` | `"aio"` / `fs` | 文件系统（本地 asyncio） | `sandbox.providers.aio` |
| `AIOShellProvider` | `"aio"` / `shell` | 命令行执行（本地 asyncio） | `sandbox.providers.aio` |
| `AIOCodeProvider` | `"aio"` / `code` | 代码执行（本地 asyncio） | `sandbox.providers.aio` |

> `jiuwenbox` 系列通过 HTTP 网关对接远端 JiuwenBox 沙箱；`aio` 系列则在本地进程内用 asyncio 直接执行。两者均继承自 `openjiuwen.core.sys_operation.sandbox.providers.base_provider` 下的 `BaseFSProvider` / `BaseShellProvider` / `BaseCodeProvider`，由 `SandboxRegistry` 在解析到对应注册名时按需实例化。

## 页面索引

- [JiuwenBox 沙箱 provider](./sandbox/providers/jiuwenbox.md)：`JiuwenBoxFSProvider`、`JiuwenBoxShellProvider`、`JiuwenBoxCodeProvider` 及五个模块级工具函数的完整 API。

## 使用要点

- 扩展 provider 以可选方式提供：导入 `openjiuwen.extensions.sys_operation.sandbox` 后即完成注册，无需手动调用 `SandboxRegistry.register`。
- 具体使用哪个 provider 由 `SysOperation` 配置中的沙箱名（如 `"jiuwenbox"` 或 `"aio"`）决定，通常不经用户直接构造。
- `jiuwenbox` provider 需要可达的 JiuwenBox 网关地址与（可选）`JIUWENBOX_API_TOKEN` 鉴权令牌；`aio` provider 无外部依赖，适合本地开发与测试。
