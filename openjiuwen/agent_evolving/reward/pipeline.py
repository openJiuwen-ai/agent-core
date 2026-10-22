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
from .prompts import ART_JUDGE_PROMPT, BLIND_NOTE, CRITERIA_PROMPT, FEEDBACK_PROMPT, JUDGE_PROMPT, SUMMARY_PROMPT
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


async def optional(model, prompt, content, stage, notes, log):
    """A model call the score does not depend on (feedback placement, summary): a failure becomes a
    warning in the result, not a failed run.
    """
    try:
        return await model.json(prompt, content)
    except Exception as error:  # noqa: BLE001 - reported in the result's warnings
        log(f"{stage} failed: {str(error)[:200]}")
        notes.append(f"{stage}没有完成（{type(error).__name__}），本次评测未使用这一步。")
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


async def evaluate_terminal(criterion, ctx, producer, judge, log, gathered, seeing=True):
    """Judge one criterion from its evidence: (evidence, images) from the evidence agent."""
    name = criterion["id"]
    evidence, images = gathered
    visual = criterion.get("kind") == "visual"
    if visual:
        seeing = ctx.get("art_seeing", seeing)
    if visual and seeing and not images:
        images = await default_screenshots(ctx["root"], log)  # looks are judged by eye, so always have pictures
    shown = images if seeing else []
    log(f"{name}: judging ({len(shown)} screenshot(s))")
    if visual and shown:  # judged by eye only: the code would tell the judge what it means to draw, not how it looks
        verdict = await ask(ctx.get("art_judge", judge), ART_JUDGE_PROMPT,
                            json.dumps({"task": ctx["evidence"]["task"], "criterion": criterion},
                                       ensure_ascii=False), f"{name}: art judge", shown)
        verdict = cap_by_critique(criterion, verdict)
        result = to_result(criterion, verdict, evidence)
        result["critique"] = verdict.get("critique") or []
        return result
    if visual:  # source code or the final reply cannot replace the missing visual evidence
        return to_result(criterion, {"claims": []}, evidence)
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
    affected = {i: issue for issue in reversed(major) for i in issue.get("claims") or []
                if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(levels)}
    for row in models.items(verdict, "claims"):
        index = row.get("index") if isinstance(row, dict) else None
        if isinstance(index, int) and not isinstance(index, bool) and index in affected \
                and levels[index] == "challenge" and row.get("status") == "met":
            row["status"] = "partial"
            row["reason"] = (row.get("reason", "") + f"（主要问题：{affected[index].get('issue', '')}）").strip()
    return verdict

# the control that starts the work (not the first button, which is often a sound or settings toggle)
START_BUTTON = ", ".join([f'button:has-text("{t}")' for t in ("开始", "Start", "START", "start", "Play", "PLAY", "play",
                                                              "进入", "Begin", "Go")]
                         + ["#startBtn", "#start", ".start", "[data-action=start]"])


async def default_screenshots(root, log):
    """A full view and a close-up after pressing the start control (a button reading 开始 / Start / Play…) and
    Space, then seven more views after pressing the right
    arrow and scrolling one screen (the next pages of a book or slides, flipped or stacked in one long page), for a
    visual criterion whose checks took no screenshot. A work without a page is shown as its picture files.
    """
    pages = sorted((p for p in root.rglob("*.htm*") if checks.INPUTS_DIR not in p.parts),
                   key=lambda p: (len(p.relative_to(root).parts), str(p)))   # the work's own page first
    if not pages:  # a work made of pictures (book pages, a poster): look at the pictures themselves
        pictures = [p for p in root.rglob("*") if p.suffix.lower() in checks.IMAGE_SUFFIXES
                    and checks.INPUTS_DIR not in p.parts]
        if not pictures:
            return []
        try:
            return [(f"delivered picture {label}", data) for label, data in await checks.image_views(pictures)]
        except Exception as error:  # noqa: BLE001 - the looks stay unscored then
            log(f"picture views failed: {str(error)[:200]}")
            return []
    try:
        flips = [a for _ in range(7) for a in ({"type": "key", "key": "ArrowRight"}, {"type": "scroll", "pages": 1},
                                               {"type": "wait", "ms": 500})]
        _, shots = await checks.run_experiment(pages[0], {"actions": [
            {"type": "click", "selector": START_BUTTON}, {"type": "wait", "ms": 600},
            {"type": "key", "key": "Space"}, {"type": "wait", "ms": 900},
            {"type": "closeup", "selector": "canvas, body", "region": [0, 0.3, 0.5, 0.7]}, *flips],
            "snapshots": [3] + [7 + 3 * i for i in range(7)]})  # later views: pages, slides or game moments
    except Exception as error:  # noqa: BLE001 - the looks stay unscored then
        log(f"default screenshots failed: {str(error)[:200]}")
        return []
    return [(f"default view, {label}", data) for label, data in shots]

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


