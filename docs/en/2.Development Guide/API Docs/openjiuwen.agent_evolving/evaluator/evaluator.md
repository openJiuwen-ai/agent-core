# openjiuwen.agent_evolving.evaluator

`openjiuwen.agent_evolving.evaluator` provides self-evolution evaluation interfaces: converting (case, model prediction) to EvaluatedCase with scores and reasons; supports single evaluation and batch parallel evaluation, as well as MetricEvaluator based on multiple metrics.

---

## class openjiuwen.agent_evolving.evaluator.evaluator.BaseEvaluator

Abstract base class for evaluators: implement `evaluate()` for single evaluation, and `batch_evaluate()` provides parallel batch evaluation.

### abstractmethod evaluate(case: Case, predict: Dict[str, Any]) -> EvaluatedCase

Score model output for a single sample.

**Parameters:**

* **case**(Case): Original Case (containing inputs, label).
* **predict**(Dict[str, Any]): Model prediction.

**Returns:**

**EvaluatedCase** containing score and reason.

### batch_evaluate(cases, predicts, num_parallel=1) -> List[EvaluatedCase]

Multiple cases and predictions are matched one-to-one, calling `evaluate` in parallel with `num_parallel` workers. Cases can be List[Case] or CaseLoader.

**Parameters:**

* **cases**: List of Cases or CaseLoader.
* **predicts**(List[Dict[str, Any]]): Model outputs for each case.
* **num_parallel**(int, optional): Number of parallel workers, must be within the range specified by TuneConstant. Default: `1`.

**Returns:**

**List[EvaluatedCase]**.

**Exceptions:**

* **StatusCode.TOOLCHAIN_EVALUATOR_EXECUTION_ERROR**: Raised when cases and predicts have different lengths.

---

## class openjiuwen.agent_evolving.evaluator.evaluator.DefaultEvaluator

Uses LLM as judge to determine consistency between question/expected answer/model answer, giving pass/fail and reason, with score mapped to 0/1.

```text
class DefaultEvaluator(
    model_config: ModelRequestConfig,
    model_client_config: ModelClientConfig,
    metric: str = "",
)
```

**Parameters:**

* **model_config**(ModelRequestConfig): LLM request configuration for judging.
* **model_client_config**(ModelClientConfig): LLM client configuration for judging.
* **metric**(str, optional): Custom evaluation metric description to be inserted into template. Default: `""`.

### evaluate(case, predict) -> EvaluatedCase

Format question, expected_answer, model_answer using LLM judge template, call model to get result and parse as pass/reason, with score of 1.0 or 0.0.

---

## class openjiuwen.agent_evolving.evaluator.evaluator.MetricEvaluator

Uses one or more Metrics for scoring, supports aggregation (e.g., mean, first) and per_metric breakdown.

```text
class MetricEvaluator(
    metrics: Union[Metric, List[Metric]],
    aggregate: str = "mean",
)
```

**Parameters:**

* **metrics**: Single Metric or list of Metrics.
* **aggregate**(str, optional): Aggregation method, e.g., `"mean"`, `"first"`. Default: `"mean"`.

### evaluate(case, predict) -> EvaluatedCase

Calculate scores for each case using all metrics, get composite score by aggregation, and write to EvaluatedCase.per_metric.

---

## class openjiuwen.agent_evolving.evaluator.metrics.base.Metric

Abstract base class for evaluation metrics: subclasses implement name and compute (and optionally compute_batch).

### @property abstractmethod name() -> str

Unique metric name.

### @property higher_is_better() -> bool

Whether higher score is better, defaults to True.

### abstractmethod compute(prediction, label, **kwargs) -> MetricResult

Score for single (prediction, label). MetricResult is float or Dict[str, float].

### compute_batch(predictions, labels, **kwargs) -> List[MetricResult]

Default implementation calls compute for each item; subclasses can override for batch computation.

---

## class openjiuwen.agent_evolving.evaluator.metrics.exact_match.ExactMatchMetric

Exact match or normalized match: 1.0 if identical, 0.0 otherwise. When normalize=True, strings are normalized before comparison.

```text
class ExactMatchMetric(normalize: bool = True)
```

* **name**: `"exact_match"`.
* **higher_is_better**: True.

### compute(prediction, label, **kwargs) -> float

Compare after normalization if specified, return 1.0 or 0.0.

---

## class openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.LLMAsJudgeMetric

Uses Model as judge to perform semantic consistency judgment on (prediction, label), optionally with question context; returns 1.0 (pass) or 0.0 (fail).

```text
class LLMAsJudgeMetric(
    model_config: ModelRequestConfig,
    model_client_config: ModelClientConfig,
    user_metrics: str = "",
)
```

**Parameters:**

* **model_config**(ModelRequestConfig): Model request configuration for judging.
* **model_client_config**(ModelClientConfig): Model client configuration for judging.
* **user_metrics**(str, optional): User-defined metric description. Default: `""`.

* **name**: `"llm_as_judge"`.
* **higher_is_better**: True.

### compute(prediction, label, question=None, **kwargs) -> float

Format question, expected_answer, model_answer using template, call model and parse result from JSON, return 1.0 or 0.0.