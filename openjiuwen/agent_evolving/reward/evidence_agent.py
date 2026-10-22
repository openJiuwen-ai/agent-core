"""Evidence by one agent-core DeepAgent: the only ReAct step of Reward.

One agent per reward run, created before evidence and destroyed after it. It sees every criterion with its claims,
the workspace and the tools, and gathers evidence for all of them:

    run_checks(checks=[...])   runs a batch of checks at once (read / python / browser), each naming the
                               criteria it serves; returns each read / python result whole
                               (up to the check output limit), a summary of each browser run with the last
                               screenshots, and where the full result is saved
    read_evidence(id, ...)     reads a saved result in part (lines or a pattern), when a browser summary is not enough

Full results stay on disk; the context grows only by what the agent asked to see. The agent stops by replying
without a tool call, or at the round / time limit. Code then hands each criterion its own checks for judging.
For a text-only reply, that reply is itself the work to inspect; a tool check is optional. An agent failure,
or no check of a file-based deliverable, raises EvidenceError with the cause.
"""

import asyncio
import json
import re
import uuid
from contextlib import aclosing
from pathlib import Path
from tempfile import TemporaryDirectory

from . import checks
from .files import clip
from .models import resolve
from .prompts import EVIDENCE_AGENT_PROMPT

ROUNDS = 12           # model turns of the agent: a safety cap; the time limit is the one meant to bind
SECONDS = 300         # the whole evidence step
SUMMARY = 1500        # characters of a browser check's result shown to the agent (its screenshots come along)
SHOWN = checks.CHECK_OUTPUT + 1500   # read / python: what the agent asked for comes back whole
IDLE_HINT = 2         # run_checks calls in a row that ran no new check before the agent is told to stop
IDLE_STOP = 4         # ... before the run is stopped
SHOWN_IMAGES = 2      # screenshots shown to the agent per batch
MAX_IMAGES = 16       # the latest screenshots per criterion handed to its judge


LIMIT_REACHED = re.compile(r"max(imum)?[ _-]*iterations?", re.I)   # agent-core's "Max iterations reached ..."

TOOL_BY_FIELD = (("actions", "browser"), ("page", "browser"), ("code", "python"), ("path", "read"))


def with_tool(check):
    """The check with its "tool" filled in from its fields when the model left it out (a "page" or "actions" means
    browser, "code" python, "path" read).
    """
    if check.get("tool"):
        return check
    tool = next((name for field, name in TOOL_BY_FIELD if check.get(field)), None)
    return {**check, "tool": tool} if tool else check


class EvidenceError(RuntimeError):
    """The evidence agent could not produce usable evidence; the message says where and why."""


def pool_entry(route):
    """A Reward model route ({"model", "base_url", "api_key", "extra_body"?, "timeout"?}) as the agent-core
    model entry DeepAgent takes; an entry given as such (e.g. by JiuwenSwarm) is used as it is.
    """
    from openjiuwen.agent_teams.models.pool import ModelPoolEntry

    if isinstance(route, ModelPoolEntry):
        return route
    if not isinstance(route, dict):   # an entry-like object (model_name, api_base_url, api_key, metadata?)
        return ModelPoolEntry(model_name=route.model_name, api_key=route.api_key, api_base_url=route.api_base_url,
                              api_provider=getattr(route, "api_provider", None) or "OpenAI",
                              metadata=dict(getattr(route, "metadata", None) or {}))
    return ModelPoolEntry(model_name=route["model"], api_key=resolve(route["api_key"]),
                          api_base_url=resolve(route["base_url"]), api_provider=route.get("provider", "OpenAI"),
                          # A stream cut mid-way is retried.
                          metadata={"client": {"timeout": float(route.get("timeout", 360)), "max_retries": 2},
                                    "request": dict(route.get("extra_body") or {})})