async def write_criteria(task, config, log=lambda message: None, knowledge="", producer=None,
                         previous_result=None):
    """Criteria from the Task alone (plus domain knowledge and an earlier result, if any); reusable to score several
    candidates alike. Claims without text are dropped, an unknown level counts as floor, a repeated id gets a
    suffix.
    """
    models.set_language(task)
    producer = producer or models.Model(config["producer"])
    log("writing criteria from the task")
    content = task if not knowledge else (
        f"{task}\n\n---\nDomain knowledge (use what fits this Task; it is not a template):\n{knowledge}")
    if previous_result:
        content += ("\n\n---\nEarlier evaluation of this Task (its criteria and how the work did):\n"
                    + json.dumps(earlier_rubric(previous_result), ensure_ascii=False))
    drafted = await ask(producer, CRITERIA_PROMPT.format(budget=config.get("criteria_budget", 3)), content, "criteria")
    criteria, ids = [], set()
    for n, c in enumerate(models.items(drafted, "criteria"), 1):
        claims = [{**x, "level": "challenge" if x.get("level") == "challenge" else "floor"}
                  for x in (c.get("claims") if isinstance(c, dict) else None) or []
                  if isinstance(x, dict) and isinstance(x.get("text"), str) and x["text"].strip()]
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


async def feedback_claims(task, user_note, process_signal, criteria, config, log=lambda message: None,
                          producer=None, notes=None):
    """The user's note and the process signal as checks placed in the rubric: each one goes to the existing
    criterion closest to its aspect (by id) and says which input it came from. A remark about one place becomes a
    rule for the whole work. [] when both are empty, or when the call failed (then a warning says so).
    """
    user_note, process_signal = (user_note or "").strip(), (process_signal or "").strip()
    if not (user_note or process_signal):
        return []
    rubric = [{"id": c["id"], "name": c.get("name", ""), "goal": c.get("goal", ""),
               **({"kind": "visual"} if c.get("kind") == "visual" else {}),
               "claims": [x.get("text", "") for x in c.get("claims") or []]} for c in criteria]
    found = await optional(producer or models.Model(config["producer"]), FEEDBACK_PROMPT, json.dumps(
            {"task": task, **({"user_note": user_note} if user_note else {}),
             **({"process_signal": process_signal} if process_signal else {}), "criteria": rubric},
            ensure_ascii=False), "把用户意见和过程信号写进评分标准", [] if notes is None else notes, log)
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


async def overall_summary(task, results, score, judge, log, notes):
    """The total verdict in two or three sentences, written by the judge from the category comments; when that
    call fails, one sentence made in code from the scores.
    """
    rows = [{"category": r.get("name") or r["id"], "score": r["score"], "comment": r.get("summary", "")}
            for r in results]
    found = await optional(judge, SUMMARY_PROMPT, json.dumps({"task": task, "total": score, "categories": rows},
                                                             ensure_ascii=False), "写总评", notes, log)
    if isinstance(found.get("summary"), str) and found["summary"].strip():
        return found["summary"].strip()
    scored = [r for r in results if r["score"] is not None]
    if not scored:
        return ""
    best, worst = max(scored, key=lambda r: r["score"]), min(scored, key=lambda r: r["score"])
    return (f"最强的是「{best.get('name') or best['id']}」（{best['score']}），"
            f"最弱的是「{worst.get('name') or worst['id']}」（{worst['score']}）。")


