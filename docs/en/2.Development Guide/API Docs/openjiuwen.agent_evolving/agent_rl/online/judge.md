# openjiuwen.agent_evolving.agent_rl.online.judge

LLM-as-a-Judge reward scorer. Scores agent turns along four dimensions (task completion, response quality, tool usage, coherence, each 0-10), normalizes the aggregate to a `[0, 1]` reward, and supports multi-vote averaging with retry logic. It is the reward source consumed by the RL Service capture pipeline (the "delayed judge" flow).

The package is layered in three tiers: `scoring.py` (pure prompt/parse/normalize, no I/O) → `evaluator.py` (async HTTP voting engine) → `judge_scorer.py` (high-level client adapter); `judge_server.py` is an optional standalone FastAPI service.

## Constant JUDGE_PROMPT_TEMPLATE

```python
JUDGE_PROMPT_TEMPLATE: str
```

A Chinese scoring prompt template instructing the model to act as a professional AI Agent quality evaluator, outputting strict JSON (keys: `task_completion`, `response_quality`, `tool_usage`, `coherence`, `overall`, each 0-10, plus `reason`). It has three placeholders: `{instruction_text}`, `{response_text}`, `{followup_user_feedback}`.

## def openjiuwen.agent_evolving.agent_rl.online.judge.scoring.build_judge_prompt

```python
def build_judge_prompt(*, instruction_text: str = "", response_text: str = "", followup_user_feedback: str = "") -> str
```

Formats `JUDGE_PROMPT_TEMPLATE`, substituting empty fields with Chinese placeholders (`"(无)"`, `"(无回复)"`, `"(无反馈)"`).

**Parameters** (all keyword-only):

* **instruction_text**(str): Instruction text. Default: `""`.
* **response_text**(str): Response text. Default: `""`.
* **followup_user_feedback**(str): Follow-up user feedback. Default: `""`.

**Returns**:

`str`, the formatted prompt.

## def openjiuwen.agent_evolving.agent_rl.online.judge.scoring.parse_judge_scores

```python
def parse_judge_scores(content: str, *, raise_on_error: bool = True) -> Optional[dict[str, Any]]
```

Robustly extracts the judge's JSON score dict from a model response. First tries the whole stripped content via `json.loads`, then any ```` ```json ````/```` ``` ```` fenced blocks (regex `r"```(?:json)?\s*(.*?)```"`); for each candidate tries `json.loads`, falling back to `_extract_first_json_object` (scans for `{` and uses `JSONDecoder.raw_decode`). On success calls `_ensure_overall` to fill a missing `overall`.

**Parameters**:

* **content**(str): Model response text.
* **raise_on_error**(bool): Whether to raise `ValueError` on parse failure. When `False`, returns `None`. Default: `True`.

**Returns**:

`Optional[dict[str, Any]]`, the scores dict; `None` when `raise_on_error=False` and unparseable.

## def openjiuwen.agent_evolving.agent_rl.online.judge.scoring.normalize_overall_score

```python
def normalize_overall_score(overall: float) -> float
```

Maps a raw 0-10 score to `[0, 1]` via `overall / 10.0`.

**Parameters**:

* **overall**(float): Raw 0-10 score.

**Returns**:

`float`, the normalized `[0, 1]` reward.

## class openjiuwen.agent_evolving.agent_rl.online.judge.evaluator.JudgeEvaluatorConfig

```python
@dataclass
class JudgeEvaluatorConfig(llm_url: str, model_id: str, api_key: str = "", num_votes: int = 1, temperature: float = 0.1, max_completion_tokens: int = 4096, max_retries: int = 0, retry_backoff_sec: float = 0.0)
```

Plain dataclass holding judge LLM endpoint and sampling config. `llm_url` and `model_id` are required; the rest have defaults.

**Fields**:

