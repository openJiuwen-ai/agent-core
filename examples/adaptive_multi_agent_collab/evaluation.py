from __future__ import annotations
import random, statistics
from collections import Counter, defaultdict; from typing import Any, Mapping, Sequence
from .weighting import (ANSWER_LABELS, SignedHashEncoder, WeightingModel, _agent_id, _dict,
                        infer_weights, support_components, trajectory_terminal_answers)
def plurality_vote(answers: Sequence[str], agent_ids: Sequence[int] | None = None,
                   return_tie: bool = False) -> str | tuple[str, bool]:
    pairs = sorted(zip(agent_ids or range(len(answers)), (str(x).upper() for x in answers)))
    valid = [(agent, answer) for agent, answer in pairs if answer in ANSWER_LABELS]
    if not valid:
        raise ValueError("No valid answers to aggregate")
    counts = Counter(answer for _, answer in valid)
    tied = {answer for answer, count in counts.items() if count == max(counts.values())}
    result = next(answer for _, answer in valid if answer in tied), len(tied) > 1
    return result if return_tie else result[0]
def weighted_vote(scores: Mapping[str, float] | Sequence[float], agent_answers: Sequence[str],
                  tolerance: float = 1e-8, return_tie: bool = False) -> str | tuple[str, bool]:
    values = dict(scores) if isinstance(scores, Mapping) else dict(zip(ANSWER_LABELS, scores))
    maximum = max(values.values())
    tied = {label for label in ANSWER_LABELS if maximum - values.get(label, float("-inf")) <= tolerance}
    prediction = next((str(x).upper() for x in agent_answers if str(x).upper() in tied), min(tied))
    result = prediction, len(tied) > 1
    return result if return_tie else prediction
def classify_transition(initial: str, terminal: str, gold: str, detailed: bool = False) -> str:
    initial, terminal, gold = initial.upper(), terminal.upper(), gold.upper()
    if initial != terminal:
        if initial != gold and terminal == gold:
            return "incorrect -> correct"
        if initial == gold and terminal != gold:
            return "correct -> incorrect"
        return "incorrect -> incorrect with changed label"
    return ("correct unchanged" if initial == gold else "incorrect unchanged") if detailed else "unchanged"
def bootstrap_accuracy_ci(outcomes: Sequence[bool], seed: int = 42,
                          samples: int = 2000) -> tuple[float | None, float | None]:
    if not outcomes:
        return None, None
    rng, size = random.Random(seed), len(outcomes)
    values = sorted(sum(outcomes[rng.randrange(size)] for _ in range(size)) / size for _ in range(samples))
    return values[int(.025 * (samples - 1))], values[int(.975 * (samples - 1))]
def _turns(data: dict[str, Any]) -> dict[int, dict[str, Any]]:
    raw = data.get("initial_turns", data.get("initial_answers", {}))
    items = raw.items() if isinstance(raw, Mapping) else enumerate(raw)
    return {_agent_id(key if isinstance(raw, Mapping) else item.get("agent_id", key)): _dict(item) for key, item in items}
def _calls(data: dict[str, Any]) -> list[dict[str, Any]]:
    if calls := [_dict(item) for item in data.get("calls", [])]:
        return calls
    calls = [_dict(turn.get("call", turn)) for turn in _turns(data).values()]
    for raw in data.get("conversations", []):
        nested = _dict(raw).get("calls", [])
        calls.extend(_dict(x) for x in (nested.values() if isinstance(nested, Mapping) else nested))
    return calls