class EvidenceRun:
    """The checks run for this reward: results saved under `folder`, sorted by the criteria they serve."""

    def __init__(self, criteria, ctx, folder, log):
        self.ids = [c["id"] for c in criteria]
        self.ctx, self.folder, self.log = ctx, Path(folder), log
        self.results = []                                   # [{"id", "criteria", "facts", "labels"}]
        self.images = {cid: [] for cid in self.ids}
        self.next_number = 1
        self.seen = {}                 # check (without bookkeeping fields) -> its shared execution task
        self.idle = 0                  # tool calls in a row that ran no new check
        self.stop = asyncio.Event()    # set when the agent keeps calling tools without new evidence

    async def run_one(self, number, check):
        check = with_tool(check)
        serves = [c for c in check.get("criteria") or [] if c in self.ids] or list(self.ids)
        try:
            facts, shots = await checks.run_check(check, self.ctx["root"], self.ctx["tools"], number)
        except Exception as error:  # noqa: BLE001 - shown to the agent, which can fix the check
            facts, shots = {"tool": check.get("tool"), "note": check.get("note", ""),
                            "could_not_run": f"{type(error).__name__}: {str(error)[:400]}"}, []
        rid = f"c{number}"
        labelled = [(f"check {number}, {label}", data) for label, data in shots]
        (self.folder / f"{rid}.txt").write_text(readable(check, serves, facts, labelled), encoding="utf-8")
        for cid in serves:
            self.images[cid].extend(labelled)
        self.results.append({"id": rid, "criteria": serves, "request": check,
                             "facts": {"check": number, **facts}})
        return rid, serves, facts, labelled

    def stalled(self, new):
        """Count run_checks calls that ran no new check (every check a repeat, or an empty or malformed list);
        read_evidence is not counted. After IDLE_HINT the agent is told to stop, after IDLE_STOP the run is stopped.
        """
        self.idle = 0 if new else self.idle + 1
        if self.idle >= IDLE_STOP:
            self.log(f"evidence agent stopped: {self.idle} tool calls in a row without a new check")
            self.stop.set()
        return ({"stop": "No new evidence in your last tool calls. Stop now: reply in one line without a tool call."}
                if self.idle >= IDLE_HINT else {})

    async def run_checks(self, checks_list):
        if not isinstance(checks_list, list) or not checks_list:
            return {"action_error": "checks must be a non-empty list of check objects", **self.stalled(False)}
        pending, fresh = [], 0
        for c in checks_list:
            c = with_tool(c if isinstance(c, dict) else {})
            key = json.dumps({k: v for k, v in c.items() if k not in ("criteria", "note", "claims")},
                             ensure_ascii=False, sort_keys=True)
            repeat = key in self.seen
            if not repeat:
                number = self.next_number
                self.next_number += 1
                self.seen[key] = asyncio.create_task(self.run_one(number, c))
                fresh += 1
            pending.append((self.seen[key], c, repeat))
        runs = await asyncio.gather(*(task for task, _, _ in pending))
        rows, shots = [], []
        for (rid, serves, facts, labelled), (_, check, repeat) in zip(runs, pending):
            requested = [c for c in check.get("criteria") or [] if c in self.ids] or list(self.ids)
            if repeat:
                row = next(r for r in self.results if r["id"] == rid)
                for cid in requested:
                    if cid not in row["criteria"]:
                        row["criteria"].append(cid)
                        self.images[cid].extend(labelled)
                serves = row["criteria"]
                # The saved receipt and the judge now agree about every consumer of this check.
                (self.folder / f"{rid}.txt").write_text(readable(row["request"], serves, facts, labelled),
                                                       encoding="utf-8")
                rows.append({"id": rid, "repeat": True, "criteria": serves,
                             "summary": f"already run as {rid}; use its result (read_evidence)"})
                continue
            failed = facts.get("could_not_run") or facts.get("skipped") or facts.get("timed_out")
            rows.append({"id": rid, "criteria": serves, "tool": facts.get("tool"),
                         "ok": not failed and facts.get("exit_code") in (0, None),
                         "result": clip(json.dumps(shown(facts), ensure_ascii=False),
                                        SUMMARY if facts.get("tool") == "browser" else SHOWN),
                         "screenshots": [label for label, _ in labelled], "full": f"read_evidence id={rid}"})
            shots += labelled
            self.log(f"evidence {rid} ({facts.get('tool')}) for {', '.join(serves)}"
                     + (f": {str(failed)[:120]}" if failed else ""))
        out = {"results": rows, **self.stalled(bool(fresh))}
        if self.ctx.get("seeing") and shots:
            out["multimodal"] = [{"type": "image", "data_url": "data:image/jpeg;base64," + data,
                                  "source": "reward check", "source_path": label}
                                 for label, data in shots[-SHOWN_IMAGES:]]
        return out

    def read_evidence(self, id, pattern=None, start=1, count=80):  # noqa: A002 - the tool's own argument name
        return self.read_saved(id, pattern, start, count)   # reading saved results is normal work, never idle

    def read_saved(self, id, pattern=None, start=1, count=80):  # noqa: A002
        path = self.folder / f"{id}.txt"
        if not re.fullmatch(r"c\d+", str(id)) or not path.is_file():
            return {"action_error": f"no saved result {id!r}; use an id returned by run_checks"}
        lines = path.read_text(encoding="utf-8").splitlines()
        try:
            if pattern:
                rx = re.compile(pattern, re.I)
                body = [f"{i}: {line}" for i, line in enumerate(lines, 1) if rx.search(line)][:60]
            else:
                start, count = max(1, int(start or 1)), max(1, min(200, int(count or 80)))
                body = [f"{i}: {line}" for i, line in enumerate(lines[start - 1:start - 1 + count], start)]
        except (re.error, TypeError, ValueError) as error:
            return {"action_error": f"bad pattern or line numbers: {error}"}
        return {"id": id, "lines": len(lines), "text": clip("\n".join(body) or "(no line matches)", 6000)}

    def evidence_for(self, cid):
        rows = sorted((r for r in self.results if cid in r["criteria"]), key=lambda r: r["facts"]["check"])
        return [r["facts"] for r in rows], self.images[cid][-MAX_IMAGES:]