* **llm_url**(str): LLM service URL.
* **model_id**(str): Model ID.
* **api_key**(str): Bearer API key. Default: `""`.
* **num_votes**(int): Number of votes. Default: `1`.
* **temperature**(float): Sampling temperature. Default: `0.1`.
* **max_completion_tokens**(int): Max completion tokens. Default: `4096`.
* **max_retries**(int): Max retries. Default: `0`.
* **retry_backoff_sec**(float): Retry backoff base (seconds). Default: `0.0`.

## async def openjiuwen.agent_evolving.agent_rl.online.judge.evaluator.evaluate_judge_scores

```python
async def evaluate_judge_scores(*, client: httpx.AsyncClient, config: JudgeEvaluatorConfig, response_text: str, instruction_text: str, followup_user_feedback: str = "", session_id: str = "", turn_num: int = 0, logger: Optional[logging.Logger] = None) -> dict[str, Any]
```

Core scoring entry. Builds messages, runs `config.num_votes` parallel `_query_vote` calls via `asyncio.gather`, averages the `overall` values (default `5.0` on empty), normalizes via `normalize_overall_score`, logs `[Judge] session=%s turn=%d votes=%s -> overall=%.2f score=%.3f`.

**Parameters** (all keyword-only):

* **client**(httpx.AsyncClient): Async HTTP client.
* **config**(JudgeEvaluatorConfig): Evaluator config.
* **response_text**(str): Response text.
* **instruction_text**(str): Instruction text.
* **followup_user_feedback**(str): Follow-up user feedback. Default: `""`.
* **session_id**(str): Session ID. Default: `""`.
* **turn_num**(int): Turn number. Default: `0`.
* **logger**(Optional[logging.Logger], optional): Logger. Default: `None`.

**Returns**:

`dict[str, Any]`, structured as:

```python
{
    "score": float,           # normalized [0, 1]
    "overall_raw": float,      # averaged raw 0-10 score
    "votes": list[float],      # per-vote overall values
    "details": dict | list,    # single vote dict if num_votes==1 else list
    "model": str,              # config.model_id
    "session_id": str,
    "turn_num": int,
}
```

## def openjiuwen.agent_evolving.agent_rl.online.judge.evaluator.build_judge_messages

```python
def build_judge_messages(*, response_text: str, instruction_text: str, followup_user_feedback: str) -> list[dict[str, str]]
```

Sanitizes each input via `_sanitize_text` (with Chinese placeholder fallback), builds the prompt via `build_judge_prompt`, and wraps it as `[{"role": "user", "content": prompt}]`.

**Parameters** (all keyword-only):

* **response_text**(str): Response text.
* **instruction_text**(str): Instruction text.
* **followup_user_feedback**(str): Follow-up user feedback.

**Returns**:

`list[dict[str, str]]`, a single user message.

## class openjiuwen.agent_evolving.agent_rl.online.judge.judge_scorer.JudgeScorer

```python
class JudgeScorer(*, judge_url: str, judge_model: str, api_key: str = "EMPTY", timeout: float = 60.0, num_votes: int = 1, max_retries: int = 2, retry_backoff_sec: float = 0.2, http_client: Optional[httpx.AsyncClient] = None)
```

High-level async client. Owns or accepts a shared `httpx.AsyncClient`, and builds an internal `JudgeEvaluatorConfig` (hardcodes `temperature=0.1`, `max_completion_tokens=4096`).

**Parameters** (all keyword-only):

* **judge_url**(str): Judge LLM service URL.
* **judge_model**(str): Judge model name.
* **api_key**(str): Bearer API key. Default: `"EMPTY"`.
* **timeout**(float): HTTP timeout in seconds. Default: `60.0`.
* **num_votes**(int): Number of votes. Default: `1`.
* **max_retries**(int): Max retries. Default: `2`.
* **retry_backoff_sec**(float): Retry backoff base in seconds. Default: `0.2`.
* **http_client**(Optional[httpx.AsyncClient], optional): Externally injected HTTP client. Default: `None`.

### async def close

```python
async def close() -> None
```

Closes the HTTP client only if it was internally created.

