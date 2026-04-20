# Sandbox

Sandbox mode is a runtime mode of SysOperation that routes filesystem, Shell, and code execution capabilities to an isolated environment. For basic concepts, direct invocation, Tool invocation, and Agent integration of SysOperation, see [System Operation](./System%20Operation.md). This document only covers the design, isolation granularity, current usage, and extension methods of sandbox mode.

Core features of sandbox mode:

- **Environment Isolation**: Execute operations in an independent sandbox environment, isolated from the local system
- **Stateless Design**: SysOperation instances are stateless; sandbox lifecycle is managed by Gateway
- **Flexible Extension**: Supports multiple sandbox types and launch methods; users can customize Providers and Launchers
- **Multi-level Isolation**: Supports SYSTEM, SESSION, and CUSTOM isolation levels

> **Note**: The current version supports connecting to already-started AIO sandboxes. For local execution, Docker or lightweight sandboxes can be used; for large-scale remote/production deployment, cloud services like Kubernetes (K8s) are recommended.

---

## 1. Core Principles

### 1.1 Layered Architecture

Sandbox mode consists of the following layers from top to bottom:

| Layer | Responsibility | Description |
|-------|---------------|-------------|
| **Operation Layer** | User Interface | `SandboxFsOperation`, `SandboxShellOperation`, `SandboxCodeOperation` provide unified operation interfaces |
| **Protocol Layer** | Interface Abstraction | Defines the minimal interface set that sandbox implementations must provide, such as `BaseFsProtocol`, `BaseShellProtocol` |
| **Gateway Layer** | Sandbox Management | Singleton pattern, responsible for sandbox creation, routing, and lifecycle management |
| **Launcher Layer** | Sandbox Launch | Responsible for sandbox launch, pause, resume, and deletion, such as `PreDeployLauncher` |
| **Provider Layer** | Capability Adaptation | Implements Protocol interfaces, adapts different sandbox APIs, such as `AIOProvider` |

**Stateless Design**: Operation is a stateless access entry, while sandbox is a stateful execution environment. States such as files, work results, and environment changes inside the sandbox are maintained by the sandbox itself; Operation is not responsible for persistence.

**Operation and Protocol**: Operation is user-facing, Protocol is implementation-facing. Protocol defines the minimal interface set, Provider implements it, and Operation combines them to provide upper-level capabilities. The current version defines three types: `BaseFsProtocol`, `BaseShellProtocol`, and `BaseCodeProtocol`.

---

## 2. Isolation Granularity and Lifecycle

The sandbox framework uses `isolation_key` to determine which sandbox instance a request hits. Isolation granularity determines the reuse boundary of sandboxes:

- Multiple Operation instances with the same `isolation_key` will access the same sandbox
- The same Operation instance may access different sandboxes under different session contexts

### 2.1 isolation_key Format

The `isolation_key` format is:

```
{container_scope}_{launcher_type}_{sandbox_type}_{prefix}_{custom_id_or_session_id}
```

Examples:

- `system_pre_deploy_aio___`: SYSTEM level
- `session_pre_deploy_aio_agent1__session123`: SESSION level, agent1 prefix
- `custom_pre_deploy_aio__my_sandbox_001`: CUSTOM level

Operation generates an `isolation_key` template during initialization, and at runtime, Gateway Client obtains `session_id` from coroutine context and concatenates it into the complete `isolation_key`.

### 2.2 SYSTEM Granularity

All requests share the same sandbox, `isolation_key` is fixed, no dynamic parts.

**Use Cases**:

- Single-process applications, globally sharing one sandbox instance
- Sandbox managed by external service, framework only responsible for connection

**Lifecycle**:

- Sandbox lifecycle managed externally
- Framework does not actively create or destroy

### 2.3 SESSION Granularity

Same `session_id` shares the same sandbox, different `session_id` uses different sandboxes. `session_id` is dynamically obtained from coroutine context, supporting runtime dynamic sandbox creation.

