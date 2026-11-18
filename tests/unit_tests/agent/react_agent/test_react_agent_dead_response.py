# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""死响应（empty + zero-usage）判定与抛错路径测试。

死响应 = 网关/代理合成的空 200（content/tool_calls/reasoning 全空且
零 token 用量），修复前被 ReAct 当正常终答收下，任务永久挂起。
"""

import pytest

from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    AssistantMessageChunk,
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.core.foundation.llm.schema.message import UsageMetadata
from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.agents.react_agent import (
    _is_dead_model_response,
    _is_truncated_model_response,
    _raise_dead_model_response,
    _raise_truncated_model_response,
)
from openjiuwen.harness.rails.llm_retry_rail import LLMRetryRail


def _usage(input_tokens: int = 0, output_tokens: int = 0) -> UsageMetadata:
    return UsageMetadata(
        model_name="m",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


def _msg(**kwargs) -> AssistantMessage:
    defaults = dict(content="", tool_calls=[], reasoning_content=None, usage_metadata=None)
    defaults.update(kwargs)
    return AssistantMessage(**defaults)


class TestIsDeadModelResponse:
    """判定矩阵：宁漏不误——任一说话通道非空或用量缺失即放过。"""

    def test_empty_with_zero_usage_is_dead(self):
        assert _is_dead_model_response(_msg(usage_metadata=_usage(0, 0))) is True

    def test_empty_with_usage_absent_is_not_dead(self):
        # provider 不上报用量：无法区分合法空答与故障，保守放过
        assert _is_dead_model_response(_msg()) is False

    def test_empty_with_real_input_usage_is_not_dead(self):
        # content_filter 类应答：模型真处理过（input>0）但没说话
        assert _is_dead_model_response(_msg(usage_metadata=_usage(100, 0))) is False

    def test_content_present_is_not_dead(self):
        assert _is_dead_model_response(_msg(content="你好", usage_metadata=_usage(0, 0))) is False

    def test_tool_calls_present_is_not_dead(self):
        tc = [{"id": "1", "type": "function", "name": "t", "arguments": "{}"}]
        assert _is_dead_model_response(_msg(tool_calls=tc, usage_metadata=_usage(0, 0))) is False

    def test_reasoning_present_is_not_dead(self):
        # reasoning 模型纯思考帧
        assert _is_dead_model_response(_msg(reasoning_content="thinking", usage_metadata=_usage(0, 0))) is False


class TestRaiseDeadModelResponse:
    """抛错契约：MODEL_CALL_FAILED + marker 命中 LLMRetryRail 既有分类。"""

    @pytest.mark.parametrize("path,marker", [
        ("invoke", "async invoke error"),
        ("stream", "async stream error"),
    ])
    def test_raises_with_retry_rail_marker(self, path, marker):
        with pytest.raises(Exception) as exc_info:
            _raise_dead_model_response(path, "no content/tool_calls/reasoning, zero token usage")
        assert marker in str(exc_info.value)
        # 钉死关键契约：LLMRetryRail._is_model_call_failed_exception 必须命中，
        # 否则重试链断（退化为直接穿透，仍优于静默但失自愈）
        assert LLMRetryRail._is_model_call_failed_exception(exc_info.value) is True

    def test_zero_chunks_reason(self):
        with pytest.raises(Exception) as exc_info:
            _raise_dead_model_response("stream", "connection closed with zero chunks")
        assert "async stream error" in str(exc_info.value)
        assert "zero chunks" in str(exc_info.value)


class TestIsTruncatedModelResponse:
    """截断判定矩阵：finish_reason 缺失 AND 零用量信息 才判——
    finish_reason 或用量任一在场即按正常收尾放过（宁漏不误）。"""

    def test_content_without_finish_reason_and_usage_is_truncated(self):
        # SSE 中途断开：有部分内容、无收尾帧、无用量 → 截断。
        # 显式置 None：构造器拒绝 None（str 字段缺省 "null"），生产链路的
        # None 经属性赋值/聚合进入（如 SDK choice.finish_reason=None 直通）。
        # 不传 finish_reason 会落到 schema 默认 "null"，与下方字符串用例撞车。
        msg = _msg(content="半截答案")
        msg.finish_reason = None
        assert _is_truncated_model_response(msg) is True

    def test_content_with_finish_reason_is_not_truncated(self):
        assert _is_truncated_model_response(_msg(content="完整答案", finish_reason="stop")) is False

    def test_content_with_null_string_finish_reason_is_truncated(self):
        # 部分 provider 把缺失写成字符串 "null"
        assert _is_truncated_model_response(_msg(content="半截", finish_reason="null")) is True

    def test_content_with_empty_string_finish_reason_is_truncated(self):
        assert _is_truncated_model_response(_msg(content="半截", finish_reason="")) is True

    def test_usage_present_falls_back_to_normal(self):
        # 不上报 finish_reason 的 provider：用量在场即放过（不判截断）
        assert _is_truncated_model_response(
            _msg(content="正常答案", usage_metadata=_usage(100, 50))
        ) is False

    def test_tool_calls_without_finish_reason_and_usage_is_truncated(self):
        # tool_calls 截断（参数 JSON 被切）同属流未走完
        tc = [{"id": "1", "type": "function", "name": "t", "arguments": "{\"a\":"}]
        assert _is_truncated_model_response(_msg(tool_calls=tc)) is True

    def test_tool_calls_with_finish_reason_is_not_truncated(self):
        tc = [{"id": "1", "type": "function", "name": "t", "arguments": "{}"}]
        assert _is_truncated_model_response(_msg(tool_calls=tc, finish_reason="tool_calls")) is False

    def test_truncation_raise_bypasses_retry_rail(self):
        # 钉死截断抛错契约（完全不重试、判死穿透）——181006
        # 专属码、不命中 rail 的 model_call_failed 文案分类（命中即被
        # 兼容兜底重试，半截+完整重复卷土重来）
        with pytest.raises(Exception) as exc_info:
            _raise_truncated_model_response(
                "stream ended without finish_reason (truncated mid-stream)"
            )
        assert "181006" in str(exc_info.value)
        assert LLMRetryRail._is_model_call_failed_exception(exc_info.value) is False


class _NoTerminalSignalStreamModel:
    """finish_reason 与 usage 均不上报的 provider 形态（双缺）。

    chunk 不带 finish_reason（schema 缺省 "null"=缺失）且无 usage_metadata，
    每次正常应答都命中截断判据——逃生阀的针对场景。
    """

    def __init__(self):
        self.call_count = 0

    async def invoke(self, **kwargs):
        raise NotImplementedError

    async def stream(self, **kwargs):
        self.call_count += 1
        yield AssistantMessageChunk(content="完整答案")


def _make_agent(tolerate: bool) -> ReActAgent:
    return ReActAgent(card=AgentCard(description="truncation tolerate test")).configure(
        ReActAgentConfig(
            model_config_obj=ModelRequestConfig(model="mock-model"),
            model_client_config=ModelClientConfig(
                client_provider="OpenAI",
                api_key="sk-test",
                api_base="https://mock.local/v1",
                verify_ssl=False,
            ),
            prompt_template=[{"role": "system", "content": "You are a test assistant."}],
            tolerate_missing_stream_terminal=tolerate,
        )
    )


class TestTolerateMissingStreamTerminal:
    """双缺 provider 逃生阀：默认判截断走失败链，tolerate=True 告警接受。"""

    @pytest.mark.asyncio
    async def test_default_fails_loud_for_no_signal_provider(self):
        agent = _make_agent(tolerate=False)
        model = _NoTerminalSignalStreamModel()
        agent.set_llm(model)

        with pytest.raises(Exception) as exc_info:
            await agent.invoke({"query": "hi"}, _streaming=True)

        # 判死穿透（181006）：不进 rail 重试分类，直接失败终态
        assert "181006" in str(exc_info.value)
        assert LLMRetryRail._is_model_call_failed_exception(exc_info.value) is False

    @pytest.mark.asyncio
    async def test_tolerate_accepts_with_warning_instead_of_failing(self):
        agent = _make_agent(tolerate=True)
        model = _NoTerminalSignalStreamModel()
        agent.set_llm(model)

        result = await agent.invoke({"query": "hi"}, _streaming=True)

        assert result["output"] == "完整答案"
        assert model.call_count == 1
