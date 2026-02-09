# Start Component

The `Start` component is the built-in start component of the openJiuwen workflow. This component defines the entry point of the workflow and is used to receive user inputs.

Define the workflow, and add the start component `start` to the workflow via the `set_start_comp` method. The `inputs_schema` is used to clearly specify the source of input parameters for the start component `start`, and needs to follow the key-value configuration rules. The key comes from the parameter id value in "inputs" in `conf`, and the value can use the `${}` method to reference workflow input variable values:

```python
from openjiuwen.core.workflow import Workflow, Start

workflow = Workflow()
workflow.set_start_comp("s", Start(), 
    inputs_schema={"query": "${user_inputs.query}","tokens": "${user_inputs.tokens}"})
```

Configure the workflow inputs via `inputs` and run the workflow:

```python
import asyncio
from openjiuwen.core.workflow import create_workflow_session

session = create_workflow_session()
result = asyncio.run(workflow.invoke(inputs={"user_inputs": {"query": "hello world", "tokens": ["a", "b"]}}, session=session))
```

The `Start` component will directly output `inputs_schema` based on the user's `inputs` parameters. The output result is:

```python
{
    "query": "hello world",
    "tokens": ["a", "b"]
}
```

To output the final result of the workflow, you need to configure an `End` component. Below is a complete example of configuring a `Start` component and an `End` component, and running the output:

```python
from openjiuwen.core.workflow import Start
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow import create_workflow_session
import asyncio

start = Start()

# Create new workflow
workflow = Workflow()
workflow.set_start_comp("s", start, 
    inputs_schema={"query": "${user_inputs.query}", "tokens": "${user_inputs.tokens}"})

# Configure End component, can have template (conf) or not
end_conf = {"response_template": "Query:{{param1}}, Infos:{{param2}}, Tokens:{{param3}}"}
end = End(conf=end_conf)
workflow.set_end_comp("e", end, 
    inputs_schema={"param1": "${s.query}", "param2": "${s.infos}", "param3": "${s.tokens}"})

# Connect start and end nodes
workflow.add_connection("s", "e")

# Input and run
inputs = {
    "user_inputs": {
        "query": "你好 openJiuwen",
        "tokens": ["a", "b"]
    }
}
session = create_workflow_session()
result = asyncio.run(workflow.invoke(inputs=inputs, session=session))
print(result.result)
```

In the above code, the `End` component renders the template with the formatted input parameters and outputs the following result:

```python
{'response': "Query:你好 openJiuwen, Infos:['c', 'd'], Tokens:['a', 'b']"}
```

If the `End` component is not configured with a template `conf` (i.e., directly `End()`), the output is:

```python
{'output': {'param1': '你好 openJiuwen', 'param2': ['c', 'd'], 'param3': ['a', 'b']}}
```

# End Component

The `End` component is the built-in end component of the openJiuwen workflow, defining the output point of the workflow. When adding an `End` component to the workflow, you can format the input parameters of the `End` component according to `input_schema`. When creating an `End` component, you can render the workflow output information via the `conf` template. The `End` component implements four capabilities: `invoke`, `stream`, `transform`, and `collect`. The implementation principle is to perform corresponding formatted output for different inputs. The template of the `End` component adopts standard language specifications, using the `{{}}` syntax to represent placeholder slots. During the template rendering process, these slots are dynamically filled based on the actual input of the `End` component to generate the complete output result.

- **invoke**: Synchronously executes the component and returns the final result at once, suitable for scenarios where the complete result needs to be obtained in one go. When the `End` component is configured with a template (`conf` contains `response_template`), the returned result includes the `response` field, representing the result after template rendering; when the `End` component is not configured with a template, the returned result includes the `output` field, representing the output result of the `End` component.

  Construct a simple workflow and add a `Start` component to the workflow:

  ```python
  from openjiuwen.core.workflow import Start
  from openjiuwen.core.workflow import Workflow
  flow = Workflow()
  # Add Start node
  flow.set_start_comp("s", Start(),
                    inputs_schema={"query": "${user_inputs.query}", "content": "${user_inputs.content}"})
  ```
  
  Create an `End` component without a `conf` template and add it to the workflow, with `inputs_schema` specifying the input format of the `End` component:
  
  ```python
  from openjiuwen.core.workflow import End
  flow.set_end_comp("e", End(), inputs_schema={"param1": "${s.query}", "param2": "${s.content}"})
  ```
  
  Connect the `Start` component and the `End` component, and run the workflow. The workflow input is `{"user_inputs": {"query": "你好", "content": "杭州"}}`:
  
  ```python
  import asyncio
  from openjiuwen.core.workflow import create_workflow_session

  flow.add_connection("s", "e")
  # Call workflow
  session = create_workflow_session()
  result = asyncio.run(flow.invoke(inputs={"user_inputs": {"query": "你好", "content": "杭州"}}, session=session))
  # Output result
  print(result.result)
  ```
  
  The output of the `Start` component (which is the input of the `End` component) is `{"query": "你好", "content": "杭州"}`. The `inputs_schema` will format the `End` component input as `{"param1": "你好", "param2": "杭州"}` and output it to the output field. The output result is as follows:
  
  ```python
  {'output': {'param1': '你好', 'param2': '杭州'}}
  ```
  
  If creating an `End` component with a `conf` template:
  
  ```python
   conf = {"response_template": "渲染结果:{{param1}},{{param2}}"}
   flow.set_end_comp("e", End(conf=conf), inputs_schema={"param1": "${s.query}", "param2": "${s.content}"})
  ```
  
  The input of the `End` component will be rendered by the template and then output to the response field. The output result is as follows:
  
  ```python
  {'response': '渲染结果:你好,杭州'}
  ```

- **stream**: The `End` component formats batch results into multi-frame openJiuwen `OutputSchema` format streaming data, suitable for scenarios requiring real-time feedback. Streaming data is output based on `payload` and supports both rendered and non-rendered situations.

  Construct a simple workflow and add a `Start` component to the workflow:
  
  ```python
  from openjiuwen.core.workflow import Workflow
  from openjiuwen.core.workflow import Start
  from openjiuwen.core.workflow import End
  
  flow = Workflow()
  # Add Start node to workflow
  flow.set_start_comp("s", Start(), inputs_schema={"query": "${user_inputs.query}", "content": "${user_inputs.content}"})
  ```
  
  Create an `End` component without a `conf` template and add it to the workflow, with `inputs_schema` specifying the input format of the `End` component:
  
  ```python
  flow.set_end_comp("e", End(), inputs_schema={"param1": "${s.query}", "param2": "${s.content}"},response_mode="streaming")
  ```
  
  Connect the `Start` component and the `End` component, and run the workflow. The workflow input is `{"user_inputs": {"query": "你好", "content": "杭州"}}`:
  
  ```python
  import asyncio

  from openjiuwen.core.workflow import create_workflow_session
  from openjiuwen.core.session.stream import BaseStreamMode

  # Connect Start component and End component
  flow.add_connection("s", "e")

  async def main():
      # Call workflow streaming output interface
      session = create_workflow_session()
      result = flow.stream(inputs={"user_inputs": {"query": "你好", "content": "杭州"}}, session=session,
                          stream_modes=[BaseStreamMode.OUTPUT])

      streams = []
      async for s in result:
          print(s)
          streams.append(s)

      return streams

    # 运行异步函数
  if __name__ == "__main__":
      asyncio.run(main())
  ```
  
  The `End` component will iterate through each input field, outputting them separately as the `payload` of the streaming data. The output result is as follows:
  
  ```python
  # 每一帧都为End组件一个字段的输入
  type='end node stream' index=0 payload={'output': {'param1': '你好'}}
  type='end node stream' index=1 payload={'output': {'param2': '杭州'}}
  ```
  
  If creating an `End` component with a `conf` template:
  
  ```python
  # 为工作流增加End节点，并提供渲染模板
  conf = {"response_template": "渲染结果:{{param1}},{{param2}}"}
  flow.set_end_comp("e", End(conf=conf), inputs_schema={"param1": "${s.query}", "param2": "${s.content}"},response_mode="streaming")
  ```
  
  For the input data of the `End` component, the system will first decompose the template into multiple sub-templates based on slot symbols, then render these sub-templates one by one, and output each rendering result as the `payload` of the streaming data, finally writing to the response field. Final result:
  
  ```python
  # 子模板的渲染结果
   type='end node stream' index=0 payload={'response': '渲染结果:'}
   type='end node stream' index=1 payload={'response': '你好'}
   type='end node stream' index=2 payload={'response': ','}
   type='end node stream' index=3 payload={'response': '杭州'}
  ```

