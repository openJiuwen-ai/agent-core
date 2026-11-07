# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""验证目标配置契约，不调用 Runtime、模型后端或构建引擎。"""

from dataclasses import asdict
import json
from pathlib import Path
import re

import pytest
from pydantic import ValidationError

from openjiuwen.symphony import config as target
from openjiuwen.symphony.agent.retrieval_toolkit import (
    SkillIndexBuildConfig as CurrentIndexBuildConfig,
)
from openjiuwen.symphony.discovery.config import DiscoverySettings
from openjiuwen.symphony.orchestration.config import (
    OrchestrationConfig as CurrentPlanningConfig,
    SymphonyFlowConfig as CurrentFlowConfig,
)
from openjiuwen.symphony.retrieval.build.tree import schema as current_tree
from openjiuwen.symphony.retrieval.build.workflows.artifacts import BuildConfig as CurrentWorkflowConfig
from openjiuwen.symphony.shared.fingerprint.settings import FingerprintSettings


MINIMAL = {"capability": {"root": "./capabilities", "type": "skill"}, "storage": {"artifact_root": "./.symphony"}}


def _get(instance, path):
    for part in path.split("."):
        instance = getattr(instance, part)
    return list(instance) if isinstance(instance, tuple) else instance


def _assert_defaults(current, planned, mapping, *, excluded=()):
    baseline = asdict(current)
    for name in excluded:
        baseline.pop(name)
    assert set(baseline) == set(mapping), "每个既有纯配置字段都应有明确的目标位置"
    for old_name, new_path in mapping.items():
        expected = baseline[old_name]
        if isinstance(expected, tuple):
            expected = list(expected)
        assert _get(planned, new_path) == expected, (old_name, new_path)


INDEX_MAPPING = {
    "max_depth": "structure.max_depth",
    "branching_factor": "structure.branching_factor",
    "root_categories": "structure.root_categories",
    "max_workers": "build.request.max_concurrency",
    "max_retries": "build.request.max_retries",
    "request_timeout_seconds": "build.request.timeout_seconds",
    "total_timeout_seconds": "task.total_timeout_seconds",
    "classification_batch_limit": "build.request.classification_batch_limit",
    "caching": "build.request.caching",
    "context_window": "build.request.context_window_tokens",
    "max_output_tokens": "build.request.max_output_tokens",
    "discovery_seed": "build.prompt.discovery_seed",
    "postprocess_enabled": "build.postprocess.enabled",
    "postprocess_max_passes": "build.postprocess.max_passes",
    "postprocess_min_skills": "build.postprocess.min_capabilities",
    "equivalence_enabled": "build.equivalence.enabled",
    "equivalence_max_groups_per_parent": "build.equivalence.max_groups_per_parent",
    "equivalence_allow_singleton_groups": "build.equivalence.allow_singleton_groups",
    "equivalence_min_lexical_similarity": "build.equivalence.min_lexical_similarity",
    "deterministic_prompts": "build.prompt.deterministic",
    "prompt_fingerprint_version": "build.prompt.fingerprint_version",
    "cache_observability": "output.cache_metrics_enabled",
    "skill_profiles_enabled": "build.profiles.enabled",
    "skill_profile_select_rules_enabled": "build.profiles.select_rules_enabled",
    "skill_profile_batch_size": "build.profiles.batch_size",
    "skill_profile_description_limit": "build.profiles.max_description_chars",
    "skill_profile_rule_limit": "build.profiles.max_rule_chars",
    "incremental_max_change_ratio": "incremental.max_change_ratio",
    "incremental_min_add_confidence": "incremental.min_add_confidence",
    "incremental_min_add_confidence_margin": "incremental.min_add_confidence_margin",
    "incremental_branch_imbalance_ratio": "incremental.branch_imbalance_ratio",
    "generate_tree_html": "output.generate_tree_html",
    "preserve_previous_index_on_failure": "task.preserve_previous_index_on_failure",
}


