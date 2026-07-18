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


def test_extract_query_terms_does_not_translate_cjk_query_terms():
    terms = extract_query_terms("检查密码刷新失败")

    assert "password" not in terms
    assert "refresh" not in terms
    assert "failure" not in terms
    assert "failed" not in terms


def test_extract_query_terms_keeps_english_terms_inside_cjk_queries():
    terms = extract_query_terms("检查 password refresh failure")

    assert "password" in terms
    assert "refresh" in terms
    assert "failure" in terms
