---
id: B_20261124_1230_mcp-timeout-and-connection-errors
title: MCP 连接与调用超时异常标准化修复
type: B
author: yingchen03
author_email: yingchen.mail@qq.com
modules:
  - core/foundation/tool/mcp
  - core/common/exception
  - core/runner/resources_manager
  - harness/tools/browser_move
files:
  - openjiuwen/core/common/exception/codes.py
  - openjiuwen/core/common/exception/errors.py
  - openjiuwen/core/common/exception/status_mapping.py
  - openjiuwen/core/foundation/tool/mcp/errors.py
  - openjiuwen/core/foundation/tool/mcp/client/mcp_client.py
  - openjiuwen/core/foundation/tool/mcp/client/stdio_client.py
  - openjiuwen/core/foundation/tool/mcp/client/sse_client.py
  - openjiuwen/core/foundation/tool/mcp/client/streamable_http_client.py
  - openjiuwen/core/foundation/tool/mcp/client/playwright_client.py
  - openjiuwen/core/foundation/tool/mcp/client/reconnect.py
  - openjiuwen/core/runner/resources_manager/tool_manager.py
  - openjiuwen/harness/tools/browser_move/clients/stdio_client.py
  - openjiuwen/harness/tools/browser_move/clients/streamable_http_client.py
  - tests/unit_tests/core/foundation/tool/mcp/client/test_mcp_operation_errors.py
  - tests/unit_tests/core/foundation/tool/mcp/client/test_playwright_client.py
  - tests/unit_tests/core/foundation/tool/test_streamable_http_client.py
  - tests/unit_tests/core/runner/test_tool_manager_mcp_dedup.py
  - tests/unit_tests/harness/tools/browser_move/test_browser_task_integrity.py
  - tests/unit_tests/harness/tools/browser_move/test_config.py
designs:
  - D-20261124-0140-mcp-timeout-errors
design_note: 关联本 issue 的公开异常契约设计；实施偏差和验证限制记录于此。
---

# MCP 连接与调用超时异常标准化修复记录

## 现象与影响

官方 issue #1977 要求直接持有 `McpClient` 的网关能按类型处理连接与调用超时。基线实现可能传播底层 `TimeoutError`、异常组或 anyio teardown 错误，也可能让 `asyncio.CancelledError` 穿透。普通 `except Exception` 无法接住 `CancelledError`，网关也难以区分内部 deadline 和调用方主动取消。

Stdio、Playwright 以及 browser_move 的覆写 client 还存在 timeout 参数未落实到工具调用、连接 deadline 返回 `False` 或超时转换为普通 `RuntimeError` 的差异。调用方无法使用统一的恢复策略。browser_move 的点击、滚动和代码执行在超时后尤其不能自动重放，因为远端可能已执行动作。

## 关联设计与当前契约

关联 `D-20261124-0140-mcp-timeout-errors`。公开异常继承框架 `ExecutionError`，而不是另建与 `StatusCode` 脱钩的异常体系：

- `McpTimeoutError` 同时继承 `McpConnectionError` 和内置 `TimeoutError`，状态码为 `TOOL_MCP_EXECUTION_TIMEOUT=182302`。
- `McpConnectionError` 使用 `TOOL_MCP_CONNECTION_ERROR=182303`，原始异常保存在 `cause` 和 `__cause__`。
- `connect()` 成功仍返回 `True`；非超时失败仍返回 `False`。内部 deadline 抛出 `McpTimeoutError`，同一异常对象保存在 `_last_connect_error`。
- ResourceManager 收到 `McpConnectionError` 时仍映射为 `RESOURCE_MCP_SERVER_CONNECTION_ERROR=110511`，不退化为通用注册错误。
- caller cancellation 原样传播。SDK 业务 `McpError` 不被无差别包装。已知 transport 断开继续沿用 reconnect，超时不触发自动重放。

设计中的 V-03 原先把所有非超时连接失败描述为 `_last_connect_error=McpConnectionError`。实施保留既有兼容行为：只有已识别的 transport 失败转换为该类型，其他配置、鉴权或启动错误保留原始异常。

## 根因与修复决策

公共转换入口 `raise_mcp_operation_error()` 统一处理 timeout、异常组、已知 transport 错误和取消。各 transport 在公共边界调用它，避免每种 client 独立解释 SDK 异常。

`asyncio.timeout()` 用于本任务内的 deadline，避免 `asyncio.wait_for()` 新建任务破坏 anyio cancel-scope ownership。Stdio 的 transport startup 和 `initialize()` 共用连接 deadline，工具调用实际应用 `timeout` 参数。Playwright 与 browser HTTP 仅在调用方显式传入正数 timeout 时安装 deadline，`NO_TIMEOUT` 保持原先不限时语义。普通连接失败的 `False` 契约保持不变；cleanup 失败不覆盖原始异常。

`reconnect.py` 对 `McpConnectionError` 递归检查原始 `cause`。这样既保留已知断连的重连能力，也阻止 `McpTimeoutError` 被当成普通断连自动重试。cancel-scope teardown 错误仍分类为 `McpConnectionError`，但不加入通用 retryable marker，避免在工具结果未知时重放。没有把所有 `RuntimeError` 都视为连接失败，因为业务错误和参数错误不应进入 transport 恢复策略。