def test_minimal_config_has_independent_enabled_features_and_expected_defaults():
    config = target.SymphonyConfig.model_validate(MINIMAL)
    assert config.capability.root == "./capabilities"
    assert config.capability.type == "skill"
    assert config.storage.artifact_root == "./.symphony"
    assert config.retrieval.enabled and config.orchestration.enabled
    assert config.retrieval.build.structure.max_depth == 6
    assert config.retrieval.build.structure.branching_factor == 128
    assert config.retrieval.build.request.max_concurrency == 2
    assert config.retrieval.build.request.timeout_seconds == 420.0
    assert config.retrieval.build.request.classification_batch_limit == 32
    assert config.retrieval.discovery.output.max_results == 10
    assert config.orchestration.min_edge_confidence == 0.7
    assert not config.capability.fingerprint.extraction.llm_enabled
    assert config.capability.fingerprint.extraction.max_concurrency == 4
    assert config.orchestration.build.max_concurrency == 1
    assert config.orchestration.build.matching.require_consensus
    assert config.orchestration.plan.mode == "fast"
    assert config.orchestration.plan.top_k == 3
    assert config.orchestration.plan.max_depth == 4
    assert config.flow.enabled


@pytest.mark.parametrize("capability_type", ["skill", "expert", "connector", "mcp"])
def test_supported_types_do_not_imply_runtime_loading(capability_type):
    config = target.SymphonyConfig.model_validate({
        "capability": {"root": "missing-directory", "type": capability_type},
        "storage": {"artifact_root": "missing-artifacts"},
        "orchestration": {"enabled": False},
    })
    assert config.capability.type == capability_type
    assert config.retrieval.enabled
    assert not config.orchestration.enabled


@pytest.mark.parametrize("value", [0, -1, True, 2.5, "10", None])
def test_invalid_nested_value_reports_full_field_path(value):
    with pytest.raises(ValidationError) as error:
        target.SymphonyConfig.model_validate({
            **MINIMAL, "retrieval": {"discovery": {"output": {"max_results": value}}},
        })
    assert error.value.errors()[0]["loc"] == ("retrieval", "discovery", "output", "max_results")


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
    assert target.FingerprintExtractionConfig().max_output_tokens == FingerprintSettings().llm_max_tokens == 2048


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
    ({"retrieval": {"discovery": {"output": {"max_result": 3}}}}, ("retrieval", "discovery", "output", "max_result")),
    ({"retrieval": None}, ("retrieval",)),
    ({"retrieval": {"enabled": "false"}}, ("retrieval", "enabled")),
    ({"orchestration": {"plan": {"mode": "unknown"}}}, ("orchestration", "plan", "mode")),
    ({"capability": {"root": "x", "type": "tool"}}, ("capability", "type")),
    ({"storage": {"artifact_root": "  "}}, ("storage", "artifact_root")),
])
def test_unknown_fields_and_invalid_groups_are_rejected(payload, location):
    with pytest.raises(ValidationError) as error:
        target.SymphonyConfig.model_validate({**MINIMAL, **payload})
    assert error.value.errors()[0]["loc"] == location


def test_disabled_groups_are_still_validated_and_both_can_be_disabled():
    with pytest.raises(ValidationError):
        target.SymphonyConfig.model_validate({
            **MINIMAL, "retrieval": {"enabled": False, "build": {"request": {"timeout_seconds": 0}}},
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
        "capability": {"root": Path("missing/../capabilities"), "type": "expert"},
        "storage": {"artifact_root": Path("./artifacts")},
    })
    assert config.capability.root == "missing/../capabilities"
    assert config.storage.artifact_root == "artifacts"
    assert config.retrieval.discovery.output.max_results == 10
    assert target.SymphonyConfig.model_validate_json(config.model_dump_json()) == config


