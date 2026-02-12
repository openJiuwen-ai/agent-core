# openjiuwen.core.session.stream

## class openjiuwen.core.session.stream.BaseStreamMode

```python
class openjiuwen.core.stream.BaseStreamMode
```

在工作流的`stream`方法中设置不同的`BaseStreamMode`类型，可以控制流式输出的数据格式和内容。

- **OUTPUT**：标准流式输出数据格式，输出为`OutputSchema`类型。
- **TRACE**：调试信息流式输出数据格式，输出为`TraceSchema`类型。
- **CUSTOM**：自定义流式输出数据格式，输出为`CustomSchema`类型。

## class openjiuwen.core.session.stream.CustomSchema

```python
class openjiuwen.core.session.stream.CustomSchema
```

用户自定义流式输出数据格式的数据类。

- **kwargs**(dict，可选)：用户自定义的流式数据。

**样例**：

基于[BaseStreamMode.CUSTOM](#class-openjiuwencoresessionstreamcustomschema)样例获取到输出：

```python
CustomSchema(custom_output = 'Check the weather in Shanghai on 2025-08-22')
```

## class openjiuwen.core.session.stream.OutputSchema

```python
class openjiuwen.core.session.stream.OutputSchema
```

openJiuwen标准流式输出数据格式的数据类。

- **type**(str)：流式数据的类型。当前支持两种内置的流式消息类型：
  - `'INTERACTION'`：表示流式消息为中断交互的中断流式消息
  - `'end node stream'`：表示流式消息为[End](../../workflow/components/flow/end_comp.md)组件的流式消息。
- **index**(int)：流式数据的下标。
- **payload**(Any)：流式数据的数据内容。

**样例**：

基于[​BaseStreamMode.OUTPUT](#class-openjiuwencoresessionstreamoutputschema)样例获取到输出：

```python
from openjiuwen.core.session.stream import OutputSchema

OutputSchema(type = 'end node stream', index = 0, payload = {'answer': '结果:'}),
OutputSchema(type = 'end node stream', index = 1, payload ={ 'answer': 'Check the weather in Shanghai on 2025-08-22' })
```

## class openjiuwen.core.session.stream.StreamMode

```python
class openjiuwen.core.session.stream.StreamMode
```

openJiuwen[流式输出数据格式](#class-openjiuwencoresessionstreambasestreammode)的基类。

**样例**：

打印该类的子类[BaseStreamMode](#class-openjiuwencoresessionstreambasestreammode)的`OUTPUT`格式。

```python
from openjiuwen.core.session.stream import BaseStreamMode

print(BaseStreamMode.OUTPUT)
```

输出如下：
```python
StreamMode(mode=output, desc=Standard stream data defined by the framework, options={})
```

## class openjiuwen.core.session.stream.StreamSchemas

```python
class openjiuwen.core.session.stream.StreamSchemas
```

openjiuwen流式数据格式的集合类，包含[OutputSchema](#class-openjiuwencoresessionstreamoutputschema)、[CustomSchema](#class-openjiuwencoresessionstreamcustomschema)、[TraceSchema](#class-openjiuwencoresessionstreamtraceschema)。

## class openjiuwen.core.session.stream.TraceSchema

```python
class openjiuwen.core.session.stream.TraceSchema
```

调试信息流式输出数据格式的数据类。

- **type**(str)： 流式数据的类型。当前支持两种内置的调试信息类型：
  
  - `'tracer_workflow'`：表示workflow的组件调测信息。
  - `'tracer_agent'`：表示agent的调测信息，包含大模型和插件等调用。
- **payload**(Any)：调测信息数据，有内置的格式定义，包含唯一标识、起止时间、输入输出、错误详情及组件状态等关键字段。
  
  - 基础调测字段：

    | 字段                      | 类型         | 说明                                                                 |
    |---------------------------|--------------|----------------------------------------------------------------------|
    | traceId                   | str          | 调测id                                                               |
    | startTime                 | datetime     | 开始时间，如 `datetime.datetime(2025, 8, 22, 14, 39, 9, 678133)` 表示2025年8月22日下午2点39分09秒678133微秒 |
    | endTime                   | datetime     | 结束时间，如 `datetime.datetime(2025, 8, 22, 14, 40, 9, 678133)` 表示2025年8月22日下午2点40分09秒678133微秒 |
    | inputs                    | dict         | 组件输入，如 `{'a_num': 1}`，为组件需要的入参                         |
    | outputs                   | dict         | 组件输出，如 `{'result': 2}`，为组件对应参数的输出                   |
    | error                     | dict         | 组件异常，如 `{"error_code": -1, "message": TypeError}`，包含错误码和异常信息 |
    | invokeId                  | str          | 事件id                                                               |
    | parentInvokeId            | str          | 父事件id，为上一个被执行组件的事件id                                 |
    | executionId               | str          | 执行id，等于调测id                                                   |
    | onInvokeData              | List[dict]   | 用于记录组件执行过程中的运行时信息                                   |
    | componentId               | str          | 组件id，无特殊传入值一般为空                                         |
    | componentName             | str          | 组件名，无特殊传入值一般为空                                         |
    | componentType             | str          | 组件类型，如使用`workflow.add_workflow_comp("a", CustomComponent())`为工作流添加组件时，“a”即为该组件的componentType                                     |
    | status                    | str          | 组件状态，包括“start”、“running”、“finish”、“error”，分别代表组件开始运行、组件正在运行、组件结束运行和组件异常 |

  - 循环组件字段：
    | 字段             | 类型   | 说明                                           |
    |------------------|--------|------------------------------------------------|
    | loopNodeId       | str    | 当组件位于循环组件中，为所属循环组件的invokeId |
    | loopIndex        | int    | 循环次数，从1开始计数                          |

  - 嵌套子workflow字段：
    | 字段             | 类型   | 说明                                                                 |
    |------------------|--------|----------------------------------------------------------------------|
    | parentNodeId     | str    | 如果组件位于子workflow中，为所属子workflow的invokeId，否则为"" |
  
**样例**：

- 样例一：基于[BaseStreamMode.TRACE](#class-openjiuwencoresessionstreambasestreammode)的基础工作流样例获取到输出。
  
  ```python
  import datetime
  from openjiuwen.core.session.stream import TraceSchema
  # 截取组件`a`的调测信息如下，可以看到会获取到两帧信息，包含一帧开始事件和一帧结束事件。其中，`traceId`用于标识此次调测事件，开始事件包含有`startTime`数值，`status`对应`“start”`，结束事件会刷新`endTime`数值，`status`对应`“end”`，`parentInvokeId`为上一次运行组件`start`的`invokeId`。
  # 组件a的调测开始事件帧
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
  # 组件a的调测结束事件帧
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
  ```

- 样例二：基于[BaseStreamMode.TRACE](#class-openjiuwencoresessionstreambasestreammode)的循环工作流样例获取到输出。
  
  ```python
  import datetime
  from openjiuwen.core.session.stream import TraceSchema
  # 截取位于循环体中的组件`a`的调测信息如下，可以看到会获取到两帧信息，包含一帧开始事件和一帧结束事件。其中，`loopIndex`表示该组件位于循环组件`loop`中，值为3，说明当前处于第3次循环执行状态。与此同时，循环体内组件`a`的`invokeId`为`loop.a`，而其`parentInvokeId`为`loop.c`，表示上一次执行的组件为上一轮循环中循环体内的组件`c`。
  # ...
  # 循环组件中组件a的第3次运行，调测开始帧
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
      'loopIndex': 3,
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
      'loopIndex': 3,
      'status': 'finish',
      'parentNodeId': ''
    })
  ...
  ```

- 样例三：基于[BaseStreamMode.TRACE](#class-openjiuwencoresessionstreambasestreammode)的嵌套工作流获取到输出。
  
  ```python
  import datetime
  from openjiuwen.core.session.stream import TraceSchema
  # 截取位于子工作流的`sub_a`组件调测信息如下，可以看到会获取到两帧信息，包含一帧开始事件和一帧结束事件。其中，该组件的`parentNodeId`字段记录其所属的子工作流的`invokeId`，即`"a"`。该字段主要用于嵌套子工作流的场景，以便明确组件的层级关系和归属结构。同时，`sub_a`组件位于子工作流组件`a`中，因此其`invokeId`为`"a.sub_a"`，用于标识该组件在整个工作流中的调用。
  # 子workflow中的sub_a组件的调测开始帧
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
  # 子workflow中的sub_a组件的调测结束帧
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
  ...
  ```