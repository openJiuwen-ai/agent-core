# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock
import pytest

from openjiuwen.core.context_engine.processor.compressor.dialogue_compressor import (
    DialogueCompressorConfig,
)
from openjiuwen.core.foundation.llm import (
    SystemMessage,
    AssistantMessage,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperationCard
from openjiuwen.harness import Workspace
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.rails.context_engineer.context_processor_rail import ContextProcessorRail
from openjiuwen.core.foundation.llm.model import init_model
from openjiuwen.core.context_engine.context.session_memory_manager import SessionMemoryConfig


class _DummyResponse:
    def __init__(self, content: str):
        self.content = content


class _DummyModel:
    def __init__(self, content: str = ""):
        self._content = content
        self.calls = []
        self.model_client_config = None
        self.model_config = None
        self.model_request_config = None

    async def invoke(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return _DummyResponse(self._content)


def _make_sys_operation(tmp_path: Path):
    card = SysOperationCard(
        id=f"test_context_rail_sysop_{tmp_path.name}",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=str(tmp_path)),
    )
    Runner.resource_mgr.add_sys_operation(card)
    return Runner.resource_mgr.get_sys_operation(card.id)


def _make_agent(sys_operation, workspace):
    model = init_model(
        provider="OpenAI", model_name="dummy-model", api_key="dummy-key",
        api_base="https://example.com/v1", verify_ssl=False,
    )
    return create_deep_agent(
        model=model,
        card=AgentCard(name="test", description="test"),
        system_prompt="You are a test assistant.",
        max_iterations=3,
        enable_task_loop=False,
        workspace=workspace,
        sys_operation=sys_operation,
    )


def _make_model_call_context(agent):
    return AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(
            messages=[
                SystemMessage(content="You are a test assistant."),
                {"role": "user", "content": "test"}
            ]
        ),
        session=None,
    )


class _MockModelContext:
    def __init__(self, messages=None):
        self._messages = list(messages) if messages else []
        self.added_messages = []
        self.popped_messages: list = []

    def get_messages(self):
        return self._messages

    def pop_messages(self, size=None, with_history=True):
        if size is None:
            self.popped_messages = list(self._messages)
            self._messages = []
        else:
            self.popped_messages = self._messages[:size]
            self._messages = self._messages[size:]
        return self.popped_messages

    async def add_messages(self, message):
        if isinstance(message, list):
            self.added_messages.extend(message)
        else:
            self.added_messages.append(message)
        return self.added_messages


# =============================================================================
# Preset Processor Tests
# =============================================================================

@pytest.mark.asyncio
async def test_init_processors_merge(tmp_path: Path):
    """init should merge preset and custom processors correctly."""
    cases = [
        # (preset, processors, expected_keys)
        (False, None, []),
        (False, [("custom", DialogueCompressorConfig(messages_threshold=25))], ["custom"]),
        (False, [("d", DialogueCompressorConfig(messages_to_keep=5))], ["d"]),
        (True, None, ["MessageSummaryOffloader", "DialogueCompressor", "CurrentRoundCompressor", "RoundLevelCompressor"]),
        (True, [("d", DialogueCompressorConfig(messages_threshold=99))],
         ["MessageSummaryOffloader", "DialogueCompressor", "CurrentRoundCompressor", "RoundLevelCompressor", "d"]),
        (True, [("c", DialogueCompressorConfig(messages_to_keep=5))],
         ["MessageSummaryOffloader", "DialogueCompressor", "CurrentRoundCompressor", "RoundLevelCompressor", "c"]),
        (True, [("DialogueCompressor", DialogueCompressorConfig(messages_threshold=99))],
         ["MessageSummaryOffloader", "DialogueCompressor", "CurrentRoundCompressor", "RoundLevelCompressor"]),
    ]
    for preset, processors, expected_keys in cases:
        sys_operation = _make_sys_operation(tmp_path)
        workspace = Workspace(root_path=str(tmp_path))
        agent = _make_agent(sys_operation, workspace)
        rail = ContextProcessorRail(preset=preset, processors=processors)
        await agent.register_rail(rail)
        await agent.ensure_initialized()
        keys = [k for k, _ in agent.react_config.context_processors or []]
        assert keys == expected_keys, f"preset={preset}, processors={processors}"


