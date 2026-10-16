# Learned-W post-hoc diagnostics

**POST-HOC DIAGNOSTIC OF EXISTING REAL LLM OUTPUTS — NO NEW LLM OR NETWORK CALLS**

This analysis uses only the existing manifest-selected 30/10/20 trajectories,
the saved `weighting.pt` checkpoint, and the existing result tables. It did not
regenerate a trajectory or alter an existing result file. Additional methods
below are diagnostic baselines, not new primary experimental claims.

## Executive conclusion

Learned W changed no held-out decision principally because 19 of 20 terminal
answer pools were unanimous. The only remaining opportunity was `D/E/E` with
gold `D`; Agent 2 was part of the incorrect `E` majority. Uniform weighting
and the learned weights `[0.002081, 0.000225, 0.997694]` therefore both selected
`E`.

The collapse is not an indexing, checkpoint, tie-breaking, detachment, or
gradient bug. It is the expected empirical-risk solution to extremely sparse
weighting supervision: only 4/30 training examples and 1/10 validation example
had terminal disagreement, and every one of those informative cases favored
Agent 2. The explicit Agent-2 embedding absorbed that signal and became a
nearly static selector. The sole test disagreement instead favored Agent 0,
so the learned rule did not generalize.

## 1. Held-out decision opportunities

| Terminal pattern | Queries |
|---|---:|
| Unanimous | 19 |
| 2-versus-1 | 1 |
| Three-way disagreement | 0 |
| Total opportunities for weighting to change a label | 1 |

On the one terminal-disagreement query:

- uniform accuracy: `0/1`;
- learned-W accuracy: `0/1`;
- predictions changed by W: `0`;
- changed decisions that helped: `0`;
- changed decisions that hurt: `0`.

The case started with a correct unanimous `D/D/D`, then reviewer–revision
produced `D/E/E`. Uniform and learned W both predicted `E`; the gold label was
`D`. Thus reviewer–revision created the only held-out terminal disagreement
and made it harmful.

## 2. Agent-2 alignment

### Accuracy by split

| Split | Agent 2 initial | Agent 2 terminal |
|---|---:|---:|
| Train | 26/30 = 86.67% | 27/30 = 90% |
| Validation | 8/10 = 80% | 8/10 = 80% |
| Test | 18/20 = 90% | 18/20 = 90% |

### Terminal alignment

| Split | Agent 2 matches uniform | Agent 2 sole dissenter | Correct when sole dissenter |
|---|---:|---:|---:|
| Train | 29/30 | 1 | 1/1 |
| Validation | 9/10 | 1 | 1/1 |
| Test | 20/20 | 0 | not applicable |
| All | 58/60 | 2 | 2/2 |

The learned prediction equals Agent 2's terminal answer on **60/60** examples.
It therefore behaves as a static Agent-2 terminal selector, not as a
meaningfully query-adaptive weighting rule.

Agent 2 is not the strongest held-out terminal agent. Test terminal accuracy is
Agent 0 `19/20`, Agent 1 `18/20`, and Agent 2 `18/20`. Agent 2 was uniquely
strongest on train and validation, which explains why validation-only model
selection preferred it, but that ordering did not hold on the test set.

## 3. Weight collapse

Population standard deviations are reported below. Entropy uses natural logs.

### Train

| Agent | Mean | Min | Max | Standard deviation |
|---|---:|---:|---:|---:|
| 0 | 0.001520 | 0.000370 | 0.002049 | 0.000400 |
| 1 | 0.000195 | 0.000047 | 0.000358 | 0.000070 |
| 2 | 0.998285 | 0.997647 | 0.999583 | 0.000464 |

- Average maximum weight: `0.998285`
- Average entropy: `0.013166` nats
- Maximum weight above 0.90 / 0.95 / 0.99: `30 / 30 / 30`

### Validation

| Agent | Mean | Min | Max | Standard deviation |
|---|---:|---:|---:|---:|
| 0 | 0.001505 | 0.001171 | 0.001797 | 0.000188 |
| 1 | 0.000183 | 0.000134 | 0.000230 | 0.000029 |
| 2 | 0.998312 | 0.997973 | 0.998684 | 0.000209 |

