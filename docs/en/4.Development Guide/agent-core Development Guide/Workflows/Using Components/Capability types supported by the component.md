Component capability types are defined based on the data forms of their inputs and outputs (streaming data or batch data) and are classified into four types. For details, refer to [Component Capability Types](../Key%20Concepts.md#component-capability-types). The following describes the capability types supported by openJiuwen **Pre-built Components** and **Custom Components**.

## Pre-built Components

The list of capabilities for pre-built components (for details, please refer to [Using Preset Components](Using%20Preset%20Components.md)) is as follows:

| Component\Capability| `invoke`| `stream` | `collect`| `transform`|
| --- | --- | --- | --- | --- |
|Start | √ | ×|×  |×  |
|End  |√  |√  | √ |√  |  
|LLMComponent  |√  | √ |×  |  ×| 
|ToolComponent  |√  | × | × |×  | 
|IntentDetectionComponent  |√  |×  | × |×  |  
|QuestionerComponent  | √ | × | ×| × |  
|BranchComponent  |√  | × | × |×  |  
| LoopComponent |√  | × | × | × |
| LoopBreakComponent|√  |×  |×  | × |
| LoopSetVariableComponent |√  | × |×  | × |
| SubWorkflowComponent| √ | √ |×  |×  |

## Custom Components

Custom components achieve corresponding capabilities by implementing the `invoke`, `stream`, `collect`, and `transform` interfaces in `ComponentExecutable`. Refer to: [Develop Custom Components](../../Advanced Usage/Develop Custom Components.md).