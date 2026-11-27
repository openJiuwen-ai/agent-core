---
id: D-20261124-0140-mcp-timeout-errors
title: MCP 连接与调用超时异常标准化
slug: mcp-timeout-errors
status: in-development
stage: active
author: yingchen03
author_email: yingchen.mail@qq.com
reviewers: [ecc:architect]
created: 2026-11-24
updated: 2026-11-24
domain: core
modules: [core, harness]
tags: [mcp, exceptions, timeout, cancellation]
scope: MCP transport 客户端公开错误契约；不改变 agent 中断恢复和连接生命周期模型
related: []
type: D
files: []
---

# MCP 连接与调用超时异常标准化设计方案

## 1. 背景与目标

官方 issue #1977（GitHub #1744）要求直接持有 `McpClient` 的网关能够按类型捕获连接和调用超时。当前官方 `develop` 已修复部分 owner-task 生命周期问题，但 `connect()` 的超时仍记录为普通 `TimeoutError` 后返回 `False`，其他操作传播底层 `TimeoutError`、`RuntimeError` 或异常组；Stdio 的 `call_tool()` 未应用调用方传入的 deadline。

目标是提供 `McpTimeoutError` / `McpConnectionError`，同时遵守仓内统一 `StatusCode` / `BaseError` 体系。异常必须能被 `except Exception` 捕获，并保留原始原因供日志定位。

本方案是独立新决策。检索现有 specs、designs 和 bugfix，未发现拥有 MCP 公共异常契约的记录；相关取消恢复记录属于 ContextEngine，不覆盖本 issue。

## 2. 范围与关键契约

### 2.1 本次包含

- Streamable HTTP、SSE、Stdio 和 Playwright 的 `connect()`、`call_tool()`、`list_tools()`、`list_resources()`、`read_resource()` 公共边界。
- 复用 `McpClient` 的公共异常转换辅助，避免各 transport 重复解释异常组。
- 已注册且已连接的 client 失去 session 时，以 `McpConnectionError` 表达，而不是裸 `RuntimeError`。
- browser_move 两个覆写 transport 与核心异常类型保持一致。
- 新增 TOOL 范围内的 MCP 连接和超时状态码，公开导出异常，补中英文 API 文档和行为测试。

### 2.2 非目标

- 不重写 SSE / Streamable HTTP 的 owner-task，不合并 reconnect 策略，不修改 `disconnect()` 的 best-effort 返回语义。
- 不把 agent / AbilityManager 的取消恢复机制改成异常重试。
- 不改变 OpenAPI 的既有 `ExecutionError` 边界。
- 不承诺第三方自定义 transport 抛出的所有 `RuntimeError` 都是连接错误；只转换本仓 client 的 guard、已知 transport 错误和 cancel-scope 清理错误。

## 3. 关键决策

### D-01 统一异常与框架错误码共存

`McpConnectionError` 继承 `ExecutionError`；`McpTimeoutError` 同时继承 `McpConnectionError` 和内置 `TimeoutError`。两个异常通过 `build_error()` 映射到新增 `StatusCode`，保留 `code`、`status`、`cause`、`__cause__` 和 `recoverable=True`。`TimeoutError` 多继承仅用于兼容已有捕获，不创建第二套错误模型。

新增 `TOOL_MCP_CONNECTION_ERROR` 和 `TOOL_MCP_EXECUTION_TIMEOUT`。使用 TOOL 范围而不是 RESOURCE 范围，因为异常产生于底层 client，ResourceManager 的连接失败错误码属于更高一层注册操作。

### D-02 明确超时、连接失败和取消的区别

- `client.connect(timeout=...)` 内部 deadline 到期：清理部分连接状态，记录 `_last_connect_error`，抛 `McpTimeoutError`。
- `connect()` 的非超时失败：继续返回 `False`。已识别的 transport 失败在 `_last_connect_error` 保存 `McpConnectionError`；其他配置、鉴权或启动错误保留原始异常，不误分类为 transport 失败。ResourceManager 对 `McpConnectionError` 仍映射为 `RESOURCE_MCP_SERVER_CONNECTION_ERROR`。
- 已连接操作的 deadline：抛 `McpTimeoutError`，不自动重试该超时；远端是否已执行工具不可由 client 推断。
- 已连接操作的已知 transport / cancel-scope 失败：抛 `McpConnectionError`，保留 reconnect 依据原始 `cause` 的判定。
- 调用方直接 `task.cancel()`，或外层 `asyncio.wait_for(client.connect(), ...)` 取消整个调用：传播 `asyncio.CancelledError`，不转换成超时。
- 异常组只含 `CancelledError`：传播取消；混合异常组保留非取消子异常，只有证据表明属于 transport 清理时才转换为连接错误。

