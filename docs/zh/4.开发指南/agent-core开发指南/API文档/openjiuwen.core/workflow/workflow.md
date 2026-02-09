# openjiuwen.core.workflow

## class WorkflowOutput

工作流执行`invoke`的输出结果的数据类。

* **result**(Union(list[[WorkflowChunk](./workflow.md#class-workflowchunk)], dict))：输出结果数据，对于执行状态不同，`result`的类型不同。
* **state**([WorkflowExecutionState](./workflow.md#enum-workflowexecutionstate))：当前执行结果的状态。

> **说明**
>
> * 当state为`WorkflowExecutionState.INPUT_REQUIRED`时，`result`为`list[WorkflowChunk]`类型，每个item为一个中断节点的信息；
> * 当state为`WorkflowExecutionState.COMPLETED`时，`result`为`dict`类型，为最终工作流的批输出结果。

## class WorkflowChunk

工作流执行`stream`返回的数据帧的数据类，类型为[CustomSchema]()，[TraceSchema]()，[OutputSchema]()其中之一。

## enum WorkflowExecutionState

工作流执行`invoke`的执行状态的枚举类。

* **COMPLETED**：执行完成状态。
* **INPUT_REQUIRED**：中断状态，需要等待用户交互输入。


## class WorkflowCard
工作流卡片数据类，继承自 `BaseCard`，包含工作流的基本信息和参数定义。

* **id**(str, 可选)：工作流的唯一标识符，默认为随机的UUID。
* **name**(str, 可选)：工作流名称，默认为`""`。
* **description**(str, 可选)：工作流描述，默认为`""`。
* **version**(str，可选)：工作流的版本，默认为`""`。
* **input_params**(Dict[str, Any] | Type[BaseModel], 可选)：工作流的输入参数模式（Schema）。可以是 JSON Schema 字典或 Pydantic 模型类。默认为空字典，表示没有输入参数模式，接受任何输入。

## class Workflow
```python
Workflow(card: WorkflowCard = None，**kwargs)
```

`Workflow`类是用于定义和执行异步计算图的核心类，作为有状态的执行引擎负责管理和运行由节点和边定义的计算图。创建`Workflow` 时，需首先定义工作流再添加节点和边，确保计算逻辑的有序执行。

​**参数**​：

* ​**card**​([WorkflowCard](./workflow.md), 可选)：工作流卡片，用于定义工作流的基本信息和输入定义，默认为`None`。
* **\*\*kwargs**：工作流环境变量配置，当前支持：
  * **workflow_max_nesting_depth**(int)：用于控制工作流的最大嵌套次数。默认为5，取值范围(0, 10]。

​**样例**​：

```python
>>> from openjiuwen.core.workflow import Workflow, WorkflowCard
>>> 
>>> # 建一个工作流卡片包含工作流的ID、名称、版本信息
>>> workflowCard = WorkflowCard(name="workflow_name", id="workflow_id", version="version-1")
>>> workflow = Workflow(workflow_config=workflowCard)
```

### set_start_comp
```python
def set_start_comp(self,
            start_comp_id: str,
            component: ComponentComposable,
            inputs_schema: dict | Transformer = None,
            outputs_schema: dict | Transformer = None
    )->Self
```

定义工作流的起始组件及数据转换规则，是工作流执行的入口。

​**参数**​：

* ​**start_comp_id**​(str)：起始组件的唯一标识符，用于在工作流中引用此组件。
* ​**component**​(ComponentComposable)：待添加的工作流组件实例。
* ​**inputs_schema**​(dict|Transformer,  可选)：起始组件输入参数的结构定义，键名应与组件输入参数名匹配，值为对应的输入参数类型。默认值：`None`。
* ​**outputs_schema**​(dict|Transformer, 可选)：起始组件输出结果的结构定义，用于定义输出数据的格式规范，便于后续组件解析。默认值：`None`。


​**样例**​：

```python
>>> from openjiuwen.core.workflow.start_comp import Start
>>> 
>>> # input示例：{"question": "Agent是什么？", "user_id": 123}
>>> workflow.set_start_comp("start", Start(), inputs_schema={"query": "${inputs.question}"})
```


### set_end_comp
```python
def set_end_comp(self,
            end_comp_id: str,
            component: ComponentComposable,
            inputs_schema: dict | Transformer = None,
            outputs_schema: dict | Transformer = None,
            stream_inputs_schema: dict | Transformer = None,
            stream_outputs_schema: dict | Transformer = None,
            response_mode: str = None
    ) -> Self:
```

定义工作流的结束组件，即工作流执行的最终节点，负责处理最终结果将数据传到graph外并终止工作流。

​**参数**​：

* ​**end_comp_id**​(str)：结束组件的唯一标识符，用于在工作流中引用此组件。
* ​**component**(ComponentComposablet)：待添加的工作流组件实例。
* ​**inputs_schema**​(dict|Transformer, 可选)：结束组件组件输入参数的结构定义，键名应与组件输入参数名匹配，值为对应的输入参数类型。默认值：`None`。
* ​**outputs_schema**​(dict|Transformer, 可选)：结束组件输出结果的结构定义，用于定义工作流最终返回结果的格式。默认值：`None`。
* ​**stream_inputs_schema**​(dict|Transformer, 可选)：结束组件输入参数的结构定义。仅适用于支持流式数据输入的组件，作用同`inputs_schema`；默认值：`None`。
* ​**stream_outputs_schema**​(dict|Transformer, 可选)：结束组件输出结果的结构定义。仅适用于支持流式数据输出的组件，作用同`outputs_schema`。默认值：`None`。
* ​**response_mode**​(str, 可选)：指定end组件支持的ComponentAbility)， 当前支持仅两种模式，默认值：`None`。
  
  * 若设置为`'streaming'`，表示使用结束组件的`ComponentAbility.STREAM`或`ComponentAbility.TRANSFORM`能力进行输出。
  * 若未设置或设置为非`streaming`，表示使用结束组件的`ComponentAbility.INVOKE`能力进行输出。

> **说明**
>
> * 当组件与上游组件连边是流式边的时候，可按需配置stream_inputs_schema。

​**样例**​：

```python
>>> from openjiuwen.core.workflow import End
>>> # 创建End组件（指定输出模板）
>>> end = End({"responseTemplate": "结果:{{final_output}}"})
>>> 
>>> # 注册End组件到工作流（批输出模式）
>>> workflow.set_end_comp("end", end, inputs_schema={"final_output": "${llm.query}"})
```

### add_workflow_comp
```python
def add_workflow_comp(
            self,
            comp_id: str,
            workflow_comp: ComponentComposable,
            *,
            wait_for_all: bool = None,
            inputs_schema: dict | Transformer = None,
            outputs_schema: dict | Transformer = None,
            stream_inputs_schema: dict | Transformer = None,
            stream_outputs_schema: dict | Transformer = None,
            comp_ability: list[ComponentAbility] = None
    ) -> Self
```

工作流组件添加接口，用于向当前工作流实例中添加一个具体的可执行组件或子工作流组件，并配置该组件的输入输出规则、数据流转换逻辑及能力属性。

​**参数**​：

* ​**comp_id**​(str)：组件的唯一标识符，用于在工作流中引用此组件。
* ​**workflow_comp**​(ComponentComposable)：待添加的工作流组件实例。
* ​**wait_for_all**​(bool, 可选)：是否等待所有前置依赖组件执行完成后再执行此组件。`True`表示需要等待所有前置依赖组件执行完成，`False`表示不需要。默认值：`False`。
* ​**inputs_schema**​(dict|Transformer, 可选)：组件输入参数的结构定义，键名应与组件输入参数名匹配，值为对应的输入参数类型。默认值：`None`。
* ​**outputs_schema**​(dict|Transformer, 可选)：组件输出结果的结构定义，用于定义输出数据的格式规范，便于后续组件解析。默认值：`None`。
* ​**stream_inputs_schema**​(dict|Transformer, 可选)：组件输入参数的结构定义。仅适用于支持流式数据输入的组件，作用同`inputs_schema`；默认值：`None`。
* ​**stream_outputs_schema**​(dict|Transformer, 可选)：组件输出结果的结构定义。仅适用于支持流式数据输出的组件，作用同`outputs_schema`。默认值：`None`。
* ​**comp_ability**​(list[ComponentAbility)], 可选)：指定组件支持的操作。当组件上下游既有流式又有批时，组件无法自行判断使用transform、invoke、collect、stream中的哪些能力，需指定该值。默认值：`None`，表示只使用invoke能力。

> **说明**
>
> * 当组件与上游组件连边是流式边的时候，可按需配置stream_inputs_schema；当组件与下游组件连边是流式边的时候，可按需配置stream_outputs_schema。



​**样例**​：

```python
>>> import os
>>> from datetime import datetime
>>> 
>>> from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
>>> from openjiuwen.core.workflow import LLMComponent, LLMCompConfig, Workflow
>>> 
>>> API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
>>> API_KEY = os.getenv("API_KEY", "sk-fake")
>>> MODEL_NAME = os.getenv("MODEL_NAME", "")
>>> MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
>>> os.environ.setdefault("LLM_SSL_VERIFY", "false")  # 关闭SSL校验仅用于本地调试，生产环境请务必打开
>>> SYSTEM_PROMPT_TEMPLATE = "你是一个query改写的AI助手。今天的日期是{}。"
>>> 
>>> fake_model_client_config = ModelClientConfig(
...         client_provider="OpenAI",
...         api_key="sk-fake",
...         api_base="https://api.openai.com/v1",
...         timeout=30,
...         max_retries=3,
...         verify_ssl=False
...     )
>>> fake_model_config = ModelRequestConfig(
...         model="fake-model",
...         temperature=0.7,
...         top_p=0.9
...     )
>>> 
>>> def build_current_date():
...      current_datetime = datetime.now()
...      return current_datetime.strftime("%Y-%m-%d")
... 
>>> def _create_llm_component() -> LLMComponent:
...      """创建 LLM 组件，仅用于抽取结构化字段（location/date）。"""
...      config = LLMCompConfig(
...             model_client_config=fake_model_client_config,
...             model_config=fake_model_config,
...             template_content=[{"role": "user", "content": "Hello {query}"}],
...             response_format={"type": "text"},
...             output_config={"result": {
...                 "type": "string",
...                 "required": True,
...             }},
...         )
...      return LLMComponent(config)
... 
>>> llm = _create_llm_component()
>>> workflow = Workflow()
>>> 
>>> # 注册大模型组件到工作流
>>> workflow.add_workflow_comp("llm", llm, inputs_schema={"query": "${start.query}"})
```

### add_connection
```python
def add_connection(self, src_comp_id: str | list[str], target_comp_id: str) -> Self
```

工作流组件连接接口，用于在源组件和目标组件间建立依赖关系，定义数据流和控制流的传递方向。

​**参数**​：

* ​**src_comp_id**​(str | list[str])：源组件的标识符，代表连接的起点。当传入字符串时表示单个起点；当传入字符串列表时，表示多个起点并行执行，共同指向同一目标组件。
* ​**target_comp_id**​(str)：目标组件的唯一标识符，表示连接的终点。

​**样例**​：

```python
>>> # 添加普通连接：从"start"组件指向"llm"
>>> workflow.add_connection("start", "llm")
>>> 
>>> # 添加普通连接：从"llm1", "llm2", "llm3"并行执行，并指向"end"
>>> workflow.add_connection(["llm", "llm2", "llm3"], "end")
```

### add_conditional_connection
```python
add_conditional_connection(self, src_comp_id: str, router: Router) -> Self
```

工作流组件条件连接接口，即从指定的源组件向多个可能的目标组件建立有条件依赖，控制流程和数据流向将根据路由中的规则动态决定。

​**参数**​：

* ​**src_comp_id**​(str)：源组件的唯一标识符。
* ​**router**​([Router](../graph/graph.md#type-alias-router))：决定下一步走向的“路由器”。它是一个普通的 Python 函数。

​**样例**​：

```python
>>> # 从Context中获取"start"组件输出的"query"字段值，基于query的大小决定下一执行的组件
>>> def router(session):
...     num = session.get_global_state("start.query")
...     if num == 0:
...         return "llm"
...     elif num == 1:
...         return "abc"
...     else:
...         return "end"
... 
>>> # 增加条件连接 start -> llm/abc/end
>>> workflow.add_conditional_connection("start", router=router)
```

### add_stream_connection
```python
add_stream_connection(self,  src_comp_id: str,  target_comp_id: str) -> Self
```

工作流组件流式连接接口，用于在源组件和目标组件间建立流式连接，支持实时数据流的处理和传递。

​**参数**​：

* ​**src_comp_id**​(str)：源组件的唯一标识符，输出流式消息的生产者组件ID，对应组件需实现stream接口或transform接口，产生实时数据流。
* ​**target_comp_id**​(str)：目标组件的唯一标识符，接受流式输入消息的消费者组件ID，对应组件需实现transform或collect接口，接收并处理实时数据流。

​**样例**​：

```python
>>> # 注册end组件时，指定流式连接输入定义stream_inputs_schema
>>> workflow.set_end_comp("end_stream", end, stream_inputs_schema={"data": "${llm.output}"})
>>> 
>>> # 添加流式连接：从"llm"指向"end"，其中大模型组件默认实现了【`stream`接口或`transform`接口】，end组件默认实现了【`stream`接口或`transform`接口】
>>> workflow.add_stream_connection("llm", "end_stream")
>>> # 注：llm节点样例见上文add_workflow_comp()接口
```

### invoke

```python
async def invoke(
            self,
            inputs: Input,
            session: Session,
            context: ModelContext = None,
            **kwargs
    ) -> WorkflowOutput
```

一次性处理完整批次数据的工作流执行方式。它接收一组完整数据作为输入，经过工作流处理后，一次性返回完整的处理结果。

​**参数**​：

* ​**inputs**​(Input)：工作流的输入数据，作为执行的初始参数。
* ​**session**​(Session)：工作流运行时环境，提供执行上下文和状态管理。
* **context**(ModelContext, 可选)：用于存储用户对话信息的上下文引擎对象。默认值：`None`，表示不开启上下文引擎功能。
* **kwargs**: 可选参数，当前支持：
    * **is_sub**(bool)：用于指定是否为嵌套的子工作流执行，默认为`False`，表示非子工作流执行。
    * **skip_inputs_validate**(bool)：用于指定是否跳过基于card指定的`input_params`的入参校验。默认为`False`，表示需要进行校验。
​**返回**​：

**WorkflowOutput**，工作流的执行结果，可能是普通输出或交互式输出。

​**样例**​：

```python
>>> import asyncio
>>> 
>>> from openjiuwen.core.workflow import create_workflow_session

>>> result = asyncio.run(workflow.invoke("查询上海的天气", create_workflow_session()))
>>> print(f"{result}")
{"state": "COMPLETED", "result": "结果:Check the weather in Shanghai on 2025-08-22"}
```

### stream

```python
async def stream(
            self,
            inputs: Input,
            session: Session,
            context: ModelContext = None,
            stream_modes: list[StreamMode] = None,
            **kwargs
    ) -> AsyncIterator[WorkflowChunk]
```

一个异步生成器方法。通过stream执行工作流后返回一个异步生成器，该生成器以流式方式逐步产生整个过程中的各个chunk；支持多种流模式和实时数据传递，使用户能即时看到进展并持续接收补充内容直至流程结束。

​**参数**​：

* ​**inputs**​(Input)：工作流的输入数据，作为执行的初始参数 。
* ​**session**​(Session)：工作流运行时环境，提供执行上下文和状态管理。
* **context**(ModelContext, 可选)：用于存储用户对话信息的上下文引擎对象。默认值：`None`，表示不开启上下文引擎功能。
* ​**stream_modes**​(list[StreamMode])], 可选)：流模式列表，指定流式处理的行为和输出内容。默认值：`None`，同`TRACE`逻辑。
* **kwargs**: 可选参数，当前支持：
    * **is_sub**(bool)：用于指定是否为嵌套的子工作流执行，默认为`False`，表示非子工作流执行。
    * **skip_inputs_validate**(bool)：用于指定是否跳过基于card指定的`input_params`的入参校验。默认为`False`，表示需要进行校验。
  
​**返回**​：

**AsyncIterator(WorkflowChunk)**，异步迭代器，逐步产生工作流处理的数据块。

​**样例**​：

openJiuwen提供了**三种流式输出方式**，提供了对于流式信息的定制化输出能力。由于所有流式消息在工作流内部共享输出管道，区分输出模式可提高流式消息的可扩展性。

* 样例一：当stream_modes为`​BaseStreamMode.OUTPUT`时，只输出框架定义的标准流式数据，数据为`OutputSchema`类型。
  
  ```python
  >>> from openjiuwen.core.workflow import create_workflow_session
  >>> from openjiuwen.core.stream.base import BaseStreamMode, OutputSchema
  >>> async for chunk in workflow.stream({"query": "查询上海的天气"}, create_workflow_session(), stream_modes=[BaseStreamMode.OUTPUT]):
  ...    print(chunk)
  >>> OutputSchema(type = 'end node stream', index = 0, payload = {'response': '结果:'}),
  >>> OutputSchema(type = 'end node stream', index = 1, payload ={ 'response': 'Check the weather in Shanghai on 2025-08-22' })
  ```

* 样例二：当stream_modes为`​BaseStreamMode.TRACE`时，只输出框架定义的调测流式数据，数据为`TraceSchema`类型。
  
  ```python

  >>> import asyncio
  >>> import datetime
  >>> 
  >>> from openjiuwen.core.context_engine import ModelContext
  >>> from openjiuwen.core.session.stream import TraceSchema, BaseStreamMode
  >>> from openjiuwen.core.workflow import create_workflow_session, WorkflowComponent, Input, Output, Workflow, Start, End, \
  ...   LoopGroup, LoopComponent, SubWorkflowComponent
  >>> # 1. 基础工作流
  >>> # 自定义组件`CustomComponent`，将输入直接作为输出返回
  >>> # 定义一个自定义组件
  >>> class CustomComponent(WorkflowComponent):
  ...     async def invoke(self, inputs: Input, session: Session, context: Context) -> Output:
  ...         return inputs
  ... 
  >>> # 搭建基础工作流
  >>> # start -> a -> end
  >>> workflow = Workflow()
  >>> # 设置start组件
  >>> workflow.set_start_comp("start", Start(), inputs_schema={"num": "${num}"})
  >>> # 设置自定义组件a
  >>> workflow.add_workflow_comp("a", CustomComponent(), inputs_schema={"a_num": "${start.num}"})
  >>> # 设置结束组件end
  >>> workflow.set_end_comp("end", End(conf={"responseTemplate": "hello:{{result}}"}),
  ...                   inputs_schema={"result": "${a.a_num}"})
  >>> # 连接组件： start -> a -> end
  >>> workflow.add_connection("start", "a")
  >>> workflow.add_connection("a", "end")
  >>> async def run_workflow_base():
  ...     async for chunk in workflow.stream({"num": 1}, session=create_workflow_session(), stream_modes=[BaseStreamMode.TRACE]):
  ...         print(f"stream chunk: {chunk}")

  >>> asyncio.run(run_workflow_base())
  
   >>> # 截取组件`a`的调测信息如下，可以看到会获取到两帧信息，包含一帧开始事件和一帧结束事件。其中，`traceId`用于标识此次调测事件，开始事件包含有`startTime`数值，`status`对应`“start”`，结束事件会刷新`endTime`数值，`status`对应`“end”`，`parentInvokeId`为上一次运行组件`start`的`invokeId`。
   >>> # 组件a的调测开始事件帧
   TraceSchema(type = 'tracer_workflow', payload = {
       'traceId': '86b76988-5549-482b-8401-444b2621641e',
       'startTime': datetime.datetime(2025, 8, 22, 14, 39, 9, 678133),
       'endTime': None,
       'inputs': {
           'a_num': 1,
       },
       'outputs': None,
       'error': None,
       'invokeId': 'a',
       'parentInvokeId': 'start',
       'executionId': '86b76988-5549-482b-8401-444b2621641e',
       'onInvokeData': None,
       'componentId': '',
       'componentName': '',
       'componentType': 'a',
       'loopNodeId': None,
       'loopIndex': None,
       'status': 'start',
       'parentNodeId': ''
   })
   >>> # 组件a的调测结束事件帧
   TraceSchema(type = 'tracer_workflow', payload = {
       'traceId': '86b76988-5549-482b-8401-444b2621641e',
       'startTime': datetime.datetime(2025, 8, 22, 14, 39, 9, 678133),
       'endTime': datetime.datetime(2025, 8, 22, 14, 39, 9, 679167),
       'inputs': {
           'a_num': 1,
       },
       'outputs': {
           'a_num': 1,
       },
       'error': None,
       'invokeId': 'a',
       'parentInvokeId': 'start',
       'executionId': '86b76988-5549-482b-8401-444b2621641e',
       'onInvokeData': None,
       'componentId': '',
       'componentName': '',
       'componentType': 'a',
       'loopNodeId': None,
       'loopIndex': None,
       'status': 'finish',
       'parentNodeId': ''
   })
  
  >>> # 2. 循环工作流
  >>> # 使用自定义组件`CustomComponent`搭建一个包含循环组件、开始组件、结束组件的工作流，循环组件使用数组循环模式，其中循环体内有三个串联的自定义组件`a`，`b`，`c`，分别输出每次循环迭代的数组元素：
  >>> # 自定义组件返回组件输入中"value"字段的值，赋值给output变量
  >>> class CustomComponent(WorkflowComponent):
  ...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
  ...         return {"output": inputs["value"]}
  ... 
  >>> # 创建LoopGroup
  >>> loop_group = LoopGroup()
  >>> 
  >>> # 为LoopGroup添加3个工作流组件(以数组循环为例)
  >>> loop_group.add_workflow_comp("a", CustomComponent(), inputs_schema={"value": "${loop.item}"})
  >>> loop_group.add_workflow_comp("b", CustomComponent(), inputs_schema={"value": "${loop.item}"})
  >>> loop_group.add_workflow_comp("c", CustomComponent(), inputs_schema={"value": "${loop.item}"})
  >>> 
  >>> # 指定组件"a"为循环开始，组件"c"为循环结束
  >>> loop_group.start_nodes(["a"])
  >>> loop_group.end_nodes(["c"])
  >>> 
  >>> # LoopGroup中的连接3个组件，a->b->c
  >>> loop_group.add_connection("a", "b")
  >>> loop_group.add_connection("b", "c")
  >>> 
  >>> # 创建LoopComponent
  >>> # 第一个参数是循环体(LoopGroup实例)，第二个参数定义了循环组件的输出模式
  >>> loop_component = LoopComponent(
  ...   loop_group, 
  ...   {"output": {"a": "${a.output}", "b": "${b.output}", "c": "${c.output}"}}
  ... )
  ... 
  >>> workflow = Workflow()
  >>> 
  >>> # 添加开始结束组件，结束组件的输入引用loop组件的输出
  >>> workflow.set_start_comp("start", Start(), inputs_schema={"query": "${user_input}"})
  >>> workflow.set_end_comp("end", End(), inputs_schema={"user_var": "${loop.output}"})
  >>> 
  >>> # 添加循环组件，输入的数组为开始组件的query输出
  >>> workflow.add_workflow_comp("loop", loop_component,
  ...                         inputs_schema={"loop_type": "array",
  ...                                      "loop_array": {"item": "${start.query}"}
  ...                                        })
  ... 
  >>> # 串行连接组件：start->loop->end
  >>> workflow.add_connection("start", "loop")
  >>> workflow.add_connection("loop", "end")
  >>> 
  >>> # 通过`flow.stream`方法执行工作流，并指定`stream_modes=[BaseStreamMode.TRACE]`，获取循环工作流的流输出调测信息：
  >>> async def run_workflow_loop():
  >>>     async for chunk in workflow.stream({"user_input": [1, 2, 3]}, session=create_workflow_session(), stream_modes=[BaseStreamMode.TRACE]):
  ...         print(f"stream chunk: {chunk}")
  >>> asyncio.run(run_workflow_loop())
   >>> # 截取位于循环体中的组件`a`的调测信息如下，可以看到会获取到两帧信息，包含一帧开始事件和一帧结束事件。其中，`loopIndex`表示该组件位于循环组件`loop`中，值为2（从0开始计数），说明当前处于第3次循环执行状态。与此同时，循环体内组件`a`的`invokeId`为`loop.a`，而其`parentInvokeId`为`loop.c`，表示上一次执行的组件为上一轮循环中循环体内的组件`c`。
   >>> # 循环组件中组件a的第3次运行，调测开始帧
   TraceSchema(type = 'tracer_workflow', payload = {
       'traceId': 'dad9cd8c-ac77-484f-90ae-20b263276e3f',
       'startTime': datetime.datetime(2025, 8, 25, 19, 31, 48, 361042),
       'endTime': None,
       'inputs': {
           'value': 3
       },
       'outputs': None,
       'error': None,
       'invokeId': 'loop.a',
       'parentInvokeId': 'loop.c',
       'executionId': 'dad9cd8c-ac77-484f-90ae-20b263276e3f',
       'onInvokeData': None,
       'componentId': '',
       'componentName': '',
       'componentType': 'loop.c',
       'loopNodeId': 'loop',
       'loopIndex': 2,
       'status': 'start',
       'parentNodeId': ''
     })
   # 循环组件中组件a的第3次运行，调测结束帧
   TraceSchema(type = 'tracer_workflow', payload = {
       'traceId': 'dad9cd8c-ac77-484f-90ae-20b263276e3f',
       'startTime': datetime.datetime(2025, 8, 25, 19, 31, 48, 361042),
       'endTime': datetime.datetime(2025, 8, 25, 19, 31, 48, 361042),
       'inputs': {
           'value': 3
       },
       'outputs': {
           'output': 3
       },
       'error': None,
       'invokeId': 'loop.a',
       'parentInvokeId': 'loop.c',
       'executionId': 'dad9cd8c-ac77-484f-90ae-20b263276e3f',
       'onInvokeData': None,
       'componentId': '',
       'componentName': '',
       'componentType': 'loop.a',
       'loopNodeId': 'loop',
       'loopIndex': 2,
       'status': 'finish',
       'parentNodeId': ''
     })
   ...
  
  >>> # 3. 嵌套工作流
  >>> # 搭建一个子工作流，包含开始组件、结束组件和之前自定义的`CustomComponent`，子工作流的输出等于调用工作流时的输入：
  >>> # 子workflow: sub_start -> sub_a -> sub_end
  >>> sub_workflow = Workflow()
  >>> # 子workflow的开始组件为sub_start
  >>> sub_workflow.set_start_comp("sub_start", Start(),
  ...                             inputs_schema={
  ...                                 "num": "${a_num}"})
  >>> # 子workflow的组件sub_a
  >>> sub_workflow.add_workflow_comp("sub_a", CustomComponent(),
  ...                                inputs_schema={
  ...                                    "a_num": "${sub_start.num}"})
  >>> # 子workflow的结束组件sub_end
  >>> sub_workflow.set_end_comp("sub_end",
  ...                           End(),
  ...                           inputs_schema={
  ...                               "result": "${sub_a.a_num}"
  ...                           })
  >>> # 连接子workflow各组件：sub_start -> sub_a -> sub_end
  >>> sub_workflow.add_connection("sub_start", "sub_a")
  >>> sub_workflow.add_connection("sub_a", "sub_end")
  >>> 
  >>> # 构建嵌套工作流，包含开始组件、结束组件和使用子工作流作为主工作流的组件"a"，主工作流的输出也等于主工作流调用时的输入：
  >>> # workflow: start -> a(子workflow) -> end
  >>> # 子workflow: sub_start -> sub_a -> sub_end
  >>> main_workflow = Workflow()
  >>> # 设置workflow的开始组件为start
  >>> main_workflow.set_start_comp("start", Start(),
  ...                              inputs_schema={
  ...                                  "num": "${num}"})
  >>> # 定义workflow组件a
  >>> main_workflow.add_workflow_comp("a", SubWorkflowComponent(sub_workflow),
  ...                                 inputs_schema={
  ...                                     "a_num": "${start.num}"})
  >>> # 设置workflow的结束组件为end
  >>> # 子工作流的End组件输出 {"output": {"result": ...}}，所以主工作流引用 ${a.result}
  >>> main_workflow.set_end_comp("end",
  ...                            End(conf={"responseTemplate": "hello:{{result}}"}),
  ...                            inputs_schema={
  ...                                "result": "${a.result}"
  ...                            })
  >>> # 连接workflow各组件：start -> a -> end
  >>> main_workflow.add_connection("start", "a")
  >>> main_workflow.add_connection("a", "end")
  >>> 
  >>> # 通过`flow.stream`方法执行工作流，并指定`stream_modes=[BaseStreamMode.TRACE]`，获取嵌套工作流的流输出调测信息：
  >>> async def run_workflow_sub():
  >>>     async for chunk in main_workflow.stream({"num": 1}, session=create_workflow_session(), stream_modes=[BaseStreamMode.TRACE]):
  ...         print(f"stream chunk: {chunk}")
  >>> asyncio.run(run_workflow_sub())
  >>> # 截取位于子工作流的`sub_a`组件调测信息如下，可以看到会获取到两帧信息，包含一帧开始事件和一帧结束事件。其中，该组件的`parentNodeId`字段记录其所属的子工作流的`invokeId`，即`"a"`。该字段主要用于嵌套子作流的场景，以便明确组件的层级关系和归属结构。同时，`sub_a`组件位于子工作流组件`a`中，因此其`invokeId`为`"a.sub_a"`，用于标识该组件在整个工作流中的调用。
  >>> # 子workflow中的sub_a组件的调测开始帧
  TraceSchema(type = 'tracer_workflow', payload = {
      'traceId': '416c45dd-12f1-427e-943f-e16c2171c69d',
      'startTime': datetime.datetime(2025, 8, 22, 17, 38, 13, 535141),
      'endTime': None,
      'inputs': {
        'a_num': 1
      },
      'outputs': None,
      'error': None,
      'invokeId': 'a.sub_a',
      'parentInvokeId': 'a.sub_start',
      'executionId': '416c45dd-12f1-427e-943f-e16c2171c69d',
      'onInvokeData': None,
      'componentId': '',
      'componentName': '',
      'componentType': 'a.sub_a',
      'loopNodeId': None,
      'loopIndex': None,
      'status': 'start',
      'parentNodeId': 'a'
  }), 
  >>> # 子workflow中的sub_a组件的调测结束帧
  TraceSchema(type = 'tracer_workflow', payload = {
      'traceId': '416c45dd-12f1-427e-943f-e16c2171c69d',
      'startTime': datetime.datetime(2025, 8, 22, 17, 38, 13, 535141),
      'endTime': datetime.datetime(2025, 8, 22, 17, 38, 13, 535141),
      'inputs': {
        'a_num': 1
      },
      'outputs': {
        'a_num': 1,
      },
      'error': None,
      'invokeId': 'a.sub_a',
      'parentInvokeId': 'a.sub_start',
      'executionId': '416c45dd-12f1-427e-943f-e16c2171c69d',
      'onInvokeData': None,
      'componentId': '',
      'componentName': '',
      'componentType': 'a.sub_a',
      'loopNodeId': None,
      'loopIndex': None,
      'status': 'finish',
      'parentNodeId': 'a'
  })
  ```

* 样例三：当stream_modes为`BaseStreamMode.CUSTOM​`时，只输出用户自定义流式数据，数据为`CustomSchema`类型。
  
  ```python
  >>> class TestLLMExecutable(LLMExecutable):
  ...     async def prepare_model_inputs(self, input_):
  ...         return await super()._prepare_model_inputs(input_)
  ...
  >>> class LLMComponent(WorkflowComponent):
  ...     async def invoke(self, inputs: Input, session: BaseSession, context: ModelContext) -> Output:
  ...         exe = TestLLMExecutable(LLMCompConfig(
  ...             model_client_config=fake_model_client_config,
  ...             model_config=fake_model_config,
  ...             template_content=[{"role": "user", "content": "Hello {query}"}],
  ...             response_format={"type": "text"},
  ...             output_config={"result": {
  ...                 "type": "string",
  ...                 "required": True,
  ...             }},
  ...         ))
  ...         model_inputs = await exe.prepare_model_inputs(fake_input(userFields=dict(query="pytest")))
  ...         llm_response = await llm.ainvoke(model_inputs)
  ...         response = llm_response.content
  ...
  ...         await session.write_custom_stream(**dict(custom_output=response))
  ...
  ...         # 2. 调用工作流流式输出
  ...         async for chunk in workflow.stream({"query": "查询上海的天气"}, session=create_workflow_session(),
  ...                                           stream_modes=[BaseStreamMode.CUSTOM]):
  ...             print(chunk)
  ...
  >>> def fake_input():
  ...     return lambda **kw: {USER_FIELDS: kw}
  ```


### draw

```python
def draw(
        self,
        title: str = "",
        output_format: str = "mermaid",  # "mermaid", "png", "svg"
        expand_subgraph: int | bool = False,
        enable_animation: bool = False,  # only works for "mermaid" format
        **kwargs
    ) -> str | bytes
```
生成工作流的可视化图表，支持导出 Mermaid 语法文本、PNG 静态图片、SVG 矢量图三种格式，统一封装工作流结构可视化能力，适用于工作流调试、文档生成与可视化展示场景。

​**参数**​：

* ​**title**​(str, 可选)：可视化图表标题，渲染后展示在图表顶部。默认值：""。
* ​**output_format**​(str, 可选)：指定输出格式，仅支持`"mermaid"`/`"png"`/`"svg"`三种类型。默认值：`"mermaid"`。
* ​**expand_subgraph**​(int | bool, 可选)：子工作流展开配置，布尔值控制全展开 / 不展开，非负整数控制子图展开深度。默认值：`False`。
* ​**enable_animation**​(bool, 可选)：Mermaid 格式动画开关，仅对 Mermaid 语法生效，开启后流式边可展示动态效果。默认值：`False`。
* ​**kwargs**​(dict, 可选)：额外渲染配置项，用于传递底层渲染器自定义参数。

​**返回**​：
* ​**str**​：当output_format="mermaid"时，返回标准 Mermaid 流程图语法字符串；
* ​**bytes**​：当output_format="png"或output_format="svg"时，返回对应格式图片的二进制数据流。

**样例**：

```python
>>> from openjiuwen.core.workflow import End
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.context_engine.base import ModelContext
>>> from openjiuwen.core.graph.executable import Output
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import Input, Workflow, WorkflowComponent
>>> 
>>> 
>>> # 自定义的组件
>>> class Node1(WorkflowComponent):
...     def __init__(self):
...         super().__init__()
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         return {}
... 
>>> 
>>> # 设置环境变量，启用工作流可视化功能
>>> import os
>>> os.environ["WORKFLOW_DRAWABLE"] = "true"
>>> 
>>> # 构建工作流
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start())
>>> flow.add_workflow_comp("a", Node1())
>>> flow.set_end_comp("end", End())
>>> flow.add_connection("start", "a")
>>> flow.add_connection("a", "end")
>>> 
>>> # 打印工作流的mermaid脚本
>>> print(flow.draw(title="simple workflow",output_format="mermaid"))
...
---
title: simple workflow
---
flowchart TB
	node_1("start")
	node_2["a"]
	node_3("end")
	node_1 --> node_2
	node_2 --> node_3

```
