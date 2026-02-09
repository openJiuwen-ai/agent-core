# openjiuwen.core.workflow.components
## class LoopGroup

循环体，用于组织和管理一组循环执行的组件。LoopGroup 是构建循环逻辑的基础容器，它可以包含多个组件并定义它们之间的连接关系，为工作流提供了强大的循环处理能力的基础支持。

### add_workflow_comp

```python
add_workflow_comp(comp_id: str, workflow_comp: ComponentComposable, *,  wait_for_all: bool = None, inputs_schema: dict | Transformer = None, stream_inputs_schema: dict | Transformer = None, outputs_schema: dict | Transformer = None, stream_outputs_schema: dict | Transformer = None, **kwargs) -> Self
```

添加工作流组件到循环体中。注意，循环体添加的组件只能在循环体内使用，不能在循环体外使用。同理，循环体外（使用`openjiuwen.core.workflow.Workflow.add_workflow_comp`接口）添加的组件也只能在循环体外使用，不能在循环体内使用。

**参数**：

- **comp_id**(str)：组件的唯一标识符，用于在循环体中引用此组件。注意是整个 Workflow 中的唯一标识符，不能与其他组件重复。
- **workflow_comp**(ComponentComposable)：待添加的循环体组件实例，取值类型不可为`LoopComponent`。
- *：参数分隔符
- **inputs_schema**(dict | Transformer, 可选)：组件常规输入参数的结构 schema。用于校验输入数据的格式合法性。键名与组件输入参数名匹配，值与组件输入数据类型匹配，未配置时不校验输入格式。默认值：`None`。
- **stream_inputs_schema**(dict | Transformer, 可选)：组件流式输入参数的结构 schema。用于定义流式输入数据的格式规范。默认值：`None`。
- **outputs_schema**(dict | Transformer, 可选)：组件常规输出结果的结构 schema。用于定义输出数据的格式规范，便于后续组件解析；未配置时输出格式由组件自身决定。默认值：`None`。
- **wait_for_all**(bool, 可选)：是否等待所有前置依赖组件执行完成后再执行此组件。`True`表示需要等待所有前置依赖组件执行完成，`False`表示不需要。默认值：`None`，表示由系统根据组件能力自动决定（流式组件默认为`True`，其他组件默认为`False`）。
- **stream_outputs_schema**(dict | Transformer, 可选)：组件流式输出结果的结构 schema。用于定义流式输出数据的格式规范。默认值：`None`。
- **kwargs**：预留参数，配置不生效。

**样例**：

```python
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.session import Session
>>> from openjiuwen.core.workflow import WorkflowComponent, Input, Output, LoopGroup
>>> # 自定义组件AddTenNode， 每次调用，对于inputs中的source累加10
>>> class AddTenNode(WorkflowComponent):
...     def __init__(self, node_id: str):
...         super().__init__()
...         self.node_id = node_id
...
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         return {"result": inputs["source"] + 10}
>>>
>>> # 创建循环体
>>> loop_group = LoopGroup()
>>> # 添加第一个组件，处理循环数组中的当前项
>>> # AddTenNode 是一个简单的组件，将输入的 source 字段值增加 10 并输出到 result 字段
>>> loop_group.add_workflow_comp("1", AddTenNode("1"), inputs_schema={"source": "${l.item}"})
>>> # 添加第二个组件，处理中间变量
>>> loop_group.add_workflow_comp("2", AddTenNode("2"), inputs_schema={"source": "${l.user_var}"})
```

### start_nodes

```python
start_nodes(nodes: list[str]) -> Self
```

设置循环体的起始组件。

**参数**：

- **nodes**(list[str])：起始组件标识符列表。当前只支持设置一个起始组件，`nodes`列表中不可存在`None`值。示例：`['start_node']`。

**样例：**

```python
>>> from openjiuwen.core.workflow  import LoopGroup
>>> # 设置循环体的起始组件
>>> loop_group = LoopGroup()
>>> loop_group.add_workflow_comp("1", AddTenNode("1"))
>>> loop_group.add_workflow_comp("2", AddTenNode("2"))
>>> # 指定组件"1"作为循环的起始组件
>>> loop_group.start_nodes(["1"])
```

