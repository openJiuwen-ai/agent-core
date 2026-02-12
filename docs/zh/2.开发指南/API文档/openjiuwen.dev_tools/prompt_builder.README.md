# prompt_builder

`openjiuwen.dev_tools.prompt_builder` 提供提示词“自优化/半自动构建”能力。

它的目标不是直接替代提示词工程师，而是把常见的优化动作产品化：

- 从任务描述生成更规范的提示词（MetaTemplateBuilder）
- 基于用户反馈增量修改提示词（FeedbackPromptBuilder）
- 基于坏例（评测失败用例）做定向修复（BadCasePromptBuilder）

## 关键依赖

这三个 Builder 都依赖模型调用能力，因此初始化时需要：

- `ModelRequestConfig`：本次优化任务的模型请求配置（如 temperature/max_tokens 等）
- `ModelClientConfig`：模型客户端配置（如 provider/base_url/api_key 等）

> 建议：优化类调用通常是“离线/受控环境”执行。
>
> 如果在在线服务中调用，请务必对频率、并发和成本做限制。

**Classes**：

| CLASS | DESCRIPTION |
|-------|-------------|
| [BadCasePromptBuilder](./prompt_builder/builder/bad_case_prompt_builder.md) | 错误案例优化器。 |
| [FeedbackPromptBuilder](./prompt_builder/builder/feedback_prompt_builder.md) | 提示词反馈优化器。 |
| [MetaTemplateBuilder](./prompt_builder/builder/meta_template_builder.md) | 提示词生成优化器。 |

