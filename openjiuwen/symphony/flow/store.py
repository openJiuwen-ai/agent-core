"""Flow 存储：证据追加与配方/能力包的版本化文件存储。"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from openjiuwen.symphony.flow.models import (
    ExperienceRecipe,
    RecipeEvidence,
    content_hash,
    utc_now_iso,
)

EVIDENCE_FILENAME = "evidence.jsonl"
RECIPES_DIRNAME = "recipes"
PACKAGES_DIRNAME = "packages"
CURRENT_FILENAME = "current.json"


class FlowStore:
    """文件存储：evidence.jsonl（追加、trace_id 幂等）与 recipes/<id>/vN.json。"""

    def __init__(self, flow_dir: str | Path) -> None:
        self.root = Path(flow_dir).resolve()
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)

    # ---------------- evidence ----------------

    def evidence_path(self) -> Path:
        return self.root / EVIDENCE_FILENAME

    def append_evidence(self, evidence: RecipeEvidence) -> bool:
        """按 trace_id 幂等追加；已存在时返回 False。"""

        with self._lock:
            if self.find_evidence(evidence.trace_id) is not None:
                return False
            payload = evidence.to_dict()
            if not payload["ingested_at"]:
                payload["ingested_at"] = utc_now_iso()
            with self.evidence_path().open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            return True

    def read_evidence(self) -> list[RecipeEvidence]:
        path = self.evidence_path()
        if not path.is_file():
            return []
        records: list[RecipeEvidence] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    records.append(RecipeEvidence.from_dict(payload))
        return records

    def find_evidence(self, trace_id: str) -> RecipeEvidence | None:
        return next(
            (item for item in self.read_evidence() if item.trace_id == trace_id),
            None,
        )

    # ---------------- recipes ----------------

    def recipe_dir(self, recipe_id: str) -> Path:
        return self.root / RECIPES_DIRNAME / recipe_id

    def recipe_version_path(self, recipe_id: str, version: int) -> Path:
        return self.recipe_dir(recipe_id) / f"v{version}.json"

    def recipe_current_path(self, recipe_id: str) -> Path:
        return self.recipe_dir(recipe_id) / CURRENT_FILENAME

    def save_recipe(self, recipe: ExperienceRecipe) -> ExperienceRecipe:
        """写入不可变版本文件并刷新 current 指针。"""

        with self._lock:
            directory = self.recipe_dir(recipe.recipe_id)
            directory.mkdir(parents=True, exist_ok=True)
            version_path = self.recipe_version_path(recipe.recipe_id, recipe.version)
            payload = recipe.to_dict()
            version_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.recipe_current_path(recipe.recipe_id).write_text(
                json.dumps(
                    {
                        "recipe_id": recipe.recipe_id,
                        "version": recipe.version,
                        "updated_at": utc_now_iso(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        return recipe

    def read_recipe(
        self,
        recipe_id: str,
        *,
        version: int | None = None,
    ) -> ExperienceRecipe | None:
        candidates = (
            [self.recipe_version_path(recipe_id, version)]
            if version is not None
            else self._recipe_version_files(recipe_id)
        )
        for path in candidates:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                return ExperienceRecipe.from_dict(payload)
        return None

    def list_recipe_ids(self) -> list[str]:
        recipes_root = self.root / RECIPES_DIRNAME
        if not recipes_root.is_dir():
            return []
        return sorted(
            child.name for child in recipes_root.iterdir() if child.is_dir() and self._recipe_version_files(child.name)
        )

    def _recipe_version_files(self, recipe_id: str) -> list[Path]:
        directory = self.recipe_dir(recipe_id)
        if not directory.is_dir():
            return []
        files: list[Path] = []
        for child in directory.iterdir():
            if not child.is_file():
                continue
            name = child.name
            if name.startswith("v") and name.endswith(".json") and name[1:-5].isdigit():
                files.append(child)
        return sorted(files, key=lambda item: int(item.name[1:-5]), reverse=True)

    # ---------------- packages ----------------

    def package_path(self, package_id: str) -> Path:
        return self.root / PACKAGES_DIRNAME / f"{package_id}.json"

    def save_package(self, package: dict[str, Any]) -> Path:
        with self._lock:
            path = self.package_path(str(package.get("package_id") or ""))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(package, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return path

    def read_package(self, package_id: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.package_path(package_id).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def list_packages(self) -> list[dict[str, Any]]:
        packages_root = self.root / PACKAGES_DIRNAME
        if not packages_root.is_dir():
            return []
        output = []
        for child in sorted(packages_root.iterdir()):
            if not child.is_file() or child.suffix != ".json":
                continue
            try:
                payload = json.loads(child.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                output.append(payload)
        return output

    # ---------------- artifacts ----------------

    def artifact_dir(self, package_id: str, target_kind: str) -> Path:
        directory = self.root / PACKAGES_DIRNAME / package_id / target_kind
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    @staticmethod
    def file_sha256(path: str | Path) -> str:
        data = Path(path).read_bytes()
        return content_hash(data.decode("utf-8", errors="replace"))
