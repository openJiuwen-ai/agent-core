# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""验证目标配置契约，不调用 Runtime、模型后端或构建引擎。"""

from dataclasses import asdict
import json
from pathlib import Path
import re

import pytest
from pydantic import TypeAdapter, ValidationError

from openjiuwen.symphony import config as target
from openjiuwen.symphony.agent.retrieval_toolkit import (
    AgenticRetrievalConfig as CurrentAgenticConfig,
    SkillIndexBuildConfig as CurrentIndexBuildConfig,
)
from openjiuwen.symphony.discovery.config import DiscoverySettings
from openjiuwen.symphony.experience.models import ExperienceBankBuildConfig as CurrentExperienceConfig
from openjiuwen.symphony.orchestration.config import (
    OrchestrationConfig as CurrentPlanningConfig,
    SymphonyFlowConfig as CurrentFlowConfig,
)
from openjiuwen.symphony.retrieval.build.tree import schema as current_tree
from openjiuwen.symphony.retrieval.build.workflows.artifacts import BuildConfig as CurrentWorkflowConfig
from openjiuwen.symphony.retrieval.llm.config import (
    OpenAIClientConfig as CurrentOpenAIConfig,
    TransformersClientConfig as CurrentTransformersConfig,
    VLLMClientConfig as CurrentVLLMConfig,
)
from openjiuwen.symphony.retrieval.search.runtime.codebooks import DEFAULT_COMPACT_BOUNDARY_CODEBOOK
from openjiuwen.symphony.retrieval.search.service.models import RetrieverConfig as CurrentRetrieverConfig
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
    "context_window": "build.request.context_window",
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
    "cache_observability": "build.prompt.cache_observability",
    "skill_profiles_enabled": "build.profiles.enabled",
    "skill_profile_select_rules_enabled": "build.profiles.select_rules_enabled",
    "skill_profile_batch_size": "build.profiles.batch_size",
    "skill_profile_description_limit": "build.profiles.description_limit",
    "skill_profile_rule_limit": "build.profiles.rule_limit",
    "incremental_max_change_ratio": "incremental.max_change_ratio",
    "incremental_min_add_confidence": "incremental.min_add_confidence",
    "incremental_min_add_confidence_margin": "incremental.min_add_confidence_margin",
    "incremental_branch_imbalance_ratio": "incremental.branch_imbalance_ratio",
    "generate_tree_html": "output.generate_tree_html",
    "preserve_previous_index_on_failure": "task.preserve_previous_index_on_failure",
    "strict_failure": "task.strict_failure",
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
    assert config.retrieval.search.top_k == 10
    assert config.retrieval.search.traversal.max_parallel_branches == 3
    assert config.retrieval.search.generation.request_timeout_seconds == 120.0
    assert config.orchestration.min_edge_confidence == 0.7
    assert not config.capability.fingerprint.extraction.llm_enabled
    assert config.capability.fingerprint.extraction.max_concurrency == 4
    assert config.orchestration.build.max_concurrency == 1
    assert config.orchestration.build.matching.require_consensus
    assert config.orchestration.plan.mode == "fast"
    assert config.orchestration.plan.top_k == 3
    assert config.orchestration.plan.max_depth == 4
    assert config.flow.enabled
    assert config.experience.build.execution.max_concurrency == 8


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
        target.SymphonyConfig.model_validate({**MINIMAL, "retrieval": {"search": {"top_k": value}}})
    assert error.value.errors()[0]["loc"] == ("retrieval", "search", "top_k")


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan"), float("inf"), True])
def test_confidence_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        target.SymphonyConfig.model_validate({**MINIMAL, "orchestration": {"min_edge_confidence": value}})


