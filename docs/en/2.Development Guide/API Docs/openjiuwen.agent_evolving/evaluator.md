# openjiuwen.agent_evolving.evaluator

`openjiuwen.agent_evolving.evaluator` provides self-evolving evaluation interfaces: converts (case, model prediction) to EvaluatedCase with score and reason; supports single evaluation, batch parallel evaluation, and MetricEvaluator based on multiple metrics.

---

## class openjiuwen.agent_evolving.evaluator.evaluator.BaseEvaluator

Abstract evaluator base class: implements evaluate() for single evaluation, batch_evaluate() provides parallel batch evaluation.

### abstractmethod evaluate(case: Case, predict: Dict[str, Any]) -> EvaluatedCase

Scores model output for a single sample.

**Parameters**:

* **case**(Case): Original Case (contains inputs, label).
* **predict**(Dict[str, Any]): Model prediction.

**Returns**:

**EvaluatedCase**, contains score and reason.

### batch_evaluate(cases, predicts, num_parallel=1) -> List[EvaluatedCase]

Multiple cases and predictions are paired, called evaluate in parallel under num_parallel workers. cases can be List[Case] or CaseLoader.

**Parameters**:

* **cases**: Case list or CaseLoader.
* **predicts**(List[Dict[str, Any]]): Corresponding model outputs for each case.
* **num_parallel**(int, optional): Parallelism count, must be within TuneConstant range. Default: `1`.

**Returns**:

**List[EvaluatedCase]**.

**Exceptions**:

* **StatusCode.TOOLCHAIN_EVALUATOR_EXECUTION_ERROR**: Raised when cases and predicts have different lengths.

---

## class openjiuwen.agent_evolving.evaluator.evaluator.DefaultEvaluator

Uses LLM as judge, judges whether question/expected answer/model answer are consistent, gives pass/fail with reason, score maps to 0/1.

```text
class DefaultEvaluator(
    model_config: ModelRequestConfig,
    model_client_config: ModelClientConfig,
    metric: str = "",
)
```

**Parameters**:

* **model_config**(ModelRequestConfig): LLM request configuration for judgment.
* **model_client_config**(ModelClientConfig): LLM client configuration for judgment.
* **metric**(str, optional): Custom evaluation metric description, filled into template. Default: `""`.

### evaluate(case, predict) -> EvaluatedCase

Formats question, expected_answer, model_answer with LLM judgment template, calls model and parses result as pass/reason, score is 1.0 or 0.0.

---

## class openjiuwen.agent_evolving.evaluator.evaluator.MetricEvaluator

Uses one or multiple Metrics for scoring, supports aggregation (e.g., mean, first) and per_metric breakdown.

```text
class MetricEvaluator(
    metrics: Union[Metric, List[Metric]],
    aggregate: str = "mean",
)
```

**Parameters**:

* **metrics**: Single Metric or list of Metrics.
* **aggregate**(str, optional): Aggregation method, such as `"mean"`, `"first"`. Default: `"mean"`.

### evaluate(case, predict) -> EvaluatedCase

Calculates scores for each case using all metrics, gets comprehensive score by aggregate, and writes to EvaluatedCase.per_metric.

---

## class openjiuwen.agent_evolving.evaluator.metrics.base.Metric

Abstract base class for evaluation metrics: subclasses implement name and compute (and optional compute_batch).

### @property abstractmethod name() -> str

Unique metric name.

### @property higher_is_better() -> bool

Whether higher score is better, default True.

### abstractmethod compute(prediction, label, **kwargs) -> MetricResult

Score for single (prediction, label). MetricResult is float or Dict[str, float].

### compute_batch(predictions, labels, **kwargs) -> List[MetricResult]

Default calls compute one by one; subclasses can override for batch computation.

---

## class openjiuwen.agent_evolving.evaluator.metrics.exact_match.ExactMatchMetric

Exact match or normalized match: 1.0 if match, otherwise 0.0. When normalize=True, normalizes strings first then compares.

```text
class ExactMatchMetric(normalize: bool = True)
```

* **name**: `"exact_match"`.
* **higher_is_better**: True.

### compute(prediction, label, **kwargs) -> float

Depending on normalize, normalizes first then compares, returns 1.0 or 0.0.

---

## class openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.LLMAsJudgeMetric

Uses Model as judge, makes semantic consistency judgment on (prediction, label), optionally accepts question context; returns 1.0 (pass) or 0.0 (fail).

```text
class LLMAsJudgeMetric(
    model_config: ModelRequestConfig,
    model_client_config: ModelClientConfig,
    user_metrics: str = "",
)
```

**Parameters**:

* **model_config**(ModelRequestConfig): Model request configuration for judgment.
* **model_client_config**(ModelClientConfig): Model client configuration for judgment.
* **user_metrics**(str, optional): User-customized metric description. Default: `""`.

* **name**: `"llm_as_judge"`.
* **higher_is_better**: True.

### compute(prediction, label, question=None, **kwargs) -> float

Formats question, expected_answer, model_answer with template, calls model and parses result from JSON, returns 1.0 or 0.0.
