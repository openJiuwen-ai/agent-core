from __future__ import annotations

import json
import os
import re
import time
from typing import Callable

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.processor.offloader.rules.query_terms import extract_query_terms
from openjiuwen.core.context_engine.processor.offloader.rules.router import RuleContentRouter
from openjiuwen.core.context_engine.processor.offloader.rules.types import RuleContext
from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage, UserMessage


CHARACTERS_PER_TOKEN = 3
RULE_COMPRESSION_DUMP_DIR_ENV = "OPENJIUWEN_RULE_COMPRESSION_DUMP_DIR"


class RuleCompressionPipeline:
    """Run deterministic compression using fixed context-relative character budgets."""

    def __init__(
        self,
        router: RuleContentRouter | None = None,
        time_func: Callable[[], float] = time.time,
        enable_dump: bool = False,
    ) -> None:
        self._router = router or RuleContentRouter()
        self._time_func = time_func
        self._enable_dump = enable_dump

    def current_time(self) -> float:
        return self._time_func()

    @staticmethod
    def context_character_capacity(context: ModelContext) -> int:
        context_tokens = context.context_window_tokens()
        return max(int(context_tokens or 0) * CHARACTERS_PER_TOKEN, 1)

    def compress(
        self,
        message: BaseMessage,
        context: ModelContext,
        *,
        pass_name: str,
        max_chars: int,
        force: bool = False,
        context_messages: list[BaseMessage] | None = None,
    ) -> BaseMessage:
        if not isinstance(message, ToolMessage) or not isinstance(message.content, str):
            return message
        if not force and len(message.content) <= max_chars:
            return message

        original = message.content
        result = self._router.compress(
            original,
            RuleContext(
                max_tokens=max(max_chars // CHARACTERS_PER_TOKEN, 1),
                head_tokens=max(max_chars // (CHARACTERS_PER_TOKEN * 2), 1),
                tail_tokens=max(max_chars // (CHARACTERS_PER_TOKEN * 2), 1),
                count_tokens=lambda text: max(len(text) // CHARACTERS_PER_TOKEN, 1),
                query_terms=self._query_terms_for_message(
                    message,
                    context_messages or context.get_messages(),
                ),
            ),
        )
        content = result.content
        if not result.modified:
            return message

        metadata = dict(getattr(message, "metadata", None) or {})
        metadata.update(
            {
                "rule_compressed_at": self.current_time(),
                "rule_compression_type": result.content_type.value,
                "rule_compression_pass": pass_name,
            }
        )
        if result.details:
            metadata["rule_compression_details"] = result.details
        dump_path = self._dump_result(
            context,
            original=original,
            compressed=content,
            pass_name=pass_name,
            content_type=result.content_type.value,
            tool_call_id=str(getattr(message, "tool_call_id", "unknown") or "unknown"),
            details=result.details,
        )
        if dump_path:
            metadata["rule_compression_dump_path"] = dump_path
        logger.info(
            "[RuleCompression] applied tool_call_id=%s pass=%s type=%s chars=%s->%s",
            getattr(message, "tool_call_id", "unknown"),
            pass_name,
            result.content_type.value,
            len(original),
            len(content),
        )
        return message.model_copy(update={"content": content, "metadata": metadata})

    def _dump_result(
        self,
        context: ModelContext,
        *,
        original: str,
        compressed: str,
        pass_name: str,
        content_type: str,
        tool_call_id: str,
        details: dict | None,
    ) -> str | None:
        if not self._enable_dump:
            return None
        dump_dir = self._dump_dir(context)
        if not dump_dir:
            return None

        timestamp = self.current_time()
        session_id = self._safe_context_value(context, "session_id", "unknown_session")
        context_id = self._safe_context_value(context, "context_id", "unknown_context")
        safe_parts = [
            str(int(timestamp * 1000)),
            pass_name,
            content_type,
            tool_call_id,
        ]
        file_name = "_".join(self._safe_filename_part(part) for part in safe_parts) + ".json"
        dump_path = os.path.join(dump_dir, file_name)
        payload = {
            "timestamp": timestamp,
            "session_id": session_id,
            "context_id": context_id,
            "tool_call_id": tool_call_id,
            "rule_compression_type": content_type,
            "rule_compression_pass": pass_name,
            "original_chars": len(original),
            "compressed_chars": len(compressed),
            "details": details or {},
            "original_content": original,
            "compressed_content": compressed,
        }
        try:
            os.makedirs(dump_dir, exist_ok=True)
            with open(dump_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("[RuleCompression] failed to dump compression payload: %s", exc)
            return None
        logger.info("[RuleCompression] dumped compression payload path=%s", dump_path)
        return dump_path

    def _dump_dir(self, context: ModelContext) -> str:
        env_dir = os.getenv(RULE_COMPRESSION_DUMP_DIR_ENV)
        if env_dir:
            return env_dir
        workspace_dir = self._workspace_dir(context)
        if not workspace_dir:
            return ""
        session_id = self._safe_context_value(context, "session_id", "unknown_session")
        return os.path.join(
            workspace_dir,
            "context",
            f"{session_id}_context",
            "rule_compression_logs",
        )

    @staticmethod
    def _workspace_dir(context: ModelContext) -> str:
        workspace_dir = getattr(context, "workspace_dir", None)
        if not callable(workspace_dir):
            return ""
        try:
            return str(workspace_dir() or "")
        except Exception:
            return ""

    @staticmethod
    def _safe_context_value(context: ModelContext, method_name: str, fallback: str) -> str:
        method = getattr(context, method_name, None)
        if not callable(method):
            return fallback
        try:
            value = method()
        except Exception:
            return fallback
        return str(value or fallback)

    @staticmethod
    def _safe_filename_part(value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
        return safe[:80] or "unknown"

    def _query_terms_for_message(
        self,
        message: BaseMessage,
        context_messages: list[BaseMessage],
    ) -> frozenset[str]:
        latest_user_content = ""
        for context_message in reversed(context_messages):
            if isinstance(context_message, UserMessage) and isinstance(context_message.content, str):
                latest_user_content = context_message.content
                break

        tool_name = None
        tool_arguments = None
        if isinstance(message, ToolMessage):
            tool_call = ContextUtils.resolve_tool_call_from_message(message, context_messages)
            if tool_call is not None:
                tool_name = ContextUtils.extract_tool_name(tool_call)
                tool_arguments = self._extract_tool_arguments(tool_call)

        return extract_query_terms(latest_user_content, tool_name, tool_arguments)

    @staticmethod
    def _extract_tool_arguments(tool_call: object) -> object:
        if isinstance(tool_call, dict):
            function = tool_call.get("function")
            arguments = function.get("arguments") if isinstance(function, dict) else None
            return arguments if arguments is not None else tool_call.get("arguments")

        function = getattr(tool_call, "function", None)
        if function is not None:
            arguments = getattr(function, "arguments", None)
            if arguments is not None:
                return arguments
        return getattr(tool_call, "arguments", None)
