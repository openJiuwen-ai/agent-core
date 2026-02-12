In the openJiuwen framework, workflows and Agent runtimes are implemented by the `Session` core class. This class enables real-time streaming of data to the outside of workflows or Agents, allowing efficient, real-time data processing and delivery, thus meeting high-performance and low-latency requirements. `Session` supports the following two streaming scenarios:

* **Workflow streaming output**: In workflows, both token generation by LLM components and the interaction process of questioner components support real-time streaming output. Debugging and exception information during component runtime can also be presented via streaming, enabling end-to-end real-time monitoring and issue handling.
* **Agent streaming output**: In Agents, the thinking, planning, and execution processes can all be fed back to users in real time via streaming data. Using asynchronous data transmission mechanisms and structured output interfaces, users can monitor and understand the Agent's execution status and processing logic in real time.

Workflows and Agents can use the `Session`-provided `write_stream` interface or `write_custom_stream` interface to asynchronously put real-time generated data into message queues, and consume/process the data in real time via the workflow and Agent `stream` interfaces. Both the openJiuwen standard format `OutputSchema` and user-defined `CustomSchema` structured outputs are supported.

# Workflow streaming output

In workflow scenarios, streaming output is typically implemented by components calling the streaming interfaces provided by `Session`, transmitting real-time data step-by-step through an asynchronous producer-consumer architecture. The following shows how to use `Session`'s two streaming output interfaces to design and implement a workflow with streaming output capabilities.

Define a custom component `CustomComponent`. The component input is a think-mode flag: `True` means think mode is enabled and outputs streaming data in the standard format; `False` means think mode is disabled and outputs streaming data in a custom format.

```python
import asyncio

from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import Start
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow import Input, Output
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.session.stream import OutputSchema, BaseStreamMode, CustomSchema
from openjiuwen.core.workflow import Workflow

class CustomComponent(WorkflowComponent):
    def __init__(self, component_id: str):
        super().__init__()
        self.component_id = component_id

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """Output different streaming formats based on whether think mode is enabled"""
        think_mode = inputs.get("think_mode", False)
        if think_mode:
            # Standard format streaming output; input type is OutputSchema
            await session.write_stream(OutputSchema(type="answer", index=0,
                payload=(self.component_id, {"content": f"I am {self.component_id}, please waiting thinking."})))
        else:
            # Custom format streaming output; input type is dict
            await session.write_custom_stream(data={"answer": "I will generate a picture for you."})
        return {}
```

Build a basic workflow by adding the start component `start`, custom component `stream_out_comp`, and end component `end`, and establish the topology among the three components. Set the streaming output modes to standard and custom, and execute the workflow under two scenarios (think mode on/off):

```python
async def run_workflow(think_mode: bool):
    # Initialize workflow
    workflow = Workflow()

    # Add start and end components to the workflow
    workflow.set_start_comp("start", Start(), inputs_schema={"query": "${user_inputs.query}"})
    workflow.set_end_comp("end", End(),
                          inputs_schema={"query": "${start.query}", "answer": "${stream_out_comp.answer}"})

    # Add the custom component that directly streams output
    stream_out_comp = CustomComponent(component_id="stream_out_comp")
    workflow.add_workflow_comp("stream_out_comp", stream_out_comp,
                               inputs_schema={"think_mode": "${user_inputs.think_mode}"})

    # Define component connections
    workflow.add_connection("start", "stream_out_comp")
    workflow.add_connection("stream_out_comp", "end")
    # Build inputs and workflow session, then invoke the workflow
    inputs = {"user_inputs": {"query": "Help me generate a picture of West Lake.",
                              "think_mode": think_mode}}
    session = create_workflow_session()

    # Set streaming modes to standard and custom
    print(f"\n{'=' * 20} think_mode is {think_mode}, begin to run workflow {'=' * 20}\n")
    async for chunk in workflow.stream(inputs, session, stream_modes=[BaseStreamMode.OUTPUT, BaseStreamMode.CUSTOM]):
        if isinstance(chunk, OutputSchema):
            print(f"Receive stream chunk (OutputSchema): {chunk}")
        elif isinstance(chunk, CustomSchema):
            print(f"Receive stream chunk (CustomSchema): {chunk}")
        else:
            print(f"Receive stream chunk (CustomSchema): {chunk}")
    print(f"\n{'=' * 21} think_mode is {think_mode}, end to run workflow {'=' * 21}\n")


async def main():
    # Scenario 1: think_mode set to True
    await run_workflow(think_mode=True)

    # Scenario 2: think_mode set to False
    await run_workflow(think_mode=False)


asyncio.run(main())
```

