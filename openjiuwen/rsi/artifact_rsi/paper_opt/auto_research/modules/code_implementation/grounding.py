"""Ground OpenJiuwen references for the coding agent.

``gather_reference_excerpts`` is the older docs-only keyword filter. The
coding agent discovers APIs itself with ``openjiuwen_ref_search`` and
``openjiuwen_ref_read_file``. ``reference_trace_ok`` checks that a trace
either read a definition and an import, or searched twice and recorded that
nothing reusable was found.
"""

from __future__ import annotations

import re
from pathlib import Path

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.schemas import ExperimentPlan

# File-relative (like the rail's own _DEFAULT_ASSETS_ROOT), not CWD-relative.
# Walk from this file: code_implementation -> modules -> auto_research ->
# paper_opt -> artifact_rsi -> rsi -> openjiuwen -> repo root.
ASSETS_ROOT = Path(__file__).resolve().parents[7] / "docs"
_DOCS_INDEX_PATH = Path("en/SUMMARY.md")
_SEARCH_SUFFIXES = {".py", ".md"}
_EXCERPT_CHARS = 240
_EXCERPT_RADIUS = 120
_MIN_KEYWORD_LEN = 5
# A capability hit is inherently more decision-relevant than a prose
# mention at the same keyword-overlap score — this nudges it ahead without
# letting a barely-related capability file bury a genuinely on-topic
# example/doc.
_CAPABILITY_SCORE_BOOST = 1.3

# A file defining one of these looks like a directly reusable capability,
# not just prose that mentions a concept — see module docstring.
_CAPABILITY_PATTERNS = [
    re.compile(r"class\s+\w+\s*\((?:[\w.]*\.)?(?:Tool|Rail|Skill|Agent)\b"),
    re.compile(r"\bToolCard\s*\("),
    re.compile(r"\bregister_(?:tool|skill|rail|ability)\b"),
]


# -- layer 1: the free map ---------------------------------------------------


def docs_index_path(assets_root: Path = ASSETS_ROOT) -> str | None:
    """Relative path to the curated docs table of contents, if present.

    Returned as a pointer, not embedded — SUMMARY.md runs ~350 lines /
    ~37KB, too big to pay in every prompt regardless of whether the agent
    ends up needing it. The agent opens it itself with its own read tool.
    """
    path = assets_root / _DOCS_INDEX_PATH
    if not path.is_file():
        return None
    try:
        return path.relative_to(assets_root.parent).as_posix()
    except ValueError:
        return path.as_posix()


# -- layer 2: the best-effort filter -----------------------------------------


def _keywords(plan: ExperimentPlan) -> list[str]:
    text = " ".join(
        [plan.setup, plan.expected_outcomes, *plan.variables, *plan.baselines, *plan.metrics]
    )
    words = {w.strip(".,()[]{}:;\"'").lower() for w in text.split()}
    return sorted(w for w in words if len(w) >= _MIN_KEYWORD_LEN)


def _iter_corpus_files(assets_root: Path):
    if not assets_root.exists():
        return
    for path in assets_root.rglob("*"):
        if path.is_file() and path.suffix in _SEARCH_SUFFIXES:
            yield path


def _load_corpus(assets_root: Path) -> list[tuple[Path, str, str]]:
    """(path, text, lowered_text) for every candidate file."""
    corpus = []
    for path in _iter_corpus_files(assets_root):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        corpus.append((path, text, text.lower()))
    return corpus


def _is_capability_file(text: str) -> bool:
    return any(pattern.search(text) for pattern in _CAPABILITY_PATTERNS)


def _best_excerpt(text: str, positions: list[int]) -> str:
    """Center the excerpt on the middle of where matches cluster, not just
    the file's first _EXCERPT_CHARS (usually just an imports/license
    header)."""
    if not positions:
        return text[:_EXCERPT_CHARS].strip()
    center = positions[len(positions) // 2]
    start = max(0, min(center - _EXCERPT_RADIUS, len(text) - _EXCERPT_CHARS))
    end = min(len(text), start + _EXCERPT_CHARS)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end].strip() + suffix