- **transform**: The `End` component formats the streaming input frame by frame into `OutputSchema` format streaming data. The `payload` is the content of each streaming input. Rendering output is not supported.

  For example, first create a mock node for streaming output:
  
  ```python
  from typing import AsyncIterator

  from openjiuwen.core.workflow import ComponentAbility, WorkflowComponent, Workflow
  from openjiuwen.core.workflow.components.component import Input, Output
  from openjiuwen.core.workflow.components import Session
  from openjiuwen.core.context_engine import ModelContext

  class MockStreamCmp(WorkflowComponent):
      async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
          yield inputs
  ```
  
  Construct a simple workflow and add a `Start` component to the workflow:
  
  ```python
  from openjiuwen.core.workflow import Workflow
  from openjiuwen.core.workflow import Start

  flow = Workflow()
  # Add Start node to workflow
  flow.set_start_comp("s", Start(),inputs_schema={"query": "${user_inputs.query}", "content": "${user_inputs.content}"})
  ```
  
  Add a mock node to the workflow:
  
  ```python
  from openjiuwen.core.workflow import ComponentAbility
  
  # Add mock node to workflow
  flow.add_workflow_comp("n", MockStreamCmp(), inputs_schema={"param1": "${s.query}", "param2": "${s.content}"},
                          comp_ability=[ComponentAbility.STREAM], wait_for_all=True)
  ```
  
  Add an End node to the workflow:
  
  ```python
  from openjiuwen.core.workflow import End

  # Add End node to workflow
  flow.set_end_comp("e", End(), stream_inputs_schema={"param1": "${n.param1}", "param2": "${n.param2}"}, response_mode="streaming")
  ```
  
  Connect the `Start` component, Mock component, and `End` component:
  
  ```python
  # Connect components
  flow.add_connection("s", "n")
  flow.add_stream_connection("n", "e")
  ```
  
  The workflow input is `{"user_inputs": {"query": "你好", "content": "杭州"}}`, triggering the `transform` capability of the `End` component:
  
  ```python
  import asyncio

  from openjiuwen.core.workflow import create_workflow_session
  from openjiuwen.core.session.stream import BaseStreamMode
  async def main():
      # Call workflow streaming output interface
      session = create_workflow_session()
      result = flow.stream(inputs={"user_inputs": {"query": "你好", "content": "杭州"}}, session=session,
                           stream_modes=[BaseStreamMode.OUTPUT])

      streams = []
      async for s in result:
          print(s)
          streams.append(s)

      return streams

  if __name__ == "__main__":
      asyncio.run(main())
  ```
  
  The output is:
  
  ```python
  type='end node stream' index=0 payload={'output': {'param1': '你好'}}
  type='end node stream' index=1 payload={'output': {'param2': '杭州'}}
  ```
  If creating an `End` component with a `conf` template:
  
  ```python
  # 为工作流增加End节点，并提供渲染模板
  conf = {"response_template": "渲染结果:{{param1}},{{param2}}"}
  flow.set_end_comp("e", End(conf=conf),
                    stream_inputs_schema={"param1": "${n.param1}", "param2": "${n.param2}"},
                    response_mode = "streaming")
  ```
  
  For the input stream data of the `End` component, the system will first decompose the template into multiple sub-templates based on slot symbols, then render these sub-templates one by one, and output them frame by frame:

  ```python
  type='end node stream' index=0 payload={'response': '渲染结果:'}
  type='end node stream' index=1 payload={'response': '你好'}
  type='end node stream' index=2 payload={'response': ','}
  type='end node stream' index=3 payload={'response': '杭州'}
  ```
  
- **collect**: The `End` component aggregates streaming data and merges it into a single complete return. When the `End` component is configured with a template (`conf` contains `response_template`), the returned result includes the `response` field, representing the final result after template rendering of all collected streaming data; when the `End` component is not configured with a template, the returned result includes the `collect_output` field, representing the content of all collected original streaming outputs. 
   For example, first create a mock node for streaming output:
  
   ```python
  from typing import AsyncIterator

  from openjiuwen.core.workflow import ComponentAbility, WorkflowComponent, Workflow
  from openjiuwen.core.workflow.components.component import Input, Output
  from openjiuwen.core.workflow.components import Session
  from openjiuwen.core.context_engine import ModelContext

  class MockStreamCmp(WorkflowComponent):
      async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
          yield inputs
   ```
  
  Construct a simple workflow and add a `Start` component to the workflow:
  
  ```python
  from openjiuwen.core.workflow import Workflow
  from openjiuwen.core.workflow import Start

  flow = Workflow()
  # Add Start node to workflow
  flow.set_start_comp("s", Start(),inputs_schema={"query": "${user_inputs.query}", "content": "${user_inputs.content}"})
  ```
  
  Add a mock node to the workflow:
  
  ```python
  from openjiuwen.core.workflow import ComponentAbility
  # Add mock node to workflow
  flow.add_workflow_comp("n", MockStreamCmp(), inputs_schema={"param1": "${s.query}", "param2": "${s.content}"},comp_ability=[ComponentAbility.STREAM], wait_for_all=True)
  ```
  
  Add an End node to the workflow:
  
  ```python
  from openjiuwen.core.workflow import End
  # Add End node to workflow
  flow.set_end_comp("e", End(), stream_inputs_schema={"param1": "${n.param1}", "param2": "${n.param2}"})
  ```
  
  Connect the `Start` component, Mock component, and `End` component:
  
  ```python
  # Connect components
  flow.add_connection("s", "n")
  flow.add_stream_connection("n", "e")
  ```
  
  The workflow input is `{"user_inputs": {"query": "你好", "content": "杭州"}}`, triggering the `collect` capability of the `End` component:
  
  ```python
  import asyncio
  from openjiuwen.core.workflow import create_workflow_session
  
  # Call workflow invoke interface
  result = asyncio.run(flow.invoke(inputs={"user_inputs": {"query": "你好", "content": "杭州"}}, session=create_workflow_session()))
  print(result)
  ```

  The output is:
  
  ```python
  result={'collect_output': [{'param1': '你好'}, {'param2': '杭州'}], 'output': None} state=<WorkflowExecutionState.COMPLETED: 'COMPLETED'>
  ```
  
  If creating an `End` component with a `conf` template:
  
  ```python
  # 为工作流增加End节点，并提供渲染模板
  conf = {"response_template": "渲染结果:{{param1}},{{param2}}"}
  flow.set_end_comp("e", End(conf=conf), stream_inputs_schema={"param1": "${s.query}", "param2": "${s.content}"})
  ```
  
  For the input stream data of the `End` component, the system will first decompose the template into multiple sub-templates based on slot symbols, then render these sub-templates one by one, and output each rendering result as the `payload` of the streaming data, finally writing to the `response` field. Final result:
  
  ```python
  result={'response': '渲染结果:你好,杭州'} state=<WorkflowExecutionState.COMPLETED: 'COMPLETED'>
  ```

# LLM Component

The Large Model (LLM) Component generates output content by invoking large language models based on input prompts. It supports filling user-defined input variables into predefined prompt templates, optionally enabling conversation history, and supports various output formats such as JSON, Markdown, and Text.

Create the configuration information object `LLMCompConfig` for the LLM component, specifying the specific configuration information of the large model invoked by the component (`model`), the prompt template information (`template_content`), the output format information (`response_format`), and the output parameter information (`output_config`). The LLM component also supports whether to enable conversation history (`enable_history`).

```python
from openjiuwen.core.foundation.llm import ModelConfig, BaseModelInfo
from openjiuwen.core.workflow import LLMCompConfig

# 定义模型配置变量（实际使用时需要替换为真实值）
MODEL_PROVIDER = "openai"  # 示例值
MODEL_NAME = "gpt-3.5-turbo"  # 示例值
API_BASE = "https://xxx"  # 示例值
API_KEY = "your-api-key"  # 示例值

model_config = ModelConfig(
    model_provider=MODEL_PROVIDER,
    model_info=BaseModelInfo(
        model=MODEL_NAME,
        api_base=API_BASE,
        api_key=API_KEY,
        temperature=0.7,
        top_p=0.9,
        timeout=30,
    )
)

config = LLMCompConfig(
    model=model_config,
    template_content=[
        {"role": "system", "content": "你是一个AI助手。"},
        {"role": "user", "content": "{{query}}"}
    ],
    response_format={"type": "text"},
    output_config={
        "output": {"type": "string", "description": "输出结果", "required": True}
    },
    enable_history=False
)
```

