# operator

`openjiuwen.core.operator` 提供可优化的**原子算子**抽象，用于自演进框架（[openjiuwen.agent_evolving](../openjiuwen.agent_evolving.README.md)）：既支持执行（invoke/stream + Session，轨迹通过 session.tracer() 记录），又支持优化（get_tunables、set_parameter、get_state/load_state）。

**基类与规格**：

| 类 / 类型 | 说明 |
|-----------|------|
| [Operator](operator/base.md) | 原子算子抽象基类 |
| [TunableSpec](operator/base.md) | 可调参数描述 |

**算子实现**：

| 类 | 说明 |
|----|------|
| [LLMCallOperator / LLMCall](operator/llm_call/base.md) | 模型调用算子，支持 system_prompt / user_prompt 可调 |
| [ToolCallOperator](operator/tool_call/base.md) | 工具调用算子，可选 tool_description 可调 |
| [MemoryCallOperator](operator/memory_call/base.md) | 记忆调用算子，enabled / max_retries 可调 |