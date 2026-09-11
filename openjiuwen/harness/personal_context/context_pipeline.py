"""The single in-process processing and Context publication pipeline.

The queue is intentionally non-durable, while complete source and Processing
artifacts live in one temporary directory for the whole fetch run.  Each batch
finishes after deterministic Processing is safely written; one explicit finish event performs
the sole Filesystem compilation and publication for that run.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Mapping, Sequence, TypeVar, cast
from urllib.parse import urlsplit, urlunsplit

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import AssistantMessage, Model, UserMessage
from openjiuwen.harness.personal_context.agent_support import run_personal_context_agent
from openjiuwen.harness.personal_context.config import PersonalContextConfig
from openjiuwen.harness.personal_context.models import FetchBatch, RawChangeItem
from openjiuwen.harness.personal_context.source_metadata import read_source_metadata, upsert_source_metadata
from openjiuwen.harness.personal_context.status_codes import StatusCode, build_error

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]\r\n]*\]\(([^)\r\n]+)\)")
_MARKDOWN_LINK_TOKEN = re.compile(r"\[[^\]\r\n]*\]\(([^)\r\n]+)\)")
_MARKDOWN_INLINE_LINK = re.compile(r"!?\[([^\]\r\n]+)\]\([^)\r\n]+\)")
_MARKDOWN_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*#*\s*$")
_SHORT_REFERENCE = re.compile(r"\[\[ref:(0|[1-9][0-9]*)\]\]")
_SOURCE_METADATA_ID = re.compile(r"src_[0-9a-f]{32}")
_URI_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
_PERSONAL_CONTEXT_MANAGED_MARKER = re.compile(r"<!--\s*personal-context:[a-z0-9-]+:(?:start|end)\s*-->")
_ROOT_NAVIGATION_START = "<!-- personal-context:navigation:start -->"
_ROOT_NAVIGATION_END = "<!-- personal-context:navigation:end -->"
_SOURCE_LINKS_START = "<!-- personal-context:source-links:start -->"
_SOURCE_LINKS_END = "<!-- personal-context:source-links:end -->"
_TOPIC_LINKS_START = "<!-- personal-context:topic-links:start -->"
_TOPIC_LINKS_END = "<!-- personal-context:topic-links:end -->"
_MANAGED_TOPIC_MARKER = "<!-- personal-context:managed-topic -->"
_MAX_BLOCK_CHARS = 4_000
_MAX_AGENT_CONTEXT_FILES = 10_000
_MAX_AGENT_CONTEXT_FILE_BYTES = 2 * 1024 * 1024
_MAX_AGENT_CONTEXT_PATH_CHARS = 1_024
_LARGE_RUN_DOCUMENT_COUNT = 10
_LARGE_RUN_TOTAL_DOCUMENT_CHARS = 60_000
_LARGE_RUN_MAX_DOCUMENT_CHARS = 40_000
_SMALL_RUN_PREVIEW_CHARS = 12_000
_LARGE_RUN_PREVIEW_CHARS = 2_800
_BRIEFING_SUMMARY_CHARS = 450
_BRIEFING_HEADING_LIMIT = 8
_BRIEFING_HEADING_CHARS = 160
_BALANCED_GROUP_SIZE = 5
_BALANCED_DIRECTORY_LIMIT = 5
_BALANCED_DIRECTORY_PREVIEW_CHARS = 240
_BALANCED_NEW_TOPIC_TITLE_CHARS = 80
_INITIAL_PROMPT_DOCUMENT_LIMIT = 12
_SMALL_PROMPT_SUMMARY_CHARS = 700
_LARGE_PROMPT_SUMMARY_CHARS = 320
_MAX_MODEL_OUTPUT_CHARS = 2_000_000
_MAX_VALIDATION_ERROR_CHARS = 512
_PROFILE_RANK = {"rules": 0, "balanced": 1, "agent": 2}
_GENERIC_TOPIC_NAMES = frozenset(
    {
        "context",
        "docs",
        "documents",
        "information",
        "notes",
        "sources",
        "topics",
        "内容",
        "文档",
        "文档资料",
        "材料",
        "知识",
        "资料",
        "资料文档",
        "主题",
    }
)
_FILESYSTEM_WIKI_INSTRUCTIONS = (
    "Source references: copy only [[ref:N]] tokens already present in the supplied Processing Markdown; never "
    "invent a number or write a permanent source ID, source URL, or source metadata path. Place each copied token "
    "where the referenced object is actually discussed. A reference may identify an origin or evidence, or merely "
    "a mention or association; it does not by itself mean support, proof, agreement, or endorsement. One page may "
    "reference multiple sources and one source may appear on multiple pages, but never add an unrelated reference "
    "just to satisfy validation. Before writing, analyze the entities, concepts, claims, and concrete facts; connect "
    "them to the existing Wiki; identify real contradictions, time differences, and uncertainty; then choose the "
    "smallest coherent change. Prefer to update or merge existing pages, and create a focused entity or concept page "
    "only when needed. Maintain useful cross-links and every relevant description.md. Mark 待核实 only in the "
    "relevant page when user judgment is genuinely required; do not create analysis, planning, review, or process "
    "files. Do not mechanically create a per-source summary page, and do not always create or update index.md, "
    "log.md, or overview.md. "
)
_SENSITIVE_METADATA_KEYS = frozenset(
    {
        "auth",
        "authentication",
        "authorization",
        "authheader",
        "authorizationheader",
        "apikey",
        "token",
        "password",
        "passwd",
        "secret",
        "clientsecret",
        "credential",
        "credentials",
        "privatekey",
        "cookie",
        "cookies",
        "setcookie",
        "sessioncookie",
        "provenance",
    }
)
_SENSITIVE_METADATA_SUFFIXES = (
    "authorization",
    "apikey",
    "token",
    "password",
    "passwd",
    "secret",
    "credential",
    "credentials",
    "privatekey",
    "cookie",
    "cookies",
)
_VALIDATION_URL_USERINFO = re.compile(r"(?i)(\b(?:https?|ftp|file)://)[^@\s/?#]+@")
_VALIDATION_URL_QUERY = re.compile(r"(?i)(\b(?:https?|ftp|file)://[^\s/?#]+(?:/[^\s?#]*)?)[?#][^\s]*")
_VALIDATION_SECRET = re.compile(
    r"(?i)(\b(?:token|access[_ -]?token|refresh[_ -]?token|password|passwd|secret|api[_ -]?key|"
    r"client[_-]?secret|authorization|bearer)\b[\"']?\s*[:=]\s*[\"']?)(?:bearer\s+)?[^\s,;&\"']+"
)
_VALIDATION_PATHS = (
    re.compile(r"(?i)\b[A-Z]:[\\/][^\s,;&]+"),
    re.compile(r"(?i)(?<![:\w])(?:\\\\|//)[^\s,;&]+"),
    re.compile(r"(?i)(?<![\w:/])/(?:[A-Za-z0-9_.-]+/){1,}[A-Za-z0-9_.-]+"),
    re.compile(r"(?i)(?<![\w:/])/[A-Za-z0-9_.-]+(?=$|[\s,;&])"),
)
_T = TypeVar("_T")


def _pipeline_error(message: str = "context pipeline execution failed") -> BaseError:
    return build_error(StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR, error_msg=message)


def _publish_error(message: str = "context publication failed") -> BaseError:
    return build_error(StatusCode.CONTEXT_PROACTIVE_PUBLISH_EXECUTION_ERROR, error_msg=message)


def _safe_segment(value: object, *, name: str) -> str:
    text = str(value)
    if not text or text in {".", ".."} or not _SAFE_SEGMENT.fullmatch(text):
        raise _publish_error(f"unsafe {name}")
    return text


def _queue_item_completion(item: object) -> asyncio.Future[None] | None:
    """Return a tagged event's trailing completion, including malformed events."""

    if not isinstance(item, tuple) or not item:
        return None
    completion = item[-1]
    return completion if isinstance(completion, asyncio.Future) else None


async def _cancel_safe_to_thread(
    function: Callable[..., _T],
    /,
    *args: object,
    **kwargs: object,
) -> _T:
    """Wait for thread I/O to settle before propagating caller cancellation."""

    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
        except BaseException:
            if cancellation is None:
                raise
    if cancellation is not None:
        with contextlib.suppress(BaseException):
            task.result()
        raise cancellation
    return task.result()


def _digest(value: str) -> str:
    # Keep managed Windows paths comfortably below MAX_PATH while retaining
    # enough entropy for the bounded, local first-version store.
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()


def _assert_no_symlinks(root: Path) -> None:
    _assert_path_chain_no_symlinks(root)
    if root.is_symlink():
        raise _publish_error("managed directory must not contain symlinks")
    if not root.exists():
        return
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in [*directories, *files]:
            if (current_path / name).is_symlink():
                raise _publish_error("managed directory must not contain symlinks")


def _assert_path_chain_no_symlinks(path: Path) -> None:
    """Reject symlinks in an existing managed path or any of its parents."""
    current = path
    while True:
        if current.is_symlink():
            raise _publish_error("managed path must not traverse symlinks")
        parent = current.parent
        if parent == current:
            return
        current = parent


def _copy_tree(source: Path, target: Path) -> None:
    if not source.exists():
        target.mkdir(parents=True, exist_ok=True)
        return
    _assert_no_symlinks(source)
    shutil.copytree(source, target, dirs_exist_ok=True, symlinks=False)