Create the LLM component object `LLMComponent`:

```python
from openjiuwen.core.workflow import LLMComponent
llm_component = LLMComponent(config)
```

The input definition of the LLM component contains only user-defined key-value pairs and no fixed input fields.

Add the LLM component object to the workflow using the `add_workflow_comp` method of `Workflow`, defining the component name as "llm", and the input parameters as custom key-value pairs, where the key is `"query"` and the value is `"Generate a verse about the moon"`:

```python
from openjiuwen.core.workflow import Workflow

flow = Workflow()
flow.add_workflow_comp(
            "llm",
            llm_component,
            inputs_schema={"query": "生成一句关于月亮的诗句"},
        )
```

The prompt template in `LLMCompConfig` uses placeholders in the `{{}}` format to fill in parameters from the user input. Therefore, the query field value in the user input is used to fill the prompt template. The fully filled prompt is:

```json
[
    {"role": "system", "content": "你是一个AI助手。"},
    {"role": "user", "content": "生成一句关于月亮的诗句"}
]
```

The output definition of the LLM component comes from the user-defined output parameters, specifically corresponding to the keys in `output_config` within `LLMCompConfig` (e.g., the `output` field in this example).

Therefore, when the user invokes the workflow, depending on the output format defined by the LLM component (the value of the `"type"` field in `response_format`), the output result is:

- If the text output format is defined, i.e., `response_format={"type": "text"}`, an example output result is:
  
  ```json
  {
      "output": "月走随人远，风来带桂香"
  }
  ```

- If the markdown output format is defined, i.e., `response_format={"type": "markdown"}`, with the input request `query="Generate a verse about the moon"` remaining unchanged, an example output result is:
  
  ```json
  {
      "output": "# **月走随人远，风来带桂香**"
  }
  ```

- If the json output format is defined, i.e., `response_format={"type": "json"}`, and the input request is `query="Extract the location information from the song title 'I Love Beijing Tiananmen'"`, it outputs a Key-Value pair `{"output": "Beijing"}`, and uniformly serves as a custom output parameter:
  
  ```json
  {
      "output": "北京"
  }
  ```

> **Note**
> In `output_config`, when outputting text or markdown (`"type"` field in `response_format` is configured as `"text"` or `"markdown"`), only a single output field can be configured; however, when the output is json (`"type"` field in `response_format` is configured as `"json"`), at least one output field must be configured.

# Plugin Component

The Plugin Component can invoke tools (including external APIs and local functions). By inputting parameters to the tool, executing the tool logic, and finally outputting the execution results of the tool, it implements functions such as information searching, web browsing, image generation, and file processing, thereby extending the capabilities of agents and workflows.

Use `RestfulApi` to create a plugin for interfacing with plugin services that provide Restful interfaces. Define the plugin name `name`, description `description`, input parameters `params`, request URL address `path`, request headers, request method, and other information:

```python
from openjiuwen.core.foundation.tool import RestfulApi, RestfulApiCard

weather_card = RestfulApiCard(
    name="WeatherReporter",
    description="天气查询插件",
    url="user's path to weather service",
    method="GET",
    headers={},
    input_params={
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "天气查询的地点，必须为英文"
            },
            "date": {
                "type": "string",
                "description": "天气查询的时间，格式为YYYY-MM-DD"
            }
        },
        "required": ["location", "date"]
    },
)
weather_tool = RestfulApi(card=weather_card)
```

Create the configuration information `ToolComponentConfig` for the plugin component. Currently, no specific configuration is involved, and it is reserved for future use.

```python
from openjiuwen.core.workflow import ToolComponentConfig

tool_config = ToolComponentConfig()
```

The input of the Plugin Component has no fixed parameters; developers must ensure correct input parameters based on the actual input of the plugin. In this example, they are `"location"` and `"date"`.

Create the Plugin Component object `ToolComponent` and associate it with the tool object via `bind_tool`:

```python
from openjiuwen.core.workflow import ToolComponent

tool_component = ToolComponent(tool_config)
# 插件组件绑定RestfulAPI对象
tool_component.bind_tool(weather_tool)
```

Add the plugin component object to the workflow using the `add_workflow_comp` method of `Workflow`, defining the component name as "tool" and the input parameters as several key-value pairs of custom parameters. For example, in the weather plugin binding example, enter two key-value pairs `{"location":"hangzhou","date":"2025-08-01"}`.

```python
from openjiuwen.core.workflow import Workflow

flow = Workflow()
flow.add_workflow_comp(
            "tool",
            tool_component ,
            inputs_schema={
                "location": "hangzhou",
                "date": "2025-08-01"
            },
        )
```

The output definition of the Plugin Component includes the execution return code `error_code`, the error message `error_message` passed through when the plugin execution fails, and the serialized data `data` returned when the plugin executes successfully.

When a user invokes the workflow, the following results will be output based on the execution status of the plugin:

- If the plugin service returns successfully, the execution error code `error_code` output by the plugin component is 0, the plugin execution error reason `error_message` is empty, and `data` is the plugin run result:
  
  ```python
  {
      "error_code": 0,
      "error_message": "",
      "data": "{'city': 'Shanghai', 'country': 'CN', 'feels_like': 36.21, 'humidity': 86, 'temperature': 29.21, 'weather': '多云', 'wind_speed': 5.81}"
  }
  ```

- If the plugin service returns a failure, the plugin component outputs the specific plugin execution error code `error_code` and the plugin execution error reason `error_message`:
  
  ```python
  {
      "error_code": 10000,  # 插件的自定义错误码
      "error_message": "plugin service unavailable",  # 插件的自定义错误错误信息
      "data": ""
  }
  ```

In addition, the framework supports creating tool objects based on local functions. For specific usage, please refer to [Custom Tools](../../Basic%20Functions/Custom%20Tools.md). The Plugin Component can associate the created tool object by calling the `bind_tool` interface.

> **Note**
>
> - The Plugin Component supports validating tool input parameter types. The validation is based on the `params` (`List[Param]`) of the tool (`Tool`) bound to the plugin component. If the input parameter type of the plugin component cannot be converted to the type specified by Param.type, an exception is thrown. The Plugin Component also supports filling in default values for required input parameters, also based on the `params(List[Param])` of the tool (`Tool`) bound to the plugin component. If an input parameter is a required parameter but is not input into the plugin component, the plugin component uses `Param.default_value` as the actual input value for that parameter.
> - A Plugin Component can only bind to a unique tool object. If `bind_tool` is called multiple times, only the last bound tool object is associated.

# Intent Detection Component

The Intent Detection Component uses Large Language Model (LLM) services to accurately identify and classify the intent of user inputs. Based on the downstream branch routing rules set by the user, it automatically routes tasks to the corresponding workflow components for processing.

Create the configuration object `IntentDetectionCompConfig` for the intent component, specifying the detailed configuration information of the large model invoked by the component (`model`), the prompt information for determining user intent (`user_prompt`), the list of intent category names (`category_name_list`), and whether to rely on conversation history (`enable_history`), etc.

```python
from openjiuwen.core.foundation.llm import ModelConfig, BaseModelInfo
from openjiuwen.core.workflow import IntentDetectionCompConfig, IntentDetectionComponent

# 定义模型配置变量（实际使用时需要替换为真实值）
MODEL_PROVIDER = "openai"  # 示例值
MODEL_NAME = "gpt-3.5-turbo"  # 示例值
API_BASE = "https://xxx"  # 示例值
API_KEY = "your-api-key"  # 示例值

model_config = ModelConfig(
    model_provider=MODEL_PROVIDER,
    model_info=BaseModelInfo(
        model=MODEL_NAME,
        api_base=API_BASE,
        api_key=API_KEY,
        temperature=0.7,
        top_p=0.9,
        timeout=30,
    )
)

config = IntentDetectionCompConfig(
    user_prompt="请判断用户意图",
    category_name_list=["查询某地天气"],
    model=model_config,
    example_content=["示例1：今天下雨吗？这是查询天气的意图"],
    enable_history=False
)
```

