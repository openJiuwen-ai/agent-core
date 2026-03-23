# ReAct Agent 工作流组件

ReAct Agent 工作流组件将 ReAct（Reasoning + Acting）代理的强大功能引入工作流系统。它允许您在工作流编排中融入复杂的推理和工具使用能力。

## 特性

- 在工作流上下文中实现完整的 ReAct 代理功能
- 支持所有工作流执行模式（invoke、stream、collect、transform）
- 工具执行能力
- 上下文管理和记忆持久化
- 可配置的迭代限制

## 使用方法

```python
from openjiuwen.core.workflow import ReActAgentComp, ReActAgentCompConfig
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig

# 创建配置
config = ReActAgentCompConfig(
    model_client_config=ModelClientConfig(
        client_provider="OpenAI",
        api_key="your-api-key",
        api_base="https://api.openai.com/v1"
    ),
    model_config_obj=ModelRequestConfig(model_name="gpt-3.5-turbo"),
    max_iterations=10
)

# 创建组件
react_component = ReActAgentComp(config=config)

# 在工作流中使用
# ...
```

## 配置

该组件接受与 ReActAgent 相同的所有配置选项，以及工作流特定的选项。

## 执行模式

ReActAgentComp 支持所有四种工作流执行模式：

- **Invoke**：同步执行 ReAct 循环，批量输入/输出
- **Stream**：流式输出执行 ReAct 循环
- **Collect**：流式输入聚合为批量输出执行 ReAct 循环
- **Transform**：流式输入/输出执行 ReAct 循环

## 与工作流集成

该组件可以无缝集成到工作流图中：

```python
from openjiuwen.core.workflow import Workflow, Start, End

# 创建工作流
flow = Workflow()

# 创建组件
start_component = Start()
end_component = End({"responseTemplate": "{{output}}"})
react_component = ReActAgentComp(config=config)  # 您配置的组件

# 设置工作流连接
flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
flow.set_end_comp("e", end_component, inputs_schema={"output": "${react.output}"})
flow.add_workflow_comp("react", react_component, inputs_schema={"query": "${s.query}"})

# 添加连接：start -> react -> end
flow.add_connection("s", "react")
flow.add_connection("react", "e")

# 创建会话上下文并调用工作流
context = create_workflow_session()
result = await flow.invoke(inputs={"query": "What is the weather today?"}, session=context)
```
