"""Score an agent's delivered work; print the Markdown reply and write the standard result JSON.

  python -m openjiuwen.agent_evolving.reward --task task.md --artifact index.html
  python -m openjiuwen.agent_evolving.reward --task task.md --artifact report.md table.xlsx \\
      --txt-response reply.md --user-note "第 2 页多一只手" --rubric rubric.json --out reward_result.json

--evidence adds every check's output under "evidence" in the JSON.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from . import pipeline, progress
from .config import load_config
from .result import render


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m openjiuwen.agent_evolving.reward", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", required=True, help="task text or a file containing it")
    parser.add_argument("--artifact", nargs="+", default=[],
                        help="the delivered files or directories; omit when the work has no file (the final reply "
                             "is scored)")
    parser.add_argument("--txt-response", help="the agent's final text reply: text or a file")
    parser.add_argument("--rubric", help="file keeping the rubric (with the checks added from feedback); later "
                                         "rounds reuse it")
    parser.add_argument("--new-rubric", action="store_true",
                        help="with --rubric: set the kept rubric aside and write a new one")
    parser.add_argument("--previous-result", help="an earlier reward_result.json of the same Task: new criteria keep "
                                                  "its floor claims and raise the challenge claims above what was met")
    parser.add_argument("--user-note", help="the user's remarks on the work (text or a file); they become checks")
    parser.add_argument("--process-signal", help="environment errors and the user's feedback across turns (text or a "
                                                 "file); they become checks")
    parser.add_argument("--config", help="JSON config; default $REWARD_CONFIG, else ~/.reward/config.json, "
                                         "else built-in defaults")
    parser.add_argument("--evidence", action="store_true", help="include every check's output in the JSON")
    parser.add_argument("--out", default="reward_result.json")
    args = parser.parse_args(argv)
    try:
        import openai  # noqa: F401
    except ImportError:
        sys.exit("Missing dependency (openai); run with the Python that has openai (and playwright for pages).")

    def text_of(value):
        return Path(value).read_text(encoding="utf-8") if value and Path(value).is_file() else (value or "")

    task = text_of(args.task)
    config = {**load_config(args.config), **({"include_evidence": True} if args.evidence else {})}
    started = time.monotonic()

    def log(message):
        print(f"[{time.monotonic() - started:5.0f}s] {message}", file=sys.stderr, flush=True)

    run = progress.use(progress.Progress.for_session("general"))
    if args.rubric and args.new_rubric:
        archived = pipeline.archive_rubric(args.rubric)
        if archived:
            log(f"the kept rubric is moved aside as {archived.name}; writing a new one")
    try:
        result = asyncio.run(pipeline.reward(task, args.artifact, config, txt_response=text_of(args.txt_response),
                                             user_note=text_of(args.user_note),
                                             process_signal=text_of(args.process_signal),
                                             rubric_path=args.rubric, log=log,
                                             previous_result=json.loads(Path(args.previous_result).read_text(
                                                 encoding="utf-8")) if args.previous_result else None))
    except BaseException as error:
        run.fail(f"{type(error).__name__}: {error}")
        raise
    run.finish(result["total"]["score"])
    result["meta"]["artifacts"] = [str(Path(p).resolve()) for p in args.artifact]
    out_path = Path(args.out).resolve()
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(render(result, out_path))


if __name__ == "__main__":
    main()