**Use Cases**:

- Multi-session scenarios, each session independently isolated
- Multiple agents within the same session further isolated via `prefix`

**Configuration Example**:

```python
# Within the same session, agent1 and agent2 use different sandboxes
SandboxIsolationConfig(
    container_scope=ContainerScope.SESSION,
    prefix="agent1_",
)

SandboxIsolationConfig(
    container_scope=ContainerScope.SESSION,
    prefix="agent2_",
)
```

**Lifecycle**:

- Sandboxes dynamically created with sessions
- Manual resource release required
- Supports `on_stop` strategies: `delete`, `pause`, `keep`

### 2.4 CUSTOM Granularity

Directly specifies the logical identity of sandbox via `custom_id`, users fully control the sandbox reuse boundary.

**Use Cases**:

- Need to hit a specific sandbox
- Bind sandbox lifecycle to business ID

**Lifecycle**:

- `custom_id` fully controlled by user
- Manual resource release required

### 2.5 Lifecycle Management

SESSION and CUSTOM granularity sandboxes require manual resource release.

**Releasing Sandbox Resources**:

```python
from openjiuwen.core.sys_operation.sandbox.gateway.gateway_client import SandboxGatewayClient

# Release via isolation_key
isolation_key = sys_op.isolation_key_template  # Get from SysOperation
await SandboxGatewayClient.release(isolation_key, on_stop="delete")
```

**Note**: `isolation_key_template` is a template, format like `session_pre_deploy_aio_agent1__{session_id}`. During invoke, `{session_id}` is automatically filled from coroutine context; but during release, the complete isolation_key must be provided manually.

> **Note**: The current version has SESSION granularity semantics, but gateway session persistence has not yet implemented "per-session reuse".

---

## 3. Configuration Details

### 3.1 Configuration Hierarchy

`SandboxGatewayConfig` is the top-level configuration for sandbox mode, containing isolation configuration and Launcher configuration:

```
SandboxGatewayConfig (Top-level Configuration)
├── isolation: SandboxIsolationConfig (Isolation Configuration)
│   ├── container_scope: ContainerScope (Isolation Level)
│   ├── prefix: Optional[str] (Namespace Prefix)
│   └── custom_id: Optional[str] (Custom Container ID)
├── launcher_config: SandboxLauncherConfig (Launcher Configuration)
│   ├── launcher_type: str (Launcher Type)
│   ├── sandbox_type: str (Sandbox Type)
│   ├── on_stop: Literal["delete", "pause", "keep"] (Stop Strategy)
│   └── idle_ttl_seconds: Optional[int] (Idle Timeout)
└── timeout_seconds: int (Timeout Setting)
```

### 3.2 Isolation Configuration

`SandboxIsolationConfig` determines the isolation and reuse boundary of requests:

| Field | Type | Description |
|-------|------|-------------|
| `container_scope` | ContainerScope | Isolation level: SYSTEM / SESSION / CUSTOM |
| `prefix` | Optional[str] | Namespace prefix for further isolation within the same scope |
| `custom_id` | Optional[str] | Custom container ID, only used in CUSTOM mode |

### 3.3 Launcher Configuration

`SandboxLauncherConfig` is the base class for Launcher configuration, supporting three stop strategies: `delete`, `pause`, `keep`.

Currently implemented subclasses:

```
SandboxLauncherConfig (Base Class)
└── PreDeployLauncherConfig (Pre-deploy Launcher Configuration, for connecting to started sandbox services)
    ├── launcher_type: Literal["pre_deploy"] = "pre_deploy"
    ├── sandbox_type: str (default "aio")
    ├── base_url: str (Sandbox Service Address)
    └── idle_ttl_seconds: Optional[int] (Idle Timeout)
```

---

## 4. Using Direct AIO Sandbox Connection

The current out-of-the-box main path is connecting to already-started AIO sandbox. AIO is one currently connected implementation, not a framework binding object.

### 4.1 Installing Dependencies

