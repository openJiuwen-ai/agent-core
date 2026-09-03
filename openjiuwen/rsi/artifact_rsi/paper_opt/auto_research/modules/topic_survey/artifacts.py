"""Deterministic Markdown artifacts for a completed topic survey."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    project_root,
    resolve_project_reference,
    to_project_relative,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.topic_survey.schemas import (
    TopicSurveyDraft,
    TopicSurveyOutput,
)


def survey_directory(topic: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:48] or "topic"
    digest = hashlib.sha256(topic.encode("utf-8")).hexdigest()[:10]
    return project_root() / "data" / "outputs" / "topic_survey" / f"{slug}-{digest}"


def _relative_source_link(source_path: Path, report_path: Path) -> str:
    return source_path.relative_to(report_path.parent).as_posix()


def _validate_source_paths(draft: TopicSurveyDraft, *, directory: Path) -> list[Path]:
    resolved: list[Path] = []
    for source in draft.sources:
        path = resolve_project_reference(source.local_path)
        try:
            path.relative_to(directory)
        except ValueError as exc:
            raise ValueError(f"survey source is outside its download directory: {source.local_path}") from exc
        if not path.is_file():
            raise FileNotFoundError(f"survey source was not downloaded: {source.local_path}")
        resolved.append(path)
    return resolved


def _bullets(items: list[str]) -> str:
    return "".join(f"- {item}\n" for item in items)


def write_survey_artifacts(topic: str, draft: TopicSurveyDraft) -> TopicSurveyOutput:
    """Validate downloaded files and render the summary artifacts atomically."""
    directory = survey_directory(topic)
    directory.mkdir(parents=True, exist_ok=True)
    report_path = directory / "research_summary.md"
    source_paths = _validate_source_paths(draft, directory=directory)

    reference_lines: list[str] = []
    source_sections: list[str] = []
    for index, (source, source_path) in enumerate(zip(draft.sources, source_paths, strict=True), 1):
        link = _relative_source_link(source_path, report_path)
        reference_lines.append(f"[{source.title}]({link})")
        source_sections.append(
            f"### {index}. [{source.title}]({link})\n\n"
            f"- **URL:** {source.url}\n"
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
        "## Sources\n\n"
        + "\n".join(source_sections)
    )
    temporary = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary.write_text(report, encoding="utf-8")
    temporary.replace(report_path)

    return TopicSurveyOutput(
        topic=topic,
        short_summary=draft.short_summary,
        key_findings=draft.key_findings,
        open_problems=draft.open_problems,
        references=reference_lines,
        research_summary_path=to_project_relative(report_path),
        sources=draft.sources,
    )