def test_root_category_path_is_serialized_without_loading(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("根分类路径不应在配置阶段读取")

    monkeypatch.setattr(Path, "read_text", unexpected)
    config = target.SymphonyConfig.model_validate({
        **MINIMAL,
        "retrieval": {"build": {
            "structure": {"root_categories": Path("./categories.yaml")},
            "output": {"display_capability_root": Path("./display")},
        }},
    })
    assert config.retrieval.build.structure.root_categories == "categories.yaml"
    assert config.retrieval.build.output.display_capability_root == "display"
    assert config.capability.root == "./capabilities"
    assert target.SymphonyConfig.model_validate_json(config.model_dump_json()) == config


def _example(model):
    examples = {
        target.CapabilityConfig: {"root": "./capabilities", "type": "skill"},
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
    assert schema["$defs"]["CapabilityConfig"]["properties"]["root"]["examples"] == ["./capabilities"]
    assert schema["$defs"]["CapabilityConfig"]["properties"]["type"]["enum"] == [
        "skill", "expert", "connector", "mcp",
    ]
    assert schema["$defs"]["CapabilityConfig"]["required"] == ["root", "type"]
    assert schema["$defs"]["StorageConfig"]["required"] == ["artifact_root"]
    for model in target.PUBLIC_CONFIG_MODELS:
        if model not in (target.SymphonyConfig, target.CapabilityConfig, target.StorageConfig):
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
        "DiscoveryBudgetConfig", "RetrievalGenerationConfig",
    }
    assert not removed & set(vars(target))
    assert not removed & {model.__name__ for model in target.CONFIG_MODELS}


def test_public_exports_are_limited_to_the_runtime_configuration_tree():
    expected = {
        "CapabilityType", "CapabilityConfig", "StorageConfig", "RetrievalBuildConfig",
        "RetrievalConfig", "OrchestrationBuildConfig", "OrchestrationPlanConfig", "OrchestrationConfig", "SymphonyConfig",
        "FlowConfig",
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
        target.SymphonyConfig.model_validate(MINIMAL).retrieval.discovery.output.max_results = 2
    assert error.value.errors()[0]["type"] == "frozen_instance"
    mutable = target.SymphonyConfig.model_validate(MINIMAL).retrieval.build.dynamic
    mutable.rebalance_interval = 80
    assert mutable.rebalance_interval == 80
    with pytest.raises(ValidationError):
        mutable.rebalance_interval = 0


def test_runtime_dependencies_are_not_configuration_fields():
    with pytest.raises(ValidationError):
        target.SymphonyConfig.model_validate({**MINIMAL, "model": object()})


def test_public_build_preserves_every_configurable_toolkit_default():
    config = target.SymphonyConfig.model_validate(MINIMAL)
    mapping = {
        old: path.removeprefix("build.") for old, path in INDEX_MAPPING.items()
        if old != "prompt_fingerprint_version"
    }
    _assert_defaults(
        CurrentIndexBuildConfig(), config.retrieval.build, mapping,
        excluded=("prompt_fingerprint_version", "strict_failure"),
    )
    # 旧 strict_failure 反向映射到唯一回退策略，不再保留第二个可写开关。
    assert config.retrieval.build.task.allow_fallback_tree is not CurrentIndexBuildConfig().strict_failure


def test_public_groups_preserve_existing_module_defaults():
    config = target.SymphonyConfig.model_validate(MINIMAL)
    assert config.capability.scan == target.DirectoryScanConfig()
    assert config.retrieval.discovery == target.DiscoveryConfig()
    assert config.flow == target.FlowConfig()
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
            "build": {
                "structure": {"branching_factor": 16, "root_categories": ["analysis", {"name": "write", "rank": 2}]},
                "dynamic": {"rebalance_interval": 80, "expansion": {"early_stop_multiplier": 2.0}},
                "request": {"max_concurrency": 3, "timeout_seconds": 60.0, "context_window_tokens": 32768},
                "postprocess": {"enabled": False}, "equivalence": {"enabled": True},
                "profiles": {"enabled": True, "max_description_chars": 160, "max_rule_chars": 200},
                "prompt": {"discovery_seed": 7},
                "incremental": {"max_change_ratio": 0.1},
                "task": {"allow_fallback_tree": False},
                "output": {"generate_tree_html": True, "verbose": True, "show_tree": False,
                           "display_capability_root": "./display", "cache_metrics_enabled": False},
            },
            "discovery": {"prompt": {"candidate_budget_ratio": 0.02, "preferred_capability_ids": ["expert-a"]}},
        },
        "orchestration": {"build": {"matching": {"require_consensus": False}}, "plan": {"mode": "beam"}},
        "flow": {"enabled": False, "acceptance": {"min_successes": 2}, "narrative": {"max_examples": 3}},
    })
    assert config.capability.scan.limits.max_entrypoints == 200
    assert config.capability.scan.manifest.max_frontmatter_depth == 8
    assert config.capability.fingerprint.extraction.llm_enabled
    assert config.capability.fingerprint.extraction.batch_size == 16
    assert config.capability.fingerprint.extraction.max_body_chars == 8000
    assert config.capability.fingerprint.extraction.max_output_tokens == 512
    assert config.capability.fingerprint.evaluation.max_evidence_chars == 256
    assert config.capability.fingerprint.evaluation.llm_enabled and not config.capability.fingerprint.cache.enabled
    assert config.retrieval.build.structure.root_categories == ["analysis", {"name": "write", "rank": 2}]
    assert config.retrieval.build.dynamic.rebalance_interval == 80
    assert config.retrieval.build.dynamic.expansion.early_stop_multiplier == 2.0
    assert config.retrieval.build.dynamic.expansion.classification_batch_multiplier == 6.0
    assert config.retrieval.build.request.max_concurrency == 3 and config.retrieval.build.request.max_retries == 2
    assert config.retrieval.build.request.context_window_tokens == 32768
    assert not config.retrieval.build.postprocess.enabled and config.retrieval.build.equivalence.enabled
    assert config.retrieval.build.profiles.enabled and config.retrieval.build.prompt.discovery_seed == 7
    assert config.retrieval.build.profiles.max_description_chars == 160
    assert config.retrieval.build.profiles.max_rule_chars == 200
    assert config.retrieval.build.incremental.max_change_ratio == 0.1
    assert config.retrieval.build.output.generate_tree_html
    assert not config.retrieval.build.task.allow_fallback_tree
    assert config.retrieval.build.output.verbose and not config.retrieval.build.output.show_tree
    assert config.retrieval.build.output.display_capability_root == "./display"
    assert not config.retrieval.build.output.cache_metrics_enabled
    assert config.retrieval.discovery.prompt.candidate_budget_tokens == 4000
    assert config.retrieval.discovery.prompt.preferred_capability_ids == ["expert-a"]
    assert not config.orchestration.build.matching.require_consensus and config.orchestration.plan.mode == "beam"
    assert not config.flow.enabled and config.flow.acceptance.min_successes == 2 and config.flow.narrative.max_examples == 3
    assert target.SymphonyConfig.model_validate_json(config.model_dump_json()) == config
    assert target.SymphonyConfig.model_validate(config.model_dump(mode="json")) == config


