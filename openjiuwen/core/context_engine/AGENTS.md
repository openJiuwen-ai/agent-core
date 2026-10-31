---
description: Implementation contracts for ContextEngine, processors, token/usage, and harness context-engineer rails in agent-core.
---

# Context Engine (agent-core)

对话上下文引擎：在 LLM 调用前把 session 消息整理成**有界、可观测、可压缩**的 `ContextWindow`。本目录是 **agent-core 侧的实现归档 hub**；用户教程与 API 手册见仓库 `docs/`（下文「用户文档（只引用）」），**不要在本 archive 里重写 API 手册**。

**L0 全局约定**（命令、风格、公共 API 纪律）：仓库根 [`AGENTS.md`](../../../AGENTS.md)。

**深入规约**：[`docs/specs/S_01_overview-and-boundaries.md`](docs/specs/S_01_overview-and-boundaries.md) 起；归档结构见 [`docs/AGENTS.md`](docs/AGENTS.md)。

**Harness rails（本 repo 内）**：[`openjiuwen/harness/rails/context_engineer/AGENTS.md`](../../harness/rails/context_engineer/AGENTS.md) — 本地 priority / preset 要点；完整 rail 契约见 [[S_06]]。

## 双仓管辖（必读）

| 关切 | **agent-core（本 archive）** | **jiuwenswarm（姊妹产品仓）** |
|------|------------------------------|-------------------------------|
| `ContextEngine`、`ModelContext`、processor 链、token/usage、`FullCompactProcessor` API | **OWNS** | 引用 + adapter 传参 |
| `ContextAssembleRail` / `ContextProcessorRail` 实现 | **OWNS** | 装配顺序、config 门控 |
| `CompressionRecallTool` / `recall_compressed_context` | **OWNS** | `context_engine_config` 启用 |
| `ContextOverflowRecoveryRail`、`context_read_patch`、Runtime/Response prompt 产品层 | 仅路径引用 | **OWNS** |
| `react.context_engine_config` 产品默认值 | 仅路径引用 | **OWNS** |

姊妹产品文档 hub（**不要在此 duplicate 产品装配**）：`jiuwenswarm/server/runtime/context/`（在 `jiuwenclaw` vendor 的 jiuwenswarm 树内）。改 overflow 恢复、config.yaml、allowlist 同步时去那边读 spec。

## 公开入口（public API）

公开符号**仅限** [`__init__.py`](__init__.py) 的 `__all__`。新增对外类型必须先 export，并补/改单测与（若行为变更）用户 API 页。

| 类别 | 代表符号 | 职责 |
|------|----------|------|
| 引擎 | `ContextEngine`, `ContextEngineConfig`, `CompressionRecallConfig` | 注册 processor、创建/池化 `ModelContext`、窗口 mutator |
| 上下文模型 | `ModelContext`, `ContextWindow`, `ContextWindowChange`, `ContextStats` | 消息列表 + 处理器状态 + 窗口快照 |
| Processor | `ContextProcessor` 及各 `*Processor` / `*Config` | 链式变换；forked 实现见 `processor/forked/`（preset 前须 `forked.activate()`） |
| Token | `TokenCounter`, `TiktokenCounter`, `TokenizerRegistry`, … | 计数策略与 tokenizer 工件 |
| Usage | `ContextUsageAnalyzer`, `SessionKVCacheAggregator`, … | 窗口/token 遥测与 KV cache 聚合 |

**用户文档（只引用，勿复制）**

- 概念与进阶用法：`docs/en/2.Development Guide/Advanced Usage/Context Engine.md`（中文：`docs/zh/2.开发指南/...`）
- API 索引：`docs/en/2.Development Guide/API Docs/openjiuwen.core/context_engine.README.md` 及 `context_engine/` 子页（processors 等）
- 中文 API：`docs/zh/2.开发指南/API文档/openjiuwen.core/context_engine/`

## 模块地图（实现）

```
context_engine/
├── __init__.py              # 公开 export 聚合
├── context_engine.py        # ContextEngine：create_context、processor 注册、get_context_window 路径
├── base.py                  # ContextWindow / ModelContext 协议面
├── schema/config.py         # ContextEngineConfig、CompressionRecallConfig
├── context/                 # SessionModelContext、utils、processor 状态记录
├── processor/               # 稳定 processor 实现 + compressor/offloader 子树
│   └── forked/              # harness preset 默认链（activate 后覆盖 registry 解析）
├── token/                   # 计数与 tokenizer 选择
├── usage/                   # 用量分析
└── docs/                    # 本模块设计归档（specs / features / designs）
```

关联 harness（**代码不在本目录，契约见 [[S_06]]**）：

```
openjiuwen/harness/rails/context_engineer/
├── context_processor_rail.py   # preset 链、recall/reload 工具与 prompt section
├── context_assemble_rail.py    # workspace / context 文件 / tools section
openjiuwen/harness/tools/compression_recall.py
openjiuwen/harness/prompts/sections/compression_recall.py | reload.py
```

## Preset processor 链（ContextProcessorRail）

`ContextProcessorRail(preset=True)` 在 `init` 写入 `react_agent._config.context_processors`。顺序（forked 实现，**不含** `FullCompactProcessor`）：