async def reward(task, artifacts, config, txt_response="", user_note="", process_signal="", rubric_path=None,
                 previous_result=None, log=lambda message: None, warnings=()):
    """Score delivered work; returns the standard result JSON (result.py).

    artifacts: the delivered files or directories (paths); [] when the answer is only in txt_response. They are
    copied into a scratch folder that is removed afterwards; the originals are never touched.
    txt_response: the agent's final text reply (scored when the answer is in it).
    user_note: the user's remarks on the work. process_signal: environment errors met during the run and the user's
    feedback across turns. Both become checks in the rubric, each in the criterion it concerns.
    rubric_path: a file that keeps the rubric (with those checks added so far), so later rounds are scored on the
    same criteria; without it every call writes new criteria. To start over with a kept file, move it aside first
    (archive_rubric).
    previous_result: an earlier result JSON of the same Task, used only when new criteria are written: they keep its
    floor claims and raise the challenge claims above what the work already met.
    """
    root = make_workspace(artifacts)
    try:
        return await score_workspace(task, root, config, txt_response=txt_response, log=log, warnings=warnings,
                                     rubric_path=rubric_path, user_note=user_note, process_signal=process_signal,
                                     previous_result=previous_result)
    finally:
        shutil.rmtree(root, ignore_errors=True)


