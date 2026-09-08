"""SymphonyFlowEngine：经验沉淀与能力包安装准备。"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openjiuwen.symphony.flow.distill import (
    DistillResult,
    distill_group,
    group_by_structure,
    normalize_execution_graph,
    qualified_edges,
    recipe_provenance,
    topological_order,
)
from openjiuwen.symphony.flow.models import (
    OUTCOME_SUCCESS,
    RECIPE_STATUS_DEPRECATED,
    SUPPORTED_TARGET_KINDS,
    TARGET_KIND_SKILL,
    VERDICT_APPROVED,
    CombinationCandidate,
    ExperienceRecipe,
    InstallPreparation,
    RecipeEvidence,
    ReviewResult,
)
from openjiuwen.symphony.flow.narrative import distill_texts
from openjiuwen.symphony.flow.packager import CapabilityPackager
from openjiuwen.symphony.flow.privacy import sanitize_distilled_text
from openjiuwen.symphony.flow.render import render_package
from openjiuwen.symphony.flow.review import PackageReviewGate
from openjiuwen.symphony.flow.store import FlowStore
from openjiuwen.symphony.orchestration.config import SymphonyFlowConfig


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
        self.config = config or SymphonyFlowConfig()
        self.llm_client = llm_client
        self.store = store or FlowStore(flow_dir)
        self.packager = packager or CapabilityPackager()
        self.gate = gate or PackageReviewGate()
        self.skill_adapter = skill_adapter
        self._worker_task: asyncio.Task[None] | None = None
        self._pending_waiters: list[asyncio.Future[tuple[CombinationCandidate, ...]]] = []
        self._retry_needed = False
        self._last_worker_error: BaseException | None = None
        self._closed = False
        self._artifact_locks: dict[tuple[str, str], asyncio.Lock] = {}

    # ---------------- ingest ----------------

    def ingest(self, payload: dict[str, Any]) -> bool:
        """接入一条执行图证据（trace_id 幂等）；规范化失败返回 False。"""

        if payload.get("outcome") != OUTCOME_SUCCESS:
            return False
        evidence = normalize_execution_graph(payload)
        if evidence is None:
            return False
        return self.store.append_evidence(evidence)

    async def submit(self, payload: dict[str, Any]) -> tuple[CombinationCandidate, ...]:
        """Ingest evidence and coalesce distillation through one async worker."""

        if self._closed:
            raise RuntimeError("SymphonyFlowEngine is closed")
        if payload.get("outcome") != OUTCOME_SUCCESS:
            return ()
        inserted = self.ingest(payload)
        if not inserted:
            if self._worker_task is not None and not self._worker_task.done():
                return await self._schedule_distillation()
            if self._retry_needed or self._evidence_changed_since_distillation():
                return await self._schedule_distillation()
            candidates = self._unacknowledged_current_candidates()
            if candidates:
                return candidates
            return ()
        return await self._schedule_distillation()

    async def start(self) -> tuple[CombinationCandidate, ...]:
        """Lazily recover persisted evidence through the normal worker path."""

        if self._closed:
            raise RuntimeError("SymphonyFlowEngine is closed")
        if not self.store.read_evidence():
            return ()
        if self._worker_task is not None and not self._worker_task.done():
            return await self._schedule_distillation()
        if self._retry_needed or self._evidence_changed_since_distillation():
            return await self._schedule_distillation()
        candidates = self._unacknowledged_current_candidates()
        if candidates:
            return candidates
        return ()

    async def _schedule_distillation(self) -> tuple[CombinationCandidate, ...]:
        future = asyncio.get_running_loop().create_future()
        self._pending_waiters.append(future)
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._drain_distillation())
        return await future

    async def _drain_distillation(self) -> None:
        await asyncio.sleep(0)
        offered_keys: set[tuple[str, int]] = set()
        while self._pending_waiters:
            waiters, self._pending_waiters = self._pending_waiters, []
            if not self._retry_needed and not self._evidence_changed_since_distillation():
                for waiter in waiters:
                    if not waiter.done():
                        waiter.set_result(())
                continue
            try:
                report = await self.distill()
                candidates = tuple(
                    candidate
                    for candidate in self._new_verified_candidates(report)
                    if (candidate.recipe_id, candidate.version) not in offered_keys
                )
                offered_keys.update((candidate.recipe_id, candidate.version) for candidate in candidates)
            except BaseException as exc:
                self._retry_needed = True
                self._last_worker_error = exc
                failed_waiters = [*waiters, *self._pending_waiters]
                self._pending_waiters = []
                for waiter in failed_waiters:
                    if not waiter.done():
                        waiter.set_exception(exc)
                return
            self._retry_needed = False
            self._last_worker_error = None
            for index, waiter in enumerate(waiters):
                if not waiter.done():
                    waiter.set_result(candidates if index == 0 else ())

    def _evidence_changed_since_distillation(self) -> bool:
        return self.store.evidence_fingerprint() != self.store.read_distillation_fingerprint()

    def _unacknowledged_current_candidates(self) -> tuple[CombinationCandidate, ...]:
        return tuple(
            candidate
            for recipe_id in self.list_recipes()
            if (candidate := self.get_candidate(recipe_id)) is not None
            and not self.store.is_candidate_acknowledged(candidate.recipe_id, candidate.version)
        )

    def _new_verified_candidates(self, report: DistillReport) -> tuple[CombinationCandidate, ...]:
        candidates: list[CombinationCandidate] = []
        for recipe_id in dict.fromkeys([*report.recipes_saved, *report.recipes_unchanged]):
            recipe = self.get_recipe(recipe_id)
            if recipe is None or recipe.status != "active" or recipe.grade != "verified":
                continue
            if not self.store.is_candidate_acknowledged(recipe.recipe_id, recipe.version):
                candidates.append(_candidate_from_recipe(recipe))
        return tuple(candidates)

    async def flush(self) -> None:
        """Wait until all distillation work scheduled so far has completed."""

        task = self._worker_task
        if task is not None:
            await asyncio.shield(task)
        if self._last_worker_error is not None:
            raise RuntimeError("Symphony flow distillation worker failed") from self._last_worker_error

    async def close(self) -> None:
        """Drain all work and reject future submissions."""

        self._closed = True
        try:
            await self.flush()
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Symphony flow close observed worker failure (%s)",
                type(exc).__name__,
            )

    # ---------------- distill ----------------

    async def distill(self) -> DistillReport:
        """批处理蒸馏：全局边统计 → 结构分组 → 叙事 → 版本化落盘。

        分组键 = 轨迹【成功边】∩ 全局合格边（失败边只参与边质量统计、
        不参与分组）；组内每条轨迹都完整走过 pack 全部边，组合级统计
        归因严格——靠被剔边成功的轨迹不会给新 pack 虚增成功经验。
        """

        records = [record for record in self.store.read_evidence() if record.outcome == OUTCOME_SUCCESS]
        report = DistillReport(evidence_total=len(records))
        qualified, stats = qualified_edges(
            records,
            min_edge_support=getattr(self.config, "min_edge_support", 1),
            min_edge_success_rate=getattr(self.config, "min_edge_success_rate", 0.8),
        )
        groups = group_by_structure(records, qualified)
        report.groups_total = len(groups)
        grouped_traces = {record.trace_id for group in groups.values() for record in group.records}
        report.groups_without_structure = [
            record.trace_id for record in records if record.trace_id not in grouped_traces
        ]
        max_examples = getattr(self.config, "max_narrative_examples", 5)

        for _signature, group in sorted(groups.items()):
            result = distill_group(
                group,
                stats,
                min_successes_candidate=getattr(self.config, "min_successes_candidate", 1),
                min_successes_verified=getattr(self.config, "min_successes_verified", 1),
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
        self.store.save_distillation_fingerprint(self.store.evidence_fingerprint(records))
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
        provenance = recipe_provenance(result, records)
        provenance["sample_queries"] = [
            sanitize_distilled_text(query) for query in provenance.get("sample_queries", ())
        ]
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
                **provenance,
                "narrative_source": texts["narrative_source"],
            },
        )

    def _save_recipe_versioned(self, recipe: ExperienceRecipe) -> bool:
        """内容未变化时不产生新版本；返回是否落盘。"""

        existing = self.store.read_recipe(recipe.recipe_id)
        if existing is not None and existing.content_identity() == recipe.content_identity():
            recipe.version = existing.version
            self.store.update_recipe_current(recipe)
            return False
        if existing is not None and existing.status == RECIPE_STATUS_DEPRECATED:
            return False
        self.store.save_recipe(recipe)
        return True

    # ---------------- prepare install ----------------

    async def review_and_prepare_install(
        self,
        recipe_id: str,
        *,
        recipe_version: int,
        target_kind: str = TARGET_KIND_SKILL,
    ) -> InstallPreparation:
        """Explicitly review one immutable recipe version, then render if approved."""

        if target_kind not in SUPPORTED_TARGET_KINDS:
            return InstallPreparation(
                recipe_id=recipe_id,
                target_kind=target_kind,
                verdict="rejected",
                reasons=[f"unsupported target_kind: {target_kind}"],
            )
        current = self.store.read_recipe(recipe_id)
        versioned = self.store.read_recipe(recipe_id, version=recipe_version)
        if current is None or versioned is None:
            return InstallPreparation(
                recipe_id=recipe_id,
                target_kind=target_kind,
                verdict="rejected",
                reasons=[f"recipe not found: {recipe_id} v{recipe_version}"],
            )
        if current.version != recipe_version:
            return InstallPreparation(
                recipe_id=recipe_id,
                target_kind=target_kind,
                verdict="rejected",
                reasons=[f"stale recipe version: current=v{current.version}, requested=v{recipe_version}"],
            )
        recipe = current
        try:
            package = self.packager.build_package(recipe, target_kind=target_kind)
        except Exception as exc:
            return InstallPreparation(
                recipe_id=recipe_id,
                target_kind=target_kind,
                verdict="rejected",
                reasons=[str(exc)],
            )

        artifact_dir = self.store.artifact_dir(str(package["package_id"]), target_kind)
        artifact_lock = self._artifact_locks.setdefault(
            (str(package["package_id"]), target_kind),
            asyncio.Lock(),
        )
        async with artifact_lock:
            review = await self.gate.review(package, artifact_dir=artifact_dir)
            self.store.save_review(review.to_dict())
            if review.verdict != VERDICT_APPROVED:
                _remove_artifact_path(artifact_dir)
                return InstallPreparation(
                    recipe_id=recipe_id,
                    target_kind=target_kind,
                    verdict=review.verdict,
                    package=package,
                    review=review,
                    reasons=review.failure_reasons(),
                )
            try:
                self._publish_approved_package(package, artifact_dir)
            except Exception as exc:
                return InstallPreparation(
                    recipe_id=recipe_id,
                    target_kind=target_kind,
                    verdict="rejected",
                    package=package,
                    review=review,
                    reasons=[str(exc)],
                )
            return InstallPreparation(
                recipe_id=recipe_id,
                target_kind=target_kind,
                verdict=VERDICT_APPROVED,
                package=package,
                artifact_dir=str(artifact_dir),
                review=review,
            )

    async def prepare_install(
        self,
        recipe_id: str,
        *,
        recipe_version: int,
        target_kind: str = TARGET_KIND_SKILL,
    ) -> InstallPreparation:
        """Compatibility alias for the explicit installation-preparation entry."""

        return await self.review_and_prepare_install(
            recipe_id,
            recipe_version=recipe_version,
            target_kind=target_kind,
        )

    def _publish_approved_package(self, package: dict[str, Any], artifact_dir: Path) -> None:
        """Render, publish, and persist one package within a rollback boundary."""

        artifact_dir.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{artifact_dir.name}.", dir=artifact_dir.parent))
        backup_root: Path | None = None
        backup: Path | None = None
        published = False
        remove_backup = True
        try:
            adapter = self.skill_adapter
            if adapter is None:
                render_package(package, temporary)
            else:
                adapter.render(package, temporary)
            if os.path.lexists(artifact_dir):
                backup_root = Path(tempfile.mkdtemp(prefix=f".{artifact_dir.name}.backup.", dir=artifact_dir.parent))
                backup = backup_root / "previous"
                os.replace(artifact_dir, backup)
            os.replace(temporary, artifact_dir)
            published = True
            self.store.save_package(package)
        except BaseException:
            if published:
                _remove_artifact_path(artifact_dir)
            if backup is not None and os.path.lexists(backup) and not os.path.lexists(artifact_dir):
                try:
                    os.replace(backup, artifact_dir)
                except BaseException:
                    remove_backup = False
                    raise
            elif backup is not None and os.path.lexists(backup):
                remove_backup = False
            raise
        finally:
            _remove_artifact_path(temporary)
            if backup_root is not None and remove_backup:
                _remove_artifact_path(backup_root)

    # ---------------- query ----------------

    def get_recipe(self, recipe_id: str) -> ExperienceRecipe | None:
        return self.store.read_recipe(recipe_id)

    def list_recipes(self) -> list[str]:
        return self.store.list_recipe_ids()

    def get_candidate(self, recipe_id: str, *, version: int | None = None) -> CombinationCandidate | None:
        """Return a public display snapshot for an active verified recipe."""

        recipe = (
            self.store.read_recipe(recipe_id, version=version) if version is not None else self.get_recipe(recipe_id)
        )
        if recipe is None or recipe.status != "active" or recipe.grade != "verified":
            return None
        return _candidate_from_recipe(recipe)

    def list_candidates(self) -> tuple[CombinationCandidate, ...]:
        """Return all current active verified candidates as immutable snapshots."""

        return tuple(
            candidate for recipe_id in self.list_recipes() if (candidate := self.get_candidate(recipe_id)) is not None
        )

    def acknowledge_candidate(self, recipe_id: str, version: int) -> bool:
        """Acknowledge successful Host Event delivery for one candidate version."""

        current = self.get_recipe(recipe_id)
        if current is None or current.version != version or current.status != "active" or current.grade != "verified":
            return False
        return self.store.acknowledge_candidate(recipe_id, version)

    def review_history(self, package_id: str) -> list[ReviewResult]:
        """评审结果按包存储；v1 返回最近一次（幂等输入 → 幂等评审）。"""

        return [ReviewResult.from_dict(value) for value in self.store.read_reviews(package_id)]

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


def _candidate_from_recipe(recipe: ExperienceRecipe) -> CombinationCandidate:
    applicability = recipe.applicability
    summary = " — ".join(
        part
        for part in (
            str(applicability.get("task_description") or "").strip(),
            str(applicability.get("trigger_conditions") or "").strip(),
        )
        if part
    )
    edges = recipe.combination_structure.get("edges") or ()
    structure = tuple(
        (
            str(edge.get("source") or ""),
            str(edge.get("target") or ""),
            str(edge.get("relation") or "can_feed"),
        )
        for edge in edges
        if isinstance(edge, dict)
    )
    capability_ids = topological_order(recipe.combination_structure)
    return CombinationCandidate(
        recipe_id=recipe.recipe_id,
        version=recipe.version,
        name=" → ".join(capability_ids) or recipe.recipe_id,
        applicability=summary,
        structure=structure,
        execution_count=int(recipe.quality.get("execution_count") or 0),
        success_count=int(recipe.quality.get("success_count") or 0),
        success_rate=float(recipe.quality.get("pack_success_rate") or 0.0),
    )


def _remove_artifact_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
