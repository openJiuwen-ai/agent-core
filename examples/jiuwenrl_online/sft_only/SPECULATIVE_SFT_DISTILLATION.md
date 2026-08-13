# 投机推理小模型 SFT 蒸馏方案

本文档说明如何用大模型输出对小模型做 hard-label SFT，以提升目标场景下的 speculative decoding 接受率。当前实现是一个独立训练入口，不依赖在线 PPO、reward、logprob 或 rollout vLLM。

## 结论

这个方案适合定位为：

```text
面向大垂域场景的 draft model / draft LoRA 蒸馏
```

它不应被理解成：

```text
让小模型在所有通用场景全面对齐大模型
```

如果训练和评估都落在同一个大垂域内，例如 Coding、办公、会议、Agent 工具调用，且 prompt 形态、工具协议、输出风格基本稳定，那么不可控点会明显减少。SFT 会把小模型的条件分布往该垂域靠近，通常有机会提升：

- 小模型 draft token 与大模型 target token 的 prefix 一致长度
- speculative verify 每轮平均接受 token 数
- 大模型 verify 调用频率
- 端到端推理吞吐或延迟

但这不能完全消除用例差异。大垂域内部仍会有子场景偏移，例如 Coding 里的代码生成、代码审查、错误排查、命令解释；办公里的总结、改写、表格抽取、邮件生成；会议里的纪要、行动项、问答追踪。这些子场景最好都进入训练和 held-out 评估。

## 当前文件

```text
agent-core/examples/jiuwenrl_online/sft_only/train_sft_from_speculative_samples.sh
agent-core/examples/jiuwenrl_online/sft_only/train_sft_from_speculative_trajectory_json.py
agent-core/examples/jiuwenrl_online/sft_only/speculative_sft_sample_trajectory.json
agent-core/examples/jiuwenrl_online/sft_only/speculative_sft_generated_trajectories.json
```

- `sft_only/train_sft_from_speculative_samples.sh`：一键入口，读取一个 JSON 轨迹文件并启动 SFT。
- `sft_only/train_sft_from_speculative_trajectory_json.py`：把轨迹 JSON 规范化为 veRL SFT parquet，统计 small/large prefix mismatch，然后调用 `verl.trainer.sft_trainer`。
- `speculative_sft_sample_trajectory.json`：最小样例，适合看字段格式。
- `speculative_sft_generated_trajectories.json`：本机跑通流程时生成的 smoke test 数据。该文件使用 Thinking teacher 生成，标签里可能含思考前缀，不建议直接作为真实训练数据模板。

## 运行方式

在 `agent-core/examples/jiuwenrl_online` 下执行：

```bash
STUDENT_MODEL_PATH=/data1/lll/models/Qwen3-0.6B \
TRAIN_GPU=4,5,6,7 \
SFT_MAX_LENGTH=4096 \
SFT_MAX_TOKEN_LEN_PER_GPU=4096 \
SFT_TOTAL_EPOCHS=1 \
bash sft_only/train_sft_from_speculative_samples.sh sft_only/speculative_sft_sample_trajectory.json
```

A5/NPU 环境需要确认本地环境脚本里设备变量和后端类型：

```bash
ONLINE_RL_DEVICE_BACKEND=ascend
ONLINE_RL_VISIBLE_DEVICES_ENV=ASCEND_RT_VISIBLE_DEVICES
TRAIN_GPU=0,1,2,3
```

如果仍沿用 CUDA 机器，则通常是：

```bash
ONLINE_RL_DEVICE_BACKEND=cuda
ONLINE_RL_VISIBLE_DEVICES_ENV=CUDA_VISIBLE_DEVICES
TRAIN_GPU=4,5,6,7
```

## 关键配置

入口脚本支持通过环境变量覆盖训练参数：

```text
STUDENT_MODEL_PATH              小模型/学生模型路径，默认继承 MODEL_PATH
TRAIN_GPU                       训练卡号，例如 4,5,6,7
SFT_OUTPUT_DIR                  输出目录，默认 records/speculative_sft
SFT_MAX_LENGTH                  单样本最大上下文长度
SFT_MAX_TOKEN_LEN_PER_GPU        veRL dynamic batch 的单卡 token 上限
SFT_TRAIN_BATCH_SIZE            全局 batch size，默认等于卡数
SFT_MICRO_BATCH_SIZE_PER_GPU     单卡 micro batch，默认 1
SFT_TOTAL_EPOCHS                epoch 数，默认 1
SFT_LR                          学习率，默认 1e-5
SFT_LORA_RANK                   LoRA rank，默认 16
SFT_LORA_ALPHA                  LoRA alpha，默认 32
SFT_TARGET_MODULES              LoRA target modules，默认 all-linear
SFT_ULYSSES_SP                  Ulysses sequence parallel size，默认 1
SFT_DTYPE                       bfloat16 或 float16，默认 bfloat16
SFT_PARAM_OFFLOAD               参数 offload，1 为开启
SFT_OPTIMIZER_OFFLOAD           optimizer offload，1 为开启
SFT_ACTIVATION_OFFLOAD          activation offload，1 为开启
```

脚本内部当前默认开启：

```text
model.use_remove_padding=True
model.enable_gradient_checkpointing=True
data.use_dynamic_bsz=True
data.truncation=error
data.pad_mode=no_padding
engine.ulysses_sequence_parallel_size=${SFT_ULYSSES_SP}
```

## 轨迹 JSON 格式

推荐格式如下：

