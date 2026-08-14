# Long Context Resource Test

This test uses synthetic trajectory data to approximate a 200k-token training batch:

```text
4 trajectories x 50,000 prompt tokens + 1 response token each
total ~= 200,004 tokens
```

This validates batch-level resource pressure. It does not fully replace a single 200k-token trajectory, because attention cost grows with per-sequence length.

```text
4 x 50k attention cost ~= 4 * 50k^2
1 x 200k attention cost ~= 200k^2
```

## Generate Data

From the repo root:

```bash
python agent-core/examples/jiuwenrl_online/train_only/generate_long_context_trajectories.py
```

Default output:

```text
agent-core/examples/jiuwenrl_online/train_only/long_context_4x50k_trajectories.json
```

Run direct training with:

```bash
bash agent-core/examples/jiuwenrl_online/train_only/train_online_rl_from_samples.sh \
  agent-core/examples/jiuwenrl_online/train_only/long_context_4x50k_trajectories.json
```

For A5/Ascend, run with:

```bash
ONLINE_RL_DEVICE_BACKEND=ascend \
TRAIN_GPU=0,1,2,3 \
bash agent-core/examples/jiuwenrl_online/train_only/train_online_rl_from_samples.sh \
  agent-core/examples/jiuwenrl_online/train_only/long_context_4x50k_trajectories.json
```

## Required Config Changes

File:

```text
agent-core/examples/jiuwenrl_online/deploy_scripts/online_rl_local_env.sh
```

Use four training devices:

```bash
: "${TRAIN_GPU:=4,5,6,7}"
```

For Ascend/A5, set:

```bash
: "${ONLINE_RL_DEVICE_BACKEND:=ascend}"
```

or override at runtime:

```bash
ONLINE_RL_DEVICE_BACKEND=ascend TRAIN_GPU=0,1,2,3 ...
```

File:

```text
agent-core/openjiuwen/agent_evolving/agent_rl/config/online_config.py
```

Increase sequence caps so the 50k-token prompt is not truncated:

```python
"max_prompt_length": 50000,
"max_response_length": 16,
```

For four GPUs, keep per-GPU micro batches at 1:

```python
"ppo_mini_batch_size": 4,
"ppo_micro_batch_size_per_gpu": 1,
```

Also keep ref and rollout log-prob micro batches at 1:

```python
"log_prob_micro_batch_size_per_gpu": 1,
```

There are two such entries under `ref` and `rollout`.

## Expected Verification Logs

During startup, confirm:

```text
Ray initialized for online PPO (...=4,5,6,7)
trainer.n_gpus_per_node: 4
ppo_micro_batch_size_per_gpu: 1
```

During data conversion, confirm:

```text
Converted 4 samples to DataProto (batch_size=4, prompt_width=50000, response_width=1, ...)
```

If `prompt_width` is much smaller than 50000, the data is being truncated by `max_prompt_length`.

## Notes

This file is a resource test, not a meaningful RL training dataset. Rewards and responses are synthetic. Use it only to check memory, sequence handling, FSDP behavior, and long-context throughput.

## Single-Batch 256k Test

Generate one synthetic trajectory whose total length is 256k tokens:

```bash
python agent-core/examples/jiuwenrl_online/train_only/generate_long_context_trajectories.py \
  --samples 1 \
  --prompt-tokens 262143 \
  --response-tokens 1 \
  --sample-id-prefix single-batch-256k \
  --output agent-core/examples/jiuwenrl_online/train_only/single_batch_256k_trajectory.json
```

On A5/Ascend, run the same direct PPO training entry with one PPO sample per step and length caps large enough to avoid truncation:

```bash
ONLINE_RL_DEVICE_BACKEND=ascend \
TRAIN_GPU=0,1,2,3 \
ONLINE_RL_FSDP_MODEL_DTYPE=bfloat16 \
ONLINE_RL_PPO_SAMPLES_PER_STEP=1 \
ONLINE_RL_TRAIN_BATCH_SIZE=1 \
ONLINE_RL_PPO_MINI_BATCH_SIZE=1 \
ONLINE_RL_PPO_MICRO_BATCH_SIZE_PER_GPU=1 \
ONLINE_RL_LOG_PROB_MICRO_BATCH_SIZE_PER_GPU=1 \
ONLINE_RL_SEQUENCE_PARALLEL_SIZE=4 \
ONLINE_RL_MAX_PROMPT_LENGTH=262143 \
ONLINE_RL_MAX_RESPONSE_LENGTH=1 \
ONLINE_RL_ACTOR_PPO_MAX_TOKEN_LEN_PER_GPU=65536 \
ONLINE_RL_REF_LOG_PROB_MAX_TOKEN_LEN_PER_GPU=65536 \
ONLINE_RL_ROLLOUT_LOG_PROB_MAX_TOKEN_LEN_PER_GPU=65536 \
ONLINE_RL_ROLLOUT_MAX_MODEL_LEN=262144 \
ONLINE_RL_ROLLOUT_MAX_NUM_BATCHED_TOKENS=262144 \
bash agent-core/examples/jiuwenrl_online/train_only/train_online_rl_from_samples.sh \
  agent-core/examples/jiuwenrl_online/train_only/single_batch_256k_trajectory.json
```

For local GPU smoke validation, use the same shape with 4k total tokens:

```bash
python agent-core/examples/jiuwenrl_online/train_only/generate_long_context_trajectories.py \
  --samples 1 \
  --prompt-tokens 4095 \
  --response-tokens 1 \
  --sample-id-prefix single-batch-4k-smoke \
  --output agent-core/examples/jiuwenrl_online/train_only/single_batch_4k_smoke_trajectory.json

TRAIN_GPU=6,7 \
ONLINE_RL_FSDP_MODEL_DTYPE=bfloat16 \
ONLINE_RL_PPO_SAMPLES_PER_STEP=1 \
ONLINE_RL_TRAIN_BATCH_SIZE=1 \
ONLINE_RL_PPO_MINI_BATCH_SIZE=1 \
ONLINE_RL_PPO_MICRO_BATCH_SIZE_PER_GPU=1 \
ONLINE_RL_LOG_PROB_MICRO_BATCH_SIZE_PER_GPU=1 \
ONLINE_RL_SEQUENCE_PARALLEL_SIZE=2 \
ONLINE_RL_MAX_PROMPT_LENGTH=4095 \
ONLINE_RL_MAX_RESPONSE_LENGTH=1 \
ONLINE_RL_ROLLOUT_MAX_MODEL_LEN=4096 \
bash agent-core/examples/jiuwenrl_online/train_only/train_online_rl_from_samples.sh \
  agent-core/examples/jiuwenrl_online/train_only/single_batch_4k_smoke_trajectory.json
```