### end_nodes

```python
end_nodes(nodes: list[str]) -> Self
```

设置循环体的结束组件。

**参数**：

- **nodes**(list[str])：结束组件标识符列表。当前只支持设置一个结束组件，`nodes`列表中不可存在`None`值。示例：`['end_node']`。

**样例**：

```python
>>> from openjiuwen.core.workflow  import LoopGroup
>>> # 设置循环体的结束组件
>>> loop_group = LoopGroup()
>>> loop_group.add_workflow_comp("1", AddTenNode("1"))
>>> loop_group.add_workflow_comp("2", AddTenNode("2"))
>>> # 指定组件"2"作为循环的结束组件
>>> loop_group.end_nodes(["2"])
```

## class LoopType

循环类型枚举类，定义了支持的循环类型。

- **Array**：数组循环，对数组中的每个元素执行循环。值为"array"。
- **Number**：数值循环，执行指定次数的循环。值为"number"。
- **AlwaysTrue**：无限循环，一直执行直到被中断。值为"always_true"。
- **Expression**：表达式循环，根据表达式结果决定是否继续循环。值为"expression"。

## class LoopComponent

```python
class LoopComponent(loop_group: LoopGroup, output_schema: dict)
```

标准循环组件类，是对循环功能的高级封装，提供了更简洁的使用方式。通过LoopComponent，可以方便地实现数组循环、数值循环等多种循环模式，并支持循环中断、中间变量传递等高级功能。

**参数**：

- **loop_group**([LoopGroup])：循环组实例，定义了循环体内要执行的组件。循环组中可以包含多个组件，并通过连接关系定义它们的执行顺序。`loop_group`内至少包含一个组件。
- **output_schema**(dict)：输出模式定义，用于从循环结果中提取特定字段。键为输出字段名，值为字段值的表达式路径。例如：`{"results": "${1.result}", "user_var": "${l.user_var}"}`表示将循环体内组件"1"的result字段和中间变量"user_var"作为循环组件的输出。

> **说明**
>
> 1. **循环体常用组件**：在循环功能实现中，通常需要与以下组件配合使用：
>
>    - [LoopBreakComponent]：用于控制循环的中断，当执行到此组件时，循环将立即停止。
>    - [LoopSetVariableComponent]：用于在循环过程中设置变量值，支持引用路径和嵌套结构变量的设置。
> 2. **循环变量作用域**：循环内部的变量仅在循环内部可见，外部组件无法直接访问。
> 3. **中间变量初始化**：使用LoopComponent的intermediate_var参数可以在循环开始前初始化中间变量，确保循环逻辑的正确性。
> 4. **循环中断机制**：
>
>    - 使用LoopBreakComponent时，需要在LoopComponent的循环体中正确配置中断逻辑。
>    - 循环中断后，已收集的结果仍会被保留并返回。
>    - 详细信息请参考[LoopBreakComponent]。
> 5. **循环条件类型**：根据不同的循环需求选择合适的循环类型：
>
>    - array：适用于需要遍历数组的场景。
>    - always_true：适用于需要无限循环直到显式中断的场景。
> 6. **性能考虑**：对于大数据集的循环，应注意内存使用和执行效率，避免在循环体中执行过于复杂的操作。

**样例**：

下面的示例展示了如何使用LoopComponent配合AlwaysTrue条件和LoopBreakComponent实现需要在特定条件下中断的无限循环。

**注意**：AlwaysTrue条件会使循环持续执行，直到LoopBreakComponent显式中断循环。详细信息请参考[LoopBreakComponent]。

