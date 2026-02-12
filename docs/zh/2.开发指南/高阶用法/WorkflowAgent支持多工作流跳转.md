多工作流跳转是openJiuwen框架中`WorkflowAgent`的核心能力，允许智能体在同一会话中管理多个工作流，支持工作流间的智能路由、并发中断和恢复。它解决了用户在同一对话中切换不同任务场景的需求，提供了灵活的多任务管理能力。

多工作流跳转的核心价值在于：

- 智能路由：根据用户意图自动选择合适的工作流
- 并发管理：支持多个工作流同时处于中断状态，互不干扰
- 无缝切换：用户可以在不同工作流间自由切换，系统自动处理状态管理
- 状态恢复：中断的工作流可以随时恢复，保持上下文连续性


# 多工作流跳转流程

`WorkflowAgent`通过`WorkflowController`实现工作流的执行和管理，能够根据用户查询自动选择合适的工作流，并支持工作流间的切换和恢复。

`WorkflowAgent`的开发流程分为以下两步：

- 创建`WorkflowAgent`：通过`WorkflowAgentConfig`创建配置，并动态添加多个工作流实例。
- 运行`WorkflowAgent`：通过`invoke`或`stream`方法执行查询，支持多工作流场景下的意图识别、跳转和恢复。

## 创建WorkflowAgent

用户可根据需求创建`WorkflowAgent`实例，并动态绑定多个工作流。

### 创建WorkflowAgent配置

首先使用`WorkflowAgentConfig`创建智能体配置。该配置支持多工作流场景，可以在创建时传入空的工作流列表，后续通过`add_workflows`方法动态添加，参考[构建WorkflowAgent](../智能体/构建WorkflowAgent.md)。

```python
import os

from openjiuwen.core.application.workflow_agent import WorkflowAgentConfig
from openjiuwen.core.foundation.llm import ModelConfig, BaseModelInfo

API_BASE = os.getenv("API_BASE", "your api base")
API_KEY = os.getenv("API_KEY", "your api key")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")

def create_model_config() -> ModelConfig:
    """根据环境变量构造模型配置。"""
    return ModelConfig(
        model_provider=MODEL_PROVIDER,
        model_info=BaseModelInfo(
            model=MODEL_NAME,
            api_base=API_BASE,
            api_key=API_KEY,
            temperature=0.7,
            top_p=0.9,
            timeout=120,
        ),
    )

# 创建最小化配置（workflows 为空列表）
config = WorkflowAgentConfig(
    id="test_multi_workflow_jump_agent",
    version="0.1.0",
    description="多工作流跳转恢复测试",
    model=_create_model_config(),
)
```

### 动态添加多个工作流

创建`WorkflowAgent`后，可以使用`add_workflows`方法动态添加多个工作流实例。该方法会自动从工作流实例中提取`schema`信息并更新配置。

#### 创建工作流实例

首先需要创建工作流实例。以下示例创建两个工作流：天气查询工作流和股票查询工作流。

```python
from openjiuwen.core.application.workflow_agent import WorkflowAgent

agent = WorkflowAgent(config)
```

#### 使用add_workflows动态添加

使用`add_workflows`方法将工作流实例添加到`WorkflowAgent`中：