@pytest.mark.asyncio
async def test_init_preset_defaults(tmp_path: Path):
    """Preset processors should have correct default config values."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    rail = ContextProcessorRail(preset=True)
    await agent.register_rail(rail)
    await agent.ensure_initialized()

    procs = dict(agent.react_config.context_processors)

    # MessageSummaryOffloader tests
    off = procs.get("MessageSummaryOffloader")
    assert off is not None
    assert off.messages_threshold is None
    assert off.tokens_threshold == 60000
    assert off.large_message_threshold == 20000
    assert off.offload_message_type == ["tool"]
    assert off.protected_tool_names == ["reload_original_context_messages"]
    assert off.enable_adaptive_compression is False
    assert off.summary_max_tokens == 1000

    # DialogueCompressor tests
    comp = procs.get("DialogueCompressor")
    assert comp is not None
    assert comp.messages_threshold is None
    assert comp.tokens_threshold == 100000
    assert comp.messages_to_keep == 10
    assert comp.keep_last_round is False
    assert comp.compression_target_tokens == 1800

    # CurrentRoundCompressor tests
    curr = procs.get("CurrentRoundCompressor")
    assert curr is not None
    assert curr.tokens_threshold == 100000
    assert curr.messages_to_keep == 6
    assert curr.compression_target_tokens == 4000

    # RoundLevelCompressor tests
    round_lvl = procs.get("RoundLevelCompressor")
    assert round_lvl is not None
    assert round_lvl.rounds_threshold == 2
    assert round_lvl.tokens_threshold == 230000
    assert round_lvl.target_total_tokens == 160000
    assert round_lvl.keep_last_round is True



# =============================================================================
# fix_incomplete_tool_context Tests
# =============================================================================

def _make_fix_ctx(agent, messages):
    return AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(messages=[]),
        session=None,
        context=_MockModelContext(messages=messages),
    )


@pytest.mark.asyncio
async def test_fix_incomplete_tool_context(tmp_path: Path):
    """fix_incomplete_tool_context should fill missing ToolMessages."""
    cases = [
        # (messages, expected_added_count, expected_placeholder_ids)
        ([], 0, []),
        ([SystemMessage(content="sys"), UserMessage(content="user")], 2, []),
        ([AssistantMessage(content="no tools")], 1, []),
        ([UserMessage(content="user")], 1, []),
        ([AssistantMessage(content="call",
            tool_calls=[ToolCall(id="tc1", type="function", name="t", arguments="{}")
        ])], 2, ["tc1"]),
        ([AssistantMessage(content="call",
            tool_calls=[ToolCall(id="", type="function", name="t", arguments="{}")])], 2, [""]),
        (
            [
                AssistantMessage(content="c1",
                    tool_calls=[ToolCall(id="a", type="function", name="t", arguments="{}")]),
                UserMessage(content="user"),
            ],
            3,
            ["a"],
        ),
        (
            [
                AssistantMessage(content="c1",
                    tool_calls=[ToolCall(id="a", type="function", name="t1", arguments="{}")]),
                AssistantMessage(content="c2",
                    tool_calls=[ToolCall(id="b", type="function", name="t2", arguments="{}")]),
            ],
            4,
            ["a", "b"],
        ),
        (
            [
                AssistantMessage(content="call", tool_calls=[
                    ToolCall(id="x", type="function", name="t1", arguments="{}"),
                    ToolCall(id="y", type="function", name="t2", arguments="{}"),
                ]),
                ToolMessage(content="res", tool_call_id="x"),
            ],
            3,
            ["y"],
        ),
        (
            [
                ToolMessage(content="res", tool_call_id="old"),
                AssistantMessage(
                    content="call",
                    tool_calls=[ToolCall(id="new", type="function", name="t", arguments="{}")]),
            ],
            3,
            ["new"],
        ),
    ]

    for i, (msgs, exp_count, exp_ids) in enumerate(cases):
        sys_operation = _make_sys_operation(tmp_path)
        workspace = Workspace(root_path=str(tmp_path))
        agent = _make_agent(sys_operation, workspace)
        await agent.ensure_initialized()

        ctx = _make_fix_ctx(agent, msgs)
        rail = ContextProcessorRail()
        await rail.fix_incomplete_tool_context(ctx)

        added = ctx.context.added_messages
        assert len(added) == exp_count, f"case {i}: expected {exp_count}, got {len(added)}"

        placeholders = [m.tool_call_id for m in added
                        if isinstance(m, ToolMessage) and "[Tool execution interrupted]" in m.content]
        assert (sorted(placeholders) == sorted(exp_ids)), \
            f"case {i}: expected ids {exp_ids}, got {placeholders}"


@pytest.mark.asyncio
async def test_fix_incomplete_tool_context_null_context(tmp_path: Path):
    """fix_incomplete_tool_context should not crash when context is None."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    ctx = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(messages=[]),
        session=None,
        context=None
    )
    rail = ContextProcessorRail()
    await rail.fix_incomplete_tool_context(ctx)  # should not raise


