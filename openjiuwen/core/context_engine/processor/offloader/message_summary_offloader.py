# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
from typing import Literal, Any

from pydantic import BaseModel, Field

from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.foundation.llm import (
    BaseMessage, SystemMessage, UserMessage,
    ModelRequestConfig, ModelClientConfig, Model
)
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.context_engine.processor.offloader.message_offloader import MessageOffloader
from openjiuwen.core.context_engine.processor.base import ContextEvent
from openjiuwen.core.context_engine.schema.messages import OffloadMixin

# Keywords used to detect "context overflow" errors from LLM responses
# Different providers use different error formats, so we use string matching for unified detection
# Examples: OpenAI says "maximum context length", Anthropic says "prompt is too long"
CONTEXT_OVERFLOW_KEYWORDS = (
    "context length",  # Context length (common in OpenAI/Azure)
    "token limit",  # Token limit
    "too long",  # Too long (common in Anthropic)
    "exceeds",  # Exceeds
    "maximum context",  # Maximum context
    "context window",  # Context window (common in local models)
)

TRUNCATED_MARKER = "...[TRUNCATED]..."

ADAPTIVE_OFFLOAD_PROMPT_TEMPLATE = """\
# Adaptive Information Compression Expert

## Core Role
You are an adaptive information compression expert in a React Agent. Your task is to intelligently analyze the information density and structural characteristics of tool return content, automatically select the most suitable compression strategy, generate an optimal condensed text, and offload detailed content to the file system for on-demand loading.

## Constraints
- **Strictly prohibited from executing the step**: You are only responsible for compression; you must not execute any steps, calculations, or operations from the step.
- **Based solely on provided information**: Only use the information in {tool_content} for compression.
- **No speculative operations**: Do not perform additional queries, calculations, or analysis based on step content.

# Compression Logic Flow

## Step 1: Analyze User Intent
- **Tool Purpose**: Understand the core purpose of this tool call (e.g., querying information, performing calculations, obtaining status).
- **Key Parameters**: What parameters were passed in the function_call? This directly indicates the focus of required information.
- **Role in the step**: What subtasks in the current step is this tool call meant to accomplish?

## Step 2: Select Compression Strategy
Based on the analyzed user intent, quickly scan the important information in tool_content:

### Characteristics favoring EXTRACTIVE compression:
- **Clear and direct results**: Key information related to user intent is explicitly present in the tool return results.
- **No deep processing needed**: The answer already exists directly in the return content; it only needs to be "extracted" to satisfy user intent without summarization or reasoning.
- **Clear structure**: For example, batches of key information, attribute lists, keyword collections, address details, etc.

### Characteristics favoring ABSTRACTIVE compression:
- **Requires integration and understanding**: To obtain an answer that matches user intent, it is necessary to summarize and synthesize multiple paragraphs, viewpoints, or data.
- **Highly narrative**: For example, long analytical reports, article content, Q&A responses, log analysis, etc.

## Step 3: Execute Compression Strategy
Based on the above evaluation, select a compression strategy according to the following process:

### **If EXTRACTIVE compression was selected in the previous step**:
Analyze `tool_content` and perform the following operations:
- **Identify core information**: Find sentences and key data that directly answer the calling intent.
- **Execute extractive compression**:
  - **RETAIN**: All original sentences or phrases that directly contain core answers, key facts, final results, main status, and necessary definitions. Prefer not to rewrite; use original expressions when possible.
  - **DELETE**:
    - Background introductions and process descriptions unrelated to the core answer.
    - Sentences that express the same meaning repeatedly.
    - Overly detailed examples and explanatory expansions (if their main points are already covered).
    - Pure formatting metadata, internal log information, redundant transitional statements.
- **Ensure coherence**: Connect the retained original sentences or fragments in a logically clear way to form coherent key information.

### **If ABSTRACTIVE compression was selected in the previous step**:
Compress the tool message content to generate a **high-density, high-integrity** summary that can adequately support the current `step`'s task needs without loading the original text.

**Summary requirements:**
- **Integrity priority**: The summary should retain **all key facts, data, conclusions, conditions, and limitations** related to the current `step` from the original text. Do not omit information that substantially impacts understanding or decision-making.
- **Strict accuracy**: All data, names, relationships, and judgments must be strictly accurate; do not distort, blur, or simplify to the point of potential misunderstanding.
- **Focus and conciseness**: Center around the `step` requirements; organize in concise, clear language; remove redundant descriptions, repetitive examples, and irrelevant background buildup, but **do not oversimplify core information**.
- **Clear structure**: Maintain logical coherence; reasonably segment or bulletize to ensure clear information hierarchy and easy reading comprehension.
- **Objective neutrality**: Make only factual statements; do not add explanations, evaluations, or speculations not present in the original text.

【Current step requirements】
{step}

【Current tool call function call】
{function_call}

【Tool message content begins】
{tool_content}
【Tool message content ends】

Return JSON with this schema:
{output_json_schema}
"""

