# openjiuwen.agent_evolving.evaluator

`openjiuwen.agent_evolving.evaluator` 提供自演进评估接口：将 (case, 模型预测) 转为带分数与理由的 EvaluatedCase；支持单条评估与批量并行评估，以及基于多指标的 MetricEvaluator。

---

## class openjiuwen.agent_evolving.evaluator.evaluator.BaseEvaluator

评估器抽象基类：实现 evaluate() 单条评估，batch_evaluate() 提供并行批量评估。

### abstractmethod evaluate(case: Case, predict: Dict[str, Any]) -> EvaluatedCase

对单条样本的模型输出进行评分。

**参数**：

* **case**(Case)：原始 Case（含 inputs、label）。
* **predict**(Dict[str, Any])：模型预测。

**返回**：

**EvaluatedCase**，含 score 与 reason。

### batch_evaluate(cases, predicts, num_parallel=1) -> List[EvaluatedCase]

多 case 与多 predict 一一对应，在 num_parallel 个 worker 下并行调用 evaluate。cases 可为 List[Case] 或 CaseLoader。

**参数**：

* **cases**：Case 列表或 CaseLoader。
* **predicts**(List[Dict[str, Any]])：每条对应的模型输出。
* **num_parallel**(int，可选)：并行数，需在 TuneConstant 规定范围内。默认值：`1`。

**返回**：

**List[EvaluatedCase]**。

**异常**：

* **StatusCode.TOOLCHAIN_EVALUATOR_EXECUTION_ERROR**：cases 与 predicts 长度不一致时抛出。

---

## class openjiuwen.agent_evolving.evaluator.evaluator.DefaultEvaluator

使用 LLM 作为评判，根据问题/期望答案/模型答案判断是否一致，给出通过与否与理由，分数映射为 0/1。

```text
class DefaultEvaluator(
    model_config: ModelRequestConfig,
    model_client_config: ModelClientConfig,
    metric: str = "",
)
```

**参数**：

* **model_config**(ModelRequestConfig)：评判用 LLM 请求配置。
* **model_client_config**(ModelClientConfig)：评判用 LLM 客户端配置。
* **metric**(str，可选)：自定义评估指标描述，会填入模板。默认值：`""`。

### evaluate(case, predict) -> EvaluatedCase

用 LLM 评判模板格式化 question、expected_answer、model_answer，调用模型得到结果并解析为 pass/reason，分数为 1.0 或 0.0。

---

## class openjiuwen.agent_evolving.evaluator.evaluator.MetricEvaluator

使用一个或多个 Metric 打分，支持聚合（如 mean、first）及 per_metric 细分。

```text
class MetricEvaluator(
    metrics: Union[Metric, List[Metric]],
    aggregate: str = "mean",
)
```

**参数**：

* **metrics**：单个 Metric 或 Metric 列表。
* **aggregate**(str，可选)：聚合方式，如 `"mean"`、`"first"`。默认值：`"mean"`。

### evaluate(case, predict) -> EvaluatedCase

对每条 case 用所有 metrics 计算得分，按 aggregate 得到综合分数，并写入 EvaluatedCase.per_metric。

---

## class openjiuwen.agent_evolving.evaluator.metrics.base.Metric

评估指标抽象基类：子类实现 name 与 compute（及可选 compute_batch）。

### @property abstractmethod name() -> str

指标唯一名称。

### @property higher_is_better() -> bool

是否分数越高越好，默认 True。

### abstractmethod compute(prediction, label, **kwargs) -> MetricResult

单条 (prediction, label) 的得分。MetricResult 为 float 或 Dict[str, float]。

### compute_batch(predictions, labels, **kwargs) -> List[MetricResult]

默认逐条调用 compute；子类可重写以批量计算。

---

## class openjiuwen.agent_evolving.evaluator.metrics.exact_match.ExactMatchMetric

精确匹配或归一化匹配：一致为 1.0，否则 0.0。normalize=True 时先对字符串做归一化再比较。

```text
class ExactMatchMetric(normalize: bool = True)
```

* **name**：`"exact_match"`。
* **higher_is_better**：True。

### compute(prediction, label, **kwargs) -> float

根据 normalize 决定是否先归一化再比较，返回 1.0 或 0.0。

---

## class openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.LLMAsJudgeMetric

使用 Model 作为评判，对 (prediction, label) 做语义一致性判断，可选传入 question 上下文；返回 1.0（通过）或 0.0（不通过）。

```text
class LLMAsJudgeMetric(
    model_config: ModelRequestConfig,
    model_client_config: ModelClientConfig,
    user_metrics: str = "",
)
```

**参数**：

* **model_config**(ModelRequestConfig)：评判用模型请求配置。
* **model_client_config**(ModelClientConfig)：评判用模型客户端配置。
* **user_metrics**(str，可选)：用户自定义指标描述。默认值：`""`。

* **name**：`"llm_as_judge"`。
* **higher_is_better**：True。

### compute(prediction, label, question=None, **kwargs) -> float

用模板格式化 question、expected_answer、model_answer，调用模型并解析 JSON 中的 result，返回 1.0 或 0.0。