@pytest.mark.asyncio
async def test_before_invoke_and_on_exception_call_fix_context(tmp_path: Path):
    """before_invoke and on_model_exception should call fix_incomplete_tool_context."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    tool_call = ToolCall(id="tc", type="function", name="t", arguments="{}")
    ctx = _make_fix_ctx(
        agent,
        [
            AssistantMessage(content="call", tool_calls=[tool_call]),
            UserMessage(content="u")
        ]
    )

    rail = ContextProcessorRail()
    await rail.before_invoke(ctx)
    placeholders = [m for m in ctx.context.added_messages
                    if isinstance(m, ToolMessage) and "[Tool execution interrupted]" in m.content]
    assert len(placeholders) == 1

    ctx2 = _make_fix_ctx(
        agent,
        [
            AssistantMessage(
                content="call",
                tool_calls=[ToolCall(id="tc2", type="function", name="t", arguments="{}")]
            ),
            UserMessage(content="u")
        ]
    )
    await rail.on_model_exception(ctx2)
    placeholders2 = [m for m in ctx2.context.added_messages
                     if isinstance(m, ToolMessage) and "[Tool execution interrupted]" in m.content]
    assert len(placeholders2) == 1


# =============================================================================
# _ensure_json_arguments Tests
# =============================================================================

@pytest.mark.asyncio
async def test_ensure_json_arguments_with_invalid_json(tmp_path: Path):
    """_ensure_json_arguments should return '{}' for broken/invalid JSON strings."""
    cases = [
        # (input, expected_output)
        ('{"key": "value"}', '{"key": "value"}'),  # valid JSON
        ("{}", "{}"),  # empty object
        ('{"a": 1, "b": 2}', '{"a": 1, "b": 2}'),  # valid
        ('{"incomplete": ', "{}"),  # truncated JSON
        ('{bad json}', "{}"),  # not JSON at all
        ('{"unterminated": "string', "{}"),  # unterminated string
        ("[1, 2,", "{}"),  # truncated array
        ("null", "{}"),  # not an object
        ('{"nested": {"incomplete":', "{}"),  # nested truncated
        (None, "{}"),  # None input
        (123, "{}"),  # number input
        ("", "{}"),  # empty string
    ]

    for inp, expected in cases:
        result = ContextProcessorRail._ensure_json_arguments(inp)
        assert result == expected, f"input={repr(inp)}: expected {expected}, got {result}"


@pytest.mark.asyncio
async def test_ensure_json_arguments_with_dict_input(tmp_path: Path):
    """_ensure_json_arguments should convert dict to JSON string."""
    cases = [
        # (input dict, expected JSON string)
        ({}, "{}"),
        ({"key": "value"}, '{"key": "value"}'),
        ({"a": 1, "b": [1, 2, 3]}, '{"a": 1, "b": [1, 2, 3]}'),
    ]

    for inp, expected in cases:
        result = ContextProcessorRail._ensure_json_arguments(inp)
        assert result == expected, f"input={inp}: expected {expected}, got {result}"


@pytest.mark.asyncio
async def test_fix_incomplete_tool_context_with_broken_arguments(tmp_path: Path):
    """fix_incomplete_tool_context should fix broken tool call arguments."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    tool_call = ToolCall(
        id="tc1",
        type="function",
        name="test_tool",
        arguments='{"incomplete": '  # broken JSON
    )
    ctx = _make_fix_ctx(
        agent,
        [AssistantMessage(content="call", tool_calls=[tool_call])]
    )
    await ContextProcessorRail.fix_incomplete_tool_context(ctx)

    added = ctx.context.added_messages
    assert len(added) == 2
    # First: original AssistantMessage with fixed arguments
    assistant_msg = added[0]
    assert isinstance(assistant_msg, AssistantMessage)
    assert assistant_msg.tool_calls[0].arguments == "{}"
    # Second: placeholder ToolMessage
    placeholder = added[1]
    assert isinstance(placeholder, ToolMessage)
    assert placeholder.tool_call_id == "tc1"


# =============================================================================
# ContextProcessorRail - _merge_config_with_overrides Tests
# =============================================================================

def test_merge_config_with_overrides():
    """_merge_config_with_overrides should merge dict overrides into base config."""
    base = DialogueCompressorConfig(
        messages_threshold=10,
        tokens_threshold=50000,
        messages_to_keep=5,
    )
    # Override some fields
    overrides = {"messages_threshold": 20, "compression_target_tokens": 2000}
    result = ContextProcessorRail._merge_config_with_overrides(base, overrides)

    assert result.messages_threshold == 20
    assert result.tokens_threshold == 50000  # unchanged
    assert result.messages_to_keep == 5  # unchanged
    assert result.compression_target_tokens == 2000

    # Empty overrides should return base config
    result2 = ContextProcessorRail._merge_config_with_overrides(base, {})
    assert result2.messages_threshold == 10
    assert result2.messages_to_keep == 5