```python
>>> import asyncio
>>>
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.session import Session
>>> from openjiuwen.core.session.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import WorkflowComponent, Input, Output, LoopGroup, BranchComponent, LoopComponent, \
...   Workflow, Start, End, LoopSetVariableComponent, LoopBreakComponent
>>>
>>> # 通用节点组件，返回输入值
>>> class CommonNode(WorkflowComponent):
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         return {"output": inputs["value"]}
>>>
>>> # 创建LoopGroup
>>> loop_group = LoopGroup()
>>>
>>> # 创建通用节点作为循环体核心组件
>>> class commonNode(CommonNode):
...     async def invoke(self, inputs: Input, session: Session, context: Context) -> Output:
...         # 模拟循环体的工作，返回结果
...         return {"output": True}
>>>
>>> # 为LoopGroup添加工作流组件
>>> loop_group.add_workflow_comp("a", commonNode())
>>>
>>> # 创建分支组件用于条件判断
>>> branch_component = BranchComponent()
>>> loop_group.add_workflow_comp("branch", branch_component)
>>>
>>> # 创建变量设置组件，递增user_var
>>> set_variable_component = LoopSetVariableComponent({"user_var": "${loop.user_var} + 1"})
>>> loop_group.add_workflow_comp("setVar", set_variable_component)
>>>
>>> # 创建终止循环组件
>>> break_node = LoopBreakComponent()
>>> loop_group.add_workflow_comp("break", break_node)
>>>
>>> # 指定组件"a"为循环开始，组件"setVar"为正常循环结束
>>> loop_group.start_nodes(["a"])
>>> loop_group.end_nodes(["setVar"])
>>>
>>> # 设置BranchComponent的分支条件
>>> # 分支1: 继续循环，执行setVariableComponent
>>> branch_component.add_branch("${loop.index} < 2", ["setVar"])
>>> # 分支2: 终止循环，执行break组件
>>> branch_component.add_branch("${loop.index} >= 2", ["break"])
>>>
>>> # 设置组件连接
>>> loop_group.add_connection("a", "branch")
>>>
>>> # 创建LoopComponent，设置输出模式，捕获最终的中间变量、循环计数和循环结果
>>> loop_component = LoopComponent(
...     loop_group,
...     {
...         "user_var": "${loop.user_var}",
...         "loop_count": "${loop.index}",
...         "results": "${a.output}"
...     }
... )
>>>
>>> # 创建工作流实例
>>> workflow = Workflow()
>>>
>>> # 添加开始组件
>>> workflow.set_start_comp("s", Start())
>>>
>>> # 添加结束组件，引用loop组件的输出结果
>>> workflow.set_end_comp("e", End(),
...                        inputs_schema={
...                            "user_var": "${loop.user_var}",
...                            "loop_count": "${loop.loop_count}",
...                            "results": "${loop.results}"
...                        })
>>>
>>> # 添加循环组件，设置为always_true循环类型和中间变量初始值
>>> workflow.add_workflow_comp("loop", loop_component,
...                               inputs_schema={
...                                   "loop_type": "always_true",
...                                   "intermediate_var": {"user_var": 0}
...                               })
>>>
>>> # 串行连接组件：start->loop->end
>>> workflow.add_connection("s", "loop")
>>> workflow.add_connection("loop", "e")
>>>
>>> # 调用invoke方法执行工作流
>>> result = asyncio.run(workflow.invoke({}, create_workflow_session()))
>>>
>>> print(f"执行结果: {result.result}")
>>> output = result.result.get('output', {})
>>>
>>> # 验证循环执行了3次
>>> assert output.get("loop_count") == 3, f"Expected loop_count == 3, got {output.get('loop_count')}"
>>>
>>> # 验证每个循环的执行结果
>>> expected_results = [True, True, True]
>>> assert output.get("results") == expected_results, f"Expected {expected_results}, got {output.get('results')}"
```


## class LoopBreakComponent

中断组件类，用于控制循环的中断，为工作流提供了强大的循环中断能力，可以在特定条件下主动终止循环执行。当执行到该组件时，循环将立即停止。

