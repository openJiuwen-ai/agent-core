#!/usr/bin/env python
# coding: utf-8
"""
OpenRouter LLM Client for OpenJiuwen Framework
Refactored from miroflow/llm/providers/claude_openrouter_client.py
"""

import asyncio
from typing import List, Dict, Any, Iterator, AsyncIterator, Optional

import tiktoken
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import AssistantMessage, UsageMetadata, AssistantMessageChunk
from openjiuwen.core.foundation.llm import ToolCall

from openjiuwen.core.foundation.llm import BaseModelClient

class ContextLimitError(Exception):
    """Exception raised when context limit is exceeded"""
    pass


class TokenUsage(BaseModel):
    """Token usage tracking"""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_input_tokens: int = 0
    total_cache_creation_input_tokens: int = 0


class OpenRouterConfig(BaseModel):
    """OpenRouter specific configuration"""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    api_key: str
    api_base: str = "https://openrouter.ai/api/v1"
    model_name: str
    max_retries: int = 3
    timeout: int = 600
    temperature: float = 0.1
    top_p: float = 1.0
    max_tokens: int = 4096
    max_context_length: int = 200000

    # Pricing (per million tokens)
    input_token_price: float = 3.0
    output_token_price: float = 15.0
    cache_input_token_price: float = 0.3

    # OpenRouter provider preference (google, anthropic, amazon)
    openrouter_provider: Optional[str] = None

    # Cache control
    disable_cache_control: bool = False


