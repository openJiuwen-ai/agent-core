# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.compressor.reasoning_tool_loop_compact_processor import (
    LOOP_COMPACT_BAILOUT_STATE_KEY,
    TOOL_ARGS_LOOP_COMPACT_BAILOUT_STATE_KEY,
    ReasoningToolLoopCompactProcessor,
    ReasoningToolLoopCompactProcessorConfig,
)
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage


class _FakeSession:
    """Minimal session double exposing the get_state/update_state contract.

    Mirrors ``openjiuwen.core.session.agent.Session``: ``get_state(key)``
    returns the stored value (or the whole dict when key is None) and
    ``update_state(dict)`` merges into a single global state dict.
    """

    def __init__(self) -> None:
        self._state: dict = {}

    def get_state(self, key=None):
        if key is None:
            return dict(self._state)
        return self._state.get(key)

    def update_state(self, data: dict) -> None:
        self._state.update(data)


def _extract_json_block(content: str, heading: str) -> list:
    marker = f"{heading}\n---\n"
    idx = content.find(marker)
    assert idx >= 0, f"missing JSON block under heading: {heading}"
    start = idx + len(marker)
    obj, _ = json.JSONDecoder().raw_decode(content, start)
    assert isinstance(obj, list)
    return obj


REASONING = (
    "用户要求我组建研究团队并完成个性化联邦学习研究想法。"
    "先分析五个 agent 背景，然后创建任务清单来跟踪进度。"
) * 2


def _tool_call(tool_id: str, name: str, arguments: str) -> ToolCall:
    return ToolCall(id=tool_id, name=name, type="function", arguments=arguments)


def _todo_round(index: int, *, reasoning: str = REASONING, arguments: str | None = None) -> list:
    args = arguments or (
        '{"tasks":[{"id":"literature_review","content":"文献综述",'
        '"activeForm":"正在进行文献综述","description":"分析Introduction"}]}'
    )
    tool_id = f"tc-{index}"
    return [
        AssistantMessage(
            content="我来组织这个研究团队协作完成任务。首先创建任务清单来跟踪进度。",
            reasoning_content=reasoning,
            tool_calls=[_tool_call(tool_id, "todo_create", args)],
        ),
        ToolMessage(
            content=f"Successfully created task(s) round-{index}",
            tool_call_id=tool_id,
        ),
    ]


def _loop_summary_messages(messages) -> list[AssistantMessage]:
    return [
        msg for msg in messages
        if isinstance(msg, AssistantMessage)
        and not msg.tool_calls
        and "请跳出多轮重复执行" in str(msg.content)
    ]


def _tool_args_loop_summary_messages(messages) -> list[AssistantMessage]:
    return [
        msg for msg in messages
        if isinstance(msg, AssistantMessage)
        and not msg.tool_calls
        and "每个工具的入参也相同" in str(msg.content)
    ]


async def _create_context(config: ReasoningToolLoopCompactProcessorConfig, history=None):
    engine = ContextEngine(ContextEngineConfig(default_window_message_num=200))
    return await engine.create_context(
        "test_reasoning_tool_loop",
        None,
        history_messages=history or [],
        processors=[("ReasoningToolLoopCompactProcessor", config)],
    )


