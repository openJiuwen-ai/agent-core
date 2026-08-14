# Online RL Rail and Multi-Trajectory Training Tasks

## Background

Current environment should keep using the existing model path, conda environment, gateway, Redis, vLLM and LoRA repository settings from `examples/jiuwenrl_online/deploy_scripts/online_rl_local_env.sh` and the current launcher scripts.

This note tracks two follow-up optimizations:

1. Enable `RLOnlineRail` trajectory collection/upload without keeping custom source changes in `jiuwenclaw` / `jiuwenswarm`.
2. Let one online RL training trigger consume many collected trajectories and publish one LoRA, instead of training/exporting one LoRA per threshold-sized batch.

## 1. Rail Initialization Should Move Out of JiuwenClaw Source

### Current Diff Against `develop`

The current `jiuwenclaw` branch adds online RL wiring directly into swarm/adapter code:

- `jiuwenswarm/agents/harness/common/rails/rl_online_rail_loader.py`
  - Dynamically imports `openjiuwen.agent_evolving.agent_rl.online.core.rail_factory.build_rl_online_rail_from_env`.
  - Creates `RLOnlineRail` when `USE_RL_ONLINE_RAIL=1`.
- `jiuwenswarm/server/runtime/agent_adapter/interface_deep.py`
  - Imports the loader.
  - Adds `self._rl_online_rail`.
  - Appends the rail in `_build_agent_rails()` and `_get_current_rails()`.
- `jiuwenswarm/server/runtime/agent_adapter/interface_code.py`
  - Imports the loader.
  - Appends the rail in code-mode `_build_agent_rails()`.
- Additional unrelated or profile-control changes also exist in the same diff, so keeping this branch diverged from `develop` increases maintenance risk.

The target is to remove the explicit `new RLOnlineRail` path from `jiuwenclaw` source and let `agent-core` own online RL enablement.

### Existing Hook Point In JiuwenClaw Develop

`jiuwenclaw` already has a user Rail extension mechanism:

- `JiuWenSwarmDeepAdapter.load_user_rails()` loads enabled extensions via `RailManager`.
- `RailManager` reads `${agent_workspace}/extensions/extensions_config.json`.
- Each extension is a folder with `rail.py`; the class is instantiated with no args.
- The team/swarm declarative path also has `swarm.plugin_rails`, but it depends on `RailManager.get_registered_rail_names()`, so it is best treated as a second-phase check.

This means `agent-core` can install a runtime extension instead of patching `interface_deep.py` / `interface_code.py`.

### Recommended Design

Added an `agent-core` installer script:

```text
agent-core/examples/jiuwenrl_online/deploy_scripts/install_rl_online_rail_extension.py
```

The script:

- Locate the JiuwenClaw agent workspace, preferably via an explicit env/config:
  - `JIUWENCLAW_AGENT_WORKSPACE`
  - fallback to JiuwenClaw's default workspace only if importable.
- Create:
  - `${workspace}/extensions/rl_online/__init__.py`
  - `${workspace}/extensions/rl_online/rail.py`
  - `${workspace}/extensions/extensions_config.json`
- Mark the extension enabled in `extensions_config.json`.
- Avoid changing tracked `jiuwenclaw` source files.

`deploy_scripts/online_rl_backend.sh start` now calls this installer automatically with:

```bash
--agent-workspace "${JIUWEN_DATA_DIR}/agent/workspace" --force
```

`rail.py` should be a no-arg wrapper class because `RailManager` instantiates classes with `rail_class()`:

```python
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.agent_evolving.agent_rl.online.core.rail_factory import build_rl_online_rail_from_env


class RLOnlineExtensionRail(AgentRail):
    priority = 100

    def __init__(self):
        self._inner = build_rl_online_rail_from_env()

    def __getattr__(self, name):
        if self._inner is None:
            raise AttributeError(name)
        return getattr(self._inner, name)

    def init(self, agent):
        if self._inner is not None and hasattr(self._inner, "init"):
            self._inner.init(agent)

    def uninit(self, agent):
        if self._inner is not None and hasattr(self._inner, "uninit"):
            self._inner.uninit(agent)

    def get_callbacks(self):
        if self._inner is None:
            return {}
        return self._inner.get_callbacks()
```

Runtime enablement then becomes environment-only:

```bash
export USE_RL_ONLINE_RAIL=1
export TRAJECTORY_GATEWAY_URL=http://127.0.0.1:18080
export TRAJECTORY_GATEWAY_API_KEY=
export RL_ONLINE_TENANT_ID=local-web-user
```

### Validation Tasks

- [x] Add installer script in `agent-core`.
- [x] Auto-install the wrapper before `deploy_scripts/online_rl_backend.sh` starts JiuwenSwarm.
- [x] Verify installer output in a temporary workspace.
- [ ] Run JiuwenClaw from a clean `develop` checkout plus installed extension.
- [ ] Verify logs show the extension Rail loaded by `load_user_rails()`.
- [ ] Verify `RLOnlineRail` uploads to `/v1/gateway/upload/batch`.
- [ ] Check code-mode and deep-mode separately.
- [ ] Check team/swarm mode; if `swarm.plugin_rails` does not pick up enabled extensions before `hot_reload_rail`, document that limitation or add an upstream-friendly generic enabled-extension provider.

## 2. One Training Trigger Should Consume Many Trajectories

