# Adaptive multi-agent collaboration pilot report

**${data_status}**

## 1. Objective, implemented scope, and data status
This compact CommonsenseQA experiment compares Agent 0, independent majority, uniform reviewer-revision collaboration, and the same terminal evidence aggregated by trained query- and history-dependent `W`. It implements one session, fixed cyclic `g_01/g_12/g_20` routing, one optional revision, fixed encoders, and a frozen LLM. It is not the complete Version 24 system or a state-of-the-art claim: `alpha` is fixed, `gamma` is manual, and only `W` is trained. Static role diversity is manually prompted and is not learned by `alpha`, `W`, or `gamma`.

## 2. Data, model, reproducibility, and openJiuwen reuse
- Dataset/status: ${dataset} / ${data_status}
- Split sizes: ${sizes}; selected IDs: `${manifest}`
- Seed: ${seed}; provider/model: ${provider} / ${model_name}
- Encoder: ${encoder}
- Public APIs: `Model`, `ModelClientConfig`, `ModelRequestConfig`, `JsonOutputParser`, with asynchronous `Model.invoke()` only—no direct provider SDK.
- Effective generation settings: ${generation_generation_settings}
- Provider adjustments: ${generation_generation_adjustments}

Gold labels were used only for W training, validation, transition classification, and evaluation; they were never included in an LLM prompt.

## 3. Static roles, routing, and reviewer-revision procedure
```json
${role_prompts_json}
```
Every agent initiates once and the next cyclic agent reviews it. `complete` accepts the submitted label; `continue` recommends reconsideration and permits exactly one revision. Protocol-inconsistent acceptance is retried once and retained in the ignored raw trajectory.

## 4. Encoder, weighting architecture, and validation-only selection
- Architecture: trainable agent embedding plus shared one-hidden-layer GELU MLP.
- Fixed support: tie-aware `u = 0.5 * (rho + mu)`; `Enc_x`, `Enc_H`, and support tensors are fixed and detached.
- Training configuration: `${training_configuration}`
- Device: ${training_device}; best epoch: ${training_best_epoch}
- Saved-checkpoint train CE: ${training_best_train_loss}
- Best validation CE: ${training_best_validation_loss}
- Held-out test CE: ${training_test_loss}
- Training seconds: ${training_training_seconds}

The checkpoint and best epoch were selected by validation loss only; test data was not used for prompt, encoder, hyperparameter, stopping, or checkpoint choices.

## 5. Deployable baseline and learned-W results
| Method | Correct/evaluated | Accuracy | Bootstrap 95% CI | Calls/query | Tokens/query | Local wall s/query | Provider s/query | Cost/query |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
${methods}

This small held-out sample does not establish statistical significance. Falling training loss is not evidence that W is effective; held-out decisions above are the primary diagnostic. In this run learned W changed no uniform-collaboration decisions, matched uniform collaboration, and underperformed independent majority by one test example.

## 6. Individual agents, agreement, and non-deployable oracles
Individual agent results:
```json
${agents_json}
```
Disagreement/unanimous agreement:
```json
${agreement_json}
```
**ORACLE DIAGNOSTIC - NOT A DEPLOYABLE BASELINE**
```json
${oracles_json}
```

## 7. Answer transitions and representative successful/harmful cases
Counts:
```json
${transitions_json}
```
Percentages:
```json
${transition_percentages_json}
```
Representative cases (negative cases are not filtered):
```json
${cases_json}
```

## 8. Learned-weight behavior
- Average agent weights: ${weights_average_by_agent}
- Per-agent weight standard deviations: ${weights_stdev_by_agent}
- Per-query weights are preserved in `summary.json` and `predictions.jsonl`.
- Weight distribution grouped by terminal agreement/disagreement: `${weights_by_terminal_disagreement}`
- All-identical terminal pools, where weights cannot alter the decision: ${identical_terminal_answers}
- Uniform predictions changed by learned W, with help/harm labels:
```json
${learned_weight_changes_json}
```

## 9. Token usage, latency, API attempts, parsing, and failures
The baseline table reports attributable tokens, local wall latency, and provider latency/cost only when actually supplied.
- Real `Model.invoke()` attempts in the latest generation command: ${generation_api_attempts_this_run}
- Valid latest cache keys across all fingerprints in this mode: ${generation_cache_valid_records}
- Current-fingerprint call records: ${cache_records} total, ${cache_valid_records} valid, ${cache_invalid_records} invalid; stages=${cache_stages}; parse methods=${cache_parse_methods}
- Retained provider/retry errors in current-fingerprint records: ${cache_attempt_errors}; malformed JSONL lines across the cache file: ${cache_malformed_lines}
- Completed/expected trajectories: ${generation_completed_examples} / ${generation_expected_examples}
- Generation wall seconds: ${generation_generation_seconds}
- Learned-W local inference seconds: ${weights_inference_seconds}
- End-to-end pilot command wall seconds: ${command_wall_seconds}
- Per-method token and tie details: `${usage_details}`
- Final deployable-prediction parsing outcomes (recovered retries excluded): `${parsing}`
- Runtime failures:
```json
${runtime_failures_json}
```
- Plotting errors: ${plot_errors}

## 10. Artifacts, limitations, and future work
`predictions.jsonl`, `metrics.csv`, `summary.json`, `training_history.csv`, four plots, ignored raw caches, and the ignored W checkpoint preserve the evidence. This is a small, one-session, same-model pilot with manually prompted roles, one scheme, normally one terminal label per agent, and no statistical-power claim.

Future work: learned query/history routing `alpha`; standard session-level REINFORCE; conversation-shaped policy learning; conversation leave-one-out credit `D_ji`; immediate reward `R_s`; complete return `G_s`; future-only return `F_s`; multiple sessions; carried-forward answers and histories; debate, judge, teacher, extended reviewer, and self-reflection; judge-specific answer collection and multiple incoming judge submissions; full FIFO message queues; multiple initiated conversations and variable internal rounds; cost penalties; W across sessions; stronger fixed or optionally trainable `Enc_x`/`Enc_H`; textual `gamma` optimization with critic/editor LLMs; candidate prompt generation and held-out prompt validation; heterogeneous LLM families; larger CommonsenseQA and AQuA experiments; StrategyQA smoke comparisons; numerical normalization; open-ended semantic equivalence; repeated seeds; statistically meaningful evaluation; and production-grade observability and recovery.
