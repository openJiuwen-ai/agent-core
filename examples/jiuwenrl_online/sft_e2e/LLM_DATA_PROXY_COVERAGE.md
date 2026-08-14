# SFTOnlineRail 与 llm-data-proxy 轨迹覆盖对比

## 结论

当前 SFT 轨迹主链路能覆盖 `llm-data-proxy` 的 ChatML 会话训练需求，并额外保留场景 2-1 需要的 `dataset_case`、`original_task`、`workspace_ref`、`context_compression`、raw/sample 状态和 rollout 元数据。

上传入口保持统一：agent/rollout 容器只调用 `/v1/gateway/upload/batch` 上传原始轨迹。gateway 负责基础校验和入库，不引入 SFT 专属上传 API；后续是 RL 奖励计算还是 SFT rollout，由 scheduler 消费轨迹时根据 `TRAIN_BACKEND`、任务配置和轨迹状态推进。

`llm-data-proxy` 是 OpenAI proxy 层的透明采集器，输出 `{messages, tools, remarks}` ChatML 文件。`SFTOnlineRail` 是 agent rail 层的训练采集器，输出 `sft-raw-v1`，再由 scheduler rollouter 转成 `sft-sample-v1` 和训练 `train.json`。

## 字段映射

| llm-data-proxy ChatML | 当前 SFT 链路 |
| --- | --- |
| `messages[].role/content/name` | `sft-raw-v1.steps[].messages[]` 和 `sft-sample-v1.messages[]` |
| assistant 响应 `content` | `steps[].response.content`、`steps[].response_text`、`sft-sample-v1.assistant_message.content` |
| assistant `tool_calls` | `steps[].response.tool_calls` 和 `sft-sample-v1.assistant_message.tool_calls` |
| request `tools` | `steps[].tools` 和 `sft-sample-v1.tools` |
| tool 返回消息 | `steps[]` 中 `type=tool` 的 `tool_name/tool_args/tool_result` |
| `timestamp` | `created_at`、`trajectory_meta`、step `meta` 可承载；训练样本不依赖逐消息时间戳 |
| `remarks.incomplete` | `session_done`、`flush_reason`、`context_compression` 可表达关闭、阈值切分、压缩等原因 |
| RL token 字段 `prompt_ids/completion_ids/logprobs` | raw step `meta`、rail token fields 可承载；SFT 训练默认只需要文本 |
| `hint` 注入后的 messages | 场景 2-3 预留在 `HintRewardpackRollouter`，当前 2-1/2-2 不主动实现 |

## 主要差异

- `llm-data-proxy` 通过请求前缀匹配维护 session；`SFTOnlineRail` 通过 agent session/close/threshold 触发 flush。
- `llm-data-proxy` 文件输出偏离线可读；当前 SFT 链路 Redis 状态机区分 raw 输入和 sample 训练数据。
- `llm-data-proxy` 支持 `/newsession` 人工切分；当前 SFT 场景 2-1 通过显式关闭会话上传，场景 2-2 可用长度阈值切分。
- 当前 RL 老链路仍有 gateway 内延迟 judge 逻辑；后续若统一到 raw 轨迹模型，应把 RL reward 也收敛到 scheduler 消费阶段，和 SFT rollout 一样通过状态更新表达处理进度。

## 覆盖要求

端到端脚本校验以下硬条件：

- original raw 必须是 `sft-raw-v1`，包含 `original_task`、`dataset_case.docker_image`、`dataset_case.instance_id`。
- raw 必须包含非空 `steps`，至少一个 LLM step，且 LLM step 有 `messages`、`response_text/response.content`、`model_id`。
- scheduler supervisor rollout 必须产生第二条 rollout raw，并最终生成 `sft-sample-v1`。
- 原始 raw 和 supervisor raw 最终状态为 `processed`，SFT sample 最终状态为 `trained`。
- dry-run `train.json` 中 assistant 消息必须有 `loss_mask=1`，保留 ChatML `role/content` 结构。
