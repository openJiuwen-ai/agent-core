import tempfile
from pathlib import Path

import pytest

from openjiuwen.symphony.shared import storage


def test_materialize_s3_dir_rejects_escaping_relative_paths(monkeypatch):
    calls: list[Path] = []

    def fake_download(*, base_uri, relative_path, destination_path):
        calls.append(Path(destination_path))
        if relative_path == "manifest.json":
            Path(destination_path).write_text("{}", encoding="utf-8")
        return True

    monkeypatch.setattr(storage, "download_s3_relative_object_if_exists", fake_download)

    with pytest.raises(ValueError, match="escapes cache dir"):
        storage.materialize_s3_dir("s3://bucket/prefix", relative_paths=["../escape.txt"])

    # The escaping entry must be rejected before any download is attempted for it.
    assert not any(Path(call).name == "escape.txt" for call in calls)
    assert not (Path(tempfile.gettempdir()) / "s3-dir-cache" / "escape.txt").exists()


def test_materialize_s3_dir_downloads_into_cache_dir(monkeypatch):
    seen: dict[str, Path] = {}

    def fake_download(*, base_uri, relative_path, destination_path):
        seen[relative_path] = Path(destination_path)
        if relative_path == "manifest.json":
            Path(destination_path).write_text("{}", encoding="utf-8")
        return True

    monkeypatch.setattr(storage, "download_s3_relative_object_if_exists", fake_download)

    local_dir = storage.materialize_s3_dir("s3://bucket/prefix", relative_paths=["data/index.bin"])

    assert (local_dir / "manifest.json").exists()
    assert Path(seen["data/index.bin"]).resolve().is_relative_to(local_dir.resolve())
