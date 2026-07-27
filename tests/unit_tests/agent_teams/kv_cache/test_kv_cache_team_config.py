from unittest.mock import patch

from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.core.foundation.kv_cache import KVCacheAffinityConfig


def test_deep_agent_spec_preserves_kv_cache_affinity_config() -> None:
    spec = DeepAgentSpec(
        kv_cache_affinity_config=KVCacheAffinityConfig(
            enable_kv_cache_affinity=True,
        )
    )

    restored = DeepAgentSpec.model_validate_json(spec.model_dump_json())

    assert restored.kv_cache_affinity_config is not None
    assert restored.kv_cache_affinity_config.enable_kv_cache_affinity is True


def test_deep_agent_spec_forwards_kv_cache_affinity_config() -> None:
    kv_config = KVCacheAffinityConfig(enable_kv_cache_affinity=True)
    spec = DeepAgentSpec(kv_cache_affinity_config=kv_config)

    with patch(
        "openjiuwen.harness.factory.resolve_deep_agent_parts",
        return_value=object(),
    ) as resolve_parts:
        spec.resolve_parts()

    assert resolve_parts.call_args.kwargs["kv_cache_affinity_config"] == kv_config
