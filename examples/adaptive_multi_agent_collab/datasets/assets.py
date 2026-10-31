"""Pinned asset plans and explicitly approved acquisition; imports never fetch files."""

from __future__ import annotations

from ..paths import PACKAGE_ROOT
import hashlib
import json
import os
import tempfile
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any


TARGET_DIRECTORY = PACKAGE_ROOT
RECONCILE_REVISION = "902ae913cbb39cfbe0cbd73d0638989bde2d3cad"
STRATEGYQA_REVISION = "1ba1e97452e189569357876f2854b01357ffbe37"
MINILM_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
MINILM_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
MINILM_FILES = (
    "config.json",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.txt",
)
TIKTOKEN_URL = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
TIKTOKEN_SHA256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
DATA_ASSETS: tuple[dict[str, Any], ...] = (
    {
        "name": "reconcile_dev",
        "repository": "dinobby/ReConcile",
        "revision": RECONCILE_REVISION,
        "repository_path": "dataset/SQA/dev.json",
        "git_blob_sha1": "32932a11f4d7033bb700cacf5826d85607ba868c",
        "size": 424195,
        "relative_path": f"reconcile/{RECONCILE_REVISION}/dev.json",
    },
    {
        "name": "strategyqa_train",
        "repository": "eladsegal/strategyqa",
        "revision": STRATEGYQA_REVISION,
        "repository_path": "data/strategyqa/train.json",
        "git_blob_sha1": "df79706e10671bebebb87a610fdd434576c27a18",
        "size": 3857762,
        "relative_path": f"strategyqa/{STRATEGYQA_REVISION}/train.json",
    },
)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def git_blob_sha1(raw: bytes) -> str:
    return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()


def asset_plan(directory: str | Path | None = None) -> dict[str, Any]:
    root = Path(directory or TARGET_DIRECTORY / "assets").resolve()
    assets = [
        {
            **item,
            "url": f"https://raw.githubusercontent.com/{item['repository']}/{item['revision']}/{item['repository_path']}",
            "destination": str(root / item["relative_path"]),
        }
        for item in DATA_ASSETS
    ]
    plan = {
        "protocol": "pinned_strategyqa_asset_plan_v1",
        "directory": str(root),
        "data": assets,
        "encoder": {
            "repository": MINILM_MODEL,
            "revision": MINILM_REVISION,
            "files": list(MINILM_FILES),
            "cache_directory": str(root / "huggingface"),
            "approximate_bytes": 91600000,
        },
        "request_tokenizer": {
            "name": "o200k_base",
            "package_version": "0.13.0",
            "url": TIKTOKEN_URL,
            "sha256": TIKTOKEN_SHA256,
            "destination": str(root / "tokenizers" / "tiktoken" / hashlib.sha1(TIKTOKEN_URL.encode()).hexdigest()),
        },
        "network_scope": "these pinned public data, encoder, tokenizer assets and their immutable file metadata only",
        "dependency_installations": [],
        "paid_model_calls": False,
    }
    return {**plan, "fingerprint": _digest(plan)}


def download_approval_template(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "protocol": "asset_download_authorization_v1",
        "plan_fingerprint": plan["fingerprint"],
        "decision": "pending",
        "reviewer": "",
        "rationale": "",
    }


def _atomic_json(path: Path, value: Any) -> None:
    _write_once(path, (json.dumps(value, indent=2) + "\n").encode())


