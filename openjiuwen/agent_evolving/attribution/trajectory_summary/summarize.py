# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Node-based summaries and evidence-backed problem updates in the same model calls."""

import asyncio
import json
import time
from dataclasses import asdict, dataclass, replace
from typing import Any

from openjiuwen.agent_evolving.attribution.events import emit_single_llm_event
from openjiuwen.agent_evolving.attribution.nodes import TrajectoryNode, user_request_text
from openjiuwen.agent_evolving.attribution.task_parse import _arguments, _response_tool_calls
from openjiuwen.core.common.logging import logger
from .models import (
    AgentSummary, ProblemLocation, ProblemRecord, ProblemSummary,
    SummaryLimits, SummaryTree, public_summary,
)
from .prepare import bounds, node_text, prepare
from .prompts import summary_prompts

_KINDS = ["tool_error", "user_correction", "retry", "conflict", "other"]
_STATES = ["unresolved", "resolved", "unknown"]
_TEXT = {"type": "string", "minLength": 1}
_IDS = {"type": "array", "items": {"type": "string"}}
_NONEMPTY_IDS = {**_IDS, "minItems": 1}
_LEAF = {"type": "object", "properties": {
    "kind": {"type": "string", "enum": _KINDS}, "summary": _TEXT, "node_ids": _NONEMPTY_IDS,
    "state": {"type": "string", "enum": _STATES}, "resolution_node_ids": _IDS,
}, "required": ["kind", "summary", "node_ids"]}
_MERGED = {"type": "object", "properties": {
    "kind": {"type": "string", "enum": _KINDS}, "summary": _TEXT,
    "source_problem_ids": {
        **_NONEMPTY_IDS,
        "description": (
            "Copy complete leaf source_problem_ids from the supplied issues; "
            "never use node IDs or invent sources. Omit a new unsupported issue instead."
        ),
    },
    "state": {"type": "string", "enum": _STATES}, "resolution_node_ids": _IDS,
}, "required": ["summary", "source_problem_ids", "state"]}


async def _parallel(jobs: list) -> list:
    tasks = [asyncio.create_task(job) for job in jobs]
    try:
        return await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@dataclass
class _MergeResult:
    narrative: str
    status: str
    problems: list[ProblemSummary]
    display_summary: str = ""


def _problem_input(problem: ProblemSummary) -> dict:
    """Model-facing merge evidence; keep full provenance in the original record."""
    return {"kind": problem.kind, "summary": problem.summary, "state": problem.state,
            "source_problem_ids": list(problem.source_problem_ids),
            "resolution_node_ids": list(problem.resolution_node_ids),
            "locations": [{"agent_id": loc.agent_id, "turn_id": loc.turn_id,
                           "segment_id": loc.segment_id, "node_ids": list(loc.node_ids)}
                          for loc in problem.locations]}


def _context_overflow(error: Exception) -> bool:
    text = str(error).lower()
    markers = (
        "context_length_exceeded", "maximum context length", "context window",
        "too many tokens", "input too long", "prompt is too long", "上下文长度",
    )
    return any(part in text for part in markers)


def _finish_reason(response: Any) -> str | None:
    if isinstance(response, dict):
        choices = response.get("choices") or []
        return response.get("finish_reason") or (choices[0].get("finish_reason") if choices else None)
    return getattr(response, "finish_reason", None)


def _shorten(value: Any) -> tuple[Any, bool]:
    """Last resort after a provider context rejection; never remove nodes or identity fields."""
    if isinstance(value, list):
        children = [_shorten(item) for item in value]
        return [item for item, _ in children], any(cut for _, cut in children)
    if not isinstance(value, dict):
        return value, False
    result, fields, changed = {}, [], False
    for key, item in value.items():
        long_fields = {"content", "arguments", "text", "narrative", "summary", "old_string", "new_string"}
        if key in long_fields and isinstance(item, str) and len(item) > 8192:
            result[key] = item[:4096] + "\n[context overflow: middle text omitted]\n" + item[-4096:]
            fields.append(key)
            changed = True
        else:
            result[key], cut = _shorten(item)
            changed = changed or cut
    if fields:
        result["truncated_fields"] = fields
    return result, changed


