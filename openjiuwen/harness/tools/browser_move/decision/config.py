# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Browser-only policy configuration, separate from the generative model."""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class BrowserDecisionConfig:
    mode: str = "llm"
    provider: str = "typesafe"
    model: str = ""
    api_base: str = ""
    api_key_env: str = ""
    request_timeout_ms: int = 3000
    max_retries: int = 1
    max_decisions: int = 40
    candidate_limit: int = 30
    min_confidence: float = 0.65
    fallback_reserve_ms: int = 1000

    def __post_init__(self) -> None:
        if self.mode not in {"llm", "shadow", "hybrid"}:
            raise ValueError("browser.decision.mode must be llm, shadow or hybrid")
        defaults = {
            "typesafe": ("jev-1.13.0", "https://api.typesafe.ai/v1", "TYPESAFE_API_KEY"),
            "openrouter": ("typesafe/jev-1.13", "https://openrouter.ai/api/alpha", "OPENROUTER_API_KEY"),
        }
        if self.provider not in defaults:
            raise ValueError("browser.decision.provider must be typesafe or openrouter")
        for name, value in zip(("model", "api_base", "api_key_env"), defaults[self.provider]):
            configured = getattr(self, name)
            if not isinstance(configured, str):
                raise ValueError(f"browser.decision.{name} must be a string")
            object.__setattr__(self, name, configured.strip() or value)
        if self.provider == "openrouter" and not (
            self.model == "~typesafe/jev-latest"
            or re.fullmatch(r"typesafe/jev-\d+\.\d+(?:\.\d+)?(?:-\d{8})?", self.model)
        ):
            raise ValueError("OpenRouter requires a typesafe/jev version or ~typesafe/jev-latest")
        address = urlsplit(self.api_base)
        if address.scheme != "https" or not address.hostname or address.username or address.password:
            raise ValueError("browser.decision.api_base requires an HTTPS origin without credentials")
        if address.query or address.fragment:
            raise ValueError("browser.decision.api_base must not contain query or fragment")
        if not self.model.strip() or not self.api_key_env.isidentifier():
            raise ValueError("browser.decision requires a model and an environment variable name")
        for name, lower, upper in (
            ("request_timeout_ms", 100, 30000), ("max_retries", 0, 1),
            ("max_decisions", 1, 200), ("candidate_limit", 1, 30),
            ("fallback_reserve_ms", 0, 10000),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
                raise ValueError(f"browser.decision.{name} must be an integer between {lower} and {upper}")
        if (isinstance(self.min_confidence, bool) or not isinstance(self.min_confidence, (int, float))
                or not 0 <= self.min_confidence <= 1):
            raise ValueError("browser.decision.min_confidence must be between 0 and 1")