def _relative_file(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise _publish_error("managed path escaped its root") from exc


def _validated_relative_path(value: object, *, name: str) -> Path:
    """Validate a workspace-relative path before resolving it."""
    if not isinstance(value, str):
        raise _publish_error(f"{name} must be a relative path")
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if not normalized or normalized.startswith("/") or re.fullmatch(r"[A-Za-z]:.*", normalized) is not None:
        raise _publish_error(f"{name} is unsafe")
    if any(part in {"", ".", ".."} for part in parts):
        raise _publish_error(f"{name} is unsafe")
    return Path(normalized)


def _is_program_description(relative: str | Path) -> bool:
    # Every directory-level description is program metadata rather than a
    # document page.  Filesystem Agent is allowed to curate these files, but
    # they must never participate in the provenance/page contract.
    return Path(relative).name == "description.md"


def _remove_tree_entry(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _make_tree_writable(path: Path) -> None:
    """Make a temporary candidate removable after read-only source copies."""

    if path.is_symlink() or not path.exists():
        return
    if path.is_dir():
        for child in path.iterdir():
            _make_tree_writable(child)
    path.chmod(path.stat().st_mode | stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)


def _remove_empty_directories(root: Path) -> None:
    if not root.exists():
        return
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        with contextlib.suppress(OSError):
            directory.rmdir()


def _copy_and_publish_tree(
    candidate: Path,
    target: Path,
    *,
    skip_relative: str | None = None,
) -> set[str]:
    """Publish candidate files without removing files still used by the old root description."""

    _assert_no_symlinks(candidate)
    target.mkdir(parents=True, exist_ok=True)
    _assert_no_symlinks(target)

    candidate_files = {_relative_file(path, candidate) for path in candidate.rglob("*") if path.is_file()}
    target_files = {_relative_file(path, target) for path in target.rglob("*") if path.is_file()}
    if skip_relative is not None:
        candidate_files.discard(skip_relative)
        target_files.discard(skip_relative)

    ordinary_files = sorted(relative for relative in candidate_files if not _is_program_description(relative))
    nested_descriptions = sorted(
        (relative for relative in candidate_files if _is_program_description(relative)),
        key=lambda relative: (-len(Path(relative).parts), relative),
    )
    for relative in [*ordinary_files, *nested_descriptions]:
        source = candidate / relative
        destination = target / relative
        _atomic_write(destination, source.read_bytes())
    return target_files - candidate_files


def _remove_published_tree_entries(target: Path, relatives: set[str]) -> None:
    """Remove obsolete files only after the new root description is visible."""

    _assert_no_symlinks(target)
    for relative in sorted(relatives):
        _remove_tree_entry(target / relative)
    _remove_empty_directories(target)


def _split_blocks(markdown: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", markdown) if part.strip()]
    blocks: list[str] = []
    for paragraph in paragraphs or [markdown.strip()]:
        for index in range(0, len(paragraph), _MAX_BLOCK_CHARS):
            end = index + _MAX_BLOCK_CHARS
            blocks.append(paragraph[index:end])
    return blocks or [""]


def _normalize_markdown(content: str) -> str:
    normalized = content.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip() for line in normalized.splitlines())
    return normalized.strip() + "\n"


def _deterministic_briefing_preview(content: str) -> dict[str, object]:
    """Extract a bounded outline and first meaningful paragraph without a model."""

    normalized = _normalize_markdown(content)
    lines = normalized.splitlines()
    headings: list[dict[str, object]] = []
    paragraph: list[str] = []
    fallback_lines: list[str] = []
    paragraph_complete = False
    in_frontmatter = bool(lines and lines[0].strip() == "---")
    in_fence = False

    for index, line in enumerate(lines):
        stripped = line.strip()
        if in_frontmatter:
            if index and stripped in {"---", "..."}:
                in_frontmatter = False
            continue
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = _MARKDOWN_HEADING.fullmatch(line)
        if heading is not None:
            if len(headings) < _BRIEFING_HEADING_LIMIT:
                text = heading.group(2).strip().replace("`", "'")[:_BRIEFING_HEADING_CHARS]
                headings.append({"level": len(heading.group(1)), "text": text})
            if paragraph:
                paragraph_complete = True
            continue
        if not stripped or stripped in {"---", "***", "___"}:
            if paragraph:
                paragraph_complete = True
            continue
        fallback_lines.append(stripped)
        if not paragraph_complete:
            paragraph.append(stripped)

    summary = " ".join(paragraph)
    if not summary:
        summary = " ".join(fallback_lines) or normalized.strip()
    return {
        "headings": headings,
        "summary": summary[:_BRIEFING_SUMMARY_CHARS].rstrip(),
        "content_chars": len(normalized),
    }


def _title_for(item: RawChangeItem) -> str:
    title = (item.title or "").replace("\r", " ").replace("\n", " ").strip()
    if title:
        return title[:512]
    return item.logical_id.rsplit("/", 1)[-1] or item.logical_id


def _remove_rules_pages_for_deleted_ids(
    context_root: Path,
    *,
    service_id: str,
    deleted_ids: Iterable[str],
) -> None:
    service_root = context_root / "sources" / _safe_segment(service_id, name="service_id")
    for logical_id in deleted_ids:
        _remove_tree_entry(service_root / f"{_digest(logical_id)}.md")


def _managed_block_bounds(markdown: str, *, start: str, end: str) -> tuple[int, int] | None:
    starts = [match.start() for match in re.finditer(re.escape(start), markdown)]
    ends = [match.start() for match in re.finditer(re.escape(end), markdown)]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise _pipeline_error("managed Markdown block markers are malformed")
    block_end = ends[0] + len(end)
    block_start = starts[0]
    nested = _PERSONAL_CONTEXT_MANAGED_MARKER.findall(markdown[block_start:block_end])
    if nested != [start, end]:
        raise _pipeline_error("managed Markdown block markers are nested")
    return starts[0], block_end


def _replace_managed_block(
    markdown: str,
    *,
    start: str,
    end: str,
    body: str | None,
    default_heading: str,
) -> str:
    """Replace one valid PersonalContext block or insert it after the first H1."""

    bounds = _managed_block_bounds(markdown, start=start, end=end)
    block = None if body is None else f"{start}\n{body.rstrip()}\n{end}"
    if bounds is not None:
        begin, finish = bounds
        return markdown[:begin] + (block or "") + markdown[finish:]
    if block is None:
        return markdown
    if not markdown:
        markdown = f"# {default_heading}\n"
    heading = re.search(r"(?m)^# [^\r\n]+(?:\r?\n|$)", markdown)
    if heading is None:
        raise _pipeline_error("managed Markdown file has no top-level heading")
    heading_end = heading.end()
    return markdown[:heading_end] + "\n" + block + "\n" + markdown[heading_end:]


def _markdown_heading(markdown: str, *, fallback: str) -> str:
    heading = next((line[2:].strip() for line in markdown.splitlines() if line.startswith("# ")), "")
    return heading or fallback


def _markdown_label(value: str) -> str:
    normalized = value.replace("[", "(").replace("]", ")").replace("\r", " ").replace("\n", " ").strip()
    return normalized[:512] or "来源"


def _relative_markdown_target(path: Path, *, from_directory: Path) -> str:
    return os.path.relpath(path, start=from_directory).replace("\\", "/")


def _render_sources_navigation(context_root: Path) -> None:
    """Render only the program-owned sources -> service -> page hierarchy."""

    sources_root = context_root / "sources"
    service_rows: list[str] = []
    if sources_root.is_dir():
        for service_root in sorted(path for path in sources_root.iterdir() if path.is_dir()):
            pages = sorted(
                path for path in service_root.glob("*.md") if path.name != "description.md" and path.is_file()
            )
            description_path = service_root / "description.md"
            if not pages:
                if description_path.exists() or description_path.is_symlink():
                    _remove_tree_entry(description_path)
                continue
            page_rows: list[str] = []
            for page in pages:
                text = page.read_text(encoding="utf-8")
                title = _markdown_label(_markdown_heading(text, fallback=page.stem))
                page_rows.append(f"- [{title}]({_markdown_link_target(page.name)})")
            service_markdown = f"# {service_root.name} 来源\n\n## 来源页\n\n" + "\n".join(page_rows) + "\n"
            _atomic_write(description_path, service_markdown.encode("utf-8"))
            service_rows.append(
                f"- [{_markdown_label(service_root.name)}]"
                f"({_markdown_link_target((Path(service_root.name) / 'description.md').as_posix())})"
            )
    sources_description = sources_root / "description.md"
    if service_rows:
        sources_markdown = "# 来源导航\n\n## 服务\n\n" + "\n".join(service_rows) + "\n"
        _atomic_write(sources_description, sources_markdown.encode("utf-8"))
    elif sources_description.exists() or sources_description.is_symlink():
        _remove_tree_entry(sources_description)


def _render_root_navigation(
    context_root: Path,
    *,
    fallback_references: Sequence[str] = (),
) -> None:
    description_path = context_root / "description.md"
    try:
        current = description_path.read_text(encoding="utf-8") if description_path.is_file() else ""
    except (OSError, UnicodeError) as exc:
        raise _publish_error("root description could not be read") from exc
    rows: list[str] = []
    if (context_root / "sources" / "description.md").is_file():
        rows.append("- [按来源查看增量](sources/description.md)")
    if (context_root / "topics" / "description.md").is_file():
        rows.append("- [按主题查看内容](topics/description.md)")
    body = "## PersonalContext 导航\n\n" + "\n".join(rows) if rows else None
    updated = _replace_managed_block(
        current,
        start=_ROOT_NAVIGATION_START,
        end=_ROOT_NAVIGATION_END,
        body=body,
        default_heading="PersonalContext",
    )
    if not rows and updated.strip() in {"", "# PersonalContext"}:
        updated = "# PersonalContext\n\nNo context documents are currently published.\n"
        if fallback_references:
            updated += "\n本次 Context 状态涉及 " + "、".join(fallback_references) + "。\n"
    _atomic_write(description_path, updated.encode("utf-8"))


def _managed_local_links(
    description_path: Path,
    *,
    context_root: Path,
    start: str,
    end: str,
    heading: str,
) -> dict[str, str]:
    markdown = description_path.read_text(encoding="utf-8")
    bounds = _managed_block_bounds(markdown, start=start, end=end)
    if bounds is None:
        return {}
    begin, finish = bounds
    block_start = begin + len(start)
    block_end = finish - len(end)
    block = markdown[block_start:block_end]
    result: dict[str, str] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped == heading:
            continue
        match = re.fullmatch(r"- \[([^\]\r\n]+)\]\(([^\r\n]+)\)", stripped)
        if match is None:
            raise _pipeline_error("managed local link block is malformed")
        destination = _markdown_destination(match.group(2))
        if destination is None or _URI_SCHEME.match(destination):
            raise _pipeline_error("managed local link target is invalid")
        target = (description_path.parent / destination.replace("/", os.sep)).resolve()
        try:
            target.relative_to(context_root.resolve())
        except ValueError as exc:
            raise _pipeline_error("managed local link escaped Context") from exc
        if target.is_file():
            result[_relative_markdown_target(target, from_directory=description_path.parent)] = match.group(1)
    return result


def _append_managed_source_link(
    description_path: Path,
    *,
    context_root: Path,
    source_page: Path,
    title: str,
) -> None:
    links = _managed_local_links(
        description_path,
        context_root=context_root,
        start=_SOURCE_LINKS_START,
        end=_SOURCE_LINKS_END,
        heading="## PersonalContext 来源关联",
    )
    target = _relative_markdown_target(source_page, from_directory=description_path.parent)
    links[target] = _markdown_label(title)
    rows = [f"- [{links[path]}]({_markdown_link_target(path)})" for path in sorted(links)]
    markdown = description_path.read_text(encoding="utf-8")
    updated = _replace_managed_block(
        markdown,
        start=_SOURCE_LINKS_START,
        end=_SOURCE_LINKS_END,
        body="## PersonalContext 来源关联\n\n" + "\n".join(rows),
        default_heading=description_path.parent.name,
    )
    _atomic_write(description_path, updated.encode("utf-8"))


def _append_managed_topic_link(
    context_root: Path,
    *,
    topic_directory: Path,
    title: str,
) -> None:
    topics_root = context_root / "topics"
    description_path = topics_root / "description.md"
    if not description_path.is_file():
        _atomic_write(description_path, "# 主题导航\n".encode("utf-8"))
    links = _managed_local_links(
        description_path,
        context_root=context_root,
        start=_TOPIC_LINKS_START,
        end=_TOPIC_LINKS_END,
        heading="## PersonalContext 受控主题",
    )
    target_path = topic_directory / "description.md"
    target = _relative_markdown_target(target_path, from_directory=description_path.parent)
    links[target] = _markdown_label(title)
    rows = [f"- [{links[path]}]({_markdown_link_target(path)})" for path in sorted(links)]
    markdown = description_path.read_text(encoding="utf-8")
    updated = _replace_managed_block(
        markdown,
        start=_TOPIC_LINKS_START,
        end=_TOPIC_LINKS_END,
        body="## PersonalContext 受控主题\n\n" + "\n".join(rows),
        default_heading="主题导航",
    )
    _atomic_write(description_path, updated.encode("utf-8"))


def _normalized_topic_term(value: str) -> str | None:
    term = re.sub(r"[`*_#]", "", value).strip()
    compact = re.sub(r"\s+", "", term)
    if len(compact) < 4 or term.casefold() in _GENERIC_TOPIC_NAMES:
        return None
    return term


def _topic_term_matches_title(term: str, title: str) -> bool:
    if re.search(r"[\u3400-\u9fff]", term):
        return term.casefold() in title.casefold()
    return (
        re.search(
            rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])",
            title,
            flags=re.IGNORECASE,
        )
        is not None
    )


def _unique_rules_topic_description(context_root: Path, *, title: str) -> Path | None:
    matches: set[Path] = set()
    for description in sorted(context_root.rglob("description.md")):
        relative = description.relative_to(context_root)
        if description.parent == context_root or (relative.parts and relative.parts[0] == "sources"):
            continue
        text = description.read_text(encoding="utf-8")
        values = (description.parent.name, _markdown_heading(text, fallback=""))
        if any(
            term is not None and _topic_term_matches_title(term, title)
            for term in (_normalized_topic_term(value) for value in values)
        ):
            matches.add(description)
    return next(iter(matches)) if len(matches) == 1 else None


def _rules_source_page(
    document: Mapping[str, object],
    *,
    summary_override: str | None = None,
) -> str:
    title = _markdown_label(str(document.get("title") or document.get("logical_id") or "来源"))
    markdown = str(document.get("markdown", "")).rstrip()
    summary_input = _SHORT_REFERENCE.sub("", markdown).strip()
    preview = _deterministic_briefing_preview(summary_input)
    summary = summary_override or str(preview["summary"]).strip() or "（没有可用的确定性预览。）"
    return f"# {title}\n\n## 摘要\n\n{summary}\n\n## 正文\n\n{markdown}\n"


def _apply_rules_increment(
    context_root: Path,
    *,
    service_id: str,
    processed: Mapping[str, object],
    fallback_references: Sequence[str] = (),
) -> set[str]:
    """Apply one conservative deterministic increment to a copied Context."""

    _assert_no_symlinks(context_root)
    _remove_rules_pages_for_deleted_ids(
        context_root,
        service_id=service_id,
        deleted_ids=_processed_deleted_ids(processed),
    )
    service_root = context_root / "sources" / _safe_segment(service_id, name="service_id")
    changed_paths: set[str] = set()
    for document in _processed_documents(processed):
        logical_id = str(document["logical_id"])
        page = service_root / f"{_digest(logical_id)}.md"
        _atomic_write(page, _rules_source_page(document).encode("utf-8"))
        changed_paths.add(page.relative_to(context_root).as_posix())
        target_description = _unique_rules_topic_description(context_root, title=str(document.get("title", "")))
        if target_description is not None:
            _append_managed_source_link(
                target_description,
                context_root=context_root,
                source_page=page,
                title=str(document.get("title", logical_id)),
            )
            changed_paths.add(target_description.relative_to(context_root).as_posix())
    _render_sources_navigation(context_root)
    _render_root_navigation(context_root, fallback_references=fallback_references)
    changed_paths.add("description.md")
    return changed_paths


def _balanced_semantic_preview(markdown: str) -> str:
    without_comments = re.sub(r"<!--.*?-->", " ", markdown, flags=re.DOTALL)
    without_links = _MARKDOWN_INLINE_LINK.sub(r"\1", without_comments)
    preview = _deterministic_briefing_preview(without_links)
    return str(preview["summary"]).strip()[:_BALANCED_DIRECTORY_PREVIEW_CHARS]


def _balanced_tokens(value: str) -> set[str]:
    tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9]{2,}", value)
        if token.casefold() not in _GENERIC_TOPIC_NAMES
    }
    for run in re.findall(r"[\u3400-\u9fff]{2,}", value):
        tokens.add(run)
        for width in (2, 3, 4):
            for index in range(max(0, len(run) - width + 1)):
                slice_end = index + width
                tokens.add(run[index:slice_end])
    return tokens


def _balanced_directory_candidates(
    context_root: Path,
    document: Mapping[str, object],
) -> tuple[list[dict[str, str]], dict[str, Path]]:
    """Return at most five opaque semantic candidates and an in-memory path map."""

    title = str(document.get("title") or "")[:512]
    source_preview = _balanced_semantic_preview(_SHORT_REFERENCE.sub("", str(document.get("markdown", ""))))
    source_text = f"{title}\n{source_preview}"
    source_tokens = _balanced_tokens(source_text)
    scored: list[tuple[int, str, str, str, Path]] = []
    for description in sorted(context_root.rglob("description.md")):
        if description.is_symlink() or not description.is_file() or description.parent == context_root:
            continue
        relative = description.relative_to(context_root)
        if relative.parts and relative.parts[0] == "sources":
            continue
        markdown = description.read_text(encoding="utf-8")
        candidate_title = _markdown_label(_markdown_heading(markdown, fallback=description.parent.name))[:160]
        preview = _balanced_semantic_preview(markdown)
        score = 0
        for term_value in (description.parent.name, candidate_title):
            term = _normalized_topic_term(term_value)
            if term is not None and _topic_term_matches_title(term, source_text):
                score += 100
        score += 10 * len(source_tokens.intersection(_balanced_tokens(f"{candidate_title}\n{preview}")))
        if score <= 0:
            continue
        scored.append((score, relative.parent.as_posix().casefold(), candidate_title, preview, description.parent))
    selected = sorted(scored, key=lambda value: (-value[0], value[1]))[:_BALANCED_DIRECTORY_LIMIT]
    public: list[dict[str, str]] = []
    targets: dict[str, Path] = {}
    for index, (_, _, candidate_title, preview, path) in enumerate(selected, start=1):
        candidate_id = f"directory_{index}"
        public.append({"id": candidate_id, "title": candidate_title, "preview": preview})
        targets[candidate_id] = path
    return public, targets


def _balanced_summary_is_safe(summary: str) -> bool:
    if not summary or len(summary) > _BRIEFING_SUMMARY_CHARS or "\x00" in summary:
        return False
    if _MARKDOWN_LINK_TOKEN.search(summary) or _SHORT_REFERENCE.search(summary):
        return False
    if "<!--" in summary or re.search(r"(?m)^\s*#{1,6}\s", summary):
        return False
    if re.search(r"(?:^|\s)(?:[A-Za-z]:[\\/]|\.\.?[\\/]|/[A-Za-z0-9_.-])", summary):
        return False
    return True


def _balanced_new_topic_title_is_safe(title: str) -> bool:
    return bool(
        title
        and len(title) <= _BALANCED_NEW_TOPIC_TITLE_CHARS
        and "\x00" not in title
        and "\n" not in title
        and "\r" not in title
        and "/" not in title
        and "\\" not in title
        and "<!--" not in title
        and _MARKDOWN_LINK_TOKEN.search(title) is None
    )


def _parse_balanced_enrichments(
    text: str,
    *,
    allowed_targets: Mapping[int, set[str]],
) -> dict[int, dict[str, str | None]]:
    """Accept valid balanced items independently; top-level failures return no enrichment."""

    try:
        value = _load_agent_json(text, error_message="balanced enrichment output is not valid JSON")
    except BaseError:
        return {}
    if not isinstance(value, Mapping) or set(value) != {"items"} or not isinstance(value.get("items"), list):
        return {}
    items = cast(list[object], value["items"])
    counts: dict[int, int] = {}
    for item in items:
        if isinstance(item, Mapping):
            raw_index = item.get("item_index")
            if isinstance(raw_index, int) and not isinstance(raw_index, bool):
                counts[raw_index] = counts.get(raw_index, 0) + 1
    accepted: dict[int, dict[str, str | None]] = {}
    expected_fields = {"item_index", "summary", "target", "new_topic_title"}
    for item in items:
        if not isinstance(item, Mapping) or set(item) != expected_fields:
            continue
        item_index = item.get("item_index")
        if not isinstance(item_index, int) or isinstance(item_index, bool):
            continue
        if counts.get(item_index) != 1 or item_index not in allowed_targets:
            continue
        summary_value = item.get("summary")
        target = item.get("target")
        if not isinstance(summary_value, str) or not isinstance(target, str):
            continue
        summary = summary_value.strip()
        if not _balanced_summary_is_safe(summary) or target not in allowed_targets[item_index]:
            continue
        topic_value = item.get("new_topic_title")
        if target == "new_topic":
            if not isinstance(topic_value, str):
                continue
            new_topic_title: str | None = topic_value.strip()
            if not _balanced_new_topic_title_is_safe(new_topic_title):
                continue
        else:
            if topic_value is not None:
                continue
            new_topic_title = None
        accepted[item_index] = {
            "summary": summary,
            "target": target,
            "new_topic_title": new_topic_title,
        }
    return accepted


