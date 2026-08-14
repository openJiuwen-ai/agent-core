# Online RL Evaler Design

## Current Design

The online RL scheduler exposes an optional evaluation extension through the
generic plugin protocol in
`openjiuwen.agent_evolving.agent_rl.online.scheduler.plugins`:

- `EvalRequest` carries the user, LoRA, base model, samples, and training
  metadata.
- `EvalResult` carries pass/fail status, score, target score, reason, and
  metrics.
- `call_evaler` accepts either an object with `evaluate(request)` or a
  callable and normalizes its result.
- `PPOTrainingExecutor` invokes the extension after publishing a LoRA when an
  evaler was configured.

This protocol is independent of the task rollouter and sandbox
implementations. A rollouter creates training data; an evaler validates a
published model. They should not share lifecycle or transport assumptions.

## Removed Built-In Evaler

The repository previously included a built-in SWE-bench evaluator under
`online/evaler/`. It bundled:

- a fixed list of SWE-bench instances;
- a Yuanrong/SWE-ReX execution path;
- model API parsing and patch extraction;
- standalone evaluation scripts and infrastructure tests.

That implementation is removed from the online runtime because it is a
product-specific evaluation workflow rather than a required part of the
online SFT or PPO training loop. Keeping it in the core package caused three
problems:

1. The built-in dataset and evaluator policy could be mistaken for the
   production evaluation contract.
2. It coupled PPO training to SWE-bench and Yuanrong-specific dependencies.
3. It duplicated the newer task-rollout and sandbox boundaries.

The generic scheduler plugin protocol remains so an application can provide an
external evaluator without changing the scheduler or trainer.

## Replacement Extension Point

Applications that need evaluation should provide a separate package or an
example-local plugin:

```python
from openjiuwen.agent_evolving.agent_rl.online.scheduler.plugins import EvalRequest, EvalResult


class MyEvaler:
    async def evaluate(self, request: EvalRequest) -> EvalResult:
        # Run application-specific checks against request.lora_path.
        return EvalResult(passed=True, reason="application checks passed")
```

Configure it with the existing `--evaler package.module:MyEvaler` option or
the equivalent `training.evaler` setting. The core package does not prescribe
the dataset, sandbox provider, API format, or pass@k policy.

## Relationship To Rollout

The supported rollout direction is:

```text
task mapping
  -> task rollouter backend
  -> jiuwenswarm + supervisor model
  -> SFT/RL trajectory upload
  -> scheduler training
  -> LoRA publication
  -> optional external evaler
```

The local Python and Yuanrong/Akernel rollouter backends are data collection
backends. They do not depend on the removed SWE-bench evaler. Yuanrong is
available as a sandbox execution backend and may also be used by an external
evaluation plugin when an application needs that behavior.

## Recovery Plan

If a built-in evaluator is needed again, add it as a separately versioned
plugin or under `examples/`, with:

1. an explicit dataset/configuration input rather than a fixed production
   dataset;
2. a provider-neutral evaluator protocol adapter;
3. isolated optional dependencies;
4. integration tests that do not run during the default unit-test suite;
5. a clear ownership boundary between evaluation and rollout.
