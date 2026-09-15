# Adaptive multi-agent collaboration pilot report

**REAL LLM OUTPUTS**

## 1. Objective, implemented scope, and data status
This compact CommonsenseQA experiment compares Agent 0, independent majority, uniform reviewer-revision collaboration, and the same terminal evidence aggregated by trained query- and history-dependent `W`. It implements one session, fixed cyclic `g_01/g_12/g_20` routing, one optional revision, fixed encoders, and a frozen LLM. It is not the complete Version 24 system or a state-of-the-art claim: `alpha` is fixed, `gamma` is manual, and only `W` is trained. Static role diversity is manually prompted and is not learned by `alpha`, `W`, or `gamma`.

## 2. Data, model, reproducibility, and openJiuwen reuse
- Dataset/status: tau/commonsense_qa / REAL LLM OUTPUTS
- Split sizes: {'train': 30, 'validation': 10, 'test': 20}; selected IDs: `/Users/IDLE_And_R/Downloads/Huawei/agent-core/examples/adaptive_multi_agent_collab/artifacts/real/cache/manifest.json`
- Seed: 42; provider/model: OpenAI / gpt-5-mini
- Encoder: deterministic normalized signed hashing (BLAKE2b), 96 dimensions
- Public APIs: `Model`, `ModelClientConfig`, `ModelRequestConfig`, `JsonOutputParser`, with asynchronous `Model.invoke()` only—no direct provider SDK.
- Effective generation settings: {'adjustments': ['max_tokens removed after provider rejection', 'temperature removed after provider rejection']}
- Provider adjustments: ['max_tokens removed after provider rejection', 'temperature removed after provider rejection']

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
- Device: mps; best epoch: 200
- Saved-checkpoint train CE: 1.004848837852478
- Best validation CE: 1.1049630641937256
- Held-out test CE: 1.004701852798462
- Training seconds: 2.3074427919927984

The checkpoint and best epoch were selected by validation loss only; test data was not used for prompt, encoder, hyperparameter, stopping, or checkpoint choices.

## 5. Deployable baseline and learned-W results
| Method | Correct/evaluated | Accuracy | Bootstrap 95% CI | Calls/query | Tokens/query | Local wall s/query | Provider s/query | Cost/query |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| single_agent | 17 / 20 | 0.85 | [0.7, 1.0] | 1.0 | 485.95 | 18.834322183346377 | None | None |
| independent_majority | 19 / 20 | 0.95 | [0.85, 1.0] | 3.0 | 1622.25 | 61.69431094790343 | None | None |
| collaboration_uniform | 18 / 20 | 0.9 | [0.75, 1.0] | 6.35 | 4673.2 | 125.3342270980007 | None | None |
| collaboration_learned | 18 / 20 | 0.9 | [0.75, 1.0] | 6.35 | 4673.2 | 125.3342270980007 | None | None |

This small held-out sample does not establish statistical significance. Falling training loss is not evidence that W is effective; held-out decisions above are the primary diagnostic. In this run learned W changed no uniform-collaboration decisions, matched uniform collaboration, and underperformed independent majority by one test example.

## 6. Individual agents, agreement, and non-deployable oracles
Individual agent results:
```json
{
  "0": {
    "initial_accuracy": 0.85,
    "terminal_accuracy": 0.95
  },
  "1": {
    "initial_accuracy": 0.9,
    "terminal_accuracy": 0.9
  },
  "2": {
    "initial_accuracy": 0.9,
    "terminal_accuracy": 0.9
  }
}
```
Disagreement/unanimous agreement:
```json
{
  "initial_disagreement_rate": 0.2,
  "terminal_disagreement_rate": 0.05,
  "initial_unanimous_rate": 0.8,
  "terminal_unanimous_rate": 0.95
}
```
**ORACLE DIAGNOSTIC - NOT A DEPLOYABLE BASELINE**
```json
{
  "label": "ORACLE DIAGNOSTIC - NOT A DEPLOYABLE BASELINE",
  "initial_accuracy": 0.95,
  "terminal_accuracy": 0.95
}
```

## 7. Answer transitions and representative successful/harmful cases
Counts:
```json
{
  "0": {
    "unchanged": 18,
    "incorrect -> correct": 2
  },
  "1": {
    "unchanged": 18,
    "correct -> incorrect": 1,
    "incorrect -> correct": 1
  },
  "2": {
    "unchanged": 18,
    "correct -> incorrect": 1,
    "incorrect -> correct": 1
  }
}
```
Percentages:
```json
{
  "0": {
    "unchanged": 0.9,
    "incorrect -> correct": 0.1
  },
  "1": {
    "unchanged": 0.9,
    "correct -> incorrect": 0.05,
    "incorrect -> correct": 0.05
  },
  "2": {
    "unchanged": 0.9,
    "correct -> incorrect": 0.05,
    "incorrect -> correct": 0.05
  }
}
```
Representative cases (negative cases are not filtered):
```json
{
  "successful": {
    "0": [
      {
        "example_id": "820df15b615d221e38a71fcc44461085",
        "initial": "B",
        "terminal": "D",
        "gold": "D"
      },
      {
        "example_id": "5169f7ae0781b15161551de3a189ebef",
        "initial": "C",
        "terminal": "E",
        "gold": "E"
      }
    ],
    "1": [
      {
        "example_id": "26d7d59ef7b9f2e0c2d47419fa5bca91",
        "initial": "C",
        "terminal": "D",
        "gold": "D"
      }
    ],
    "2": [
      {
        "example_id": "c74ae684ba6c76e2a913493483678c9d",
        "initial": "B",
        "terminal": "A",
        "gold": "A"
      }
    ]
  },
  "harmful": {
    "0": [],
    "1": [
      {
        "example_id": "410f907f817dd7aa8e73291a918d3d86",
        "initial": "D",
        "terminal": "E",
        "gold": "D"
      }
    ],
    "2": [
      {
        "example_id": "410f907f817dd7aa8e73291a918d3d86",
        "initial": "D",
        "terminal": "E",
        "gold": "D"
      }
    ]
  }
}
```

