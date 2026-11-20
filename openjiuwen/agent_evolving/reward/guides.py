"""Domain knowledge ("metric guides") for writing criteria and planning checks: Markdown files whose first line may
be "keywords: a, b, c". Before each run the guides are scanned: guides with a keyword in the Task and guides without
keywords become candidates, and one short model call picks at most one that fits. Guides are read from the bundled
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
    """([(name, body)] with a keyword in the Task, [(name, body)] without keywords). The first list is only
    candidates for the model, not a decision; see for_task.
    """
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

PICK_PROMPT = """You choose which evaluation guides fit a Task. You see the Task and, for each candidate, its
name and opening. Candidates are guides whose keywords occur in the Task and guides without keywords. Choose at
most one guide that genuinely fits the work the Task asks for; return none when no candidate fits. Return JSON:
{"guides": ["name"]} ({"guides": []} when none fits)."""


async def for_task(task, config, model=None, log=lambda message: None, notes=None):
    """[(name, body)] of the guide that fits this Task, chosen by the model from keyword matches and guides without
    keywords. No candidates, no model or a failed pick means no guide; a failed pick adds a warning when possible.

    A keyword match is only a candidate, not a decision: a word that occurs incidentally, or several guides that
    match at once, still need the model's confirmation. Guides with the same name collapse to one; the later
    source (the user folder, then metric_guides) wins.
    """
    chosen, undecided = by_keywords(task, config)
    candidates = list({name: (name, body) for name, body in chosen + undecided}.values())
    if not candidates or model is None:
        return []
    try:
        found = await model.json(PICK_PROMPT, json.dumps(
            {"task": task, "guides": [{"name": n, "opening": b[:400]} for n, b in candidates]}, ensure_ascii=False))
        names = found.get("guides") if isinstance(found, dict) else found
        if not isinstance(names, list):
            raise ValueError("the guide pick must be a list")
        picked = [(n, b) for n, b in candidates if isinstance(n, str) and n in set(names)]
        if len(picked) > 1:
            log("choosing guides picked more than one; using none")
            return []
        return picked
    except Exception as error:  # noqa: BLE001 - guides help, the score does not depend on them
        log(f"choosing guides failed: {str(error)[:200]}")
        if notes is not None:
            notes.append(f"挑选评测知识没有完成（{type(error).__name__}），本次没有使用评测知识。")
        return []


def as_text(guides):
    """The chosen guides as one block for a prompt, clipped to GUIDE_LIMIT."""
    text = "\n\n".join(f"## Guide: {name}\n{body}" for name, body in guides)
    return text[:GUIDE_LIMIT]