@pytest.mark.parametrize("path, value", [
    ("retrieval.build.max_depth", 3),
    ("retrieval.build.branching_factor", 16),
    ("retrieval.build.max_concurrency", 2),
    ("retrieval.build.request_timeout_seconds", 60.0),
    ("orchestration.build.extraction_mode", "llm"),
    ("orchestration.build.require_consensus", False),
    ("orchestration.build.matching.max_concurrency", 2),
    ("orchestration.plan.min_edge_confidence", 0.8),
    ("capability.fingerprint.protocol", {"schema_version": "2.0"}),
    ("retrieval.build.prompt.fingerprint_version", "v2"),
    ("retrieval.build.manager", {}),
    ("retrieval.build.build", {}),
    ("retrieval.build.dynamic.structure", {"branching_factor": 8}),
    ("retrieval.build.dynamic.max_capabilities_per_node", 12),
    ("retrieval.build.artifacts", {"allow_fallback_tree": False}),
    ("retrieval.build.model", {"model": "example"}),
    ("retrieval.build.method", 4),
    ("retrieval.build.task.strict_failure", False),
    ("retrieval.build.dynamic.expansion.max_capabilities_per_node", 1.5),
    ("retrieval.build.dynamic.expansion.expand_threshold", 0.7),
    ("retrieval.build.dynamic.expansion.early_stop", 1.7),
    ("retrieval.build.dynamic.expansion.lazy_split", 1.3),
    ("retrieval.build.dynamic.expansion.classification_batch", 6.0),
    ("retrieval.build.dynamic.expansion.structure_sample", 12.0),
    ("capability.fingerprint.extraction.body_limit", 16000),
    ("capability.fingerprint.extraction.max_tokens", 2048),
    ("capability.fingerprint.evaluation.evidence_text_limit", 512),
    ("retrieval.build.profiles.description_limit", 140),
    ("retrieval.build.profiles.rule_limit", 120),
    ("retrieval.build.request.context_window", 0),
    ("retrieval.build.prompt.cache_observability", True),
    ("retrieval.build.output.cache_observability", True),
    ("retrieval.discovery.budget", {"candidate_budget_ratio": 0.01}),
    ("retrieval.discovery.prompt.candidate_budget_tokens", 2000),
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
    ({"retrieval": {"build": {"structure": {"branching_factor": 1}}}}, ("retrieval", "build", "structure", "branching_factor")),
    ({"flow": {"enabled": False, "acceptance": {"min_pack_success_rate": -0.1}}},
     ("flow", "acceptance", "min_pack_success_rate")),
    ({"retrieval": {"build": {"dynamic": {"rebalance_interval": 0}}}},
     ("retrieval", "build", "dynamic", "rebalance_interval")),
    ({"retrieval": {"build": {"dynamic": {"expansion": {"lazy_split_multiplier": 0}}}}},
     ("retrieval", "build", "dynamic", "expansion", "lazy_split_multiplier")),
    ({"retrieval": {"build": {"task": {"allow_fallback_tree": "false"}}}},
     ("retrieval", "build", "task", "allow_fallback_tree")),
    ({"retrieval": {"build": {"output": {"display_capability_root": " "}}}},
     ("retrieval", "build", "output", "display_capability_root")),
])
def test_merged_configuration_errors_keep_complete_paths(payload, location):
    with pytest.raises(ValidationError) as error:
        target.SymphonyConfig.model_validate({**MINIMAL, **payload})
    assert error.value.errors()[0]["loc"] == location


