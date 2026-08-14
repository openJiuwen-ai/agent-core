# Full-Parameter SFT from Speculative Samples

## 目标

`sft_only/train_sft_full_from_speculative_samples.sh` 用 speculative/trajectory JSON 直接跑 verl SFT。
它和 `sft_only/train_sft_from_speculative_samples.sh` 的区别是：

- 强制 `model.lora_rank=0`，不走 LoRA。
- 默认保存 FSDP sharded checkpoint。
- 支持通过 `SFT_FULL_TRAIN_LAYER_SPEC` 只训练指定 transformer 层，但仍然是全参数层训练，不是 LoRA。
- 支持 CUDA 和 Ascend/A5 通过 `ONLINE_RL_DEVICE_BACKEND` 一键切换。

## 默认数据

脚本默认数据按 profile 区分：

- CUDA / `gpu_smoke`：默认读取 `examples/jiuwenrl_online/sft_only/speculative_sft_sample_trajectory.json`，用于本地快速验通完整训练和 checkpoint 保存。
- Ascend / `a5_27b`：默认读取 `examples/jiuwenrl_online/train_only/long_context_4x50k_trajectories.json`，用于 A5 上验证 Qwen3.5-27B 长上下文资源。

这个文件是 4 条约 50k 上下文的 RL trajectory 格式。全参 SFT 入口会把：

- `trajectory.prompt_text` 转成 user prompt，`loss_mask=0`
- `trajectory.response_text` 转成 assistant target，`loss_mask=1`
- 如果 `prompt_text` / `response_text` 为空，则用 student tokenizer 从 `prompt_ids` / `response_ids` decode 回文本。

如果传入 speculative SFT 格式，即包含 `messages`、`small`、`large` 的样本，也会按原格式读取。

## A5 / Qwen3.5-27B 默认 profile

当设置：

```bash
export ONLINE_RL_DEVICE_BACKEND=ascend
```

且未显式指定 `SFT_FULL_PROFILE` 时，脚本自动使用 `a5_27b` profile：

```bash
STUDENT_MODEL_PATH=/data1/lll/models/Qwen3.5-27B
TRAIN_GPU=4,5,6,7
SFT_FULL_MAX_LENGTH=65536
SFT_FULL_MAX_TOKEN_LEN_PER_GPU=65536
SFT_FULL_ULYSSES_SP=4
SFT_FULL_TRAIN_LAYER_SPEC=last:4
SFT_FULL_SAVE_HF_MODEL=0
SFT_FULL_OPTIMIZER_OFFLOAD=1
SFT_FULL_ACTIVATION_OFFLOAD=1
SFT_FULL_USE_TORCH_COMPILE=0
SFT_FULL_USE_ORIG_PARAMS=1
```

本地 Qwen3.5-27B config 为 64 层、24 attention heads，因此 `SFT_FULL_ULYSSES_SP=4`
满足 `num_attention_heads % sp == 0`。脚本会在启动前校验这个条件。

`SFT_FULL_SAVE_HF_MODEL=0` 是为了避免 27B 在保存 HuggingFace 全量模型时出现额外 CPU/GPU 内存峰值。默认仍保存 FSDP sharded checkpoint；需要导出完整 HF 权重时再显式设置：

```bash
export SFT_FULL_SAVE_HF_MODEL=1
```

## 常用运行方式

A5 上跑 27B 长上下文默认验证：

```bash
cd /data1/lll/workspace/openjiuwen/refactor/agent-core/examples/jiuwenrl_online

export ONLINE_RL_DEVICE_BACKEND=ascend
export TRAIN_GPU=4,5,6,7

bash sft_only/train_sft_full_from_speculative_samples.sh
```

只训练最后 2 层：

```bash
export SFT_FULL_TRAIN_LAYER_SPEC=last:2
bash sft_only/train_sft_full_from_speculative_samples.sh
```

训练指定层：

```bash
export SFT_FULL_TRAIN_LAYER_SPEC=layers:60-63
bash sft_only/train_sft_full_from_speculative_samples.sh
```

本地 GPU 只验证流程，建议用小样本：

```bash
export ONLINE_RL_DEVICE_BACKEND=cuda
export TRAIN_GPU=7
export SFT_FULL_PROFILE=gpu_smoke
export SFT_FULL_MAX_LENGTH=256
export SFT_FULL_MAX_TOKEN_LEN_PER_GPU=256
export SFT_FULL_TRAIN_LAYER_SPEC=last:1
export SFT_FULL_TRAIN_LM_HEAD=0
export SFT_FULL_SAVE_HF_MODEL=0

bash sft_only/train_sft_full_from_speculative_samples.sh sft_only/speculative_sft_sample_trajectory.json
```

## 层选择参数

`SFT_FULL_TRAIN_LAYER_SPEC` 支持：

- `all`：训练全部参数。
- `last:N`：只训练最后 N 层。
- `first:N`：只训练前 N 层。
- `layers:i,j,k`：只训练指定层。
- `layers:start-end`：只训练闭区间层号。

默认不训练 embedding 和 lm head：

```bash
SFT_FULL_TRAIN_EMBEDDINGS=0
SFT_FULL_TRAIN_LM_HEAD=0
```

对 27B 这类大 vocab 模型，默认不训练 `lm_head` 可以明显降低 optimizer state 显存和保存体积。

## 本地验证结果

在本机 CUDA 上用 Qwen3-4B-Thinking-2507、小轨迹、单卡 GPU 7 跑通过：

```text
train_layer_spec=last:1 selected_layers=35
trainable_params=100930816 total_params=4022468096
train/loss=4.5369
checkpoint=records/speculative_sft_full/run_20260714_191653/checkpoints/global_step_1
```

另用 Qwen3.5-27B + `long_context_4x50k_trajectories.json` 做了 prepare-only 校验：

```text
rows=4
parallel_config={'model_type': 'qwen3_5', 'num_hidden_layers': 64, 'num_attention_heads': 24, 'ulysses_sp': 4, 'fsdp_size': -1}
```
