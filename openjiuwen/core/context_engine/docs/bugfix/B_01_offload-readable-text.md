# 卸载产物超过 read_file 读取限制时模型无法读回

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-11-25 |
| 严重程度 | 高：正文超过约 10 万字符的工具输出被卸载后，模型无法按 marker 读回原文；没有源文件的输出（命令输出、MCP 返回、网页内容）细节丢失 |
| 范围 | `processor/offload_text.py`（新增）、`processor/forked/base.py`、`processor/forked/offloader/message_offloader.py`、`harness/prompts/sections/reload.py` |
| 测试基线 | `tests/unit_tests/core/context_engine/test_offload_text.py`、`tests/unit_tests/core/context_engine/test_forked_message_offloader.py`、`tests/unit_tests/harness/tools/test_filesystem_tools.py::test_offload_text_mirrors_read_tool_limits`（执行结果见「验证」） |
| 关联 spec | [[S_05]]（不变量 5、7–10） |

## 现象

会话 `officeclaw_760d72aced87f973a15d5663`（2026-10-22，采集包 `office_claw_log_20261022_110021`）：模型用 bash `cat` 读取约 70 万字符的日志并传 `max_output_chars=0`，输出整段进入工具消息，被 `MessageSummaryOffloader` 卸载。模型按 marker 调用 `read_file(.../MessageSummaryOffloader_<handle>.json)`，返回 `File content (710.5KB) exceeds maximum allowed size (256KB)`；随后放弃卸载文件，改为分批读取源文件才完成任务。

## 根因

1. `_write_offload_to_file` 以 `json.dumps(..., indent=2)` 写盘，`messages[].content` 中的换行被转义，整段原文成为 JSON 中的一行。
2. `read_file` 不传 `limit` 时整文件超过 256KB 即拒绝；传 `limit` 时单页按 `len // 4` 估算超过 25000 token 即拒绝，且 `offset` / `limit` 只能按行分页，无法切开这一行。`grep`（`rg --max-columns 500`）对超长行只报命中、不显示内容。正文超过约 10 万字符时，marker 指向的文件无法读回，reload 提示中的"分段读取"与"搜索"两条兜底也不可能成功。
3. 相邻缺陷：经 `sys_operation` 写盘时 `_write_offload_to_file` 不检查返回值，而 `fs().write_file` 失败时返回错误结果、不抛异常，marker 可能指向不存在的文件，"写盘失败保留原文"的既有策略未生效。

## 决策

- `.json` 保持不变（程序回读），另写同名 `.txt` 供模型读回：按真实换行拆行，单行超过 `OFFLOAD_TEXT_WRAP_BYTES` 时在字符边界折行，tool_calls 输出一行摘要；marker `path` 指向 `.txt`，`.txt` 写失败时回退指向 `.json`。文本渲染放进 `asyncio.to_thread`（与同模块规则压缩一致），写盘沿用 `.json` 的写法。
- harness 工具上限在 core 镜像为常量（core 不 import harness），一致性由 harness 侧单测断言。
- forked `MessageSummaryOffloader` 在 marker 前追加读取提示；建议 `limit` 以该处理器的卸载阈值为单页 token 预算，并受 `read_file` 自身预算约束，随模型窗口变化。
- 写盘统一检查返回码，失败记 error 日志；失败后的处理沿用既有策略（`.json` 失败时保留原文）。
- reload 提示改为"先搜索定位，再按建议 `limit` 分页读取"，删除对单行 JSON 无效的"超过大小限制请用 offset 和 limit 分段读取"。

## 拒绝的方案