class OpenRouterLLM(BaseModelClient):
    """OpenRouter LLM implementation following openjiuwen patterns"""

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://openrouter.ai/api/v1",
        model_name: str = "anthropic/claude-3.5-sonnet",
        max_retries: int = 3,
        timeout: int = 600,
        **kwargs
    ):
        super().__init__(
            api_key=api_key,
            api_base=api_base,
            max_retries=max_retries,
            timeout=timeout
        )

        self.config = OpenRouterConfig(
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
            max_retries=max_retries,
            timeout=timeout,
            **kwargs
        )

        # Create async client
        self._client = AsyncOpenAI(
            api_key=self.config.api_key,
            base_url=self.config.api_base,
            timeout=self.config.timeout
        )

        # Token usage tracking
        self.token_usage = TokenUsage()
        self.last_call_tokens = {"prompt_tokens": 0, "completion_tokens": 0}

        # tiktoken for token estimation
        self.encoding = None

    def model_provider(self) -> str:
        return "openrouter"

    def _invoke(self, model_name: str, messages: List[Dict], tools: List[Dict] = None,
                temperature: float = 0.1, top_p: float = 0.1, **kwargs: Any) -> AssistantMessage:
        """Sync invoke - delegates to async"""
        return asyncio.run(self._ainvoke(model_name, messages, tools, temperature, top_p, **kwargs))

    async def _ainvoke(
        self,
        model_name: str,
        messages: List[Dict],
        tools: List[Dict] = None,
        temperature: float = 0.1,
        top_p: float = 0.1,
        **kwargs: Any
    ) -> AssistantMessage:
        """Async invoke - main LLM call implementation"""

        try:
            # Prepare messages
            processed_messages = self._prepare_messages(messages)

            # Build request params
            params = {
                "model": model_name or self.config.model_name,
                "messages": processed_messages,
                "temperature": temperature,
                "top_p": top_p if top_p != 1.0 else None,
                "max_tokens": self.config.max_tokens,
                "stream": False,
            }

            # Add tools if provided (convert from ToolInfo format to OpenAI format)
            if tools:
                params["tools"] = self._convert_tools_to_openai_format(tools)
                logger.debug(f"Added {len(params['tools'])} tools to API call")

            # Add provider preference if configured
            if self.config.openrouter_provider:
                params["extra_body"] = self._get_provider_config(self.config.openrouter_provider)

            # Make API call
            response = await self._client.chat.completions.create(**params)

            # Update token usage
            if response.usage:
                self._update_token_usage(response.usage)

            # Parse and return response
            return self._parse_response(response)

        except Exception as e:
            error_str = str(e)
            if any(phrase in error_str for phrase in [
                "Input is too long",
                "context limit",
                "maximum context length"
            ]):
                logger.error(f"Context limit exceeded: {error_str}")
                raise ContextLimitError(f"Context limit exceeded: {error_str}")

            logger.error(f"OpenRouter LLM call failed: {error_str}")
            raise

    def _stream(self, model_name: str, messages: List[Dict], tools: List[Dict] = None,
                temperature: float = 0.1, top_p: float = 0.1, **kwargs: Any) -> Iterator[AssistantMessageChunk]:
        """Sync stream - not implemented, delegates to async"""
        raise NotImplementedError("Use async streaming with _astream")

    async def _astream(
        self,
        model_name: str,
        messages: List[Dict],
        tools: List[Dict] = None,
        temperature: float = 0.1,
        top_p: float = 0.1,
        **kwargs: Any
    ) -> AsyncIterator[AssistantMessageChunk]:
        """Async streaming - for future implementation"""
        # For now, just yield a single chunk from invoke
        result = await self._ainvoke(model_name, messages, tools, temperature, top_p, **kwargs)
        yield AssistantMessageChunk(
            role="assistant",
            content=result.content,
            tool_calls=result.tool_calls
        )

    def _prepare_messages(self, messages: List[Dict]) -> List[Dict]:
        """Prepare messages with cache control if enabled"""
        if self.config.disable_cache_control:
            return messages

        return self._apply_cache_control(messages)

    def _apply_cache_control(self, messages: List[Dict]) -> List[Dict]:
        """Apply cache control to system and last user message"""
        cached_messages = []
        user_turns_processed = 0

        for turn in reversed(messages):
            if (turn["role"] == "user" and user_turns_processed < 1) or turn["role"] == "system":
                # Add ephemeral cache control to text content
                new_content = []
                processed_text = False

                if isinstance(turn.get("content"), list):
                    for item in turn["content"]:
                        if item.get("type") == "text" and len(item.get("text", "")) > 0 and not processed_text:
                            text_item = item.copy()
                            text_item["cache_control"] = {"type": "ephemeral"}
                            new_content.append(text_item)
                            processed_text = True
                        else:
                            new_content.append(item.copy())

                    cached_messages.append({"role": turn["role"], "content": new_content})
                else:
                    cached_messages.append(turn)

                if turn["role"] == "user":
                    user_turns_processed += 1
            else:
                cached_messages.append(turn)

        return list(reversed(cached_messages))

    def _convert_tools_to_openai_format(self, tools: List[Any]) -> List[Dict]:
        """
        Convert ToolInfo objects to OpenAI function calling format

        Args:
            tools: List of ToolInfo objects or dicts

        Returns:
            List of tools in OpenAI format
        """
        openai_tools = []

        for tool in tools:
            # Handle ToolInfo objects (they have a 'function' attribute)
            if hasattr(tool, 'function'):
                function_info = tool.function
                # Convert to dict if it's a pydantic model
                if hasattr(function_info, 'model_dump'):
                    function_dict = function_info.model_dump(exclude_none=True)
                elif hasattr(function_info, 'dict'):
                    function_dict = function_info.dict(exclude_none=True)
                else:
                    function_dict = function_info

                openai_tools.append({
                    "type": "function",
                    "function": function_dict
                })
            # Handle dict format (already in correct format)
            elif isinstance(tool, dict):
                if "type" in tool and "function" in tool:
                    openai_tools.append(tool)
                else:
                    # Assume it's just the function part
                    openai_tools.append({
                        "type": "function",
                        "function": tool
                    })

        return openai_tools

    def _get_provider_config(self, provider: str) -> Dict:
        """Get provider-specific configuration"""
        provider = provider.strip().lower()

        if provider == "google":
            return {
                "provider": {
                    "only": ["google-vertex/us", "google-vertex/europe", "google-vertex/global"]
                }
            }
        elif provider == "anthropic":
            return {"provider": {"only": ["anthropic"]}}
        elif provider == "amazon":
            return {"provider": {"only": ["amazon-bedrock"]}}
        else:
            return {}

    def _update_token_usage(self, usage_data):
        """Update cumulative token usage"""
        if not usage_data:
            return

        input_tokens = getattr(usage_data, "prompt_tokens", 0)
        output_tokens = getattr(usage_data, "completion_tokens", 0)

        prompt_tokens_details = getattr(usage_data, "prompt_tokens_details", None)
        cached_tokens = 0
        if prompt_tokens_details:
            cached_tokens = getattr(prompt_tokens_details, "cached_tokens", 0)

        self.last_call_tokens = {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens
        }

        self.token_usage.total_input_tokens += input_tokens
        self.token_usage.total_output_tokens += output_tokens
        self.token_usage.total_cache_read_input_tokens += cached_tokens

        logger.debug(
            f"Token usage - Input: {self.token_usage.total_input_tokens}, "
            f"Output: {self.token_usage.total_output_tokens}, "
            f"Cache: {self.token_usage.total_cache_read_input_tokens}"
        )

    def _parse_response(self, response) -> AssistantMessage:
        """Parse OpenRouter/OpenAI API response to AIMessage"""
        if not response or not response.choices:
            raise ValueError("LLM did not return a valid response")

        choice = response.choices[0]
        message = choice.message

        # Extract content
        content = message.content or ""

        # Extract tool calls (if any)
        tool_calls = None
        if hasattr(message, "tool_calls") and message.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    type=tc.type,
                    name=tc.function.name,
                    arguments=tc.function.arguments if tc.function.arguments is not None else "{}"
                )
                for tc in message.tool_calls
            ]

        # Create usage metadata
        usage_metadata = None
        if response.usage:
            usage_metadata = UsageMetadata(
                model_name=response.model,
                finish_reason=choice.finish_reason
            )

        return AssistantMessage(
            role="assistant",
            content=content,
            tool_calls=tool_calls,
            usage_metadata=usage_metadata
        )

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count using tiktoken"""
        if not self.encoding:
            try:
                self.encoding = tiktoken.get_encoding("o200k_base")
            except Exception:
                self.encoding = tiktoken.get_encoding("cl100k_base")

        try:
            return len(self.encoding.encode(text))
        except Exception:
            # Fallback: ~4 chars per token
            return len(text) // 4

    def ensure_summary_context(
        self,
        message_history: List[Dict],
        summary_prompt: str
    ) -> bool:
        """
        Check if current message_history + summary_prompt would exceed context limit.
        If yes, remove the last assistant-user pair and return False.
        Returns True if context is OK, False if messages were removed.
        """
        # Get last call token usage
        last_prompt_tokens = self.last_call_tokens.get("prompt_tokens", 0)
        last_completion_tokens = self.last_call_tokens.get("completion_tokens", 0)
        buffer_factor = 1.2

        # Estimate summary prompt tokens
        summary_tokens = self._estimate_tokens(summary_prompt) * buffer_factor

        # Estimate last user message tokens (if exists and not sent yet)
        last_user_tokens = 0
        if message_history and message_history[-1]["role"] == "user":
            content = message_history[-1]["content"]
            if isinstance(content, list):
                text = " ".join(item.get("text", "") for item in content if item.get("type") == "text")
            else:
                text = content
            last_user_tokens = self._estimate_tokens(text) * buffer_factor

        # Calculate total estimated tokens
        estimated_total = (
            last_prompt_tokens +
            last_completion_tokens +
            last_user_tokens +
            summary_tokens +
            self.config.max_tokens
        )

        if estimated_total >= self.config.max_context_length:
            logger.warning(
                f"Context + summary would exceed limit: {estimated_total} >= {self.config.max_context_length}"
            )

            # Remove last user message (tool results)
            if message_history and message_history[-1]["role"] == "user":
                message_history.pop()

            # Remove last assistant message (tool call request)
            if message_history and message_history[-1]["role"] == "assistant":
                message_history.pop()

            logger.info(f"Removed last assistant-user pair, current history length: {len(message_history)}")
            return False

        logger.debug(f"Context check passed: {estimated_total}/{self.config.max_context_length}")
        return True

    def get_token_usage(self) -> Dict[str, int]:
        """Get current cumulative token usage"""
        return self.token_usage.model_dump()

    def format_token_usage_summary(self) -> tuple[List[str], str]:
        """Format token usage statistics and cost estimation"""
        usage = self.token_usage

        total_input = usage.total_input_tokens
        total_output = usage.total_output_tokens
        cache_input = usage.total_cache_read_input_tokens

        # Calculate cost
        cost = (
            ((total_input - cache_input) / 1_000_000 * self.config.input_token_price) +
            (cache_input / 1_000_000 * self.config.cache_input_token_price) +
            (total_output / 1_000_000 * self.config.output_token_price)
        )

        summary_lines = [
            "\n" + "-" * 20 + " Token Usage & Cost " + "-" * 20,
            f"Total Input Tokens: {total_input}",
            f"Total Cache Input Tokens: {cache_input}",
            f"Total Output Tokens: {total_output}",
            "-" * 60,
            f"Input Token Price: ${self.config.input_token_price:.4f} USD",
            f"Output Token Price: ${self.config.output_token_price:.4f} USD",
            f"Cache Input Token Price: ${self.config.cache_input_token_price:.4f} USD",
            "-" * 60,
            f"Estimated Cost (with cache): ${cost:.4f} USD",
            "-" * 60
        ]

        log_string = (
            f"[OpenRouter/{self.config.model_name}] "
            f"Total Input: {total_input}, Cache Input: {cache_input}, "
            f"Output: {total_output}, Cost: ${cost:.4f} USD"
        )

        return summary_lines, log_string
