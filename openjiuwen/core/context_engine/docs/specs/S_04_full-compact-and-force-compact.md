# Full compact and force compact

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `processor/compressor/full_compact_processor.py`、`processor/forked/compressor/full_compact_processor.py` |
| 最近一次修订日期 | 2026-10-26 |

## 范围 / 边界

- `FullCompactProcessor` / `FullCompactProcessorConfig`（稳定与 forked 两套实现保持相同控制面语义）。
- 通过 **GET context window** 触发的全量压缩路径。
- 与 jiuwenswarm **ContextOverflowRecoveryRail** 的协作：recovery **产品侧 OWNS** 调用时序；core **OWNS** `set_force_compact` / `set_overflow_threshold_override` 语义。

## 不变量

1. **不在默认 preset 链中** — 加入 FullCompact 必须是显式 processor 列表或产品装配决策（[[S_03]]）。
2. **`set_force_compact(True)`** — 下一次 `get_context_window` **无条件**尝试 FullCompact（注释与实现一致）。
3. **`set_overflow_threshold_override(threshold)`** — 用给定 token 阈值作为压缩预算（典型：模型真实窗口 × 0.85，由调用方计算）。
4. **双实现** — `processor/compressor/` 与 `processor/forked/compressor/` 均暴露相同 setter；forked 仅在 activate 后由 registry 选中。
5. **与 micro/round/dialogue 压缩正交** — FullCompact 是更重级的全窗整理；改触发条件须评估与 `ReasoningToolLoopCompactProcessor` bailout 的交互。

## 接口契约

| API | 语义 |
|-----|------|
| `FullCompactProcessor.set_force_compact(value=True)` | -arm 单次 force；消费后在实现内清除或按实现文档重置 |
| `FullCompactProcessor.set_overflow_threshold_override(threshold_override: int)` | 覆盖 overflow 判定阈值直至下次 GET 路径消费 |

实现细节（保留消息策略、LLM 调用）见源码与用户 API 页 — 本 spec 只固定 **触发面与 setter 契约**。

## 与其它 spec 的关系

- 窗口 GET：[[S_02]]
- Preset 不含 FullCompact：[[S_03]]
- 产品 overflow retry：jiuwenswarm `ContextOverflowRecoveryRail`（cite [[S_01]] 双仓表）
