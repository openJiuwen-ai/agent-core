# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import json
from datetime import datetime, timedelta
from typing import Dict, Any
from openjiuwen.core.foundation.llm import JsonOutputParser
from openjiuwen.core.memory.process.extract.common import ExtractMemoryParams
from openjiuwen.core.memory.prompts.prompt_applier import PromptApplier
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType
from openjiuwen.core.memory.config.config import MemoryScopeConfig


class LongTermMemoryExtractor:
    def __init__(self) -> None:
        pass

    @staticmethod
    async def extract_long_term_memory(
        extract_memory_paras: ExtractMemoryParams,
        timestamp: str,
        scope_config: MemoryScopeConfig,
        retries: int = 3,
    ) -> Dict[str, Any]:
        reference_str = ""
        input_msg_str = ""
        for msg in extract_memory_paras.history_messages:
            reference_str += f"{msg.name or msg.role}: {msg.content}\n"
        for msg in extract_memory_paras.messages:
            reference_str += f"{msg.name or msg.role}: {msg.content}\n"
            if msg.role == "user":
                input_msg_str += f"{msg.name or msg.role}: {msg.content}\n"

        if not scope_config:
            scope_config = MemoryScopeConfig()

        current_week = LongTermMemoryExtractor._build_time_context(timestamp)
        prompt_content = PromptApplier().apply(
            "fragment_memory_prompt",
            {
                "conversation_time": timestamp,
                "input_messages": input_msg_str,
                "reference_messages": reference_str,
                "user_profile_definition": scope_config.user_profile_definition or "",
                "semantic_memory_definition": scope_config.semantic_memory_definition or "",
                "episodic_memory_definition": scope_config.episodic_memory_definition or "",
                "current_week": current_week,
            },
        )
        model_input = [{"role": "user", "content": prompt_content}]
        parser = JsonOutputParser()
        for attempt in range(retries):
            try:
                response = await extract_memory_paras.base_chat_model.invoke(messages=model_input)
                result = await parser.parse(response.content)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError as e:
                if attempt < retries - 1:
                    continue
                memory_logger.error(
                    "Long term memory extractor model output format error",
                    event_type=LogEventType.MEMORY_PROCESS,
                    exception=str(e)
                )
        return {}

    @staticmethod
    def _build_time_context(timestamp: str) -> str:
        try:
            dt = datetime.fromisoformat(timestamp)
            monday = dt - timedelta(days=dt.weekday())
            sunday = monday + timedelta(days=6)
            return (
                f"{monday.year}年{monday.month}月{monday.day}日(周一)～"
                f"{sunday.year}年{sunday.month}月{sunday.day}日(周日)"
                f"（即{monday.strftime('%m.%d')}～{sunday.strftime('%m.%d')}）"
            )
        except (ValueError, TypeError):
            return timestamp