def _safe_balanced_topic_slug(title: str) -> str:
    ascii_words = re.findall(r"[A-Za-z0-9]+", title.casefold())
    slug = "-".join(ascii_words)[:48].strip("-")
    return slug or f"topic-{_digest(title)[:12]}"


def _balanced_topic_directory(context_root: Path, *, title: str) -> Path:
    topics_root = context_root / "topics"
    base = _safe_balanced_topic_slug(title)
    suffixes = ("", *(_digest(title)[:length] for length in (8, 12, 16, 32)))
    for suffix in suffixes:
        name = base if not suffix else f"{base}-{suffix}"
        target = topics_root / name
        description = target / "description.md"
        if not target.exists():
            _atomic_write(
                description,
                f"# {_markdown_label(title)}\n\n{_MANAGED_TOPIC_MARKER}\n".encode("utf-8"),
            )
            return target
        if target.is_dir() and description.is_file():
            markdown = description.read_text(encoding="utf-8")
            if _MANAGED_TOPIC_MARKER in markdown:
                return target
    raise _pipeline_error("balanced topic path conflicts with existing Context")


def _apply_balanced_enrichment(
    context_root: Path,
    *,
    service_id: str,
    document: Mapping[str, object],
    enrichment: Mapping[str, str | None],
    target_paths: Mapping[str, Path],
) -> set[str]:
    logical_id = str(document["logical_id"])
    source_page = context_root / "sources" / _safe_segment(service_id, name="service_id") / f"{_digest(logical_id)}.md"
    summary = enrichment.get("summary")
    target = enrichment.get("target")
    if not isinstance(summary, str) or not isinstance(target, str) or not source_page.is_file():
        raise _pipeline_error("balanced enrichment target is invalid")
    _atomic_write(source_page, _rules_source_page(document, summary_override=summary).encode("utf-8"))
    changed = {source_page.relative_to(context_root).as_posix()}
    if target.startswith("directory_"):
        target_directory = target_paths.get(target)
        if target_directory is None:
            raise _pipeline_error("balanced directory target is invalid")
        description = target_directory / "description.md"
        _append_managed_source_link(
            description,
            context_root=context_root,
            source_page=source_page,
            title=str(document.get("title", logical_id)),
        )
        changed.add(description.relative_to(context_root).as_posix())
    elif target == "new_topic":
        topic_title = enrichment.get("new_topic_title")
        if not isinstance(topic_title, str):
            raise _pipeline_error("balanced topic title is invalid")
        topic_directory = _balanced_topic_directory(context_root, title=topic_title)
        topic_description = topic_directory / "description.md"
        _append_managed_source_link(
            topic_description,
            context_root=context_root,
            source_page=source_page,
            title=str(document.get("title", logical_id)),
        )
        _append_managed_topic_link(context_root, topic_directory=topic_directory, title=topic_title)
        changed.update(
            {
                topic_description.relative_to(context_root).as_posix(),
                "topics/description.md",
            }
        )
    elif target != "sources":
        raise _pipeline_error("balanced enrichment target is unsupported")
    return changed


def _markdown_link_target(relative: str) -> str:
    """Render a local Markdown destination without losing filename spaces."""

    return f"<{relative}>" if any(character.isspace() for character in relative) else relative


def _markdown_reference_text(markdown: str) -> str:
    """Return Markdown text outside fenced and inline code."""

    lines: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    for line in markdown.splitlines():
        if fence_character is not None:
            closing = re.match(r"^ {0,3}(`{3,}|~{3,})[ \t]*$", line)
            if closing is not None and closing.group(1)[0] == fence_character and len(closing.group(1)) >= fence_length:
                fence_character = None
                fence_length = 0
            continue
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if opening is not None:
            marker = opening.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
            continue
        lines.append(re.sub(r"`+[^`\r\n]*`+", "", line))
    return "\n".join(lines)


def _markdown_destination(raw_target: str) -> str | None:
    """Parse one inline Markdown destination, including fragments and titles."""

    target = raw_target.strip()
    if not target or target.startswith("#"):
        return None
    if target.startswith("<"):
        match = re.fullmatch(r'<([^<>]+)>(?:\s+(?:"[^"]*"|\'[^\']*\'|\([^)]*\)))?', target)
    else:
        match = re.fullmatch(r'(\S+)(?:\s+(?:"[^"]*"|\'[^\']*\'|\([^)]*\)))?', target)
    if match is None:
        return None
    destination = re.split(r"[?#]", match.group(1), maxsplit=1)[0].strip()
    return destination or None


def _reference_source_path(
    source_root: Path,
    source_id: str,
    *,
    error: Callable[[str], BaseError],
) -> Path:
    if _SOURCE_METADATA_ID.fullmatch(source_id) is None:
        raise error("candidate atomic source ID is invalid")
    source_path = source_root / f"{source_id}.md"
    try:
        read_source_metadata(source_path)
    except BaseError as exc:
        raise error("candidate atomic source metadata is missing or invalid") from exc
    return source_path


def _resolve_short_references(
    context_root: Path,
    *,
    final_context_root: Path,
    source_root: Path,
    alias_targets: Mapping[str, str],
) -> None:
    """Replace every known run token with a final relative Markdown link."""

    _assert_no_symlinks(context_root)
    replacements: dict[Path, bytes] = {}
    validated_sources: dict[str, Path] = {}
    for page in sorted(context_root.rglob("*.md")):
        try:
            text = page.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise _pipeline_error("candidate Markdown could not be read for source reference resolution") from exc
        display_numbers: dict[str, int] = {}
        resolved = _SHORT_REFERENCE.sub(
            _short_reference_replacer(
                context_root=context_root,
                current_page=page,
                final_context_root=final_context_root,
                source_root=source_root,
                alias_targets=alias_targets,
                validated_sources=validated_sources,
                display_numbers=display_numbers,
            ),
            text,
        )
        if "[[ref:" in resolved:
            raise _pipeline_error("candidate contains a malformed or unresolved short source reference")
        if resolved != text:
            replacements[page] = resolved.encode("utf-8")
    for page, data in replacements.items():
        _atomic_write(page, data)


def _short_reference_replacer(
    *,
    context_root: Path,
    current_page: Path,
    final_context_root: Path,
    source_root: Path,
    alias_targets: Mapping[str, str],
    validated_sources: dict[str, Path],
    display_numbers: dict[str, int],
) -> Callable[[re.Match[str]], str]:
    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        source_id = alias_targets.get(token)
        if source_id is None:
            raise _pipeline_error("candidate contains an unknown short source reference")
        source_path = validated_sources.get(source_id)
        if source_path is None:
            source_path = _reference_source_path(source_root, source_id, error=_pipeline_error)
            validated_sources[source_id] = source_path
        try:
            relative_target = os.path.relpath(
                source_path,
                start=(final_context_root / current_page.relative_to(context_root)).parent,
            ).replace("\\", "/")
        except ValueError as exc:
            raise _pipeline_error("candidate source reference cannot be made relative") from exc
        display_number = display_numbers.setdefault(source_id, len(display_numbers) + 1)
        return f"[来源{display_number}]({_markdown_link_target(relative_target)})"

    return replace


def _classify_reference_target(
    raw_target: str,
    *,
    page_relative: PurePosixPath,
    context_root: Path,
    final_context_root: Path,
    source_root: Path,
    error: Callable[[str], BaseError],
) -> tuple[str, str] | None:
    destination = _markdown_destination(raw_target)
    if destination is None:
        return None
    if len(destination) > _MAX_AGENT_CONTEXT_PATH_CHARS:
        raise error("candidate Markdown reference path exceeds the safety limit")
    if destination.startswith(("/", "\\")) or "\\" in destination or re.fullmatch(r"[A-Za-z]:.*", destination):
        raise error("candidate Markdown reference leaves the allowed roots")
    if _URI_SCHEME.match(destination) is not None:
        return None
    if PurePosixPath(destination).suffix.casefold() != ".md":
        return None
    logical_context_root = final_context_root.resolve()
    logical_source_root = source_root.resolve()
    logical_page = logical_context_root / Path(*page_relative.parts)
    logical_target = (logical_page.parent / destination).resolve()
    if logical_target.is_relative_to(logical_context_root):
        relative = logical_target.relative_to(logical_context_root)
        if relative.suffix.casefold() != ".md":
            return None
        candidate_target = context_root / relative
        if not candidate_target.is_file() or candidate_target.is_symlink():
            raise error("candidate Markdown reference target is missing")
        return "context", relative.as_posix()
    if logical_target.is_relative_to(logical_source_root):
        relative = logical_target.relative_to(logical_source_root)
        if len(relative.parts) != 1 or relative.suffix.casefold() != ".md":
            raise error("candidate atomic source reference is invalid")
        source_id = relative.stem
        _reference_source_path(source_root, source_id, error=error)
        return "source", source_id
    raise error("candidate Markdown reference leaves the allowed roots")


def _validate_reference_graph(
    context_root: Path,
    *,
    final_context_root: Path,
    source_root: Path,
    alias_targets: Mapping[str, str] | None = None,
    repairable: bool,
) -> None:
    """Require every Context Markdown to reach an atomic source."""

    error = _pipeline_error if repairable else _publish_error
    _assert_no_symlinks(context_root)
    pages = {page.relative_to(context_root).as_posix(): page for page in context_root.rglob("*.md") if page.is_file()}
    root_description = "description.md"
    if root_description not in pages:
        raise error("candidate root description.md is missing")

    context_edges: dict[str, set[str]] = {relative: set() for relative in pages}
    source_edges: dict[str, set[str]] = {relative: set() for relative in pages}
    for relative, page in pages.items():
        try:
            text = page.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise error("candidate Markdown reference graph could not be read") from exc
        if "[[ref:" in _SHORT_REFERENCE.sub("", text):
            raise error("candidate contains a malformed or unresolved short source reference")
        reference_text = _markdown_reference_text(text)
        for token_match in _SHORT_REFERENCE.finditer(reference_text):
            token = token_match.group(0)
            source_id = alias_targets.get(token) if alias_targets is not None else None
            if source_id is None:
                raise error("candidate contains an unknown short source reference")
            _reference_source_path(source_root, source_id, error=error)
            source_edges[relative].add(source_id)
        page_relative = PurePosixPath(relative)
        for raw_target in _MARKDOWN_LINK.findall(reference_text):
            classified = _classify_reference_target(
                raw_target,
                page_relative=page_relative,
                context_root=context_root,
                final_context_root=final_context_root,
                source_root=source_root,
                error=error,
            )
            if classified is None:
                continue
            kind, target = classified
            if kind == "source":
                source_edges[relative].add(target)
            elif target == relative:
                raise error("candidate Markdown contains a self-reference")
            else:
                context_edges[relative].add(target)

    directory_paths = {PurePosixPath(relative).parent for relative in pages}
    directory_paths.add(PurePosixPath("."))
    for directory in sorted(directory_paths, key=lambda value: (len(value.parts), value.as_posix())):
        description = (directory / "description.md").as_posix()
        if description.startswith("./"):
            description = description[2:]
        if description not in pages:
            raise error("candidate Context directory is missing description.md")
        direct_pages = {
            relative
            for relative in pages
            if PurePosixPath(relative).parent == directory and PurePosixPath(relative).name != "description.md"
        }
        direct_directories = {child for child in directory_paths if child != directory and child.parent == directory}
        expected = direct_pages | {(child / "description.md").as_posix() for child in direct_directories}
        missing = expected - context_edges[description]
        if missing:
            raise error("candidate description.md does not link every direct child")

    reachable_from_root: set[str] = set()
    pending = [root_description]
    while pending:
        current = pending.pop()
        if current in reachable_from_root:
            continue
        reachable_from_root.add(current)
        pending.extend(context_edges[current] - reachable_from_root)
    if reachable_from_root != set(pages):
        raise error("candidate Context contains an orphan Markdown page")

    source_reachable = {relative for relative, targets in source_edges.items() if targets}
    while True:
        expanded = source_reachable | {
            relative for relative, targets in context_edges.items() if targets.intersection(source_reachable)
        }
        if expanded == source_reachable:
            break
        source_reachable = expanded
    if source_reachable != set(pages):
        raise error("candidate Context contains a reference chain without an atomic source")


def _source_ids_reachable_from_page(
    context_root: Path,
    *,
    source_root: Path,
    page_relative: str,
) -> set[str]:
    """Return atomic sources reachable from one already-published Context page."""

    final_context_root = context_root
    pending = [page_relative]
    visited: set[str] = set()
    sources: set[str] = set()
    while pending:
        relative = pending.pop()
        if relative in visited:
            continue
        visited.add(relative)
        page = context_root / _validated_relative_path(relative, name="existing Context page path")
        try:
            reference_text = _markdown_reference_text(page.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            raise _pipeline_error("existing Context reference graph could not be read") from exc
        for raw_target in _MARKDOWN_LINK.findall(reference_text):
            classified = _classify_reference_target(
                raw_target,
                page_relative=PurePosixPath(relative),
                context_root=context_root,
                final_context_root=final_context_root,
                source_root=source_root,
                error=_pipeline_error,
            )
            if classified is None:
                continue
            kind, target = classified
            if kind == "source":
                sources.add(target)
            elif target not in visited:
                pending.append(target)
    return sources


def _register_batch_source_refs(
    source_root: Path,
    batch: FetchBatch,
    *,
    provider: str,
    service_id: str,
    state: dict[str, object],
) -> dict[str, str]:
    """Register batch sources and return logical_id to [[ref:N]]."""

    aliases_value = state.get("source_alias_by_id")
    logical_ids_value = state.get("source_id_by_logical_id")
    if not isinstance(aliases_value, dict) or not isinstance(logical_ids_value, dict):
        raise _pipeline_error("run source reference state is invalid")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in aliases_value.items()):
        raise _pipeline_error("run source alias state is invalid")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in logical_ids_value.items()):
        raise _pipeline_error("run logical source state is invalid")
    source_alias_by_id = cast(dict[str, str], aliases_value)
    source_id_by_logical_id = cast(dict[str, str], logical_ids_value)
    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    source_refs: dict[str, str] = {}
    for item in batch.items:
        source_id = upsert_source_metadata(
            source_root,
            item,
            provider=provider,
            service_id=service_id,
            observed_at=observed_at,
        )
        source_ref = source_alias_by_id.get(source_id)
        if source_ref is None:
            source_ref = f"[[ref:{len(source_alias_by_id)}]]"
            source_alias_by_id[source_id] = source_ref
        source_id_by_logical_id[item.logical_id] = source_id
        source_refs[item.logical_id] = source_ref
    return source_refs