browser_move 两个覆写 client 复用同一异常类型。Stdio 的副作用操作超时保留“execution may have occurred, reconcile state before repeating an action”提示。强制关闭期间 owner 仍在清理时保留其引用并拒绝重连，防止旧 owner 清理覆盖新会话；调用方取消仍原样传播。Streamable HTTP 对点击等不安全动作不执行超时或断连重放，查询类 retry-safe 动作仍保留原策略。

## 回归证据与适用范围

测试提交 `8e0529975`（rebase 前为 `a0f712fb9`）固定了公开异常、Stdio/SSE deadline、取消和 reconnect 的 RED 复现。修复过程中曾出现 7 个旧契约/实现失败，随后修正普通连接失败、混合异常组和取消清理路径。

2026-11-24，在 Windows 11、Python 3.11.15 和 worktree `.venv` 中：

- 核心 MCP、ResourceManager、Playwright 的定向回归曾为 `96 passed`。
- browser_move 的 `test_config.py` 与 `test_browser_task_integrity.py` 首轮合并运行结果为 `59 passed, 3 warnings`。
- 新增 Browser Stdio caller-cancellation 用例后，`test_browser_task_integrity.py` 单独运行结果为 `29 passed, 3 warnings`。
- 一次包含核心和 browser_move 的 158 项回归先得到 `154 passed, 4 failed`。三项失败来自测试夹具缺少 `_name`，一项来自旧的“超时后重试成功”期望；修正测试后，上述 59 项和 29 项通过。
- 修正后的完整合并回归在旧基线上曾得到 `160 passed, 3 warnings in 18.15s`。rebase 到官方最新 `develop=83b859c87` 并合并官方 SSE lazy reconnect 修复后，再次运行结果为 `163 passed, 3 warnings in 27.19s`。新增 3 项来自官方基线。范围包括核心 MCP client、Streamable HTTP、ResourceManager、Playwright 和 browser_move 两个测试文件。
- 独立 reviewer 对 rebase 后候选给出三个 P1：browser HTTP 在断连后重放副作用动作、`list_tools()` typed failure 绕过注册清理、嵌套异常组未分类。新增测试首轮为 `9 failed, 49 passed`；其中一个取消测试错误地取消了 pytest runner，随后改为探测 `cancelling()` 状态。修复后为 `58 passed, 3 warnings in 4.46s`，最终完整合并回归为 `173 passed, 3 warnings in 10.89s`。
- 同一 reviewer 对三个 P1 的实际修复做 focused re-review，确认全部解决，修复范围内没有新的 P0/P1。该结论是本地独立代码复核，不是官方 committer 审批或 PR 批准。
- `examples/mcp/timeout_error_probe.py` 使用真实本地 FastMCP Stdio server，验证调用超时、调用方取消和连接清理。用户授权后于 2026-11-24 执行两次，均输出 `local stdio MCP timeout and caller-cancellation probes passed`。取消场景清理仍记录一次底层 TaskGroup 异常日志，探针确认最终 `disconnect()` 成功；未把它描述为底层 SDK 完全无 teardown 噪音。
- 官方自动代码审查后新增取消/清理边界用例：HTTP stale-stack 和 initialize 的混合异常组保留 caller cancellation，Stdio watchdog 保留进入时已有 cancellation count，browser owner 未结束时拒绝重连。修正夹具后，定向合并回归为 `185 passed, 3 warnings in 10.07s`。独立 reviewer 对后三项 P1 修复做 focused 静态复核，确认均已解决且所查修复未发现新的 P0/P1；该 reviewer 未独立重跑测试，此结论不是官方批准。
- 对本轮 15 个 Python 改动文件运行 Ruff，import 排序与 F/E 规则检查通过；沿用既有 `timeout` API，未把仓内 ASYNC109 作为本轮全量整改范围。
- 自动文档意见称 PR 仅含 17 文件、缺少 transport 和测试，与 PR 页面和 `origin/develop...HEAD` 的 34 文件 diff 不符，属于误报；不删减正确的文件清单或回归证据。
- 同步官方 `develop=7960a5a10` 时只有 `S_05_tools-contract.md` 修订日期冲突，保留 2026-11-24。普通 merge 后 PR 相对 develop 仍为 34 文件；同一回归集再次得到 `185 passed, 3 warnings in 11.31s`。

单元测试使用 fake SDK transport/session，不访问外部服务。未运行全量 formatter、pylint、mypy、`make check` 或 `make format`，遵守仓内约束。尚未执行全仓测试、真实浏览器验证或外部宿主联调。

## 防错模式与资料同步

中英文 MCP 用户指南新增直接 client 的异常处理示例。`S_05_tools-contract` 明确 browser_move 覆写 client 使用 core 异常类型、超时不重放副作用动作。既有 `F_05_browser-task-integrity` 续写同一安全决策，不另建重复 feature。

本文不声称 PR 创建、官方 reviewer/committer 批准或合入已完成。本地 focused 独立代码复核已完成，范围和结论如上。

## 遗留与后续

- 等待官方 reviewer/committer 审批与新一轮 CI；创建者不自行合入。
- 后续如单独处理 SDK teardown 日志，应先提供真实 Stdio 复现，不与本 PR 的公开异常契约扩展混在一起。
