"""Merge one arranged Context snapshot with concurrent published changes."""

from __future__ import annotations

import os
import re
import hashlib
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator, Mapping

from openjiuwen.harness.personal_context.context_usage import _fingerprint
from openjiuwen.harness.personal_context.source_markdown import markdown_reference_text, rewrite_markdown_prose


def _existing_blocks(markdown: str) -> frozenset[str]:
    from openjiuwen.harness.personal_context.context_pipeline import CONTEXT_MERGE_BLOCKS, _managed_block_bounds

    return frozenset(
        start for start, end in CONTEXT_MERGE_BLOCKS
        if _managed_block_bounds(markdown, start=start, end=end) is not None
    )


def _knowledge_text(markdown: str, *, existing_blocks: frozenset[str] | None = None) -> str:
    """Ignore generated navigation and link destinations, but retain knowledge."""
    from openjiuwen.harness.personal_context.context_pipeline import (
        CONTEXT_MERGE_BLOCKS,
        _MANAGED_SOURCE_MARKER,
        _MARKDOWN_INLINE_LINK,
        _managed_block_bounds,
    )

    for start, end in CONTEXT_MERGE_BLOCKS:
        bounds = _managed_block_bounds(markdown, start=start, end=end)
        if bounds is not None:
            begin, finish = bounds
            before, after = markdown[:begin], markdown[finish:]
            # A newly inserted block adds one separator newline on each side.
            # Existing-block removal retains them, as _replace_managed_block
            # does. No other body whitespace or Unicode characters are folded.
            if existing_blocks is not None and start not in existing_blocks:
                if before.endswith("\r\n\r\n"):
                    before = before[:-2]
                elif before.endswith("\n\n"):
                    before = before[:-1]
                if after.startswith("\r\n"):
                    after = after[2:]
                elif after.startswith("\n"):
                    after = after[1:]
            markdown = before + after
    markdown = _MANAGED_SOURCE_MARKER.sub("", markdown)
    markdown = rewrite_markdown_prose(markdown, lambda text: _MARKDOWN_INLINE_LINK.sub(r"\1", text))
    return markdown


def _read_page(path: Path) -> str:
    from openjiuwen.harness.personal_context.context_pipeline import _extended_path

    return _extended_path(path).read_text(encoding="utf-8")


def _page_files(root: Path) -> Iterator[tuple[str, Path]]:
    from openjiuwen.harness.personal_context.context_pipeline import (
        _assert_no_symlinks,
        _path_is_file,
        _walk_tree_paths,
    )

    _assert_no_symlinks(root)
    for path in _walk_tree_paths(root):
        if path.suffix.casefold() == ".md" and _path_is_file(path):
            yield path.relative_to(root).as_posix(), path


def _page_signatures(root: Path) -> dict[str, str]:
    from openjiuwen.harness.personal_context.context_pipeline import _hash_file

    return {relative: _hash_file(path) for relative, path in _page_files(root)}


def _ordinary_pages(root: Path, paths: Iterable[str]) -> dict[str, Path]:
    from openjiuwen.harness.personal_context.context_pipeline import _is_program_description

    return {path: root / path for path in paths if not _is_program_description(path)}