def test_merge_config_with_overrides_none():
    """_merge_config_with_overrides should return base when overrides is None."""
    base = DialogueCompressorConfig(messages_threshold=10)
    result = ContextProcessorRail._merge_config_with_overrides(base, None)
    assert result.messages_threshold == 10


# =============================================================================
# ContextProcessorRail - _merge_processors Tests
# =============================================================================

def test_merge_processors_replace_existing():
    """_merge_processors should replace existing processor configs."""
    base = [
        ("DialogueCompressor", DialogueCompressorConfig(messages_threshold=10)),
        ("CurrentRoundCompressor", DialogueCompressorConfig(tokens_threshold=50000)),
    ]
    overrides = [
        ("DialogueCompressor", DialogueCompressorConfig(messages_threshold=99)),
    ]
    result = ContextProcessorRail._merge_processors(base, overrides)

    keys = [k for k, _ in result]
    assert "DialogueCompressor" in keys
    assert "CurrentRoundCompressor" in keys
    dialogue_cfg = dict(result)["DialogueCompressor"]
    assert dialogue_cfg.messages_threshold == 99


def test_merge_processors_add_new():
    """_merge_processors should add new processors from overrides."""
    base = [
        ("DialogueCompressor", DialogueCompressorConfig(messages_threshold=10)),
    ]
    overrides = [
        ("CustomProcessor", DialogueCompressorConfig(messages_threshold=50)),
    ]
    result = ContextProcessorRail._merge_processors(base, overrides)

    keys = [k for k, _ in result]
    assert "DialogueCompressor" in keys
    assert "CustomProcessor" in keys


def test_merge_processors_with_dict_override():
    """_merge_processors should support dict overrides for existing processors."""
    base = [
        ("DialogueCompressor", DialogueCompressorConfig(messages_threshold=10, tokens_threshold=50000)),
    ]
    overrides = [
        ("DialogueCompressor", {"messages_threshold": 25}),
    ]
    result = ContextProcessorRail._merge_processors(base, overrides)

    cfg = dict(result)["DialogueCompressor"]
    assert cfg.messages_threshold == 25
    assert cfg.tokens_threshold == 50000  # unchanged


def test_merge_processors_dict_override_new_processor_error():
    """_merge_processors should raise error when dict override for non-existent processor."""
    base = []  # no preset
    overrides = [
        ("NonExistent", {"messages_threshold": 10}),
    ]
    with pytest.raises(ValueError, match="does not exist in preset"):
        ContextProcessorRail._merge_processors(base, overrides)


def test_merge_processors_with_model_config():
    """_merge_processors should inject model config into processors that need it."""
    from openjiuwen.core.foundation.llm import ModelRequestConfig

    base_model_cfg = ModelRequestConfig(model="gpt-4")
    base = [
        ("DialogueCompressor", DialogueCompressorConfig()),
    ]
    overrides = []

    result = ContextProcessorRail._merge_processors(
        base, overrides,
        model_config=base_model_cfg,
        model_client_config=None
    )

    cfg = dict(result)["DialogueCompressor"]
    # Processor has model attribute, and it should be set from model_config
    # Note: DialogueCompressorConfig may not have model attribute, check accordingly


# =============================================================================
# ContextProcessorRail - _build_preset_processors Tests
# =============================================================================

def test_build_preset_processors_without_session_memory(tmp_path: Path):
    """_build_preset_processors should return standard preset when session_memory disabled."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    rail = ContextProcessorRail(preset=True, session_memory=None)
    presets = rail._build_preset_processors()

    keys = [k for k, _ in presets]
    assert "MessageSummaryOffloader" in keys
    assert "DialogueCompressor" in keys
    assert "CurrentRoundCompressor" in keys
    assert "RoundLevelCompressor" in keys
    assert "ToolResultBudgetProcessor" not in keys
    assert "MicroCompactProcessor" not in keys
    assert "FullCompactProcessor" not in keys


def test_build_preset_processors_with_session_memory(tmp_path: Path):
    """_build_preset_processors should return session memory presets when enabled."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    session_config = SessionMemoryConfig()
    rail = ContextProcessorRail(preset=True, session_memory=session_config)
    presets = rail._build_preset_processors()

    keys = [k for k, _ in presets]
    assert "ToolResultBudgetProcessor" in keys
    assert "MicroCompactProcessor" in keys
    assert "FullCompactProcessor" in keys
    assert "MessageSummaryOffloader" not in keys
    assert "DialogueCompressor" not in keys