> **说明**
>
> * `LoopBreakComponent`可与[LoopComponent]和`loop_type="always_true"`（无限循环）配合使用。通过设置无限循环条件，使循环持续执行，直到遇到中断组件时才会退出循环。
> * 可以结合条件判断组件，实现更复杂的条件中断逻辑，例如在满足特定条件时才执行中断操作。
> * 在循环体中，`LoopBreakComponent`的执行顺序会影响循环中断的时机，需要合理安排其在循环体中的位置。

**样例**：

```python
>>> import asyncio
>>> 
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.session import Session
>>> from openjiuwen.core.session.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import WorkflowComponent, Workflow, Start, End, LoopGroup, LoopSetVariableComponent, \
...     BranchComponent, LoopBreakComponent, LoopComponent
>>> 
>>> 
>>> # 自定义组件AddTenNode， 每次调用，对于inputs中的source累加10
>>> class AddTenNode(WorkflowComponent):
...      def __init__(self, node_id: str):
...          super().__init__()
...          self.node_id = node_id
... 
...      async def invoke(self, inputs, session: Session, context: ModelContext):
...          return {"result": inputs["source"] + 10}
... 
>>> # 空节点，用于标记循环结束节点
>>> class EmptyNode(WorkflowComponent):
...      def __init__(self, node_id: str):
...          super().__init__()
...          self.node_id = node_id
... 
...      async def invoke(self, inputs, session: Session, context: ModelContext):
...          return inputs
... 
>>> 
>>> # 执行测试
>>> # input_number=2, loop_number=2: 循环执行2次
>>> async def run_workflow():
...      flow = Workflow()
...      flow.set_start_comp("s", Start())
...      flow.set_end_comp("e", End(),
...                        inputs_schema={"array_result": "${l.results}", "user_var": "${l.user_var}"})
... 
...      # 创建循环体
...      loop_group = LoopGroup()
...      # AddTenNode("1"): 接收当前循环索引l.index作为输入，每次循环索引会自动递增
...      # 注意：这里的"1"是组件的标识ID，用于在output_schema中引用其结果
...      loop_group.add_workflow_comp("1", AddTenNode("1"), inputs_schema={"source": "${l.index}"})
...      # AddTenNode("2"): 接收中间变量l.user_var作为输入
...      loop_group.add_workflow_comp("2", AddTenNode("2"),
...                                   inputs_schema={"source": "${l.user_var}"})
...      # SetVariableComponent: 更新中间变量l.user_var为AddTenNode("2")的处理结果
...      # 这使得每次循环的中间变量值都可以基于前一次循环结果进行更新
...      set_variable_component = LoopSetVariableComponent({"${l.user_var}": "${2.result}"})
...      loop_group.add_workflow_comp("3", set_variable_component)
... 
...      # 添加分支组件来决定是否继续循环
...      # BranchComponent用于根据条件选择不同的执行路径
...      sw = BranchComponent()
...      # 第一个分支条件：当当前循环索引(l.index)大于等于(循环次数-1)时，执行break组件
...      # 注意：索引从0开始，所以循环3次时索引值为0,1,2，当索引=2时应该中断
...      sw.add_branch("${l.index} >= ${loop_number} - 1", ["4"], "1")
...      # 第二个分支条件：当当前循环索引小于(循环次数-1)时，进入循环结束节点，开始下一轮循环
...      # 注意：分支目标必须是循环体内部的节点，否则会出现 Channel not found 错误
...      sw.add_branch("${l.index} < ${loop_number} - 1", ["5"], "2")
... 
...      loop_group.add_workflow_comp("sw", sw)
... 
...      # 添加break组件
...      # LoopBreakComponent用于在满足条件时显式中断循环执行
...      # 这是在使用always_true循环类型时控制循环次数的关键组件
...      break_node = LoopBreakComponent()
...      loop_group.add_workflow_comp("4", break_node)
... 
...      # 添加空节点作为循环的结束节点
...      empty_node = EmptyNode("5")
...      loop_group.add_workflow_comp("5", empty_node)
... 
...      # 设置循环体的起始节点和结束节点
...      loop_group.start_nodes(["1"])
...      loop_group.end_nodes(["5"])
...      # 设置循环体内各组件的连接关系
...      loop_group.add_connection("1", "2")
...      loop_group.add_connection("2", "3")
...      loop_group.add_connection("3", "sw")
...      loop_group.add_connection("4", "5")
... 
...      # 创建LoopComponent
...      # 参数1: loop_group - 定义的循环体
...      # 参数2: output_schema - 定义循环组件的输出结构
...      # - results: 收集每次循环中AddTenNode("1")的结果到数组
...      # - user_var: 保留循环结束后的最终中间变量值
...      loop_component = LoopComponent(loop_group, {"results": "${1.result}", "user_var": "${l.user_var}"})
... 
...      # 添加到工作流
...      # loop_type: "always_true" - 表示循环会一直执行直到遇到LoopBreakComponent中断
...      # intermediate_var: 初始化循环内的中间变量，这里将input_number的值赋给user_var
...      # 在always_true类型的循环中，必须通过LoopBreakComponent来控制循环次数或终止条件
...      flow.add_workflow_comp("l", loop_component, inputs_schema={"loop_type": "always_true",
...                                                                 "intermediate_var": {"user_var": "${input_number}"}})
... 
...      # 设置连接
...      flow.add_connection("s", "l")
...      flow.add_connection("l", "e")
...      return await flow.invoke({"input_number": 2, "loop_number": 2}, create_workflow_session())
... 
>>> if __name__ == "__main__":
...      result = asyncio.get_event_loop().run_until_complete(run_workflow())
...      print(result.result)
... 
>>> # 预期结果说明：
>>> # - array_result: [10, 11] 是循环中每个AddTenNode("1")处理后的结果数组
>>> #   (索引0+10=10, 索引1+10=11)
>>> # - user_var: 22 是循环结束后的最终中间变量值，计算过程：
>>> #   初始值2 → 2+10=12 → 12+10=22（共2次循环）
>>> # 在always_true类型的循环中，循环执行次数由LoopBreakComponent和分支条件共同控制
>>> # 此示例中，当l.index >= loop_number - 1 (1 >= 2-1=1)时触发LoopBreakComponent中断循环
>>> {'output': {'array_result': [10, 11], 'user_var': 22}}
```


