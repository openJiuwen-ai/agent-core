# Adaptive multi-agent collaboration pilot

This example is a compact experimental prototype of the Version 24 adaptive
multi-agent collaboration model. It runs one reviewer-revision session with
three static reasoning roles on CommonsenseQA, then learns a query- and
history-dependent agent weighting model `W`.

It is deliberately not the complete Version 24 system:

- routing policy `alpha` is fixed to the cyclic edges `g_01`, `g_12`, `g_20`;
- prompt collection `gamma` is manually specified;
- `W` is genuinely trained from cached trajectories;
- role diversity is manually induced by static prompts, not learned by
  `alpha`, `W`, or `gamma`;
- one session and at most one revision per conversation are implemented;
- reviewer-revision is the only collaboration scheme.

## Reused openJiuwen APIs

All real model calls use only:

```python
from openjiuwen.core.foundation.llm import (
    JsonOutputParser,
    Model,
    ModelClientConfig,
    ModelRequestConfig,
)
```

Calls use `await Model.invoke(...)`. One `Model` is created per reasoning role
so agent identity and client lifecycle remain explicit. `JsonOutputParser` is
preferred, with a strict local JSON parser and conservative label fallback.
Provider SDKs are never called directly. `LLMComponent` is not used because
its ordinary workflow output does not preserve all usage metadata.

## Layout

```text
adaptive_multi_agent_collab/
  config.py                 CLI/runtime configuration
  schemas.py                records, parsing, JSONL call cache
  prompts.py                static roles and versioned prompts
  openjiuwen_client.py      async openJiuwen/mock invocation
  experiment.py             dataset split and reviewer-revision session
  weighting.py              fixed encoders and trainable W
  evaluation.py             baselines, metrics, transitions, accounting
  run_experiment.py         generate/train/evaluate/report/all CLI
  report_template.md        report structure rendered from numeric results
  tests/                    credential-free unit tests
  artifacts/{real,mock}/
    cache/                  ignored raw prompts, responses, trajectories
    checkpoints/            ignored learned parameters
    results/                tracked reports, tables, summaries, plots
```

Real and mock artifacts are strictly separated. Reports are prominently
labelled `REAL LLM OUTPUTS`, `PARTIAL REAL OUTPUTS`, `NOT RUN`, or
`SYNTHETIC OFFLINE MOCK OUTPUTS - NOT EXPERIMENTAL EVIDENCE`.

## Environment and dependencies

Use the existing external interpreter for every command:

```text
/Users/IDLE_And_R/.virtualenvs/openjiuwen-agent-core/bin/python
```

Required model variables:

- `MODEL_PROVIDER`
- `API_BASE`
- `API_KEY`
- `MODEL_NAME`
- `LLM_SSL_VERIFY`

Never place them in this directory. `API_KEY` is passed directly to
`ModelClientConfig`; it is excluded from cache keys, manifests, reports, logs,
and exceptions. Non-secret provider and model names are recorded.

Example-only dependencies are listed in `requirements.txt`. Install them into
the external environment, never into a repository-local virtual environment:

```bash
/Users/IDLE_And_R/.virtualenvs/openjiuwen-agent-core/bin/python \
  -m pip install -r examples/adaptive_multi_agent_collab/requirements.txt
```

## Commands

Credential-free unit tests:

```bash
/Users/IDLE_And_R/.virtualenvs/openjiuwen-agent-core/bin/python \
  -m pytest examples/adaptive_multi_agent_collab/tests -q \
  -o addopts= \
  -o cache_dir=examples/adaptive_multi_agent_collab/.pytest_cache \
  --basetemp=examples/adaptive_multi_agent_collab/tmp/pytest
```

Synthetic end-to-end smoke:

```bash
/Users/IDLE_And_R/.virtualenvs/openjiuwen-agent-core/bin/python \
  -m examples.adaptive_multi_agent_collab.run_experiment smoke --offline-mock
```

Real 2/1/2 smoke:

```bash
/Users/IDLE_And_R/.virtualenvs/openjiuwen-agent-core/bin/python \
  -m examples.adaptive_multi_agent_collab.run_experiment smoke
```

Real 30/10/20 pilot:

```bash
/Users/IDLE_And_R/.virtualenvs/openjiuwen-agent-core/bin/python \
  -m examples.adaptive_multi_agent_collab.run_experiment all \
  --train-size 30 --val-size 10 --test-size 20 \
  --seed 42 --concurrency 3 --max-api-calls 650
```

Exact resume command for that pilot configuration:

```bash
/Users/IDLE_And_R/.virtualenvs/openjiuwen-agent-core/bin/python \
  -m examples.adaptive_multi_agent_collab.run_experiment all \
  --train-size 30 --val-size 10 --test-size 20 \
  --seed 42 --concurrency 3 --max-api-calls 650 \
  --request-timeout 90.0 --epochs 200 --patience 25 \
  --learning-rate 0.001 --weight-decay 0.0001 \
  --artifact-root /Users/IDLE_And_R/Downloads/Huawei/agent-core/examples/adaptive_multi_agent_collab/artifacts
```

