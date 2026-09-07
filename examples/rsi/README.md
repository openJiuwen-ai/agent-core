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