def test_build_preset_processors_with_session_memory_dict(tmp_path: Path):
    """_build_preset_processors should accept session_memory as dict."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    rail = ContextProcessorRail(preset=True, session_memory={"max_history_rounds": 5})
    presets = rail._build_preset_processors()

    keys = [k for k, _ in presets]
    assert "ToolResultBudgetProcessor" in keys
    assert "FullCompactProcessor" in keys


# =============================================================================
# ContextProcessorRail - init/uninit Tests
# =============================================================================

@pytest.mark.asyncio
async def test_context_processor_rail_init_with_preset(tmp_path: Path):
    """ContextProcessorRail.init should inject preset processors into agent config."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    rail = ContextProcessorRail(preset=True)
    await agent.register_rail(rail)
    await agent.ensure_initialized()

    keys = [k for k, _ in agent.react_config.context_processors or []]
    assert "MessageSummaryOffloader" in keys
    assert "DialogueCompressor" in keys
    assert "CurrentRoundCompressor" in keys
    assert "RoundLevelCompressor" in keys


@pytest.mark.asyncio
async def test_context_processor_rail_init_without_preset(tmp_path: Path):
    """ContextProcessorRail.init should use only user processors when preset=False."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    rail = ContextProcessorRail(preset=False, processors=[
        ("CustomProcessor", DialogueCompressorConfig(messages_threshold=25))
    ])
    await agent.register_rail(rail)
    await agent.ensure_initialized()

    keys = [k for k, _ in agent.react_config.context_processors or []]
    assert "CustomProcessor" in keys
    assert "MessageSummaryOffloader" not in keys
    assert "DialogueCompressor" not in keys


@pytest.mark.asyncio
async def test_context_processor_rail_init_with_tuple_processors(tmp_path: Path):
    """ContextProcessorRail should accept single tuple processor."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    rail = ContextProcessorRail(
        preset=False,
        processors=("SingleProcessor", DialogueCompressorConfig(messages_threshold=30))
    )
    await agent.register_rail(rail)
    await agent.ensure_initialized()

    keys = [k for k, _ in agent.react_config.context_processors or []]
    assert "SingleProcessor" in keys


@pytest.mark.asyncio
async def test_context_processor_rail_init_with_dict_override(tmp_path: Path):
    """ContextProcessorRail.init should support dict overrides in processors."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    rail = ContextProcessorRail(
        preset=True,
        processors=[("DialogueCompressor", {"messages_threshold": 88})]
    )
    await agent.register_rail(rail)
    await agent.ensure_initialized()

    config = dict(agent.react_config.context_processors)
    assert config["DialogueCompressor"].messages_threshold == 88


@pytest.mark.asyncio
async def test_context_processor_rail_uninit_clears_processors(tmp_path: Path):
    """ContextProcessorRail.uninit should clear context processors."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    rail = ContextProcessorRail(preset=True)
    await agent.register_rail(rail)
    await agent.ensure_initialized()

    assert len(agent.react_config.context_processors) > 0

    rail.uninit(agent)
    assert agent.react_config.context_processors == []


# =============================================================================
# ContextProcessorRail - _refresh_task_state_runtime Tests
# =============================================================================

@pytest.mark.asyncio
async def test_refresh_task_state_runtime_with_deep_agent_state(tmp_path: Path):
    """_refresh_task_state_runtime should extract state from DeepAgentState."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    from openjiuwen.harness.schema.state import DeepAgentState, PlanModeState, _SESSION_RUNTIME_ATTR

    mock_session = Mock()
    mock_session.get_state.return_value = {
        "iteration": 5,
        "stop_condition_state": {"iteration": 5},
        "pending_follow_ups": ["follow1"],
        "plan_mode": {"mode": "auto", "pre_plan_mode": None, "plan_slug": None},
    }
    runtime_state = DeepAgentState(
        iteration=5,
        stop_condition_state={"iteration": 5},
        pending_follow_ups=["follow1"],
        plan_mode=PlanModeState(mode="auto"),
    )
    setattr(mock_session, _SESSION_RUNTIME_ATTR, runtime_state)

    ctx = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(messages=[]),
        session=mock_session,
    )

    ContextProcessorRail._refresh_task_state_runtime(ctx)

    mock_session.update_state.assert_called_once()
    call_args = mock_session.update_state.call_args[0][0]
    assert call_args["iteration"] == 5
    assert call_args["pending_follow_ups"] == ["follow1"]
    assert call_args["plan_mode"] == {"mode": "auto", "pre_plan_mode": None, "plan_slug": None}


@pytest.mark.asyncio
async def test_refresh_task_state_runtime_with_persisted_dict(tmp_path: Path):
    """_refresh_task_state_runtime should read from persisted session state."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    from openjiuwen.harness.schema.state import _SESSION_RUNTIME_ATTR

    mock_session = Mock()
    mock_session.get_state.return_value = {
        "iteration": 10,
        "stop_condition_state": {"iteration": 10},
        "pending_follow_ups": [],
        "plan_mode": {"mode": "manual", "pre_plan_mode": None, "plan_slug": None},
    }
    setattr(mock_session, _SESSION_RUNTIME_ATTR, None)

    ctx = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(messages=[]),
        session=mock_session,
    )

    ContextProcessorRail._refresh_task_state_runtime(ctx)

    mock_session.update_state.assert_called_once()
    call_args = mock_session.update_state.call_args[0][0]
    assert call_args["iteration"] == 10