### Current Behavior

Online scheduler path:

- `OnlineTrainingScheduler._poll_once()`
  - Calls `get_users_above_threshold(min_samples_for_training)`.
  - Fetches exactly `min_samples_for_training` via `fetch_and_mark_training(user_id, min_samples_for_training)`.
  - Starts one `_train_batch(...)`.
- `PPOTrainingExecutor.train_batch(...)`
  - Converts the received samples into one `DataProto`.
  - Calls one `OnlineTaskRunner.train_on_batch(...)`.
  - Exports and publishes one LoRA immediately.

So if `threshold=4`, every four pending trajectories train and export one LoRA. If a day collects 100 trajectories, the current scheduler naturally produces many small LoRA versions over time instead of one adapter trained over all pending trajectories.

The direct JSON path already loads all samples from a file:

- `examples/jiuwenrl_online/train_only/train_online_rl_from_trajectory_json.py`
- `examples/jiuwenrl_online/train_only/train_online_rl_from_samples.sh`

However, that direct path currently passes all loaded samples into one `train_batch`, so it is not yet a robust replacement for scheduled multi-trajectory training when the sample count or sequence length is large.

### Recommended Design

Separate three concepts:

- `train_trigger_threshold`: minimum pending samples needed to start a run.
- `train_drain_limit`: maximum samples to claim for this run.
- `ppo_samples_per_step`: PPO mini training chunk size, defaulting to the old threshold/batch behavior.

Implemented env/config names:

```bash
export TRAIN_THRESHOLD=4
export ONLINE_RL_DRAIN_PENDING_ON_TRAIN=1
export ONLINE_RL_MAX_SAMPLES_PER_RUN=0       # 0 means drain all currently pending
export ONLINE_RL_PPO_SAMPLES_PER_STEP=4      # keep old 4-sample PPO step size
```

Scheduler logic:

1. User reaches `TRAIN_THRESHOLD`.
2. Query pending count for that user.
3. If drain is enabled:
   - fetch `min(pending_count, ONLINE_RL_MAX_SAMPLES_PER_RUN)` samples, or all pending when max is `0`.
4. Mark all fetched samples as `training`.
5. Call executor once.

Executor logic:

1. Split samples into chunks of `ONLINE_RL_PPO_SAMPLES_PER_STEP`.
2. For each chunk:
   - convert chunk to `DataProto`;
   - call `OnlineTaskRunner.train_on_batch(...)`.
3. Export/publish LoRA only once after the final chunk.
4. Metadata should include:
   - total sample count;
   - number of PPO steps;
   - chunk size;
   - avg judge score;
   - per-step numeric metrics summary.

This satisfies "one trigger consumes 100 trajectories and generates one LoRA" without requiring one giant `DataProto` batch. It also preserves the current 4-sample training memory profile.

### Edge Cases

- If `ppo_samples_per_step=4` and 100 samples are available, one run performs 25 PPO steps and exports one LoRA.
- If the last chunk has fewer than `ppo_samples_per_step`, either train it if veRL accepts the batch, or keep it pending. Safer default: keep undersized tail pending unless `ONLINE_RL_ALLOW_PARTIAL_LAST_STEP=1`.
- If one chunk fails, mark the whole claimed run failed or reset unprocessed samples back to pending. Safer first implementation: mark all claimed samples failed and keep detailed run logs.
- Long-context samples should use chunking. A single `DataProto` with 100 long trajectories can OOM even if four-at-a-time works.

### Implementation Tasks

- [x] Add scheduler config:
  - `drain_pending_on_train: bool`
  - `max_samples_per_run: int`
  - `ppo_samples_per_step: int`
  - `allow_partial_last_step: bool`
- [x] Wire CLI/env in online launcher and `deploy_scripts/online_rl_local_env.sh`.
- [x] Update `OnlineTrainingScheduler._poll_once()` to fetch pending count and choose fetch limit.
- [x] Update `PPOTrainingExecutor.train_batch()` to train multiple chunks and export once.
- [x] Update direct JSON script to use the same chunked executor path.
- [x] Add logs:
  - claimed samples;
  - chunk index/count;
  - exported LoRA path;
  - sample ids for failures.
- [ ] Add unit tests with 10 fake samples, threshold 4, chunk size 4:
  - expected one training run;
  - expected 3 chunks when partial is enabled or 2 chunks plus 2 pending when disabled;
  - expected one LoRA publish.
- [ ] Add Redis store test for draining all pending samples.

## Open Questions

- Should online scheduled training drain all pending samples by default, or only when explicitly enabled? Recommended default: disabled for backward compatibility.
- For daily 100+ trajectories, should training be user-level only (`user_id`) or support a domain-level shared LoRA (`tenant/domain`)?
- Should low-score samples be filtered, weighted, or all included? Current PPO path includes score as reward, so keep all initially.
- Should failures reset samples to pending instead of marking failed? For long runs, reset unprocessed samples is better but needs chunk-level bookkeeping.

## Current Recommendation

Use the extension installer approach first. It is now implemented and is the smallest path to keep `jiuwenclaw` close to `develop`.

For multi-trajectory training, chunked multi-step PPO with one final LoRA export is now implemented. This directly matches the target of collecting 100+ trajectories while keeping the current 4-sample memory profile when `ONLINE_RL_PPO_SAMPLES_PER_STEP=4`.
