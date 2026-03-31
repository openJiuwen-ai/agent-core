# openjiuwen.core.workflow

`openjiuwen.core.workflow.components` 提供工作流预置组件，与 [workflow](../workflow.README.md) 基类配合使用。组件类通过 `openjiuwen.core.workflow` 导出，建议使用 `from openjiuwen.core.workflow import ...` 导入。

| 文档 | 说明 |
|------|------|
| [base](components/components.md) | WorkflowComponent、ComponentExecutable、ComponentConfig |
| [branch_comp](./components/flow/branch_comp.md) | BranchComponent |
| [branch_router](./components/flow/branch_router.md) | BranchRouter、Branch |
| [condition/condition](./components/condition/condition.md) | Condition、FuncCondition、AlwaysTrue |
| [condition/expression](./components/condition/expression.md) | ExpressionCondition |
| [condition/array](./components/condition/array.md) | ArrayCondition |
| [condition/number](./components/condition/number.md) | NumberCondition |
| [end_comp](./components/flow/end_comp.md) | End、EndConfig |
| [intent_detection_comp](./components/llm/intent_detection_comp.md) | IntentDetectionComponent、IntentDetectionCompConfig |
| [llm_comp](./components/llm/llm_comp.md) | LLMComponent、LLMCompConfig |
| [loop_comp](./components/flow/loop/loop_comp.md) | LoopComponent、LoopGroup、LoopBreakComponent、LoopSetVariableComponent |
| [questioner_comp](./components/llm/questioner_comp.md) | QuestionerComponent、QuestionerConfig、FieldInfo |
| [start_comp](./components/flow/start_comp.md) | Start |
| [tool_comp](./components/tool/tool_comp.md) | ToolComponent、ToolComponentConfig |
| [knowledge_retrieval_comp](./components/resource/knowledge_retrieval_comp.md) | ComponentKBConfig、KnowledgeRetrievalComponent、KnowledgeRetrievalCompConfig |
| [memory_retrieval_comp](./components/resource/memory_retrieval_comp.md) | MemoryRetrievalComponent、MemoryRetrievalCompConfig |
| [memory_write_comp](./components/resource/memory_write_comp.md) | MemoryWriteComponent、MemoryWriteCompConfig |
| [workflow_comp](./components/flow/workflow_comp.md) | SubWorkflowComponent |
