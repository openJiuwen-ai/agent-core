# openjiuwen.agent_evolving.agent_rl.online.judge

LLM-as-a-Judge 奖励评分器。沿四个维度（任务完成度、响应质量、工具使用、连贯性，各 0-10 分）对智能体单轮响应评分，归一化为 `[-1, 1]` 奖励，支持多投票平均与重试逻辑。是 gateway 轨迹流水线（延迟 judge 流）消费的奖励来源。

模块内部分三层：`scoring.py`（纯 prompt/解析/归一化，无 I/O）→ `evaluator.py`（异步 HTTP 投票引擎）→ `judge_scorer.py`（高层客户端适配器）；`judge_server.py` 为可选独立 FastAPI 服务。

## 常量 JUDGE_PROMPT_TEMPLATE

```python
JUDGE_PROMPT_TEMPLATE: str
```

中文评分 prompt 模板，要求模型作为专业 AI Agent 质量评估器，输出严格 JSON（键：`task_completion`、`response_quality`、`tool_usage`、`coherence`、`overall`，各 0-10，以及 `reason`）。包含三个占位符：`{instruction_text}`、`{response_text}`、`{followup_user_feedback}`。

## def openjiuwen.agent_evolving.agent_rl.online.judge.scoring.build_judge_prompt

```python
def build_judge_prompt(*, instruction_text: str = "", response_text: str = "", followup_user_feedback: str = "") -> str
```

格式化 `JUDGE_PROMPT_TEMPLATE`，空字段以中文占位符（`"(无)"`、`"(无回复)"`、`"(无反馈)"`）填充。

**参数**（均为关键字参数）：

* **instruction_text**(str)：指令文本。默认值：`""`。
* **response_text**(str)：响应文本。默认值：`""`。
* **followup_user_feedback**(str)：后续用户反馈。默认值：`""`。

**返回**：

`str`，格式化后的 prompt。

## def openjiuwen.agent_evolving.agent_rl.online.judge.scoring.parse_judge_scores

```python
def parse_judge_scores(content: str, *, raise_on_error: bool = True) -> Optional[dict[str, Any]]
```

从模型响应中稳健提取 judge 评分 JSON 字典。先尝试整体 `json.loads`，再尝试 ```` ```json ````/```` ``` ```` 围栏块（正则 `r"```(?:json)?\s*(.*?)```"`），对每个候选尝试 `json.loads`，失败回退到 `_extract_first_json_object`（扫描 `{` 并用 `JSONDecoder.raw_decode`）。成功后调用 `_ensure_overall` 补全缺失的 `overall`。

**参数**：

* **content**(str)：模型响应文本。
* **raise_on_error**(bool)：解析失败时是否抛出 `ValueError`。为 `False` 时返回 `None`。默认值：`True`。

**返回**：

`Optional[dict[str, Any]]`，评分字典；`raise_on_error=False` 且不可解析时返回 `None`。

## def openjiuwen.agent_evolving.agent_rl.online.judge.scoring.normalize_overall_score

```python
def normalize_overall_score(overall: float) -> float
```

将原始 0-10 分映射到 `[-1, 1]`，公式为 `(overall - 5.0) / 5.0`。

**参数**：

* **overall**(float)：原始 0-10 分。

**返回**：

`float`，归一化后的 `[-1, 1]` 奖励。

## class openjiuwen.agent_evolving.agent_rl.online.judge.evaluator.JudgeEvaluatorConfig

```python
@dataclass
class JudgeEvaluatorConfig(llm_url: str, model_id: str, api_key: str = "", num_votes: int = 1, temperature: float = 0.1, max_completion_tokens: int = 4096, max_retries: int = 0, retry_backoff_sec: float = 0.0)
```

持有 judge LLM 端点与采样配置的普通 dataclass。`llm_url` 与 `model_id` 必填，其余有默认值。

**字段**：

* **llm_url**(str)：LLM 服务 URL。
* **model_id**(str)：模型 ID。
* **api_key**(str)：Bearer API key。默认值：`""`。
* **num_votes**(int)：投票数。默认值：`1`。
* **temperature**(float)：采样温度。默认值：`0.1`。
* **max_completion_tokens**(int)：最大补全 token 数。默认值：`4096`。
* **max_retries**(int)：最大重试次数。默认值：`0`。
* **retry_backoff_sec**(float)：重试退避基数（秒）。默认值：`0.0`。

## async def openjiuwen.agent_evolving.agent_rl.online.judge.evaluator.evaluate_judge_scores

