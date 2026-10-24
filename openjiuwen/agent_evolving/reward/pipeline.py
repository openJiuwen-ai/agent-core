"""The flow: criteria from the Task -> checks per criterion -> judge -> score in code -> overall verdict.

Pure scoring: no trajectory is read and nothing is proposed. The result is the standard JSON of result.py.
"""

import asyncio
import json
import re
import shutil
from pathlib import Path

from . import checks, evidence_agent, guides, models, progress
from .files import FILE_LIMIT, bundle, clip
from .options import RewardOptions
from .prompts import ART_JUDGE_PROMPT, BLIND_NOTE, CRITERIA_FEEDBACK, CRITERIA_PROMPT, FEEDBACK_PROMPT, JUDGE_PROMPT
from .result import build
from .scoring import to_result, total
from .workspace import make_workspace, workspace_files

INPUTS_INLINE = 30000    # inputs longer than this in total are shown as an index; checks read them on demand


async def ask(model, prompt, content, stage, images=()):
    """A model call whose answer decides scores (criteria, judges): a failure stops the run with its cause, so no
    score is made up.
    """
    try:
        return await model.json(prompt, content, images) if images else await model.json(prompt, content)
    except Exception as error:  # noqa: BLE001 - keep the stage and the original cause
        raise evidence_agent.EvidenceError(f"{stage} failed: {type(error).__name__}: {error}") from error


async def optional(model, prompt, content, stage, options):
    """A model call the score does not depend on (feedback placement, summary): a failure becomes a
    warning in options.warnings (and a line in options.log), not a failed run.
    """
    try:
        return await model.json(prompt, content)
    except Exception as error:  # noqa: BLE001 - reported in the result's warnings
        options.log(f"{stage} failed: {str(error)[:200]}")
        options.warnings.append(f"{stage}没有完成（{type(error).__name__}），本次评测未使用这一步。")
        return {}


def inputs_index(references):
    """One row per input file: its path under ./_inputs/, its size and its first lines (a URL for a web page)."""
    return [{"path": f"_inputs/{name}", "chars": len(text), "head": clip(text[:300], 300)}
            for name, text in references.items()]


def text_evidence(task, deliverable, response, references):
    total_chars = sum(len(text) for text in references.values())
    if not references:
        shown = "(not provided)"
    elif total_chars <= INPUTS_INLINE:
        shown = bundle(references)
    else:   # too long to show: the Judge sees what the checks quoted from them, and this index
        shown = json.dumps({"note": "inputs too long to show in full; the checks quote what they found in them",
                            "inputs_index": inputs_index(references)}, ensure_ascii=False)
    return {
        "task": task,
        "deliverable": bundle(deliverable) if deliverable else "(no deliverable files)",
        "final_reply": clip(response or "(none)", FILE_LIMIT),
        "references": shown,
    }


async def evaluate_terminal(criterion, ctx, judge, gathered, log):
    """Judge one criterion from its evidence: (evidence, images) from the evidence agent. Whether the ordinary
    judge can see pictures is ctx["judge_seeing"] (default: yes).
    """
    name = criterion["id"]
    evidence, images = gathered
    seeing = ctx.get("judge_seeing", True)
    visual = criterion.get("kind") == "visual"
    if visual:
        seeing = ctx.get("art_seeing", seeing)
    if visual and seeing and not images:
        images = await default_screenshots(ctx["root"], log)  # looks are judged by eye, so always have pictures
    shown = images if seeing else []
    log(f"{name}: judging ({len(shown)} screenshot(s))")
    if visual and not shown:  # looks are not judged from code: no picture, no score (and the reason is kept)
        result = to_result(criterion, {"claims": []}, evidence)
        result["unscored"] = "no_screenshots" if seeing else "blind"
        return result
    if visual:  # judged by eye only: the code would tell the judge what it means to draw, not how it looks
        verdict = await ask(ctx.get("art_judge", judge), ART_JUDGE_PROMPT,
                            json.dumps({"task": ctx["evidence"]["task"], "criterion": criterion},
                                       ensure_ascii=False), f"{name}: art judge", shown)
        verdict = cap_by_critique(criterion, verdict)
        result = to_result(criterion, verdict, evidence)
        result["critique"] = verdict.get("critique") or []
        return result
    verdict = await ask(judge, JUDGE_PROMPT + ("" if seeing else BLIND_NOTE), json.dumps(
            {"task": ctx["evidence"]["task"], "criterion": criterion, "checks": evidence,
             "workspace": ctx["evidence"]["deliverable"],
             "final_reply": ctx["evidence"]["final_reply"], "inputs": ctx["evidence"]["references"]},
            ensure_ascii=False), f"{name}: judge", shown)
    return to_result(criterion, verdict, evidence)


