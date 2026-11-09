# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""验证配置树生成结果及独立导出入口，不调用 Symphony 运行链路。"""

import importlib.util
from pathlib import Path
import subprocess
import sys
from typing import Literal

from pydantic import BaseModel, Field
import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts/export_symphony_config.py"


@pytest.fixture(scope="module")
def exporter():
    spec = importlib.util.spec_from_file_location("symphony_config_exporter_tests", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tree_preserves_required_optional_enum_and_nested_factory_defaults(exporter):
    class ChildConfig(BaseModel):
        depth: int = Field(6, description="树深度。")

    class ExampleConfig(BaseModel):
        root: str = Field(description="对象目录。")
        label: str | None = Field(None, description="可选标签。")
        mode: Literal["fast", "beam"] = Field("fast", description="规划方式。")
        child: ChildConfig = Field(default_factory=lambda: ChildConfig(depth=3), description="树配置。")

    lines = exporter.render_fields(ExampleConfig, expand=True)
    assert lines == [
        "├── root # 必填；对象目录。",
        "├── label # 默认=None；可选标签。",
        '├── mode # 默认="fast"；规划方式。 可选：fast / beam。',
        '└── child → ChildConfig # 默认=ChildConfig({"depth": 3})；树配置。',
        "    └── depth # 默认=3；树深度。",
    ]


def test_recursive_model_is_rendered_as_a_reference(exporter):
    class NodeConfig(BaseModel):
        child: "NodeConfig | None" = Field(None, description="可选子节点。")

    assert exporter.render_fields(NodeConfig, expand=True) == [
        "└── child → NodeConfig # 默认=None；可选子节点。",
    ]


def test_context_dependent_default_is_not_evaluated_without_input(exporter):
    class ExampleConfig(BaseModel):
        root: str = Field(description="必填目录。")
        label: str = Field(default_factory=lambda data: data["root"], description="默认取目录。")

    assert exporter.render_fields(ExampleConfig)[1] == "└── label # 默认值由前序字段计算；默认取目录。"


def test_default_document_contains_only_public_configuration(exporter):
    config = exporter.load_config()
    public = exporter.render_document(config)
    assert f"用户配置树覆盖 {len(config.PUBLIC_CONFIG_MODELS)} 个模型" in public
    assert "├── capability → CapabilityConfig # 必填" in public
    assert "├── sources → CapabilitySourceConfig # 必填" in public
    assert "│   ├── enabled # 默认=true；允许构建和读取分类树、创建发现会话" in public
    assert "min_edge_confidence # 默认=0.7" in public
    for model in config.PUBLIC_CONFIG_MODELS:
        assert model.__name__ + " # " in public
    for model in config.INTERNAL_CONFIG_MODELS:
        assert "\n" + model.__name__ + " # " not in public
        assert "→ " + model.__name__ + " # " not in public
    assert "全量配置" in public
    assert "search →" not in public and "toolkit →" not in public
    assert "scan → DirectoryScanConfig" in public
    assert "fingerprint → FingerprintPolicyConfig" in public
    assert "index_build → RetrievalBuildConfig" in public
    assert "discovery →" not in public
    assert "    └── experience → OrchestrationExperienceConfig" in public
    assert "allow_fallback_tree # 默认=true" in public
    assert "strict_failure #" not in public
    structure = public.split("structure → RetrievalTreeStructureConfig", 1)[1].split("request → IndexRequestConfig", 1)[0]
    assert "max_capabilities_per_node_multiplier # 默认=1.5" in structure
    assert "叶节点容量阈值" in structure
    assert public.count("max_capabilities_per_node_multiplier #") == 1
    for name in (
        "dynamic", "expansion", "rebalance_interval", "caching", "max_capabilities_per_node",
        "expand_threshold_multiplier", "early_stop_multiplier", "lazy_split_multiplier",
        "classification_batch_multiplier", "structure_sample_multiplier",
    ):
        assert f"── {name} #" not in public
        assert f"── {name} →" not in public
    assert "capability_cluster_num #" not in public
    assert "cache_metrics_enabled # 默认=true" in public
    assert "prompt → DiscoveryPromptConfig" in public
    assert "candidate_budget_ratio # 默认=0.01" in public
    assert "context_window_tokens # 默认=200000" in public
    assert "max_body_chars # 默认=16000" in public
    assert "max_evidence_chars # 默认=512" in public
    assert "max_description_chars # 默认=140" in public
    assert "max_rule_chars # 默认=120" in public
    for name in (
        "body_limit", "max_tokens", "evidence_text_limit", "description_limit", "rule_limit",
        "context_window", "cache_observability", "request_timeout_seconds",
    ):
        assert f"── {name} #" not in public
    assert "generation →" not in public and "budget →" not in public
    assert "show_tree # 默认=true" in public
    assert "display_capability_root # 默认=None" in public
    assert "protocol →" not in public and "fingerprint_version #" not in public
    assert "llm →" not in public and "独立内部配置" not in public and "内部模型参考" not in public
    assert "candidate_budget_tokens #" not in public


def test_optional_reference_covers_all_remaining_internal_fields(exporter):
    config = exporter.load_config()
    document = exporter.render_document(config, include_internal=True)
    public, internal = document.split("# 内部模型参考：", 1)
    assert "SymphonyConfig #" in public
    assert f"{len(config.INTERNAL_CONFIG_MODELS)} 个" in internal
    for model in config.INTERNAL_CONFIG_MODELS:
        block = internal.split("\n" + model.__name__ + " # ", 1)[1].split("\n\n", 1)[0]
        assert len(block.splitlines()) == len(model.model_fields) + 1
        for field in model.model_fields:
            assert f"── {field} # " in block or f"── {field} → " in block
    for name in ("FingerprintProtocolConfig", "OneShotTreeBuildConfig"):
        assert "\n" + name + " # " in internal
    for name in (
        "TreeRequestConfig", "TreeStructureConfig", "IndexTreeStructureConfig", "TreeBuildConfig",
        "IndexTreeBuildConfig", "TreeManagerConfig", "TreePromptConfig", "IndexBuildConfig", "IndexArtifactConfig",
        "TreeBuildOptions", "ProgressiveRetrieverConfig", "RetrieverConfig", "DisclosureConfig",
        "DiscoveryBudgetConfig", "RetrievalGenerationConfig", "DynamicTreeConfig", "TreeExpansionConfig",
    ):
        assert "\n" + name + " # " not in document
        assert "→ " + name + " # " not in document
    assert "candidate_budget_tokens #" not in document


def test_generated_document_is_current(exporter):
    assert exporter.OUTPUT_PATH.read_text(encoding="utf-8") == exporter.render_document(exporter.load_config())


def test_export_is_repeatable_and_does_not_rewrite_identical_content(exporter, tmp_path, monkeypatch):
    output = tmp_path / "config.tree.txt"
    assert exporter.main(["--output", str(output)]) == 0
    original = output.read_bytes()

    def unexpected_write(*args, **kwargs):
        raise AssertionError("相同内容不应重复写入")

    monkeypatch.setattr(Path, "write_text", unexpected_write)
    assert exporter.main(["--output", str(output)]) == 0
    assert output.read_bytes() == original


def test_reference_export_uses_separate_default_path_and_supports_custom_output(exporter, tmp_path, monkeypatch):
    user_document = tmp_path / "config.tree.txt"
    monkeypatch.setattr(exporter, "OUTPUT_PATH", user_document)
    assert exporter.main([]) == 0
    original = user_document.read_bytes()
    assert exporter.main(["--include-internal"]) == 0
    reference = tmp_path / "config.reference.txt"
    assert "# 内部模型参考：" in reference.read_text(encoding="utf-8")
    assert user_document.read_bytes() == original
    custom = tmp_path / "custom" / "reference.txt"
    assert exporter.main(["--include-internal", "--output", str(custom)]) == 0
    assert custom.read_bytes() == reference.read_bytes()


@pytest.mark.parametrize("include_internal", [False, True])
def test_export_runs_outside_repository_without_importing_runtime(tmp_path, include_internal):
    output = tmp_path / "exported.tree.txt"
    probe = (
        "import runpy, sys; "
        "module = runpy.run_path(sys.argv[1]); "
        "assert module['main'](['--output', sys.argv[2]] + sys.argv[3:]) == 0; "
        "assert not any(name == 'openjiuwen' or name.startswith('openjiuwen.') for name in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", probe, str(SCRIPT), str(output),
         *(["--include-internal"] if include_internal else [])],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    document = output.read_text(encoding="utf-8")
    assert document.startswith("# 由 scripts/export_symphony_config.py")
    assert ("# 内部模型参考：" in document) is include_internal
