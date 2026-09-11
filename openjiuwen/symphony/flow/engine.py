"""SymphonyFlowEngine：经验沉淀与能力包安装准备。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openjiuwen.symphony.flow.distill import (
    DistillResult,
    distill_group,
    group_by_structure,
    normalize_execution_graph,
    qualified_edges,
    recipe_provenance,
)
from openjiuwen.symphony.flow.models import (
    RECIPE_STATUS_DEPRECATED,
    SUPPORTED_TARGET_KINDS,
    TARGET_KIND_SKILL,
    VERDICT_APPROVED,
    ExperienceRecipe,
    InstallPreparation,
    RecipeEvidence,
    ReviewResult,
)
from openjiuwen.symphony.flow.narrative import distill_texts
from openjiuwen.symphony.flow.packager import CapabilityPackager
from openjiuwen.symphony.flow.render import render_package
from openjiuwen.symphony.flow.review import PackageReviewGate
from openjiuwen.symphony.flow.store import FlowStore


@dataclass
class DistillReport:
    """一次批处理蒸馏的汇总。"""

    evidence_total: int = 0
    groups_total: int = 0
    groups_without_structure: list[str] = field(default_factory=list)
    recipes_saved: list[str] = field(default_factory=list)
    recipes_unchanged: list[str] = field(default_factory=list)


class SymphonyFlowEngine:
    """经验沉淀与能力包安装准备；不执行用户任务，也不做本地安装。"""

    def __init__(
        self,
        flow_dir: str | Any,
        *,
        config: Any | None = None,
        llm_client: Any | None = None,
        store: FlowStore | None = None,
        packager: CapabilityPackager | None = None,
        gate: PackageReviewGate | None = None,
        skill_adapter: Any | None = None,
    ) -> None:
        self.config = config
        self.llm_client = llm_client
        self.store = store or FlowStore(flow_dir)
        self.packager = packager or CapabilityPackager()
        self.gate = gate or PackageReviewGate()
        self.skill_adapter = skill_adapter

    # ---------------- ingest ----------------

    def ingest(self, payload: dict[str, Any]) -> bool:
        """接入一条执行图证据（trace_id 幂等）；规范化失败返回 False。"""

        evidence = normalize_execution_graph(payload)
        if evidence is None:
            return False
        return self.store.append_evidence(evidence)

    # ---------------- distill ----------------

    async def distill(self) -> DistillReport:
        """批处理蒸馏：全局边统计 → 结构分组 → 叙事 → 版本化落盘。

        分组键 = 轨迹【成功边】∩ 全局合格边（失败边只参与边质量统计、
        不参与分组）；组内每条轨迹都完整走过 pack 全部边，组合级统计
        归因严格——靠被剔边成功的轨迹不会给新 pack 虚增成功经验。
        """

        records = self.store.read_evidence()
        report = DistillReport(evidence_total=len(records))
        qualified, stats = qualified_edges(
            records,
            min_edge_support=getattr(self.config, "min_edge_support", 2),
            min_edge_success_rate=getattr(self.config, "min_edge_success_rate", 0.8),
        )
        groups = group_by_structure(records, qualified)
        report.groups_total = len(groups)
        grouped_traces = {record.trace_id for group in groups.values() for record in group.records}
        report.groups_without_structure = [
            record.trace_id for record in records if record.trace_id not in grouped_traces
        ]
        max_examples = getattr(self.config, "max_narrative_examples", 5)

        for signature, group in sorted(groups.items()):
            result = distill_group(
                group,
                stats,
                min_successes_candidate=getattr(self.config, "min_successes_candidate", 3),
                min_successes_verified=getattr(self.config, "min_successes_verified", 5),
                min_pack_success_rate_verified=getattr(self.config, "min_pack_success_rate_verified", 0.8),
            )
            recipe = await self._build_recipe(
                result,
                group.records,
                max_examples=max_examples,
            )
            saved = self._save_recipe_versioned(recipe)
            if saved:
                report.recipes_saved.append(recipe.recipe_id)
            else:
                report.recipes_unchanged.append(recipe.recipe_id)
        return report

    async def _build_recipe(
        self,
        result: DistillResult,
        records: list[RecipeEvidence],
        *,
        max_examples: int,
    ) -> ExperienceRecipe:
        texts = await distill_texts(
            records,
            skill_pack=result.skill_pack,
            max_examples=max_examples,
            llm_client=self.llm_client,
        )
        existing = self.store.read_recipe(result.recipe_id)
        version = (existing.version + 1) if existing else 1
        return ExperienceRecipe(
            recipe_id=result.recipe_id,
            version=version,
            status=result.status,
            grade=result.grade,
            applicability=texts["applicability"],
            combination_structure=result.skill_pack,
            execution_narrative=texts["execution_narrative"],
            quality=result.quality,
            provenance={
                **recipe_provenance(result, records),
                "narrative_source": texts["narrative_source"],
            },
        )

    def _save_recipe_versioned(self, recipe: ExperienceRecipe) -> bool:
        """内容未变化时不产生新版本；返回是否落盘。"""

        existing = self.store.read_recipe(recipe.recipe_id)
        if existing is not None and existing.content_identity() == recipe.content_identity():
            return False
        if existing is not None and existing.status == RECIPE_STATUS_DEPRECATED:
            return False
        self.store.save_recipe(recipe)
        return True

    # ---------------- prepare install ----------------

    def prepare_install(
        self,
        recipe_id: str,
        *,
        target_kind: str = TARGET_KIND_SKILL,
    ) -> InstallPreparation:
        """打包 + 渲染 + 静态评审；approved 时产物已写入 artifact 目录。"""

        if target_kind not in SUPPORTED_TARGET_KINDS:
            return InstallPreparation(
                recipe_id=recipe_id,
                target_kind=target_kind,
                verdict="rejected",
                reasons=[f"unsupported target_kind: {target_kind}"],
            )
        recipe = self.store.read_recipe(recipe_id)
        if recipe is None:
            return InstallPreparation(
                recipe_id=recipe_id,
                target_kind=target_kind,
                verdict="rejected",
                reasons=[f"recipe not found: {recipe_id}"],
            )
        try:
            package = self.packager.build_package(recipe, target_kind=target_kind)
        except ValueError as exc:
            return InstallPreparation(
                recipe_id=recipe_id,
                target_kind=target_kind,
                verdict="rejected",
                reasons=[str(exc)],
            )

        self.store.save_package(package)
        artifact_dir = self.store.artifact_dir(str(package["package_id"]), target_kind)
        try:
            render_package(package, artifact_dir)
        except ValueError as exc:
            return InstallPreparation(
                recipe_id=recipe_id,
                target_kind=target_kind,
                verdict="rejected",
                package=package,
                reasons=[str(exc)],
            )
        review = self.gate.review(package, artifact_dir=artifact_dir)
        if review.verdict != VERDICT_APPROVED:
            return InstallPreparation(
                recipe_id=recipe_id,
                target_kind=target_kind,
                verdict=review.verdict,
                package=package,
                artifact_dir=str(artifact_dir),
                review=review,
                reasons=review.failure_reasons(),
            )
        return InstallPreparation(
            recipe_id=recipe_id,
            target_kind=target_kind,
            verdict=VERDICT_APPROVED,
            package=package,
            artifact_dir=str(artifact_dir),
            review=review,
        )

    # ---------------- query ----------------

    def get_recipe(self, recipe_id: str) -> ExperienceRecipe | None:
        return self.store.read_recipe(recipe_id)

    def list_recipes(self) -> list[str]:
        return self.store.list_recipe_ids()

    def review_history(self, package_id: str) -> list[ReviewResult]:
        """评审结果按包存储；v1 返回最近一次（幂等输入 → 幂等评审）。"""

        package = self.store.read_package(package_id)
        if package is None:
            return []
        review = self.gate.review(
            package, artifact_dir=self.store.artifact_dir(package_id, str(package.get("target_kind")))
        )
        return [review]

    @classmethod
    def from_symphony_config(
        cls,
        flow_dir: str | Any,
        config: Any,
        *,
        llm_client: Any | None = None,
    ) -> "SymphonyFlowEngine":
        return cls(flow_dir, config=config, llm_client=llm_client)


__all__ = [
    "DistillReport",
    "SymphonyFlowEngine",
]
