from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from openjiuwen.core.common.exception.errors import ValidationError as JiuwenValidationError
from openjiuwen.harness.personal_context.config import PersonalContextConfig


def _local_service(service_id: str = "notes", *, enabled: bool = True) -> dict:
    return {
        "service_id": service_id,
        "provider": "local_files",
        "enabled": enabled,
        "interval_seconds": 300,
        "max_items_per_run": 100,
        "time_range": {"mode": "all"},
        "source": {"root_dir": "~/notes"},
        "credentials": {},
    }


def _valid_config(*services: dict, strategy_profile: str = "rules") -> dict:
    return {
        "collection_enabled": True,
        "agent_use_enabled": True,
        "strategy_profile": strategy_profile,
        "model_client": None,
        "model_request": None,
        "fetch_services": list(services or (_local_service(),)),
    }


def _github_service(token: str = "mock-token") -> dict:
    return {
        "service_id": "repo",
        "provider": "github",
        "enabled": True,
        "interval_seconds": 60,
        "max_items_per_run": None,
        "time_range": {"mode": "all"},
        "source": {"owner": "openai", "repo": "agent-core"},
        "credentials": {"token": token},
    }


def _bookmark_service(service_id: str = "bookmarks") -> dict:
    service = _local_service(service_id)
    service.update(provider="browser_bookmarks", source={}, credentials={})
    return service


def test_config_normalizes_service_order_and_is_frozen():
    config = PersonalContextConfig.from_dict(_valid_config(_local_service("z"), _local_service("a")))

    assert [item.service_id for item in config.fetch_services] == ["a", "z"]
    with pytest.raises(PydanticValidationError):
        config.enabled = False


def test_dual_global_switches_default_false_and_serialize_without_legacy_fields():
    raw = _valid_config(_local_service())
    raw.pop("collection_enabled")
    raw.pop("agent_use_enabled")

    config = PersonalContextConfig.from_dict(raw)
    dumped = config.model_dump(mode="json")

    assert config.collection_enabled is False
    assert config.agent_use_enabled is False
    assert dumped["collection_enabled"] is False
    assert dumped["agent_use_enabled"] is False
    assert "enabled" not in dumped
    assert "fetching_enabled" not in dumped


@pytest.mark.parametrize("legacy_field", ["enabled", "fetching_enabled"])
def test_config_rejects_legacy_global_switch_fields(legacy_field: str):
    raw = _valid_config(_local_service())
    raw[legacy_field] = True

    with pytest.raises(JiuwenValidationError):
        PersonalContextConfig.from_dict(raw)


@pytest.mark.parametrize(
    ("time_range", "expected"),
    [
        ({"mode": "all"}, {"mode": "all"}),
        (
            {"mode": "recent", "recent_days": 3},
            {"mode": "recent", "recent_days": 3},
        ),
        (
            {
                "mode": "fixed",
                "start_at": "2026-08-01T00:00:00+08:00",
                "end_at": "2026-08-11T00:00:00+08:00",
            },
            {
                "mode": "fixed",
                "start_at": "2026-07-31T16:00:00Z",
                "end_at": "2026-08-10T16:00:00Z",
            },
        ),
    ],
)
def test_fetch_service_accepts_and_normalizes_strict_time_ranges(
    time_range: dict[str, object],
    expected: dict[str, object],
):
    service = _local_service()
    service["time_range"] = time_range

    config = PersonalContextConfig.from_dict(_valid_config(service))

    assert config.fetch_services[0].time_range == expected


def test_fetch_service_requires_time_range():
    service = _local_service()
    service.pop("time_range")

    with pytest.raises(JiuwenValidationError):
        PersonalContextConfig.from_dict(_valid_config(service))


