# Best-of-N Multi-Attempt Repair

When a code change fails CI, the default behavior is a **two-phase fix
loop**: the agent reads the error log, attempts a repair, and retries CI.
This works well for simple lint or type errors, but for complex bugs a
single repair strategy can get stuck in a local optimum.

**Best-of-N** generates *N* independent repair attempts, scores each
resulting workspace, and promotes the best patch. This approach mirrors
the technique used by top SWE-Bench systems.

## How It Works

1. **Clone** the current workspace `N` times into isolated directories.
2. **Repair** each clone with a different strategy prompt:
   - Attempt 0: focus on correctness
   - Attempt 1: minimize diff size
   - Attempt 2+: consider edge cases
3. **Score** every clone:
   - Primary: tests passed / tests total
   - Tie-break 1: diff line count (smaller is better)
   - Tie-break 2: lint error count (fewer is better)
4. **Select** the highest-scoring clone.
5. **Promote** it back to the original workspace.
6. **Clean up** the remaining clones.

## Configuration

Enable best-of-N globally via `AutoHarnessConfig`:

```python
from openjiuwen.auto_harness.schema import AutoHarnessConfig

config = AutoHarnessConfig(
    # ... other fields ...
    best_of_n_enabled=True,
    best_of_n_attempts=3,
    best_of_n_timeout_per_attempt=600.0,
)
```

| Field | Default | Description |
|-------|---------|-------------|
| `best_of_n_enabled` | `False` | When `True`, the verify stage uses best-of-N instead of the classic fix loop. |
| `best_of_n_attempts` | `3` | Number of independent attempts to generate. |
| `best_of_n_timeout_per_attempt` | `600.0` | Timeout in seconds for each individual attempt. |

## When to Use

| Scenario | Recommendation |
|----------|---------------|
| Simple lint / type errors | Keep `best_of_n_enabled=False`. The fix loop is faster and sufficient. |
| Complex bug fixes, SWE-Bench tasks | Set `best_of_n_enabled=True`. The 3x compute cost is usually justified by higher pass rates. |
| Terminal / bash-heavy tasks | Best-of-N also helps here because repair strategies vary significantly. |

## Architecture

```
MetaVerifyStage
    |-- if CI passes -> done
    |-- if CI fails
         |-- [best_of_n_enabled=True]
         |    |-- BestOfNController.run()
         |         |-- WorkspaceCloner.clone_n()  -> N workspaces
         |         |-- for each workspace
         |         |    |-- attempt_factory(path, seed)  -> agent repair
         |         |    |-- AttemptScorer.score()        -> test + diff + lint
         |         |-- AttemptSelector.select()          -> best candidate
         |         |-- promote best -> original workspace
         |         |-- remove losers
         |-- [best_of_n_enabled=False]
              |-- FixLoopController.run()  -> classic two-phase fix
```

## Extending

You can plug in custom scorers or selectors:

```python
from openjiuwen.auto_harness.infra.best_of_n import BestOfNController
from openjiuwen.auto_harness.infra.attempt_scorer import AttemptScorer
from openjiuwen.auto_harness.infra.attempt_selector import AttemptSelector

class CoverageScorer(AttemptScorer):
    async def score(self, workspace, ci_runner=None):
        # ... custom scoring logic ...
        pass

class MySelector(AttemptSelector):
    def select(self, candidates):
        # ... custom selection logic ...
        pass

ctrl = BestOfNController(
    n_attempts=5,
    scorer=CoverageScorer(),
    selector=MySelector(),
)
```

## Core Components

| File | Purpose |
|------|---------|
| `openjiuwen.auto_harness.infra.attempt_scorer` | Score a workspace by test passes, diff size, lint errors. |
| `openjiuwen.auto_harness.infra.attempt_selector` | Select the best candidate from scored attempts. |
| `openjiuwen.auto_harness.infra.workspace_cloner` | Clone a workspace N times using `shutil.copytree`. |
| `openjiuwen.auto_harness.infra.best_of_n` | `BestOfNController` -- orchestrates the full pipeline. |
| `openjiuwen.auto_harness.infra.fix_loop` | Classic two-phase CI fix loop (incremental repairs). |
| `openjiuwen.auto_harness.stages.verify` | Verify stage dispatch -- chooses between fix loop and best-of-N. |