OUTPUT_JSON_SCHEMA = """\
{{
  "compression_strategy": "extractive" | "abstractive",
  "summary": "A compact result generated based on the selected strategy (within {summary_max_tokens} tokens). If using extractive strategy, directly concatenate key original text; if using abstractive strategy, provide a condensed summary. Ensure it contains all key information needed for the step, with clear structure and appropriate length.",
  "offload_data_explanation": {{
    "category": "The category of information offloaded (e.g., 'raw log data', 'complete product list', 'detailed calculation steps')",
    "description": "Briefly describe what detailed information is missing from the compressed text and its potential use cases, for subsequent on-demand loading of these offloaded information.",
    "inferability": "high" | "medium" | "low" // Based on the current compressed text, how easily can the deleted details be inferred:
      // - high: The compressed text already contains core logic or conclusions; details can be reasonably inferred.
      // - medium: The compressed text provides a summary, but exact values or complete lists are unknown.
      // - low: The compressed text does not cover this detail at all, or the detail is unique/complex and cannot be inferred.
  }}
}}
"""

STEP_SUMMARY_PROMPT = """\
Summarize the current user task in one concise sentence.
Return the task only.

Conversation context:
{context}
"""

# Default prompt for non-adaptive (simple LLM summary) mode
# Used when enable_adaptive_compression=False
DEFAULT_OFFLOAD_SUMMARY_PROMPT: str = \
    """
    You are a "high-density summarizer".
    Your task is to shrink the overly long message below into 2–4 concise sentences that:
    Contain ≤ 15 % of the original token count;
    Keep all critical facts, figures, conclusions, requests or decisions verbatim;
    Remove greetings, repetition, filler, examples, jokes, and ornamental language;
    Speak in neutral, third-person style;
    Do NOT explain, comment, or add extra information—output the summary only.
    Begin:
    """