@pytest.mark.parametrize(
    "time_range",
    [
        {"mode": "all", "recent_days": 3},
        {"mode": "recent"},
        {"mode": "recent", "recent_days": 0},
        {"mode": "recent", "recent_days": -1},
        {"mode": "recent", "recent_days": True},
        {"mode": "recent", "recent_days": 1.5},
        {"mode": "recent", "recent_days": 3, "start_at": "2026-08-01T00:00:00Z"},
        {
            "mode": "fixed",
            "start_at": "2026-08-01T00:00:00",
            "end_at": "2026-08-11T00:00:00Z",
        },
        {
            "mode": "fixed",
            "start_at": "2026-08-11T00:00:00Z",
            "end_at": "2026-08-01T00:00:00Z",
        },
        {
            "mode": "fixed",
            "start_at": "2026-08-01T00:00:00Z",
            "end_at": "2026-08-01T00:00:00Z",
        },
        {
            "mode": "fixed",
            "start_at": "2026-08-01T00:00:00Z",
            "end_at": "2026-08-11T00:00:00Z",
            "recent_days": 3,
        },
        {"mode": "fixed", "start_at": "2026-08-01T00:00:00Z"},
        {"mode": "unknown"},
    ],
)
def test_fetch_service_rejects_invalid_time_range_combinations(
    time_range: dict[str, object],
):
    service = _local_service()
    service["time_range"] = time_range

    with pytest.raises(JiuwenValidationError):
        PersonalContextConfig.from_dict(_valid_config(service))


def test_fetch_service_interval_defaults_to_three_hours():
    service = _local_service()
    service.pop("interval_seconds")

    config = PersonalContextConfig.from_dict(_valid_config(service))

    assert config.fetch_services[0].interval_seconds == 10_800.0


def test_credentials_are_not_in_repr():
    config = PersonalContextConfig.from_dict(_valid_config(_github_service("mock-secret")))

    assert "mock-secret" not in repr(config)


def test_config_rejects_unknown_fields_and_duplicate_service_ids():
    with pytest.raises(JiuwenValidationError):
        PersonalContextConfig.from_dict({**_valid_config(), "unexpected": True})

    with pytest.raises(JiuwenValidationError):
        PersonalContextConfig.from_dict(_valid_config(_local_service("same"), _local_service("same")))


def test_config_rejects_more_than_twenty_services_for_one_provider():
    services = [_local_service(f"local-{index:02d}") for index in range(21)]

    with pytest.raises(JiuwenValidationError):
        PersonalContextConfig.from_dict(_valid_config(*services))


def test_config_counts_each_provider_independently():
    local_services = [_local_service(f"local-{index:02d}") for index in range(20)]
    bookmark_services = [_bookmark_service(f"bookmark-{index:02d}") for index in range(20)]

    config = PersonalContextConfig.from_dict(_valid_config(*local_services, *bookmark_services))

    assert len(config.fetch_services) == 40


def test_service_id_is_bounded_to_128_safe_segment_characters():
    with pytest.raises(JiuwenValidationError):
        PersonalContextConfig.from_dict(_valid_config(_local_service("a" * 129)))

    config = PersonalContextConfig.from_dict(_valid_config(_local_service("a" * 128)))
    assert config.fetch_services[0].service_id == "a" * 128


@pytest.mark.parametrize(
    ("provider", "source", "credentials"),
    [
        ("local_files", {"root_dir": "~/notes"}, {}),
        ("github", {"owner": "openai", "repo": "agent-core"}, {"token": "mock"}),
        (
            "feishu",
            {"mode": "account", "resources": ["docs"]},
            {},
        ),
        ("browser_bookmarks", {}, {}),
        ("zhihu_reader", {"column_url": "https://www.zhihu.com/column/example"}, {}),
        ("toutiao_reader", {"profile_url": "https://www.toutiao.com/c/user/token/example"}, {}),
    ],
)
def test_config_accepts_the_closed_provider_shapes(provider: str, source: dict, credentials: dict):
    service = _local_service("service")
    service.update(provider=provider, source=source, credentials=credentials)

    config = PersonalContextConfig.from_dict(_valid_config(service))

    assert config.fetch_services[0].provider == provider


def test_feishu_rejects_embedded_access_tokens():
    service = _local_service("feishu-token")
    service.update(
        provider="feishu",
        source={"mode": "account", "resources": ["docs"]},
        credentials={"access_token": "mock-token"},
    )

    with pytest.raises(JiuwenValidationError):
        PersonalContextConfig.from_dict(_valid_config(service))


