"""Configuration contracts for the embedded PersonalContext core."""

from __future__ import annotations

import os
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.security.path_checker import is_sensitive_path
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.harness.personal_context.status_codes import StatusCode, build_error

Provider = Literal[
    "local_files",
    "github",
    "feishu",
    "browser_bookmarks",
    "zhihu_reader",
    "toutiao_reader",
]

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_WINDOWS_SENSITIVE_PATH = re.compile(r"^[a-z]:\\windows\\(?:system32|syswow64|system)(?:\\|$)", re.IGNORECASE)
_GITHUB_RESOURCES = ("readme", "issues", "pull_requests", "commits", "code")
_FEISHU_RESOURCES = ("docs", "tasks", "calendar")
_RFC3339_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


def _mapping(value: object, *, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return deepcopy(dict(value))


def _non_empty_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _safe_segment(value: object, *, name: str) -> str:
    text = _non_empty_text(value, name=name)
    if text in {".", ".."} or not _SAFE_SEGMENT.fullmatch(text):
        raise ValueError(f"{name} must be a safe path segment")
    return text


def _safe_profile(value: object) -> str:
    text = _non_empty_text(value, name="profile")
    if text in {".", ".."} or any(separator in text for separator in ("/", "\\")):
        raise ValueError("profile must be a single path segment")
    return text


def _normalize_rfc3339(value: object, *, name: str) -> tuple[str, datetime]:
    text = _non_empty_text(value, name=name)
    if not _RFC3339_TIMESTAMP.fullmatch(text):
        raise ValueError(f"{name} must be a timezone-aware RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be a timezone-aware RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    utc_value = parsed.astimezone(UTC)
    return utc_value.isoformat().replace("+00:00", "Z"), utc_value


def _normalize_time_range(value: object) -> dict[str, object]:
    raw = _mapping(value, name="time_range")
    mode = raw.get("mode")
    if mode == "all":
        if set(raw) != {"mode"}:
            raise ValueError("all time_range must contain only mode")
        return {"mode": "all"}
    if mode == "recent":
        if set(raw) != {"mode", "recent_days"}:
            raise ValueError("recent time_range must contain only mode and recent_days")
        recent_days = raw["recent_days"]
        if isinstance(recent_days, bool) or not isinstance(recent_days, int) or recent_days <= 0:
            raise ValueError("recent_days must be a positive integer")
        return {"mode": "recent", "recent_days": recent_days}
    if mode == "fixed":
        if set(raw) != {"mode", "start_at", "end_at"}:
            raise ValueError("fixed time_range must contain only mode, start_at, and end_at")
        start_at, start_value = _normalize_rfc3339(raw["start_at"], name="start_at")
        end_at, end_value = _normalize_rfc3339(raw["end_at"], name="end_at")
        if start_value >= end_value:
            raise ValueError("fixed time_range start_at must be before end_at")
        return {"mode": "fixed", "start_at": start_at, "end_at": end_at}
    raise ValueError("time_range mode must be all, recent, or fixed")


def _normalize_path(value: object, *, name: str) -> str:
    text = _non_empty_text(str(value) if isinstance(value, Path) else value, name=name)
    expanded = os.path.expanduser(text)
    path = Path(expanded)
    user_relative = text == "~" or text.startswith(("~/", "~\\"))
    if not path.is_absolute() and not user_relative:
        raise ValueError(f"{name} must be absolute or start with '~'")
    resolved = path.resolve()
    windows_path = str(resolved).replace("/", "\\")
    if is_sensitive_path(resolved) or _WINDOWS_SENSITIVE_PATH.match(windows_path):
        raise ValueError(f"{name} points to a sensitive path")
    return str(resolved)


def _normalize_url(value: object, *, name: str, host_suffix: str, path_prefix: str | None = None) -> str:
    text = _non_empty_text(value, name=name)
    parsed = urlsplit(text)
    if parsed.scheme.casefold() != "https" or not parsed.netloc:
        raise ValueError(f"{name} must be an https URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{name} must be an https URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} must not contain a custom port") from exc
    if port is not None:
        raise ValueError(f"{name} must not contain a custom port")
    if "?" in text or "#" in text:
        raise ValueError(f"{name} must not contain query or fragment")
    host = (parsed.hostname or "").casefold()
    if not host == host_suffix and not host.endswith(f".{host_suffix}"):
        raise ValueError(f"{name} must point to {host_suffix}")
    path = parsed.path.rstrip("/")
    if path_prefix is not None and not path.casefold().startswith(path_prefix.rstrip("/").casefold() + "/"):
        raise ValueError(f"{name} must contain {path_prefix}")
    if not path or path == path_prefix:
        raise ValueError(f"{name} must include a resource path")
    return urlunsplit(("https", parsed.netloc, path, "", ""))


def _ordered_resources(value: object, *, allowed: tuple[str, ...], name: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{name} must be a non-empty list")
    values = [_non_empty_text(item, name=name).casefold() for item in value]
    if any(item not in allowed for item in values):
        raise ValueError(f"{name} contains an unsupported resource")
    return [item for item in allowed if item in values]


def _normalize_service_source(provider: str, source: object) -> dict[str, Any]:
    raw = _mapping(source, name="source")

    if provider == "local_files":
        if set(raw) != {"root_dir"}:
            raise ValueError("local_files source must contain only root_dir")
        return {"root_dir": _normalize_path(raw["root_dir"], name="root_dir")}

    if provider == "github":
        allowed = {"owner", "repo", "resources"}
        if set(raw) - allowed or not {"owner", "repo"}.issubset(raw):
            raise ValueError("github source requires owner and repo")
        resources = raw.get("resources", list(_GITHUB_RESOURCES))
        return {
            "owner": _safe_segment(raw["owner"], name="owner"),
            "repo": _safe_segment(raw["repo"], name="repo"),
            "resources": _ordered_resources(resources, allowed=_GITHUB_RESOURCES, name="resources"),
        }

    if provider == "feishu":
        mode = _non_empty_text(raw.get("mode"), name="mode").casefold()
        if mode == "account":
            allowed = {"mode", "resources", "document_ids", "query", "start", "end"}
            if set(raw) - allowed:
                raise ValueError("feishu account source contains unsupported fields")
            result: dict[str, Any] = {
                "mode": mode,
                "resources": _ordered_resources(raw.get("resources"), allowed=_FEISHU_RESOURCES, name="resources"),
            }
            has_document_ids = "document_ids" in raw
            has_query = "query" in raw
            if has_document_ids and has_query:
                raise ValueError("feishu docs source accepts document_ids or query, not both")
            if (has_document_ids or has_query) and "docs" not in result["resources"]:
                raise ValueError("document_ids/query require docs resource")
            if any(field_name in raw for field_name in ("start", "end")) and "calendar" not in result["resources"]:
                raise ValueError("start/end require calendar resource")
            if "document_ids" in raw:
                if not isinstance(raw["document_ids"], (list, tuple)):
                    raise ValueError("document_ids must be a list")
                result["document_ids"] = [_non_empty_text(item, name="document_ids") for item in raw["document_ids"]]
            for field_name in ("query", "start", "end"):
                if field_name in raw:
                    result[field_name] = _non_empty_text(raw[field_name], name=field_name)
            return result
        if mode == "wiki_space":
            allowed = {"mode", "wiki_space_id", "root_node_token", "max_depth", "max_nodes"}
            if set(raw) - allowed or "wiki_space_id" not in raw:
                raise ValueError("feishu wiki_space source requires wiki_space_id")
            max_depth = raw.get("max_depth", 3)
            max_nodes = raw.get("max_nodes", 200)
            if isinstance(max_depth, bool) or not isinstance(max_depth, int) or not 0 <= max_depth <= 20:
                raise ValueError("max_depth must be between 0 and 20")
            if isinstance(max_nodes, bool) or not isinstance(max_nodes, int) or not 1 <= max_nodes <= 10_000:
                raise ValueError("max_nodes must be between 1 and 10000")
            result = {
                "mode": mode,
                "wiki_space_id": _non_empty_text(raw["wiki_space_id"], name="wiki_space_id"),
                "max_depth": max_depth,
                "max_nodes": max_nodes,
            }
            if "root_node_token" in raw:
                result["root_node_token"] = _non_empty_text(raw["root_node_token"], name="root_node_token")
            return result
        raise ValueError("feishu mode must be account or wiki_space")

    if provider == "browser_bookmarks":
        allowed = {"profile", "bookmarks_path", "bookmark_folder_paths", "include_subfolders", "fetch_page_content"}
        if set(raw) - allowed:
            raise ValueError("browser_bookmarks source contains unsupported fields")
        profile = _safe_profile(raw.get("profile", "Default"))
        bookmarks_path = raw.get("bookmarks_path")
        if bookmarks_path is None:
            local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            bookmarks_path = local_app_data / "Microsoft" / "Edge" / "User Data" / profile / "Bookmarks"
        elif not isinstance(bookmarks_path, str):
            raise ValueError("bookmarks_path must be a path")
        folders = raw.get("bookmark_folder_paths", [])
        if not isinstance(folders, (list, tuple)):
            raise ValueError("bookmark_folder_paths must be a list")
        include_subfolders = raw.get("include_subfolders", True)
        fetch_page_content = raw.get("fetch_page_content", True)
        if not isinstance(include_subfolders, bool) or not isinstance(fetch_page_content, bool):
            raise ValueError("bookmark boolean options must be booleans")
        return {
            "profile": profile,
            "bookmarks_path": _normalize_path(bookmarks_path, name="bookmarks_path"),
            "bookmark_folder_paths": [_non_empty_text(item, name="bookmark_folder_paths") for item in folders],
            "include_subfolders": include_subfolders,
            "fetch_page_content": fetch_page_content,
        }

    if provider == "zhihu_reader":
        if set(raw) != {"column_url"}:
            raise ValueError("zhihu_reader source must contain only column_url")
        column_url = _normalize_url(
            raw["column_url"], name="column_url", host_suffix="zhihu.com", path_prefix="/column/"
        )
        path_parts = [part for part in urlsplit(column_url).path.split("/") if part]
        if len(path_parts) != 2 or path_parts[0] != "column" or not path_parts[1]:
            raise ValueError("column_url must be a single Zhihu column URL")
        return {"column_url": column_url}

    if provider == "toutiao_reader":
        if set(raw) != {"profile_url"}:
            raise ValueError("toutiao_reader source must contain only profile_url")
        raw_profile_url = _non_empty_text(raw["profile_url"], name="profile_url")
        profile_url = _normalize_url(
            raw_profile_url,
            name="profile_url",
            host_suffix="toutiao.com",
            path_prefix="/c/user/token/",
        )
        # Toutiao's public profile endpoint redirects the slashless form;
        # keep a user-supplied trailing slash so the provider can fetch it
        # without following an untrusted redirect.
        if urlsplit(raw_profile_url).path.endswith("/"):
            profile_url += "/"
        path_parts = [part for part in urlsplit(profile_url).path.split("/") if part]
        if len(path_parts) != 4 or path_parts[:3] != ["c", "user", "token"] or not path_parts[3]:
            raise ValueError("profile_url must be a Toutiao profile homepage URL")
        return {"profile_url": profile_url}

    raise ValueError("unsupported provider")


def _normalize_credentials(provider: str, credentials: object) -> dict[str, str]:
    raw = _mapping(credentials, name="credentials")
    if provider in {"local_files", "feishu", "browser_bookmarks", "zhihu_reader", "toutiao_reader"}:
        if raw:
            raise ValueError(f"{provider} does not accept credentials")
        return {}
    required = "token" if provider == "github" else "access_token"
    if set(raw) != {required}:
        raise ValueError(f"{provider} credentials must contain only {required}")
    value = _non_empty_text(raw[required], name=required)
    return {required: value}


class PersonalContextFetchServiceConfig(BaseModel):
    """One independently scheduled, fixed-provider fetch service."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    service_id: str
    provider: Provider
    enabled: bool
    interval_seconds: float = Field(default=10_800.0, gt=0, le=31_536_000)
    max_items_per_run: int | None = Field(default=None, ge=1, le=10_000)
    time_range: dict[str, object]
    source: dict[str, object]
    credentials: dict[str, str] = Field(default_factory=dict, repr=False)

    @model_validator(mode="before")
    @classmethod
    def normalize_provider_payload(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        copied = deepcopy(dict(value))
        provider = copied.get("provider")
        if isinstance(provider, str):
            provider = provider.strip().casefold()
            copied["provider"] = provider
            if "time_range" in copied:
                copied["time_range"] = _normalize_time_range(copied["time_range"])
            copied["source"] = _normalize_service_source(provider, copied.get("source", {}))
            copied["credentials"] = _normalize_credentials(provider, copied.get("credentials", {}))
        return copied

    @field_validator("service_id")
    @classmethod
    def validate_service_id(cls, value: str) -> str:
        return _safe_segment(value, name="service_id")


class PersonalContextConfig(BaseModel):
    """Complete immutable PersonalContext configuration parsed from a plain dictionary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    collection_enabled: bool = False
    agent_use_enabled: bool = False
    strategy_profile: Literal["rules", "balanced", "agent"]
    model_client: ModelClientConfig | None = Field(default=None, repr=False)
    model_request: ModelRequestConfig | None = None
    fetch_services: tuple[PersonalContextFetchServiceConfig, ...]

    @classmethod
    def from_dict(cls, config: dict[str, object]) -> "PersonalContextConfig":
        """Validate and normalize a plain configuration dictionary.

        Pydantic and path/provider failures are intentionally converted at this
        boundary into the SDK's existing validation error type.
        """
        safe_error: BaseError | None = None
        try:
            if not isinstance(config, dict):
                raise TypeError("PersonalContext configuration must be a dictionary")
            result = cls.model_validate(deepcopy(config))
        except BaseError:
            safe_error = build_error(
                StatusCode.CONTEXT_PROACTIVE_CONFIG_INVALID,
                error_msg="invalid PersonalContext configuration",
            )
        except Exception:
            safe_error = build_error(
                StatusCode.CONTEXT_PROACTIVE_CONFIG_INVALID,
                error_msg="invalid PersonalContext configuration",
            )
        if safe_error is not None:
            raise safe_error from None
        return result

    @model_validator(mode="before")
    @classmethod
    def normalize_input(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        copied = deepcopy(dict(value))
        services = copied.get("fetch_services")
        if isinstance(services, (list, tuple)):
            copied["fetch_services"] = sorted(
                services,
                key=lambda item: str(item.get("service_id", "")) if isinstance(item, Mapping) else "",
            )
        return copied

    @model_validator(mode="after")
    def validate_semantics(self) -> "PersonalContextConfig":
        service_ids = [service.service_id for service in self.fetch_services]
        if len(service_ids) != len(set(service_ids)):
            raise ValueError("fetch_services service_id values must be unique")
        provider_counts: dict[str, int] = {}
        for service in self.fetch_services:
            provider_counts[service.provider] = provider_counts.get(service.provider, 0) + 1
            if provider_counts[service.provider] > 20:
                raise ValueError(f"{service.provider} may define at most 20 fetch services")
        has_client = self.model_client is not None
        has_request = self.model_request is not None
        if has_client != has_request:
            raise ValueError("model_client and model_request must be provided together")
        if self.strategy_profile in {"balanced", "agent"} and not (has_client and has_request):
            raise ValueError(f"{self.strategy_profile} strategy requires model_client and model_request")
        return self