- Average maximum weight: `0.998312`
- Average entropy: `0.013028` nats
- Maximum weight above 0.90 / 0.95 / 0.99: `10 / 10 / 10`

### Test

| Agent | Mean | Min | Max | Standard deviation |
|---|---:|---:|---:|---:|
| 0 | 0.001544 | 0.001036 | 0.002081 | 0.000290 |
| 1 | 0.000200 | 0.000135 | 0.000261 | 0.000038 |
| 2 | 0.998257 | 0.997669 | 0.998829 | 0.000321 |

- Average maximum weight: `0.998257`
- Average entropy: `0.013407` nats
- Maximum weight above 0.90 / 0.95 / 0.99: `20 / 20 / 20`

Across all 60 queries, Agent 2 is the maximum-weight agent 60 times, its
minimum weight is `0.997647`, and mean entropy is `0.013223` nats. The weights
do vary numerically, but only at roughly the fourth decimal place and never
enough to alter the selected agent.

### Query/history effect versus identity

The checkpoint was evaluated without retraining under several controlled
feature substitutions:

- Replacing each query and history with fixed empirical means retained average
  weights `[0.001517, 0.000194, 0.998289]`; mean absolute change from full
  weights was only `0.000208`, and Agent 2 remained argmax on 60/60.
- One hundred deterministic cross-example query/history permutations produced
  mean absolute weight changes of approximately `0.00015–0.00029`; every
  permutation retained Agent 2 as argmax.
- Under all six agent-embedding permutations, the argmax followed whichever
  output slot received the original Agent-2 embedding on every query.
- Permuting history slots while keeping embeddings fixed left output slot 2 as
  argmax every time.
- Equalizing the three explicit embeddings produced near-uniform average
  weights `[0.328, 0.325, 0.347]`.

Query and history features therefore have a measurable numerical effect, but
no measurable decision effect in this run. The explicit trainable identity
embedding dominates. Histories themselves also contain static agent ID and
role, so the equal-embedding check is not perfectly identity-free; the
embedding-permutation result is the stronger evidence.

## 4. Data and implementation audit

All requested implementation checks passed:

- `query_text()` reads only the question and A–E labelled option text.
- `perspective_history()` contains role, initial/reviewer/revision/terminal
  observations and agreement/change indicators, but no gold or computed
  correctness label.
- Changing a cached example's gold label changed only its training target:
  query, history, and support tensors remained bit-identical with maximum
  feature difference `0.0`.
- Actual train, validation, and test ID overlaps are all zero. The test data
  comes from the public CommonsenseQA validation split, while W train and
  validation come from non-overlapping portions of its train split.
- `_train()` receives only train and validation trajectories. Test loss is
  computed only after `_evaluate()` loads the saved checkpoint.
- Checkpoint saving and patience use validation cross-entropy only. The saved
  state is reloaded after training; evaluation reconstructs the configured
  model, strictly loads that state, and uses evaluation mode.
- Support tensors are detached in feature construction, score construction,
  and the loss path. Their range is `[0,1]` and their gradient is `None`.
- A backward pass on a real disagreement produced nonzero gradient norms:
  Agent embeddings `0.007394`, first MLP weight `0.326040`, final MLP weight
  `0.189032`.
- Candidate ordering is consistently `A, B, C, D, E` in support tensors,
  candidate scores, gold indices, and voting.
- Uniform and learned aggregation call the same deterministic weighted
  tie-breaker. Both held-out tie rates are zero, so tie behavior cannot explain
  the identical predictions.

No evidence of an implementation bug was found.

## 5. Training behavior

The configured maximum was 200 epochs and patience was 25. Validation loss
registered a new best under the `1e-8` improvement rule at every epoch, so the
stale counter remained zero. Early stopping worked as implemented but never
triggered; training stopped because it reached the epoch cap.

| Epoch | Train CE | Validation CE |
|---|---:|---:|
| 1 | 1.047615 | 1.160871 |
| 200 | 1.004849 | 1.104963 |

Validation CE was still decreasing at epoch 200, but only by
`0.000001192` from epoch 199. Its improvement from epoch 100 through 200 was
only `0.000278`. The selected epoch is therefore a boundary best on a very
flat tail, not evidence that an especially meaningful change occurred at
epoch 200.

