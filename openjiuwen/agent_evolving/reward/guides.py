"""Domain knowledge ("metric guides") for writing criteria and planning checks: Markdown files whose first line may
be "keywords: a, b, c". Before each run the guides are scanned: one with keywords is used when one of them occurs in
the Task; for those without keywords, one short model call picks the ones that fit. Guides are read from the bundled
metrics/ folder (picture books, games, personal assistants), from ~/.reward/metrics/
(or $REWARD_HOME/metrics/) and from config "metric_guides" (files or folders).
"""

import json
import os
import re
from pathlib import Path

BUNDLED = Path(__file__).with_name("metrics")
GUIDE_LIMIT = 8000   # characters of guide text given to a model


def user_folder():
    return Path(os.environ.get("REWARD_HOME", "~/.reward")).expanduser() / "metrics"


def guide_files(extra=()):
    """The bundled guides, the user's folder and the files and folders named in `extra`, each once, in that order."""
    found = []
    for source in [BUNDLED, user_folder(), *map(Path, extra or ())]:
        source = source.expanduser()
        files = sorted(source.glob("*.md")) if source.is_dir() else [source] if source.is_file() else []
        found += [f for f in files if f.resolve() not in {g.resolve() for g in found}]
    return found


def read_guide(path):
    """(name, keywords, body) of one guide file."""
    text = Path(path).read_text(encoding="utf-8")
    first, _, rest = text.partition("\n")
    match = re.match(r"\s*keywords\s*:\s*(.*)", first, re.I)
    keywords = [k.strip().lower() for k in match.group(1).split(",") if k.strip()] if match else []
    return Path(path).stem, keywords, (rest if match else text).strip()


def by_keywords(task, config):
    """([(name, body)] chosen by keyword, [(name, body)] without keywords, still to be decided)."""
    text = (task or "").lower()
    chosen, undecided = [], []
    for path in guide_files(config.get("metric_guides")):
        try:
            name, keywords, body = read_guide(path)
        except OSError:
            continue
        if not keywords:
            undecided.append((name, body))
        elif any(k in text for k in keywords):
            chosen.append((name, body))
    return chosen, undecided

PICK_PROMPT = """You choose which evaluation guides fit a Task. You see the Task and, for each guide, its name and
opening. Choose a guide only when the work the Task asks for is of the kind the guide is about. Return JSON:
{"guides": ["name", ...]} (empty when none fits)."""


async def for_task(task, config, model=None, log=lambda message: None, notes=None):
    """[(name, body)] of the guides that fit this Task: keyword matches, plus those without keywords that the model
    picks (none of them when there is no model; when the pick fails, a warning says so).
    """
    chosen, undecided = by_keywords(task, config)
    if undecided and model is not None:
        try:
            found = await model.json(PICK_PROMPT, json.dumps(
                {"task": task, "guides": [{"name": n, "opening": b[:400]} for n, b in undecided]}, ensure_ascii=False))
            names = set(found.get("guides") or []) if isinstance(found, dict) else set(found or [])
            chosen += [(n, b) for n, b in undecided if n in names]
        except Exception as error:  # noqa: BLE001 - guides help, the score does not depend on them
            log(f"choosing guides failed: {str(error)[:200]}")
            if notes is not None:
                notes.append(f"挑选评测知识没有完成（{type(error).__name__}），本次只用了按关键词选中的文档。")
    return chosen


def as_text(guides):
    """The chosen guides as one block for a prompt, clipped to GUIDE_LIMIT."""
    text = "\n\n".join(f"## Guide: {name}\n{body}" for name, body in guides)
    return text[:GUIDE_LIMIT]
