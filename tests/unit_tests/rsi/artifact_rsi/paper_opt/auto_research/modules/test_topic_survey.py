"""Covers topic_survey's relaxed validation: a source with no standalone
key finding, a survey with no overall open problems, a survey grounded only
in web_page (non-paper) sources, and write_survey_artifacts dropping
individually-bad sources instead of discarding an otherwise-good survey --
all previously hard failures, now non-fatal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    set_project_root,
    to_project_relative,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.topic_survey.agent import TopicSurveyAgent
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.topic_survey.artifacts import (
    survey_directory,
    write_survey_artifacts,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.topic_survey.schemas import (
    SurveySource,
    TopicSurveyDraft,
)


@pytest.fixture(autouse=True)
def _project_root(tmp_path: Path):
    set_project_root(tmp_path)
    yield


def _source(topic: str, filename: str, *, source_type: str = "web_page", **overrides) -> SurveySource:
    directory = survey_directory(topic) / "sources"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text("content", encoding="utf-8")
    fields = dict(
        title="A Source",
        url="https://example.com/a",
        source_type=source_type,
        local_path=to_project_relative(path),
        summary="a summary",
        key_findings=["a finding"],
    )
    fields.update(overrides)
    return SurveySource(**fields)


def test_source_with_no_key_findings_is_accepted():
    source = _source("t1", "a.html", key_findings=[])
    assert source.key_findings == []


def test_draft_with_no_open_problems_or_findings_is_accepted():
    topic = "t2"
    draft = TopicSurveyDraft(
        short_summary="summary",
        key_findings=[],
        open_problems=[],
        sources=[_source(topic, "a.html")],
    )
    assert draft.open_problems == []
    assert draft.key_findings == []


def test_survey_with_no_paper_source_does_not_raise():
    topic = "t3"
    draft = TopicSurveyDraft(
        short_summary="summary",
        key_findings=["finding"],
        open_problems=["problem"],
        sources=[_source(topic, "a.html", source_type="web_page")],
    )
    # Must not raise -- previously RuntimeError'd for having zero paper sources.
    TopicSurveyAgent._validate_paper_submission(draft)


def test_source_outside_download_directory_is_dropped_not_fatal():
    topic = "t4"
    good = _source(topic, "good.html")
    escaped = SurveySource(
        title="Escaped",
        url="https://example.com/escaped",
        source_type="web_page",
        local_path="elsewhere/file.html",
        summary="s",
        key_findings=[],
    )
    draft = TopicSurveyDraft(
        short_summary="summary", key_findings=[], open_problems=[], sources=[good, escaped]
    )

    output = write_survey_artifacts(topic, draft)

    assert [s.title for s in output.sources] == ["A Source"]
    assert (survey_directory(topic) / "research_summary.md").is_file()


def test_source_never_actually_downloaded_is_dropped_not_fatal():
    topic = "t5"
    good = _source(topic, "good.html")
    directory = survey_directory(topic)
    missing_path = directory / "sources" / "missing.html"  # never written to disk
    missing = SurveySource(
        title="Missing",
        url="https://example.com/missing",
        source_type="web_page",
        local_path=to_project_relative(missing_path),
        summary="s",
        key_findings=[],
    )
    draft = TopicSurveyDraft(
        short_summary="summary", key_findings=[], open_problems=[], sources=[good, missing]
    )

    output = write_survey_artifacts(topic, draft)

    assert [s.title for s in output.sources] == ["A Source"]


def test_all_sources_bad_still_raises_clearly():
    topic = "t6"
    directory = survey_directory(topic)
    missing_path = directory / "sources" / "missing.html"
    missing = SurveySource(
        title="Missing",
        url="https://example.com/missing",
        source_type="web_page",
        local_path=to_project_relative(missing_path),
        summary="s",
        key_findings=[],
    )
    draft = TopicSurveyDraft(
        short_summary="summary", key_findings=[], open_problems=[], sources=[missing]
    )

    with pytest.raises(ValueError, match="no sources that were actually downloaded"):
        write_survey_artifacts(topic, draft)
