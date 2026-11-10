from __future__ import annotations

import json
import logging
from typing import Any, Dict, Sequence

from openjiuwen.symphony.retrieval.build.tree.prompts import SUBTREE_REBUILD_PROMPT
from openjiuwen.symphony.config import RetrievalBuildConfig
from openjiuwen.symphony.model_bridge import _BuildInterrupted, _is_transient_model_error, _model_scope, _response_content, _response_finish_reason
from openjiuwen.symphony.retrieval.build.workflows.artifacts import can_build_tree_with_llm
from openjiuwen.symphony.retrieval.build.workflows.tree_ops import slug_term, unique_child_cid

LOGGER = logging.getLogger("index_builder")


class IncrementalSubtreeRebuilder:
    def __init__(self, config: RetrievalBuildConfig, model=None) -> None:
        self._config = config
        self._model = model

    def rebuild(
        self,
        nodes: Sequence[object],
        root_cid: str,
        records_by_worker: Dict[str, Any],
        max_direct_leaf_children: int,
    ) -> list[dict[str, object]] | None:
        with _model_scope(self._model) as model:
            original = self._model
            self._model = model
            try:
                return self._rebuild(nodes, root_cid, records_by_worker, max_direct_leaf_children)
            finally:
                self._model = original

    def _rebuild(self, nodes, root_cid, records_by_worker, max_direct_leaf_children):
        if not can_build_tree_with_llm(self._model):
            return None
        normalized = []
        for node in nodes:
            if isinstance(node, dict):
                normalized.append(dict(node))
        root_node = None
        for node in normalized:
            if str(node.get("cid") or "") != root_cid:
                continue
            if str(node.get("type") or "") != "branch":
                continue
            root_node = node
            break
        if root_node is None:
            return None
        leaves: list[dict[str, object]] = []
        for node in normalized:
            if str(node.get("type") or "") == "branch":
                continue
            if is_descendant_cid(str(node.get("cid") or ""), root_cid):
                leaves.append(dict(node))
        if len(leaves) < 2:
            return None
        try:
            groups = self._expand_llm_subtree_groups(
                root_cid=root_cid,
                root_node=root_node,
                leaves=leaves,
                records_by_worker=records_by_worker,
                max_direct_leaf_children=max_direct_leaf_children,
                depth=_cid_depth(root_cid),
            )
            if not groups:
                return None
            rebuilt = build_subtree_from_llm_groups(
                nodes=normalized,
                root_cid=root_cid,
                leaves=leaves,
                groups=groups,
                records_by_worker=records_by_worker,
                max_direct_leaf_children=max_direct_leaf_children,
            )
            LOGGER.info(
                "incremental llm subtree rebuild succeeded | branch=%s | groups=%s",
                root_cid,
                self._count_llm_subtree_groups(groups),
            )
            return rebuilt
        except Exception as exc:
            if isinstance(exc, _BuildInterrupted):
                raise
            if not self._config.task.allow_fallback_tree:
                raise
            LOGGER.warning("incremental llm subtree rebuild skipped | branch=%s | error=%s", root_cid, exc)
            return None

    def _expand_llm_subtree_groups(
        self,
        *,
        root_cid: str,
        root_node: dict[str, object],
        leaves: Sequence[dict[str, object]],
        records_by_worker: Dict[str, Any],
        max_direct_leaf_children: int,
        depth: int,
    ) -> list[dict[str, object]] | None:
        if depth >= max(1, int(self._config.structure.max_depth or 1)):
            return None
        groups = self._request_llm_subtree_groups(
            root_cid=root_cid,
            root_node=root_node,
            leaves=leaves,
            records_by_worker=records_by_worker,
            max_direct_leaf_children=max_direct_leaf_children,
        )
        if not groups:
            return None
        leaf_by_worker = {str(leaf.get("worker_id") or ""): dict(leaf) for leaf in leaves}
        expanded: list[dict[str, object]] = []
        for group_index, group in enumerate(groups, start=1):
            skill_ids = _filter_known_skill_ids(group.get("skill_ids") or (), leaf_by_worker)
            if not skill_ids:
                continue
            expanded_group = dict(group)
            expanded_group["skill_ids"] = skill_ids
            if len(skill_ids) > max_direct_leaf_children:
                if depth + 1 >= max(1, int(self._config.structure.max_depth or 1)):
                    return None
                raw_segment = str(group.get("id") or group.get("name") or f"group-{group_index}")
                segment = slug_term(raw_segment, fallback=f"group-{group_index}")
                nested_root_cid = f"{root_cid}.{segment}" if root_cid else segment
                child_groups = self._expand_llm_subtree_groups(
                    root_cid=nested_root_cid,
                    root_node={
                        "cid": nested_root_cid,
                        "description": str(group.get("description") or group.get("name") or raw_segment),
                    },
                    leaves=[leaf_by_worker[skill_id] for skill_id in skill_ids],
                    records_by_worker=records_by_worker,
                    max_direct_leaf_children=max_direct_leaf_children,
                    depth=depth + 1,
                )
                if not child_groups:
                    return None
                expanded_group["children"] = child_groups
            expanded.append(expanded_group)
        return expanded or None

    def _request_llm_subtree_groups(
        self,
        *,
        root_cid: str,
        root_node: dict[str, object],
        leaves: Sequence[dict[str, object]],
        records_by_worker: Dict[str, Any],
        max_direct_leaf_children: int,
    ) -> list[dict[str, object]]:
        skills_payload = []
        for leaf in sorted(leaves, key=lambda item: str(item.get("worker_id") or "")):
            worker_id = str(leaf.get("worker_id") or "")
            record = records_by_worker.get(worker_id)
            skills_payload.append(
                {
                    "skill_id": worker_id,
                    "name": str(getattr(record, "name", "") or worker_id),
                    "description": str(getattr(record, "description", "") or leaf.get("description") or ""),
                }
            )
        system_prompt = (
            "/no_think\n"
            "You rebuild one capability-tree subtree for skill routing. "
            "Return one JSON object only. Do not explain."
        )
        user_prompt = SUBTREE_REBUILD_PROMPT.format(
            parent_branch=json.dumps(
                {
                    "cid": root_cid,
                    "description": str(root_node.get("description") or ""),
                },
                ensure_ascii=False,
                indent=2,
            ),
            skills_payload=json.dumps(skills_payload, ensure_ascii=False, indent=2),
            max_direct_leaf_children=max_direct_leaf_children,
        )
        with _model_scope(self._model) as model:
            for attempt in range(self._config.request.max_retries + 1):
                try:
                    response = model.invoke(
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0,
                        max_tokens=self._config.request.max_output_tokens or 2048,
                        timeout=self._config.request.timeout_seconds,
                    )
                    break
                except Exception as exc:
                    if attempt >= self._config.request.max_retries or not _is_transient_model_error(exc):
                        raise
        if _response_finish_reason(response) == "length":
            raise ValueError("model subtree output was truncated")
        content = _response_content(response)
        payload = extract_json_object(content)
        valid_worker_ids = {str(item.get("worker_id") or "") for item in leaves}
        groups = normalize_llm_subtree_groups(payload, valid_worker_ids=valid_worker_ids)
        if not groups:
            raise ValueError("model did not return valid subtree assignments")
        return groups

    @staticmethod
    def _normalize_llm_subtree_groups(
        payload: dict[str, object],
        *,
        valid_worker_ids: set[str],
    ) -> list[dict[str, object]]:
        return normalize_llm_subtree_groups(payload, valid_worker_ids=valid_worker_ids)

    @staticmethod
    def _build_subtree_from_llm_groups(
        *,
        nodes: Sequence[dict[str, object]],
        root_cid: str,
        leaves: Sequence[dict[str, object]],
        groups: Sequence[dict[str, object]],
        records_by_worker: Dict[str, Any],
        max_direct_leaf_children: int,
    ) -> list[dict[str, object]]:
        return build_subtree_from_llm_groups(
            nodes=nodes,
            root_cid=root_cid,
            leaves=leaves,
            groups=groups,
            records_by_worker=records_by_worker,
            max_direct_leaf_children=max_direct_leaf_children,
        )

    @staticmethod
    def _count_llm_subtree_groups(groups: Sequence[dict[str, object]]) -> int:
        total = 0
        for group in groups:
            total += 1
            child_groups = group.get("children")
            if isinstance(child_groups, list):
                nested_groups = []
                for child in child_groups:
                    if isinstance(child, dict):
                        nested_groups.append(child)
                total += IncrementalSubtreeRebuilder._count_llm_subtree_groups(nested_groups)
        return total


