# prompt_builder

`openjiuwen.dev_tools.prompt_builder` provides prompt "self-optimization/semi-automatic building" capabilities.

Its goal is not to directly replace prompt engineers, but to productize common optimization actions:

- Generate more standardized prompts from task descriptions (MetaTemplateBuilder)
- Incrementally modify prompts based on user feedback (FeedbackPromptBuilder)
- Targeted fixes based on bad cases (evaluation failed cases) (BadCasePromptBuilder)

## Key Dependencies

These three Builders all depend on model invocation capabilities, therefore initialization requires:

- `ModelRequestConfig`: Model request configuration for this optimization task (e.g., temperature/max_tokens, etc.)
- `ModelClientConfig`: Model client configuration (e.g., provider/base_url/api_key, etc.)

> Recommendation: Optimization calls are usually executed in "offline/controlled environments".
>
> If called in online services, please be sure to limit frequency, concurrency and cost.

**Classes**:

| CLASS | DESCRIPTION |
|-------|-------------|
| [BadCasePromptBuilder](./prompt_builder/builder/bad_case_prompt_builder.md) | Bad case optimizer. |
| [FeedbackPromptBuilder](./prompt_builder/builder/feedback_prompt_builder.md) | Prompt feedback optimizer. |
| [MetaTemplateBuilder](./prompt_builder/builder/meta_template_builder.md) | Prompt generation optimizer. |