AIO direct connection depends on `agent-sandbox` client, which is an optional extra in this project.

Install from PyPI:

```bash
pip install -U "openjiuwen[sandbox]"
```

If developing in the source repository:

```bash
uv sync --extra sandbox
```

### 4.2 Preparing AIO Sandbox Service

Before running AIO direct connection mode, you need to prepare an accessible AIO sandbox service.

**Run with Docker (Recommended)**:

```bash
# Pull and run the latest version
docker run --security-opt seccomp=unconfined --rm -it -p 8080:8080 ghcr.io/agent-infra/sandbox:latest
```

> **Note**: For more AIO Sandbox usage instructions, refer to [AIO Sandbox Quick Start](https://sandbox.agent-infra.com/zh/guide/start/quick-start).

### 4.3 Basic Usage

Connect to an already-started AIO sandbox service (SYSTEM level):

```python
import asyncio
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import (
    SysOperationCard,
    OperationMode,
    SandboxGatewayConfig,
    SandboxIsolationConfig,
    ContainerScope,
)
from openjiuwen.core.sys_operation.config import PreDeployLauncherConfig


async def main():
    # 1. Start Runner
    await Runner.start()

    try:
        # 2. Create sandbox configuration
        card = SysOperationCard(
            id="my_sandbox",
            mode=OperationMode.SANDBOX,
            gateway_config=SandboxGatewayConfig(
                isolation=SandboxIsolationConfig(
                    container_scope=ContainerScope.SYSTEM
                ),
                launcher_config=PreDeployLauncherConfig(
                    base_url="http://localhost:8080",
                    sandbox_type="aio",
                    idle_ttl_seconds=600,
                ),
                timeout_seconds=30,
            ),
        )

        # 3. Register to resource manager
        result = Runner.resource_mgr.add_sys_operation(card)
        assert result.is_ok()

        # 4. Get SysOperation instance and execute operations
        sys_op = Runner.resource_mgr.get_sys_operation("my_sandbox")

        # Execute Shell command
        shell_res = await sys_op.shell().execute_cmd(command="echo hello world")
        print(f"Shell output: {shell_res.data.stdout.strip()}")

        # Read file
        fs_res = await sys_op.fs().read_file(path="/etc/hosts")
        print(f"File content length: {len(fs_res.data.content)}")

        # Execute Python code
        code_res = await sys_op.code().execute_code(
            code="print('Hello from Python')",
            language="python"
        )
        print(f"Code output: {code_res.data.stdout.strip()}")

    finally:
        # 5. Clean up resources
        Runner.resource_mgr.remove_sys_operation(sys_operation_id="my_sandbox")
        await Runner.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

**Key Configuration Notes**:

- `mode=OperationMode.SANDBOX`: Specifies sandbox mode
- `SandboxIsolationConfig`: Determines request isolation and reuse boundary
- `PreDeployLauncherConfig`: Indicates connecting to an existing sandbox service
- `sandbox_type="aio"`: Indicates AIO provider maps unified interfaces to AIO sandbox

### 4.4 Using as Agent Tool

After registering the sandbox, its methods are automatically wrapped as `LocalFunction` tools, which can be obtained via Tool ID and added to Agent:

```python
# Get tool instance
tool_id = SysOperationCard.generate_tool_id("my_sandbox", "shell", "execute_cmd")
execute_cmd_tool = Runner.resource_mgr.get_tool(tool_id)

# Invoke tool
res = await execute_cmd_tool.invoke({"command": "echo hello"})
print(res.data.stdout.strip())

# Add to Agent
agent.ability_manager.add(execute_cmd_tool.card)
```

---

## 5. Extending Custom Sandboxes

Sandbox framework extension points are divided into two categories:

| Extension Point | Responsibility |
|-----------------|----------------|
| **Launcher** | Sandbox instance acquisition and lifecycle management |
| **Provider** | Sandbox capability protocol implementation and adaptation |

Both are orthogonal:

- If sandbox acquisition method doesn't change, only extend Provider
- If sandbox acquisition method also needs customization, extend both Launcher and Provider

### 5.1 Extending Launcher

Launcher is responsible for sandbox launch, pause, resume, and deletion. The core base class is `SandboxLauncher`.

```python
from openjiuwen.core.sys_operation.sandbox.launchers.base import SandboxLauncher, LaunchedSandbox
from openjiuwen.core.sys_operation.config import SandboxLauncherConfig


class MyCustomLauncher(SandboxLauncher):
    async def launch(
        self,
        config: SandboxLauncherConfig,
        timeout_seconds: int,
        isolation_key: str | None = None,
    ) -> LaunchedSandbox:
        # Implement sandbox launch logic
        # Return LaunchedSandbox(base_url="...", sandbox_id="...")
        pass

    async def pause(self, sandbox_id: str) -> None:
        # Implement sandbox pause logic
        pass

    async def resume(self, sandbox_id: str) -> None:
        # Implement sandbox resume logic
        pass

    async def delete(self, sandbox_id: str) -> None:
        # Implement sandbox deletion logic
        pass
```

### 5.2 Extending Provider

Provider needs to implement interfaces defined by Protocol. Protocol defines the minimal interface set that sandbox implementations must provide.

**Protocol Types**:

| Protocol | Description | Basic Methods |
|----------|-------------|---------------|
| `BaseFsProtocol` | Filesystem Protocol | `read_file`, `write_file`, `list_files`, etc. |
| `BaseShellProtocol` | Shell Protocol | `execute_cmd`, `execute_cmd_stream`, etc. |
| `BaseCodeProtocol` | Code Execution Protocol | `execute_code`, `execute_code_stream`, etc. |

**Extension Points**:

- Provider only needs to implement basic methods defined by Protocol
- Operation layer's advanced features are implemented based on these basic methods
- No need to implement all capabilities at once
- Theoretically, only implementing Shell Protocol can also compose other upper-level behaviors based on Shell

Provider is responsible for implementing Protocol interfaces and adapting specific sandbox APIs:

```python
from openjiuwen.core.sys_operation.sandbox.providers.base_provider import BaseFSProvider
from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxEndpoint
from openjiuwen.core.sys_operation.result import ReadFileResult


class MyCustomFSProvider(BaseFSProvider):
    """Filesystem Provider implementing BaseFsProtocol interface"""
    
    def __init__(self, endpoint: SandboxEndpoint, config=None):
        super().__init__(endpoint, config)
        # Initialize sandbox client

    async def read_file(
        self,
        path: str,
        mode: str = "text",
        **kwargs
    ) -> ReadFileResult:
        # Implement file read logic (basic method required by Protocol)
        # Call sandbox API
        pass

    # Implement other Protocol methods...
```

### 5.3 Complete Extension Example

This extension example demonstrates the complete chain:

1. Define custom `SandboxLauncherConfig`
2. Define `SandboxLauncher`
3. Define `Provider`
4. Register Launcher and Provider
5. Reference new `launcher_type` and `sandbox_type` in `SysOperationCard`
6. Still call through unified `sys_op.shell()` interface

If you need to support filesystem or code execution, just add corresponding `fs` and `code` provider implementations.

The following example only implements `shell` capability, demonstrating that you can start from a minimal capability set:

```python
import asyncio
from typing import Any, AsyncIterator, Dict, Literal, Optional

from pydantic import Field

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import OperationMode, SandboxGatewayConfig, SysOperationCard
from openjiuwen.core.sys_operation.config import (
    ContainerScope,
    SandboxIsolationConfig,
    SandboxLauncherConfig,
)
from openjiuwen.core.sys_operation.result import (
    ExecuteCmdChunkData,
    ExecuteCmdData,
    ExecuteCmdResult,
    ExecuteCmdStreamResult,
)
from openjiuwen.core.sys_operation.sandbox.launchers.base import LaunchedSandbox, SandboxLauncher
from openjiuwen.core.sys_operation.sandbox.providers.base_provider import BaseShellProvider
from openjiuwen.core.sys_operation.sandbox.sandbox_registry import SandboxRegistry


# Step 1: Define custom configuration
class MySandboxLauncherConfig(SandboxLauncherConfig):
    launcher_type: Literal["my_launcher"] = "my_launcher"
    sandbox_type: str = Field(default="my_sandbox")
    base_url: str = Field(..., description="Custom sandbox service address")


# Step 2: Define Launcher
class MySandboxLauncher(SandboxLauncher):
    async def launch(
        self,
        config: SandboxLauncherConfig,
        timeout_seconds: int,
        isolation_key: Optional[str] = None,
    ) -> LaunchedSandbox:
        if not isinstance(config, MySandboxLauncherConfig):
            raise ValueError("MySandboxLauncher requires MySandboxLauncherConfig")
        return LaunchedSandbox(
            base_url=config.base_url,
            sandbox_id=isolation_key,
        )


# Step 3: Define Provider
class MySandboxShellProvider(BaseShellProvider):
    async def execute_cmd(
        self,
        command: str,
        *,
        cwd: Optional[str] = None,
        timeout: Optional[int] = 300,
        environment: Optional[Dict[str, str]] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> ExecuteCmdResult:
        return ExecuteCmdResult(
            code=StatusCode.SUCCESS.code,
            message=StatusCode.SUCCESS.errmsg,
            data=ExecuteCmdData(
                command=command,
                cwd=cwd or ".",
                exit_code=0,
                stdout=f"[my_sandbox] {command}\n",
                stderr="",
            ),
        )

    async def execute_cmd_stream(
        self,
        command: str,
        *,
        cwd: Optional[str] = None,
        timeout: Optional[int] = 300,
        environment: Optional[Dict[str, str]] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[ExecuteCmdStreamResult]:
        yield ExecuteCmdStreamResult(
            code=StatusCode.SUCCESS.code,
            message=StatusCode.SUCCESS.errmsg,
            data=ExecuteCmdChunkData(
                text=f"[my_sandbox] {command}\n",
                type="stdout",
                chunk_index=0,
                exit_code=0,
            ),
        )


# Step 4: Register Launcher and Provider
SandboxRegistry.register_launcher("my_launcher", MySandboxLauncher)
SandboxRegistry.register_provider("my_sandbox", "shell", MySandboxShellProvider)


# Step 5: Reference custom components in configuration
async def main():
    await Runner.start()

    card_id = "my_sandbox_op"

    try:
        card = SysOperationCard(
            id=card_id,
            mode=OperationMode.SANDBOX,
            gateway_config=SandboxGatewayConfig(
                isolation=SandboxIsolationConfig(
                    container_scope=ContainerScope.CUSTOM,
                    custom_id="demo_shell_sandbox",
                ),
                launcher_config=MySandboxLauncherConfig(
                    base_url="http://localhost:9000",
                    idle_ttl_seconds=600,
                ),
                timeout_seconds=30,
            ),
        )

        add_res = Runner.resource_mgr.add_sys_operation(card)
        assert add_res.is_ok()

        sys_op = Runner.resource_mgr.get_sys_operation(card_id)
        res = await sys_op.shell().execute_cmd(command="echo hello")

        if res.code == 0:
            print(res.data.stdout.strip())
    finally:
        Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
        await Runner.stop()


asyncio.run(main())
```

---

## References

- [System Operation](./System%20Operation.md): Basic concepts and LOCAL mode usage of SysOperation
- [Skills and System Operation](./Skills%20and%20System%20Operation.md): How to combine system operations with Skills
- [AIO Sandbox Quick Start](https://sandbox.agent-infra.com/zh/guide/start/quick-start): Installation and usage instructions for AIO Sandbox
- API Documentation: `openjiuwen.core.sys_operation.config` module
