# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional, Union

import httpx

from openjiuwen.extensions.external_provider.openai_auth.openai_account_auth import (
    OPENAI_ACCOUNT_PROVIDER,
    DEFAULT_OPENAI_ACCOUNT_BASE_URL,
    default_openai_account_auth_path,
)


OPENAI_ACCOUNT_MODELS_CLIENT_VERSION = "1.0.0"
OPENAI_ACCOUNT_MODELS_CACHE_FILENAME = "openai_account_models_cache.json"

DEFAULT_OPENAI_ACCOUNT_MODELS: list[str] = [
    "gpt-5.5",
    "gpt-5.4-mini",
    "gpt-5.4",
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
    "gpt-5-codex",
]

_FORWARD_COMPAT_TEMPLATE_MODELS: list[tuple[str, tuple[str, ...]]] = [
    ("gpt-5.5", ("gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex", "gpt-5-codex")),
    ("gpt-5.4-mini", ("gpt-5.3-codex", "gpt-5-codex")),
    ("gpt-5.4", ("gpt-5.3-codex", "gpt-5-codex")),
    ("gpt-5.3-codex-spark", ("gpt-5.3-codex",)),
]

_HIDDEN_VISIBILITIES = {"hide", "hidden"}


class OpenAIAccountModelListError(Exception):
    """Raised when OpenAI account model discovery cannot use the live endpoint."""

    def __init__(self, message: str, *, status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def __str__(self) -> str:
        return self.message


class OpenAIAccountModelCatalog:
    """Fetch and cache the OpenAI account backend model list."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_OPENAI_ACCOUNT_BASE_URL,
        cache_path: Optional[str | Path] = None,
        timeout_seconds: float = 10.0,
        client_version: str = OPENAI_ACCOUNT_MODELS_CLIENT_VERSION,
        transport: Optional[httpx.BaseTransport] = None,
        verify: Union[bool, str, Any] = True,
        proxy: Optional[str] = None,
        max_retries: int = 0,
        now: Optional[Callable[[], float]] = None,
    ):
        self.base_url = (base_url or DEFAULT_OPENAI_ACCOUNT_BASE_URL).rstrip("/")
        self.cache_path = Path(cache_path).expanduser() if cache_path else default_openai_account_models_cache_path()
        self.timeout_seconds = timeout_seconds
        self.client_version = client_version
        self._transport = transport
        self._verify = verify
        self._proxy = proxy
        self._max_retries = max(0, int(max_retries or 0))
        self._now = now or time.time

    def list_model_ids(
        self,
        *,
        auth_manager: Any = None,
        access_token: Optional[str] = None,
        force_refresh: bool = False,
    ) -> list[str]:
        """Return live OpenAI account models, falling back to cache and then built-ins.

        This catalog uses a synchronous httpx client. Async callers should
        wrap model discovery with ``asyncio.to_thread`` if event-loop
        blocking matters.
        """
        token = access_token
        if token is None and auth_manager is not None:
            try:
                token = auth_manager.resolve_access_token(force_refresh=force_refresh)
            except Exception:
                token = None

        if token:
            try:
                payload, model_ids = self.fetch_models(access_token=token)
                self.write_cache(payload=payload, model_ids=model_ids)
                return model_ids
            except OpenAIAccountModelListError:
                pass

        cached = self.read_cache_model_ids()
        if cached:
            return cached
        return _add_forward_compat_models(DEFAULT_OPENAI_ACCOUNT_MODELS)

    def fetch_models(self, *, access_token: str) -> tuple[dict[str, Any], list[str]]:
        if not access_token:
            raise OpenAIAccountModelListError("OpenAI account model discovery requires an access token.")

        with self._client() as client:
            response = client.get(
                self._models_url(),
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
        if response.status_code != 200:
            raise OpenAIAccountModelListError(
                f"OpenAI account model discovery failed with status {response.status_code}.",
                status_code=response.status_code,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise OpenAIAccountModelListError("OpenAI account model discovery returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise OpenAIAccountModelListError("OpenAI account model discovery returned an unsupported payload.")

        model_ids = parse_openai_account_model_ids(payload)
        if not model_ids:
            raise OpenAIAccountModelListError("OpenAI account model discovery returned no visible models.")
        return payload, model_ids

    def read_cache_model_ids(self) -> list[str]:
        if not self.cache_path.is_file():
            return []
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(payload, dict):
            return []

        cached_ids = _model_ids_from_sequence(payload.get("model_ids"))
        if cached_ids:
            return _add_forward_compat_models(cached_ids)

        cached_payload = payload.get("payload")
        if isinstance(cached_payload, dict):
            return parse_openai_account_model_ids(cached_payload)
        return parse_openai_account_model_ids(payload)

    def write_cache(self, *, payload: dict[str, Any], model_ids: list[str]) -> None:
        if not model_ids:
            return
        data = {
            "provider": OPENAI_ACCOUNT_PROVIDER,
            "base_url": self.base_url,
            "client_version": self.client_version,
            "fetched_at": self._now(),
            "model_ids": _add_forward_compat_models(model_ids),
            "payload": payload,
        }
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.cache_path.with_name(f"{self.cache_path.name}.tmp")
            tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp_path, self.cache_path)
        except OSError:
            return

    def _models_url(self) -> str:
        return str(httpx.URL(f"{self.base_url}/models").copy_add_param("client_version", self.client_version))

    def _client(self) -> httpx.Client:
        kwargs: dict[str, Any] = {"timeout": httpx.Timeout(max(5.0, float(self.timeout_seconds)))}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        else:
            kwargs["transport"] = httpx.HTTPTransport(
                verify=self._verify,
                proxy=self._proxy,
                retries=self._max_retries,
            )
        return httpx.Client(**kwargs)


def default_openai_account_models_cache_path() -> Path:
    cache_file = os.getenv("OPENJIUWEN_OPENAI_ACCOUNT_MODELS_CACHE_FILE")
    if cache_file:
        return Path(cache_file).expanduser()
    return default_openai_account_auth_path().parent / OPENAI_ACCOUNT_MODELS_CACHE_FILENAME


def parse_openai_account_model_ids(payload: dict[str, Any]) -> list[str]:
    """Extract visible OpenAI account model IDs without using supported_in_api."""
    entries = _model_entries(payload)
    sortable: list[tuple[int, str]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        visibility = item.get("visibility")
        if isinstance(visibility, str) and visibility.strip().lower() in _HIDDEN_VISIBILITIES:
            continue
        model_id = _model_id(item)
        if not model_id:
            continue
        sortable.append((_priority(item), model_id))

    sortable.sort(key=lambda item: (item[0], item[1]))
    ordered: list[str] = []
    seen: set[str] = set()
    for _, model_id in sortable:
        if model_id in seen:
            continue
        ordered.append(model_id)
        seen.add(model_id)
    return _add_forward_compat_models(ordered)


def _model_entries(payload: dict[str, Any]) -> list[Any]:
    models = payload.get("models")
    if isinstance(models, list):
        return models
    if isinstance(models, dict):
        return [_dict_model_entry(model_id, metadata) for model_id, metadata in models.items()]

    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [_dict_model_entry(model_id, metadata) for model_id, metadata in data.items()]
    return []


def _dict_model_entry(model_id: Any, metadata: Any) -> dict[str, Any]:
    entry = dict(metadata) if isinstance(metadata, dict) else {}
    entry.setdefault("slug", model_id)
    return entry


def _model_id(item: dict[str, Any]) -> str:
    for key in ("slug", "id", "name", "model"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _priority(item: dict[str, Any]) -> int:
    value = item.get("priority")
    if value is None or isinstance(value, bool):
        return 10_000
    try:
        return int(value)
    except (TypeError, ValueError):
        return 10_000


def _model_ids_from_sequence(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    model_ids: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        model_id = item.strip()
        if not model_id or model_id in seen:
            continue
        model_ids.append(model_id)
        seen.add(model_id)
    return model_ids


def _add_forward_compat_models(model_ids: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for model_id in model_ids:
        if model_id not in seen:
            ordered.append(model_id)
            seen.add(model_id)

    for synthetic_model, template_models in _FORWARD_COMPAT_TEMPLATE_MODELS:
        if synthetic_model in seen:
            continue
        if any(template in seen for template in template_models):
            ordered.append(synthetic_model)
            seen.add(synthetic_model)
    return ordered
