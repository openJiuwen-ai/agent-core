"""Secret-safe async adapter around openJiuwen's public Model API."""
from __future__ import annotations

import asyncio, hashlib, json, os, time
from dataclasses import dataclass, field
from typing import Any
from openjiuwen.core.foundation.llm import JsonOutputParser, Model, ModelClientConfig, ModelRequestConfig


class BudgetExhausted(RuntimeError): pass


class ApiCallBudget:
    def __init__(self, maximum: int) -> None:
        if maximum < 1: raise ValueError("max_api_calls must be positive")
        self.maximum, self.count, self._lock = maximum, 0, asyncio.Lock()

    async def reserve(self) -> int:
        async with self._lock:
            if self.count >= self.maximum:
                raise BudgetExhausted(f"real API call budget exhausted ({self.count}/{self.maximum})")
            self.count += 1
            return self.count

    @property
    def exhausted(self) -> bool: return self.count >= self.maximum


@dataclass(slots=True)
class InvocationResult:
    content: str = ""; parser_content: Any = None
    usage_metadata: dict[str, Any] | None = None; wall_latency: float = 0.0
    attempts: int = 0; error: str | None = None
    attempt_errors: list[str] = field(default_factory=list)
    generation_adjustments: list[str] = field(default_factory=list)


def usage_to_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None: return None
    if hasattr(usage, "model_dump"): values = usage.model_dump()
    elif isinstance(usage, dict): values = usage
    else:
        names = ("model_name", "total_latency", "input_tokens", "output_tokens",
                 "total_tokens", "cache_tokens", "input_cost", "output_cost", "total_cost")
        values = {name: getattr(usage, name) for name in names if hasattr(usage, name)}
    result = {str(key): value for key, value in values.items() if value is not None}
    costs = ("input_cost", "output_cost", "total_cost")
    if all(not result.get(name) for name in costs):
        for name in costs: result.pop(name, None)
    if not result.get("total_latency"): result.pop("total_latency", None)
    return result


