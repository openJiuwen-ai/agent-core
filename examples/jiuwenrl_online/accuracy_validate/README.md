# JiuwenRL Online Accuracy Validate

这组验证用例用于把 online RL 流程拆成可定位的阶段，验证 A5 与 GPU/CUDA 路径在固定输入下是否存在精度或流程差异。

## 验证思路

可行。需要注意的是，vLLM 贪心推理在固定 prompt、`temperature=0`、`top_k=1`、`top_p=1`、`seed` 后，应该具备强确定性；训练 LoRA 由于分布式归约、后端算子和 checkpoint 保存实现不同，不一定保证跨后端字节级完全一致。因此训练阶段同时检查：

- LoRA 产物结构完整性：`adapter_config.json`、`adapter_model.safetensors`、`metadata.json`。
- 同后端重复训练的 adapter hash 或 tensor 差异。
- 两个重复训练 LoRA 热加载后，在相同探针 prompt 下输出是否一致。

如果要做 A5 vs GPU 对比，建议分别在两边运行同一组用例，保留 `artifacts/` 里的 JSON 结果，再比较推理输出签名、DataProto 张量摘要、LoRA tensor 差异。

## 阶段划分

1. `test_01_inference_determinism.py`
   - 同 prompt 重复请求，检查 completion 文本和 token ids 一致。
   - 同一组 prompt 改变请求顺序，检查每个 prompt 的输出不受顺序影响。
   - 检查 vLLM 返回 token ids/logprobs，确保 Rail 采集所需字段可用。

2. `test_02_trajectory_dataproto.py`
   - 固定轨迹样本两次转换 DataProto，检查张量摘要完全一致。
   - 固定截断配置，检查 prompt/response 宽度和截断计数。
   - 检查 PPO 样本切分逻辑，定位“多轨迹一次训练”进入训练前的 batch 边界。

3. `test_03_training_lora_determinism.py`
   - 固定 4 条轨迹训练一次，检查 LoRA 产物完整。
   - 相同模型和轨迹重复训练两次，比较 LoRA 配置、hash 和 tensor 差异。
   - 将两个 LoRA 显式热加载进 vLLM，用相同探针 prompt 比较输出。

## 运行方式

在 A5 上优先用 run 脚本，它会复用 `examples/jiuwenrl_online/deploy_scripts/online_rl_local_env.sh` 的模型路径、conda 环境和端口配置：

```bash
cd /data1/lll/workspace/openjiuwen/refactor/agent-core

export ONLINE_RL_DEVICE_BACKEND=ascend
export ONLINE_RL_VISIBLE_DEVICES_ENV=ASCEND_RT_VISIBLE_DEVICES
export TRAIN_GPU=4,5,6,7
export VLLM_URL=http://127.0.0.1:18002

bash examples/jiuwenrl_online/accuracy_validate/run_st_tests.sh
```

直接执行 `bash run_st_tests.sh` 默认会先检查 `VLLM_URL/health`；如果 vLLM 没起来，会调用 `examples/jiuwenrl_online/deploy_scripts/start_online_rl_services.sh` 自动拉起完整 online-RL 服务栈，然后跑直接 PPO 训练用例。CUDA 测试机上如果未显式指定 `TRAIN_GPU`，脚本会默认使用 `ST_TEST_TRAIN_GPU=6,7`；A5 上建议显式设置 `TRAIN_GPU=4,5,6,7`。

只跑推理和 DataProto 快速检查：

```bash
ST_TEST_RUN_TRAINING=0 bash examples/jiuwenrl_online/accuracy_validate/run_st_tests.sh \
  test_01_inference_determinism.py test_02_trajectory_dataproto.py
```

训练阶段默认会跑两次直接 PPO 训练，耗时较长。关键环境变量：

```bash
ST_TEST_RUN_TRAINING=1 \
TRAIN_GPU=6,7 \
ONLINE_RL_PPO_SAMPLES_PER_STEP=4 \
TRAIN_THRESHOLD=4 \
bash examples/jiuwenrl_online/accuracy_validate/run_st_tests.sh test_03_training_lora_determinism.py
```

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MODEL_PATH` | 来自 `deploy_scripts/online_rl_local_env.sh` | 训练和 tokenizer 使用的基座模型路径 |
| `MODEL_NAME` | 来自 `deploy_scripts/online_rl_local_env.sh` | vLLM 请求中的模型名 |
| `VLLM_URL` | `http://127.0.0.1:18002` | vLLM OpenAI API 地址 |
| `TRAIN_GPU` | 来自 `deploy_scripts/online_rl_local_env.sh` | 训练可见卡，如 A5 `4,5,6,7` |
| `ONLINE_RL_DEVICE_BACKEND` | `cuda` | A5 设置为 `ascend` |
| `ONLINE_RL_VISIBLE_DEVICES_ENV` | 按 backend 推导 | A5 设置为 `ASCEND_RT_VISIBLE_DEVICES` |
| `ST_TEST_RUN_TRAINING` | run 脚本中为 `1` | 置 `0` 跳过训练 LoRA 用例 |
| `ST_TEST_TRAIN_GPU` | CUDA 下为 `6,7` | 当用户未显式设置 `TRAIN_GPU` 时，ST 使用的 CUDA 训练卡 |
| `ONLINE_RL_FSDP_MODEL_DTYPE` | CUDA 下 `fp16`，A5 下 `bf16` | 覆盖 VERL FSDP 加载模型 dtype，避免默认 fp32 放大显存压力 |
| `ST_TEST_STRICT_LORA_NUMERIC` | `0` | 置 `1` 后要求重复训练 LoRA tensor 数值差异不超过阈值 |
| `ST_TEST_LORA_MAX_ABS_DIFF` | `1e-4` | 严格数值模式下的 LoRA tensor 最大绝对误差阈值 |
| `ST_TEST_REQUIRE_EXACT_LORA_HASH` | `0` | 置 `1` 要求 adapter 文件 hash 完全一致 |
| `ST_TEST_AUTO_START_SERVICES` | `1` | vLLM 未健康时自动执行 `deploy_scripts/start_online_rl_services.sh`；置 `0` 可只复用现有服务 |

