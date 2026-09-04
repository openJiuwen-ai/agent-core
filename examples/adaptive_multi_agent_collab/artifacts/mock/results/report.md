# Adaptive multi-agent collaboration pilot report

**SYNTHETIC OFFLINE MOCK OUTPUTS - NOT EXPERIMENTAL EVIDENCE**

## 1. Objective, implemented scope, and data status
This compact CommonsenseQA experiment compares Agent 0, independent majority, uniform reviewer-revision collaboration, and the same terminal evidence aggregated by trained query- and history-dependent `W`. It implements one session, fixed cyclic `g_01/g_12/g_20` routing, one optional revision, fixed encoders, and a frozen LLM. It is not the complete Version 24 system or a state-of-the-art claim: `alpha` is fixed, `gamma` is manual, and only `W` is trained. Static role diversity is manually prompted and is not learned by `alpha`, `W`, or `gamma`.

## 2. Data, model, reproducibility, and openJiuwen reuse
- Dataset/status: synthetic / SYNTHETIC OFFLINE MOCK OUTPUTS - NOT EXPERIMENTAL EVIDENCE
- Split sizes: {'train': 2, 'validation': 1, 'test': 2}; selected IDs: `/Users/IDLE_And_R/Downloads/Huawei/agent-core/examples/adaptive_multi_agent_collab/artifacts/mock/cache/manifest.json`
- Seed: 42; provider/model: mock / deterministic-mock
- Encoder: deterministic normalized signed hashing (BLAKE2b), 96 dimensions
- Public APIs: `Model`, `ModelClientConfig`, `ModelRequestConfig`, `JsonOutputParser`, with asynchronous `Model.invoke()` only—no direct provider SDK.
- Effective generation settings: {'temperature': 0.2, 'max_tokens': 220, 'adjustments': []}
- Provider adjustments: []

Gold labels were used only for W training, validation, transition classification, and evaluation; they were never included in an LLM prompt.

## 3. Static roles, routing, and reviewer-revision procedure
```json
{
  "0": "You are an analytical solver. Identify relevant facts, compare every option concisely, and select the strongest answer.",
  "1": "You are an option eliminator. Inspect every distractor, explain why rejected options are unsupported, and select the strongest remainder.",
  "2": "You are a skeptical verifier. Check hidden assumptions and counterexamples, challenge the obvious answer, compare plausible alternatives, and select the best support."
}
```
Every agent initiates once and the next cyclic agent reviews it. `complete` accepts the submitted label; `continue` recommends reconsideration and permits exactly one revision. Protocol-inconsistent acceptance is retried once and retained in the ignored raw trajectory.

## 4. Encoder, weighting architecture, and validation-only selection
- Architecture: trainable agent embedding plus shared one-hidden-layer GELU MLP.
- Fixed support: tie-aware `u = 0.5 * (rho + mu)`; `Enc_x`, `Enc_H`, and support tensors are fixed and detached.
- Training configuration: `{'query_dim': 96, 'history_dim': 96, 'agent_embedding_dim': 12, 'hidden_dim': 48, 'dropout': 0.05, 'learning_rate': 0.001, 'weight_decay': 0.0001, 'epochs': 200, 'patience': 25, 'batch_size': 8, 'seed': 42}`
- Device: mps; best epoch: 83
- Saved-checkpoint train CE: 1.3171286582946777
- Best validation CE: 1.8401435613632202
- Held-out test CE: 1.591389775276184
- Training seconds: 1.3046364590991288

The checkpoint and best epoch were selected by validation loss only; test data was not used for prompt, encoder, hyperparameter, stopping, or checkpoint choices.

## 5. Deployable baseline and learned-W results
| Method | Correct/evaluated | Accuracy | Bootstrap 95% CI | Calls/query | Tokens/query | Local wall s/query | Provider s/query | Cost/query |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| single_agent | 1 / 2 | 0.5 | [0.0, 1.0] | 1.0 | 91.0 | 1.293746754527092e-05 | None | None |
| independent_majority | 1 / 2 | 0.5 | [0.0, 1.0] | 3.0 | 279.5 | 3.4125521779060364e-05 | None | None |
| collaboration_uniform | 0 / 2 | 0.0 | [0.0, 0.0] | 7.5 | 786.5 | 8.372962474822998e-05 | None | None |
| collaboration_learned | 0 / 2 | 0.0 | [0.0, 0.0] | 7.5 | 786.5 | 8.372962474822998e-05 | None | None |

This small held-out sample does not establish statistical significance. Falling training loss is not evidence that W is effective; held-out decisions above are the primary diagnostic. In this run learned W changed no uniform-collaboration decisions, matched uniform collaboration, and underperformed independent majority by one test example.

## 6. Individual agents, agreement, and non-deployable oracles
Individual agent results:
```json
{
  "0": {
    "initial_accuracy": 0.5,
    "terminal_accuracy": 0.0
  },
  "1": {
    "initial_accuracy": 0.5,
    "terminal_accuracy": 0.5
  },
  "2": {
    "initial_accuracy": 0.5,
    "terminal_accuracy": 0.5
  }
}
```
Disagreement/unanimous agreement:
```json
{
  "initial_disagreement_rate": 1.0,
  "terminal_disagreement_rate": 1.0,
  "initial_unanimous_rate": 0.0,
  "terminal_unanimous_rate": 0.0
}
```
**ORACLE DIAGNOSTIC - NOT A DEPLOYABLE BASELINE**
```json
{
  "label": "ORACLE DIAGNOSTIC - NOT A DEPLOYABLE BASELINE",
  "initial_accuracy": 1.0,
  "terminal_accuracy": 1.0
}
```

