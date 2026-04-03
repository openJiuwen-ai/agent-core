# Human-in-the-Loop

Human-in-the-Loop (HITL) is a human-machine collaboration mechanism in the openJiuwen framework that allows Agents to pause at critical nodes during tool execution and return decision-making power to users. This mechanism is implemented based on the framework's Rail interceptor, injecting "interrupt → wait for user response → resume execution" interaction capabilities into tool calls without modifying the Agent's core execution flow.

Typical application scenarios include:

- **Sensitive Operation Confirmation**: Pause and request user approval or rejection before executing high-risk tools such as file deletion or fund transfers.
- **Information Completion**: When an Agent discovers missing necessary information during execution, it actively asks the user and waits for a response.
- **Execution Process Auditing**: Establish manual approval nodes for critical operations to ensure the Agent's autonomous behavior remains under human supervision.

***

## 1. Built-in Rails

The HITL mechanism is implemented based on Rail interceptors. The framework provides two built-in Rails that can be registered to Agents through `register_rail`.

### 1.1 ConfirmInterruptRail —— Operation Confirmation

Intercepts calls to specified tools, pauses execution, and requests user approval or rejection.

**Configuration**:

Specify the tools to intercept through the `tool_names` parameter. When the Agent calls these tools, they will be paused:

```python
from openjiuwen.harness.rails import ConfirmInterruptRail

rail = ConfirmInterruptRail(tool_names=["read", "write", "delete"])
await agent.register_rail(rail)
```

**Response Fields**:

Users provide decisions through `InteractiveInput`, containing the following fields:

- `approved`: Whether to approve execution (required, True means approve, False means reject)
- `feedback`: User feedback information (optional)
- `auto_confirm`: Whether to automatically confirm subsequent tool calls with the same name (optional, default False)

**Approve Execution**:

```python
interactive_input = InteractiveInput()
interactive_input.update(tool_call_id, {
    "approved": True,
    "feedback": "Confirm execution"
})
```

**Reject Execution**:

After rejection, the tool will not execute, and the feedback information will be returned to the Agent as a `ToolMessage`. The Agent can adjust subsequent behavior based on the feedback:

```python
interactive_input = InteractiveInput()
interactive_input.update(tool_call_id, {
    "approved": False,
    "feedback": "This operation is too dangerous, reject execution"
})
```