class ContextPipelineService:
    """Process queued batches and publish each completed fetch run once."""

    def __init__(
        self,
        *,
        home: Path,
        config: PersonalContextConfig,
        input_queue: asyncio.Queue[object],
    ) -> None:
        self._home = home.expanduser().resolve()
        self._config = config
        self._input_queue = input_queue
        self._context_root = self._home / "workspace" / "context"
        self._source_meta_root = self._home / "workspace" / "source-meta"
        self._sandboxes_root = self._home / "workspace" / "sandboxes"
        self._consumer_task: asyncio.Task[None] | None = None
        self._accepting = False
        self._active_completion: asyncio.Future[None] | None = None
        self._run_states: dict[tuple[str, str], dict[str, object]] = {}
        self._publish_lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the sole consumer coroutine; repeated calls are idempotent."""
        if self.is_running():
            return
        await _cancel_safe_to_thread(self._cleanup_stale_run_sandboxes)
        self._accepting = True
        self._consumer_task = asyncio.create_task(self._consume(), name="personal-context-context-pipeline")

    async def stop(self, *, timeout_seconds: float) -> None:
        """Stop intake and drain the queue, cancelling unfinished work at the deadline."""
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self._accepting = False
        task = self._consumer_task
        if task is None:
            self._fail_queued(_pipeline_error("context pipeline stopped before processing"))
            return

        join_task = asyncio.create_task(self._input_queue.join())
        try:
            await asyncio.wait_for(join_task, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            join_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await join_task
            error = _pipeline_error("context pipeline stop timed out")
            self._fail_active(error)
            self._fail_queued(error)
        except asyncio.CancelledError:
            if not join_task.done():
                join_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await join_task
            error = _pipeline_error("context pipeline stop cancelled")
            self._fail_active(error)
            self._fail_queued(error)
            raise
        finally:
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            try:
                await self._cleanup_all_run_states()
            finally:
                self._consumer_task = None
                self._active_completion = None

    def is_running(self) -> bool:
        """Return whether the unique consumer task is alive."""
        return self._consumer_task is not None and not self._consumer_task.done()

    async def _consume(self) -> None:
        while True:
            item = await self._input_queue.get()
            self._active_completion = _queue_item_completion(item)
            try:
                await self._process_queue_item(item)
            except asyncio.CancelledError:
                self._fail_active(_pipeline_error("context pipeline event cancelled"))
                raise
            except BaseError as error:
                self._fail_active(error)
            except Exception:
                self._fail_active(_pipeline_error())
            finally:
                self._active_completion = None
                self._input_queue.task_done()

    async def _process_queue_item(self, item: object) -> None:
        if not isinstance(item, tuple) or len(item) != 5:
            raise _pipeline_error("invalid context pipeline queue item")
        tag, service_id, run_id, payload, completion = item
        if not isinstance(completion, asyncio.Future):
            raise _pipeline_error("queue item completion must be a Future")
        if completion.done():
            return
        if not isinstance(tag, str) or not isinstance(service_id, str) or not isinstance(run_id, str):
            raise _pipeline_error("invalid context pipeline queue item")
        safe_service = _safe_segment(service_id, name="service_id")
        safe_run = _safe_segment(run_id, name="run_id")
        if tag == "batch":
            if not isinstance(payload, FetchBatch):
                raise _pipeline_error("batch event payload must be a FetchBatch")
            await self._process_batch_event(safe_service, safe_run, payload)
        elif tag == "finish":
            if payload is not None:
                raise _pipeline_error("finish event payload must be None")
            await self._finish_run_event(safe_service, safe_run)
        elif tag == "abort":
            if payload is not None:
                raise _pipeline_error("abort event payload must be None")
            await self._abort_run_event(safe_service, safe_run)
        else:
            raise _pipeline_error("unknown context pipeline event tag")
        if not completion.done():
            completion.set_result(None)

    async def _process_batch_event(self, service_id: str, run_id: str, batch: FetchBatch) -> None:
        """Process one batch into its run-owned disk inputs without publishing."""

        key = (service_id, run_id)
        state = self._run_states.get(key)
        if state is None:
            if any(
                existing_run == run_id and existing_service != service_id
                for existing_service, existing_run in self._run_states
            ):
                raise _pipeline_error("run_id is already owned by another service")
            sandbox = self._run_sandbox_path(service_id, run_id)
            try:
                _assert_path_chain_no_symlinks(self._home / "workspace")
                _assert_no_symlinks(self._sandboxes_root)
                sandbox.mkdir(parents=True, exist_ok=False)
            except OSError as exc:
                raise _publish_error("run sandbox could not be created") from exc
            state = {
                "sandbox": sandbox,
                "batch_ids": [],
                "batch_count": 0,
                "provider": self._provider_for_service(service_id),
                "source_alias_by_id": {},
                "source_id_by_logical_id": {},
                "materialized_source_path": None,
                "materialized_revision": None,
                "status": "processing",
            }
            self._run_states[key] = state
        elif state.get("status") != "processing":
            await self._cleanup_run_state(key)
            raise _pipeline_error("run is not accepting batch events")

        batch_ids = state.get("batch_ids")
        if not isinstance(batch_ids, list):
            await self._cleanup_run_state(key)
            raise _pipeline_error("run batch state is invalid")
        safe_batch = _safe_segment(batch.batch_id, name="batch_id")
        if safe_batch in batch_ids:
            await self._cleanup_run_state(key)
            raise _pipeline_error("duplicate batch_id in run")
        try:
            self._merge_materialized_source(state, batch)
        except BaseError:
            await self._cleanup_run_state(key)
            raise
        sandbox_value = state.get("sandbox")
        if not isinstance(sandbox_value, Path):
            await self._cleanup_run_state(key)
            raise _pipeline_error("run sandbox state is invalid")
        try:
            await _cancel_safe_to_thread(_assert_no_symlinks, sandbox_value)
            provider = state.get("provider")
            if not isinstance(provider, str) or not provider:
                raise _pipeline_error("run provider state is invalid")
            source_refs = await _cancel_safe_to_thread(
                _register_batch_source_refs,
                self._source_meta_root,
                batch,
                provider=provider,
                service_id=service_id,
                state=state,
            )
            record_paths = await _cancel_safe_to_thread(self._write_batch_records, sandbox_value, batch)
            processed = await self._process_deterministic(batch)
            await _cancel_safe_to_thread(
                self._write_processed_batch,
                sandbox_value,
                batch,
                processed,
                record_paths,
                source_refs,
            )
            batch_ids.append(safe_batch)
            state["batch_count"] = len(batch_ids)
        except asyncio.CancelledError:
            await self._cleanup_run_state(key)
            raise
        except BaseError:
            await self._cleanup_run_state(key)
            raise
        except OSError as exc:
            await self._cleanup_run_state(key)
            raise _publish_error("batch inputs could not be written") from exc
        except Exception as exc:
            await self._cleanup_run_state(key)
            raise _pipeline_error("batch processing failed") from exc

    async def _finish_run_event(self, service_id: str, run_id: str) -> None:
        """Compile and publish all persisted batches from one run exactly once."""

        key = (service_id, run_id)
        state = self._run_states.get(key)
        if state is None:
            raise _pipeline_error("run has no processed batches to finish")
        if state.get("status") != "processing":
            await self._cleanup_run_state(key)
            raise _pipeline_error("run has no processed batches to finish")
        batch_count = state.get("batch_count")
        if not isinstance(batch_count, int) or batch_count <= 0:
            await self._cleanup_run_state(key)
            raise _pipeline_error("run has no processed batches to finish")
        sandbox = state.get("sandbox")
        if not isinstance(sandbox, Path):
            await self._cleanup_run_state(key)
            raise _pipeline_error("run sandbox state is invalid")
        state["status"] = "finishing"
        try:
            processed = await _cancel_safe_to_thread(self._prepare_run_finish_io, sandbox, dict(state))
            batch = FetchBatch(
                batch_id="finish-run",
                items=(),
                materialized_source_path=cast(str | None, state.get("materialized_source_path")),
                materialized_revision=cast(str | None, state.get("materialized_revision")),
            )
            aliases_value = state.get("source_alias_by_id")
            if not isinstance(aliases_value, dict) or not all(
                isinstance(source_id, str) and isinstance(source_ref, str)
                for source_id, source_ref in aliases_value.items()
            ):
                raise _pipeline_error("run source alias state is invalid")
            alias_targets = {
                source_ref: source_id for source_id, source_ref in cast(dict[str, str], aliases_value).items()
            }
            logical_sources_value = state.get("source_id_by_logical_id")
            if not isinstance(logical_sources_value, dict) or not all(
                isinstance(logical_id, str) and isinstance(source_id, str)
                for logical_id, source_id in logical_sources_value.items()
            ):
                raise _pipeline_error("run logical source state is invalid")
            logical_sources = cast(dict[str, str], logical_sources_value)
            deleted_source_ids = {
                logical_sources[logical_id]
                for logical_id in _processed_deleted_ids(processed)
                if logical_id in logical_sources
            }
            filesystem_profile = await self._filesystem_with_fallback(
                processed=processed,
                sandbox=sandbox,
                batch=batch,
                alias_targets=alias_targets,
                deleted_source_ids=deleted_source_ids,
                service_id=service_id,
            )
            actual_profile = filesystem_profile
            processed["actual_profile"] = actual_profile
            documents_value = processed.get("documents", [])
            if isinstance(documents_value, list):
                for document in documents_value:
                    if isinstance(document, dict):
                        document["actual_profile"] = actual_profile
            await self._publish_processed(
                service_id=service_id,
                run_id=run_id,
                batch=batch,
                processed=processed,
                sandbox=sandbox,
                alias_targets=alias_targets,
            )
            state["status"] = "published"
        finally:
            await self._cleanup_run_state(key)

    async def _abort_run_event(self, service_id: str, run_id: str) -> None:
        """Idempotently discard one unpublished run."""

        await self._cleanup_run_state((service_id, run_id))

    @staticmethod
    def _merge_materialized_source(state: dict[str, object], batch: FetchBatch) -> None:
        candidate = batch.materialized_source_path
        revision = batch.materialized_revision
        current_candidate = state.get("materialized_source_path")
        current_revision = state.get("materialized_revision")
        if current_candidate is None and current_revision is None:
            state["materialized_source_path"] = candidate
            state["materialized_revision"] = revision
            return
        if candidate is not None and (candidate != current_candidate or revision != current_revision):
            raise _pipeline_error("materialized source changed within one run")

    @staticmethod
    def _write_batch_records(sandbox: Path, batch: FetchBatch) -> dict[str, str]:
        batch_id = _safe_segment(batch.batch_id, name="batch_id")
        batch_root = sandbox / "inputs" / "records" / batch_id
        if batch_root.exists() or batch_root.is_symlink():
            raise _pipeline_error("duplicate batch input directory")
        record_paths: dict[str, str] = {}
        for index, item in enumerate(batch.items):
            if item.logical_id in record_paths:
                raise _pipeline_error("duplicate logical_id in batch")
            entry_name = f"{index:04d}-{_digest(item.logical_id)[:12]}"
            entry_root = batch_root / entry_name
            content = item.content or ""
            _atomic_write(entry_root / "content.md", content.encode("utf-8"))
            # The definitive small/large preview is rewritten at finish, once
            # the complete run size is known.  Keeping the small-run preview
            # here makes an interrupted run locally inspectable without ever
            # truncating the full content.md artifact.
            _atomic_write(entry_root / "context.md", content[:_SMALL_RUN_PREVIEW_CHARS].encode("utf-8"))
            metadata = {
                "index": index,
                "batch_id": batch_id,
                "logical_id": item.logical_id,
                "revision_id": item.revision_id,
                "operation": item.operation,
                "title": item.title,
                "original_ref": _agent_reference(item.original_ref),
                "metadata": _agent_metadata(item.metadata),
            }
            _atomic_write(
                entry_root / "metadata.json",
                (json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            )
            if isinstance(item.raw_snapshot, str):
                _atomic_write(entry_root / "raw.txt", item.raw_snapshot.encode("utf-8"))
            elif isinstance(item.raw_snapshot, bytes):
                _atomic_write(entry_root / "raw.bin", item.raw_snapshot)
            record_paths[item.logical_id] = _relative_file(entry_root, sandbox)
        return record_paths

    @staticmethod
    def _write_processed_batch(
        sandbox: Path,
        batch: FetchBatch,
        processed: Mapping[str, object],
        record_paths: Mapping[str, str],
        source_refs: Mapping[str, str],
    ) -> None:
        batch_id = _safe_segment(batch.batch_id, name="batch_id")
        processed_root = sandbox / "inputs" / "processed" / batch_id
        if processed_root.exists() or processed_root.is_symlink():
            raise _pipeline_error("duplicate processed batch directory")
        processed_root.mkdir(parents=True)
        documents_value = processed.get("documents", [])
        documents = documents_value if isinstance(documents_value, list) else []
        blocks_value = processed.get("blocks", [])
        blocks = blocks_value if isinstance(blocks_value, list) else []
        for index, document in enumerate(documents):
            if not isinstance(document, Mapping) or not isinstance(document.get("logical_id"), str):
                raise _pipeline_error("processed document is invalid")
            logical_id = str(document["logical_id"])
            record_root_value = record_paths.get(logical_id)
            if record_root_value is None:
                raise _pipeline_error("processed document has no source record")
            source_ref = source_refs.get(logical_id)
            if source_ref is None:
                raise _pipeline_error("processed document has no source reference")
            entry_root = processed_root / f"{index:04d}-{_digest(logical_id)[:12]}"
            markdown = f"{source_ref}\n\n{document.get('markdown', '')}"
            _atomic_write(entry_root / "context-document.md", markdown.encode("utf-8"))
            document_blocks = [
                dict(block) for block in blocks if isinstance(block, Mapping) and block.get("logical_id") == logical_id
            ]
            blocks_text = "\n".join(json.dumps(block, ensure_ascii=False, sort_keys=True) for block in document_blocks)
            _atomic_write(
                entry_root / "blocks.jsonl",
                ((blocks_text + "\n") if blocks_text else "").encode("utf-8"),
            )
            record_root = sandbox / _validated_relative_path(record_root_value, name="source record path")
            raw_path: str | None = None
            for raw_name in ("raw.txt", "raw.bin"):
                candidate = record_root / raw_name
                if candidate.is_file():
                    raw_path = _relative_file(candidate, sandbox)
                    break
            record = {
                "logical_id": logical_id,
                "source_ref": source_ref,
                "revision_id": str(document.get("revision_id", "")),
                "title": str(document.get("title", logical_id)),
                "original_ref": _agent_reference(str(document.get("original_ref", ""))),
                "metadata": _agent_metadata(document.get("metadata", {})),
                "actual_profile": str(document.get("actual_profile", processed.get("actual_profile", "deterministic"))),
                "raw_snapshot_path": raw_path,
                "source_record_path": record_root_value,
            }
            _atomic_write(
                entry_root / "record.json",
                (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            )
        deleted_value = processed.get("deleted_ids", [])
        deleted_ids = [str(value) for value in deleted_value] if isinstance(deleted_value, list) else []
        _atomic_write(
            sandbox / "inputs" / "deleted" / f"{batch_id}.json",
            (json.dumps(deleted_ids, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )

    def _prepare_run_finish_io(
        self,
        sandbox: Path,
        state: Mapping[str, object],
    ) -> dict[str, object]:
        """Read one run and freeze its inputs in one worker-thread transaction."""

        _assert_no_symlinks(sandbox)
        processed = self._load_run_processed(state)
        large_run = _is_large_run(processed)
        processed["_large_run"] = large_run
        self._rewrite_run_previews(sandbox, state, large_run=large_run)
        self._write_run_briefing(sandbox, state)
        _make_tree_read_only(sandbox / "inputs")
        return processed

    def _provider_for_service(self, service_id: str) -> str:
        for service in self._config.fetch_services:
            if service.service_id == service_id:
                return service.provider
        # Unit-level callers may exercise the Pipeline without a configured
        # provider.  The service ID is still a stable, non-secret source label.
        return service_id

    @staticmethod
    def _rewrite_run_previews(
        sandbox: Path,
        state: Mapping[str, object],
        *,
        large_run: bool,
    ) -> None:
        batch_ids = state.get("batch_ids")
        if not isinstance(batch_ids, list):
            raise _pipeline_error("run batch state is invalid")
        limit = _LARGE_RUN_PREVIEW_CHARS if large_run else _SMALL_RUN_PREVIEW_CHARS
        try:
            for batch_value in batch_ids:
                batch_id = _safe_segment(batch_value, name="batch_id")
                records_root = sandbox / "inputs" / "records" / batch_id
                for entry_root in sorted(records_root.iterdir()):
                    if not entry_root.is_dir() or entry_root.is_symlink():
                        raise _pipeline_error("source record directory is invalid")
                    content = (entry_root / "content.md").read_text(encoding="utf-8")
                    _atomic_write(entry_root / "context.md", content[:limit].encode("utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise _publish_error("run source previews could not be written") from exc

    @staticmethod
    def _load_run_processed(state: Mapping[str, object]) -> dict[str, object]:
        sandbox = state.get("sandbox")
        batch_ids = state.get("batch_ids")
        if not isinstance(sandbox, Path) or not isinstance(batch_ids, list):
            raise _pipeline_error("run state is invalid")
        documents: list[dict[str, object]] = []
        blocks: list[dict[str, object]] = []
        deleted_ids: list[str] = []
        try:
            for batch_value in batch_ids:
                batch_id = _safe_segment(batch_value, name="batch_id")
                batch_root = sandbox / "inputs" / "processed" / batch_id
                if not batch_root.is_dir() or batch_root.is_symlink():
                    raise _pipeline_error("processed batch directory is missing")
                _assert_no_symlinks(batch_root)
                for entry_root in sorted(batch_root.iterdir()):
                    if not entry_root.is_dir() or entry_root.is_symlink():
                        raise _pipeline_error("processed record directory is invalid")
                    record = json.loads((entry_root / "record.json").read_text(encoding="utf-8"))
                    if not isinstance(record, Mapping):
                        raise _pipeline_error("processed record is invalid")
                    raw_snapshot: str | bytes | None = None
                    raw_value = record.get("raw_snapshot_path")
                    if raw_value is not None:
                        raw_path = sandbox / _validated_relative_path(raw_value, name="raw snapshot path")
                        _relative_file(raw_path, sandbox)
                        raw_snapshot = (
                            raw_path.read_text(encoding="utf-8") if raw_path.suffix == ".txt" else raw_path.read_bytes()
                        )
                    metadata_value = record.get("metadata", {})
                    document: dict[str, object] = {
                        "logical_id": str(record.get("logical_id", "")),
                        "revision_id": str(record.get("revision_id", "")),
                        "title": str(record.get("title", "")),
                        "markdown": (entry_root / "context-document.md").read_text(encoding="utf-8"),
                        "original_ref": str(record.get("original_ref", "")),
                        "metadata": dict(metadata_value) if isinstance(metadata_value, Mapping) else {},
                        "raw_snapshot": raw_snapshot,
                        "actual_profile": str(record.get("actual_profile", "deterministic")),
                    }
                    if not document["logical_id"] or not document["revision_id"] or not document["original_ref"]:
                        raise _pipeline_error("processed record identifiers are invalid")
                    documents.append(document)
                    for line in (entry_root / "blocks.jsonl").read_text(encoding="utf-8").splitlines():
                        value = json.loads(line)
                        if not isinstance(value, dict):
                            raise _pipeline_error("processed block is invalid")
                        blocks.append(value)
                deleted_value = json.loads(
                    (sandbox / "inputs" / "deleted" / f"{batch_id}.json").read_text(encoding="utf-8")
                )
                if not isinstance(deleted_value, list) or not all(isinstance(value, str) for value in deleted_value):
                    raise _pipeline_error("deleted ID list is invalid")
                deleted_ids.extend(deleted_value)
        except (OSError, ValueError, TypeError) as exc:
            raise _publish_error("run inputs could not be read") from exc
        return {
            "documents": documents,
            "blocks": blocks,
            "deleted_ids": deleted_ids,
            "actual_profile": "deterministic",
        }

    @staticmethod
    def _write_run_briefing(sandbox: Path, state: Mapping[str, object]) -> None:
        batch_ids = state.get("batch_ids")
        if not isinstance(batch_ids, list):
            raise _pipeline_error("run batch state is invalid")
        aliases_value = state.get("source_alias_by_id")
        logical_ids_value = state.get("source_id_by_logical_id")
        if not isinstance(aliases_value, Mapping) or not isinstance(logical_ids_value, Mapping):
            raise _pipeline_error("run source reference state is invalid")
        source_refs_by_logical: dict[str, str] = {}
        for logical_id, source_id in logical_ids_value.items():
            source_ref = aliases_value.get(source_id)
            if not isinstance(logical_id, str) or not isinstance(source_id, str) or not isinstance(source_ref, str):
                raise _pipeline_error("run source reference state is invalid")
            source_refs_by_logical[logical_id] = source_ref
        provider = str(state.get("provider") or "unknown")
        entries: list[dict[str, object]] = []
        lines = [
            "# PersonalContext Agent Input Briefing",
            "",
            "The complete fetch run is stored below inputs/records/ and inputs/processed/.",
            "Read complete files only when this preview is insufficient.",
            "",
        ]
        try:
            processed_paths: dict[str, dict[str, str]] = {}
            for batch_value in batch_ids:
                batch_id = _safe_segment(batch_value, name="batch_id")
                processed_root = sandbox / "inputs" / "processed" / batch_id
                for entry_root in sorted(processed_root.iterdir()):
                    if not entry_root.is_dir() or entry_root.is_symlink():
                        raise _pipeline_error("processed record directory is invalid")
                    record = json.loads((entry_root / "record.json").read_text(encoding="utf-8"))
                    if not isinstance(record, Mapping) or not isinstance(record.get("logical_id"), str):
                        raise _pipeline_error("processed record is invalid")
                    logical_id = str(record["logical_id"])
                    source_ref = record.get("source_ref")
                    if not isinstance(source_ref, str) or source_ref != source_refs_by_logical.get(logical_id):
                        raise _pipeline_error("processed source reference is invalid")
                    processed_paths[logical_id] = {
                        "processed_document": _relative_file(entry_root / "context-document.md", sandbox),
                        "blocks": _relative_file(entry_root / "blocks.jsonl", sandbox),
                        "processed_record": _relative_file(entry_root / "record.json", sandbox),
                    }
            for batch_value in batch_ids:
                batch_id = _safe_segment(batch_value, name="batch_id")
                records_root = sandbox / "inputs" / "records" / batch_id
                for entry_root in sorted(records_root.iterdir()):
                    metadata = json.loads((entry_root / "metadata.json").read_text(encoding="utf-8"))
                    if not isinstance(metadata, Mapping):
                        raise _pipeline_error("source record metadata is invalid")
                    logical_id = str(metadata.get("logical_id", ""))
                    preview = _deterministic_briefing_preview((entry_root / "content.md").read_text(encoding="utf-8"))
                    artifacts = {
                        "source_content": _relative_file(entry_root / "content.md", sandbox),
                        "source_preview": _relative_file(entry_root / "context.md", sandbox),
                        "source_metadata": _relative_file(entry_root / "metadata.json", sandbox),
                        **processed_paths.get(logical_id, {}),
                    }
                    raw_snapshot_type = "none"
                    for raw_name, raw_type in (("raw.txt", "text"), ("raw.bin", "binary")):
                        raw_path = entry_root / raw_name
                        if raw_path.is_file():
                            artifacts["source_raw"] = _relative_file(raw_path, sandbox)
                            raw_snapshot_type = raw_type
                            break
                    entry = {
                        "batch_id": batch_id,
                        "logical_id": logical_id,
                        "revision_id": str(metadata.get("revision_id", "")),
                        "provider": provider,
                        "title": metadata.get("title"),
                        "operation": str(metadata.get("operation", "")),
                        "original_ref": _agent_reference(metadata.get("original_ref")),
                        "source_ref": source_refs_by_logical.get(logical_id),
                        "headings": preview["headings"],
                        "summary": preview["summary"],
                        "content_chars": preview["content_chars"],
                        "raw_snapshot_type": raw_snapshot_type,
                        "artifacts": artifacts,
                    }
                    entries.append(entry)
                    outline = " | ".join(
                        f"H{heading['level']} {heading['text']}"
                        for heading in cast(list[dict[str, object]], preview["headings"])
                    )
                    lines.extend(
                        [
                            f"## {entry['title'] or entry['logical_id']}",
                            "",
                            f"- logical_id: `{entry['logical_id']}`",
                            f"- revision: `{entry['revision_id']}`",
                            f"- provider: `{provider}`",
                            f"- batch: `{batch_id}`",
                            f"- original_ref: `{entry['original_ref']}`",
                            f"- source_ref: `{entry['source_ref']}`",
                            f"- content_chars: `{entry['content_chars']}`",
                            f"- raw_snapshot_type: `{raw_snapshot_type}`",
                            f"- outline: `{outline or '(none)'}`",
                            f"- artifacts: `{json.dumps(artifacts, ensure_ascii=False, sort_keys=True)}`",
                            "",
                            str(preview["summary"]),
                            "",
                        ]
                    )
            briefing = {"schema_version": 1, "source_count": len(entries), "sources": entries}
            _atomic_write(
                sandbox / "inputs" / "briefing.json",
                (json.dumps(briefing, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            )
            _atomic_write(
                sandbox / "inputs" / "briefing.md",
                ("\n".join(lines).rstrip() + "\n").encode("utf-8"),
            )
        except (OSError, ValueError, TypeError) as exc:
            raise _publish_error("run briefing could not be written") from exc

    def _run_sandbox_path(self, service_id: str, run_id: str) -> Path:
        safe_service = _safe_segment(service_id, name="service_id")
        safe_run = _safe_segment(run_id, name="run_id")
        _assert_path_chain_no_symlinks(self._sandboxes_root)
        root = self._sandboxes_root.resolve()
        target = self._sandboxes_root / safe_service / safe_run
        try:
            relative = target.resolve().relative_to(root)
        except (OSError, ValueError) as exc:
            raise _publish_error("run sandbox escaped its managed root") from exc
        if relative.parts != (safe_service, safe_run):
            raise _publish_error("run sandbox is not a controlled service/run path")
        return target

    async def _cleanup_run_state(self, key: tuple[str, str]) -> None:
        await _cancel_safe_to_thread(self._delete_run_sandbox, key)
        self._run_states.pop(key, None)

    def _delete_run_sandbox(self, key: tuple[str, str]) -> None:
        """Delete one controlled run tree without mutating in-memory state."""

        target = self._run_sandbox_path(*key)
        if target.exists() or target.is_symlink():
            _assert_no_symlinks(target)
            try:
                _make_tree_writable(target)
                shutil.rmtree(target)
            except OSError as exc:
                raise _publish_error("run sandbox could not be removed") from exc
        service_root = target.parent
        with contextlib.suppress(OSError):
            service_root.rmdir()

    async def _cleanup_all_run_states(self) -> None:
        first_error: BaseError | None = None
        for key in list(self._run_states):
            try:
                await self._cleanup_run_state(key)
            except BaseError as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    def _cleanup_stale_run_sandboxes(self) -> None:
        _assert_path_chain_no_symlinks(self._home / "workspace")
        for root, name in (
            (self._home / "workspace" / "source-proofs", "legacy source proof root"),
            (self._home / "materialized-sources", "materialized source root"),
        ):
            _assert_path_chain_no_symlinks(root)
            if not root.exists() and not root.is_symlink():
                continue
            if root.is_symlink() or not root.is_dir():
                raise _publish_error(f"{name} is invalid")
            _assert_no_symlinks(root)
            try:
                _make_tree_writable(root)
                shutil.rmtree(root)
            except OSError as exc:
                raise _publish_error(f"{name} could not be cleaned") from exc
        _assert_no_symlinks(self._sandboxes_root)
        if not self._sandboxes_root.exists():
            return
        if not self._sandboxes_root.is_dir():
            raise _publish_error("sandbox root is not a directory")
        try:
            for service_root in list(self._sandboxes_root.iterdir()):
                if service_root.is_symlink() or not service_root.is_dir():
                    raise _publish_error("sandbox root contains an uncontrolled entry")
                service_id = _safe_segment(service_root.name, name="service_id")
                for run_root in list(service_root.iterdir()):
                    if run_root.is_symlink() or not run_root.is_dir():
                        raise _publish_error("service sandbox contains an uncontrolled entry")
                    run_id = _safe_segment(run_root.name, name="run_id")
                    if self._run_sandbox_path(service_id, run_id) != run_root:
                        raise _publish_error("stale run path is not controlled")
                    _assert_no_symlinks(run_root)
                    _make_tree_writable(run_root)
                    shutil.rmtree(run_root)
                service_root.rmdir()
        except OSError as exc:
            raise _publish_error("stale run sandboxes could not be cleaned") from exc

    async def _process_deterministic(self, batch: FetchBatch) -> dict[str, object]:
        """Normalize one batch without consulting the configured Filesystem profile."""

        documents: list[dict[str, object]] = []
        blocks: list[dict[str, object]] = []
        for item in batch.items:
            markdown = _normalize_markdown(item.content or "")
            document: dict[str, object] = {
                "logical_id": item.logical_id,
                "revision_id": item.revision_id,
                "title": _title_for(item),
                "markdown": markdown,
                "original_ref": item.original_ref,
                "metadata": dict(item.metadata),
                "raw_snapshot": item.raw_snapshot,
                "actual_profile": "deterministic",
            }
            documents.append(document)
            for order, block_text in enumerate(_split_blocks(markdown)):
                blocks.append(
                    {
                        "block_id": _digest(f"{item.logical_id}:{item.revision_id}:{order}"),
                        "logical_id": item.logical_id,
                        "order": order,
                        "text": block_text,
                    }
                )
        return {
            "documents": documents,
            "blocks": blocks,
            "deleted_ids": [],
            "actual_profile": "deterministic",
        }

    async def _filesystem_with_fallback(
        self,
        *,
        processed: dict[str, object],
        sandbox: Path,
        batch: FetchBatch,
        alias_targets: Mapping[str, str] | None = None,
        deleted_source_ids: set[str] | None = None,
        service_id: str | None = None,
    ) -> str:
        requested = self._config.strategy_profile
        effective_service_id = service_id or "local"

        def prepare_rules_candidate() -> tuple[dict[str, tuple[int, str]], set[str]]:
            _reset_filesystem_sandbox(sandbox)
            baseline = _snapshot_managed_files(self._context_root)
            _prepare_agent_candidate(self._context_root, sandbox)
            changed = _apply_rules_increment(
                sandbox / "context",
                service_id=effective_service_id,
                processed=processed,
                fallback_references=tuple(alias_targets or ()),
            )
            processed["_agent_changed_context_paths"] = changed
            processed["_filesystem_candidate_prepared"] = True
            processed["_filesystem_candidate_profile"] = "rules"
            processed["_balanced_accepted_count"] = 0
            return baseline, changed

        if requested == "rules" or self._config.model_client is None or self._config.model_request is None:
            prepare_rules_candidate()
            return "rules"
        profiles = [
            candidate
            for candidate in ("agent", "balanced", "rules")
            if _PROFILE_RANK[candidate] <= _PROFILE_RANK[requested]
        ]
        materialized_path = (
            _materialize_candidate_source(
                batch.materialized_source_path,
                sandbox=sandbox,
                home=self._home,
            )
            if requested == "agent"
            else None
        )
        materialized_baseline = (
            _snapshot_managed_files(sandbox / "materialized-source") if materialized_path is not None else None
        )
        for candidate in profiles:
            if candidate == "rules":
                prepare_rules_candidate()
                return "rules"
            try:
                _reset_filesystem_sandbox(sandbox)
                context_baseline = _snapshot_managed_files(self._context_root)
                _prepare_agent_candidate(
                    self._context_root,
                    sandbox,
                )
                if candidate == "agent":
                    _remove_rules_pages_for_deleted_ids(
                        sandbox / "context",
                        service_id=effective_service_id,
                        deleted_ids=_processed_deleted_ids(processed),
                    )
                    (sandbox / "tmp").mkdir(parents=True, exist_ok=True)
                else:
                    _apply_rules_increment(
                        sandbox / "context",
                        service_id=effective_service_id,
                        processed=processed,
                        fallback_references=tuple(alias_targets or ()),
                    )
                payload: dict[str, object] = {}
                inputs_baseline: Mapping[str, tuple[int, str]] | None = None
                if candidate == "agent":
                    if (sandbox / "inputs").is_dir():
                        inputs_baseline = _snapshot_managed_files(sandbox / "inputs")
                    else:
                        inputs_baseline = _prepare_agent_inputs(batch, sandbox=sandbox, processed=processed)
                    large_run = bool(processed.get("_large_run", _is_large_run(processed)))
                    payload = {
                        "profile": candidate,
                        "large_run": large_run,
                        "briefing_path": "inputs/briefing.md",
                        "processed_input_root": "inputs/processed",
                        "document_previews": _agent_documents_payload(processed, large_run=large_run),
                        "deleted_count": len(_processed_deleted_ids(processed)),
                        "deleted_input_root": "inputs/deleted",
                        "context_root": "context",
                        "temporary_root": "tmp",
                    }
                if candidate == "agent" and materialized_path is not None:
                    payload["materialized_source_path"] = materialized_path
                    payload["materialized_revision"] = batch.materialized_revision

                changed_paths: set[str]
                balanced_accepted_count = 0
                if candidate == "agent":
                    # The Agent owns the candidate filesystem.  It writes
                    # Markdown/descriptions; the return value is only a
                    # non-empty confirmation string.
                    if large_run:
                        source_reading_instruction = (
                            "This is a large run: use the complete briefing first to group related sources, then "
                            "read only the targeted source_preview, source_content, or Processing artifacts needed "
                            "for each topic. Do not eagerly read every source_preview or source_content. After "
                            "you have enough evidence for one topic, write that topic page before expanding the "
                            "next topic. Never draft multiple complete pages in one model response. At most one "
                            "complete page may be submitted per model response; when a page is long, write a "
                            "concise valid body first and refine it with later edit calls. After writing one page, "
                            "continue with later tool calls until every planned topic page with supported facts "
                            "has been written. Every upsert source with distinct, non-duplicative key facts must "
                            "be represented in a dedicated or related aggregate page. "
                        )
                    else:
                        source_reading_instruction = (
                            "This is a small run: read every bounded source_preview listed in inputs/briefing.md "
                            "before writing. For a page focused on one source, also read its source_content when "
                            "the preview does not contain enough concrete facts. Every upsert source's distinct "
                            "key facts must be represented in a dedicated or related aggregate page. Do not create "
                            "one page per source merely to satisfy this instruction. "
                        )
                    message = UserMessage(
                        content=(
                            "Use the sandbox filesystem to organize the untrusted context data. Read and write "
                            "Markdown pages below context/, including any level's description.md. Start with "
                            "inputs/briefing.md and the existing context/description.md. "
                            + source_reading_instruction
                            + "Then group sources by topic and keep the result concise without discarding concrete "
                            "technical facts. "
                            + _FILESYSTEM_WIKI_INSTRUCTIONS
                            + "PersonalContext applies deletions programmatically. Their full list remains under "
                            "inputs/deleted/; "
                            "read it only when needed for organizing the candidate and do not copy the list into "
                            "your reply. "
                            "Use only read_file, write_file, edit_file, glob, list_files, and grep. Never execute "
                            "code or use unlisted tools. For large files, each write_file or edit_file call may add "
                            "no more than 2000 characters of Markdown. Start a new page with a bounded first section, "
                            "then append bounded sections with edit_file and a short unique tail anchor. "
                            "Do not rewrite "
                            "a complete large file when only one section changes. "
                            "inputs/ and materialized-source/ are read-only. Use tmp/ for scratch files; tmp/ is "
                            "never published. Keep page paths below sandbox/context, ending in .md. Never use parent "
                            "traversal or create a business page named description.md. Do not add YAML/frontmatter, "
                            "credentials, absolute paths, "
                            "network content, or files outside the sandbox. You may use only context/, inputs/, "
                            "tmp/, and the optional materialized source copy. This is a "
                            "disposable PersonalContext sandbox: do not follow generic soft-delete/archive rules, "
                            "do not "
                            "create .archive, .deleted, recycle-bin, or any other root entry. PersonalContext cleans "
                            "scratch files after the attempt. The only permitted sandbox-root "
                            "entries are framework-created .agent_history, context, inputs, tmp, "
                            "and materialized-source. Keep ordinary "
                            "pages that are not part of this batch unchanged. Write all final wiki Markdown in "
                            "Simplified Chinese, retaining English only for proper nouns, technical terms, code, "
                            "paths, and citations where translation would reduce accuracy. Make "
                            "context/description.md a summary-first semantic portal that explains knowledge "
                            "scope, major themes, key findings, comparison entry points, and then navigation; "
                            "do not make it only a file list. Give every business-directory description.md a "
                            "short semantic summary, key information, and links to details. Use short ASCII slug "
                            "filenames and keep long human-readable titles inside Markdown headings. Every "
                            "ordinary Context page you create or materially edit must contain exactly one "
                            "top-level # heading outside fenced code blocks. Reuse or replace an existing source "
                            "heading instead of keeping it and adding another one. All relative "
                            "links between Context pages must be relative to the Markdown file that contains the "
                            "link; for pages in the same directory use other-page.md rather than repeating the "
                            "directory prefix. Do not leave links to planned pages that you did not create. Before "
                            "finishing, perform one lightweight check of the internal Context links you created or "
                            "modified and create the target, fix the link, or remove it when the target is absent. "
                            "Never "
                            "publish runtime files, prompts, plans, logs, traces, or temporary paths into "
                            "context/. After the files are valid, reply with a short confirmation "
                            "only.\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True)
                        )
                    )
                    output = await run_personal_context_agent(
                        model_client=self._config.model_client,
                        model_request=self._config.model_request,
                        sandbox_path=sandbox,
                        messages=[message],
                        validate_result=lambda text, candidate_path: _validate_filesystem_agent_result(
                            text,
                            candidate_path,
                            processed,
                            context_baseline=context_baseline,
                            materialized_baseline=materialized_baseline,
                            inputs_baseline=cast(Mapping[str, tuple[int, str]], inputs_baseline),
                            baseline_root=self._context_root,
                            final_context_root=self._context_root,
                            source_root=self._source_meta_root,
                            alias_targets=alias_targets,
                            deleted_source_ids=deleted_source_ids,
                        ),
                    )
                    del output
                    changed_paths = _changed_context_paths(sandbox / "context", context_baseline)
                else:
                    if candidate != "balanced":
                        raise _pipeline_error("unsupported Filesystem profile")
                    changed_paths, balanced_accepted_count = await self._filesystem_balanced_model_attempt(
                        processed=processed,
                        sandbox=sandbox,
                        service_id=effective_service_id,
                        context_baseline=context_baseline,
                        alias_targets=alias_targets,
                    )
                    processed["_balanced_accepted_count"] = balanced_accepted_count

                _validate_agent_candidate(
                    sandbox / "context",
                    baseline=context_baseline,
                    changed_paths=changed_paths,
                    baseline_root=self._context_root,
                    final_context_root=self._context_root,
                    source_root=self._source_meta_root,
                    deleted_source_ids=deleted_source_ids,
                    require_description=candidate == "agent",
                    require_single_h1=candidate == "agent",
                )
                if (
                    candidate == "agent"
                    and _processed_documents(processed)
                    and not _agent_updated_context_knowledge_page(
                        sandbox / "context",
                        baseline_root=self._context_root,
                        baseline=context_baseline,
                    )
                ):
                    raise _pipeline_error("agent did not add or update any Context knowledge page")
                if alias_targets is not None:
                    _validate_reference_graph(
                        sandbox / "context",
                        final_context_root=self._context_root,
                        source_root=self._source_meta_root,
                        alias_targets=alias_targets,
                        repairable=True,
                    )
                processed["_agent_changed_context_paths"] = changed_paths
                processed["_agent_candidate_prepared"] = True
                processed["_filesystem_candidate_prepared"] = True
                final_candidate = "balanced" if candidate == "balanced" and balanced_accepted_count > 0 else candidate
                if candidate == "balanced" and balanced_accepted_count == 0:
                    final_candidate = "rules"
                processed["_filesystem_candidate_profile"] = final_candidate
                return final_candidate
            except (OSError, UnicodeError) as error:
                raise _publish_error("filesystem candidate could not be prepared") from error
            except Exception as error:
                if not _profile_fallback_allowed(error):
                    raise
                continue
        return "rules"

    async def _filesystem_balanced_model_attempt(
        self,
        *,
        processed: dict[str, object],
        sandbox: Path,
        service_id: str | None,
        context_baseline: Mapping[str, tuple[int, str]],
        alias_targets: Mapping[str, str] | None,
    ) -> tuple[set[str], int]:
        """Enrich one Rules candidate with one model call per five upserts."""

        if self._config.model_client is None or self._config.model_request is None:
            raise build_error(
                StatusCode.CONTEXT_PROACTIVE_CONFIG_INVALID,
                error_msg="model configuration is missing",
            )
        documents = _processed_documents(processed)
        if not documents:
            return _changed_context_paths(sandbox / "context", context_baseline), 0
        model = Model(
            model_client_config=self._config.model_client,
            model_config=self._config.model_request,
        )
        context_root = sandbox / "context"
        effective_service_id = service_id or "local"
        accepted_count = 0
        for start in range(0, len(documents), _BALANCED_GROUP_SIZE):
            group_end = start + _BALANCED_GROUP_SIZE
            group = documents[start:group_end]
            payload_items: list[dict[str, object]] = []
            target_paths_by_index: dict[int, dict[str, Path]] = {}
            allowed_targets: dict[int, set[str]] = {}
            documents_by_index: dict[int, Mapping[str, object]] = {}
            for offset, document in enumerate(group):
                item_index = start + offset
                public_candidates, target_paths = _balanced_directory_candidates(context_root, document)
                preview = _balanced_semantic_preview(_SHORT_REFERENCE.sub("", str(document.get("markdown", ""))))
                payload_items.append(
                    {
                        "item_index": item_index,
                        "title": _markdown_label(str(document.get("title") or "")),
                        "preview": preview,
                        "candidates": public_candidates,
                    }
                )
                target_paths_by_index[item_index] = target_paths
                allowed_targets[item_index] = {"sources", "new_topic", *target_paths}
                documents_by_index[item_index] = document
            request_payload = {"items": payload_items}
            message = UserMessage(
                content=(
                    "Return JSON with exactly one top-level items array. For each supplied item, return exactly "
                    "item_index, summary, target, and new_topic_title. summary must be plain Simplified Chinese "
                    "text of at most 450 characters. target must be one supplied directory ID, sources, or "
                    "new_topic; use a short new_topic_title only for new_topic. Do not return paths, Markdown "
                    "links, citations, HTML comments, files, or complete pages.\n"
                    + json.dumps(request_payload, ensure_ascii=False, sort_keys=True)
                )
            )
            try:
                result = await model.invoke([message])
                output_text = _model_result_text(result)
            except Exception:
                break
            accepted = _parse_balanced_enrichments(output_text, allowed_targets=allowed_targets)
            for item_index in sorted(accepted):
                _apply_balanced_enrichment(
                    context_root,
                    service_id=effective_service_id,
                    document=documents_by_index[item_index],
                    enrichment=accepted[item_index],
                    target_paths=target_paths_by_index[item_index],
                )
                accepted_count += 1
        _render_root_navigation(
            context_root,
            fallback_references=tuple(alias_targets or ()),
        )
        return _changed_context_paths(context_root, context_baseline), accepted_count

    async def _publish_processed(
        self,
        *,
        service_id: str,
        run_id: str,
        batch: FetchBatch,
        processed: Mapping[str, object],
        sandbox: Path,
        alias_targets: Mapping[str, str] | None = None,
    ) -> None:
        del run_id, batch
        async with self._publish_lock:
            _assert_path_chain_no_symlinks(self._home / "workspace")
            candidate_context = sandbox / "context"
            candidate_prepared = bool(
                processed.get("_filesystem_candidate_prepared") or processed.get("_agent_candidate_prepared")
            )
            if not candidate_prepared:
                _copy_tree(self._context_root, candidate_context)
            candidate_context.mkdir(parents=True, exist_ok=True)
            _assert_no_symlinks(candidate_context)

            deleted_value = processed.get("deleted_ids", [])
            deleted_values = deleted_value if isinstance(deleted_value, list) else []
            deleted_ids = [str(value) for value in deleted_values]
            _remove_rules_pages_for_deleted_ids(
                candidate_context,
                service_id=service_id,
                deleted_ids=deleted_ids,
            )
            if not candidate_prepared:
                _apply_rules_increment(
                    candidate_context,
                    service_id=service_id,
                    processed=processed,
                    fallback_references=tuple(alias_targets or ()),
                )
            _validate_candidate(
                candidate_context,
                final_context_root=self._context_root,
                source_root=self._source_meta_root,
            )
            if alias_targets is not None:
                _resolve_short_references(
                    candidate_context,
                    final_context_root=self._context_root,
                    source_root=self._source_meta_root,
                    alias_targets=alias_targets,
                )
                _validate_reference_graph(
                    candidate_context,
                    final_context_root=self._context_root,
                    source_root=self._source_meta_root,
                    repairable=False,
                )
            obsolete_context_files = _copy_and_publish_tree(
                candidate_context,
                self._context_root,
                skip_relative="description.md",
            )
            description = candidate_context / "description.md"
            _atomic_write(self._context_root / "description.md", description.read_bytes())
            _remove_published_tree_entries(self._context_root, obsolete_context_files)
            _assert_no_symlinks(self._context_root)

    def _fail_active(self, error: BaseError) -> None:
        if self._active_completion is not None and not self._active_completion.done():
            self._active_completion.set_exception(error)

    def _fail_queued(self, error: BaseError) -> None:
        while True:
            try:
                item = self._input_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                completion = _queue_item_completion(item)
                if completion is not None and not completion.done():
                    completion.set_exception(error)
            finally:
                self._input_queue.task_done()


_NON_FALLBACK_AGENT_STATUSES = frozenset(
    {
        # DeepAgent setup, context and runtime failures are not model-output
        # failures; changing profile cannot repair the agent runtime itself.
        StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
        StatusCode.DEEPAGENT_INPUT_PARAM_ERROR,
        StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
        StatusCode.DEEPAGENT_RUNTIME_ERROR,
        StatusCode.DEEPAGENT_TASK_LOOP_NOT_IMPLEMENTED,
        StatusCode.DEEPAGENT_CREATE_SUBAGENT_NOT_FOUND,
        StatusCode.DEEPAGENT_LOAD_PLUGIN_ERROR,
        StatusCode.DEEPAGENT_LOAD_AGENT_TEMPLATE_ERROR,
        StatusCode.DEEPAGENT_UNLOAD_EXTENSION_ERROR,
        # Agent/controller validation and orchestration failures are likewise
        # not fixed by retrying with a lower content-generation profile.
        StatusCode.AGENT_TOOL_NOT_FOUND,
        StatusCode.AGENT_TASK_NOT_SUPPORT,
        StatusCode.AGENT_WORKFLOW_EXECUTION_ERROR,
        StatusCode.AGENT_PROMPT_PARAM_ERROR,
        StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
        StatusCode.AGENT_CONTROLLER_USER_INPUT_PROCESS_ERROR,
        StatusCode.AGENT_CONTROLLER_TASK_PARAM_ERROR,
        StatusCode.AGENT_CONTROLLER_INTENT_PARAM_ERROR,
        StatusCode.AGENT_CONTROLLER_TASK_EXECUTION_ERROR,
        StatusCode.AGENT_CONTROLLER_EVENT_HANDLER_ERROR,
        StatusCode.AGENT_CONTROLLER_EVENT_QUEUE_ERROR,
        # LLM/tool configuration or input errors are not transient model/tool
        # execution failures.  The invoke/execution statuses remain eligible
        # for the established same-session repair and profile fallback paths.
        StatusCode.COMPONENT_LLM_TEMPLATE_CONFIG_ERROR,
        StatusCode.COMPONENT_LLM_RESPONSE_CONFIG_INVALID,
        StatusCode.COMPONENT_LLM_CONFIG_ERROR,
        StatusCode.COMPONENT_LLM_INIT_FAILED,
        StatusCode.COMPONENT_LLM_TEMPLATE_PROCESS_ERROR,
        StatusCode.COMPONENT_LLM_CONFIG_INVALID,
        StatusCode.COMPONENT_TOOL_INPUT_PARAM_ERROR,
        StatusCode.COMPONENT_TOOL_INIT_FAILED,
        StatusCode.MODEL_INVOKE_PARAM_ERROR,
    }
)


def _profile_fallback_allowed(error: BaseException) -> bool:
    if isinstance(error, (OSError, UnicodeError)):
        return False
    status = getattr(error, "status", None)
    if status in {
        StatusCode.CONTEXT_PROACTIVE_CONFIG_INVALID,
        StatusCode.CONTEXT_PROACTIVE_STATE_INVALID,
        StatusCode.CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR,
        StatusCode.CONTEXT_PROACTIVE_FETCH_EXECUTION_ERROR,
        StatusCode.CONTEXT_PROACTIVE_PUBLISH_EXECUTION_ERROR,
        StatusCode.CONTEXT_PROACTIVE_RUNTIME_TIMEOUT,
        StatusCode.MODEL_PROVIDER_INVALID,
        StatusCode.MODEL_SERVICE_CONFIG_ERROR,
        StatusCode.MODEL_CONFIG_ERROR,
        StatusCode.MODEL_CLIENT_CONFIG_INVALID,
    }:
        return False
    if status in _NON_FALLBACK_AGENT_STATUSES:
        return False
    details = getattr(error, "details", None)
    return not (isinstance(details, Mapping) and details.get("fallback_allowed") is False)


def _reset_filesystem_sandbox(sandbox: Path) -> None:
    """Discard Filesystem outputs while preserving the run-owned input tree."""

    _assert_no_symlinks(sandbox)
    for entry in list(sandbox.iterdir()):
        if entry.name in {"inputs", "materialized-source"}:
            continue
        _make_tree_writable(entry)
        _remove_tree_entry(entry)


def _snapshot_managed_files(root: Path) -> dict[str, tuple[int, str]]:
    """Record managed files before an Agent attempt so silent edits are rejected."""

    if not root.exists():
        return {}
    _assert_no_symlinks(root)
    result: dict[str, tuple[int, str]] = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            data_hash = _hash_file(path)
            size = path.stat().st_size
        except OSError as exc:
            raise _publish_error("managed file could not be inspected") from exc
        result[path.relative_to(root).as_posix()] = (size, data_hash)
    return result


def _changed_context_paths(
    context_root: Path,
    baseline: Mapping[str, tuple[int, str]],
) -> set[str]:
    """Return newly created or materially changed Context Markdown paths."""

    current = _snapshot_managed_files(context_root)
    return {
        relative
        for relative, fingerprint in current.items()
        if relative.casefold().endswith(".md") and baseline.get(relative) != fingerprint
    }


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise _publish_error("managed file could not be read") from exc
    return digest.hexdigest()


def _validate_unchanged_tree(root: Path, baseline: Mapping[str, tuple[int, str]], *, name: str) -> None:
    """Reject Agent writes to a program-owned input tree."""

    current = _snapshot_managed_files(root)
    if current != dict(baseline):
        raise _publish_error(f"agent modified program-owned {name}")


def _make_tree_read_only(root: Path) -> None:
    """Remove write bits after PersonalContext has finished preparing an Agent input tree."""

    if not root.exists() or root.is_symlink():
        return
    for path in [*root.rglob("*"), root]:
        mode = path.stat().st_mode
        path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _prepare_agent_inputs(
    batch: FetchBatch,
    *,
    sandbox: Path,
    processed: Mapping[str, object] | None = None,
) -> dict[str, tuple[int, str]]:
    """Write the complete current batch to a read-only Agent input tree.

    The prompt only tells the Agent where to start.  Source bodies, optional
    raw snapshots, and Filesystem-stage Processing output remain available as
    files without being clipped to the model-message budget.
    """

    inputs_root = sandbox / "inputs"
    tmp_root = sandbox / "tmp"
    try:
        if inputs_root.exists() or inputs_root.is_symlink():
            _make_tree_writable(inputs_root)
            _remove_tree_entry(inputs_root)
        inputs_root.mkdir(parents=True)
        tmp_root.mkdir(parents=True, exist_ok=True)
        if inputs_root.is_symlink() or tmp_root.is_symlink():
            raise _publish_error("agent input or temporary path is invalid")

        record_rows: list[str] = []
        for index, item in enumerate(batch.items):
            entry_name = f"{index:04d}-{_digest(item.logical_id)[:12]}"
            entry_root = inputs_root / "records" / entry_name
            content = item.content or ""
            _atomic_write(entry_root / "content.md", content.encode("utf-8"))
            metadata = {
                "index": index,
                "logical_id": item.logical_id,
                "revision_id": item.revision_id,
                "operation": item.operation,
                "title": item.title,
                "original_ref": _agent_reference(item.original_ref),
                "metadata": _agent_metadata(item.metadata),
            }
            _atomic_write(
                entry_root / "metadata.json",
                (json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            )
            raw = item.raw_snapshot
            if isinstance(raw, str):
                _atomic_write(entry_root / "raw-snapshot.txt", raw.encode("utf-8"))
            elif isinstance(raw, bytes):
                _atomic_write(entry_root / "raw-snapshot.bin", raw)
            record_rows.extend(
                [
                    f"## {index:04d}. {item.title or item.logical_id}",
                    "",
                    f"- logical_id: `{item.logical_id}`",
                    f"- revision_id: `{item.revision_id}`",
                    f"- operation: `{item.operation}`",
                    f"- full content: `inputs/records/{entry_name}/content.md`",
                    f"- metadata: `inputs/records/{entry_name}/metadata.json`",
                    "",
                ]
            )

        if processed is not None:
            documents_value = processed.get("documents", [])
            documents = documents_value if isinstance(documents_value, list) else []
            blocks_value = processed.get("blocks", [])
            blocks = blocks_value if isinstance(blocks_value, list) else []
            for index, document in enumerate(documents):
                if not isinstance(document, Mapping) or not isinstance(document.get("logical_id"), str):
                    continue
                logical_id = str(document["logical_id"])
                entry_name = f"{index:04d}-{_digest(logical_id)[:12]}"
                processed_root = inputs_root / "processed" / entry_name
                _atomic_write(
                    processed_root / "context-document.md",
                    str(document.get("markdown", "")).encode("utf-8"),
                )
                document_blocks = [
                    block for block in blocks if isinstance(block, Mapping) and block.get("logical_id") == logical_id
                ]
                block_text = "\n".join(
                    json.dumps(dict(block), ensure_ascii=False, sort_keys=True) for block in document_blocks
                )
                _atomic_write(
                    processed_root / "blocks.jsonl",
                    ((block_text + "\n") if block_text else "").encode("utf-8"),
                )

        briefing = [
            "# PersonalContext Agent Input Briefing",
            "",
            "The complete current batch is stored below inputs/records/.",
            "Read the listed files with sandbox tools when the prompt preview is insufficient.",
            "inputs/ is PersonalContext-owned and read-only; use tmp/ only for scratch work.",
            "",
            *record_rows,
        ]
        if processed is not None:
            briefing.extend(
                [
                    "## Processing output",
                    "",
                    "Complete processed documents and blocks are under inputs/processed/.",
                    "",
                ]
            )
        _atomic_write(inputs_root / "briefing.md", ("\n".join(briefing).rstrip() + "\n").encode("utf-8"))
        _make_tree_read_only(inputs_root)
        return _snapshot_managed_files(inputs_root)
    except (OSError, ValueError, TypeError) as exc:
        raise _publish_error("agent inputs could not be safely prepared") from exc


def _processed_deleted_ids(processed: Mapping[str, object]) -> set[str]:
    value = processed.get("deleted_ids", [])
    return {item for item in value if isinstance(item, str)} if isinstance(value, list) else set()


def _processed_changed_ids(processed: Mapping[str, object]) -> set[str]:
    changed = _processed_deleted_ids(processed)
    documents_value = processed.get("documents", [])
    if isinstance(documents_value, list):
        changed.update(
            str(document["logical_id"])
            for document in documents_value
            if isinstance(document, Mapping) and isinstance(document.get("logical_id"), str)
        )
    return changed


def _validate_agent_sandbox_layout(
    sandbox: Path,
    *,
    materialized_baseline: Mapping[str, tuple[int, str]] | None,
    inputs_baseline: Mapping[str, tuple[int, str]] | None = None,
) -> None:
    """Reject writes outside the Agent's narrowly scoped sandbox contract."""

    if not sandbox.is_dir() or sandbox.is_symlink():
        raise _publish_error("agent sandbox path is invalid")
    _assert_no_symlinks(sandbox)
    # ``.agent_history`` is created by agent-core's file/shell tools to keep
    # an operation audit trail.  It is framework-owned, never published, and
    # must not be confused with an Agent-authored output directory.
    allowed = {
        ".agent_history",
        "context",
        "inputs",
        "materialized-source",
        "tmp",
    }
    for entry in sandbox.iterdir():
        if entry.name not in allowed:
            raise _publish_error("agent wrote outside the allowed sandbox areas")
    materialized_root = sandbox / "materialized-source"
    try:
        materialized_stat = materialized_root.lstat()
    except FileNotFoundError:
        materialized_stat = None
    except OSError as exc:
        raise _publish_error("agent materialized source could not be inspected") from exc
    if materialized_baseline is None:
        if materialized_stat is not None:
            raise _publish_error("agent created an undeclared materialized source")
    else:
        if materialized_stat is None:
            raise _publish_error("agent removed the materialized source")
        if not stat.S_ISDIR(materialized_stat.st_mode) or stat.S_ISLNK(materialized_stat.st_mode):
            raise _publish_error("agent materialized source path is invalid")
        _validate_unchanged_tree(materialized_root, materialized_baseline, name="materialized-source")

    inputs_root = sandbox / "inputs"
    try:
        inputs_stat = inputs_root.lstat()
    except FileNotFoundError:
        inputs_stat = None
    except OSError as exc:
        raise _publish_error("agent inputs could not be inspected") from exc
    if inputs_baseline is None:
        if inputs_stat is not None:
            raise _publish_error("agent created undeclared inputs")
    else:
        if inputs_stat is None:
            raise _publish_error("agent removed the supplied inputs")
        if not stat.S_ISDIR(inputs_stat.st_mode) or stat.S_ISLNK(inputs_stat.st_mode):
            raise _publish_error("agent input path is invalid")
        _validate_unchanged_tree(inputs_root, inputs_baseline, name="inputs")

    tmp_root = sandbox / "tmp"
    try:
        tmp_stat = tmp_root.lstat()
    except FileNotFoundError:
        tmp_stat = None
    except OSError as exc:
        raise _publish_error("agent temporary path could not be inspected") from exc
    if tmp_stat is not None and (not stat.S_ISDIR(tmp_stat.st_mode) or stat.S_ISLNK(tmp_stat.st_mode)):
        raise _publish_error("agent temporary path is invalid")