Output:

```python
==================== think_mode is True, begin to run workflow ====================
Receive stream chunk (OutputSchema): type='answer' index=0 payload=('stream_out_comp', {'content': 'I am stream_out_comp, please waiting thinking.'})
Receive stream chunk (OutputSchema): type='workflow_final' index=0 payload={'output': {'query': 'Help me generate a picture of West Lake.'}}
===================== think_mode is True, end to run workflow =====================

==================== think_mode is False, begin to run workflow ====================
Receive stream chunk (CustomSchema): answer='I will generate a picture for you.'
Receive stream chunk (OutputSchema): type='workflow_final' index=0 payload={'output': {'query': 'Help me generate a picture of West Lake.'}}
===================== think_mode is False, end to run workflow =====================
```

From the workflow execution results, we can see:

* When the user input `think_mode` is `True`, the custom component `stream_out_comp` outputs streaming data of type `OutputSchema` with content: `type='answer' index=0 payload=('stream_out_comp', {'content': 'I am stream_out_comp, please waiting thinking.'})`.
* When the user input `think_mode` is `False`, the custom component `stream_out_comp` outputs streaming data of type `CustomSchema` with content: `answer='I will generate a picture for you.'`.
* In both scenarios, the workflow outputs the final execution result as streaming data of type `OutputSchema` with content: `type='workflow_final' index=0 payload={'output': {'query': 'Help me generate a picture of West Lake.'}}`.

# Agent streaming output

In Agent scenarios, streaming output is typically implemented via the Agent's skill modules (such as LLM services, workflows, etc.), by calling the `Session` streaming interfaces to achieve real-time, efficient data transmission and processing. The following demonstrates how to use `Session`'s two streaming output interfaces to design and implement an Agent with streaming capabilities.

To simulate the process of an LLM generating tokens, create a `MockModel` class that generates different tokens based on the think-mode flag. If think mode is enabled, it streams tokens in the standard format from the list `["I", "am", "MockModel,", "please", "waiting", "thinking."]`. If think mode is disabled, it streams tokens in the custom format from the list `["I", "will", "generate", "a", "picture", "for", "you."]`.

```python
import asyncio
from typing import Dict, AsyncIterator, Any

from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.single_agent import BaseAgent
from openjiuwen.core.single_agent import Session, create_agent_session
from openjiuwen.core.session.stream import OutputSchema, CustomSchema, StreamMode
from openjiuwen.core.session import get_default_inmemory_checkpointer

class MockModel:
    def __init__(self, model_name: str):
        self.model_name = model_name if model_name else "MockModel"

    async def call(self, inputs: Dict, session: Session):
        """Simulate an LLM generating tokens"""
        think_mode = inputs.get("think_mode", False)
        # Simulate the asynchronous token generation process
        if think_mode:
            # Standard format streaming output; input type is OutputSchema
            tokens = ["I", "am", "MockModel,", "please", "waiting", "thinking."]
            index = 0
            for token in tokens:
                await asyncio.sleep(0.1)
                await session.write_stream(OutputSchema(type="answer", index=index,
                                                        payload=(self.model_name,
                                                                 {"content": token})))
                index += 1
        else:
            # Custom format streaming output; input type is dict
            tokens = ["I", "will", "generate", "a", "picture", "for", "you."]
            for token in tokens:
                await asyncio.sleep(0.1)
                await session.write_custom_stream(data={"answer": token})
```

Create a custom Agent based on the Agent abstract base class, and create a default-configured `MockModel` instance as a member of this Agent. Implement the Agent to call `MockModel` to stream data:

