# workflow

`openjiuwen.core.workflow` is the foundational module in the openJiuwen framework that supports workflow orchestration and execution. A workflow refers to a mechanism in which multiple components or Agents collaborate in an orderly manner according to a pre-designed orchestration process to complete complex tasks, with the goal of improving the efficiency and stability of complex task processing.

**Classes**：

| CLASS                                                                                                                                                                | DESCRIPTION                                                         |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| [WorkflowConfig](./workflow/workflow.md)             | Workflow configuration class (card, spec, workflow_max_nesting_depth).            |
| [WorkflowOutput](./workflow/workflow.md)                        | Data class for the output result of workflow execution invoke.                                |
| [WorkflowChunk](./workflow/workflow.md)                          | Data class for the data frame returned from workflow execution stream.                              |
| [WorkflowExecutionState](./workflow/workflow.md)        | Enumeration class for the execution state of workflow execution invoke.                                |
| [Workflow](./workflow/workflow.md)                                    | Workflow class.                                                          |
| [WorkflowCard](./workflow/workflow.md)                              | Workflow card class.                                                      |
| [WorkflowComponent](./workflow/components/components.md)              | Abstract base class for custom workflow components.                                        |
| [ComponentExecutable](./workflow/components/components.md)            | Component executable interface.                                                    |
| [ComponentComposable](./workflow/components/components.md)            | Component composable interface.                                                    |
| [ComponentConfig](./workflow/components/components.md)                | Component configuration base class.                                                      |
| [LLMComponent](./workflow/components/llm/llm_comp.md)               | Pre-built large model component in openJiuwen.                                        |
| [LLMCompConfig](./workflow/components/llm/llm_comp.md)             | Data class for large model component configuration information.                                        |
| [IntentDetectionComponent](./workflow/components/llm/intent_detection_comp.md) | Pre-built intent detection component in openJiuwen.                                     |
| [IntentDetectionCompConfig](./workflow/components/llm/intent_detection_comp.md) | Data class for intent detection component configuration information.                                      |
| [QuestionerComponent](./workflow/components/llm/questioner_comp.md) | Pre-built questioner component in openJiuwen.                                        |
| [QuestionerConfig](./workflow/components/llm/questioner_comp.md)   | Data class for questioner component configuration information.                                        |
| [FieldInfo](./workflow/components/llm/questioner_comp.md)           | Data class for metadata information of parameters to be extracted by the questioner component.                          |
| [ToolComponent](./workflow/components/tool/tool_comp.md)            | Pre-built plugin component in openJiuwen.                                          |
| [ToolComponentConfig](./workflow/components/tool/tool_comp.md)      | Data class for plugin component configuration information.                                          |
| [Start](./workflow/components/flow/start_comp.md)                   | Pre-built workflow start component in openJiuwen.                                    |
| [End](./workflow/components/flow/end_comp.md)                       | Pre-built workflow end component in openJiuwen.                                    |
| [EndConfig](./workflow/components/flow/end_comp.md)                 | Data class for end component configuration information.                                          |
| [BranchComponent](./workflow/components/flow/branch_comp.md)         | Pre-built workflow branch component in openJiuwen.                                    |
| [LoopComponent](./workflow/components/flow/loop/loop_comp.md)             | Standard loop component class.                                                    |
| [LoopGroup](./workflow/components/flow/loop/loop_comp.md)                | Loop body, the basic container for building loop logic.                                  |
| [SubWorkflowComponent](./workflow/components/flow/workflow_comp.md) | Pre-built sub-workflow component in openJiuwen.                                      |
| [BranchRouter](./workflow/components/flow/branch_router.md)         | Core class implementing branch router.                                            |
| [Branch](./workflow/components/flow/branch_router.md)               | Branch class.                                                            |
| [Condition](./workflow/components/condition/condition.md)      | Condition judgment base class.                                                      |
| [FuncCondition](./workflow/components/condition/condition.md)  | Function-based condition judgment implementation.                                            |
| [AlwaysTrue](./workflow/components/condition/condition.md)      | Always-true condition judgment implementation.                                              |
| [ExpressionCondition](./workflow/components/condition/expression.md) | Expression-based condition judgment implementation.                                          |
| [ArrayCondition](./workflow/components/condition/array.md)     | Array condition judgment implementation.                                                  |
| [NumberCondition](./workflow/components/condition/number.md)    | Number condition judgment implementation.                                                  |

**Functions**：

| FUNCTION | DESCRIPTION |
|----------|-------------|
| [generate_workflow_key](./workflow/workflow.md) | Generate workflow key value. |
| [create_workflow_session](./workflow/workflow.md) | Create workflow session. |