```python
from openjiuwen.core.application.workflow_agent import WorkflowAgent
from openjiuwen.core.workflow import (
    End, QuestionerComponent, QuestionerConfig, FieldInfo, Start, Workflow,
    WorkflowCard
)

def _create_start_component():
    return Start()

def _build_questioner_workflow(workflow_id: str,
                               workflow_name: str,
                               workflow_description: str,
                               question_field: str,
                               question_desc: str) -> Workflow:
    """
    构建包含提问器的简单工作流。

    Args:
        workflow_id: 工作流ID
        workflow_name: 工作流名称
        question_field: 提问字段名
        question_desc: 提问字段描述

    Returns:
        Workflow: 包含 start -> questioner -> end 的工作流
    """
    workflow_card = WorkflowCard(
        id=workflow_id,
        name=workflow_name,
        version="1.0",
        description=workflow_description,
        input_params={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "用户输入"}},
            "required": ['query']
        }
    )
    flow = Workflow(card=workflow_card)

    # 创建组件
    start = _create_start_component()

    # 创建提问器
    key_fields = [
        FieldInfo(field_name=question_field, description=question_desc, required=True),
    ]
    model_config = ModelRequestConfig(
        model=MODEL_NAME,
        temperature=0.8,
        top_p=0.9
    )

    model_client_config = ModelClientConfig(
        client_provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        timeout=30,
        verify_ssl=False,
    )

    questioner_config = QuestionerConfig(
        model_client_config=model_client_config,
        model_config=model_config,
        question_content="",
        extract_fields_from_response=True,
        field_names=key_fields,
        with_chat_history=False,
        extra_prompt_for_fields_extraction="",
        example_content="",
    )
    questioner = QuestionerComponent(questioner_config)

    # End 组件，返回提问器收集的字段值
    end = End({"responseTemplate": f"{{{{{question_field}}}}}"})

    # 注册组件
    flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
    flow.add_workflow_comp("questioner", questioner, inputs_schema={"query": "${start.query}"})
    flow.set_end_comp("end", end, inputs_schema={question_field: f"${{questioner.{question_field}}}"})

    # 连接拓扑
    flow.add_connection("start", "questioner")
    flow.add_connection("questioner", "end")

    return flow


weather_workflow = _build_questioner_workflow(
    workflow_id="weather_flow",
    workflow_name="天气查询",
    workflow_description="查询某地的天气情况、温度、气象信息",
    question_field="location",
    question_desc="地点"
)
stock_workflow = _build_questioner_workflow(
    workflow_id="stock_flow",
    workflow_name="股票查询",
    workflow_description="查询股票价格、股市行情、股票走势等金融信息",
    question_field="stock_code",
    question_desc="股票代码"
)

# 创建最小化配置（workflows 为空列表）
config = WorkflowAgentConfig(
    id="test_multi_workflow_jump_agent",
    version="0.1.0",
    description="多工作流跳转恢复测试",
    model=_create_model_config(),
)
agent = WorkflowAgent(config)

# 使用 add_workflows 动态添加（自动提取 schema）
agent.add_workflows([weather_workflow, stock_workflow])
```

## 运行WorkflowAgent

`WorkflowAgent`支持多工作流场景下的跳转和恢复功能。以下示例演示了完整的多工作流跳转和恢复流程：

场景描述

1. 用户发起天气查询请求，但未提供地点信息，工作流中断等待输入
2. 用户转而查询股票信息，但未提供股票代码，另一个工作流也中断
3. 用户提供地点信息，恢复天气查询工作流并完成
4. 用户提供股票代码，恢复股票查询工作流并完成

### 步骤1：用户查询"查询天气"

- 意图识别：识别为天气查询工作流
- 执行工作流：工作流执行到提问器组件
- 中断：提问器询问地点

```python
import asyncio

async def main():
    conversation_id = "test-jump-recovery-001"
    result1 = await agent.invoke({
        "query": "查询天气",
        "conversation_id": conversation_id
    })
    print(result1)

if __name__ == "__main__":
    asyncio.run(main())
```

预期输出

```python
[
    OutputSchema(
        type='__interaction__',
        index=0,
        payload=InteractionOutput(
            id="questioner",
            value="请您提供地点相关的信息"
        )
    )
]
```

### 步骤2：用户查询"查看股票"

- 意图识别：识别为股票查询工作流（不同的工作流）
- 检查中断：股票工作流无中断任务
- 执行工作流：执行股票查询工作流
- 中断：提问器询问股票代码

```python
import asyncio

async def main():
    conversation_id = "test-jump-recovery-001"
    result2 = await agent.invoke({
        "query": "查看股票",
        "conversation_id": conversation_id
    })
    print(result2)

if __name__ == "__main__":
    asyncio.run(main())
```

