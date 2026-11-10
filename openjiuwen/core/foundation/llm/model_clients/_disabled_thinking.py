# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Scoped retry policy for gateways that reject disabled thinking."""

import inspect
from collections.abc import Awaitable, Callable, Iterable, Mapping
from copy import deepcopy
from typing import Any, Optional

from openjiuwen.core.common.logging import LogEventType, llm_logger
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


class DisabledThinkingFallback:
    """Remember disabled-thinking rejections on one model client."""

    def __init__(self, provider: str, api_label: str) -> None:
        self._provider = provider
        self._api_label = api_label
        self._models_rejecting_disabled_thinking: set[str] = set()

    def apply_cached(self, params: dict[str, Any]) -> dict[str, Any]:
        if not is_disabled_thinking_fallback_allowed():
            return params
        model_key = self._disabled_thinking_model_key(params)
        if model_key not in self._models_rejecting_disabled_thinking:
            return params
        if not self._has_disabled_thinking_params(params):
            return params
        return self._without_disabled_thinking_params(params)

    async def call(
        self,
        create: Callable[..., Awaitable[Any]],
        params: dict[str, Any],
        *,
        is_stream: bool,
    ) -> Any:
        if not is_disabled_thinking_fallback_allowed():
            return await create(**params)

        request_params = self.apply_cached(params)
        try:
            return await create(**request_params)
        except Exception as exc:
            retry_params = self._retry_params_without_disabled_thinking(request_params, exc)
            if retry_params is None:
                raise

            model_key = self._disabled_thinking_model_key(request_params)
            if model_key:
                self._models_rejecting_disabled_thinking.add(model_key)

            llm_logger.warning(
                f"{self._api_label} model rejected disabled thinking; retrying with model defaults.",
                event_type=LogEventType.LLM_CALL_ERROR,
                model_name=model_key,
                model_provider=self._provider,
                is_stream=is_stream,
            )
            return await create(**retry_params)

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
        if isinstance(reasoning, Mapping) and cls._is_false_flag(reasoning.get("enabled")):
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
        if isinstance(reasoning, Mapping) and cls._is_false_flag(reasoning.get("enabled")):
            reasoning_params = dict(reasoning)
            reasoning_params.pop("enabled", None)
            if reasoning_params:
                params["reasoning"] = reasoning_params
            else:
                params.pop("reasoning", None)

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
    def _disabled_thinking_model_key(params: Mapping[str, Any]) -> Optional[str]:
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
        return mentions_disable or "parameter" in error_text or "参数" in error_text

    def _retry_params_without_disabled_thinking(
        self,
        params: dict[str, Any],
        exc: BaseException,
    ) -> Optional[dict[str, Any]]:
        if not self._has_disabled_thinking_params(params):
            return None
        if not self._is_disabled_thinking_rejection(exc):
            return None
        return self._without_disabled_thinking_params(params)
