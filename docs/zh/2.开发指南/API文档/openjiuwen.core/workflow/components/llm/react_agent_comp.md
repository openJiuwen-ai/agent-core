# ReAct Agent 工作流组件

ReAct Agent 工作流组件将 ReAct（Reasoning + Acting）代理的强大功能引入工作流系统。它允许您在工作流编排中融入复杂的推理和工具使用能力。

## 特性

- 在工作流上下文中实现完整的 ReAct 代理功能
- 支持所有工作流执行模式（invoke、stream、collect、transform）
- 工具执行能力
- 上下文管理和记忆持久化
- 可配置的迭代限制

## 使用方法

### 基础用法

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

### 带工具调用的用法

ReActAgentComp 支持工具调用功能，允许代理在执行过程中调用外部工具完成任务。

```python
from openjiuwen.core.workflow import Workflow, Start, End, create_workflow_session
from openjiuwen.core.workflow.components.llm.react import ReActAgentComp, ReActAgentCompConfig
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.runner import Runner

# 1. 创建工具
add_tool = LocalFunction(
    card=ToolCard(
        name="add",
        description="加法运算，计算两个数的和",
        input_params={
            "type": "object",
            "properties": {
                "a": {"description": "第一个加数", "type": "number"},
                "b": {"description": "第二个加数", "type": "number"},
            },
            "required": ["a", "b"],
        },
    ),
    func=lambda a, b: a + b,
)

# 2. 注册工具到 Runner.resource_mgr（必须在创建组件之前）
Runner.resource_mgr.add_tool(add_tool)

# 3. 创建工作流
flow = Workflow()

# 创建组件
start_component = Start()
end_component = End({"responseTemplate": "{{output}}"})

# 创建 ReActAgentComp 配置
config = ReActAgentCompConfig(
    model_client_config=ModelClientConfig(
        client_provider="OpenAI",
        api_key="your-api-key",
        api_base="https://api.openai.com/v1",
        verify_ssl=False,
    ),
    model_config_obj=ModelRequestConfig(model_name="gpt-3.5-turbo"),
    max_iterations=5,
)
react_component = ReActAgentComp(config=config)

# 4. 添加工具到 Agent 的能力列表（关键步骤）
# 通过 executable 的 ability_manager 公共属性添加工具
react_component.executable.ability_manager.add(add_tool.card)

# 5. 设置工作流连接
flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
flow.set_end_comp("e", end_component, inputs_schema={"output": "${react.output}"})
flow.add_workflow_comp("react", react_component, inputs_schema={"query": "${s.query}"})

# 添加连接：start -> react -> end
flow.add_connection("s", "react")
flow.add_connection("react", "e")

# 6. 创建会话上下文
context = create_workflow_session()

# 7. 调用工作流，请求使用工具进行计算
result = await flow.invoke(
    inputs={"query": "使用 add 工具计算 123 + 456"},
    session=context
)

# 8. 验证结果
print(f"计算结果：{result.result['response']}")
# 输出：计算结果：123 + 456 = 579
```

**关键点说明：**

1. **工具注册顺序**：必须先调用 `Runner.resource_mgr.add_tool()` 注册工具，然后才能创建组件
2. **添加能力**：通过 `react_component.executable._react_agent.ability_manager.add()` 将工具卡片添加到 Agent 的能力列表
3. **缓存机制**：`react_component.executable` 会缓存可执行实例，确保工具注册到正确的实例
4. **工具调用流程**：
   - LLM 接收请求并决定调用工具
   - 通过 `Runner.resource_mgr.get_tool()` 获取工具实例
   - 执行工具并获取结果
   - LLM 根据结果生成最终响应

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