> **Note**
> The Intent Detection Component provides a default prompt template. You can use the field information in `IntentDetectionCompConfig` to fill this default prompt template to better guide the LLM in recognizing user input requests.
>
> - `user_prompt`: User-defined prompt information. This information will be filled as a segment into the built-in prompt template of the Intent Detection Component to help the LLM better recognize user intent.
> - `example_content`: User-defined examples. This information will be filled as a segment into the built-in prompt template of the Intent Detection Component to provide examples and help the LLM better recognize user intent.
> - `category_name_list`: Intent identifiers and intent information are concatenated according to rules and filled into the prompt template of the Intent Detection Component; a default intent is also supplemented (i.e., used as a fallback output when the user input does not match any user-defined intents).
> - User Input: Corresponds to the value of the `"query"` field in the input of the Intent Detection Component, serving as the object for intent recognition.
> - Conversation History: When the `enable_history` switch is turned on, the historical information in the current conversation context will be filled into the prompt template.

Create the intent detection component object `IntentDetectionComponent`, and use the `add_branch` method to add rules for routing to downstream branches. If the intent is to check the weather, route to the llm branch for processing; otherwise, route to the end branch to terminate the flow:

```python
intent_component = IntentDetectionComponent(config)
intent_component.add_branch("${intent.classification_id} == 1", ["llm"], "查询天气分支")
intent_component.add_branch("${intent.classification_id} == 0", ["end"], "默认分支")
```

> **Note**
> The `classification_id` for the default branch is fixed at 0. The `classification_id` for custom branches starts from 1 and increments sequentially.

The fixed input parameter for the Intent Detection Component is `query`, indicating that the component will recognize the intent based on this input information.

Add the Intent Detection Component object to the workflow using the `add_workflow_comp` method of `Workflow`, defining the component name as "intent", and referencing the value of the query field from the start component in the input parameters.

```python
from openjiuwen.core.workflow import Workflow

workflow = Workflow()
workflow.add_workflow_comp(
    "intent",
    intent_component,
    inputs_schema={"query": "${start.query}"},
)
```

The output definition of the Intent Detection Component includes `classification_id`, which represents the intent recognition number, and `reason`, which explains the intent classification.

When a user invokes the workflow, the following results will be output depending on the user input:

- When the user input is `"Check today's weather in Shanghai"`, it is recognized as a weather check request, and the output example may be as follows:
  
  ```json
  {
      "classification_id": 1,
      "reason": "用户问上海的天气怎么样，识别为查询天气的请求",
      "category_name": "查询某地天气"
  }
  ```

- When the user input is `"Please recommend a restaurant"`, it is recognized as not being a weather check request, so the default intent is used, and the output example may be as follows:
  
  ```json
  {
      "classification_id": 0,
      "reason": "用户请求未匹配任一意图，归类为默认意图",
      "category_name": "默认意图"
  }
  ```

# Questioner Component

The Questioner Component supports configuring preset questions and actively asking users questions based on these presets to collect feedback. At the same time, it supports configuring a parameter extraction switch. Once enabled, the system will extract specified parameters by combining user feedback and optional conversation history. Provided that the maximum number of follow-up questions is not exceeded, it can continue to ask follow-up questions to guide the user in completing the information. In addition, the component supports flexible integration with custom third-party LLMs and flexible configuration of prompt templates for parameter extraction.

Create the configuration object `QuestionerConfig` for the Questioner Component, specifying the detailed configuration information of the large model invoked by the component (`model`), custom preset questions (`question_content`), whether to extract parameters (`extract_fields_from_response`), parameter information to be extracted (`field_names`), and whether to extract parameters based on external conversation history (`with_chat_history`). Other configuration information will be filled into the prompt template built into the Questioner.

```python
from openjiuwen.core.foundation.llm import ModelConfig, BaseModelInfo
from openjiuwen.core.workflow import FieldInfo, QuestionerConfig, QuestionerComponent

# 定义模型配置变量（实际使用时需要替换为真实值）
MODEL_PROVIDER = "openai"  # 示例值
MODEL_NAME = "gpt-3.5-turbo"  # 示例值
API_BASE = "https://xxx"  # 示例值
API_KEY = "your-api-key"  # 示例值

model_config = ModelConfig(
    model_provider=MODEL_PROVIDER,
    model_info=BaseModelInfo(
        model=MODEL_NAME,
        api_base=API_BASE,
        api_key=API_KEY,
        temperature=0.7,
        top_p=0.9,
        timeout=30,
    )
)
# FieldInfo描述了需要提取的参数信息，包含参数名field_name、参数描述信息description、是否必须提取required以及参数默认值default_value。如果是必选参数且没有默认值，就必须提取该待提取参数的值。
key_fields = [
    FieldInfo(
        field_name="location",
        description="地点",
        required=True
    ),
    FieldInfo(
        field_name="date",
        description="时间",
        required=True,
        default_value="today",
    ),
]
config = QuestionerConfig(
    model=model_config,
    question_content="",
    extract_fields_from_response=True,
    field_names=key_fields,
    with_chat_history=False,
    extra_prompt_for_fields_extraction="开发者自定义的参数提取的约束",
    example_content="开发者自定义的样例"
)
```

> **Note**
> The Questioner Component provides a default prompt template, relying on the field information in `QuestionerConfig` to complete the filling, which can better guide the LLM to extract user-defined parameters.
>
> - `field_names`: Parameter names for extraction are concatenated into a text segment according to a fixed template; the current concatenation result format is: `"Location, Time 2 necessary infos"`. In addition, the parameter names and descriptions of all parameters to be extracted are concatenated into a text segment according to a fixed template and filled into the default prompt template; the current concatenation rule is `Parameter Name: Parameter Description`: `"location: Location\n date: Time"`.
> - `extra_prompt_for_fields_extraction`: User-defined prompt information. This information will be filled as a segment into the built-in prompt template of the Questioner Component to provide constraint information and help the LLM better extract parameter information.
> - `example_content`: User-defined examples. This information will be filled as a segment into the built-in prompt template of the Questioner Component to provide examples and help the LLM better extract parameter information.
> - Conversation History: When the `with_chat_history` switch is turned on, the Questioner Component can retrieve conversation history information from the context and fill it into the prompt template. When the `with_chat_history` switch is turned off, the conversation history only contains the most recent user message with `role=user`; when the `with_chat_history` switch is turned on, the conversation history contains not only the most recent user message but also the historical interaction information of the current session, i.e., several rounds of `role=user` and `role=assistant` dialogue.

Create the Questioner Component object `QuestionerComponent`:

```python
from openjiuwen.core.workflow import QuestionerComponent
questioner_component = QuestionerComponent(config)
```

The input definition of the Questioner Component includes the fixed input parameter `query` and user-defined key-value pairs of parameters.

Add the Questioner Component object to the workflow using the `add_workflow_comp` method of `Workflow`, naming the component "questioner", and referencing the value of the `query` field from the start component in the input parameters:

```python
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow import Start

workflow = Workflow()
workflow.set_start_comp("start", Start({"inputs": [{"id": "query", "type": "String", "required": "true", "sourceType": "ref"}]}), inputs_schema={"query": "${query}"})
workflow.add_workflow_comp(
    "questioner",
    questioner_component,
    inputs_schema={"query": "${start.query}"}
)
workflow.add_connection("start", "questioner")
```

By calling the `interact` interface of the session/interaction module inside the Questioner Component, the execution flow of the Questioner is interrupted, and a follow-up question is asked to the user. When a developer orchestrates a workflow containing a Questioner Component, the return value of the workflow includes the follow-up question. Developers can judge whether it is a return result of human-machine interaction through the field `state=WorkflowExecutionState.INPUT_REQUIRED` in the workflow output data structure `WorkflowOutput`.

In the following example, execute the workflow:

```python
import asyncio
from openjiuwen.core.workflow import create_workflow_session

session_id = "test_questioner"
session = create_workflow_session(session_id=session_id)  # Use the same session_id to support breakpoint continuation
workflow_result = asyncio.run(workflow.invoke({"query": "时间是2025年8月1日", "conversation_id": "c123"}, session))
print(repr(workflow_result))
```

The workflow output result is as follows:

```python
WorkflowOutput(
    result=[OutputSchema(type='__interaction__', index=0, payload=InteractionOutput(id='questioner', value='请您提供地点相关的信息'))], 
    state=<WorkflowExecutionState.INPUT_REQUIRED: 'INPUT_REQUIRED'>
)
```

It can be seen that the state is `WorkflowExecutionState.INPUT_REQUIRED`, requiring the user to construct input for interaction.