### D-03 在调用链的适当位置转换

连接错误在公共 `connect()` 边界转换，使 owner-task 返回的异常不会绕开转换。普通操作的错误在 transport 边界转换，再由 reconnect 装饰器依据 `McpConnectionError.cause` 做既有重连判定；业务 `McpError` 和输入校验异常原样传播。转换器遇到已经是目标类型的错误时原样返回，不重复包装。

不采用仅新增异常类但不接线、全量捕获 `RuntimeError`、把所有 `CancelledError` 转超时、或在 ResourceManager 层才转换的方案。这些方案分别无法满足直接调用方、会误分类业务失败、破坏取消语义或覆盖不到自持客户端。

## 4. 模块与兼容

| 位置 | 责任 |
| --- | --- |
| `core/foundation/tool/mcp/errors.py` | 公开异常 re-export |
| `core/common/exception/codes.py`、`status_mapping.py` | 状态码与 `ExecutionError` 子类映射 |
| `core/foundation/tool/mcp/client/mcp_client.py` | 公共连接边界与转换入口 |
| 四个核心 transport | 调用 deadline、guard 和失败分类 |
| `client/reconnect.py` | 按原始 cause 维持可重试 transport 判定 |
| 两个 browser_move transport | 覆写方法的同等错误契约 |
| `core/foundation/tool/__init__.py` | 公共导出 |

`connect()` 成功仍返回 `True`，非超时失败仍返回 `False`。新增超时异常是该 issue 明确要求的行为变化；旧的 `except TimeoutError` 仍可捕获。`MCPTool.invoke()` 的外层 `TOOL_MCP_EXECUTION_ERROR` 包装暂不改变，避免扩大上层 ToolError 契约；直接 client 调用获得新类型。

## 5. 验证设计与通过条件

| ID | 场景 | 通过条件 |
| --- | --- | --- |
| V-01 | 四个核心 client 的内部连接 deadline | 公开 `connect()` 抛 `McpTimeoutError`，可由 `Exception` 和 `TimeoutError` 捕获；原因链存在；清理被调用 |
| V-02 | 四个 client 的 `call_tool(timeout=...)` | deadline 实际生效，抛 `McpTimeoutError`，不改变业务结果 |
| V-03 | 非超时连接失败 | 返回 `False`；已知 transport 失败的 `_last_connect_error` 为 `McpConnectionError`，其他失败保留原始异常 |
| V-04 | 未连接、已知 transport 断开和 cancel-scope 错误 | 已连接操作抛 `McpConnectionError`；业务错误不误转换 |
| V-05 | 调用方 task 取消、纯取消异常组 | `CancelledError` / 取消组传播，不被转成连接或超时错误 |
| V-06 | SSE / Streamable HTTP 自动重连 | 既有可重试 transport 错误仍能重连；超时不触发盲目重试 |
| V-07 | browser_move 覆写 | 与核心 client 相同的公开错误类型，无独立异常方言 |
| V-08 | 本地真实 MCP server | 至少一种真实 transport 的连接或调用超时可通过公开类型捕获，无外部服务依赖 |

单元测试通过 fake SDK session 控制等待和异常，不依赖墙钟长等待。真实探针使用临时本地 server，结束后关闭 client 和 server。按仓内约束不运行 formatter / static checker。

## 6. 文档与交接

新增本设计和实现后的 bugfix 记录；公开中英文 tool API 文档补异常用途和示例。设计阶段不代表代码、测试、PR 或合入已完成。用户在本次任务中明确要求完成修复并创建供 committer 审核的 PR，因此进入实现和 PR 创建；未授权自行合入。