1. `forked.activate()`
2. `MessageSummaryOffloader`
3. `SessionMemoryCompressor`（`enabled=False` 默认；opt-in 后挂 `SessionMemoryManager`）
4. `ReasoningToolLoopCompactProcessor`
5. `DialogueCompressor` → `CurrentRoundCompressor` → `RoundLevelCompressor`

`FullCompactProcessor` 由产品/Recovery 路径显式加入或 GET 路径触发；见 [[S_04]]。

## 测试

| 区域 | 路径 |
|------|------|
| 引擎与 processor | `tests/unit_tests/core/context_engine/` |
| Assemble rail | `tests/unit_tests/harness/test_context_assemble_rail.py` |
| Processor rail | `tests/unit_tests/harness/test_context_processor_rail.py` |
| Recall 工具 | `tests/unit_tests/harness/test_compression_recall_tool.py` |

仓库根命令（见 L0 `AGENTS.md`）：`uv sync`；`make test TESTFLAGS="tests/unit_tests/core/context_engine/..."`。

## 架构铁律

1. **`__init__.py` 即 public API 边界** — 未 export 的符号视为内部；breaking 变更须同步 API 文档与测试。[[S_01]]
2. **Processor 链顺序即语义** — 改顺序或默认 enabled 是行为变更，须单测 + spec/feature 归档。[[S_03]]
3. **`forked.activate()` 与 preset 绑定** — preset 链或显式 `SessionMemoryCompressor` 注册前必须 activate；否则 registry 解析到稳定实现而非 forked。[[S_03]]
4. **`get_context_window` 是压缩触发面** — `FullCompactProcessor.set_force_compact` / `set_overflow_threshold_override` 在**下一次** window GET 生效；不要在 processor `process` 里隐式改全局 force 标志。[[S_04]]
5. **Loop bailout 必须 AbortError** — `ReasoningToolLoopCompactProcessor` 超阈值时 rail 抛 `AbortError(build_error(..., StatusCode.CONTEXT_EXECUTION_ERROR))`，让 callback 框架传播 cause。[[S_03]]
6. **Recall 与 offload 保护** — 启用 compression recall 时 `recall_compressed_context` 须注册且进入 offloader `protected_tool_names`；marker `[[COMPRESSION_RECALL: id=...]]` 协议稳定。[[S_05]]
7. **CompressionRecallConfig 校验** — `chunk_overlap_tokens < chunk_size_tokens`（Pydantic validator）；违反即配置错误，不在运行时静默夹断。[[S_05]]
8. **Token 计数可插拔** — `create_context(..., token_counter=)` 默认 `TiktokenCounter`；改默认或计数语义须覆盖 usage/窗口测试。[[S_02]]
9. **Rail priority 85 成对** — `ContextAssembleRail` 与 `ContextProcessorRail` 同为 `priority = 85`；调整 relative 顺序须读 harness callback 排序规则并跑 rail 单测。[[S_06]]
10. **双仓不混写** — 产品 `context_engine_config`、overflow recovery、read patch 只在 jiuwenswarm 改；core 只提供能力与 rail，不在此 archive 写产品默认值表。[[S_01]]

## 软规则（Good / Bad）

**Good**

- 先读 [[S_NN]] 再改 processor 或 window 逻辑；行为变更带 `tests/unit_tests/core/context_engine/` 或 harness rail 测试。
- 用户可见教程/API 变更去 `docs/en|zh/.../context_engine*`；本 archive 只写**实现契约**与决策。
- 发现 AGENTS/spec 与代码不一致：同 PR 以代码为准刷新文档（双向同步）。

**Bad**

- 在 `context_engine/` 复制 jiuwenswarm 的 config.yaml 键表或 overflow 流程图。
- 未 `forked.activate()` 却在 preset 里挂 forked-only processor key。
- 用裸 `Exception` 代替 loop bailout 的 `AbortError` + `CONTEXT_EXECUTION_ERROR`。
- 把 `FullCompactProcessor` 塞进默认 preset 而不更新 [[S_03]]/[[S_04]] 与产品装配 spec。

## 设计文档归档与双向同步（本模块强制）

设计文档落在 [`docs/`](docs/)，命名见 [`docs/AGENTS.md`](docs/AGENTS.md)。把 **`openjiuwen/core/context_engine/` 代码 + `docs/specs/` + `docs/features/` + 本 `AGENTS.md` +（若改 rail）`harness/rails/context_engineer/AGENTS.md`** 当作**一个一致性单元**。

**输入侧**：改设计前先 `grep` 并阅读相关 `S_*` / `F_*`；方案定稿时**同步**改 spec，不要「先码后档」。

**产出侧（提交必查）**

1. 特性级改动：代码、测试、文档 **三个连续 commit**（scope `context`），见 `docs/AGENTS.md`。
2. 契约/不变量变：修订对应 `docs/specs/S_NN_*.md` 或新增 spec。
3. **双向同步**：任一份 AGENTS/spec/feature 与代码不符 → 以代码为准当场修文档；**更新 `AGENTS.md`，不要改仅作壳的 `CLAUDE.md`**。

元信息只写日期 `YYYY-MM-DD`，不写 commit hash。

## 提交约定

本模块改动 commit scope 固定 **`context`**（如 `feat(context): ...`、`test(context): ...`、`docs(context): ...`）。
