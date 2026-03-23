This section introduces the **Agent as Tool** capability in openJiuwen. Through this capability, any Agent can be registered as a tool (Ability) in another Agent's `AbilityManager`. The host Agent's LLM can then invoke the sub-Agent just like a regular tool during reasoning, enabling multi-agent collaboration.



# Usage

## Step 1: Define the Sub-Agent

The sub-Agent is a regular `BaseAgent` subclass implementing `invoke()`. The `AgentCard`'s `description` and `input_params` fields are converted into tool descriptions that directly influence the LLM's call decisions — write them clearly and accurately.

```python
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

class TranslatorAgent(BaseAgent):
    """Translation Agent: translates input text to a target language."""

    def configure(self, config): return self

    async def invoke(self, inputs, session=None):
        text = inputs.get("text", "") if isinstance(inputs, dict) else str(inputs)
        target_lang = inputs.get("target_lang", "English") if isinstance(inputs, dict) else "English"
        # In production, call a real translation service here
        translated = f"[{target_lang}] {text}"
        return {"translated": translated}

    async def stream(self, inputs, session=None):
        yield await self.invoke(inputs, session)


translator_card = AgentCard(
    id="translator_agent",
    name="translator_agent",
    description="Translation Agent that translates input text into the specified target language",
    input_params={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The original text to translate"
            },
            "target_lang": {
                "type": "string",
                "description": "Target language, e.g. English, French, Japanese"
            }
        },
        "required": ["text", "target_lang"]
    }
)
```

## Step 2: Register the Sub-Agent with ResourceManager

The sub-Agent must be registered with `Runner.resource_mgr` so the framework can retrieve its instance at runtime:

```python
from openjiuwen.core.runner import Runner

translator = TranslatorAgent(card=translator_card)
Runner.resource_mgr.add_agent(translator_card, lambda: translator)
```

## Step 3: Register the Sub-Agent as an Ability of the Host Agent

Use `ability_manager.add(AgentCard)` to register the sub-Agent's Card as a capability of the host:

```python
host_agent.ability_manager.add(translator_card)
```

Once registered, `translator_agent` appears in the tool list sent to the LLM, which can invoke it at the appropriate time.

# Complete Example

The following example registers `SummarizerAgent` and `TranslatorAgent` as tools for a `ReActAgent`. The host Agent automatically decides which sub-Agent to call based on the user request.

```python
import asyncio
import os
from typing import Any, AsyncIterator, Optional

from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import ReActAgent, ReActAgentConfig, AgentCard
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.session.session import Session

API_BASE = os.getenv("API_BASE", "your api base")
API_KEY = os.getenv("API_KEY", "your api key")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")


class SummarizerAgent(BaseAgent):
    """Summarizer Agent: generates a summary of the input text."""

    def configure(self, config): return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        text = inputs.get("text", "") if isinstance(inputs, dict) else str(inputs)
        summary = f"Summary: {text[:50]}{'...' if len(text) > 50 else ''}"
        return {"summary": summary}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, session)


class TranslatorAgent(BaseAgent):
    """Translation Agent: translates text to a target language."""

    def configure(self, config): return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        text = inputs.get("text", "") if isinstance(inputs, dict) else str(inputs)
        target_lang = inputs.get("target_lang", "English") if isinstance(inputs, dict) else "English"
        translated = f"[Translated to {target_lang}] {text}"
        return {"translated": translated}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, session)


summarizer_card = AgentCard(
    id="summarizer_agent",
    name="summarizer_agent",
    description="Summarizer Agent that generates a concise summary of longer texts",
    input_params={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The original text to summarize"}
        },
        "required": ["text"]
    }
)

translator_card = AgentCard(
    id="translator_agent",
    name="translator_agent",
    description="Translation Agent that translates input text into the specified target language",
    input_params={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The original text to translate"},
            "target_lang": {"type": "string", "description": "Target language, e.g. English, French, Japanese"}
        },
        "required": ["text", "target_lang"]
    }
)


async def main():
    # 1. Create sub-Agent instances and register with ResourceManager
    summarizer = SummarizerAgent(card=summarizer_card)
    translator = TranslatorAgent(card=translator_card)
    Runner.resource_mgr.add_agent(summarizer_card, lambda: summarizer)
    Runner.resource_mgr.add_agent(translator_card, lambda: translator)

    # 2. Create host ReActAgent
    host_card = AgentCard(id="host_react_agent", name="host_react_agent", description="Host Agent")
    react_config = ReActAgentConfig(
        model_config_obj=ModelRequestConfig(model=MODEL_NAME, temperature=0.7),
        model_client_config=ModelClientConfig(
            client_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
            timeout=60,
            verify_ssl=False,
        ),
        prompt_template=[
            {
                "role": "system",
                "content": (
                    "You are an intelligent assistant with access to the following tools:\n"
                    "- summarizer_agent: generates a concise summary of longer texts\n"
                    "- translator_agent: translates text into a target language\n"
                    "Use tools appropriately based on the user request."
                )
            }
        ]
    )
    host_agent = ReActAgent(card=host_card).configure(react_config)

    # 3. Register sub-Agents as Abilities of the host
    host_agent.ability_manager.add(summarizer_card)
    host_agent.ability_manager.add(translator_card)

    # 4. Run the host Agent
    result = await host_agent.invoke(
        {"query": "Please translate the following into English: AI is changing the world."}
    )
    print(f"Final output: {result}")


if __name__ == "__main__":
    asyncio.run(main())
```

The host `ReActAgent` will automatically identify the user's intent, call `translator_agent`, and return a result such as:

```text
Final output: [Translated to English] AI is changing the world.
```
