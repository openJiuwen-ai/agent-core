"""Domain-gated revision text for one scored deliverable.

The suggestion reads the score it is given, it does not score again. A personal-assistant
result stays with attribution. Only a result whose single selected guide is game
receives this text. One model call sees what the scoring saw: the screenshots, the
check receipts, the deliverable source and the guide.
"""

from __future__ import annotations

import json

from . import models
from .next_version_prompt import PROMPT

DEFAULT_DOMAINS = ["game"]
SCREENSHOTS = 8          # pictures handed to the designer, spread over the run
CHECK_OUTPUT = 1500      # characters kept from one check's output
SOURCE_CHARS = 120_000   # characters of deliverable source in one request


def suggestion_policy(config, guides):
    """Enable suggestion only when the single selected guide is an allowed domain."""
    setting = (config or {}).get("suggestion") or {}
    if not isinstance(setting, dict):
        raise TypeError("suggestion must be an object")
    unknown = set(setting) - {"enabled", "domains"}
    if unknown:
        raise ValueError("unknown suggestion fields: " + ", ".join(sorted(unknown)))
    enabled = setting.get("enabled", True)
    domains = setting.get("domains", DEFAULT_DOMAINS)
    if type(enabled) is not bool:
        raise TypeError("suggestion.enabled must be a boolean")
    if not isinstance(domains, list) or any(not isinstance(name, str) or not name.strip() for name in domains):
        raise TypeError("suggestion.domains must be a list of nonempty domain identifiers")
    names = []
    for item in guides or []:
        name = item[0] if isinstance(item, (list, tuple)) and item else item
        if isinstance(name, str) and name not in names:
            names.append(name)
    domain = names[0] if len(names) == 1 else None
    if not enabled:
        reason = "disabled"
    elif domain is None:
        reason = "domain_unknown"
    elif domain not in domains:
        reason = "domain_not_enabled"
    else:
        reason = None
    return {"enabled": enabled, "domain": domain, "domains": list(domains),
            "status": "skipped" if reason else "enabled", "reason": reason}


def spread(shots, count=SCREENSHOTS):
    """At most `count` screenshots, from the opening to the last one taken."""
    shots = list(shots or [])
    if len(shots) <= count:
        return shots
    step = (len(shots) - 1) / (count - 1)
    return [shots[round(i * step)] for i in range(count)]


def _claims(result):
    rows = []
    for item in result.get("sub_category_items") or []:
        if not isinstance(item, dict):
            continue
        rows.append({
            "rubric": str(item.get("rubric") or ""),
            "status": item.get("status"),
            "comment": str(item.get("comment") or ""),
            "type": item.get("type"),
        })
    return rows