class MessageSummaryOffloaderConfig(BaseModel):
    """
    Configuration for MessageSummaryOffloader.

    Extends MessageOffloaderConfig with adaptive compression capabilities.
    When adaptive mode is enabled, uses LLM to generate context-aware summaries.
    When disabled, falls back to simple LLM-based summary.

    **Evaluation order (highest → lowest priority):**
    1. `messages_to_keep` – newest N messages are **immune** to off-loading.
    2. `keep_last_round` – the latest **user + assistant** turn is **always** kept.
    3. `messages_threshold` – total message **count** trigger.
    4. `tokens_threshold` – total **token** trigger (checked after every append).
    5. `large_message_threshold` – **per-message** token size; larger messages are
       **preferentially** selected for compression.

    Only roles listed in `offload_message_type` are eligible; others are **never** touched.
    """

    # -------------------------------------------------------------------------
    # Parameters retained from the original offloader / summary-offloader family
    # -------------------------------------------------------------------------

    messages_threshold: int | None = Field(default=None, gt=0)
    """Hard ceiling on **message count**.  Exceeding it starts off-loading."""

    tokens_threshold: int = Field(default=20000, gt=0)
    """Hard ceiling on **accumulated tokens** (tokenizer-dependent).  Checked after each append."""

    large_message_threshold: int = Field(default=1000, gt=0)
    """
    Token length above which a single message is labelled *large* and 
    becomes a **preferred** compression candidate.
    """

    offload_message_type: list[Literal["user", "assistant", "tool"]] = Field(default=["tool"])
    """White-list of **roles** that may be compressed or off-loaded.  Roles absent here are **protected**."""

    protected_tool_names: list[str] = Field(default=["reload_original_context_messages"])
    """Tool messages produced by these tools are always kept in full and never offloaded."""

    messages_to_keep: int | None = Field(default=None, gt=0)
    """Guarantee that the **newest** *N* messages are **never** off-loaded."""

    keep_last_round: bool = Field(default=True)
    """If *True*, the **latest user–assistant round** (two messages) is **immune** to any off-loading."""

    model: ModelRequestConfig | None = Field(default=None)
    """Supplies **tokenizer** and **context-window** limits. If omitted, conservative fall-backs are used."""

    model_client: ModelClientConfig | None = Field(default=None)
    """
    Optional **client-level** settings (endpoint, timeout, retry, headers) 
    for the model that **performs** the summary/compression.
    """

    customized_summary_prompt: str | None = Field(default=None)
    """User-supplied **prompt** for the summary model.  If *None*, a built-in prompt is used."""

    # -------------------------------------------------------------------------
    # Adaptive compression specific options
    # -------------------------------------------------------------------------

    enable_adaptive_compression: bool = Field(default=False)
    """Whether to enable **adaptive** compression mode.

    When enabled, uses LLM to generate intelligent summaries (extractive or abstractive)
    based on current task context. When disabled, falls back to the parent class behavior
    which uses a fixed summary prompt.
    """

    summary_max_tokens: int = Field(default=1000, gt=0)
    """Maximum tokens allowed in the generated summary.

    The LLM will attempt to compress the message within this limit.
    Smaller values save more context space but may lose more information.
    """

    enable_precise_step: bool = Field(default=False)
    """Whether to use LLM to precisely extract the current task/step from context.

    When enabled, calls LLM to summarize "what the user wants to do" from recent messages.
    When disabled, uses a heuristic (last user message) as the task description.
    Enabling this improves summary relevance but adds one extra LLM call per offloading.
    """

    step_summary_max_context_messages: int = Field(default=10, gt=0)
    """Number of recent messages to consider when extracting current task (if enable_precise_step=True).

    More messages give better context but increase the chance of hitting context limits.
    Fallback logic will retry with fewer messages if overflow errors occur.
    """

    content_max_chars_for_compression: int = Field(default=100000, gt=0)
    """Maximum character length of tool output that will be sent to LLM for compression.

    If tool output exceeds this, it will be smartly truncated before compression.
    This prevents "prompt too long" errors when dealing with extremely large tool outputs.
    The fallback logic tries: full → truncated → further halved if still too long.
    """