@pytest.mark.parametrize("value", [None, 1024])
def test_fingerprint_output_budget_supports_injected_model_default(value):
    config = target.SymphonyConfig.model_validate({
        **MINIMAL,
        "capability": {**MINIMAL["capability"], "fingerprint": {"extraction": {"max_tokens": value}}},
    })
    assert config.capability.fingerprint.extraction.max_tokens == value
    assert target.SymphonyConfig.model_validate_json(config.model_dump_json()) == config
    assert target.FingerprintExtractionConfig().max_tokens == FingerprintSettings().llm_max_tokens == 2048


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "2048"])
def test_fingerprint_output_budget_rejects_invalid_values(value):
    payload = {
        **MINIMAL,
        "capability": {**MINIMAL["capability"], "fingerprint": {"extraction": {"max_tokens": value}}},
    }
    for validate, data in (
        (target.SymphonyConfig.model_validate, payload),
        (target.SymphonyConfig.model_validate_json, json.dumps(payload)),
    ):
        with pytest.raises(ValidationError) as error:
            validate(data)
        assert error.value.errors()[0]["loc"] == ("capability", "fingerprint", "extraction", "max_tokens")


@pytest.mark.parametrize("payload, location", [
    ({"retrieval": {"search": {"topk": 3}}}, ("retrieval", "search", "topk")),
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
    monkeypatch.setenv("SYMPHONY_RETRIEVAL_TOP_K", "999")
    config = target.SymphonyConfig.model_validate({
        "capability": {"root": Path("missing/../capabilities"), "type": "expert"},
        "storage": {"artifact_root": Path("./artifacts")},
    })
    assert config.capability.root == "missing/../capabilities"
    assert config.storage.artifact_root == "artifacts"
    assert config.retrieval.search.top_k == 10
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
        target.CapabilityIndexStorageConfig: {"index_root": "./index"},
        target.TrieConstraintConfig: {"allowed_output_ids": ["capability-a"]},
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


def test_schema_preserves_bounds_enum_examples_and_backend_discriminator():
    schema = target.SymphonyConfig.model_json_schema()
    assert schema["required"] == ["capability", "storage"]
    search = schema["$defs"]["RetrievalSearchConfig"]["properties"]["top_k"]
    assert search["default"] == 10 and search["exclusiveMinimum"] == 0
    assert search["examples"] == [5, 10]
    assert schema["$defs"]["CapabilityConfig"]["properties"]["type"]["enum"] == [
        "skill", "expert", "connector", "mcp",
    ]
    assert schema["$defs"]["CapabilityConfig"]["required"] == ["root", "type"]
    assert schema["$defs"]["StorageConfig"]["required"] == ["artifact_root"]
    for model in target.PUBLIC_CONFIG_MODELS:
        if model not in (target.SymphonyConfig, target.CapabilityConfig, target.StorageConfig):
            assert not any(info.is_required() for info in model.model_fields.values()), model.__name__
    backend = TypeAdapter(target.LLMClientConfig).json_schema()
    assert backend["discriminator"]["propertyName"] == "backend"


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
    }
    assert not removed & set(vars(target))
    assert not removed & {model.__name__ for model in target.CONFIG_MODELS}


def test_public_exports_are_limited_to_the_runtime_configuration_tree():
    expected = {
        "CapabilityType", "CapabilityConfig", "StorageConfig", "RetrievalBuildConfig", "RetrievalSearchConfig",
        "RetrievalConfig", "OrchestrationBuildConfig", "OrchestrationPlanConfig", "OrchestrationConfig", "SymphonyConfig",
        "FlowConfig", "ExperienceConfig",
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
    first, second = target.RetrievalSearchConfig(), target.RetrievalSearchConfig()
    first.render.compact_codebook.append("EXAMPLE")
    first_backend, second_backend = target.OpenAIClientConfig(), target.OpenAIClientConfig()
    first_backend.extra_body["options"] = {"enabled": True}
    assert second.render.compact_codebook == list(DEFAULT_COMPACT_BOUNDARY_CODEBOOK)
    assert second_backend.extra_body == {}
    with pytest.raises(ValidationError) as error:
        target.SymphonyConfig.model_validate(MINIMAL).retrieval.search.top_k = 2
    assert error.value.errors()[0]["type"] == "frozen_instance"
    mutable = target.SymphonyConfig.model_validate(MINIMAL).retrieval.build.dynamic
    mutable.rebalance_interval = 80
    assert mutable.rebalance_interval == 80
    with pytest.raises(ValidationError):
        mutable.rebalance_interval = 0


@pytest.mark.parametrize("backend, model_type", [
    ("openai", target.OpenAIClientConfig),
    ("vllm", target.VLLMClientConfig),
    ("transformers", target.TransformersClientConfig),
    ("transformers_prefix_cached", target.TransformersClientConfig),
    ("transformers_prefix_cached_generation", target.TransformersClientConfig),
])
def test_backend_union_can_be_selected_from_serializable_configuration(backend, model_type):
    adapter = TypeAdapter(target.LLMClientConfig)
    config = adapter.validate_python({"backend": backend})
    assert isinstance(config, model_type)
    assert adapter.validate_json(adapter.dump_json(config)) == config


def test_runtime_dependencies_are_not_configuration_fields():
    with pytest.raises(ValidationError):
        target.OpenAIClientConfig(client=object())
    with pytest.raises(ValidationError):
        target.LLMGenerationConfig(prompt_cache=object())
    with pytest.raises(ValidationError):
        target.SymphonyConfig.model_validate({**MINIMAL, "model": object()})


@pytest.mark.parametrize("value", [object(), float("nan"), float("inf")])
def test_backend_extensions_must_be_serializable_json(value):
    with pytest.raises(ValidationError):
        target.OpenAIClientConfig(extra_body={"custom": value})


def test_public_build_preserves_every_configurable_toolkit_default():
    config = target.SymphonyConfig.model_validate(MINIMAL)
    mapping = {
        old: path.removeprefix("build.") for old, path in INDEX_MAPPING.items()
        if old != "prompt_fingerprint_version"
    }
    _assert_defaults(
        CurrentIndexBuildConfig(), config.retrieval.build, mapping, excluded=("prompt_fingerprint_version",),
    )


def test_public_groups_preserve_existing_module_defaults():
    config = target.SymphonyConfig.model_validate(MINIMAL)
    assert config.capability.scan == target.DirectoryScanConfig()
    current = CurrentRetrieverConfig()
    assert config.retrieval.search.top_k == current.top_k
    for old, planned in (
        (current.traversal_config, config.retrieval.search.traversal),
        (current.render_config, config.retrieval.search.render),
    ):
        _assert_defaults(old, planned, {name: name for name in asdict(old)})
    _assert_defaults(current.generation_config, config.retrieval.search.generation, {
        name: name.replace("logit_", "logit.", 1) if name.startswith("logit_") else name
        for name in asdict(current.generation_config)
    })
    assert config.retrieval.discovery == target.DiscoveryConfig()
    assert config.retrieval.toolkit == target.AgenticRetrievalConfig()
    assert config.flow == target.FlowConfig()
    assert config.experience.build == target.ExperienceBankBuildConfig()
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
                "extraction": {"llm_enabled": True, "max_concurrency": 2},
                "evaluation": {"llm_enabled": True}, "cache": {"enabled": False},
            },
        },
        "retrieval": {
            "build": {
                "structure": {"branching_factor": 16, "root_categories": ["analysis", {"name": "write", "rank": 2}]},
                "dynamic": {"rebalance_interval": 80, "expansion": {"early_stop": 2.0}},
                "request": {"max_concurrency": 3, "timeout_seconds": 60.0},
                "postprocess": {"enabled": False}, "equivalence": {"enabled": True},
                "profiles": {"enabled": True}, "prompt": {"discovery_seed": 7},
                "incremental": {"max_change_ratio": 0.1},
                "task": {"strict_failure": True, "allow_fallback_tree": False},
                "output": {"generate_tree_html": True, "verbose": True, "show_tree": False,
                           "display_capability_root": "./display"},
            },
            "search": {
                "top_k": 7, "traversal": {"max_parallel_branches": 1}, "render": {"compact_codebook": ["AA", "BB"]},
                "generation": {"mode": "logit_selection", "logit": {"min_probability": 0.2}},
            },
            "discovery": {"budget": {"candidate_budget_ratio": 0.02}, "prompt": {"preferred_capability_ids": ["expert-a"]}},
            "toolkit": {"render": {"max_exposure_depth": 2}, "tools": {"branch_explore": "capability_explore"}},
        },
        "orchestration": {"build": {"matching": {"require_consensus": False}}, "plan": {"mode": "beam"}},
        "flow": {"enabled": False, "acceptance": {"min_successes": 2}, "narrative": {"max_examples": 3}},
        "experience": {"build": {"clustering": {"min_cluster_size": 2}, "execution": {"max_concurrency": 1}}},
    })
    assert config.capability.scan.limits.max_entrypoints == 200
    assert config.capability.scan.manifest.max_frontmatter_depth == 8
    assert config.capability.fingerprint.extraction.llm_enabled
    assert config.capability.fingerprint.extraction.batch_size == 16
    assert config.capability.fingerprint.evaluation.llm_enabled and not config.capability.fingerprint.cache.enabled
    assert config.retrieval.build.structure.root_categories == ["analysis", {"name": "write", "rank": 2}]
    assert config.retrieval.build.dynamic.rebalance_interval == 80
    assert config.retrieval.build.dynamic.expansion.early_stop == 2.0
    assert config.retrieval.build.dynamic.expansion.classification_batch == 6.0
    assert config.retrieval.build.request.max_concurrency == 3 and config.retrieval.build.request.max_retries == 2
    assert not config.retrieval.build.postprocess.enabled and config.retrieval.build.equivalence.enabled
    assert config.retrieval.build.profiles.enabled and config.retrieval.build.prompt.discovery_seed == 7
    assert config.retrieval.build.incremental.max_change_ratio == 0.1
    assert config.retrieval.build.task.strict_failure and config.retrieval.build.output.generate_tree_html
    assert not config.retrieval.build.task.allow_fallback_tree
    assert config.retrieval.build.output.verbose and not config.retrieval.build.output.show_tree
    assert config.retrieval.build.output.display_capability_root == "./display"
    assert config.retrieval.search.top_k == 7 and config.retrieval.search.traversal.max_parallel_branches == 1
    assert config.retrieval.search.render.compact_codebook == ["AA", "BB"]
    assert config.retrieval.search.generation.logit.min_probability == 0.2
    assert config.retrieval.discovery.budget.candidate_budget_tokens == 4000
    assert config.retrieval.discovery.prompt.preferred_capability_ids == ["expert-a"]
    assert config.retrieval.toolkit.render.max_exposure_depth == 2
    assert config.retrieval.toolkit.tools.branch_explore == "capability_explore"
    assert not config.orchestration.build.matching.require_consensus and config.orchestration.plan.mode == "beam"
    assert not config.flow.enabled and config.flow.acceptance.min_successes == 2 and config.flow.narrative.max_examples == 3
    assert config.experience.build.clustering.min_cluster_size == 2 and config.experience.build.execution.max_concurrency == 1
    assert config.experience.build.patterns.merge_threshold == 0.9
    assert target.SymphonyConfig.model_validate_json(config.model_dump_json()) == config
    assert target.SymphonyConfig.model_validate(config.model_dump(mode="json")) == config