By creating an `InteractiveInput` object carrying the user's feedback information, you can call `invoke_workflow` again to execute the workflow, triggering the breakpoint to continue execution, re-executing the Questioner's process, and continuing to extract parameters based on user feedback.

```python
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput

# After user feedback on the location information `"地点是杭州"`, continue executing the workflow
user_input = InteractiveInput()
for item in workflow_result.result:
    user_input.update(item.payload.id, "地点是杭州")  # Get the breakpoint component id from the previous question to the user, and provide user feedback
session = create_workflow_session(session_id=session_id)  # Reuse the same session_id, the state in the session remains unchanged, thus enabling workflow breakpoint continuation
result = asyncio.run(workflow.invoke(user_input, session))
```

> **Note**
>
> - The original `session_id` must be used to ensure that the workflow continues execution from the previous breakpoint, which in this example is the Questioner Component waiting for user feedback.
> - If all the parameters to be extracted still cannot be fully extracted, the process of follow-up question -> interruption -> waiting for user feedback -> continuing execution from breakpoint is repeated until all parameters are fully extracted, and the Questioner outputs the parameter extraction results. If all parameters still cannot be fully extracted after exceeding the maximum number of times, an exception is thrown.

The output definition of the Questioner Component includes the content of the most recent follow-up question asked to the user (`question`), the content of the most recent user feedback (`user_response`), and the parameters extracted by the Questioner output in the form of key-value pairs.

When a user invokes the workflow, the following results will be output depending on the user input:

- When the user input is `"Shanghai on August 1, 2025"`, and the parameter extraction is complete, the output result is as follows:
  
  ```json
  {
      "question": "",
      "user_response": "",
      "location": "上海",
      "date": "2025-08-01"
  }
  ```

- When the user input is `"The time is August 1, 2025"`, and the parameter extraction is incomplete, if the maximum number of follow-up questions has not been reached, the Questioner Component will actively ask the user a question, corresponding to the `payload` field information in `WorkflowOutput` in the previous example:
  
  ```json
  id = "questioner"  # 工作流中断的组件id
  value = "请您提供地点相关的信息"  # 提问器向用户追问的问题内容
  ```
  
  After the user feeds back the location information `"The location is Hangzhou"` for the follow-up question, the Workflow object continues to execute, and the final output result of the Questioner Component is:
  
  ```json
  {
      "question": "请您提供地点相关的信息",
      "user_response": "地点是杭州",
      "location": "杭州",
      "date": "2025-08-01"
  }
  ```

- When the user input is irrelevant information (e.g., `"Help me book a flight"`), and the parameter extraction is incomplete, if the maximum number of follow-up rounds has been reached, an exception is thrown.
  
  ```text
  错误码为101074
  错误信息为"Questioner component exceed max response."
  ```

# Branch Component

During the execution of a workflow, it is often necessary to determine the subsequent process branches based on the current data state. In addition to relying on the conditional connection capabilities provided by the workflow itself, openJiuwen also pre-sets a `BranchComponent` to support branch logic control in a more concise way. Through the Branch Component, the workflow can be split into multiple parallel branch paths, and the specific branch to be executed is selected based on user-defined conditions. This not only enhances the flexibility of the process but also improves the efficiency and controllability of execution.

> **Note**
> The Branch Component is an optional component. If this component is added to the workflow, there is no need to explicitly call the `add_connection` or `add_conditional_connection` methods of the workflow to define the connection between this component and the target component, but the following constraints must be followed:
>
> - Input: Supports adding multiple conditional branches. However, please note that at least one branch must be set to meet the preset conditions during execution to ensure that the workflow can run normally. The judgment of branch preset conditions follows the following rules:
>   - The Branch Component judges one by one in order whether the current workflow data state meets the preset conditions in the branch. Once a branch meets the condition, it will immediately jump to the target component corresponding to that branch to continue execution, and the remaining branches will no longer be judged or executed.
>   - If no branches meet the conditions, the workflow execution will fail and throw a `JiuWenBaseException` with error code 101102 and error message "Branch meeting the condition was not found.".
> - Output: Supports directing the workflow to one or more predefined target components through conditional branches. However, please note that the returned target component ID will be used to control the workflow process jump. If you do not want to generate invalid branches, you need to set all target component IDs to component IDs that have been defined and registered in the workflow. When the branch logic returns one target component, the target component will be executed directly after the Branch Component is executed; when the branch logic returns multiple target components, the target components in the list will be executed in parallel through the task scheduling mechanism.

The following describes how to use the Branch Component to design workflow branch logic, including the creation of Branch Component instances, the addition of conditional judgment branches, and the process of running the workflow to observe branch execution results. The specific goal is to design a workflow with branch logic that flexibly executes different processing paths based on different ranges of input numerical values (positive, negative, zero).

Create a Branch Component instance with default configuration:

```python
from openjiuwen.core.workflow import BranchComponent

branch_comp = BranchComponent()
```

Use the `add_branch` method of the Branch Component to add preset conditions and target component ID lists for branches to implement branch logic. Preset conditions support three types: `str`, `Condition`, and `Callable[[], bool]`. Taking adding three branches to a Branch Component as an example, the usage of three different preset conditions is introduced respectively.

- Add a `str` type branch: Set the preset condition to `"${start.query} > 0"`, the target component to the end component, and the branch name to pos_branch.

```python
# str类型，表示字符串形式的bool表达式
expression_str = "${start.query} > 0"
branch_comp.add_branch(expression_str, ["end"], "pos_branch")
```

- Add a `Condition` type branch: Set the preset condition to `ExpressionCondition("${start.query} < 0")`, the target component to the custom absolute value calculation component, and the branch name to neg_branch.

```python
from openjiuwen.core.workflow import ExpressionCondition
# Condition类型，表示一个预定义的、可调用的条件对象
expression_condition = ExpressionCondition("${start.query} < 0")
branch_comp.add_branch(expression_condition, ["abs"], "neg_branch")
```

- Add a `Callable[[], bool]` type branch: Set the preset condition to the `expression_callable` method, the target component to the custom absolute value calculation component, and the branch name to zero_branch.

```python
import os

# Callable[[], bool]类型，表示一个返回值为bool类型的函数，用于判断条件是否满足
def expression_callable() -> bool:
   return str(os.environ.get("ALLOW_ZERO", "false")).lower() == "true"
branch_comp.add_branch(expression_callable, ["abs"], "zero_branch")
```

After adding the condition judgment logic, add the Branch Component to a workflow containing a start component, a custom absolute value calculation component, and an end component, and then execute the workflow. Complete example:

```python
import asyncio
import os

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.workflow import ExpressionCondition
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.workflow import ExpressionCondition
from openjiuwen.core.workflow import ComponentAbility, WorkflowComponent, Workflow
from openjiuwen.core.workflow.components.component import Input, Output
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.workflow import Workflow, WorkflowOutput
from openjiuwen.core.workflow import Start
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import BranchComponent


class AbsComponent(WorkflowComponent):
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        num = inputs["num"]
        if num < 0:
            return {"result": -num}
        return {"result": num}


async def run_workflow(num: int) -> tuple[WorkflowOutput | str, bool]:
    # 初始化工作流
    workflow = Workflow()

    # 添加开始、结束组件到工作流
    workflow.set_start_comp("start", Start(), inputs_schema={"query": "${user_inputs.query}"})
    workflow.set_end_comp("end", End(), inputs_schema={"raw": "${start.query}", "updated": "${abs.result}"})

    # 添加实现绝对值计算的自定义组件
    abs_comp = AbsComponent()
    workflow.add_workflow_comp("abs", abs_comp, inputs_schema={"num": "${start.query}"})

    # 添加分支组件
    branch_comp = BranchComponent()
    # 1. 分支pos_branch：condition为str类型
    expression_str = "${start.query} > 0"
    branch_comp.add_branch(expression_str, ["end"], "pos_branch")

    # 2. 分支neg_branch：condition为Condition类型
    expression_condition = ExpressionCondition("${start.query} < 0")
    branch_comp.add_branch(expression_condition, ["abs"], "neg_branch")

    # 3. 分支zero_branch：condition为Callable[[], bool]类型
    def expression_callable() -> bool:
        return str(os.environ.get("ALLOW_ZERO", "false")).lower() == "true"

    branch_comp.add_branch(expression_callable, ["abs"], "zero_branch")
    workflow.add_workflow_comp("branch", branch_comp)

    # Define component connections: branch component conditional edges do not need to explicitly call workflow's add_connection or add_conditional_connection methods
    workflow.add_connection("start", "branch")
    workflow.add_connection("abs", "end")
    # Construct input, workflow session, and call workflow
    inputs = {"user_inputs": {"query": num}}
    session = create_workflow_session()

    try:
        workflow_output = await workflow.invoke(inputs, session)
        return workflow_output, True
    except JiuWenBaseException as e:
        print(f"workflow execute error: {e}")
        return e.message, False


async def main():
    # 场景1：用户输入为10
    workflow_output, run_success = await run_workflow(num=10)
    run_status = "workflow run success" if run_success else "workflow run failed"
    result = workflow_output.result
    print(f"When the user input num is 10, {run_status}, result is {result}.")

    # 场景2：用户输入为-10
    workflow_output, run_success = await run_workflow(num=-10)
    run_status = "workflow run success" if run_success else "workflow run failed"
    result = workflow_output.result
    print(f"When the user input num is -10, {run_status}, result is {result}.")

    # 场景3：用户输入为0，环境变量ALLOW_ZERO为true
    os.environ["ALLOW_ZERO"] = "true"
    workflow_output, run_success = await run_workflow(num=0)
    run_status = "workflow run success" if run_success else "workflow run failed"
    result = workflow_output.result
    print(f"When the user input num is 0, ALLOW_ZERO is true, {run_status}, result is {result}.")

    # 场景4：用户输入为0，环境变量ALLOW_ZERO为false
    os.environ["ALLOW_ZERO"] = "false"
    workflow_output, run_success = await run_workflow(num=0)
    run_status = "workflow run success" if run_success else "workflow run failed"
    result = workflow_output
    print(f"When the user input num is 0, ALLOW_ZERO is false, {run_status}, result is {result}")


asyncio.run(main())
```