def _match_pages(
    baseline: Mapping[str, Path], pages: Mapping[str, Path], relocations: Mapping[str, str],
    *, removed_live_paths: set[str] | None = None, ambiguous_sandbox_origins: set[str] | None = None,
) -> dict[str, str]:
    from openjiuwen.harness.personal_context.context_pipeline import _MANAGED_SOURCE_MARKER

    matches: dict[str, str] = {}
    available = set(pages)
    # Explicit moves and page identity precede path reuse by new knowledge.
    for old in baseline:
        target = relocations.get(old)
        if target is not None and target != old and target in available:
            matches[old] = target
            available.remove(target)

    def digest(markdown: str) -> bytes:
        return hashlib.sha256(markdown.encode("utf-8")).digest()

    def content_identity(path: Path, relative: str, markdown: str) -> str:
        # The usage fingerprint preserves link targets across directory moves.
        root = path.parents[len(PurePosixPath(relative).parts) - 1]
        try:
            return _fingerprint(root, relative, {}, markdown=markdown)
        except ValueError:
            return "raw:" + digest(markdown).hex()

    old_markers, new_markers = {}, {}
    old_texts, new_texts = {}, {}
    old_bodies, new_bodies = {}, {}
    profiles = set()
    for old, path in baseline.items():
        if old in matches:
            continue
        markdown = _read_page(path)
        marker = tuple(_MANAGED_SOURCE_MARKER.findall(markdown))
        if marker:
            old_markers.setdefault(marker, set()).add(old)
        else:
            profile = _existing_blocks(markdown)
            profiles.add(profile)
            key = profile, digest(_knowledge_text(markdown))
            old_bodies.setdefault(key, set()).add(old)
        old_texts.setdefault(content_identity(path, old, markdown), set()).add(old)
    for relative in available:
        markdown = _read_page(pages[relative])
        marker = tuple(_MANAGED_SOURCE_MARKER.findall(markdown))
        if marker:
            new_markers.setdefault(marker, set()).add(relative)
        new_texts.setdefault(content_identity(pages[relative], relative, markdown), set()).add(relative)
        for profile in profiles:
            key = profile, digest(_knowledge_text(markdown, existing_blocks=profile))
            new_bodies.setdefault(key, set()).add(relative)

    # Stronger identities claim both endpoints before weaker text comparisons.
    for originals, targets in ((old_markers, new_markers), (old_texts, new_texts), (old_bodies, new_bodies)):
        for key, old_paths in originals.items():
            target_paths = targets.get(key, set())
            stationary = old_paths & target_paths
            if originals is old_texts and ambiguous_sandbox_origins is not None and len(old_paths) > 1:
                # With indistinguishable originals, an edit at one path looks exactly
                # like a swap followed by that edit. A concurrent retirement cannot
                # safely discard a sandbox page on the strength of its path alone.
                unresolved = {old for old in old_paths if old not in matches}
                if len(unresolved) > 1 and not unresolved.issubset(target_paths & available):
                    ambiguous_sandbox_origins.update(unresolved)
            if originals is old_texts and removed_live_paths and not old_paths.isdisjoint(removed_live_paths):
                # A live page may now equal a retired one because GC removed its links.
                from openjiuwen.harness.personal_context.context_capacity import _remove_retired_links

                for target in (target_paths & available) - old_paths:
                    if target not in baseline or target in matches:
                        continue
                    try:
                        cleaned = _remove_retired_links(_read_page(baseline[target]), target, removed_live_paths)
                    except ValueError:
                        continue  # An unparseable link supplies no evidence of a cleanup.
                    if cleaned == _read_page(pages[target]):
                        stationary.add(target)
            for old in stationary:
                if old not in matches and old in available:
                    matches[old] = old
                    available.remove(old)

        # Infer a move only when neither endpoint has another possible owner or destination.
        moves: dict[str, str | None] = {}
        for key, old_paths in originals.items():
            unmatched = [old for old in old_paths if old not in matches]
            if not unmatched:
                continue
            remaining = targets.get(key, set()) & available
            owner = unmatched[0] if len(unmatched) == len(remaining) == 1 else None
            for target in remaining:
                moves[target] = owner if target not in moves else None
        for target, old in moves.items():
            if old is not None:
                matches[old] = target
                available.remove(target)
    for old in baseline:
        if old not in matches and old in available:
            matches[old] = old
            available.remove(old)
    return matches


def prepare_context_merge(baseline_root: Path, candidate_root: Path) -> tuple[dict[str, str], dict[str, Path]]:
    """Hash the baseline for comparison with live and list the finished sandbox pages."""
    return _page_signatures(baseline_root), dict(_page_files(candidate_root))


