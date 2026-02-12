This chapter demonstrates how to rapidly develop a custom Agent based on openJiuwen. The Agent built in this example is capable of calling Large Language Model (LLM) services and identifying sensitive words that may be contained in the LLM output results. Through this example, you will learn the following information:

- How to inherit the `AgentCard` base class to implement a configuration class for a custom Agent.
- How to override the initialization method `__init__` of the `BaseAgent` base class to implement the initialization method for a custom Agent.
- How to implement the batch execution abstract interface `invoke` defined by the `BaseAgent` base class to asynchronously batch execute the specific business logic of the custom Agent.

# Application Design Flow

This chapter designs a custom Agent to implement a safer Agent: it can validate output answers based on a preset list of sensitive words. To implement this Agent, developers need to:

- Design the configuration class for the custom Agent.
- Configure the preset list of sensitive words for the Agent in its initialization method `__init__`.
- Implement the Agent's business logic in the custom Agent's batch execution abstract interface `invoke`: combine the Agent's default system prompt and the user request to construct the input for calling the LLM; then call the LLM service and wait for the LLM's response; finally, validate the LLM output based on the configured sensitive words list to ensure the Agent does not output malicious information.

  <div align="center">
    <img src="../images/MyAgent.png" alt="MyAgent" width="70%">
  </div>

# Prerequisites

The Python version should be higher than or equal to Python 3.11. Version 3.11.4 is recommended. Please check the Python version information before use.

# Install openJiuwen

Users can choose to create a virtual environment to install openJiuwen. The installation command is as follows:

```bash
pip install -U openjiuwen
```

# Implementing the Custom Agent Configuration Class