@pytest.mark.parametrize("path, value", [
    ("retrieval.build.max_depth", 3),
    ("retrieval.build.branching_factor", 16),
    ("retrieval.build.max_concurrency", 2),
    ("retrieval.build.request_timeout_seconds", 60.0),
    ("retrieval.search.max_parallel_branches", 1),
    ("retrieval.search.request_timeout_seconds", 60.0),
    ("orchestration.build.extraction_mode", "llm"),
    ("orchestration.build.require_consensus", False),
    ("orchestration.build.matching.max_concurrency", 2),
    ("orchestration.plan.min_edge_confidence", 0.8),
    ("capability.fingerprint.protocol", {"schema_version": "2.0"}),
    ("retrieval.build.prompt.fingerprint_version", "v2"),
    ("retrieval.search.llm", {"backend": "vllm"}),
    ("retrieval.search.progressive", {}),
    ("retrieval.build.manager", {}),
    ("retrieval.build.build", {}),
    ("retrieval.build.dynamic.structure", {"branching_factor": 8}),
    ("retrieval.build.dynamic.max_capabilities_per_node", 12),
    ("retrieval.build.artifacts", {"allow_fallback_tree": False}),
    ("retrieval.build.model", {"model": "example"}),
    ("retrieval.build.method", 4),
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
    ({"retrieval": {"search": {"generation": {"logit": {"min_probability": 2}}}}},
     ("retrieval", "search", "generation", "logit", "min_probability")),
    ({"flow": {"enabled": False, "acceptance": {"min_pack_success_rate": -0.1}}},
     ("flow", "acceptance", "min_pack_success_rate")),
    ({"experience": {"build": {"execution": {"max_concurrency": "2"}}}},
     ("experience", "build", "execution", "max_concurrency")),
    ({"retrieval": {"build": {"dynamic": {"rebalance_interval": 0}}}},
     ("retrieval", "build", "dynamic", "rebalance_interval")),
    ({"retrieval": {"build": {"dynamic": {"expansion": {"lazy_split": 0}}}}},
     ("retrieval", "build", "dynamic", "expansion", "lazy_split")),
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
    first.retrieval.search.render.compact_codebook.append("EXAMPLE")
    first.retrieval.toolkit.render.compact_codebook.append("TOOL")
    first.retrieval.discovery.prompt.preferred_capability_ids.append("expert-extra")
    # 经验构建节点保留既有可修改语义，赋值仍校验且默认对象不能跨根实例共享。
    first.experience.build.execution = target.ExperienceExecutionConfig(max_concurrency=1)
    first.retrieval.build.dynamic.rebalance_interval = 80
    first.retrieval.build.dynamic.expansion = target.TreeExpansionConfig(early_stop=2.0)
    assert second.retrieval.search.render.compact_codebook == list(DEFAULT_COMPACT_BOUNDARY_CODEBOOK)
    assert second.retrieval.toolkit.render.compact_codebook == []
    assert "expert-extra" not in second.retrieval.discovery.prompt.preferred_capability_ids
    assert second.experience.build.execution.max_concurrency == 8
    assert second.retrieval.build.dynamic.rebalance_interval == 50
    assert second.retrieval.build.dynamic.expansion.early_stop == 1.7
    with pytest.raises(ValidationError):
        first.experience.build.execution = {"max_concurrency": 0}


def test_index_task_policy_and_output_have_separate_serialized_paths():
    config = target.RetrievalBuildConfig.model_validate({
        "task": {"total_timeout_seconds": 60.0, "preserve_previous_index_on_failure": False,
                 "strict_failure": True, "allow_fallback_tree": False},
        "output": {"generate_tree_html": True, "verbose": True, "show_tree": False,
                   "display_capability_root": "./display"},
    })
    payload = config.model_dump(mode="json")
    assert payload["task"] == {
        "total_timeout_seconds": 60.0, "preserve_previous_index_on_failure": False, "strict_failure": True,
        "allow_fallback_tree": False,
    }
    assert payload["output"] == {
        "generate_tree_html": True, "verbose": True, "show_tree": False, "display_capability_root": "./display",
    }
    assert target.RetrievalBuildConfig.model_validate_json(config.model_dump_json()) == config


@pytest.mark.parametrize("field", ["preserve_previous_index_on_failure", "strict_failure", "allow_fallback_tree"])
def test_index_failure_policy_rejects_the_previous_output_path(field):
    with pytest.raises(ValidationError) as error:
        target.RetrievalBuildConfig.model_validate({"output": {field: False}})
    assert error.value.errors()[0]["loc"] == ("output", field)


def test_public_build_matches_existing_workflow_and_shared_tree_defaults():
    tree_mapping = {
        "max_workers": "request.max_concurrency", "caching": "request.caching",
        "num_retries": "request.max_retries", "timeout": "request.timeout_seconds",
        "context_window": "request.context_window", "max_output_tokens": "request.max_output_tokens",
        "classify_batch_cap": "request.classification_batch_limit",
        "postprocess_enabled": "postprocess.enabled", "postprocess_max_passes": "postprocess.max_passes",
        "postprocess_min_skills": "postprocess.min_capabilities",
        "equiv_grouping_enabled": "equivalence.enabled",
        "equiv_max_groups_per_parent": "equivalence.max_groups_per_parent",
        "equiv_allow_singleton_groups": "equivalence.allow_singleton_groups",
        "equiv_min_lexical_similarity": "equivalence.min_lexical_similarity",
        "deterministic_prompts": "prompt.deterministic", "discovery_seed": "prompt.discovery_seed",
        "cache_observability": "prompt.cache_observability",
        "skill_profiles_enabled": "profiles.enabled", "skill_profile_select_rules_enabled": "profiles.select_rules_enabled",
        "skill_profile_batch_size": "profiles.batch_size", "skill_profile_description_limit": "profiles.description_limit",
        "skill_profile_rule_limit": "profiles.rule_limit",
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
        "method", "llm_openai_client", "llm_model", "llm_api_key", "llm_base_url", "llm_seed",
        "tree_prompt_fingerprint_version",
    ))


def test_fingerprint_flow_discovery_and_experience_defaults_match_existing_sources():
    public = target.SymphonyConfig.model_validate(MINIMAL)
    current_fingerprint = FingerprintSettings()
    _assert_defaults(current_fingerprint, public.capability.fingerprint, {
        "enable_llm_extraction": "extraction.llm_enabled", "enable_llm_evaluation": "evaluation.llm_enabled",
        "batch_size": "extraction.batch_size", "max_concurrency": "extraction.max_concurrency",
        "body_limit": "extraction.body_limit", "llm_max_tokens": "extraction.max_tokens",
        "llm_timeout": "extraction.timeout_seconds", "evidence_text_limit": "evaluation.evidence_text_limit",
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
        "candidate_budget_ratio": "budget.candidate_budget_ratio", "context_window_tokens": "budget.context_window_tokens",
        "max_output_chars": "output.max_chars", "max_results": "output.max_results",
        "max_list_entries": "output.max_list_entries", "incremental_notice_max_chars": "output.incremental_notice_max_chars",
        "use_existing_index": "use_existing_index", "prompt_preferred_skills": "prompt.preferred_capability_ids",
    })
    _assert_defaults(CurrentExperienceConfig(), target.ExperienceBankBuildConfig(), {
        "min_cluster_size": "clustering.min_cluster_size", "max_workers": "execution.max_concurrency",
        "max_success_examples": "patterns.max_success_examples", "pending_flush_threshold": "execution.pending_flush_threshold",
        "min_hits_for_pattern": "patterns.min_hits", "skill_cluster_num": "clustering.capability_cluster_num",
        "pattern_merge_threshold": "patterns.merge_threshold", "query_examples_count": "patterns.query_examples_count",
    })


def test_agentic_and_planning_defaults_preserve_the_current_contracts():
    public = target.SymphonyConfig.model_validate(MINIMAL)
    _assert_defaults(CurrentPlanningConfig(), public.orchestration, {
        "mode": "plan.mode", "top_k": "plan.top_k", "max_depth": "plan.max_depth",
        "min_edge_confidence": "min_edge_confidence", "dynamic_graph_enabled": "plan.dynamic_graph_enabled",
    })
    _assert_defaults(CurrentAgenticConfig(), target.AgenticRetrievalConfig(), {
        "top_k": "top_k", "compact_codes_enabled": "render.compact_codes_enabled",
        "flatten_tree": "render.flatten_tree", "max_exposure_depth": "render.max_exposure_depth",
        "exposure_threshold": "render.exposure_threshold", "max_tokens": "request.max_tokens",
        "request_timeout_seconds": "request.request_timeout_seconds", "index_build_tool_name": "tools.index_build",
        "branch_explore_tool_name": "tools.branch_explore", "branch_peek_tool_name": "tools.branch_peek",
    })
    assert target.AgenticRenderConfig().max_exposure_depth == 1
    assert target.RetrievalRenderConfig().max_exposure_depth == 99


def test_backend_defaults_preserve_all_pure_fields():
    _assert_defaults(CurrentOpenAIConfig(), target.OpenAIClientConfig(), {
        "backend": "backend", "model": "model", "api_key": "api_key", "base_url": "base_url",
        "seed": "seed", "extra_body": "extra_body",
    }, excluded=("client",))
    _assert_defaults(CurrentVLLMConfig(), target.VLLMClientConfig(), {
        "backend": "backend", "model_path": "model_path", "tokenizer_path": "tokenizer_path",
        "request_model": "request_model", "vllm_kwargs": "options",
    })
    _assert_defaults(CurrentTransformersConfig(), target.TransformersClientConfig(), {
        "backend": "backend", "model_path": "model_path", "tokenizer_path": "tokenizer_path",
        "device": "device", "dtype": "dtype", "tensor_parallel_size": "parallelism.tensor_parallel_size",
        "data_parallel_size": "parallelism.data_parallel_size", "device_ids": "parallelism.device_ids",
        "attn_implementation": "optimization.attn_implementation", "torch_compile": "optimization.torch_compile",
        "tensor_parallel_plan": "parallelism.tensor_parallel_plan",
    })


def test_public_retrieval_keeps_nested_overrides_and_independent_defaults():
    config = target.RetrievalSearchConfig.model_validate({
        "traversal": {"max_parallel_branches": 1},
        "render": {"max_exposure_depth": 3},
        "generation": {"mode": "logit_selection", "logit": {"min_probability": 0.4}},
    })
    assert config.top_k == 10
    assert config.traversal.max_parallel_branches == 1
    assert config.render.max_exposure_depth == 3
    assert config.generation.mode == "logit_selection"
    assert config.generation.logit.min_probability == 0.4
    assert target.RetrievalSearchConfig.model_validate_json(config.model_dump_json()) == config
    config.render.compact_codebook.append("EXAMPLE")
    assert target.RetrievalSearchConfig().render.compact_codebook == list(DEFAULT_COMPACT_BOUNDARY_CODEBOOK)


def test_public_dynamic_and_output_defaults_preserve_source_values_without_runtime_settings():
    config = target.SymphonyConfig.model_validate(MINIMAL).retrieval.build
    assert config.dynamic.rebalance_interval == current_tree.DynamicTreeConfig().rebalance_interval
    assert config.dynamic.expansion.model_dump() == {
        "max_capabilities_per_node": current_tree.MAX_SKILLS_PER_NODE_MULTIPLIER,
        "expand_threshold": current_tree.EXPAND_THRESHOLD_MULTIPLIER,
        "early_stop": current_tree.EARLY_STOP_MULTIPLIER,
        "lazy_split": current_tree.LAZY_SPLIT_MULTIPLIER,
        "classification_batch": current_tree.CLASSIFICATION_BATCH_MULTIPLIER,
        "structure_sample": current_tree.STRUCTURE_SAMPLE_MULTIPLIER,
    }
    assert config.output.model_dump() == {
        "generate_tree_html": False, "verbose": False, "show_tree": True, "display_capability_root": None,
    }
    assert not any(hasattr(config.dynamic, name) for name in (
        "structure", "max_capabilities_per_node", "expand_threshold", "early_stop_capability_count",
        "lazy_split_threshold", "classification_batch_size", "structure_sample_size",
    ))
    assert not hasattr(target, "TreeBuildMethod") and not hasattr(target, "_strict_integer")


def test_independent_derived_configuration_values_and_explicit_overrides():
    assert target.DiscoveryBudgetConfig().candidate_budget_tokens == 2000
    assert target.DistributedGenerationConfig(tp_size=2, dp_size=3).world_size == 6


def test_existing_package_exports_continue_to_use_the_existing_configuration():
    import openjiuwen.symphony as symphony

    assert symphony.OrchestrationConfig is CurrentPlanningConfig
    assert symphony.OrchestrationConfig is not target.OrchestrationConfig
    assert "SymphonyConfig" not in symphony.__all__
