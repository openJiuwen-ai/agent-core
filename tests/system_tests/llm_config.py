# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Loader for ``config_llm_local.yaml`` — the system tests' model endpoints.

System tests need real endpoints, and every E2E used to grow its own way of
finding them: one file per endpoint, model names hard-coded in scripts, and
provider names inferred from a URL. This module makes the YAML the single
source of truth and gives every E2E one way to read it.

Schema::

    endpoints:
      - name: deepseek            # logical id, referenced by E2Es
        provider: DeepSeek        # openjiuwen ProviderType value
        api_base: https://api.deepseek.com
        api_key: sk-...
        models: [deepseek-v4-flash]

The list is ordered: the first entry is the default (``config.default``),
which is what the single-model E2Es run on. One entry may serve many models
(an OpenRouter-style gateway); several entries may repeat one model name
under different keys (failover peers). Both shapes are load-bearing —
``IntelliRouterConfig`` needs the first to offer a choice of models and the
second to have something to fail over to.

Model refs
----------
Because an endpoint may serve many models, naming one takes both halves.
``LlmConfig.resolve`` turns a ref into a ``ModelRef`` coordinate::

    cfg.resolve()                   # default: first model of first endpoint
    cfg.resolve("gateway/GLM-5.1")  # exactly that pair
    cfg.resolve("gateway")          # that endpoint's first model
    cfg.resolve("GLM-5.1")          # first endpoint serving it

A single-model E2E resolves ``$OPENJIUWEN_E2E_MODEL`` by default, so a run can
be re-pointed without editing YAML::

    OPENJIUWEN_E2E_MODEL=gateway/GLM-5.1 python ..._e2e.py

``api_base`` convention
-----------------------
``api_base`` is written the way **openjiuwen** wants it: it is handed
straight to ``ModelClientConfig.api_base``, which becomes the OpenAI SDK's
``base_url`` and has ``/chat/completions`` appended to it. So an
OpenAI-compatible gateway must include the ``/v1`` suffix here.

IntelliRouter's provider adapters build ``f"{api_base}/v1/chat/completions"``
themselves, so they need the bare root instead. ``ModelEndpoint.router_api_base``
does that conversion; never hand ``api_base`` to a deployment directly, or
every request 404s (and reports ``ResponseNotRead`` rather than the 404).

Deepseek accepts both spellings, which is why the two conventions coexisted
unnoticed until an IntelliRouter E2E pointed at a gateway.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config_llm_local.yaml"
EXAMPLE_CONFIG_PATH = Path(__file__).resolve().parent / "config_llm_local.example.yaml"

_ENV_CONFIG_PATH = "OPENJIUWEN_E2E_LLM_CONFIG"
"""Env var pointing at an alternative config file (absolute or relative)."""

ENV_MODEL_REF = "OPENJIUWEN_E2E_MODEL"
"""Env var holding a model ref, so a run can be re-pointed without editing YAML.

    OPENJIUWEN_E2E_MODEL=gateway/GLM-5.1 python ..._e2e.py

See ``LlmConfig.resolve`` for the accepted spellings.
"""


@dataclass(frozen=True, slots=True)
class ModelEndpoint:
    """One API endpoint plus the credentials and model names it serves."""

    name: str
    """Short identifier, referenced by E2Es and typed into refs. No ``/``:
    that character separates the halves of ``<endpoint>/<model>``, and only
    the first occurrence is the separator — the model half keeps its own
    slashes. ``load_llm_config`` rejects a name containing one."""
    provider: str
    api_base: str
    api_key: str
    models: tuple[str, ...]

    @property
    def model(self) -> str:
        """Primary model — the first declared name."""
        return self.models[0]

    @property
    def router_api_base(self) -> str:
        """``api_base`` as IntelliRouter wants it: the root, without ``/v1``.

        See the module docstring — IntelliRouter's adapters append their own
        ``/v1/chat/completions``.
        """
        return self.api_base.rstrip("/").removesuffix("/v1")

    @property
    def router_provider(self) -> str:
        """Provider name as IntelliRouter's registry spells it.

        openjiuwen's ``ProviderType`` uses CamelCase (``DeepSeek``,
        ``OpenAI``, ``SiliconFlow``); IntelliRouter registers lowercase
        (``deepseek``, ``openai``, ``siliconflow``). Lowercasing bridges every
        provider both layers currently share. An unknown name raises inside
        IntelliRouter with the supported list, so a future mismatch surfaces
        loudly rather than silently routing wrong.
        """
        return self.provider.lower()

    def serves(self, model: str) -> bool:
        """Whether this endpoint declares ``model``."""
        return model in self.models

    def router_deployment(self, model: str, *, id: str | None = None, **extra: Any) -> dict:
        """Render an ``IntelliRouterDeployment`` kwargs dict for one model.

        Args:
            model: Model name; must be one this endpoint declares.
            id: Deployment id. Defaults to ``<endpoint name>-<model>``.
            **extra: Extra deployment fields (``rpm``, ``tpm``, ``tags``, ...).

        Raises:
            ValueError: when ``model`` is not served by this endpoint —
                catching the config drift here beats a 404 at request time.
        """
        if not self.serves(model):
            raise ValueError(
                f"endpoint {self.name!r} does not serve model {model!r}; declared: {list(self.models)}",
            )
        return {
            "id": id or f"{self.name}-{model}",
            "model_name": model,
            "api_key": self.api_key,
            "api_base": self.router_api_base,
            "provider": self.router_provider,
            **extra,
        }