| 方案 | 拒绝原因 |
|---|---|
| 改 `read_file`，支持行内续读或超长行截断 | 改公共工具语义，影响所有读文件场景与 `edit_file` 的先读校验；另立需求 |
| 新增按 handle 读回的工具（参考 `browser_recall_offload`） | 接入面大（注册、继承、权限、提示词迁移），`grep` 仍不可用；另立需求 |
| 只改 `.json` 格式，使正文保留真实换行 | 破坏 `ContextMessageBuffer` 依赖的 JSON 回读契约 |
| 折行时加续行前缀，支持从 `.txt` 字符级还原 | 精确原文已由 `.json` 保留，增加格式约定没有收益 |
| 禁止 bash 用 `max_output_chars=0` 关闭落盘 | 只覆盖 bash；会把更多输出写入系统临时目录；改变已公开的参数语义。修复卸载器后，该路径同样可读回 |
| 写盘失败时不把原文留在上下文 | 与"写盘失败保留原文"的既有设计冲突（`test_keeps_original_message_when_filesystem_offload_write_fails`） |
| 同步修改非 forked `processor/base.py` | 主 DeepAgent preset 走 forked `MessageSummaryOffloader`（本事故路径）。独立 browser agent 会挂非 forked 的 `ToolResultWindowProcessor`（继承 `ToolResultBudgetProcessor`），但它在 `offload_messages` 之后**自行重写** marker，且 `path` 钉死为当初的 `.json`；只改 base 写 `.txt` 仍无人引用。加返回码检查还会牵动以 `AsyncMock()` 模拟 `write_file` 的旧单测。另立缺陷修该处理器，不在本修复范围 |

## 验证

```text
uv run pytest tests/unit_tests/core/context_engine/test_offload_text.py tests/unit_tests/core/context_engine/test_forked_message_offloader.py::TestMessageOffloaderTextCopy tests/unit_tests/harness/tools/test_filesystem_tools.py::test_offload_text_mirrors_read_tool_limits -q
```

| 用例 | 覆盖 |
|---|---|
| `test_marker_points_to_line_oriented_text_copy` | marker 指向 `.txt`；同名 `.json` 存在；文件头首行；正文与原文逐行一致 |
| `test_read_hint_limit_follows_offload_threshold` | 读取提示的行数、token 数；建议 `limit` 随窗口（卸载阈值）变化 |
| `test_text_copy_wraps_long_line_at_character_boundaries` | 中文与 emoji 单行超长内容折行后每行不超过 `OFFLOAD_TEXT_WRAP_BYTES`，拼接还原一致 |
| `test_marker_keeps_json_path_when_text_copy_write_fails` | `.txt` 写失败时 marker 回退 `.json`，不追加读取提示 |
| `test_keeps_original_message_when_sys_operation_write_returns_error` | `write_file` 返回错误码时保留原消息 |
| `test_sys_operation_writes_text_copy_without_leading_newline` | `.txt` 经 `sys_operation` 写入时 `prepend_newline=False`，首行即文件头 |
| `test_offload_text.py` | 拆行、折行、多消息、tool_calls 摘要（含超长 arguments 截断标记）与非文本 part、建议 `limit` 的边界（不依赖 harness） |
| `test_offload_text_mirrors_read_tool_limits` | core 镜像常量与 harness `GrepTool` / `ReadFileTool` 一致 |

执行结果：2026-11-25 二次审查后复跑上述命令，**16 passed**（`test_offload_text.py` 8 + `TestMessageOffloaderTextCopy` 7 + 镜像常量 1）。

## 已知遗留

- 修复前产生的会话，marker 仍指向 `.json`，读不回。
- `ToolResultBudgetProcessor` / `ToolResultWindowProcessor`（含独立 browser agent 路径）及非 forked offloader 未改：自拼 `.json` marker，另立缺陷。
- in_memory 卸载无法由模型读回（`OffloadMessageBuffer.reload` 无 core 内调用方）。
- 用户自己的单行超大文件，`read_file` 仍整页报错。
- 规则压缩路径写盘失败时，`_finalize_rule_compressed_message` 只保留截断后的内容，与"写盘失败保留原文"不一致，另行修复。
- 规则压缩路径的预览超过 `max_chars` 时由 `_truncate_preserving_offload_marker` 截断，只保证 marker 完整；窗口极小时读取提示可能被截掉。
- 无 `sys_operation` 时仍以同步 `open()` 直写本地文件，与 `.json` 的既有写法一致，未改为线程化。
- `harness/prompts/sections/offload.py`（`build_offload_section`）无调用方，未修改。
