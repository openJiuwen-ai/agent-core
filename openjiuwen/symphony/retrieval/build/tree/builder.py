from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Optional, TypedDict, Unpack

if TYPE_CHECKING:
    from openai import OpenAI
else:
    try:
        from openai import OpenAI
    except ModuleNotFoundError:
        OpenAI = None

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
    DynamicTreeConfig,
    TreeManagerConfig,
)
from .shared import console


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
        config: Optional[DynamicTreeConfig] = None,
        manager_config: TreeManagerConfig | None = None,
        client: OpenAI | None = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        llm_seed: int | None = None,
        max_workers: Optional[int] = None,
        display_skills_dir: Path | str | None = None,
        item_type: str = "skill",
        skill_entries: list[dict] | None = None,
    ):
        mcfg = manager_config or TreeManagerConfig()
        build_cfg = mcfg.build
        if skills_dir is None:
            raise ValueError("TreeBuilder requires a non-empty skills_dir")
        self.scanner = create_scanner(item_type, skills_dir, display_items_dir=display_skills_dir)
        self._skill_entries_override = (
            [dict(item) for item in (skill_entries or [])] if skill_entries is not None else None
        )
        default_tree_path = DEFAULT_TREE_OUTPUT_PATH
        self.output_path = Path(output_path) if output_path else default_tree_path
        self.config = config or DynamicTreeConfig(
            branching_factor=mcfg.branching_factor,
            max_depth=mcfg.max_depth,
            root_categories=mcfg.root_categories,
        )
        self.model = str(model or "").strip()
        self.api_key = str(api_key or "").strip()
        self.base_url = str(base_url or "").strip()
        if not self.model:
            raise ValueError("TreeBuilder requires a non-empty llm model")
        if client is None and not self.api_key:
            raise ValueError("TreeBuilder requires a non-empty llm api key")
        resolved_client = (
            client
            if client is not None
            else (OpenAI(api_key=self.api_key, base_url=self.base_url) if OpenAI is not None else None)
        )
        self.max_workers = max_workers or build_cfg.max_workers
        self.settings = TreeBuildSettings.from_manager_config(mcfg, llm_seed=llm_seed)
        self.state = TreeBuildState(client=resolved_client, max_workers=self.max_workers)
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

    def _merged_extra_body(self) -> dict:
        return self.llm_runtime.merged_extra_body()

    def _model_limits(self) -> tuple[int, int]:
        """Resolve model limits."""
        return self.llm_runtime.model_limits()

    def build(
        self,
        verbose: bool = False,
        show_tree: bool = True,
    ) -> dict:
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


class TreeBuildOptions(TypedDict, total=False):
    """Keyword options accepted by :func:`build_tree`."""

    config: DynamicTreeConfig | None
    manager_config: TreeManagerConfig | None
    client: OpenAI | None
    model: str | None
    api_key: str | None
    base_url: str | None
    llm_seed: int | None
    max_workers: int | None
    verbose: bool
    show_tree: bool
    display_skills_dir: Path | str | None
    item_type: str
    skill_entries: list[dict] | None


def build_tree(
    skills_dir: Path | str | None = None,
    output_path: Path | str | None = None,
    **options: Unpack[TreeBuildOptions],
) -> dict:
    """Build capability tree."""
    builder = TreeBuilder(
        skills_dir,
        output_path,
        config=options.get("config"),
        manager_config=options.get("manager_config"),
        client=options.get("client"),
        model=options.get("model"),
        api_key=options.get("api_key"),
        base_url=options.get("base_url"),
        llm_seed=options.get("llm_seed"),
        max_workers=options.get("max_workers"),
        display_skills_dir=options.get("display_skills_dir"),
        item_type=options.get("item_type", "skill"),
        skill_entries=options.get("skill_entries"),
    )
    return builder.build(
        verbose=options.get("verbose", False),
        show_tree=options.get("show_tree", True),
    )
