# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""验证目标配置契约，不调用 Runtime、模型后端或构建引擎。"""

from dataclasses import asdict
import json
from pathlib import Path
import re

import pytest
from pydantic import ValidationError

from openjiuwen.symphony import config as target


MINIMAL = {
    "capability": {"sources": [{"type": "skill", "root": "./capabilities"}]},
    "storage": {"artifact_root": "./.symphony"},
}


def _get(instance, path):
    for part in path.split("."):
        instance = getattr(instance, part)
    return list(instance) if isinstance(instance, tuple) else instance






def test_minimal_config_has_independent_enabled_features_and_expected_defaults():
    config = target.SymphonyConfig.model_validate(MINIMAL)
    assert config.capability.sources[0].root == "./capabilities"
    assert config.capability.sources[0].type == "skill"
    assert config.storage.artifact_root == "./.symphony"
    assert config.retrieval.enabled and config.orchestration.enabled
    assert config.retrieval.index_build.structure.max_depth == 6
    assert config.retrieval.index_build.structure.branching_factor == 128
    assert config.retrieval.index_build.structure.max_capabilities_per_node_multiplier == 1.5
    assert config.retrieval.index_build.request.max_concurrency == 2
    assert config.retrieval.index_build.request.timeout_seconds == 420.0
    assert config.retrieval.index_build.request.classification_batch_limit == 32
    assert config.retrieval.output.max_results == 10
    assert config.orchestration.min_edge_confidence == 0.7
    assert not config.capability.fingerprint.extraction.llm_enabled
    assert config.capability.fingerprint.extraction.max_concurrency == 4
    assert config.orchestration.build.max_concurrency == 1
    assert config.orchestration.build.matching.require_consensus
    assert config.orchestration.plan.mode == "fast"
    assert config.orchestration.plan.top_k == 3
    assert config.orchestration.plan.max_depth == 4
    assert not config.orchestration.experience_enabled


@pytest.mark.parametrize("capability_type", ["skill", "expert", "connector", "mcp"])
def test_supported_types_do_not_imply_runtime_loading(capability_type):
    config = target.SymphonyConfig.model_validate({
        "capability": {"sources": [{"type": capability_type, "root": "missing-directory"}]},
        "storage": {"artifact_root": "missing-artifacts"},
        "orchestration": {"enabled": False},
    })
    assert config.capability.sources[0].type == capability_type
    assert config.retrieval.enabled
    assert not config.orchestration.enabled


def test_mixed_capability_sources_round_trip_in_input_order():
    sources = [
        {"type": "skill", "root": "./skills"},
        {"type": "skill", "root": "./shared-skills"},
        {"type": "expert", "root": "./experts"},
    ]
    config = target.SymphonyConfig.model_validate({**MINIMAL, "capability": {"sources": sources}})
    assert config.model_dump(mode="json")["capability"]["sources"] == sources
    assert target.SymphonyConfig.model_validate_json(config.model_dump_json()) == config


def test_old_capability_fields_are_rejected_as_unknown():
    with pytest.raises(ValidationError) as error:
        target.SymphonyConfig.model_validate({**MINIMAL, "capability": {"root": "./skills", "type": "skill"}})
    assert {(item["loc"], item["type"]) for item in error.value.errors()} == {
        (("capability", "sources"), "missing"),
        (("capability", "root"), "extra_forbidden"),
        (("capability", "type"), "extra_forbidden"),
    }


@pytest.mark.parametrize("value", [0, -1, True, 2.5, "10", None])
def test_invalid_nested_value_reports_full_field_path(value):
    with pytest.raises(ValidationError) as error:
        target.SymphonyConfig.model_validate({
            **MINIMAL, "retrieval": {"output": {"max_results": value}},
        })
    assert error.value.errors()[0]["loc"] == ("retrieval", "output", "max_results")


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan"), float("inf"), True])
def test_confidence_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        target.SymphonyConfig.model_validate({**MINIMAL, "orchestration": {"min_edge_confidence": value}})