def cap_by_critique(criterion, verdict):
    """A major weakness caps only the visual challenge claims it actually concerns."""
    major = [c for c in verdict.get("critique") or [] if isinstance(c, dict) and c.get("severity") == "major"]
    if not major:
        return verdict
    levels = [c.get("level", "floor") for c in criterion.get("claims") or []]
    affected = {}
    for issue in reversed(major):   # an earlier issue wins when several concern the same claim
        for claim in issue.get("claims") or []:
            if is_index(claim) and claim < len(levels):
                affected[claim] = issue
    for row in models.items(verdict, "claims"):
        index = row.get("index") if isinstance(row, dict) else None
        if not is_index(index) or index not in affected:
            continue
        if levels[index] == "challenge" and row.get("status") == "met":
            row["status"] = "partial"
            row["reason"] = (row.get("reason", "") + f"（主要问题：{affected[index].get('issue', '')}）").strip()
    return verdict


def is_index(value):
    """A usable position: a non-negative int (a bool is not one)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0

# the control that starts the work (not the first button, which is often a sound or settings toggle)
START_LABELS = ("开始", "Start", "START", "start", "Play", "PLAY", "play", "进入", "Begin", "Go")
START_BUTTON = ", ".join([f'button:has-text("{t}")' for t in START_LABELS]
                         + ["#startBtn", "#start", ".start", "[data-action=start]"])


DEFAULT_WALK = ("default walk, run before the evidence agent: press the start control and Space, close-up, then "
                "7 times the right arrow and one screen down, with a screenshot after each")


async def default_walk(root, log, errors=None):
    """The default look at the work, as a check: (check, facts, screenshots), or None when there is nothing to look
    at or it could not run. A page: a full view and a close-up after pressing the start control (a button reading
    开始 / Start / Play…) and Space, then seven more views after pressing the right arrow and scrolling one screen
    (the next pages of a book or slides, flipped or stacked in one long page). A work without a page, or whose page
    cannot be opened in a browser: its picture files, read straight from the files (no browser needed). It runs while
    the criteria are written and becomes the evidence agent's first check.
    errors: a list that gets the reason when the page could not be opened, or nothing could be looked at.
    """
    own_files = [p for p in root.rglob("*") if checks.INPUTS_DIR not in p.parts]   # not the inputs given to the work
    pages = [p for p in own_files if p.match("*.htm*")]   # the same name test as root.rglob("*.htm*")
    pages.sort(key=lambda p: (len(p.relative_to(root).parts), str(p)))   # the work's own page first
    pictures = [p for p in own_files if p.suffix.lower() in checks.IMAGE_SUFFIXES]
    if pages:
        walked = await walk_page(root, pages[0], log, errors)
        if walked or not pictures:
            return walked
        log("the page could not be opened; looking at the delivered pictures instead")
    if not pictures:
        return None
    shots = [(f"delivered picture {label}", data) for label, data in await checks.image_views(pictures)]
    if not shots:
        if errors is not None:
            errors.append("none of the delivered picture files could be read as a picture")
        return None
    note = "default view, run before the evidence agent: the delivered pictures, each shown whole"
    names = sorted((str(p.relative_to(root)) for p in pictures), key=checks.natural)
    return ({"tool": "read", "pictures": names, "note": note},
            {"tool": "read", "note": note, "pictures": names}, shots)


async def walk_page(root, page_path, log, errors=None):
    """The default walk through one page in the browser (see default_walk), or None when it could not run."""
    page = str(page_path.relative_to(root))
    one_flip = ({"type": "key", "key": "ArrowRight"}, {"type": "scroll", "pages": 1}, {"type": "wait", "ms": 500})
    experiment = {"note": DEFAULT_WALK, "actions": [
        {"type": "click", "selector": START_BUTTON}, {"type": "wait", "ms": 600},
        {"type": "key", "key": "Space"}, {"type": "wait", "ms": 900},
        {"type": "closeup", "selector": "canvas, body", "region": [0, 0.3, 0.5, 0.7]}, *list(one_flip) * 7],
        "snapshots": [3] + [7 + 3 * i for i in range(7)]}  # later views: pages, slides or game moments
    try:
        result, shots = await asyncio.wait_for(checks.run_experiment(page_path, experiment),
                                               checks.BROWSER_CHECK_SECONDS)
    except Exception as error:  # noqa: BLE001 - the agent and the judges do without it then
        log(f"default screenshots failed: {str(error)[:200]}")
        if errors is not None:
            errors.append(f"{type(error).__name__}: {str(error)[:300]}")
        return None
    return ({"tool": "browser", "page": page, **experiment}, {"tool": "browser", "page": page, **result},
            [(f"default view, {label}", data) for label, data in shots])


async def default_screenshots(root, log):
    """The screenshots of the default walk, for a visual criterion whose checks took none; [] when it cannot run."""
    walk = await default_walk(root, log)
    return walk[2] if walk else []

# names that mean looks only; "界面" (UI) and "chart" are not, since a UI or chart criterion is also about behaviour
# and data, and a visual criterion is judged from screenshots alone
VISUAL_NAME = re.compile(r"视觉|画面|美术|外观|造型|画风|观感|美观|\b(?:visual|looks?|art|artwork|graphics?|appearance)\b", re.I)
MIXED_VISUAL_NAME = re.compile(r"交互|操作|流程|手感|数据|行为|\b(?:interaction|controls?|flow|feel|data|behaviou?r)\b", re.I)
VISUAL_TASK = re.compile(r"游戏|画面|页面|网页|海报|图表|界面|动画|插画|绘本|图画书|漫画|幻灯片|小程序|game|web ?page|poster|chart|\bui\b|"
                         r"website|animation|illustration|landing|picture ?book|storybook|comic|slides", re.I)
LOOK_CLAIM = {"zh": "整体画面达到同类已发布作品的水准，没有业余感。",
              "en": "The look matches published work of this kind; nothing looks amateur."}
JUDGE_NAMED = re.compile(r"美术总监|艺术总监|art director", re.I)   # who judges is not part of a claim


def visual_criterion(chinese):
    if chinese:
        return {"id": "visual", "scope": "terminal", "kind": "visual", "name": "画面质量",
                "goal": "主体和整体画面看起来像已发布的同类成品。", "claims": [
                    {"text": "主体（角色、产品或图表）一眼就能认出来。", "level": "floor"},
                    {"text": "画面风格符合任务主题。", "level": "floor"},
                    {"text": "主体按所选画风造型到位：一眼可读，比例和简化符合该画风，没有错位、穿插或被截断。", "level": "challenge"},
                    {"text": "构图服务于作品用途，主体大小、位置和留白符合同类已发布作品的惯例。", "level": "challenge"},
                    {"text": "画风统一，配色和谐。", "level": "challenge"},
                    {"text": LOOK_CLAIM["zh"], "level": "challenge"}]}
    return {"id": "visual", "scope": "terminal", "kind": "visual", "name": "Visual quality",
            "goal": "The main subject and the whole view look like published work of this kind.", "claims": [
                {"text": "The main subject (a character, product or chart) is recognisable at a glance.",
                 "level": "floor"},
                {"text": "The look fits the Task's theme.", "level": "floor"},
                {"text": ("The main subject is well built for its art style: a readable silhouette, "
                          "proportions true to that style, nothing misplaced, tangled or cut off."),
                 "level": "challenge"},
                {"text": ("The composition serves the work's purpose; size, placement and space "
                          "follow the conventions of published work of its genre."), "level": "challenge"},
                {"text": "One consistent art style and a harmonious palette.", "level": "challenge"},
                {"text": LOOK_CLAIM["en"], "level": "challenge"}]}


def ensure_visual(criteria, task):
    """Looks are judged by eye whatever the criteria writer did: a terminal criterion named for looks becomes
    "kind": "visual" and gets the published-work claim (replacing a claim that names a judge, e.g. "an art director
    would accept it"); when the Task makes something people look at and no such
    criterion exists, a standard one is added. A "kind": "visual" that neither the Task nor the criterion's name
    supports (e.g. "clarity" of a text answer) is dropped: it would be judged from screenshots that do not exist.
    """
    chinese = models.is_chinese(task)
    looked_at = bool(VISUAL_TASK.search(task or ""))
    found = False
    for c in criteria:
        if c.get("scope") != "terminal":
            continue
        title = f"{c.get('id', '')} {c.get('name', '')}"
        named = VISUAL_NAME.search(title) and not MIXED_VISUAL_NAME.search(title)
        if c.get("kind") == "visual" and not (named or looked_at):
            c.pop("kind")
        if c.get("kind") == "visual" or named:
            c["kind"], found = "visual", True
            claim = LOOK_CLAIM["zh" if chinese else "en"]
            c["claims"] = [{**x, "text": claim} if JUDGE_NAMED.search(x.get("text", "")) else x
                           for x in c.get("claims") or []]
            if not any(x.get("text") == claim for x in c.get("claims") or []):
                c["claims"] = list(c.get("claims") or []) + [{"text": claim, "level": "challenge"}]
    if not found and looked_at:
        criteria.append(visual_criterion(chinese))
    return criteria


def earlier_rubric(result):
    """An earlier result of the same Task as the criteria writer sees it: each category with its claims, their level
    and how the work did.
    """
    items = {}
    for item in result.get("sub_category_items") or []:
        items.setdefault(item.get("category_id"), []).append(
            {"text": item.get("rubric", ""), "level": "challenge" if item.get("type") == "proactive_challenge"
             else "floor", "status": item.get("status")})
    return [{"name": c.get("category_name", ""), "goal": c.get("rubric", ""),
             "claims": items.get(c.get("category_id"), [])}
            for c in result.get("categories") or []]


FEEDBACK_SOURCES = {"user_note": "feedback", "feedback": "feedback", "process_signal": "process_signal"}
MAX_FEEDBACK_CLAIMS = 6


def feedback_inputs(options):
    """{"user_note": ..., "process_signal": ...} with the ones given (stripped), {} when neither is."""
    found = {"user_note": (options.user_note or "").strip(), "process_signal": (options.process_signal or "").strip()}
    return {key: value for key, value in found.items() if value}


async def write_criteria(task, config, options=None, knowledge="", producer=None, with_feedback=False):
    """Criteria from the Task alone (plus domain knowledge, and an earlier result in options.previous_result, if
    any); reusable to score several candidates alike. Claims without text are dropped, an unknown level counts as
    floor, a repeated id gets a suffix. with_feedback: the user's note and the process signal (options) are placed
    in the same call, as floor claims with "source" ("feedback" / "process_signal", at most 6), instead of a call of
    their own (feedback_claims).
    """
    options = options or RewardOptions()
    previous_result = options.previous_result
    models.set_language(task)
    producer = producer or models.Model(config["producer"])
    options.log("writing criteria from the task")
    content = task if not knowledge else (
        f"{task}\n\n---\nDomain knowledge (use what fits this Task; it is not a template):\n{knowledge}")
    if previous_result:
        content += ("\n\n---\nEarlier evaluation of this Task (its criteria and how the work did):\n"
                    + json.dumps(earlier_rubric(previous_result), ensure_ascii=False))
    feedback = feedback_inputs(options) if with_feedback else {}
    prompt = CRITERIA_PROMPT.format(budget=config.get("criteria_budget", 3))
    if feedback:
        content += "\n\n---\nFeedback:\n" + json.dumps(feedback, ensure_ascii=False)
        prompt += CRITERIA_FEEDBACK
    allowed = [FEEDBACK_SOURCES[key] for key in feedback]
    drafted = await ask(producer, prompt, content, "criteria")
    criteria, ids, placed = [], set(), 0
    for n, c in enumerate(models.items(drafted, "criteria"), 1):
        claims = []
        for x in (c.get("claims") if isinstance(c, dict) else None) or []:
            if not (isinstance(x, dict) and isinstance(x.get("text"), str) and x["text"].strip()):
                continue
            claim = {k: v for k, v in x.items() if k != "source"}
            if allowed and x.get("source"):   # placed from the feedback: a requirement, marked with where it came from
                if placed >= MAX_FEEDBACK_CLAIMS:
                    continue
                placed += 1
                source = FEEDBACK_SOURCES.get(str(x["source"]))
                claims.append({**claim, "level": "floor", "source": source if source in allowed else allowed[0]})
            else:
                claims.append({**claim, "level": "challenge" if x.get("level") == "challenge" else "floor"})
        if not claims or c.get("scope", "terminal") != "terminal":
            continue
        cid = str(c.get("id") or f"c{n}").strip()
        cid = cid if cid not in ids else f"{cid}-{n}"
        ids.add(cid)
        criteria.append({**c, "id": cid, "scope": "terminal", "claims": claims})
    if not criteria:
        raise evidence_agent.EvidenceError("No criteria were written: " + json.dumps(drafted, ensure_ascii=False)[:300])
    return ensure_visual(criteria, task)

write_text_criteria = write_criteria  # the batch script's name for it


async def feedback_claims(task, criteria, config, options, producer=None):
    """The user's note and the process signal (options.user_note, options.process_signal) as checks placed in the
    rubric: each one goes to the existing criterion closest to its aspect (by id) and says which input it came
    from. A remark about one place becomes a rule for the whole work. [] when both are empty, or when the call
    failed (then a warning in options.warnings says so).
    """
    user_note, process_signal = (options.user_note or "").strip(), (options.process_signal or "").strip()
    if not (user_note or process_signal):
        return []
    rubric = [{"id": c["id"], "name": c.get("name", ""), "goal": c.get("goal", ""),
               **({"kind": "visual"} if c.get("kind") == "visual" else {}),
               "claims": [x.get("text", "") for x in c.get("claims") or []]} for c in criteria]
    found = await optional(producer or models.Model(config["producer"]), FEEDBACK_PROMPT, json.dumps(
            {"task": task, **({"user_note": user_note} if user_note else {}),
             **({"process_signal": process_signal} if process_signal else {}), "criteria": rubric},
            ensure_ascii=False), "把用户意见和过程信号写进评分标准", options)
    sources = [name for name, value in (("user_note", user_note), ("process_signal", process_signal)) if value]
    return [{"criterion": row.get("criterion"), "text": row["text"].strip(),
             "from": row.get("from") if row.get("from") in sources else sources[0]}
            for row in models.items(found, "claims")[:6]
            if isinstance(row, dict) and isinstance(row.get("text"), str) and row["text"].strip()]


def fallback_criterion(criteria):
    """Where a check goes when the model named no existing criterion: the "quality" criterion, else the last one
    that is not judged from screenshots alone.
    """
    seen_in_runs = [c for c in criteria if c.get("kind") != "visual"] or criteria   # never drop a concern
    return next((c for c in seen_in_runs if c.get("id") == "quality"), seen_in_runs[-1] if seen_in_runs else None)


def merge_feedback(criteria, claims):
    """Add the checks from the user's note ("source": "feedback") and the process signal ("source":
    "process_signal") to existing criteria as floor claims (they are requirements). No criterion is added.
    """
    criteria = [{**c, "claims": list(c.get("claims") or [])} for c in criteria]
    by_id = {c["id"]: c for c in criteria}
    for claim in claims:
        target = by_id.get(claim["criterion"]) or fallback_criterion(criteria)
        if target is not None and not any(x.get("text") == claim["text"] for x in target["claims"]):
            source = "process_signal" if claim.get("from") == "process_signal" else "feedback"
            target["claims"].append({"text": claim["text"], "level": "floor", "source": source})
    return criteria


def load_rubric(path):
    """The criteria kept for this session ({"criteria": [...]}), or None when the file does not exist."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as error:
        raise evidence_agent.EvidenceError(f"cannot read kept rubric {path}: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("criteria"), list) or not data["criteria"]:
        raise evidence_agent.EvidenceError(f"kept rubric {path} has no criteria list")
    return data


def save_rubric(path, criteria):
    """Keep the criteria (with the checks added from feedback so far) for the next rounds."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({"criteria": criteria},
                                     ensure_ascii=False, indent=2), encoding="utf-8")


def archive_rubric(path):
    """For callers: move a kept rubric aside (reward_rubric.1.json, .2.json, ...) so the next reward() writes a
    new one; returns the archive path, or None when there was nothing to move.
    """
    path = Path(path)
    if not path.exists():
        return None
    n = 1
    while path.with_name(f"{path.stem}.{n}{path.suffix}").exists():
        n += 1
    target = path.with_name(f"{path.stem}.{n}{path.suffix}")
    path.rename(target)
    return target


def overall_summary(task, results):
    """The total verdict, made in code (no model call, which would only restate the judges): the strongest and the
    weakest category with their scores, then the judge's comment on the weakest. "" when nothing was scored.
    """
    scored = [r for r in results if r.get("score") is not None]
    if not scored:
        return ""
    chinese = models.is_chinese(task)
    best, worst = max(scored, key=lambda r: r["score"]), min(scored, key=lambda r: r["score"])
    top, low = best.get("name") or best["id"], worst.get("name") or worst["id"]
    if len(scored) == 1:
        head = f"「{top}」得分 {best['score']}" if chinese else f"{top} scored {best['score']}"
    elif best["score"] == worst["score"]:
        head = f"各维度得分均为 {best['score']}" if chinese else f"Every category scored {best['score']}"
    elif chinese:
        head = f"最强的是「{top}」（{best['score']}），最弱的是「{low}」（{worst['score']}）"
    else:
        head = f"Strongest: {top} ({best['score']}); weakest: {low} ({worst['score']})"
    comment = str(worst.get("summary") or "").strip()
    if not comment:
        return head + ("。" if chinese else ".")
    return head + ("：" if chinese else ": ") + comment


async def reward(task, artifacts, config, options=None):
    """Score delivered work; returns the standard result JSON (result.py).

    artifacts: the delivered files or directories (paths); [] when the answer is only in options.txt_response. They
    are copied into a scratch folder that is removed afterwards; the originals are never touched.
    options: a RewardOptions (the final reply, the user's note, the process signal, a kept rubric file, an earlier
    result, a log function...); see options.py for each field.
    """
    root = make_workspace(artifacts)
    try:
        return await score_workspace(task, root, config, options)
    finally:
        shutil.rmtree(root, ignore_errors=True)


async def rubric_for(task, config, options, producer):
    """(criteria, chosen guides, their text) for this run: the caller's criteria (options.criteria) as they are; else
    the kept rubric with the user's note and the process signal placed by their own call; else new criteria written
    together with them in one call. A changed rubric is kept in options.rubric_path.
    """
    log, notes, criteria, rubric_path = options.log, options.warnings, options.criteria, options.rubric_path
    chosen = await guides.for_task(task, config, producer, log, notes) if not criteria else []
    knowledge = guides.as_text(chosen)
    if chosen:
        log(f"domain knowledge: {', '.join(name for name, _ in chosen)}")
    kept = load_rubric(rubric_path) if rubric_path and not criteria else None
    fixed = bool(criteria)   # criteria given by the caller (batch runs) are used as they are; [] means none given
    placed = []
    if kept:
        log("reusing the kept rubric")
        criteria = ensure_visual([{**c, "scope": "terminal"} for c in kept["criteria"]
                                  if c.get("scope", "terminal") == "terminal"], task)
        placed = await feedback_claims(task, criteria, config, options, producer)
        if placed:
            criteria = merge_feedback(criteria, placed)
    elif not fixed:
        progress.current().stage("criteria")
        criteria = await write_criteria(task, config, options, knowledge, producer, with_feedback=True)
        for c in criteria:
            placed.extend({"criterion": c["id"]} for x in c["claims"] if x.get("source"))
    if placed:   # the user's concerns and the run's errors become part of the rubric, kept for the next rounds
        log(f"user note / process signal placed in: {', '.join(sorted({str(p['criterion']) for p in placed}))}")
    if rubric_path and not fixed:
        if not kept or placed:
            save_rubric(rubric_path, criteria)
    return criteria, chosen, knowledge


async def score_workspace(task, root, config, options=None):
    """reward() on a scratch folder that already exists (deliverable at the top, inputs under _inputs/); criteria
    given by the caller in options.criteria are used as they are (batch runs). There is no time limit.
    """
    options = (options or RewardOptions()).for_run()
    models.set_language(task)
    root = Path(root)
    deliverable, inputs = workspace_files(root)
    tools = checks.available_tools(root, config)
    has_pictures = "browser" in tools or any(p.suffix.lower() in checks.IMAGE_SUFFIXES for p in root.rglob("*")
                                           if checks.INPUTS_DIR not in p.parts)
    # The default walk through the work needs no criteria, so it runs while they are written.
    walk_errors: list[str] = []
    walk = asyncio.ensure_future(default_walk(root, options.log, walk_errors)) if has_pictures else None
    try:
        return await score_with(task, root, config, options, deliverable=deliverable, inputs=inputs, tools=tools,
                                has_pictures=has_pictures, walk=walk, walk_errors=walk_errors)
    finally:
        if walk is not None and not walk.done():
            walk.cancel()
        if walk is not None:
            await asyncio.gather(walk, return_exceptions=True)


WARNING = evidence_agent.WARNING   # a log line meant for the person watching (the attribution progress shows it)


async def score_with(task, root, config, options, *, deliverable, inputs, tools, has_pictures, walk, walk_errors):
    """The run after the workspace was read: rubric, evidence (the default walk `walk` is its first check), judging.
    What keeps a part of the work from being seen (a model that cannot read images, a browser that does not start) is
    said as it happens (a WARNING log line) and kept in the result's warnings.
    """
    response, notes, log = options.txt_response, options.warnings, options.log

    def warn(text):
        notes.append(text)
        log(WARNING + text)

    user_note, process_signal = options.user_note, options.process_signal
    producer = models.Model(config["producer"])
    judge = models.Model(config.get("judge") or config["producer"])
    criteria, chosen, knowledge = await rubric_for(task, config, options, producer)
    # work used in a browser is judged by using it, as its user would: its source is shown to no one
    readable = {
        n: t for n, t in deliverable.items() if "browser" not in tools or not n.lower().endswith(checks.PAGE_CODE)
    }
    evidence = text_evidence(task, readable, response, inputs)
    if readable != deliverable:
        evidence["deliverable"] = (bundle(readable) + "\n" if readable else "") + "(page source is not shown: " \
            + ", ".join(sorted(set(deliverable) - set(readable))) + ")"
    # A stronger eye, if configured.
    art = models.Model(config["art_judge"]) if config.get("art_judge") else judge
    ctx = {"root": root, "tools": tools, "evidence": evidence, "art_judge": art, "config": config,
           "plan_input": {"task": task, "available_tools": tools, "final_reply": response,
                          **({"domain_knowledge": knowledge} if knowledge else {}),
                          "workspace": {"files": {name: f"{text.count(chr(10)) + 1} lines"
                                                  for name, text in sorted(deliverable.items())},
                                        "inputs": sorted(inputs),
                                        **({"inputs_index": inputs_index(inputs)}
                                           if sum(map(len, inputs.values())) > INPUTS_INLINE else {}),
                                        "excerpts": bundle(readable) or "(page source is not shown)"}}}

    async def sees(model):   # each role decides for itself whether it gets pictures
        before = len(notes)
        seen = await models.can_see(model, log, notes) if has_pictures else True
        for text in notes[before:]:   # a probe without a clear answer: say so now
            log(WARNING + text)
        return seen

    evidence_route = config.get("evidence_model") or config["producer"]
    seeing = await sees(judge)
    art_seeing = seeing if art is judge else await sees(art)
    # The evidence model gets no screenshots unless "evidence_sees_images" is set: the judges get every screenshot
    # anyway, and pictures in every turn slow a slow model down further.
    evidence_seeing = False
    if config.get("evidence_sees_images"):
        same_as_judge = not isinstance(evidence_route, dict) or evidence_route.get("model") == judge.name
        evidence_seeing = seeing if same_as_judge else await sees(models.Model(evidence_route))
    ctx["judge_seeing"], ctx["art_seeing"], ctx["seeing"] = seeing, art_seeing, evidence_seeing
    if not seeing:
        warn(f"判定模型 {getattr(judge, 'name', '')} 看不到图片（看图试探的结论；模型能看图时，在模型配置里写 "
             "supports_vision: true 可跳过试探），普通维度只按文字和运行数值判定。")
    if not art_seeing and any(c.get("kind") == "visual" for c in criteria):
        warn(f"画面判定模型 {getattr(art, 'name', '')} 看不到图片，画面维度不评分、不计入总分；"
             "换用能看图的 art_judge 可评价这部分。")
    if not evidence_seeing:
        log("evidence model gets no screenshots (text only); the judges get all of them")
    log(f"{len(criteria)} criteria: {', '.join(c['id'] for c in criteria)}; tools: {tools}")
    progress.current().stage("evidence", total=len(criteria))
    limits = evidence_agent.Limits(rounds=int(config.get("evidence_rounds", evidence_agent.ROUNDS)))
    walked = await walk if walk is not None else None
    if walk is not None and walked is None:
        warn("默认浏览没能运行" + (f"（{walk_errors[0]}）" if walk_errors else "")
             + "，取证改由取证 Agent 自己打开作品。")
    # one DeepAgent gathers evidence for every criterion, starting from the default walk
    gathered = await evidence_agent.gather(criteria, ctx, evidence_route, log, limits,
                                           prefetched=[walked] if walked else [])
    browser_checks = {}   # a check serving several criteria counts once
    for evidence, _ in gathered.values():
        for f in evidence:
            if isinstance(f, dict) and f.get("tool") == "browser":
                browser_checks[id(f)] = f
    browser_runs = list(browser_checks.values())
    if browser_runs and all(f.get("could_not_run") for f in browser_runs):
        warn(f"浏览器检查全部没能运行（{str(browser_runs[0]['could_not_run'])[:200]}），"
             "作品的画面和交互都没有被实际看到。")
    progress.current().stage("judging", total=len(criteria))
    jobs = [evaluate_terminal(c, ctx, judge, gathered[c["id"]], log) for c in criteria]
    results = await asyncio.gather(*(progress.current().counted(job, c["id"]) for job, c in zip(jobs, criteria)))
    score = total(results)
    summary = overall_summary(task, results)
    log("done")
    warnings = notes
    used_tools = set()
    for r in results:
        for e in r.get("evidence") or []:
            if isinstance(e, dict) and e.get("tool") and "skipped" not in e:
                used_tools.add(e["tool"])
    used = sorted(used_tools)
    meta = {"deliverable": sorted(deliverable), "final_reply": bool(response), "inputs": sorted(inputs),
            **({"user_note": user_note} if user_note else {}),
            **({"process_signal": process_signal} if process_signal else {}),
            "models": {"producer": producer.name, "judge": judge.name,
                       **({"art_judge": art.name} if art is not judge else {})},
            "vision": {"judge": seeing, "art_judge": art_seeing},   # whether each judging model got pictures
            "tools": {"available": tools, "used": used},
            **({"guides": [name for name, _ in chosen]} if chosen else {}),
            **({"warnings": warnings} if warnings else {})}
    result = build(task, results, score, summary, meta)
    if config.get("include_evidence"):
        result["evidence"] = {r["id"]: r.get("evidence") for r in results}
    return result


async def text_reward(task, deliverable, config, options=None, references=None):
    """Score work given as text ({name: text}) with the same pipeline; used by the batch script. The final reply and
    the criteria given by the caller are options.txt_response and options.criteria.
    """
    root = make_workspace(texts=deliverable, reference_texts=references)
    try:
        return await score_workspace(task, root, config, options)
    finally:
        shutil.rmtree(root, ignore_errors=True)
