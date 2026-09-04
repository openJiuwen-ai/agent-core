# RSI dataset adapters

`run_single_harness.py` is the common entry point. Dataset schemas and official
evaluation protocols stay in adapter modules; the RSI optimization engine stays
benchmark-neutral.

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

The same entry point also exposes the official Evo-Bench launcher. Use `--help`
after the dataset and action to inspect adapter-specific options.
