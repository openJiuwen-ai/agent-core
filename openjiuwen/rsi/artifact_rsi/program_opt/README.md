# `program_opt` — program artifact optimization

`ProgramArtifactProvider` optimizes a program with a PUCT search, ported from
ScienceDiscovery's evolve service. It is the implementation, not a protocol
restating one — the structural contract is `artifact_rsi.provider.
ArtifactProvider`, and `isinstance` still checks against it.

The search engine ships as an optional extra:

```bash
pip install 'openjiuwen[program-opt]'
```

It is pinned exactly (`agentdescent==0.4.6`) rather than floored: the vendored
files below are copied out of that release's `examples/` and are written against
internals — `FlatPuct`, `Candidate.prior`, `AggregatorConfig`, `vv_staleness`,
`Ledger`, `get_policy` — that promise no stability across releases. Without the
extra, `run` fails with `SEARCHENGINEUNAVAILABLE` and the install line; nothing
else in `openjiuwen.rsi` is affected.

```python
from openjiuwen.rsi.artifact_rsi.program_opt import ProgramArtifactProvider

provider = ProgramArtifactProvider()
result = await provider.run(request, on_event)
```

Everything lives flat in this package. `provider` is the contract surface,
`runtime` answers model and sandbox, `state` does the event projection, and
`puct_engine` plus what it imports is the search itself. `domain`, `program`,
`sandbox`, `search` and `tree` are vendored from AgentDescent's ERA example
(https://github.com/Birfy/agentdescent @ b3d4240, `examples/era/*`) — the
algorithm bodies live under `examples/`, which the wheel does not package, so
they are copied while the engine (`evolve()`, `FlatPuct`, `Ledger`, the
policies) stays a normal dependency.

The search runs **in this process**, not behind HTTP. ScienceDiscovery deploys it
as a sidecar because its control plane must not hold a provider key; here the
engine's `completion_factory` seam is filled directly, so no key crosses a
process boundary and there is no proxy to stand up.

## Configuring the model

`ArtifactEngineRequest.model_config` is `model_refs["optimizer"]` — a **path** to
the same YAML/JSON the rest of RSI uses:

```yaml
model_client_config:
  api_base: https://your-endpoint/v1     # the /v1 root; /chat/completions is appended
  api_key: ${OPTIMIZER_API_KEY}          # ${VAR} is expanded from the environment
  timeout: 900                           # seconds; one rewrite is minutes, not seconds
model_request_config:
  max_tokens: 32000                      # see below
  thinking: disabled                     # "enabled" / "disabled" / omit for the provider default
```

Read rather than resolved through `Model`: the search sends one prompt and reads
one string back, so the platform client's retry rails and streaming would go
unused.

Two numbers are worth setting deliberately.

**`max_tokens`.** The default is 32000 and the floor is real: a reasoning model
bills hidden thought against the same budget, and at 16000 it spent the entire
allowance thinking and returned nothing — six times running, at exactly 16001 of
16000 permitted tokens. An empty reply becomes a failed candidate, which reads as
a model that cannot write code rather than a ceiling set too low. `RunSpec`'s own
default is that 16000, so the value here is what the provider passes down.

**`thinking`.** On, a reasoning model spends roughly forty times the output
tokens per call and writes better candidates. That trade belongs to whoever pays
for the run, so it is configuration rather than a default this module picks.

## Configuring the sandbox

**Isolation is not optional and the contract has no field for it.** A program
optimizer executes code a model wrote, dozens of times per task; without a
sandbox one candidate can read the task's own model key, reach the network, or
write outside its scratch directory. A run with no backend is refused.

The backend is detected: `bwrap` on Linux, `sandbox-exec` (seatbelt) on macOS.
Nothing to configure when one of those is on the host — install `bubblewrap` if
neither is.

Deployments that know better than the probe can name one:

```python
ProgramArtifactProvider(sandbox_backend="bwrap")
```

This is deliberately *not* routed through `openjiuwen/extensions/sys_operation/
sandbox` — those providers isolate an agent's shell tools, where this isolates a
single short-lived Python evaluation and needs the container corrections
(`--disable-userns`, the procfs fallback) the vendored probe already carries.
Swapping the two is a contained change: `runtime.require_sandbox` is the only
caller.

## What the task directory must contain

`ArtifactEngineRequest` carries six fields, and the search needs one more thing
that none of them can hold: **how a candidate is scored**. It is read from
`<run_dir>/scorecard.json`, and a task without one is refused rather than scored
by something this provider made up.

```json
{
  "scorecard": {"aggregate": "weighted_sum", "criteria": [...], "constraints": []},
  "hash": "sha256:...",
  "statement": "bring the reconstruction error down",
  "script": "def evaluate(...): ...",
  "rubric": "",
  "packages": ["xgboost"],
  "entrypoint": "candidate.py",
  "options": {"c_puct": 1.0, "prior_exponent": 1.0}
}
```

`entrypoint` names the file the evaluator imports; it defaults to
`candidate.py` and is only needed when the seed is a directory whose entrypoint
cannot be guessed.

`packages` takes **bare distribution names, optionally `==version`, and nothing
else** — paths, URLs, VCS refs and pip options are refused. The field is written
by a model working out from the task that a boosting library is wanted, so
readability is the whole security boundary.

The provider writes `state.json`, `report.json`, `nodes.json` and `tree.json`
into the same directory, atomically, *before* emitting the event that announces
them. That is what lets `read_state` / `read_report` / `get_tree` answer after a
restart, and what lets `resume` continue the original tree instead of starting a
second root. Set `SCIENCE_AGENT_RSI_RUNS` to the parent of every task directory
so the queries can still find a task the process no longer remembers.

## What is being optimized: a file tree

The program is `{relative path: text}`, not one file. `artifact_path` may be a
single `.py` — placed at `candidate.py`, which is what every one-file run has
always been — or a **directory**, which keeps its own layout so a seed that is
already a package is not renamed into this provider's conventions.

Serialisation is `agentdescent.filetree`: `load_tree` reads the directory,
`canonical` / `parse_tree` are the lossless pair the genome travels as (the
engine caches evaluations on the rendered string, so two different programs must
never render alike), and `materialize` writes it back — re-validating every
relative path, which is what stops a model-authored `../../etc/passwd`.

**The evaluator contract does not change.** It is still told the entrypoint's
path through `SCIENCE_AGENT_CANDIDATE` and still does `import candidate`; a
multi-file program simply means `candidate` is a package, or that it imports
siblings that are now on `sys.path` beside it.

Which file is the entrypoint is guessed only where the guess cannot be wrong:
`candidate.py`, `candidate/__init__.py`, or a tree with exactly one Python file.
A directory of five modules is refused with `ARTIFACT_ENTRYPOINT_UNCLEAR` rather
than guessed at — picking one would send every candidate to an evaluator
importing the wrong module, and the run would report that the program never
works. Set `entrypoint` in `scorecard.json` to say.

### What the model returns

**Only the files it changed**, each in its own fenced block labelled with its
path; a path that does not appear is inherited from the parent. A model asked to
restate ten files to change one spends the tokens on nine copies and rewrites the
nine. `DELETE path/to/file.py` on its own line removes a file — never the
entrypoint, without which every later candidate would fail identically. A single
unlabelled block is still the entrypoint, which is what a one-file run looks
like.

Each candidate lands in `<run_dir>/candidates/<digest>/` as a real directory you
can open and run, with the serialised tree beside it so a resumed run rebuilds
the exact string the hash was taken over.

## The pre-flight

`run` scores the starting program, then scores a copy deliberately made worse,
before it draws a single candidate. Two numbers that come back the same mean the
scoring cannot separate a good candidate from a bad one — and a search on flat
terrain is a random walk that looks completely normal from outside: every event
fires, every candidate is recorded, and the run reports that it found nothing.
A refusal here costs a handful of evaluations and returns `PROBE_REFUSED` with a
sentence naming what is wrong with the scorecard.

## What a candidate can read

Nothing from this process's environment. Both backends are given the same
allowlist — thread caps, a scratch `TMPDIR` and `HOME`, and `PATH=/usr/bin:/bin`
— and the network is denied. This is stricter than the sidecar the code came
from, and it has to be: there the surrounding process held no provider key,
here it holds every one of them.

## Not implemented

`pause` returns `NOT_IMPLEMENTED`. The search has no state between node
boundaries, so `terminate` + `resume` is the honest equivalent — it stops at a
node boundary, keeps everything measured, and continues the same tree. Pausing
would have to hold the task in the contract's non-terminal `paused`, which this
search cannot do.

## Divergence from ScienceDiscovery

Multi-file support was added here and not in ScienceDiscovery's evolve service,
which the search was ported from. The two copies of the engine have diverged
from that point: a fix on either side has to be carried across by hand.