## 7. Answer transitions and representative successful/harmful cases
Counts:
```json
{
  "0": {
    "correct -> incorrect": 1,
    "unchanged": 1
  },
  "1": {
    "unchanged": 2
  },
  "2": {
    "incorrect -> incorrect with changed label": 1,
    "unchanged": 1
  }
}
```
Percentages:
```json
{
  "0": {
    "correct -> incorrect": 0.5,
    "unchanged": 0.5
  },
  "1": {
    "unchanged": 1.0
  },
  "2": {
    "incorrect -> incorrect with changed label": 0.5,
    "unchanged": 0.5
  }
}
```
Representative cases (negative cases are not filtered):
```json
{
  "successful": {
    "0": [],
    "1": [],
    "2": []
  },
  "harmful": {
    "0": [
      {
        "example_id": "synthetic-test-000",
        "initial": "C",
        "terminal": "D",
        "gold": "C"
      }
    ],
    "1": [],
    "2": []
  }
}
```

## 8. Learned-weight behavior
- Average agent weights: [0.4922564625740051, 0.25009895861148834, 0.2576446086168289]
- Per-agent weight standard deviations: [0.0013788938522338867, 0.0031716078519821167, 0.004550471901893616]
- Per-query weights are preserved in `summary.json` and `predictions.jsonl`.
- Weight distribution grouped by terminal agreement/disagreement: `{'agreement': {'count': 0, 'average_by_agent': [None, None, None]}, 'disagreement': {'count': 2, 'average_by_agent': [0.4922564625740051, 0.25009895861148834, 0.2576446086168289]}}`
- All-identical terminal pools, where weights cannot alter the decision: 0
- Uniform predictions changed by learned W, with help/harm labels:
```json
[]
```

## 9. Token usage, latency, API attempts, parsing, and failures
The baseline table reports attributable tokens, local wall latency, and provider latency/cost only when actually supplied.
- Real `Model.invoke()` attempts in the latest generation command: 0
- Valid latest cache keys across all fingerprints in this mode: 78
- Current-fingerprint call records: 39 total, 39 valid, 0 invalid; stages={'initial': 15, 'review': 16, 'revision': 8}; parse methods={'structured': 27, 'explicit_marker': 12}
- Retained provider/retry errors in current-fingerprint records: 0; malformed JSONL lines across the cache file: 0
- Completed/expected trajectories: 5 / 5
- Generation wall seconds: 0.004498415859416127
- Learned-W local inference seconds: 0.008714415831491351
- End-to-end pilot command wall seconds: 1.369494792073965
- Per-method token and tie details: `{'single_agent': {'average_input_tokens': 84.0, 'average_output_tokens': 7.0, 'average_total_tokens': 91.0, 'average_cached_tokens': None, 'tie_rate': 0.0}, 'independent_majority': {'average_input_tokens': 259.0, 'average_output_tokens': 20.5, 'average_total_tokens': 279.5, 'average_cached_tokens': None, 'tie_rate': 0.0}, 'collaboration_uniform': {'average_input_tokens': 735.0, 'average_output_tokens': 51.5, 'average_total_tokens': 786.5, 'average_cached_tokens': None, 'tie_rate': 0.5}, 'collaboration_learned': {'average_input_tokens': 735.0, 'average_output_tokens': 51.5, 'average_total_tokens': 786.5, 'average_cached_tokens': None, 'tie_rate': 0.0}}`
- Final deployable-prediction parsing outcomes (recovered retries excluded): `{'single_agent': {'fallback_rate': 1.0, 'failure_rate': 0.0, 'failed_predictions': 0}, 'independent_majority': {'fallback_rate': 0.8333333333333333, 'failure_rate': 0.0, 'failed_predictions': 0}, 'collaboration_uniform': {'fallback_rate': 0.4642857142857143, 'failure_rate': 0.0, 'failed_predictions': 0}, 'collaboration_learned': {'fallback_rate': 0.4642857142857143, 'failure_rate': 0.0, 'failed_predictions': 0}}`
- Runtime failures:
```json
[]
```
- Plotting errors: none

## 10. Artifacts, limitations, and future work
`predictions.jsonl`, `metrics.csv`, `summary.json`, `training_history.csv`, four plots, ignored raw caches, and the ignored W checkpoint preserve the evidence. This is a small, one-session, same-model pilot with manually prompted roles, one scheme, normally one terminal label per agent, and no statistical-power claim.

Future work: learned query/history routing `alpha`; standard session-level REINFORCE; conversation-shaped policy learning; conversation leave-one-out credit `D_ji`; immediate reward `R_s`; complete return `G_s`; future-only return `F_s`; multiple sessions; carried-forward answers and histories; debate, judge, teacher, extended reviewer, and self-reflection; judge-specific answer collection and multiple incoming judge submissions; full FIFO message queues; multiple initiated conversations and variable internal rounds; cost penalties; W across sessions; stronger fixed or optionally trainable `Enc_x`/`Enc_H`; textual `gamma` optimization with critic/editor LLMs; candidate prompt generation and held-out prompt validation; heterogeneous LLM families; larger CommonsenseQA and AQuA experiments; StrategyQA smoke comparisons; numerical normalization; open-ended semantic equivalence; repeated seeds; statistically meaningful evaluation; and production-grade observability and recovery.
