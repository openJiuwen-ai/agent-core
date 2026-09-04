# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Materialize ``*_id_split`` manifests into full training payloads.

Default skill_train data checkouts ship id-only split directories (small, shareable).
Before ReflACT training, each env hydrates those IDs into cached
``{env}_split`` directories under ``skill_train/data/``.

Missing assets under ``skill_train/data`` are downloaded from Hugging Face
when possible (``HF_TOKEN`` may be required for gated datasets).
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from openjiuwen.agent_evolving.skill_train.paths import resolve_asset_path, skill_train_data_root

SPLIT_NAMES = ("train", "val", "test")


def is_id_split_dir(path: str | Path) -> bool:
    name = Path(path).name.lower()
    return name.endswith("_id_split")


_PROGRESS_WIDTH = 92
_progress_lock = threading.Lock()
_progress_labels: list[str] = []
_vt_enabled: bool | None = None


def _enable_vt_mode() -> bool:
    global _vt_enabled
    if _vt_enabled is not None:
        return _vt_enabled
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        _vt_enabled = False
        return False
    if sys.platform != "win32":
        _vt_enabled = True
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            _vt_enabled = False
            return False
        _vt_enabled = bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        _vt_enabled = False
    return _vt_enabled


def _progress(msg: str) -> None:
    with _progress_lock:
        print(f"  [materialize] {msg}", flush=True)


def _progress_reset() -> None:
    with _progress_lock:
        if _progress_labels:
            sys.stdout.write("\n")
            sys.stdout.flush()
        _progress_labels.clear()


def _progress_part(label: str, msg: str) -> None:
    """Refresh one in-place line per download part (no extra rows for the same part)."""
    text = f"  [materialize] {msg}".ljust(_PROGRESS_WIDTH)
    with _progress_lock:
        if label not in _progress_labels:
            _progress_labels.append(label)
            print(text, flush=True)
            return
        if not _enable_vt_mode():
            sys.stdout.write("\r" + text)
            sys.stdout.flush()
            return
        idx = _progress_labels.index(label)
        n = len(_progress_labels)
        up = n - idx
        sys.stdout.write(f"\033[{up}A\r{text}\n")
        down = n - idx - 1
        if down:
            sys.stdout.write(f"\033[{down}B")
        sys.stdout.flush()


def _hf_token() -> str:
    raw = (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or os.environ.get("HUGGINGFACEHUB_API_TOKEN")
        or ""
    ).strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
        raw = raw[1:-1].strip()
    return raw


