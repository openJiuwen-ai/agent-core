from unittest.mock import patch

from openjiuwen.core.context_engine.token.tiktoken_counter import TiktokenCounter
from openjiuwen.core.foundation.llm import SystemMessage, UserMessage
from openjiuwen.core.foundation.tool import ToolInfo


def test_static_token_cache_preserves_counts_and_only_caches_stable_material(monkeypatch):
    monkeypatch.setenv("JIUWENSWARM_STATIC_ASSEMBLY_CACHE", "1")
    counter = TiktokenCounter()
    original_count = counter.count

    with patch.object(counter, "count", wraps=original_count) as count_spy:
        system = SystemMessage(content="stable system prefix " * 200)
        first_system_count = counter.count_messages([system])
        second_system_count = counter.count_messages([system])
        assert first_system_count == second_system_count
        assert count_spy.call_count == 1

        user = UserMessage(content="same user data")
        first_user_count = counter.count_messages([user])
        second_user_count = counter.count_messages([user])
        assert first_user_count == second_user_count
        assert count_spy.call_count == 3


def test_static_tool_schema_count_cache_preserves_exact_result(monkeypatch):
    monkeypatch.setenv("JIUWENSWARM_STATIC_ASSEMBLY_CACHE", "1")
    counter = TiktokenCounter()
    tool = ToolInfo(
        name="stable_tool",
        description="A stable tool schema",
        parameters={"type": "object", "properties": {"value": {"type": "string"}}},
    )
    original_count = counter.count

    with patch.object(counter, "count", wraps=original_count) as count_spy:
        first = counter.count_tools([tool])
        second = counter.count_tools([tool])
        assert first == second
        assert count_spy.call_count == 1
