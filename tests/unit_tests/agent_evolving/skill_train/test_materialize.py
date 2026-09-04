# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.agent_evolving.skill_train.datasets import materialize as mat
from openjiuwen.agent_evolving.skill_train.datasets.materialize import (
    ensure_materialized_docvqa,
    ensure_materialized_officeqa,
    ensure_materialized_searchqa,
    is_id_split_dir,
)


def _write_items(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


@pytest.mark.level0
def test_is_hf_host() -> None:
    assert mat._is_hf_host("huggingface.co")
    assert mat._is_hf_host("hf-mirror.com")
    assert mat._is_hf_host("cas-bridge.xethub.hf.co")
    assert not mat._is_hf_host("example.com")


@pytest.mark.level0
def test_http_session_keeps_auth_on_hf_redirect() -> None:
    import requests

    session = mat._http_session("tok")
    prepared = session.prepare_request(requests.Request("GET", "https://huggingface.co/datasets/x"))
    response = requests.Response()
    response.status_code = 308
    response.url = "https://hf-mirror.com/datasets/x"
    response.request = session.prepare_request(
        requests.Request("GET", "https://hf-mirror.com/datasets/x")
    )
    session.rebuild_auth(prepared, response)
    assert prepared.headers.get("Authorization") == "Bearer tok"


@pytest.mark.level0
def test_is_id_split_dir() -> None:
    assert is_id_split_dir("searchqa_id_split")
    assert is_id_split_dir(Path("/tmp/docvqa_id_split"))
    assert not is_id_split_dir("searchqa_split")


@pytest.mark.level0
def test_split_byte_ranges_covers_total() -> None:
    ranges = mat._split_byte_ranges(1000, 4, min_part=100)
    assert ranges[0][0] == 0
    assert ranges[-1][1] == 999
    covered = sum(end - start + 1 for start, end in ranges)
    assert covered == 1000


@pytest.mark.level0
def test_progress_part_reuses_same_line(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(mat, "_vt_enabled", False)
    mat._progress_labels.clear()
    mat._progress_part("p1", "validation-00001[1/8]: 1.0/20.0 MB")
    mat._progress_part("p1", "validation-00001[1/8]: 8.0/20.0 MB")
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert "\r" in out
    assert out.count("8.0/20.0") == 1


@pytest.mark.level0
def test_move_or_copy_kills_lockers_then_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "a.bin"
    dest = tmp_path / "sub" / "b.bin"
    src.write_bytes(b"hello")
    attempts = {"n": 0}
    real_replace = Path.replace

    def _flaky(self: Path, target: Path) -> Path:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError(32, "locked")
        return real_replace(self, target)

    killed: list[str] = []
    monkeypatch.setattr(Path, "replace", _flaky)
    monkeypatch.setattr(mat, "_kill_locking_processes", lambda path: killed.append(str(path)) or [123])
    monkeypatch.setattr(mat.time, "sleep", lambda _s: None)
    assert mat._move_or_copy(src, dest)
    assert dest.read_bytes() == b"hello"
    assert killed


@pytest.mark.level0
def test_docvqa_parquets_downloads_missing_shards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("SKILL_TRAIN_DATA_ROOT", str(data_root))
    shard_dir = data_root / "_downloads" / "docvqa" / "DocVQA"
    shard_dir.mkdir(parents=True)
    first = shard_dir / "validation-00000-of-00006.parquet"
    first.write_bytes(b"PAR1" + b"\x00" * 8 + b"PAR1")

    downloaded: list[str] = []

    def _fake_download(url: str, dest: Path, *, token: str = "") -> None:
        downloaded.append(dest.name)
        dest.write_bytes(b"PAR1" + b"\x00" * 8 + b"PAR1")

    monkeypatch.setattr(mat, "_download_file", _fake_download)
    files = mat._docvqa_validation_parquets()
    assert len(files) == 6
    assert set(downloaded) == {f"validation-{i:05d}-of-00006.parquet" for i in range(1, 6)}


@pytest.mark.level0
def test_parquet_looks_complete(tmp_path: Path) -> None:
    path = tmp_path / "x.parquet"
    path.write_bytes(b"PAR1" + b"\x00" * 8 + b"PAR1")
    assert mat._parquet_looks_complete(path)
    path.write_bytes(b"PAR1xxxx")
    assert not mat._parquet_looks_complete(path)
    path.write_bytes(b"not-parquet")
    assert not mat._parquet_looks_complete(path)


@pytest.mark.level0
def test_ensure_materialized_searchqa_from_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("SKILL_TRAIN_DATA_ROOT", str(data_root))
    catalog = tmp_path / "catalog"
    monkeypatch.setattr(mat, "_candidate_dirs", lambda *names: [catalog])

    id_dir = data_root / "searchqa_id_split"
    for split, item_id in (("train", "a1"), ("val", "a2"), ("test", "a3")):
        _write_items(id_dir / split / "items.json", [{"id": item_id}])
        _write_items(
            catalog / split / "items.json",
            [{"id": item_id, "question": f"Q-{item_id}", "context": "c", "answers": ["x"]}],
        )

    out = ensure_materialized_searchqa(id_dir)
    assert out == data_root / "searchqa_split"
    train = json.loads((out / "train" / "items.json").read_text(encoding="utf-8"))
    assert train[0]["question"] == "Q-a1"


@pytest.mark.level0
def test_ensure_materialized_docvqa_joins_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("SKILL_TRAIN_DATA_ROOT", str(data_root))
    catalog = data_root / "docvqa_split"
    images = data_root / "docvqa_images"
    images.mkdir(parents=True)

    id_dir = data_root / "docvqa_id_split"
    for split, qid in (("train", "1"), ("val", "2"), ("test", "3")):
        _write_items(
            id_dir / split / "items.json",
            [{"id": qid, "questionId": qid, "image_path": f"data/docvqa_images/{qid}.png"}],
        )
        _write_items(
            catalog / split / "items.json",
            [
                {
                    "id": qid,
                    "questionId": qid,
                    "question": f"Ask{qid}",
                    "answer": "A",
                    "image_path": f"data/docvqa_images/{qid}.png",
                }
            ],
        )
        (images / f"{qid}.png").write_bytes(b"png")

    out = ensure_materialized_docvqa(id_dir)
    assert out == data_root / "docvqa_split"
    train = json.loads((out / "train" / "items.json").read_text(encoding="utf-8"))
    assert train[0]["question"] == "Ask1"


@pytest.mark.level0
def test_ensure_officeqa_docs_downloads_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("SKILL_TRAIN_DATA_ROOT", str(data_root))
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    monkeypatch.setattr(mat, "_candidate_dirs", lambda *names: [])
    rel = "treasury_bulletins_parsed/transformed/treasury_bulletin_1944_01.txt"
    monkeypatch.setattr(mat, "_list_hf_dataset_files", lambda *_a, **_k: [rel])

    def _fake_download(url: str, dest: Path, *, token: str = "") -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("hello", encoding="utf-8")

    monkeypatch.setattr(mat, "_download_small_file", _fake_download)

    out = mat.ensure_officeqa_docs(allow_download=True)
    assert out is not None
    assert (out / "treasury_bulletin_1944_01.txt").is_file()


@pytest.mark.level0
def test_ensure_materialized_officeqa_joins_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("SKILL_TRAIN_DATA_ROOT", str(data_root))
    catalog = tmp_path / "catalog"
    monkeypatch.setattr(mat, "_candidate_dirs", lambda *names: [catalog])
    monkeypatch.setattr(mat, "ensure_officeqa_docs", lambda **kwargs: None)

    id_dir = data_root / "officeqa_id_split"
    for split, uid in (("train", "UID1"), ("val", "UID2"), ("test", "UID3")):
        _write_items(id_dir / split / "items.json", [{"id": uid, "uid": uid, "category": "easy"}])
        _write_items(
            catalog / split / "items.json",
            [{"id": uid, "uid": uid, "question": f"OQ-{uid}", "ground_truth": "1", "category": "easy"}],
        )

    out = ensure_materialized_officeqa(id_dir)
    assert out == data_root / "officeqa_split"
    train = json.loads((out / "train" / "items.json").read_text(encoding="utf-8"))
    assert train[0]["question"] == "OQ-UID1"
