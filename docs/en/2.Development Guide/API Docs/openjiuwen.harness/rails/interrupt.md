# openjiuwen.harness.rails.interrupt

## class InterruptDecision

Base class for interrupt decisions, used to represent the decision result after interrupt handling. All decision types inherit from this class.

## class ApproveResult

Continue execution decision, indicates allowing the tool to continue execution.

**Parameters**:

* **new_args**(str, optional): Modified tool arguments. If provided, will replace the original tool arguments. Default: `None`.

## class RejectResult

Reject execution decision, indicates skipping tool execution.

**Parameters**:

* **tool_result**(object): Tool return result, will be used as the tool's return value. Default: `None`.
* **tool_message**(ToolMessage, optional): Tool message object. If not provided, will be automatically created based on `tool_result`. Default: `None`.

## class InterruptResult

Interrupt wait decision, indicates pausing execution and waiting for user input.

**Parameters**:

* **request**([InterruptRequest](../../openjiuwen.core/single_agent/interrupt.md#class-interruptrequest)): Interrupt request data structure, containing the data format definition required from user input.

## class BaseInterruptRail

Base class for interrupt handling Rail, providing the core framework for interruption and resumption. Developers can inherit from this class to implement custom interrupt logic. Subclasses must implement the `resolve_interrupt` method.

```python
class BaseInterruptRail(AgentRail)
```

**Parameters**:

* **tool_names**(Iterable[str], optional): List of tool names to intercept. Default: `None`, meaning no tools are intercepted.

### approve

```python
approve(new_args: Optional[str] = None) -> ApproveResult
```

Creates a continue execution decision.

**Parameters**:

* **new_args**(str, optional): Modified tool arguments. Default: `None`.

**Returns**:

**[ApproveResult](#class-approveresult)**, the continue execution decision object.

### reject

```python
reject(tool_result: object = None) -> RejectResult
```

Creates a reject execution decision.

**Parameters**:

* **tool_result**(object): Tool return result. Default: `None`.

**Returns**:

**[RejectResult](#class-rejectresult)**, the reject execution decision object.

### interrupt

```python
interrupt(request: InterruptRequest) -> InterruptResult
```

Creates an interrupt wait decision.

**Parameters**:

* **request**([InterruptRequest](../../openjiuwen.core/single_agent/interrupt.md#class-interruptrequest)): Interrupt request data structure.

**Returns**:

**[InterruptResult](#class-interruptresult)**, the interrupt wait decision object.

### add_tool

```python
add_tool(tool_name: str) -> None
```

Registers a tool name to intercept.

**Parameters**:

* **tool_name**(str): Tool name.

### add_tools

```python
add_tools(tool_names: Iterable[str]) -> None
```

Batch registers tool names to intercept.

**Parameters**:

* **tool_names**(Iterable[str]): List of tool names.

### get_tools

```python
get_tools() -> Set[str]
```

Gets all registered tool names.

**Returns**:

**Set[str]**, set of tool names.

### async resolve_interrupt

```python
async resolve_interrupt(
    ctx: AgentCallbackContext,
    tool_call: Optional[ToolCall],
    user_input: Optional[Any],
    auto_confirm_config: Optional[dict] = None
) -> InterruptDecision
```

Handles interrupt logic and returns a decision. Subclasses must implement this method.

**Parameters**:

* **ctx**(AgentCallbackContext): Agent callback context.
* **tool_call**(ToolCall, optional): Intercepted tool call object.
* **user_input**(Any, optional): User input, provided in resume scenarios, `None` on first call.
* **auto_confirm_config**(dict, optional): Auto-confirmation configuration, obtained from Session's state field.

**Returns**:

**[InterruptDecision](#class-interruptdecision)**, interrupt decision object.

**Example**:

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
    """Custom interrupt Rail example"""

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

        # First call: check auto-confirmation or create interrupt
        if user_input is None:
            if auto_confirm_config and auto_confirm_config.get(tool_name, False):
                return self.approve()

            return self.interrupt(InterruptRequest(
                message=f"Confirm execution of tool {tool_name}?",
                payload_schema={
                    "type": "object",
                    "properties": {
                        "approved": {"type": "boolean", "default": True},
                    }
                }
            ))

        # Resume call: parse user input
        if isinstance(user_input, dict) and user_input.get("approved", False):
            return self.approve()

        return self.reject(tool_result="User rejected execution")


async def main():
    # Create Agent and Session
    agent = ReActAgent(ReActAgentConfig())
    session_id = uuid.uuid4().hex
    session = await Runner.get_session(session_id)

    # Register custom Rail
    agent.register_rail(CustomInterruptRail(tool_names=["read", "delete"]))

    # Execute Agent, trigger interrupt
    result = await agent.invoke(
        {"query": "Read file", "conversation_id": session_id},
        session=session,
    )
    print(f"Result type: {result['result_type']}")
    print(f"Interrupt IDs: {result.get('interrupt_ids', [])}")

    # Resume execution
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

Data structure for user confirmation response.

**Parameters**:

* **approved**(bool): Whether to approve execution.
* **feedback**(str): Feedback information. Default: `""`.
* **auto_confirm**(bool): Whether to auto-confirm subsequent tool calls with the same name. Default: `False`.

## class ConfirmRequest

Confirmation request configuration.

**Parameters**:

* **message**(str): Prompt message to display to the user. Default: `"Please approve or reject?"`.
* **payload_schema**(dict): Data structure definition for user input. Default: JSON Schema of `ConfirmPayload`.

## class ConfirmInterruptRail

Confirmation-type interrupt Rail, used for user confirmation before sensitive tool execution. Inherits from [BaseInterruptRail](#class-baseinterruptrail).

```python
class ConfirmInterruptRail(BaseInterruptRail)
```

**Parameters**:

* **tool_names**(Iterable[str], optional): List of tool names requiring confirmation. Default: `None`.

**Example**:

```python
import asyncio
import uuid

from openjiuwen.harness.rails.interrupt import ConfirmInterruptRail
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import ReActAgent, ReActAgentConfig


async def main():
    # 1. Create and register Rail
    rail = ConfirmInterruptRail(tool_names=["read", "delete"])
    agent = ReActAgent(ReActAgentConfig())
    session_id = uuid.uuid4().hex
    session = await Runner.get_session(session_id)
    agent.register_rail(rail)

    # 2. First invoke: trigger interrupt
    result = await agent.invoke(
        {"query": "Call read tool to read /tmp/test.txt", "conversation_id": session_id},
        session=session,
    )
    print(f"Result type: {result['result_type']}")
    print(f"Interrupt IDs: {result.get('interrupt_ids', [])}")

    # 3. User confirmation
    tool_call_id = result['interrupt_ids'][0]
    interactive_input = InteractiveInput()
    interactive_input.update(tool_call_id, {
        "approved": True,
        "feedback": "Confirm execution",
        "auto_confirm": False
    })

    # 4. Resume execution
    result = await agent.invoke(
        {"query": interactive_input, "conversation_id": session_id},
        session=session,
    )
    print(f"Result type: {result['result_type']}")


if __name__ == "__main__":
    asyncio.run(main())
```

## class AskUserPayload

Data structure for user input response, used to pass user answers when resuming execution.

**Parameters**:

* **answers**(Dict[str, str]): A dictionary mapping question text to answers. The key is the full question text (`question` field value), and the value is the user's answer. Default: `{}`.

## class AskUserRequest

Ask-user request configuration, inherits from `InterruptRequest`, extends the `questions` field for multi-question mode.

**Parameters**:

* **questions**(List[dict]): List of questions to present to the user, each containing header, question, options, and other fields. Default: `[]`.

## class AskUserRail

User input collection Rail, used for the Agent to actively ask the user for information. Inherits from [BaseInterruptRail](#class-baseinterruptrail). AskUserRail automatically registers the `ask_user` tool to the Agent, developers do not need to register it manually.

```python
class AskUserRail(BaseInterruptRail)
```

**Parameters**:

* **tool_names**(Iterable[str], optional): List of tool names to intercept. Default: `["ask_user"]`.

### Multi-Question Mode

The `ask_user` tool supports multi-question mode, allowing 1-4 questions to be presented to the user at once, each with 2-4 optional choices. In the `InterruptRequest` returned from an interrupt:

* **`questions`**: Display data, containing the full question structure (header, question, options, multi_select, preview, etc.)
* **`payload_schema`**: User input format definition, describing the JSON Schema of `AskUserPayload` (the input format for answers)

When resuming execution, use the `answers` dictionary to provide answers, where the key is the question text (`question` field value) and the value is the user's selected answer.

**Example**:

```python
import asyncio
import uuid

from openjiuwen.harness.rails.interrupt import AskUserRail
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import ReActAgent, ReActAgentConfig


async def main():
    # 1. Create and register Rail
    rail = AskUserRail()
    agent = ReActAgent(ReActAgentConfig())
    session_id = uuid.uuid4().hex
    session = await Runner.get_session(session_id)
    agent.register_rail(rail)

    # 2. First invoke: Agent calls ask_user tool
    result = await agent.invoke(
        {"query": "Please ask the user for the filename they want", "conversation_id": session_id},
        session=session,
    )
    print(f"Result type: {result['result_type']}")
    print(f"Interrupt IDs: {result.get('interrupt_ids', [])}")

    # 3. User retrieves question text from questions field and provides answer
    tool_call_id = result['interrupt_ids'][0]
    state = result['state'][0]
    payload = state.payload.value
    questions = payload.questions  # Question list for displaying to user
    first_question = questions[0]['question']  # Get full question text

    interactive_input = InteractiveInput()
    interactive_input.update(tool_call_id, {"answers": {first_question: "user_answer.txt"}})

    # 4. Resume execution
    result = await agent.invoke(
        {"query": interactive_input, "conversation_id": session_id},
        session=session,
    )
    print(f"Result type: {result['result_type']}")


if __name__ == "__main__":
    asyncio.run(main())
```
