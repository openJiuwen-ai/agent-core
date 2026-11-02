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
    AgenticRetrievalConfig as CurrentAgenticConfig,
    SkillIndexBuildConfig as CurrentIndexBuildConfig,
)
from openjiuwen.symphony.discovery.config import DiscoverySettings
from openjiuwen.symphony.experience.models import ExperienceBankBuildConfig as CurrentExperienceConfig
from openjiuwen.symphony.orchestration.config import (
    OrchestrationConfig as CurrentPlanningConfig,
    SymphonyFlowConfig as CurrentFlowConfig,
)
from openjiuwen.symphony.retrieval.build.tree.schema import TreeBuildConfig as CurrentTreeBuildConfig
from openjiuwen.symphony.retrieval.build.workflows.artifacts import BuildConfig as CurrentWorkflowConfig
from openjiuwen.symphony.retrieval.llm.config import (
    OpenAIClientConfig as CurrentOpenAIConfig,
    TransformersClientConfig as CurrentTransformersConfig,
    VLLMClientConfig as CurrentVLLMConfig,
)
from openjiuwen.symphony.retrieval.search.runtime.codebooks import DEFAULT_COMPACT_BOUNDARY_CODEBOOK
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
    "preserve_previous_index_on_failure": "output.preserve_previous_index_on_failure",
    "strict_failure": "output.strict_failure",
}


def test_minimal_config_has_independent_enabled_features_and_expected_defaults():
    config = target.SymphonyConfig.model_validate(MINIMAL)
    assert config.capability.root == "./capabilities"
    assert config.capability.type == "skill"
    assert config.storage.artifact_root == "./.symphony"
    assert config.retrieval.enabled and config.orchestration.enabled
    assert config.retrieval.build.max_depth == 6
    assert config.retrieval.build.branching_factor == 128
    assert config.retrieval.build.max_concurrency == 2
    assert config.retrieval.search.top_k == 10
    assert config.orchestration.min_edge_confidence == 0.7
    assert config.orchestration.build.extraction_mode == "metadata"
    assert config.orchestration.build.max_concurrency == 1
    assert config.orchestration.plan.mode == "fast"
    assert config.orchestration.plan.top_k == 3
    assert config.orchestration.plan.max_depth == 4


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
            **MINIMAL, "retrieval": {"enabled": False, "build": {"request_timeout_seconds": 0}},
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
    config = target.IndexBuildConfig.model_validate({
        "structure": {"root_categories": Path("./categories.yaml")},
    })
    assert config.structure.root_categories == "categories.yaml"
    assert target.IndexBuildConfig.model_validate_json(config.model_dump_json()) == config
    assert target.TreeBuildOptions().model is None


def _example(model):
    examples = {
        target.CapabilityConfig: {"root": "./capabilities", "type": "skill"},
        target.StorageConfig: {"artifact_root": "./artifacts"},
        target.SymphonyConfig: MINIMAL,
        target.CapabilityIndexStorageConfig: {"index_root": "./index"},
        target.TrieConstraintConfig: {"allowed_output_ids": ["capability-a"]},
        target.ResolvedIndexTreeStructureConfig: {"branching_factor": 128, "max_depth": 6, "root_categories": None},
    }
    if model is target.TreeBuildSettings:
        return model.from_manager_config(target.TreeManagerConfig(), llm_seed=42)
    if model is target.ResolvedIndexBuildConfig:
        data = target.IndexBuildConfig().model_dump(mode="json")
        return model.model_validate(data)
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
    backend = target.RetrieverConfig.model_json_schema()["properties"]["llm"]
    assert backend["discriminator"]["propertyName"] == "backend"


def test_model_registry_covers_all_concrete_models():
    classes = {
        value for value in vars(target).values()
        if isinstance(value, type) and issubclass(value, target.SymphonyConfigModel)
        and value is not target.SymphonyConfigModel
    }
    assert set(target.CONFIG_MODELS) == classes
    assert len(target.CONFIG_MODELS) == len(classes)


def test_default_containers_are_not_shared_and_public_assignment_is_frozen():
    first, second = target.RetrieverConfig(), target.RetrieverConfig()
    first.render.compact_codebook.append("EXAMPLE")
    first.llm.extra_body["options"] = {"enabled": True}
    assert second.render.compact_codebook == list(DEFAULT_COMPACT_BOUNDARY_CODEBOOK)
    assert second.llm.extra_body == {}
    with pytest.raises(ValidationError) as error:
        target.SymphonyConfig.model_validate(MINIMAL).retrieval.search.top_k = 2
    assert error.value.errors()[0]["type"] == "frozen_instance"
    mutable = target.DynamicTreeConfig()
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
    config = target.RetrieverConfig.model_validate({"llm": {"backend": backend}})
    assert isinstance(config.llm, model_type)
    assert target.RetrieverConfig.model_validate_json(config.model_dump_json()) == config


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


