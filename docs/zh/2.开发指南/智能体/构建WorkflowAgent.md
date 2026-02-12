本章节示例演示了如何基于openJiuwen平台，从创建预置组件到编排工作流，最终构建并执行一个完整的天气查询WorkflowAgent。通过本示例，可以了解到如下信息：

- 如何创建组件，具体包括以下openJiuwen的预置组件：开始组件、结束组件、大模型组件、插件组件、意图识别组件、提问器组件。
- 如何创建Workflow流程图。
- 如何创建和执行`WorkflowAgent`。

# 应用设计流程

WorkflowAgent是一种专注于多步骤、任务导向的流程自动化Agent，通过严格遵循用户预定义的任务流程高效地执行复杂任务。用户可预先设定清晰的任务步骤、执行条件及角色分工，将任务拆解为多个可执行的子任务或工具，并通过组件间的拓扑连接与数据传递，逐步推进整个工作流，最终输出预期结果。其侧重于基于预设流程实现任务的规范化与高效化执行，适用于任务结构清晰、可分解为多个步骤的场景。

在本样例中，创建了一个天气查询工作流，确保能够按照预设的流程准确高效的查询到天气信息：

- 开始组件定义了工作流的输入参数规范。
- 意图识别组件判断用户请求的意图是否为查天气，如果是查天气意图，则路由到天气查询分支进行处理，否则路由到默认分支结束流程。
- 大模型组件用于对用户原始输入的query进行改写，自动补充当前日期信息和把地名转换为英文，方便提问器组件提取日期和地名信息。
- 提问器组件用于从用户输入信息中提取日期和地名信息。
- 插件组件用于使用提问器提取的日期和地名作为入参，调用天气插件进行天气查询。
- 结束组件定义了工作流的输出结果格式。

![WorkflowAgent](../images/WorkflowAgent.png)

# 前提条件

Python的版本应高于或者等于Python 3.11版本，建议使用3.11.4版本，使用前请检查Python版本信息。详细的规格约束请参考。

# 安装openJiuwen

执行如下命令安装openJiuwen：

```bash
pip install -U openjiuwen
```

# 创建Workflow流程

创建Workflow的整体流程如下：首先通过`Workflow`初始化工作流，指定`workflow_config`配置参数。接着定义各组件并将组件注册到工作流，并设置组件间的连接，从而完成整个工作流的创建。本示例设计的天气查询Workflow如下：用户输入 → 意图识别 → query改写 → 参数提取 → 调用天气API → 返回结果。示例代码如下：

## 初始化工作流

初始化工作流，指定`workflow_config`配置参数：

```python
from openjiuwen.core.workflow import Workflow, WorkflowCard
from openjiuwen.core.workflow.workflow_config import WorkflowConfig

# 初始化工作流与上下文
id = "test_weather_agent"
version = "1.0"
name = "weather"
workflow_config = WorkflowConfig(
    card=WorkflowCard(id=id, name=name, version=version,
                      description="天气查询工作流")
)
flow = Workflow(workflow_config=workflow_config)
```

## 注册组件到工作流

根据任务需求创建核心组件实例，配置各组件的输入/输出规则，并将组件注册到工作流。本教程主要使用以下openJiuwen的预置组件：开始组件、结束组件、大模型组件、插件组件、意图识别组件、提问器组件。

### 开始组件

通过`Start`创建开始组件对象。开始组件作为工作流的开端，定义了工作流的输入参数规范。在本例中，开始组件的入参为固定输入参数`query`，类型为字符串，并且参数值引用自工作流的输入。示例代码如下：

```python
from openjiuwen.core.workflow import Start

def create_start_component():
	return Start()
```

> **说明**
> 此处定义了开始组件的固定输入参数`query`，该参数的值引用自WorkflowAgent的输入，即用户在调用WorkflowAgent执行接口invoke、stream时，输入中必须带有`query`字段。例如`workflow_agent.invoke({"query": "你好"})`时，则开始组件接收到的输入为`{"query": "你好"}`。

### 结束组件