@dataclass(frozen=True, slots=True)
class ModelRef:
    """One model at one endpoint — the coordinate a single-model run needs.

    An endpoint alone is not enough to name a model: a gateway serves a whole
    catalogue, and ``endpoint.model`` only ever yields the first of them. This
    pairs the two, so "the GLM-5.1 on the gateway" is expressible without
    reordering the config.

    Credentials are proxied off the endpoint, so a caller that just needs to
    reach the model never has to reach through to it.
    """

    endpoint: ModelEndpoint
    model: str

    @property
    def ref(self) -> str:
        """Canonical ``<endpoint>/<model>`` spelling of this coordinate."""
        return f"{self.endpoint.name}/{self.model}"

    @property
    def provider(self) -> str:
        """openjiuwen ``ProviderType`` value of the hosting endpoint."""
        return self.endpoint.provider

    @property
    def api_base(self) -> str:
        """openjiuwen-style base URL (may end in ``/v1``)."""
        return self.endpoint.api_base

    @property
    def api_key(self) -> str:
        """Credential of the hosting endpoint."""
        return self.endpoint.api_key

    @property
    def router_api_base(self) -> str:
        """IntelliRouter-style base URL (no ``/v1``)."""
        return self.endpoint.router_api_base

    @property
    def router_provider(self) -> str:
        """IntelliRouter-style provider name (lowercase)."""
        return self.endpoint.router_provider

    def router_deployment(self, **extra: Any) -> dict:
        """Render this exact coordinate as an IntelliRouter deployment dict."""
        return self.endpoint.router_deployment(self.model, **extra)


@dataclass(frozen=True, slots=True)
class LlmConfig:
    """Every endpoint declared in ``config_llm_local.yaml``, in order."""

    endpoints: tuple[ModelEndpoint, ...]

    @property
    def default(self) -> ModelRef:
        """First model of the first endpoint — what single-model E2Es run on."""
        first = self.endpoints[0]
        return ModelRef(endpoint=first, model=first.model)

    def resolve(self, ref: str | None = None) -> ModelRef:
        """Resolve a model ref into an ``(endpoint, model)`` coordinate.

        Accepted spellings:

        * ``None`` / empty → ``self.default``.
        * ``"<endpoint>"`` → that endpoint's first model.
        * ``"<model>"`` → the first endpoint serving it.
        * ``"<endpoint>/<model>"`` → exactly that pair, and the only spelling
          that can pick between endpoints serving the same model.

        Resolution tries the whole string as an endpoint name, then as a model
        name, and only then splits on the first ``/``. That order exists
        because **model names routinely contain slashes** (``z-ai/glm-5.1``,
        ``moonshotai/kimi-k2.6``): splitting first would read such a name as
        endpoint ``z-ai`` + model ``glm-5.1`` and fail. Splitting on the
        *first* slash also keeps ``gateway/z-ai/glm-5.1`` working, since the
        model half is whatever follows it.

        Args:
            ref: The ref to resolve. Defaults to ``$OPENJIUWEN_E2E_MODEL``
                when unset, so a caller can pass its own flag through and
                still honour the env var.

        Raises:
            ValueError: when the ref names nothing, listing what exists.
        """
        raw = (ref if ref is not None else os.getenv(ENV_MODEL_REF) or "").strip()
        if not raw:
            return self.default
        endpoint = self._endpoint_or_none(raw)
        if endpoint is not None:
            return ModelRef(endpoint=endpoint, model=endpoint.model)
        serving = self.endpoints_for(raw)
        if serving:
            return ModelRef(endpoint=serving[0], model=raw)
        if "/" in raw:
            endpoint_name, _, model = raw.partition("/")
            endpoint = self._endpoint_or_none(endpoint_name.strip())
            model = model.strip()
            if endpoint is not None and endpoint.serves(model):
                return ModelRef(endpoint=endpoint, model=model)
        raise ValueError(f"model ref {raw!r} matches no endpoint or model; {self._inventory()}")

    def _endpoint_or_none(self, name: str) -> ModelEndpoint | None:
        """Look up an endpoint by name without raising."""
        for endpoint in self.endpoints:
            if endpoint.name == name:
                return endpoint
        return None

    def _inventory(self) -> str:
        """Render the available refs, for an error message."""
        refs = [f"{e.name}/{m}" for e in self.endpoints for m in e.models]
        return f"available: {refs}"

    def endpoint(self, name: str) -> ModelEndpoint:
        """Look up one endpoint by ``name``.

        Raises:
            KeyError: when no endpoint carries that name.
        """
        for endpoint in self.endpoints:
            if endpoint.name == name:
                return endpoint
        available = [e.name for e in self.endpoints]
        raise KeyError(f"no endpoint named {name!r}; available: {available}")

    def endpoints_for(self, model: str) -> list[ModelEndpoint]:
        """Every endpoint serving ``model``, in declaration order.

        More than one means the model has failover peers (same name, distinct
        keys or hosts).
        """
        return [e for e in self.endpoints if e.serves(model)]

    def require(self, *models: str) -> None:
        """Assert every named model is served by some endpoint.

        Lets an E2E fail with "the config no longer has this model" instead of
        a 404 from an endpoint that never heard of it.

        Raises:
            ValueError: listing the models nothing serves.
        """
        missing = [m for m in models if not self.endpoints_for(m)]
        if missing:
            served = sorted({m for e in self.endpoints for m in e.models})
            raise ValueError(f"config serves none of: {missing}; available models: {served}")


