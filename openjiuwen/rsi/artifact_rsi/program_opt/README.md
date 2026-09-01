# `program_opt` — program artifact optimization

`ScienceDiscoveryProgramProvider` implements `ProgramArtifactProvider` on top of
the ScienceDiscovery PUCT search, vendored under `sciencediscovery/`.

```python
from openjiuwen.rsi.artifact_rsi.program_opt import ScienceDiscoveryProgramProvider

provider = ScienceDiscoveryProgramProvider()
result = await provider.run(request, on_event)
```

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
ScienceDiscoveryProgramProvider(sandbox_backend="bwrap")
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
  "options": {"c_puct": 1.0, "prior_exponent": 1.0}
}
```

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

## Not implemented

`pause` returns `NOT_IMPLEMENTED`. The search has no state between node
boundaries, so `terminate` + `resume` is the honest equivalent — it stops at a
node boundary, keeps everything measured, and continues the same tree. Pausing
would have to hold the task in the contract's non-terminal `paused`, which this
search cannot do.