## class LoopSetVariableComponent

```python
class LoopSetVariableComponent(variable_mapping: dict[str, Any])
```

`LoopSetVariableComponent` 是openJiuwen内置的工作流的变量赋值组件，该组件提供了配置变量映射、动态设置和管理变量的能力，用于实现工作流中数据的动态传递和状态管理。该组件也支持在循环组件[LoopComponent]内部使用，通过变量赋值组件，可以实现循环过程中数据的动态更新和传递。

**参数**：

* **variable_mapping**(dict[str, Any])：变量映射字典，定义了要设置的变量名称和对应的变量取值，不可取值为`None`。该映射字典支持设置一个或多个键值对，键值对支持的类型如下所示：
  * 变量映射的键支持以下3种类型：

    | 类型| 描述 |  示例 |
    | :---: |  :---: |  :---: |
    | 非引用形式变量名称 | 字符串，直接获取变量名称 | `"node_a.var_value"` |
    | 单层引用形式变量名称 | 字符串，通过`"${variable_path}"`占位符语法取出变量名称，引用路径以符号`.`进行分割，只有单层路径 | `"${node_a.var_value}"`取出变量名称即`"node_a.var_value"` |
    | 嵌套引用形式变量名称 |字符串，通过`"${variable_path}"`占位符语法取出变量名称，引用路径以符号`.`进行分割，有多层路径  | `"${node_a.nested.var_value}"`取出变量名称即`"node_a.nested.var_value"` |

  * 变量映射的值支持以下3种类型：

    | 类型| 描述 |  示例 |
    | :---: |  :---: |  :---: |
    | 固定值 | 直接输入文本、数字等静态数据 | `"hello world"`、`123`等|
    | 单层动态引用 | 通过`"${variable_path}"`占位符语法取出变量动态值，引用路径以符号`.`进行分割，只有单层路径 | 假设`node_b.var_value = "hello world"`，则`"${node_b.var_value}"`取出变量动态值即`"hello world"` |
    | 嵌套动态引用 | 通过`"${variable_path}"`占位符语法取出变量动态值，引用路径以符号`.`进行分割，有多层路径 | 假设`node_b.nested = {"var_value": 123}`，则`"${node_b.nested.var_value}"`取出变量动态值即`123` |