```json
{
  "samples": [
    {
      "sample_id": "coding-review-001",
      "session_id": "coding-session-a",
      "turn_num": 1,
      "tools": [],
      "enable_thinking": false,
      "messages": [
        {
          "role": "system",
          "content": "You are a coding assistant.",
          "loss_mask": 0
        },
        {
          "role": "user",
          "content": "请审查下面这段 Python 代码是否有资源泄露风险。",
          "loss_mask": 0
        },
        {
          "role": "assistant",
          "content": "这段代码的主要风险是文件句柄没有通过上下文管理器关闭，异常路径下可能泄露资源。建议改成 with open(...) as f，并把异常处理限制在必要范围内。",
          "loss_mask": 1
        }
      ],
      "small": {
        "model": "draft-model",
        "text": "这段代码可能存在文件没有关闭的问题，建议使用 with open。"
      },
      "large": {
        "model": "teacher-model",
        "message": {
          "role": "assistant",
          "content": "这段代码的主要风险是文件句柄没有通过上下文管理器关闭，异常路径下可能泄露资源。建议改成 with open(...) as f，并把异常处理限制在必要范围内。"
        }
      }
    }
  ]
}
```

字段含义：

```text
messages       训练上下文和目标输出。loss_mask=1 的 assistant 内容是 SFT label。
small          小模型 draft 输出，只用于 mismatch / prefix acceptance 统计，不作为训练 label。
large          大模型 teacher 输出。没有显式 loss_mask=1 时，脚本会把 large.message 追加为训练目标。
tools          可选，保留工具 schema 或工具元信息。
enable_thinking 可选，传给 veRL 多轮 SFT 数据集。
```

也支持简化格式：

```json
{
  "samples": [
    {
      "prompt": "把下面会议记录整理成行动项。",
      "small_output": "需要整理行动项。",
      "large_output": "行动项：1. 张三在周五前提交方案；2. 李四同步预算；3. 下周一复盘风险。"
    }
  ]
}
```

真实训练更推荐显式 `messages + loss_mask`，这样多轮、工具结果、用户信息都能清楚控制哪些 token 参与 loss。

## 场景建议

Coding 场景建议覆盖：

- 代码生成
- 代码解释
- 代码审查
- bug 定位
- 单测生成
- shell 命令和日志排查
- 多文件修改计划

办公场景建议覆盖：

- 文档总结
- 邮件改写
- 表格信息抽取
- 待办事项生成
- 决策材料归纳
- 多轮修改

会议场景建议覆盖：

- 会议纪要
- 行动项抽取
- 参会人观点归纳
- 后续追问
- 历史会议上下文引用
- 长上下文摘要

Agent 工具调用场景建议覆盖：

- 工具调用前的参数构造
- 工具结果返回后的解释
- 工具失败后的重试策略
- 多工具链路中的中间回复
- 用户画像或上下文记忆注入后的回复

## 数据配比

为了减少每次 SFT 都偏向某一批具体数据，建议不要只训练 hard mismatch 样本。一个可执行的初始配比是：

```text
70% 目标垂域真实轨迹
20% 垂域内通用保持数据
10% small/large 不一致的 hard case
```

如果目标是强业务场景加速，可以调整为：

```text
80% 目标垂域真实轨迹
10% 垂域内通用保持数据
10% hard case
```

其中 hard case 的作用是提高小模型在大模型不同意的位置上的纠偏能力，但比例过高会让训练分布变窄，可能降低垂域内普通用例的稳定性。

## 评估指标

不要只看 SFT loss。至少需要固定三套 held-out 评估集：

```text
A. 同垂域同类型 held-out：验证最直接的 speculative 提升
B. 同垂域不同子场景 held-out：验证大垂域内泛化
C. 通用保持集或安全集：验证没有明显能力漂移
```

建议记录：

```text
student-tokenizer prefix acceptance rate
真实 speculative runtime acceptance rate
平均每轮 accepted tokens
大模型 verify 次数
端到端 latency / tokens per second
任务质量人工或 judge 评分
通用保持集质量变化
```

当前脚本会输出 `speculative_stats.json`，其中的 acceptance 是基于 student tokenizer 对 small/large 文本重新编码后的 prefix 统计。这个指标可用于离线排查，但如果大小模型 tokenizer 不同，它不等价于真实 speculative runtime acceptance。

## tokenizer 说明

hard-label SFT 不要求 teacher 和 student tokenizer 同源，因为 teacher 文本会被 student tokenizer 重新编码后训练。

但 speculative decoding 强烈建议大小模型同 tokenizer、同 chat template、同模型族。原因是 runtime acceptance 是 token 级别校验；tokenizer 不同会导致文本相似但 token 边界不同，从而让离线文本对齐和真实接受率之间出现偏差。

因此推荐优先级是：

```text
同 tokenizer / 同 chat template / 同模型族的小模型 + 大模型
>
同 tokenizer 但不同规模模型
>
不同 tokenizer 的模型组合
```

## 注意事项

- 优先用 LoRA/adapter 训练，不要直接覆盖小模型基座，便于按垂域切换和回滚。
- teacher 标签应使用最终对用户可见的答案，不要混入 Thinking 模型的内部思考文本。
- 轨迹里的工具结果、用户信息、历史上下文应作为 `loss_mask=0` 的上下文；只有希望小模型学习输出的 assistant 文本设为 `loss_mask=1`。
- 训练集和评估集必须按 session 或任务维度切分，避免同一会话泄漏到评估集。
- 大垂域内也要覆盖子场景，否则会出现“评估集好、实际新用例提升不稳定”的问题。
