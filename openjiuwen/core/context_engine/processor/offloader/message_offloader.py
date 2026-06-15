# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import fnmatch
import json
import os
import re
import uuid
from typing import List, Dict, Any, Literal, Tuple, Optional
from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.processor.base import ContextProcessor, ContextEvent
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.processor.offloader.rules import RuleCompressionPipeline
from openjiuwen.core.context_engine.schema.messages import OffloadMixin
from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage


class MessageOffloaderConfig(BaseModel):
    """
    Configuration for the MessageOffloader ContextProcessor.

    The offloader keeps the conversation history within safe memory/token limits
    by trimming or offloading messages once the configured thresholds are exceeded.
    Rules are evaluated in the following order:

    1. messages_to_keep: the most recent N messages are always retained.
    2. messages_threshold: when total message count exceeds this value offloading
       is triggered.
    3. tokens_threshold: when accumulated token count exceeds this value
       offloading is triggered.

    Only messages whose role appears in `offload_message_type` and whose token
    length is greater than `large_message_threshold` are eligible for offloading.
    The last user-assistant round can be preserved independently of the above
    rules by setting `keep_last_round=True`.
    """

    messages_threshold: int | None = Field(default=None, gt=0)
    """Maximum number of messages allowed in memory before offloading is triggered."""

    tokens_threshold: int = Field(default=20000, gt=0)
    """Maximum accumulated token count before offloading is triggered."""

    large_message_threshold: int = Field(default=1000, gt=0)
    """Messages whose token count exceeds this value are considered 'large' and may be offloaded."""

    offload_message_type: list[Literal["user", "assistant", "tool"]] = Field(default=["tool"])
    """Roles eligible for offloading. Messages whose role is not in this list are always kept."""

    protected_tool_names: list[str] = Field(default=["reload_original_context_messages"])
    """Tool messages produced by these tools are never offloaded, even if they are large."""

    trim_size: int = Field(default=100, gt=0)
    """Number of tokens to retain when a message is offloaded. The remainder is replaced with an omission marker."""

    messages_to_keep: int | None = Field(default=None, gt=0)
    """Guaranteed number of most-recent messages to retain, regardless of any other threshold."""

    keep_last_round: bool = Field(default=True)
    """If True, the most recent user-assistant round is always preserved even if it would otherwise be offloaded."""

    rule_compression_ratio: float = Field(default=0.2, gt=0.0, lt=1.0)
    """Single-message threshold ratio for deterministic rule compression."""

    rule_compression_expired_ratio: float = Field(default=0.1, gt=0.0, lt=1.0)
    """Stricter threshold ratio used when TTL-expired rule-compressed messages are reprocessed."""

    rule_compression_context_window_tokens: int | None = Field(default=None, gt=0)
    """Optional context window token budget used to derive the 20% rule-compression threshold."""

    rule_compression_ttl_seconds: int = Field(default=300, ge=0)
    """Minimum interval before the same message may be re-evaluated by the rule pipeline."""

    rule_compression_ttl_keep_recent_messages: int = Field(default=8, ge=0)
    """Number of most-recent messages skipped by TTL rule-compression sweeps."""

    rule_truncate_head_tokens: int = Field(default=2000, gt=0)
    """Approximate head tokens retained when rule-compressed content remains oversized."""

    rule_truncate_tail_tokens: int = Field(default=2000, gt=0)
    """Approximate tail tokens retained when rule-compressed content remains oversized."""


OMIT_STRING = "..."