```python
async def evaluate_judge_scores(*, client: httpx.AsyncClient, config: JudgeEvaluatorConfig, response_text: str, instruction_text: str, followup_user_feedback: str = "", session_id: str = "", turn_num: int = 0, logger: Optional[logging.Logger] = None) -> dict[str, Any]
```

核心评分入口。构建消息，通过 `asyncio.gather` 并行运行 `config.num_votes` 个 `_query_vote`，平均各投票的 `overall`（空时默认 `5.0`），经 `normalize_overall_score` 归一化，记录日志 `[Judge] session=%s turn=%d votes=%s -> overall=%.2f score=%.3f`。

**参数**（均为关键字参数）：

* **client**(httpx.AsyncClient)：异步 HTTP 客户端。
* **config**(JudgeEvaluatorConfig)：评估器配置。
* **response_text**(str)：响应文本。
* **instruction_text**(str)：指令文本。
* **followup_user_feedback**(str)：后续用户反馈。默认值：`""`。
* **session_id**(str)：会话 ID。默认值：`""`。
* **turn_num**(int)：轮次号。默认值：`0`。
* **logger**(Optional[logging.Logger]，可选)：日志器。默认值：`None`。

**返回**：

`dict[str, Any]`，结构如下：

```python
{
    "score": float,           # 归一化后 [-1, 1]
    "overall_raw": float,      # 平均原始 0-10 分
    "votes": list[float],      # 各投票的 overall 值
    "details": dict | list,    # num_votes==1 时为单个投票字典，否则为列表
    "model": str,              # config.model_id
    "session_id": str,
    "turn_num": int,
}
```

## def openjiuwen.agent_evolving.agent_rl.online.judge.evaluator.build_judge_messages

```python
def build_judge_messages(*, response_text: str, instruction_text: str, followup_user_feedback: str) -> list[dict[str, str]]
```

经 `_sanitize_text` 净化（含中文占位回退）后，用 `build_judge_prompt` 构建 prompt，包装为 `[{"role": "user", "content": prompt}]`。

**参数**（均为关键字参数）：

* **response_text**(str)：响应文本。
* **instruction_text**(str)：指令文本。
* **followup_user_feedback**(str)：后续用户反馈。

**返回**：

`list[dict[str, str]]`，单条 user 消息。

## class openjiuwen.agent_evolving.agent_rl.online.judge.judge_scorer.JudgeScorer

```python
class JudgeScorer(*, judge_url: str, judge_model: str, api_key: str = "EMPTY", timeout: float = 60.0, num_votes: int = 1, max_retries: int = 2, retry_backoff_sec: float = 0.2, http_client: Optional[httpx.AsyncClient] = None)
```

高层异步客户端。持有或接受共享 `httpx.AsyncClient`，内部构建 `JudgeEvaluatorConfig`（固定 `temperature=0.1`、`max_completion_tokens=4096`）。

**参数**（均为关键字参数）：

* **judge_url**(str)：judge LLM 服务 URL。
* **judge_model**(str)：judge 模型名。
* **api_key**(str)：Bearer API key。默认值：`"EMPTY"`。
* **timeout**(float)：HTTP 超时（秒）。默认值：`60.0`。
* **num_votes**(int)：投票数。默认值：`1`。
* **max_retries**(int)：最大重试次数。默认值：`2`。
* **retry_backoff_sec**(float)：重试退避基数（秒）。默认值：`0.2`。
* **http_client**(Optional[httpx.AsyncClient]，可选)：外部注入的 HTTP 客户端。默认值：`None`。

### async def close

```python
async def close() -> None
```

仅当客户端为内部创建时关闭 HTTP 客户端。

### async def score

```python
async def score(*, response_text: str, instruction_text: str, followup_user_feedback: str, session_id: str = "", turn_num: int = 0) -> dict[str, Any]
```

委托 `evaluate_judge_scores(..., logger=logger)`，随后从结果中移除 `model`、`session_id`、`turn_num` 键后返回（返回字典仅含 `score`、`overall_raw`、`votes`、`details`）。

**参数**（均为关键字参数）：

* **response_text**(str)：响应文本。
* **instruction_text**(str)：指令文本。
* **followup_user_feedback**(str)：后续用户反馈。
* **session_id**(str)：会话 ID。默认值：`""`。
* **turn_num**(int)：轮次号。默认值：`0`。

**返回**：

`dict[str, Any]`，评分结果（已剥离元数据）。

