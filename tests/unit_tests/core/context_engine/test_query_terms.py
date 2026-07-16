from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.query_terms import (
    extract_query_terms,
)


def test_extract_query_terms_skips_tool_file_path_values():
    terms = extract_query_terms(
        "调研 team leader member 通信方式",
        "read_file",
        {
            "file_path": r"D:\work\code\new_jiuwenswarm\agent-core\openjiuwen\agent_teams\agent\team_agent.py",
        },
    )

    assert "read_file" in terms
    assert "leader" in terms
    assert "member" in terms
    assert "file_path" in terms
    assert "team_agent" not in terms
    assert "agent_teams" not in terms
    assert "openjiuwen" not in terms
    assert "new_jiuwenswarm" not in terms


def test_extract_query_terms_keeps_non_path_tool_argument_values():
    terms = extract_query_terms(
        "inspect auth handling",
        "search",
        {"query": "token refresh failure"},
    )

    assert "query" in terms
    assert "token" in terms
    assert "refresh" in terms
    assert "failure" in terms


def test_extract_query_terms_adds_builtin_cjk_query_terms():
    terms = extract_query_terms("检查密码刷新失败")

    assert "password" in terms
    assert "refresh" in terms
    assert "failure" in terms
    assert "failed" in terms


def test_extract_query_terms_can_add_external_translated_cjk_query_terms():
    calls: list[str] = []

    def translate_query_text(text: str) -> str:
        calls.append(text)
        return "custom translator signal"

    terms = extract_query_terms(
        "检查中文查询",
        translate_query_text=translate_query_text,
    )

    assert calls == ["检查中文查询"]
    assert "chinese" in terms
    assert "query" in terms
    assert "custom" in terms
    assert "translator" in terms
    assert "signal" in terms


def test_extract_query_terms_does_not_translate_non_cjk_queries():
    calls: list[str] = []

    terms = extract_query_terms(
        "inspect password refresh failure",
        translate_query_text=lambda text: calls.append(text) or "unused translated text",
    )

    assert calls == []
    assert "password" in terms
    assert "refresh" in terms
    assert "failure" in terms
