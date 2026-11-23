# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Scoped retry policy for provider requests that reject specific model options."""

import inspect
import json
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Mapping
from copy import deepcopy
from typing import Any, Optional

from openjiuwen.core.common.logging import LogEventType, llm_logger
from openjiuwen.core.foundation.llm.reasoning import reasoning_request_controls
from openjiuwen.core.foundation.llm.request_context import is_disabled_thinking_fallback_allowed


_DISABLED_THINKING_TYPES = {"disabled"}
_DISABLED_REASONING_EFFORTS = {"off", "none"}
_DISABLED_THINKING_NON_RETRY_STATUSES = {401, 403, 408, 429}
_UNSUPPORTED_DISABLED_THINKING_MARKERS = (
    "unsupported",
    "not support",
    "does not support",
    "doesn't support",
    "cannot disable",
    "can't disable",
    "不支持",
    "不能关闭",
    "不允许关闭",
)
_DISABLED_THINKING_VALUE_MARKERS = (
    "disabled",
    "disable",
    "off",
    "关闭",
)
_UNSUPPORTED_PARAMETER_CODES = {"unknown_parameter", "unrecognized_parameter", "unsupported_parameter"}
_UNSUPPORTED_PARAMETER_MARKERS = (
    "unknown parameter",
    "unrecognized parameter",
    "unsupported",
    "not support",
    "does not support",
    "doesn't support",
    "not allowed",
    "not permitted",
    "不支持",
    "不允许",
    "未知参数",
    "无法识别",
)
_STOP_FIELD = re.compile(r"(?<![a-z0-9_])stop(?![a-z0-9_])")
_MAX_STOP_CACHE_ENTRIES = 128
_MAX_TEMPERATURE_CACHE_ENTRIES = 128
_TEMPERATURE_FIELD = re.compile(r"\btemperature\b")
_UNSUPPORTED_TEMPERATURE = re.compile(
    r"\b(?:unsupported|unknown|unrecognized) (?:parameter|field)[:\s]+['\"`]?temperature\b"
    r"|\btemperature['\"`]?(?: (?:parameter|field))? "
    r"(?:is (?:unsupported|not (?:supported|configurable))|cannot be (?:set|changed|specified)|must be omitted)\b"
    r"(?!\s+(?:to|outside|above|below)\b)"
    r"|\b(?:does not|doesn't|do not) support (?:setting )?(?:the )?(?:custom )?['\"`]?temperature\b"
)
_DEFAULT_TEMPERATURE_ONLY = re.compile(
    r"\bonly (?:the )?default\b"
    r"|\b(?:only (?:be (?:set to )?)?|temperature['\"`]? must be (?:set to )?)['\"`]?1(?:\.0+)?['\"`]?"
    r"(?=\s*(?:is (?:allowed|accepted|supported)\b|(?:for|when|with)\b|[.!)]?$))"
)


