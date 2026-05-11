# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.common.utils.singleton import Singleton
from openjiuwen.core.foundation.llm.schema.message import BaseMessage
from openjiuwen.core.memory.config.config import (
    AgentMemoryConfig,
    MemoryScopeConfig,
)
from openjiuwen.core.memory.process.extract.common import ExtractMemoryParams
from openjiuwen.core.memory.process.extract.long_term_memory_extractor import (
    LongTermMemoryExtractor,
)
from openjiuwen.core.memory.process.extract.memory_analyzer import MemoryAnalyzer
from openjiuwen.core.memory.prompts.prompt_applier import PromptApplier


CUSTOM_USER_PROFILE_DEFINITION = "自定义用户画像定义"
CUSTOM_SEMANTIC_MEMORY_DEFINITION = "自定义语义记忆定义"
CUSTOM_EPISODIC_MEMORY_DEFINITION = "自定义情景记忆定义"


@pytest.fixture(autouse=True)
def reset_singletons():
    if PromptApplier in Singleton._instances:
        del Singleton._instances[PromptApplier]
    yield
    if PromptApplier in Singleton._instances:
        del Singleton._instances[PromptApplier]


# ============================================================
# MemoryScopeConfig 默认值与自定义值测试
# ============================================================


class TestMemoryScopeConfigDefaults:
    """MemoryScopeConfig 默认定义字符串验证"""

    def test_default_user_profile_definition(self):
        config = MemoryScopeConfig()
        assert config.user_profile_definition == "用户本人的肯定或否定表述（包含不限于基本身份、兴趣偏好、人际关系、资产状况）"

    def test_default_semantic_memory_definition(self):
        config = MemoryScopeConfig()
        assert config.semantic_memory_definition == "用户对话中涉及的和时间无明确关系的事实性内容或概念"

    def test_default_episodic_memory_definition(self):
        config = MemoryScopeConfig()
        assert config.episodic_memory_definition == "用户对话中涉及的和时间有明确关系的事实性内容或概念"


class TestMemoryScopeConfigCustom:
    """MemoryScopeConfig 自定义定义字符串验证"""

    def test_custom_definitions(self):
        config = MemoryScopeConfig(
            user_profile_definition=CUSTOM_USER_PROFILE_DEFINITION,
            semantic_memory_definition=CUSTOM_SEMANTIC_MEMORY_DEFINITION,
            episodic_memory_definition=CUSTOM_EPISODIC_MEMORY_DEFINITION,
        )
        assert config.user_profile_definition == CUSTOM_USER_PROFILE_DEFINITION
        assert config.semantic_memory_definition == CUSTOM_SEMANTIC_MEMORY_DEFINITION
        assert config.episodic_memory_definition == CUSTOM_EPISODIC_MEMORY_DEFINITION

    def test_partial_custom(self):
        config = MemoryScopeConfig(
            semantic_memory_definition=CUSTOM_SEMANTIC_MEMORY_DEFINITION,
        )
        assert config.semantic_memory_definition == CUSTOM_SEMANTIC_MEMORY_DEFINITION
        assert config.user_profile_definition == "用户本人的肯定或否定表述（包含不限于基本身份、兴趣偏好、人际关系、资产状况）"
        assert config.episodic_memory_definition == "用户对话中涉及的和时间有明确关系的事实性内容或概念"


# ============================================================
# MemoryAnalyzer 自定义 prompt definition 注入测试
# ============================================================