通过`End`创建结束组件对象。结束组件作为工作流的终止，定义了工作流的输出结果格式。在本示例中，输出结果的格式为输出文本。示例代码如下：

```python
from openjiuwen.core.workflow import End

def create_end_component():
	return End({"responseTemplate": "最终结果为：{{output}}"})
```

> **说明**
> 此处结束组件配置了输出文本的模板`responseTemplate`，因此最终结果会按照输出变量`output`字段的值，拼接得到字符串，作为结束组件的`responseContent`字段输出。

### 意图识别组件

通过`IntentDetectionComponent`构造意图识别组件，用于判断用户意图。意图识别组件提供了`add_branch`方法用于定义不同意图下路由到相应的分支链路。本示例用于判断用户请求的意图是否为查天气，如果是查天气意图，则路由到`llm`分支进行处理，否则路由到`end`分支结束流程。代码如下：

```python
import os
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.workflow import (
    IntentDetectionComponent, IntentDetectionCompConfig)

API_BASE = os.getenv("API_BASE", "your api base")
API_KEY = os.getenv("API_KEY", "your api key")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")

def create_model_config() -> ModelRequestConfig:
    """根据环境变量构造模型配置。"""
    return ModelRequestConfig(
        model=MODEL_NAME,
        temperature=0.8,
        top_p=0.9
    )

def create_model_client_config() -> ModelClientConfig:
    """根据环境变量构造客户端模型配置。"""
    return ModelClientConfig(
        client_provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        timeout=30,
        verify_ssl=False,
    )

def create_intent_detection_component() -> IntentDetectionComponent:
    """创建意图识别组件。"""
    model_client_config = create_model_client_config()
    model_config = create_model_config()
    user_prompt = "请识别意图"
    config = IntentDetectionCompConfig(
        user_prompt=user_prompt,
        category_name_list=["查询某地天气"],
        model_client_config=model_client_config,
        model_config=model_config,
    )
    intent_component = IntentDetectionComponent(config)
    intent_component.add_branch("${intent.classification_id} == 0", ["llm"], "默认分支")
    intent_component.add_branch("${intent.classification_id} == 1", ["llm"], "查询天气分支")
    return intent_component
```

需要注意的是：意图识别组件的提示词模板填充，依赖`IntentDetectionCompConfig`中的字段信息，能够更好地引导大模型识别用户输入的请求。

> - `{{user_prompt}}`：用户可自定义意图识别组件的提示词。
> - `{{category_name_list}}`：是一个​**预定义的意图类别列表**​，用于限定意图识别组件所能识别的功能分类范围。至少包含一个元素"默认意图"。
> - `{{model}}`：模型信息。

### 大模型组件

通过`LLMComponent`构造大模型组件对象，通过设置提示词引导大模型输出预定义格式的结果。本示例用于对用户原始输入的query进行改写，自动补充当前日期信息，便于后续提问器组件提取`location`和`date`字段信息，最终输出为文本类型。示例代码如下：

```python
from datetime import datetime
from openjiuwen.core.workflow import LLMComponent, LLMCompConfig

def build_current_date():
    current_datetime = datetime.now()
    return current_datetime.strftime("%Y-%m-%d")

def create_llm_component() -> LLMComponent:
    """创建 LLM 组件，仅用于抽取结构化字段（location/date）。"""
    model_client_config = create_model_client_config()
    model_config = create_model_config()
    current_date = build_current_date()
    user_prompt_prefix = "你是一个query改写的AI助手。今天的日期是{}。"
    user_prompt = "\n原始query为：{{query}}\n\n帮我改写原始query，要求：\n1. 只把地名改为英文，其他信息保留中文；\n2. 默认日期为今天；\n3. 时间为YYYY-MM-DD格式。"
    config = LLMCompConfig(
        model_client_config=model_client_config,
        model_config=model_config,
        template_content=[{"role": "user", "content": user_prompt_prefix.format(current_date) + user_prompt}],
        response_format={"type": "text"},
        output_config={
            "query": {"type": "string", "description": "改写后的query", "required": True}
        },
    )
    return LLMComponent(config)
```