class _Runner:
    def __init__(self, model: Any, tree: SummaryTree, limits: SummaryLimits, language: str,
                 status_sink: Any, model_name: str, counter: list[int], primary: dict) -> None:
        self.model, self.tree, self.limits = model, tree, limits
        self.prompts = summary_prompts(language)
        self.language = self.prompts["language"]
        self.sink, self.model_name, self.counter = status_sink, model_name, counter
        self.semaphore = asyncio.Semaphore(limits.concurrency)
        self.nodes = {n.node_id: n for group in primary.values() for n in group}

    def warn(self, code: str, message: str, segment_id: str | None = None) -> None:
        item = {"code": code, "message": message or code}
        if segment_id:
            item["segment_id"] = segment_id
        self.tree.warnings.append(item)

    async def call(self, purpose: str, payload: Any, properties: dict, required: list[str], validate: Any) -> tuple:
        stage = purpose.split(":", 1)[0]
        prompts = summary_prompts(self.language, stage)
        tool = {"type": "function", "function": {"name": "report_summary", "description": prompts["report"],
                "parameters": {"type": "object", "properties": properties, "required": required}}}
        feedback, shortened = "", False
        async with self.semaphore:
            for attempt in range(self.limits.retries + 1):
                started, error = time.time_ns(), None
                response = None
                try:
                    content = {"purpose": purpose, "input": payload,
                               "output_guidance": prompts["guidance"][stage],
                               "retry_feedback": feedback}
                    response = await asyncio.wait_for(self.model.invoke([
                        {"role": "system", "content": prompts["system"]},
                        {"role": "user", "content": json.dumps(content, ensure_ascii=False)},
                    ], tools=[tool], temperature=0.0), timeout=self.limits.timeout_s)
                    if _finish_reason(response) in {"length", "max_tokens"}:
                        raise ValueError("model output truncated")
                    reports = [c for c in _response_tool_calls(response)
                               if (c.get("function") or c).get("name") == "report_summary"]
                    if len(reports) != 1:
                        raise ValueError("expected one report_summary tool call")
                    return validate(_arguments(reports[0])), shortened
                except asyncio.CancelledError:
                    error = "cancelled"
                    raise
                except Exception as exc:
                    error = str(exc)[:200] or type(exc).__name__
                    if _finish_reason(response):
                        error += f" (finish_reason={_finish_reason(response)})"
                    feedback = prompts["retry"].format(error=error)
                    if _context_overflow(exc) and attempt < self.limits.retries:
                        payload, cut = _shorten(payload)
                        shortened = shortened or cut
                        if cut:
                            self.warn("input_truncated", purpose + ": provider context limit; original nodes retained")
                            feedback += prompts["shortened"]
                    if attempt == self.limits.retries:
                        raise
                finally:
                    emit_single_llm_event(self.sink, stage="trajectory_summary", model=self.model_name,
                        counter=self.counter, started_at_ns=started, ended_at_ns=time.time_ns(),
                        retry_index=attempt, error=error, preview=purpose)

    @staticmethod
    def narrative(report: Any) -> str:
        if not isinstance(report, dict) or not isinstance(report.get("narrative"), str):
            raise ValueError("missing narrative")
        if not report["narrative"].strip():
            raise ValueError("empty narrative (length=0)")
        return report["narrative"].strip()

    @staticmethod
    def _ids(value: Any, allowed: set[str], nonempty: bool = True) -> bool:
        return (isinstance(value, list) and (bool(value) or not nonempty)
                and all(isinstance(x, str) and x in allowed for x in value))

    def _state(self, report: dict, allowed: set[str]) -> tuple[str, list[str]]:
        state, ids = report.get("state", "unknown"), report.get("resolution_node_ids", [])
        if state not in _STATES or not self._ids(ids, allowed, state == "resolved"):
            raise ValueError("invalid problem state or recovery evidence")
        return state, list(dict.fromkeys(ids))

    def _expected_skip(self, ids: list[str]) -> bool:
        tools = [self.nodes[x] for x in ids if self.nodes[x].role == "tool"]
        markers = ("跳过重复", "already sent", "duplicate delivery")
        for node in tools:
            if node.name != "send_file_to_user" or node.error:
                return False
            if not any(marker in (node.content or "").lower() for marker in markers):
                return False
        return bool(tools)

    @staticmethod
    def _leaf_ids(value: Any, allowed: set[str], aliases: dict[str, list[str]], field: str,
                  nonempty: bool = True) -> list[str]:
        """Accept exact IDs or an unambiguous local numeric node label, never foreign IDs."""
        if not isinstance(value, list) or (nonempty and not value):
            raise ValueError(f"{field} must be {'a nonempty' if nonempty else 'an'} array")
        result = []
        for ref in value:
            key = ref.strip() if isinstance(ref, str) else str(ref) if type(ref) is int else None
            if key in allowed:
                node_id = key
            elif key is not None and key.isascii() and key.isdecimal():
                matches = aliases.get(key, [])
                if len(matches) != 1:
                    raise ValueError(f"{field} invalid or outside Segment: {repr(ref)[:160]}")
                node_id = matches[0]
            else:
                raise ValueError(f"{field} invalid or outside Segment: {repr(ref)[:160]}")
            if node_id not in result:
                result.append(node_id)
        return result

    def leaf_problems(self, reports: Any, segment: Any, allowed: set[str]) -> list[ProblemRecord]:
        if not isinstance(reports, list):
            self.warn("problem_rejected", "Segment problems must be a list", segment.segment_id)
            return []
        aliases = {}
        for node_id in allowed:
            aliases.setdefault(node_id.rsplit(":", 1)[-1], []).append(node_id)
        result = []
        for index, p in enumerate(reports):
            try:
                if not isinstance(p, dict):
                    raise ValueError("problem must be an object")
                if p.get("kind") not in _KINDS:
                    raise ValueError(f"invalid problem kind: {repr(p.get('kind'))[:80]}")
                if not isinstance(p.get("summary"), str) or not p["summary"].strip():
                    raise ValueError("missing or empty problem summary")
                ids = self._leaf_ids(p.get("node_ids"), allowed, aliases, "node_ids")
                if self._expected_skip(ids):
                    continue
                kind = p["kind"]
                if kind == "user_correction" and not any(user_request_text(self.nodes[x]) for x in ids):
                    kind = "other"
                state = p.get("state", "unknown")
                if state not in _STATES:
                    raise ValueError(f"invalid problem state: {repr(state)[:80]}")
                recovery = self._leaf_ids(p.get("resolution_node_ids", []), allowed, aliases,
                                          "resolution_node_ids", state == "resolved")
                if any(old.kind == kind and old.node_ids == ids for old in result):
                    continue
                result.append(ProblemRecord(f"{segment.segment_id}:p{len(result)}", segment.turn_id,
                    segment.agent_id, segment.segment_id, kind, p["summary"].strip(), ids, "model", state, recovery))
            except ValueError as exc:
                self.warn("problem_rejected", f"candidate {index}: {exc}", segment.segment_id)
        return result

    def leaf_summary(self, p: ProblemRecord) -> ProblemSummary:
        refs = [{"node_id": x, "trace_id": self.nodes[x].trace_id, "span_id": self.nodes[x].span_id}
                for x in p.node_ids if self.nodes[x].trace_id and self.nodes[x].span_id]
        return ProblemSummary(p.problem_id, p.kind, p.summary,
            [ProblemLocation(p.agent_id, p.turn_id, p.segment_id, p.node_ids, refs)], [p.problem_id], p.source,
            p.state, p.resolution_node_ids)

    @staticmethod
    def problem_fallback(scope: str, index: int | str, reason: str, sources: Any = None) -> None:
        """A rejected update does not degrade coverage when known issues are retained."""
        logger.info(
            "[trajectory_summary] problem fallback scope=%s candidate=%s reason=%s "
            "sources=%s; original issues retained",
            scope, index, reason, repr(sources)[:500],
        )

    def merge_problems(
        self, report: dict, children: list[ProblemSummary], allowed: set[str], scope: str,
    ) -> list[ProblemSummary]:
        known = {x for p in children for x in p.source_problem_ids}

        def sources(value: Any) -> list[str] | None:
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                return None
            cleaned = list(dict.fromkeys(x.strip() for x in value))
            return cleaned if self._ids(cleaned, known, False) else None

        groups = report.get("problems")
        if not isinstance(groups, list):
            self.problem_fallback(scope, "all", "problem updates missing or not a list")
            return children
        dismissed = report.get("dismissed_problem_ids", [])
        dismissed_ids = sources(dismissed)
        if dismissed_ids is None:
            self.problem_fallback(scope, "dismissed", "unknown or malformed dismissed sources", dismissed)
        dismissed_ids = set(dismissed_ids or [])
        # Never dismiss just part of an already-merged child issue.
        used = set()
        for problem in children:
            if set(problem.source_problem_ids) <= dismissed_ids:
                used.update(problem.source_problem_ids)
        if used:
            logger.info("[trajectory_summary] dismissed issue sources scope=%s sources=%s", scope, sorted(used))
        result = []
        for index, group in enumerate(groups):
            try:
                if not isinstance(group, dict):
                    raise ValueError("problem update is not an object")
                cleaned = sources(group.get("source_problem_ids"))
                if not cleaned:
                    raise ValueError("missing, empty or unknown source_problem_ids")
                if not isinstance(group.get("summary"), str) or not group["summary"].strip():
                    raise ValueError("missing or empty problem summary")
                ids = set(cleaned)
                originals = [p for p in children if ids.intersection(p.source_problem_ids)]
                # A reference to a known merged issue carries its complete provenance.
                ids = {x for p in originals for x in p.source_problem_ids}
                if ids.intersection(used):
                    raise ValueError("overlapping problem sources")
                state, recovery = self._state(group, allowed)
                if len({p.kind for p in originals}) > 1 and "kind" not in group:
                    raise ValueError("mixed problem kinds require an explicit kind")
                kind = group.get("kind", originals[0].kind)
                if kind not in _KINDS:
                    raise ValueError("invalid problem kind")
                if kind == "user_correction" and not any(p.kind == kind for p in originals):
                    kind = "other"
                locations = [loc for p in originals for loc in p.locations]
                result.append(ProblemSummary(originals[0].problem_id, kind, group["summary"].strip(),
                    locations, sorted(ids), "model", state, recovery))
                used.update(ids)
            except ValueError as exc:
                self.problem_fallback(
                    scope, index, str(exc),
                    group.get("source_problem_ids") if isinstance(group, dict) else None,
                )
        # An omitted issue is not silently lost. Filtering must explicitly name its source IDs.
        result.extend(p for p in children if not set(p.source_problem_ids) <= used)
        return result

    async def segment(self, segment: Any, nodes: list[TrajectoryNode], context: str) -> None:
        allowed = {n.node_id for n in nodes}

        def validate(report: Any) -> tuple:
            text = self.narrative(report)
            return text, self.leaf_problems(report.get("problems", []), segment, allowed)

        try:
            (segment.narrative, segment.problems), cut = await self.call(
                "segment: summarize primary nodes and material issues",
                {"agent_id": segment.agent_id, "turn_id": segment.turn_id, "segment_id": segment.segment_id,
                 "reference": context, "primary": [json.loads(node_text(n)) for n in nodes]},
                {"narrative": _TEXT, "problems": {"type": "array", "items": _LEAF}},
                ["narrative", "problems"], validate)
            segment.status = "degraded" if cut else "completed"
        except Exception as exc:
            self.warn("segment_failed", str(exc) or type(exc).__name__, segment.segment_id)

    async def merge(self, items: list[dict], purpose: str, problems: list[ProblemSummary],
                    allowed: set[str], inputs: list | None = None, scope_id: str = "root") -> _MergeResult:
        complete = all(x["status"] == "completed" for x in items)
        if not any(x.get("narrative") for x in items):
            return _MergeResult("", "failed", problems)
        root = purpose.startswith("root:")
        if len(items) == 1 and not root:
            return _MergeResult(items[0]["narrative"], "completed" if complete else "degraded", problems)
        problem_schema = {**_MERGED, "properties": {**_MERGED["properties"],
            "source_problem_ids": {**_NONEMPTY_IDS, "description": self.prompts["sources"]}}}
        properties = {"narrative": _TEXT, "problems": {"type": "array", "items": problem_schema},
                      "dismissed_problem_ids": {**_IDS, "description": self.prompts["dismissed"]}}
        if root:
            properties["display_summary"] = _TEXT

        def validate(report: Any) -> tuple:
            text = self.narrative(report)
            merged = self.merge_problems(report, problems, allowed, f"{purpose.split(':', 1)[0]}:{scope_id}")
            display = report.get("display_summary", "")
            if root and (not isinstance(display, str) or not display.strip()):
                self.warn("display_summary_missing", "Technical summary retained; readable summary unavailable")
                display = ""
            return text, merged, display.strip() if isinstance(display, str) else ""
        try:
            payload = {"children": items, "problems": [_problem_input(p) for p in problems]}
            if inputs:
                payload["inputs"] = [asdict(value) for value in inputs]
            (text, merged, display), cut = await self.call(purpose, payload, properties,
                ["narrative", "problems"] + (["display_summary"] if root else []), validate)
            return _MergeResult(text, "completed" if complete and not cut else "degraded", merged, display)
        except Exception as exc:
            self.warn("merge_failed", purpose + ": " + (str(exc) or type(exc).__name__))
            note = "归并未完成，以下保留各子项摘要。" if self.language == "zh" else "Merge incomplete. Child summaries follow."
            text = note + "\n\n" + "\n\n".join(f"{x.get('label', i + 1)}:\n{x.get('narrative') or '[unavailable]'}"
                                                for i, x in enumerate(items))
            return _MergeResult(text, "degraded", problems)


