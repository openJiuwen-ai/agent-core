# Compression recall and reload

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `schema/config.py`、`harness/tools/compression_recall.py`、`core/context_engine` recall 实现 |
| 最近一次修订日期 | 2026-10-26 |

## 范围 / 边界

- `CompressionRecallConfig` 与 `ContextEngineConfig.compression_recall_config`。
- 工具 **`recall_compressed_context`**（`CompressionRecallTool` / harness 注册）。
- 消息 marker **`[[COMPRESSION_RECALL: id=...]]`** 与 reload hint **`[[HANDLE:...]]`**。
- `ContextProcessorRail` 对 recall/reload 的 prompt section 与 offloader 保护。

## 不变量

1. **Config 校验**：`chunk_overlap_tokens < chunk_size_tokens`（`CompressionRecallConfig` Pydantic validator）；否则配置无效。
2. **工具名稳定**：`recall_compressed_context` 为 ability key；启用 recall 时须注册对应 `ToolCard`。
3. **Protected tools**：启用 recall 时，`MessageSummaryOffloader`（或等价 offloader）的 `protected_tool_names` 必须包含 `recall_compressed_context`（rail 自动追加）。
4. **Marker 协议**：压缩替换后的占位/content 含 `[[COMPRESSION_RECALL: id=<memory_id>]]`；recall 成功后相关 marker 从 context 清除（见 `tests/unit_tests/core/context_engine/test_compression_recall.py`）。
5. **Reload**：`ContextEngineConfig.enable_reload=True` 时引擎对 `[[HANDLE:xxx]]` 做透明重载；False 时 hint 不自动解析（[[S_02]]）。
6. **实现位置**：recall 核心逻辑在 core；harness `compression_recall.py` 包装为 DeepAgent 工具；产品启用开关在 jiuwenswarm config（[[S_01]]）。

## 接口契约

| 表面 | 契约 |
|------|------|
| `recall_compressed_context(...)` | `memory_id` 来自 marker；省略时在策略允许范围内搜索相关块 |
| Rail `init` | 根据 `CompressionRecallConfig.enabled` / reload 配置注入 prompt sections（`build_compression_recall_section` / `build_reload_section`） |
| 禁用 recall | 不注册工具、不追加 protected_tool_names、不注入 recall section |

## 数据结构

- 归档块分 chunk：`chunk_size_tokens`、`chunk_overlap_tokens` 控制存储与检索粒度。
- Session/context 内 memory id 与 marker 一一对应；改 id 格式是 breaking change。

## 与其它 spec 的关系

- Engine config：[[S_02]]
- Processor rail：[[S_03]]、[[S_06]]
