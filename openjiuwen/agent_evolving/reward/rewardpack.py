"""Batch evaluation on a RewardPack (cases.json + original_tasks/), compared with the official scores.

For every case, round and arm it scores the candidate from what the agent had and produced: the task prompt, the
deliverable files' text, the final reply and the task's input files as inputs
(WorkBuddy workspace inputs, Claw-Eval fixtures). Gold answers and graders are never shown. Both arms of a task and
round share one criteria set, so they are judged alike.

Usage:
  python -m openjiuwen.agent_evolving.reward.rewardpack --pack /path/to/rewardpack_top10 --out results/
      [--rounds featured|all] [--limit N] [--concurrency 3] [--config config.json]
Results: results/results.jsonl (one line per candidate, resumable), results/criteria/ and results/summary.json.
"""

import argparse
import asyncio
import json
import sys
import tarfile
import time
from pathlib import Path

from . import pipeline
from .config import load_config
from .files import extract_text, read_files

REFERENCE_SUFFIXES = {".md", ".txt", ".csv", ".json", ".jsonl", ".yaml", ".yml", ".tsv", ".xlsx", ".docx", ".pptx",
                      ".html", ".xml", ".py", ".cu", ".h", ".cpp"}


def references(pack, case):
    """The inputs the agent could read: WorkBuddy workspace files, or Claw-Eval fixtures."""
    root = pack / "original_tasks" / case["benchmark"] / case["task"]
    files = {}
    archive = root / "environment" / "workspace.tar.gz"
    if archive.is_file():
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                if member.isfile() and Path(member.name).suffix.lower() in REFERENCE_SUFFIXES:
                    text = extract_text(member.name, tar.extractfile(member).read())
                    if text:
                        files[member.name.lstrip("./")] = text
    fixtures = root / "fixtures"
    if fixtures.is_dir():
        files.update({f"fixtures/{k}": v for k, v in read_files([fixtures]).items()})
    return files


def candidate(arm):
    """(deliverable, final reply) of one arm."""
    deliverable = {a["file"]: a.get("text") or "[no text extracted]" for a in arm.get("artifacts") or []}
    changed = [f"{c['path']} ({c.get('bytes', '?')} bytes)" for c in arm.get("changed_files") or []]
    deliverable["(workspace changes)"] = "\n".join(changed) or "(no files changed)"
    return deliverable, arm.get("final_response") or ""


def ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    result = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            result[order[k]] = (i + j) / 2
        i = j + 1
    return result


def spearman(xs, ys):
    if len(xs) < 3:
        return None
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    var = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return round(cov / var, 3) if var else None


def summarize(rows):
    done = [r for r in rows if r.get("total") is not None]
    pairs, agree = 0, 0
    by_key = {}
    for r in done:
        by_key.setdefault((r["task"], r["round"]), {})[r["arm"]] = r
    for both in by_key.values():
        if "A" in both and "B" in both and both["A"]["official"] != both["B"]["official"]:
            pairs += 1
            official = both["B"]["official"] > both["A"]["official"]
            ours = both["B"]["total"] > both["A"]["total"]
            agree += int(official == ours and both["B"]["total"] != both["A"]["total"])
    totals = [r["total"] for r in done]

    def mean(values):
        values = [v for v in values if v is not None]
        return round(sum(values) / len(values), 1) if values else None

    return {
        "candidates": len(done), "failed": len(rows) - len(done),
        "pairwise_agreement": f"{agree}/{pairs}" if pairs else None,
        "spearman_vs_official": spearman(totals, [r["official"] for r in done]),
        "mean_total": mean(totals),
        "mean_official": mean([r["official"] for r in done]),
        "share_at_or_above_90": round(sum(t >= 90 for t in totals) / len(totals), 2) if totals else None,
        "max_total": max(totals) if totals else None,
    }


async def main_async(args):
    pack, out = Path(args.pack), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    config = load_config(args.config)
    results_path = out / "results.jsonl"
    rows = [json.loads(line) for line in results_path.read_text("utf-8").splitlines()] \
        if results_path.is_file() else []
    seen = {(r["task"], r["round"], r["arm"]) for r in rows if r.get("total") is not None}
    rows = [r for r in rows if r.get("total") is not None]

    jobs = []
    for case in json.loads((pack / "cases.json").read_text("utf-8"))["cases"]:
        refs = references(pack, case)
        for rnd in case["rounds"]:
            if args.rounds == "featured" and rnd["round"] != case["featured_round"]:
                continue
            for arm_name in ("A", "B"):
                if (case["task"], rnd["round"], arm_name) not in seen:
                    jobs.append((case, rnd, arm_name, refs))
    jobs = jobs[:args.limit] if args.limit else jobs
    print(f"{len(jobs)} candidate(s) to score, {len(rows)} already done", file=sys.stderr)

    gate = asyncio.Semaphore(args.concurrency)
    lock = asyncio.Lock()
    started = time.monotonic()
    criteria_dir = out / "criteria"
    criteria_dir.mkdir(exist_ok=True)
    criteria_tasks = {}

    async def criteria_for(case, rnd):
        """One criteria set per task and round, shared by both arms so they are judged alike; cached on disk."""
        path = criteria_dir / f"{case['task']}_r{rnd['round']}.json"
        if path.is_file():
            return json.loads(path.read_text("utf-8"))
        key = path.name
        if key not in criteria_tasks:
            async def write():
                written = await pipeline.write_criteria(rnd["task_prompt"], config)
                path.write_text(json.dumps(written, ensure_ascii=False, indent=2), encoding="utf-8")
                return written
            criteria_tasks[key] = asyncio.ensure_future(write())
        return await criteria_tasks[key]

    async def run(case, rnd, arm_name, refs):
        deliverable, reply = candidate(rnd["arms"][arm_name])
        label = f"{case['task']} r{rnd['round']} {arm_name}"
        async with gate:
            try:
                criteria = await criteria_for(case, rnd)
                report = await pipeline.text_reward(rnd["task_prompt"], deliverable, config, response=reply,
                                              references=refs, criteria=criteria, log=lambda m: None)
                row = {"total": report["total"]["score"], "report": report}
            except Exception as error:  # noqa: BLE001 - record and move on
                row = {"total": None, "error": f"{type(error).__name__}: {str(error)[:300]}"}
        row = {"task": case["task"], "benchmark": case["benchmark"], "round": rnd["round"], "arm": arm_name,
               "official": rnd[f"{arm_name.lower()}_score"], **row}
        async with lock:
            rows.append(row)
            with results_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"[{time.monotonic() - started:5.0f}s] {label}: ours {row['total']} official {row['official']:.1f}"
              + (f" ERROR {row['error']}" if row.get("error") else ""), file=sys.stderr, flush=True)

    await asyncio.gather(*(run(*job) for job in jobs))
    summary = summarize(rows)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m openjiuwen.agent_evolving.reward.rewardpack",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--pack", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--rounds", choices=("featured", "all"), default="featured")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--config")
    asyncio.run(main_async(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