class TestReasoningToolLoopCompactProcessor:
    @pytest.mark.asyncio
    async def test_compacts_identical_reasoning_and_tool_rounds(self):
        config = ReasoningToolLoopCompactProcessorConfig(consecutive_threshold=3)
        ctx = await _create_context(config)

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        for index in range(1, 4):
            await ctx.add_messages(_todo_round(index))

        messages = ctx.get_messages()
        summaries = _loop_summary_messages(messages)
        assert len(summaries) == 1
        content = summaries[0].content
        tool_calls = _extract_json_block(content, "工具调用命令如下：")
        tool_results = _extract_json_block(content, "工具执行结果如下：")
        assert tool_calls == [{
            "name": "todo_create",
            "arguments": {
                "tasks": [{
                    "id": "literature_review",
                    "content": "文献综述",
                    "activeForm": "正在进行文献综述",
                    "description": "分析Introduction",
                }],
            },
        }]
        assert tool_results == [{
            "name": "todo_create",
            "content": "Successfully created task(s) round-3",
        }]
        assert "Successfully created task(s) round-1" not in content
        assert "Successfully created task(s) round-2" not in content

        # All matched tool rounds removed; only the summary Assistant remains.
        assert not any(
            isinstance(msg, AssistantMessage) and msg.tool_calls
            for msg in messages
        )
        assert not any(isinstance(msg, ToolMessage) for msg in messages)

    @pytest.mark.asyncio
    async def test_does_not_compact_below_threshold(self):
        config = ReasoningToolLoopCompactProcessorConfig(consecutive_threshold=3)
        ctx = await _create_context(config)

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        await ctx.add_messages(_todo_round(1))
        await ctx.add_messages(_todo_round(2))

        messages = ctx.get_messages()
        assert not _loop_summary_messages(messages)
        assert sum(1 for msg in messages if isinstance(msg, AssistantMessage) and msg.tool_calls) == 2

    @pytest.mark.asyncio
    async def test_requires_both_reasoning_and_tool_name_set_match(self):
        config = ReasoningToolLoopCompactProcessorConfig(consecutive_threshold=3)
        ctx = await _create_context(config)

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        await ctx.add_messages(_todo_round(1))
        await ctx.add_messages(_todo_round(2))
        await ctx.add_messages(
            _todo_round(
                3,
                arguments=(
                    '{"tasks":[{"id":"brainstorming","content":"头脑风暴",'
                    '"activeForm":"正在头脑风暴","description":"协作想法"}]}'
                ),
            )
        )

        messages = ctx.get_messages()
        summaries = _loop_summary_messages(messages)
        assert len(summaries) == 1
        tool_calls = _extract_json_block(summaries[0].content, "工具调用命令如下：")
        assert tool_calls[0]["arguments"]["tasks"][0]["id"] == "brainstorming"
        assert "literature_review" not in summaries[0].content
        assert not any(
            isinstance(msg, AssistantMessage) and msg.tool_calls
            for msg in messages
        )

    @pytest.mark.asyncio
    async def test_different_tool_name_set_breaks_streak(self):
        config = ReasoningToolLoopCompactProcessorConfig(consecutive_threshold=3)
        ctx = await _create_context(config)

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        await ctx.add_messages(_todo_round(1))
        await ctx.add_messages(_todo_round(2))
        tool_id = "tc-3"
        await ctx.add_messages([
            AssistantMessage(
                content="更新任务状态",
                reasoning_content=REASONING,
                tool_calls=[_tool_call(tool_id, "todo_modify", '{"action":"update"}')],
            ),
            ToolMessage(content="Successfully updated", tool_call_id=tool_id),
        ])

        messages = ctx.get_messages()
        assert not _loop_summary_messages(messages)
        assert sum(1 for msg in messages if isinstance(msg, AssistantMessage) and msg.tool_calls) == 3

    @pytest.mark.asyncio
    async def test_multi_tool_round_matches_by_tool_name_set(self):
        config = ReasoningToolLoopCompactProcessorConfig(consecutive_threshold=3)
        ctx = await _create_context(config)

        def _multi_tool_round(index: int) -> list:
            return [
                AssistantMessage(
                    content="并行执行",
                    reasoning_content=REASONING,
                    tool_calls=[
                        _tool_call(f"tc-{index}-a", "todo_modify", '{"action":"update"}'),
                        _tool_call(f"tc-{index}-b", "todo_list", "{}"),
                    ],
                ),
                ToolMessage(content=f"updated-{index}", tool_call_id=f"tc-{index}-a"),
                ToolMessage(content=f"listed-{index}", tool_call_id=f"tc-{index}-b"),
            ]

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        for index in range(1, 4):
            await ctx.add_messages(_multi_tool_round(index))

        messages = ctx.get_messages()
        summaries = _loop_summary_messages(messages)
        assert len(summaries) == 1
        tool_calls = _extract_json_block(summaries[0].content, "工具调用命令如下：")
        tool_results = _extract_json_block(summaries[0].content, "工具执行结果如下：")
        assert tool_calls == [
            {"name": "todo_modify", "arguments": {"action": "update"}},
            {"name": "todo_list", "arguments": {}},
        ]
        assert tool_results == [
            {"name": "todo_modify", "content": "updated-3"},
            {"name": "todo_list", "content": "listed-3"},
        ]
        assert "updated-1" not in summaries[0].content
        assert "listed-1" not in summaries[0].content
        assert not any(
            isinstance(msg, AssistantMessage) and msg.tool_calls
            for msg in messages
        )

    @pytest.mark.asyncio
    async def test_english_summary_when_language_set(self):
        config = ReasoningToolLoopCompactProcessorConfig(
            consecutive_threshold=3,
            language="en",
        )
        ctx = await _create_context(config)

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        for index in range(1, 4):
            await ctx.add_messages(_todo_round(index))

        messages = ctx.get_messages()
        summaries = [
            msg for msg in messages
            if isinstance(msg, AssistantMessage)
            and not msg.tool_calls
            and "Break out of the multi-turn repeated execution" in str(msg.content)
        ]
        assert len(summaries) == 1
        tool_calls = _extract_json_block(summaries[0].content, "Tool calls:")
        tool_results = _extract_json_block(summaries[0].content, "Tool results:")
        assert tool_calls[0]["name"] == "todo_create"
        assert tool_results == [{
            "name": "todo_create",
            "content": "Successfully created task(s) round-3",
        }]
        assert "Successfully created task(s) round-1" not in summaries[0].content

    @pytest.mark.asyncio
    async def test_records_bailout_signal_on_each_compaction(self):
        """Each successful fold increments the shared counter on the session."""
        config = ReasoningToolLoopCompactProcessorConfig(
            consecutive_threshold=3,
            bailout_threshold=2,
        )
        ctx = await _create_context(config)
        session = _FakeSession()
        ctx.set_session_ref(session)

        await ctx.add_messages(UserMessage(content="请生成研究想法"))

        # First loop of 3 identical rounds -> one fold -> counter == 1.
        for index in range(1, 4):
            await ctx.add_messages(_todo_round(index))
        assert len(_loop_summary_messages(ctx.get_messages())) == 1
        assert session.get_state(LOOP_COMPACT_BAILOUT_STATE_KEY) == 1

        # Model keeps looping: 3 more identical rounds -> second fold -> counter == 2.
        for index in range(4, 7):
            await ctx.add_messages(_todo_round(index))
        assert session.get_state(LOOP_COMPACT_BAILOUT_STATE_KEY) == 2

    @pytest.mark.asyncio
    async def test_bailout_disabled_does_not_record_signal(self):
        """bailout_threshold=0 folds as usual but never touches the counter."""
        config = ReasoningToolLoopCompactProcessorConfig(
            consecutive_threshold=3,
            bailout_threshold=0,
        )
        ctx = await _create_context(config)
        session = _FakeSession()
        ctx.set_session_ref(session)

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        for index in range(1, 4):
            await ctx.add_messages(_todo_round(index))

        # Compaction still happened, but no bail-out signal was recorded.
        assert len(_loop_summary_messages(ctx.get_messages())) == 1
        assert session.get_state(LOOP_COMPACT_BAILOUT_STATE_KEY) is None

    @pytest.mark.asyncio
    async def test_compaction_without_session_does_not_crash(self):
        """Folding must be a no-op on the counter when no session is bound."""
        config = ReasoningToolLoopCompactProcessorConfig(
            consecutive_threshold=3,
            bailout_threshold=2,
        )
        ctx = await _create_context(config)  # session is None

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        for index in range(1, 4):
            await ctx.add_messages(_todo_round(index))

        # No exception raised; compaction still worked.
        assert len(_loop_summary_messages(ctx.get_messages())) == 1

    @pytest.mark.asyncio
    async def test_disabled_config_skips_compaction(self):
        config = ReasoningToolLoopCompactProcessorConfig(
            enabled=False,
            consecutive_threshold=3,
        )
        ctx = await _create_context(config)
        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        for index in range(1, 4):
            await ctx.add_messages(_todo_round(index))

        messages = ctx.get_messages()
        assert sum(1 for msg in messages if isinstance(msg, AssistantMessage) and msg.tool_calls) == 3
        assert not _loop_summary_messages(messages)

    @pytest.mark.asyncio
    async def test_reasoning_rule_preferred_when_both_rules_match(self):
        """When both rules are simultaneously satisfied, the reasoning+tools rule wins.

        Default ``_todo_round`` keeps BOTH ``reasoning_content`` and the tool
        arguments identical, so with equal thresholds the reasoning+tools rule
        and the tools+args rule become eligible on the very same round. The code
        comment promises "Prefer the legacy reasoning+tools rule"; this asserts it.
        """
        config = ReasoningToolLoopCompactProcessorConfig(
            consecutive_threshold=3,
            tool_args_consecutive_threshold=3,  # equal -> both eligible on round 3
            bailout_threshold=2,
            tool_args_bailout_threshold=2,
        )
        ctx = await _create_context(config)
        session = _FakeSession()
        ctx.set_session_ref(session)

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        for index in range(1, 4):
            await ctx.add_messages(_todo_round(index))

        messages = ctx.get_messages()
        # Folded exactly once via the reasoning+tools template, not the tool-args one.
        assert len(_loop_summary_messages(messages)) == 1
        assert not _tool_args_loop_summary_messages(messages)
        # Definitive proof of which rule fired: only the reasoning counter moved.
        assert session.get_state(LOOP_COMPACT_BAILOUT_STATE_KEY) == 1
        assert session.get_state(TOOL_ARGS_LOOP_COMPACT_BAILOUT_STATE_KEY) is None
        # All raw tool rounds were folded away.
        assert not any(
            isinstance(msg, AssistantMessage) and msg.tool_calls
            for msg in messages
        )

    @pytest.mark.asyncio
    async def test_tool_args_compacts_identical_tools_and_arguments(self):
        """Same tools+args for N rounds compact even when reasoning differs."""
        config = ReasoningToolLoopCompactProcessorConfig(
            consecutive_threshold=99,  # disable reasoning rule
            tool_args_consecutive_threshold=5,
        )
        ctx = await _create_context(config)

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        for index in range(1, 6):
            await ctx.add_messages(_todo_round(index, reasoning=f"different reasoning {index}"))

        messages = ctx.get_messages()
        summaries = _tool_args_loop_summary_messages(messages)
        assert len(summaries) == 1
        tool_calls = _extract_json_block(summaries[0].content, "工具调用命令如下：")
        assert tool_calls[0]["name"] == "todo_create"
        assert not any(
            isinstance(msg, AssistantMessage) and msg.tool_calls
            for msg in messages
        )

    @pytest.mark.asyncio
    async def test_tool_args_does_not_compact_below_threshold(self):
        config = ReasoningToolLoopCompactProcessorConfig(
            consecutive_threshold=99,
            tool_args_consecutive_threshold=5,
        )
        ctx = await _create_context(config)

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        for index in range(1, 5):
            await ctx.add_messages(_todo_round(index, reasoning=f"r-{index}"))

        messages = ctx.get_messages()
        assert not _tool_args_loop_summary_messages(messages)
        assert sum(1 for msg in messages if isinstance(msg, AssistantMessage) and msg.tool_calls) == 4

    @pytest.mark.asyncio
    async def test_tool_args_different_arguments_break_streak(self):
        config = ReasoningToolLoopCompactProcessorConfig(
            consecutive_threshold=99,
            tool_args_consecutive_threshold=5,
        )
        ctx = await _create_context(config)

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        for index in range(1, 5):
            await ctx.add_messages(_todo_round(index, reasoning=f"r-{index}"))
        await ctx.add_messages(
            _todo_round(
                5,
                reasoning="r-5",
                arguments=(
                    '{"tasks":[{"id":"brainstorming","content":"头脑风暴",'
                    '"activeForm":"正在头脑风暴","description":"协作想法"}]}'
                ),
            )
        )

        messages = ctx.get_messages()
        assert not _tool_args_loop_summary_messages(messages)
        assert sum(1 for msg in messages if isinstance(msg, AssistantMessage) and msg.tool_calls) == 5

    @pytest.mark.asyncio
    async def test_tool_args_large_arguments_are_truncated_for_matching(self):
        config = ReasoningToolLoopCompactProcessorConfig(
            consecutive_threshold=99,
            tool_args_consecutive_threshold=5,
            arguments_max_chars=64,
        )
        ctx = await _create_context(config)

        prefix = "x" * 80
        args_a = json.dumps({"payload": prefix + "AAA"}, ensure_ascii=False)
        args_b = json.dumps({"payload": prefix + "BBB"}, ensure_ascii=False)

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        for index in range(1, 5):
            await ctx.add_messages(_todo_round(index, reasoning=f"r-{index}", arguments=args_a))
        await ctx.add_messages(_todo_round(5, reasoning="r-5", arguments=args_b))

        assert len(_tool_args_loop_summary_messages(ctx.get_messages())) == 1

    @pytest.mark.asyncio
    async def test_tool_args_records_bailout_signal_on_each_compaction(self):
        config = ReasoningToolLoopCompactProcessorConfig(
            consecutive_threshold=99,
            tool_args_consecutive_threshold=5,
            tool_args_bailout_threshold=2,
        )
        ctx = await _create_context(config)
        session = _FakeSession()
        ctx.set_session_ref(session)

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        for index in range(1, 6):
            await ctx.add_messages(_todo_round(index, reasoning=f"r-{index}"))
        assert len(_tool_args_loop_summary_messages(ctx.get_messages())) == 1
        assert session.get_state(TOOL_ARGS_LOOP_COMPACT_BAILOUT_STATE_KEY) == 1
        assert session.get_state(LOOP_COMPACT_BAILOUT_STATE_KEY) is None

        for index in range(6, 11):
            await ctx.add_messages(_todo_round(index, reasoning=f"r-{index}"))
        assert session.get_state(TOOL_ARGS_LOOP_COMPACT_BAILOUT_STATE_KEY) == 2

    @pytest.mark.asyncio
    async def test_tool_args_bailout_disabled_does_not_record_signal(self):
        config = ReasoningToolLoopCompactProcessorConfig(
            consecutive_threshold=99,
            tool_args_consecutive_threshold=5,
            tool_args_bailout_threshold=0,
        )
        ctx = await _create_context(config)
        session = _FakeSession()
        ctx.set_session_ref(session)

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        for index in range(1, 6):
            await ctx.add_messages(_todo_round(index, reasoning=f"r-{index}"))

        assert len(_tool_args_loop_summary_messages(ctx.get_messages())) == 1
        assert session.get_state(TOOL_ARGS_LOOP_COMPACT_BAILOUT_STATE_KEY) is None