def test_nested_public_defaults_are_independent_between_instances():
    first = target.SymphonyConfig.model_validate(MINIMAL)
    second = target.SymphonyConfig.model_validate(MINIMAL)
    first.retrieval.discovery.prompt.preferred_capability_ids.append("expert-extra")
    first.retrieval.build.dynamic.rebalance_interval = 80
    first.retrieval.build.dynamic.expansion = target.TreeExpansionConfig(early_stop_multiplier=2.0)
    assert "expert-extra" not in second.retrieval.discovery.prompt.preferred_capability_ids
    assert second.retrieval.build.dynamic.rebalance_interval == 50
    assert second.retrieval.build.dynamic.expansion.early_stop_multiplier == 1.7


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


def test_public_build_matches_existing_workflow_and_shared_tree_defaults():
    tree_mapping = {
        "max_workers": "request.max_concurrency", "caching": "request.caching",
        "num_retries": "request.max_retries", "timeout": "request.timeout_seconds",
        "context_window": "request.context_window_tokens", "max_output_tokens": "request.max_output_tokens",
        "classify_batch_cap": "request.classification_batch_limit",
        "postprocess_enabled": "postprocess.enabled", "postprocess_max_passes": "postprocess.max_passes",
        "postprocess_min_skills": "postprocess.min_capabilities",
        "equiv_grouping_enabled": "equivalence.enabled",
        "equiv_max_groups_per_parent": "equivalence.max_groups_per_parent",
        "equiv_allow_singleton_groups": "equivalence.allow_singleton_groups",
        "equiv_min_lexical_similarity": "equivalence.min_lexical_similarity",
        "deterministic_prompts": "prompt.deterministic", "discovery_seed": "prompt.discovery_seed",
        "cache_observability": "output.cache_metrics_enabled",
        "skill_profiles_enabled": "profiles.enabled", "skill_profile_select_rules_enabled": "profiles.select_rules_enabled",
        "skill_profile_batch_size": "profiles.batch_size", "skill_profile_description_limit": "profiles.max_description_chars",
        "skill_profile_rule_limit": "profiles.max_rule_chars",
    }
    config = target.SymphonyConfig.model_validate(MINIMAL).retrieval.build
    # 底层入口的并发 1、超时 180、批量 20 留在业务模块；提示版本是协议身份。
    excluded_tree_fields = ("max_workers", "timeout", "classify_batch_cap", "prompt_fingerprint_version")
    _assert_defaults(current_tree.TreeBuildConfig(), config, {
        name: path for name, path in tree_mapping.items() if name not in excluded_tree_fields
    }, excluded=excluded_tree_fields)
    workflow_mapping = {"tree_" + name: path for name, path in tree_mapping.items()}
    workflow_mapping["tree_timeout_seconds"] = workflow_mapping.pop("tree_timeout")
    workflow_mapping.update({
        "tree_branching_factor": "structure.branching_factor", "tree_max_depth": "structure.max_depth",
        "tree_root_categories": "structure.root_categories", "generate_tree_html": "output.generate_tree_html",
        "allow_fallback_tree": "task.allow_fallback_tree",
        **{old: new for old, new in INDEX_MAPPING.items() if old.startswith("incremental_")},
    })
    _assert_defaults(CurrentWorkflowConfig(), config, workflow_mapping, excluded=(
        "llm_openai_client", "llm_model", "llm_api_key", "llm_base_url", "llm_seed",
        "tree_prompt_fingerprint_version",
    ))


