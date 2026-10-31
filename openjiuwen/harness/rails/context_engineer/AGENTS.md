# Context engineer rails (L2)

Harness 侧 **上下文工程师** rails：把 ContextEngine preset 与 system prompt 上下文块接到 DeepAgent。

**完整规约与双仓表** → [`openjiuwen/core/context_engine/AGENTS.md`](../../../core/context_engine/AGENTS.md) 与 [`docs/specs/S_06_context-engineer-rails.md`](../../../core/context_engine/docs/specs/S_06_context-engineer-rails.md)。

## 本目录文件

| 文件 | 职责 |
|------|------|
| `context_processor_rail.py` | Preset processor 链、session memory、recall/reload、loop bailout |
| `context_assemble_rail.py` | Workspace / AGENT·SOUL 等 context 文件 / tools section |
| `__init__.py` | 仅 export `ContextProcessorRail`, `ContextAssembleRail` |

## 本地优先级（改 rail 时先查）

1. **`priority = 85`** — Assemble 与 Processor 同级；动排序先读 harness callback 顺序再改。
2. **Preset 链** — `forked.activate()` → offloader → disabled session memory → loop compact → dialogue/current/round（**无 FullCompact**）。见 [[S_03]]。
3. **Assemble 不管 processor**；**Processor 不管 workspace section** — 禁止把压缩逻辑塞进 assemble rail。
4. **Loop bailout** — 必须 `AbortError` + `StatusCode.CONTEXT_EXECUTION_ERROR`（[[S_03]]）。
5. **产品门控** — `context_engine_config.enabled` 只关 Processor rail 的**注册**由 jiuwenswarm adapter 负责；勿在 core rail 内硬编码产品 YAML。

## 测试

- `tests/unit_tests/harness/test_context_assemble_rail.py`
- `tests/unit_tests/harness/test_context_processor_rail.py`
