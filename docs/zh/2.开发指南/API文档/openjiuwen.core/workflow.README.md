# workflow

`openjiuwen.core.workflow`是openJiuwen框架支持工作流编排与执行的基础模块。工作流具体指多个组件或Agent按照预先设计的编排流程进行有序协作，从而完成复杂任务的一种机制，旨在提升复杂任务的处理效率与稳定性。

**Classes**：

| CLASS                                                                                                                                                                | DESCRIPTION                                                         |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| [WorkflowConfig](./workflow/workflow.md)             | 工作流配置类（card、spec、workflow_max_nesting_depth）。            |
| [WorkflowOutput](./workflow/workflow.md)                        | 工作流执行invoke的输出结果的数据类。                                |
| [WorkflowChunk](./workflow/workflow.md)                          | 工作流执行stream返回的数据帧的数据类。                              |
| [WorkflowExecutionState](./workflow/workflow.md)        | 工作流执行invoke的执行状态的枚举类。                                |
| [Workflow](./workflow/workflow.md)                                    | 工作流类。                                                          |
| [WorkflowCard](./workflow/workflow.md)                              | 工作流卡片类。                                                      |
| [WorkflowComponent](workflow/components/components.md)              | 自定义工作流组件的抽象基类。                                        |
| [ComponentExecutable](workflow/components/components.md)            | 组件可执行接口。                                                    |
| [ComponentComposable](workflow/components/components.md)            | 组件可组合接口。                                                    |
| [ComponentConfig](workflow/components/components.md)                | 组件配置基类。                                                      |
| [LLMComponent](./workflow/components/llm/llm_comp.md)               | openJiuwen预置的大模型组件。                                        |
| [LLMCompConfig](./workflow/components/llm/llm_comp.md)             | 大模型组件配置信息的数据类。                                        |
| [IntentDetectionComponent](./workflow/components/llm/intent_detection_comp.md) | openJiuwen预置的意图识别组件。                                     |
| [IntentDetectionCompConfig](./workflow/components/llm/intent_detection_comp.md) | 意图识别组件配置信息的数据类。                                      |
| [QuestionerComponent](./workflow/components/llm/questioner_comp.md) | openJiuwen预置的提问器组件。                                        |
| [QuestionerConfig](./workflow/components/llm/questioner_comp.md)   | 提问器组件配置信息的数据类。                                        |
| [FieldInfo](./workflow/components/llm/questioner_comp.md)           | 提问器组件待提取参数的元数据信息的数据类。                          |
| [ToolComponent](./workflow/components/tool/tool_comp.md)            | openJiuwen预置的插件组件。                                          |
| [ToolComponentConfig](./workflow/components/tool/tool_comp.md)      | 插件组件配置信息的数据类。                                          |
| [Start](./workflow/components/flow/start_comp.md)                   | openJiuwen预置的工作流开始组件。                                    |
| [End](./workflow/components/flow/end_comp.md)                       | openJiuwen预置的工作流结束组件。                                    |
| [EndConfig](./workflow/components/flow/end_comp.md)                 | 结束组件配置信息的数据类。                                          |
| [BranchComponent](./workflow/components/flow/branch_comp.md)         | openJiuwen预置的工作流分支组件。                                    |
| [LoopComponent](./workflow/components/flow/loop/loop_comp.md)             | 标准循环组件类。                                                    |
| [LoopGroup](./workflow/components/flow/loop/loop_comp.md)                | 循环体，是构建循环逻辑的基础容器。                                  |
| [SubWorkflowComponent](./workflow/components/flow/workflow_comp.md) | openJiuwen预置的子工作流组件。                                      |
| [BranchRouter](./workflow/components/flow/branch_router.md)         | 实现分支路由器的核心类。                                            |
| [Branch](./workflow/components/flow/branch_router.md)               | 分支类。                                                            |
| [Condition](./workflow/components/condition/condition.md)      | 条件判断基类。                                                      |
| [FuncCondition](./workflow/components/condition/condition.md)  | 基于函数的条件判断实现。                                            |
| [AlwaysTrue](./workflow/components/condition/condition.md)      | 恒成立的条件判断实现。                                              |
| [ExpressionCondition](./workflow/components/condition/expression.md) | 基于表达式的条件判断实现。                                          |
| [ArrayCondition](./workflow/components/condition/array.md)     | 数组条件判断实现。                                                  |
| [NumberCondition](./workflow/components/condition/number.md)    | 数字条件判断实现。                                                  |

**Functions**：

| FUNCTION | DESCRIPTION |
|----------|-------------|
| [generate_workflow_key](./workflow/workflow.md) | 生成工作流键值。 |
| [create_workflow_session](./workflow/workflow.md) | 创建工作流会话。 |
