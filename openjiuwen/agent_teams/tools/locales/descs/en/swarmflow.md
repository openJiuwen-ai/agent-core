Run a swarmflow orchestration script (a multi-agent workflow) that orchestrates a fleet of worker subagents **deterministically**. The script spawns and coordinates those workers — to be **comprehensive** (decompose and cover in parallel), to be **confident** (verify with independent perspectives before concluding), or to take on **scale one context can't hold** (migrations, audits, deep research). The script is where you encode that structure: **what fans out, what verifies, what synthesizes**. This tool **returns immediately** with a run_id (the workflow runs asynchronously in the background); that run_id is the handle for a later `resume_id`, and phase progress plus the final result are fed back into your context automatically.

## When to call / when not to
- **Pick the mechanism by collaboration nature**: a multi-agent task whose structure can be thought through up front and written as deterministic control flow (known topology; fan-out / pipeline / verify / synthesize can be coded) **defaults to this tool** — no explicit user naming required. Conversely, when collaboration is **emergent** — members need to autonomously communicate / negotiate with each other, there is no fixed information-flow topology, the task DAG is unclear, there are many dynamic scenarios, or it needs persistent / HITT collaboration — that is `build_team` team territory. The basis is the task's collaboration nature, not the user's wording or task complexity.
- **Use it**: complex, multi-step, parallel / pipeline, adversarial-verification, large-scale ranking, root-cause, exploration, triage work — tasks that genuinely need multiple agents working together.
- **Don't**: a simple single-agent task does not need a workflow — not every task needs a fleet of workers. First ask "does this really need multiple agents working together?"; if yes, orchestrate; if no, use a single `agent()` or just do it yourself. The reversed default is about *which mechanism* multi-agent collaboration uses (swarmflow over build_team), **not** about splitting simple tasks into orchestrations.
- **Hybrid is best**: before orchestrating, scout inline first (list the relevant files, find the data sources, scope the diff) to discover the work-list, then have the script pipeline over it. You don't need to know the shape before the *task* — only before the *orchestration step*.

> This tool's presence means `enable_swarmflow` is on. Choosing swarmflow vs build_team depends on the **task's collaboration nature** (structure can be deterministically orchestrated → swarmflow; emergent autonomous collaboration, topology / DAG not predetermined, highly dynamic, members must talk to each other → build_team), not on whether the user names it this turn. When unsure, default to swarmflow.

## Common workflow shapes (chainable across turns)
- **Understand**: parallel readers over relevant subsystems → a structured map.
- **Design**: a judge panel of N independent approaches → scored synthesis.
- **Review**: findings by dimension → adversarial verify (see skeleton below).
- **Research**: multi-modal sweep → deep read → a synthesized, cited conclusion.
- **Migrate**: discover sites → transform each (with `isolation='worktree'` when needed) → verify.

Larger work can be **split into several scripts run in sequence** (start the next after one finishes), or staged within one script via `phase()`. Read each stage's result before deciding the next — you stay in the loop, and each script is one well-scoped fan-out.

## Behavior contract (must follow)
- This tool **returns immediately** (with a run_id) — the workflow runs asynchronously in the background; **do not poll** or call it repeatedly.
- Phase progress arrives **automatically** as notifications in your context; when the workflow **completes or fails, the final result is fed back to you automatically** — no need to query.
- You are a **spectator**: the script orchestrates all the workers itself. On each progress notification, relay it to the user in brief natural language; staying quiet between notifications is the normal state.
- **Do not** spawn members, `create_task`, or orchestrate yourself — the script owns all orchestration; and do not rewrite a worker's intermediate results.

## Script sources (one of) and args
- `script` (**available today**): inline script source, no disk write needed. **Prefer it for simple cases** — pass the source straight in to run, skipping the file and iterating fastest.
- `script_path` (**available today**): path to a script file on disk. Best for scripts already on disk (`swarmskill-creator` output, or ones you iterate / resume repeatedly).
- `name` (interface in place, execution coming): a saved / named workflow, resolved to a self-contained script.
- `resume_id` (interface in place, execution coming): a prior run's run_id, to resume.
- `args`: a **string** argument passed to the script's `run(args)` (e.g. a question, a target path). For structured input, `json.loads(args)` inside the script.

