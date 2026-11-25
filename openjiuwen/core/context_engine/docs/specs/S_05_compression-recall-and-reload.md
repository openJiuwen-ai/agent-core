# Compression recall and reload

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `schema/config.py`、`harness/tools/compression_recall.py`、`core/context_engine` recall 实现、`processor/forked/base.py`（filesystem 卸载写盘）、`processor/offload_text.py`、`harness/prompts/sections/reload.py` |
| 最近一次修订日期 | 2026-11-25 |

## 范围 / 边界

- `CompressionRecallConfig` 与 `ContextEngineConfig.compression_recall_config`。
- 工具 **`recall_compressed_context`**（`CompressionRecallTool` / harness 注册）。
- 消息 marker **`[[COMPRESSION_RECALL: id=...]]`** 与卸载 marker **`[[OFFLOAD: handle=..., type=..., path=...]]`**。
- forked filesystem 卸载的落盘产物（`.json` + `.txt`）与模型读回契约。
- `ContextProcessorRail` 对 recall/reload 的 prompt section 与 offloader 保护。

## 不变量

1. **Config 校验**：`chunk_overlap_tokens < chunk_size_tokens`（`CompressionRecallConfig` Pydantic validator）；否则配置无效。
2. **工具名稳定**：`recall_compressed_context` 为 ability key；启用 recall 时须注册对应 `ToolCard`。
3. **Protected tools**：启用 recall 时，`MessageSummaryOffloader`（或等价 offloader）的 `protected_tool_names` 必须包含 `recall_compressed_context`（rail 自动追加）。
4. **Marker 协议**：压缩替换后的占位/content 含 `[[COMPRESSION_RECALL: id=<memory_id>]]`；marker 常驻，recall 按 `memory_id` 检索归档（见 `tests/unit_tests/core/context_engine/test_compression_recall.py`）。
5. **Reload**：`ContextEngineConfig.enable_reload=True` 时注入 reload prompt 段（`build_reload_section`），引导模型按 `[[OFFLOAD: ...]]` 的 `path` 用 `read_file` 读回；`OffloadMessageBuffer.reload(offload_handle, offload_type)` 可用但无 core 内调用方；False 时不注入（[[S_02]]）。
6. **实现位置**：recall 核心逻辑在 core；harness `compression_recall.py` 包装为 DeepAgent 工具；产品启用开关在 jiuwenswarm config（[[S_01]]）。
7. **卸载产物成对**：forked filesystem 卸载先写 `<prefix>_<handle>.json`（程序回读，格式不变），再写同名 `.txt`（模型读回）。`.txt` 写入成功时 marker `path` 指向 `.txt`；写入失败时 `path` 保持 `.json`。`OffloadMessageBuffer._filesystem_reload_paths` 只匹配 `.json`，`.txt` 不参与程序回读。
8. **`.txt` 可按行读回**：正文按 `str.splitlines` 拆行，单行超过 `OFFLOAD_TEXT_WRAP_BYTES`（= `GREP_MAX_COLUMNS - 20` UTF-8 字节）时在字符边界折行、不加续行前缀；前 `OFFLOAD_TEXT_HEADER_LINES`（3）行为文件头，只写与模型无关的事实。`GREP_MAX_COLUMNS` / `READ_FILE_MAX_*` 是 harness 工具限制的镜像（core 不 import harness），由 harness 侧 `tests/unit_tests/harness/tools/test_filesystem_tools.py::test_offload_text_mirrors_read_tool_limits` 钉住（core 单测不依赖 harness）。
9. **写盘失败判定**：`fs().write_file` 返回非成功状态码与抛异常同样视为失败并记 error 日志；`.json` 写失败时 forked `offload_messages` 返回 `None`，调用方保留原消息（既有策略，不回退 in_memory）。
10. **读取提示**：forked `MessageSummaryOffloader` 在 marker 前追加一行读取提示（正文行数、token 数、建议 `limit`）。建议 `limit` 由 `suggest_read_limit` 计算：单页 token 不超过该处理器的 `_add_message_token_threshold(context)`（随模型窗口变化），且不超过 `read_file` 自身 `len // 4` 预算的 80%，夹在 `[20, READ_FILE_MAX_LINES]`。提示是会话内容，换模型后只作参考。

## 接口契约

| 表面 | 契约 |
|------|------|
| `recall_compressed_context(...)` | `memory_id` 来自 marker；省略时在策略允许范围内搜索相关块 |
| Rail `before_model_call` | 根据 `CompressionRecallConfig.enabled` / reload 配置注入 prompt sections（`build_compression_recall_section` / `build_reload_section`）；`init` 只设置 `_recall_enabled` / `_reload_enabled`、注册 recall 工具并追加保护名 |
| 禁用 recall | 不注册工具、不追加 protected_tool_names、不注入 recall section |

## 数据结构

- 归档块分 chunk：`chunk_size_tokens`、`chunk_overlap_tokens` 控制存储与检索粒度。
- Session/context 内 memory id 与 marker 一一对应；改 id 格式是 breaking change。
- 归档路径：`<workspace>/context/<session>-<digest>_context/compression_recall/<memory_id>/`（`processor/forked/compressor/recall/archive.py`）。
- 失败降级：`RecallUnavailable` → 继续压缩但不写 marker；`ArchiveError` → 放弃压缩、保留原文（`processor/forked/compressor/base.py`、`recall/archive.py`）。
- 卸载产物：`<workspace>/context/<session>_context/offload/<prefix>_<handle>.json` 与同名 `.txt`（`processor/forked/base.py::_write_offload_to_file` / `_write_offload_text_file`）。`.txt` 文件头三行：`[OFFLOAD handle=... [tool=...] tool_call_id=...]`、`[original_chars=... total_lines=... max_line_bytes=... wrapped_lines=... wrap_bytes=...]`、正文起始行与折行说明；之后为正文。

## 与其它 spec 的关系

- Engine config：[[S_02]]
- Processor rail：[[S_03]]、[[S_06]]
- 卸载产物可读回缺陷归档：`docs/bugfix/B_01_offload-readable-text.md`
