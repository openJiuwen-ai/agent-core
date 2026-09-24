from __future__ import annotations

from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Optional

from openjiuwen.symphony.shared.rich_compat import (
    BarColumn,
    Panel,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)

from .expansion import TreeExpansionEngine as _TreeExpansionEngine
from .mixin import TreeBuilderMixin
from .prompts import SKILL_PROFILE_PROMPT
from .schema import Skill, TreeNode
from .shared import console
from .types import ChildGroup as ExternalChildGroup
from .types import QueuedNode as ExternalQueuedNode

_ChildGroup = ExternalChildGroup
_QueuedNode = ExternalQueuedNode


class TreeBuilderRuntimeMixin(TreeBuilderMixin):
    def _load_skill_entries(self) -> list[dict]:
        console.print("\n[bold]Step 1: Scanning skills...[/bold]")
        if self._skill_entries_override is not None:
            skill_entries = [dict(item) for item in self._skill_entries_override]
        else:
            skill_entries = self.scanner.to_dict_list()
        if skill_entries:
            console.print(f"[green]Found {len(skill_entries)} skills[/green]")
        return skill_entries

    def _enrich_skill_profiles(self, skill_entries: list[dict], *, verbose: bool = False) -> list[dict]:
        if not self.settings.skill_profiles_enabled:
            return list(skill_entries)

        console.print("\n[bold]Step 1b: Normalizing skill routing profiles...[/bold]")
        enriched: list[dict] = []
        ordered = self._sorted_skills(skill_entries)
        for batch in self._skill_profile_batches(ordered):
            profiles = self._generate_skill_profiles(batch, verbose=verbose)
            for skill in batch:
                enriched.append(self._apply_skill_profile(skill, profiles.get(str(skill.get("id") or "").strip())))
        if self.settings.deterministic_prompts:
            return sorted(enriched, key=lambda item: (str(item.get("id", "")), str(item.get("name", ""))))
        return enriched

    def _skill_profile_batches(self, skills: list[dict]) -> list[list[dict]]:
        batch_size = min(
            self.settings.skill_profile_batch_size,
            max(1, self._auto_batch_size()),
        )
        batches: list[list[dict]] = []
        for index in range(0, len(skills), batch_size):
            batch_end = index + batch_size
            batches.append(skills[index:batch_end])
        return batches

    def _generate_skill_profiles(self, skills: list[dict], *, verbose: bool = False) -> dict[str, dict[str, str]]:
        if not skills:
            return {}
        prompt = SKILL_PROFILE_PROMPT.format(
            skills_list=self._format_skill_profile_inputs(skills),
            description_limit=self.settings.skill_profile_description_limit,
            rule_limit=self.settings.skill_profile_rule_limit,
        )
        result = self._call_llm_json(prompt)
        raw_profiles = result.get("profiles", {}) if isinstance(result, dict) else {}
        if not isinstance(raw_profiles, dict):
            if verbose:
                console.print("[yellow]  Skill profile generation returned no profile mapping[/yellow]")
            return {}

        valid_ids = {str(skill.get("id") or "").strip() for skill in skills}
        profiles: dict[str, dict[str, str]] = {}
        for skill_id, payload in raw_profiles.items():
            normalized_id = str(skill_id or "").strip()
            if normalized_id not in valid_ids or not isinstance(payload, dict):
                continue
            profiles[normalized_id] = {
                "description": self._compact_profile_field(
                    payload.get("description"),
                    limit=self.settings.skill_profile_description_limit,
                ),
                "select_when": self._compact_profile_field(
                    payload.get("select_when"),
                    limit=self.settings.skill_profile_rule_limit,
                ),
                "dont_select_when": self._compact_profile_field(
                    payload.get("dont_select_when"),
                    limit=self.settings.skill_profile_rule_limit,
                ),
            }
        return profiles

    def _format_skill_profile_inputs(self, skills: list[dict]) -> str:
        rows: list[str] = []
        for skill in self._sorted_skills(skills):
            skill_id = str(skill.get("id") or "").strip()
            name = str(skill.get("name") or skill_id).strip() or skill_id
            description = self._compact_profile_field(skill.get("description"), limit=600)
            content = self._compact_profile_field(skill.get("content"), limit=900)
            rows.append(f"- id: {skill_id}")
            rows.append(f"  name: {name}")
            if description:
                rows.append(f"  source_description: {description}")
            if content:
                rows.append(f"  source_content: {content}")
        return "\n".join(rows)

    def _apply_skill_profile(self, skill: dict, profile: dict[str, str] | None) -> dict:
        source_description = str(skill.get("source_description") or skill.get("description") or "").strip()
        if not profile:
            return self._with_fallback_skill_profile(skill)

        description = self._compact_profile_field(
            profile.get("description"),
            limit=self.settings.skill_profile_description_limit,
        )
        if not description:
            return self._with_fallback_skill_profile(skill)

        select_when = self._compact_profile_field(
            profile.get("select_when"),
            limit=self.settings.skill_profile_rule_limit,
        )
        dont_select_when = self._compact_profile_field(
            profile.get("dont_select_when"),
            limit=self.settings.skill_profile_rule_limit,
        )
        if not self.settings.skill_profile_select_rules_enabled:
            select_when = ""
            dont_select_when = ""

        updated = dict(skill)
        updated["source_description"] = source_description
        updated["routing_description"] = description
        updated["select_when"] = select_when
        updated["dont_select_when"] = dont_select_when
        updated["description"] = self._compose_skill_routing_description(
            description=description,
            select_when=select_when,
            dont_select_when=dont_select_when,
        )
        return updated

    def _with_fallback_skill_profile(self, skill: dict) -> dict:
        source_description = str(skill.get("source_description") or skill.get("description") or "").strip()
        name = str(skill.get("name") or skill.get("id") or "").strip()
        fallback = self._compact_profile_field(
            source_description or name,
            limit=self.settings.skill_profile_description_limit,
        )
        updated = dict(skill)
        updated["source_description"] = source_description
        updated["routing_description"] = fallback
        updated.setdefault("select_when", "")
        updated.setdefault("dont_select_when", "")
        updated["description"] = self._compose_skill_routing_description(
            description=fallback,
            select_when=str(updated.get("select_when") or ""),
            dont_select_when=str(updated.get("dont_select_when") or ""),
        )
        return updated

    def _compose_skill_routing_description(
        self,
        *,
        description: str,
        select_when: str = "",
        dont_select_when: str = "",
    ) -> str:
        parts = [
            self._compact_profile_field(
                description,
                limit=self.settings.skill_profile_description_limit,
            )
        ]
        if self.settings.skill_profile_select_rules_enabled and select_when:
            parts.append(
                f"Select when: {self._compact_profile_field(select_when, limit=self.settings.skill_profile_rule_limit)}"
            )
        if self.settings.skill_profile_select_rules_enabled and dont_select_when:
            parts.append(
                "Don't select when: "
                f"{self._compact_profile_field(dont_select_when, limit=self.settings.skill_profile_rule_limit)}"
            )
        return "\n".join(part for part in parts if part).strip()

    @staticmethod
    def _compact_profile_field(value: object, *, limit: int) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)].rstrip() + "..."

    def _build_tree_preset(
        self,
        tree_dict: dict,
        *,
        show_tree: bool,
    ) -> dict:
        console.print("\n[bold]Step 3: Preparing tree preset...[/bold]")
        preset_dict = self._tree_to_orchestrator_preset(tree_dict)
        if show_tree:
            console.print("\n[bold]Tree Structure:[/bold]")
            self._print_tree(tree_dict)
        return preset_dict

    def _print_build_summary(self) -> None:
        summary_lines = [f"[bold green]Done![/bold green] ({self.state.llm_calls} LLM calls)"]
        if self.settings.cache_observability:
            summary_lines.extend(
                [
                    "Cache hits/misses/unknown: "
                    f"{self.state.cache_hits}/{self.state.cache_misses}/{self.state.cache_unknown}",
                    f"Unique prompt fingerprints: {len(self.state.prompt_fingerprints)}",
                ]
            )
        console.print(Panel.fit("\n".join(summary_lines), border_style="green"))

    def _build_tree(self, skills: list[dict], verbose: bool = False) -> TreeNode:
        console.print("\n[bold]Step 2: Building tree structure...[/bold]")
        root = TreeNode(id="root", name="Root", description="Skill capability tree root")
        self.state.leaf_skills = 0
        pending_nodes: deque[ExternalQueuedNode] = deque([ExternalQueuedNode(root, skills, 0, None)])
        active_jobs: dict = {}

        with self._tree_progress(total=len(skills)) as progress:
            self.state.progress = progress
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                self.state.executor = executor
                while pending_nodes or active_jobs:
                    self._submit_pending_nodes(pending_nodes, active_jobs, executor, verbose=verbose)
                    self._refresh_pending_metric(active_jobs)
                    if not active_jobs:
                        continue
                    self._harvest_finished_nodes(active_jobs, pending_nodes)

            self.state.progress = None
            self.state.executor = None

        self._repair_tree(root, source_skills=skills, verbose=verbose)
        return root

    def _tree_progress(self, *, total: int) -> Progress:
        status_text = (
            "("
            "{task.fields[leaf]}/{task.fields[total_count]} skills done, "
            "{task.fields[llm]} LLM, "
            "{task.fields[pending]} pending)"
        )
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn(status_text),
            console=console,
            transient=False,
        )
        self.state.progress_task = progress.add_task(
            "Building capability tree",
            total=total,
            pending=0,
            leaf=0,
            llm=0,
            total_count=total,
        )
        return progress

    def _submit_pending_nodes(
        self,
        pending_nodes: deque[ExternalQueuedNode],
        active_jobs: dict,
        executor: ThreadPoolExecutor,
        *,
        verbose: bool,
    ) -> None:
        while pending_nodes:
            job = pending_nodes.popleft()
            future = executor.submit(
                self._process_node,
                node=job.node,
                skills=job.skills,
                depth=job.depth,
                parent_context=job.parent_context,
                verbose=verbose,
            )
            active_jobs[future] = job

    def _refresh_pending_metric(self, active_jobs: dict) -> None:
        if self.state.progress is None or self.state.progress_task is None:
            return
        self.state.progress.update(self.state.progress_task, pending=len(active_jobs))

    def _harvest_finished_nodes(
        self,
        active_jobs: dict,
        pending_nodes: deque[ExternalQueuedNode],
    ) -> None:
        done, _ = wait(tuple(active_jobs.keys()), return_when=FIRST_COMPLETED)
        finished = sorted(done, key=lambda future: active_jobs[future].node.id)
        for future in finished:
            job = active_jobs.pop(future)
            try:
                child_groups = future.result()
            except Exception as exc:
                console.print(f"[red]Error processing {job.node.id}: {exc}[/red]")
                continue
            for child_group in child_groups:
                pending_nodes.append(self._queue_child_node(child_group, depth=job.depth + 1))

    def _repair_tree(self, root: TreeNode, *, source_skills: list[dict], verbose: bool) -> None:
        self._audit_tree(root, source_skills)
        if self.settings.postprocess_enabled and self.settings.postprocess_max_passes > 0:
            self._postprocess_tree(root, verbose)
        if self.settings.equiv_grouping_enabled:
            self._normalize_to_equivalence_groups(root, verbose)
        self._audit_tree(root, source_skills)

    @staticmethod
    def _collect_leaf_skills(node: TreeNode) -> set:
        seen_ids: set[str] = set()
        frontier = [node]
        while frontier:
            current = frontier.pop()
            if current.children:
                frontier.extend(current.children)
                continue
            for skill in current.skills:
                seen_ids.add(skill.id)
        return seen_ids

    def _audit_tree(self, root: TreeNode, input_skills: list[dict]) -> None:
        expected_ids = {str(skill.get("id", "")).strip() for skill in input_skills}
        discovered_ids = self._collect_leaf_skills(root)
        missing_ids = sorted(skill_id for skill_id in expected_ids if skill_id and skill_id not in discovered_ids)
        if not missing_ids:
            return

        missing_lookup = {str(skill.get("id", "")).strip(): skill for skill in input_skills}
        recovered_payloads = [missing_lookup[skill_id] for skill_id in missing_ids if skill_id in missing_lookup]
        console.print(
            Panel(
                "\n".join(
                    [
                        "[bold red]Recovered "
                        f"{len(recovered_payloads)} missing skills after tree construction.[/bold red]",
                        "They have been attached under a fallback branch to preserve index completeness.",
                    ]
                ),
                title="[bold red]Tree Audit Recovery[/bold red]",
                border_style="red",
            )
        )

        fallback_branch = TreeNode(
            id="uncategorized",
            name="Uncategorized",
            description="Skills recovered by integrity audit after tree construction.",
            depth=1,
            parent_id=root.id,
        )
        self._assign_skills_to_leaf(fallback_branch, recovered_payloads)
        root.children.append(fallback_branch)

    @staticmethod
    def _queue_child_node(child_group: _ChildGroup, *, depth: int) -> _QueuedNode:
        """Convert a processed child subtree into a queued work item."""
        return _QueuedNode(
            node=child_group.node,
            skills=child_group.skills,
            depth=depth,
            parent_context={
                "name": child_group.node.name,
                "description": child_group.node.description,
            },
        )

    def _process_node(
        self,
        node: TreeNode,
        skills: list[dict],
        depth: int,
        parent_context: Optional[dict],
        verbose: bool = False,
    ) -> list[_ChildGroup]:
        expansion_engine = getattr(self, "expansion_engine", None) or _TreeExpansionEngine(self._tree_builder())
        return expansion_engine.process_node(
            node=node,
            skills=skills,
            depth=depth,
            parent_context=parent_context,
            verbose=verbose,
        )

    # =========================================================================
    # Two-phase classification: discover groups -> flat assignment
    # =========================================================================

    def _assign_skills_to_leaf(self, node: TreeNode, skills: list[dict]) -> None:
        """Assign skill dicts to a leaf node as Skill objects. Updates progress counter."""
        for skill_data in skills:
            node.skills.append(self._skill_from_data(skill_data, path=node.id))
        # Update leaf skills count (thread-safe)
        with self.state.counter_lock:
            self.state.leaf_skills += len(skills)
            if self.state.progress and self.state.progress_task is not None:
                self.state.progress.update(
                    self.state.progress_task,
                    leaf=self.state.leaf_skills,
                    completed=self.state.leaf_skills,
                )

    @staticmethod
    def _skill_from_data(skill_data: dict, *, path: str) -> Skill:
        """Materialize a Skill object from a skill dict."""
        return Skill(
            id=skill_data["id"],
            name=skill_data.get("name", skill_data["id"]),
            description=skill_data.get("description", ""),
            path=path,
            skill_path=skill_data.get("skill_path", ""),
            content=skill_data.get("content", ""),
            select_when=skill_data.get("select_when", ""),
            dont_select_when=skill_data.get("dont_select_when", ""),
            source_description=skill_data.get("source_description", ""),
            github_url=skill_data.get("github_url", ""),
            stars=skill_data.get("stars", 0),
            is_official=skill_data.get("is_official", False),
            author=skill_data.get("author", ""),
        )

    @staticmethod
    def _skill_to_data(skill: Skill) -> dict:
        """Convert a Skill object back into a mutable skill dict."""
        return skill.to_dict(include_content=True)