预期输出

```python
[
    OutputSchema(
        type='__interaction__',
        index=0,
        payload=InteractionOutput(
            id="questioner",
            value="请您提供股票代码相关的信息"
        )
    )
]
```

### 步骤3：用户查询"查询北京天气"

- 意图识别：识别为天气查询工作流
- 检查中断：发现天气工作流有中断任务
- 恢复任务：创建InteractiveInput，恢复天气工作流
- 完成：工作流从提问器继续执行，返回结果

```python
import asyncio

async def main():
    conversation_id = "test-jump-recovery-001"
    result3 = await agent.invoke({
        "query": "查询北京天气",
        "conversation_id": conversation_id
    })
    print(result3)

if __name__ == "__main__":
    asyncio.run(main())
```

预期输出（完成）

```python
{
    "output": WorkflowOutput(
        state=WorkflowExecutionState.COMPLETED,
        result={
            "responseContent": "北京",  # 返回用户输入的地点
            "output": {}
        }
    ),
    "result_type": "answer"
}
```

### 步骤4：用户查询"查看AAPL股票"

- 意图识别：识别为股票查询工作流
- 检查中断：发现股票工作流有中断任务
- 恢复任务：创建InteractiveInput，恢复股票工作流
- 完成：工作流从提问器继续执行，返回结果

```python
import asyncio

async def main():
    conversation_id = "test-jump-recovery-001"
    result4 = await agent.invoke({
        "query": "查看AAPL股票",
        "conversation_id": conversation_id
    })
    print(result4)

if __name__ == "__main__":
    asyncio.run(main())
```

预期输出（完成）

```python
{
    "output": WorkflowOutput(
        state=WorkflowExecutionState.COMPLETED,
        result={
            "responseContent": "AAPL",  # 返回用户输入的股票代码
            "output": {}
        }
    ),
    "result_type": "answer"
}
```