class TestMemoryAnalyzerPromptInjection:
    """MemoryAnalyzer.analyze 验证自定义定义注入到 prompt 变量"""

    @pytest.mark.asyncio
    async def test_custom_scope_config_injected_into_prompt(self):
        scope_config = MemoryScopeConfig(
            user_profile_definition=CUSTOM_USER_PROFILE_DEFINITION,
            semantic_memory_definition=CUSTOM_SEMANTIC_MEMORY_DEFINITION,
            episodic_memory_definition=CUSTOM_EPISODIC_MEMORY_DEFINITION,
        )

        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"has_key_information": false, "variables": [], "summary": ""}'
        mock_model.invoke = AsyncMock(return_value=mock_response)

        mock_parser_instance = MagicMock()
        mock_parser_instance.parse = AsyncMock(
            return_value={"has_key_information": False, "variables": [], "summary": ""}
        )

        messages = [BaseMessage(role="user", content="你好")]
        history_messages = [BaseMessage(role="assistant", content="你好呀")]
        memory_config = AgentMemoryConfig()

        with patch.object(PromptApplier, "apply", return_value="rendered_prompt") as mock_apply, \
             patch("openjiuwen.core.memory.process.extract.memory_analyzer.JsonOutputParser", return_value=mock_parser_instance):
            result = await MemoryAnalyzer.analyze(
                messages=messages,
                history_messages=history_messages,
                base_chat_model=mock_model,
                memory_config=memory_config,
                summary_max_token=128,
                scope_config=scope_config,
            )

            mock_apply.assert_called_once()
            call_kwargs = mock_apply.call_args[0][1]
            assert call_kwargs["user_profile_definition"] == CUSTOM_USER_PROFILE_DEFINITION
            assert call_kwargs["semantic_memory_definition"] == CUSTOM_SEMANTIC_MEMORY_DEFINITION
            assert call_kwargs["episodic_memory_definition"] == CUSTOM_EPISODIC_MEMORY_DEFINITION
            assert result is not None

    @pytest.mark.asyncio
    async def test_none_scope_config_passes_empty_strings(self):
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"has_key_information": false, "variables": [], "summary": ""}'
        mock_model.invoke = AsyncMock(return_value=mock_response)

        mock_parser_instance = MagicMock()
        mock_parser_instance.parse = AsyncMock(
            return_value={"has_key_information": False, "variables": [], "summary": ""}
        )

        messages = [BaseMessage(role="user", content="测试")]
        history_messages = []
        memory_config = AgentMemoryConfig()

        with patch.object(PromptApplier, "apply", return_value="rendered_prompt") as mock_apply, \
             patch("openjiuwen.core.memory.process.extract.memory_analyzer.JsonOutputParser", return_value=mock_parser_instance):
            result = await MemoryAnalyzer.analyze(
                messages=messages,
                history_messages=history_messages,
                base_chat_model=mock_model,
                memory_config=memory_config,
                summary_max_token=128,
                scope_config=None,
            )

            call_kwargs = mock_apply.call_args[0][1]
            assert call_kwargs["user_profile_definition"] == ""
            assert call_kwargs["semantic_memory_definition"] == ""
            assert call_kwargs["episodic_memory_definition"] == ""

    @pytest.mark.asyncio
    async def test_default_scope_config_uses_defaults(self):
        scope_config = MemoryScopeConfig()

        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"has_key_information": false, "variables": [], "summary": ""}'
        mock_model.invoke = AsyncMock(return_value=mock_response)

        mock_parser_instance = MagicMock()
        mock_parser_instance.parse = AsyncMock(
            return_value={"has_key_information": False, "variables": [], "summary": ""}
        )

        messages = [BaseMessage(role="user", content="默认测试")]
        history_messages = []
        memory_config = AgentMemoryConfig()

        with patch.object(PromptApplier, "apply", return_value="rendered_prompt") as mock_apply, \
             patch("openjiuwen.core.memory.process.extract.memory_analyzer.JsonOutputParser", return_value=mock_parser_instance):
            result = await MemoryAnalyzer.analyze(
                messages=messages,
                history_messages=history_messages,
                base_chat_model=mock_model,
                memory_config=memory_config,
                summary_max_token=128,
                scope_config=scope_config,
            )

            call_kwargs = mock_apply.call_args[0][1]
            assert call_kwargs["user_profile_definition"] == "用户本人的肯定或否定表述（包含不限于基本身份、兴趣偏好、人际关系、资产状况）"
            assert call_kwargs["semantic_memory_definition"] == "用户对话中涉及的和时间无明确关系的事实性内容或概念"
            assert call_kwargs["episodic_memory_definition"] == "用户对话中涉及的和时间有明确关系的事实性内容或概念"

    @pytest.mark.asyncio
    async def test_empty_messages_returns_none(self):
        mock_model = MagicMock()
        memory_config = AgentMemoryConfig()

        result = await MemoryAnalyzer.analyze(
            messages=[],
            history_messages=[],
            base_chat_model=mock_model,
            memory_config=memory_config,
            summary_max_token=128,
            scope_config=None,
        )

        assert result is None