def _checks(result):
    """The scoring's check receipts, each output clipped; the same check serving several criteria is listed once."""
    seen, rows = set(), []
    for facts in (result.get("evidence") or {}).values():
        for row in facts or []:
            if not isinstance(row, dict):
                continue
            key = json.dumps(row, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            output = row.get("output")
            if isinstance(output, str) and len(output) > CHECK_OUTPUT:
                row = {**row, "output": output[:CHECK_OUTPUT] + f"[cut {len(output) - CHECK_OUTPUT} characters]"}
            rows.append(row)
    return rows


def _source(deliverable):
    texts, left = {}, SOURCE_CHARS
    for name, text in sorted((deliverable or {}).items()):
        if not isinstance(text, str) or left <= 0:
            continue
        texts[name] = text if len(text) <= left else text[:left] + f"[cut {len(text) - left} characters]"
        left -= len(texts[name])
    return texts


def _line(item, zh):
    labels = (("gain", "收益" if zh else "gain"), ("existing", "已有" if zh else "existing"),
              ("delta", "新增变化" if zh else "delta"))
    tail = ("；" if zh else "; ").join(f"{label}：{item[key]}" if zh else f"{label}: {item[key]}"
                                     for key, label in labels if item.get(key))
    evidence = (f"（{item['evidence']}）" if zh else f" ({item['evidence']})") if item.get("evidence") else ""
    return f"- {item.get('idea', '')}{evidence}" + (("。" if zh else ". ") + tail if tail else "")


def _design_brief(advice, zh):
    plan = advice["design_plan"]
    target = "\n".join("> " + line for line in str(plan.get("target", "")).strip().split("\n"))
    parts = ["## " + ("本轮主设计" if zh else "Main design") + "\n" + target,
             "## " + ("优先变化" if zh else "Priority changes")]
    evidence, checks = [], []
    for key, label in (("improve", ""), ("explore", "探索：" if zh else "Explore: ")):
        for item in advice.get(key) or []:
            lines = ["### " + label + str(item.get("idea", ""))]
            for field, name in (("delta", "具体改变" if zh else "Change"),
                                ("gain", "用户收益" if zh else "User benefit"),
                                ("risk", "风险与取舍" if zh else "Risk and tradeoff")):
                if item.get(field):
                    lines.append(name + ": " + str(item[field]))
            parts.append("\n\n".join(lines))
            facts = [name + ": " + str(item[field]) for field, name in (
                ("existing", "当前基线" if zh else "Baseline"), ("evidence", "依据" if zh else "Evidence"))
                if item.get(field)]
            if facts:
                evidence.append("### " + str(item.get("idea", "")) + "\n\n" + "\n\n".join(facts))
            if item.get("check"):
                checks.append("- " + str(item.get("idea", "")) + "：" + str(item["check"]))
    parts.append("## " + ("实现顺序与技术方案" if zh else "Implementation order and approach")
                 + "\n" + str(plan.get("realization", "")))
    keep = [_line(item, zh) for item in advice.get("preserve") or []]
    if keep:
        parts.append("## " + ("保留" if zh else "Keep") + "\n" + "\n".join(keep))
    appendix = ["## " + ("依据与验证" if zh else "Evidence and validation")]
    appendix += evidence
    appendix.append("### " + ("整体效果如何验证" if zh else "How to validate the overall effect")
                    + "\n" + str(plan.get("validation", "")))
    if checks:
        appendix.append("### " + ("各项补充验证" if zh else "Item-specific validation") + "\n" + "\n".join(checks))
    parts.append("\n\n".join(appendix))
    return "\n\n".join(parts)


def render(advice, language="zh") -> str:
    """The next-version design the existing diagnosis card hands to the worker."""
    zh = str(language).lower().startswith("zh")
    if isinstance(advice.get("design_plan"), dict):
        return _design_brief(advice, zh)
    groups = (("保留长处" if zh else "Preserve", advice.get("preserve") or []),
              ("改善不足" if zh else "Improve", advice.get("improve") or []),
              ("探索可能" if zh else "Explore", advice.get("explore") or []))
    text = "\n\n".join("## " + heading + "\n" + "\n".join(_line(item, zh) for item in items)
                       for heading, items in groups if items)
    if not text:
        raise ValueError("suggestion returned no design_plan, preserve, improve or explore")
    return text


async def suggest(result, model, language="zh", materials=None, final_reply="") -> str:
    """Read the score and what the scoring kept in `materials`, then ask for the next-version design."""
    if not isinstance(result, dict):
        raise ValueError("reward evidence result must be an object")
    materials = materials or {}
    images = spread(materials.get("screenshots"))
    seeing = bool(images) and await models.can_see(model)
    payload = {
        "task": (result.get("meta") or {}).get("task") or "",
        "metric": materials.get("knowledge") or "",
        "summary": (result.get("total") or {}).get("summary") or "",
        "claims": _claims(result),
        "checks": _checks(result),
        "final_reply": final_reply or "",
        "deliverable_text": _source(materials.get("deliverable")),
        "seeing_screenshots": seeing,
        "screenshots": {"chosen": [label for label, _ in images] if seeing else []},
        "evidence_policy": "Missing evidence is unknown, not a defect. Address established problems and record "
                           "remaining uncertainties as validation needs.",
    }
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    advice = await (model.json(PROMPT, content, images) if seeing else model.json(PROMPT, content))
    if not isinstance(advice, dict):
        raise ValueError("suggestion must return one JSON object")
    return render(advice, language)