### 提问器组件

通过`QuestionerComponent`构造提问器组件对象，支持从用户输入信息中提取指定参数；若出现参数提取不完整的情况，支持主动提问、收集用户反馈信息。本示例主要使用提问器提取参数的能力，一次性提取完整`location`和`date`字段的值，示例代码如下：

```python
from openjiuwen.core.workflow import FieldInfo, QuestionerComponent, QuestionerConfig

def create_questioner_component() -> QuestionerComponent:
    """创建提问器组件。"""
    key_fields = [
        FieldInfo(field_name="location", description="地点", required=True),
        FieldInfo(field_name="date", description="时间", required=True, default_value="today"),
    ]
    model_client_config = create_model_client_config()
    model_config = create_model_config()
    config = QuestionerConfig(
        model_client_config=model_client_config,
        model_config=model_config,
        question_content="",
        extract_fields_from_response=True,
        field_names=key_fields,
        with_chat_history=False,
    )
    return QuestionerComponent(config)
```

提问器组件的提示词模板填充，依赖`QuestionerConfig`中的字段信息，能够更好地引导大模型提取用户自定义的待提取参数。

- `{{required_name}}`对应`field_names`：提取待提取参数的参数名，并按照固定模板拼接为一段文本；当前的拼接方式结果为：`"地点、时间2个必要信息"`。
- `{{required_params_list}}`同样对应`field_names`：提取所有待提取参数的参数名和参数描述信息，并按照固定模板拼接为一段文本；当前的拼接方式结果为：`"location：地点\n date：时间"`。
- `{{extra_info}}`对应`extra_prompt_for_fields_extraction`：非必选配置，支持用户自定义约束。
- `{{example}}`对应`example_content`：非必选配置，支持用户自定义示例。
- `{{dialogue_history}}`对应对话历史：提问器组件默认能够从上下文中获取对话历史信息。

### 插件组件

通过`ToolComponent`构造插件组件节点，用于调用插件，通过`bind_tool`方法关联实际执行插件逻辑的`Tool`插件对象。本示例用于调用天气组件进行天气查询，示例代码如下：

```python
from openjiuwen.core.workflow import ToolComponent, ToolComponentConfig
from openjiuwen.core.foundation.tool.param import Param
from openjiuwen.core.foundation.tool.service_api.restful_api import RestfulApi

def create_plugin_component() -> ToolComponent:
    """创建插件组件，可调用外部 RESTful API。"""
    tool_config = ToolComponentConfig()
    weather_card = RestfulApiCard(
        name="WeatherReporter",
        description="天气查询插件",
        url="your weather search api url",
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
    weather_plugin = RestfulApi(card=weather_card)
    return ToolComponent(tool_config).bind_tool(weather_plugin)
```

上述部分介绍了各组件的实例化过程，下面统一介绍将组件注册到工作流：

```python
# 实例化各组件
start = create_start_component()
intent = create_intent_detection_component()
llm = create_llm_component()
questioner = create_questioner_component()
plugin = create_plugin_component()
end = create_end_component()

# 注册组件到工作流
flow.set_start_comp(
    "start",
    start,
    inputs_schema={"query": "${query}"},
)
flow.add_workflow_comp(
    "intent",
    intent,
    inputs_schema={"query": "${start.query}"},
)
flow.add_workflow_comp(
    "llm",
    llm,
    inputs_schema={"query": "${start.query}"},
)
flow.add_workflow_comp(
    "questioner",
    questioner,
    inputs_schema={"query": "${llm.query}"}
)
flow.add_workflow_comp(
    "plugin",
    plugin,
    inputs_schema={
        "location": "${questioner.location}",
        "date": "${questioner.date}",
        "validated": True,
    },
)
flow.set_end_comp("end", end, inputs_schema={"output": "${plugin.data}"})
```

## 连接组件

通过`flow.add_connection`方法设置组件间的连接关系，完成工作流的创建：