## 8. Learned-weight behavior
- Average agent weights: [0.0015437082562129944, 0.00019977567790192553, 0.9982565253973007]
- Per-agent weight standard deviations: [0.00028955511554599675, 3.8041839065229254e-05, 0.00032100083534182484]
- Per-query weights are preserved in `summary.json` and `predictions.jsonl`.
- Weight distribution grouped by terminal agreement/disagreement: `{'agreement': {'count': 19, 'average_by_agent': [0.0015154392029599923, 0.0001984243893897847, 0.9982861468666478]}, 'disagreement': {'count': 1, 'average_by_agent': [0.002080820268020034, 0.00022545015963260084, 0.9976937174797058]}}`
- All-identical terminal pools, where weights cannot alter the decision: 19
- Uniform predictions changed by learned W, with help/harm labels:
```json
[]
```

## 9. Token usage, latency, API attempts, parsing, and failures
The baseline table reports attributable tokens, local wall latency, and provider latency/cost only when actually supplied.
- Real `Model.invoke()` attempts in the latest generation command: 341
- Valid latest cache keys across all fingerprints in this mode: 782
- Current-fingerprint call records: 374 total, 373 valid, 1 invalid; stages={'initial': 180, 'review': 181, 'revision': 13}; parse methods={'structured': 373, 'unparsed': 1}
- Retained provider/retry errors in current-fingerprint records: 6; malformed JSONL lines across the cache file: 0
- Completed/expected trajectories: 60 / 60
- Generation wall seconds: 943.4677218750585
- Learned-W local inference seconds: 0.11487158085219562
- End-to-end pilot command wall seconds: 951.0226839580573
- Per-method token and tie details: `{'single_agent': {'average_input_tokens': 126.7, 'average_output_tokens': 359.25, 'average_total_tokens': 485.95, 'average_cached_tokens': 0.0, 'tie_rate': 0.0}, 'independent_majority': {'average_input_tokens': 387.1, 'average_output_tokens': 1235.15, 'average_total_tokens': 1622.25, 'average_cached_tokens': 0.0, 'tie_rate': 0.0}, 'collaboration_uniform': {'average_input_tokens': 1125.9, 'average_output_tokens': 3547.3, 'average_total_tokens': 4673.2, 'average_cached_tokens': 0.0, 'tie_rate': 0.0}, 'collaboration_learned': {'average_input_tokens': 1125.9, 'average_output_tokens': 3547.3, 'average_total_tokens': 4673.2, 'average_cached_tokens': 0.0, 'tie_rate': 0.0}}`
- Final deployable-prediction parsing outcomes (recovered retries excluded): `{'single_agent': {'fallback_rate': 0.0, 'failure_rate': 0.0, 'failed_predictions': 0}, 'independent_majority': {'fallback_rate': 0.0, 'failure_rate': 0.0, 'failed_predictions': 0}, 'collaboration_uniform': {'fallback_rate': 0.0, 'failure_rate': 0.0, 'failed_predictions': 0}, 'collaboration_learned': {'fallback_rate': 0.0, 'failure_rate': 0.0, 'failed_predictions': 0}}`
- Runtime failures:
```json
[]
```
- Plotting errors: none

## 10. Artifacts, limitations, and future work
`predictions.jsonl`, `metrics.csv`, `summary.json`, `training_history.csv`, four plots, ignored raw caches, and the ignored W checkpoint preserve the evidence. This is a small, one-session, same-model pilot with manually prompted roles, one scheme, normally one terminal label per agent, and no statistical-power claim.

Future work: learned query/history routing `alpha`; standard session-level REINFORCE; conversation-shaped policy learning; conversation leave-one-out credit `D_ji`; immediate reward `R_s`; complete return `G_s`; future-only return `F_s`; multiple sessions; carried-forward answers and histories; debate, judge, teacher, extended reviewer, and self-reflection; judge-specific answer collection and multiple incoming judge submissions; full FIFO message queues; multiple initiated conversations and variable internal rounds; cost penalties; W across sessions; stronger fixed or optionally trainable `Enc_x`/`Enc_H`; textual `gamma` optimization with critic/editor LLMs; candidate prompt generation and held-out prompt validation; heterogeneous LLM families; larger CommonsenseQA and AQuA experiments; StrategyQA smoke comparisons; numerical normalization; open-ended semantic equivalence; repeated seeds; statistically meaningful evaluation; and production-grade observability and recovery.
