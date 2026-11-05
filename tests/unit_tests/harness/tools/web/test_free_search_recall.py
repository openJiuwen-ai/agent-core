# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Parser and relevance regressions through the real free-search entry point."""

import copy
import json

import pytest

from openjiuwen.harness.tools.web import WebFreeSearchTool, _http
from openjiuwen.harness.tools.web._quality import WebContentError, challenge_reason
from openjiuwen.harness.tools.web.free_search import _parse_ddg_reader, _select_search_candidates
from openjiuwen.harness.tools.web._assessment import CandidateAssessment, SearchAssessment


READER_PAGE = """Title: AI Agent progress at DuckDuckGo
URL Source: https://duckduckgo.com/html/?q=AI+Agent+progress
Markdown Content:
## [A practical guide](https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fguide)

[![Image 1](https://external-content.duckduckgo.com/ip3/example.org.ico)](https://example.org/guide)[example.org/guide](https://example.org/guide)

[AI agents gain new capabilities in this release.](https://example.org/guide)

## [Second result](https://other.invalid/story)

[other.invalid/story](https://other.invalid/story)

A completely different snippet.

[Feedback](https://duckduckgo.com/feedback.html)
"""


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, scripted_search_model):
    scripted_search_model.labels.update({
        "Second result": "rejected", "Remote desktop": "rejected",
        "AI agents take the next step": "uncertain",
    })
    for name in (
        "FREE_SEARCH_DEBUG", "FREE_SEARCH_DEBUG_DIR", "FREE_SEARCH_DDG_ENABLED",
        "FREE_SEARCH_BING_ENABLED", "FREE_SEARCH_DDG_URL",
        "FREE_SEARCH_BING_MARKET", "FREE_SEARCH_BING_FALLBACK_MARKET",
        "WEB_RESEARCH_SEARCHES_PER_ENGINE", "WEB_RESEARCH_MAX_ENGINES", "WEB_RESEARCH_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_reader_snippets_are_bound_to_their_result_block():
    rows = _parse_ddg_reader(READER_PAGE, 10)
    assert len(rows) == 2
    assert rows[0] == {
        "title": "A practical guide", "url": "https://example.org/guide",
        "snippet": "AI agents gain new capabilities in this release.", "source": "duckduckgo-jina",
    }
    assert rows[1]["snippet"] == "A completely different snippet."
    assert "different" not in rows[0]["snippet"]
    assert _parse_ddg_reader(READER_PAGE, 1) == rows[:1]


def test_reader_parses_escaped_labels_balanced_url_and_plain_paragraphs():
    rows = _parse_ddg_reader(
        "## [Async \\[API\\] **guide**](https://example.org/doc_(async))\n\n"
        "A multiline\nsummary with *formatting* and &amp; entities.\n\n"
        "## [No summary](https://example.org/empty)\n\n"
        "## [Third](https://example.org/third)\n\nOnly third summary.", 10,
    )
    assert rows[0]["url"] == "https://example.org/doc_(async)"
    assert rows[0]["title"] == "Async [API] guide"
    assert rows[0]["snippet"] == "A multiline summary with formatting and & entities."
    assert rows[1]["snippet"] == ""
    assert rows[2]["snippet"] == "Only third summary."


def test_reader_unheaded_links_keep_own_plain_summary_without_merging_results():
    rows = _parse_ddg_reader(
        "# Search results\n\n[One](https://example.org/one)\n\nOne summary.\n\n"
        "[Two](https://example.org/two)\n\nTwo summary.", 10,
    )
    assert [row["snippet"] for row in rows] == ["One summary.", "Two summary."]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["duckduckgo", "ddg"])
