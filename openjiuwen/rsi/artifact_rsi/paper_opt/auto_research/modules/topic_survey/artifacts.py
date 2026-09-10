"""Deterministic Markdown artifacts for a completed topic survey."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.logging import get_logger
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    project_root,
    resolve_project_reference,
    to_project_relative,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.topic_survey.schemas import (
    CitationMetadata,
    SurveySource,
    TopicSurveyDraft,
    TopicSurveyOutput,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.topic_survey.citations import (
    resolve_citation,
    stable_source_id,
)

_SOURCE_MANIFEST_FILENAME = "source-manifest.json"

_LOGGER = get_logger(__name__)


def survey_directory(topic: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:48] or "topic"
    digest = hashlib.sha256(topic.encode("utf-8")).hexdigest()[:10]
    return project_root() / "data" / "outputs" / "topic_survey" / f"{slug}-{digest}"


def _relative_source_link(source_path: Path, report_path: Path) -> str:
    return source_path.relative_to(report_path.parent).as_posix()


def _validate_source_paths(draft: TopicSurveyDraft, *, directory: Path) -> list[tuple[SurveySource, Path]]:
    """Resolve each source's reported local_path. A source whose path
    escapes the download directory, or that the model claimed was
    downloaded but isn't actually on disk, is dropped (logged, not raised)
    -- the rest of an otherwise-good survey shouldn't be discarded over one
    mis-reported path. Callers must still treat zero surviving sources as a
    real failure."""
    kept: list[tuple[SurveySource, Path]] = []
    for source in draft.sources:
        path = resolve_project_reference(source.local_path)
        try:
            path.relative_to(directory)
        except ValueError:
            _LOGGER.warning("dropping survey source outside its download directory: %s", source.local_path)
            continue
        if not path.is_file():
            _LOGGER.warning("dropping survey source that was not actually downloaded: %s", source.local_path)
            continue
        kept.append((source, path))
    return kept


def _bullets(items: list[str]) -> str:
    return "".join(f"- {item}\n" for item in items)


def _normalize_source(source: SurveySource, source_path: Path) -> SurveySource:
    """Resolve source metadata on the host, never trusting model flags."""

    retrieval_mode = source.retrieval_mode
    if source_path.suffix.lower() == ".pdf":
        retrieval_mode = "downloaded_pdf"
    elif source_path.name.endswith(".metadata.md"):
        retrieval_mode = "metadata_only"
    else:
        retrieval_mode = "downloaded_html"

    resolution = resolve_citation(
        title=source.title,
        url=source.url,
        local_path=source_path,
        supplied=source.citation or CitationMetadata(),
    )
    return source.model_copy(
        update={
            "retrieval_mode": retrieval_mode,
            "citation": resolution.metadata,
            "citation_key": resolution.key,
            "citation_eligible": resolution.eligible,
            "citation_exclusion_reasons": resolution.exclusion_reasons,
        }
    )


def _write_manifest(directory: Path, sources: list[SurveySource]) -> Path:
    manifest_path = directory / _SOURCE_MANIFEST_FILENAME
    payload = {
        "version": 1,
        "sources": [
            {
                "source_id": stable_source_id(source.url),
                **source.model_dump(mode="json"),
            }
            for source in sources
        ],
    }
    _atomic_write_text(
        manifest_path,
        json.dumps(payload, ensure_ascii=False, indent=2),
    )
    return manifest_path


def _atomic_write_text(path: Path, content: str) -> None:
    """Replace a text artifact atomically, even when writes overlap."""

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def write_survey_artifacts(topic: str, draft: TopicSurveyDraft) -> TopicSurveyOutput:
    """Validate saved evidence and render summary/manifest artifacts atomically."""
    directory = survey_directory(topic)
    directory.mkdir(parents=True, exist_ok=True)
    report_path = directory / "research_summary.md"
    kept = _validate_source_paths(draft, directory=directory)
    if not kept:
        raise ValueError(
            f"topic survey produced no sources that were actually downloaded "
            f"(all {len(draft.sources)} reported source(s) failed path validation)"
        )
    normalized = [(_normalize_source(source, source_path), source_path) for source, source_path in kept]

    reference_lines: list[str] = []
    source_sections: list[str] = []
    for index, (source, source_path) in enumerate(normalized, 1):
        link = _relative_source_link(source_path, report_path)
        reference_lines.append(f"[{source.title}]({link})")
        citation_status = "citable" if source.citation_eligible else "evidence-only"
        citation_details = (
            f"- **Citation key:** {source.citation_key}\n"
            if source.citation_key
            else "- **Citation exclusion reason:** "
            + ", ".join(source.citation_exclusion_reasons or ["incomplete_metadata"])
            + "\n"
        )
        authors = ", ".join(source.citation.authors) or "(unknown)"
        year = source.citation.year or "(unknown)"
        venue = source.citation.venue or "(unknown)"
        doi = source.citation.doi or "(unknown)"
        source_sections.append(
            f"### {index}. [{source.title}]({link})\n\n"
            f"- **URL:** {source.url}\n"
            f"- **Retrieval mode:** {source.retrieval_mode}\n"
            f"- **Citation status:** {citation_status}\n"
            f"{citation_details}"
            f"- **Authors:** {authors}\n"
            f"- **Year:** {year}\n"
            f"- **Venue:** {venue}\n"
            f"- **DOI:** {doi}\n"
            f"- **Abstract:** {source.abstract or '(not available)'}\n"
            f"- **Summary:** {source.summary}\n"
            f"- **Key findings:**\n{_bullets(source.key_findings)}"
            f"- **Limitations:**\n{_bullets(source.limitations or ['(none recorded)'])}"
        )

    report = (
        f"# Topic Survey: {topic}\n\n"
        "## Short Summary\n\n"
        f"{draft.short_summary}\n\n"
        "## Key Findings\n\n"
        f"{_bullets(draft.key_findings)}\n"
        "## Open Problems\n\n"
        f"{_bullets(draft.open_problems)}\n"
        "## Sources\n\n" + "\n".join(source_sections)
    )
    _atomic_write_text(report_path, report)
    _write_manifest(directory, [source for source, _path in normalized])

    return TopicSurveyOutput(
        topic=topic,
        short_summary=draft.short_summary,
        key_findings=draft.key_findings,
        open_problems=draft.open_problems,
        references=reference_lines,
        research_summary_path=to_project_relative(report_path),
        sources=[source for source, _path in normalized],
    )