def _top_level_heading_count(markdown: str) -> int:
    """Count non-empty H1 headings outside fenced code blocks."""

    count = 0
    fence_character: str | None = None
    fence_length = 0
    for line in markdown.splitlines():
        if fence_character is not None:
            closing_fence = re.match(r"^ {0,3}(`{3,}|~{3,})[ \t]*$", line)
            if (
                closing_fence is not None
                and closing_fence.group(1)[0] == fence_character
                and len(closing_fence.group(1)) >= fence_length
            ):
                fence_character = None
                fence_length = 0
            continue
        opening_fence = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if opening_fence is not None:
            marker = opening_fence.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
            continue
        if fence_character is None and re.match(r"^ {0,3}#[ \t]+\S", line):
            count += 1
    return count


def _validate_agent_pages(context_root: Path, relative_paths: Sequence[str]) -> None:
    """Reject invalid Agent-authored pages."""

    for relative in relative_paths:
        path = context_root / _validated_relative_path(relative, name="agent page path")
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise _publish_error("agent page is unreadable") from exc
        except UnicodeError as exc:
            raise _pipeline_error("agent page is not valid UTF-8") from exc
        if not content.strip() or len(content) > 2_000_000:
            raise _pipeline_error("agent page is empty or exceeds the safety limit")
        if content.lstrip().startswith("---"):
            raise _publish_error("agent page must not contain frontmatter")