**样例**：

* 样例1：
  
```python
>>> # 在循环体中使用`LoopSetVariableComponent`设置中间变量
>>> import asyncio
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.session import Session
>>> from openjiuwen.core.session.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import WorkflowComponent, Workflow, Start, End, LoopGroup, LoopSetVariableComponent, \
...     BranchComponent, LoopBreakComponent, LoopComponent
>>> 
>>> 
>>> class AddTenNode(WorkflowComponent):
...     def __init__(self, node_id: str):
...         super().__init__()
...         self.node_id = node_id
... 
...     async def invoke(self, inputs, session: Session, context: ModelContext):
...         return {"result": inputs["source"] + 10}
>>> 
>>> 
>>> class CommonNode(WorkflowComponent):
...     def __init__(self, node_id: str):
...         super().__init__()
...         self.node_id = node_id
... 
...     async def invoke(self, inputs, session: Session, context: ModelContext):
...         return inputs
... 
...     async def stream(self, inputs, session: Session, context: ModelContext):
...         yield await self.invoke(inputs, session, context)
>>> 
>>> 
>>> # 创建工作流
>>> flow = Workflow()
>>> # MockStartNode: 工作流的起始节点，简单地将输入数据传递给下一个节点
>>> flow.set_start_comp("s", Start())
>>> # MockEndNode: 工作流的结束节点，收集并返回工作流的最终结果
>>> flow.set_end_comp("e", End(), inputs_schema={"array_result": "${b.array_result}", "user_var": "${b.user_var}"})
>>> # CommonNode: 通用节点，将输入数据原样返回
>>> flow.add_workflow_comp("a", CommonNode("a"))
>>> flow.add_workflow_comp("b", CommonNode("b"),
>>>                        inputs_schema={"array_result": "${l.results}", "user_var": "${l.user_var}"})
>>> 
>>> # 创建循环体
>>> loop_group = LoopGroup()
>>> # AddTenNode: 特殊节点，将输入的source字段值加10后返回
>>> loop_group.add_workflow_comp("1", AddTenNode("1"), inputs_schema={"source": "${l.index}"})
>>> loop_group.add_workflow_comp("2", AddTenNode("2"), inputs_schema={"source": "${l.user_var}"})
>>> # 使用SetVariableComponent设置中间变量
>>> set_variable_component = LoopSetVariableComponent({"${l.user_var}": "${2.result}"})
>>> loop_group.add_workflow_comp("3", set_variable_component)
>>> loop_group.start_nodes(["1"])
>>> loop_group.end_nodes(["3"])
>>> loop_group.add_connection("1", "2")
>>> loop_group.add_connection("2", "3")
>>> 
>>> # 创建LoopComponent
>>> loop_component = LoopComponent(loop_group, {"results": "${1.result}", "user_var": "${l.user_var}"})
>>> 
>>> # 添加到工作流
>>> flow.add_workflow_comp("l", loop_component, inputs_schema={"loop_type": "number", "loop_number": 3,
>>>                                                            "intermediate_var": {"user_var": "${input_number}"}})
>>> 
>>> # 设置连接
>>> flow.add_connection("s", "a")
>>> flow.add_connection("a", "l")
>>> flow.add_connection("l", "b")
>>> flow.add_connection("b", "e")
>>> 
>>> 
>>> async def run_workflow():
...     return await flow.invoke({"input_number": 2}, WorkflowRuntime())
>>> 
>>> 
>>> if __name__ == "__main__":
...     # 执行测试
...     result = asyncio.get_event_loop().run_until_complete(run_workflow())
...     print(result.result)
{'output': {'array_result': [10, 11, 12], 'user_var': 32}}
```

