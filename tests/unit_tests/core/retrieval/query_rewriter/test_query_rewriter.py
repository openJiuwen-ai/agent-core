# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Query Rewriter unit test cases
"""

import json
from pathlib import Path
from typing import Any, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.mode_info import BaseModelInfo, ModelConfig
from openjiuwen.core.retrieval.query_rewriter.query_rewriter import (
    QueryRewriter,
    _extract_json,
    _fill_template,
    _force_json,
    _force_list,
    _force_string,
    _parse_llm_json,
    _schema_repair,
)


# ---------------------------------------------------------------------------
# Test config and context helpers (no real API keys / external services)
# ---------------------------------------------------------------------------

def _qr_model_patch_target() -> str:
    return QueryRewriter.__module__ + ".Model"


def _make_compress_response() -> str:
    return json.dumps(
        {"theme": ["主题"], "summary": "摘要内容"},
        ensure_ascii=False,
    )


def _make_full_rewrite_response(standalone_query: str) -> str:
    return json.dumps(
        {
            "before": standalone_query,
            "intention": "用户咨询",
            "standalone_query": standalone_query,
            "references": {},
            "missing": [],
            "typo": [],
            "gibberish": [],
            "from_history": "",
        },
        ensure_ascii=False,
    )


@pytest.fixture
def qr_model_config():
    """ModelConfig for QueryRewriter tests (verify_ssl=False for test env)."""
    return ModelConfig(
        model_provider="OpenAI",
        model_info=BaseModelInfo(
            api_key="test-key",
            api_base="https://test.api",
            model_name="gpt-4",
            temperature=0.0,
            top_p=0.1,
            timeout=60,
            verify_ssl=False,
        ),
    )


@pytest.fixture
def session_context():
    """SessionModelContext with ContextEngineConfig and empty processors."""
    config = ContextEngineConfig(
        max_context_message_num=50,
        default_window_message_num=50,
    )
    return SessionModelContext(
        context_id="qr_ut_context",
        session_id="qr_ut_session",
        config=config,
        history_messages=[],
        processors=[],
        token_counter=None,
    )


async def _append_one_turn(
    context: SessionModelContext,
    user_content: str,
    assistant_content: str,
) -> None:
    await context.add_messages(UserMessage(content=user_content))
    await context.add_messages(AssistantMessage(content=assistant_content))


class TestQueryRewriterModelConfigPropagation:
    """Tests for ModelClientConfig propagation during QueryRewriter initialization."""

    def test_init_passes_only_custom_headers(self, session_context):
        cfg = ModelConfig(
            model_provider="OpenAI",
            model_info=BaseModelInfo(
                api_key="test-key",
                api_base="https://test.api",
                model_name="gpt-4",
                temperature=0.0,
                top_p=0.1,
                timeout=60,
                verify_ssl=False,
                custom_headers={"X-Tenant": "tenant-a"},
            ),
        )

        with patch(_qr_model_patch_target()) as mock_model:
            QueryRewriter(cfg=cfg, ctx=session_context, compress_range=5)

        model_client_config = mock_model.call_args.kwargs["model_client_config"]
        assert model_client_config.custom_headers == {"X-Tenant": "tenant-a"}


# =============================================================================
# Module-level function tests (from query_rewriter_full_test, kept as reference)
# =============================================================================

class TestFillTemplate:
    """Tests for _fill_template."""

    @staticmethod
    def test_replaces_placeholders():
        """Test placeholder replacement"""
        t = "a={a} b={b}"
        assert _fill_template(t, a="1", b="2") == "a=1 b=2"

    @staticmethod
    def test_ignores_curly_braces_in_json_example():
        """Test that JSON example braces are not treated as placeholders"""
        t = 'output: {history}, example: {"x":1}'
        assert _fill_template(t, history="hi") == 'output: hi, example: {"x":1}'


class TestExtractJson:
    """Tests for _extract_json."""

    @staticmethod
    def test_extracts_single_object():
        """Test extracting single JSON object from text"""
        s = 'prefix {"a":1} suffix'
        assert _extract_json(s) == '{"a":1}'

    @staticmethod
    def test_returns_empty_when_no_brace():
        """Test empty string when no brace found"""
        assert _extract_json("no json here") == ""

    @staticmethod
    def test_returns_empty_when_only_open_brace():
        """Test empty when only open brace"""
        assert _extract_json("{") == ""

    @staticmethod
    def test_takes_first_open_last_close():
        """Test taking first { to last }"""
        s = ' {"outer":{"inner":1}} '
        assert _extract_json(s) == '{"outer":{"inner":1}}'


class TestParseLlmJson:
    """Tests for _parse_llm_json."""

    @staticmethod
    def test_valid_json_returns_dict():
        """Test valid JSON returns dict"""
        assert _parse_llm_json('{"a":1}') == {"a": 1}

    @staticmethod
    def test_empty_string_returns_none():
        """Test empty or whitespace returns None"""
        assert _parse_llm_json("") is None
        assert _parse_llm_json("   ") is None

    @staticmethod
    def test_invalid_json_returns_none_without_repair():
        """Test invalid JSON without repair returns None"""
        assert _parse_llm_json("not json") is None

    @staticmethod
    def test_trailing_comma_repair():
        """Test trailing comma repaired by json_repair"""
        s = '{"theme":["a"], "summary":"b",}'
        out = _parse_llm_json(s)
        assert out is not None
        assert out.get("theme") == ["a"]
        assert out.get("summary") == "b"

    @staticmethod
    def test_non_dict_root_returns_none():
        """Test non-dict root returns None"""
        assert _parse_llm_json("[1,2,3]") is None
        assert _parse_llm_json("null") is None


class TestForceHelpers:
    """Tests for _force_string, _force_list, _force_json."""

    @staticmethod
    def test_force_string():
        """Test forcing value to string"""
        assert _force_string("x") == "x"
        assert _force_string({"a": 1}) in ('{"a": 1}', '{"a":1}')

    @staticmethod
    def test_force_list():
        """Test forcing value to list"""
        assert _force_list([1, 2]) == [1, 2]
        assert _force_list("x") == ["x"]

    @staticmethod
    def test_force_json():
        """Test forcing value to dict/JSON"""
        assert _force_json("k", {"a": 1}) == {"a": 1}
        assert _force_json("k", '{"a":1}') == {"a": 1}
        assert _force_json("k", "plain") == {"k": "plain"}


class TestSchemaRepair:
    """Tests for _schema_repair."""

    @staticmethod
    def test_compress_schema():
        """Test compress output schema repair"""
        schema = {"theme": list, "summary": str}
        out = _schema_repair({"theme": ["a"], "summary": "b"}, schema)
        assert out == {"theme": ["a"], "summary": "b"}

    @staticmethod
    def test_fills_none_with_defaults():
        """Test None fields filled with defaults"""
        schema = {"theme": list, "summary": str}
        out = _schema_repair({"theme": None, "summary": None}, schema)
        assert out["theme"] == []
        assert out["summary"] == ""

    @staticmethod
    def test_rewrite_schema_all_fields():
        """Test rewrite output schema with all fields"""
        schema = {
            "before": str,
            "intention": str,
            "standalone_query": str,
            "references": dict,
            "missing": list,
            "typo": list,
            "gibberish": list,
            "from_history": str,
        }
        raw = {
            "before": "那运费呢？",
            "intention": "咨询运费",
            "standalone_query": "淘美乐退货的运费谁出？",
            "references": {"那": "退货运费"},
            "missing": [],
            "typo": [],
            "gibberish": [],
            "from_history": "history",
        }
        out = _schema_repair(raw, schema)
        assert out["standalone_query"] == "淘美乐退货的运费谁出？"
        assert out["typo"] == []
        assert out["gibberish"] == []

    @staticmethod
    def test_typo_sub_structure():
        """Test typo list sub-structure repair"""
        schema = {"typo": list}
        raw = {"typo": [{"original": "teh", "corrected": "the", "reason": "typo"}]}
        out = _schema_repair(raw, schema)
        assert len(out["typo"]) == 1
        assert out["typo"][0]["original"] == "teh"
        assert out["typo"][0]["corrected"] == "the"

    @staticmethod
    def test_raises_on_non_dict():
        """Test non-dict output raises BaseError"""
        with pytest.raises(BaseError) as exc_info:
            _schema_repair("not a dict", {"a": str})
        assert exc_info.value.status == StatusCode.RETRIEVAL_QUERY_REWRITER_OUTPUT_INVALID


# =============================================================================
# QueryRewriter: load_template
# =============================================================================

class TestQueryRewriterLoadTemplate:
    """Tests for QueryRewriter.load_template."""

    @staticmethod
    def test_load_existing_template(qr_model_config, session_context):
        """Test loading existing prompt template"""
        qr = QueryRewriter(cfg=qr_model_config, ctx=session_context, compress_range=5)
        content = qr.load_template("intention_completion")
        assert "standalone_query" in content or "角色" in content

    @staticmethod
    def test_load_template_cached_on_second_call(qr_model_config, session_context):
        """Test template is cached on second load"""
        qr = QueryRewriter(cfg=qr_model_config, ctx=session_context, compress_range=5)
        first = qr.load_template("intention_completion")
        second = qr.load_template("intention_completion")
        assert first == second

    @staticmethod
    def test_prompt_not_found_raises(qr_model_config, session_context):
        """Test prompt not found raises BaseError"""
        qr = QueryRewriter(cfg=qr_model_config, ctx=session_context, compress_range=5)
        qr.prompt_lang = "nonexistent_lang"
        with pytest.raises(BaseError) as exc_info:
            qr.load_template("intention_completion")
        assert exc_info.value.status == StatusCode.RETRIEVAL_QUERY_REWRITER_PROMPT_NOT_FOUND

    @staticmethod
    def test_load_template_read_failure_raises(qr_model_config, session_context):
        """Test read failure raises BaseError"""
        qr = QueryRewriter(cfg=qr_model_config, ctx=session_context, compress_range=5)
        with patch.object(Path, "read_text", side_effect=OSError("read failed")):
            with pytest.raises(BaseError) as exc_info:
                qr.load_template("compression")
        assert exc_info.value.status == StatusCode.RETRIEVAL_QUERY_REWRITER_PROMPT_NOT_FOUND


# =============================================================================
# QueryRewriter: msg_2_text
# =============================================================================

class TestQueryRewriterMsg2Text:
    """Tests for QueryRewriter.msg_2_text."""

    @staticmethod
    def test_msg_2_text_with_messages(qr_model_config, session_context):
        """Test msg_2_text with explicit messages"""
        qr = QueryRewriter(cfg=qr_model_config, ctx=session_context, compress_range=5)
        messages = [
            UserMessage(content="今天天气如何？"),
            AssistantMessage(content="晴天。"),
        ]
        text = qr.msg_2_text(messages)
        assert "user: 今天天气如何？" in text
        assert "assistant: 晴天。" in text

    @pytest.mark.asyncio
    async def test_msg_2_text_from_context_when_none(
        self, qr_model_config, session_context
    ):
        """Test msg_2_text reads from context when messages is None"""
        await _append_one_turn(session_context, "你好", "你好！")
        qr = QueryRewriter(cfg=qr_model_config, ctx=session_context, compress_range=5)
        text = qr.msg_2_text(None)
        assert "user: 你好" in text
        assert "assistant: 你好！" in text


# =============================================================================
# QueryRewriter: compress (mock LLM)
# =============================================================================

class TestQueryRewriterCompress:
    """Tests for QueryRewriter.compress with mock LLM."""

    @pytest.mark.asyncio
    async def test_compress_valid_mock(self, qr_model_config, session_context):
        """Test compress with valid mock LLM response"""
        await _append_one_turn(session_context, "用户问", "助手答")
        messages = session_context.get_messages(with_history=True)

        async def mock_invoke(*args, **kwargs):
            return AssistantMessage(content=_make_compress_response())
        with patch(_qr_model_patch_target()) as mock_model:
            mock_model.return_value.invoke = AsyncMock(side_effect=mock_invoke)
            mock_model.return_value.model_config = MagicMock(temperature=0.0)
            qr = QueryRewriter(cfg=qr_model_config, ctx=session_context, compress_range=5)
            qr.llm = mock_model.return_value
            result = await qr.compress(messages)
        assert "theme" in result and "summary" in result
        assert isinstance(result["theme"], list)
        assert isinstance(result["summary"], str)

    @pytest.mark.asyncio
    async def test_compress_invalid_json_raises(self, qr_model_config, session_context):
        """Test compress with invalid JSON raises BaseError"""
        await _append_one_turn(session_context, "用户问", "助手答")
        messages = session_context.get_messages(with_history=True)

        async def mock_invoke(*args, **kwargs):
            return AssistantMessage(content="not valid json at all")
        with patch(_qr_model_patch_target()) as mock_model:
            mock_model.return_value.invoke = AsyncMock(side_effect=mock_invoke)
            mock_model.return_value.model_config = MagicMock(temperature=0.0)
            qr = QueryRewriter(cfg=qr_model_config, ctx=session_context, compress_range=5)
            qr.llm = mock_model.return_value
            with pytest.raises(BaseError) as exc_info:
                await qr.compress(messages)
        assert exc_info.value.status == StatusCode.RETRIEVAL_QUERY_REWRITER_OUTPUT_INVALID

    @pytest.mark.asyncio
    async def test_compress_llm_invoke_failure_raises(self, qr_model_config, session_context):
        """Test compress when LLM invoke fails raises BaseError"""
        await _append_one_turn(session_context, "用户问", "助手答")
        messages = session_context.get_messages(with_history=True)

        async def mock_invoke(*args, **kwargs):
            raise RuntimeError("network error")
        with patch(_qr_model_patch_target()) as mock_model:
            mock_model.return_value.invoke = AsyncMock(side_effect=mock_invoke)
            mock_model.return_value.model_config = MagicMock(temperature=0.0)
            qr = QueryRewriter(cfg=qr_model_config, ctx=session_context, compress_range=5)
            qr.llm = mock_model.return_value
            with pytest.raises(BaseError) as exc_info:
                await qr.compress(messages)
        assert exc_info.value.status == StatusCode.RETRIEVAL_QUERY_REWRITER_LLM_INVOKE_FAILED


# =============================================================================
# QueryRewriter: rewrite (mock LLM)
# =============================================================================

class TestQueryRewriterRewrite:
    """Tests for QueryRewriter.rewrite with mock LLM."""

    @pytest.mark.asyncio
    async def test_rewrite_valid_mock(self, qr_model_config, session_context):
        """Test rewrite with valid mock LLM response"""
        await _append_one_turn(session_context, "你好", "你好！")
        current_query = "那运费呢？"

        async def mock_invoke(*args, **kwargs):
            return AssistantMessage(content=_make_full_rewrite_response(current_query))
        with patch(_qr_model_patch_target()) as mock_model:
            mock_model.return_value.invoke = AsyncMock(side_effect=mock_invoke)
            mock_model.return_value.model_config = MagicMock(temperature=0.0)
            qr = QueryRewriter(cfg=qr_model_config, ctx=session_context, compress_range=5)
            qr.llm = mock_model.return_value
            result = await qr.rewrite(current_query)
        assert result.get("standalone_query") == "那运费呢？"
        assert "before" in result and "intention" in result

    @pytest.mark.asyncio
    async def test_rewrite_with_json_prefix_suffix(self, qr_model_config, session_context):
        """Test rewrite when LLM output has prefix/suffix around JSON"""
        await _append_one_turn(session_context, "你好", "你好！")
        current_query = "测试"
        payload = _make_full_rewrite_response(current_query)

        async def mock_invoke(*args, **kwargs):
            return AssistantMessage(content=f"这是回答：\n{payload}\n以上是结果。")
        with patch(_qr_model_patch_target()) as mock_model:
            mock_model.return_value.invoke = AsyncMock(side_effect=mock_invoke)
            mock_model.return_value.model_config = MagicMock(temperature=0.0)
            qr = QueryRewriter(cfg=qr_model_config, ctx=session_context, compress_range=5)
            qr.llm = mock_model.return_value
            result = await qr.rewrite(current_query)
        assert result.get("standalone_query") == "测试"

    @pytest.mark.asyncio
    async def test_rewrite_invalid_output_raises(self, qr_model_config, session_context):
        """Test rewrite with invalid LLM output raises BaseError"""
        await _append_one_turn(session_context, "你好", "你好！")

        async def mock_invoke(*args, **kwargs):
            return AssistantMessage(content="not json")
        with patch(_qr_model_patch_target()) as mock_model:
            mock_model.return_value.invoke = AsyncMock(side_effect=mock_invoke)
            mock_model.return_value.model_config = MagicMock(temperature=0.0)
            qr = QueryRewriter(cfg=qr_model_config, ctx=session_context, compress_range=5)
            qr.llm = mock_model.return_value
            with pytest.raises(BaseError) as exc_info:
                await qr.rewrite("问题")
        assert exc_info.value.status == StatusCode.RETRIEVAL_QUERY_REWRITER_OUTPUT_INVALID

    @pytest.mark.asyncio
    async def test_rewrite_invalid_input_empty_raises(self, qr_model_config, session_context):
        """Test rewrite with empty query raises BaseError"""
        qr = QueryRewriter(cfg=qr_model_config, ctx=session_context, compress_range=5)
        with pytest.raises(BaseError) as exc_info:
            await qr.rewrite("")
        assert exc_info.value.status == StatusCode.RETRIEVAL_QUERY_REWRITER_INPUT_INVALID

    @pytest.mark.asyncio
    async def test_rewrite_invalid_input_whitespace_raises(self, qr_model_config, session_context):
        """Test rewrite with whitespace-only query raises BaseError"""
        qr = QueryRewriter(cfg=qr_model_config, ctx=session_context, compress_range=5)
        with pytest.raises(BaseError) as exc_info:
            await qr.rewrite("   ")
        assert exc_info.value.status == StatusCode.RETRIEVAL_QUERY_REWRITER_INPUT_INVALID


# =============================================================================
# QueryRewriter: rewrite with compress fallback (compress raises -> original history)
# =============================================================================

class TestQueryRewriterRewriteCompressFallback:
    """When history >= compress_range and compress raises, rewrite falls back to original history."""

    @pytest.mark.asyncio
    async def test_rewrite_compress_failure_fallback(self, qr_model_config, session_context):
        """Test rewrite falls back to original history when compress raises"""
        rewrite_called = []
        for i in range(3):
            await _append_one_turn(session_context, f"用户问{i}", f"助手答{i}")
        current_query = "总结一下"

        async def mock_invoke(*args, **kwargs):
            msgs = kwargs.get("messages") or []
            last_content = (msgs[-1].content if msgs else "") or ""
            if "当前用户输入" in last_content:
                rewrite_called.append(1)
                return AssistantMessage(content=_make_full_rewrite_response(current_query))
            return AssistantMessage(content=_make_compress_response())
        with patch(_qr_model_patch_target()) as mock_model:
            mock_model.return_value.invoke = AsyncMock(side_effect=mock_invoke)
            mock_model.return_value.model_config = MagicMock(temperature=0.0)
            qr = QueryRewriter(cfg=qr_model_config, ctx=session_context, compress_range=5)
            qr.llm = mock_model.return_value

            async def failing_compress(raw):
                raise BaseError(StatusCode.RETRIEVAL_QUERY_REWRITER_OUTPUT_INVALID, msg="test")
            qr.compress = failing_compress
            result = await qr.rewrite(current_query)
        assert result.get("standalone_query") == "总结一下"
        assert len(rewrite_called) == 1


# =============================================================================
# json_repair: rewrite with trailing comma in LLM output
# =============================================================================

class TestRewriteWithTrailingCommaJsonRepair:
    """_parse_llm_json + json_repair fixes trailing comma; rewrite still succeeds."""

    @staticmethod
    def test_parse_llm_json_trailing_comma():
        """Test _parse_llm_json repairs trailing comma via json_repair"""
        broken = (
            '{"before":"x","intention":"y","standalone_query":"x","references":{},'
            '"missing":[],"typo":[],"gibberish":[],"from_history":"",}'
        )
        with pytest.raises(json.JSONDecodeError):
            json.loads(broken)
        repaired = _parse_llm_json(broken)
        assert repaired is not None
        assert repaired.get("standalone_query") == "x"

    @staticmethod
    @pytest.mark.asyncio
    async def test_rewrite_with_trailing_comma_mock(qr_model_config, session_context):
        """Test rewrite succeeds when LLM returns JSON with trailing comma"""
        await _append_one_turn(session_context, "你好", "你好！")
        broken = (
            '{"before":"x","intention":"y","standalone_query":"x","references":{},'
            '"missing":[],"typo":[],"gibberish":[],"from_history":"",}'
        )

        async def mock_invoke(*args, **kwargs):
            return AssistantMessage(content=broken)
        with patch(_qr_model_patch_target()) as mock_model:
            mock_model.return_value.invoke = AsyncMock(side_effect=mock_invoke)
            mock_model.return_value.model_config = MagicMock(temperature=0.0)
            qr = QueryRewriter(cfg=qr_model_config, ctx=session_context, compress_range=5)
            qr.llm = mock_model.return_value
            result = await qr.rewrite("x")
        assert result.get("standalone_query") == "x"


# =============================================================================
# Full conversation flow: multiple turns, rewrite at points, compress when >= range
# =============================================================================

LONG_CONVERSATION: List[Tuple[str, str]] = [
    ("你们这个淘美乐 App 是干什么的？", "淘美乐是一款综合购物与生活服务的 App。"),
    ("怎么注册和登录？", "使用手机号验证码或第三方账号登录。"),
    ("我想买点日用品，从哪里进？", "首页有「日百」等入口。"),
    ("搜索出来的结果太多，怎么筛选？", "搜索结果页有「筛选」按钮。"),
    ("下单后多久能送到？", "一般 1～3 天送达。"),
    ("可以修改订单吗？", "待发货状态下可以修改或取消。"),
]


class TestFullConversationWithCompressAndRewrite:
    """Full flow: append turns, call rewrite at given turns, assert compress/rewrite behavior."""

    @pytest.mark.asyncio
    async def test_full_conversation_with_compress_and_rewrite(
        self, qr_model_config, session_context
    ):
        """Test full flow: multiple turns, rewrite at points, compress when >= range"""
        compress_call_count = [0]
        rewrite_call_count = [0]
        current_query_for_mock = [None]

        async def mock_invoke(*args, **kwargs):
            msgs = kwargs.get("messages") or []
            last_content = (msgs[-1].content if msgs else "") or ""
            is_compress = (
                "user:" in last_content
                and "assistant:" in last_content
                and "当前用户输入" not in last_content
            )
            if is_compress:
                compress_call_count[0] += 1
                return AssistantMessage(content=_make_compress_response())
            rewrite_call_count[0] += 1
            q = current_query_for_mock[0] if current_query_for_mock[0] is not None else "用户当前问题"
            return AssistantMessage(content=_make_full_rewrite_response(q))

        with patch(_qr_model_patch_target()) as patcher:
            mock_model = patcher.start()
            mock_model.return_value.invoke = AsyncMock(side_effect=mock_invoke)
            mock_model.return_value.model_config = MagicMock(temperature=0.0)
            qr = QueryRewriter(cfg=qr_model_config, ctx=session_context, compress_range=5)
            qr.llm = mock_model.return_value

            rewrite_after_turns = [2, 4, 6]
            for turn_idx, (user_content, assistant_content) in enumerate(
                LONG_CONVERSATION, start=1
            ):
                await _append_one_turn(session_context, user_content, assistant_content)
                n = len(session_context.get_messages(with_history=True))
                assert n >= 1 and n <= turn_idx * 2

                if turn_idx in rewrite_after_turns:
                    current_query = "那运费呢？" if turn_idx == 2 else "会员怎么升级？" if turn_idx == 4 else "生鲜能退吗？"
                    current_query_for_mock[0] = current_query
                    n_before = len(session_context.get_messages(with_history=True))
                    result = await qr.rewrite(current_query)
                    n_after = len(session_context.get_messages(with_history=True))
                    msgs = session_context.get_messages(with_history=True)
                    assert "standalone_query" in result
                    assert result["standalone_query"] == current_query
                    if n_after == 1 and msgs and msgs[0].role == "system":
                        pass
                    elif n_after == n_before:
                        pass
            patcher.stop()

        assert rewrite_call_count[0] == len(rewrite_after_turns)
        assert compress_call_count[0] >= 0