class OpenJiuwenClient:
    def __init__(
        self, models: dict[int, Any], *, provider: str, model_name: str,
        api_base: str = "", verify_ssl: bool = True, offline_mock: bool = False,
        concurrency: int = 3, max_api_calls: int = 650, request_timeout: float = 60.0,
        max_retries: int = 2, backoff_base: float = 0.5, seed: int = 42,
        secret: str = "", generation_settings: dict[str, Any] | None = None,
    ) -> None:
        self.models, self.provider, self.model_name = models, provider, model_name
        self.api_base, self.verify_ssl, self.offline_mock = api_base, verify_ssl, offline_mock
        self.request_timeout, self.max_retries = request_timeout, max(0, min(max_retries, 2))
        self.backoff_base, self.seed, self._secret = backoff_base, seed, secret
        self.budget = ApiCallBudget(max_api_calls)
        self._semaphore, self._parser = asyncio.Semaphore(max(1, concurrency)), JsonOutputParser()
        self._mock_settings = generation_settings or {"temperature": 0.2, "max_tokens": 220}
        self.generation_adjustments: list[str] = []

    @classmethod
    def from_environment(cls, **kwargs: Any) -> OpenJiuwenClient:
        if kwargs.get("offline_mock"):
            return cls({}, provider="mock", model_name="deterministic-mock",
                       generation_settings={"temperature": 0.2, "max_tokens": 220}, **kwargs)
        names = ("MODEL_PROVIDER", "API_BASE", "API_KEY", "MODEL_NAME")
        values = {name: os.environ.get(name, "") for name in names}
        missing = [name for name, value in values.items() if not value.strip()]
        if missing: raise ValueError(f"missing required model environment variable(s): {', '.join(missing)}")
        ssl_text = os.environ.get("LLM_SSL_VERIFY", "true").strip().lower()
        if ssl_text not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
            raise ValueError("LLM_SSL_VERIFY must be a boolean value")
        verify_ssl = ssl_text in {"1", "true", "yes", "on"}
        try:
            client_config = ModelClientConfig(
                client_provider=values["MODEL_PROVIDER"], api_base=values["API_BASE"],
                api_key=values["API_KEY"], verify_ssl=verify_ssl,
                timeout=float(kwargs.get("request_timeout", 60.0)), max_retries=0)
            request_config = ModelRequestConfig(
                model=values["MODEL_NAME"], temperature=0.2, max_tokens=220)
            request_config.top_p = None
            models = {
                agent: Model(model_client_config=client_config.model_copy(deep=True),
                             model_config=request_config.model_copy(deep=True))
                for agent in range(3)
            }
        except Exception as exc:
            safe = str(exc).replace(values["API_KEY"], "[REDACTED]")
            raise RuntimeError(f"openJiuwen model construction failed: {safe}") from None
        return cls(
            models, provider=values["MODEL_PROVIDER"], model_name=values["MODEL_NAME"],
            api_base=values["API_BASE"], verify_ssl=verify_ssl, secret=values["API_KEY"],
            generation_settings={"temperature": 0.2, "max_tokens": 220}, **kwargs)

    def effective_generation_settings(self) -> dict[str, Any]:
        config = next(iter(self.models.values())).model_config if self.models else None
        values = {
            name: getattr(config, name, self._mock_settings.get(name)) if config
            else self._mock_settings.get(name)
            for name in ("temperature", "top_p", "max_tokens", "stop")
        }
        return {**{key: value for key, value in values.items() if value is not None},
                "adjustments": list(self.generation_adjustments)}

    def base_client_identity(self) -> dict[str, Any]:
        return {"provider": self.provider, "api_base": self.api_base,
                "model_name": self.model_name, "verify_ssl": self.verify_ssl}

    def non_secret_fingerprint(self) -> dict[str, Any]:
        return {**self.base_client_identity(),
                "generation_settings": self.effective_generation_settings()}

    def apply_generation_adjustments(self, notes: list[str]) -> None:
        for note in notes:
            parameter = str(note).split(" ", 1)[0]
            if parameter not in {"max_tokens", "temperature", "top_p"}: continue
            for model in self.models.values(): setattr(model.model_config, parameter, None)
            normalized = f"{parameter} removed after provider rejection"
            if normalized not in self.generation_adjustments:
                self.generation_adjustments.append(normalized)

    def _safe_error(self, exc: BaseException) -> str:
        text = f"{type(exc).__name__}: {exc}"
        return text.replace(self._secret, "[REDACTED]") if self._secret else text

    def _delay(self, context: dict[str, Any], attempt: int) -> float:
        safe = json.dumps(context, sort_keys=True, default=str).encode()
        jitter = hashlib.sha256(safe + str(self.seed).encode()).digest()[0] / 2550
        return self.backoff_base * 2 ** (attempt - 1) + jitter

    def _remove_rejected_parameter(self, error: str) -> None:
        if self.offline_mock or not any(x in error for x in ("unsupported", "not support", "invalid")):
            return
        for parameter in ("max_tokens", "temperature", "top_p"):
            if parameter not in error: continue
            self.apply_generation_adjustments([f"{parameter} removed after provider rejection"])
            break

    async def invoke(
        self, agent_id: int, prompt: str, *, stage: str,
        context: dict[str, Any] | None = None, max_attempts: int | None = None,
    ) -> InvocationResult:
        context = {**(context or {}), "agent_id": agent_id, "stage": stage}
        errors, elapsed = [], 0.0
        limit = min(self.max_retries + 1, max_attempts or self.max_retries + 1)
        for attempt in range(1, limit + 1):
            started = time.perf_counter()
            try:
                if self.offline_mock:
                    content = self._mock_content(context)
                    parsed = await self._parser.parse(content)
                    usage = {"input_tokens": len(prompt.split()), "output_tokens": len(content.split()),
                             "total_tokens": len(prompt.split()) + len(content.split()), "synthetic": True}
                else:
                    async with asyncio.timeout(self.request_timeout):
                        async with self._semaphore:
                            await self.budget.reserve()
                            response = await self.models[agent_id].invoke(
                                prompt, output_parser=self._parser, timeout=self.request_timeout)
                    content, parsed = str(response.content or ""), response.parser_content
                    usage = usage_to_dict(response.usage_metadata)
                elapsed += time.perf_counter() - started
                return InvocationResult(content, parsed, usage, elapsed, attempt, None, errors,
                                        list(self.generation_adjustments))
            except BudgetExhausted: raise
            except Exception as exc:
                elapsed += time.perf_counter() - started
                error = self._safe_error(exc); errors.append(error)
                self._remove_rejected_parameter(error.lower())
                if attempt < limit: await asyncio.sleep(self._delay(context, attempt))
        return InvocationResult(
            wall_latency=elapsed, attempts=limit, error=errors[-1] if errors else "unknown failure",
            attempt_errors=errors, generation_adjustments=list(self.generation_adjustments))

    def _mock_content(self, context: dict[str, Any]) -> str:
        payload = json.dumps(context, sort_keys=True, default=str).encode()
        number = int.from_bytes(hashlib.sha256(payload).digest()[:4], "big")
        label, stage = "ABCDE"[(number + int(context["agent_id"])) % 5], str(context["stage"])
        if stage == "initial":
            return (f"After checking the options, final answer: {label.lower()}." if number % 4 == 0
                    else json.dumps({"answer": label, "justification": "Synthetic concise rationale."}))
        if stage == "review":
            current = str(context.get("current_answer", label)).upper()
            if context.get("corrective"):
                return json.dumps({"status": "complete", "feedback": "Format repaired.",
                                   "recommended_answer": current})
            recommended = "ABCDE"[("ABCDE".index(current) + 1) % 5] if number % 5 == 0 else label
            status = "complete" if number % 5 == 0 or number % 2 else "continue"
            return json.dumps({"status": status, "feedback": "Synthetic option-specific feedback.",
                               "recommended_answer": current if status == "complete" and number % 5 else recommended})
        if stage == "revision":
            return (f"Revision complete. Answer: {label}" if number % 3 == 0 else
                    json.dumps({"answer": label, "justification": "Synthetic revised rationale."}))
        raise ValueError(f"unsupported mock stage: {stage}")