Generate or resume trajectories:

```bash
/Users/IDLE_And_R/.virtualenvs/openjiuwen-agent-core/bin/python \
  -m examples.adaptive_multi_agent_collab.run_experiment generate
```

Train, evaluate, or regenerate reports without LLM calls:

```bash
/Users/IDLE_And_R/.virtualenvs/openjiuwen-agent-core/bin/python \
  -m examples.adaptive_multi_agent_collab.run_experiment train
/Users/IDLE_And_R/.virtualenvs/openjiuwen-agent-core/bin/python \
  -m examples.adaptive_multi_agent_collab.run_experiment evaluate
/Users/IDLE_And_R/.virtualenvs/openjiuwen-agent-core/bin/python \
  -m examples.adaptive_multi_agent_collab.run_experiment report
```

Every completed call is appended immediately to a model- and prompt-versioned
JSONL cache. Re-run the same command to resume. Use `--force-regenerate` only
when existing valid calls should be replaced. The default real API cap is 650
attempts, including retries. A budget stop preserves progress and prints an
exact resume command.

Important options include `--offline-mock`, `--force-regenerate`,
`--concurrency`, `--train-size`, `--val-size`, `--test-size`, `--seed`,
`--epochs`, `--patience`, `--learning-rate`, `--weight-decay`,
`--max-api-calls`, `--request-timeout`, and `--artifact-root`.

## Data and model

With seed 42, real mode loads the current Hugging Face identifier
`tau/commonsense_qa`, then deterministically samples 30 training and 10
validation examples from non-overlapping parts of its train split and 20
held-out examples from its validation split. The official test split is not
used because its labels are hidden. Selected IDs and split assertions are
written to the manifest. Gold labels are never sent to an LLM.

Mock mode uses visibly labelled deterministic synthetic MCQs and never loads
or overwrites real artifacts.

`Enc_x` and `Enc_H` use deterministic normalized signed feature hashing with a
stable cryptographic hash. They are fixed and detached. `W` contains trainable
agent embeddings and a shared one-hidden-layer MLP. It produces positive
softmax-normalized weights from query, agent identity, and completed
agent-perspective history. Training uses cached fixed support tensors, AdamW,
validation-only checkpoint selection, MPS when available, and CPU otherwise.

The evaluated deployable methods are:

1. Agent 0's independent answer;
2. independent three-agent majority;
3. reviewer-revision terminal answers with uniform weights;
4. the same terminal answers with learned `W`.

Initial and terminal oracles are reported only as
`ORACLE DIAGNOSTIC - NOT A DEPLOYABLE BASELINE`.

## Artifacts and security

Raw prompts, raw responses, caches, and checkpoints are ignored by Git.
Numerical summaries, CSV tables, Markdown reports, and final plots remain
visible. A run can be resumed from:

- `artifacts/real/cache/calls.jsonl` and `trajectories.jsonl`;
- `artifacts/mock/cache/calls.jsonl` and `trajectories.jsonl`.

The cache tolerates an interrupted final line and uses the latest valid
duplicate record. Cache identities include dataset ID/split, provider, model,
agent/stage/edge identity, prompt hash, and non-secret generation settings.

## Limitations

This small pilot is not evidence of statistical significance or
state-of-the-art performance. All three roles may use the same LLM. Prompted
role diversity is not equivalent to heterogeneous model diversity. The learned
weighting model is trained on very little data, one session, one collaboration
scheme, and normally one terminal answer per agent. Provider usage fields may
be absent. The report preserves parse failures, runtime failures, harmful
revisions, and negative learned-weight results.

## Future work

The full model still requires learned query- and history-dependent routing
policy `alpha`; standard session-level REINFORCE; conversation-shaped policy
learning; conversation-level leave-one-out credit `D_ji`; immediate session
reward `R_s`; complete return `G_s`; future-only return `F_s`; multiple
sessions; carried-forward answers and collaboration history; debate, judge,
teacher, extended reviewer, and self-reflection schemes; judge-specific answer
collection and multiple incoming judge submissions; full FIFO queues; several
conversations initiated by one agent; variable internal rounds; cost
penalties; training `W` across all sessions; stronger fixed encoders and
optionally trainable `Enc_x`/`Enc_H` with larger data; textual optimization of
`gamma`; critic and editor LLMs; candidate prompt generation; held-out prompt
validation; heterogeneous LLM families; larger CommonsenseQA and AQuA
experiments; StrategyQA smoke comparisons; numerical normalization;
open-ended semantic equivalence; repeated seeds; statistically meaningful
evaluation; and production-grade observability and recovery.
