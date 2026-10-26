# Overview and boundaries

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/core/context_engine/`、`openjiuwen/harness/rails/context_engineer/` |
| 最近一次修订日期 | 2026-10-26 |
| 关联 feature | （按需） |

## 范围 / 边界

### 本 spec 管什么

- `ContextEngine` 作为 session 级上下文入口：创建/复用 `ModelContext`、注册 processor、应用窗口 mutator。
- Processor 链语义、forked vs 稳定实现、压缩/recall/reload 在 **core + harness rails** 内的分工。
- Token 计数、usage 遥测与公开 export 边界（`__init__.py`）。
- agent-core 侧 **ContextAssembleRail** / **ContextProcessorRail** 的实现契约（不含产品 YAML）。

### 不管什么

- 终端用户教程与逐 API 参数说明 → 引用 `docs/en|zh/.../Context Engine.md` 与 `openjiuwen.core/context_engine` API 树。
- jiuwenswarm 产品装配：`react.context_engine_config`、`ContextOverflowRecoveryRail`、`context_read_patch`、interface_deep/flash 门控 → **姊妹仓 OWNS**，路径 `jiuwenswarm/server/runtime/context/`。
- OfficeClaw / relay 上层 agent 目录结构。

## 双仓管辖（不变量）

| 关切 | agent-core（本 repo archive） | jiuwenswarm（姊妹产品 docs） |
|------|--------------------------------|------------------------------|
| ContextEngine、processors、token/usage、FullCompact API | **OWNS** | cites + passes config |
| ContextAssembleRail / ContextProcessorRail | **OWNS** | wires rails + gates |
| CompressionRecallTool | **OWNS** | enables via config |
| ContextOverflowRecoveryRail、context_read_patch、产品 Runtime/Response prompt | cite only | **OWNS** |
| `react.context_engine_config` defaults | cite only | **OWNS** |

**不要**在本 archive 复制产品 config 键表或 overflow 状态机；改那些行为时读 jiuwenswarm `S_06` 等产品 spec。

## 不变量

1. 公开 API 以 `openjiuwen/core/context_engine/__init__.py` 的 `__all__` 为准。
2. `ContextEngine` 按 `(session_id, context_id)` 池化 `ModelContext`；同一键重复 `create_context` 返回已存在实例（处理器列表以创建时为准，见 [[S_02]]）。
3. Harness 对外只 export 两个 rail 符号：`ContextProcessorRail`、`ContextAssembleRail`（`context_engineer/__init__.py`）。
4. 文档 archive 与代码同属一个一致性单元（见 L1 `AGENTS.md` 双向同步）。

## 接口契约（实现向）

- **引擎入口**：`ContextEngine.create_context`、`get_context`、processor 注册与 `register_window_mutator` — 细节见 `context_engine.py` 与用户 API README（不重复参数表）。
- **配置根**：`ContextEngineConfig` + 嵌套 `CompressionRecallConfig`（[[S_05]]）。
- **错误域**：上下文执行失败使用 `StatusCode.CONTEXT_EXECUTION_ERROR`（与 loop bailout 一致，[[S_03]]）。

## 与其它 spec 的关系

| Spec | 关系 |
|------|------|
| [[S_02]] | 窗口 GET、mutator、消息/轮次限制 |
| [[S_03]] | Processor 链、forked preset、loop bailout |
| [[S_04]] | FullCompact 与 force/overflow override |
| [[S_05]] | Compression recall、reload handles |
| [[S_06]] | Harness rails 与 assemble/processor 分工 |