### @staticmethod def _parse_scores (私有)

```python
@staticmethod
def _parse_scores(content: str) -> dict[str, Any]
```

`parse_judge_scores(content)` 的薄封装（默认 `raise_on_error=True`）。

## class openjiuwen.agent_evolving.agent_rl.online.judge.judge_server.JudgeConfig

```python
@dataclass
class JudgeConfig(JudgeEvaluatorConfig)
```

继承 `JudgeEvaluatorConfig`，新增两个服务端字段：

* **timeout**(float)：HTTP 客户端超时。默认值：`120.0`。
* **expected_api_key**(str)：`/score` 端点鉴权用 API key。默认值：`""`。

## class openjiuwen.agent_evolving.agent_rl.online.judge.judge_server.ScoreRequest

```python
class ScoreRequest(BaseModel)
```

`/score` 请求体 Pydantic 模型。字段：`response_text: str = ""`、`instruction_text: str = ""`、`followup_user_feedback: str = ""`、`session_id: str = ""`、`turn_num: int = 0`。

## class openjiuwen.agent_evolving.agent_rl.online.judge.judge_server.ScoreResponse

```python
class ScoreResponse(BaseModel)
```

`/score` 响应体 Pydantic 模型。字段：`score: float`、`overall_raw: float`、`votes: list[float]`、`details: Any`、`model: str`、`session_id: str`、`turn_num: int`。

## def openjiuwen.agent_evolving.agent_rl.online.judge.judge_server.create_app

```python
def create_app(config: JudgeConfig) -> FastAPI
```

构建 FastAPI 应用。创建单一 `httpx.AsyncClient(timeout=config.timeout)`，生命周期中关闭。注册路由：

- `GET /healthz` → `{"ok": True, "model": config.model_id, "num_votes": config.num_votes}`
- `POST /score`（response_model=`ScoreResponse`）：校验 `Authorization: Bearer <token>`（缺失/格式错 401，不匹配 403），调用 `evaluate_judge_scores(...)`。

## def openjiuwen.agent_evolving.agent_rl.online.judge.judge_server.parse_args

```python
def parse_args() -> argparse.Namespace
```

CLI 参数解析。参数：`--host`（默认 `127.0.0.1`）、`--port`（必填）、`--llm-url`（必填）、`--model-id`（必填）、`--api-key`（默认 `""`）、`--judge-api-key`（默认 `""`）、`--num-votes`（默认 1）、`--temperature`（默认 0.1）、`--max-completion-tokens`（默认 4096）、`--timeout`（默认 120.0）、`--log-level`（默认 `info`）。

## def openjiuwen.agent_evolving.agent_rl.online.judge.judge_server.main

```python
def main() -> None
```

入口函数。配置日志、构造 `JudgeConfig`、`create_app`、`uvicorn.run(app, host=..., port=..., log_level=...)`。

## 被使用情况

- [bootstrap.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/gateway/app/bootstrap.py)：当 `config.judge_url` 设置时构造 `JudgeScorer` 并注入 `trajectory_runtime.set_judge_scorer(judge_scorer)`。这是 `JudgeScorer` 唯一的生产构造点。
- [persistence.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/gateway/trajectory/persistence.py)：`set_judge_scorer` 将其传入新的 `JudgeDispatcher`。
- [judge_dispatcher.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/gateway/trajectory/judge_dispatcher.py)：`_finalize_sample` 中调用 `await self._judge_scorer.score(...)`（异常回退 `{"score": 0.0, ...}`）。这是 `JudgeScorer.score` 唯一的生产调用点。
- [test_gateway_support.py](file:///Users/dongdong/Desktop/project/agent-core/tests/unit_tests/agent_evolving/agent_rl/online/test_gateway_support.py)：测试 `JudgeScorer._parse_scores` 与 `finish_reason=="length"` 重试路径。
- [test_processor_components.py](file:///Users/dongdong/Desktop/project/agent-core/tests/unit_tests/agent_evolving/agent_rl/online/gateway/test_processor_components.py)：定义 duck-typed `_FakeJudgeScorer` 验证 `.score(...)` 接口。
- 注意：`judge_server.py` 的 `create_app`/`main`/`JudgeConfig`/`ScoreRequest`/`ScoreResponse` **未被任何模块导入**。launcher 以原始 vLLM 进程启动 judge，而非此 FastAPI 服务；该模块为可选独立服务（`python -m judge.judge_server ...`）。