### async def score

```python
async def score(*, response_text: str, instruction_text: str, followup_user_feedback: str, session_id: str = "", turn_num: int = 0) -> dict[str, Any]
```

Delegates to `evaluate_judge_scores(..., logger=logger)`, then pops the `model`, `session_id`, and `turn_num` keys from the result before returning (the returned dict contains only `score`, `overall_raw`, `votes`, `details`).

**Parameters** (all keyword-only):

* **response_text**(str): Response text.
* **instruction_text**(str): Instruction text.
* **followup_user_feedback**(str): Follow-up user feedback.
* **session_id**(str): Session ID. Default: `""`.
* **turn_num**(int): Turn number. Default: `0`.

**Returns**:

`dict[str, Any]`, the scoring result (metadata stripped).

### @staticmethod def _parse_scores (private)

```python
@staticmethod
def _parse_scores(content: str) -> dict[str, Any]
```

Thin wrapper around `parse_judge_scores(content)` (default `raise_on_error=True`).

## class openjiuwen.agent_evolving.agent_rl.online.judge.judge_server.JudgeConfig

```python
@dataclass
class JudgeConfig(JudgeEvaluatorConfig)
```

Extends `JudgeEvaluatorConfig` with two server-specific fields:

* **timeout**(float): HTTP client timeout. Default: `120.0`.
* **expected_api_key**(str): API key for authenticating `/score` requests. Default: `""`.

## class openjiuwen.agent_evolving.agent_rl.online.judge.judge_server.ScoreRequest

```python
class ScoreRequest(BaseModel)
```

`/score` request body Pydantic model. Fields: `response_text: str = ""`, `instruction_text: str = ""`, `followup_user_feedback: str = ""`, `session_id: str = ""`, `turn_num: int = 0`.

## class openjiuwen.agent_evolving.agent_rl.online.judge.judge_server.ScoreResponse

```python
class ScoreResponse(BaseModel)
```

`/score` response body Pydantic model. Fields: `score: float`, `overall_raw: float`, `votes: list[float]`, `details: Any`, `model: str`, `session_id: str`, `turn_num: int`.

## def openjiuwen.agent_evolving.agent_rl.online.judge.judge_server.create_app

```python
def create_app(config: JudgeConfig) -> FastAPI
```

Builds a FastAPI app. Creates a single `httpx.AsyncClient(timeout=config.timeout)`, closed on lifespan shutdown. Registers routes:

- `GET /healthz` → `{"ok": True, "model": config.model_id, "num_votes": config.num_votes}`
- `POST /score` (response_model=`ScoreResponse`): validates `Authorization: Bearer <token>` (401 if missing/malformed, 403 if mismatch), then calls `evaluate_judge_scores(...)`.

## def openjiuwen.agent_evolving.agent_rl.online.judge.judge_server.parse_args

```python
def parse_args() -> argparse.Namespace
```

CLI argument parser. Args: `--host` (default `127.0.0.1`), `--port` (required), `--llm-url` (required), `--model-id` (required), `--api-key` (default `""`), `--judge-api-key` (default `""`), `--num-votes` (default 1), `--temperature` (default 0.1), `--max-completion-tokens` (default 4096), `--timeout` (default 120.0), `--log-level` (default `info`).

## def openjiuwen.agent_evolving.agent_rl.online.judge.judge_server.main

```python
def main() -> None
```

Entry point. Configures logging, constructs `JudgeConfig`, `create_app`, `uvicorn.run(app, host=..., port=..., log_level=...)`.

## Usage

- `service.py` constructs `JudgeScorer` from the RL Service configuration and injects it into `CapturePipeline`.
- `capture_pipeline.py` calls `JudgeScorer.score(...)` while atomically publishing a completed delayed-feedback turn.
- `test_gateway_support.py` covers score parsing and the `finish_reason=="length"` retry path.
- `test_capture_pipeline.py` covers the scorer interface, atomic publication, and Judge failure handling.
