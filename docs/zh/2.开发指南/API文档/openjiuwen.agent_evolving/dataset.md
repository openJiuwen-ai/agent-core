# openjiuwen.agent_evolving.dataset

`openjiuwen.agent_evolving.dataset` 提供自演进用的样本数据类型与加载、打乱、划分能力。

---

## class openjiuwen.agent_evolving.dataset.case.Case

单条训练/评估样本（Pydantic 模型）。

* **inputs**(Dict[str, Any])：输入数据（如 query 或对话内容），至少一对键值。
* **label**(Dict[str, Any])：期望答案或期望输出，至少一对键值。
* **tools**(List[ToolInfo]，可选)：该样本可用的工具列表。默认值：`None`。
* **case_id**(str，可选)：样本唯一标识；不传则自动生成。默认值：由 `uuid.uuid4().hex` 生成。

---

## class openjiuwen.agent_evolving.dataset.case.EvaluatedCase

带模型输出与分数的评估结果。

* **case**(Case)：原始 Case。
* **answer**(Dict[str, Any]，可选)：模型输出/预测。默认值：`None`。
* **score**(float)：综合分数，取值 [0, 1]；校验时会被裁剪到该区间。默认值：`0.0`。
* **reason**(str)：评分理由或错误分析。默认值：`""`。
* **per_metric**(Dict[str, float]，可选)：使用 MetricEvaluator 时各指标得分。默认值：`None`。

通过属性 `inputs`、`label`、`tools`、`case_id` 可代理访问底层 `case` 的对应字段。

---

## class openjiuwen.agent_evolving.dataset.case_loader.CaseLoader

对 Case 列表的封装，支持迭代与按比例划分。

```text
class openjiuwen.agent_evolving.dataset.case_loader.CaseLoader(cases: List[Case])
```

**参数**：

* **cases**(List[Case])：要包装的 Case 列表。

### __len__() -> int

返回样本数量。

### __iter__() -> Iterator[Case]

按顺序迭代 Case。

### get_cases() -> List[Case]

返回内部 Case 列表的拷贝。

### split(ratio: float, seed: int = 0) -> Tuple[CaseLoader, CaseLoader]

按比例将样本划分为两份（先按 seed 打乱再切分）。

**参数**：

* **ratio**(float)：划分比例，取值范围为 [0.0, 1.0]。
* **seed**(int，可选)：随机种子，保证可复现。默认值：`0`。

**返回**：

**(CaseLoader, CaseLoader)**，前一份与后一份的 CaseLoader。

**异常**：

* **ValueError**：`ratio` 不在 [0.0, 1.0] 时抛出。

---

## func openjiuwen.agent_evolving.dataset.case_loader.shuffle_cases(cases, seed=0)

对 Case 列表按给定种子打乱，不修改原列表。

**参数**：

* **cases**(List[Case])：待打乱的列表。
* **seed**(int，可选)：随机种子。默认值：`0`。

**返回**：

**List[Case]**，新的已打乱列表。

---

## func openjiuwen.agent_evolving.dataset.case_loader.split_cases(cases, ratio)

按比例将 Case 列表切分为两段。

**参数**：

* **cases**(List[Case])：待切分列表。
* **ratio**(float)：比例，取值范围为 [0.0, 1.0]。

**返回**：

**Tuple[List[Case], List[Case]]**，前一段与后一段。

**异常**：

* **ValueError**：`ratio` 不在 [0.0, 1.0] 时抛出。