async def score_workspace(task, root, config, txt_response="", criteria=None, log=lambda message: None,
                          warnings=(), rubric_path=None, user_note="", process_signal="", previous_result=None):
    """reward() on a scratch folder that already exists (deliverable at the top, inputs under _inputs/); criteria
    given by the caller are used as they are (batch runs).
    """
    response, notes = txt_response, list(warnings)
    models.set_language(task)
    root = Path(root)
    loop = asyncio.get_running_loop()
    limit = config.get("deadline_seconds")   # optional: the caller's own time limit for the whole run
    deadline = None if limit is None else loop.time() + float(limit)
    producer = models.Model(config["producer"])
    judge = models.Model(config.get("judge") or config["producer"])
    producer.deadline = judge.deadline = deadline
    # leave judging and the summary their time
    stop_at = None if deadline is None else deadline - min(getattr(judge, "quota", 360), 180)
    chosen = await guides.for_task(task, config, producer, log, notes) if not criteria else []
    knowledge = guides.as_text(chosen)
    if chosen:
        log(f"domain knowledge: {', '.join(name for name, _ in chosen)}")
    kept = load_rubric(rubric_path) if rubric_path and not criteria else None
    fixed = criteria is not None   # criteria given by the caller (batch runs) are used as they are
    if kept:
        log("reusing the kept rubric")
        criteria = ensure_visual([{**c, "scope": "terminal"} for c in kept["criteria"]
                                  if c.get("scope", "terminal") == "terminal"], task)
    elif not criteria:
        progress.current().stage("criteria")
        criteria = await write_criteria(task, config, log, knowledge, producer, previous_result)
    placed = [] if fixed else await feedback_claims(task, user_note, process_signal, criteria, config, log,
                                                   producer, notes)
    if placed:   # the user's concerns and the run's errors become part of the rubric, kept for the next rounds
        criteria = merge_feedback(criteria, placed)
        log(f"user note / process signal placed in: {', '.join(sorted({str(p['criterion']) for p in placed}))}")
    if rubric_path and not fixed and (not kept or placed):
        save_rubric(rubric_path, criteria)
    deliverable, inputs = workspace_files(root)
    tools = checks.available_tools(root, config)
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
    art.deadline = deadline
    ctx = {"root": root, "tools": tools, "evidence": evidence, "art_judge": art, "config": config,
           "plan_input": {"task": task, "available_tools": tools, "final_reply": response,
                          **({"domain_knowledge": knowledge} if knowledge else {}),
                          "workspace": {"files": {name: f"{text.count(chr(10)) + 1} lines"
                                                  for name, text in sorted(deliverable.items())},
                                        "inputs": sorted(inputs),
                                        **({"inputs_index": inputs_index(inputs)}
                                           if sum(map(len, inputs.values())) > INPUTS_INLINE else {}),
                                        "excerpts": bundle(readable) or "(page source is not shown)"}}}
    has_pictures = "browser" in tools or any(p.suffix.lower() in checks.IMAGE_SUFFIXES for p in root.rglob("*")
                                           if checks.INPUTS_DIR not in p.parts)

    async def sees(model):   # each role decides for itself whether it gets pictures
        return await models.can_see(model, log, notes) if has_pictures else True

    evidence_route = config.get("evidence_model") or config["producer"]
    seeing = await sees(judge)
    art_seeing = seeing if art is judge else await sees(art)
    same_as_judge = not isinstance(evidence_route, dict) or evidence_route.get("model") == judge.name
    evidence_seeing = seeing if same_as_judge else await sees(models.Model(evidence_route))
    ctx["art_seeing"], ctx["seeing"] = art_seeing, evidence_seeing
    if not seeing:
        log(f"ordinary judge {getattr(judge, 'name', '')} cannot read images; ordinary criteria use text and values")
    if not art_seeing:
        log(f"art judge {getattr(art, 'name', '')} cannot read images; visual criteria are unscored")
    if not evidence_seeing:
        log("evidence model cannot read images; tool results include text only, judges retain all screenshots")
    log(f"{len(criteria)} criteria: {', '.join(c['id'] for c in criteria)}; tools: {tools}")
    progress.current().stage("evidence", total=len(criteria))
    gathered = await evidence_agent.gather(   # one DeepAgent gathers evidence for every criterion
        criteria, ctx, evidence_route, log,
        int(config.get("evidence_rounds", evidence_agent.ROUNDS)),
        min(float(config.get("evidence_seconds", evidence_agent.SECONDS)),
            max(5.0, stop_at - loop.time()) if stop_at is not None else float("inf")))
    progress.current().stage("judging", total=len(criteria))
    jobs = [evaluate_terminal(c, ctx, producer, judge, log, gathered[c["id"]], seeing) for c in criteria]
    results = await asyncio.gather(*(progress.current().counted(job, c["id"]) for job, c in zip(jobs, criteria)))
    score = total(results)
    summary = await overall_summary(task, results, score, judge, log, notes)
    log("done")
    warnings = notes
    if not seeing:
        warnings.append(f"当前模型 {getattr(judge, 'name', '')} 不支持看图，普通维度只能根据文字和运行数值判定。")
    if not art_seeing:
        warnings.append(f"画面判定模型 {getattr(art, 'name', '')} 不支持看图，画面维度整项不计分、不计入总分；"
                        "换用支持图片的 art_judge 可评价这部分。")
    used = sorted({e.get("tool") for r in results for e in r.get("evidence") or []
                   if isinstance(e, dict) and e.get("tool") and "skipped" not in e})
    meta = {"deliverable": sorted(deliverable), "final_reply": bool(response), "inputs": sorted(inputs),
            **({"user_note": user_note} if user_note else {}),
            **({"process_signal": process_signal} if process_signal else {}),
            "models": {"producer": producer.name, "judge": judge.name,
                       **({"art_judge": art.name} if art is not judge else {})},
            "tools": {"available": tools, "used": used},
            **({"guides": [name for name, _ in chosen]} if chosen else {}),
            **({"warnings": warnings} if warnings else {})}
    result = build(task, results, score, summary, meta)
    if config.get("include_evidence"):
        result["evidence"] = {r["id"]: r.get("evidence") for r in results}
    return result


async def text_reward(task, deliverable, config, response="", references=None, criteria=None,
                      log=lambda message: None):
    """Score work given as text ({name: text}) with the same pipeline; used by the batch script."""
    root = make_workspace(texts=deliverable, reference_texts=references)
    try:
        return await score_workspace(task, root, config, txt_response=response, criteria=criteria, log=log)
    finally:
        shutil.rmtree(root, ignore_errors=True)