To implement an Agent capable of calling LLM services and identifying sensitive words that may be contained in the LLM output results, in addition to the basic configuration items defined in `AgentCard` (such as the Agent's `id`, `version`, `description`, etc.), custom configurations are also needed (specifically including the configuration information `model` for calling the LLM and the custom sensitive words list `sensitive_words`). The example code is as follows:

```python
import os
from typing import Optional, List
from pydantic import Field

from openjiuwen.core.foundation.llm import ModelConfig, BaseModelInfo
from openjiuwen.core.single_agent import AgentCard, BaseAgent

API_BASE = os.getenv("API_BASE", "your api url")
API_KEY = os.getenv("API_KEY", "your api token")
MODEL_NAME = os.getenv("MODEL_NAME", "your model name")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "your model provider")

class MyAgentCard(AgentCard):
    model: Optional[ModelConfig] = Field(default=None)
    sensitive_words: List[str] = Field(default_factory=list)


def create_model_config() -> ModelConfig:
    """Get LLM-related configuration information through environment variables"""
    return ModelConfig(
        model_provider=MODEL_PROVIDER,
        model_info=BaseModelInfo(
            model=MODEL_NAME,
            api_base=API_BASE,
            api_key=API_KEY,
            temperature=0.7,
            top_p=0.9,
            timeout=30,
        ),
    )

# Create custom Agent configuration object
my_agent_card = MyAgentCard(
    id="my_agent_id",
    name="my_agent",
    description="My custom agent",
    model=create_model_config(),  # Configure LLM-related parameter information
    sensitive_words=["Custom sensitive words list"]
)
```

Among them, the configuration information of the LLM service is read via the `create_model_config` method through environment variables, including the model provider (`MODEL_PROVIDER`), model name (`MODEL_NAME`), LLM service call path (`API_BASE`), and LLM service authentication information (`API_KEY`), completing the configuration of the LLM service call object.

# Implementing the Initialization Method

Next, developers need to implement the initialization method of the custom Agent to support the creation of custom Agent objects. The initialization method of the `BaseAgent` base class provides the Agent with the capabilities of initializing `Session` and configuration management `Config`. In addition to calling the initialization method of the `BaseAgent` base class, the custom Agent also needs to create an LLM call object based on the LLM configuration information and initialize the configured sensitive words list. The specific example is as follows:

```python
from openjiuwen.core.foundation.llm import (
    Model, ModelClientConfig, ModelRequestConfig
)
from openjiuwen.core.single_agent import AgentCard, BaseAgent

class MyAgent(BaseAgent):
    def __init__(self, agent_card: AgentCard):
        # Initialize configuration information
        super().__init__(agent_card)
        # Get LLM configuration
        self._model_config = agent_card.model
        # Use Model + ModelClientConfig / ModelRequestConfig to create LLM call object
        client_config = ModelClientConfig(
            client_provider=self._model_config.model_provider,
            api_key=self._model_config.model_info.api_key,
            api_base=self._model_config.model_info.api_base,
            timeout=self._model_config.model_info.timeout,
            verify_ssl=False,
        )
        request_config = ModelRequestConfig(
            model=self._model_config.model_info.model_name,
            temperature=self._model_config.model_info.temperature,
            top_p=self._model_config.model_info.top_p,
        )
        self._llm = Model(
            model_client_config=client_config,
            model_config=request_config,
        )
        # Initialize configured custom sensitive words list
        self._sensitive_words = agent_card.sensitive_words

    def configure(self, config) -> 'BaseAgent':
        pass
```

# Implementing the invoke Method

The Agent in this example possesses the following functions: it is capable of calling LLM services and identifying sensitive words that may be contained in the LLM output results.

The `invoke` method is used to call the LLM. Before calling, the user input is encapsulated as a `UserMessage`, combined with the custom Agent's built-in system prompt `SystemMessage`, and used as input for the LLM to obtain the returned result. Finally, check whether the LLM output contains custom sensitive words. Once the LLM output contains custom sensitive words, a fixed phrase is output: "对不起，无法回答您的问题。" The example code is as follows:

```python
from typing import Dict
from openjiuwen.core.foundation.llm import UserMessage, SystemMessage
from openjiuwen.core.single_agent import Session
from openjiuwen.core.single_agent import BaseAgent

class MyAgent(BaseAgent):
    async def invoke(self, inputs: Dict, session: Session | None = None):
        # inputs is the input of the custom Agent, with llm_inputs as the key, user input information as the user prompt
        user_message = UserMessage(content=inputs.get("llm_inputs", "")).model_dump(exclude_none=True)
        # Default system prompt of the custom Agent
        system_message = SystemMessage(content="You are an AI assistant").model_dump(exclude_none=True)
        # LLM input includes: system prompt and user prompt
        llm_input_messages = [system_message, user_message]
        # Call the model's asynchronous execution method to get the model's output
        res = await self._llm.invoke(model=self._model_config.model_info.model_name, messages=llm_input_messages)
        llm_output = res.content
        for word in self._sensitive_words:
            if word in llm_output:
                return "Sorry, I cannot answer your question."
        # If the LLM output does not contain sensitive words, directly return the LLM output content
        return llm_output
```

# Implementing the stream Method

The `stream` method enables streaming invocation capability for the agent, returning an asynchronous iterator to support real-time responses. In this example implementation, for simplicity, the method directly reuses the complete processing logic from `invoke`, returning the final result as a single streaming chunk. The implementation is as follows:

```python
from typing import AsyncIterator, Any, Optional, List

from openjiuwen.core.session.stream.base import StreamMode
from openjiuwen.core.single_agent import Session

class MyAgent(BaseAgent):
    async def stream(self,
            inputs: Any,
            session: Optional[Session] = None,
            stream_modes: Optional[List[StreamMode]] = None
    ) -> AsyncIterator[Any]:
        content = await self.invoke(inputs)
        yield {"type": "answer", "content": content}
```

# Running the Custom Agent

After the developer completes the relevant implementation of the custom Agent, they can call the `invoke` method to run the custom Agent asynchronously and non-streamingly. The example code is as follows:

```python
import asyncio

# Create custom Agent object
my_agent = MyAgent(my_agent_card)

inputs = {"llm_inputs": "写一个笑话"}
res = asyncio.run(my_agent.invoke(inputs))
print(res)
```

# Complete Example Code

The complete example reference is as follows:

```python
import asyncio
import os
from pydantic import Field
from typing import Optional, List, Dict, AsyncIterator, Any

from openjiuwen.core.foundation.llm import (
    ModelConfig, BaseModelInfo, UserMessage, SystemMessage,
    Model, ModelClientConfig, ModelRequestConfig
)
from openjiuwen.core.single_agent import Session
from openjiuwen.core.session.stream.base import StreamMode
from openjiuwen.core.single_agent import AgentCard, BaseAgent

API_BASE = os.getenv("API_BASE", "your api url")
API_KEY = os.getenv("API_KEY", "your api key")
MODEL_NAME = os.getenv("MODEL_NAME", "your model name")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "your model provider")

class MyAgentCard(AgentCard):
    model: Optional[ModelConfig] = Field(default=None)
    sensitive_words: List[str] = Field(default_factory=list)


def create_model_config() -> ModelConfig:
    """Get LLM-related configuration information through environment variables"""
    return ModelConfig(
        model_provider=MODEL_PROVIDER,
        model_info=BaseModelInfo(
            model=MODEL_NAME,
            api_base=API_BASE,
            api_key=API_KEY,
            temperature=0.7,
            top_p=0.9,
            timeout=30,
        ),
    )

# Create custom Agent configuration object
my_agent_card = MyAgentCard(
    id="my_agent_id",
    name="my_agent",
    description="My custom agent",
    model=create_model_config(),  # Configure LLM-related parameter information
    sensitive_words=["Custom sensitive words list"]
)

class MyAgent(BaseAgent):
    def configure(self, config) -> 'BaseAgent':
        pass

    def __init__(self, agent_card: AgentCard):
        # Initialize configuration information
        super().__init__(agent_card)
        # Get LLM configuration
        self._model_config = agent_card.model
        # Use Model + ModelClientConfig / ModelRequestConfig to create LLM call object
        client_config = ModelClientConfig(
            client_provider=self._model_config.model_provider,
            api_key=self._model_config.model_info.api_key,
            api_base=self._model_config.model_info.api_base,
            timeout=self._model_config.model_info.timeout,
            verify_ssl=False,
        )
        request_config = ModelRequestConfig(
            model=self._model_config.model_info.model_name,
            temperature=self._model_config.model_info.temperature,
            top_p=self._model_config.model_info.top_p,
        )
        self._llm = Model(
            model_client_config=client_config,
            model_config=request_config,
        )
        # Initialize configured custom sensitive words list
        self._sensitive_words = agent_card.sensitive_words

    async def invoke(self, inputs: Dict, session: Session | None = None):
        # inputs is the input of the custom Agent, with llm_inputs as the key, user input information as the user prompt
        user_message = UserMessage(content=inputs.get("llm_inputs", "")).model_dump(exclude_none=True)
        # Default system prompt of the custom Agent
        system_message = SystemMessage(content="You are an AI assistant").model_dump(exclude_none=True)
        # LLM input includes: system prompt and user prompt
        llm_input_messages = [system_message, user_message]
        # Call the model's asynchronous execution method to get the model's output
        res = await self._llm.invoke(model=self._model_config.model_info.model_name, messages=llm_input_messages)
        llm_output = res.content
        for word in self._sensitive_words:
            if word in llm_output:
                return "Sorry, I cannot answer your question."
        # If the LLM output does not contain sensitive words, directly return the LLM output content
        return llm_output

    async def stream(self,
            inputs: Any,
            session: Optional[Session] = None,
            stream_modes: Optional[List[StreamMode]] = None
    ) -> AsyncIterator[Any]:
        content = await self.invoke(inputs)
        yield {"type": "answer", "content": content}

# Create custom Agent object
my_agent = MyAgent(agent_card=my_agent_card)

inputs = {"llm_inputs": "写一个笑话"}
res = asyncio.run(my_agent.invoke(inputs))
print(res)
```

Final output result:

```
Why don't programmers like to go out in summer?

Because they are afraid of "heatstroke" (heatstroke in Chinese is zhòng shǔ, which sounds like "middle number" - they deal with "middle number" problems all day, such as whether array indices start from 0 or 1, which is already a headache!) 😂
```