> **Pick a source by complexity**: for a simple task whose orchestration is obvious at a glance, **prefer inline `script`** and run it directly, or hand-write a minimal script file and use `script_path` — no need to involve `swarmskill-creator`. Only for complex work (multi-phase / multi-role / needing executable retry·degrade·budget constraints / meant to become a reusable skill) use the `swarmskill-creator` skill to author a script through its full develop-and-validate flow.
>
> **When to install a `swarmskill-creator`-authored script**: if you are **augmenting / modifying a script inside an existing swarmskill**, follow creator's normal flow. If you are **writing a brand-new script from scratch**, after writing the script file **do not** immediately generate and install the skill — first call this tool to run the workflow to completion, and once swarmflow finishes and you have the actual result, **ask the user whether to install it as a skill**; install only after they confirm. Don't freeze an un-run, unverified orchestration into a skill.
>
> **To iterate**: edit the script file on disk and re-invoke with the same `script_path` — no need to resend the source. If `swarmskill-creator` is unavailable, tell the user honestly rather than forcing the call or hand-writing one.
>
> `name` (saved / named workflow) and `resume_id` (resume) have their interface in place but execution is still coming; a call today is rejected with an **explicit error** (never a silent no-op).

## Script structure (Python)
A script is a Python module: a top-level `META` (pure literal) plus `async def run(args)`, importing the primitives from `swarmflow`.

```python
from swarmflow import agent, agent_session, human, parallel, pipeline, map_parallel, phase, log, workflow, budget, compact

META = {
    "name": "deep-research",
    "description": "one line, shown in the permission dialog",
    "whenToUse": "what it's for (optional, shown in the workflow list)",
    "phases": [{"title": "Search", "detail": "parallel retrieval"}, {"title": "Verify", "model": "..."}],  # title may be a plain string
}

async def run(args):
    phase("Search")
    hits = await agent(f"search: {args}", schema=HITS)
    return {"answer": ...}  # the structured result IS the return value, fed back automatically
```

- `META` must be a **pure literal** — no variables, function calls, f-strings, or string concatenation (extracted statically at load time).
- Required `name` / `description`; optional `whenToUse` (shown in the workflow list), `phases`.
- `phases[].title` must match the `phase()` calls exactly; a phase with no matching call forms its own progress group. A phase entry may carry `model` to override that phase's default model (`{"title": "Verify", "model": "..."}`).
- **Return-value semantics**: a worker is told its final text **IS** the return value (not a human-facing message), so it returns **raw data**. The value `run(args)` returns (usually a dict) is the workflow's final result, fed back to the caller automatically.

