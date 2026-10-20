"""The standard result JSON (meta, total, categories, sub_category_items) and the Markdown reply rendered from it.

    {"schema_version": "reward/3",
     "meta": {...},                                    # when, what was evaluated, with which models
     "total": {"score", "summary"},                   # 0-100 and the overall verdict (model-written)
     "categories": [{"category_id", "category_name", "rubric", "comment", "score", "issues"}],
     "sub_category_items": [{"sub_category_id", "category_id", "category_name", "rubric", "comment", "score",
                             "status", "type", "gain", "from_user_note", "from_process_signal"}]}

Scores are 0-100; null means no evidence, not zero. "status" is how far a sub-item was achieved: met (fully, 100),
partial (in part, 50), unmet (not, 0), unknown (no evidence either way, null). "issues" are the major visual
weaknesses the art judge saw (looks only). "gain" is how many points the total would rise if the item were met;
"type" is "floor" (asked for by the Task) or "proactive_challenge" (what the best work does unasked).
"from_user_note" / "from_process_signal" mark the sub-items made from the user's note / the process signal.
One result describes one evaluation: no earlier scores are kept here. Nothing here proposes
changes: that is the job of whoever reads this result (e.g. fast_attribution).
"""

from datetime import datetime, timezone
from pathlib import Path

from .scoring import FLOOR_FAIL_CAP, FLOOR_WEIGHT, claim_gains

SCHEMA_VERSION = "reward/3"

ITEM_SCORE = {"met": 100, "partial": 50, "unmet": 0, "unknown": None}

SCORING_RULE = (f"每个维度 0–100：基础项占 {round(100 * FLOOR_WEIGHT)}%，主动挑战项占 {round(100 * (1 - FLOOR_WEIGHT))}%"
                f"（达成 1、部分 0.5、未达成 0；无证据的项不计入）；基础项有未达成时该维度最多 {FLOOR_FAIL_CAP} 分；"
                "基础项无证据不计入；整个维度都没有证据时不计分；总分是有分数维度的平均分。")


def fallback_comment(result):
    """A category comment from the item reasons, when the judge gave none."""
    bad = [c["reason"] for c in result["claims"] if c["status"] in ("unmet", "partial") and c.get("reason")]
    good = [c["reason"] for c in result["claims"] if c["status"] == "met" and c.get("reason")]
    return "；".join((bad or good)[:2])


def build(task, results, total_score, summary, meta):
    """The result dict. results: the scored criteria (scoring.to_result plus "critique" for visual ones)."""
    gains = claim_gains(results)
    categories, items = [], []
    for r in results:
        name = r.get("name") or r["id"]
        categories.append({"category_id": r["id"], "category_name": name, "rubric": r.get("goal", ""),
                           "comment": r.get("summary") or fallback_comment(r), "score": r["score"],
                           "issues": [i["issue"] for i in r.get("critique") or []
                                      if isinstance(i, dict) and i.get("issue") and i.get("severity") == "major"]})
        for index, c in enumerate(r["claims"]):
            ref = f"{r['id']}#{index}"
            items.append({"sub_category_id": ref, "category_id": r["id"], "category_name": name, "rubric": c["text"],
                          "comment": c.get("reason", ""), "score": ITEM_SCORE[c["status"]], "status": c["status"],
                          "type": "floor" if c.get("level", "floor") == "floor" else "proactive_challenge",
                          "gain": gains.get(ref, 0.0), "from_user_note": c.get("source") == "feedback",
                          "from_process_signal": c.get("source") == "process_signal"})
    return {"schema_version": SCHEMA_VERSION,
            "meta": {"evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "task": task,
                     "scoring": SCORING_RULE, **meta},
            "total": {"score": total_score, "summary": summary},
            "categories": categories, "sub_category_items": items}


# ---------- Markdown ----------

MARK = {"met": "✓", "partial": "◐", "unmet": "✗", "unknown": "?"}

MAIN = (".html", ".htm", ".md", ".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".txt", ".json")


def shown(value):
    return "无证据" if value is None else f"{value} / 100"


def files_shown(files, most=4):
    """The deliverable files for the reply; a long list shows its main files and a count."""
    if not files:
        return "无"
    if len(files) <= most:
        return "、".join(files)
    main = [f for f in files if "/" not in f and Path(f).suffix.lower() in MAIN] or [f for f in files if "/" not in f]
    return "、".join((main or files)[:2]) + f" 等 {len(files)} 个文件"


def render(result, report_path=None):
    """The result as short Markdown for the reply: total and verdict, inputs, each category with its comment and
    the items it did not meet.
    """
    meta, total = result["meta"], result["total"]
    lines = [f"**总分 {shown(total['score'])}**"]
    if total.get("summary"):
        lines.append(total["summary"])
    lines += [f"⚠️ {w}" for w in meta.get("warnings") or []]

    parts = ["任务描述", "交付物 " + files_shown(meta.get("deliverable") or [])]
    if meta.get("final_reply"):
        parts.append("最终回复")
    if meta.get("user_note"):
        parts.append(f"用户建议「{meta['user_note']}」")
    if meta.get("process_signal"):
        signal = meta["process_signal"]
        parts.append(f"过程信号「{signal[:80]}{'…' if len(signal) > 80 else ''}」")
    if meta.get("inputs"):
        parts.append(f"输入材料 {len(meta['inputs'])} 个")
    models = meta.get("models") or {}
    if models:
        names = [models.get("producer"), models.get("judge"), models.get("art_judge")]
        parts.append("模型 " + "、".join(dict.fromkeys(n for n in names if n)))
    if meta.get("guides"):
        parts.append("评测知识 " + "、".join(meta["guides"]))
    lines += ["", "**输入**：" + "；".join(parts)]

    lines += ["", "**各维度**"]
    by_category = {}
    for item in result["sub_category_items"]:
        by_category.setdefault(item["category_id"], []).append(item)
    for n, c in enumerate(result["categories"], 1):
        lines.append(f"{n}. {c['category_name']} **{shown(c['score'])}**：{c['rubric']}")
        if c.get("comment"):
            lines.append(f"   评价：{c['comment']}")
        for item in by_category.get(c["category_id"], []):
            if item["status"] != "met":
                lines.append(f"   - {MARK[item['status']]} {item['rubric']}"
                             + ("（用户建议）" if item.get("from_user_note") else "")
                             + ("（过程信号）" if item.get("from_process_signal") else "")
                             + (f"——{item['comment']}" if item.get("comment") else ""))
        for issue in (c.get("issues") or [])[:3]:
            lines.append(f"   - 画面问题：{issue}")
    lines += ["（✗ 未达成 ◐ 部分达成 ? 无证据；已达成的子项不列出）", "", f"计分：{meta.get('scoring', SCORING_RULE)}"]
    if report_path:
        lines += ["", f"详细报告：{report_path}"]
    return "\n".join(lines)
