"""Live in-memory Skill directory backed by Symphony's retriever tree."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

import portalocker

from openjiuwen.symphony.retrieval.build.io import load_tree_preset
from openjiuwen.symphony.retrieval.common.models import RetrieverItem, RetrieverNode
from openjiuwen.symphony.retrieval.search.artifacts import CatalogRecord, build_tree_root, load_retriever_index
from openjiuwen.symphony.retrieval.search.runtime.lexical import LexicalDocument, LexicalIndex

from .config import DiscoverySettings
from .models import SkillInventory, SkillRecord, inventory_from_records, sanitize_model_text


SkillRecordsProvider = Callable[[], Iterable[SkillRecord]]
VisibleSkillNames = set[str] | frozenset[str] | Callable[[], set[str] | frozenset[str] | None] | None
_PINNED_INDEX_SNAPSHOT_UNSET = object()
_INDEX_LOCK_FILENAME = ".skill-index.publish.lock"
_OVERLAY_SEGMENT = "newly_installed_skills"
_OVERLAY_NODE_ID = "__SYMPHONY_LIVE_SKILL_OVERLAY__"
_SAFE_SEGMENT = re.compile(r"[^0-9A-Za-z._+\-\u3400-\u9fff]+")
_LEXICAL_CACHE_LOCK = threading.Lock()
_LEXICAL_CACHE: OrderedDict[str, LexicalIndex] = OrderedDict()
_LEXICAL_CACHE_MAX_ENTRIES = 4
_INDEX_SNAPSHOT_CACHE: OrderedDict[tuple[str, tuple[tuple[int, int, int, int], ...]], SkillIndexSnapshot] = (
    OrderedDict()
)
_INDEX_SNAPSHOT_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True)
class SkillIndexSnapshot:
    """Serializable taxonomy generation pinned by one logical session."""

    nodes: tuple[dict[str, Any], ...]
    record_hashes: tuple[tuple[str, str], ...]
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [dict(node) for node in self.nodes],
            "record_hashes": dict(self.record_hashes),
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SkillIndexSnapshot":
        if not isinstance(payload, Mapping):
            raise TypeError("pinned index snapshot must be a mapping")
        raw_nodes = payload.get("nodes")
        raw_hashes = payload.get("record_hashes")
        fingerprint = str(payload.get("fingerprint") or "").strip()
        if not isinstance(raw_nodes, list) or any(not isinstance(node, Mapping) for node in raw_nodes):
            raise ValueError("pinned index snapshot nodes must be an array of objects")
        if (
            not isinstance(raw_hashes, Mapping)
            or not raw_hashes
            or any(not str(key).strip() or not str(value).strip() for key, value in raw_hashes.items())
        ):
            raise ValueError("pinned index snapshot record_hashes must be a non-empty string mapping")
        if not fingerprint:
            raise ValueError("pinned index snapshot fingerprint is required")
        nodes = tuple(dict(node) for node in raw_nodes)
        record_hashes = tuple(sorted((str(key), str(value)) for key, value in raw_hashes.items()))
        _validate_taxonomy_nodes(nodes, expected_worker_ids={key for key, _ in record_hashes})
        return cls(
            nodes=nodes,
            record_hashes=record_hashes,
            fingerprint=fingerprint,
        )


@dataclass(frozen=True)
class SkillFSArtifact:
    """Current live inventory projected through one taxonomy generation."""

    root: Path
    layout: str
    index_state: str
    inventory: SkillInventory
    items: tuple[SkillRecord, ...]
    fingerprint: str


@dataclass(frozen=True)
class SkillPromptEntry:
    worker_id: str
    description: str
    source: str


@dataclass(frozen=True)
class SkillPromptBranch:
    path: str
    label: str
    description: str


@dataclass(frozen=True)
class SkillPromptSnapshot:
    mode: str
    total_count: int
    entries: tuple[SkillPromptEntry, ...]
    estimated_candidate_tokens: int
    candidate_budget_tokens: int
    index_state: str
    branches: tuple[SkillPromptBranch, ...] = ()
    omitted_branch_count: int = 0

    @property
    def all_candidates_included(self) -> bool:
        return self.mode == "small"

    @property
    def omitted_count(self) -> int:
        return max(0, self.total_count - len(self.entries))


@dataclass(frozen=True)
class DirectoryEntry:
    """One model-visible row in the in-memory directory."""

    path: str
    kind: str
    label: str
    description: str
    worker_id: str = ""
    depth: int = 0


class SkillDirectoryView:
    """Path projection of a standard ``RetrieverNode`` tree."""

    def __init__(
        self,
        *,
        root: RetrieverNode,
        records: tuple[SkillRecord, ...],
        catalog_by_payload: Mapping[str, CatalogRecord],
    ) -> None:
        self.root = root
        self.record_by_id = {record.worker_id: record for record in records}
        self._catalog_by_payload = dict(catalog_by_payload)
        self.node_by_path: dict[str, RetrieverNode] = {"/": root}
        self.record_path_by_id: dict[str, str] = {}
        self.record_id_by_meta_path: dict[str, str] = {}
        self._children: dict[str, tuple[DirectoryEntry, ...]] = {}
        self._index_paths()

    @staticmethod
    def normalize_path(value: str) -> str:
        raw = str(value or "/").strip() or "/"
        path = PurePosixPath("/" + raw.lstrip("/"))
        if ".." in path.parts:
            raise ValueError("Skill directory paths must not contain '..'")
        normalized = "/" + "/".join(part for part in path.parts if part != "/")
        return normalized.rstrip("/") or "/"

    def children(self, path: str) -> tuple[DirectoryEntry, ...]:
        normalized = self.normalize_path(path)
        if normalized not in self.node_by_path:
            raise ValueError(f"No such Skill directory: {normalized}")
        return self._children.get(normalized, ())

    def entries(
        self,
        paths: Iterable[str],
        *,
        recursive: bool,
        max_depth: int | None = None,
        directories_only: bool = False,
        directory_entry: bool = False,
    ) -> tuple[DirectoryEntry, ...]:
        return self._scoped_entries(
            paths,
            max_depth,
            include_root=directory_entry,
            recursive=None if directory_entry else recursive,
            directories_only=directories_only,
        )

    def tree_entries(
        self,
        paths: Iterable[str],
        *,
        max_depth: int | None = None,
        directories_only: bool = False,
    ) -> tuple[DirectoryEntry, ...]:
        """Return each scope root followed by its descendants in tree order."""

        return self._scoped_entries(paths, max_depth, directories_only=directories_only)

    def searchable_entries(
        self,
        paths: Iterable[str],
        *,
        max_depth: int | None = None,
    ) -> tuple[DirectoryEntry, ...]:
        """Return visible directory and Skill entries in the requested scopes."""

        return self._scoped_entries(paths, max_depth, allow_metadata=True)

    def scoped_records(self, paths: Iterable[str], *, max_depth: int | None = None) -> tuple[SkillRecord, ...]:
        entries = self.searchable_entries(paths, max_depth=max_depth)
        identifiers = dict.fromkeys(entry.worker_id for entry in entries if entry.worker_id)
        return tuple(self.record_by_id[worker_id] for worker_id in identifiers)

    def metadata_path(self, worker_id: str) -> str:
        try:
            return f"{self.record_path_by_id[worker_id]}/META.md"
        except KeyError as exc:
            raise ValueError(f"Unknown Skill ID: {worker_id}") from exc

    def resolve_metadata_path(self, path: str) -> SkillRecord:
        normalized = self.normalize_path(path)
        worker_id = self.record_id_by_meta_path.get(normalized)
        if worker_id is None:
            raise ValueError(f"No such Skill metadata path: {normalized}")
        return self.record_by_id[worker_id]

    def _scoped_entries(
        self,
        paths: Iterable[str],
        max_depth: int | None,
        *,
        include_root: bool = True,
        recursive: bool | None = True,
        directories_only: bool = False,
        allow_metadata: bool = False,
    ) -> tuple[DirectoryEntry, ...]:
        rows: list[DirectoryEntry] = []
        seen: set[tuple[str, str]] = set()
        for raw_path in paths:
            path = self.normalize_path(raw_path)
            worker_id = self.record_id_by_meta_path.get(path) if allow_metadata else None
            if worker_id is not None:
                record = self.record_by_id[worker_id]
                candidates = (
                    DirectoryEntry(
                        self.record_path_by_id[worker_id],
                        "skill",
                        record.name or worker_id,
                        record.description,
                        worker_id=worker_id,
                    ),
                )
            else:
                node = self.node_by_path.get(path)
                if node is None:
                    raise ValueError(f"No such Skill directory: {path}")
                candidates = []
                if include_root:
                    description = node.description or ("Installed Skills" if recursive is True and path == "/" else "")
                    candidates.append(DirectoryEntry(path, "dir", node.label, description, depth=0))
                if recursive is not None:
                    candidates.extend(self._walk(path, recursive=recursive, max_depth=max_depth))
            for entry in candidates:
                if directories_only and entry.kind != "dir":
                    continue
                key = (entry.kind, entry.path)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(entry)
        return tuple(rows)

    def _walk(self, path: str, *, recursive: bool, max_depth: int | None) -> tuple[DirectoryEntry, ...]:
        rows: list[DirectoryEntry] = []

        def visit(current: str, depth: int) -> None:
            for entry in self._children.get(current, ()):
                relative_depth = depth + 1
                if max_depth is not None and relative_depth > max_depth:
                    continue
                rows.append(replace(entry, depth=relative_depth))
                if recursive and entry.kind == "dir":
                    visit(entry.path, relative_depth)

        visit(path, 0)
        return tuple(rows)

    def _index_paths(self) -> None:
        def visit(node: RetrieverNode, path: str) -> None:
            candidates: list[tuple[str, str, str, RetrieverNode | SkillRecord]] = []
            for child in node.children:
                preferred = (
                    _OVERLAY_SEGMENT
                    if child.node_id == _OVERLAY_NODE_ID
                    else child.label or child.node_id.rsplit(".", 1)[-1]
                )
                candidates.append((preferred, "dir", child.node_id, child))
            for item in node.items:
                catalog = self._catalog_by_payload.get(item.payload)
                if catalog is None:
                    continue
                worker_id = str(catalog.worker_id).strip()
                record = self.record_by_id.get(worker_id)
                if record is None:
                    continue
                candidates.append((worker_id, "skill", worker_id, record))

            candidates.sort(
                key=lambda candidate: (
                    _segment_base(candidate[0]).casefold(),
                    _segment_base(candidate[0]),
                    candidate[1],
                    candidate[2].casefold(),
                    candidate[2],
                )
            )
            overlay_present = any(kind == "dir" and identity == _OVERLAY_NODE_ID for _, kind, identity, _ in candidates)
            used: set[str] = {_OVERLAY_SEGMENT.casefold()} if overlay_present else set()
            resolved: list[tuple[str, str, RetrieverNode | SkillRecord]] = []
            for preferred, kind, identity, value in candidates:
                segment = (
                    _OVERLAY_SEGMENT
                    if kind == "dir" and identity == _OVERLAY_NODE_ID
                    else _unique_segment(preferred, identity, used)
                )
                resolved.append((segment, kind, value))
            resolved.sort(key=lambda item: (item[0].casefold(), item[0], item[1]))

            entries: list[DirectoryEntry] = []
            child_nodes: list[tuple[RetrieverNode, str]] = []
            for segment, kind, value in resolved:
                entry_path = _join_path(path, segment)
                if kind == "dir":
                    if not isinstance(value, RetrieverNode):
                        raise TypeError("Skill directory entry must contain a RetrieverNode")
                    self.node_by_path[entry_path] = value
                    entries.append(DirectoryEntry(entry_path, "dir", value.label or segment, value.description))
                    child_nodes.append((value, entry_path))
                    continue
                if not isinstance(value, SkillRecord):
                    raise TypeError("Skill entry must contain a SkillRecord")
                metadata_path = f"{entry_path}/META.md"
                self.record_path_by_id[value.worker_id] = entry_path
                self.record_id_by_meta_path[metadata_path] = value.worker_id
                entries.append(
                    DirectoryEntry(
                        entry_path,
                        "skill",
                        value.name or value.worker_id,
                        value.description,
                        worker_id=value.worker_id,
                    )
                )
            self._children[path] = tuple(entries)
            for child, child_path in child_nodes:
                visit(child, child_path)

        visit(self.root, "/")


class SkillFS:
    """Session-local live catalog using the existing Symphony retriever tree."""

    def __init__(
        self,
        records_provider: SkillRecordsProvider,
        *,
        settings: DiscoverySettings,
        visible_skill_names: VisibleSkillNames = None,
        artifact_root: Path | str,
        index_root: Path | str | None = None,
        pin_index_revision: bool = False,
        pinned_index_snapshot: SkillIndexSnapshot | Mapping[str, Any] | None | object = _PINNED_INDEX_SNAPSHOT_UNSET,
        **_: Any,
    ) -> None:
        if not callable(records_provider):
            raise TypeError("records_provider must be callable")
        self._records_provider = records_provider
        self._settings = settings
        self._visible_skill_names = visible_skill_names
        self._artifact_root = Path(artifact_root).expanduser().resolve()
        self._index_root = Path(index_root).expanduser().resolve() if index_root is not None else self._artifact_root
        snapshot_provided = pinned_index_snapshot is not _PINNED_INDEX_SNAPSHOT_UNSET
        supplied = (
            SkillIndexSnapshot.from_dict(pinned_index_snapshot)
            if isinstance(pinned_index_snapshot, Mapping)
            else pinned_index_snapshot
            if snapshot_provided
            else None
        )
        if supplied is not None and not isinstance(supplied, SkillIndexSnapshot):
            raise TypeError("pinned_index_snapshot must be a SkillIndexSnapshot, mapping, or None")
        self._pin_index_revision = bool(pin_index_revision or snapshot_provided)
        self._index_snapshot = (
            supplied
            if snapshot_provided
            else capture_index_snapshot(self._index_root)
            if self._pin_index_revision and settings.use_existing_index
            else None
        )
        self._body_cache: dict[tuple[str, str], str] = {}
        self._lexical_cache_key = ""
        self._lexical_index: LexicalIndex | None = None
        self._artifact: SkillFSArtifact
        self._view: SkillDirectoryView
        self._refresh()

    @property
    def artifact(self) -> SkillFSArtifact:
        return self._artifact

    @property
    def settings(self) -> DiscoverySettings:
        return self._settings

    @property
    def pinned_index_snapshot(self) -> SkillIndexSnapshot | None:
        return self._index_snapshot

    @property
    def directory(self) -> SkillDirectoryView:
        self._refresh()
        return self._view

    def selection_cards(self, *, refresh: bool = True) -> dict[str, dict[str, str]]:
        if refresh:
            self._refresh()
        return {
            record.worker_id: {
                "name": record.worker_id,
                "description": record.description or record.name or record.worker_id,
            }
            for record in self._artifact.items
        }

    def prompt_snapshot(self) -> SkillPromptSnapshot:
        self._refresh()
        documents = tuple(sorted(self._artifact.items, key=lambda item: (item.worker_id.casefold(), item.worker_id)))
        rendered = "\n".join(f"- {item.worker_id}: {' '.join(item.description.split())}" for item in documents)
        estimated = _estimate_tokens(rendered)
        small = estimated < self._settings.candidate_budget_tokens
        selected = documents if small else self._large_prompt_items(documents)
        mode = (
            "small"
            if small
            else "indexed"
            if self._artifact.layout == "tree" and self._artifact.index_state == "fresh"
            else "indexed-stale"
            if self._artifact.layout == "tree"
            else "large-flat"
        )
        branches: tuple[SkillPromptBranch, ...] = ()
        omitted = 0
        if not small and self._artifact.layout == "tree":
            directory_rows = tuple(entry for entry in self._view.children("/") if entry.kind == "dir")
            shown = directory_rows[: self._settings.max_list_entries]
            branches = tuple(SkillPromptBranch(entry.path, entry.label, entry.description) for entry in shown)
            omitted = max(0, len(directory_rows) - len(shown))
        return SkillPromptSnapshot(
            mode=mode,
            total_count=len(documents),
            entries=tuple(
                SkillPromptEntry(item.worker_id, item.description or item.name, item.source) for item in selected
            ),
            estimated_candidate_tokens=estimated,
            candidate_budget_tokens=self._settings.candidate_budget_tokens,
            index_state=self._artifact.index_state,
            branches=branches,
            omitted_branch_count=omitted,
        )

    def read_body(self, record: SkillRecord) -> str:
        key = (record.worker_id, record.content_hash)
        cached = self._body_cache.get(key)
        if cached is not None:
            return cached
        try:
            resolved = Path(record.skill_file).expanduser().resolve(strict=True)
            if record.source_root:
                root = Path(record.source_root).expanduser().resolve(strict=True)
                if not resolved.is_relative_to(root):
                    raise ValueError("Skill source escapes its authorized root")
            body = resolved.read_text(encoding="utf-8", errors="replace")
        except (OSError, RuntimeError, ValueError):
            body = ""
        self._body_cache = {
            cache_key: value for cache_key, value in self._body_cache.items() if cache_key[0] != record.worker_id
        }
        self._body_cache[key] = body
        return body

    def search_content(
        self,
        records: Iterable[SkillRecord],
        query: str,
        *,
        case_insensitive: bool,
        fixed_strings: bool,
    ) -> tuple[str, ...]:
        """Search one live scope using corpus statistics cached by inventory hash."""

        cache_key = (
            self._artifact.inventory.fingerprint + "\0" + "\0".join(item.worker_id for item in self._artifact.items)
        )
        if self._lexical_index is None or cache_key != self._lexical_cache_key:
            with _LEXICAL_CACHE_LOCK:
                self._lexical_index = _LEXICAL_CACHE.get(cache_key)
                if self._lexical_index is None:
                    self._lexical_index = LexicalIndex(
                        tuple(
                            LexicalDocument(
                                item.worker_id,
                                item.name,
                                item.description,
                                self.read_body(item),
                            )
                            for item in self._artifact.items
                        )
                    )
                    _LEXICAL_CACHE[cache_key] = self._lexical_index
                    while len(_LEXICAL_CACHE) > _LEXICAL_CACHE_MAX_ENTRIES:
                        _LEXICAL_CACHE.popitem(last=False)
                else:
                    _LEXICAL_CACHE.move_to_end(cache_key)
            self._lexical_cache_key = cache_key
        hits = self._lexical_index.search(
            query,
            keys=(record.worker_id for record in records),
            case_insensitive=case_insensitive,
            fixed_strings=fixed_strings,
        )
        return tuple(hit.key for hit in hits)

    def _refresh(self) -> None:
        inventory = inventory_from_records(self._records_provider())
        visible = self._visible_skill_names() if callable(self._visible_skill_names) else self._visible_skill_names
        visible_set = None if visible is None else {str(item) for item in visible}
        items = tuple(item for item in inventory.items if visible_set is None or item.worker_id in visible_set)
        snapshot = self._index_snapshot
        if self._settings.use_existing_index and not self._pin_index_revision:
            snapshot = capture_index_snapshot(self._index_root)
        current_hashes = {item.worker_id: item.content_hash for item in inventory.items}
        index_state = (
            "missing" if snapshot is None else "fresh" if dict(snapshot.record_hashes) == current_hashes else "stale"
        )
        root, catalog = _build_live_tree(items, snapshot)
        view = SkillDirectoryView(root=root, records=items, catalog_by_payload=catalog)
        fingerprint_payload = {
            "inventory": inventory.fingerprint,
            "visible": [item.worker_id for item in items],
            "index": snapshot.fingerprint if snapshot is not None else "",
            "state": index_state,
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        self._artifact = SkillFSArtifact(
            root=self._artifact_root,
            layout="tree" if snapshot is not None else "flat",
            index_state=index_state,
            inventory=inventory,
            items=items,
            fingerprint=fingerprint,
        )
        self._view = view

    def _large_prompt_items(self, items: tuple[SkillRecord, ...]) -> tuple[SkillRecord, ...]:
        preferred = {name.casefold() for name in self._settings.prompt_preferred_skills}
        selected = [
            item for item in items if item.worker_id.casefold() in preferred or item.name.casefold() in preferred
        ]
        selected_ids = {item.worker_id for item in selected}
        selected.extend(item for item in items if item.worker_id not in selected_ids)
        return tuple(selected[: self._settings.max_list_entries])


def capture_index_snapshot(root: Path | str) -> SkillIndexSnapshot | None:
    """Read one committed taxonomy generation under the builder's publish lock."""

    index_root = Path(root).expanduser().resolve()
    index_dir = index_root / "index"
    index_root.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(str(index_root / _INDEX_LOCK_FILENAME), mode="a+", timeout=30):
        from openjiuwen.symphony.agent.retrieval_toolkit import _recover_index_directory_unlocked

        snapshot = _load_index_snapshot(index_dir)
        if snapshot is not None:
            return snapshot
        recovered = _recover_index_directory_unlocked(
            index_dir,
            replace_invalid=True,
            validator=lambda candidate: _load_index_snapshot(candidate) is not None,
        )
        return _load_index_snapshot(index_dir) if recovered else None