def _poll_round(
    index: int,
    *,
    run_id: str = "run-abc",
    reasoning: str | None = None,
) -> list:
    """One completed image_get_run round (direct call)."""
    tool_id = f"poll-{index}"
    arguments = json.dumps({"run_id": run_id}, ensure_ascii=False)
    kwargs = {
        "content": f"poll status round-{index}",
        "tool_calls": [_tool_call(tool_id, "image_get_run", arguments)],
    }
    if reasoning is not None:
        kwargs["reasoning_content"] = reasoning
    return [
        AssistantMessage(**kwargs),
        ToolMessage(
            content=json.dumps({"status": "running", "run_id": run_id}, ensure_ascii=False),
            tool_call_id=tool_id,
        ),
    ]


def _invoke_tool_poll_round(
    index: int,
    *,
    inner_tool_name: str = "image_get_run",
    run_id: str = "run-abc",
    reasoning: str | None = None,
) -> list:
    """One completed invoke_tool round wrapping an inner poll tool (JSON-string args)."""
    tool_id = f"invoke-poll-{index}"
    arguments = json.dumps(
        {"tool_name": inner_tool_name, "arguments": {"run_id": run_id}},
        ensure_ascii=False,
    )
    kwargs = {
        "content": f"invoke poll status round-{index}",
        "tool_calls": [_tool_call(tool_id, "invoke_tool", arguments)],
    }
    if reasoning is not None:
        kwargs["reasoning_content"] = reasoning
    return [
        AssistantMessage(**kwargs),
        ToolMessage(
            content=json.dumps(
                {"status": "running", "run_id": run_id, "tool_name": inner_tool_name},
                ensure_ascii=False,
            ),
            tool_call_id=tool_id,
        ),
    ]