def _validate_filesystem_agent_result(
    text: str,
    sandbox: Path,
    processed: Mapping[str, object],
    *,
    context_baseline: Mapping[str, tuple[int, str]],
    materialized_baseline: Mapping[str, tuple[int, str]] | None,
    inputs_baseline: Mapping[str, tuple[int, str]],
    baseline_root: Path | None,
    final_context_root: Path,
    source_root: Path,
    alias_targets: Mapping[str, str] | None,
    deleted_source_ids: set[str] | None,
) -> list[str]:
    """Validate one real Filesystem DeepAgent turn.

    The model's textual response is deliberately ignored beyond the
    non-empty check performed by ``run_personal_context_agent``.  All useful output lives
    in the candidate Context and the root manifest.
    """

    del text
    try:
        _validate_agent_sandbox_layout(
            sandbox,
            materialized_baseline=materialized_baseline,
            inputs_baseline=inputs_baseline,
        )
        changed_paths = _changed_context_paths(sandbox / "context", context_baseline)
        _validate_agent_candidate(
            sandbox / "context",
            baseline=context_baseline,
            changed_paths=changed_paths,
            baseline_root=baseline_root,
            final_context_root=final_context_root,
            source_root=source_root,
            deleted_source_ids=deleted_source_ids,
            materialized_baseline=materialized_baseline,
            require_single_h1=True,
        )
        if (
            _processed_documents(processed)
            and baseline_root is not None
            and not _agent_updated_context_knowledge_page(
                sandbox / "context",
                baseline_root=baseline_root,
                baseline=context_baseline,
            )
        ):
            raise _pipeline_error("agent did not add or update any Context knowledge page")
        _validate_agent_pages(sandbox / "context", sorted(changed_paths))
        if alias_targets is not None:
            _validate_reference_graph(
                sandbox / "context",
                final_context_root=final_context_root,
                source_root=source_root,
                alias_targets=alias_targets,
                repairable=True,
            )
    except BaseError as error:
        # Publish/path/sandbox failures are security or integrity failures;
        # they must not be sent back as model repair instructions.
        if getattr(error, "status", None) != StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR:
            raise
        return _bounded_validation_errors(error)
    except UnicodeError as error:
        del error
        return _bounded_validation_errors(_pipeline_error("agent candidate contains invalid UTF-8"))
    except OSError as error:
        raise _publish_error("agent candidate could not be inspected") from error
    except Exception as error:
        return _bounded_validation_errors(error)
    return []