async def summarize_trajectory(
    *, model: Any, nodes_by_agent: dict[str, list[TrajectoryNode]],
    language: str = "zh", background: str = "", limits: SummaryLimits | None = None,
    status_sink: Any = None, model_name: str = "", status_counter: list[int] | None = None,
) -> SummaryTree:
    limits = limits or SummaryLimits()
    tree, primary = prepare(nodes_by_agent, limits)
    if not tree.turns:
        tree.warnings.append({"code": "empty_scope", "message": "No selected trajectory nodes."})
        return tree
    runner = _Runner(model, tree, limits, language, status_sink, model_name,
                     status_counter if status_counter is not None else [0], primary)

    def item(label: str, text: str, status: str, first: str, last: str, times: tuple) -> dict:
        return public_summary({"label": label, "narrative": text, "status": status,
                               "start_node_id": first, "end_node_id": last,
                               "start_ts_ns": times[0], "end_ts_ns": times[1]})

    async def summarize_turn(turn: Any) -> None:
        # A turn depends only on its own segments; unrelated branches may still be running.
        await _parallel([runner.segment(s, primary[s.segment_id], background) for s in turn.segments])
        children = [item(f"Segment {i + 1}", s.narrative, s.status, s.start_node_id, s.end_node_id,
                         (s.start_ts_ns, s.end_ts_ns)) for i, s in enumerate(turn.segments)]
        problems = [runner.leaf_summary(p) for s in turn.segments for p in s.problems]
        allowed = {n.node_id for s in turn.segments for n in primary[s.segment_id]}
        merged = await runner.merge(children, "turn: summarize actions and update material issues", problems, allowed,
                                    turn.inputs, scope_id=f"{turn.agent_id}/{turn.turn_id}")
        turn.narrative, turn.status, turn.problems = merged.narrative, merged.status, merged.problems

    async def summarize_agent(agent_id: str, turns: list) -> tuple:
        await _parallel([summarize_turn(t) for t in turns])
        children = [item(f"Turn {t.turn_number if t.turn_number is not None else i + 1}", t.narrative, t.status,
                         t.start_node_id, t.end_node_id, (t.start_ts_ns, t.end_ts_ns)) for i, t in enumerate(turns)]
        problems = [p for t in turns for p in t.problems]
        if len(tree.turns) == 1:
            return children, problems
        nodes = nodes_by_agent[agent_id]
        merged = await runner.merge(children, "agent: summarize progress and update material issues", problems,
                                    {n.node_id for n in nodes}, scope_id=agent_id)
        summary = AgentSummary(agent_id, nodes[0].display_name, nodes[0].kind, merged.narrative,
            nodes[0].node_id, nodes[-1].node_id, *bounds(nodes), merged.status, merged.problems)
        tree.agent_summaries[agent_id] = summary
        return [item(summary.display_name, summary.summary, summary.status, summary.start_node_id,
                     summary.end_node_id, (summary.start_ts_ns, summary.end_ts_ns))], merged.problems

    groups = await _parallel([summarize_agent(a, turns) for a, turns in tree.turns.items()])
    tree.agent_summaries = {a: tree.agent_summaries[a] for a in tree.turns if a in tree.agent_summaries}
    merged = await runner.merge([x for children, _ in groups for x in children],
        "root: synthesize collaboration, final issue states and a readable outcome",
        [p for _, problems in groups for p in problems], set(runner.nodes))
    tree.trajectory_summary, tree.status = merged.narrative, merged.status
    tree.problems = [replace(p, problem_id=f"problem:{i}") for i, p in enumerate(merged.problems)]
    tree.display_summary = merged.display_summary
    if tree.status == "completed" and tree.warnings:
        tree.status = "degraded"
    return tree
