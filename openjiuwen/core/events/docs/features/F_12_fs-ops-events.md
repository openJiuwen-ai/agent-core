# EV-12 FS 事件与 `ops.fs` 能力

> 建档日期：2026-10-30
>
> 方案状态：已实现并验证
>
> 计划条目：[Events 特性开发计划 EV-12](../designs/events-development-plan.md#ev-12-fs-事件与-opsfs-能力)
>
> 前置特性：[F_03 Builder/Ops](F_03_ev03-builder-ops.md)、[F_04 EventBus Build 与业务调用集成](F_04_ev04-event-bus-integration.md)
>
> 公共契约：[S_01 Events 公共契约规约](../specs/S_01_events-contract.md)
>
> 验证摘要：`fs.*` 六个事件与 `ops.fs.*` 已实现；专项 23 passed、Events 回归 138 passed（见第 6 节）。

## 1. 问题

插件需要读写文件，但不能直接持有 `SysOperation`：路径解析、沙箱策略、权限与超时都属于平台。插件直接 import 文件系统实现会绕过会话级后端，也无法统一限制访问范围。

因此把文件系统访问做成独立的 `fs.*` 事件族，以 `ops.fs.*` 暴露给 Handler。

## 2. 目标与范围

### 2.1 目标

1. 提供 `fs.read` / `fs.write` / `fs.list` / `fs.stat` / `fs.exists` / `fs.ancestors` 六个内建事件，由 CORE 真实执行。
2. 以 `ops.fs.*` 暴露能力，与 `tool.call` 的模型工具调用互不干扰。
3. 后端按会话解析：由 `get_agent_runtime().get(session_id)` 取当前 Agent，用其 `deep_config.sys_operation` 执行；取不到时为 `denied`。
4. 六个动作共用一条路径解析规则：执行前用该 Agent 的 `cwd` / `project_root` / `workspace` 播种 `CwdState`，相对路径在事件内规范化为绝对路径后再交给后端，结束后恢复原值。
5. 单次调用带超时预算（默认 30 秒、上限 10 分钟）；同名 `fs.*` 调用在 `ops.*` 入口不可自递归。

### 2.2 非目标

- 不新增 `stat` 原语：`BaseFsOperation` 只提供 `read_file` / `write_file` / `list_files` / `list_directories` / `search_files` 与上传下载，`fs.stat`、`fs.exists` 由父目录列表推导。
- 不修改 `core/sys_operation/`，沙箱与权限策略保持原有实现。
- 不提供流式读写、上传下载、`fs.disconnect` 等能力。
- 不接入 `tool.call`。

## 3. 设计

### 3.1 事件与 Handler

| 事件 | 层级 | 触发方 | CORE 实现 |
| --- | --- | --- | --- |
| `fs.read` | CORE | `ops.fs.read`、业务直接 `trigger` | `FsService.read` → `read_file` |
| `fs.write` | CORE | `ops.fs.write`、业务直接 `trigger` | `FsService.write` → `write_file` |
| `fs.list` | CORE | `ops.fs.list`、业务直接 `trigger` | `FsService.list` → `list_files` + `list_directories` |
| `fs.stat` | CORE | `ops.fs.stat`、业务直接 `trigger` | `FsService.stat` → 父目录列表推导 |
| `fs.exists` | CORE | `ops.fs.exists`、业务直接 `trigger` | `FsService.exists` → 父目录列表推导 |
| `fs.ancestors` | CORE | `ops.fs.ancestors`、业务直接 `trigger` | `FsService.ancestors` → 逐级 `list_files` + `read_file` |

六个事件统一使用 `no_match_pattern`，即 Handler 不声明匹配条件时才参与该事件。事件类型注册字段级输入 Validator 和输出类型 Validator；非法输入在事件入口被拒绝（`StatusCode.EVENTS_PAYLOAD_INVALID`），不进入 Handler，`ops.fs.*` 与直接 `bus.trigger` 行为一致。

不注册默认 BUILTIN 策略 Handler。USER / BUILTIN Handler 仍可匹配并短路返回自己的结果；若它们通过 `ops.fs.*` 发起同名调用，`OpsCallGuard` 会拒绝，不会形成递归。

### 3.2 数据模型

- `FsReadInput(path, mode, head, tail, line_range, encoding, chunk_size, only_read, timeout_ms)` / `FsReadOutput(status, path, content, mode, error)`
- `FsWriteInput(path, content, mode, append, prepend_newline, append_newline, create_if_not_exist, permissions, encoding, timeout_ms)` / `FsWriteOutput(status, path, size, mode, error)`
- `FsListInput(path, recursive, max_depth, sort_by, sort_descending, file_types, include_files, include_dirs, max_entries, timeout_ms)` / `FsListOutput(status, root_path, entries, total_count, truncated, error)`
- `FsStatInput(path, timeout_ms)` / `FsStatOutput(status, path, found, kind, size, modified_time, error)`
- `FsExistsInput(path, timeout_ms)` / `FsExistsOutput(status, exists, error)`
- `FsAncestorsInput(names, start, stop_at, max_files, max_bytes_per_file, encoding, timeout_ms)` / `FsAncestorsOutput(status, entries, truncated, skipped, error)`
- `FsEntry(name, path, is_directory, size, modified_time, suffix)`、`FsAncestor(name, path, content)`
- `FsStatus`：`ok` / `denied` / `timeout` / `error`；`OpsError(kind, message)` 承载失败原因。

`status=ok` 表示事件链执行完毕。`fs.stat` 用 `found=False`、`fs.exists` 用 `exists=False` 表达路径确实不存在；父目录列举失败时不返回 `ok`，而是 `error` 并带上后端原因，避免把无法确认的路径当成不存在。

`truncated` 只在确实丢弃内容时置位：`fs.list` 命中 `max_entries`，`fs.ancestors` 命中 `max_files` 或单文件超过 `max_bytes_per_file`。`fs.list` 的任一列举失败时返回 `error`，已获取的 `entries` 保留；`fs.ancestors` 的起始目录列举失败直接返回 `error`，父级失败与指令文件读取失败均计入 `skipped`。

| `status` | 典型 `error.kind` | 含义 |
| --- | --- | --- |
| `ok` | - | 事件链执行完毕 |
| `denied` | `reentrant_call` / `sys_operation_unavailable` | 同名重入或无可用后端 |
| `timeout` | `timeout` | CORE 超时预算耗尽 |
| `error` | `fs_error` | 底层文件系统调用失败 |

### 3.3 能力装配

`handler_ops/fs_ops.py` 在 `ops.build` 的 BUILTIN 阶段向 Builder 添加 `fs` 命名空间与六个方法。方法收到当前 Handler 的 `EventHandlerOps`，由 `FsOpsFacade.from_ops` 生成调用上下文后触发对应事件。

`FsOpsFacade` 负责 `OpsCallGuard` 重入与深度保护、结果类型校验；同名重入不抛异常，返回 `status=denied`（`error.kind=reentrant_call`）。字段校验只在事件入口执行一次。

超时由 `fs_events.py` 的 CORE Handler 统一包裹；`ops.fs.*` 与直接 `bus.trigger` 使用同一预算，超时返回 `status=timeout`（`error.kind=timeout`）。

`FsService.from_ops` 由 `runtime.resolve_agent(session_id)` 取 Agent，再用 `runtime.resolve_sys_operation(agent)` 取后端；Agent 未绑定或未配置 `sys_operation` 时返回 `status=denied`（`error.kind=sys_operation_unavailable`）。

```python
async def load_instructions(ops: EventHandlerOps) -> str:
    result = await ops.fs.read(FsReadInput(path="AGENTS.md"))
    if result.status is not FsStatus.OK:
        message = result.error.message if result.error else "fs.read failed"
        raise RuntimeError(message)
    return result.content if isinstance(result.content, str) else ""
```

### 3.4 调用约束

- 路径：CORE 进入时由 `agent_working_dir` 播种 `CwdState`；`SessionContext.cwd` 为空时由 `runtime.build_context` 回填播种后的工作目录。六个动作都用该值把相对路径规范化为绝对路径，避免 `stat` 与 `read` 落到不同目录。调用结束后恢复进入前的 `cwd`、`original_cwd` 与其余工作目录字段。
- `fs.write` 默认 `prepend_newline=False`、`permissions="644"`：事件语义是写入给定内容，不继承 `write_file` 的追加式默认换行；需要与底层默认一致时显式开启。
- `fs.list` 分别调用 `list_files` / `list_directories` 后合并，再按 `sort_by` 统一排序；`include_dirs` 默认关闭。
- `fs.ancestors` 从 `start`（默认 cwd）向上遍历到 `stop_at` 或磁盘根，结果按由外到内返回。
- `fs.*` 只依赖 Agent 的 `sys_operation`，不经过 `ResourceMgr`，与 `AbilityManager` 无关。

## 4. 实现清单

| 文件 | 职责 |
| --- | --- |
| `builtins/event_types/fs_events.py` | 六个事件类型注册、字段/输出 Validator、超时预算与 CORE Handler |
| `builtins/handler_ops/fs_ops.py` | `ops.build` 注入 `fs` 命名空间 |
| `ops_impl/fs/models.py` | 输入输出模型与 `FsStatus` |
| `ops_impl/fs/facade.py` | 插件入口：重入保护、结果类型校验 |
| `ops_impl/fs/service.py` | 六个动作执行、路径解析与目录推导 |
| `ops_impl/{common,runtime,guard}.py` | 共享错误与超时上限、Agent / 上下文解析、重入保护 |

新增命名空间只需增加 `*_events.py`、`*_ops.py` 与 `ops_impl/<namespace>/`，事件总线与已有命名空间无需修改。

## 5. 关键决策

1. `fs.*` 走 CORE 真实执行，与 Mods 的 `core` 层一致；不注册默认 BUILTIN 策略 Handler，避免重复一层拒绝逻辑。
2. 不新增 `stat` 原语：`fs.stat` / `fs.exists` 用父目录列表推导，只返回能真实产出的 `kind` / `size` / `modified_time`；`real_path`、`is_symlink`、`mtime_ns` 暂不提供。
3. `fs.*` 不经过 `AbilityManager`，不改变模型工具列表。
4. 后端只来自 `AgentRuntime` 绑定的 Agent；不再扫描 `ResourceMgr` 的 LOCAL 后端，避免多会话共用一个后端。
5. 事件内统一解析路径，而不是把相对路径交给后端，保证六个动作使用同一 cwd。
6. 超时只在 CORE Handler 包裹一次，`ops.fs.*` 与直接 `trigger` 使用同一预算；不把 `timeout_ms` 透传给底层接口。
7. 字段校验只在事件入口执行一次，Facade 不重复校验。
8. 重入保护按 `event_type` + asyncio 任务上下文，深度上限 8。

## 6. 验证

```text
python -m pytest tests/unit_tests/core/events/test_fs_ops_events.py -q -o addopts=""
python -m pytest tests/unit_tests/core/events -q -o addopts=""
python -m ruff check openjiuwen/core/events tests/unit_tests/core/events
```

结果：FS 专项 23 passed；Events 回归 138 passed。用例覆盖 `ops.build` 命名空间、读取、缺后端降级、写入与 `stat`/`exists` 往返、目录合并、`max_entries` 截断、缺省排除目录、不存在路径、列举失败、`ancestors` 顺序、`ancestors` 名称大小写与 `stop_at` 边界、直接触发与 `ops.fs.*` 超时、非法字段、读取窗口校验、目录部分失败、起始目录不可读、`skipped` 计数、同名重入、工作目录还原。

## 7. 待确认

- `fs.list` 的 `truncated` 只统计 `max_entries`，后端递归深度截断不回流。
- `fs.ancestors` 未传 `stop_at` 时会一直向上遍历到磁盘根；插件应显式传入项目边界。
- 目录访问范围不在本特性：是否允许某个根目录仍由 `SysOperation` 的沙箱与权限配置决定。
- `fs.*` 依赖 `AgentRuntime`：`TeamAgent` 尚未 bind，team 模式暂时返回 `denied`。
- 事件任务内的 `CwdState` 由 CORE 播种并在调用后恢复（含 `original_cwd`），同一任务内串行执行的多个会话不会互相污染。