def is_descendant_cid(cid: str, root_cid: str) -> bool:
    return bool(cid and root_cid and cid.startswith(f"{root_cid}."))


def normalize_llm_subtree_groups(
    payload: dict[str, object],
    *,
    valid_worker_ids: set[str],
) -> list[dict[str, object]]:
    raw_groups = payload.get("groups") if isinstance(payload, dict) else None
    if not isinstance(raw_groups, list):
        return []
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_group in enumerate(raw_groups, start=1):
        if not isinstance(raw_group, dict):
            continue
        skill_ids = []
        raw_skill_ids = raw_group.get("skill_ids") or []
        if not isinstance(raw_skill_ids, list):
            raw_skill_ids = []
        for raw_skill_id in raw_skill_ids:
            skill_id = str(raw_skill_id or "").strip()
            if skill_id in valid_worker_ids and skill_id not in seen:
                skill_ids.append(skill_id)
                seen.add(skill_id)
        if not skill_ids:
            continue
        name = str(raw_group.get("name") or raw_group.get("id") or f"group-{index}").strip()
        groups.append(
            {
                "id": str(raw_group.get("id") or name or f"group-{index}").strip(),
                "name": name or f"group-{index}",
                "description": str(raw_group.get("description") or "").strip(),
                "skill_ids": skill_ids,
            }
        )
    missing = sorted(valid_worker_ids - seen)
    if missing:
        if not groups:
            return []
        fallback_group = max(groups, key=lambda group: (len(group["skill_ids"]), str(group["id"])))
        existing_skill_ids = fallback_group.get("skill_ids")
        fallback_group["skill_ids"] = [
            *(existing_skill_ids if isinstance(existing_skill_ids, list) else []),
            *missing,
        ]
    return groups