```python
# 连接组件
flow.add_connection("start", "intent")
flow.add_connection("llm", "questioner")
flow.add_connection("questioner", "plugin")
flow.add_connection("plugin", "end")
```

# 创建WorkflowAgent

通过`WorkflowAgentConfig`的初始化方法创建WorkflowAgentConfig对象，涵盖`WorkflowAgent`相关的描述信息等。示例代码如下：

```python
from openjiuwen.core.application.workflow_agent import WorkflowAgentConfig

agent_config = WorkflowAgentConfig(
    id="weather_agent",
    version="0.1.0",
    description="天气查询agent",
)
```

通过`WorkflowAgent`的初始化方法指定相关配置参数信息创建`WorkflowAgent`对象，再通过`bind_workflows`方法绑定工作流对象。示例代码如下：

```python
from openjiuwen.core.application.workflow_agent import WorkflowAgent

workflow_agent = WorkflowAgent(agent_config)
workflow_agent.add_workflows([flow])
```

# 运行WorkflowAgent

可以调用`invoke`方法，获取用户query的回复。示例代码如下：

```python
import asyncio

async def weather_workflow(agent: WorkflowAgent):
    result = await workflow_agent.invoke({"conversation_id": "12345","query": "上海天气如何"})
    print(f"{result}")


asyncio.run(test_weather_workflow())
```

查询成功后，会得到如下的结果：

```text
{"responseContent":"最终结果为：{'city': 'Shanghai', 'country': 'CN', 'feels_like': 36.21, 'humidity': 86, 'temperature': 29.21, 'weather': '多云', 'wind_speed': 5.81}"}
```

# 完整代码