Output result:

```
When the user input num is 10, workflow run success, result is {'output': {'raw': 10}}.

When the user input num is -10, workflow run success, result is {'output': {'raw': -10, 'updated': 10}}.

When the user input num is 0, ALLOW_ZERO is true, workflow run success, result is {'output': {'raw': 0, 'updated': 0}}.

When the user input num is 0, ALLOW_ZERO is false, workflow run failed, result is Branch meeting the condition was not found.
```

From the workflow execution results, it can be seen that:

- When the user input is 10, the branch `pos_branch` is satisfied, and the components `start`, `branch`, and `end` are executed sequentially. The workflow execution is successful, and the output result is `{'output': {'raw': 10}}`.
- When the user input is -10, the branch `neg_branch` is satisfied, and the components `start`, `branch`, `abs`, and `end` are executed sequentially. The workflow execution is successful, and the output result is `{'output': {'raw': -10, 'updated': 10}}`.
- When the user input is 0 and the environment variable `ALLOW_ZERO` is `true`, `zero_branch` is satisfied, and the components `start`, `branch`, `abs`, and `end` are executed sequentially. The workflow execution is successful, and the output result is `{'output': {'raw': 0, 'updated': 0}}`.
- When the user input is 0 and the environment variable `ALLOW_ZERO` is `false`, no satisfying conditional branch is found. The workflow execution fails and throws a `JiuWenBaseException` with the error message "Branch meeting the condition was not found.".

# Loop Component

In workflow orchestration, simple loop processes can be achieved via conditional connections by jumping the process back to executed preceding components for repetition. However, for sub-process loop scenarios requiring multi-component coordination and complex logic, the more powerful `LoopComponent` should be used. This component supports configuring loop conditions to flexibly control the continuation or termination of the loop, thereby realizing more complex and refined process control logic.

The Loop Component defines the loop body through `LoopGroup` and controls the loop process in combination with loop configurations, supporting various loop scenarios. Additionally, it can work with the `LoopSetVariableComponent` to implement variable assignment and passing within the loop body for flexible variable control, and cooperate with the `LoopBreakComponent` to actively break out of the loop under specific conditions, achieving early termination of the loop.

The Loop Component supports 4 types of loop scenarios, configured via the `loop_type` parameter:

| Scenario Type | `loop_type` Value | Description |
| :--- | :--- | :--- |
| **Array Loop** | `"array"` | Iterates through an array, using each element of the array as variable input. The number of iterations equals the array length. The array can be configured based on variables output by preceding components in array type, or specific array data can be specified. |
| **Number Loop** | `"number"` | Specifies a fixed number of iterations. The count configuration can be based on output variable values of executed components or configured as a fixed value. |
| **Infinite Loop** | `"always_true"` | Implements an infinite loop. Components within the loop body execute indefinitely within the maximum limit (max 1000 iterations), relying entirely on `LoopBreakComponent` to implement the logic for terminating the loop process. |
| **Expression Loop** | `"expression"` | Controls loop conditions via an expression. The loop continues when the calculation result of the expression is true; otherwise, it terminates. It can be combined with intermediate variables to implement complex loop control logic. |

Inside the loop body, loop-related variables can be accessed in the form of `${loop_id.variable_name}`, where `loop_id` is the ID of the loop component. This ID can be customized to other fields, such as `loop1`, `loop2`, etc. Examples:

- `loop_id.index`: Current loop index (starting from 0).
- `loop_id.Current element name in Array Loop`: The current element in an Array Loop, e.g., `loop_id.item`.
- `loop_id.Custom variable name`: Intermediate variables defined via `intermediate_var`. Example: `workflow.add_workflow_comp("loop", loop_component, inputs_schema={..., "intermediate_var": {"user_var": "openJiuwen"}} )`. Inside the loop body, the custom variable can be accessed via `loop.user_var`.

When custom intermediate variables are needed in the loop body, these variables can be initialized via the `intermediate_var` parameter. These intermediate variables can be updated during the loop process via `LoopSetVariableComponent`, thereby implementing complex state management and loop control logic.

> **Note**
>
> 1. When adding a Loop Component to a workflow, subscript access (e.g., `"loop_array": {"item": "${a.b}[0]"}`、`"loop_number": "${a.b}[0]['c']"`、`LoopSetVariableComponent({"${loop.user_var": "${a.b}[0]"})`、`"intermediate_var": {"user_var": "${a.b}[0]['c']"}`) is **not supported** when configuring the `loop_array` parameter for Array Loop, the `loop_number` parameter for Number Loop, the `LoopSetVariableComponent` parameter, and the `intermediate_var` parameter. Otherwise, a "LoopComponent error" will be reported.
> 2. The Loop Component does not support nested loops.
> 3. `LoopSetVariableComponent` does not support updating output variables of preceding components.

## Array Loop

The Array Loop is the most common loop type, used to iterate through each element in an array and perform the same operation. The following details the usage of the Loop Component using a typical Array Loop as an example.

Create a loop body. Adding components within the loop body is consistent with adding components to a workflow. Specify the start and end components via the `start_nodes` and `end_nodes` methods, respectively.

```python
from openjiuwen.core.workflow import LoopGroup, LoopComponent
from openjiuwen.core.workflow import ComponentAbility, WorkflowComponent, Workflow
from openjiuwen.core.workflow.components.component import Input, Output
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.context_engine import ModelContext

# Common node component that returns input value
class CommonNode(WorkflowComponent):
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        return {"output": inputs["value"]}

# 创建LoopGroup
loop_group = LoopGroup()

# 为LoopGroup添加3个工作流组件(以数组循环为例)
# ${loop.item}表示当前数组元素，item在把循环组件添加到工作流时定义，${loop.index}表示当前索引
loop_group.add_workflow_comp("a", CommonNode(), inputs_schema={"value": "${loop.item}"})
loop_group.add_workflow_comp("b", CommonNode(), inputs_schema={"value": "${loop.item}"})
loop_group.add_workflow_comp("c", CommonNode(), inputs_schema={"value": "${loop.item}"})
# 指定组件"a"为循环开始，组件"c"为循环结束
loop_group.start_nodes(["a"])
loop_group.end_nodes(["c"])

# LoopGroup中的连接3个组件
loop_group.add_connection("a", "b")
loop_group.add_connection("b", "c")
```

Create the Loop Component, specifying the loop body and output mode. The output mode defines how data is extracted from the loop results.

```python
# 创建LoopComponent
# 第一个参数是循环体(LoopGroup实例)，第二个参数定义了循环组件的输出模式
loop_component = LoopComponent(
    loop_group, 
    {"output": {"a": "${a.output}", "b": "${b.output}", "c": "${c.output}"}}
)
```

