# RSI dataset adapters

The public engine entry point remains
`SingleHarnessIterativeOptimizationOrchestrator.run()`. Pass a benchmark suite
through the existing `IterativeSingleHarnessRequest.dataset_files` field; no
benchmark-specific request type is required. The loader accepts the original
`cases` forms and suite files containing one `validation` or `evaluation` list.
Relative `assets_dir` values are resolved from the suite file, so callers keep
the extracted dataset tree unchanged.

`run_single_harness.py` is a standalone example and debugging facade, not a
replacement service interface. Dataset schemas and official evaluation
protocols stay in adapter modules; the RSI optimization engine stays
benchmark-neutral.

## Scoring requirements

Execution completion is not a correctness grade. The default `script-based`
judger requires a backend-provided `JudgeResult`, official SWE-bench metadata,
or an explicit answer in `reference.answer`, `reference.expected_output`,
`reference_answer`, or `expected_output`. `exact_match` also requires an answer.
`expected_files` or an output filename alone does not validate file contents.

For open-ended tasks, use the benchmark's evaluator or inject an
`EvaluationJudger` through `CaseRunner`; the direct engine does not automatically
select an LLM judge just because a model configuration exists. Missing grading
evidence raises `EvaluationInfrastructureError`, leaves `evaluation_error.json`,
and does not publish a baseline score or feed a fabricated failure to Analyzer.
Existing request/event fields and native plugin loading are unchanged.

Older `backend_completed`/`method=none` scores are invalid evaluation evidence.
They are rejected by result aggregation and case-evidence reuse. Preserve those
runs for diagnosis, but use a new run with a real evaluator rather than resuming
an already-completed run with a completion-only baseline.

## SWE-bench verifier on Windows

The Windows verifier runs the official SWE-bench harness in WSL. Configure the
service process to use the WSL environment where SWE-bench is installed:

```powershell
$env:SWEBENCH_WSL_DISTRO = "Ubuntu-24.04"
$env:SWEBENCH_WSL_PYTHON = "/path/to/swebench-venv/bin/python"
wsl.exe -d $env:SWEBENCH_WSL_DISTRO -- $env:SWEBENCH_WSL_PYTHON -c "import swebench.harness.run_evaluation, docker; assert docker.from_env().ping()"
```

Run that check before starting AgentServer, which inherits these environment
variables. Explicit case metadata `swebench.wsl_distro` and
`swebench.python_path` takes precedence. With no overrides the defaults remain
`Ubuntu-24.04` and `python3`. WSL overrides do not affect native Linux execution.
Dataset validation alone does not verify this interpreter or Docker access.

Dependency manifests are cached by repository and setup commit in
`~/.cache/openjiuwen/rsi/swebench_dependency_cache`, independent of the service
working directory. To keep using an existing cache, set
`SWEBENCH_DEPENDENCY_CACHE_ROOT` to its absolute root before starting the service.
Completed revision caches are reused without downloading again.

Host-side manifest downloads honor the standard Requests proxy settings. An
optional `SWEBENCH_DOWNLOAD_PROXY` overrides the proxy for these downloads only,
not model calls or WSL processes. Transient connection/time-out and HTTP
429/500/502/503/504 failures get at most three attempts, with 1 and 2 second
backoff. Failed downloads do not publish a complete-cache marker.

## Frontend iteration limit

The Harness engine accepts `IterativeSingleHarnessRequest.max_iteration`.
One iteration is one complete epoch, including its batches and final evaluation.
The frontend sends a positive integer, for example `{"max_iteration": 3}`.
The service forwards it through the existing entry point:

```python
request = IterativeSingleHarnessRequest(
    dataset_files=[suite_path],
    harness_refs_path=harness_refs_path,
    output_dir=run_dir,
    max_iteration=payload.get("max_iteration"),
)
result = await orchestrator.run(request, on_event=on_event)
```

Omission preserves `config.max_epochs` (default 5). An explicit value overrides
that configuration for this run only; zero, negative values, booleans, strings
and fractional values are rejected. A resumed run keeps its original limit;
a conflicting explicit limit is rejected. This does not change batch size,
candidate repair budgets or the task agent's internal step limit.
It is an upper bound: existing completion/early-stop conditions still apply.

Progress `iteration` counts completed epochs and `total_iterations` is the
effective `max_iteration`. The initial H0 node has iteration 0. Each completed
epoch emits one further node; individual cases and candidate attempts remain
in the run's detailed records, not additional tree nodes. If the final selected
Harness differs from the evaluated version, its node has no score until that
exact version is evaluated. The AgentServer HTTP handler is maintained by the
caller; this repository provides the engine request and event contract.

## Full H0 before optimization