class RequestParameterFallback:
    """Remember provider parameter rejections for one client and API.

    Temperature and disabled-thinking recovery only run inside
    ``disabled_thinking_fallback_scope``. With ``retry_without_stop``, a refused
    ``stop`` is also retried without it on every request: some Responses endpoints
    (OpenAI) reject the field while others (e.g. Qwen and DeepSeek via OpenRouter)
    honour it, so it is sent as configured and dropped only after a refusal.
    """

    def __init__(self, provider: str, api_label: str, *, retry_without_stop: bool = False) -> None:
        self._provider = provider
        self._api_label = api_label
        self._retry_without_stop = retry_without_stop
        self._models_rejecting_disabled_thinking: set[str] = set()
        self._models_rejecting_stop: OrderedDict[str, None] = OrderedDict()
        self._rejected_temperatures: OrderedDict[tuple[str, str], None] = OrderedDict()

    def apply_cached(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._retry_without_stop and "stop" in params and self._model_key(params) in self._models_rejecting_stop:
            params = self._without_stop(params)
        if not is_disabled_thinking_fallback_allowed():
            return params
        model_key = self._model_key(params)
        if model_key in self._models_rejecting_disabled_thinking and self._has_disabled_thinking_params(params):
            params = self._without_disabled_thinking_params(params)
        if self._temperature_cache_key(params) in self._rejected_temperatures:
            params = self._without_temperature(params)
        return params

    async def call(
        self,
        create: Callable[..., Awaitable[Any]],
        params: dict[str, Any],
        *,
        is_stream: bool,
    ) -> Any:
        scoped = is_disabled_thinking_fallback_allowed()
        if not scoped and not self._retry_without_stop:
            return await create(**params)

        request_params = self.apply_cached(params)
        model_key = self._model_key(request_params)
        rejected_temperature = None
        rejected_stop = False
        # Each recovery removes the fields it matches, so it can run only once.
        while True:
            try:
                result = await create(**request_params)
            except Exception as exc:
                if self._retry_without_stop and "stop" in request_params and self._rejects_stop(exc):
                    request_params = self._without_stop(request_params)
                    rejected_stop = True
                    rejected_option = "stop"
                elif not scoped:
                    raise exc
                elif self._rejects_temperature(exc):
                    if self._exception_status_code(exc) != 400 or not self._has_temperature(request_params):
                        raise exc
                    rejected_temperature = self._temperature_cache_key(request_params)
                    request_params = self._without_temperature(request_params)
                    rejected_option = "temperature"
                elif self._has_disabled_thinking_params(request_params) and self._is_disabled_thinking_rejection(exc):
                    request_params = self._without_disabled_thinking_params(request_params)
                    if model_key:
                        self._models_rejecting_disabled_thinking.add(model_key)
                    rejected_option = "disabled thinking"
                else:
                    raise exc
                llm_logger.warning(
                    f"{self._api_label} model rejected {rejected_option}; retrying with model defaults.",
                    event_type=LogEventType.LLM_CALL_ERROR,
                    model_name=model_key,
                    model_provider=self._provider,
                    is_stream=is_stream,
                )
            else:
                if rejected_stop and model_key:
                    self._models_rejecting_stop[model_key] = None
                    if len(self._models_rejecting_stop) > _MAX_STOP_CACHE_ENTRIES:
                        self._models_rejecting_stop.popitem(last=False)
                if rejected_temperature is not None:
                    self._rejected_temperatures[rejected_temperature] = None
                    if len(self._rejected_temperatures) > _MAX_TEMPERATURE_CACHE_ENTRIES:
                        self._rejected_temperatures.popitem(last=False)
                return result

    @staticmethod
    def _without_stop(params: Mapping[str, Any]) -> dict[str, Any]:
        cleaned = dict(params)
        cleaned.pop("stop", None)
        return cleaned

    @classmethod
    def _rejects_stop(cls, exc: BaseException) -> bool:
        """Return whether ``exc`` says the endpoint does not accept the ``stop`` field at all.

        A structured error that names ``stop`` in ``param`` counts only with an
        unknown/unsupported-parameter ``code`` or refusal wording, because value and
        type errors set ``param`` too. Otherwise the error text must name ``stop`` and
        use refusal wording. Value errors such as "Invalid type for 'stop'" and errors
        about other fields are raised as before.
        """
        status_code = cls._exception_status_code(exc)
        if status_code is None or not 400 <= status_code < 500 or status_code in _DISABLED_THINKING_NON_RETRY_STATUSES:
            return False

        body = cls._exception_attr(exc, "body")
        error = body.get("error", body) if isinstance(body, Mapping) else None
        details = error if isinstance(error, Mapping) else {}
        field = str(details.get("param") or "").strip().lower()
        if field:
            if field != "stop":
                return False
            code = str(details.get("code") or "").lower()
            message = str(details.get("message") or "").lower()
            return code in _UNSUPPORTED_PARAMETER_CODES or any(
                marker in message for marker in _UNSUPPORTED_PARAMETER_MARKERS
            )

        # The whole error is searched, so errors that a gateway wraps (e.g. OpenRouter's
        # error.metadata.raw) are found too.
        text = cls._exception_text(exc).lower()
        return any(marker in text for marker in _UNSUPPORTED_PARAMETER_MARKERS) and bool(_STOP_FIELD.search(text))

    @staticmethod
    def _has_temperature(params: Mapping[str, Any]) -> bool:
        extra_body = params.get("extra_body")
        return "temperature" in params or (isinstance(extra_body, Mapping) and "temperature" in extra_body)

    @staticmethod
    def _without_temperature(params: Mapping[str, Any]) -> dict[str, Any]:
        cleaned = dict(params)
        cleaned.pop("temperature", None)
        extra_body = cleaned.get("extra_body")
        if isinstance(extra_body, Mapping) and "temperature" in extra_body:
            cleaned_extra = dict(extra_body)
            cleaned_extra.pop("temperature")
            if cleaned_extra:
                cleaned["extra_body"] = cleaned_extra
            else:
                cleaned.pop("extra_body")
        return cleaned

    @classmethod
    def _temperature_cache_key(cls, params: Mapping[str, Any]) -> Optional[tuple[str, str]]:
        if not cls._has_temperature(params):
            return None
        extra_body = params.get("extra_body")
        # SDK extra_body keys take precedence in the outgoing JSON payload.
        wire = dict(params)
        wire.pop("extra_body", None)
        if isinstance(extra_body, Mapping):
            wire.update(extra_body)
        model = cls._model_key(wire)
        if model is None:
            return None
        controls = reasoning_request_controls(wire)
        template_kwargs = wire.get("chat_template_kwargs")
        if isinstance(template_kwargs, Mapping) and "enable_thinking" in template_kwargs:
            controls["chat_template_kwargs.enable_thinking"] = template_kwargs["enable_thinking"]
        try:
            return model, json.dumps([wire["temperature"], controls], sort_keys=True)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_disabled_thinking_type(value: Any) -> bool:
        return isinstance(value, str) and value.strip().lower() in _DISABLED_THINKING_TYPES

    @staticmethod
    def _is_false_flag(value: Any) -> bool:
        if isinstance(value, bool):
            return value is False
        if isinstance(value, str):
            return value.strip().lower() in {"false", "0", "no"}
        return False

    @staticmethod
    def _is_disabled_reasoning_effort(value: Any) -> bool:
        return isinstance(value, str) and value.strip().lower() in _DISABLED_REASONING_EFFORTS

    @classmethod
    def _mapping_has_disabled_thinking_params(cls, value: Any) -> bool:
        if not isinstance(value, Mapping):
            return False

        thinking = value.get("thinking")
        if isinstance(thinking, Mapping) and cls._is_disabled_thinking_type(thinking.get("type")):
            return True
        if cls._is_false_flag(value.get("enable_thinking")):
            return True

        chat_template_kwargs = value.get("chat_template_kwargs")
        if (
            isinstance(chat_template_kwargs, Mapping)
            and cls._is_false_flag(chat_template_kwargs.get("enable_thinking"))
        ):
            return True

        reasoning = value.get("reasoning")
        if isinstance(reasoning, Mapping) and (
            cls._is_false_flag(reasoning.get("enabled"))
            or cls._is_disabled_reasoning_effort(reasoning.get("effort"))
        ):
            return True

        return cls._is_disabled_reasoning_effort(value.get("reasoning_effort"))

    @classmethod
    def _has_disabled_thinking_params(cls, params: Mapping[str, Any]) -> bool:
        if cls._mapping_has_disabled_thinking_params(params):
            return True
        return cls._mapping_has_disabled_thinking_params(params.get("extra_body"))

    @classmethod
    def _strip_disabled_thinking_fields(cls, params: dict[str, Any]) -> None:
        thinking = params.get("thinking")
        if isinstance(thinking, Mapping) and cls._is_disabled_thinking_type(thinking.get("type")):
            thinking_params = dict(thinking)
            thinking_params.pop("type", None)
            if thinking_params:
                params["thinking"] = thinking_params
            else:
                params.pop("thinking", None)

        if cls._is_false_flag(params.get("enable_thinking")):
            params.pop("enable_thinking", None)

        chat_template_kwargs = params.get("chat_template_kwargs")
        if (
            isinstance(chat_template_kwargs, Mapping)
            and cls._is_false_flag(chat_template_kwargs.get("enable_thinking"))
        ):
            chat_template_params = dict(chat_template_kwargs)
            chat_template_params.pop("enable_thinking", None)
            if chat_template_params:
                params["chat_template_kwargs"] = chat_template_params
            else:
                params.pop("chat_template_kwargs", None)

        reasoning = params.get("reasoning")
        if isinstance(reasoning, Mapping):
            reasoning_params = dict(reasoning)
            if cls._is_false_flag(reasoning.get("enabled")):
                reasoning_params.pop("enabled")
            if cls._is_disabled_reasoning_effort(reasoning.get("effort")):
                reasoning_params.pop("effort")
            if reasoning_params != reasoning:
                if reasoning_params:
                    params["reasoning"] = reasoning_params
                else:
                    params.pop("reasoning")

        if cls._is_disabled_reasoning_effort(params.get("reasoning_effort")):
            params.pop("reasoning_effort", None)

    @classmethod
    def _without_disabled_thinking_params(cls, params: Mapping[str, Any]) -> dict[str, Any]:
        cleaned = deepcopy(dict(params))
        cls._strip_disabled_thinking_fields(cleaned)

        extra_body = cleaned.get("extra_body")
        if isinstance(extra_body, Mapping):
            cleaned_extra_body = dict(extra_body)
            cls._strip_disabled_thinking_fields(cleaned_extra_body)
            if cleaned_extra_body:
                cleaned["extra_body"] = cleaned_extra_body
            else:
                cleaned.pop("extra_body", None)
        return cleaned

    @staticmethod
    def _model_key(params: Mapping[str, Any]) -> Optional[str]:
        model_name = params.get("model")
        if model_name is None:
            return None
        model_key = str(model_name).strip()
        return model_key or None

    @classmethod
    def _iter_exception_chain(cls, exc: BaseException) -> Iterable[BaseException]:
        seen: set[int] = set()
        current: Optional[BaseException] = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            yield current
            current = current.__cause__ or current.__context__

    @staticmethod
    def _optional_int(value: Any) -> Optional[int]:
        if isinstance(value, bool) or value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _exception_attr(value: Any, attr: str) -> Any:
        instance_dict = getattr(value, "__dict__", None)
        if isinstance(instance_dict, Mapping) and attr in instance_dict:
            return instance_dict[attr]
        try:
            return inspect.getattr_static(value, attr)
        except AttributeError:
            return None

    @classmethod
    def _exception_status_code(cls, exc: BaseException) -> Optional[int]:
        for item in cls._iter_exception_chain(exc):
            candidates = (item, cls._exception_attr(item, "response"))
            for candidate in candidates:
                if candidate is None:
                    continue
                for attr in ("status_code", "status"):
                    status_code = cls._optional_int(cls._exception_attr(candidate, attr))
                    if status_code is not None:
                        return status_code
        return None

    @classmethod
    def _collect_exception_text_parts(cls, value: Any, seen: set[int]) -> list[str]:
        if value is None or id(value) in seen:
            return []
        seen.add(id(value))

        if isinstance(value, (str, int, float, bool)):
            return [str(value)]
        if isinstance(value, Mapping):
            parts: list[str] = []
            for key, item in value.items():
                parts.extend(cls._collect_exception_text_parts(key, seen))
                parts.extend(cls._collect_exception_text_parts(item, seen))
            return parts
        if isinstance(value, (list, tuple, set, frozenset)):
            parts = []
            for item in value:
                parts.extend(cls._collect_exception_text_parts(item, seen))
            return parts

        parts = [str(value)]
        for attr in ("message", "body", "code", "type", "param", "status_code", "response"):
            attr_value = cls._exception_attr(value, attr)
            if attr_value is not value:
                parts.extend(cls._collect_exception_text_parts(attr_value, seen))
        return parts

    @classmethod
    def _exception_text(cls, exc: BaseException) -> str:
        parts: list[str] = []
        seen: set[int] = set()
        for item in cls._iter_exception_chain(exc):
            parts.extend(cls._collect_exception_text_parts(item, seen))
        return "\n".join(part for part in parts if part)

    @classmethod
    def _rejects_temperature(cls, exc: BaseException) -> bool:
        body = cls._exception_attr(exc, "body")
        if isinstance(body, Mapping):
            body = body.get("error", body)
        details = body if isinstance(body, Mapping) else {}
        param = details.get("param") or cls._exception_attr(exc, "param")
        field = str(param or "").strip().lower()
        if field and field != "temperature":
            return False

        code = details.get("code") or cls._exception_attr(exc, "code")
        if field == "temperature" and str(code).lower() in _UNSUPPORTED_PARAMETER_CODES:
            return True

        message = str(details.get("message") or cls._exception_attr(exc, "message") or exc).lower()
        return bool(
            _UNSUPPORTED_TEMPERATURE.search(message)
            or ((field == "temperature" or _TEMPERATURE_FIELD.search(message))
                and _DEFAULT_TEMPERATURE_ONLY.search(message))
        )

    @classmethod
    def _is_disabled_thinking_rejection(cls, exc: BaseException) -> bool:
        status_code = cls._exception_status_code(exc)
        if (
            status_code in _DISABLED_THINKING_NON_RETRY_STATUSES
            or (status_code is not None and status_code >= 500)
        ):
            return False

        error_text = cls._exception_text(exc).lower()
        if "1210" in error_text:
            return True

        mentions_thinking = "thinking" in error_text or "reasoning" in error_text or "思考" in error_text
        if not mentions_thinking:
            return False

        mentions_unsupported = any(marker in error_text for marker in _UNSUPPORTED_DISABLED_THINKING_MARKERS)
        if not mentions_unsupported:
            return False

        mentions_disable = any(marker in error_text for marker in _DISABLED_THINKING_VALUE_MARKERS)
        if re.search(r"\breasoning\.effort\b[^\n]*['\"`]none['\"`]", error_text):
            return True
        return mentions_disable or "parameter" in error_text or "参数" in error_text