def build_subtree_from_llm_groups(
    *,
    nodes: Sequence[dict[str, object]],
    root_cid: str,
    leaves: Sequence[dict[str, object]],
    groups: Sequence[dict[str, object]],
    records_by_worker: Dict[str, Any],
    max_direct_leaf_children: int,
) -> list[dict[str, object]]:
    retained: list[dict[str, object]] = []
    for node in nodes:
        cid = str(node.get("cid") or "")
        if cid == root_cid or not is_descendant_cid(cid, root_cid):
            retained.append(dict(node))
    leaf_by_worker = {str(leaf.get("worker_id") or ""): dict(leaf) for leaf in leaves}
    used = {str(node.get("cid") or "") for node in retained}
    rebuilt: list[dict[str, object]] = []

    def append_group(parent_cid: str, group: dict[str, object], group_index: int) -> None:
        skill_ids = _filter_known_skill_ids(group.get("skill_ids") or (), leaf_by_worker)
        if not skill_ids:
            return
        raw_segment = str(group.get("id") or group.get("name") or f"group-{group_index}")
        segment = slug_term(raw_segment, fallback=f"group-{group_index}")
        branch_cid = unique_child_cid(parent=parent_cid, segment=segment, used=used)
        used.add(branch_cid)
        rebuilt.append(
            {
                "cid": branch_cid,
                "type": "branch",
                "description": str(group.get("description") or group.get("name") or segment),
            }
        )
        child_groups = group.get("children")
        if isinstance(child_groups, list) and child_groups:
            for child_index, child_group in enumerate(child_groups, start=1):
                if isinstance(child_group, dict):
                    append_group(branch_cid, child_group, child_index)
            return
        if len(skill_ids) > max_direct_leaf_children:
            raise ValueError(
                f"oversized LLM subtree group without child groups: {branch_cid} has {len(skill_ids)} skills"
            )
        for worker_id in skill_ids:
            leaf = dict(leaf_by_worker[worker_id])
            record = records_by_worker.get(worker_id)
            old_cid = str(leaf.get("cid") or "")
            leaf_segment = old_cid.rsplit(".", 1)[-1] if old_cid else slug_term(worker_id, fallback="skill")
            leaf["cid"] = unique_child_cid(parent=branch_cid, segment=leaf_segment, used=used)
            leaf["description"] = str(getattr(record, "description", "") or leaf.get("description") or "")
            used.add(str(leaf["cid"]))
            rebuilt.append(leaf)

    for group_index, group in enumerate(groups, start=1):
        append_group(root_cid, group, group_index)
    return sorted(retained + rebuilt, key=lambda item: str(item.get("cid") or ""))


def _cid_depth(cid: str) -> int:
    depth = 0
    for part in str(cid or "").split("."):
        if part:
            depth += 1
    return depth


def _filter_known_skill_ids(raw_ids: object, leaf_by_worker: dict[str, dict[str, object]]) -> list[str]:
    skill_ids: list[str] = []
    values = raw_ids if isinstance(raw_ids, (list, tuple, set)) else ()
    for item in values:
        skill_id = str(item)
        if skill_id in leaf_by_worker:
            skill_ids.append(skill_id)
    return skill_ids


def extract_json_object(text: str) -> dict[str, object]:
    raw = str(text or "").strip()
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            object_end = end + 1
            payload = json.loads(raw[start:object_end])
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}


_extract_json_object = extract_json_object
_is_descendant_cid = is_descendant_cid