def _validate_agent_candidate(
    context_root: Path,
    *,
    baseline: Mapping[str, tuple[int, str]],
    changed_paths: set[str],
    baseline_root: Path | None = None,
    final_context_root: Path | None = None,
    source_root: Path | None = None,
    deleted_source_ids: set[str] | None = None,
    materialized_baseline: Mapping[str, tuple[int, str]] | None = None,
    require_description: bool = True,
    require_single_h1: bool = False,
) -> None:
    """Bound Agent-created Context files and reject unsafe files or deletions."""

    if not context_root.exists():
        raise _publish_error("agent context candidate is missing")
    _assert_no_symlinks(context_root)
    files = [path for path in context_root.rglob("*") if path.is_file()]
    if len(files) > _MAX_AGENT_CONTEXT_FILES:
        raise _pipeline_error("agent context file count exceeds the safety limit")
    baseline_paths = set(baseline)
    candidate_paths: set[str] = set()
    root_description = context_root / "description.md"
    if require_description:
        try:
            root_description_stat = root_description.lstat()
        except FileNotFoundError as exc:
            raise _pipeline_error("agent root description.md is missing or empty") from exc
        except OSError as exc:
            raise _publish_error("agent root description.md is unreadable") from exc
        if stat.S_ISLNK(root_description_stat.st_mode):
            raise _publish_error("agent root description.md path is invalid")
        if not stat.S_ISREG(root_description_stat.st_mode):
            raise _pipeline_error("agent root description.md is missing or empty")
        try:
            root_description_text = root_description.read_text(encoding="utf-8")
        except OSError as exc:
            raise _publish_error("agent root description.md is unreadable") from exc
        except UnicodeError as exc:
            raise _pipeline_error("agent root description.md is not valid UTF-8") from exc
        if not root_description_text.strip():
            raise _pipeline_error("agent root description.md is missing or empty")
    for path in files:
        relative = path.relative_to(context_root).as_posix()
        candidate_paths.add(relative)
        if len(relative) > _MAX_AGENT_CONTEXT_PATH_CHARS:
            raise _pipeline_error("agent context path exceeds the safety limit")
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise _publish_error("agent context file could not be inspected") from exc
        if size > _MAX_AGENT_CONTEXT_FILE_BYTES:
            raise _pipeline_error("agent context file exceeds the safety limit")
        if _is_program_description(relative):
            try:
                description_text = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise _publish_error("agent description is unreadable") from exc
            except UnicodeError as exc:
                raise _pipeline_error("agent description is not valid UTF-8") from exc
            if size == 0 or not description_text.strip():
                raise _pipeline_error("agent description is empty")
            if description_text.lstrip().startswith("---"):
                raise _pipeline_error("agent description contains frontmatter")
            continue
        if path.suffix.lower() != ".md":
            raise _pipeline_error("agent context contains a non-Markdown page")
        if relative not in baseline_paths or (size, _hash_file(path)) != baseline[relative]:
            _validate_agent_pages(context_root, [relative])
            if require_single_h1:
                try:
                    page_content = path.read_text(encoding="utf-8")
                except OSError as exc:
                    raise _publish_error("agent context page is unreadable") from exc
                except UnicodeError as exc:
                    raise _pipeline_error("agent context page is not valid UTF-8") from exc
                if _top_level_heading_count(page_content) != 1:
                    raise _pipeline_error(
                        "agent context page must contain exactly one top-level heading outside fenced code blocks"
                    )
    deleted_source_ids = deleted_source_ids or set()
    allowed_missing_pages: set[str] = set()
    for relative in baseline_paths - candidate_paths:
        if _is_program_description(relative):
            if require_description:
                raise _pipeline_error("agent removed a description.md")
            continue
        if baseline_root is None or source_root is None:
            raise _pipeline_error("agent removed an undeclared context file")
        source_ids = _source_ids_reachable_from_page(
            baseline_root,
            source_root=source_root,
            page_relative=relative,
        )
        if not source_ids or not source_ids.issubset(deleted_source_ids):
            raise _pipeline_error("agent removed a page not exclusively linked to deleted sources")
        allowed_missing_pages.add(relative)

    for relative in changed_paths:
        page = context_root / relative
        if not page.is_file() or page.is_symlink():
            raise _pipeline_error("agent changed page is missing")

    if materialized_baseline is not None:
        # This check is intentionally kept here for direct balanced callers;
        # the DeepAgent validator performs the same check before returning.
        materialized_root = context_root.parent / "materialized-source"
        try:
            materialized_stat = materialized_root.lstat()
        except FileNotFoundError as exc:
            raise _publish_error("agent removed the materialized source") from exc
        except OSError as exc:
            raise _publish_error("agent materialized source could not be inspected") from exc
        if not stat.S_ISDIR(materialized_stat.st_mode) or stat.S_ISLNK(materialized_stat.st_mode):
            raise _publish_error("agent materialized source path is invalid")
        _validate_unchanged_tree(materialized_root, materialized_baseline, name="materialized-source")
    _validate_description_navigation(
        context_root,
        final_context_root=final_context_root or baseline_root,
        source_root=source_root,
        repairable=True,
        allowed_missing=allowed_missing_pages,
    )


