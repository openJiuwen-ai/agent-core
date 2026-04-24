# openjiuwen.harness.rails.interrupt

## class InterruptDecision

中断决策基类，用于表示中断处理后的决策结果。所有决策类型都继承自此类。

## class ApproveResult

继续执行决策，表示允许工具继续执行。

**参数**：

* **new_args**(str, 可选)：修改后的工具参数。如果提供，将使用新参数替换原工具参数。默认值：`None`。

## class RejectResult

拒绝执行决策，表示跳过工具执行。

**参数**：

* **tool_result**(object)：工具返回结果，将作为工具的返回值。默认值：`None`。
* **tool_message**(ToolMessage, 可选)：工具消息对象。如果未提供，将根据`tool_result`自动创建。默认值：`None`。

## class InterruptResult

中断等待决策，表示暂停执行并等待用户输入。

**参数**：

* **request**([InterruptRequest](../../openjiuwen.core/single_agent/interrupt.md#class-interruptrequest))：中断请求数据结构，包含需要用户输入的数据格式定义。

## class BaseInterruptRail

中断处理Rail基类，提供中断和恢复的核心框架。开发者可以继承此类实现自定义的中断逻辑。子类必须实现`resolve_interrupt`方法。

```python
class BaseInterruptRail(AgentRail)
```

**参数**：

* **tool_names**(Iterable[str], 可选)：需要拦截的工具名称列表。默认值：`None`，表示不拦截任何工具。

### approve

```python
approve(new_args: Optional[str] = None) -> ApproveResult
```

创建继续执行的决策。

**参数**：

* **new_args**(str, 可选)：修改后的工具参数。默认值：`None`。

**返回**：

**[ApproveResult](#class-approveresult)**，继续执行的决策对象。

### reject

```python
reject(tool_result: object = None) -> RejectResult
```

创建拒绝执行的决策。

**参数**：

* **tool_result**(object)：工具返回结果。默认值：`None`。

**返回**：

**[RejectResult](#class-rejectresult)**，拒绝执行的决策对象。

### interrupt

```python
interrupt(request: InterruptRequest) -> InterruptResult
```

创建中断等待的决策。

**参数**：

* **request**([InterruptRequest](../../openjiuwen.core/single_agent/interrupt.md#class-interruptrequest))：中断请求数据结构。

**返回**：

**[InterruptResult](#class-interruptresult)**，中断等待的决策对象。

### add_tool

```python
add_tool(tool_name: str) -> None
```

注册需要拦截的工具名称。

**参数**：

* **tool_name**(str)：工具名称。

### add_tools

```python
add_tools(tool_names: Iterable[str]) -> None
```

批量注册需要拦截的工具名称。

**参数**：

* **tool_names**(Iterable[str])：工具名称列表。

### get_tools

```python
get_tools() -> Set[str]
```

获取所有已注册的工具名称。

**返回**：

**Set[str]**，工具名称集合。

### async resolve_interrupt

```python
async resolve_interrupt(
    ctx: AgentCallbackContext,
    tool_call: Optional[ToolCall],
    user_input: Optional[Any],
    auto_confirm_config: Optional[dict] = None
) -> InterruptDecision
```

处理中断逻辑并返回决策。子类必须实现此方法。

**参数**：

* **ctx**(AgentCallbackContext)：Agent回调上下文。
* **tool_call**(ToolCall, 可选)：被拦截的工具调用对象。
* **user_input**(Any, 可选)：用户输入，恢复场景时提供，首次调用时为`None`。
* **auto_confirm_config**(dict, 可选)：自动确认配置，从Session的state字段中获取。

**返回**：

**[InterruptDecision](#class-interruptdecision)**，中断决策对象。

**样例**：

```python
import asyncio
import uuid
from typing import Any, Optional, Iterable

from openjiuwen.harness.rails.interrupt import BaseInterruptRail, InterruptRequest
from openjiuwen.core.single_agent.rail import AgentCallbackContext
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import ReActAgent, ReActAgentConfig


class CustomInterruptRail(BaseInterruptRail):
    """自定义中断Rail示例"""

    def __init__(self, tool_names: Optional[Iterable[str]] = None):
        super().__init__(tool_names=tool_names)

    async def resolve_interrupt(
        self,
        ctx: AgentCallbackContext,
        tool_call: Optional[ToolCall],
        user_input: Optional[Any],
        auto_confirm_config: Optional[dict] = None,
    ):
        tool_name = tool_call.name if tool_call else ""

        # 首次调用：检查自动确认或创建中断
        if user_input is None:
            if auto_confirm_config and auto_confirm_config.get(tool_name, False):
                return self.approve()

            return self.interrupt(InterruptRequest(
                message=f"确认执行工具 {tool_name}？",
                payload_schema={
                    "type": "object",
                    "properties": {
                        "approved": {"type": "boolean", "default": True},
                    }
                }
            ))

        # 恢复调用：解析用户输入
        if isinstance(user_input, dict) and user_input.get("approved", False):
            return self.approve()

        return self.reject(tool_result="用户拒绝执行")


async def main():
    # 创建Agent和Session
    agent = ReActAgent(ReActAgentConfig())
    session_id = uuid.uuid4().hex
    session = await Runner.get_session(session_id)

    # 注册自定义Rail
    agent.register_rail(CustomInterruptRail(tool_names=["read", "delete"]))

    # 执行Agent，触发中断
    result = await agent.invoke(
        {"query": "读取文件", "conversation_id": session_id},
        session=session,
    )
    print(f"Result type: {result['result_type']}")
    print(f"Interrupt IDs: {result.get('interrupt_ids', [])}")

    # 恢复执行
    if result['result_type'] == "interrupt":
        interactive_input = InteractiveInput()
        interactive_input.update(result['interrupt_ids'][0], {"approved": True})
        result = await agent.invoke(
            {"query": interactive_input, "conversation_id": session_id},
            session=session,
        )
        print(f"Result type: {result['result_type']}")


if __name__ == "__main__":
    asyncio.run(main())
```

## class ConfirmPayload

用户确认响应的数据结构。

**参数**：

* **approved**(bool)：是否批准执行。
* **feedback**(str)：反馈信息。默认值：`""`。
* **auto_confirm**(bool)：是否自动确认后续同名工具调用。默认值：`False`。

## class ConfirmRequest

确认请求配置。

**参数**：

* **message**(str)：向用户显示的提示消息。默认值：`"Please approve or reject?"`。
* **payload_schema**(dict)：用户输入的数据结构定义。默认值：`ConfirmPayload`的JSON Schema。

## class ConfirmInterruptRail

确认型中断Rail，用于敏感工具执行前的用户确认。继承自[BaseInterruptRail](#class-baseinterruptrail)。

```python
class ConfirmInterruptRail(BaseInterruptRail)
```

**参数**：

* **tool_names**(Iterable[str], 可选)：需要确认的工具名称列表。默认值：`None`。

**样例**：

```python
import asyncio
import uuid

from openjiuwen.harness.rails.interrupt import ConfirmInterruptRail
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import ReActAgent, ReActAgentConfig


async def main():
    # 1. 创建并注册Rail
    rail = ConfirmInterruptRail(tool_names=["read", "delete"])
    agent = ReActAgent(ReActAgentConfig())
    session_id = uuid.uuid4().hex
    session = await Runner.get_session(session_id)
    agent.register_rail(rail)

    # 2. 第一次invoke：触发中断
    result = await agent.invoke(
        {"query": "调用 read 工具读取 /tmp/test.txt", "conversation_id": session_id},
        session=session,
    )
    print(f"Result type: {result['result_type']}")
    print(f"Interrupt IDs: {result.get('interrupt_ids', [])}")

    # 3. 用户确认
    tool_call_id = result['interrupt_ids'][0]
    interactive_input = InteractiveInput()
    interactive_input.update(tool_call_id, {
        "approved": True,
        "feedback": "确认执行",
        "auto_confirm": False
    })

    # 4. 恢复执行
    result = await agent.invoke(
        {"query": interactive_input, "conversation_id": session_id},
        session=session,
    )
    print(f"Result type: {result['result_type']}")


if __name__ == "__main__":
    asyncio.run(main())
```

## class AskUserPayload

用户输入响应的数据结构，用于恢复执行时传递用户答案。

**参数**：

* **answers**(Dict[str, str])：问题文本到答案的映射字典，键为问题的完整文本（`question` 字段值），值为用户的答案。默认值：`{}`。

## class AskUserRequest

询问用户请求配置，继承自 `InterruptRequest`，扩展了 `questions` 字段用于多问题模式。

**参数**：

* **questions**(List[dict])：向用户展示的问题列表，每个问题包含 header、question、options 等字段。默认值：`[]`。

## class AskUserRail

用户输入收集型Rail，用于Agent主动向用户询问信息。继承自[BaseInterruptRail](#class-baseinterruptrail)。AskUserRail会自动注册`ask_user`工具到Agent，开发者无需手动注册。

```python
class AskUserRail(BaseInterruptRail)
```

**参数**：

* **tool_names**(Iterable[str], 可选)：需要拦截的工具名称列表。默认值：`["ask_user"]`。

### 多问题模式

`ask_user` 工具支持多问题模式，可一次性向用户提出1-4个问题，每个问题可带2-4个选项。中断返回的 `InterruptRequest` 中：

* **`questions`**：展示数据，包含完整的问题结构（header、question、options、multi_select、preview等）
* **`payload_schema`**：用户输入格式定义，描述 `AskUserPayload` 的 JSON Schema（answers 的输入格式）

用户恢复执行时，使用 `answers` 字典提供答案，键为问题文本（`question` 字段值），值为用户选择的答案。

**样例**：

```python
import asyncio
import uuid

from openjiuwen.harness.rails.interrupt import AskUserRail
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import ReActAgent, ReActAgentConfig


async def main():
    # 1. 创建并注册Rail
    rail = AskUserRail()
    agent = ReActAgent(ReActAgentConfig())
    session_id = uuid.uuid4().hex
    session = await Runner.get_session(session_id)
    agent.register_rail(rail)

    # 2. 第一次invoke：Agent调用ask_user工具
    result = await agent.invoke(
        {"query": "请向用户询问他们想要的文件名", "conversation_id": session_id},
        session=session,
    )
    print(f"Result type: {result['result_type']}")
    print(f"Interrupt IDs: {result.get('interrupt_ids', [])}")

    # 3. 用户通过questions字段获取问题文本，提供答案
    tool_call_id = result['interrupt_ids'][0]
    state = result['state'][0]
    payload = state.payload.value
    questions = payload.questions  # 问题列表，用于展示给用户
    first_question = questions[0]['question']  # 获取问题完整文本

    interactive_input = InteractiveInput()
    interactive_input.update(tool_call_id, {"answers": {first_question: "user_answer.txt"}})

    # 4. 恢复执行
    result = await agent.invoke(
        {"query": interactive_input, "conversation_id": session_id},
        session=session,
    )
    print(f"Result type: {result['result_type']}")


if __name__ == "__main__":
    asyncio.run(main())
```