def test_toolkit_index_defaults_are_preserved_for_every_source_field():
    _assert_defaults(CurrentIndexBuildConfig(), target.CapabilityIndexBuildConfig(), INDEX_MAPPING)


def test_underlying_tree_and_workflow_keep_their_distinct_defaults():
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
        "prompt_fingerprint_version": "prompt.fingerprint_version", "cache_observability": "prompt.cache_observability",
        "skill_profiles_enabled": "profiles.enabled", "skill_profile_select_rules_enabled": "profiles.select_rules_enabled",
        "skill_profile_batch_size": "profiles.batch_size", "skill_profile_description_limit": "profiles.description_limit",
        "skill_profile_rule_limit": "profiles.rule_limit",
    }
    _assert_defaults(CurrentTreeBuildConfig(), target.TreeBuildConfig(), tree_mapping)
    workflow_mapping = {"tree_" + name: "build." + path for name, path in tree_mapping.items()}
    workflow_mapping["tree_timeout_seconds"] = workflow_mapping.pop("tree_timeout")
    workflow_mapping.update({
        "method": "method", "llm_model": "model.model", "llm_api_key": "model.api_key",
        "llm_base_url": "model.base_url", "llm_seed": "model.seed",
        "tree_branching_factor": "structure.branching_factor", "tree_max_depth": "structure.max_depth",
        "tree_root_categories": "structure.root_categories", "generate_tree_html": "artifacts.generate_tree_html",
        "allow_fallback_tree": "artifacts.allow_fallback_tree",
        **{old: new for old, new in INDEX_MAPPING.items() if old.startswith("incremental_")},
    })
    _assert_defaults(CurrentWorkflowConfig(), target.IndexBuildConfig(), workflow_mapping, excluded=("llm_openai_client",))


def test_fingerprint_flow_discovery_and_experience_defaults_match_existing_sources():
    _assert_defaults(FingerprintSettings(), target.FingerprintConfig(), {
        "enable_llm_extraction": "extraction.llm_enabled", "enable_llm_evaluation": "evaluation.llm_enabled",
        "batch_size": "extraction.batch_size", "max_concurrency": "extraction.max_concurrency",
        "body_limit": "extraction.body_limit", "llm_max_tokens": "extraction.max_tokens",
        "llm_timeout": "extraction.timeout_seconds", "evidence_text_limit": "evaluation.evidence_text_limit",
        "cache_enabled": "cache.enabled", "extraction_protocol_version": "protocol.extraction_version",
        "evaluation_protocol_version": "protocol.evaluation_version", "configuration_signature": "protocol.configuration_signature",
    })
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
    _assert_defaults(CurrentPlanningConfig(), target.GraphPlanningConfig(), {
        name: name for name in asdict(CurrentPlanningConfig())
    })
    _assert_defaults(CurrentAgenticConfig(), target.AgenticRetrievalConfig(), {
        "top_k": "top_k", "compact_codes_enabled": "render.compact_codes_enabled",
        "flatten_tree": "render.flatten_tree", "max_exposure_depth": "render.max_exposure_depth",
        "exposure_threshold": "render.exposure_threshold", "max_tokens": "request.max_tokens",
        "request_timeout_seconds": "request.request_timeout_seconds", "index_build_tool_name": "tools.index_build",
        "branch_explore_tool_name": "tools.branch_explore", "branch_peek_tool_name": "tools.branch_peek",
    })
    assert target.ProgressiveRetrieverConfig().top_k == 5
    assert target.RetrieverConfig().top_k == 10
    assert target.AgenticRenderConfig().max_exposure_depth == 1
    assert target.DisclosureConfig().max_exposure_depth == 2
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


def test_derived_configuration_values_and_explicit_overrides():
    dynamic = target.DynamicTreeConfig(structure=target.TreeStructureConfig(branching_factor=10))
    assert dynamic.max_capabilities_per_node == 15
    assert dynamic.expand_threshold == 7
    assert dynamic.early_stop_capability_count == 17
    assert dynamic.lazy_split_threshold == 19
    assert dynamic.classification_batch_size == 60
    assert dynamic.structure_sample_size == 120
    assert target.DiscoveryBudgetConfig().candidate_budget_tokens == 2000
    assert target.DistributedGenerationConfig(tp_size=2, dp_size=3).world_size == 6
    manager = target.TreeManagerConfig(build=target.TreeBuildConfig(
        profiles=target.CapabilityProfilesConfig(enabled=True, batch_size=24),
    ))
    resolved = target.TreeBuildSettings.from_manager_config(manager, llm_seed=42)
    assert resolved.profiles.enabled and resolved.profiles.batch_size == 24
    assert resolved.llm_seed == 42
    assert resolved.profiles is not manager.build.profiles


def test_existing_package_exports_continue_to_use_the_existing_configuration():
    import openjiuwen.symphony as symphony

    assert symphony.OrchestrationConfig is CurrentPlanningConfig
    assert symphony.OrchestrationConfig is not target.OrchestrationConfig
    assert "SymphonyConfig" not in symphony.__all__