```python
class CustomAgent(BaseAgent):
    def __init__(self, card: AgentCard):
        super().__init__(card)
        self._model = MockModel(model_name="MockModel")

    def configure(self, config) -> 'BaseAgent':
        """Set configuration"""
        pass

    async def invoke(self, inputs: Dict, session: Optional[Session] = None) -> Dict:
        """invoke"""
        pass

    async def stream(self, inputs: Any, session: Optional[Session] = None, stream_modes: Optional[List[StreamMode]] = None) -> AsyncIterator[Any]:
        """Call the mock LLM to stream data"""
        session_id = inputs.pop("conversation_id", "default_session")
        # Create current runtime
        session = create_agent_session(session_id=session_id, card=self.card)

        # Temporary solution, will be removed in future versions
        await get_default_inmemory_checkpointer().pre_agent_execute(getattr(getattr(session, "_inner"), "_inner"), inputs)

        async def stream_process():
            try:
                # Call the mock model and pass in the session to record streaming data in real time
                await self._model.call(inputs, session)
            except Exception as ex:
                print(f"Model call error: {ex}")
            finally:
                # Post-run handling to clean up resources
                await session.post_run()

        # Create async task to produce streaming data
        task = asyncio.create_task(stream_process())
        
        # Consume streaming data in real time
        async for chunk in session.stream_iterator():
            yield chunk

        try:
            await task
        except Exception as e:
            print(f"CustomAgent stream error: {e}")
```

Create a custom Agent instance and run it under two scenarios (think mode on/off):

```python
async def run_agent(think_mode: bool):
    card = AgentCard(id="CustomAgent", description="Custom Agent")
    agent = CustomAgent(card)
    print(f"\n{'=' * 20} think_mode is {think_mode}, begin to run agent {'=' * 20}\n")
    async for chunk in agent.stream({"conversation_id": "mock_session", "think_mode": think_mode}):
        if isinstance(chunk, OutputSchema):
            print(f"Receive stream chunk (OutputSchema): {chunk}")
        elif isinstance(chunk, CustomSchema):
            print(f"Receive stream chunk (CustomSchema): {chunk}")
        else:
            print(f"Receive unsupported stream chunk: {chunk}")
    print(f"\n{'=' * 21} think_mode is {think_mode}, end to run agent {'=' * 21}\n")


async def main():
    # Scenario 1: think_mode set to True
    await run_agent(think_mode=True)

    # Scenario 2: think_mode set to False
    await run_agent(think_mode=False)


asyncio.run(main())
```

Output:

```python
==================== think_mode is True, begin to run agent ====================
Receive stream chunk (OutputSchema): type='answer' index=0 payload=('MockModel', {'content': 'I'})
Receive stream chunk (OutputSchema): type='answer' index=1 payload=('MockModel', {'content': 'am'})
Receive stream chunk (OutputSchema): type='answer' index=2 payload=('MockModel', {'content': 'MockModel,'})
Receive stream chunk (OutputSchema): type='answer' index=3 payload=('MockModel', {'content': 'please'})
Receive stream chunk (OutputSchema): type='answer' index=4 payload=('MockModel', {'content': 'waiting'})
Receive stream chunk (OutputSchema): type='answer' index=5 payload=('MockModel', {'content': 'thinking.'})
===================== think_mode is True, end to run agent =====================

==================== think_mode is False, begin to run agent ====================
Receive stream chunk (CustomSchema): answer='I'
Receive stream chunk (CustomSchema): answer='will'
Receive stream chunk (CustomSchema): answer='generate'
Receive stream chunk (CustomSchema): answer='a'
Receive stream chunk (CustomSchema): answer='picture'
Receive stream chunk (CustomSchema): answer='for'
Receive stream chunk (CustomSchema): answer='you.'
===================== think_mode is False, end to run agent =====================
```

From the Agent execution results, we can see:

* When the user input `think_mode` is `True`, the Agent outputs streaming data of type `OutputSchema`, and the token contents match the simulation, i.e., tokens from the list `["I", "am", "MockModel,", "please", "waiting", "thinking."]`.
* When the user input `think_mode` is `False`, the Agent outputs streaming data of type `CustomSchema`, and the token contents match the simulation, i.e., tokens from the list `["I", "will", "generate", "a", "picture", "for", "you."]`.