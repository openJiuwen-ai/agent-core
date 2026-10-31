# Context engineer harness rails

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/rails/context_engineer/` |
| 最近一次修订日期 | 2026-10-26 |

## 范围 / 边界

- **ContextAssembleRail** — system prompt 注入：workspace、context 文件分节、tools 列表。
- **ContextProcessorRail** — context engine processor 链、session memory manager、recall/reload、loop bailout。
- 与 agent factory 的挂载（`harness/cli/agent/factory.py` 等）仅作索引；产品门控见 jiuwenswarm [[S_01]] 姊妹 spec。

## 不变量

1. **Public export**：`context_engineer/__init__.py` 的 `__all__` **仅** `ContextAssembleRail`、`ContextProcessorRail`。
2. **Priority**：两 rail 均为 **`priority = 85`**；调整先后须验证 callback 排序与单测。
3. **Assemble 职责边界**  
   - `disabled_tools`：从 **# 可用工具** section 省略名称；不扫描 sibling rails 取黑名单。  
   - `tool_name_allowlist`：可选固定顺序；与 fingerprint 稳定 tools section。  
   - Context 文件 section：`context.agent` / `soul` / `identity` / `user` 等（见 `context_assemble_rail.py` 常量）。
4. **Processor 职责边界** — 不写 workspace 文件；只改 `context_processors`、session memory、tool recall、tool 上下文修补与 bailout。
5. **Assemble vs Processor 门控（产品）** — jiuwenswarm 规定：`ContextAssembleRail` 可在 engine config 关闭时仍注册；`ContextProcessorRail` 受 `context_engine_config.enabled` 门控。**实现**在 core；**门控**在产品 adapter（不要在本 repo archive 改门控表而不改姊妹 spec）。
6. **Factory 默认**：Deep agent factory 在模块可用时 append `ContextProcessorRail(preset=True)` 与 `ContextAssembleRail()`（顺序以 factory 列表为准，变更须跑 harness 测试）。

## 接口契约

| Rail | 钩子 | 契约 |
|------|------|------|
| `ContextAssembleRail` | `init`, `before_model_call` | 捕获 `system_prompt_builder`、`ability_manager`；注入 workspace/context/tools sections |
| `ContextProcessorRail` | `init`, `before_invoke`, `on_model_exception`, `uninit` | 合并 processors；注册 recall 工具；loop bailout → `AbortError` |

## 与其它 spec 的关系

- Preset 链：[[S_03]]
- Recall/reload：[[S_05]]
- 双仓：[[S_01]]

## 测试

- `tests/unit_tests/harness/test_context_assemble_rail.py`
- `tests/unit_tests/harness/test_context_processor_rail.py`
- `tests/unit_tests/harness/test_compression_recall_tool.py`
