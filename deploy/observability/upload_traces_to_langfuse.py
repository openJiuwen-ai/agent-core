# coding: utf-8
"""Upload ``.jsonl`` trace files to Langfuse via the local OTel collector.

Accepts either a single file or a directory:

    python upload_traces_to_langfuse.py <path>
    python upload_traces_to_langfuse.py --file traces-2026-06-29.jsonl
    python upload_traces_to_langfuse.py --dir <traces_dir>

Where ``<path>`` is:
  - a ``.jsonl`` file → upload every line in it
  - a directory     → upload every ``*.jsonl`` directly under it (flat,
                      no sub-folder walking)

The file exporter writes one per-day ``traces-<YYYY-MM-DD>.jsonl`` whose
lines are spans from potentially many traces, interleaved. Every line
is a standalone OTLP JSON ``ExportTraceServiceRequest`` carrying a
single span — just POST each line to the collector. No reconstruction,
no merging. The collector splits traces by the ``traceId`` carried on
each span, so interleaving is irrelevant for ingestion.
``session.id`` (if present) is read by Langfuse from span attributes,
not the filename.

After upload, the script prints the unique trace IDs ingested, parsed
from each uploaded line's ``resourceSpans[].scopeSpans[].spans[].traceId``.

Prerequisites:
    docker-compose up -d   # from deploy/observability/

The collector listens on :4318 (OTLP HTTP, no auth).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request

_COLLECTOR = "http://localhost:4318/v1/traces"


def _iter_lines(path: str):
    """Yield non-empty stripped lines from a .jsonl file."""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield line


def _extract_trace_id(line_body: bytes) -> str | None:
    """Parse one OTLP JSON line and return its traceId, or None."""
    try:
        data = json.loads(line_body)
    except (ValueError, json.JSONDecodeError):
        return None
    for rs in data.get("resourceSpans", []):
        for ss in rs.get("scopeSpans", []):
            for sp in ss.get("spans", []):
                tid = sp.get("traceId")
                if isinstance(tid, str):
                    return tid
    return None


def _post_line(body: bytes, endpoint: str) -> bool:
    """POST one OTLP JSON line to the collector. Returns True on success."""
    req = urllib.request.Request(endpoint, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except urllib.error.HTTPError as e:
        snippet = e.read()[:200]
        print(f"[upload] HTTP {e.code}: {snippet}", flush=True)
    except urllib.error.URLError as e:
        print(f"[upload] url error: {e}", flush=True)
    except OSError as e:
        print(f"[upload] net error: {e}", flush=True)
    return False


def _upload_one(path: str, endpoint: str) -> tuple[int, int, list[str]]:
    """Upload every line of one ``.jsonl`` file.

    Returns (lines_ok, lines_fail, trace_ids) where trace_ids are the
    unique traceIds seen in successfully uploaded lines.
    """
    ok = 0
    fail = 0
    seen: set[str] = set()
    trace_ids: list[str] = []
    for line in _iter_lines(path):
        body = line.encode("utf-8")
        tid = _extract_trace_id(body)
        if _post_line(body, endpoint):
            ok += 1
            if tid and tid not in seen:
                seen.add(tid)
                trace_ids.append(tid)
        else:
            fail += 1
    return ok, fail, trace_ids


def _collect_files(path: str) -> list[str] | None:
    """Return list of .jsonl files to upload from a file/dir path.

    Returns None if path doesn't exist; empty list if dir has no .jsonl.
    Only top-level ``*.jsonl`` under a directory are picked up (flat
    layout — matches the file exporter's per-trace output).
    """
    if os.path.isfile(path):
        return [path]
    if os.path.isdir(path):
        return sorted(glob.glob(os.path.join(path, "*.jsonl")))
    return None


def _resolve_input_path(args: argparse.Namespace) -> str | None:
    """Pick the input path from positional / --dir / --file (in that order)."""
    if args.path:
        return args.path
    if args.dir:
        return args.dir
    if args.file:
        return args.file
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload per-trace .jsonl trace files to collector")
    parser.add_argument(
        "path",
        nargs="?",
        help="trace .jsonl file or directory containing .jsonl files",
    )
    parser.add_argument(
        "--dir",
        help="directory containing .jsonl trace files (alternative to positional path)",
    )
    parser.add_argument(
        "--file",
        help="single .jsonl trace file (alternative to positional path)",
    )
    parser.add_argument("--endpoint", default=_COLLECTOR)
    args = parser.parse_args()

    path = _resolve_input_path(args)
    if not path:
        parser.error("provide a trace file/dir as a positional argument, or use --dir / --file")

    files = _collect_files(path)
    if files is None:
        print(f"[upload] path not found: {path}", flush=True)
        return 2
    if not files:
        print(f"[upload] no *.jsonl found under {path}", flush=True)
        return 2

    print(
        f"[upload] source={path}  files={len(files)}  endpoint={args.endpoint}",
        flush=True,
    )

    total_ok = 0
    total_fail = 0
    uploaded_trace_ids: list[str] = []
    t0 = time.time()
    for fpath in files:
        ok, fail, trace_ids = _upload_one(fpath, args.endpoint)
        total_ok += ok
        total_fail += fail
        uploaded_trace_ids.extend(trace_ids)

    elapsed = time.time() - t0
    print(
        f"[upload] total_lines={total_ok + total_fail} ok={total_ok} fail={total_fail} elapsed={elapsed:.1f}s",
        flush=True,
    )

    unique_ids = list(dict.fromkeys(uploaded_trace_ids))
    if unique_ids:
        print(f"[upload] trace_ids ({len(unique_ids)}):", flush=True)
        for tid in unique_ids:
            print(f"  {tid}", flush=True)
    else:
        print("[upload] no trace ids parsed from uploaded files", flush=True)

    return 0 if total_fail == 0 and total_ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
