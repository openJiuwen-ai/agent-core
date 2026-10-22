# Harness RSI runtime controls and recovery

## Solver configuration

The initial solver budget and host ceiling both default to 300. At exhaustion,
RSI stops without an additional finalization/model call. Harness runtime.yaml
can change max_iterations within the host ceiling. Explicit frozen legacy
budgets are preserved. RSI permits only error or return_observations exhaustion
policies; neither performs an additional model call.

```yaml
evaluator:
  solver_max_iterations: 300
  solver_on_max_iterations: error
  solver_max_iterations_hard_limit: 300
```

Candidates may add/modify/remove package-local runtime.yaml, containing only
max_iterations and on_max_iterations. The evaluator owns the immutable hard
ceiling, normally 300 (or an explicitly larger initial budget when no ceiling
is supplied). Credentials, model connections and grading stay outside this
surface. Actual configuration and package bytes enter evaluation identity;
replay, retention and full checkpoints preserve each selected policy and its
resource costs. Existing score/regression gates remain unchanged.

JiuwenSwarm accepts all three evaluator controls directly or in training_options
and freezes them into new task profiles. No existing experiment is rewritten.


## Interactive candidate authors and repair

Action execution and verification repair use native read_file, grep, glob,
list_files, write_file, and edit_file tools. Authors can read local package
dependencies and supported host contracts. Writes are restricted to declared
action paths. Repair writes remain inside the integration package and cannot
modify audit or publication files. Tools enforce these bounds independently
of the model's claims; implicit skill discovery cannot add a shell bypass.

The author reads, edits, inspects, and returns a completion report. The controller
checks actual filesystem changes and syntax before merging. Legacy structured
file_writes responses remain supported, but are no longer the execution model.
A modify action with no baseline target fails before generation. No-progress
repair attempts stop rather than repeating identical requests. Invocation-owned
tools and filesystem resources are released without stopping the shared Runner.

## Runtime evidence

Single-Harness executions acquire native observability and create an agent run
root before invoking the solver. Role archives are stored under case/tr; exporter
records use case/otel. Execution metadata includes `execution_evidence` with
archive location, counts, capture issues, timing completeness, and truncation.
Missing or corrupt archives have unknown counts. Incomplete evidence does not
establish that a capability was unused or invoked late: both primary candidate
and epoch checkpoint gates classify those negative findings as inconclusive.
Single-round invoke and stream share an invocation-local steering channel with
the inner ReAct loop.

## Judge behavior and output budgets

The Judge's native search performs bounded literal substring searches without
launching an external rg binary. It preserves the read-only evidence sandbox.
Search limits and omitted evidence are reported explicitly.

Model output limits are respected at configuration and invocation boundaries,
including max_tokens/max_completion_tokens in extra_body. Null limits are omitted;
an unset limit still delegates length to the provider. RSI disables reasoning
according to its existing independent reasoning policy.

An assessment attempt validates one invocation. Initial grading, format repair,
and regrading share one attempt cap rather than multiplying nested retry budgets.
Invalid or truncated assessments and unavailable format repairs trigger bounded
regrading from the same complete frozen evidence, with specific validation feedback.
Format repair never invents scores. Cancellation and permanent provider errors
propagate. Each attempt records its response, failure category, finish reason,
and token usage where the provider supplies them. The retry budget is
`judge_max_retries` additional assessment attempts.

## Progress and diagnosis

A candidate can continue inside its batch budget when independently scored
requirements improve with evidence and no requirement regresses. Aggregate
score increases alone are insufficient. This local continuation does not publish
a candidate or weaken final replay, retention, or global promotion checks.

Diagnosis requests distinguish original input guarantees from self-authored
probe assumptions. Provided task_contract_checks select stable clause_id values; the controller
restores original text and line ranges. Legacy quotes require a unique exact
match. Citation/schema failures and real contract conflicts produce separate feedback.
This validation does not prove arbitrary mathematical or causal claims.


## Bounded output correction

Each of `evaluator`, `evaluation_result_analyzer`, and `member_optimizer` accepts:

```yaml
output_repair:
  max_attempts: 3
  max_stagnant_attempts: 2
```

Both fields require positive integers. The attempt cap includes the initial
output; 1 disables correction. Repeated identical errors and identical output
or package state stop at the stagnation threshold (`no_progress`). The overall
cap stops with `attempt_limit_reached`, even when outputs keep changing. Native
agent tool iteration limits remain separate and finite.

Planner parsing/finalization and semantic validation share a cap. Judge format
repair and full-evidence reassessment also share a cap, further limited by
`judge_max_retries + 1` (at most 6 under the existing Judge configuration limit).
Existing diagnosis/stage/attribution limits may reduce the allowance.
Analyzer preserves valid diagnoses, records individual raw attempts and field
errors, and marks partially usable diagnosis results as partial. Missing schema
fields are not evidence of a semantic contract violation. Executor feeds
repairable status/schema/static errors back to its tool agent. Repair feeds
no-change and transient failures back within its limit. Scope violations,
permanent provider failures, and cancellation do not trigger ordinary retries.

Behavior checks correct invalid structure and unsupported verbatim citations.
Failed observations remain diagnostic artifacts and are not reused as successful
cache entries. Genuine evidence gaps remain unknown, never fabricated findings.

JiuwenSwarm accepts `output_repair_max_attempts` (default 3) and
`output_repair_max_stagnant_attempts` (default 2), directly or in
`training_options`, and freezes them into all three stage configurations.
Existing frozen task profiles are preserved.

## Invocation logs and search-led diagnosis

DeepAgentConfig.invocation_log_dir enables a unique invocation-<id>.jsonl per
call. Records carry agent, role, session, invocation and parent invocation IDs.
Subagents inherit the directory unless overridden, using separate files. RSI
solvers record under case/logs; other native agents use their own stage roots.
Analyzer logs and bounded evidence are archived under diagnosis_attempts.
Global logging stays unchanged. Core logging summaries are sanitized and limited
to 4 MiB per file and 2 KiB per record; raw tool results remain in trajectories.

Effective runtime_config.json, termination.json, evidence_manifest.json,
task_clauses.json and runtime_log_search.json guide diagnosis. Search levels
WARNING/ERROR/CRITICAL first, then termination/exception/timeout keywords, including
INFO records. Do not read entire logs by default. Initial results are limited to
50 hits, 500 characters per summary and 32 KiB total. Read nearby context as needed.
Physical evidence reads are capped at 4 MiB per source and 16 MiB total; oversized
historical sources preserve a tail-first/head sample with explicit byte ranges.
Omissions never establish absence. Distinguish budget insufficiency, repeated
ineffective work, environment failures and missing finalization from evidence.

The native hard ceiling supports single-round mode only; combining it with the
outer task loop is rejected. Existing task-loop configurations without a ceiling
remain unchanged. Streaming uses a separate producer task and bounded queue so
logging contexts enter/exit in one task and never leak into stream consumers.

## Judge parsing boundary

The Judge runtime returns model text without verdict parsing. The assessment
loop persists response_<n>.json before parsing/scoring. Invalid JSON and schema
errors enter the existing bounded correction loop; format repair and regrading
share the same cap. JSONDecodeError must not escape before raw output capture.

grading_events.jsonl records raw-output receipt, validation failures, next
recovery mode, and validated completion, referencing saved raw output when
available. This grading lifecycle is separate from a successful DeepAgent call.
Permanent provider errors and cancellation retain their existing handling.