Set `IterativeSingleHarnessRequest.auto_full_baseline=True` to evaluate every
input case with the unchanged initial Harness before entering the existing
epoch/batch loop. The engine default remains `False` for existing CLI callers;
AgentServer enables it for newly created Harness tasks. Resumes must retain the
value recorded in the run fingerprint, including older baseline-free runs.

The complete evaluation is saved under `evaluations/frozen_baseline`. Before
completion, `baseline` is `null`, not a partial-batch average or a fabricated
zero. Once complete, the engine persists `baseline_score` and emits the existing
`EventProgress.baseline` and an updated H0 `EventNode.score`, including a real
zero score. AgentServer exposes these through `rsi.task.get` (`progress.baseline`),
`rsi.report.get` (`baseline`), and `rsi.tree.get` (ROOT `score`), plus its existing
progress/tree pushes. The frontend does not need a new request or response field.

H0 uses iteration 0 and does not consume `max_iteration`. The root node is emitted
before evaluation so per-case stages can be displayed while H0 is running.
Resuming a completed baseline reuses its persisted evaluation; subsequent Harness
scores never overwrite the frozen baseline.

Epoch node parents describe Harness inheritance, not execution order. A rejected
or unchanged epoch does not become the parent of the next attempt: attempts
starting from H0 point to `h0` (mapped to `ROOT` by AgentServer), and attempts
starting from an adopted Harness point to the epoch that adopted that version.
Live events and recovered tree queries use the same projection. Existing
`score`, `adopted`, and `reason`/`failure_reason` fields remain unchanged in shape;
an unretained change is not automatically labeled a score-comparison rejection.

## Model calls and token usage

The same `on_event` callback additionally receives `EventUsage`
(`event_type="progress.usage"`, `family="progress"`, `kind="usage"`). Existing
progress/node event shapes and the epoch/batch optimization algorithm are unchanged.
Pass the service's `task_id` in `IterativeSingleHarnessRequest` for attribution.

| Field | Meaning |
| --- | --- |
| `task_id`, `ts`, `event_id` | Task identity, UTC timestamp, increasing usage-record sequence |
| `call_id` | Stable request id; deduplicate by `(task_id, call_id)` |
| `node_ref` | `h0` for the baseline; `epoch-001`, etc. for an iteration |
| `stage_ref` | `evaluate`, `judge`, `analyze`, or `optimize` |
| `model_call.model`, `call_count` | Model name and one call, not a stage total |
| `model_call.tokens` | `input`, `output`, `cache_hit` raw provider counters |
| `model_call.status`, `duration_ms` | `succeeded`/`failed`/`incomplete` and elapsed time |

```python
from dataclasses import asdict
from openjiuwen.rsi.events import EventUsage

async def on_event(event):
    if isinstance(event, EventUsage):
        await usage_recorder.record(asdict(event))  # service-owned persistence/push
    else:
        await handle_existing_engine_event(event)
```

`EventProgress.usage`, `single_harness_state.yaml` and the final report contain
the cumulative snapshot. Do not add that snapshot to the per-call deltas again.
The adjacent `model_calls.jsonl` retains each delta before callback delivery.
Resume restores totals without re-emitting old deltas; the service can reconcile
missed deliveries from that ledger using `call_id`, not the debug sequence.
An iteration reference can appear in usage before its completed tree node arrives.
Runs created before this instrumentation have no recoverable historical counters;
resuming them records only new calls, not an estimate for their old work.

Unknown provider counters are `null`, not zero. A cumulative counter is also
`null` if any contributing call omitted it; known per-call counters remain in
the ledger. Cache hits are a subset of input, and reasoning tokens are already
part of output: neither is added a second time. `cost_estimate` stays `null`;
pricing belongs to the service. Failed observed requests count as calls even if
the provider returns no usage. Streaming chunks do not increment call count.
SDK-internal HTTP retries that emit no core callback cannot be counted separately.
No prompts, responses, API keys, endpoint URLs or raw exceptions enter these events.

Automatic capture covers in-process core `Model` calls, including Task Agent,
Analyzer, Improver and an LLM Judge. External evaluators (WSL/E2B subprocesses or
independent SDK clients) must report their own per-call usage to the parent with
`openjiuwen.rsi.usage.record_model_usage(model=..., call_id=..., usage=...,
stage_ref="judge")`. Their calls are not silently inferred from scores or trace
text. The standalone Evo-Bench subprocess currently does not export these deltas,
so its Task Agent/Judge usage is **not included** until that adapter implements
the hook. Deterministic test-based judges make no model calls.

## Diagnosis and candidate generation