Add the Loop Component to the workflow:

```python
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow import Start
from openjiuwen.core.workflow import End

# 创建工作流实例
workflow = Workflow()

# 添加开始组件，定义输入参数
workflow.set_start_comp("s", Start({}),
                        inputs_schema={"query": "${user_input}"})

# 添加结束组件，引用loop组件的输出结果
workflow.set_end_comp("e", End({}),
                   inputs_schema={"user_var": "${loop.output}"})



# 添加循环组件，设置数组循环类型和数组来源，这里的item与loop_group.add_workflow_comp中的loop.item对应
# 注意：loop_array配置不支持使用下标访问变量（如${a.b}[0]）
workflow.add_workflow_comp("loop", loop_component,
                           inputs_schema={"loop_type": "array",
                                          "loop_array": {"item": "${s.query}"}})

# 串行连接组件：start->loop->end
workflow.add_connection("s", "loop")
workflow.add_connection("loop", "e")
```

Run the workflow containing the Loop Component. The output result is an array formed by concatenating the output values of the 3 components within the loop body after 3 iterations:

```python
from openjiuwen.core.workflow import create_workflow_session
import asyncio

# 准备输入参数
inputs = {
    "user_input": [1, 2, 3]  # 要循环遍历的数组
}

# Call invoke method to execute workflow
result = asyncio.run(workflow.invoke(inputs, create_workflow_session()))

assert result.result["output"]["user_var"] == {"a": [1, 2, 3], "b": [1, 2, 3], "c": [1, 2, 3]}
```

## Number Loop

The Number Loop is used to execute loop operations a fixed number of times, suitable for scenarios where the required number of repetitions is known.

Configure `loop_type` as "number" in `LoopComponent`, and specify the number of iterations via `loop_number`. Inside the loop body, the current loop index (starting from 0) can be obtained via `${loop.index}`:

```python
from openjiuwen.core.workflow import LoopGroup, LoopComponent
from openjiuwen.core.workflow import ComponentAbility, WorkflowComponent, Workflow
from openjiuwen.core.workflow.components.component import Input, Output
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.context_engine import ModelContext

# Add 3 workflow components to LoopGroup, each component input is the current loop index
# (using the CommonNode component defined earlier)
class CommonNode(WorkflowComponent):
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        return {"output": inputs["value"]}

loop_group = LoopGroup()
loop_group.add_workflow_comp("a", CommonNode(), inputs_schema={"value": "${loop.index}"})
loop_group.add_workflow_comp("b", CommonNode(), inputs_schema={"value": "${loop.index}"})
loop_group.add_workflow_comp("c", CommonNode(), inputs_schema={"value": "${loop.index}"})
loop_group.start_nodes(["a"])
loop_group.end_nodes(["c"])
loop_group.add_connection("a", "b")
loop_group.add_connection("b", "c")

# 创建循环组件，设置输出模式
loop_component = LoopComponent(
    loop_group,
    {"output": {"a": "${a.output}", "b": "${b.output}", "c": "${c.output}"}}
)
```

Add to the workflow, setting the loop type and count:

```python
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow import Start
from openjiuwen.core.workflow import End

# 创建工作流实例
workflow = Workflow()

# 添加开始组件，定义输入参数
workflow.set_start_comp("s", Start({}),
                        inputs_schema={"query": "${user_input}"})

# 添加结束组件，引用loop组件的输出结果
workflow.set_end_comp("e", End({}),
                    inputs_schema={"user_var": "${loop.output}"})



# 添加循环组件，设置数组循环类型和数组来源
# 注意：loop_number配置不支持使用下标访问变量（如${a.b}[0]）
workflow.add_workflow_comp("loop", loop_component,
                           inputs_schema={"loop_type": "number",
                                          "loop_number": "${s.query}"})

# 串行连接组件：start->loop->end
workflow.add_connection("s", "loop")
workflow.add_connection("loop", "e")
```

The workflow structure remains unchanged. Run the workflow with an input of `3`. The output is an array of the results from the components within the loop body for each of the 3 iterations.

```python
from openjiuwen.core.workflow import create_workflow_session
import asyncio

# 准备输入参数
inputs = {
    "user_input": 3  # 要循环遍历的次数
}

# Call invoke method to execute workflow
result = asyncio.run(workflow.invoke(inputs, create_workflow_session()))

assert result.result["output"]["user_var"] == {"a": [0, 1, 2], "b": [0, 1, 2], "c": [0, 1, 2]}
```

## Expression Loop

The Expression Loop dynamically controls the continuation or termination of the loop through expressions, providing the most flexible way of loop control, suitable for complex loop condition judgments. Configure `loop_type` as "expression" in `LoopComponent`, set the loop condition expression via the `bool_expression` parameter, and define intermediate variables via `intermediate_var` to implement complex loop state management.

First, create the loop body and related components. Expression loops usually need to work with intermediate variables and the `LoopSetVariableComponent` to implement complex state management:

```python
import asyncio
from openjiuwen.core.workflow import LoopComponent, LoopGroup
from openjiuwen.core.workflow import LoopSetVariableComponent
from openjiuwen.core.workflow import ComponentAbility, WorkflowComponent, Workflow, ComponentExecutable
from openjiuwen.core.workflow.components.component import Input, Output
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow import create_workflow_session

# Simple addition component that adds 10 to the input value
class AddTenNode(ComponentExecutable, WorkflowComponent):
    def __init__(self, node_id: str = None):
        super().__init__()
        self.node_id = node_id

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        # 安全处理输入
        if inputs is None or "source" not in inputs:
            return {"result": 10}
        return {"result": inputs["source"] + 10}

# 创建LoopGroup
loop_group = LoopGroup()
# 添加第一个组件，从循环索引(loop.index)获取输入
loop_group.add_workflow_comp("1", AddTenNode("1"), inputs_schema={"source": "${loop.index}"})
# 添加第二个组件，从中间变量中获取user_var值作为输入
loop_group.add_workflow_comp("2", AddTenNode("2"),
                             inputs_schema={"source": "${loop.user_var}"})
# 创建变量设置组件，将第二个组件的结果更新到中间变量user_var中
set_variable_component = LoopSetVariableComponent({"${loop.user_var}": "${2.result}"})  
loop_group.add_workflow_comp("3", set_variable_component)

# 指定起始和结束组件
loop_group.start_nodes(["1"])
loop_group.end_nodes(["3"])

# 设置组件连接
loop_group.add_connection("1", "2")
loop_group.add_connection("2", "3")
```

Create the Loop Component, specifying the loop body and output mode:

```python
# 创建LoopComponent
loop_component = LoopComponent(loop_group, {"results": "${1.result}", "user_var": "${loop.user_var}"})
```

Add the Loop Component to the workflow, setting the Expression Loop type and loop condition:

```python
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow import Start
from openjiuwen.core.workflow import End

# 创建工作流实例
workflow = Workflow()

# 添加开始组件
workflow.set_start_comp("s", Start({}),
                        inputs_schema={"input_number": "${input_number}",
                                       "loop_number": "${loop_number}"})

# 添加结束组件，引用loop组件的输出结果
workflow.set_end_comp("e", End({}),
                      inputs_schema={"array_result": "${loop.results}", "user_var": "${loop.user_var}"})

# 添加循环组件，设置表达式循环类型、循环条件和中间变量
# 注意：intermediate_var配置不支持使用下标访问变量（如${a.b}[0]）
workflow.add_workflow_comp("loop", loop_component, 
                           inputs_schema={"loop_type": "expression",
                                          "bool_expression": "(${loop.index} != ${s.loop_number})",
                                          "intermediate_var": {"user_var": "${s.input_number}"}})

# 串行连接组件：start->loop->end
workflow.add_connection("s", "loop")
workflow.add_connection("loop", "e")
```

Run the workflow and verify the results:

```python
import asyncio
from openjiuwen.core.workflow import create_workflow_session

# 准备输入参数
inputs = {
    "input_number": 2,  # 初始化user_var的初始值
    "loop_number": 2   # 控制循环次数
}

# Call invoke method to execute workflow
result = asyncio.run(workflow.invoke(inputs, create_workflow_session()))

# 验证结果
output = result.result.get("output", {})

# 预期结果：
# - results: [10, 11] 是循环中每个AddTenNode("1")处理后的结果数组
#   (索引0+10=10, 索引1+10=11)
# - user_var: 22 是循环结束后的最终中间变量值，计算过程：
#   初始值2 → 2+10=12 → 12+10=22（共2次循环）
assert output.get("array_result") == [10, 11]
assert output.get("user_var") == 22
```

