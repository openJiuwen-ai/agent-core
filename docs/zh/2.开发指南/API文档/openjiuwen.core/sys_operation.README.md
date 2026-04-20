# sys_operation

`openjiuwen.core.sys_operation`是openJiuwen框架的系统操作模块，提供对底层系统资源的受控访问能力，包括文件系统操作、代码执行和Shell命令执行，支持本地和沙箱模式。

**Classes**：

| CLASS | DESCRIPTION |
|-------|-------------|
| [OperationMode](./sys_operation/sys_operation.md) | 系统运行模式枚举。 |
| [LocalWorkConfig](./sys_operation/sys_operation.md) | 本地工作环境配置。 |
| [SandboxGatewayConfig](./sys_operation/sys_operation.md) | 远程沙箱网关连接配置。 |
| [SysOperationCard](./sys_operation/sys_operation.md) | 系统操作配置卡片。 |
| [SysOperation](./sys_operation/sys_operation.md) | 系统操作入口类。 |
| [BaseResult](./sys_operation/result.md) | 所有操作结果的通用泛型基类。 |
| [ExecuteCodeData](./sys_operation/result.md) | 代码执行结果数据。 |
| [ExecuteCodeChunkData](./sys_operation/result.md) | 流式执行时的分片数据。 |
| [ExecuteCodeResult](./sys_operation/result.md) | 代码执行的最终结果类型。 |
| [ExecuteCodeStreamResult](./sys_operation/result.md) | 代码流式执行的结果类型。 |
| [ExecuteCmdData](./sys_operation/result.md) | Shell 命令执行结果数据。 |
| [ExecuteCmdChunkData](./sys_operation/result.md) | Shell 流式执行分片数据。 |
| [ExecuteCmdResult](./sys_operation/result.md) | Shell 执行的最终结果类型。 |
| [ExecuteCmdStreamResult](./sys_operation/result.md) | Shell 流式执行的结果类型。 |
| [FileSystemItem](./sys_operation/result.md) | 文件或目录的基本属性。 |
| [FileSystemData](./sys_operation/result.md) | 列出文件或目录的结果数据。 |
| [SearchFilesData](./sys_operation/result.md) | 搜索文件的结果数据。 |
| [ReadFileData](./sys_operation/result.md) | 读取文件数据。 |
| [ReadFileChunkData](./sys_operation/result.md) | 读取文件分片数据。 |
| [WriteFileData](./sys_operation/result.md) | 写入文件数据。 |
| [UploadFileData](./sys_operation/result.md) | 上传文件数据。 |
| [UploadFileChunkData](./sys_operation/result.md) | 上传文件分片数据。 |
| [DownloadFileData](./sys_operation/result.md) | 下载文件数据。 |
| [DownloadFileChunkData](./sys_operation/result.md) | 下载文件分片数据。 |
| [ReadFileResult](./sys_operation/result.md) | 读取文件结果。 |
| [ReadFileStreamResult](./sys_operation/result.md) | 流式读取文件结果。 |
| [WriteFileResult](./sys_operation/result.md) | 写入文件结果。 |
| [UploadFileResult](./sys_operation/result.md) | 上传文件结果。 |
| [UploadFileStreamResult](./sys_operation/result.md) | 流式上传文件结果。 |
| [DownloadFileResult](./sys_operation/result.md) | 下载文件结果。 |
| [DownloadFileStreamResult](./sys_operation/result.md) | 流式下载文件结果。 |
| [ListFilesResult](./sys_operation/result.md) | 列出文件结果。 |
| [ListDirsResult](./sys_operation/result.md) | 列出目录结果。 |
| [SearchFilesResult](./sys_operation/result.md) | 搜索文件结果。 |

## 沙箱模式

沙箱模式是 SysOperation 的一种运行模式，将文件系统、Shell 和代码执行能力路由到隔离环境中执行。

**沙箱 API 文档**：

| CLASS | DESCRIPTION |
|-------|-------------|
| [sandbox_config](./sys_operation/sandbox/sandbox_config.md) | 沙箱配置类 |
| [sandbox_registry](./sys_operation/sandbox/sandbox_registry.md) | 沙箱注册中心 |
| [gateway](./sys_operation/sandbox/gateway/gateway.md) | 沙箱网关 |
| [gateway_client](./sys_operation/sandbox/gateway/gateway_client.md) | 沙箱网关客户端 |
| [launchers/base](./sys_operation/sandbox/launchers/base.md) | 沙箱启动器基类 |
| [providers/base_provider](./sys_operation/sandbox/providers/base_provider.md) | 沙箱 Provider 基类 |