## Orchestration primitives (`from swarmflow import ...`)
- `await agent(prompt, *, schema=None, label=None, phase=None, options=None)` — spawn a one-shot worker subagent. Orchestration/identity params (`label` / `phase` / `schema`) are explicit; tuning and forward-compat params ride in the `options` bag (mirroring `agent_session().send`).
  - No `schema` returns text; a JSON Schema dict returns a validated dict, a pydantic model returns a model instance (validation is at the tool-call layer, the model retries on mismatch); failure (retries exhausted / spawn cap hit) returns `None` — filter with `compact()`.
  - `label` overrides the label shown in progress.
  - `phase` explicitly assigns this `agent()` to a progress group — **inside** a `pipeline`/`parallel` stage, always pass `phase` explicitly to avoid racing on the global `phase()` state; the same `phase` string groups into the same box.
  - `options` is the tuning / forward-compat bag (a dict); keys are whitelisted against the engine + backend option sets and an unknown key fails fast. Currently supported keys:
    - `model` overrides this worker's model. **Default to omitting it** — a worker inherits the team's teammate model (almost always correct); set it only when you're highly confident a worker needs a different tier.
    - `timeout` the per-call timeout in seconds for this worker.
    - `isolation='worktree'`: run the worker in a fresh git worktree, **EXPENSIVE** (~200-500ms setup + disk per worker), use **ONLY** when workers mutate files in parallel and would otherwise conflict; the worktree is auto-removed if unchanged.
    - `agent_type`: use a named specialist subagent (e.g. a class of teammate on the team) instead of the default worker, resolved from the same registry as the team; composes with `schema` (the specialist's system prompt gets a structured-output instruction appended). (Interface in place, execution coming.)
- `agent_session(label=, phase=, instructions=, options=)` + `await s.send(prompt, *, schema=, notify=False)` — a **stateful** multi-turn agent that remembers across turns; the second turn need not restate the first. `notify=True` pushes one-way, returns `None`.
- `await human(prompt, *, schema=)` / `human_session()` + `.send()` — one-shot / stateful **human-in-the-loop**; waiting on a person holds no concurrency permit and costs no spawn budget, and is replayable from the journal.
- `await parallel([thunk, ...])` — a fork-join **barrier**: runs concurrently, returns once all finish; a throwing branch resolves to `None` and the call **never raises** (filter with `compact` first). A thunk is a zero-arg callable like `lambda: agent(...)`.
- `await pipeline(items, stage1, stage2, ...)` — a **no-barrier** stream: each item flows through all stages independently (A can be in stage 3 while B is still in stage 1); each stage callback receives `(prev, item, index)` — later stages can use `item`/`index` to label work without threading context through `prev`; a throwing stage drops only that item to `None` and skips its remaining stages.
- `await map_parallel(items, fn)` (alias `pmap`) — closure-trap-free fan-out, `fn` is `async def fn(item)` or `fn(item, i)`, binding each item correctly.
- `phase(title)` / `log(message)` — progress (open a phase / a narration line).
- `await workflow(name_or_path, args)` — run another workflow inline as a sub-step and return its return value. The sub-workflow **shares** this run's concurrency cap, agent counter, abort signal, and token budget (its agents count toward `budget.spent()`). Nesting is **one level only** (a sub-workflow calling it again raises); unknown name / unreadable path / syntax error raise, catch with try/except.
- `budget.total` / `budget.spent()` / `budget.remaining()` — token budget (see below).
- `compact(xs)` / `flatten_filter(xs)` — pure list helpers: drop falsy (None/''/0/[]) / flatten one level and drop falsy.

## What a worker can run
Each worker spawned by `agent()` / a session (one-shot, or multi-turn within a session) is a team-member instance that **inherits the team's teammate spec capabilities**: model, tools, skills, workspace, sys_operation. But it has **no team-coordination tools** (cannot spawn members / create tasks / send messages) — it is a focused, disposable execution unit. So a script can let a worker call the tools configured in its spec (retrieval, code execution, etc.) to actually do work; pass a `schema` for a structured product.

## pipeline or parallel (default pipeline)
**Default to `pipeline`** (no barrier; wall-clock = the slowest single-item chain, not sum-of-slowest-per-stage). A barrier (`parallel`) is correct ONLY when a stage **needs all cross-item results from the previous one**:

- dedup / merge the **full result set** before expensive downstream work;
- **early-exit** when the count is zero ("0 findings → skip verification entirely");
- the next stage's prompt **compares against** "all the other findings".

These do NOT justify a barrier: "I need to flatten / map / filter first" (do it inside a pipeline stage), "the stages are conceptually separate" (that's what pipeline models — separate ≠ synchronized), "it's cleaner" (barrier latency is real: if the slowest of 5 finders takes 3× the fastest, a barrier wastes 2/3 of the fast finders' idle time).

**Smell test**: if you wrote

```python
a = await parallel([...])
b = transform(a)                      # flatten / map / filter — no cross-item dependency
c = await parallel([... for x in b])
```

that middle `transform` needs no barrier — rewrite as a pipeline with the transform inside a stage: `pipeline(items, stageA, lambda r, *_: transform([r]), stageB)`. When in doubt: pipeline.

## Determinism constraints (scripting rules)
- `META` is a pure literal.
- A script is plain Python, running in an async context — `await` directly. The standard library is available, **but** load-time lint **rejects** nondeterministic sources: `time.time()` / `time.monotonic()` / `random.*` / `*.now()` / `*.today()` (they would break resume). Pass timestamps via `args` and stamp afterwards; for randomness, vary the prompt / label by index.
- **Closure trap**: `lambda: agent(...)` inside a comprehension makes every lambda capture the same (last) variable. Bind it — `lambda x=x: agent(...)` — or use `map_parallel`.
- No filesystem, no out-of-band network — everything goes through the primitives.

## Concurrency and limits
- Concurrency cap = `min(16, CPU cores - 2)`; excess `agent()` calls queue and run as slots free. You can still pass many items to `parallel`/`pipeline`; only ~cap run at any moment.
- Total agents over a workflow's lifetime are capped at **1000** (`agent()` returns `None` past it, no retry — a runaway backstop).
- A single `parallel` / `pipeline` call takes at most **4096** items; exceeding it is an **explicit error**, not a silent truncation.
- `workflow()` nesting is **one level only**.

## Structured output (schema)
- `schema=None` → text; `schema=<JSON Schema dict>` → dict; `schema=<pydantic model>` → model instance (attribute access + static narrowing).
- Validation failure is retried by the model; on exhaustion it returns `None`. Filter `None` with `compact()` / `.filter` before use.

## Budget (hard ceiling)
- `budget.total` (the turn's token target, `None` if unset), `budget.spent()` (output tokens spent, shared across the main loop and all workflows — not per-workflow), `budget.remaining()` (`max(0, total - spent())`, infinite when no target).
- **Hard ceiling**: once `spent()` reaches `total`, further `agent()` calls raise. Drive depth dynamically (`while budget.total and budget.remaining() > N`) or scale fan-out statically (`FLEET = budget.total // 100_000 if budget.total else 5`). With no `total` set, `remaining()` is infinite — a dynamic loop MUST guard on `budget.total`, else it runs to the 1000 cap. (Note: the pre-call hard cutoff is coming; today it counts and the script self-checks.)

## Resume
- `resume_id` = a prior run's **run_id** (the handle this tool returns). On resume it is **content-addressed**: unchanged `agent()` calls reuse cached results instantly; an upstream prompt change flips the downstream signature and re-runs it automatically (no manual marking). **Same script + same args → 100% cache hit.**
- Maintained by the async-tool execution framework via a content-addressed journal (same model as the reference tool's runId). (Execution coming.)

## Orchestration pattern library (compose by scale)
- **Adversarial verify**: spawn N independent skeptics per finding, each told to **refute**; kill it if a majority refute — stops plausible-but-wrong findings.
- **Perspective-diverse verify**: give each verifier a distinct lens (correctness / security / performance / does-it-reproduce) instead of N identical refuters.
- **Judge panel**: generate N independent attempts from different angles → score in parallel → synthesize from the winner, grafting the runners-up's best ideas.
- **Loop-until-count**: accumulate to a target — `while len(bugs) < 10: ...`, pushing fresh findings each round.
- **Loop-until-dry**: for unknown-size discovery, keep spawning finders until K consecutive rounds add nothing new (simple counters miss the tail).
- **Multi-modal sweep**: parallel agents each searching a different way (by container / content / entity / time), each blind to the others.
- **Completeness critic**: a final agent that asks "what's missing — a modality not run, a claim unverified, a source unread?"; what it finds becomes the next round.
- **No silent caps**: if you bound coverage (top-N / sampling), `log()` what was dropped — a silent truncation reads as "covered everything".
- Scale to the user's wording: "find a few bugs" → a few finders, single-vote verify; "thoroughly audit / be comprehensive" → a larger finder pool, 3–5 vote adversarial pass, a synthesis stage.

These patterns are **not exhaustive** — compose novel harnesses when the task calls for it (tournament brackets, self-repair loops, staged escalation, and so on).

## Code skeletons (real swarmflow API)
Multi-dimension review — pipeline by default, each dimension verifies the moment its review completes ('bugs' verifies while 'perf' is still reviewing, no wasted wall-clock):

```python
async def run(args):
    dims = [{"key": "bugs", "prompt": "find bugs"}, {"key": "perf", "prompt": "find perf issues"}]

    async def review(_prev, d, _i):
        return await agent(d["prompt"], label=f"review:{d['key']}", phase="Review", schema=FINDINGS)

    async def verify(rev, _d, _i):
        findings = rev["findings"] if rev else []
        return await parallel([
            (lambda f=f: agent(f"Adversarially verify: {f['title']}", phase="Verify", schema=VERDICT))
            for f in findings
        ])

    results = await pipeline(dims, review, verify)
    return {"confirmed": [f for rev in compact(results) for f in compact(rev) if f.get("is_real")]}
```

Barrier-is-correct — dedup all findings before expensive verification (genuinely needs them all at once):

```python
async def run(args):
    raw = await parallel([(lambda d=d: agent(d, schema=FINDINGS)) for d in DIMENSIONS])
    deduped = dedupe_by_file_and_line([f for r in compact(raw) for f in r["findings"]])  # plain code, not an agent
    return await parallel([(lambda f=f: agent(verify_prompt(f), schema=VERDICT)) for f in deduped])
```

Loop-until-count — accumulate to a target:

```python
async def run(args):
    bugs = []
    while len(bugs) < 10:
        r = await agent("Find bugs in this codebase.", schema=BUGS)
        bugs.extend(r["bugs"] if r else [])
        log(f"{len(bugs)}/10 found")
    return {"bugs": bugs}
```

Loop-until-budget — scale depth to the budget (guard on `budget.total`):

```python
async def run(args):
    bugs = []
    while budget.total and budget.remaining() > 50_000:
        r = await agent("Find bugs in this codebase.", schema=BUGS)
        bugs.extend(r["bugs"] if r else [])
        log(f"{len(bugs)} found, {budget.remaining() // 1000}k tokens left")
    return {"bugs": bugs}
```

Composing patterns — exhaustive review (find → dedup vs `seen` → diverse-lens panel → loop-until-dry). Keep spawning finders until two consecutive rounds add nothing; judge each fresh finding concurrently, each via 3 distinct lenses, confirm only on a majority. **Dedup against `seen`, not `confirmed`**, or judge-rejected findings reappear every round and it never converges. Note `parallel` can nest `parallel`:

```python
async def run(args):
    seen, confirmed, dry = set(), [], 0
    while dry < 2:                                  # stop after two empty rounds
        found = compact(await parallel([(lambda p=p: agent(p, phase="Find", schema=BUGS)) for p in FINDERS]))
        fresh = [b for r in found for b in (r["bugs"] if r else []) if b["id"] not in seen]
        if not fresh:
            dry += 1
            continue
        dry = 0
        for b in fresh:
            seen.add(b["id"])

        async def judge(bug):                       # per finding: 3 lenses vote concurrently, real if >= 2
            votes = compact(await parallel([
                (lambda lens=lens: agent(f'Judge "{bug["desc"]}" via the {lens} lens — real?', phase="Verify", schema=VERDICT))
                for lens in ("correctness", "security", "repro")
            ]))
            return bug if sum(1 for v in votes if v.get("real")) >= 2 else None

        confirmed.extend(compact(await parallel([(lambda b=b: judge(b)) for b in fresh])))
    return {"confirmed": confirmed}
```

Use this tool for multi-step orchestration where control flow should be **deterministic** (loops, conditionals, fan-out) rather than model-driven.