def _load_index_snapshot(index_dir: Path) -> SkillIndexSnapshot | None:
    """Load and cross-check one generation without mutating publication state."""

    required = (
        index_dir / "tree_index.yaml",
        index_dir / "catalog.jsonl",
        index_dir / "manifest.json",
        index_dir / "commit.json",
    )
    if not all(path.is_file() for path in required):
        return None
    try:
        signatures = tuple(
            (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            for path in required
            for stat in (path.stat(),)
        )
        cache_key = (str(index_dir), signatures)
        with _INDEX_SNAPSHOT_CACHE_LOCK:
            cached = _INDEX_SNAPSHOT_CACHE.get(cache_key)
            if cached is not None:
                _INDEX_SNAPSHOT_CACHE.move_to_end(cache_key)
                return cached
        state = json.loads((index_dir / "commit.json").read_text(encoding="utf-8"))
        tree = load_tree_preset(index_dir / "tree_index.yaml")
        nodes = tree.get("nodes")
        hashes = state.get("source_file_hashes") or state.get("record_hashes")
        fingerprint = str(state.get("fingerprint") or "").strip()
        if not isinstance(nodes, list) or not isinstance(hashes, Mapping) or not fingerprint:
            return None
        snapshot = SkillIndexSnapshot.from_dict({"nodes": nodes, "record_hashes": hashes, "fingerprint": fingerprint})
        loaded = load_retriever_index(index_dir)
        catalog_ids = [str(record.worker_id or "").strip() for record in loaded.catalog_records]
        manifest_ids = loaded.manifest.get("worker_ids")
        expected_ids = set(dict(snapshot.record_hashes))
        if len(catalog_ids) != len(expected_ids) or len(set(catalog_ids)) != len(catalog_ids):
            return None
        if set(catalog_ids) != expected_ids or not isinstance(manifest_ids, list):
            return None
        normalized_manifest_ids = tuple(map(str, manifest_ids))
        if len(normalized_manifest_ids) != len(expected_ids):
            return None
        if len(set(normalized_manifest_ids)) != len(normalized_manifest_ids):
            return None
        if set(normalized_manifest_ids) != expected_ids:
            return None
        if int(loaded.manifest.get("count", -1)) != len(expected_ids):
            return None
        with _INDEX_SNAPSHOT_CACHE_LOCK:
            _INDEX_SNAPSHOT_CACHE[cache_key] = snapshot
            _INDEX_SNAPSHOT_CACHE.move_to_end(cache_key)
            while len(_INDEX_SNAPSHOT_CACHE) > 16:
                _INDEX_SNAPSHOT_CACHE.popitem(last=False)
        return snapshot
    except Exception:
        return None


def _build_live_tree(
    items: tuple[SkillRecord, ...],
    snapshot: SkillIndexSnapshot | None,
) -> tuple[RetrieverNode, dict[str, CatalogRecord]]:
    if snapshot is None:
        root = RetrieverNode(
            node_id="ROOT",
            label="ROOT",
            description="Installed Skills",
            items=tuple(
                RetrieverItem(item.worker_id, item.worker_id, item.name or item.worker_id, item.description)
                for item in items
            ),
        )
        return root, {
            item.worker_id: CatalogRecord(
                item.worker_id,
                item.worker_id,
                worker_id=item.worker_id,
                name=item.name,
                description=item.description,
            )
            for item in items
        }

    leaf_cid_by_worker: dict[str, str] = {}
    for node in snapshot.nodes:
        worker_id = str(node.get("worker_id") or "").strip()
        cid = str(node.get("cid") or "").strip()
        if str(node.get("type") or "").strip().lower() == "leaf" and worker_id and cid:
            leaf_cid_by_worker[worker_id] = cid
    indexed_records: list[CatalogRecord] = []
    catalog: dict[str, CatalogRecord] = {}
    overlay_items: list[RetrieverItem] = []
    for item in items:
        cid = leaf_cid_by_worker.get(item.worker_id)
        if cid:
            record = CatalogRecord(
                choice_id=item.worker_id,
                payload=cid,
                worker_id=item.worker_id,
                name=item.name,
                description=item.description,
            )
            indexed_records.append(record)
            catalog[cid] = record
        else:
            overlay_items.append(RetrieverItem(item.worker_id, item.worker_id, item.name, item.description))
            catalog[item.worker_id] = CatalogRecord(
                item.worker_id,
                item.worker_id,
                worker_id=item.worker_id,
                name=item.name,
                description=item.description,
            )
    visible_leaf_cids = {record.payload for record in indexed_records}
    visible_branch_cids: set[str] = set()
    for cid in visible_leaf_cids:
        parent = cid.rsplit(".", 1)[0] if "." in cid else ""
        while parent:
            visible_branch_cids.add(parent)
            parent = parent.rsplit(".", 1)[0] if "." in parent else ""
    visible_nodes: list[Mapping[str, Any]] = []
    for node in snapshot.nodes:
        kind = str(node.get("type") or "").strip().lower()
        cid = str(node.get("cid") or "").strip()
        if kind == "leaf" and cid in visible_leaf_cids:
            visible_nodes.append(node)
        elif kind == "branch" and cid in visible_branch_cids:
            visible_nodes.append(node)
    safe_nodes = tuple(
        {
            **node,
            **{
                key: sanitize_model_text(node.get(key) or "")
                for key in ("description", "select_when", "dont_select_when")
                if key in node
            },
        }
        for node in visible_nodes
    )
    root = build_tree_root(safe_nodes, catalog_records=indexed_records)
    if overlay_items:
        overlay = RetrieverNode(
            node_id=_OVERLAY_NODE_ID,
            label=_OVERLAY_SEGMENT,
            description="Skills installed after the pinned taxonomy was built.",
            items=tuple(sorted(overlay_items, key=lambda item: (item.item_id.casefold(), item.item_id))),
        )
        root = RetrieverNode(
            node_id=root.node_id,
            label=root.label,
            description=root.description,
            children=tuple(root.children) + (overlay,),
            items=root.items,
        )
    return root, catalog


def _validate_taxonomy_nodes(nodes: tuple[dict[str, Any], ...], *, expected_worker_ids: set[str]) -> None:
    identifiers: set[str] = set()
    branch_ids: set[str] = set()
    leaf_worker_ids: set[str] = set()
    for node in nodes:
        cid = str(node.get("cid") or "").strip()
        kind = str(node.get("type") or "").strip().lower()
        worker_id = str(node.get("worker_id") or "").strip()
        if not cid or cid == _OVERLAY_NODE_ID or kind not in {"branch", "leaf"}:
            raise ValueError("pinned index snapshot contains invalid taxonomy nodes")
        if cid in identifiers:
            raise ValueError("pinned index snapshot contains invalid taxonomy nodes")
        if kind == "leaf" and (not worker_id or worker_id in leaf_worker_ids):
            raise ValueError("pinned index snapshot contains invalid taxonomy nodes")
        identifiers.add(cid)
        if kind == "branch":
            branch_ids.add(cid)
        else:
            leaf_worker_ids.add(worker_id)
    for cid in identifiers:
        if "." in cid and cid.rsplit(".", 1)[0] not in branch_ids:
            raise ValueError("pinned index snapshot contains an orphan taxonomy node")
    if leaf_worker_ids != expected_worker_ids:
        raise ValueError("pinned index snapshot does not match its record hashes")


def _unique_segment(value: str, identity: str, used: set[str]) -> str:
    base = _segment_base(value)
    candidate = base
    if candidate.casefold() in used:
        candidate = f"{base}-{hashlib.sha256(identity.encode('utf-8', errors='surrogatepass')).hexdigest()[:8]}"
    index = 2
    while candidate.casefold() in used:
        candidate = f"{base}-{index}"
        index += 1
    used.add(candidate.casefold())
    return candidate


def _segment_base(value: str) -> str:
    return _SAFE_SEGMENT.sub("-", str(value or "").strip()).strip(".-") or "unnamed"


def _join_path(parent: str, segment: str) -> str:
    return f"/{segment}" if parent == "/" else f"{parent}/{segment}"


def _estimate_tokens(value: str) -> int:
    return max(0, (len(str(value or "")) + 3) // 4)


__all__ = [
    "DirectoryEntry",
    "SkillDirectoryView",
    "SkillFS",
    "SkillFSArtifact",
    "SkillIndexSnapshot",
    "SkillPromptBranch",
    "SkillPromptEntry",
    "SkillPromptSnapshot",
    "SkillRecordsProvider",
    "VisibleSkillNames",
    "capture_index_snapshot",
]