def shown(facts):
    """What the agent sees of a check: no echo of its own code or browser actions (it wrote them), so the outcome
    (each step's ok, probe value or error) is not cut off by the summary limit.
    """
    facts = {k: v for k, v in facts.items() if k != "code"}
    if isinstance(facts.get("actions"), list):
        facts["actions"] = [{k: v for k, v in a.items() if k != "action"} if isinstance(a, dict) else a
                            for a in facts["actions"]]
    return facts


def readable(check, serves, facts, labelled):
    """A saved check result as plain lines: the check, then each fact with multi-line values kept as lines, so
    read_evidence can search and page through it.
    """
    lines = [f"check: {json.dumps(check, ensure_ascii=False)}", f"criteria: {', '.join(serves)}",
             f"screenshots: {', '.join(label for label, _ in labelled) or '-'}"]
    for key, value in facts.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1)
        lines.append(f"--- {key} ---" if "\n" in text else f"{key}: {text}")
        if "\n" in text:
            lines.append(text)
    return "\n".join(lines) + "\n"


def evidence_tools(run):
    """The two tools, as an agent-core rail."""
    from openjiuwen.core.foundation.tool import ToolCard
    from openjiuwen.core.foundation.tool.base import Tool
    from openjiuwen.core.single_agent.rail.base import AgentRail
    from openjiuwen.harness.tools.base_tool import ToolOutput

    class EvidenceTool(Tool):
        def __init__(self, name, description, params, required, handler):
            super().__init__(ToolCard(id=f"reward.{name}", name=name, description=description,
                                      parallel_safe=True, properties={"resilience": {"timeout_s": None}},
                                      input_params={"type": "object", "properties": params, "required": required,
                                                    "additionalProperties": False}))
            self.handler = handler

        async def invoke(self, inputs, **kwargs):
            result = self.handler(**inputs)
            if asyncio.iscoroutine(result):
                result = await result
            if "multimodal" in result:   # images go to the model separately, never as tool text
                result = {**result, "content": {k: v for k, v in result.items() if k != "multimodal"}}
            error = result.get("action_error")
            return ToolOutput(success=error is None, data=result,
                              error=json.dumps(result, ensure_ascii=False) if error else None)

        async def stream(self, inputs, **kwargs):
            yield await self.invoke(inputs, **kwargs)

    tools = [
        EvidenceTool("run_checks",
                     "Run several checks at once, in parallel. Put every check that does not depend on another one's "
                     "result into the same call. Each check is one object as described in the instructions, with "
                     "\"criteria\": the ids of the criteria it gives evidence for.",
                     {"checks": {"type": "array", "minItems": 1, "items": {
                         "type": "object", "required": ["tool", "criteria"],
                         "properties": {"tool": {"type": "string", "enum": ["read", "python", "browser"]},
                                        "criteria": {"type": "array", "items": {"type": "string"}}}}}},
                     ["checks"],
                     lambda checks: run.run_checks(checks)),
        EvidenceTool("read_evidence",
                     "Read a saved check result in part: lines from `start`, or the lines matching `pattern`.",
                     {"id": {"type": "string"}, "pattern": {"type": "string"}, "start": {"type": "integer"},
                      "count": {"type": "integer"}}, ["id"], run.read_evidence),
    ]

    class EvidenceRail(AgentRail):
        priority = 1000

        def init(self, agent):
            for tool in tools:
                agent.ability_manager.add_ability(tool.card, tool)

        def uninit(self, agent):
            for tool in tools:
                agent.ability_manager.remove_ability(tool.card.name)

    return EvidenceRail()


def stream_error(output):
    """The error text of an agent stream item that reports a failed run ({"result_type": "error"}), else None."""
    payload = getattr(output, "payload", output)
    for value in (payload, getattr(payload, "__dict__", None)):
        if isinstance(value, dict):
            inner = value.get("data") if isinstance(value.get("data"), dict) else value
            if inner.get("result_type") == "error":
                return str(inner.get("output") or "unknown error")[:500]
    return None