@pytest.mark.parametrize("value", [None, 1024])
def test_fingerprint_output_budget_supports_injected_model_default(value):
    config = target.SymphonyConfig.model_validate({
        **MINIMAL,
        "capability": {**MINIMAL["capability"], "fingerprint": {"extraction": {"max_output_tokens": value}}},
    })
    assert config.capability.fingerprint.extraction.max_output_tokens == value
    assert target.SymphonyConfig.model_validate_json(config.model_dump_json()) == config
    assert target.FingerprintExtractionConfig().max_output_tokens == 2048


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "2048"])
def test_fingerprint_output_budget_rejects_invalid_values(value):
    payload = {
        **MINIMAL,
        "capability": {**MINIMAL["capability"], "fingerprint": {"extraction": {"max_output_tokens": value}}},
    }
    for validate, data in (
        (target.SymphonyConfig.model_validate, payload),
        (target.SymphonyConfig.model_validate_json, json.dumps(payload)),
    ):
        with pytest.raises(ValidationError) as error:
            validate(data)
        assert error.value.errors()[0]["loc"] == ("capability", "fingerprint", "extraction", "max_output_tokens")


@pytest.mark.parametrize("payload, location", [
    ({"retrieval": {"output": {"max_result": 3}}}, ("retrieval", "output", "max_result")),
    ({"retrieval": None}, ("retrieval",)),
    ({"retrieval": {"enabled": "false"}}, ("retrieval", "enabled")),
    ({"orchestration": {"plan": {"mode": "unknown"}}}, ("orchestration", "plan", "mode")),
    ({"capability": {"sources": None}}, ("capability", "sources")),
    ({"capability": {"sources": []}}, ("capability", "sources")),
    ({"capability": {"sources": [{"type": "tool", "root": "x"}]}}, ("capability", "sources", 0, "type")),
    ({"capability": {"sources": [{"type": "skill", "root": "  "}]}}, ("capability", "sources", 0, "root")),
    ({"capability": {"sources": [{"root": "x"}]}}, ("capability", "sources", 0, "type")),
    ({"capability": {"sources": [{"type": "skill"}]}}, ("capability", "sources", 0, "root")),
    ({"storage": {"artifact_root": "  "}}, ("storage", "artifact_root")),
])
def test_unknown_fields_and_invalid_groups_are_rejected(payload, location):
    with pytest.raises(ValidationError) as error:
        target.SymphonyConfig.model_validate({**MINIMAL, **payload})
    assert error.value.errors()[0]["loc"] == location


def test_disabled_groups_are_still_validated_and_both_can_be_disabled():
    with pytest.raises(ValidationError):
        target.SymphonyConfig.model_validate({
            **MINIMAL, "retrieval": {"enabled": False, "index_build": {"request": {"timeout_seconds": 0}}},
        })
    config = target.SymphonyConfig.model_validate({
        **MINIMAL, "retrieval": {"enabled": False}, "orchestration": {"enabled": False},
    })
    assert not config.retrieval.enabled and not config.orchestration.enabled