# 完整示例代码
```python
import asyncio
import os

from openjiuwen.core.application.workflow_agent import WorkflowAgent
from openjiuwen.core.foundation.llm import ModelConfig, BaseModelInfo, ModelRequestConfig, ModelClientConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.workflow import (
    End, QuestionerComponent, QuestionerConfig, FieldInfo, Start, Workflow,
    WorkflowCard
)
from openjiuwen.core.single_agent.legacy import WorkflowAgentConfig


API_BASE = os.getenv("API_BASE", "your api url")
API_KEY = os.getenv("API_KEY", "your model token")
MODEL_NAME = os.getenv("MODEL_NAME", "your model name")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "your model provider")
os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")

SYSTEM_PROMPT_TEMPLATE = "你是一个query改写的AI助手。今天的日期是{}。"


def create_model_config() -> ModelConfig:
    """根据环境变量构造模型配置。"""
    return ModelConfig(
        model_provider=MODEL_PROVIDER,
        model_info=BaseModelInfo(
            model=MODEL_NAME,
            api_base=API_BASE,
            api_key=API_KEY,
            temperature=0.7,
            top_p=0.9,
            timeout=120,
        ),
    )

def _create_start_component():
    return Start()

def _build_questioner_workflow(workflow_id: str,
                               workflow_name: str,
                               workflow_description: str,
                               question_field: str,
                               question_desc: str) -> Workflow:
    """
    构建包含提问器的简单工作流。

    Args:
        workflow_id: 工作流ID
        workflow_name: 工作流名称
        question_field: 提问字段名
        question_desc: 提问字段描述

    Returns:
        Workflow: 包含 start -> questioner -> end 的工作流
    """
    workflow_card = WorkflowCard(
        id=workflow_id,
        name=workflow_name,
        version="1.0",
        description=workflow_description,
        input_params={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "用户输入"}},
            "required": ['query']
        }
    )
    flow = Workflow(card=workflow_card)

    # 创建组件
    start = _create_start_component()

    # 创建提问器
    key_fields = [
        FieldInfo(field_name=question_field, description=question_desc, required=True),
    ]
    model_config = ModelRequestConfig(
        model=MODEL_NAME,
        temperature=0.8,
        top_p=0.9
    )

    model_client_config = ModelClientConfig(
        client_provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        timeout=30,
        verify_ssl=False,
    )

    questioner_config = QuestionerConfig(
        model_client_config=model_client_config,
        model_config=model_config,
        question_content="",
        extract_fields_from_response=True,
        field_names=key_fields,
        with_chat_history=False,
        extra_prompt_for_fields_extraction="",
        example_content="",
    )
    questioner = QuestionerComponent(questioner_config)

    # End 组件，返回提问器收集的字段值
    end = End({"responseTemplate": f"{{{{{question_field}}}}}"})

    # 注册组件
    flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
    flow.add_workflow_comp("questioner", questioner, inputs_schema={"query": "${start.query}"})
    flow.set_end_comp("end", end, inputs_schema={question_field: f"${{questioner.{question_field}}}"})

    # 连接拓扑
    flow.add_connection("start", "questioner")
    flow.add_connection("questioner", "end")

    return flow


async def main():
    """
    测试多工作流间的跳转和恢复功能。

    场景：
    1. query1 -> workflow1（天气查询）-> 提问器中断（询问地点）
    2. query2 -> 意图识别 -> workflow2（股票查询）-> 提问器中断（询问股票代码）
    3. query3（InteractiveInput）-> 恢复 workflow1，提供地点信息 -> 完成
    4. query4（InteractiveInput）-> 恢复 workflow2，提供股票代码 -> 完成
    """

    # 创建两个带提问器的工作流
    weather_workflow = _build_questioner_workflow(
        workflow_id="weather_flow",
        workflow_name="天气查询",
        workflow_description="查询某地的天气情况、温度、气象信息",
        question_field="location",
        question_desc="地点"
    )
    stock_workflow = _build_questioner_workflow(
        workflow_id="stock_flow",
        workflow_name="股票查询",
        workflow_description="查询股票价格、股市行情、股票走势等金融信息",
        question_field="stock_code",
        question_desc="股票代码"
    )

    # 创建最小化配置（workflows 为空列表）
    config = WorkflowAgentConfig(
        id="test_multi_workflow_jump_agent",
        version="0.1.0",
        description="多工作流跳转恢复测试",
        model=create_model_config(),
    )
    agent = WorkflowAgent(config)

    # 使用 add_workflows 动态添加（自动提取 schema）
    agent.add_workflows([weather_workflow, stock_workflow])

    conversation_id = "test-jump-recovery-001"

    # ========== 步骤1: query1 -> workflow1 -> 中断 ==========
    print("\n【步骤1】发送 query1: 查询天气")
    result1 = await Runner.run_agent(agent=agent, inputs={"query": "查询天气", "conversation_id": conversation_id})
    print("步骤1 结果: ", result1)

    # ========== 步骤2: query2 -> workflow2 -> 中断 ==========
    print("\n【步骤2】发送 query2: 查看股票")
    result2 = await Runner.run_agent(agent=agent, inputs={"query": "查看股票", "conversation_id": conversation_id})
    print(f"步骤2 结果: {result2}")

    # ========== 步骤3: query3 -> 恢复 workflow1 ==========
    print("\n【步骤3】发送 query3: 提供地点信息，恢复 workflow1")
    result3 = await Runner.run_agent(agent=agent, inputs={"query": "查询北京天气", "conversation_id": conversation_id})
    print(f"步骤3 结果: {result3}")

    # ========== 步骤4: query4 -> 恢复 workflow2 ==========
    print("\n【步骤4】发送 query4: 提供股票代码，恢复 workflow2")
    result4 = await Runner.run_agent(agent=agent, inputs={"query": "查看AAPL股票", "conversation_id": conversation_id})
    print(f"步骤4 结果: {result4}")


if __name__ == "__main__":
    asyncio.run(main())
```