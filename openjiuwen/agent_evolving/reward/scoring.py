"""Scoring in code: claim statuses to 0-100 per criterion and in total, and what each unmet claim costs."""

from .models import items

STATUSES = ("met", "partial", "unmet", "unknown")

VALUE = {"met": 1.0, "partial": 0.5, "unmet": 0.0}

FLOOR_WEIGHT = 0.5     # the rest goes to (proactive) challenge claims; floor met, challenge partial = 75

FLOOR_FAIL_CAP = 25    # any floor claim unmet caps the criterion here


def is_floor(claim):
    return claim.get("level", "floor") == "floor"


def score(criterion, statuses):
    """0-100 from claim statuses, computed in code.

    Floor and challenge claims each average met=1, partial=0.5, unmet=0 and are weighted
    FLOOR_WEIGHT : 1 - FLOOR_WEIGHT. An unknown claim, floor or challenge, is left out: a criterion whose
    challenge claims are all unknown is scored on its floor claims alone.
    A criterion with no evidence for any claim is unscored (None): nothing was seen, so nothing is scored.
    A failed floor claim caps the score at FLOOR_FAIL_CAP.
    """
    if not statuses or all(s == "unknown" for s in statuses):
        return None
    floors = [is_floor(c) for c in criterion.get("claims") or []]
    floor = [VALUE[s] for s, f in zip(statuses, floors) if f and s != "unknown"]
    challenge = [VALUE[s] for s, f in zip(statuses, floors) if not f and s != "unknown"]
    floor_part = sum(floor) / len(floor) if floor else 0.0
    challenge_part = sum(challenge) / len(challenge) if challenge else 0.0
    if not floor:
        value = round(100 * challenge_part)  # a criterion with only challenge claims (or unknown floors)
    elif not challenge:
        value = round(100 * floor_part)  # only floor claims settled, e.g. a rubric of requirements only
    else:
        value = round(100 * (FLOOR_WEIGHT * floor_part + (1 - FLOOR_WEIGHT) * challenge_part))
    if any(s == "unmet" for s, f in zip(statuses, floors) if f):
        value = min(value, FLOOR_FAIL_CAP)
    return value


def total(results):
    """0-100: the mean of the scored criteria; None when none could be scored."""
    scored = [r["score"] for r in results if r["score"] is not None]
    return round(sum(scored) / len(scored)) if scored else None


def to_result(criterion, verdict, evidence):
    rows = {row.get("index"): row for row in items(verdict, "claims") if isinstance(row, dict)}
    claims = []
    for index, claim in enumerate(criterion["claims"]):
        row = rows.get(index, {})
        status = row.get("status") if row.get("status") in STATUSES else "unknown"
        claims.append({**claim, "status": status, "reason": row.get("reason", "")})
    summary = verdict.get("summary") if isinstance(verdict, dict) else ""
    return {"id": criterion["id"], "name": criterion.get("name", ""),
            **({"kind": criterion["kind"]} if criterion.get("kind") else {}),
            "goal": criterion.get("goal", ""),
            "score": score(criterion, [c["status"] for c in claims]),
            "summary": summary if isinstance(summary, str) else "",
            "claims": claims, "evidence": evidence}


def claim_gains(results):
    """{"<criterion id>#<index>": points the total would rise} for each claim not met (unknown claims included: what
    showing it met would add).
    """
    scored = [r for r in results if r["score"] is not None]
    gains = {}
    for r in scored:
        statuses = [c["status"] for c in r["claims"]]
        for index, claim in enumerate(r["claims"]):
            if claim["status"] != "met":
                raised = score(r, statuses[:index] + ["met"] + statuses[index + 1:])
                gains[f"{r['id']}#{index}"] = round((raised - r["score"]) / len(scored), 1)
    return gains
