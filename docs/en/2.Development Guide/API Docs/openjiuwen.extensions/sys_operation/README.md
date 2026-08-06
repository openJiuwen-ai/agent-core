# openjiuwen.extensions.sys_operation

`openjiuwen.extensions.sys_operation` provides optional **sandbox provider** extensions for connecting `SysOperation`'s file system, command line, and code execution capabilities to external sandbox services.

Currently, it contains only one subpackage:

- `sandbox.providers`: concrete provider implementations. Importing `openjiuwen.extensions.sys_operation.sandbox` or `openjiuwen.extensions.sys_operation.sandbox.providers` will register all extension providers to the `SandboxRegistry`.

## Registered Providers

| Provider Class | Registered Name | Capability | Source Module |
|---|---|---|---|
| `JiuwenBoxFSProvider` | `"jiuwenbox"` / `fs` | File system | `sandbox.providers.jiuwenbox` |
| `JiuwenBoxShellProvider` | `"jiuwenbox"` / `shell` | Command line execution | `sandbox.providers.jiuwenbox` |
| `JiuwenBoxCodeProvider` | `"jiuwenbox"` / `code` | Code execution | `sandbox.providers.jiuwenbox` |
| `AIOFSProvider` | `"aio"` / `fs` | File system (local asyncio) | `sandbox.providers.aio` |
| `AIOShellProvider` | `"aio"` / `shell` | Command line execution (local asyncio) | `sandbox.providers.aio` |
| `AIOCodeProvider` | `"aio"` / `code` | Code execution (local asyncio) | `sandbox.providers.aio` |

> The `jiuwenbox` series connects to remote JiuwenBox sandboxes via HTTP gateway; the `aio` series executes directly in the local process using asyncio. Both inherit from `BaseFSProvider` / `BaseShellProvider` / `BaseCodeProvider` under `openjiuwen.core.sys_operation.sandbox.providers.base_provider`, and are instantiated on demand by `SandboxRegistry` when the corresponding registered name is resolved.

## Page Index

- [JiuwenBox Sandbox Provider](./sandbox/providers/jiuwenbox.md): Complete API for `JiuwenBoxFSProvider`, `JiuwenBoxShellProvider`, `JiuwenBoxCodeProvider`, and five module-level utility functions.

## Usage Notes

- Extension providers are provided in an optional manner: importing `openjiuwen.extensions.sys_operation.sandbox` completes registration, without needing to manually call `SandboxRegistry.register`.
- Which specific provider to use is determined by the sandbox name in the `SysOperation` configuration (e.g., `"jiuwenbox"` or `"aio"`), and is usually not directly constructed by users.
- The `jiuwenbox` provider requires a reachable JiuwenBox gateway address and (optional) `JIUWENBOX_API_TOKEN` authentication token; the `aio` provider has no external dependencies and is suitable for local development and testing.