The final validation-minus-train gap is `0.100114`. There is no classical
overfitting reversal in the curve: validation loss never rises. Nevertheless,
the selected Agent-2 rule improves CE over uniform on train and validation but
worsens test CE:

| Split | Uniform CE | Learned CE |
|---|---:|---:|
| Train | 1.052731 | 1.004849 |
| Validation | 1.165756 | 1.104963 |
| Test | 0.985295 | 1.004702 |

That is evidence of small-data selection overfit/non-generalization even
without a rising validation curve.

No alternative random initialisations were run. Because all four informative
train disagreements and the sole validation disagreement favor Agent 2, the
identity of the collapsed agent is more likely to remain Agent 2 than to change
under another seed. The exact sharpness and convergence path could vary; this
is an expectation from the observed objective, not an experimental multi-seed
result.

## 6. Cross-entropy scale

For every agent and candidate, `rho` and `mu` are in `[0,1]`, so
`u = 0.5 * (rho + mu)` is in `[0,1]`. Agent weights are positive and sum to
one. Therefore:

```text
0 <= s_y = sum_k w_k * u_k,y <= 1
```

The observed maximum was `1.000000119`, a float32-roundoff excess over the
mathematical bound.

For five candidates, the best possible bounded score vector is one correct
score of 1 and four incorrect scores of 0. Its cross-entropy is:

```text
-log(exp(1) / (exp(1) + 4 * exp(0)))
= 0.9048324416
```

Thus zero cross-entropy is structurally impossible under the current score
parameterization. The observed train, validation, and test losses are
approximately `0.1000`, `0.2001`, and `0.0999` above this global floor. They
must not be compared directly with an unconstrained classifier whose logits
can separate enough for cross-entropy to approach zero.

## 7. Static reliability diagnostics

These diagnostics reuse terminal answers already generated by the original
collaboration run. They are not independent one-call agent baselines.

| Diagnostic | Train | Validation | Test |
|---|---:|---:|---:|
| Always Agent 0 terminal | 26/30 | 7/10 | **19/20** |
| Always Agent 1 terminal | 23/30 | 7/10 | 18/20 |
| Always Agent 2 terminal | **27/30** | **8/10** | 18/20 |
| Train-terminal-reliability weighted terminal vote | 26/30 | 7/10 | 18/20 |
| Train-initial-reliability weighted terminal vote | 26/30 | 7/10 | 18/20 |
| Uniform terminal collaboration | 26/30 | 7/10 | 18/20 |
| Learned W | **27/30** | **8/10** | 18/20 |

The train-terminal reliabilities are `[0.8667, 0.7667, 0.9000]`.
Normalizing them gives global weights:

```text
[0.342105, 0.302632, 0.355263]
```

Train initial accuracies are all `26/30`, so their normalized reliability
weights are exactly `[1/3, 1/3, 1/3]`. Both train-only global weight sets were
applied to the terminal answers; neither changed any uniform terminal
prediction.

Learned W improves one train and one validation decision over uniform by
selecting Agent 2, but it changes no test prediction. Always selecting Agent 0
is the strongest held-out terminal diagnostic at 19/20, although this is a
post-hoc result and must not be promoted to a newly selected primary baseline.

## 8. Defensible meeting interpretation

The defensible conclusion is:

> The W optimisation and checkpoint path function correctly, but this pilot
> did not demonstrate useful query-adaptive reliability. With only four
> informative training disagreements and one validation disagreement, all
> favoring Agent 2, W learned a static Agent-2 selector. Nineteen of twenty
> test terminal pools were unanimous, and the sole remaining disagreement
> favored Agent 0, so learned W had no opportunity to show a benefit and
> failed to generalize on the one opportunity it had.

This is a combination of expected optimisation behavior and small-data
selection overfit, not an implementation bug. Agent 2 is not genuinely the
strongest held-out terminal agent; Agent 0 is.

Possible improvements, clearly outside the current result:

1. Use substantially more disagreement-rich train and validation data,
   stratify validation diagnostics by disagreement, and repeat across seeds.
2. Regularize or constrain the identity embedding, add entropy/load-balancing
   pressure, and predeclare a static-reliability comparator.
3. Introduce a calibrated candidate-score scale or learned temperature and
   validate it without using held-out test labels.