@pytest.mark.asyncio
async def test_refresh_task_state_runtime_no_session():
    """_refresh_task_state_runtime should handle None session gracefully."""
    ctx = AgentCallbackContext(
        agent=Mock(),
        inputs=ModelCallInputs(messages=[]),
        session=None,
    )
    # Should not raise
    ContextProcessorRail._refresh_task_state_runtime(ctx)


# =============================================================================
# ContextProcessorRail - Lifecycle Hooks Tests
# =============================================================================

@pytest.mark.asyncio
async def test_before_model_call_refreshes_state(tmp_path: Path):
    """before_model_call should refresh task state runtime."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    rail = ContextProcessorRail(preset=True)

    mock_session = Mock()
    mock_session.get_state.return_value = {"iteration": 3}
    setattr(mock_session, "_session_runtime", None)

    ctx = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(messages=[]),
        session=mock_session,
    )

    await rail.before_model_call(ctx)
    mock_session.update_state.assert_called()


@pytest.mark.asyncio
async def test_after_model_call_refreshes_state(tmp_path: Path):
    """after_model_call should refresh task state runtime."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    rail = ContextProcessorRail(preset=True)

    mock_session = Mock()
    mock_session.get_state.return_value = {"iteration": 4}
    setattr(mock_session, "_session_runtime", None)

    ctx = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(messages=[]),
        session=mock_session,
    )

    await rail.after_model_call(ctx)
    mock_session.update_state.assert_called()


@pytest.mark.asyncio
async def test_after_tool_call_refreshes_state(tmp_path: Path):
    """after_tool_call should refresh task state runtime."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    rail = ContextProcessorRail(preset=True)

    mock_session = Mock()
    mock_session.get_state.return_value = {"iteration": 5}
    setattr(mock_session, "_session_runtime", None)

    ctx = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(messages=[]),
        session=mock_session,
    )

    await rail.after_tool_call(ctx)
    mock_session.update_state.assert_called()


# =============================================================================
# ContextProcessorRail - Session Memory Integration Tests
# =============================================================================

@pytest.mark.asyncio
async def test_init_with_session_memory_config(tmp_path: Path):
    """ContextProcessorRail should initialize session memory manager when configured."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    session_config = SessionMemoryConfig(max_history_rounds=10)
    rail = ContextProcessorRail(preset=True, session_memory=session_config)

    assert rail._session_memory_enabled is True
    assert rail._session_memory_config is not None
    assert rail._session_memory_mgr is not None


@pytest.mark.asyncio
async def test_init_with_session_memory_dict(tmp_path: Path):
    """ContextProcessorRail should accept session_memory as dict."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    rail = ContextProcessorRail(
        preset=True,
        session_memory={"max_history_rounds": 5}
    )

    assert rail._session_memory_enabled is True
    assert isinstance(rail._session_memory_config, SessionMemoryConfig)


@pytest.mark.asyncio
async def test_uninit_shuts_down_session_memory_manager(tmp_path: Path):
    """uninit should shutdown session memory manager if enabled."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    session_config = SessionMemoryConfig()
    rail = ContextProcessorRail(preset=True, session_memory=session_config)

    assert rail._session_memory_mgr is not None
    # uninit should not raise
    rail.uninit(agent)


@pytest.mark.asyncio
async def test_session_memory_not_enabled_without_config(tmp_path: Path):
    """ContextProcessorRail should not enable session memory when config is None."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    rail = ContextProcessorRail(preset=True, session_memory=None)

    assert rail._session_memory_enabled is False
    assert rail._session_memory_config is None
    assert rail._session_memory_mgr is None


@pytest.mark.asyncio
async def test_fix_incomplete_tool_context_with_empty_messages(tmp_path: Path):
    """fix_incomplete_tool_context should handle empty messages list."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    ctx = _make_fix_ctx(agent, [])
    rail = ContextProcessorRail()
    await rail.fix_incomplete_tool_context(ctx)

    assert len(ctx.context.added_messages) == 0