async def test_reader_summary_reaches_tool_output_and_provenance(monkeypatch, provider):
    calls = []

    async def request(session, method, url, **kwargs):
        calls.append(url)
        page = READER_PAGE if "r.jina.ai" in url else "temporary server failure"
        return (200 if "r.jina.ai" in url else 500), {}, page.encode(), url, False

    recorded = []
    monkeypatch.setattr(_http, "request", request)
    tool = WebFreeSearchTool(enabled_engines=("duckduckgo", "bing"))
    monkeypatch.setattr(tool, "_on_search_results", lambda rows: recorded.extend(rows))
    result = await tool.invoke({"query": "AI Agent latest developments", "provider": provider})
    assert "Free search results (DuckDuckGo (via jina.ai))" in result
    assert "Snippet: AI agents gain new capabilities" in result
    assert "other.invalid" not in result
    assert len(calls) == 2 and all("bing.com" not in url for url in calls)
    assert recorded[0]["url"] == "https://example.org/guide"


@pytest.mark.parametrize("provider", ["duckduckgo", "bing"])
@pytest.mark.asyncio
async def test_diagnostics_keep_raw_candidates_and_rejection_reasons(monkeypatch, tmp_path, provider):
    monkeypatch.setenv("FREE_SEARCH_DEBUG", "true")
    monkeypatch.setenv("FREE_SEARCH_DEBUG_DIR", str(tmp_path))
    good = "https://allowed.invalid/good"
    unrelated = "https://allowed.invalid/unrelated"
    outside = "https://outside.invalid/good"
    if provider == "bing":
        page = "".join(
            f'<li class="b_algo"><h2><a href="{url}">{title}</a></h2><p>{snippet}</p></li>'
            for url, title, snippet in ((good, "杭州天气", "气温预报"), (unrelated, "Remote desktop", "Support"), (outside, "杭州天气", "气温预报"))
        )
    else:
        page = "".join(
            f'<div class="result"><h2><a class="result__a" href="{url}">{title}</a></h2><a class="result__snippet">{snippet}</a></div>'
            for url, title, snippet in ((good, "杭州天气", "气温预报"), (unrelated, "Remote desktop", "Support"), (outside, "杭州天气", "气温预报"))
        )

    async def request(session, method, url, **kwargs):
        return 200, {"Content-Type": "text/html; charset=utf-8", "Authorization": "never-log-this"}, page.encode(), url, False

    monkeypatch.setattr(_http, "request", request)
    result = await WebFreeSearchTool(enabled_engines=(provider,), allowed_domains=("allowed.invalid",)).invoke({
        "query": "杭州天气", "provider": provider, "max_results": 1,
    })
    assert good in result and unrelated not in result and outside not in result
    raw = json.loads(next(tmp_path.glob("*_candidate.json")).read_text(encoding="utf-8"))
    filtered = json.loads(next(tmp_path.glob("*_filtered.json")).read_text(encoding="utf-8"))
    assert len(raw["rows"]) == 3 and len(filtered["rows"]) == 1
    assert filtered["decisions"][1]["reasons"] == ["scripted judgment"]
    assert filtered["decisions"][2]["reasons"] == ["outside_allowed_domains"]
    assert len(list(tmp_path.glob("*_response.json"))) == 1
    assert all("never-log-this" not in path.read_text(encoding="utf-8") for path in tmp_path.glob("*.json"))


def test_uncertain_candidates_are_bounded_ranked_and_input_rows_remain_intact():
    rows = [{"title": "Unknown subject", "url": f"https://example.org/{i}", "snippet": "Excerpt"} for i in range(5)]
    rows += [dict(rows[4]), {"title": "Navigation", "url": "https://bing.com/search?q=anything"}]
    original = copy.deepcopy(rows)
    assessment = SearchAssessment(status="ok", results=[
        CandidateAssessment(id=4, relevance="relevant", reason="Useful"),
        *[CandidateAssessment(id=i, relevance="uncertain", reason="Missing detail") for i in range(4)],
    ])
    selected, decisions = _select_search_candidates("Any query", rows, assessment=assessment)
    assert rows == original
    assert selected[0]["url"] == rows[4]["url"]
    assert len(selected) == 3 and all(row["relevance"] == "uncertain" for row in selected[1:])
    assert decisions[2]["reasons"] == ["Missing detail", "uncertain_limit"]
    assert decisions[5]["reasons"] == ["duplicate_url"]
    assert decisions[6]["reasons"] == ["search_navigation"]