def build_agent(route, workspace, rounds):
    """One DeepAgent with only the evidence tools (no shell, no file writes, no planning, no subagents)."""
    from openjiuwen.core.context_engine.context.context_utils import ContextUtils
    from openjiuwen.core.context_engine.schema.config import CompressionRecallConfig, ContextEngineConfig
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard
    from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec, WorkspaceSpec

    entry = pool_entry(route)
    run_id = f"reward_{uuid.uuid4().hex[:12]}"
    window = ContextUtils.resolve_context_max(model_name=entry.model_name,
                                              fallback_context_window_tokens=128000)
    spec = DeepAgentSpec(
        card=AgentCard(id=run_id, name=run_id, description="Reward evidence"),
        model=entry.to_team_model_config(), system_prompt=EVIDENCE_AGENT_PROMPT, prompt_mode="none",
        enable_task_loop=False, enable_sys_operation=False, enable_task_planning=False,
        enable_subagent_runtime=False, enable_skill_discovery=False, enable_read_image_multimodal=False,
        tools=[], rails=[], mcps=[], subagents=[], skills=[], max_iterations=rounds,
        workspace=WorkspaceSpec(root_path=str(workspace)), auto_create_workspace=False,
        context_engine_config=ContextEngineConfig(context_window_tokens=window, model_name=entry.model_name,
                                                  compression_recall_config=CompressionRecallConfig(enabled=False)))
    return spec.build(), run_id


async def gather(criteria, ctx, route, log, rounds=ROUNDS, seconds=SECONDS):
    """{criterion id: (evidence, images)} gathered by one DeepAgent for all criteria."""
    from openjiuwen.core.session.agent import create_agent_session

    with TemporaryDirectory(prefix="reward_evidence_") as tmp:
        folder = Path(tmp) / "evidence"
        folder.mkdir()
        run = EvidenceRun(criteria, ctx, folder, log)
        seed = {**ctx["plan_input"], "seeing_screenshots": bool(ctx.get("seeing")),
                "limits": {"rounds": rounds, "seconds": int(seconds)},
                "criteria": [{"id": c["id"], "name": c.get("name", ""), "kind": c.get("kind", ""),
                              "claims": [{"index": i, "text": x["text"], "level": x.get("level", "floor")}
                                         for i, x in enumerate(c["claims"])]} for c in criteria]}
        stage = "building the evidence agent"
        agent = session = failure = streaming = stopping = None
        try:
            agent, run_id = build_agent(route, Path(tmp), rounds)
            agent.add_rail(evidence_tools(run))
            stage = "running the evidence agent"
            session = create_agent_session(session_id=run_id, card=agent.card)
            inputs = {"query": json.dumps(seed, ensure_ascii=False)}
            await session.pre_run(inputs=inputs)

            async def drain():
                found = None
                async with aclosing(agent.stream(inputs, session=session)) as outputs:
                    async for out in outputs:
                        found = found or stream_error(out)
                return found

            streaming = asyncio.ensure_future(drain())
            stopping = asyncio.ensure_future(run.stop.wait())
            done, _ = await asyncio.wait({streaming, stopping}, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
            stopping.cancel()
            if streaming in done:
                failure = streaming.result()
            else:   # the time limit, or no new evidence for several tool calls: stop and judge what was gathered
                streaming.cancel()
                await asyncio.gather(streaming, return_exceptions=True)
                if not run.stop.is_set():
                    log(f"evidence agent stopped at the {int(seconds)} s limit after {len(run.results)} check(s)")
        except Exception as error:
            raise EvidenceError(f"Reward evidence failed while {stage}: {type(error).__name__}: {error}") \
                from error
        finally:
            tasks = [task for task in (streaming, stopping) if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if session is not None:
                await session.post_run()
            if agent is not None:
                await agent.cleanup_task_resources()
        if failure and LIMIT_REACHED.search(failure):   # the round limit is a normal stop, like the time limit
            log(f"evidence agent stopped at the {rounds}-round limit after {len(run.results)} check(s)")
            failure = None
        if failure:   # the agent reports model errors in its stream instead of raising; fail loudly
            raise EvidenceError(f"Reward evidence failed while running the evidence agent "
                                f"(after {len(run.results)} check(s)): {failure}")
        if not run.results:
            material = ctx["plan_input"]
            work = material.get("workspace", {})
            if material.get("final_reply", "").strip() and not work.get("files"):
                log("text-only reply supplied directly; evidence agent requested no additional check")
                return {cid: ([], []) for cid in run.ids}
            raise EvidenceError("Reward evidence failed: the evidence agent ran no check, so no usable reward "
                                "can be given (the model may not call tools, or the time limit is too short)")
        if not any(not (r["facts"].get("could_not_run") or r["facts"].get("skipped") or
                        r["facts"].get("timed_out")) and r["facts"].get("exit_code") in (None, 0)
                   for r in run.results):
            raise EvidenceError("Reward evidence failed: every check failed to execute; "
                                "check errors are not evidence of product defects")
        missing = [cid for cid in run.ids if not run.evidence_for(cid)[0]]
        if missing:
            log(f"no check served: {', '.join(missing)} (judged from the files and the reply)")
        return {cid: run.evidence_for(cid) for cid in run.ids}
