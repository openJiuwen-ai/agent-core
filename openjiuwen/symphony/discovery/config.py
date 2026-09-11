"""Configuration contract for Symphony's runtime Skill discovery surface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DEFAULT_CANDIDATE_BUDGET_RATIO = 0.01
DEFAULT_MAX_OUTPUT_CHARS = 12_000
DEFAULT_MAX_RESULTS = 10
DEFAULT_MAX_LIST_ENTRIES = 40
DEFAULT_INCREMENTAL_NOTICE_MAX_CHARS = 4_000
DEFAULT_PROMPT_PREFERRED_SKILLS = (
    "skill-creator",
    "swarmskill-creator",
    "symphony-assistant",
)
_DISCOVERY_SETTING_NAMES = frozenset(
    {
        "candidate_budget_ratio",
        "context_window_tokens",
        "max_output_chars",
        "max_results",
        "max_list_entries",
        "incremental_notice_max_chars",
        "use_existing_index",
        "prompt_preferred_skills",
    }
)


@dataclass(frozen=True)
class DiscoverySettings:
    """Resolved task-independent settings for one SkillFS environment."""

    candidate_budget_ratio: float = DEFAULT_CANDIDATE_BUDGET_RATIO
    context_window_tokens: int = 200_000
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS
    max_results: int = DEFAULT_MAX_RESULTS
    max_list_entries: int = DEFAULT_MAX_LIST_ENTRIES
    incremental_notice_max_chars: int = DEFAULT_INCREMENTAL_NOTICE_MAX_CHARS
    use_existing_index: bool = False
    prompt_preferred_skills: tuple[str, ...] = DEFAULT_PROMPT_PREFERRED_SKILLS

    @property
    def candidate_budget_tokens(self) -> int:
        return max(1, int(self.context_window_tokens * self.candidate_budget_ratio))


def load_discovery_settings(config: dict[str, Any] | None = None) -> DiscoverySettings:
    """Load the DCI discovery settings from an application config mapping."""

    resolved = config if isinstance(config, dict) else {}
    symphony = _mapping(resolved.get("symphony"))
    retrieval = _mapping(symphony.get("skill_retrieval"))
    raw = _mapping(retrieval.get("discovery"))
    if not raw and _DISCOVERY_SETTING_NAMES.intersection(resolved):
        raw = resolved

    return DiscoverySettings(
        candidate_budget_ratio=_ratio(
            raw.get("candidate_budget_ratio"),
            DEFAULT_CANDIDATE_BUDGET_RATIO,
        ),
        context_window_tokens=_context_window_tokens(resolved, raw),
        max_output_chars=_positive_int(
            raw.get("max_output_chars"),
            DEFAULT_MAX_OUTPUT_CHARS,
        ),
        max_results=_positive_int(raw.get("max_results"), DEFAULT_MAX_RESULTS),
        max_list_entries=_positive_int(
            raw.get("max_list_entries"),
            DEFAULT_MAX_LIST_ENTRIES,
        ),
        incremental_notice_max_chars=_positive_int(
            raw.get("incremental_notice_max_chars"),
            DEFAULT_INCREMENTAL_NOTICE_MAX_CHARS,
        ),
        use_existing_index=_bool(
            raw.get("use_existing_index"),
            False,
        ),
        prompt_preferred_skills=_string_tuple(
            raw.get("prompt_preferred_skills"),
            DEFAULT_PROMPT_PREFERRED_SKILLS,
        ),
    )


def _context_window_tokens(config: dict[str, Any], raw: dict[str, Any]) -> int:
    explicit = _optional_positive_int(raw.get("context_window_tokens"))
    if explicit is not None:
        return explicit
    model_client = _default_model_client_config(config)
    model_name = str(model_client.get("model_name") or model_client.get("model") or "").strip()
    react = _mapping(config.get("react"))
    context_engine = _mapping(react.get("context_engine_config"))
    fallback = _optional_positive_int(context_engine.get("context_window_tokens"))
    model_windows = context_engine.get("model_context_window_tokens")
    if not isinstance(model_windows, dict):
        model_windows = None
    try:
        from openjiuwen.core.context_engine.context.context_utils import ContextUtils

        return ContextUtils.resolve_context_max(
            model_name=model_name or None,
            fallback_context_window_tokens=fallback,
            model_context_window_tokens=model_windows,
        )
    except Exception:
        return fallback or 200_000


def _default_model_client_config(config: dict[str, Any]) -> dict[str, Any]:
    models = _mapping(config.get("models"))
    defaults = models.get("defaults")
    if isinstance(defaults, list):
        first_candidate: dict[str, Any] = {}
        for item in defaults:
            if not isinstance(item, dict):
                continue
            candidate = _mapping(item.get("model_client_config"))
            if not candidate:
                continue
            if item.get("is_default") is True:
                return candidate
            if not first_candidate:
                first_candidate = candidate
        if first_candidate:
            return first_candidate
    default = _mapping(models.get("default"))
    return _mapping(default.get("model_client_config"))


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _positive_int(value: Any, default: int) -> int:
    parsed = _optional_positive_int(value)
    return parsed if parsed is not None else default


def _optional_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _ratio(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if 0 < parsed <= 1 else default


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on", "enabled"}


def _string_tuple(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        items: tuple[Any, ...] = tuple(value.split(","))
    elif isinstance(value, (list, tuple)):
        items = tuple(value)
    else:
        return default
    strings: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text:
            strings.append(text)
    return tuple(dict.fromkeys(strings))


__all__ = ["DiscoverySettings", "load_discovery_settings"]