def attribute_usage(trajectory: Any, method: str) -> dict[str, float | int | None]:
    calls = _calls(_dict(trajectory))
    if method == "single_agent":
        calls = [x for x in calls if x.get("stage") == "initial" and _agent_id(x.get("agent_id", 0)) == 0]
    elif method == "independent_majority":
        calls = [x for x in calls if x.get("stage") == "initial"]
    usage = [_dict(call.get("usage_metadata", call.get("usage", {}))) for call in calls]
    def total(field: str, require_evidence: bool = False) -> float | None:
        observed = []
        for item in usage:
            value = item.get(field, item.get("cached_tokens") if field == "cache_tokens" else None)
            provided = field in set(item.get("_provided_fields", ()))
            if value is not None and (not require_evidence or provided or float(value) != 0):
                observed.append(float(value))
        return sum(observed) if observed else None
    result = {field: total(field) for field in ("input_tokens", "output_tokens", "total_tokens", "cache_tokens")}
    result.update(calls=sum(int(x.get("attempts", x.get("attempt", 1))) for x in calls),
                  total_cost=total("total_cost", True), provider_latency=total("total_latency", True))
    wall = sum(float(x.get("wall_latency", x.get("latency", 0)) or 0) for x in calls)
    result["wall_latency"] = result["latency"] = wall
    parsed = [_dict(call.get("parsed", {})) for call in calls]
    methods = [str(call.get("parse_method", item.get("parse_method", ""))) for call, item in zip(calls, parsed)]
    failures = [bool(call.get("error", item.get("parse_error"))) and
                not (item.get("answer") or item.get("recommended_answer")) for call, item in zip(calls, parsed)]
    result["parse_fallback_rate"] = sum(x in {"explicit_marker", "isolated_label"} for x in methods) / len(calls) if calls else None
    result["parse_failure_rate"] = sum(failures) / len(calls) if calls else None
    return result
def _method_metrics(rows: list[dict[str, Any]], method: str, total: int, seed: int) -> dict[str, Any]:
    valid = [row for row in rows if row.get(method) in ANSWER_LABELS]
    outcomes, usage = [row[method] == row["gold"] for row in valid], [row["usage"][method] for row in valid]
    low, high = bootstrap_accuracy_ci(outcomes, seed)
    def average(field: str) -> float | None:
        values = [item[field] for item in usage if item.get(field) is not None]
        return sum(values) / len(values) if values else None
    result = {"correct": sum(outcomes), "evaluated": len(outcomes), "failed": total - len(outcomes),
              "accuracy": sum(outcomes) / len(outcomes) if outcomes else None, "bootstrap_95_ci": [low, high]}
    for source, target in (("calls", "average_calls"), ("input_tokens", "average_input_tokens"),
                           ("output_tokens", "average_output_tokens"), ("total_tokens", "average_total_tokens"),
                           ("cache_tokens", "average_cached_tokens"), ("total_cost", "average_total_cost"),
                           ("wall_latency", "average_wall_latency"), ("provider_latency", "average_provider_latency"),
                           ("latency", "average_latency"), ("parse_fallback_rate", "parse_fallback_rate"),
                           ("parse_failure_rate", "parse_failure_rate")):
        result[target] = average(source)
    result["tie_rate"] = sum(bool(row["ties"].get(method)) for row in valid) / len(valid) if valid else None
    return result
def _weighted_scores(terminals: Sequence[Sequence[str]], weights: Sequence[float]) -> list[float]:
    support = [support_components(labels)[2] for labels in terminals]
    return [sum(weights[agent] * support[agent][candidate].item() for agent in range(3)) for candidate in range(5)]
def _weight_group(rows: Sequence[dict[str, Any]], disagreement: bool) -> dict[str, Any]:
    selected = [row for row in rows if (len(set(x for x in row["terminal_answers"] if x)) > 1) == disagreement]
    return {"count": len(selected), "average_by_agent": [
        statistics.fmean(row["weights"][agent] for row in selected) if selected else None for agent in range(3)]}