@pytest.mark.asyncio
async def test_uncertain_only_results_are_explicit_to_model(monkeypatch):
    async def request(session, method, url, **kwargs):
        html = '<li class="b_algo"><h2><a href="https://example.org/agents">AI agents take the next step</a></h2><p>Capabilities</p></li>'
        return 200, {}, html.encode(), url, False

    monkeypatch.setattr(_http, "request", request)
    result = await WebFreeSearchTool(enabled_engines=("bing",)).invoke({"query": "AI Agent news", "provider": "bing"})
    assert "Content status: uncertain" in result
    assert "Relevance: uncertain (scripted judgment)" in result
    assert "Content status: ok" not in result


@pytest.mark.asyncio
async def test_verification_page_is_not_an_empty_search(monkeypatch, tmp_path):
    monkeypatch.setenv("FREE_SEARCH_DEBUG", "true")
    monkeypatch.setenv("FREE_SEARCH_DEBUG_DIR", str(tmp_path))
    calls = []

    async def request(session, method, url, **kwargs):
        calls.append(url)
        return 200, {}, b'<title>Just a moment...</title><div id="app"></div>', "https://verification.invalid/", False

    monkeypatch.setattr(_http, "request", request)
    result = await WebFreeSearchTool(enabled_engines=("bing",)).invoke({"query": "西安天气", "provider": "bing"})
    assert "Content status: blocked" in result and "empty" not in result
    assert len(calls) == 1
    response = json.loads(next(tmp_path.glob("*_response.json")).read_text(encoding="utf-8"))
    assert response["final_url"] == "https://verification.invalid/"
    assert challenge_reason("An article about CAPTCHA") is None


@pytest.mark.asyncio
async def test_disabled_debug_does_not_create_evidence_files(monkeypatch, tmp_path):
    monkeypatch.setenv("FREE_SEARCH_DEBUG_DIR", str(tmp_path))

    async def request(session, method, url, **kwargs):
        return 200, {}, b'<title>Just a moment...</title>', "https://verification.invalid/", False

    monkeypatch.setattr(_http, "request", request)
    await WebFreeSearchTool(enabled_engines=("bing",)).invoke({"query": "weather", "provider": "bing"})
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("runner", [
    WebFreeSearchTool._search_duckduckgo,
    WebFreeSearchTool._search_duckduckgo_via_jina,
    WebFreeSearchTool._search_bing,
])
@pytest.mark.asyncio
async def test_truncated_search_response_is_not_classified_as_no_results(monkeypatch, runner):
    async def request(session, method, url, **kwargs):
        assert kwargs["max_bytes"] == 2_000_000
        return 200, {}, b"Incomplete response", url, True

    monkeypatch.setattr(_http, "request", request)
    async with _http.new_session() as session:
        with pytest.raises(WebContentError) as error:
            await runner(session, "Python", 10, 10)
    assert error.value.status == "truncated"


@pytest.mark.asyncio
async def test_reader_origin_error_is_not_an_empty_success(monkeypatch):
    async def request(session, method, url, **kwargs):
        page = b"Warning: Target URL returned error 404: Not Found\nMarkdown Content:\nNot found."
        return 200, {}, page, url, False

    monkeypatch.setattr(_http, "request", request)
    async with _http.new_session() as session:
        with pytest.raises(WebContentError) as error:
            await WebFreeSearchTool._search_duckduckgo_via_jina(session, "Python", 10, 10)
    assert error.value.status == "source_error"


def test_result_limit_preserves_omitted_candidates_in_diagnostics():
    raw = [{"title": "Python release", "url": f"https://example.org/{i}"} for i in range(3)]
    assessment = SearchAssessment(status="ok", results=[
        CandidateAssessment(id=i, relevance="relevant", reason="Useful") for i in range(3)
    ])
    selected, decisions = _select_search_candidates("Python release", raw, max_results=1, assessment=assessment)
    assert len(selected) == 1 and len(decisions) == 3
    assert decisions[1]["relevance"] == "relevant"
    assert decisions[1]["reasons"] == ["Useful", "result_limit"]
    assert decisions[1]["row"] == raw[1]