@ContextEngine.register_processor()
class MessageOffloader(ContextProcessor):
    def __init__(self, config: MessageOffloaderConfig):
        super().__init__(config)
        self._validate_config()
        self._rule_pipeline = RuleCompressionPipeline()
        self._last_rule_compression_ttl_sweep_at_by_context: dict[str, float] = {}
        self._pending_rule_compression_ttl_sweep_contexts: set[str] = set()

    async def on_add_messages(
            self,
            context: ModelContext,
            messages_to_add: List[BaseMessage],
            **kwargs
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        context_messages = context.get_messages() + messages_to_add
        context_size = len(context)
        event, processed_messages = await self._offload_large_messages(context_messages, context, **kwargs)
        context_messages, messages_to_add = (
            processed_messages[:context_size],
            processed_messages[context_size:]
        )
        context.set_messages(context_messages)
        return event, messages_to_add

    async def trigger_add_messages(
            self,
            context: ModelContext,
            messages_to_add: List[BaseMessage],
            **kwargs
    ) -> bool:
        config = self.config
        all_messages = context.get_messages() + messages_to_add
        if self._has_rule_compression_candidate(messages_to_add, context):
            return True
        if self._should_trigger_context_ttl_sweep(context):
            return True
        message_size = len(all_messages)
        if config.messages_to_keep and message_size <= config.messages_to_keep:
            return False

        if config.messages_threshold and message_size > config.messages_threshold:
            if not self._has_offload_candidate(all_messages, context):
                return False
            logger.info(f"[{self.processor_type()} triggered] context messages num {message_size} "
                        f"exceeds threshold of {config.messages_threshold}")
            return True

        token_counter = context.token_counter()
        tokens = 0
        if token_counter:
            context_token = token_counter.count_messages(context.get_messages())
            messages_to_add_token = token_counter.count_messages(messages_to_add)
            tokens = messages_to_add_token + context_token
        if tokens > config.tokens_threshold:
            if not self._has_offload_candidate(all_messages, context):
                return False
            logger.info(f"[{self.processor_type()} triggered] context tokens {tokens} "
                        f"exceeds threshold of {config.tokens_threshold}")
            return True
        return False

    def _has_rule_compression_candidate(self, messages: List[BaseMessage], context: ModelContext) -> bool:
        return self._rule_pipeline.has_candidate(messages, context, self.config)

    async def _offload_large_messages(
            self,
            messages: List[BaseMessage],
            context: ModelContext,
            **kwargs
    ) -> Tuple[ContextEvent, List[BaseMessage]]:
        processed_messages = messages[:]
        offload_range = self._get_offload_range(messages)

        event = ContextEvent(event_type=self.processor_type())
        ttl_sweep_active = self._consume_context_ttl_sweep(context)
        for idx, msg in enumerate(processed_messages):
            if not self._should_process_ttl_message(idx, msg, processed_messages, ttl_sweep_active):
                continue
            replacement = await self._process_ttl_message(msg, context)
            if replacement is None:
                continue
            processed_messages = ContextUtils.replace_messages(processed_messages, [replacement], idx, idx)
            event.messages_to_modify.append(idx)
        if ttl_sweep_active:
            self._mark_context_ttl_sweep_complete(context)

        for idx in range(offload_range - 1, -1, -1):
            msg = processed_messages[idx]
            if not self._should_consider_message(msg, processed_messages):
                continue
            replacement = await self._process_large_message(msg, processed_messages, context, **kwargs)
            if replacement is None:
                continue
            processed_messages = ContextUtils.replace_messages(processed_messages, [replacement], idx, idx)
            event.messages_to_modify.append(idx)

        return event, processed_messages

    async def _process_large_message(
            self,
            message: BaseMessage,
            context_messages: List[BaseMessage],
            context: ModelContext,
            **kwargs
    ) -> BaseMessage | None:
        processed = self._apply_rule_compression_if_needed(message, context)
        if self._should_offload_message(processed, context_messages, context):
            return await self._offload_message(processed, context, original_message=message, **kwargs)
        if processed is not message:
            return processed
        return None

    async def _offload_message(
            self,
            message: BaseMessage,
            context: ModelContext,
            **kwargs
    ) -> BaseMessage:
        if self._is_rule_compressed_message(message):
            trimmed_content = message.content
        else:
            trimmed_content = message.content[:self.config.trim_size] + OMIT_STRING
        extra_fields = message.model_dump()
        original_message = kwargs.pop("original_message", message)
        extra_fields.pop("role", None)
        extra_fields.pop("content", None)
        offload_handle, offload_path = self._new_offload_handle_and_path(context)
        offload_message = await self.offload_messages(
            role=message.role,
            content=trimmed_content,
            messages=[original_message],
            context=context,
            offload_handle=offload_handle,
            offload_path=offload_path,
            **extra_fields,
            **kwargs
        )
        return offload_message

    @staticmethod
    def _is_rule_compressed_message(message: BaseMessage) -> bool:
        metadata = getattr(message, "metadata", None) or {}
        return bool(metadata.get("rule_compressed_at"))

    def _new_offload_handle_and_path(self, context: ModelContext) -> tuple[str, str | None]:
        offload_handle = uuid.uuid4().hex
        session_id = context.session_id()
        workspace_dir = context.workspace_dir()
        file_name = f"{self.processor_type()}_{offload_handle}.json"
        if workspace_dir:
            return (
                offload_handle,
                os.path.join(workspace_dir, "context", f"{session_id}_context", "offload", file_name),
            )
        return offload_handle, None

    def load_state(self, state: Dict[str, Any]) -> None:
        return

    def save_state(self) -> Dict[str, Any]:
        return dict()

    def _validate_config(self):
        if self.config.trim_size >= self.config.large_message_threshold:
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR,
                error_msg=f"trim_size {self.config.trim_size} cannot larger than "
                          f"large_message_threshold {self.config.large_message_threshold}"
            )
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

    def _get_offload_range(self, messages: List[BaseMessage]) -> int:
        last_ai_msg_index = None
        if self.config.keep_last_round:
            last_ai_msg_index = ContextUtils.find_last_ai_message_without_tool_call(messages)
        keep_index = (
            len(messages)
            if not self.config.messages_to_keep
            else len(messages) - self.config.messages_to_keep
        )
        return keep_index if last_ai_msg_index is None else min(last_ai_msg_index, keep_index)

    def _has_offload_candidate(self, messages: List[BaseMessage], context: ModelContext) -> bool:
        offload_range = self._get_offload_range(messages)
        for idx in range(offload_range - 1, -1, -1):
            if self._should_consider_message(messages[idx], messages):
                return True
        return False

    def _should_consider_message(
        self,
        message: BaseMessage,
        context_messages: List[BaseMessage],
    ) -> bool:
        if message.role not in self.config.offload_message_type:
            return False
        if not isinstance(getattr(message, "content", None), str):
            return False
        if len(message.content) <= self.config.large_message_threshold:
            return False
        if isinstance(message, OffloadMixin):
            return False
        if self._is_protected_tool_message(message, context_messages):
            return False
        return True

    def _should_offload_message(
        self,
        message: BaseMessage,
        context_messages: List[BaseMessage],
        context: ModelContext,
    ) -> bool:
        if message.role not in self.config.offload_message_type:
            return False
        if not isinstance(getattr(message, "content", None), str):
            return False
        if len(message.content) <= self.config.large_message_threshold:
            return False
        if isinstance(message, OffloadMixin):
            return False
        if self._is_protected_tool_message(message, context_messages):
            return False
        return True

    def _apply_rule_compression_if_needed(self, message: BaseMessage, context: ModelContext) -> BaseMessage:
        return self._rule_pipeline.compress_if_needed(message, context, self.config)

    def _should_trigger_context_ttl_sweep(self, context: ModelContext) -> bool:
        ttl_seconds = int(getattr(self.config, "rule_compression_ttl_seconds", 0) or 0)
        if ttl_seconds <= 0:
            return False
        key = self._context_ttl_key(context)
        now = self._rule_pipeline.current_time()
        last_sweep_at = self._last_rule_compression_ttl_sweep_at_by_context.get(key)
        if last_sweep_at is None:
            self._last_rule_compression_ttl_sweep_at_by_context[key] = now
            return False
        if now - last_sweep_at < ttl_seconds:
            return False
        self._pending_rule_compression_ttl_sweep_contexts.add(key)
        return True

    def _consume_context_ttl_sweep(self, context: ModelContext) -> bool:
        key = self._context_ttl_key(context)
        if key in self._pending_rule_compression_ttl_sweep_contexts:
            self._pending_rule_compression_ttl_sweep_contexts.discard(key)
            return True
        return self._should_trigger_context_ttl_sweep(context)

    def _mark_context_ttl_sweep_complete(self, context: ModelContext) -> None:
        key = self._context_ttl_key(context)
        self._last_rule_compression_ttl_sweep_at_by_context[key] = self._rule_pipeline.current_time()
        self._pending_rule_compression_ttl_sweep_contexts.discard(key)

    @staticmethod
    def _context_ttl_key(context: ModelContext) -> str:
        try:
            session_id = context.session_id()
        except Exception:
            session_id = "unknown_session"
        try:
            context_id = context.context_id()
        except Exception:
            context_id = str(id(context))
        return f"{session_id}:{context_id}"

    def _should_process_ttl_message(
        self,
        index: int,
        message: BaseMessage,
        context_messages: List[BaseMessage],
        ttl_sweep_active: bool,
    ) -> bool:
        keep_recent = int(getattr(self.config, "rule_compression_ttl_keep_recent_messages", 0) or 0)
        if self._is_ttl_recent_index(index, len(context_messages), keep_recent):
            return False
        if not isinstance(message, ToolMessage):
            return False
        if isinstance(message, OffloadMixin):
            return False
        if not isinstance(getattr(message, "content", None), str):
            return False
        if self._is_protected_tool_message(message, context_messages):
            return False
        return ttl_sweep_active

    @staticmethod
    def _is_ttl_recent_index(index: int, message_count: int, keep_recent: int) -> bool:
        return keep_recent > 0 and index >= max(message_count - keep_recent, 0)

    async def _process_ttl_message(self, message: BaseMessage, context: ModelContext) -> BaseMessage | None:
        if self._is_rule_compressed_message(message):
            message = self._mark_ttl_offload_metadata(message)
            return await self._offload_message(message, context, original_message=message)
        return self._compress_ttl_message(message, context)

    def _mark_ttl_offload_metadata(self, message: BaseMessage) -> BaseMessage:
        metadata = dict(getattr(message, "metadata", None) or {})
        metadata.update(
            {
                "rule_compressed_at": self._rule_pipeline.current_time(),
                "rule_compression_pass": "ttl",
                "rule_compression_ttl_offloaded": True,
            }
        )
        return message.model_copy(update={"metadata": metadata})

    def _compress_ttl_message(self, message: BaseMessage, context: ModelContext) -> BaseMessage | None:
        if not isinstance(message, ToolMessage) or not isinstance(getattr(message, "content", None), str):
            return None

        visible_content, offload_marker = self._split_offload_marker(message.content)
        threshold = self._rule_pipeline.threshold(
            self.config,
            threshold_ratio=self.config.rule_compression_expired_ratio,
        )
        head_tokens = max(threshold // 2, 1)
        tail_tokens = max(threshold - head_tokens, 0)
        candidate = message.model_copy(update={"content": visible_content})
        processed = self._rule_pipeline.compress_if_needed(
            candidate,
            context,
            self.config,
            threshold_ratio=self.config.rule_compression_expired_ratio,
            force=True,
            pass_name="ttl",
            truncate_head_tokens=head_tokens,
            truncate_tail_tokens=tail_tokens,
        )
        if processed is candidate:
            refreshed = self._refresh_expired_rule_metadata(candidate, threshold)
            if offload_marker:
                refreshed = refreshed.model_copy(update={"content": f"{refreshed.content}{offload_marker}"})
            return refreshed
        if offload_marker:
            processed = processed.model_copy(update={"content": f"{processed.content}{offload_marker}"})
        return processed

    def _refresh_expired_rule_metadata(self, message: BaseMessage, threshold: int) -> BaseMessage:
        metadata = dict(getattr(message, "metadata", None) or {})
        metadata.update(
            {
                "rule_compressed_at": self._rule_pipeline.current_time(),
                "rule_compression_threshold": threshold,
                "rule_compression_pass": "ttl",
            }
        )
        return message.model_copy(update={"metadata": metadata})

    @staticmethod
    def _split_offload_marker(content: str) -> tuple[str, str]:
        match = re.search(r"\[\[OFFLOAD: [^\]]+\]\]\s*$", content)
        if not match:
            return content, ""
        return content[: match.start()], match.group(0)

    def _rule_compression_threshold(self, context: ModelContext) -> int:
        _ = context
        return self._rule_pipeline.threshold(self.config)

    def _estimate_content_tokens(self, content: str, context: ModelContext) -> int:
        return self._estimate_text_tokens(content, context)

    @staticmethod
    def _estimate_text_tokens(content: str, context: ModelContext) -> int:
        return RuleCompressionPipeline.estimate_text_tokens(content, context)

    def _truncate_head_tail_by_tokens(
        self,
        content: str,
        head_tokens: int,
        tail_tokens: int,
        context: ModelContext,
    ) -> str:
        return self._rule_pipeline.truncate_head_tail_by_tokens(content, head_tokens, tail_tokens, context)

    def _is_protected_tool_message(
        self,
        message: BaseMessage,
        context_messages: List[BaseMessage],
    ) -> bool:
        if not isinstance(message, ToolMessage):
            return False
        tool_call = self._resolve_tool_call_from_message(message, context_messages)
        if not tool_call:
            return False

        tool_name = ContextUtils.extract_tool_name(tool_call)
        tool_args = self._extract_tool_args(tool_call)

        for protected in self.config.protected_tool_names:
            if ":" in protected:
                protected_tool, protected_pattern = protected.split(":", 1)
                if tool_name == protected_tool:
                    if self._match_pattern(tool_args, protected_pattern):
                        return True
            else:
                if tool_name == protected:
                    return True

        return False

    def _resolve_tool_call_from_message(
        self,
        message: BaseMessage,
        context_messages: List[BaseMessage],
    ) -> Optional[Any]:
        """Look up the tool_call object that corresponds to a tool message by traversing context backwards.

        Args:
            message: ToolMessage to look up.
            context_messages: Context message list.

        Returns:
            The matching tool_call object, or None if not found.
        """
        return ContextUtils.resolve_tool_call_from_message(message, context_messages)

    def _resolve_tool_name_from_message(
        self,
        message: BaseMessage,
        context_messages: List[BaseMessage],
    ) -> Optional[str]:
        """Look up the tool name that corresponds to a tool message by traversing context backwards.

        Args:
            message: ToolMessage to look up.
            context_messages: Context message list.

        Returns:
            Tool name string, or None if not found.
        """
        return ContextUtils.resolve_tool_name_from_message(message, context_messages)

    @staticmethod
    def _extract_tool_args(tool_call: Any) -> dict[str, Any]:
        """Extract argument dictionary from a tool_call object.

        Args:
            tool_call: tool_call object (dict or object with attributes).

        Returns:
            Argument dict, or empty dict on failure.
        """
        if isinstance(tool_call, dict):
            function = tool_call.get("function")
            if isinstance(function, dict):
                args_str = function.get("arguments")
                if isinstance(args_str, str):
                    try:
                        return json.loads(args_str)
                    except json.JSONDecodeError:
                        return {}
                if isinstance(args_str, dict):
                    return args_str
            args_str = tool_call.get("arguments")
            if isinstance(args_str, str):
                try:
                    return json.loads(args_str)
                except json.JSONDecodeError:
                    return {}
            if isinstance(args_str, dict):
                return args_str

        # Attribute-based access
        function = getattr(tool_call, "function", None)
        if function is not None:
            args_str = getattr(function, "arguments", None)
        else:
            args_str = getattr(tool_call, "arguments", None)

        if isinstance(args_str, dict):
            return args_str
        if isinstance(args_str, str):
            try:
                return json.loads(args_str)
            except json.JSONDecodeError:
                return {}
        return {}

    @staticmethod
    def _match_pattern(args: dict[str, Any], pattern: str) -> bool:
        """Check if any argument value matches a fnmatch wildcard pattern.

        Args:
            args: Argument dictionary.
            pattern: fnmatch wildcard pattern, e.g. "*.md", "path/to/*.py".

        Returns:
            True if any argument value matches the pattern.
        """

        for value in args.values():
            if isinstance(value, str) and fnmatch.fnmatch(value, pattern):
                return True
        return False