def test_config_normalizes_paths_and_isolated_input():
    original = _local_service()
    config = PersonalContextConfig.from_dict(_valid_config(original))
    original["source"]["root_dir"] = "~/changed"

    assert config.fetch_services[0].source["root_dir"] == str(Path("~/notes").expanduser().resolve())


def test_source_urls_reject_query_and_fragment_without_leaking_credentials():
    service = _local_service("zhihu")
    service.update(
        provider="zhihu_reader",
        source={"column_url": "https://www.zhihu.com/column/example?token=mock-secret#frag"},
        credentials={},
    )

    with pytest.raises(JiuwenValidationError) as caught:
        PersonalContextConfig.from_dict(_valid_config(service))

    error = caught.value
    assert "mock-secret" not in str(error)
    assert "mock-secret" not in repr(error)
    assert "mock-secret" not in error.to_json()
    assert error.__cause__ is None or "mock-secret" not in str(error.__cause__)
    assert error.__context__ is None


def test_sensitive_source_path_is_rejected():
    service = _local_service("system32")
    service["source"]["root_dir"] = r"C:\Windows\System32"

    with pytest.raises(JiuwenValidationError):
        PersonalContextConfig.from_dict(_valid_config(service))


def test_sensitive_windows_path_is_rejected_case_insensitively():
    service = _local_service("system32-lower")
    service["source"]["root_dir"] = r"c:\windows\system32"

    with pytest.raises(JiuwenValidationError):
        PersonalContextConfig.from_dict(_valid_config(service))


@pytest.mark.parametrize(
    "column_url",
    [
        "https://www.zhihu.com:443/column/example",
        "https://www.zhihu.com/column/example/extra",
        "https://www.zhihu.com/column/",
    ],
)
def test_zhihu_url_shape_rejects_ports_extra_segments_and_empty_ids(column_url: str):
    service = _local_service("zhihu-shape")
    service.update(provider="zhihu_reader", source={"column_url": column_url}, credentials={})

    with pytest.raises(JiuwenValidationError):
        PersonalContextConfig.from_dict(_valid_config(service))


@pytest.mark.parametrize(
    "profile_url",
    [
        "https://www.toutiao.com:443/c/user/token/example",
        "https://www.toutiao.com/robots.txt",
        "https://www.toutiao.com/c/user/token/",
    ],
)
def test_toutiao_url_shape_requires_public_profile_homepage(profile_url: str):
    service = _local_service("toutiao-shape")
    service.update(provider="toutiao_reader", source={"profile_url": profile_url}, credentials={})

    with pytest.raises(JiuwenValidationError):
        PersonalContextConfig.from_dict(_valid_config(service))


def test_toutiao_profile_url_preserves_trailing_slash_for_public_endpoint():
    service = _local_service("toutiao-slash")
    service.update(
        provider="toutiao_reader",
        source={"profile_url": "https://www.toutiao.com/c/user/token/example/"},
        credentials={},
    )

    config = PersonalContextConfig.from_dict(_valid_config(service))

    assert config.fetch_services[0].source["profile_url"].endswith("/")


def test_model_configuration_is_pairwise_and_required_for_non_rules_profiles():
    with pytest.raises(JiuwenValidationError):
        PersonalContextConfig.from_dict(
            {**_valid_config(strategy_profile="balanced"), "model_client": None, "model_request": {"model": "gpt"}}
        )

    with pytest.raises(JiuwenValidationError):
        PersonalContextConfig.from_dict(
            {
                **_valid_config(strategy_profile="agent"),
                "model_client": {"client_provider": "OpenAI", "api_key": "mock", "api_base": "https://example.com"},
                "model_request": None,
            }
        )


@pytest.mark.parametrize("calendar_field", ["start", "end"])
def test_feishu_account_calendar_fields_require_calendar_resource(calendar_field: str):
    source = {"mode": "account", "resources": ["docs"], calendar_field: "2026-01-01"}
    service = _local_service("feishu")
    service.update(provider="feishu", source=source, credentials={"access_token": "mock"})

    with pytest.raises(JiuwenValidationError):
        PersonalContextConfig.from_dict(_valid_config(service))