The Harness engine uses the migrated experiment-side diagnosis flow: a bounded,
read-only DeepAgent reads each failed case, its full execution history and the
Harness declarations that produced that evaluation. A short evidence summary
is an index, not a replacement for the original history. Tool and Rail code is
not executed while projecting Harness declarations, and model credentials are
not included in that projection.

Per-case diagnoses are compiled into Issues deterministically, without a second
model rewriting their attribution. The Improver receives the same required
behavior and may generate Prompt, Skill, Tool or Rail changes permitted by the
existing action policy. A single-case Skill is not automatically rewritten into
a Prompt. Missing generated Prompt/Skill files are failures, not fabricated
placeholder improvements. Candidate isolation, Plugin loading, verification and
evaluation remain on the target branch's runtime; the legacy experiment runtime
is not copied into this package.

A failed model diagnosis is not reported as "no issues": its failure artifact
is preserved, and resuming retries that diagnosis. Successful partial diagnoses
remain usable. Existing batch optimization, candidate acceptance and final epoch
evaluation are retained. This migration's unit tests validate those contracts;
they do not establish benchmark score improvement.

## Portable dataset example

The Evo-Bench adapter accepts the portable task layout below:

```text
task/<id>/
  harness/harness_refs.yaml
  models/evaluation.yaml
  models/analysis.yaml
  models/member_optimization.yaml
  models/judge.yaml                 # optional
```

Model YAML files may reference credentials with `${ENVIRONMENT_VARIABLE}`.
The repository example under `tasks/evobench_gdpval` contains no credentials.
For General/GDPval local execution, neither `E2B_API_KEY` nor
`SERPER_API_KEY` is required. Keep the suite's `assets` directory beside the
suite file; no separate asset argument is needed.

From the repository root, prepare the environment and run one batch-oriented
optimization directly. A separate full-suite H0 run is not required:

```powershell
uv sync --extra observability

$env:RSI_EVALUATION_API_BASE = "https://evaluation.example/v1"
$env:RSI_EVALUATION_API_KEY = "..."
$env:RSI_ANALYSIS_API_BASE = "https://analysis.example/v1"
$env:RSI_ANALYSIS_API_KEY = "..."
$env:RSI_MEMBER_OPTIMIZATION_API_BASE = "https://optimization.example/v1"
$env:RSI_MEMBER_OPTIMIZATION_API_KEY = "..."
$env:RSI_JUDGE_API_BASE = "https://judge.example/v1"
$env:RSI_JUDGE_API_KEY = "..."

uv run python examples/rsi/run_single_harness.py evobench optimize `
  --task-dir examples/rsi/tasks/evobench_gdpval `
  --suite-path D:\data\gdpval\suites\train_suite.json `
  --evobench-root D:\src\Evo-Bench `
  --execution-mode local `
  --run-name gdpval_train_v1 `
  --output-dir .local/rsi/runs `
  --batch-size 1 `
  --max-epochs 1 `
  --sibling-candidate-count 1 `
  --rollout-concurrency 1
```

Use `evobench optimize --help` to inspect adapter-specific options.

## Common case fields (direct engine / AgentServer)

Pass a dataset JSON path as before. Keep declared files beside it when moving
or distributing a dataset packet:

```json
{"cases": [{"case_id": "sum", "input": "Compute 1+2; reply with only the number.", "assets": [], "reference": {"answer": "3"}}]}
```

`case_id` and `input` are required for execution. `assets` lists public files;
`reference.files` lists private grading files. Paths are relative to the JSON,
must exist and cannot escape its directory. AgentServer snapshots both sets,
but only public assets are copied into the solver workspace. Task input never
falls back to the whole case. This is a delivery boundary, not an OS sandbox.
File content hashes participate in resume checks and evaluation evidence reuse.

The built-in `script-based` judger supports explicit reference answers and the
existing official SWE verifier. `exact-match` supports reference answers only.
Both compare answer text exactly, unwrapping the native final-answer transport
envelope without trimming or rewriting its content. Generic `reference.rubric`
and arbitrary private-file grading are not implemented by these judgers and
fail before Task Agent execution rather than receiving a completion-only score.
The separate Evo-Bench adapter above retains its own grading implementation.

Legacy datasets remain supported. Export legacy SWE JSON into the four-field
format without losing its verifier and environment options:

```powershell
python examples/rsi/convert_swebench_dataset.py old_cases.json new_dataset
```

`new_dataset` must not exist. Submit `new_dataset/cases.json`; distribute the
whole directory. Each private `verifier.json` contains
`{"adapter": "swebench_official", "config": {...}}`, retaining the original
SWE configuration with a packet-relative `official_dataset_path`. Both this
manifest and its official record must be listed in `reference.files`. The
loader verifies matching instance IDs and reconstructs the existing runtime
input; neither optimization policy nor official test scoring changes.