# ============================================================
# LongTermMemoryExtractor 自定义 prompt definition 注入测试
# ============================================================


class TestLongTermMemoryExtractorPromptInjection:
    """LongTermMemoryExtractor.extract_long_term_memory 验证自定义定义注入到 prompt 变量"""

    @pytest.mark.asyncio
    async def test_custom_scope_config_injected_into_prompt(self):
        scope_config = MemoryScopeConfig(
            user_profile_definition=CUSTOM_USER_PROFILE_DEFINITION,
            semantic_memory_definition=CUSTOM_SEMANTIC_MEMORY_DEFINITION,
            episodic_memory_definition=CUSTOM_EPISODIC_MEMORY_DEFINITION,
        )

        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"user_profile": [], "semantic_memory": [], "episodic_memory": []}'
        mock_model.invoke = AsyncMock(return_value=mock_response)

        mock_parser_instance = MagicMock()
        mock_parser_instance.parse = AsyncMock(
            return_value={"user_profile": [], "semantic_memory": [], "episodic_memory": []}
        )

        messages = [BaseMessage(role="user", content="我喜欢打篮球")]
        history_messages = [BaseMessage(role="assistant", content="很好")]

        extract_params = ExtractMemoryParams(
            user_id="u1",
            scope_id="s1",
            messages=messages,
            history_messages=history_messages,
            base_chat_model=mock_model,
        )

        with patch.object(PromptApplier, "apply", return_value="rendered_prompt") as mock_apply, \
             patch("openjiuwen.core.memory.process.extract.long_term_memory_extractor.JsonOutputParser", return_value=mock_parser_instance):
            result = await LongTermMemoryExtractor.extract_long_term_memory(
                extract_memory_paras=extract_params,
                timestamp="2026-01-01T00:00:00",
                scope_config=scope_config,
            )

            mock_apply.assert_called_once()
            call_kwargs = mock_apply.call_args[0][1]
            assert call_kwargs["user_profile_definition"] == CUSTOM_USER_PROFILE_DEFINITION
            assert call_kwargs["semantic_memory_definition"] == CUSTOM_SEMANTIC_MEMORY_DEFINITION
            assert call_kwargs["episodic_memory_definition"] == CUSTOM_EPISODIC_MEMORY_DEFINITION
            assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_default_scope_config_uses_defaults(self):
        scope_config = MemoryScopeConfig()

        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"user_profile": [], "semantic_memory": [], "episodic_memory": []}'
        mock_model.invoke = AsyncMock(return_value=mock_response)

        mock_parser_instance = MagicMock()
        mock_parser_instance.parse = AsyncMock(
            return_value={"user_profile": [], "semantic_memory": [], "episodic_memory": []}
        )

        messages = [BaseMessage(role="user", content="今天天气不错")]
        history_messages = []

        extract_params = ExtractMemoryParams(
            user_id="u1",
            scope_id="s1",
            messages=messages,
            history_messages=history_messages,
            base_chat_model=mock_model,
        )

        with patch.object(PromptApplier, "apply", return_value="rendered_prompt") as mock_apply, \
             patch("openjiuwen.core.memory.process.extract.long_term_memory_extractor.JsonOutputParser", return_value=mock_parser_instance):
            result = await LongTermMemoryExtractor.extract_long_term_memory(
                extract_memory_paras=extract_params,
                timestamp="2026-01-01T00:00:00",
                scope_config=scope_config,
            )

            call_kwargs = mock_apply.call_args[0][1]
            assert call_kwargs["user_profile_definition"] == "用户本人的肯定或否定表述（包含不限于基本身份、兴趣偏好、人际关系、资产状况）"
            assert call_kwargs["semantic_memory_definition"] == "用户对话中涉及的和时间无明确关系的事实性内容或概念"
            assert call_kwargs["episodic_memory_definition"] == "用户对话中涉及的和时间有明确关系的事实性内容或概念"
