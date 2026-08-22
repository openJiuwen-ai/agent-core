# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Configuration schema and loader for the Team Verification Layer.

Expected config.yaml structure under ``team.verification``:

```yaml
team:
  verification:
    enabled: false                   # Master toggle (default: false)
    block_on_fail: false             # Block leader consolidation on FAIL (default: false)
    auto_rework: false               # Auto-create rework tasks (default: false)
    pass_threshold: 70               # Minimum score for PASS (default: 70)
    rework_threshold: 40             # Below this = FAIL (default: 40)
    skip_patterns:                   # Task title patterns to skip
      - "heartbeat"
      - "ping"
      - "sync"
    model:                           # Optional: dedicated reviewer model
      model_client_config:
        model_name: "gpt-4o-mini"
        client_provider: "openai"
      model_config_obj:
        temperature: 0.2
```
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VerificationConfig:
    """Typed configuration for the Team Verification Layer."""

    enabled: bool = False
    block_on_fail: bool = False
    auto_rework: bool = False
    pass_threshold: int = 70
    rework_threshold: int = 40
    skip_patterns: set[str] = field(default_factory=set)
    model_config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, config: dict[str, Any] | None) -> VerificationConfig:
        """Build from a raw config dict (typically config.yaml's ``team.verification``)."""
        if not config:
            return cls()

        skip_patterns = set()
        raw_patterns = config.get("skip_patterns", [])
        if isinstance(raw_patterns, list):
            skip_patterns = {str(p).lower() for p in raw_patterns if isinstance(p, str)}

        return cls(
            enabled=bool(config.get("enabled", False)),
            block_on_fail=bool(config.get("block_on_fail", False)),
            auto_rework=bool(config.get("auto_rework", False)),
            pass_threshold=int(config.get("pass_threshold", 70)),
            rework_threshold=int(config.get("rework_threshold", 40)),
            skip_patterns=skip_patterns,
            model_config=dict(config.get("model", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for storage/display."""
        return {
            "enabled": self.enabled,
            "block_on_fail": self.block_on_fail,
            "auto_rework": self.auto_rework,
            "pass_threshold": self.pass_threshold,
            "rework_threshold": self.rework_threshold,
            "skip_patterns": sorted(self.skip_patterns),
            "model_config": self.model_config,
        }
