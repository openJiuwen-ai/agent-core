from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Any

from openjiuwen.symphony.config import RetrievalBuildConfig
from openjiuwen.symphony.model_bridge import _ModelBridge, _model_identity
from openjiuwen.symphony.retrieval.build.scanners import create_scanner
from openjiuwen.symphony.shared.rich_compat import Panel

from .adapters import TreeBuilderAdaptersMixin
from .context import TreeBuilderOperations, TreeBuildSettings, TreeBuildState
from .equivalence import TreeBuilderEquivalenceMixin
from .expansion import TreeExpansionEngine as ExternalTreeExpansionEngine
from .expansion_repair import TreeBuilderExpansionRepairMixin
from .grouping import TreeGroupingEngine
from .llm_runtime import TreeLLMRuntime as ExternalTreeLLMRuntime
from .preset_writer import TreePresetWriter as ExternalTreePresetWriter
from .repair import TreeRepairEngine as ExternalTreeRepairEngine
from .runtime import TreeBuilderRuntimeMixin
from .schema import (
    DEFAULT_TREE_OUTPUT_PATH,
    ResolvedTreeStructure,
    normalize_root_categories,
)
from .shared import console
from .root_categories import resolve_tree_root_categories


class TreeBuilder(
    TreeBuilderRuntimeMixin,
    TreeBuilderExpansionRepairMixin,
    TreeBuilderEquivalenceMixin,
    TreeBuilderAdaptersMixin,
):
    """
    Unified tree builder with auto-selection and node splitting.

    Features:
    - Auto-selects build method based on skill count
    - Splits oversized nodes (> max_skills_per_node)
    - Simple tree visualization
    """

    # Token budget constants for auto batch size calculation
    PROMPT_OVERHEAD_TOKENS = 3000  # prompt template + instructions
    OUTPUT_RESERVE_TOKENS = 4000  # JSON response reserve
    AVG_TOKENS_PER_SKILL = 75  # average tokens per skill entry
    DEFAULT_CONTEXT_WINDOW = 128000  # fallback context window size
    DEFAULT_MAX_OUTPUT_TOKENS = 32768  # fallback max output tokens

    def __init__(
        self,
        skills_dir: Path | str | None = None,
        output_path: Path | str | None = None,
        *,
        config: RetrievalBuildConfig | None = None,
        model: Any = None,
        item_type: str = "skill",
        skill_entries: list[dict] | None = None,
    ):
        policy = (config or RetrievalBuildConfig()).model_copy(deep=True)
        if skills_dir is None:
            raise ValueError("TreeBuilder requires a non-empty skills_dir")
        if model is None:
            raise ValueError("TreeBuilder requires an injected model")
        self.scanner = create_scanner(
            item_type, skills_dir, display_items_dir=policy.output.display_capability_root,
        )
        self._skill_entries_override = (
            [dict(item) for item in (skill_entries or [])] if skill_entries is not None else None
        )
        self.output_path = Path(output_path) if output_path else DEFAULT_TREE_OUTPUT_PATH
        structure = policy.structure
        self.config = ResolvedTreeStructure(
            branching_factor=structure.branching_factor,
            max_depth=structure.max_depth,
            root_categories=normalize_root_categories(resolve_tree_root_categories(structure.root_categories)),
            max_skills_per_node=int(structure.branching_factor * structure.max_capabilities_per_node_multiplier),
        )
        self.model = _model_identity(model).get("model", "")
        self.max_workers = policy.request.max_concurrency
        self.settings = TreeBuildSettings.from_config(policy)
        self._owns_model_bridge = not isinstance(model, _ModelBridge)
        bridge = _ModelBridge(model) if self._owns_model_bridge else model
        self.state = TreeBuildState(model=bridge, max_workers=self.max_workers)
        self.llm_runtime = ExternalTreeLLMRuntime(self)
        self.preset_writer = ExternalTreePresetWriter(self)
        self.expansion_engine = ExternalTreeExpansionEngine(self)
        self.repair_engine = ExternalTreeRepairEngine(self)
        self.grouping_engine = TreeGroupingEngine(self)
        self.operations = TreeBuilderOperations(
            assign_skills_to_leaf=self._assign_skills_to_leaf,
            insert_skill_into_subtree=self._insert_skill_into_subtree,
            prune_empty_children=self._prune_empty_children,
            repair_small_leaf_children=self._repair_small_leaf_children,
            discover_equivalence_groups=self._discover_equivalence_groups,
            normalize_equivalence_groups=self._normalize_equivalence_groups,
            build_equivalence_group_id=self._build_equivalence_group_id,
        )

    def _auto_batch_size(self) -> int:
        """Calculate batch size from model context window."""
        return self.llm_runtime.auto_batch_size()

    def _get_max_output_tokens(self) -> int:
        """Get max output tokens for the model, with caching."""
        return self.llm_runtime.get_max_output_tokens()

    def _model_limits(self) -> tuple[int, int]:
        """Resolve model limits."""
        return self.llm_runtime.model_limits()

    def close(self) -> None:
        if self._owns_model_bridge:
            self.state.model.close()

    def build(self) -> dict:
        try:
            return self._build()
        finally:
            self.close()

    def _build(self) -> dict:
        verbose = self.settings.config.output.verbose
        show_tree = self.settings.config.output.show_tree
        console.print(Panel.fit("[bold cyan]Building Capability Tree[/bold cyan]", border_style="cyan"))
        step1_start = perf_counter()
        skill_entries = self._load_skill_entries()
        console.print(f"[dim]Step 1 elapsed: {(perf_counter() - step1_start) * 1000.0:.2f} ms[/dim]")
        if not skill_entries:
            console.print("[red]No skills found.[/red]")
            return {}

        step1b_start = perf_counter()
        skill_entries = self._enrich_skill_profiles(skill_entries, verbose=verbose)
        console.print(f"[dim]Step 1b elapsed: {(perf_counter() - step1b_start) * 1000.0:.2f} ms[/dim]")
        step2_start = perf_counter()
        tree_root = self._build_tree(skill_entries, verbose)
        console.print(f"[dim]Step 2 elapsed: {(perf_counter() - step2_start) * 1000.0:.2f} ms[/dim]")
        step3_start = perf_counter()
        tree_dict = self._tree_to_dict(tree_root)
        preset_dict = self._build_tree_preset(
            tree_dict,
            show_tree=show_tree,
        )
        console.print(f"[dim]Step 3 elapsed: {(perf_counter() - step3_start) * 1000.0:.2f} ms[/dim]")
        self._print_cache_stats()
        self._print_build_summary()
        return preset_dict


def build_tree(
    skills_dir: Path | str | None = None,
    output_path: Path | str | None = None,
    *,
    config: RetrievalBuildConfig | None = None,
    model: Any = None,
    item_type: str = "skill",
    skill_entries: list[dict] | None = None,
) -> dict:
    """Build a recursive capability tree using the shared policy and model."""
    return TreeBuilder(
        skills_dir, output_path, config=config, model=model,
        item_type=item_type, skill_entries=skill_entries,
    ).build()