## Infinite Loop

The Infinite Loop is suitable for scenarios where the stop time needs to be dynamically decided based on runtime conditions. Early termination of the loop is implemented via `LoopBreakComponent`.

Configure `loop_type` as "always_true" in `LoopComponent` to implement an infinite loop. The loop body will execute continuously until it encounters a `LoopBreakComponent` or reaches the system's maximum loop limit (default is 1000 times). The following example demonstrates how to use an Infinite Loop combined with condition judgment and the Break Component to achieve flexible loop control.

The process within the loop body is shown below:

<div align="center">
  <img src="../../images/loop.png" alt="loop" width="40%">
</div>

Create the loop body, registering custom components, branch components, variable setting components, and the break component.

```python
from openjiuwen.core.workflow import ComponentAbility, WorkflowComponent, Workflow
from openjiuwen.core.workflow.components.component import Input, Output
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow import LoopGroup
from openjiuwen.core.workflow import LoopBreakComponent
from openjiuwen.core.workflow import LoopSetVariableComponent
from openjiuwen.core.workflow import BranchComponent

# Create LoopGroup
loop_group = LoopGroup()

# Create common node as the core component of the loop body
class CommonNode(ComponentExecutable, WorkflowComponent):
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        # Simulate the work of the loop body and return result
        return {"output": True}

# Create increment component for incrementing loop variable
class IncrementNode(ComponentExecutable, WorkflowComponent):
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        # 从输入中获取当前值并递增
        current_value = inputs.get("value", 0)
        return {"result": current_value + 1}

# 为LoopGroup添加工作流组件
loop_group.add_workflow_comp("a", CommonNode())

# 创建分支组件用于条件判断
branch_component = BranchComponent()
loop_group.add_workflow_comp("branch", branch_component)

# 创建递增组件，将循环变量的当前值加1
increment_node = IncrementNode()
loop_group.add_workflow_comp("increment", increment_node, inputs_schema={"value": "${loop.user_var}"})

# 创建变量设置组件，将递增后的值赋值回循环变量
set_variable_component = LoopSetVariableComponent({"${loop.user_var}": "${increment.result}"})
loop_group.add_workflow_comp("setVar", set_variable_component)

# 创建终止循环组件
break_node = LoopBreakComponent()
loop_group.add_workflow_comp("break", break_node)

# 指定组件"a"为循环开始，组件"setVar"为正常循环结束
loop_group.start_nodes(["a"])
loop_group.end_nodes(["setVar"])

# 设置BranchComponent的分支条件
# 分支1: 继续循环，执行increment和setVariableComponent
branch_component.add_branch("${loop.index} < 2", ["increment"])
# 分支2: 终止循环，执行break组件
branch_component.add_branch("${loop.index} >= 2", ["break"])

# 设置组件连接
loop_group.add_connection("a", "branch")
loop_group.add_connection("increment", "setVar")
```

Create the Loop Component and set intermediate variables:

```python
from openjiuwen.core.workflow import LoopComponent

# 创建LoopComponent，设置输出模式，捕获最终的中间变量、循环计数和循环结果
loop_component = LoopComponent(loop_group, {"user_var": "${loop.user_var}", "loop_count": "${loop.index}", "results": "${a.output}"})
```

Add to the workflow:

```python
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import Start

# 创建工作流实例
workflow = Workflow()

# 添加开始组件
workflow.set_start_comp("s", Start({}))

# 添加结束组件，引用loop组件的输出结果
workflow.set_end_comp("e", End({}),
                      inputs_schema={"user_var": "${loop.user_var}", "loop_count": "${loop.loop_count}", "results": "${loop.results}"})

# 添加循环组件，设置为always_true循环类型和中间变量初始值
# 注意：intermediate_var配置不支持使用下标访问变量（如${a.b}[0]）
workflow.add_workflow_comp("loop", loop_component,
                           inputs_schema={"loop_type": "always_true",
                                          "intermediate_var": {"user_var": 0}})
# 串行连接组件：start->loop->end
workflow.add_connection("s", "loop")
workflow.add_connection("loop", "e")
```

The workflow structure remains unchanged. Run the workflow; the loop body loops infinitely until the intermediate variable value is 2, at which point the loop terminates and outputs the final intermediate variable value:

```python
import asyncio
from openjiuwen.core.workflow import create_workflow_session

# Call invoke method to execute workflow
result = asyncio.run(workflow.invoke({}, create_workflow_session()))

print(f"执行结果: {result.result}")
output = result.result.get('output', {})
# 验证循环执行了3次（索引0、1、2）
assert output.get("loop_count") == 3  # 最后一次循环的索引是3
# 验证每个循环的执行结果
assert output.get("results") == [True, True, True], f"Expected [True, True, True], got {output.get('results')}"
```

# Workflow Execution Component

In some complex workflow scenarios, nested calls of workflows are often required. In such cases, the Workflow Execution Component can be introduced into the main workflow. The openJiuwen framework provides a built-in component `SubWorkflowComponent` to support the execution of sub-workflows. With this component, a complete workflow can be embedded into the main workflow and run as a single component. This mechanism effectively improves the modularity and reusability of workflows, allowing users to organize and manage complex task processes more flexibly.

The following example introduces how to use `SubWorkflowComponent` to implement workflow nesting functionality.

First, define the `CustomComponent` component:

```python
from openjiuwen.core.workflow import ComponentAbility, WorkflowComponent, Workflow
from openjiuwen.core.workflow.components.component import Input, Output
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.context_engine import ModelContext

class CustomComponent(WorkflowComponent):
    """Custom component"""

    def __init__(self):
        super().__init__()

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """Process input and return user query"""
        query = inputs.get("query", "")
        return {
            "result": query
    }
```

Next, define the sub-workflow, containing `Start`, `CustomComponent`, and `End` components:

```python
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow import Start
from openjiuwen.core.workflow import End

# 初始化子工作流
sub_workflow = Workflow()

# 添加以sub_start、sub_node、sub_end为ID的3个组件到子工作流
sub_workflow.set_start_comp("sub_start", Start(), inputs_schema={"query": "${query}"})
sub_workflow.add_workflow_comp("sub_custom_comp", CustomComponent(), inputs_schema={"query": "${sub_start.query}"})
sub_workflow.set_end_comp("sub_end", End(), inputs_schema={"result": "${sub_custom_comp.result}"})

# 设置子工作流拓扑连接，sub_start -> sub_custom_comp -> sub_end
sub_workflow.add_connection("sub_start", "sub_custom_comp")
sub_workflow.add_connection("sub_custom_comp", "sub_end")
```

Finally, define the main workflow and add the sub-workflow to it:

```python
from openjiuwen.core.workflow import SubWorkflowComponent

# 初始化主工作流
main_workflow = Workflow()

# 使用之前构造的子工作流实例构造工作流执行组件
sub_workflow_comp = SubWorkflowComponent(sub_workflow)

# 添加以start、sub_workflow_comp、end为ID的3个组件到主工作流
main_workflow.set_start_comp("start", Start({}), inputs_schema={"query": "${user_inputs.query}"})
main_workflow.add_workflow_comp("sub_workflow_comp", sub_workflow_comp, inputs_schema={"query": "${start.query}"})
main_workflow.set_end_comp("end", End({}), inputs_schema={"result": "${sub_workflow_comp.output.result}"})

# 设置主工作流拓扑连接，start -> sub_workflow_comp -> end
main_workflow.add_connection("start", "sub_workflow_comp")
main_workflow.add_connection("sub_workflow_comp", "end")
```

Execute the main workflow, invoking the created sub-workflow via the Workflow Execution Component:

```python
import asyncio
from openjiuwen.core.workflow import create_workflow_session

async def run_workflow():
    # Create workflow session
    session = create_workflow_session(session_id="test_session")

    # Construct input
    inputs = {"user_inputs": {"query": "hello"}}

    # Call workflow
    result = await main_workflow.invoke(inputs, session)
    return result


res = asyncio.run(run_workflow())
print(f"main workflow with sub workflow run result: {res}")
```

The result obtained after execution:

```
main workflow with sub workflow run result: result={'output': {'result': 'hello'}} state=<WorkflowExecutionState.COMPLETED: 'COMPLETED'>
```