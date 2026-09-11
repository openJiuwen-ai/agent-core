# coding: utf-8

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.harness.tools.web import _http
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import set_project_root
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.tools.download_survey_source import (
    DownloadSurveySourceTool,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.bibliography import (
    build_bibliography,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.agent import (
    ReportingAgent,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.schemas import (
    ResearchBrief,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.topic_survey.artifacts import (
    survey_directory,
    write_survey_artifacts,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.topic_survey.citations import (
    doi_from_url,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.topic_survey.schemas import (
    CitationMetadata,
    SurveySource,
    TopicSurveyDraft,
)


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.asyncio
async def test_no_proxy_still_downloads_pdf_directly(tmp_path, monkeypatch):
    calls: list[str | None] = []

    async def fake_request(session, method, url, **kwargs):
        calls.append(kwargs.get("proxy_url"))
        return 200, {"Content-Type": "application/pdf"}, b"%PDF-1.7", url, False

    monkeypatch.setattr(_http, "new_session", lambda: _FakeSession())
    monkeypatch.setattr(_http, "request", fake_request)

    tool = DownloadSurveySourceTool(
        download_dir=tmp_path / "sources",
        project_root=tmp_path,
        proxy_url=None,
    )
    result = await tool.invoke({"url": "https://example.test/paper.pdf"})

    assert result["success"] is True
    assert result["retrieval_mode"] == "downloaded_pdf"
    assert result["source_type"] == "pdf"
    assert calls == [None]
    assert (tmp_path / result["local_path"]).suffix == ".pdf"


@pytest.mark.asyncio
async def test_no_proxy_still_downloads_html_directly(tmp_path, monkeypatch):
    calls: list[str | None] = []
    url = "https://example.test/article"
    html = b"<html><head><meta name='citation_author' content='Ada Lovelace'></head></html>"

    async def fake_request(session, method, request_url, **kwargs):
        calls.append(kwargs.get("proxy_url"))
        assert request_url == url
        return 200, {"Content-Type": "text/html"}, html, url, False

    monkeypatch.setattr(_http, "new_session", lambda: _FakeSession())
    monkeypatch.setattr(_http, "request", fake_request)

    tool = DownloadSurveySourceTool(
        download_dir=tmp_path / "sources",
        project_root=tmp_path,
        proxy_url=None,
    )
    result = await tool.invoke({"url": url})

    assert result["success"] is True
    assert result["retrieval_mode"] == "downloaded_html"
    assert result["source_type"] == "html"
    assert calls == [None]
    downloaded = tmp_path / result["local_path"]
    assert downloaded.suffix == ".html"
    assert downloaded.read_bytes() == html


@pytest.mark.asyncio
async def test_configured_proxy_keeps_being_forwarded_to_download(tmp_path, monkeypatch):
    calls: list[str | None] = []

    async def fake_request(session, method, url, **kwargs):
        calls.append(kwargs.get("proxy_url"))
        return 200, {"Content-Type": "application/pdf"}, b"%PDF-1.7", url, False

    monkeypatch.setattr(_http, "new_session", lambda: _FakeSession())
    monkeypatch.setattr(_http, "request", fake_request)

    tool = DownloadSurveySourceTool(
        download_dir=tmp_path / "sources",
        project_root=tmp_path,
        proxy_url="http://proxy.example.test:7890",
    )
    result = await tool.invoke({"url": "https://example.test/paper.pdf"})

    assert result["success"] is True
    assert result["retrieval_mode"] == "downloaded_pdf"
    assert calls == ["http://proxy.example.test:7890"]


@pytest.mark.asyncio
async def test_failed_download_saves_fallback_without_retry(tmp_path, monkeypatch):
    calls = 0

    async def fake_request(session, method, url, **kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("direct request timed out")

    monkeypatch.setattr(_http, "new_session", lambda: _FakeSession())
    monkeypatch.setattr(_http, "request", fake_request)

    tool = DownloadSurveySourceTool(
        download_dir=tmp_path / "sources",
        project_root=tmp_path,
        proxy_url=None,
    )
    result = await tool.invoke(
        {
            "url": "https://example.test/blocked-paper",
            "fallback_evidence": {
                "title": "Blocked paper",
                "source_type": "paper",
                "abstract": "The search result abstract remains usable.",
                "search_snippet": "A concise result snippet.",
                "authors": ["Ada Lovelace"],
                "year": "1843",
                "doi": "10.1234/example",
            },
        }
    )

    assert calls == 1
    assert result["success"] is True
    assert result["retrieval_mode"] == "metadata_only"
    evidence = tmp_path / result["local_path"]
    assert evidence.name.endswith(".metadata.md")
    text = evidence.read_text(encoding="utf-8")
    assert "The search result abstract remains usable." in text
    assert "direct request timed out" in text


@pytest.mark.asyncio
async def test_metadata_fallback_preserves_citation_fields_for_host_resolution(tmp_path, monkeypatch):
    async def fake_request(session, method, url, **kwargs):
        raise TimeoutError("direct request timed out")

    monkeypatch.setattr(_http, "new_session", lambda: _FakeSession())
    monkeypatch.setattr(_http, "request", fake_request)

    topic = "Fallback citation test"
    set_project_root(tmp_path)
    download_dir = survey_directory(topic) / "sources"
    tool = DownloadSurveySourceTool(
        download_dir=download_dir,
        project_root=tmp_path,
        proxy_url=None,
    )
    downloaded = await tool.invoke(
        {
            "url": "https://example.test/blocked-paper",
            "fallback_evidence": {
                "title": "Blocked paper",
                "source_type": "paper",
                "abstract": "Usable abstract.",
                "authors": [" Ada Lovelace "],
                "year": "published in 1843",
                "venue": " Journal of Examples ",
                "doi": "https://doi.org/10.1234/example?utm_source=test#abstract",
            },
        }
    )
    try:
        draft = TopicSurveyDraft(
            short_summary="summary",
            key_findings=["finding"],
            open_problems=["problem"],
            sources=[
                SurveySource(
                    title="Blocked paper",
                    url="https://example.test/blocked-paper",
                    source_type="paper",
                    local_path=downloaded["local_path"],
                    summary="summary",
                    key_findings=["finding"],
                )
            ],
        )
        output = write_survey_artifacts(topic, draft)
        assert output.sources[0].citation_eligible is True
        assert output.sources[0].citation.year == "1843"
        assert output.sources[0].citation.venue == "Journal of Examples"
        assert output.sources[0].citation.doi == "10.1234/example"
        assert "- DOI: 10.1234/example" in (tmp_path / downloaded["local_path"]).read_text(encoding="utf-8")
    finally:
        set_project_root(None)


def test_doi_from_url_ignores_query_and_fragment():
    url = "https://doi.org/10.1038/s41586-020-2649-2?utm_source=newsletter#abstract"

    assert doi_from_url(url) == "10.1038/s41586-020-2649-2"
    assert doi_from_url("https://example.test/article/10.1234/example#details") == ("10.1234/example")
    assert doi_from_url("https://example.test/volume10.1234/example") is None
    assert doi_from_url("https://[invalid") is None


def _write_source_case(tmp_path: Path, *, complete: bool):
    set_project_root(tmp_path)
    topic = "Citation evidence test"
    directory = survey_directory(topic)
    source_path = directory / "sources" / ("source.html" if complete else "source.metadata.md")
    source_path.parent.mkdir(parents=True, exist_ok=True)
    if complete:
        source_path.write_text(
            "<html><head>"
            "<meta name='citation_author' content='Ada Lovelace'>"
            "<meta name='citation_publication_date' content='1843-01-01'>"
            "<meta name='citation_doi' content='10.1234/example'>"
            "</head><body>evidence</body></html>",
            encoding="utf-8",
        )
    else:
        source_path.write_text("abstract without complete citation metadata", encoding="utf-8")
    relative = source_path.relative_to(tmp_path).as_posix()
    citation = (
        CitationMetadata(authors=["Ada Lovelace"], year="1843", doi="10.1234/example")
        if complete
        else CitationMetadata()
    )
    draft = TopicSurveyDraft(
        short_summary="summary",
        key_findings=["finding"],
        open_problems=["problem"],
        sources=[
            SurveySource(
                title="Citation source",
                url="https://example.test/article",
                source_type="paper",
                local_path=relative,
                summary="summary",
                abstract="abstract",
                key_findings=["finding"],
                citation=citation,
                retrieval_mode="downloaded_html" if complete else "metadata_only",
            )
        ],
    )
    return topic, write_survey_artifacts(topic, draft), source_path


def test_artifacts_persist_citation_status_and_manifest(tmp_path):
    try:
        topic, output, _ = _write_source_case(tmp_path, complete=True)
        summary_path = tmp_path / output.research_summary_path
        manifest_path = summary_path.parent / "source-manifest.json"

        assert output.sources[0].citation_eligible is True
        assert output.sources[0].citation_key
        assert "**Citation status:** citable" in summary_path.read_text(encoding="utf-8")
        assert manifest_path.is_file()
    finally:
        set_project_root(None)


def test_incomplete_source_remains_evidence_only_and_is_not_cited(tmp_path):
    try:
        topic, output, _ = _write_source_case(tmp_path, complete=False)
        summary_path = tmp_path / output.research_summary_path

        assert output.sources[0].citation_eligible is False
        assert output.sources[0].citation_key is None
        assert "missing_author" in output.sources[0].citation_exclusion_reasons
        assert "missing_doi" in output.sources[0].citation_exclusion_reasons

        bibliography = build_bibliography(summary_path, network_enabled=False)
        assert bibliography.bib_text == ""
        assert bibliography.title_to_key == {}
        assert bibliography.evidence_only_sources
    finally:
        set_project_root(None)


def test_complete_source_enters_bibliography_from_manifest(tmp_path):
    try:
        topic, output, _ = _write_source_case(tmp_path, complete=True)
        summary_path = tmp_path / output.research_summary_path

        bibliography = build_bibliography(summary_path, network_enabled=False)

        assert bibliography.known_keys
        assert "10.1234/example" in bibliography.bib_text
        assert "Ada Lovelace" in bibliography.bib_text
        assert bibliography.evidence_only_sources == []
    finally:
        set_project_root(None)


def test_reporting_receives_downloaded_html_as_evidence(tmp_path):
    try:
        topic, output, source_path = _write_source_case(tmp_path, complete=True)
        brief = ResearchBrief(
            resource_paths=[
                output.research_summary_path,
                source_path.relative_to(tmp_path).as_posix(),
            ]
        )

        evidence = ReportingAgent._read_survey_summary(brief)

        assert evidence is not None
        assert "Detailed source evidence: source.html" in evidence
        assert "evidence" in evidence
    finally:
        set_project_root(None)


def test_reporting_bounds_large_html_evidence(tmp_path):
    set_project_root(tmp_path)
    summary_path = tmp_path / "research_summary.md"
    source_path = tmp_path / "source.html"
    summary_path.write_text("summary", encoding="utf-8")
    source_path.write_text(
        "<html><head><title>source</title></head><body>" + ("visible text " * 10_000) + "</body></html>",
        encoding="utf-8",
    )
    brief = ResearchBrief(resource_paths=["research_summary.md", "source.html"])

    try:
        evidence = ReportingAgent._read_survey_summary(brief)
    finally:
        set_project_root(None)

    assert evidence is not None
    assert "Detailed source evidence: source.html" in evidence
    assert evidence.count("visible text") <= 600