* 样例2：
```python
>>> # 在循环体中使用`LoopSetVariableComponent`设置中间变量
>>> import asyncio
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.session import Session
>>> from openjiuwen.core.session.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import WorkflowComponent, Workflow, Start, End, LoopGroup, LoopSetVariableComponent, \
...     BranchComponent, LoopBreakComponent, LoopComponent
>>> 
>>> 
>>> class AddTenNode(WorkflowComponent):
...     def __init__(self, node_id: str):
...         super().__init__()
...         self.node_id = node_id
... 
...     async def invoke(self, inputs, session: Session, context: ModelContext):
...         return {"result": inputs["source"] + 10}
>>> 
>>> 
>>> class CommonNode(WorkflowComponent):
...     def __init__(self, node_id: str):
...         super().__init__()
...         self.node_id = node_id
... 
...     async def invoke(self, inputs, session: Session, context: ModelContext):
...         return inputs
... 
...     async def stream(self, inputs, session: Session, context: ModelContext):
...         yield await self.invoke(inputs, session, context)
>>> 
>>> 
>>> flow = Workflow()
>>> flow.set_start_comp("s", Start())
>>> flow.set_end_comp("e", End(), inputs_schema={"result": "${a.nested.value}"})
>>> 
>>>  # 创建SetVariableComponent，设置嵌套结构变量
>>> set_variable_component = LoopSetVariableComponent({"${a.nested.value}": 123})
>>> flow.add_workflow_comp("a", set_variable_component)
>>> 
>>>  # 设置连接
>>> flow.add_connection("s", "a")
>>> flow.add_connection("a", "e")
>>> 
>>> 
>>> async def run_workflow():
...     return await flow.invoke({"input_number": 2}, create_workflow_session())
>>> 
>>> 
>>> if __name__ == "__main__":
...     # 执行测试
...     result = asyncio.get_event_loop().run_until_complete(run_workflow())
...     print(result.result)
{'output': {'result': 123}}
```

* 样例3：
  
```python
>>> import asyncio
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.session import Session
>>> from openjiuwen.core.session.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import WorkflowComponent, Workflow, Start, End, LoopGroup, LoopSetVariableComponent, \
    BranchComponent, LoopBreakComponent, LoopComponent
>>> 
>>> class AddTenNode(WorkflowComponent):
...     def __init__(self, node_id: str):
...         super().__init__()
...         self.node_id = node_id
...     async def invoke(self, inputs, session: Session, context: ModelContext):
...         return {"result": inputs["source"] + 10}
>>> 
>>> flow = Workflow()
>>> flow.set_start_comp("s", Start(), inputs_schema={"input_value": "${input_value}"})
>>> flow.set_end_comp("e", End(), inputs_schema={"result": "${c.result}"})
>>> 
>>>  # 创建计算节点
>>>  # AddTenNode: 特殊节点，将输入的source字段值加10后返回
>>> flow.add_workflow_comp("a", AddTenNode("a"), inputs_schema={"source": "${s.input_value}"})
>>> 
>>>  # 使用SetVariableComponent通过引用路径设置变量
>>> set_variable_component = LoopSetVariableComponent({"${b.value}": "${a.result}"})
>>> flow.add_workflow_comp("b", set_variable_component)
>>> 
>>>  # 创建另一个计算节点，使用设置的变量
>>> flow.add_workflow_comp("c", AddTenNode("c"), inputs_schema={"source": "${b.value}"})
>>> 
>>>  # 设置连接
>>> flow.add_connection("s", "a")
>>> flow.add_connection("a", "b")
>>> flow.add_connection("b", "c")
>>> flow.add_connection("c", "e")
>>> 
>>>  # 执行测试
>>> async def run_workflow():
...       return await flow.invoke({"input_value": 5}, create_workflow_session())
>>> 
>>> if __name__ == "__main__":
...     result = asyncio.get_event_loop().run_until_complete(run_workflow())
...     print(result.result)
{'output': {'result': 25}}
```