def _agent_updated_context_knowledge_page(
    context_root: Path,
    *,
    baseline_root: Path,
    baseline: Mapping[str, tuple[int, str]],
) -> bool:
    """Return whether Agent added or materially edited an ordinary Context page."""

    for candidate_page in context_root.rglob("*.md"):
        relative = candidate_page.relative_to(context_root).as_posix()
        if _is_program_description(relative):
            continue
        if relative not in baseline:
            return True
        baseline_page = baseline_root / relative
        candidate_body = _normalize_markdown(candidate_page.read_text(encoding="utf-8"))
        baseline_body = _normalize_markdown(baseline_page.read_text(encoding="utf-8"))
        if candidate_body != baseline_body:
            return True
    return False


def _prepare_agent_candidate(
    context_root: Path,
    sandbox: Path,
) -> None:
    """Give Filesystem Agent a clean Context candidate."""

    candidate_context = sandbox / "context"
    _copy_tree(context_root, candidate_context)
    candidate_context.mkdir(parents=True, exist_ok=True)


def _materialize_candidate_source(
    source_value: str | None,
    *,
    sandbox: Path,
    home: Path,
) -> str | None:
    """Copy a provider candidate into a read-only subtree inside the sandbox."""

    if source_value is None:
        return None
    try:
        raw_source = Path(source_value).expanduser()
        _assert_path_chain_no_symlinks(raw_source)
        source = raw_source.resolve()
        allowed = False
        for root in (home / "materialized-sources", home / "workspace" / "materialized-sources"):
            try:
                source.relative_to(root.resolve())
            except ValueError:
                continue
            allowed = True
            break
        if not allowed or not source.is_dir():
            raise _publish_error("materialized source path is outside the managed root")
        _assert_no_symlinks(source)
        target = sandbox / "materialized-source"
        _copy_tree(source, target)
        for path in [target, *target.rglob("*")]:
            mode = path.stat().st_mode
            path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        return "materialized-source"
    except (OSError, ValueError) as exc:
        raise _publish_error("materialized source could not be safely copied") from exc


def _processed_documents(processed: Mapping[str, object]) -> list[Mapping[str, object]]:
    documents_value = processed.get("documents", [])
    if not isinstance(documents_value, list):
        return []
    return [document for document in documents_value if isinstance(document, Mapping)]


def _is_large_run(processed: Mapping[str, object]) -> bool:
    """Classify a run using the exact strict preview-budget thresholds."""

    documents = _processed_documents(processed)
    lengths = [len(str(document.get("markdown", ""))) for document in documents]
    return (
        len(documents) > _LARGE_RUN_DOCUMENT_COUNT
        or sum(lengths) > _LARGE_RUN_TOTAL_DOCUMENT_CHARS
        or max(lengths, default=0) > _LARGE_RUN_MAX_DOCUMENT_CHARS
    )


def _agent_documents_payload(
    processed: Mapping[str, object],
    *,
    large_run: bool | None = None,
) -> list[dict[str, object]]:
    """Build the bounded document preview shared by Filesystem model paths."""

    documents = _processed_documents(processed)
    is_large = _is_large_run(processed) if large_run is None else large_run
    summary_limit = _LARGE_PROMPT_SUMMARY_CHARS if is_large else _SMALL_PROMPT_SUMMARY_CHARS
    result: list[dict[str, object]] = []
    for document in documents[:_INITIAL_PROMPT_DOCUMENT_LIMIT]:
        result.append(
            {
                "logical_id": document.get("logical_id"),
                "revision_id": document.get("revision_id"),
                "title": str(document.get("title") or "")[:512],
                "summary": str(document.get("markdown", ""))[:summary_limit],
            }
        )
    return result


def _agent_reference(value: object) -> object:
    if not isinstance(value, str):
        return value
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        try:
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            return f"{parsed.scheme}://[redacted]"
        if not host:
            return f"{parsed.scheme}://[redacted]"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        safe_netloc = host if port is None else f"{host}:{port}"
        return urlunsplit((parsed.scheme, safe_netloc, parsed.path, "", ""))
    # Local source paths are not persisted in source metadata or exposed to
    # the Agent as executable paths.
    if parsed.scheme == "file" or Path(value).is_absolute() or re.match(r"^[A-Za-z]:[\\/].*", value):
        return "<local source path withheld; use the supplied content>"
    return value


def _agent_metadata(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _agent_metadata(item) for key, item in value.items() if not _is_sensitive_metadata_key(key)}
    if isinstance(value, list):
        return [_agent_metadata(item) for item in value]
    if isinstance(value, tuple):
        return [_agent_metadata(item) for item in value]
    if isinstance(value, str):
        return _agent_reference(value)
    return value


def _is_sensitive_metadata_key(value: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(value).casefold())
    return normalized in _SENSITIVE_METADATA_KEYS or normalized.endswith(_SENSITIVE_METADATA_SUFFIXES)


def _model_result_text(result: object) -> str:
    """Extract a bounded text response from the direct balanced Model call."""

    if isinstance(result, str):
        text = result
    elif isinstance(result, AssistantMessage):
        text = result.content if isinstance(result.content, str) else ""
    elif isinstance(result, Mapping):
        value = result.get("output", result.get("content", result.get("result")))
        text = value if isinstance(value, str) else ""
    else:
        value = getattr(result, "output", getattr(result, "content", ""))
        text = value if isinstance(value, str) else ""
    text = text.strip()
    if not text:
        raise _pipeline_error("balanced model returned empty output")
    if len(text) > _MAX_MODEL_OUTPUT_CHARS:
        raise _pipeline_error("balanced model output exceeds the configured size limit")
    return text


def _bounded_validation_errors(error: BaseException | object) -> list[str]:
    """Return short, redacted output-validation details for a repair prompt."""

    text = getattr(error, "message", None)
    if not isinstance(text, str) or not text.strip():
        text = str(error)
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    text = " ".join(text.split())
    if "traceback" in text.casefold() or "stack trace" in text.casefold():
        text = "validator reported an internal validation failure"
    text = _VALIDATION_URL_USERINFO.sub(r"\1[REDACTED]@", text)
    text = _VALIDATION_URL_QUERY.sub(r"\1", text)
    text = _VALIDATION_SECRET.sub(r"\1[REDACTED]", text)
    for pattern in _VALIDATION_PATHS:
        text = pattern.sub("[PATH_REDACTED]", text)
    text = text[:_MAX_VALIDATION_ERROR_CHARS]
    return [text or "output failed validation"]


def _load_agent_json(text: str, *, error_message: str) -> object:
    """Decode a model JSON object while tolerating harmless presentation wrappers."""

    if not isinstance(text, str):
        raise _pipeline_error(error_message)
    candidates = [text.strip()]
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidates[0], flags=re.DOTALL | re.IGNORECASE)
    if fenced is not None:
        candidates.append(fenced.group(1).strip())
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, str):
            with contextlib.suppress(json.JSONDecodeError):
                value = json.loads(value)
        if isinstance(value, dict):
            return value
        start = candidate.find("{")
        if start >= 0:
            with contextlib.suppress(json.JSONDecodeError):
                value, _ = decoder.raw_decode(candidate[start:])
            if isinstance(value, dict):
                return value
    raise _pipeline_error(error_message)


def _validate_candidate(
    context_root: Path,
    *,
    final_context_root: Path | None = None,
    source_root: Path | None = None,
) -> None:
    _assert_no_symlinks(context_root)
    description = context_root / "description.md"
    try:
        description_text = description.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise _publish_error("candidate description.md is unreadable") from exc
    if not description_text.strip():
        raise _publish_error("candidate description.md is empty")
    for entry in context_root.rglob("*"):
        if entry.is_file() and entry.suffix.casefold() != ".md":
            raise _publish_error("candidate Context contains a non-Markdown file")
    _validate_description_navigation(
        context_root,
        final_context_root=final_context_root,
        source_root=source_root,
    )


def _validate_description_navigation(
    context_root: Path,
    *,
    final_context_root: Path | None = None,
    source_root: Path | None = None,
    repairable: bool = False,
    allowed_missing: set[str] | None = None,
) -> None:
    """Require Context navigation and verified source links to resolve safely."""

    resolved_final_context = (final_context_root or context_root).resolve()
    resolved_source_root = source_root.resolve() if source_root is not None else None
    error = _pipeline_error if repairable else _publish_error
    for page in context_root.rglob("description.md"):
        try:
            text = page.read_text(encoding="utf-8")
        except OSError as exc:
            raise error("candidate description navigation could not be read") from exc
        except UnicodeError as exc:
            raise error("candidate description navigation is not valid UTF-8") from exc
        for raw_target in _MARKDOWN_LINK.findall(text):
            target = raw_target.strip()
            if not target or target.startswith("#") or target.startswith(("http://", "https://", "mailto:")):
                continue
            angled = target.startswith("<") and target.endswith(">")
            if angled:
                target = target[1:-1].strip()
            target = target.split("#", 1)[0].split("?", 1)[0].strip()
            if not target:
                continue
            if not angled:
                titled = re.fullmatch(r"(\S+)\s+(?:\"[^\"]*\"|'[^']*')", target)
                if titled is not None:
                    target = titled.group(1)
            if target.startswith(("/", "\\")) or re.fullmatch(r"[A-Za-z]:.*", target) is not None:
                raise error("candidate description navigation leaves Context")
            page_relative = page.relative_to(context_root)
            logical_page = resolved_final_context / page_relative
            target_path = (logical_page.parent / target).resolve()
            try:
                relative_target = target_path.relative_to(resolved_final_context).as_posix()
            except ValueError:
                if (
                    source_root is None
                    or resolved_source_root is None
                    or not target_path.is_relative_to(resolved_source_root)
                ):
                    raise error("candidate description navigation leaves Context") from None
                source_relative = target_path.relative_to(resolved_source_root)
                if len(source_relative.parts) != 1 or source_relative.suffix.casefold() != ".md":
                    raise error("candidate atomic source reference is invalid") from None
                _reference_source_path(source_root, source_relative.stem, error=error)
                continue
            candidate_target = context_root / relative_target
            if not candidate_target.exists() and allowed_missing is not None and relative_target in allowed_missing:
                continue
            if not candidate_target.exists() or candidate_target.is_symlink():
                raise error("candidate description navigation target is missing")