当前 GPU 实测中，固定输入、固定采样和固定 ST seed 后，vLLM 推理可重复通过；直接 PPO 两次训练均可生成 LoRA 且热加载后探针输出一致，但 adapter tensor 仍存在约 `4e-2` 量级差异。因此默认不把 LoRA tensor 字节级/数值级一致作为通过条件，而是生成 `lora_repeat_compare.json` 供 A5/GPU 横向比较。需要排查训练强确定性时再打开 `ST_TEST_STRICT_LORA_NUMERIC=1`。

## LoRA 漂移包络

由于多卡 FSDP/bf16 训练难以保证 LoRA tensor 字节级一致，推荐用 GPU 重复训练得到“正常数值漂移包络”，再在 A5 上验证不超过该包络。这个验证目标是判断 A5 是否出现超出 GPU 正常波动的异常漂移，而不是证明两边 bitwise 一致。

在 GPU/CUDA 环境先跑 10 次固定轨迹训练，脚本会做 `C(10, 2)=45` 组两两比较，并生成建议阈值：

```bash
cd /data1/lll/workspace/openjiuwen/refactor/agent-core

python examples/jiuwenrl_online/accuracy_validate/measure_lora_drift_envelope.py \
  --mode baseline \
  --runs 10 \
  --train-gpu 6,7 \
  --work-dir /tmp/jiuwen_lora_drift_gpu
```

报告中的关键字段：

- `observed.max_abs.max`：GPU 45 组比较中最大的 LoRA tensor 绝对误差。
- `observed.mean_abs.max`：GPU 45 组比较中最大的 LoRA tensor 平均绝对误差。
- `suggested_thresholds.max_abs` / `suggested_thresholds.mean_abs`：默认在 GPU 最大观测值上加 10% margin 和 `1e-6` epsilon 后得到的 A5 阈值。

后续不需要每次都跑 10 次。读取 GPU baseline 后，只跑 1 次固定轨迹训练做 validate。脚本会把这 1 个新 LoRA 和 baseline JSON 里记录的 GPU adapter 逐个比较，检查是否超过 GPU 10 轮测出来的漂移阈值。

默认 baseline 会写到 `examples/jiuwenrl_online/accuracy_validate/data/<model_name>_gpu_lora_drift_baseline.json`。模型可通过参数显式指定：

```bash
python examples/jiuwenrl_online/accuracy_validate/measure_lora_drift_envelope.py \
  --mode baseline \
  --runs 10 \
  --model-path /data1/lll/models/Qwen3-4B-Thinking-2507 \
  --model-name Qwen3-4B-Thinking-2507 \
  --train-gpu 6,7 \
  --work-dir /tmp/jiuwen_lora_drift_gpu
```

GPU 上单次回归验证，不传 `--baseline-json` 时会按 `--model-name` 自动读取 `accuracy_validate/data` 下的 baseline：

```bash
python examples/jiuwenrl_online/accuracy_validate/measure_lora_drift_envelope.py \
  --mode validate \
  --runs 1 \
  --train-gpu 6,7 \
  --work-dir /tmp/jiuwen_lora_drift_gpu_once \
  --output /tmp/jiuwen_lora_drift_gpu_once/lora_drift_gpu_once_validate.json
```

A5 上单次验证：

```bash
python examples/jiuwenrl_online/accuracy_validate/measure_lora_drift_envelope.py \
  --mode validate \
  --runs 1 \
  --train-gpu 4,5,6,7 \
  --work-dir /tmp/jiuwen_lora_drift_a5_once \
  --output /tmp/jiuwen_lora_drift_a5_once/lora_drift_a5_once_validate.json
```

git 里内置的 baseline JSON 只用于阈值和 LoRA 自身统计验证，不要求历史 GPU adapter 路径可访问。如果要额外做 A5-vs-GPU 交叉 tensor diff，可以把 GPU baseline 产物目录同步到 A5，并显式传入 reference adapter 路径：

```bash
python examples/jiuwenrl_online/accuracy_validate/measure_lora_drift_envelope.py \
  --mode validate \
  --runs 1 \
  --train-gpu 4,5,6,7 \
  --reference-adapter-dir /tmp/jiuwen_lora_drift_gpu/lora_repo_drift_00/st-a5-drift_00/v1 \
  --work-dir /tmp/jiuwen_lora_drift_a5_once \
  --output /tmp/jiuwen_lora_drift_a5_once/lora_drift_a5_once_validate.json
```

可调阈值 margin：

```bash
export ST_TEST_LORA_MAX_ABS_MARGIN_RATIO=0.10
export ST_TEST_LORA_MEAN_ABS_MARGIN_RATIO=0.10
export ST_TEST_LORA_DRIFT_ABS_EPS=1e-6
```

所有临时产物会写到 pytest 的 `tmp_path` 下；如需保留，可设置：

```bash
export ST_TEST_KEEP_ARTIFACTS=1
```