def _write_once(path: Path, raw: bytes) -> None:
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError(f"existing asset differs; explicit operator reconciliation is required: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "collaboration-pinned-assets"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def _verify_data(raw: bytes, item: Mapping[str, Any]) -> None:
    if len(raw) != item["size"] or git_blob_sha1(raw) != item["git_blob_sha1"]:
        raise ValueError("dataset bytes do not match the pinned official repository blob")


def download_assets(directory: str | Path, authorization_path: str | Path) -> dict[str, Any]:
    """Fetch only after an explicit approval artifact binds this exact local plan."""

    plan = asset_plan(directory)
    root = Path(plan["directory"])
    if not root.is_relative_to(TARGET_DIRECTORY / "assets"):
        raise ValueError("managed assets must stay inside this example's assets directory")
    approval = json.loads(Path(authorization_path).read_text(encoding="utf-8"))
    expected = download_approval_template(plan)
    if (
        set(approval) != set(expected)
        or approval.get("protocol") != expected["protocol"]
        or approval.get("plan_fingerprint") != plan["fingerprint"]
        or approval.get("decision") != "approve_download"
        or any(not isinstance(approval.get(key), str) or not approval[key].strip() for key in ("reviewer", "rationale"))
    ):
        raise ValueError("asset download requires explicit approval of the exact sources, revisions, and destinations")
    records = []
    for item in plan["data"]:
        path = Path(item["destination"])
        raw = path.read_bytes() if path.is_file() else _fetch(item["url"])
        _verify_data(raw, item)
        _write_once(path, raw)
        records.append(
            {
                "name": item["name"],
                "path": str(path),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "git_blob_sha1": git_blob_sha1(raw),
                "size": len(raw),
            }
        )
    tokenizer = plan["request_tokenizer"]
    path = Path(tokenizer["destination"])
    raw = path.read_bytes() if path.is_file() else _fetch(tokenizer["url"])
    if hashlib.sha256(raw).hexdigest() != tokenizer["sha256"]:
        raise ValueError("o200k_base vocabulary differs from the installed pinned tiktoken checksum")
    _write_once(path, raw)
    records.append({"name": "o200k_base", "path": str(path), "sha256": tokenizer["sha256"], "size": len(raw)})
    from huggingface_hub import HfApi, hf_hub_download

    info = HfApi().model_info(MINILM_MODEL, revision=MINILM_REVISION, files_metadata=True, token=False)
    if info.sha != MINILM_REVISION:
        raise ValueError("encoder metadata does not resolve to the predeclared immutable revision")
    siblings = {item.rfilename: item for item in info.siblings}
    for name in MINILM_FILES:
        metadata = siblings[name]
        downloaded = Path(
            hf_hub_download(
                MINILM_MODEL, name, revision=MINILM_REVISION, cache_dir=plan["encoder"]["cache_directory"], token=False
            )
        )
        raw = downloaded.read_bytes()
        if metadata.size != len(raw):
            raise ValueError("encoder file size differs from pinned public metadata")
        lfs = metadata.lfs
        expected_sha = lfs.sha256 if lfs is not None else None
        if (expected_sha and hashlib.sha256(raw).hexdigest() != expected_sha) or (
            expected_sha is None and git_blob_sha1(raw) != metadata.blob_id
        ):
            raise ValueError("encoder file checksum differs from pinned public metadata")
        records.append(
            {"name": name, "path": str(downloaded), "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
        )
    receipt = {"protocol": "verified_strategyqa_assets_v1", "plan_fingerprint": plan["fingerprint"], "files": records}
    _atomic_json(root / "asset_receipt.json", receipt)
    return receipt


def inspect_assets(directory: str | Path | None = None) -> dict[str, Any]:
    plan = asset_plan(directory)
    result: dict[str, Any] = {"plan": plan, "missing": [], "invalid": [], "network_accessed": False}
    for item in plan["data"]:
        path = Path(item["destination"])
        if not path.is_file():
            result["missing"].append(item["name"])
        else:
            try:
                _verify_data(path.read_bytes(), item)
            except ValueError:
                result["invalid"].append(item["name"])
    tokenizer = plan["request_tokenizer"]
    path = Path(tokenizer["destination"])
    if not path.is_file():
        result["missing"].append("o200k_base")
    elif hashlib.sha256(path.read_bytes()).hexdigest() != tokenizer["sha256"]:
        result["invalid"].append("o200k_base")
    snapshot = (
        Path(plan["encoder"]["cache_directory"])
        / ("models--" + MINILM_MODEL.replace("/", "--"))
        / "snapshots"
        / MINILM_REVISION
    )
    result["missing"].extend(f"encoder/{name}" for name in MINILM_FILES if not (snapshot / name).is_file())
    receipt_path = Path(plan["directory"]) / "asset_receipt.json"
    if not receipt_path.is_file():
        result["missing"].append("asset_receipt")
    else:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("plan_fingerprint") != plan["fingerprint"]:
            result["invalid"].append("asset_receipt_plan")
        expected_paths = {item["name"]: str(item["destination"]) for item in plan["data"]}
        expected_paths["o200k_base"] = str(plan["request_tokenizer"]["destination"])
        expected_paths.update({name: str(snapshot / name) for name in MINILM_FILES})
        actual_paths = {item.get("name"): item.get("path") for item in receipt.get("files", ())}
        if actual_paths != expected_paths or len(receipt.get("files", ())) != len(expected_paths):
            result["invalid"].append("asset_receipt_file_set")
        for item in receipt.get("files", ()):
            path = Path(item["path"])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                result["invalid"].append(item["name"])
    result["ready"] = not result["missing"] and not result["invalid"]
    return result


def materialize_strategyqa(directory: str | Path) -> dict[str, Any]:
    """Create stripped working data and protected IDs from verified local originals."""

    from .data import load_strategyqa

    root = Path(directory).resolve()
    if not root.is_relative_to(TARGET_DIRECTORY / "assets"):
        raise ValueError("materialized assets must stay inside this example's assets directory")
    originals = []
    for item in DATA_ASSETS:
        path = root / item["relative_path"]
        _verify_data(path.read_bytes(), item)
        originals.append(path)
    examples, _ = load_strategyqa(
        (originals[1], originals[0]), revision=f"strategyqa:{STRATEGYQA_REVISION};reconcile:{RECONCILE_REVISION}"
    )
    source = json.loads(originals[0].read_text(encoding="utf-8"))
    ids = [str(record["qid"]) for record in source[:100]]
    if len(ids) != 100 or len(set(ids)) != 100:
        raise ValueError("the verified ReConcile release does not provide 100 unique prefix IDs")
    working = [
        {"qid": example.example_id, "question": example.query, "answer": example.gold_answer} for example in examples
    ]
    _atomic_json(root / "working_strategyqa.json", working)
    _atomic_json(root / "protected_main100_ids.json", ids)
    record = {
        "repository": "https://github.com/dinobby/ReConcile",
        "repository_revision": RECONCILE_REVISION,
        "repository_path": "dataset/SQA/dev.json",
        "source_sha256": hashlib.sha256(originals[0].read_bytes()).hexdigest(),
        "source_git_blob_sha1": DATA_ASSETS[0]["git_blob_sha1"],
        "local_source": str(originals[0]),
        "ordered_ids": ids,
        "working_examples": len(working),
        "fact_annotations_copied": False,
    }
    _atomic_json(root / "data_provenance.json", record)
    return record


def freeze_strategyqa_manifest(specification_path: str | Path, directory: str | Path) -> dict[str, Any]:
    """Regenerate local-path provenance and seeded plans from the verified full pool."""
    from dataclasses import asdict
    from ..operations.experiment import prepare_experiment, _write_json_atomic

    source = Path(specification_path).resolve()
    if not source.is_relative_to(TARGET_DIRECTORY):
        raise ValueError("the prepared experiment manifest must stay inside this example")
    record = materialize_strategyqa(directory)
    specification = json.loads(source.read_text(encoding="utf-8"))
    specification["benchmark"]["evaluation_evidence"]["source_sha256"] = record["source_sha256"]
    specification["active_data"].pop("plan", None)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", dir=source.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(specification, stream)
        prepared = prepare_experiment(temporary)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    if prepared.active_data_plan is None:
        raise ValueError("the primary manifest requires the fixed active-data plan")
    specification["active_data"]["plan"] = prepared.active_data_plan.to_dict()
    _write_json_atomic(source, specification)
    prepared = prepare_experiment(source)
    root = Path(directory)
    prepared.dataset.write_manifest(root / "partition_manifest.json")
    _write_json_atomic(root / "active_manifest.json", prepared.active_data_plan.to_dict())
    _write_json_atomic(root / "pilot_manifest.json", asdict(prepared.pilot_plan))
    return {
        "manifest": str(source),
        "dataset_fingerprint": prepared.dataset.fingerprint,
        "working_examples": record["working_examples"],
        "protected_count": 100,
        "partition_counts": {
            "train": len(prepared.dataset.train),
            "component_validation": len(prepared.dataset.component_validation),
            "system_validation": len(prepared.dataset.system_validation),
        },
        "active_counts": [60, 20, 20, 8],
        "network_accessed": False,
    }
