本章节介绍 openJiuwen 的 **Agent as Tool** 能力。通过该能力，可以将任意 Agent 作为工具（Tool）注册到另一个 Agent 的 `AbilityManager`，主 Agent 可以像调用普通工具一样调用子 Agent，从而实现多智能体协作。



# 使用方式

## 第一步：定义子 Agent

子 Agent 是普通的 `BaseAgent` 子类，需要实现 `invoke()` 方法。`AgentCard` 的 `description` 和 `input_params` 字段会被转换为工具描述，直接影响大模型的调用决策，因此应尽量清晰准确。

```python
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

class TranslatorAgent(BaseAgent):
    """翻译 Agent：将输入文本翻译为目标语言。"""

    def configure(self, config): return self

    async def invoke(self, inputs, session=None):
        text = inputs.get("text", "") if isinstance(inputs, dict) else str(inputs)
        target_lang = inputs.get("target_lang", "English") if isinstance(inputs, dict) else "English"
        # 实际业务中这里调用翻译服务
        translated = f"[{target_lang}] {text}"
        return {"translated": translated}

    async def stream(self, inputs, session=None):
        result = await self.invoke(inputs, session)
        yield result


translator_card = AgentCard(
    id="translator_agent",
    name="translator_agent",
    description="翻译 Agent，将输入文本翻译为指定的目标语言",
    input_params={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "需要翻译的原始文本"
            },
            "target_lang": {
                "type": "string",
                "description": "目标语言，例如 English、French、Japanese"
            }
        },
        "required": ["text", "target_lang"]
    }
)
```

## 第二步：注册子 Agent 到 ResourceManager

子 Agent 必须注册到 `Runner.resource_mgr`，框架才能在运行时获取其实例：

```python
from openjiuwen.core.runner import Runner

translator = TranslatorAgent(card=translator_card)
Runner.resource_mgr.add_agent(translator_card, lambda: translator)
```

## 第三步：将子 Agent 注册为主 Agent 的 Ability

通过 `ability_manager.add(AgentCard)` 将子 Agent 的 Card 注册为主的能力：

```python
host_agent.ability_manager.add(translator_card)
```

注册后，`translator_agent` 会出现在主 Agent 发给大模型的工具列表中，大模型可在适当时机调用它。

# 完整示例

以下示例展示了将 `SummarizerAgent` 和 `TranslatorAgent` 作为工具注册给 `ReActAgent`，主 Agent 根据用户请求自动决策调用哪个子 Agent。

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


# ---------------------------------------------------------------------------
# 子 Agent 定义
# ---------------------------------------------------------------------------

class SummarizerAgent(BaseAgent):
    """摘要 Agent：对输入文本生成摘要。"""

    def configure(self, config): return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        text = inputs.get("text", "") if isinstance(inputs, dict) else str(inputs)
        # 实际业务中可调用 LLM 生成摘要，此处简化处理
        summary = f"摘要：{text[:50]}{'...' if len(text) > 50 else ''}"
        return {"summary": summary}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        result = await self.invoke(inputs, session)
        yield result


class TranslatorAgent(BaseAgent):
    """翻译 Agent：将文本翻译为目标语言。"""

    def configure(self, config): return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        text = inputs.get("text", "") if isinstance(inputs, dict) else str(inputs)
        target_lang = inputs.get("target_lang", "English") if isinstance(inputs, dict) else "English"
        # 实际业务中可调用翻译服务，此处简化处理
        translated = f"[Translated to {target_lang}] {text}"
        return {"translated": translated}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        result = await self.invoke(inputs, session)
        yield result


# ---------------------------------------------------------------------------
# AgentCard 定义
# ---------------------------------------------------------------------------

summarizer_card = AgentCard(
    id="summarizer_agent",
    name="summarizer_agent",
    description="摘要 Agent，对较长文本生成简洁的摘要",
    input_params={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "需要生成摘要的原始文本"
            }
        },
        "required": ["text"]
    }
)

translator_card = AgentCard(
    id="translator_agent",
    name="translator_agent",
    description="翻译 Agent，将输入文本翻译为指定的目标语言",
    input_params={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "需要翻译的原始文本"
            },
            "target_lang": {
                "type": "string",
                "description": "目标语言，例如 English、French、Japanese"
            }
        },
        "required": ["text", "target_lang"]
    }
)


async def main():
    # 1. 创建子 Agent 实例并注册到 ResourceManager
    summarizer = SummarizerAgent(card=summarizer_card)
    translator = TranslatorAgent(card=translator_card)
    Runner.resource_mgr.add_agent(summarizer_card, lambda: summarizer)
    Runner.resource_mgr.add_agent(translator_card, lambda: translator)

    # 2. 创建主 ReActAgent
    host_card = AgentCard(id="host_react_agent", name="host_react_agent", description="主智能体")
    react_config = ReActAgentConfig(
        model_config_obj=ModelRequestConfig(
            model=MODEL_NAME,
            temperature=0.7,
        ),
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
                    "你是一个智能助手，可以调用以下工具完成用户任务：\n"
                    "- summarizer_agent：对长文本生成摘要\n"
                    "- translator_agent：将文本翻译为目标语言\n"
                    "请根据用户请求合理调用工具。"
                )
            }
        ]
    )
    host_agent = ReActAgent(card=host_card).configure(react_config)

    # 3. 将子 Agent 注册为主的 Ability
    host_agent.ability_manager.add(summarizer_card)
    host_agent.ability_manager.add(translator_card)

    # 4. 运行主 Agent
    result = await host_agent.invoke(
        {"query": "请把以下内容翻译成英文：人工智能正在改变世界。"}
    )
    print(f"最终输出：{result}")


if __name__ == "__main__":
    asyncio.run(main())
```

运行后，主 `ReActAgent` 会自动识别用户意图，调用 `translator_agent` 完成翻译任务，输出示例：

```text
最终输出：[Translated to English] 人工智能正在改变世界。
```