@ContextEngine.register_processor()
class MessageSummaryOffloader(MessageOffloader):
    """
    Message offloader with intelligent compression capabilities.

    **When enable_adaptive_compression=True:**
    - Per-message triggering: triggers when individual messages exceed threshold
    - Task-aware compression: considers current task context when summarizing
    - Fallback mechanisms: retries with truncated content if LLM context overflows

    **When enable_adaptive_compression=False (default):**
    - Uses simple LLM summary without task context
    """

    def __init__(self, config: MessageSummaryOffloaderConfig):
        """Initialize with config and set up the LLM model."""
        self._adaptive_config = config
        super().__init__(config)
        self._model = Model(
            model_client_config=self.config.model_client,
            model_config=self.config.model,
        )

    @property
    def config(self) -> MessageSummaryOffloaderConfig:
        """Return the configuration (shadowing parent's config)."""
        return self._adaptive_config

    async def trigger_add_messages(
            self,
            context: ModelContext,
            messages_to_add: list[BaseMessage],
            **kwargs: Any,
    ) -> bool:
        """Determine whether offloading should be triggered.

        **Behavior:**
        - When adaptive mode enabled: checks if ANY new message exceeds large_message_threshold
        - When disabled: delegates to parent's token-based triggering logic

        Args:
            context: Current conversation context.
            messages_to_add: New messages being added.

        Returns:
            True if offloading should be triggered.
        """
        if not self.config.enable_adaptive_compression:
            return await super().trigger_add_messages(context, messages_to_add, **kwargs)
        context_messages = context.get_messages() + messages_to_add
        return any(self._should_offload_message(message, context, context_messages) for message in messages_to_add)

    async def on_add_messages(
            self,
            context: ModelContext,
            messages_to_add: list[BaseMessage],
            **kwargs: Any,
    ) -> tuple[ContextEvent | None, list[BaseMessage]]:
        """Process messages and offload large ones.

        **Behavior:**
        - When adaptive mode enabled: only processes newly added messages, not entire context
        - When disabled: delegates to parent's full-context scanning logic

        Args:
            context: Current conversation context.
            messages_to_add: New messages being added.

        Returns:
            Tuple of (event with modified indices, processed messages).
        """
        if not self.config.enable_adaptive_compression:
            return await super().on_add_messages(context, messages_to_add, **kwargs)

        processed_messages = list(messages_to_add)
        event = ContextEvent(event_type=self.processor_type())
        base_index = len(context)

        for index, message in enumerate(messages_to_add):
            if not self._should_offload_message(message, context, context.get_messages() + messages_to_add):
                continue
            processed_messages[index] = await self._offload_message_adaptive(message, context)
            event.messages_to_modify.append(base_index + index)

        if not event.messages_to_modify:
            return None, messages_to_add
        return event, processed_messages

    async def _offload_message(self, message: BaseMessage, context: ModelContext) -> BaseMessage:
        """Route to appropriate offload method based on configuration.

        **Behavior:**
        - Routes to adaptive or simple summary based on enable_adaptive_compression flag
        - Parent uses simple truncation; this class uses LLM-based summarization

        Args:
            message: The message to offload.
            context: Current conversation context.

        Returns:
            The offloaded (summarized) message.
        """
        if not self.config.enable_adaptive_compression:
            return await self._offload_message_simple_summary(message, context)
        return await self._offload_message_adaptive(message, context)

    async def _offload_message_simple_summary(
            self, message: BaseMessage, context: ModelContext
    ) -> BaseMessage:
        """Generate a simple LLM summary for the message (non-adaptive mode).

        This replicates the original simple summary behavior:
        uses a fixed system prompt to summarize the message content.

        Args:
            message: The message to summarize.
            context: Current conversation context.

        Returns:
            The summarized message with offload metadata.
        """
        prompt = self.config.customized_summary_prompt or DEFAULT_OFFLOAD_SUMMARY_PROMPT
        system_message = SystemMessage(content=prompt)
        response = await self._model.invoke(
            [
                system_message,
                UserMessage(content=message.content)
            ]
        )
        summarized_content = response.content
        extra_fields = message.model_dump()
        extra_fields.pop("role", None)
        extra_fields.pop("content", None)
        offload_message = await self.offload_messages(
            role=message.role,
            content=summarized_content,
            messages=[message],
            context=context,
            **extra_fields
        )
        return offload_message

    async def _offload_message_adaptive(self, message: BaseMessage, context: ModelContext) -> BaseMessage:
        """Generate task-aware adaptive summary for the message.

        **When adaptive mode is enabled:**
        - Extracts function call information from context chain
        - Identifies current task/step from conversation
        - Uses structured JSON output with compression strategy metadata
        - Includes fallback mechanisms for context overflow

        Args:
            message: The tool message to offload.
            context: Current conversation context.

        Returns:
            The adaptively summarized message with rich metadata.
        """
        context_messages = context.get_messages()
        function_call = self._get_function_call_from_chain(message, context_messages)

        if self.config.enable_precise_step:
            step = await self._get_step_from_chain_precise(context_messages + [message])
            if step == "":
                step = self._get_step_from_chain_default(context_messages)
        else:
            step = self._get_step_from_chain_default(context_messages)

        if isinstance(message.content, str):
            tool_content = message.content
        else:
            try:
                tool_content = json.dumps(message.content, ensure_ascii=False)
            except TypeError:
                tool_content = str(message.content)
        compression_result = await self._compress_with_fallback(
            step=step,
            function_call=function_call,
            tool_content=tool_content,
        )
        if compression_result is None:
            return message

        summary = compression_result.get("summary", "")
        final_content = summary
        offload_data_explanation = compression_result.get("offload_data_explanation") or {}
        if offload_data_explanation:
            explanation_lines = [
                "[offloaded_info]",
                f"category: {offload_data_explanation.get('category', '')}",
                f"description: {offload_data_explanation.get('description', '')}",
                f"inferability: {offload_data_explanation.get('inferability', '')}",
            ]
            final_content = f"{summary}\n\n" + "\n".join(explanation_lines)

        extra_fields = message.model_dump()
        extra_fields.pop("role", None)
        extra_fields.pop("content", None)

        return await self.offload_messages(
            role=message.role,
            content=final_content,
            messages=[message],
            context=context,
            **extra_fields,
        )

    def _should_offload_message(
            self,
            message: BaseMessage,
            context: ModelContext,
            context_messages: list[BaseMessage] | None = None,
    ) -> bool:
        """Check if a message should be offloaded.

        **When adaptive mode is enabled:**
        - Only processes tool messages (not user/assistant)
        - Uses token count (if available) or character count
        - Skips messages already marked as offloaded (OffloadMixin)

        Args:
            message: The message to check.
            context: Current conversation context.

        Returns:
            True if the message should be offloaded.
        """
        if not self.config.enable_adaptive_compression:
            return super()._should_offload_message(
                message=message,
                context_messages=context_messages or context.get_messages(),
                context=context,
            )
        if message.role != "tool":
            return False
        if isinstance(message, OffloadMixin):
            return False
        if context_messages is None:
            context_messages = context.get_messages()
        if self._is_protected_tool_message(message, context_messages):
            return False
        length = self._message_size(message, context)
        return length > self.config.large_message_threshold

    def _message_size(self, message: BaseMessage, context: ModelContext) -> int:
        """Calculate the size of a message for threshold comparison.

        **When adaptive mode is enabled:**
        - Prioritizes token count via context's token_counter
        - Falls back to character count / 3 as an approximation of token count
          (3 is a rough average: English ~4 chars/token, Chinese ~1-2 chars/token)

        Args:
            message: The message to measure.
            context: Current conversation context.

        Returns:
            Token count (preferred) or approximate token count based on character length.
        """
        token_counter = context.token_counter()
        if token_counter is not None:
            return token_counter.count_messages([message])
        if isinstance(message.content, str):
            return len(message.content) // 3
        try:
            return len(json.dumps(message.content, ensure_ascii=False)) // 3
        except TypeError:
            return len(str(message.content)) // 3

    def _get_function_call_from_chain(
            self,
            tool_message: BaseMessage,
            context_messages: list[BaseMessage],
    ) -> Any:
        """Extract the function call that triggered this tool message.

        **When adaptive mode is enabled:**
        - Walks backwards through context to find matching assistant message
        - Correlates tool_call_id with the original function call
        - Used to provide context for intelligent summarization

        Args:
            tool_message: The tool response message.
            context_messages: All messages in the conversation context.

        Returns:
            The matched raw tool_call object/dict, or None if not found.
        """
        for message in reversed(context_messages):
            if message.role != "assistant":
                continue
            tool_calls = getattr(message, "tool_calls", None) or []
            for tool_call in tool_calls:
                if ContextUtils.tool_call_matches_id(tool_call, getattr(tool_message, "tool_call_id", None)):
                    return tool_call
        return None

    def _get_step_from_chain_default(
            self,
            context_messages: list[BaseMessage],
    ) -> str:
        """Extract current task using heuristic (last user message).

        **When adaptive mode is enabled:**
        - Simple heuristic: find the most recent user message
        - Used when enable_precise_step=False (default)
        - Faster than LLM-based extraction but less context-aware

        Args:
            context_messages: All messages in the conversation context.

        Returns:
            The last user message content as the task description.
        """
        for message in reversed(context_messages):
            if message.role == "user":
                if isinstance(message.content, str):
                    return message.content
                try:
                    return json.dumps(message.content, ensure_ascii=False)
                except TypeError:
                    return str(message.content)
        return ""

    async def _get_step_from_chain_precise(self, context_messages: list[BaseMessage]) -> str:
        """Extract current task using LLM (more accurate but slower).

        **When adaptive mode is enabled:**
        - Uses LLM to understand the current task from conversation
        - Includes fallback: reduces context if LLM context overflow
        - Used when enable_precise_step=True

        Args:
            context_messages: All messages in the conversation context.

        Returns:
            LLM-generated task summary.

        Raises:
            ContextExecutionError: if fails after all retries.
        """
        messages_to_use = self._select_messages_for_step_summary(context_messages)

        if messages_to_use == "":
            return ""

        max_retries = 3

        for attempt in range(max_retries):
            try:
                context_text = "\n\n".join(
                    f"[{message.role}] "
                    f"{(message.content if isinstance(message.content, str) else str(message.content))[:2000]}"
                    for message in messages_to_use
                )
                response = await self._model.invoke(
                    [UserMessage(content=STEP_SUMMARY_PROMPT.format(context=context_text))]
                )
                if isinstance(response.content, str):
                    return response.content.strip()
                try:
                    return json.dumps(response.content, ensure_ascii=False).strip()
                except TypeError:
                    return str(response.content).strip()
            except Exception as exc:
                if not self._is_context_overflow_error(exc):
                    raise
                if attempt >= max_retries - 1 or len(messages_to_use) <= 2:
                    raise build_error(
                        StatusCode.CONTEXT_EXECUTION_ERROR,
                        error_msg=f"Failed to generate precise step summary after {max_retries} attempts: {exc}",
                    ) from exc
                messages_to_use = messages_to_use[2:]
        return ""

    @staticmethod
    def _is_valid_for_step_summary(msg: BaseMessage) -> bool:
        """Check if message is valid for step summary extraction.

        Valid messages are:
        - User messages
        - Assistant messages without tool_calls

        Args:
            msg: Message to check.

        Returns:
            True if message should be included in step summary context.
        """
        if msg.role == "user":
            return True
        return msg.role == "assistant" and not getattr(msg, "tool_calls", None)

    def _select_messages_for_step_summary(self, context_messages: list[BaseMessage]) -> list[BaseMessage] | str:
        """Select recent messages for step extraction.

        **Filtering rules (when adaptive mode is enabled):**
        - Only keep messages with role == 'user'
        - Only keep messages with role == 'assistant' and no tool_calls (pure conversational responses)
        - Excluded: ToolMessage, AssistantMessage with tool_calls

        **Fallback logic:**
        - If filtered message count <= 1, skip precise step and use fallback
        - Returns empty string (""), caller should use _get_step_from_chain_default

        Args:
            context_messages: All messages in the conversation context.

        Returns:
            Subset of messages for step extraction, filtered by type.
            Returns empty string ("") when filtered count <= 1 to trigger fallback.
        """
        filtered = [msg for msg in context_messages if self._is_valid_for_step_summary(msg)]

        if len(filtered) <= 1:
            return ""

        max_messages = self.config.step_summary_max_context_messages
        if len(filtered) <= max_messages:
            return list(filtered)
        return list(filtered[-max_messages:])

    async def _compress_with_fallback(
            self,
            step: str,
            function_call: Any,
            tool_content: str,
    ) -> dict[str, Any] | None:
        """Compress tool content with fallback on context overflow.

        **When adaptive mode is enabled:**
        - Builds multiple content attempts (full, truncated, halved)
        - Retries with shorter content if LLM context overflows
        - Returns structured JSON with compression results

        Args:
            step: Current task description.
            function_call: Function call that triggered this tool response.
            tool_content: The tool output to compress.

        Returns:
            Dict with summary and offload_data_explanation.
            Returns None when compression should be abandoned and the original
            message should be kept unchanged.

        Raises:
            ContextExecutionError: if all compression attempts fail.
        """
        attempts = self._build_compression_attempts(tool_content)

        for index, content_to_compress in enumerate(attempts, start=1):
            try:
                prompt = self._build_compression_prompt(step, function_call, content_to_compress)
                response = await self._model.invoke([UserMessage(content=prompt)])
                if isinstance(response.content, str):
                    response_content = response.content
                else:
                    try:
                        response_content = json.dumps(response.content, ensure_ascii=False)
                    except TypeError:
                        response_content = str(response.content)
                try:
                    return self._parse_compression_result(response_content)
                except Exception:
                    if len(response_content) >= len(tool_content):
                        return None
                    return {
                        "summary": response_content,
                        "offload_data_explanation": {},
                    }
            except Exception as exc:
                if not self._is_context_overflow_error(exc):
                    raise
                if index >= len(attempts):
                    raise build_error(
                        StatusCode.CONTEXT_EXECUTION_ERROR,
                        error_msg=f"Failed to compress message after {len(attempts)} attempts: {exc}",
                    ) from exc
        return {}

    def _build_compression_attempts(self, tool_content: str) -> list[str]:
        """Build fallback attempts for compression.

        **When adaptive mode is enabled:**
        - Attempt 1: Full content
        - Attempt 2: Smart truncated to content_max_chars_for_compression
        - Attempt 3: Halved limit if still too long

        Args:
            tool_content: The original tool output.

        Returns:
            List of content strings to try in order.
        """
        attempts = [tool_content]
        max_chars = self.config.content_max_chars_for_compression
        if len(tool_content) <= max_chars:
            return attempts

        attempts.append(self._smart_truncate_content(tool_content, max_chars))
        reduced_limit = max(max_chars // 2, 1)
        if reduced_limit < max_chars:
            attempts.append(self._smart_truncate_content(tool_content, reduced_limit))
        return attempts

    def _smart_truncate_content(self, content: str, max_chars: int) -> str:
        """Smartly truncate content preserving head, middle, and tail sections.

        **When adaptive mode is enabled:**
        - Keeps beginning (33%), middle (33%), and end (33%) of content
        - Marks truncated sections with TRUNCATED_MARKER
        - Better than simple truncation for LLM comprehension

        Args:
            content: The content to truncate.
            max_chars: Maximum characters to keep (including markers).

        Returns:
            Truncated content with head, middle, and tail preserved.
        """
        if len(content) <= max_chars:
            return content
        joiner_overhead = 4
        if max_chars <= len(TRUNCATED_MARKER) * 2 + joiner_overhead + 3:
            return content[:max_chars]

        available_chars = max_chars - len(TRUNCATED_MARKER) * 2 - joiner_overhead
        head_chars = max(available_chars // 3, 1)
        tail_chars = max(available_chars // 3, 1)
        middle_chars = max(available_chars - head_chars - tail_chars, 1)

        center = len(content) // 2
        middle_start = max(center - middle_chars // 2, head_chars)
        middle_end = min(middle_start + middle_chars, len(content) - tail_chars)
        middle_start = max(head_chars, middle_end - middle_chars)

        head = content[:head_chars]
        middle = content[middle_start:middle_end]
        tail = content[-tail_chars:]
        return f"{head}\n{TRUNCATED_MARKER}\n{middle}\n{TRUNCATED_MARKER}\n{tail}"

    def _build_compression_prompt(
            self,
            step: str,
            function_call: Any,
            tool_content: str,
    ) -> str:
        """Build the prompt for adaptive compression.

        **When adaptive mode is enabled:**
        - Includes current task context (step)
        - Includes function call details
        - Specifies JSON output schema for structured results

        Args:
            step: Current task description.
            function_call: Function call that triggered this tool response.
            tool_content: The tool output to compress.

        Returns:
            Formatted prompt string for LLM.
        """
        if function_call is None:
            function_call_text = "N/A"
        elif isinstance(function_call, str):
            function_call_text = function_call
        else:
            try:
                function_call_text = json.dumps(function_call, ensure_ascii=False)
            except TypeError:
                function_call_text = str(function_call)
        output_schema = OUTPUT_JSON_SCHEMA.format(summary_max_tokens=self.config.summary_max_tokens)
        return ADAPTIVE_OFFLOAD_PROMPT_TEMPLATE.format(
            step=step or "N/A",
            function_call=function_call_text,
            tool_content=tool_content,
            output_json_schema=output_schema,
        )

    def _parse_compression_result(self, response_content: str) -> dict[str, Any]:
        """Parse LLM compression response into structured dict.

        **When adaptive mode is enabled:**
        - Extracts JSON from LLM response (handles markdown wrapping)
        - Validates required 'summary' field
        - Returns structured dict with summary and offload_data_explanation

        Args:
            response_content: Raw LLM response string.

        Returns:
            Parsed dictionary with compression results.

        Raises:
            ContextExecutionError: if JSON parsing fails or summary missing.
        """
        try:
            result = json.loads(response_content.strip())
        except json.JSONDecodeError as original_exc:
            json_start = response_content.find("{")
            json_end = response_content.rfind("}")
            if json_start < 0 or json_end <= json_start:
                raise build_error(
                    StatusCode.CONTEXT_EXECUTION_ERROR,
                    error_msg=f"No JSON found in compression result: {response_content[:200]}",
                ) from original_exc
            try:
                result = json.loads(response_content[json_start:json_end + 1])
            except json.JSONDecodeError as exc:
                raise build_error(
                    StatusCode.CONTEXT_EXECUTION_ERROR,
                    error_msg=f"Failed to parse compression result as JSON: {response_content[:200]}",
                ) from exc

        if "summary" not in result:
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR,
                error_msg="Missing 'summary' field in compression result",
            )
        return result

    def _is_context_overflow_error(self, exc: Exception) -> bool:
        """Detect if an exception is caused by LLM context overflow.

        Different model providers report this error differently (some throw exceptions,
        some return HTTP errors, some return error messages directly). We use
        "keyword containment in string" approach for detection. While not precise,
        it covers the vast majority of real-world scenarios.

        Args:
            exc: The caught exception.

        Returns:
            True - Likely a context overflow error, should attempt fallback retry.
            False - Other types of errors, should be raised directly.
        """
        error_message = str(exc).lower()
        return any(keyword in error_message for keyword in CONTEXT_OVERFLOW_KEYWORDS)

    def _validate_config(self):
        """Validate configuration.

        **Behavior:**
        - Skips MessageOffloader trim_size validation because this class no longer
          uses truncation-based offload
        - Keeps only the validation rules that still make sense
        """
        if not getattr(self, "_config", None):
            return
        if (
                self.config.messages_to_keep
                and self.config.messages_threshold
                and self.config.messages_to_keep >= self.config.messages_threshold
        ):
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR,
                error_msg=f"messages_to_keep {self.config.messages_to_keep} cannot larger than "
                          f"messages_threshold {self.config.messages_threshold}"
            )