def test_fingerprint_flow_and_discovery_defaults_match_existing_sources():
    public = target.SymphonyConfig.model_validate(MINIMAL)
    current_fingerprint = FingerprintSettings()
    _assert_defaults(current_fingerprint, public.capability.fingerprint, {
        "enable_llm_extraction": "extraction.llm_enabled", "enable_llm_evaluation": "evaluation.llm_enabled",
        "batch_size": "extraction.batch_size", "max_concurrency": "extraction.max_concurrency",
        "body_limit": "extraction.max_body_chars", "llm_max_tokens": "extraction.max_output_tokens",
        "llm_timeout": "extraction.timeout_seconds", "evidence_text_limit": "evaluation.max_evidence_chars",
        "cache_enabled": "cache.enabled",
    }, excluded=("extraction_protocol_version", "evaluation_protocol_version", "configuration_signature"))
    protocol = target.FingerprintProtocolConfig()
    assert protocol.extraction_version == current_fingerprint.extraction_protocol_version
    assert protocol.evaluation_version == current_fingerprint.evaluation_protocol_version
    assert protocol.configuration_signature == current_fingerprint.configuration_signature
    _assert_defaults(CurrentFlowConfig(), target.FlowConfig(), {
        "enabled": "enabled", "min_successes": "acceptance.min_successes",
        "min_pack_success_rate": "acceptance.min_pack_success_rate", "max_narrative_examples": "narrative.max_examples",
    })
    _assert_defaults(DiscoverySettings(), target.DiscoveryConfig(), {
        "candidate_budget_ratio": "prompt.candidate_budget_ratio", "context_window_tokens": "prompt.context_window_tokens",
        "max_output_chars": "output.max_chars", "max_results": "output.max_results",
        "max_list_entries": "output.max_list_entries", "incremental_notice_max_chars": "output.incremental_notice_max_chars",
        "use_existing_index": "use_existing_index", "prompt_preferred_skills": "prompt.preferred_capability_ids",
    })


def test_planning_defaults_preserve_the_current_contract():
    public = target.SymphonyConfig.model_validate(MINIMAL)
    _assert_defaults(CurrentPlanningConfig(), public.orchestration, {
        "mode": "plan.mode", "top_k": "plan.top_k", "max_depth": "plan.max_depth",
        "min_edge_confidence": "min_edge_confidence", "dynamic_graph_enabled": "plan.dynamic_graph_enabled",
    })


@pytest.mark.parametrize("caching", [True, False])
@pytest.mark.parametrize("cache_metrics_enabled", [True, False])
def test_cache_metrics_remain_independent_of_the_build_cache_policy(caching, cache_metrics_enabled):
    config = target.RetrievalBuildConfig.model_validate({
        "request": {"caching": caching}, "output": {"cache_metrics_enabled": cache_metrics_enabled},
    })
    assert config.request.caching is caching
    assert config.output.cache_metrics_enabled is cache_metrics_enabled
    assert "cache_observability" not in config.prompt.model_dump()
    assert target.RetrievalBuildConfig.model_validate_json(config.model_dump_json()) == config


@pytest.mark.parametrize("ratio, context_tokens, expected", [(0.02, 50000, 1000), (0.333, 10, 3), (0.0001, 1, 1)])
def test_discovery_prompt_budget_keeps_derivation_and_default_priority_list(ratio, context_tokens, expected):
    config = target.SymphonyConfig.model_validate({
        **MINIMAL, "retrieval": {"discovery": {"prompt": {
            "candidate_budget_ratio": ratio, "context_window_tokens": context_tokens,
        }}},
    })
    prompt = config.retrieval.discovery.prompt
    assert prompt.candidate_budget_tokens == expected
    assert prompt.preferred_capability_ids == list(DiscoverySettings().prompt_preferred_skills)
    assert "candidate_budget_tokens" not in prompt.model_dump()
    assert "candidate_budget_tokens" not in prompt.model_json_schema()["properties"]
    assert target.SymphonyConfig.model_validate_json(config.model_dump_json()) == config


