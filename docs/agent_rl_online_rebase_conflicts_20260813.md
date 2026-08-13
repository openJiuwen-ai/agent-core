# Agent RL/SFT Online Rebase Conflict Notes

This note records the conflict decisions made while rebasing the
`agentos-sft-core-dev` online RL/SFT work onto the latest `develop` branch on
2026-08-13.

## Resolution Summary

| Area | Conflict | Resolution | Runtime Impact |
| --- | --- | --- | --- |
| Trajectory model | The feature branch still depended on older online trajectory helpers, while `develop` moved to the canonical OTLP trajectory package. | Keep the `develop` trajectory model and adapt RL/SFT rails to use `TrajectorySpanProcessor`, `Trajectory.from_otlp`, and span helper APIs. | RL/SFT capture must receive a span processor when rails are built. |
| Online rail layout | The feature branch introduced `online/abstract`, `online/core`, and `online/backends`, while old imports still referenced `online/rail/*`. | Keep the refactored layout as the implementation. Re-add `online/rail/*` as compatibility wrappers only. | Existing imports keep working, but new code should import from `online/core` or `online/backends`. |
| Harness rail injection | `DeepAgent` and `harness.factory` both had environment-driven online rail injection paths. | Keep both entry points but de-duplicate online training rails and share a `TrajectorySpanProcessor` with env-created rails. | Prevents duplicate uploads while preserving host-driven env configuration. |
| Harness default rails | `develop` added newer harness rails such as `ModelAnomalyDetectionRail`. | Preserve `develop` defaults and append online training rails after normal default rails. | Online training does not override current harness safety/resilience behavior. |
| Gateway tests | Refactored SFT persistence and management APIs require explicit stores in tests. | Keep the new API surface and update tests to build the expected local stores. | Tests exercise the same gateway shape used by runtime. |
| RL rail tests | RL rail now consumes canonical prepared evolution input instead of legacy step builders. | Update tests to build canonical OTLP trajectories and pass `TrajectorySpanProcessor`. | Unit coverage follows the new capture path. |

## Notes

- The old `openjiuwen.agent_evolving.trajectory` public import is retained from
  `develop`; the removed legacy implementation was not restored.
- `openjiuwen.agent_evolving.agent_rl.online.rail` remains a compatibility
  namespace. It should not grow new implementation logic.
- Manual training trigger semantics are preserved by configuration: set
  `ONLINE_RL_DRAIN_PENDING_ON_TRAIN=1` and trigger through the training API.

## Validation

The rebased branch was validated with real RL and SFT flows using the
`openjiuwen-rl` Conda environment. Both flows used manual API-triggered
training with `ONLINE_RL_DRAIN_PENDING_ON_TRAIN=1` and a high
`TRAIN_THRESHOLD`, so training was not started by the automatic threshold path.

| Flow | Command Surface | Result |
| --- | --- | --- |
| RL online PPO | `examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh start`, then real jiuwenswarm requests plus the manual training API | Uploaded real RL samples, trained `local-web-user:v2` from 4 samples, and hot-loaded the LoRA. |
| SFT local program | `examples/jiuwenrl_online/sft_e2e/run_sft_verl_e2e_local.sh` with 5 local Python cases | Uploaded 5 SFT samples, trained `sft-verl-local-e2e:v1`, generated `adapter_model.safetensors`, and hot-loaded the LoRA. |
| Unit tests | `python -m pytest tests/unit_tests/agent_evolving/agent_rl/online -q` | `113 passed`. |