def _parse_endpoint(raw: dict[str, Any], index: int) -> ModelEndpoint:
    """Build one ``ModelEndpoint``, rejecting an incomplete or unusable entry."""
    missing = [k for k in ("name", "provider", "api_base", "api_key") if not str(raw.get(k) or "").strip()]
    if missing:
        raise ValueError(f"endpoints[{index}] is missing required field(s): {missing}")
    name = str(raw["name"])
    if "/" in name:
        # "/" separates the halves of a "<endpoint>/<model>" ref, and only the
        # first one is the separator (model names contain slashes of their own).
        # A slash in the name would silently split in the wrong place.
        raise ValueError(
            f"endpoints[{index}] name {name!r} must not contain '/': it is the separator "
            f"in a '<endpoint>/<model>' ref. Model names may contain slashes; endpoint names may not.",
        )
    models = tuple(raw.get("models") or ())
    if not models:
        raise ValueError(f"endpoints[{index}] ({name!r}) declares no models")
    return ModelEndpoint(
        name=name,
        provider=str(raw["provider"]),
        api_base=str(raw["api_base"]),
        api_key=str(raw["api_key"]),
        models=models,
    )


def load_llm_config(path: Path | str | None = None) -> LlmConfig:
    """Load the endpoint config.

    Args:
        path: Config file. Defaults to ``$OPENJIUWEN_E2E_LLM_CONFIG`` when set,
            otherwise ``config_llm_local.yaml`` beside this module.

    Raises:
        FileNotFoundError: when the file is absent — E2Es need real endpoints
            and cannot fall back to anything meaningful.
        ValueError: when the file declares no endpoints or one is incomplete.
    """
    resolved = Path(path or os.getenv(_ENV_CONFIG_PATH) or DEFAULT_CONFIG_PATH)
    if not resolved.is_file():
        raise FileNotFoundError(
            f"model endpoint config not found: {resolved}\n"
            f"  cp {EXAMPLE_CONFIG_PATH.name} {DEFAULT_CONFIG_PATH.name}   # then fill in real hosts/keys\n"
            f"  (or point {_ENV_CONFIG_PATH} at an existing one)",
        )
    with open(resolved, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    entries = raw.get("endpoints")
    if isinstance(entries, list):
        names = [str((e or {}).get("name", "")) for e in entries if isinstance(e, dict)]
        duplicates = sorted({n for n in names if n and names.count(n) > 1})
        if duplicates:
            # Lookup returns the first match, so a duplicate name would make one
            # entry permanently unreachable — and its ref silently resolve elsewhere.
            raise ValueError(f"{resolved} declares duplicate endpoint name(s): {duplicates}")
    if not entries:
        # The config is gitignored, so a pre-existing local copy does not update
        # itself when the schema changes. Name the old shape explicitly rather
        # than leaving the reader with "declares no endpoints" over a file that
        # visibly declares an endpoint.
        if {"api_base", "api_key"} <= raw.keys():
            raise ValueError(
                f"{resolved} uses the old single-endpoint format "
                f"(top-level api_base / api_key / models).\n"
                f"  It is now a list of named endpoints, so several hosts, keys and providers "
                f"can coexist. Rewrite it as:\n"
                f"      endpoints:\n"
                f"        - name: primary\n"
                f"          provider: DeepSeek        # openjiuwen ProviderType\n"
                f"          api_base: {raw.get('api_base')!r}\n"
                f"          api_key: <your key>\n"
                f"          models: {list(raw.get('models') or ['<model>'])!r}\n"
                f"  See {EXAMPLE_CONFIG_PATH.name} for the full schema.",
            )
        raise ValueError(f"{resolved} declares no 'endpoints'; see {EXAMPLE_CONFIG_PATH.name}")
    return LlmConfig(endpoints=tuple(_parse_endpoint(e, i) for i, e in enumerate(entries)))


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "ENV_MODEL_REF",
    "EXAMPLE_CONFIG_PATH",
    "LlmConfig",
    "ModelEndpoint",
    "ModelRef",
    "load_llm_config",
]