@pytest.mark.parametrize("field, value", [
    ("candidate_budget_ratio", 0), ("candidate_budget_ratio", 1.01),
    ("context_window_tokens", 0), ("context_window_tokens", True),
])
def test_discovery_budget_errors_are_located_under_prompt(field, value):
    with pytest.raises(ValidationError) as error:
        target.SymphonyConfig.model_validate({**MINIMAL, "retrieval": {"discovery": {"prompt": {field: value}}}})
    assert error.value.errors()[0]["loc"] == ("retrieval", "discovery", "prompt", field)


def test_public_dynamic_and_output_defaults_preserve_source_values_without_runtime_settings():
    config = target.SymphonyConfig.model_validate(MINIMAL).retrieval.build
    assert config.dynamic.rebalance_interval == current_tree.DynamicTreeConfig().rebalance_interval
    assert config.dynamic.expansion.model_dump() == {
        "max_capabilities_per_node_multiplier": current_tree.MAX_SKILLS_PER_NODE_MULTIPLIER,
        "expand_threshold_multiplier": current_tree.EXPAND_THRESHOLD_MULTIPLIER,
        "early_stop_multiplier": current_tree.EARLY_STOP_MULTIPLIER,
        "lazy_split_multiplier": current_tree.LAZY_SPLIT_MULTIPLIER,
        "classification_batch_multiplier": current_tree.CLASSIFICATION_BATCH_MULTIPLIER,
        "structure_sample_multiplier": current_tree.STRUCTURE_SAMPLE_MULTIPLIER,
    }
    assert config.output.model_dump() == {
        "generate_tree_html": False, "verbose": False, "show_tree": True, "display_capability_root": None,
        "cache_metrics_enabled": True,
    }
    assert not any(hasattr(config.dynamic, name) for name in (
        "structure", "max_capabilities_per_node", "expand_threshold", "early_stop_capability_count",
        "lazy_split_threshold", "classification_batch_size", "structure_sample_size",
    ))
    assert not hasattr(target, "TreeBuildMethod") and not hasattr(target, "_strict_integer")


@pytest.mark.parametrize("field", [
    "max_capabilities_per_node_multiplier", "expand_threshold_multiplier", "early_stop_multiplier",
    "lazy_split_multiplier", "classification_batch_multiplier", "structure_sample_multiplier",
])
@pytest.mark.parametrize("value", [0, -0.1, True, "1.5", float("nan"), float("inf")])
def test_expansion_multipliers_preserve_positive_numeric_constraints(field, value):
    payload = {**MINIMAL, "retrieval": {"build": {"dynamic": {"expansion": {field: value}}}}}
    with pytest.raises(ValidationError) as error:
        target.SymphonyConfig.model_validate(payload)
    assert error.value.errors()[0]["loc"] == ("retrieval", "build", "dynamic", "expansion", field)


def test_independent_derived_configuration_values_and_explicit_overrides():
    assert target.DiscoveryPromptConfig().candidate_budget_tokens == 2000


def test_existing_package_exports_continue_to_use_the_existing_configuration():
    import openjiuwen.symphony as symphony

    assert symphony.OrchestrationConfig is CurrentPlanningConfig
    assert symphony.OrchestrationConfig is not target.OrchestrationConfig
    assert "SymphonyConfig" not in symphony.__all__


@pytest.mark.parametrize("payload, location", [
    ({"retrieval": {"search": {}}}, ("retrieval", "search")),
    ({"retrieval": {"toolkit": {}}}, ("retrieval", "toolkit")),
    ({"experience": {}}, ("experience",)),
])
def test_retired_capabilities_are_rejected_without_compatibility_aliases(payload, location):
    with pytest.raises(ValidationError) as error:
        target.SymphonyConfig.model_validate({**MINIMAL, **payload})
    assert error.value.errors()[0]["loc"] == location
    assert error.value.errors()[0]["type"] == "extra_forbidden"


def test_registry_contains_only_retained_capabilities():
    assert len(target.PUBLIC_CONFIG_MODELS) == 33
    assert len(target.INTERNAL_CONFIG_MODELS) == 3
    assert {m.__name__ for m in target.INTERNAL_CONFIG_MODELS} == {
        "FingerprintProtocolConfig", "OneShotTreeBuildConfig", "RetrievalModelConfig",
    }
    for name in (
        "_DEFAULT_COMPACT_CODES", "LLMClientConfig", "RetrievalSearchConfig", "AgenticRetrievalConfig",
        "ExperienceConfig", "CapabilityIndexStorageConfig", "ScoreTruncationConfig",
    ):
        assert not hasattr(target, name)