```python
import asyncio
import os
from datetime import datetime

from openjiuwen.core.application.workflow_agent import WorkflowAgentConfig, WorkflowAgent
from openjiuwen.core.workflow import (
    Workflow,
    Start,
    End,
    IntentDetectionComponent, IntentDetectionCompConfig,
    LLMComponent, LLMCompConfig,
    QuestionerComponent, FieldInfo, QuestionerConfig,
    ToolComponent, ToolComponentConfig,
)
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool.service_api.restful_api import RestfulApi, RestfulApiCard
from openjiuwen.core.workflow import WorkflowCard
from openjiuwen.core.workflow.workflow_config import WorkflowConfig

API_BASE = os.getenv("API_BASE", "your api base")
API_KEY = os.getenv("API_KEY", "your api key")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")

def build_current_date():
    current_datetime = datetime.now()
    return current_datetime.strftime("%Y-%m-%d")

def create_model_config() -> ModelRequestConfig:
    """根据环境变量构造模型配置。"""
    return ModelRequestConfig(
        model=MODEL_NAME,
        temperature=0.8,
        top_p=0.9
    )

def create_model_client_config() -> ModelClientConfig:
    """根据环境变量构造客户端模型配置。"""
    return ModelClientConfig(
        client_provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        timeout=30,
        verify_ssl=False,
    )

def create_llm_component() -> LLMComponent:
    """创建 LLM 组件，仅用于抽取结构化字段（location/date）。"""
    model_client_config = create_model_client_config()
    model_config = create_model_config()
    current_date = build_current_date()
    user_prompt_prefix = "你是一个query改写的AI助手。今天的日期是{}。"
    user_prompt = "\n原始query为：{{query}}\n\n帮我改写原始query，要求：\n1. 只把地名改为英文，其他信息保留中文；\n2. 默认日期为今天；\n3. 时间为YYYY-MM-DD格式。"
    config = LLMCompConfig(
        model_client_config=model_client_config,
        model_config=model_config,
        template_content=[{"role": "user", "content": user_prompt_prefix.format(current_date) + user_prompt}],
        response_format={"type": "text"},
        output_config={
            "query": {"type": "string", "description": "改写后的query", "required": True}
        },
    )
    return LLMComponent(config)

def create_intent_detection_component() -> IntentDetectionComponent:
    """创建意图识别组件。"""
    model_client_config = create_model_client_config()
    model_config = create_model_config()
    user_prompt = "请识别意图"
    config = IntentDetectionCompConfig(
        user_prompt=user_prompt,
        category_name_list=["查询某地天气"],
        model_client_config=model_client_config,
        model_config=model_config,
    )
    intent_component = IntentDetectionComponent(config)
    intent_component.add_branch("${intent.classification_id} == 0", ["end"], "默认分支")
    intent_component.add_branch("${intent.classification_id} == 1", ["llm"], "查询天气分支")
    return intent_component

def create_questioner_component() -> QuestionerComponent:
    """创建提问器组件。"""
    key_fields = [
        FieldInfo(field_name="location", description="地点", required=True),
        FieldInfo(field_name="date", description="时间", required=True, default_value="today"),
    ]
    model_client_config = create_model_client_config()
    model_config = create_model_config()
    config = QuestionerConfig(
        model_client_config=model_client_config,
        model_config=model_config,
        question_content="",
        extract_fields_from_response=True,
        field_names=key_fields,
        with_chat_history=False,
    )
    return QuestionerComponent(config)

def create_start_component():
    """创建开始组件"""
    return Start({"inputs": [{"id": "query", "type": "String", "required": "true", "sourceType": "ref"}]})

def create_end_component():
    """创建结束组件"""
    return End({"responseTemplate": "最终结果为：{{output}}"})

def create_plugin_component() -> ToolComponent:
    """创建插件组件，可调用外部 RESTful API。"""
    tool_config = ToolComponentConfig()
    weather_card = RestfulApiCard(
        name="WeatherReporter",
        description="天气查询插件",
        url="your weather search api url",
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
    weather_plugin = RestfulApi(card=weather_card)
    return ToolComponent(tool_config).bind_tool(weather_plugin)

def bind_agent():
    id = "weather_workflow"
    version = "1.0"
    name = "weather"
    workflow_config = WorkflowConfig(
        card=WorkflowCard(id=id, name=name, version=version,
                          description="天气查询工作流")
    )
    flow = Workflow(workflow_config=workflow_config)

    start = create_start_component()  # 起始组件
    intent = create_intent_detection_component()  # 意图识别组件

    llm = create_llm_component()  # LLM改写组件
    questioner = create_questioner_component()  # 参数收集组件
    end = create_end_component()  # 结束组件
    plugin = create_plugin_component()  # 插件组件

    # 注册组件到工作流
    flow.set_start_comp(
        "start",
        start,
        inputs_schema={"query": "${query}"},
    )
    flow.add_workflow_comp(
        "intent",
        intent,
        inputs_schema={"query": "${start.query}"},
    )

    flow.add_workflow_comp(
        "llm",
        llm,
        inputs_schema={"query": "${start.query}"},
    )
    flow.add_workflow_comp(
        "questioner",
        questioner,
        inputs_schema={"query": "${llm.query}"}
    )
    flow.add_workflow_comp(
        "plugin",
        plugin,
        inputs_schema={
            "location": "${questioner.location}",
            "date": "${questioner.date}",
            "validated": True,
        },
    )
    flow.set_end_comp("end", end, inputs_schema={"output": "${plugin.data}"})

    flow.add_connection("start", "intent")
    flow.add_connection("llm", "questioner")
    flow.add_connection("questioner", "plugin")
    flow.add_connection("plugin", "end")

    agent_config = WorkflowAgentConfig(
        id="weather_agent",
        version="0.1.0",
        description="天气查询agent",
    )

    workflow_agent = WorkflowAgent(agent_config)
    workflow_agent.add_workflows([flow])
    return workflow_agent

async def weather_workflow(agent: WorkflowAgent):
    result = await agent.invoke({"conversation_id": "12345", "query": "上海今天天气如何"})
    print(result)

if __name__ == "__main__":
    os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")  # 关闭SSL校验仅用于本地调试，生产环境请务必打开
    os.environ.setdefault("SSRF_PROTECT_ENABLED", "false")  # ip地址合法性校验仅用于本地调试，生产环境请务必打开
    workflow_agent = bind_agent()
    asyncio.run(weather_workflow(workflow_agent))
```
