from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openjiuwen.core.context_engine.processor.offloader.rules.common import (
    ERROR_RE,
    meets_savings_ratio,
)
from openjiuwen.core.context_engine.processor.offloader.rules.types import (
    ContentType,
    RuleCompressionResult,
    RuleContext,
)


@dataclass(frozen=True)
class DiffHunk:
    header: str
    lines: tuple[str, ...]
    position: int

    @property
    def change_count(self) -> int:
        return sum(1 for line in self.lines if _is_change_line(line))


@dataclass(frozen=True)
class DiffFile:
    header: tuple[str, ...]
    hunks: tuple[DiffHunk, ...]
    position: int

    @property
    def label(self) -> str:
        return self.header[0] if self.header else f"diff-file-{self.position}"

    @property
    def change_count(self) -> int:
        return sum(hunk.change_count for hunk in self.hunks)


class DiffCompressor:
    def compress(self, content: str, ctx: RuleContext) -> RuleCompressionResult:
        if len(content.splitlines()) < ctx.diff_min_lines:
            return _unchanged(content)
        preamble, files = _parse_diff(content)
        if not files:
            return _unchanged(content)

        selected_files = self._select_files(files, ctx)
        output = list(preamble)
        hunks_retained = 0
        hunks_omitted = 0
        context_lines_retained = 0
        context_lines_omitted = 0

        for file in sorted(selected_files, key=lambda item: item.position):
            output.extend(file.header)
            selected_hunks = self._select_hunks(file.hunks, ctx)
            selected_positions = {hunk.position for hunk in selected_hunks}
            omitted_for_file = len(file.hunks) - len(selected_hunks)
            for hunk in file.hunks:
                if hunk.position not in selected_positions:
                    continue
                reduced, retained, omitted = _reduce_context(
                    hunk.lines,
                    ctx.diff_max_context_lines,
                )
                output.append(hunk.header)
                output.extend(reduced)
                if omitted:
                    output.append(
                        f"[{omitted} unchanged/context diff lines omitted]"
                    )
                hunks_retained += 1
                context_lines_retained += retained
                context_lines_omitted += omitted
            if omitted_for_file:
                output.append(f"[{omitted_for_file} diff hunks omitted from {file.label}]")
                hunks_omitted += omitted_for_file

        files_omitted = len(files) - len(selected_files)
        if files_omitted:
            output.append(f"[{files_omitted} diff files omitted]")

        candidate = "\n".join(output)
        details: dict[str, Any] = {
            "files_affected": len(files),
            "files_retained": len(selected_files),
            "files_omitted": files_omitted,
            "hunks_affected": sum(len(file.hunks) for file in files),
            "hunks_retained": hunks_retained,
            "hunks_omitted": hunks_omitted,
            "context_lines_retained": context_lines_retained,
            "context_lines_omitted": context_lines_omitted,
        }
        lossy = files_omitted > 0 or hunks_omitted > 0 or context_lines_omitted > 0
        if candidate != content and lossy and meets_savings_ratio(content, candidate, ctx):
            return RuleCompressionResult(
                content=candidate,
                content_type=ContentType.GIT_DIFF,
                modified=True,
                lossy=True,
                details=details,
            )
        return RuleCompressionResult(
            content=content,
            content_type=ContentType.GIT_DIFF,
            modified=False,
            lossy=False,
            details=details,
        )

    @staticmethod
    def _select_files(files: list[DiffFile], ctx: RuleContext) -> list[DiffFile]:
        if len(files) <= ctx.diff_max_files:
            return files
        return sorted(
            files,
            key=lambda file: (
                -_score_text(_file_text(file), file.change_count, ctx.query_terms),
                file.position,
            ),
        )[: ctx.diff_max_files]

    @staticmethod
    def _select_hunks(hunks: tuple[DiffHunk, ...], ctx: RuleContext) -> list[DiffHunk]:
        limit = ctx.diff_max_hunks_per_file
        if len(hunks) <= limit:
            return list(hunks)

        selected: dict[int, DiffHunk] = {}
        for hunk in (hunks[0], hunks[-1]):
            if len(selected) < limit:
                selected[hunk.position] = hunk
        for hunk in sorted(
            hunks,
            key=lambda item: (
                -_score_text(
                    "\n".join((item.header, *item.lines)),
                    item.change_count,
                    ctx.query_terms,
                ),
                item.position,
            ),
        ):
            if len(selected) >= limit:
                break
            selected.setdefault(hunk.position, hunk)
        return sorted(selected.values(), key=lambda item: item.position)


def _parse_diff(content: str) -> tuple[tuple[str, ...], list[DiffFile]]:
    preamble: list[str] = []
    files: list[DiffFile] = []
    file_header: list[str] | None = None
    hunks: list[DiffHunk] = []
    hunk_header: str | None = None
    hunk_lines: list[str] = []

    def finish_hunk() -> None:
        nonlocal hunk_header, hunk_lines
        if hunk_header is not None:
            hunks.append(DiffHunk(hunk_header, tuple(hunk_lines), len(hunks)))
        hunk_header = None
        hunk_lines = []

    def finish_file() -> None:
        nonlocal file_header, hunks
        if file_header is not None:
            finish_hunk()
            files.append(DiffFile(tuple(file_header), tuple(hunks), len(files)))
        file_header = None
        hunks = []

    for line in content.splitlines():
        if _is_file_header(line):
            finish_file()
            file_header = [line]
        elif file_header is None:
            preamble.append(line)
        elif _is_hunk_header(line):
            finish_hunk()
            hunk_header = line
        elif hunk_header is None:
            file_header.append(line)
        else:
            hunk_lines.append(line)
    finish_file()
    return tuple(preamble), files


def _reduce_context(lines: tuple[str, ...], max_context: int) -> tuple[list[str], int, int]:
    change_positions = [index for index, line in enumerate(lines) if _is_change_line(line)]
    kept: list[str] = []
    context_retained = 0
    context_omitted = 0
    for index, line in enumerate(lines):
        if _is_context_line(line):
            if any(abs(index - change) <= max_context for change in change_positions):
                kept.append(line)
                context_retained += 1
            else:
                context_omitted += 1
        else:
            kept.append(line)
    return kept, context_retained, context_omitted


def _score_text(text: str, change_count: int, query_terms: frozenset[str]) -> int:
    lowered = text.lower()
    score = change_count * 10
    score += 100 if ERROR_RE.search(text) else 0
    score += 30 * sum(1 for term in query_terms if term in lowered)
    return score


def _file_text(file: DiffFile) -> str:
    lines = list(file.header)
    for hunk in file.hunks:
        lines.extend((hunk.header, *hunk.lines))
    return "\n".join(lines)


def _is_file_header(line: str) -> bool:
    return line.startswith(("diff --git ", "diff --cc ", "diff --combined "))


def _is_hunk_header(line: str) -> bool:
    return line.startswith(("@@ ", "@@@ "))


def _is_change_line(line: str) -> bool:
    return line.startswith(("+", "-")) and not line.startswith(("+++ ", "--- "))


def _is_context_line(line: str) -> bool:
    return line.startswith(" ") and not _is_change_line(line)


def _unchanged(content: str) -> RuleCompressionResult:
    return RuleCompressionResult(
        content=content,
        content_type=ContentType.GIT_DIFF,
        modified=False,
    )