def merge_context_candidate(
    baseline_root: Path,
    candidate_root: Path,
    live_root: Path,
    *,
    source_root: Path,
    relocations: dict[str, str],
    prepared: tuple[dict[str, str], dict[str, Path]] | None = None,
) -> bool:
    """Apply B/S/L selection under the publication lock; S knowledge wins conflicts."""
    from openjiuwen.harness.personal_context.context_pipeline import (
        _MARKDOWN_LINK,
        _RELATED_END,
        _RELATED_START,
        _SOURCE_LINKS_END,
        _SOURCE_LINKS_START,
        _classify_reference_target,
        _extended_path,
        _is_program_description,
        _managed_block_bounds,
        _markdown_link_target,
        _publish_error,
        _reference_source_path,
        _render_context_navigation,
        _replace_managed_block,
        _rewrite_context_markdown_links,
        _source_ids_reachable_from_page,
    )

    baseline_all, candidate_all = prepared or prepare_context_merge(baseline_root, candidate_root)
    live_all = _page_signatures(live_root)
    if baseline_all == live_all:
        return False
    baseline = _ordinary_pages(baseline_root, baseline_all)
    candidate = _ordinary_pages(candidate_root, candidate_all)
    live = _ordinary_pages(live_root, live_all)
    ambiguous_sandbox_origins: set[str] = set()
    candidate_paths = _match_pages(
        baseline, candidate, relocations, ambiguous_sandbox_origins=ambiguous_sandbox_origins,
    )
    live_paths = _match_pages(baseline, live, {}, removed_live_paths=baseline_all.keys() - live_all.keys())
    chosen = candidate_all.copy()
    inherited: dict[str, str] = {}
    final_paths: dict[str, str] = {}
    for old, path in baseline.items():
        original = _read_page(path)
        arranged_path = candidate_paths.get(old)
        current_path = live_paths.get(old)
        arranged = _read_page(candidate[arranged_path]) if arranged_path is not None else None
        current = _read_page(live[current_path]) if current_path is not None else None
        profile = _existing_blocks(original)
        original_knowledge = _knowledge_text(original)
        arranged_changed = (
            arranged is not None and _knowledge_text(arranged, existing_blocks=profile) != original_knowledge
        )
        current_changed = (
            current is not None and _knowledge_text(current, existing_blocks=profile) != original_knowledge
        )
        if arranged_changed:
            final_paths[old] = arranged_path
        elif arranged is not None and current == original and current_path == old:
            # Preserve valid sandbox maintenance when this live page did not
            # change; another page's retirement must not discard new links.
            final_paths[old] = arranged_path
        elif current is None:
            if arranged_path is not None and old not in ambiguous_sandbox_origins:
                chosen.pop(arranged_path, None)
        elif arranged is not None or current_changed:
            target = current_path if arranged_path in {None, old} else arranged_path
            if arranged_path is not None and target != arranged_path:
                chosen.pop(arranged_path, None)
            # An independently authored sandbox page owns a path collision.
            if target in candidate and target != arranged_path:
                raise _publish_error("concurrent Context changes have conflicting page paths")
            chosen[target] = live[current_path]
            inherited[target] = current_path
            final_paths[old] = target
    matched_live = set(live_paths.values())
    for relative in live_all:
        if _is_program_description(relative):
            original = _read_page(baseline_root / relative) if relative in baseline_all else None
            arranged = _read_page(candidate_root / relative) if relative in candidate_all else None
            if arranged is None or (
                original is not None
                and _knowledge_text(arranged, existing_blocks=_existing_blocks(original)) == _knowledge_text(original)
            ):
                chosen[relative] = live_root / relative
        elif relative not in matched_live and relative not in chosen:
            chosen[relative] = live[relative]
            inherited[relative] = relative
    live_mapping = {
        live_paths[old]: target for old, target in final_paths.items() if old in live_paths
    }

    def selected_markdown(relative: str) -> str:
        markdown = _read_page(chosen[relative])
        if relative in inherited:
            markdown = _rewrite_context_markdown_links(
                markdown, context_root=live_root, source_root=source_root,
                old_page_relative=inherited[relative], new_page_relative=relative, mapping=live_mapping,
            )
        return markdown

    def classify_target(raw_target: str, relative: str) -> tuple[str, str] | None:
        return _classify_reference_target(
            raw_target, page_relative=PurePosixPath(relative), context_root=candidate_root,
            final_context_root=live_root, source_root=source_root, error=_publish_error,
            validate_target=False,
        )

    def remove_deleted_links(markdown: str, relative: str) -> str:
        def remove_deleted_link(match: re.Match[str]) -> str:
            classified = classify_target(match.group(1), relative)
            if classified is not None and classified[0] == "source":
                _reference_source_path(source_root, classified[1], error=_publish_error)
            if classified is None or classified[0] != "context" or classified[1] not in removed:
                return match.group(0)
            return match.group(0)[1:].partition("](")[0]

        return rewrite_markdown_prose(
            markdown, lambda text: _MARKDOWN_LINK.sub(remove_deleted_link, text),
        )

    # Rebase links that were valid in S and lost their targets during the
    # merge. Links already broken by S remain visible to publication checks.
    removed = set(candidate_all) - set(chosen)
    inherited_sources: dict[str, set[str]] = {}
    for relative in chosen:
        if _is_program_description(relative):
            continue
        original = _read_page(candidate_root / relative) if relative in candidate_all else selected_markdown(relative)
        related = _managed_block_bounds(
            original, start=_RELATED_START, end=_RELATED_END,
        )
        if related is not None:
            original = original[:related[0]] + original[related[1]:]
        for raw_target in _MARKDOWN_LINK.findall(markdown_reference_text(original)):
            classified = classify_target(raw_target, relative)
            if classified is None or classified[0] != "context" or classified[1] not in removed:
                continue
            target = classified[1]
            inherited_sources.setdefault(relative, set()).update(_source_ids_reachable_from_page(
                candidate_root, final_context_root=live_root, source_root=source_root, page_relative=target,
                allow_missing_body=True,
            ))

    # Finish every source proof before mutating S, then rewrite one page at a time.
    for relative in removed:
        _extended_path(candidate_root / relative).unlink()
    for relative in chosen:
        markdown = remove_deleted_links(selected_markdown(relative), relative)
        sources = inherited_sources.get(relative, set())
        if sources:
            rows = []
            for source_id in sorted(sources):
                source = _reference_source_path(source_root, source_id, error=_publish_error)
                target = os.path.relpath(source, start=(live_root / relative).parent).replace("\\", "/")
                rows.append(f"- [原始来源]({_markdown_link_target(target)})")
            bounds = _managed_block_bounds(
                markdown, start=_SOURCE_LINKS_START, end=_SOURCE_LINKS_END,
            )
            body = "## PersonalContext 来源关联"
            if bounds is not None:
                body = markdown[bounds[0] + len(_SOURCE_LINKS_START):
                                bounds[1] - len(_SOURCE_LINKS_END)].strip()
            markdown = _replace_managed_block(
                markdown, start=_SOURCE_LINKS_START, end=_SOURCE_LINKS_END,
                body=body + "\n\n" + "\n".join(rows), default_heading=PurePosixPath(relative).stem,
            )
        if relative in candidate_all and markdown == _read_page(candidate_root / relative):
            continue
        path = _extended_path(candidate_root / relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
    description_moves = {
        old: target for old, target in relocations.items()
        if _is_program_description(old) and target in chosen
    }
    relocations.clear()
    relocations.update(description_moves)
    relocations.update({old: target for old, target in final_paths.items() if old != target})
    _render_context_navigation(candidate_root)
    return True