def test_configuration_does_not_resolve_paths_or_read_environment(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("配置构造不应访问文件系统")

    for name in ("resolve", "exists", "is_dir", "mkdir", "read_text"):
        monkeypatch.setattr(Path, name, unexpected)
    monkeypatch.setenv("SYMPHONY_DISCOVERY_MAX_RESULTS", "999")
    config = target.SymphonyConfig.model_validate({
        "capability": {"sources": [{"type": "expert", "root": Path("missing/../capabilities")}]},
        "storage": {"artifact_root": Path("./artifacts")},
    })
    assert config.capability.sources[0].root == "missing/../capabilities"
    assert config.storage.artifact_root == "artifacts"
    assert config.retrieval.output.max_results == 10
    assert target.SymphonyConfig.model_validate_json(config.model_dump_json()) == config


def test_root_category_path_is_serialized_without_loading(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("根分类路径不应在配置阶段读取")

    monkeypatch.setattr(Path, "read_text", unexpected)
    config = target.SymphonyConfig.model_validate({
        **MINIMAL,
        "retrieval": {"index_build": {
            "structure": {"root_categories": Path("./categories.yaml")},
            "output": {"display_capability_root": Path("./display")},
        }},
    })
    assert config.retrieval.index_build.structure.root_categories == "categories.yaml"
    assert config.retrieval.index_build.output.display_capability_root == "display"
    assert config.capability.sources[0].root == "./capabilities"
    assert target.SymphonyConfig.model_validate_json(config.model_dump_json()) == config


def _example(model):
    examples = {
        target.CapabilityConfig: MINIMAL["capability"],
        target.CapabilitySourceConfig: MINIMAL["capability"]["sources"][0],
        target.StorageConfig: {"artifact_root": "./artifacts"},
        target.SymphonyConfig: MINIMAL,
    }
    return model.model_validate(examples.get(model, {}))


@pytest.mark.parametrize("model", target.CONFIG_MODELS, ids=lambda model: model.__name__)
def test_all_config_models_round_trip_through_json_and_dict(model):
    instance = _example(model)
    assert model.model_validate_json(instance.model_dump_json()) == instance
    assert model.model_validate(instance.model_dump(mode="json")) == instance


@pytest.mark.parametrize("model", target.CONFIG_MODELS, ids=lambda model: model.__name__)
def test_all_config_models_export_chinese_field_documentation(model):
    schema = model.model_json_schema()
    json.dumps(schema, ensure_ascii=False, allow_nan=False)
    assert re.search(r"[\u4e00-\u9fff]", schema["description"])
    for name, info in model.model_fields.items():
        assert re.search(r"[\u4e00-\u9fff]", info.title), (model.__name__, name)
        assert re.search(r"[\u4e00-\u9fff]", info.description), (model.__name__, name)
        assert "skill" not in name.lower(), (model.__name__, name)
        assert schema["properties"][name]["description"] == info.description


def test_schema_preserves_bounds_enum_and_examples():
    schema = target.SymphonyConfig.model_json_schema()
    assert schema["required"] == ["capability", "storage"]
    output = schema["$defs"]["DiscoveryOutputConfig"]["properties"]["max_results"]
    assert output["default"] == 10 and output["exclusiveMinimum"] == 0
    capacity = schema["$defs"]["RetrievalTreeStructureConfig"]["properties"]["max_capabilities_per_node_multiplier"]
    assert capacity["default"] == 1.5 and capacity["exclusiveMinimum"] == 0
    assert capacity["type"] == "number"
    sources = schema["$defs"]["CapabilityConfig"]["properties"]["sources"]
    assert sources["minItems"] == 1 and sources["items"]["$ref"] == "#/$defs/CapabilitySourceConfig"
    assert schema["$defs"]["CapabilityConfig"]["required"] == ["sources"]
    source = schema["$defs"]["CapabilitySourceConfig"]
    assert source["properties"]["root"]["examples"] == ["./capabilities"]
    assert source["properties"]["type"]["enum"] == [
        "skill", "expert", "connector", "mcp",
    ]
    assert source["required"] == ["type", "root"]
    assert schema["$defs"]["StorageConfig"]["required"] == ["artifact_root"]
    for model in target.PUBLIC_CONFIG_MODELS:
        if model not in (target.SymphonyConfig, target.CapabilityConfig, target.CapabilitySourceConfig, target.StorageConfig):
            assert not any(info.is_required() for info in model.model_fields.values()), model.__name__


def test_model_registry_covers_all_concrete_models():
    classes = {
        value for value in vars(target).values()
        if isinstance(value, type) and issubclass(value, target.SymphonyConfigModel)
        and value is not target.SymphonyConfigModel
    }
    assert set(target.CONFIG_MODELS) == classes
    assert len(target.CONFIG_MODELS) == len(classes)
    assert not set(target.PUBLIC_CONFIG_MODELS) & set(target.INTERNAL_CONFIG_MODELS)
    assert set(target.CONFIG_MODELS) == set(target.PUBLIC_CONFIG_MODELS) | set(target.INTERNAL_CONFIG_MODELS)


def test_legacy_mirrors_and_resolved_snapshots_are_not_target_configuration():
    removed = {
        "FingerprintConfig", "GraphPlanningConfig", "GraphMatchingConfig", "CapabilityIndexBuildConfig",
        "TreeBuildSettings", "ResolvedIndexTreeStructureConfig", "ResolvedIndexBuildConfig", "RuntimeRetrieverConfig",
        "TreeRequestConfig", "TreeStructureConfig", "IndexTreeStructureConfig", "TreeBuildConfig",
        "IndexTreeBuildConfig", "TreeManagerConfig", "TreePromptConfig", "IndexBuildConfig", "IndexArtifactConfig",
        "TreeBuildOptions", "ProgressiveRetrieverConfig", "RetrieverConfig", "DisclosureConfig",
        "DiscoveryConfig", "DiscoveryBudgetConfig", "RetrievalGenerationConfig",
        "DynamicTreeConfig", "TreeExpansionConfig",
    }
    assert not removed & set(vars(target))
    assert not removed & {model.__name__ for model in target.CONFIG_MODELS}
    assert not removed & set(target.SymphonyConfig.model_json_schema()["$defs"])


def test_public_exports_are_limited_to_the_runtime_configuration_tree():
    expected = {
        "CapabilityType", "CapabilityConfig", "StorageConfig", "RetrievalBuildConfig",
        "RetrievalConfig", "OrchestrationBuildConfig", "OrchestrationPlanConfig", "OrchestrationConfig", "SymphonyConfig",
        "OrchestrationExperienceConfig",
    }
    assert set(target.__all__) == expected
    public_names = {model.__name__ for model in target.PUBLIC_CONFIG_MODELS}
    assert expected - {"CapabilityType"} <= public_names
    assert all(hasattr(target, name) for name in target.__all__)
    public_schema = target.SymphonyConfig.model_json_schema()
    # JsonValue 是根分类数据的递归类型，不是额外配置模型。
    definitions = {name for name, schema in public_schema["$defs"].items() if "properties" in schema}
    assert definitions | {"SymphonyConfig"} == public_names
    assert not {model.__name__ for model in target.INTERNAL_CONFIG_MODELS} & definitions


def test_default_containers_are_not_shared_and_public_assignment_is_frozen():
    first, second = target.DiscoveryPromptConfig(), target.DiscoveryPromptConfig()
    first.preferred_capability_ids.append("extra")
    assert "extra" not in second.preferred_capability_ids
    with pytest.raises(ValidationError) as error:
        target.SymphonyConfig.model_validate(MINIMAL).retrieval.output.max_results = 2
    assert error.value.errors()[0]["type"] == "frozen_instance"
    structure = target.SymphonyConfig.model_validate(MINIMAL).retrieval.index_build.structure
    with pytest.raises(ValidationError) as error:
        structure.max_capabilities_per_node_multiplier = 2.0
    assert error.value.errors()[0]["type"] == "frozen_instance"


def test_runtime_dependencies_are_not_configuration_fields():
    with pytest.raises(ValidationError):
        target.SymphonyConfig.model_validate({**MINIMAL, "model": object()})




def test_public_groups_preserve_existing_module_defaults():
    config = target.SymphonyConfig.model_validate(MINIMAL)
    assert config.capability.scan == target.DirectoryScanConfig()
    assert config.retrieval == target.RetrievalConfig()
    assert config.orchestration.experience == target.OrchestrationExperienceConfig()
    assert config.orchestration.build.matching.model_dump() == {
        "batch_size": 12, "require_consensus": True, "max_candidates_per_capability_relation": 32,
        "max_port_mappings_per_candidate": 12, "max_exact_io_pair_fanout": 64,
    }


def test_partial_overrides_reach_all_public_groups_and_round_trip():
    config = target.SymphonyConfig.model_validate({
        **MINIMAL,
        "capability": {
            **MINIMAL["capability"],
            "scan": {"max_depth": 3, "limits": {"max_entrypoints": 200}, "manifest": {"max_frontmatter_depth": 8}},
            "fingerprint": {
                "extraction": {"llm_enabled": True, "max_concurrency": 2,
                               "max_body_chars": 8000, "max_output_tokens": 512},
                "evaluation": {"llm_enabled": True, "max_evidence_chars": 256}, "cache": {"enabled": False},
            },
        },
        "retrieval": {
            "index_build": {
                "structure": {"branching_factor": 16, "max_capabilities_per_node_multiplier": 2.0,
                              "root_categories": ["analysis", {"name": "write", "rank": 2}]},
                "request": {"max_concurrency": 3, "timeout_seconds": 60.0, "context_window_tokens": 32768},
                "postprocess": {"enabled": False}, "equivalence": {"enabled": True},
                "profiles": {"enabled": True, "max_description_chars": 160, "max_rule_chars": 200},
                "prompt": {"discovery_seed": 7},
                "incremental": {"max_change_ratio": 0.1},
                "task": {"allow_fallback_tree": False},
                "output": {"generate_tree_html": True, "verbose": True, "show_tree": False,
                           "display_capability_root": "./display", "cache_metrics_enabled": False},
            },
            "prompt": {"candidate_budget_ratio": 0.02, "preferred_capability_ids": ["expert-a"]},
        },
        "orchestration": {
            "build": {"matching": {"require_consensus": False}},
            "plan": {"mode": "beam"},
            "experience": {"enabled": True, "acceptance": {"min_successes": 2}, "narrative": {"max_examples": 3}},
        },
    })
    assert config.capability.scan.limits.max_entrypoints == 200
    assert config.capability.scan.manifest.max_frontmatter_depth == 8
    assert config.capability.fingerprint.extraction.llm_enabled
    assert config.capability.fingerprint.extraction.batch_size == 16
    assert config.capability.fingerprint.extraction.max_body_chars == 8000
    assert config.capability.fingerprint.extraction.max_output_tokens == 512
    assert config.capability.fingerprint.evaluation.max_evidence_chars == 256
    assert config.capability.fingerprint.evaluation.llm_enabled and not config.capability.fingerprint.cache.enabled
    assert config.retrieval.index_build.structure.root_categories == ["analysis", {"name": "write", "rank": 2}]
    assert config.retrieval.index_build.structure.max_capabilities_per_node_multiplier == 2.0
    assert config.retrieval.index_build.structure.max_depth == 6
    assert config.retrieval.index_build.request.max_concurrency == 3 and config.retrieval.index_build.request.max_retries == 2
    assert config.retrieval.index_build.request.context_window_tokens == 32768
    assert not config.retrieval.index_build.postprocess.enabled and config.retrieval.index_build.equivalence.enabled
    assert config.retrieval.index_build.profiles.enabled and config.retrieval.index_build.prompt.discovery_seed == 7
    assert config.retrieval.index_build.profiles.max_description_chars == 160
    assert config.retrieval.index_build.profiles.max_rule_chars == 200
    assert config.retrieval.index_build.incremental.max_change_ratio == 0.1
    assert config.retrieval.index_build.output.generate_tree_html
    assert not config.retrieval.index_build.task.allow_fallback_tree
    assert config.retrieval.index_build.output.verbose and not config.retrieval.index_build.output.show_tree
    assert config.retrieval.index_build.output.display_capability_root == "./display"
    assert not config.retrieval.index_build.output.cache_metrics_enabled
    assert config.retrieval.prompt.candidate_budget_tokens == 4000
    assert config.retrieval.prompt.preferred_capability_ids == ["expert-a"]
    assert not config.orchestration.build.matching.require_consensus and config.orchestration.plan.mode == "beam"
    experience = config.orchestration.experience
    assert config.orchestration.experience_enabled
    assert experience.acceptance.min_successes == 2 and experience.narrative.max_examples == 3
    assert target.SymphonyConfig.model_validate_json(config.model_dump_json()) == config
    assert target.SymphonyConfig.model_validate(config.model_dump(mode="json")) == config


@pytest.mark.parametrize("path, value", [
    ("retrieval.index_build.max_depth", 3),
    ("retrieval.index_build.branching_factor", 16),
    ("retrieval.index_build.max_concurrency", 2),
    ("retrieval.index_build.request_timeout_seconds", 60.0),
    ("orchestration.build.extraction_mode", "llm"),
    ("orchestration.build.require_consensus", False),
    ("orchestration.build.matching.max_concurrency", 2),
    ("orchestration.plan.min_edge_confidence", 0.8),
    ("capability.fingerprint.protocol", {"schema_version": "2.0"}),
    ("retrieval.index_build.prompt.fingerprint_version", "v2"),
    ("retrieval.index_build.manager", {}),
    ("retrieval.index_build.build", {}),
    ("retrieval.index_build.artifacts", {"allow_fallback_tree": False}),
    ("retrieval.index_build.model", {"model": "example"}),
    ("retrieval.index_build.method", 4),
    ("retrieval.index_build.task.strict_failure", False),
    ("capability.fingerprint.extraction.body_limit", 16000),
    ("capability.fingerprint.extraction.max_tokens", 2048),
    ("capability.fingerprint.evaluation.evidence_text_limit", 512),
    ("retrieval.index_build.profiles.description_limit", 140),
    ("retrieval.index_build.profiles.rule_limit", 120),
    ("retrieval.index_build.request.context_window", 0),
    ("retrieval.index_build.prompt.cache_observability", True),
    ("retrieval.index_build.output.cache_observability", True),
    ("retrieval.budget", {"candidate_budget_ratio": 0.01}),
    ("retrieval.prompt.candidate_budget_tokens", 2000),
])
def test_duplicate_or_internal_input_paths_are_rejected(path, value):
    payload = json.loads(json.dumps(MINIMAL))
    cursor = payload
    parts = path.split(".")
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value
    for validate, data in (
        (target.SymphonyConfig.model_validate, payload),
        (target.SymphonyConfig.model_validate_json, json.dumps(payload)),
    ):
        with pytest.raises(ValidationError) as error:
            validate(data)
        assert error.value.errors()[0]["loc"] == tuple(parts)
        assert error.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize("payload, location", [
    ({"retrieval": {"index_build": {"structure": {"branching_factor": 1}}}}, ("retrieval", "index_build", "structure", "branching_factor")),
    ({"orchestration": {"experience": {"acceptance": {"min_pack_success_rate": -0.1}}}},
     ("orchestration", "experience", "acceptance", "min_pack_success_rate")),
    ({"retrieval": {"index_build": {"structure": {"max_capabilities_per_node_multiplier": 0}}}},
     ("retrieval", "index_build", "structure", "max_capabilities_per_node_multiplier")),
    ({"retrieval": {"index_build": {"task": {"allow_fallback_tree": "false"}}}},
     ("retrieval", "index_build", "task", "allow_fallback_tree")),
    ({"retrieval": {"index_build": {"output": {"display_capability_root": " "}}}},
     ("retrieval", "index_build", "output", "display_capability_root")),
])
def test_merged_configuration_errors_keep_complete_paths(payload, location):
    with pytest.raises(ValidationError) as error:
        target.SymphonyConfig.model_validate({**MINIMAL, **payload})
    assert error.value.errors()[0]["loc"] == location


def test_nested_public_defaults_are_independent_between_instances():
    first = target.SymphonyConfig.model_validate(MINIMAL)
    second = target.SymphonyConfig.model_validate(MINIMAL)
    first.retrieval.prompt.preferred_capability_ids.append("expert-extra")
    assert "expert-extra" not in second.retrieval.prompt.preferred_capability_ids
    assert first.retrieval.index_build.structure is not second.retrieval.index_build.structure
    assert first.retrieval.index_build.request is not second.retrieval.index_build.request
    assert second.retrieval.index_build.structure.max_capabilities_per_node_multiplier == 1.5

    payload = {**MINIMAL, "retrieval": {"index_build": {"structure": {"root_categories": [{"name": "analysis"}]}}}}
    first = target.SymphonyConfig.model_validate(payload)
    second = target.SymphonyConfig.model_validate(payload)
    first.retrieval.index_build.structure.root_categories[0]["name"] = "changed"
    assert second.retrieval.index_build.structure.root_categories == [{"name": "analysis"}]
    assert payload["retrieval"]["index_build"]["structure"]["root_categories"] == [{"name": "analysis"}]


def test_index_task_policy_and_output_have_separate_serialized_paths():
    config = target.RetrievalBuildConfig.model_validate({
        "task": {"total_timeout_seconds": 60.0, "preserve_previous_index_on_failure": False,
                 "allow_fallback_tree": False},
        "output": {"generate_tree_html": True, "verbose": True, "show_tree": False,
                   "display_capability_root": "./display"},
    })
    payload = config.model_dump(mode="json")
    assert payload["task"] == {
        "total_timeout_seconds": 60.0, "preserve_previous_index_on_failure": False,
        "allow_fallback_tree": False,
    }
    assert payload["output"] == {
        "generate_tree_html": True, "verbose": True, "show_tree": False, "display_capability_root": "./display",
        "cache_metrics_enabled": True,
    }
    assert target.RetrievalBuildConfig.model_validate_json(config.model_dump_json()) == config


@pytest.mark.parametrize("allow_fallback_tree", [True, False])
@pytest.mark.parametrize("preserve_previous_index_on_failure", [True, False])
def test_fallback_and_previous_index_preservation_are_independent_policies(
    allow_fallback_tree, preserve_previous_index_on_failure,
):
    task = target.IndexTaskConfig(
        allow_fallback_tree=allow_fallback_tree,
        preserve_previous_index_on_failure=preserve_previous_index_on_failure,
    )
    assert task.model_dump() == {
        "total_timeout_seconds": 0.0,
        "preserve_previous_index_on_failure": preserve_previous_index_on_failure,
        "allow_fallback_tree": allow_fallback_tree,
    }
    assert target.IndexTaskConfig.model_validate_json(task.model_dump_json()) == task
    assert "strict_failure" not in target.IndexTaskConfig.model_json_schema()["properties"]


@pytest.mark.parametrize("field", ["preserve_previous_index_on_failure", "strict_failure", "allow_fallback_tree"])
def test_index_failure_policy_rejects_the_previous_output_path(field):
    with pytest.raises(ValidationError) as error:
        target.RetrievalBuildConfig.model_validate({"output": {field: False}})
    assert error.value.errors()[0]["loc"] == ("output", field)








@pytest.mark.parametrize("fingerprint_cache_enabled", [True, False])
@pytest.mark.parametrize("cache_metrics_enabled", [True, False])
def test_cache_metrics_and_fingerprint_cache_keep_independent_configuration(
    fingerprint_cache_enabled, cache_metrics_enabled,
):
    config = target.SymphonyConfig.model_validate({
        **MINIMAL,
        "capability": {**MINIMAL["capability"], "fingerprint": {"cache": {"enabled": fingerprint_cache_enabled}}},
        "retrieval": {"index_build": {"output": {"cache_metrics_enabled": cache_metrics_enabled}}},
    })
    assert config.capability.fingerprint.cache.enabled is fingerprint_cache_enabled
    assert config.retrieval.index_build.output.cache_metrics_enabled is cache_metrics_enabled
    assert "cache_observability" not in config.retrieval.index_build.prompt.model_dump()
    assert "caching" not in config.retrieval.index_build.request.model_dump()
    assert "caching" not in target.IndexRequestConfig.model_json_schema()["properties"]
    assert target.SymphonyConfig.model_validate_json(config.model_dump_json()) == config


@pytest.mark.parametrize("ratio, context_tokens, expected", [(0.02, 50000, 1000), (0.333, 10, 3), (0.0001, 1, 1)])
def test_discovery_prompt_budget_keeps_derivation_and_default_priority_list(ratio, context_tokens, expected):
    config = target.SymphonyConfig.model_validate({
        **MINIMAL, "retrieval": {"prompt": {
            "candidate_budget_ratio": ratio, "context_window_tokens": context_tokens,
        }},
    })
    prompt = config.retrieval.prompt
    assert prompt.candidate_budget_tokens == expected
    assert prompt.preferred_capability_ids == ["skill-creator", "swarmskill-creator", "symphony-assistant"]
    assert "candidate_budget_tokens" not in prompt.model_dump()
    assert "candidate_budget_tokens" not in prompt.model_json_schema()["properties"]
    assert target.SymphonyConfig.model_validate_json(config.model_dump_json()) == config


@pytest.mark.parametrize("field, value", [
    ("candidate_budget_ratio", 0), ("candidate_budget_ratio", 1.01),
    ("context_window_tokens", 0), ("context_window_tokens", True),
])
def test_discovery_budget_errors_are_located_under_prompt(field, value):
    with pytest.raises(ValidationError) as error:
        target.SymphonyConfig.model_validate({**MINIMAL, "retrieval": {"prompt": {field: value}}})
    assert error.value.errors()[0]["loc"] == ("retrieval", "prompt", field)


def test_public_structure_and_output_defaults_preserve_source_values_without_runtime_settings():
    config = target.SymphonyConfig.model_validate(MINIMAL).retrieval.index_build
    assert config.structure.model_dump() == {
        "branching_factor": 128,
        "max_depth": 6,
        "root_categories": None,
        "max_capabilities_per_node_multiplier": 1.5,
    }
    assert config.output.model_dump() == {
        "generate_tree_html": False, "verbose": False, "show_tree": True, "display_capability_root": None,
        "cache_metrics_enabled": True,
    }
    assert "dynamic" not in config.model_dump()
    assert "dynamic" not in target.RetrievalBuildConfig.model_json_schema()["properties"]
    assert not hasattr(target, "TreeBuildMethod") and not hasattr(target, "_strict_integer")


@pytest.mark.parametrize("value", [0, -0.1, True, "1.5", float("nan"), float("inf")])
def test_node_capacity_multiplier_preserves_positive_numeric_constraints(value):
    field = "max_capabilities_per_node_multiplier"
    payload = {**MINIMAL, "retrieval": {"index_build": {"structure": {field: value}}}}
    for validate, data in (
        (target.SymphonyConfig.model_validate, payload),
        (target.SymphonyConfig.model_validate_json, json.dumps(payload)),
    ):
        with pytest.raises(ValidationError) as error:
            validate(data)
        assert error.value.errors()[0]["loc"] == ("retrieval", "index_build", "structure", field)


def test_independent_derived_configuration_values_and_explicit_overrides():
    assert target.DiscoveryPromptConfig().candidate_budget_tokens == 2000


def test_existing_package_exports_continue_to_use_the_existing_configuration():
    import openjiuwen.symphony as symphony

    assert symphony.SymphonyConfig is target.SymphonyConfig
    assert "SymphonyConfig" in symphony.__all__


@pytest.mark.parametrize("payload, location", [
    ({"retrieval": {"search": {}}}, ("retrieval", "search")),
    ({"retrieval": {"toolkit": {}}}, ("retrieval", "toolkit")),
    ({"retrieval": {"build": {}}}, ("retrieval", "build")),
    ({"retrieval": {"discovery": {}}}, ("retrieval", "discovery")),
    ({"experience": {}}, ("experience",)),
    ({"retrieval": {"index_build": {"dynamic": {}}}}, ("retrieval", "index_build", "dynamic")),
    ({"retrieval": {"index_build": {"dynamic": {"rebalance_interval": 50, "expansion": {
        "max_capabilities_per_node_multiplier": 1.5, "expand_threshold_multiplier": 0.7,
        "early_stop_multiplier": 1.7, "lazy_split_multiplier": 1.3,
        "classification_batch_multiplier": 6.0, "structure_sample_multiplier": 12.0,
    }}}}}, ("retrieval", "index_build", "dynamic")),
    ({"retrieval": {"index_build": {"request": {"caching": False}}}}, ("retrieval", "index_build", "request", "caching")),
    ({"retrieval": {"index_build": {"request": {"caching": True}}}}, ("retrieval", "index_build", "request", "caching")),
])
def test_retired_capabilities_are_rejected_without_compatibility_aliases(payload, location):
    payload = {**MINIMAL, **payload}
    for validate, data in (
        (target.SymphonyConfig.model_validate, payload),
        (target.SymphonyConfig.model_validate_json, json.dumps(payload)),
    ):
        with pytest.raises(ValidationError) as error:
            validate(data)
        assert error.value.errors()[0]["loc"] == location
        assert error.value.errors()[0]["type"] == "extra_forbidden"


def test_registry_contains_only_retained_capabilities():
    assert len(target.PUBLIC_CONFIG_MODELS) == 31
    assert len(target.INTERNAL_CONFIG_MODELS) == 2
    assert len(target.CONFIG_MODELS) == 33
    assert {m.__name__ for m in target.INTERNAL_CONFIG_MODELS} == {
        "FingerprintProtocolConfig", "OneShotTreeBuildConfig",
    }
    for name in (
        "_DEFAULT_COMPACT_CODES", "LLMClientConfig", "RetrievalSearchConfig", "AgenticRetrievalConfig",
        "ExperienceConfig", "CapabilityIndexStorageConfig", "ScoreTruncationConfig",
    ):
        assert not hasattr(target, name)


def test_all_public_defaults_match_the_committed_pre_integration_contract():
    # 独立基线来自 dcdcef3ef 的公共配置，不能用运行中的消费者反向生成期望值。
    expected = json.loads((Path(__file__).parent / "fixtures" / "config_defaults_dcdcef3ef.json").read_text())
    retrieval = expected["retrieval"]
    retrieval["index_build"] = retrieval.pop("build")
    retrieval.update(retrieval.pop("discovery"))
    capability = expected["capability"]
    capability["sources"] = [{"type": capability.pop("type"), "root": capability.pop("root")}]
    experience = expected.pop("flow")
    experience["enabled"] = False
    experience["acceptance"]["min_successes"] = 3
    expected["orchestration"]["experience"] = experience
    assert target.SymphonyConfig.model_validate(MINIMAL).model_dump(mode="json") == expected


def test_model_connection_configuration_is_owned_by_host():
    assert not hasattr(target, "RetrievalModelConfig")