def evaluate_trajectories(trajectories: Sequence[Any], weighting_model: WeightingModel | None = None,
                          encoder: SignedHashEncoder | None = None, seed: int = 42) -> dict[str, Any]:
    del encoder
    rows, counts, examples = [], defaultdict(Counter), defaultdict(lambda: defaultdict(list))
    initial_agent, terminal_agent, elapsed = defaultdict(list), defaultdict(list), []
    for trajectory in trajectories:
        data, terminals = _dict(trajectory), trajectory_terminal_answers(trajectory)
        turns, example = _turns(data), _dict(data.get("example", data))
        gold = str(example.get("gold_label", example.get("gold_answer", ""))).upper()
        if gold not in ANSWER_LABELS:
            continue
        initial = [str(turns.get(i, {}).get("answer", turns.get(i, {}).get("parsed_answer", ""))).upper() for i in range(3)]
        terminal = [plurality_vote(labels) if labels else "" for labels in terminals]
        supplied = [answer for labels in terminals for answer in labels]
        try:
            majority, majority_tie = plurality_vote(initial, return_tie=True)
            uniform, uniform_tie = weighted_vote(_weighted_scores(terminals, [1 / 3] * 3), supplied, return_tie=True)
        except ValueError:
            majority = uniform = ""
            majority_tie = uniform_tie = False
        learned, learned_tie, weights = "", False, [1 / 3] * 3
        if weighting_model and any(terminals):
            weights, seconds = infer_weights(weighting_model, trajectory)
            elapsed.append(seconds)
            learned, learned_tie = weighted_vote(_weighted_scores(terminals, weights), supplied, return_tie=True)
        identifier = str(example.get("example_id", example.get("id", "")))
        for agent in range(3):
            if initial[agent] in ANSWER_LABELS:
                initial_agent[agent].append(initial[agent] == gold)
            if terminal[agent] in ANSWER_LABELS:
                terminal_agent[agent].append(terminal[agent] == gold)
            if initial[agent] in ANSWER_LABELS and terminal[agent] in ANSWER_LABELS:
                kind = classify_transition(initial[agent], terminal[agent], gold)
                counts[agent][kind] += 1
                if len(examples[agent][kind]) < 3:
                    examples[agent][kind].append({"example_id": identifier, "initial": initial[agent],
                                                  "terminal": terminal[agent], "gold": gold})
        methods = ("single_agent", "independent_majority", "collaboration_uniform", "collaboration_learned")
        rows.append({"example_id": identifier, "gold": gold, "single_agent": initial[0], "independent_majority": majority,
                     "collaboration_uniform": uniform, "collaboration_learned": learned, "initial_oracle": gold in initial,
                     "terminal_oracle": gold in supplied, "initial_answers": initial, "terminal_answers": terminal,
                     "weights": weights, "ties": {"independent_majority": majority_tie,
                     "collaboration_uniform": uniform_tie, "collaboration_learned": learned_tie},
                     "usage": {method: attribute_usage(trajectory, method) for method in methods}})
    methods = {name: _method_metrics(rows, name, len(trajectories), seed) for name in
               ("single_agent", "independent_majority", "collaboration_uniform", "collaboration_learned")}
    weight_values = {row["example_id"]: row["weights"] for row in rows}
    transition_percentages = {str(agent): {kind: count / sum(agent_counts.values()) for kind, count in agent_counts.items()}
                              for agent, agent_counts in counts.items()}
    return {"predictions": rows, "methods": methods,
            "oracles": {"label": "ORACLE DIAGNOSTIC - NOT A DEPLOYABLE BASELINE",
                        "initial_accuracy": statistics.fmean(row["initial_oracle"] for row in rows) if rows else None,
                        "terminal_accuracy": statistics.fmean(row["terminal_oracle"] for row in rows) if rows else None},
            "agents": {str(i): {"initial_accuracy": statistics.fmean(initial_agent[i]) if initial_agent[i] else None,
                               "terminal_accuracy": statistics.fmean(terminal_agent[i]) if terminal_agent[i] else None}
                       for i in range(3)},
            "agreement": {"initial_disagreement_rate": statistics.fmean(len(set(x["initial_answers"])) > 1 for x in rows) if rows else None,
                          "terminal_disagreement_rate": statistics.fmean(len(set(x for x in row["terminal_answers"] if x)) > 1 for row in rows) if rows else None,
                          "initial_unanimous_rate": statistics.fmean(len(set(x["initial_answers"])) == 1 for x in rows) if rows else None,
                          "terminal_unanimous_rate": statistics.fmean(len(set(x for x in row["terminal_answers"] if x)) == 1 for row in rows) if rows else None},
            "transitions": {str(agent): dict(value) for agent, value in counts.items()},
            "transition_percentages": transition_percentages,
            "transition_examples": {str(agent): dict(value) for agent, value in examples.items()},
            "weights": {"per_query": weight_values,
                        "average_by_agent": [statistics.fmean(x[i] for x in weight_values.values()) if weight_values else None for i in range(3)],
                        "stdev_by_agent": [statistics.pstdev(x[i] for x in weight_values.values()) if weight_values else None for i in range(3)],
                        "by_terminal_disagreement": {"agreement": _weight_group(rows, False),
                                                    "disagreement": _weight_group(rows, True)},
                        "inference_seconds": sum(elapsed)}}
