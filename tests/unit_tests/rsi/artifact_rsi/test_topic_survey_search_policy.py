# coding: utf-8

import os

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.topic_survey.agent import (
    TopicSurveyAgent,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.topic_survey.schemas import (
    SurveySource,
    TopicSurveyDraft,
    TopicSurveyInput,
)


def _draft(*, url: str, source_type: str = "paper", local_path: str = "data/sources/source.html"):
    return TopicSurveyDraft(
        short_summary="summary",
        key_findings=["finding"],
        open_problems=["problem"],
        sources=[
            SurveySource(
                title="title",
                url=url,
                source_type=source_type,
                local_path=local_path,
                summary="summary",
                key_findings=["finding"],
            )
        ],
    )


def test_topic_survey_configured_free_search_does_not_mutate_process_env(monkeypatch):
    monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "false")
    monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "false")
    agent = TopicSurveyAgent(
        {"topic_survey": {"free_search": {"duckduckgo": True, "bing": False}}}
    )

    assert agent._configure_web_search() == ("duckduckgo",)
    assert agent._configure_web_search() is not None
    assert os.getenv("FREE_SEARCH_DDG_ENABLED") == "false"
    assert os.getenv("FREE_SEARCH_BING_ENABLED") == "false"
    assert agent._search_scope() == "global"


@pytest.mark.parametrize("web_proxy", [None, "http://proxy.example.test:7890"])
def test_topic_survey_proxy_only_changes_transport(web_proxy):
    topic_config = {}
    if web_proxy:
        topic_config["web_proxy"] = web_proxy
    agent = TopicSurveyAgent({"topic_survey": topic_config})

    assert agent._search_scope() == "global"


def test_topic_survey_without_proxy_reminds_agent_to_find_downloadable_sources():
    agent = TopicSurveyAgent({"topic_survey": {}})
    query = agent._build_survey_query(
        TopicSurveyInput(topic="retrieval systems"),
        relative_download_dir="data/sources",
    )

    assert "same global search, fetch, and download workflow" in query
    assert "directly accessible and downloadable" in query
    assert "skip it and search for another accessible source" in query


def test_topic_survey_with_proxy_keeps_same_query_workflow_without_no_proxy_hint():
    agent = TopicSurveyAgent(
        {"topic_survey": {"web_proxy": "http://proxy.example.test:7890"}}
    )
    query = agent._build_survey_query(
        TopicSurveyInput(topic="retrieval systems"),
        relative_download_dir="data/sources",
    )

    assert "Search for relevant papers and authoritative webpages" in query
    assert "same global search, fetch, and download workflow" not in query


def test_topic_survey_explicit_domestic_scope_remains_supported():
    agent = TopicSurveyAgent({"topic_survey": {"search_scope": "domestic"}})

    assert agent._search_scope() == "domestic"
    query = agent._build_survey_query(
        TopicSurveyInput(topic="retrieval systems"),
        relative_download_dir="data/sources",
    )
    assert "explicitly configured domestic academic sources" in query


def test_topic_survey_rejects_domestic_portal_home_as_paper():
    draft = _draft(url="https://www.cnki.net/")

    with pytest.raises(RuntimeError, match="did not produce a paper source"):
        TopicSurveyAgent._validate_paper_submission(draft)


def test_topic_survey_accepts_downloaded_pdf_even_if_model_used_web_page_type():
    draft = _draft(
        url="https://kns.cnki.net/kcms2/article/abstract?v=paper-1",
        source_type="web_page",
        local_path="data/sources/paper.pdf",
    )

    TopicSurveyAgent._validate_paper_submission(draft)
