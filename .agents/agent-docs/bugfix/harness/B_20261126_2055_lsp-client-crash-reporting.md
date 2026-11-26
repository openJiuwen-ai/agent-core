---
id: B_20261126_2055_lsp-client-crash-reporting
title: LSP 客户端 EOF 崩溃不上报、超时请求条目泄漏与 Windows 文件 URI 未编码
type: B
author: linghb
author_email: 957733934@qq.com
modules:
  - openjiuwen/harness
files:
  - openjiuwen/harness/lsp/core/client.py
  - openjiuwen/harness/lsp/core/utils/file_uri.py
  - tests/unit_tests/harness/lsp/test_lsp_client.py
designs: []
design_note: 对 LSP 客户端通信层的三处确定性缺陷修复，不改变对外契约，无需独立设计。
---

# LSP 客户端 EOF 崩溃不上报、超时请求条目泄漏与 Windows 文件 URI 未编码

## 现象与根因

三处相互独立的缺陷，同属 `lsp/core` 通信层：

1. **EOF 不上报崩溃**。`LSPClient._read_loop` 在 stdout EOF（服务进程退出，
   最常见的崩溃方式）时直接 `return`，绕过 `_on_crash`。`_on_crash` 只在读循环
   抛异常时触发，导致 `LSPServerInstance._handle_crash` 在绝大多数崩溃场景下
   从不执行：in-flight 请求的 future 不会被 `ConnectionError` 立即唤醒，而是空等
   `DEFAULT_REQUEST_TIMEOUT_MS = 15_000` 毫秒超时；`MAX_CRASH_RECOVERY_ATTEMPTS`
   的崩溃恢复计数也形同虚设。

2. **超时请求条目泄漏**。`_rpc_request` 的 `on_timeout` 只给 future 置异常，不从
   `_pending` 移除条目；迟到的响应到达时 `_dispatch` 两个分支看到 `future.done()`
   都直接 `return`，也不移除。条目在服务器存活期间永久驻留 `_pending`。

3. **Windows 文件 URI 未编码**。`path_to_file_uri` 的 Windows 分支不做百分号编码，
   含空格、`#`、`%` 的路径产出非法 URI；`#` 会被严格解析的服务端当作 fragment
   截断（实测 `C:\Users\John Doe\proj#1` 生成 `file:///C:/Users/John Doe/proj#1`，
   `urlsplit` 后只剩 `/C:/Users/John Doe/proj`）。

## 修复决策

1. EOF 分支改为 `self._on_crash(self._process.returncode)` 后再返回。安全性依据：
   `_crash_reported` 标志保证回调至多触发一次；优雅停路径 `stop()` 先 cancel
   reader task（走 `CancelledError` 分支），且 `_handle_crash` 对 STOPPING 状态
   早退，不会误伤优雅关停。
2. `on_timeout` 置异常后补 `self._pending.pop(msg_id, None)`；`_dispatch` 的两个
   `future.done()` 分支改为先 pop 再 return。
3. Windows 分支复用 POSIX 分支的 `urllib.parse.quote(..., safe="/:")`，`safe` 保住
   驱动器冒号与分隔符；普通路径输出不变（`file:///C:/work/proj/main.py` 保持原样），
   `file_uri_to_path` 解码端本就支持百分号解码，往返兼容。

## 回归证据

基线为 `46014823e`（develop HEAD），环境为 Windows、Python 3.11、uv 同步测试环境。

新增测试 `tests/unit_tests/harness/lsp/test_lsp_client.py` 共 8 项：
- 修复前 5 失败 3 通过（EOF 不触发回调、pending future 空等、条目泄漏、URI 未编码
  四个断言组全部命中预期失败）；
- 修复后 8 项全部通过。

既有 LSP 回归无破坏：

```powershell
.venv/Scripts/python.exe -B -m pytest tests/unit_tests/harness/lsp/test_lsp_client.py tests/unit_tests/harness/tools/test_lsp_diagnostics.py tests/unit_tests/harness/tools/test_lsp_tool.py tests/unit_tests/harness/rails/test_lsp_rail.py -q
```

结果 144 项通过（新增 8 + 既有 136），0 失败。URI 解码端额外验证
`file:///C:/work/proj/main.py` 与 `file:///d%3A/foo` 解码不变。

## 遗留

- `_dispatch` 对 server-initiated request 分发的 `asyncio.create_task` 未持引用，
  属既有行为，本次不动。
- 优雅 `stop()` 中 shutdown 请求仍受 2 秒超时约束，行为未变。
