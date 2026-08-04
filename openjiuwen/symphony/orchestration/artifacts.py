"""Versioned, atomic graph artifact storage for online orchestration."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from openjiuwen.symphony.orchestration.contracts import GraphArtifactStatus, GraphBuildResult

SCHEMA_VERSION = "1.0"
SUPPORTED_SCHEMA_MAJOR = 1


ArtifactStatus = GraphArtifactStatus
ArtifactBuild = GraphBuildResult


@dataclass(frozen=True)
class GraphArtifacts:
    """Internal compatibility view consumed by the migrated planners."""

    graph_dir: Path
    manifest: dict[str, Any]
    skills: list[dict[str, Any]]
    graph: dict[str, Any]
    lookup: dict[str, Any]
    io_name_vocab: dict[str, Any] | None = None

    @property
    def skill_by_id(self) -> dict[str, dict[str, Any]]:
        return {item["id"]: item for item in self.skills if item.get("id")}


class GraphArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def status(self, *, expected_snapshot: dict[str, Any] | None = None, building: bool = False) -> ArtifactStatus:
        try:
            current = self._read_current()
            payload = self.read()
        except FileNotFoundError:
            return ArtifactStatus(exists=False, fresh=False, building=building)
        snapshot = payload.get("source_snapshot")
        return ArtifactStatus(
            exists=True,
            fresh=expected_snapshot is None or snapshot == expected_snapshot,
            version=str(current.get("version") or "") or None,
            generated_at=str(payload.get("generated_at") or "") or None,
            schema_version=str(payload.get("schema_version") or "") or None,
            building=building,
        )

    def read(self, version: str | None = None) -> dict[str, Any]:
        if version is None:
            current = self._read_current()
            version = str(current.get("version") or "")
        if not version or not re.fullmatch(r"[A-Za-z0-9._-]+", version):
            raise ValueError("Invalid Symphony graph artifact version.")
        path = self.root / "versions" / version / "graph.json"
        payload = _read_json(path)
        _validate_schema(payload)
        return payload

    def stage(self, payload: dict[str, Any], *, version: str) -> ArtifactBuild:
        """Materialize an immutable version without switching ``current.json``."""

        self.root.mkdir(parents=True, exist_ok=True)
        runs = self.root / ".build_runs"
        versions = self.root / "versions"
        runs.mkdir(parents=True, exist_ok=True)
        versions.mkdir(parents=True, exist_ok=True)
        run_dir = runs / f"{version}-{uuid4().hex}"
        run_dir.mkdir()
        _write_json_atomic(run_dir / "graph.json", payload)
        version_dir = versions / version
        if version_dir.exists():
            raise FileExistsError(f"Symphony graph version already exists: {version}")
        os.replace(run_dir, version_dir)
        return ArtifactBuild(
            version=version,
            graph_path=version_dir / "graph.json",
            generated_at=str(payload["generated_at"]),
        )

    def activate(self, artifact: ArtifactBuild) -> ArtifactBuild:
        """Atomically make a fully staged version current."""

        if not artifact.graph_path.is_file():
            raise FileNotFoundError(f"Missing staged Symphony graph artifact: {artifact.graph_path}")
        current = {
            "schema_version": SCHEMA_VERSION,
            "version": artifact.version,
            "generated_at": artifact.generated_at,
            "artifact": f"versions/{artifact.version}/graph.json",
        }
        _write_json_atomic(self.root / "current.json", current)
        return artifact

    def publish(self, payload: dict[str, Any], *, version: str) -> ArtifactBuild:
        """Synchronous compatibility helper for callers without cancellation."""

        return self.activate(self.stage(payload, version=version))

    def _read_current(self) -> dict[str, Any]:
        current = _read_json(self.root / "current.json")
        _validate_schema(current)
        return current


def load_graph_artifacts(root: str | Path) -> GraphArtifacts:
    payload = GraphArtifactStore(root).read()
    capabilities = []
    for item in payload.get("capabilities", []):
        capability = dict(item)
        capability["id"] = capability.get("capability_id") or capability.get("id")
        capability["type"] = capability.get("capability_type") or capability.get("type")
        capabilities.append(capability)
    return GraphArtifacts(
        graph_dir=Path(root),
        manifest=dict(payload.get("config") or {}),
        skills=capabilities,
        graph={"nodes": list(payload.get("nodes") or []), "edges": list(payload.get("edges") or [])},
        lookup=dict(payload.get("lookup") or {}),
    )


def filter_disabled_graph_artifacts(
    artifacts: GraphArtifacts,
    disabled_capability_names: Sequence[str] | None,
) -> GraphArtifacts:
    disabled = {_normalize_ref(item) for item in disabled_capability_names or [] if _normalize_ref(item)}
    if not disabled:
        return artifacts
    disabled_ids = {
        str(item.get("id") or "")
        for item in artifacts.skills
        if _normalize_ref(item.get("id")) in disabled or _normalize_ref(item.get("name")) in disabled
    }
    skills = [item for item in artifacts.skills if str(item.get("id") or "") not in disabled_ids]
    edges = [
        item
        for item in artifacts.graph.get("edges", [])
        if _node_id(item.get("source")) not in disabled_ids and _node_id(item.get("target")) not in disabled_ids
    ]
    nodes = [item for item in artifacts.graph.get("nodes", []) if _node_id(item.get("id")) not in disabled_ids]
    lookup = _filter_lookup(artifacts.lookup, disabled_ids)
    return GraphArtifacts(
        graph_dir=artifacts.graph_dir,
        manifest=artifacts.manifest,
        skills=skills,
        graph={"nodes": nodes, "edges": edges},
        lookup=lookup,
        io_name_vocab=artifacts.io_name_vocab,
    )


def _filter_lookup(value: Any, disabled: set[str]) -> Any:
    if isinstance(value, dict):
        return {key: _filter_lookup(item, disabled) for key, item in value.items() if _node_id(key) not in disabled}
    if isinstance(value, list):
        return [_filter_lookup(item, disabled) for item in value if _node_id(item) not in disabled]
    return value


def _node_id(value: Any) -> str:
    text = str(value or "")
    return text.removeprefix("skill:").removeprefix("capability:")


def _normalize_ref(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _node_id(value).strip().lower()).strip("-")


def _validate_schema(payload: dict[str, Any]) -> None:
    version = str(payload.get("schema_version") or "")
    try:
        major = int(version.split(".", 1)[0])
    except ValueError as exc:
        raise ValueError(f"Invalid Symphony graph schema_version: {version!r}") from exc
    if major != SUPPORTED_SCHEMA_MAJOR:
        raise ValueError(f"Unsupported Symphony graph schema major version: {major}")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing Symphony graph artifact: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Symphony graph artifact must be an object: {path}")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