def _read_json_list(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array in {path}")
    return data


def _write_json_list(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_id_split(id_split_dir: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for name in SPLIT_NAMES:
        items_path = id_split_dir / name / "items.json"
        if not items_path.is_file():
            raise FileNotFoundError(f"Missing id-split items: {items_path}")
        out[name] = _read_json_list(items_path)
    return out


def _materialized_ready(out_dir: Path, id_split_dir: Path, *, required_field: str) -> bool:
    if not out_dir.is_dir():
        return False
    try:
        id_splits = _load_id_split(id_split_dir)
    except FileNotFoundError:
        return False
    for name, id_items in id_splits.items():
        items_path = out_dir / name / "items.json"
        csv_path = out_dir / name / "items.csv"
        if items_path.is_file():
            items = _read_json_list(items_path)
        elif csv_path.is_file():
            with csv_path.open(encoding="utf-8", newline="") as f:
                items = list(csv.DictReader(f))
        else:
            return False
        if len(items) != len(id_items):
            return False
        if items and not str(items[0].get(required_field) or "").strip():
            return False
    return True


def _candidate_dirs(*rel_names: str) -> list[Path]:
    """Only look under skill_train/data (and SKILL_TRAIN_DATA_ROOT)."""
    roots: list[Path] = [skill_train_data_root()]
    env_root = os.environ.get("SKILL_TRAIN_DATA_ROOT", "").strip()
    if env_root:
        root = Path(env_root).expanduser()
        roots.append(root if root.name == "data" else root / "data")
        roots.append(root)

    seen: set[Path] = set()
    out: list[Path] = []
    for root in roots:
        for name in rel_names:
            path = root.joinpath(*Path(name).parts)
            try:
                key = path.resolve()
            except OSError:
                key = path
            if key in seen:
                continue
            seen.add(key)
            out.append(path)
    return out


def _load_json_or_csv_items(split_path: Path) -> list[dict]:
    json_files = sorted(split_path.glob("*.json"))
    if json_files:
        return _read_json_list(json_files[0])
    csv_files = sorted(split_path.glob("*.csv"))
    if csv_files:
        with csv_files[0].open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    return []


def _index_from_split_dirs(split_dirs: list[Path], *, key_fields: tuple[str, ...]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for split_dir in split_dirs:
        if not split_dir.is_dir():
            continue
        _progress(f"indexing catalog {split_dir}")
        for name in SPLIT_NAMES:
            rows = _load_json_or_csv_items(split_dir / name)
            before = len(index)
            for row in rows:
                key = ""
                for field in key_fields:
                    key = str(row.get(field) or "").strip()
                    if key:
                        break
                if key and key not in index:
                    index[key] = row
            _progress(f"  {name}: +{len(index) - before} keys (total={len(index)})")
    return index


def _write_materialized(
    out_dir: Path,
    id_split_dir: Path,
    id_splits: dict[str, list[dict]],
    hydrate: Callable[[dict], dict],
    *,
    source: str,
    env_name: str,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    total = sum(len(items) for items in id_splits.values())
    done = 0
    counts: dict[str, int] = {}
    _progress(f"{env_name}: writing {total} items -> {out_dir}")
    for name, id_items in id_splits.items():
        items: list[dict] = []
        for i, row in enumerate(id_items, start=1):
            items.append(hydrate(row))
            done += 1
            if i == len(id_items) or i % 50 == 0:
                _progress(f"  {name}: {i}/{len(id_items)} (overall {done}/{total})")
        _write_json_list(out_dir / name / "items.json", items)
        counts[name] = len(items)
        _progress(f"  wrote {name}/items.json ({len(items)})")
    manifest = {
        "manifest_type": "materialized_split",
        "source_id_split": str(id_split_dir),
        "source": source,
        "counts": counts,
    }
    (out_dir / "split_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _progress(f"{env_name}: done -> {out_dir}")
    return out_dir


def _load_searchqa_parquet_index() -> dict[str, dict]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise FileNotFoundError(
            "SearchQA materialization requires pyarrow to read Hugging Face parquet. "
            "Install with: uv add pyarrow"
        ) from exc

    hub = Path.home() / ".cache/huggingface/hub/datasets--lucadiliello--searchqa"
    parquet_files = sorted(hub.rglob("*.parquet"))
    if not parquet_files:
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise FileNotFoundError(
                "No local SearchQA parquet cache. Install huggingface_hub and retry, "
                "or place a full catalog at skill_train/data/searchqa_split."
            ) from exc
        _progress("searchqa: downloading lucadiliello/searchqa parquet via Hugging Face")
        snapshot_download(
            repo_id="lucadiliello/searchqa",
            repo_type="dataset",
            allow_patterns="data/*.parquet",
            token=_hf_token() or None,
        )
        parquet_files = sorted(hub.rglob("*.parquet"))
    if not parquet_files:
        raise FileNotFoundError(
            "SearchQA parquet still missing after download. "
            "Check network / HF_ENDPOINT and retry."
        )

    index: dict[str, dict] = {}
    for file_idx, path in enumerate(parquet_files, start=1):
        _progress(f"loading parquet {file_idx}/{len(parquet_files)}: {path.name}")
        table = pq.read_table(path)
        keys = table.column("key").to_pylist()
        questions = table.column("question").to_pylist()
        contexts = table.column("context").to_pylist()
        answers = table.column("answers").to_pylist()
        for i, key in enumerate(keys):
            key_s = str(key)
            if key_s in index:
                continue
            ans = answers[i]
            index[key_s] = {
                "id": key_s,
                "question": questions[i],
                "context": contexts[i],
                "answers": list(ans) if ans is not None else [],
                "task_type": "searchqa",
            }
            if (i + 1) % 20000 == 0 or i + 1 == len(keys):
                _progress(f"  scanned {i + 1}/{len(keys)} rows (index={len(index)})")
    return index


def ensure_materialized_searchqa(id_split_dir: Path | str) -> Path:
    id_split_dir = Path(id_split_dir)
    out_dir = skill_train_data_root() / "searchqa_split"
    if _materialized_ready(out_dir, id_split_dir, required_field="question"):
        _progress(f"searchqa: cache hit -> {out_dir}")
        return out_dir

    _progress(f"searchqa: materializing from {id_split_dir}")
    id_splits = _load_id_split(id_split_dir)
    _progress(
        "searchqa: id counts "
        + " ".join(f"{name}={len(items)}" for name, items in id_splits.items())
    )
    index = _index_from_split_dirs(_candidate_dirs("searchqa_split"), key_fields=("id", "key"))
    if not index:
        _progress("searchqa: local catalog miss, downloading/loading HF parquet")
        index = _load_searchqa_parquet_index()
    else:
        _progress(f"searchqa: catalog index size={len(index)}")

    missing: list[str] = []

    def hydrate(row: dict) -> dict:
        key = str(row.get("id") or row.get("key") or "").strip()
        payload = index.get(key)
        if payload is None:
            missing.append(key)
            return {"id": key}
        return dict(payload)

    result = _write_materialized(
        out_dir,
        id_split_dir,
        id_splits,
        hydrate,
        source="lucadiliello/searchqa",
        env_name="searchqa",
    )
    if missing:
        raise FileNotFoundError(
            f"SearchQA materialization missing {len(missing)} ids (e.g. {missing[:3]})."
        )
    return result


def _save_docvqa_image(image_obj: object, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return
    if hasattr(image_obj, "save"):
        image_obj.save(dest)
        return
    if isinstance(image_obj, (bytes, bytearray, memoryview)):
        dest.write_bytes(bytes(image_obj))
        return
    if isinstance(image_obj, dict):
        path = image_obj.get("path")
        if path and Path(path).exists():
            dest.write_bytes(Path(path).read_bytes())
            return
        data = image_obj.get("bytes")
        if data:
            dest.write_bytes(data)
            return
    raise TypeError(f"Unsupported DocVQA image object: {type(image_obj)!r}")


def _answers_to_list(answers: object) -> list[str]:
    if answers is None:
        return []
    if isinstance(answers, list):
        return [str(a).strip() for a in answers if str(a).strip()]
    text = str(answers).strip()
    return [text] if text else []


_DOWNLOAD_CONNECTIONS = 8
_DOWNLOAD_FILE_WORKERS = 2
_RANGE_MIN_PART = 4 * 1024 * 1024


def _hf_endpoint() -> str:
    return (
        os.environ.get("HF_ENDPOINT")
        or os.environ.get("HUGGINGFACE_HUB_ENDPOINT")
        or "https://huggingface.co"
    ).strip().rstrip("/")


def _hf_resolve_url(repo_id: str, relpath: str, *, official: bool = False) -> str:
    """Build a ``/resolve/main/...`` URL. Gated files use huggingface.co."""
    endpoint = "https://huggingface.co" if official else _hf_endpoint()
    return f"{endpoint}/datasets/{repo_id}/resolve/main/{relpath.lstrip('/')}"


def _http_status(exc: BaseException) -> int | None:
    import requests

    response = getattr(exc, "response", None)
    if isinstance(exc, requests.HTTPError) and response is not None:
        return int(response.status_code)
    return None


def _gated_hf_401_message(repo_id: str) -> str:
    return (
        f"{repo_id} returned 401 Unauthorized. Accept gated access at "
        f"https://huggingface.co/datasets/{repo_id} and set a valid HF_TOKEN. "
        "If HF_ENDPOINT points at a mirror, Authorization is kept across "
        "redirects to huggingface.co / *.hf.co."
    )


def _download_connections() -> int:
    raw = os.environ.get("SKILL_TRAIN_DOWNLOAD_CONNECTIONS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return min(32, int(raw))
    return _DOWNLOAD_CONNECTIONS


def _download_file_workers() -> int:
    raw = os.environ.get("SKILL_TRAIN_DOWNLOAD_WORKERS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return min(6, int(raw))
    return _DOWNLOAD_FILE_WORKERS


def _http_headers(token: str = "", extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {"User-Agent": "openjiuwen-skill-train"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra:
        headers.update(extra)
    return headers


def _parquet_looks_complete(path: Path) -> bool:
    """Parquet files start and end with the ``PAR1`` magic."""
    if not path.is_file() or path.stat().st_size < 8:
        return False
    with path.open("rb") as handle:
        head = handle.read(4)
        handle.seek(-4, os.SEEK_END)
        tail = handle.read(4)
    return head == b"PAR1" and tail == b"PAR1"


def _split_byte_ranges(total: int, connections: int, *, min_part: int = _RANGE_MIN_PART) -> list[tuple[int, int]]:
    """Split ``[0, total)`` into inclusive ``(start, end)`` ranges."""
    if total <= 0:
        return []
    n = max(1, min(connections, max(1, total // min_part)))
    n = min(n, total)
    part = total // n
    ranges: list[tuple[int, int]] = []
    start = 0
    for i in range(n):
        end = total - 1 if i == n - 1 else start + part - 1
        ranges.append((start, end))
        start = end + 1
    return ranges


def _parse_total_size(response) -> int:
    content_range = response.headers.get("content-range") or ""
    if content_range.startswith("bytes ") and "/" in content_range:
        suffix = content_range.rsplit("/", 1)[1].strip()
        if suffix.isdigit():
            return int(suffix)
    return int(response.headers.get("content-length") or 0)


_HTTP_CONNECT_TIMEOUT = 60
_HTTP_RESOLVE_READ_TIMEOUT = 180
_HTTP_STREAM_READ_TIMEOUT = 600
_HTTP_RETRIES = 5
_HF_AUTH_HOSTS = ("huggingface.co", "hf-mirror.com", "hf.co")


def _is_hf_host(host: str) -> bool:
    host = (host or "").lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in _HF_AUTH_HOSTS)


def _http_session(token: str = ""):
    """requests session that keeps HF Authorization across host redirects."""
    import requests
    from urllib.parse import urlparse

    class _HFSession(requests.Session):
        def rebuild_auth(self, prepared_request, response):
            super().rebuild_auth(prepared_request, response)
            if token and _is_hf_host(urlparse(prepared_request.url or "").netloc):
                prepared_request.headers["Authorization"] = f"Bearer {token}"

    return _HFSession()


def _is_retryable_http(exc: BaseException) -> bool:
    import requests

    return isinstance(
        exc,
        (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ),
    )


def _resolve_remote(url: str, *, token: str = "") -> tuple[str, int, bool]:
    """Follow redirects; return ``(final_url, total_size, supports_range)``."""
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    headers = _http_headers(token, {"Range": "bytes=0-0"})
    last_exc: BaseException | None = None
    session = _http_session(token)
    for attempt in range(1, _HTTP_RETRIES + 1):
        try:
            with session.get(
                url,
                headers=headers,
                stream=True,
                timeout=(_HTTP_CONNECT_TIMEOUT, _HTTP_RESOLVE_READ_TIMEOUT),
                allow_redirects=True,
            ) as response:
                response.raise_for_status()
                total = _parse_total_size(response)
                ranged = response.status_code == 206 or (
                    response.headers.get("accept-ranges") or ""
                ).lower() == "bytes"
                return response.url, total, ranged
        except Exception as exc:
            last_exc = exc
            if not _is_retryable_http(exc) or attempt >= _HTTP_RETRIES:
                raise
            time.sleep(min(2 * attempt, 10))
    raise last_exc  # pragma: no cover


def _stream_to_file(
    url: str,
    dest: Path,
    *,
    token: str = "",
    start: int = 0,
    end: int | None = None,
    existing: int = 0,
    total: int = 0,
    label: str = "",
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    name = label or dest.name
    last_exc: BaseException | None = None
    for attempt in range(1, _HTTP_RETRIES + 1):
        have = dest.stat().st_size if dest.is_file() else 0
        try:
            _stream_to_file_once(
                url,
                dest,
                token=token,
                start=start,
                end=end,
                existing=max(existing, have),
                total=total,
                label=name,
            )
            return
        except Exception as exc:
            last_exc = exc
            if not _is_retryable_http(exc) or attempt >= _HTTP_RETRIES:
                raise
            _progress_part(name, f"{name}: retry {attempt}/{_HTTP_RETRIES} after {type(exc).__name__}")
            time.sleep(min(2 * attempt, 10))
    raise last_exc  # pragma: no cover


def _stream_to_file_once(
    url: str,
    dest: Path,
    *,
    token: str = "",
    start: int = 0,
    end: int | None = None,
    existing: int = 0,
    total: int = 0,
    label: str = "",
) -> None:
    extra: dict[str, str] = {}
    resume_from = start + existing
    if end is not None:
        extra["Range"] = f"bytes={resume_from}-{end}"
    elif resume_from > 0:
        extra["Range"] = f"bytes={resume_from}-"
    headers = _http_headers(token, extra)
    mode = "ab" if existing > 0 else "wb"
    name = label or dest.name
    with _http_session(token).get(
        url,
        headers=headers,
        stream=True,
        timeout=(_HTTP_CONNECT_TIMEOUT, _HTTP_STREAM_READ_TIMEOUT),
        allow_redirects=True,
    ) as response:
        if response.status_code == 200 and (existing > 0 or start > 0 or end is not None):
            if end is not None:
                raise OSError(f"server ignored Range for {name}")
            existing = 0
            resume_from = start
            mode = "wb"
        elif response.status_code not in (200, 206):
            response.raise_for_status()
        if not total:
            total = _parse_total_size(response)
        done = resume_from if end is None else start + existing
        last_report = -1
        with dest.open(mode) as handle:
            for chunk in response.iter_content(1024 * 256):
                if not chunk:
                    continue
                handle.write(chunk)
                done += len(chunk)
                mb = done // (1024 * 1024)
                if mb != last_report:
                    last_report = mb
                    if total:
                        _progress_part(name, f"{name}: {done / 1e6:.1f}/{total / 1e6:.1f} MB")
                    else:
                        _progress_part(name, f"{name}: {done / 1e6:.1f} MB")
        if total:
            _progress_part(name, f"{name}: {done / 1e6:.1f}/{total / 1e6:.1f} MB")
        else:
            _progress_part(name, f"{name}: {done / 1e6:.1f} MB")


def _download_single(url: str, dest: Path, *, token: str = "", total: int = 0) -> None:
    partial = dest.with_suffix(dest.suffix + ".partial")
    existing = partial.stat().st_size if partial.is_file() else 0
    if existing > 0:
        _progress_part(dest.name, f"{dest.name}: resume from {existing / 1e6:.1f} MB")
    _stream_to_file(url, partial, token=token, existing=existing, total=total, label=dest.name)
    if total and partial.stat().st_size < total:
        raise OSError(
            f"Incomplete download for {dest.name}: {partial.stat().st_size}/{total} bytes"
        )
    if not _move_or_copy(partial, dest) and not dest.exists():
        raise OSError(f"Could not place downloaded file at {dest}")


def _download_multipart(url: str, dest: Path, *, token: str = "", total: int) -> None:
    connections = _download_connections()
    ranges = _split_byte_ranges(total, connections)
    parts = [dest.parent / f"{dest.name}.part{i}" for i in range(len(ranges))]

    def fetch(idx: int, start: int, end: int) -> None:
        part = dest.parent / f"{dest.name}.part{idx}"
        expected = end - start + 1
        existing = part.stat().st_size if part.is_file() else 0
        if existing == expected:
            return
        if existing > expected:
            part.unlink()
            existing = 0
        _stream_to_file(
            url,
            part,
            token=token,
            start=start,
            end=end,
            existing=existing,
            total=total,
            label=f"{dest.name}[{idx + 1}/{len(ranges)}]",
        )
        if part.stat().st_size != expected:
            raise OSError(
                f"Incomplete part {idx} for {dest.name}: {part.stat().st_size}/{expected}"
            )

    with ThreadPoolExecutor(max_workers=len(ranges)) as pool:
        futs = [
            pool.submit(fetch, i, start, end)
            for i, (start, end) in enumerate(ranges)
        ]
        for fut in as_completed(futs):
            fut.result()

    tmp = dest.with_suffix(dest.suffix + ".joining")
    with tmp.open("wb") as out:
        for part in parts:
            with part.open("rb") as src:
                shutil.copyfileobj(src, out, 1024 * 1024)
    if not _move_or_copy(tmp, dest) and not dest.exists():
        raise OSError(f"Could not place downloaded file at {dest}")
    for part in parts:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            _kill_locking_processes(part)
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass


def _windows_locking_pids(path: Path) -> list[int]:
    """Return PIDs locking ``path`` via the Windows Restart Manager."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    rstrtmgr = ctypes.WinDLL("rstrtmgr", use_last_error=True)
    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(33)
    if rstrtmgr.RmStartSession(ctypes.byref(session), 0, key) != 0:
        return []

    class _RmUniqueProcess(ctypes.Structure):
        _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]

    class _RmProcessInfo(ctypes.Structure):
        _fields_ = [
            ("Process", _RmUniqueProcess),
            ("strAppName", wintypes.WCHAR * 256),
            ("strServiceShortName", wintypes.WCHAR * 64),
            ("ApplicationType", ctypes.c_uint),
            ("AppStatus", wintypes.ULONG),
            ("TSSessionId", wintypes.DWORD),
            ("bRestartable", wintypes.BOOL),
        ]

    try:
        files = (ctypes.c_wchar_p * 1)(os.fspath(path.resolve()))
        if rstrtmgr.RmRegisterResources(session, 1, files, 0, None, 0, None) != 0:
            return []
        needed = wintypes.UINT(0)
        n_info = wintypes.UINT(16)
        reboot = wintypes.DWORD()
        infos = (_RmProcessInfo * 16)()
        rc = rstrtmgr.RmGetList(
            session, ctypes.byref(needed), ctypes.byref(n_info), infos, ctypes.byref(reboot)
        )
        if rc == 234 and needed.value:  # ERROR_MORE_DATA
            infos = (_RmProcessInfo * needed.value)()
            n_info = wintypes.UINT(needed.value)
            rc = rstrtmgr.RmGetList(
                session, ctypes.byref(needed), ctypes.byref(n_info), infos, ctypes.byref(reboot)
            )
        if rc != 0:
            return []
        return [infos[i].Process.dwProcessId for i in range(n_info.value)]
    finally:
        rstrtmgr.RmEndSession(session)


def _kill_locking_processes(path: Path) -> list[int]:
    """Kill processes (except this PID / parent) that hold ``path`` open."""
    protected = {os.getpid(), os.getppid(), 0, 4}
    killed: list[int] = []
    for pid in _windows_locking_pids(path):
        if pid in protected:
            continue
        _progress(f"killing PID {pid} locking {path.name}")
        result = subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            killed.append(pid)
            protected.add(pid)
        else:
            _progress(f"taskkill PID {pid} failed: {(result.stderr or result.stdout or '').strip()}")
    return killed


def _move_or_copy(src: Path, dest: Path) -> bool:
    """Move ``src`` to ``dest``; if locked, kill the locker and retry."""
    if dest.exists() or not src.is_file():
        return dest.exists()
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_exc: OSError | None = None
    for attempt in range(3):
        try:
            src.replace(dest)
            return True
        except OSError as exc:
            last_exc = exc
            _progress(f"{src.name} busy ({exc}); killing lockers then retry {attempt + 1}/3")
            _kill_locking_processes(src)
            _kill_locking_processes(dest)
            time.sleep(0.4 * (attempt + 1))
    try:
        shutil.copy2(src, dest)
        _progress(f"copied after unlock {src.name} -> {dest.name}")
        return True
    except OSError as exc:
        _progress(f"still locked {src}: {last_exc}; copy failed: {exc}")
        return False


def _download_complete(path: Path) -> bool:
    if path.suffix.lower() == ".parquet":
        return _parquet_looks_complete(path)
    return path.is_file() and path.stat().st_size > 0


def _list_hf_dataset_files(repo_id: str, prefix: str = "") -> list[str]:
    """List files under a Hugging Face dataset via the Hub tree API (no huggingface_hub)."""
    endpoint = _hf_endpoint()
    token = _hf_token()
    headers = _http_headers(token)
    rel = prefix.strip("/")
    url = f"{endpoint}/api/datasets/{repo_id}/tree/main"
    if rel:
        url = f"{url}/{rel}"
    last_exc: BaseException | None = None
    session = _http_session(token)
    for attempt in range(1, _HTTP_RETRIES + 1):
        try:
            response = session.get(
                url,
                headers=headers,
                params={"recursive": "1"},
                timeout=(_HTTP_CONNECT_TIMEOUT, _HTTP_RESOLVE_READ_TIMEOUT),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise FileNotFoundError(f"Unexpected HF tree payload from {url}")
            names: list[str] = []
            for item in payload:
                if not isinstance(item, dict) or item.get("type") != "file":
                    continue
                path = str(item.get("path") or "").replace("\\", "/")
                if path:
                    names.append(path)
            return names
        except Exception as exc:
            last_exc = exc
            if attempt >= _HTTP_RETRIES:
                break
            time.sleep(min(2 * attempt, 10))
    raise FileNotFoundError(
        f"Failed to list {repo_id} files from {url}: {last_exc}"
    ) from last_exc


def _download_file(url: str, dest: Path, *, token: str = "") -> None:
    """Download ``url`` to ``dest`` with resume and multi-connection Range requests."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _download_complete(dest):
        _progress_part(dest.name, f"cache hit {dest.name}")
        return
    if dest.is_file():
        _move_or_copy(dest, dest.with_suffix(dest.suffix + ".partial"))

    final_url, total, ranged = _resolve_remote(url, token=token)
    partial = dest.with_suffix(dest.suffix + ".partial")
    if partial.is_file() and partial.stat().st_size > 0:
        _download_single(final_url, dest, token=token, total=total)
        return
    if ranged and total > _RANGE_MIN_PART * 2:
        _download_multipart(final_url, dest, token=token, total=total)
        return
    _download_single(final_url, dest, token=token, total=total)


def _download_small_file(url: str, dest: Path, *, token: str = "") -> None:
    """Single-connection download for small txt/csv files (no Range probe)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 0:
        return
    partial = dest.with_suffix(dest.suffix + ".partial")
    last_exc: BaseException | None = None
    session = _http_session(token)
    for attempt in range(1, _HTTP_RETRIES + 1):
        existing = partial.stat().st_size if partial.is_file() else 0
        headers = _http_headers(token)
        if existing > 0:
            headers["Range"] = f"bytes={existing}-"
        try:
            with session.get(
                url,
                headers=headers,
                stream=True,
                timeout=(_HTTP_CONNECT_TIMEOUT, _HTTP_RESOLVE_READ_TIMEOUT),
                allow_redirects=True,
            ) as response:
                if response.status_code == 200:
                    existing = 0
                    mode = "wb"
                elif response.status_code == 206:
                    mode = "ab"
                else:
                    response.raise_for_status()
                    mode = "wb"
                with partial.open(mode) as handle:
                    for chunk in response.iter_content(64 * 1024):
                        if chunk:
                            handle.write(chunk)
            if not _move_or_copy(partial, dest) and not dest.exists():
                raise OSError(f"Could not place downloaded file at {dest}")
            return
        except Exception as exc:
            last_exc = exc
            if not _is_retryable_http(exc) or attempt >= _HTTP_RETRIES:
                raise
            time.sleep(min(2 * attempt, 10))
    raise last_exc  # pragma: no cover


def _collect_docvqa_parquets() -> dict[str, Path]:
    """Index complete validation parquet files by basename."""
    names = {f"validation-{i:05d}-of-00006.parquet" for i in range(6)}
    found: dict[str, Path] = {}
    roots = [
        skill_train_data_root() / "_downloads" / "docvqa" / "DocVQA",
        skill_train_data_root() / "_downloads" / "docvqa",
        Path.home() / ".cache/huggingface/hub/datasets--lmms-lab-encoder--DocVQA",
        Path.home() / ".cache/huggingface/hub/datasets--lmms-lab--DocVQA",
    ]
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("validation-*.parquet"):
            name = path.name
            if name not in names or "InfographicVQA" in path.as_posix():
                continue
            if name in found or not _parquet_looks_complete(path):
                continue
            found[name] = path
            if len(found) >= 6:
                return found
    return found


def _docvqa_validation_parquets() -> list[Path]:
    try:
        import pyarrow.parquet as pq  # noqa: F401
    except ImportError as exc:
        raise FileNotFoundError(
            "DocVQA download requires pyarrow to read Hugging Face parquet. "
            "Install with: uv add --dev pyarrow"
        ) from exc

    names = [f"validation-{i:05d}-of-00006.parquet" for i in range(6)]
    found = _collect_docvqa_parquets()
    if len(found) >= 6:
        return [found[name] for name in names]

    local_dir = skill_train_data_root() / "_downloads" / "docvqa" / "DocVQA"
    legacy = skill_train_data_root() / "_downloads" / "docvqa"
    missing = [name for name in names if name not in found]
    _progress(
        f"docvqa: have {len(found)}/6 parquet files, downloading missing {missing}"
    )

    # huggingface_hub + hf-mirror often fails on Xet metadata; stream via HTTP.
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    endpoint = _hf_endpoint()
    token = _hf_token()
    local_dir.mkdir(parents=True, exist_ok=True)

    # Adopt any previously interrupted download sitting one level up.
    for name in missing:
        dest = local_dir / name
        if dest.exists() or dest.with_suffix(dest.suffix + ".partial").exists():
            continue
        for candidate in (legacy / name, legacy / f"{name}.partial"):
            if not candidate.is_file():
                continue
            target = dest if _parquet_looks_complete(candidate) else dest.with_suffix(dest.suffix + ".partial")
            _move_or_copy(candidate, target)
            break

    failures: list[str] = []

    def fetch_one(name: str) -> None:
        dest = local_dir / name
        if _parquet_looks_complete(dest):
            return
        url = f"{endpoint}/datasets/lmms-lab-encoder/DocVQA/resolve/main/DocVQA/{name}"
        last_exc: BaseException | None = None
        for attempt in range(1, _HTTP_RETRIES + 1):
            try:
                _download_file(url, dest, token=token)
                return
            except Exception as exc:
                last_exc = exc
                if not _is_retryable_http(exc) or attempt >= _HTTP_RETRIES:
                    raise
                _progress_part(name, f"{name}: retry {attempt}/{_HTTP_RETRIES} after {type(exc).__name__}")
                time.sleep(min(2 * attempt, 10))
        raise last_exc  # pragma: no cover

    _progress(
        f"docvqa: downloading {len(missing)} files from {endpoint} "
        f"({_download_connections()} connections/file, {_download_file_workers()} files in parallel)"
    )
    with ThreadPoolExecutor(max_workers=_download_file_workers()) as pool:
        futs = {pool.submit(fetch_one, name): name for name in missing}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001 - keep partials for resume
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
    _progress_reset()

    found = _collect_docvqa_parquets()
    if len(found) >= 6:
        return [found[name] for name in names]

    detail = "; ".join(failures[:3]) if failures else "no complete parquet files"
    raise FileNotFoundError(
        "DocVQA validation parquet download failed "
        f"(got {len(found)}/6 files). {detail}\n"
        "hf-mirror redirects large files to huggingface CDN; multi-connection "
        "Range download is used, but a blocked CDN will still be slow.\n"
        "Fix options:\n"
        "  1) Place images at skill_train/data/docvqa_images/*.png and a catalog at "
        "skill_train/data/docvqa_split/ (or docvqa/splits/), then retry\n"
        "  2) Let the downloader resume (partial *.partial / *.partN files are kept)\n"
        "  3) Raise connections: SKILL_TRAIN_DOWNLOAD_CONNECTIONS=16\n"
        "  4) Use a network/VPN that can reach huggingface CDN"
    )


def _load_docvqa_hf_index(wanted: set[str]) -> dict[str, dict]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise FileNotFoundError(
            "DocVQA download requires pyarrow to read Hugging Face parquet. "
            "Install with: uv sync --extra online-rl   (or: uv add pyarrow)"
        ) from exc

    by_qid: dict[str, dict] = {}
    parquet_files = _docvqa_validation_parquets()
    for file_idx, path in enumerate(parquet_files, start=1):
        if len(by_qid) == len(wanted):
            break
        _progress(f"docvqa: scanning parquet {file_idx}/{len(parquet_files)}: {path.name}")
        pf = pq.ParquetFile(path)
        names = set(pf.schema_arrow.names)
        qid_col = "questionId" if "questionId" in names else "question_id"
        if qid_col not in names:
            raise FileNotFoundError(f"DocVQA parquet missing questionId column: {path}")
        for batch in pf.iter_batches(batch_size=64):
            cols = {name: batch.column(name) for name in batch.schema.names}
            qids = cols[qid_col]
            for i in range(batch.num_rows):
                qid = str(qids[i].as_py()).strip()
                if not qid or qid not in wanted or qid in by_qid:
                    continue
                row = {name: cols[name][i].as_py() for name in batch.schema.names}
                by_qid[qid] = row
                if len(by_qid) == len(wanted):
                    break
            if len(by_qid) == len(wanted):
                break
        _progress(f"  matched {len(by_qid)}/{len(wanted)} ids")
    return by_qid


def _download_docvqa_from_hf(id_split_dir: Path, out_dir: Path, image_dir: Path) -> Path:
    id_splits = _load_id_split(id_split_dir)
    wanted = {
        str(item.get("questionId") or item.get("id") or "").strip()
        for items in id_splits.values()
        for item in items
    }
    wanted.discard("")
    _progress(f"docvqa: hydrating {len(wanted)} ids from lmms-lab/DocVQA validation parquet")
    by_qid = _load_docvqa_hf_index(wanted)
    missing = sorted(wanted - set(by_qid))
    if missing:
        raise FileNotFoundError(
            f"DocVQA HF source missing {len(missing)} questionId(s), e.g. {missing[:3]}"
        )

    image_dir.mkdir(parents=True, exist_ok=True)

    def hydrate(row: dict) -> dict:
        qid = str(row.get("questionId") or row.get("id") or "").strip()
        src = by_qid[qid]
        doc_id = str(row.get("docId") or src.get("docId") or "").strip()
        rel_image = str(
            row.get("image_path") or f"data/docvqa_images/q{qid}_d{doc_id}.png"
        ).replace("\\", "/")
        abs_image = image_dir / Path(rel_image).name
        image = src.get("image")
        if image is None:
            raise ValueError(f"DocVQA row {qid} has no image")
        _save_docvqa_image(image, abs_image)
        answers = _answers_to_list(src.get("answers") or src.get("answer"))
        return {
            **row,
            "id": qid,
            "questionId": qid,
            "docId": doc_id,
            "question": str(src.get("question") or "").strip(),
            "answer": answers[0] if answers else "",
            "answers": answers,
            "ground_truth": answers[0] if answers else "",
            "image_path": str(abs_image),
            "topic": str(row.get("topic") or src.get("question_types") or "docvqa").strip(),
            "source_split": "validation",
        }

    return _write_materialized(
        out_dir,
        id_split_dir,
        id_splits,
        hydrate,
        source="lmms-lab/DocVQA",
        env_name="docvqa",
    )


def ensure_docvqa_images(*, id_split_dir: Path | None = None) -> Path | None:
    """Ensure DocVQA images exist under skill_train/data/docvqa_images."""
    local = skill_train_data_root() / "docvqa_images"
    if local.is_dir() and any(local.glob("*.png")):
        _progress(f"docvqa images: cache hit -> {local}")
        return local
    if id_split_dir is None:
        _progress("docvqa images: missing (will download during DocVQA materialization)")
        return None
    out_dir = skill_train_data_root() / "docvqa_split"
    _download_docvqa_from_hf(Path(id_split_dir), out_dir, local)
    if local.is_dir() and any(local.glob("*.png")):
        return local
    return None


def ensure_materialized_docvqa(id_split_dir: Path | str) -> Path:
    id_split_dir = Path(id_split_dir)
    out_dir = skill_train_data_root() / "docvqa_split"
    image_dir = skill_train_data_root() / "docvqa_images"

    if _materialized_ready(out_dir, id_split_dir, required_field="question") and (
        image_dir.is_dir() and any(image_dir.glob("*.png"))
    ):
        _progress(f"docvqa: cache hit -> {out_dir}")
        return out_dir

    legacy = skill_train_data_root() / "docvqa" / "splits"
    if _materialized_ready(legacy, id_split_dir, required_field="question") and (
        image_dir.is_dir() and any(image_dir.glob("*.png"))
    ):
        _progress(f"docvqa: cache hit -> {legacy}")
        return legacy

    _progress(f"docvqa: materializing from {id_split_dir}")
    id_splits = _load_id_split(id_split_dir)
    _progress(
        "docvqa: id counts "
        + " ".join(f"{name}={len(items)}" for name, items in id_splits.items())
    )
    index = _index_from_split_dirs(
        _candidate_dirs("docvqa_split", "docvqa/splits"),
        key_fields=("questionId", "id"),
    )
    images_ready = image_dir.is_dir() and any(image_dir.glob("*.png"))
    if index and images_ready:
        _progress(f"docvqa: catalog index size={len(index)}")
        missing: list[str] = []

        def hydrate(row: dict) -> dict:
            key = str(row.get("questionId") or row.get("id") or "").strip()
            payload = index.get(key)
            if payload is None:
                missing.append(key)
                return dict(row)
            merged = dict(row)
            merged.update(payload)
            raw_image = str(merged.get("image_path") or "").strip()
            if raw_image:
                merged["image_path"] = resolve_asset_path(raw_image)
            return merged

        result = _write_materialized(
            out_dir,
            id_split_dir,
            id_splits,
            hydrate,
            source="lmms-lab/DocVQA",
            env_name="docvqa",
        )
        if missing:
            raise FileNotFoundError(
                f"DocVQA materialization missing {len(missing)} ids (e.g. {missing[:3]})."
            )
        return result

    _progress("docvqa: local catalog/images miss, downloading from Hugging Face")
    return _download_docvqa_from_hf(id_split_dir, out_dir, image_dir)


def _collect_officeqa_txt(root: Path) -> list[Path]:
    transformed = root / "treasury_bulletins_parsed" / "transformed"
    if transformed.is_dir():
        files = sorted(transformed.glob("*.txt"))
        if files:
            return files
    return sorted(root.rglob("treasury_bulletin_*.txt"))


def _download_officeqa_docs_from_hf(output_dir: Path) -> Path:
    token = _hf_token()
    if not token:
        raise FileNotFoundError(
            "OfficeQA docs are gated on Hugging Face. Accept access at "
            "https://huggingface.co/datasets/databricks/officeqa then set "
            "HF_TOKEN (or run: hf auth login).\n"
            "Optional mirror: set HF_ENDPOINT=https://hf-mirror.com"
        )

    prefix = "treasury_bulletins_parsed/transformed"
    endpoint = _hf_endpoint()
    _progress(f"officeqa docs: listing {prefix}/*.txt from {endpoint}")
    try:
        remote_files = [
            path
            for path in _list_hf_dataset_files("databricks/officeqa", prefix)
            if path.endswith(".txt")
        ]
    except Exception as exc:  # noqa: BLE001
        raise FileNotFoundError(
            f"OfficeQA docs listing failed: {type(exc).__name__}: {exc}\n"
            "Tips: confirm gated access, set HF_TOKEN, optionally HF_ENDPOINT=https://hf-mirror.com"
        ) from exc
    if not remote_files:
        raise FileNotFoundError(
            f"OfficeQA docs: no {prefix}/*.txt found in databricks/officeqa"
        )

    cache_dir = skill_train_data_root() / "_downloads" / "officeqa_hf"
    total = len(remote_files)
    _progress(f"officeqa docs: downloading {total} txt files")
    failures: list[str] = []
    done = 0
    done_lock = threading.Lock()
    workers = max(_download_file_workers(), min(8, total))
    unauthorized = threading.Event()

    def _mark(relpath: str, status: str) -> None:
        nonlocal done
        with done_lock:
            done += 1
            current = done
        _progress_part(
            "officeqa-docs",
            f"officeqa docs: {current}/{total} {status} {Path(relpath).name}",
        )

    def fetch_one(relpath: str) -> Path:
        if unauthorized.is_set():
            raise FileNotFoundError("aborted after Hugging Face 401")
        dest = cache_dir / relpath
        if dest.is_file() and dest.stat().st_size > 0:
            _mark(relpath, "cache")
            return dest
        url = _hf_resolve_url("databricks/officeqa", relpath, official=True)
        _progress_part("officeqa-docs", f"officeqa docs: {done}/{total} get {Path(relpath).name}")
        _download_small_file(url, dest, token=token)
        _mark(relpath, "done")
        return dest

    downloaded: list[Path] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(fetch_one, relpath): relpath for relpath in remote_files}
        for fut in as_completed(futs):
            relpath = futs[fut]
            try:
                downloaded.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{relpath}: {type(exc).__name__}: {exc}")
                _mark(relpath, "fail")
                if _http_status(exc) == 401:
                    unauthorized.set()
                    for pending in futs:
                        pending.cancel()
    _progress_reset()
    if unauthorized.is_set():
        raise FileNotFoundError(_gated_hf_401_message("databricks/officeqa"))

    src_files = _collect_officeqa_txt(cache_dir)
    if not src_files:
        detail = "; ".join(failures[:3]) if failures else "no files written"
        raise FileNotFoundError(
            f"OfficeQA docs download finished but found 0 treasury_bulletin_*.txt. {detail}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    total = len(src_files)
    _progress(f"officeqa docs: copying {total} files -> {output_dir}")
    for i, path in enumerate(src_files, start=1):
        shutil.copy2(path, output_dir / path.name)
        if i == total or i % 50 == 0:
            _progress(f"  copied {i}/{total}")
    if failures:
        _progress(f"officeqa docs: {len(failures)} files failed, continuing with {total} copied")
    _progress(f"officeqa docs: download done -> {output_dir}")
    return output_dir


def ensure_officeqa_docs(*, allow_download: bool = True) -> Path | None:
    """Ensure OfficeQA docs under skill_train/data/officeqa_docs_official.

    If missing, download from gated Hugging Face ``databricks/officeqa``.
    """
    local = skill_train_data_root() / "officeqa_docs_official"
    if local.is_dir() and any(local.glob("treasury_bulletin_*.txt")):
        _progress(f"officeqa docs: cache hit -> {local}")
        return local
    if not allow_download:
        _progress("officeqa docs: not found")
        return None
    try:
        return _download_officeqa_docs_from_hf(local)
    except FileNotFoundError as exc:
        _progress(f"officeqa docs: download unavailable ({exc})")
        return None


def _download_officeqa_catalog_from_hf() -> dict[str, dict]:
    token = _hf_token()
    if not token:
        raise FileNotFoundError(
            "OfficeQA catalog is gated. Set HF_TOKEN after accepting access on "
            "https://huggingface.co/datasets/databricks/officeqa"
        )
    dest = skill_train_data_root() / "_downloads" / "officeqa_hf" / "officeqa_full.csv"
    if not dest.is_file() or dest.stat().st_size <= 0:
        _progress("officeqa: downloading officeqa_full.csv from Hugging Face")
        url = _hf_resolve_url("databricks/officeqa", "officeqa_full.csv", official=True)
        _download_file(url, dest, token=token)
        _progress_reset()
    index: dict[str, dict] = {}
    with dest.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            key = str(row.get("uid") or row.get("id") or "").strip()
            if key and key not in index:
                index[key] = row
    _progress(f"officeqa: HF catalog index size={len(index)}")
    return index


def ensure_materialized_officeqa(id_split_dir: Path | str) -> Path:
    id_split_dir = Path(id_split_dir)
    out_dir = skill_train_data_root() / "officeqa_split"
    if _materialized_ready(out_dir, id_split_dir, required_field="question"):
        _progress(f"officeqa: cache hit -> {out_dir}")
        ensure_officeqa_docs()
        return out_dir

    _progress(f"officeqa: materializing from {id_split_dir}")
    id_splits = _load_id_split(id_split_dir)
    _progress(
        "officeqa: id counts "
        + " ".join(f"{name}={len(items)}" for name, items in id_splits.items())
    )
    index = _index_from_split_dirs(_candidate_dirs("officeqa_split"), key_fields=("uid", "id"))
    if not index:
        _progress("officeqa: local catalog miss, downloading from Hugging Face")
        index = _download_officeqa_catalog_from_hf()
    else:
        _progress(f"officeqa: catalog index size={len(index)}")

    missing: list[str] = []

    def hydrate(row: dict) -> dict:
        key = str(row.get("uid") or row.get("id") or "").strip()
        payload = index.get(key)
        if payload is None:
            missing.append(key)
            return dict(row)
        merged = dict(row)
        merged.update(payload)
        return merged

    result = _write_materialized(
        out_dir,
        id_split_dir,
        id_splits,
        hydrate,
        source="databricks/officeqa",
        env_name="officeqa",
    )
    if missing:
        raise FileNotFoundError(
            f"OfficeQA materialization missing {len(missing)} ids (e.g. {missing[:3]})."
        )
    ensure_officeqa_docs()
    return result


def ensure_materialized_split(env_name: str, split_dir: str | Path) -> Path:
    """If *split_dir* is an id-split, materialize and return the payload dir."""
    path = Path(split_dir)
    if not is_id_split_dir(path):
        return path
    env = str(env_name or "").strip().lower()
    if env == "searchqa":
        return ensure_materialized_searchqa(path)
    if env == "docvqa":
        return ensure_materialized_docvqa(path)
    if env == "officeqa":
        return ensure_materialized_officeqa(path)
    raise ValueError(f"No materializer registered for env={env_name!r}")
