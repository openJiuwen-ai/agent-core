# execute_single_component

## 功能描述

执行单个组件并返回执行结果。

## 函数签名

```python
async def execute_single_component(
        component_id: str,
        session: Session,
        executor: ComponentComposable,
        inputs: dict,  # 输入数据
        inputs_schema: dict = None,  # 输入 schema
        outputs_schema: dict = None,  # 输出 schema
        context: ModelContext = None  # 上下文，可选参数
) -> Optional[Dict[str, Any]]
```

## 参数说明

| 参数名 | 类型 | 必需 | 描述 |
|-------|------|------|------|
| `component_id` | `str` | 是 | 节点 ID |
| `session` | `Session` | 是 | 会话对象 |
| `executor` | `ComponentComposable` | 是 | 组件可执行对象 |
| `inputs` | `dict` | 是 | 输入数据 |
| `inputs_schema` | `dict` | 否 | 输入 schema，用于从全局状态中获取输入数据 |
| `outputs_schema` | `dict` | 否 | 输出 schema，用于处理组件的输出数据 |
| `context` | `ModelContext` | 否 | 上下文对象，可选参数 |

## 返回值

| 类型 | 描述 |
|------|------|
| `Optional[Dict[str, Any]]` | 组件执行结果，如果没有结果则返回 `None` |

## 执行流程

1. 创建 `WorkflowSession`
2. 创建 `NodeSession`
3. 创建 `Vertex`
4. 初始化 `Vertex`
5. 直接设置 `_node_config` 属性，创建简单的配置对象
6. 提交输入数据到 `NodeSession` 的状态中
7. 创建 `PregelConfig`
8. 执行组件
9. 提交所有状态更新
10. 获取执行结果并返回

## 示例代码

### 基本用法

```python
import asyncio
from openjiuwen.core.workflow import WorkflowComponent, Input, Output
from openjiuwen.core.workflow import execute_single_component
from openjiuwen.core.session import Session
from openjiuwen.core.context_engine import ModelContext

class CustomComponent(WorkflowComponent):
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        # 处理输入数据
        result = {"processed_data": inputs.get("data", "") + " processed"}
        return result

async def run_single_component():
    # 创建组件实例
    component = CustomComponent()
    
    # 创建会话对象
    from openjiuwen.core.workflow import create_workflow_session
    session = create_workflow_session()
    
    # 准备输入数据
    inputs = {"data": "test"}
    
    # 执行单个组件
    result = await execute_single_component(
        component_id="custom_node",
        session=session,
        executor=component,
        inputs=inputs,
        inputs_schema={"data": "${data}"},
        outputs_schema={"result": "${processed_data}"}
    )
    
    print(f"执行结果: {result}")

if __name__ == "__main__":
    asyncio.run(run_single_component())
```

### 使用 LLMComponent

```python
import asyncio
from openjiuwen.core.workflow.components.llm.llm_comp import LLMComponent
from openjiuwen.core.workflow import execute_single_component
from openjiuwen.core.session import Session
from openjiuwen.core.context_engine import ModelContext

async def run_llm_component():
    # 创建 LLM 组件配置
    from openjiuwen.core.workflow.components.llm.llm_comp import LLMCompConfig
    llm_config = LLMCompConfig(
        model="gpt-3.5-turbo",
        system_prompt="你是一个智能助手",
        max_tokens=100
    )
    
    # 创建 LLM 组件实例
    llm_component = LLMComponent(llm_config)
    
    # 创建会话对象
    from openjiuwen.core.workflow import create_workflow_session
    session = create_workflow_session()
    
    # 准备输入数据
    inputs = {"prompt": "请介绍一下人工智能"}
    
    # 执行单个组件
    result = await execute_single_component(
        component_id="llm_node",
        session=session,
        executor=llm_component,
        inputs=inputs,
        inputs_schema={"prompt": "${prompt}"}
    )
    
    print(f"LLM 响应: {result}")

if __name__ == "__main__":
    asyncio.run(run_llm_component())
```

## 注意事项

1. 该函数会创建临时的 `WorkflowSession` 和 `NodeSession` 来执行组件，不会影响原始会话的状态。

2. 如果没有提供 `inputs_schema`，则直接使用传入的 `inputs` 作为组件的输入。

3. 如果没有提供 `outputs_schema`，则直接返回组件的原始输出。

4. 当 `context` 为 `None` 时，函数仍然可以正常执行。

5. 该函数适用于需要单独测试或执行某个组件的场景，不依赖于完整的工作流定义。