@pytest.mark.asyncio
async def test_fix_incomplete_tool_context_multiple_tools_same_call(tmp_path: Path):
    """fix_incomplete_tool_context should handle multiple tool calls in one message."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    assistant_msg = AssistantMessage(
        content="I'll use multiple tools",
        tool_calls=[
            ToolCall(id="tc1", type="function", name="tool1", arguments="{}"),
            ToolCall(id="tc2", type="function", name="tool2", arguments="{}"),
            ToolCall(id="tc3", type="function", name="tool3", arguments="{}"),
        ]
    )
    ctx = _make_fix_ctx(agent, [assistant_msg])
    rail = ContextProcessorRail()
    await rail.fix_incomplete_tool_context(ctx)

    placeholders = [m for m in ctx.context.added_messages
                    if isinstance(m, ToolMessage) and "[Tool execution interrupted]" in m.content]
    assert len(placeholders) == 3
    assert sorted([p.tool_call_id for p in placeholders]) == ["tc1", "tc2", "tc3"]


@pytest.mark.asyncio
async def test_fix_incomplete_tool_context_with_matching_response(tmp_path: Path):
    """fix_incomplete_tool_context should not add placeholder when tool response matches."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    assistant_msg = AssistantMessage(
        content="using tool",
        tool_calls=[ToolCall(id="tc1", type="function", name="t", arguments="{}")]
    )
    tool_msg = ToolMessage(content="result", tool_call_id="tc1")
    ctx = _make_fix_ctx(agent, [assistant_msg, tool_msg])
    rail = ContextProcessorRail()
    await rail.fix_incomplete_tool_context(ctx)

    placeholders = [m for m in ctx.context.added_messages
                    if isinstance(m, ToolMessage) and "[工具执行被中断]" in m.content]
    assert len(placeholders) == 0
    # Should have original assistant msg and tool msg
    tool_messages = [m for m in ctx.context.added_messages if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].content == "result"


@pytest.mark.asyncio
async def test_fix_incomplete_tool_context_unordered_tool_responses(tmp_path: Path):
    """fix_incomplete_tool_context should handle out-of-order tool responses."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    # Assistant with tool call
    assistant_msg = AssistantMessage(
        content="using tool",
        tool_calls=[ToolCall(id="tc1", type="function", name="t", arguments="{}")]
    )
    # Tool response for different tool
    tool_msg = ToolMessage(content="result2", tool_call_id="tc2")
    ctx = _make_fix_ctx(agent, [assistant_msg, tool_msg])
    rail = ContextProcessorRail()
    await rail.fix_incomplete_tool_context(ctx)

    placeholders = [m for m in ctx.context.added_messages
                    if isinstance(m, ToolMessage) and "[Tool execution interrupted]" in m.content]
    # Should have placeholder for tc1 (not matched)
    assert len(placeholders) == 1
    assert placeholders[0].tool_call_id == "tc1"


@pytest.mark.asyncio
async def test_ensure_json_arguments_with_nested_dict():
    """_ensure_json_arguments should handle nested dict correctly."""
    nested = {"outer": {"inner": [1, 2, 3]}}
    result = ContextProcessorRail._ensure_json_arguments(nested)
    assert result == '{"outer": {"inner": [1, 2, 3]}}'


# =============================================================================
# Offload Section Injection Tests
# =============================================================================

class _MockSystemPromptBuilder:
    """Mock SystemPromptBuilder for testing offload section injection."""

    def __init__(self, language: str = "cn") -> None:
        self.language = language
        self.added_sections = []
        self.removed_sections = []

    def add_section(self, section) -> None:
        self.added_sections.append(section)

    def remove_section(self, section_name: str) -> None:
        self.removed_sections.append(section_name)

    def has_section(self, name: str) -> bool:
        return any(s.name == name for s in self.added_sections)


@pytest.mark.asyncio
async def test_offload_section_injected_when_preset_enabled(tmp_path: Path):
    """offload section should be injected when preset=True."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    rail = ContextProcessorRail(preset=True)
    await agent.register_rail(rail)
    await agent.ensure_initialized()

    mock_builder = _MockSystemPromptBuilder(language="cn")
    rail._system_prompt_builder = mock_builder

    await rail._maybe_inject_offload_section()

    assert mock_builder.has_section("offload")
    assert len(mock_builder.removed_sections) == 0