For a complete example, see [5.1 Tool Execution Confirmation](#51-tool-execution-confirmation).

### 1.2 AskUserRail —— User Input Collection

Allows the Agent to actively ask users for information, suitable for information completion scenarios.

**Configuration**:

After registration, the Agent can collect user input through the built-in `ask_user` tool:

```python
from openjiuwen.harness.rails import AskUserRail

rail = AskUserRail()
await agent.register_rail(rail)
```

**Response Fields**:

Users provide answers through `InteractiveInput`:

- `answer`: The answer provided by the user (required)

**Return Answer**:

```python
interactive_input = InteractiveInput()
interactive_input.update(tool_call_id, {"answer": "user_input.txt"})
```

For a complete example, see [5.2 User Input Collection](#52-user-input-collection).

***

## 2. Handling Interruptions

Regardless of which Rail is used, the process for handling interruptions is the same: detect interruption, parse information, construct response, resume execution.

### 2.1 Detect Interruption

When a tool is intercepted by a Rail, the `result_type` in the result returned by `Runner.run_agent` will be marked as `"interrupt"`:

```python
if result.get("result_type") == "interrupt":
    interrupt_ids = result.get("interrupt_ids", [])
```

### 2.2 Parse Interrupt Information

The complete structure of the interrupt result is as follows:
```python
{
    "result_type": "interrupt",
    "interrupt_ids": ["call_dd26eaa14529440c81b54eab"],
    "state": [
        {
            "type": "__interaction__",
            "index": 0,
            "payload": {
                "id": "call_dd26eaa14529440c81b54eab",
                "value": {
                    "message": "Please approve or reject?",
                    "payload_schema": {
                        "description": "Payload for user confirmation response.",
                        "properties": {
                            "approved": {"title": "Approved", "type": "boolean"},
                            "feedback": {"default": "", "title": "Feedback", "type": "string"},
                            "auto_confirm": {"default": False, "title": "Auto Confirm", "type": "boolean"}
                        },
                        "required": ["approved"],
                        "title": "ConfirmPayload",
                        "type": "object"
                    },
                    "auto_confirm_key": "read",
                    "tool_name": "read",
                    "tool_call_id": "call_dd26eaa14529440c81b54eab",
                    "tool_args": '{"filepath": "/tmp/test1.txt"}',
                    "index": 0
                }
            }
        }
    ]
}
```

**Field Descriptions**:
- `result_type`: Result type, `"interrupt"` indicates the tool was intercepted
- `interrupt_ids`: List of intercepted tool call IDs
- `state`: List of interrupt information, each element contains detailed information about one interruption

**Payload Structure**:
Each interrupt's `payload` contains the following fields:
- `id`: Tool call ID, used to identify the specific interruption when resuming execution
- `value`: Detailed information about the interruption, containing the following fields:
  - `message`: Prompt message
  - `payload_schema`: JSON Schema defining the user response format
  - `auto_confirm_key`: Key for auto-confirmation, used for the auto_confirm feature
  - `tool_name`: Tool name
  - `tool_call_id`: Tool call ID
  - `tool_args`: Tool arguments
  - `index`: Tool call index

Get detailed interrupt information from the `state` field:

```python
state_list = result.get("state", [])
for state in state_list:
    payload = state.payload
    tool_call_id = payload.id
    interrupt_value = payload.value
    
    tool_name = interrupt_value.tool_name
    tool_args = interrupt_value.tool_args
    message = interrupt_value.message
    payload_schema = interrupt_value.payload_schema
```

### 2.3 Construct Response and Resume Execution

Users pass decisions through `InteractiveInput` and call `Runner.run_agent` again to resume execution. The response format should be filled according to `payload_schema`:

```python
interactive_input = InteractiveInput()
interactive_input.update(tool_call_id, {
    "approved": True,
    "feedback": "Confirm execution",
    "auto_confirm": False
})

result = await Runner.run_agent(
    agent=agent,
    inputs={"query": interactive_input, "conversation_id": "session_1"},
)
```

Different Rails may have different `payload_schema`, users need to fill in `InteractiveInput` according to the actual schema.

### 2.4 Session Management

Auto-confirmation configuration is stored in the Session. If the Session is configured with persistence, the auto-confirmation configuration will also be persisted.

```python
await agent.clear_session(session_id)
```

After clearing the Session, the auto-confirmation configuration will be cleared, and subsequent calls will trigger interruptions again.

***

## 3. Advanced Scenarios

### 3.1 Auto Confirmation

When confirming, set `auto_confirm=True`, and subsequent tool calls with the same name will automatically pass without requiring confirmation again:

```python
interactive_input = InteractiveInput()
interactive_input.update(tool_call_id, {
    "approved": True,
    "feedback": "Confirmed, auto-pass subsequent calls",
    "auto_confirm": True
})

result2 = await Runner.run_agent(
    agent=agent,
    inputs={"query": interactive_input, "conversation_id": "session_1"},
)

result3 = await Runner.run_agent(
    agent=agent,
    inputs={"query": "Read /tmp/other.txt again", "conversation_id": "session_1"},
)

print(f"Result type: {result3.get('result_type')}")
```

### 3.2 Concurrent Tool Interruptions

When multiple tools are called concurrently, all interruptions will be returned in batch, and users need to provide decisions for each interruption:

```python
result1 = await Runner.run_agent(
    agent=agent,
    inputs={"query": "Read both a.txt and b.txt", "conversation_id": "session_1"},
)

interrupt_ids = result1.get("interrupt_ids", [])

# Both tools need confirmation
assert len(interrupt_ids) == 2

# Provide decisions in order
interactive_input = InteractiveInput()
interactive_input.update(interrupt_ids[0], {"approved": True, "feedback": "Confirm"})
interactive_input.update(interrupt_ids[1], {"approved": True, "feedback": "Confirm"})

result2 = await Runner.run_agent(
    agent=agent,
    inputs={"query": interactive_input, "conversation_id": "session_1"},
)
```

Each tool call has a unique `tool_call_id`, which can be obtained through `interrupt_ids`. At the same time, the `state` field contains detailed information for each tool call, which can be used to distinguish different tool calls.

### 3.3 Interruptions in Streaming Output

In streaming output scenarios, interruptions are detected in the streaming output, and streaming output needs to be called again to resume execution:

```python
# First streaming call: detect interruption
outputs1 = []
interrupt_detected = False
tool_call_id = None

async for output in Runner.run_agent_streaming(
    agent=agent,
    inputs={"query": "Read /tmp/test.txt", "conversation_id": "session_1"},
):
    outputs1.append(output)
    if output.type == '__interaction__':
        interrupt_detected = True
        tool_call_id = output.payload.id

# Construct user response
interactive_input = InteractiveInput()
interactive_input.update(tool_call_id, {
    "approved": True,
    "feedback": "Confirm execution",
    "auto_confirm": True
})

# Second streaming call: resume execution
outputs2 = []
async for output in Runner.run_agent_streaming(
    agent=agent,
    inputs={"query": interactive_input, "conversation_id": "session_1"},
):
    outputs2.append(output)
```

### 3.4 Sub-Agent Nested Interruption

When using the AgentAsTool mode, interruptions within sub-agents automatically propagate up to the parent Agent, and users only need to confirm at the outermost layer. When resuming, confirmation information is automatically passed to the sub-agent.

**Interrupt Propagation Flow**:

```
main_agent
  └─ calls sub_agent
        └─ calls read tool → [Rail intercepts] → interruption propagates up
              ↓
        Returns interrupt result to main_agent
              ↓
        User confirms (only needs to operate at outermost layer)
              ↓
        Confirmation information passes down to sub_agent
              ↓
        Resume execution
```

**Auto Confirm Propagation**:

Auto-confirmation configuration is uniformly stored in the main Agent's Session and automatically passed to sub-agents. When a sub-agent calls a tool, it reads the auto_confirm configuration from the parent Agent's Session.

Sub-agent interruptions are transparent to users, who only need to confirm at the outermost Agent. The auto_confirm configuration automatically propagates to all sub-agents.

For a complete example, see [5.3 Sub-Agent Nested Interruption](#53-sub-agent-nested-interruption).

***

## 4. Custom Interrupt Rail

Developers can implement custom interrupt logic by inheriting from `BaseInterruptRail`.

### 4.1 Three Decision Types

Custom Rails need to implement the `resolve_interrupt` method, returning one of three decisions:

| Decision Type    | Description        | Use Case                        |
| ---------------   | ------------------ | ------------------------------- |
| ApproveResult    | Approve execution  | User confirms execution, or needs to modify parameters |
| RejectResult     | Reject execution   | User rejects execution, or validation fails |
| InterruptResult  | Interrupt wait     | First interception, waiting for user input |

### 4.2 Extension Example

The following example demonstrates two main extension points: custom decision engine and fine-grained auto-confirmation.

```python
from typing import Any, Optional
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails import ConfirmInterruptRail, ConfirmPayload
from openjiuwen.harness.rails.interrupt.decision import InterruptDecision

class BashPermissionRail(ConfirmInterruptRail):
    """Bash Permission Control Rail - Demonstrates custom decision engine and fine-grained auto-confirmation
    
    Features:
    1. Custom decision engine: Check if command contains dangerous patterns (like rm *)
    2. Fine-grained auto-confirmation: Generate different auto_confirm keys based on command content
    """
    
    def __init__(self, tool_names):
        super().__init__(tool_names=tool_names)
    
    async def resolve_interrupt(
        self,
        ctx: AgentCallbackContext,
        tool_call: Optional[ToolCall],
        user_input: Optional[Any],
        auto_confirm_config: Optional[dict] = None,
    ) -> InterruptDecision:
        """Custom decision engine: Control three decision logics"""
        tool_name = tool_call.name if tool_call else ""
        command = self._get_command(tool_call)
        auto_confirm_key = self._get_auto_confirm_key(command)
        
        # Check if already auto-confirmed (check on first call)
        if user_input is None:
            if self._is_auto_confirmed(auto_confirm_config, auto_confirm_key):
                return self.approve()
            
            # Check if contains dangerous patterns
            if "rm *" in command or "rm -rf /" in command:
                return self.reject(tool_result="Command contains dangerous operations, rejected")
            
            return self.interrupt(InterruptRequest(
                message=f"Bash command requires authorization: {command}",
                payload_schema=ConfirmPayload.to_schema(),
                auto_confirm_key=auto_confirm_key,
            ))
        
        # Process after user response
        if user_input.get("approved"):
            return self.approve()
        else:
            return self.reject(tool_result=user_input.get("feedback", "User rejected"))
    
    def _get_auto_confirm_key(self, command: str) -> str:
        """Fine-grained auto-confirmation: Generate key based on command content"""
        # Simple example: Generate key based on first word of command
        # ls /tmp -> bash_ls
        # cat file.txt -> bash_cat
        cmd_parts = command.strip().split()
        if cmd_parts:
            return f"bash_{cmd_parts[0]}"
        return "bash"
    
    def _get_command(self, tool_call):
        """Get command content"""
        if tool_call.name == "bash":
            args = json.loads(tool_call.arguments) if isinstance(tool_call.arguments, str) else tool_call.arguments
            return args.get("command", "")
        return ""
```

**Usage Example**:

```python
rail = BashPermissionRail(tool_names=["bash"])
await agent.register_rail(rail)

# Scenario 1: Custom decision engine
# Execute "rm *" -> Directly rejected (contains dangerous pattern)

# Scenario 2: Fine-grained auto-confirmation
# Execute "ls /tmp" -> Interrupt -> Confirm (auto_confirm=True, key: bash_ls)
# Execute "ls /home" again -> Auto-pass (key: bash_ls matches)
# Execute "cat file.txt" -> Interrupt (key: bash_cat doesn't match)
```

***

## 5. Complete Examples

The following examples use the `@tool` decorator to define tools and demonstrate the complete HITL usage flow.

### 5.1 Tool Execution Confirmation

The following example demonstrates the tool execution confirmation flow: requesting user confirmation when reading a file.

```python
import asyncio
import json
from typing import Annotated

from pydantic import Field

from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.rails import ConfirmInterruptRail
from openjiuwen.core.foundation.tool import tool


@tool
async def read_file(
    file_path: Annotated[str, Field(description="The file path to read")]
) -> str:
    """Read file content"""
    return "Hello"


async def main():
    await Runner.start()

    try:
        agent = ReActAgent(card=AgentCard(id="confirm_agent"))
        config = ReActAgentConfig()
        config.configure_model_client(
            provider="openai",
            api_key="your-api-key",
            api_base="https://api.openai.com/v1",
            model_name="gpt-4",
        )
        agent.configure(config)

        Runner.resource_mgr.add_tool(read_file)
        agent.ability_manager.add(read_file.card)

        rail = ConfirmInterruptRail(tool_names=["read_file"])
        await agent.register_rail(rail)

        print("=== First call: Trigger interrupt ===")
        result1 = await Runner.run_agent(
            agent=agent,
            inputs={"query": "Please read the /tmp/test.txt file", "conversation_id": "session_1"},
        )

        print(f"Result type: {result1.get('result_type')}")

        if result1.get("result_type") == "interrupt":
            interrupt_ids = result1.get("interrupt_ids", [])
            print(f"Interrupt count: {len(interrupt_ids)}")

            state_list = result1.get("state", [])
            for state in state_list:
                payload = state.payload
                tool_call_id = payload.id
                interrupt_value = payload.value

                print(f"\nInterrupt details:")
                print(f"  Tool name: {interrupt_value.tool_name}")
                print(f"  Tool arguments: {interrupt_value.tool_args}")
                print(f"  Prompt message: {interrupt_value.message}")
                print(f"  Auto-confirm key: {interrupt_value.auto_confirm_key}")

                interactive_input = InteractiveInput()
                interactive_input.update(tool_call_id, {
                    "approved": True,
                    "feedback": "Confirm read"
                })

                print("\n=== Second call: Resume execution ===")
                result2 = await Runner.run_agent(
                    agent=agent,
                    inputs={"query": interactive_input, "conversation_id": "session_1"},
                )

                print(f"Result type: {result2.get('result_type')}")

    finally:
        await Runner.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

**Output Example**:

```
=== First call: Trigger interrupt ===
Result type: interrupt
Interrupt count: 1

Interrupt details:
  Tool name: read_file
  Tool arguments: {"file_path": "/tmp/test.txt"}
  Prompt message: Please approve or reject?
  Auto-confirm key: read_file

=== Second call: Resume execution ===
Result type: answer
```

### 5.2 User Input Collection

The following example demonstrates the Agent actively asking the user for information:

```python
import asyncio
import json

from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.rails import AskUserRail


async def main():
    await Runner.start()

    try:
        agent = ReActAgent(card=AgentCard(id="ask_user_agent"))
        config = ReActAgentConfig()
        config.configure_model_client(
            provider="openai",
            api_key="your-api-key",
            api_base="https://api.openai.com/v1",
            model_name="gpt-4",
        )
        config.configure_prompt_template([
            {"role": "system", "content": ""}
        ])
        agent.configure(config)

        rail = AskUserRail()
        await agent.register_rail(rail)

        print("=== First call: Trigger interrupt ===")
        result1 = await Runner.run_agent(
            agent=agent,
            inputs={"query": "Please ask the user for the filename they want. use ask_user tool",
                    "conversation_id": "session_1"},
        )

        print(f"Result type: {result1.get('result_type')}")

        if result1.get("result_type") == "interrupt":
            interrupt_ids = result1.get("interrupt_ids", [])
            print(f"Interrupt count: {len(interrupt_ids)}")

            state_list = result1.get("state", [])
            for state in state_list:
                payload = state.payload
                tool_call_id = payload.id
                interrupt_value = payload.value

                print(f"\nInterrupt details:")
                print(f"  Prompt message: {interrupt_value.message}")
                print(f"  payload_schema: {json.dumps(interrupt_value.payload_schema, indent=4, ensure_ascii=False)}")

                print("\nUser answer: my_document.txt")
                interactive_input = InteractiveInput()
                interactive_input.update(tool_call_id, {"answer": "my_document.txt"})

                print("\n=== Second call: Resume execution ===")
                result2 = await Runner.run_agent(
                    agent=agent,
                    inputs={"query": interactive_input, "conversation_id": "session_1"},
                )

                print(f"Result type: {result2.get('result_type')}")
    finally:
        await Runner.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

**Output Example**:

```
=== First call: Trigger interrupt ===
Result type: interrupt
Interrupt count: 1

Interrupt details:
  Prompt message: Please input
  payload_schema: {
    "description": "Payload for user input response.",
    "properties": {
        "answer": {
            "default": "",
            "description": "answer",
            "title": "Answer",
            "type": "string"
        }
    },
    "title": "AskUserPayload",
    "type": "object"
}

User answer: my_document.txt

=== Second call: Resume execution ===
Result type: answer
```

### 5.3 Sub-Agent Nested Interruption

The following example demonstrates interrupt propagation and auto-confirmation in nested sub-agent scenarios:

```python
import asyncio
from typing import Annotated

from pydantic import Field

from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.rails import ConfirmInterruptRail
from openjiuwen.core.foundation.tool import tool


@tool
async def read_file(
    file_path: Annotated[str, Field(description="The file path to read")]
) -> str:
    """Read file content"""
    return "Hello"


async def main():
    await Runner.start()

    try:
        Runner.resource_mgr.add_tool(read_file)

        print("=== Create sub-agent ===")
        sub_agent_card = AgentCard(
            id="sub_agent",
            name="sub_agent",
            description="A sub-agent that can read files",
            input_params={
                "type": "object",
                "properties": {
                    "query": {"description": "Task description", "type": "string"},
                },
                "required": ["query"],
            },
        )
        sub_agent = ReActAgent(card=sub_agent_card)
        sub_config = ReActAgentConfig()
        sub_config.configure_model_client(
            provider="openai",
            api_key="your-api-key",
            api_base="https://api.openai.com/v1",
            model_name="gpt-4",
        )
        sub_agent.configure(sub_config)

        sub_agent.ability_manager.add(read_file.card)

        sub_rail = ConfirmInterruptRail(tool_names=["read_file"])
        await sub_agent.register_rail(sub_rail)
        print("Sub-agent has registered read_file interrupt Rail")

        print("\n=== Create main agent ===")
        main_agent = ReActAgent(card=AgentCard(id="main_agent"))
        main_config = ReActAgentConfig()
        main_config.configure_model_client(
            provider="openai",
            api_key="your-api-key",
            api_base="https://api.openai.com/v1",
            model_name="gpt-4",
        )
        main_agent.configure(main_config)

        Runner.resource_mgr.add_agent(sub_agent.card, agent=lambda: sub_agent)
        main_agent.ability_manager.add(sub_agent.card)
        print("Main agent has added sub-agent as tool")

        print("\n=== First call: Trigger interrupt ===")
        result1 = await Runner.run_agent(
            agent=main_agent,
            inputs={"query": "Call sub-agent to read file /tmp/test.txt", "conversation_id": "session_1"},
        )

        print(f"Result type: {result1.get('result_type')}")

        if result1.get("result_type") == "interrupt":
            interrupt_ids = result1.get("interrupt_ids", [])
            print(f"Interrupt count: {len(interrupt_ids)}")

            state_list = result1.get("state", [])
            for state in state_list:
                payload = state.payload
                tool_call_id = payload.id
                interrupt_value = payload.value

                print(f"\nInterrupt details:")
                print(f"  Tool name: {interrupt_value.tool_name}")
                print(f"  Prompt message: {interrupt_value.message}")
                print(f"  Note: Interrupt from sub-agent, but handled uniformly at main agent")

                interactive_input = InteractiveInput()
                interactive_input.update(tool_call_id, {
                    "approved": True,
                    "feedback": "Confirm",
                    "auto_confirm": True
                })

                print("\n=== Second call: Resume execution ===")
                result2 = await Runner.run_agent(
                    agent=main_agent,
                    inputs={"query": interactive_input, "conversation_id": "session_1"},
                )

                print(f"Result type: {result2.get('result_type')}")

                if result2.get("result_type") == "answer":
                    messages = result2.get("messages", [])
                    if messages:
                        print(f"Final response: {messages[-1].content[:100]}")

                print("\n=== Third call: Test auto-confirm ===")
                result3 = await Runner.run_agent(
                    agent=main_agent,
                    inputs={"query": "Call sub-agent to read file /tmp/test.txt again", "conversation_id": "session_1"},
                )

                print(f"Result type: {result3.get('result_type')}")
                print("Note: Due to auto_confirm enabled, this call did not trigger interrupt")
    finally:
        await Runner.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

**Output Example**:

```
=== Create sub-agent ===
Sub-agent has registered read_file interrupt Rail

=== Create main agent ===
Main agent has added sub-agent as tool

=== First call: Trigger interrupt ===
Result type: interrupt
Interrupt count: 1

Interrupt details:
  Tool name: read_file
  Prompt message: Please approve or reject?
  Note: Interrupt from sub-agent, but handled uniformly at main agent

=== Second call: Resume execution ===
Result type: answer

=== Third call: Test auto-confirm ===
Result type: answer
Note: Due to auto_confirm enabled, this call did not trigger interrupt
```
