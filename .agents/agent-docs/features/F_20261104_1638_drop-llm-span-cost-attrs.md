---
id: F_20261104_1638_drop-llm-span-cost-attrs
title: LLM span 去掉 usage cost 属性（openjiuwen.gen_ai.usage.*_cost）
type: F
author: weixin_39960157
author_email: 1046386000@qq.com
modules:
  - openjiuwen/extensions/tracer_otel
  - openjiuwen/extensions/observability
  - openjiuwen/harness_providers
files:
  - openjiuwen/extensions/tracer_otel/handler.py
  - openjiuwen/extensions/tracer_otel/semconv.py
  - openjiuwen/extensions/observability/callback_handler.py
  - openjiuwen/extensions/observability/semconv.py
  - openjiuwen/harness_providers/trajectory.py
  - tests/unit_tests/extensions/tracer_otel/test_handler.py
  - tests/unit_tests/extensions/tracer_otel/test_otel_rail.py
  - tests/unit_tests/extensions/observability/test_callback_handler.py
designs: []
design_note: 纯删减类变更（去掉一处已定属性的发射），无方案级取舍需要独立设计；键的引入背景与命名迁移见 #1833。
---

# LLM span 去掉 usage cost 属性

## 背景与决策

`openjiuwen.gen_ai.usage.{total,input,output}_cost` 三个 span 属性由 #1833
引入（!3045/!3046，含从非规范 `gen_ai.usage.cost.*_usd` 到厂商命名空间的
迁移）。去掉的原因：

- OTel GenAI 语义约定没有 cost 键，该属性自引入起就是项目自造键；
- cost 可由 token 数 × 模型单价在后端推导，span 上携带属于冗余；
- 仓库内零消费方：agent-studio 全仓（py/java/ts/js/vue/go）对这三个键与
  `OJ_GEN_AI_USAGE_*_COST` 常量无任何引用（studio 的 `total_cost` 命中均为
  业务 usage 字典：前端展示 / 日志 / insight 载荷，与 span 属性无关）。

## 删除的发射点（全量枚举）

| 栈 | 位置 | 说明 |
|---|---|---|
| tracer_otel | `handler.py` 导入 + `_set_llm_response_attrs` 三行 `set_attribute` | 单栈发射 |
| tracer_otel | `semconv.py` 三个常量定义 | |
| observability | `callback_handler.py` 导入 + usage 投影循环（value/dst_attr 三元组） | 与 tracer_otel 双写，删单栈键仍会出现在 traces |
| observability | `semconv.py` 三个常量定义 + 模块 docstring 扩展命名空间举例去掉 cost | |
| harness trajectory | `trajectory.py` 导入 + `if event.cost is not None: attributes[...]`（仅 total） | claudecode 轨迹重放的 inference span，引用 observability 常量；清点时新发现的第三发射点 |

## 保留的业务数据（不动）

- `UsageMetadata.input_cost/output_cost/total_cost` schema 字段与各 model_client
  的提取逻辑：usage 载荷继续携带 cost（studio insight 展示、context_engine
  计费账本 `context_state.py`、`rsi/budget_rail` 等消费方均在）；
- `harness_providers/claudecode/mapping.py` 的 `total_cost_usd` →
  `MonetaryAmount` 事件映射：事件本身仍是业务数据，只去掉其 span 投影；
- `test_intelli_router_model_client` / `test_claudecode` 等 schema/映射测试。

## 验证

- 回归：tracer + tracer_otel + observability + trajectory_recorder 四套件
  387 passed（`.testenv`，PYTHONPATH=.）；
- 反向咬合：仅回滚 `openjiuwen/` 改动（stash 源码保留测试）后恰好 3 个用例
  失败，全部落在新增的 cost 缺席反断言上（tracer_otel handler / otel_rail
  端到端 / observability 流式契约），证明删减被测试锁定且删得干净；
- 常量残留：`OJ_GEN_AI_USAGE_*_COST` 全仓（openjiuwen/ + tests/）零引用。

## 遗留

- 仓库外的遥测后端若有按这三个键建的索引/看板，属两仓之外的消费方，需
  使用方自行确认（仓库内无法验证）；
- 与 !3570/!3571（LLM 壳合并 + TTFT）孪生线并行，合并顺序任意；
  `semconv.py` docstring 与 handler 邻域有少量行级冲突预期，属正常合并。
