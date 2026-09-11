"""Flow 存储：证据追加与配方/能力包的版本化文件存储。"""

from __future__ import annotations

import json
import os
import re
import tempfile
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
CURRENT_STATE_FILENAME = "current_state.json"
ACKNOWLEDGEMENTS_FILENAME = "candidate_acknowledgements.json"
DISTILLATION_STATE_FILENAME = "distillation_state.json"
REVIEWS_DIRNAME = "reviews"

_RECIPE_ID_PATTERN = re.compile(r"recipe_[0-9a-f]{12}\Z")
_PACKAGE_ID_PATTERN = re.compile(r"cap-[0-9a-f]{12}\Z")
_REVIEW_ID_PATTERN = re.compile(r"review_[0-9a-f]{12}\Z")
_PATH_SEGMENT_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


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

    def evidence_fingerprint(self, records: list[RecipeEvidence] | None = None) -> str:
        """Return a stable fingerprint of the accepted evidence content."""

        accepted = records if records is not None else self.read_evidence()
        return content_hash(
            [
                {
                    "trace_id": item.trace_id,
                    "query": item.query,
                    "outcome": item.outcome,
                    "graph": item.graph,
                }
                for item in accepted
                if item.outcome == "success"
            ]
        )

    def read_distillation_fingerprint(self) -> str | None:
        """Read the fingerprint successfully distilled most recently."""

        try:
            value = json.loads((self.root / DISTILLATION_STATE_FILENAME).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        fingerprint = value.get("evidence_fingerprint") if isinstance(value, dict) else None
        return str(fingerprint) if isinstance(fingerprint, str) and fingerprint else None

    def save_distillation_fingerprint(self, fingerprint: str) -> None:
        """Atomically persist one successfully completed distillation watermark."""

        _atomic_write_json(
            self.root / DISTILLATION_STATE_FILENAME,
            {"evidence_fingerprint": fingerprint, "updated_at": utc_now_iso()},
        )

    # ---------------- recipes ----------------

    def recipe_dir(self, recipe_id: str) -> Path:
        _validate_identifier(recipe_id, _RECIPE_ID_PATTERN, "recipe_id")
        return _safe_child(self.root, RECIPES_DIRNAME, recipe_id)

    def recipe_version_path(self, recipe_id: str, version: int) -> Path:
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("recipe version must be a positive integer")
        return self.recipe_dir(recipe_id) / f"v{version}.json"

    def recipe_current_path(self, recipe_id: str) -> Path:
        return self.recipe_dir(recipe_id) / CURRENT_FILENAME

    def recipe_current_state_path(self, recipe_id: str) -> Path:
        return self.recipe_dir(recipe_id) / CURRENT_STATE_FILENAME

    def save_recipe(self, recipe: ExperienceRecipe) -> ExperienceRecipe:
        """写入不可变版本文件并刷新 current 指针。"""

        with self._lock:
            directory = self.recipe_dir(recipe.recipe_id)
            directory.mkdir(parents=True, exist_ok=True)
            version_path = self.recipe_version_path(recipe.recipe_id, recipe.version)
            payload = recipe.to_dict()
            _atomic_write_json(version_path, payload)
            self._save_recipe_current(recipe)
        return recipe

    def update_recipe_current(self, recipe: ExperienceRecipe) -> ExperienceRecipe:
        """Refresh mutable quality/provenance state without creating a version."""

        with self._lock:
            self.recipe_dir(recipe.recipe_id).mkdir(parents=True, exist_ok=True)
            self._save_recipe_current(recipe)
        return recipe

    def _save_recipe_current(self, recipe: ExperienceRecipe) -> None:
        _atomic_write_json(self.recipe_current_state_path(recipe.recipe_id), recipe.to_dict())
        _atomic_write_json(
            self.recipe_current_path(recipe.recipe_id),
            {
                "recipe_id": recipe.recipe_id,
                "version": recipe.version,
                "updated_at": utc_now_iso(),
            },
        )

    def read_recipe(
        self,
        recipe_id: str,
        *,
        version: int | None = None,
    ) -> ExperienceRecipe | None:
        try:
            candidates = (
                [self.recipe_version_path(recipe_id, version)]
                if version is not None
                else [
                    self.recipe_current_state_path(recipe_id),
                    *self._recipe_version_files(recipe_id),
                ]
            )
        except ValueError:
            return None
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
        recipe_ids: list[str] = []
        for child in recipes_root.iterdir():
            if not child.is_dir() or child.is_symlink():
                continue
            if not _RECIPE_ID_PATTERN.fullmatch(child.name):
                continue
            if self._recipe_version_files(child.name):
                recipe_ids.append(child.name)
        return sorted(recipe_ids)

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
        _validate_identifier(package_id, _PACKAGE_ID_PATTERN, "package_id")
        return _safe_child(self.root, PACKAGES_DIRNAME, f"{package_id}.json")

    def save_package(self, package: dict[str, Any]) -> Path:
        with self._lock:
            path = self.package_path(str(package.get("package_id") or ""))
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(path, package)
        return path

    def save_review(self, review: dict[str, Any]) -> Path:
        with self._lock:
            package_id = str(review.get("package_id") or "")
            review_id = str(review.get("review_id") or "")
            _validate_identifier(package_id, _PACKAGE_ID_PATTERN, "package_id")
            _validate_identifier(review_id, _REVIEW_ID_PATTERN, "review_id")
            path = _safe_child(self.root, REVIEWS_DIRNAME, package_id, f"{review_id}.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(path, review)
            return path

    def read_reviews(self, package_id: str) -> list[dict[str, Any]]:
        try:
            _validate_identifier(package_id, _PACKAGE_ID_PATTERN, "package_id")
            directory = _safe_child(self.root, REVIEWS_DIRNAME, package_id)
        except ValueError:
            return []
        if not directory.is_dir():
            return []
        reviews: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                reviews.append(value)
        return reviews

    def candidate_acknowledgements_path(self) -> Path:
        return self.root / ACKNOWLEDGEMENTS_FILENAME

    def acknowledge_candidate(self, recipe_id: str, version: int) -> bool:
        """Persist a recipe_id+version delivery acknowledgement."""

        try:
            _validate_identifier(recipe_id, _RECIPE_ID_PATTERN, "recipe_id")
        except ValueError:
            return False
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            return False
        key = f"{recipe_id}:v{version}"
        with self._lock:
            path = self.candidate_acknowledgements_path()
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                value = []
            keys = {str(item) for item in value} if isinstance(value, list) else set()
            if key in keys:
                return False
            keys.add(key)
            _atomic_write_json(path, sorted(keys))
            return True

    def is_candidate_acknowledged(self, recipe_id: str, version: int) -> bool:
        try:
            _validate_identifier(recipe_id, _RECIPE_ID_PATTERN, "recipe_id")
        except ValueError:
            return False
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            return False
        key = f"{recipe_id}:v{version}"
        with self._lock:
            try:
                value = json.loads(self.candidate_acknowledgements_path().read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return False
            return isinstance(value, list) and key in {str(item) for item in value}

    def read_package(self, package_id: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.package_path(package_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def list_packages(self) -> list[dict[str, Any]]:
        packages_root = self.root / PACKAGES_DIRNAME
        if not packages_root.is_dir():
            return []
        output = []
        for child in sorted(packages_root.iterdir()):
            if not child.is_file() or child.is_symlink():
                continue
            if child.suffix != ".json" or not _PACKAGE_ID_PATTERN.fullmatch(child.stem):
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
        _validate_identifier(package_id, _PACKAGE_ID_PATTERN, "package_id")
        _validate_identifier(target_kind, _PATH_SEGMENT_PATTERN, "target_kind")
        return _safe_child(self.root, PACKAGES_DIRNAME, package_id, target_kind)

    @staticmethod
    def file_sha256(path: str | Path) -> str:
        data = Path(path).read_bytes()
        return content_hash(data.decode("utf-8", errors="replace"))


def _atomic_write_json(path: Path, value: Any) -> None:
    """Replace one JSON file atomically within its destination directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _validate_identifier(value: object, pattern: re.Pattern[str], field_name: str) -> str:
    text = str(value)
    if pattern.fullmatch(text) is None:
        raise ValueError(f"invalid {field_name}")
    return text


def _safe_child(root: Path, *parts: str) -> Path:
    """Resolve one child and reject both traversal and escaping symlinks."""

    base = root.resolve()
    candidate = base.joinpath(*parts).resolve()
    if os.path.commonpath((str(base), str(candidate))) != str(base):
        raise ValueError("storage path escapes flow root")
    return candidate