def gather_reference_excerpts(
    plan: ExperimentPlan, *, max_hits: int = 5, assets_root: Path = ASSETS_ROOT
) -> list[tuple[str, str, bool]]:
    """Best-effort local search over agent-core-rsi's docs/ for plan-relevant starting points.

    Returns up to `max_hits` (relative_path, excerpt, is_capability) triples,
    most relevant first. `is_capability=True` flags a file that looks like it
    defines a directly reusable Tool/Rail/Skill/Agent class (see
    `_CAPABILITY_PATTERNS`) rather than just mentioning a concept in prose.
    Scoring is word-boundary keyword overlap (not raw substring counting —
    "search" matching inside "researching" was a real false-positive class
    under the old approach). Returns an empty list if `assets_root` doesn't
    exist or nothing matches — callers should treat that as "no starting
    point found," not an error; combined with the free map (layer 1) above,
    that's itself a real signal that plain code is the right call here.
    """
    keywords = _keywords(plan)
    if not keywords:
        return []

    corpus = _load_corpus(assets_root)
    if not corpus:
        return []

    patterns = {kw: re.compile(rf"\b{re.escape(kw)}\b") for kw in keywords}

    scored: list[tuple[float, str, str, bool]] = []
    for path, text, lowered in corpus:
        file_matches: dict[str, list[int]] = {}
        for kw, pattern in patterns.items():
            positions = [m.start() for m in pattern.finditer(lowered)]
            if positions:
                file_matches[kw] = positions
        if not file_matches:
            continue
        score = float(sum(len(positions) for positions in file_matches.values()))
        is_capability = _is_capability_file(text)
        if is_capability:
            score *= _CAPABILITY_SCORE_BOOST
        all_positions = sorted(p for positions in file_matches.values() for p in positions)
        try:
            rel = path.relative_to(assets_root.parent).as_posix()
        except ValueError:
            rel = path.as_posix()
        scored.append((score, rel, _best_excerpt(text, all_positions), is_capability))

    scored.sort(key=lambda hit: hit[0], reverse=True)
    return [(rel, excerpt, is_cap) for _, rel, excerpt, is_cap in scored[:max_hits]]


def reference_trace_ok(events: list[dict], assumptions: str = "") -> bool:
    """True when source was explored, or two source searches found nothing reusable.

    A passing explore trace has ``openjiuwen_ref_search`` and at least two
    distinct ``source/openjiuwen/...`` reads (the definition and an import).
    A from-scratch trace has two source searches and an assumptions note that
    nothing reusable was found. Reading only ``SUMMARY.md`` is not enough.
    """
    source_searches = sum(1 for event in events if _is_source_search(event))
    source_reads = {
        path
        for event in events
        for path in _event_paths(event)
        if path.startswith("source/openjiuwen/")
    }
    explored = source_searches >= 1 and len(source_reads) >= 2
    gave_up = source_searches >= 2 and "nothing reusable" in assumptions.lower()
    return explored or gave_up


def _is_source_search(event: dict) -> bool:
    name = str(event.get("name") or event.get("tool") or "")
    if name != "openjiuwen_ref_search":
        return False
    scopes = event.get("scopes")
    data = event.get("data")
    if scopes is None and isinstance(data, dict):
        scopes = data.get("scopes")
    if scopes in (None, "", [], ()):
        return True
    if isinstance(scopes, str):
        scopes = [scopes]
    return "source" in {str(item) for item in scopes}


def _event_paths(event: dict) -> list[str]:
    paths: list[str] = []
    for key in ("path", "file_path", "virtual_path"):
        value = event.get(key)
        if value:
            paths.append(str(value).replace("\\", "/"))
    data = event.get("data")
    if isinstance(data, dict):
        for key in ("path", "file_path", "virtual_path"):
            value = data.get(key)
            if value:
                paths.append(str(value).replace("\\", "/"))
    return paths
