# sys_operation

`openjiuwen.core.sys_operation` is the system operation module of the openJiuwen framework, providing controlled access to underlying system resources, including file system operations, code execution, and Shell command execution, supporting local and sandbox modes.

**Classes**:

| CLASS | DESCRIPTION |
|-------|-------------|
| [OperationMode](./sys_operation/sys_operation.md) | System operation mode enumeration. |
| [LocalWorkConfig](./sys_operation/sys_operation.md) | Local work environment configuration. |
| [SandboxGatewayConfig](./sys_operation/sys_operation.md) | Remote sandbox gateway connection configuration. |
| [SysOperationCard](./sys_operation/sys_operation.md) | System operation configuration card. |
| [SysOperation](./sys_operation/sys_operation.md) | System operation entry class. |
| [BaseResult](./sys_operation/result.md) | Generic base class for all operation results. |
| [ExecuteCodeData](./sys_operation/result.md) | Code execution result data. |
| [ExecuteCodeChunkData](./sys_operation/result.md) | Chunk data during streaming execution. |
| [ExecuteCodeResult](./sys_operation/result.md) | Final result type for code execution. |
| [ExecuteCodeStreamResult](./sys_operation/result.md) | Result type for streaming code execution. |
| [ExecuteCmdData](./sys_operation/result.md) | Shell command execution result data. |
| [ExecuteCmdChunkData](./sys_operation/result.md) | Shell streaming execution chunk data. |
| [ExecuteCmdResult](./sys_operation/result.md) | Final result type for Shell execution. |
| [ExecuteCmdStreamResult](./sys_operation/result.md) | Result type for streaming Shell execution. |
| [FileSystemItem](./sys_operation/result.md) | Basic attributes of files or directories. |
| [FileSystemData](./sys_operation/result.md) | Result data for listing files or directories. |
| [SearchFilesData](./sys_operation/result.md) | Result data for searching files. |
| [ReadFileData](./sys_operation/result.md) | Read file data. |
| [ReadFileChunkData](./sys_operation/result.md) | Read file chunk data. |
| [WriteFileData](./sys_operation/result.md) | Write file data. |
| [UploadFileData](./sys_operation/result.md) | Upload file data. |
| [UploadFileChunkData](./sys_operation/result.md) | Upload file chunk data. |
| [DownloadFileData](./sys_operation/result.md) | Download file data. |
| [DownloadFileChunkData](./sys_operation/result.md) | Download file chunk data. |
| [ReadFileResult](./sys_operation/result.md) | Read file result. |
| [ReadFileStreamResult](./sys_operation/result.md) | Streaming read file result. |
| [WriteFileResult](./sys_operation/result.md) | Write file result. |
| [UploadFileResult](./sys_operation/result.md) | Upload file result. |
| [UploadFileStreamResult](./sys_operation/result.md) | Streaming upload file result. |
| [DownloadFileResult](./sys_operation/result.md) | Download file result. |
| [DownloadFileStreamResult](./sys_operation/result.md) | Streaming download file result. |
| [ListFilesResult](./sys_operation/result.md) | List files result. |
| [ListDirsResult](./sys_operation/result.md) | List directories result. |
| [SearchFilesResult](./sys_operation/result.md) | Search files result. |

## Sandbox Mode

Sandbox mode is a runtime mode of SysOperation that routes filesystem, Shell, and code execution capabilities to an isolated environment.

**Sandbox API Documentation**:

| CLASS | DESCRIPTION |
|-------|-------------|
| [sandbox_config](./sys_operation/sandbox/sandbox_config.md) | Sandbox configuration classes |
| [sandbox_registry](./sys_operation/sandbox/sandbox_registry.md) | Sandbox registry |
| [gateway](./sys_operation/sandbox/gateway/gateway.md) | Sandbox gateway |
| [gateway_client](./sys_operation/sandbox/gateway/gateway_client.md) | Sandbox gateway client |
| [launchers/base](./sys_operation/sandbox/launchers/base.md) | Sandbox launcher base class |
| [providers/base_provider](./sys_operation/sandbox/providers/base_provider.md) | Sandbox provider base class |
