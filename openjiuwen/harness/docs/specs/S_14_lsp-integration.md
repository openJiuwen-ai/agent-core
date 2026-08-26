# S_14 LSP 集成

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/lsp/`（`core/` + `servers/` + `types.py`）、`openjiuwen/harness/tools/lsp_tool/` |
| 最近一次修订日期 | 2026-08-23 |
| 关联 feature | N/A |

## 范围 / 边界

本规约定义 harness 的 LSP（Language Server Protocol）集成：子系统生命周期、服务器管理、
诊断、工具面。`lsp/` 是本仓库对 LSP 的封装，DeepAgent 经 `LspRail`（`S_04`）与
`LspTool`（`S_05`）消费。

具体覆盖：

- `lsp/__init__.py`：`initialize_lsp` / `shutdown_lsp` / `get_lsp_status` /
  `get_lsp_tool` / `get_pending_lsp_diagnostics` + 导出面（`__version__ = "0.1.10"`、
  `CustomServerConfig` / `InitializeOptions` / `InitializeResult` / `LspStatus` /
  `LspOperation` / `LSPServerManager` / `MAX_LSP_FILE_SIZE_BYTES` /
  `filter_git_ignored_locations` / 诊断注册表族）。
- `lsp/core/manager.py`：`LSPServerManager`（初始化 / 启动 / 停止 / 状态 / 打开更改文件）。
- `lsp/core/client.py` / `instance.py` / `diagnostic_registry.py` / `types.py`：
  LSP 客户端、实例、诊断注册表（`LspDiagnosticRegistry` / `LspDiagnosticFile` /
  `LspDiagnosticItem` / `MAX_DIAG_PER_FILE=10` / `MAX_DIAG_TOTAL=30`）。
- `lsp/core/utils/`: `constants.MAX_LSP_FILE_SIZE_BYTES`、`git_ignore.filter_git_ignored_locations`。
- `lsp/servers/registry.py`：`nearest_root` / `build_configs`（服务器发现）。
- `tools/lsp_tool/`：`LspTool` / `LspToolMetadataProvider`（工具面，契约见 `S_05`）。

不在本规约范围内：
- `LspRail` 的 priority / 事件路由 —— `S_04`。
- `LspTool` 工具描述与 schema —— `S_05`。
- LSP 服务器二进制 / 协议细节 —— 第三方。

## 不变量

1. **生命周期唯一**：`initialize_lsp(options)`（幂等、懒启动混合模式——初始化只建配置映射，
   服务器按需启动）→ `LSPServerManager.get_instance()`；`shutdown_lsp()` 停全部服务器进程。
   `initialize` / `shutdown` 是模块级唯一入口。
2. **服务器按需**：`get_or_start_server(file_path)` 首次需要时启动 LSP 服务器；
   `_path_belongs_to_root(file_path, root)` 约束文件必须属于 workspace root。
   `nearest_root` / `build_configs`（`servers/registry.py`）决定用哪个服务器配置。
3. **诊断注册表全局唯一**：`LspDiagnosticRegistry.get_instance()` 是单例；
   `get_pending_lsp_diagnostics(max_per_file, max_total)` **读取并清空**队列；
   诊断上限 `MAX_DIAG_PER_FILE=10` / `MAX_DIAG_TOTAL=30`；按 severity Error 优先排序。
   `filter_git_ignored_locations` 在收集时过滤 git-ignore 文件。
4. **状态查询**：`get_lsp_status() -> LspStatus`（`initialized` + `servers: list[LspServerStatus]`）；
   `get_instance() is None` 时返回 `LspStatus(initialized=False, servers=[])`。
5. **文件合约**：`open_file(file_path, language_id)` / `change_file(...)` /
   `send_request(...)` 是 manager 的文件操作面；文件大小超 `MAX_LSP_FILE_SIZE_BYTES` 不进
   LSP（`is_file_open` 状态可查）。
6. **工具面描述唯一**：`get_lsp_tool() -> dict`（`name`/`description`/`input_schema`）经
   `tools/lsp_tool/_tool.build_lsp_tool()` 构造；`LspToolMetadataProvider` 是
   `S_05` 不变量 7 的元数据提供者。
7. **导出面**：`lsp/__init__.py` 的 `__all__` 是契约（见上覆盖列表）；`LspOperation`（enum）
   定义工具操作类型（从 `tools/lsp_tool/_schemas` 导入）。

## 接口契约

```python
async def initialize_lsp(options: InitializeOptions | None = None) -> InitializeResult
async def shutdown_lsp() -> None
def get_lsp_status() -> LspStatus
def get_lsp_tool() -> dict[str, Any]
def get_pending_lsp_diagnostics(max_per_file: int = MAX_DIAG_PER_FILE,
                                max_total: int = MAX_DIAG_TOTAL) -> list[LspDiagnosticFile]

class LSPServerManager:
    @classmethod
    async def initialize(cls, options: InitializeOptions | None = None) -> InitializeResult
    @classmethod
    async def shutdown(cls) -> None
    @classmethod
    def get_instance(cls) -> LSPServerManager | None
    async def get_or_start_server(self, file_path: str) -> LSPServerInstance | None
    def get_pending_diagnostics(self, ...) -> Any
    def is_file_open(self, uri: str) -> bool
    async def open_file(self, file_path: str, language_id: str) -> None
    async def change_file(self, ...) -> None
    async def send_request(self, ...) -> Any
    def get_status(self) -> list[LspServerStatus]

class InitializeOptions:
    cwd: str | None = None
    custom_servers: dict[str, CustomServerConfig] | None = None

class CustomServerConfig:
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    extensions: list[str] | None = None
    language_id: str | None = None
    initialization_options: dict | None = None
    disabled: bool = False
```

错误 / 返回语义：

- `initialize_lsp` 返回 `InitializeResult(success, servers_loaded, duration_ms)`；失败
  `success=False`（不抛）。
- `get_or_start_server` 无匹配服务器 → `None`（不抛）。
- `get_pending_lsp_diagnostics` 无 pending → 空 list。
- `send_request` 服务器未就绪 → 抛 / 返回错误（以 manager 实现为准）。

## 数据结构

### LSP 状态

| 对象 | 语义 |
|---|---|
| `LspStatus` | `initialized` + `servers: list[LspServerStatus]` |
| `LspServerStatus` | 单服务器状态（`lsp/core/types.py`） |
| `LspDiagnosticFile` / `LspDiagnosticItem` | 诊断按文件归组 + 单条诊断（severity / message / range） |

### 诊断管线

`didOpen/didChange` → 服务器 `publishDiagnostics` → `LspDiagnosticRegistry`（去重、封顶）→
`get_pending_lsp_diagnostics`（host 每轮 drain）→ `LspRail` 注入 prompt（`S_04`）。

## 与其它 spec 的关系

- `LspRail`（priority 60）注入诊断 prompt section —— `S_04`。
- `LspTool` / `LspToolMetadataProvider` 工具面 —— `S_05`。
- 诊断消费方是 `S_02` 的上下文（diagnostic 注入 prompt → `S_06`）。
- 与 `agent_teams` 侧 LSP / CLI 互操作是独立实现（`openjiuwen/agent_teams/mcp` 等）。