def _write_file_round(index: int, *, path: str = "/tmp/out.txt", content: str = "x") -> list:
    tool_id = f"write-{index}"
    arguments = json.dumps({"path": path, "content": content}, ensure_ascii=False)
    return [
        AssistantMessage(
            content=f"write file round-{index}",
            tool_calls=[_tool_call(tool_id, "write_file", arguments)],
        ),
        ToolMessage(content=f"wrote {path}", tool_call_id=tool_id),
    ]


def _ignored_poll_config(**overrides) -> ReasoningToolLoopCompactProcessorConfig:
    base = dict(
        consecutive_threshold=99,
        tool_args_consecutive_threshold=5,
        tool_args_bailout_threshold=2,
        ignored_tool_calls=[
            {"tool_name": "image_get_run", "argument_equals": {}},
            {
                "tool_name": "invoke_tool",
                "argument_equals": {"tool_name": "image_get_run"},
            },
        ],
    )
    base.update(overrides)
    return ReasoningToolLoopCompactProcessorConfig.model_validate(base)


class TestIgnoredToolCallsAndExemptWall:
    """ignored_tool_calls exemption for async poll tools (break wall; no fold/bailout)."""

    def test_invoke_tool_with_empty_argument_equals_is_rejected(self):
        with pytest.raises(Exception) as exc_info:
            ReasoningToolLoopCompactProcessorConfig.model_validate({
                "ignored_tool_calls": [
                    {"tool_name": "invoke_tool", "argument_equals": {}},
                ],
            })
        err = str(exc_info.value).lower()
        assert "invoke_tool" in err or "wrapper" in err or "argument_equals" in err

    @pytest.mark.asyncio
    async def test_direct_image_get_run_x12_does_not_compact_or_bailout(self):
        config = _ignored_poll_config()
        ctx = await _create_context(config)
        session = _FakeSession()
        ctx.set_session_ref(session)

        await ctx.add_messages(UserMessage(content="poll creative poster"))
        for index in range(1, 13):
            await ctx.add_messages(_poll_round(index))

        messages = ctx.get_messages()
        assert not _tool_args_loop_summary_messages(messages)
        assert not _loop_summary_messages(messages)
        assert sum(
            1 for msg in messages if isinstance(msg, AssistantMessage) and msg.tool_calls
        ) == 12
        assert session.get_state(TOOL_ARGS_LOOP_COMPACT_BAILOUT_STATE_KEY) is None
        assert session.get_state(LOOP_COMPACT_BAILOUT_STATE_KEY) is None

    @pytest.mark.asyncio
    async def test_wall_fixture_a3_poll_n_a2_does_not_fold(self):
        """write then exempt poll then write must not fold at threshold=5 (poll breaks streak)."""
        config = _ignored_poll_config(tool_args_consecutive_threshold=5)
        ctx = await _create_context(config)

        await ctx.add_messages(UserMessage(content="do work then poll"))
        for index in range(1, 4):
            await ctx.add_messages(_write_file_round(index, content="same"))
        for index in range(1, 6):
            await ctx.add_messages(_poll_round(index))
        for index in range(4, 6):
            await ctx.add_messages(_write_file_round(index, content="same"))

        messages = ctx.get_messages()
        assert not _tool_args_loop_summary_messages(messages)
        # 3 write + 5 poll + 2 write = 10 tool rounds still present
        assert sum(
            1 for msg in messages if isinstance(msg, AssistantMessage) and msg.tool_calls
        ) == 10

    @pytest.mark.asyncio
    async def test_empty_ignored_keeps_original_compaction(self):
        config = ReasoningToolLoopCompactProcessorConfig(
            consecutive_threshold=99,
            tool_args_consecutive_threshold=5,
            ignored_tool_calls=[],
        )
        ctx = await _create_context(config)
        await ctx.add_messages(UserMessage(content="poll"))
        for index in range(1, 6):
            await ctx.add_messages(_poll_round(index))

        assert len(_tool_args_loop_summary_messages(ctx.get_messages())) == 1

    @pytest.mark.asyncio
    async def test_non_exempt_write_file_still_compacts(self):
        config = _ignored_poll_config()
        ctx = await _create_context(config)
        await ctx.add_messages(UserMessage(content="write loop"))
        for index in range(1, 6):
            await ctx.add_messages(_write_file_round(index, content="same"))

        assert len(_tool_args_loop_summary_messages(ctx.get_messages())) == 1

    @pytest.mark.asyncio
    async def test_identical_reasoning_with_exempt_poll_does_not_fold(self):
        """identical reasoning with exempt poll must not fold via the reasoning consecutive rule."""
        same_reasoning = "再查一次状态，确认创意海报是否完成渲染。" * 2
        config = _ignored_poll_config(
            consecutive_threshold=3,
            tool_args_consecutive_threshold=99,
        )
        ctx = await _create_context(config)
        await ctx.add_messages(UserMessage(content="poll with reasoning"))
        for index in range(1, 4):
            await ctx.add_messages(_poll_round(index, reasoning=same_reasoning))

        messages = ctx.get_messages()
        assert not _loop_summary_messages(messages)
        assert sum(
            1 for msg in messages if isinstance(msg, AssistantMessage) and msg.tool_calls
        ) == 3

    @pytest.mark.asyncio
    async def test_invoke_tool_image_get_run_x12_does_not_compact_or_bailout(self):
        """Wrapper path: invoke_tool + tool_name=image_get_run stays exempt end-to-end."""
        config = _ignored_poll_config()
        ctx = await _create_context(config)
        session = _FakeSession()
        ctx.set_session_ref(session)

        await ctx.add_messages(UserMessage(content="poll creative poster via invoke_tool"))
        for index in range(1, 13):
            await ctx.add_messages(_invoke_tool_poll_round(index))

        messages = ctx.get_messages()
        assert not _tool_args_loop_summary_messages(messages)
        assert not _loop_summary_messages(messages)
        assert sum(
            1 for msg in messages if isinstance(msg, AssistantMessage) and msg.tool_calls
        ) == 12
        assert session.get_state(TOOL_ARGS_LOOP_COMPACT_BAILOUT_STATE_KEY) is None
        assert session.get_state(LOOP_COMPACT_BAILOUT_STATE_KEY) is None

    @pytest.mark.asyncio
    async def test_invoke_tool_mismatched_inner_name_still_compacts(self):
        """Same invoke_tool rule but inner tool_name mismatch must still fold at threshold."""
        config = _ignored_poll_config()
        ctx = await _create_context(config)

        await ctx.add_messages(UserMessage(content="poll other tool via invoke_tool"))
        for index in range(1, 6):
            await ctx.add_messages(
                _invoke_tool_poll_round(index, inner_tool_name="image_create_run")
            )

        messages = ctx.get_messages()
        assert len(_tool_args_loop_summary_messages(messages)) == 1
        assert not any(
            isinstance(msg, AssistantMessage) and msg.tool_calls
            for msg in messages
        )

    def test_is_tool_call_exempt_accepts_dict_arguments(self):
        """_parse_tool_arguments dict branch: exempt match without JSON-string arguments."""
        processor = ReasoningToolLoopCompactProcessor(_ignored_poll_config())
        hit = SimpleNamespace(
            name="invoke_tool",
            arguments={"tool_name": "image_get_run", "arguments": {"run_id": "run-abc"}},
        )
        miss = SimpleNamespace(
            name="invoke_tool",
            arguments={"tool_name": "image_create_run", "arguments": {"run_id": "run-abc"}},
        )
        assert processor._is_tool_call_exempt(hit) is True
        assert processor._is_tool_call_exempt(miss) is False