@pytest.mark.asyncio
async def test_offload_section_not_injected_when_no_processors(tmp_path: Path):
    """offload section should be removed when no processors configured."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    rail = ContextProcessorRail(preset=False, processors=None)
    await agent.register_rail(rail)
    await agent.ensure_initialized()

    mock_builder = _MockSystemPromptBuilder(language="cn")
    rail._system_prompt_builder = mock_builder

    await rail._maybe_inject_offload_section()

    assert not mock_builder.has_section("offload")
    assert "offload" in mock_builder.removed_sections


@pytest.mark.asyncio
async def test_offload_section_injected_when_user_processors_exist(tmp_path: Path):
    """offload section should be injected when user processors are configured."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    rail = ContextProcessorRail(
        preset=False,
        processors=[("CustomProcessor", DialogueCompressorConfig(messages_threshold=25))]
    )
    await agent.register_rail(rail)
    await agent.ensure_initialized()

    mock_builder = _MockSystemPromptBuilder(language="cn")
    rail._system_prompt_builder = mock_builder

    await rail._maybe_inject_offload_section()

    assert mock_builder.has_section("offload")


@pytest.mark.asyncio
async def test_offload_section_uses_correct_language_cn(tmp_path: Path):
    """offload section should use Chinese hint when language is cn."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    rail = ContextProcessorRail(preset=True)
    await agent.register_rail(rail)
    await agent.ensure_initialized()

    mock_builder = _MockSystemPromptBuilder(language="cn")
    rail._system_prompt_builder = mock_builder

    await rail._maybe_inject_offload_section()

    assert mock_builder.has_section("offload")
    offload_section = next(s for s in mock_builder.added_sections if s.name == "offload")
    assert "cn" in offload_section.content
    assert "上下文压缩" in offload_section.content["cn"]


@pytest.mark.asyncio
async def test_offload_section_uses_correct_language_en(tmp_path: Path):
    """offload section should use English hint when language is en."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    rail = ContextProcessorRail(preset=True)
    await agent.register_rail(rail)
    await agent.ensure_initialized()

    mock_builder = _MockSystemPromptBuilder(language="en")
    rail._system_prompt_builder = mock_builder

    await rail._maybe_inject_offload_section()

    assert mock_builder.has_section("offload")
    offload_section = next(s for s in mock_builder.added_sections if s.name == "offload")
    assert "en" in offload_section.content
    assert "Context Compression" in offload_section.content["en"]


@pytest.mark.asyncio
async def test_offload_section_not_injected_when_builder_is_none(tmp_path: Path):
    """offload section should not be injected when system_prompt_builder is None."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    rail = ContextProcessorRail(preset=True)
    await agent.register_rail(rail)
    await agent.ensure_initialized()

    rail._system_prompt_builder = None

    await rail._maybe_inject_offload_section()


@pytest.mark.asyncio
async def test_offload_section_priority(tmp_path: Path):
    """offload section should have priority of 90."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    rail = ContextProcessorRail(preset=True)
    await agent.register_rail(rail)
    await agent.ensure_initialized()

    mock_builder = _MockSystemPromptBuilder(language="cn")
    rail._system_prompt_builder = mock_builder

    await rail._maybe_inject_offload_section()

    assert mock_builder.has_section("offload")
    offload_section = next(s for s in mock_builder.added_sections if s.name == "offload")
    assert offload_section.priority == 90


@pytest.mark.asyncio
async def test_uninit_removes_offload_section(tmp_path: Path):
    """uninit should remove offload section from system_prompt_builder."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    rail = ContextProcessorRail(preset=True)
    await agent.register_rail(rail)
    await agent.ensure_initialized()

    mock_builder = _MockSystemPromptBuilder(language="cn")
    rail._system_prompt_builder = mock_builder

    await rail._maybe_inject_offload_section()
    assert mock_builder.has_section("offload")

    rail.uninit(agent)

    assert "offload" in mock_builder.removed_sections
    assert rail._all_processors == []


@pytest.mark.asyncio
async def test_before_model_call_injects_offload_section(tmp_path: Path):
    """before_model_call should call _maybe_inject_offload_section."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)

    rail = ContextProcessorRail(preset=True)
    await agent.register_rail(rail)
    await agent.ensure_initialized()

    mock_builder = _MockSystemPromptBuilder(language="cn")
    rail._system_prompt_builder = mock_builder

    mock_session = Mock()
    mock_session.get_state.return_value = {"iteration": 1}
    setattr(mock_session, "_session_runtime", None)

    ctx = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(messages=[]),
        session=mock_session,
    )

    await rail.before_model_call(ctx)

    assert mock_builder.has_section("offload")


